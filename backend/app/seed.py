from __future__ import annotations

import json
import os
from typing import Any
from pathlib import Path
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.runtime import get_runtime_paths
from app.models import Player
from app.player_popularity import difficulty_from_popularity

DATASET_PATH = get_runtime_paths().data_dir / "players.seed.json"
BUNDLED_DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "players.seed.json"
PLAYER_SYNC_FIELDS = (
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

VALID_POSITION_GROUPS = {"goalkeeper", "defender", "midfielder", "forward"}
CURRENT_YEAR = datetime.now().year


def _player_to_payload(player: Player) -> dict[str, Any]:
    return {field: getattr(player, field) for field in PLAYER_SYNC_FIELDS}


def _normalize_string_list(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value).strip()
        if not value:
            continue
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(value)
    return normalized


def _normalize_players(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    male_players: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for raw_player in players:
        wikidata_id = str(raw_player.get("wikidata_id", "")).strip()
        if not wikidata_id or wikidata_id in seen_ids:
            continue
        if str(raw_player.get("gender_key", "male")).strip().lower() != "male":
            continue

        seen_ids.add(wikidata_id)
        player = dict(raw_player)
        player["wikidata_id"] = wikidata_id
        player["gender_key"] = "male"
        player["name"] = str(player.get("name", "")).strip()
        player["name_ar"] = str(player.get("name_ar", "")).strip()
        player["image_url"] = str(player.get("image_url", "")).strip()
        player["fame_score"] = int(player.get("fame_score") or 0)
        player["birth_year"] = int(player.get("birth_year") or 0)
        player["is_active"] = bool(player.get("is_active"))
        player["countries"] = _normalize_string_list(list(player.get("countries") or []))
        player["countries_ar"] = _normalize_string_list(list(player.get("countries_ar") or []))
        player["continents"] = _normalize_string_list(list(player.get("continents") or []))
        player["continents_ar"] = _normalize_string_list(list(player.get("continents_ar") or []))
        player["positions"] = _normalize_string_list(list(player.get("positions") or []))
        player["positions_ar"] = _normalize_string_list(list(player.get("positions_ar") or []))
        player["aliases"] = _normalize_string_list(list(player.get("aliases") or []))
        player["current_team"] = str(player.get("current_team", "")).strip()
        player["current_team_ar"] = str(player.get("current_team_ar", "")).strip()

        position_group = str(player.get("position_group", "unknown")).strip().lower()
        player["position_group"] = position_group
        if position_group not in VALID_POSITION_GROUPS or not player["positions"]:
            continue
        if not player["name"] or not player["image_url"]:
            continue
        if not player["countries"] and not player["countries_ar"]:
            continue
        if player["birth_year"] < 1860 or player["birth_year"] > CURRENT_YEAR:
            continue

        male_players.append(player)

    male_players.sort(key=lambda item: (-item["fame_score"], item["name"]))
    for player in male_players:
        player["difficulty"] = difficulty_from_popularity(player["fame_score"], player["countries"])

    return male_players


def _load_source_players(db: Session) -> list[dict[str, Any]]:
    if DATASET_PATH.exists():
        return json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    if BUNDLED_DATASET_PATH.exists():
        return json.loads(BUNDLED_DATASET_PATH.read_text(encoding="utf-8"))

    existing_players = [_player_to_payload(player) for player in db.scalars(select(Player)).all()]
    if existing_players:
        return existing_players

    try:
        from scripts.generate_football_players import generate_players
    except Exception:
        return []

    return generate_players()


def _write_dataset(players: list[dict[str, Any]]) -> None:
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(json.dumps(players, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_database(db: Session, players: list[dict[str, Any]]) -> None:
    existing_players = {
        player.wikidata_id: player
        for player in db.scalars(select(Player)).all()
    }
    incoming_ids: set[str] = set()

    for payload in players:
        incoming_ids.add(payload["wikidata_id"])
        existing_player = existing_players.get(payload["wikidata_id"])
        if existing_player is None:
            db.add(
                Player(
                    **{
                        field: payload.get(field)
                        for field in PLAYER_SYNC_FIELDS
                    }
                )
            )
            continue

        for field in PLAYER_SYNC_FIELDS:
            setattr(existing_player, field, payload[field])

    for wikidata_id, player in existing_players.items():
        if wikidata_id not in incoming_ids:
            db.delete(player)

    db.commit()


def seed_database(db: Session) -> None:
    if os.getenv("VERCEL", "").strip():
        existing_count = len(db.scalars(select(Player.id).limit(1)).all())
        if existing_count > 0:
            return

    source_players = _load_source_players(db)
    if not source_players:
        raise RuntimeError(
            f"Player seed file not found at {DATASET_PATH} or {BUNDLED_DATASET_PATH}, and no existing player catalog was available."
        )

    normalized_players = _normalize_players(source_players)
    if not normalized_players:
        raise RuntimeError("No male football players were available after normalizing the catalog.")

    _write_dataset(normalized_players)
    _sync_database(db, normalized_players)
