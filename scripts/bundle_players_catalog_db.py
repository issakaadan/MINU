from __future__ import annotations

import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "backend" / "data" / "players.seed.json"
DATABASE_PATH = PROJECT_ROOT / "backend" / "data" / "players.catalog.db"


def main() -> None:
    if DATABASE_PATH.exists() and DATABASE_PATH.stat().st_size > 0:
        print(f"Using existing bundled catalog database: {DATABASE_PATH}")
        return

    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Players seed file not found at {DATASET_PATH}")

    players = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()

    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                wikidata_id VARCHAR(32) NOT NULL UNIQUE,
                name VARCHAR(160) NOT NULL,
                name_ar VARCHAR(160) NOT NULL DEFAULT '',
                image_url VARCHAR(500) NOT NULL,
                difficulty INTEGER NOT NULL,
                fame_score INTEGER NOT NULL DEFAULT 0,
                birth_year INTEGER NOT NULL,
                gender_key VARCHAR(16) NOT NULL DEFAULT 'male',
                position_group VARCHAR(24) NOT NULL DEFAULT 'unknown',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                countries JSON NOT NULL DEFAULT '[]',
                countries_ar JSON NOT NULL DEFAULT '[]',
                continents JSON NOT NULL DEFAULT '[]',
                continents_ar JSON NOT NULL DEFAULT '[]',
                positions JSON NOT NULL DEFAULT '[]',
                positions_ar JSON NOT NULL DEFAULT '[]',
                aliases JSON NOT NULL DEFAULT '[]',
                current_team VARCHAR(160) NOT NULL DEFAULT '',
                current_team_ar VARCHAR(160) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX ix_players_id ON players (id)")
        connection.execute("CREATE INDEX ix_players_wikidata_id ON players (wikidata_id)")
        connection.execute("CREATE INDEX ix_players_name ON players (name)")
        connection.execute("CREATE INDEX ix_players_difficulty ON players (difficulty)")

        for index, player in enumerate(players, start=1):
            connection.execute(
                """
                INSERT INTO players (
                    id,
                    wikidata_id,
                    name,
                    name_ar,
                    image_url,
                    difficulty,
                    fame_score,
                    birth_year,
                    gender_key,
                    position_group,
                    is_active,
                    countries,
                    countries_ar,
                    continents,
                    continents_ar,
                    positions,
                    positions_ar,
                    aliases,
                    current_team,
                    current_team_ar,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    index,
                    player["wikidata_id"],
                    player["name"],
                    player.get("name_ar", ""),
                    player["image_url"],
                    int(player["difficulty"]),
                    int(player.get("fame_score") or 0),
                    int(player.get("birth_year") or 0),
                    player.get("gender_key", "male"),
                    player.get("position_group", "unknown"),
                    1 if player.get("is_active") else 0,
                    json.dumps(player.get("countries") or [], ensure_ascii=False),
                    json.dumps(player.get("countries_ar") or [], ensure_ascii=False),
                    json.dumps(player.get("continents") or [], ensure_ascii=False),
                    json.dumps(player.get("continents_ar") or [], ensure_ascii=False),
                    json.dumps(player.get("positions") or [], ensure_ascii=False),
                    json.dumps(player.get("positions_ar") or [], ensure_ascii=False),
                    json.dumps(player.get("aliases") or [], ensure_ascii=False),
                    player.get("current_team", ""),
                    player.get("current_team_ar", ""),
                    "2026-07-08T00:00:00+00:00",
                ),
            )

        connection.commit()
        print(f"Bundled {len(players)} players into {DATABASE_PATH}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
