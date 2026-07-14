from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.runtime import get_runtime_paths
from app.models import Player
from app.player_popularity import difficulty_from_popularity
from app.schemas import AdminPlayerRead, AdminPlayerWrite
from app.seed import BUNDLED_DATASET_PATH, DATASET_PATH, _normalize_players, _sync_database, _write_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_football_players import (
    build_aliases,
    determine_position_group,
    extract_claim_ids,
    extract_current_club_ids,
    fetch_entities,
    has_death_claim,
    infer_activity,
    label_for,
    unique_preserving_order,
)

VALID_POSITION_GROUPS = {"goalkeeper", "defender", "midfielder", "forward"}
REFRESH_REPORT_PATH = get_runtime_paths().data_dir / "player_catalog_refresh.json"
SYNC_FIELDS = (
    "wikidata_id",
    "name",
    "name_ar",
    "image_url",
    "difficulty",
    "fame_score",
    "birth_year",
    "gender_key",
    "position_group",
    "is_active",
    "countries",
    "countries_ar",
    "continents",
    "continents_ar",
    "positions",
    "positions_ar",
    "aliases",
    "current_team",
    "current_team_ar",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_refresh_report_file(path: Path = REFRESH_REPORT_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    return payload


def get_last_refresh_report() -> dict[str, Any] | None:
    return _read_refresh_report_file()


def _write_refresh_report(summary: dict[str, Any]) -> dict[str, Any]:
    report = {
        "refreshed_at": _utc_now().isoformat(),
        "scanned_players": int(summary.get("scanned_players") or 0),
        "updated_players": int(summary.get("updated_players") or 0),
        "removed_players": int(summary.get("removed_players") or 0),
        "locked_players": int(summary.get("locked_players") or 0),
        "total_players": int(summary.get("total_players") or 0),
    }
    REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFRESH_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _read_dataset_records(db: Session) -> list[dict[str, Any]]:
    if DATASET_PATH.exists():
        return json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if BUNDLED_DATASET_PATH.exists():
        return json.loads(BUNDLED_DATASET_PATH.read_text(encoding="utf-8"))

    players = db.scalars(select(Player).order_by(Player.fame_score.desc(), Player.name.asc())).all()
    return [_record_from_player(player) for player in players]


def _record_from_player(player: Player) -> dict[str, Any]:
    payload = {field: getattr(player, field) for field in SYNC_FIELDS}
    payload["admin_locked"] = False
    return payload


def _record_from_payload(payload: AdminPlayerWrite) -> dict[str, Any]:
    wikidata_id = payload.wikidata_id.strip() or f"MANUAL-{uuid4().hex[:12].upper()}"
    return {
        "wikidata_id": wikidata_id,
        "name": payload.name.strip(),
        "name_ar": payload.name_ar.strip(),
        "image_url": payload.image_url.strip(),
        "difficulty": payload.difficulty,
        "fame_score": payload.fame_score,
        "birth_year": payload.birth_year,
        "gender_key": "male",
        "position_group": payload.position_group,
        "is_active": payload.is_active,
        "countries": payload.countries,
        "countries_ar": payload.countries_ar,
        "continents": payload.continents,
        "continents_ar": payload.continents_ar,
        "positions": payload.positions,
        "positions_ar": payload.positions_ar,
        "aliases": payload.aliases or build_aliases(payload.name.strip(), payload.name_ar.strip()),
        "current_team": payload.current_team.strip(),
        "current_team_ar": payload.current_team_ar.strip(),
        "admin_locked": payload.admin_locked,
    }


def _extract_birth_year(entity: dict[str, Any], fallback_year: int) -> int:
    for claim in entity.get("claims", {}).get("P569", []):
        try:
            time_value = claim["mainsnak"]["datavalue"]["value"]["time"]
        except KeyError:
            continue
        if len(time_value) >= 5:
            return int(time_value[1:5])
    return fallback_year


def _extract_image_url(entity: dict[str, Any], fallback_url: str) -> str:
    for claim in entity.get("claims", {}).get("P18", []):
        try:
            file_name = str(claim["mainsnak"]["datavalue"]["value"]).strip().replace(" ", "_")
        except KeyError:
            continue
        if file_name:
            return f"https://commons.wikimedia.org/wiki/Special:FilePath/{file_name}"
    return fallback_url


def _admin_locked(record: dict[str, Any]) -> bool:
    return bool(record.get("admin_locked"))


def _manual_record(record: dict[str, Any]) -> bool:
    return str(record.get("wikidata_id", "")).upper().startswith("MANUAL-")


def _country_continent_map(country_entities: dict[str, Any]) -> dict[str, list[str]]:
    return {
        entity_id: extract_claim_ids(entity, "P30")
        for entity_id, entity in country_entities.items()
    }


def _build_refreshed_record(
    existing_record: dict[str, Any],
    entity: dict[str, Any] | None,
    country_entities: dict[str, Any],
    continent_entities: dict[str, Any],
    position_entities: dict[str, Any],
    gender_entities: dict[str, Any],
    club_entities: dict[str, Any],
    country_continent_lookup: dict[str, list[str]],
) -> dict[str, Any] | None:
    if not entity:
        return None

    country_ids = extract_claim_ids(entity, "P27")
    position_ids = extract_claim_ids(entity, "P413")
    gender_ids = extract_claim_ids(entity, "P21")
    current_club_ids = extract_current_club_ids(entity)

    countries_en = unique_preserving_order([label_for(country_entities.get(entity_id, {}), "en") for entity_id in country_ids])
    countries_ar = unique_preserving_order([label_for(country_entities.get(entity_id, {}), "ar") for entity_id in country_ids])
    if not countries_en and not countries_ar:
        return None

    continent_ids = unique_preserving_order(
        [
            continent_id
            for country_id in country_ids
            for continent_id in country_continent_lookup.get(country_id, [])
        ]
    )
    continents_en = unique_preserving_order([label_for(continent_entities.get(entity_id, {}), "en") for entity_id in continent_ids])
    continents_ar = unique_preserving_order([label_for(continent_entities.get(entity_id, {}), "ar") for entity_id in continent_ids])

    positions_en = unique_preserving_order([label_for(position_entities.get(entity_id, {}), "en") for entity_id in position_ids])
    positions_ar = unique_preserving_order([label_for(position_entities.get(entity_id, {}), "ar") for entity_id in position_ids])
    position_group = determine_position_group(positions_en)
    if not positions_en or position_group not in VALID_POSITION_GROUPS:
        return None

    gender_label = label_for(gender_entities.get(gender_ids[0], {}), "en") if gender_ids else "male"
    gender_key = "female" if "female" in gender_label.casefold() else "male"
    if gender_key != "male":
        return None

    birth_year = _extract_birth_year(entity, int(existing_record.get("birth_year") or 0))
    is_dead = has_death_claim(entity)
    is_active = infer_activity(current_club_ids, birth_year, is_dead)
    current_clubs_en = unique_preserving_order([label_for(club_entities.get(entity_id, {}), "en") for entity_id in current_club_ids])
    current_clubs_ar = unique_preserving_order([label_for(club_entities.get(entity_id, {}), "ar") for entity_id in current_club_ids])
    sitelinks = entity.get("sitelinks", {}) or {}
    fame_score = len(sitelinks) or int(existing_record.get("fame_score") or 0)

    refreshed = dict(existing_record)
    refreshed.update(
        {
            "name": label_for(entity, "en") or str(existing_record.get("name", "")).strip(),
            "name_ar": label_for(entity, "ar") or str(existing_record.get("name_ar", "")).strip(),
            "image_url": _extract_image_url(entity, str(existing_record.get("image_url", "")).strip()),
            "difficulty": difficulty_from_popularity(fame_score, countries_en),
            "fame_score": fame_score,
            "birth_year": birth_year,
            "gender_key": "male",
            "position_group": position_group,
            "is_active": is_active,
            "countries": countries_en,
            "countries_ar": countries_ar,
            "continents": continents_en,
            "continents_ar": continents_ar,
            "positions": positions_en,
            "positions_ar": positions_ar,
            "aliases": build_aliases(
                label_for(entity, "en") or str(existing_record.get("name", "")).strip(),
                label_for(entity, "ar") or str(existing_record.get("name_ar", "")).strip(),
            ),
            "current_team": current_clubs_en[0] if current_clubs_en else "",
            "current_team_ar": current_clubs_ar[0] if current_clubs_ar else "",
        }
    )
    return refreshed


def _admin_lock_map(records: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        str(record.get("wikidata_id", "")): _admin_locked(record)
        for record in records
    }


def _sync_records(db: Session, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = _normalize_players(records)
    _write_dataset(normalized)
    _sync_database(
        db,
        [
            {field: record.get(field) for field in SYNC_FIELDS}
            for record in normalized
        ],
    )
    return normalized


def _find_player_by_wikidata(db: Session, wikidata_id: str) -> Player | None:
    return db.scalar(select(Player).where(Player.wikidata_id == wikidata_id))


def to_admin_player_read(player: Player, admin_locked: bool = False) -> AdminPlayerRead:
    return AdminPlayerRead.model_validate(
        {
            **_record_from_player(player),
            "id": player.id,
            "created_at": player.created_at,
            "admin_locked": admin_locked,
        }
    )


def create_player(db: Session, payload: AdminPlayerWrite) -> tuple[Player, int]:
    records = _read_dataset_records(db)
    new_record = _record_from_payload(payload)
    existing = next((record for record in records if str(record.get("wikidata_id", "")).casefold() == new_record["wikidata_id"].casefold()), None)
    if existing is not None:
        raise HTTPException(status_code=400, detail="هذا اللاعب موجود من قبل بنفس Wikidata.")

    normalized = _sync_records(db, [*records, new_record])
    player = _find_player_by_wikidata(db, new_record["wikidata_id"])
    if player is None:
        raise HTTPException(status_code=500, detail="ما ضبط حفظ اللاعب.")
    return player, len(normalized)


def update_player(db: Session, player_id: int, payload: AdminPlayerWrite) -> tuple[Player, int]:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="اللاعب مو موجود.")

    records = _read_dataset_records(db)
    target_record = next((record for record in records if str(record.get("wikidata_id", "")) == player.wikidata_id), None)
    if target_record is None:
        raise HTTPException(status_code=404, detail="سجل اللاعب مو موجود.")

    updated_record = _record_from_payload(payload)
    duplicate = next(
        (
            record for record in records
            if record is not target_record
            and str(record.get("wikidata_id", "")).casefold() == updated_record["wikidata_id"].casefold()
        ),
        None,
    )
    if duplicate is not None:
        raise HTTPException(status_code=400, detail="فيه لاعب ثاني بنفس Wikidata.")

    target_record.update(updated_record)
    normalized = _sync_records(db, records)
    updated_player = _find_player_by_wikidata(db, updated_record["wikidata_id"])
    if updated_player is None:
        raise HTTPException(status_code=500, detail="ما ضبط تحديث اللاعب.")
    return updated_player, len(normalized)


def delete_player(db: Session, player_id: int) -> int:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="اللاعب مو موجود.")

    records = _read_dataset_records(db)
    filtered_records = [
        record for record in records
        if str(record.get("wikidata_id", "")) != player.wikidata_id
    ]
    normalized = _sync_records(db, filtered_records)
    return len(normalized)


def refresh_players_from_source(db: Session) -> dict[str, Any]:
    records = _read_dataset_records(db)
    locked_players = sum(1 for record in records if _admin_locked(record) or _manual_record(record))
    refreshable_records = [
        record for record in records
        if not _admin_locked(record) and not _manual_record(record)
    ]
    refreshable_ids = [str(record.get("wikidata_id", "")).strip() for record in refreshable_records if str(record.get("wikidata_id", "")).strip()]

    player_entities = fetch_entities(refreshable_ids, props="labels|claims|sitelinks")
    country_ids = unique_preserving_order(
        [
            country_id
            for entity in player_entities.values()
            for country_id in extract_claim_ids(entity, "P27")
        ]
    )
    position_ids = unique_preserving_order(
        [
            position_id
            for entity in player_entities.values()
            for position_id in extract_claim_ids(entity, "P413")
        ]
    )
    gender_ids = unique_preserving_order(
        [
            gender_id
            for entity in player_entities.values()
            for gender_id in extract_claim_ids(entity, "P21")
        ]
    )
    club_ids = unique_preserving_order(
        [
            club_id
            for entity in player_entities.values()
            for club_id in extract_current_club_ids(entity)
        ]
    )
    country_entities = fetch_entities(country_ids, props="labels|claims")
    continent_ids = unique_preserving_order(
        [
            continent_id
            for entity in country_entities.values()
            for continent_id in extract_claim_ids(entity, "P30")
        ]
    )
    continent_entities = fetch_entities(continent_ids, props="labels")
    position_entities = fetch_entities(position_ids, props="labels")
    gender_entities = fetch_entities(gender_ids, props="labels")
    club_entities = fetch_entities(club_ids, props="labels")
    country_continent_lookup = _country_continent_map(country_entities)

    updated_records: list[dict[str, Any]] = []
    updated_players = 0
    removed_players = 0

    for record in records:
        if _admin_locked(record) or _manual_record(record):
            updated_records.append(dict(record))
            continue

        wikidata_id = str(record.get("wikidata_id", "")).strip()
        refreshed = _build_refreshed_record(
            existing_record=record,
            entity=player_entities.get(wikidata_id),
            country_entities=country_entities,
            continent_entities=continent_entities,
            position_entities=position_entities,
            gender_entities=gender_entities,
            club_entities=club_entities,
            country_continent_lookup=country_continent_lookup,
        )
        if refreshed is None:
            removed_players += 1
            continue

        if any(refreshed.get(field) != record.get(field) for field in SYNC_FIELDS):
            updated_players += 1
        updated_records.append(refreshed)

    normalized = _sync_records(db, updated_records)
    summary: dict[str, Any] = {
        "scanned_players": len(records),
        "updated_players": updated_players,
        "removed_players": removed_players,
        "locked_players": locked_players,
        "total_players": len(normalized),
    }
    report = _write_refresh_report(summary)
    return {
        "refreshed_at": report["refreshed_at"],
        **summary,
    }


def admin_lock_map(db: Session) -> dict[str, bool]:
    return _admin_lock_map(_read_dataset_records(db))
