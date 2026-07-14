from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LOCAL_APPDATA = os.getenv("LOCALAPPDATA", "").strip()
RUNTIME_ROOT = (
    Path(LOCAL_APPDATA) / "WhoIsThePlayerFootball"
    if LOCAL_APPDATA
    else Path.home() / ".who_is_the_player_football"
)
OUTPUT_PATH = RUNTIME_ROOT / "data" / "players.seed.json"
USER_AGENT = "WhoIsThePlayerFootball/1.0 (Codex)"
CURRENT_YEAR = datetime.now().year
PLAYERS_PER_BUCKET = 110
QUERY_SLEEP_SECONDS = 1.0
ENTITY_BATCH_SIZE = 25

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.player_popularity import difficulty_from_popularity

DIFFICULTY_BUCKETS = [
    {"difficulty": 1, "min_links": 55, "max_links": None},
    {"difficulty": 2, "min_links": 35, "max_links": 55},
    {"difficulty": 3, "min_links": 20, "max_links": 35},
    {"difficulty": 4, "min_links": 10, "max_links": 20},
    {"difficulty": 5, "min_links": 3, "max_links": 10},
]
OUTPUT_DIFFICULTY_LEVELS = 4

MANUAL_EXCLUSIONS = {
    "Albert Camus",
    "Sean Connery",
}

