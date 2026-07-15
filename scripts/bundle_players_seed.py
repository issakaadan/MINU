from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_DATASET_PATH = PROJECT_ROOT / "backend" / "data" / "players.seed.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_football_players import difficulty_from_popularity, generate_players


def _write_rebalanced_dataset(players: list[dict[str, object]], source: str) -> None:
    for player in players:
        player["difficulty"] = difficulty_from_popularity(
            int(player.get("fame_score") or 0),
            player.get("countries") or [],
        )
    BUNDLED_DATASET_PATH.write_text(
        json.dumps(players, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Bundled and rebalanced {len(players)} football players from {source}")


def _local_runtime_dataset() -> Path | None:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if not local_appdata:
        return None

    candidate = Path(local_appdata) / "WhoIsThePlayerFootball" / "data" / "players.seed.json"
    return candidate if candidate.exists() else None


def main() -> None:
    BUNDLED_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)

    if BUNDLED_DATASET_PATH.exists() and BUNDLED_DATASET_PATH.stat().st_size > 0:
        players = json.loads(BUNDLED_DATASET_PATH.read_text(encoding="utf-8"))
        _write_rebalanced_dataset(players, str(BUNDLED_DATASET_PATH))
        return

    runtime_dataset = _local_runtime_dataset()
    if runtime_dataset is not None:
        players = json.loads(runtime_dataset.read_text(encoding="utf-8"))
        _write_rebalanced_dataset(players, str(runtime_dataset))
        return

    players = generate_players()
    _write_rebalanced_dataset(players, "Wikidata generation")


if __name__ == "__main__":
    main()