POSITION_GROUP_MAP = {
    "goalkeeper": ("goalkeeper", "keeper"),
    "defender": ("defender", "back", "full-back", "full back", "centre-back", "center-back", "sweeper", "wing-back"),
    "midfielder": ("midfielder", "half", "wing half"),
    "forward": ("forward", "striker", "winger", "attacker", "inside forward"),
}
VALID_POSITION_GROUPS = {"goalkeeper", "defender", "midfielder", "forward"}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = value.casefold()
    value = re.sub(r"[^\w\s\u0600-\u06FF]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def build_bucket_query(min_links: int, max_links: int | None, limit: int) -> str:
    filters = [f"?sitelinks >= {min_links}"]
    if max_links is not None:
        filters.append(f"?sitelinks < {max_links}")
    filter_clause = " && ".join(filters)

    return f"""
SELECT DISTINCT ?player ?playerLabelEn ?playerLabelAr ?image ?birthDate ?sitelinks
WHERE {{
  ?player wdt:P31 wd:Q5;
          wdt:P106 wd:Q937857;
          wdt:P413 ?position;
          wdt:P18 ?image;
          wdt:P569 ?birthDate;
          wikibase:sitelinks ?sitelinks.
  FILTER({filter_clause})
  ?player rdfs:label ?playerLabelEn FILTER(LANG(?playerLabelEn) = "en")
  OPTIONAL {{ ?player rdfs:label ?playerLabelAr FILTER(LANG(?playerLabelAr) = "ar") }}
}}
ORDER BY DESC(?sitelinks)
LIMIT {limit}
"""


def fetch_json(url: str, attempts: int = 5, timeout: int = 90) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 429 and attempt < attempts:
                retry_after = error.headers.get("Retry-After", "").strip()
                try:
                    wait_seconds = max(8, int(retry_after))
                except ValueError:
                    wait_seconds = attempt * 12
                time.sleep(wait_seconds)
                continue
            if attempt == attempts:
                raise
            time.sleep(attempt * 3)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(attempt * 2)
    return {}


def fetch_sparql_bucket(min_links: int, max_links: int | None, limit: int) -> list[dict[str, Any]]:
    query = build_bucket_query(min_links, max_links, limit)
    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    payload = fetch_json(url)
    return payload["results"]["bindings"]


def fetch_entities(ids: list[str], props: str = "labels|claims", languages: str = "en|ar") -> dict[str, Any]:
    if not ids:
        return {}
    entities: dict[str, Any] = {}
    for batch in chunked(ids, ENTITY_BATCH_SIZE):
        ids_value = "|".join(batch)
        url = (
            "https://www.wikidata.org/w/api.php"
            f"?action=wbgetentities&ids={quote(ids_value)}&languages={quote(languages)}&props={quote(props)}&format=json"
        )
        payload = fetch_json(url, timeout=60)
        entities.update(payload.get("entities", {}))
        time.sleep(0.7)
    return entities


def extract_entity_id(claim: dict[str, Any]) -> str | None:
    try:
        datavalue = claim["mainsnak"]["datavalue"]["value"]
        if isinstance(datavalue, dict):
            return datavalue.get("id")
    except KeyError:
        return None
    return None


def extract_claim_ids(entity: dict[str, Any], property_id: str) -> list[str]:
    results: list[str] = []
    for claim in entity.get("claims", {}).get(property_id, []):
        entity_id = extract_entity_id(claim)
        if entity_id:
            results.append(entity_id)
    return unique_preserving_order(results)


def extract_current_club_ids(entity: dict[str, Any]) -> list[str]:
    results: list[str] = []
    for claim in entity.get("claims", {}).get("P54", []):
        qualifiers = claim.get("qualifiers", {})
        if "P582" in qualifiers:
            continue
        entity_id = extract_entity_id(claim)
        if entity_id:
            results.append(entity_id)
    return unique_preserving_order(results)


def has_death_claim(entity: dict[str, Any]) -> bool:
    return bool(entity.get("claims", {}).get("P570"))


def label_for(entity: dict[str, Any], language: str) -> str:
    return entity.get("labels", {}).get(language, {}).get("value", "").strip()


def determine_position_group(positions_en: list[str]) -> str:
    score = {key: 0 for key in POSITION_GROUP_MAP}
    for position in positions_en:
        normalized = normalize_text(position)
        for group, keywords in POSITION_GROUP_MAP.items():
            if any(keyword in normalized for keyword in keywords):
                score[group] += 1
    best_group = max(score, key=score.get)
    return best_group if score[best_group] > 0 else "unknown"


def infer_activity(current_clubs_en: list[str], birth_year: int, is_dead: bool) -> bool:
    if is_dead:
        return False
    if current_clubs_en:
        return True
    return birth_year >= CURRENT_YEAR - 26


def build_aliases(name_en: str, name_ar: str) -> list[str]:
    aliases = [name_en]
    if name_ar:
        aliases.append(name_ar)

    for name in [name_en, name_ar]:
        if not name:
            continue
        parts = [part for part in re.split(r"\s+", name) if part]
        if parts:
            aliases.append(parts[-1])
        if len(parts) >= 2:
            aliases.append(" ".join(parts[-2:]))

    return unique_preserving_order(aliases)


def parse_players(
    bucket_rows: list[dict[str, Any]],
    difficulty: int,
    player_entities: dict[str, Any],
    country_entities: dict[str, Any],
    continent_entities: dict[str, Any],
    position_entities: dict[str, Any],
    gender_entities: dict[str, Any],
    club_entities: dict[str, Any],
) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []

    country_continent_map = {
        entity_id: extract_claim_ids(entity, "P30")
        for entity_id, entity in country_entities.items()
    }

    for row in bucket_rows:
        wikidata_id = row["player"]["value"].rsplit("/", 1)[-1]
        name = row["playerLabelEn"]["value"].strip()
        name_ar = row.get("playerLabelAr", {}).get("value", "").strip()
        if name.startswith("Q") or name in MANUAL_EXCLUSIONS:
            continue

        player_entity = player_entities.get(wikidata_id)
        if not player_entity:
            continue

        country_ids = extract_claim_ids(player_entity, "P27")
        position_ids = extract_claim_ids(player_entity, "P413")
        gender_ids = extract_claim_ids(player_entity, "P21")
        current_club_ids = extract_current_club_ids(player_entity)

        countries_en = unique_preserving_order([label_for(country_entities.get(entity_id, {}), "en") for entity_id in country_ids])
        countries_ar = unique_preserving_order([label_for(country_entities.get(entity_id, {}), "ar") for entity_id in country_ids])
        if not countries_en and not countries_ar:
            continue

        continent_ids = unique_preserving_order(
            [
                continent_id
                for country_id in country_ids
                for continent_id in country_continent_map.get(country_id, [])
            ]
        )
        continents_en = unique_preserving_order([label_for(continent_entities.get(entity_id, {}), "en") for entity_id in continent_ids])
        continents_ar = unique_preserving_order([label_for(continent_entities.get(entity_id, {}), "ar") for entity_id in continent_ids])

        positions_en = unique_preserving_order([label_for(position_entities.get(entity_id, {}), "en") for entity_id in position_ids])
        positions_ar = unique_preserving_order([label_for(position_entities.get(entity_id, {}), "ar") for entity_id in position_ids])
        position_group = determine_position_group(positions_en)
        if not positions_en or position_group not in VALID_POSITION_GROUPS:
            continue

        birth_year = int(row["birthDate"]["value"][:4])
        is_dead = has_death_claim(player_entity)
        is_active = infer_activity(current_club_ids, birth_year, is_dead)
        current_clubs_en = unique_preserving_order([label_for(club_entities.get(entity_id, {}), "en") for entity_id in current_club_ids])
        current_clubs_ar = unique_preserving_order([label_for(club_entities.get(entity_id, {}), "ar") for entity_id in current_club_ids])

        gender_label = label_for(gender_entities.get(gender_ids[0], {}), "en") if gender_ids else "male"
        gender_key = "female" if "female" in gender_label.casefold() else "male"
        if gender_key != "male":
            continue

        players.append(
            {
                "wikidata_id": wikidata_id,
                "name": name,
                "name_ar": name_ar,
                "image_url": row["image"]["value"].replace("http://", "https://"),
                "difficulty": difficulty,
                "fame_score": int(row["sitelinks"]["value"]),
                "birth_year": birth_year,
                "gender_key": gender_key,
                "position_group": position_group,
                "is_active": is_active,
                "countries": countries_en,
                "countries_ar": countries_ar,
                "continents": continents_en,
                "continents_ar": continents_ar,
                "positions": positions_en,
                "positions_ar": positions_ar,
                "aliases": build_aliases(name, name_ar),
                "current_team": current_clubs_en[0] if current_clubs_en else "",
                "current_team_ar": current_clubs_ar[0] if current_clubs_ar else "",
            }
        )

    return players


def generate_players() -> list[dict[str, Any]]:
    all_players: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for bucket in DIFFICULTY_BUCKETS:
        print(f"Fetching bucket for difficulty {bucket['difficulty']} ...")
        bucket_rows = fetch_sparql_bucket(bucket["min_links"], bucket["max_links"], PLAYERS_PER_BUCKET)
        player_ids = [row["player"]["value"].rsplit("/", 1)[-1] for row in bucket_rows]
        player_entities = fetch_entities(player_ids)

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
        country_entities = fetch_entities(country_ids)
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

        bucket_players = parse_players(
            bucket_rows,
            bucket["difficulty"],
            player_entities,
            country_entities,
            continent_entities,
            position_entities,
            gender_entities,
            club_entities,
        )

        for player in bucket_players:
            if player["wikidata_id"] in seen_ids:
                continue
            seen_ids.add(player["wikidata_id"])
            all_players.append(player)

        print(f"Collected {len(bucket_players)} players for difficulty {bucket['difficulty']}.")
        time.sleep(QUERY_SLEEP_SECONDS)

    all_players.sort(key=lambda item: (-item["fame_score"], item["name"]))
    for player in all_players:
        player["difficulty"] = difficulty_from_popularity(player["fame_score"], player.get("countries") or [])

    all_players.sort(key=lambda item: (item["difficulty"], -item["fame_score"], item["name"]))
    return all_players


def main() -> None:
    players = generate_players()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(players, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(players)} football players to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
