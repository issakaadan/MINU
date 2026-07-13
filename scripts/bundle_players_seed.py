from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_DATASET_PATH = PROJECT_ROOT / "backend" / "data" / "players.seed.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_football_players import generate_players


def _local_runtime_dataset() -> Path | None:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if not local_appdata:
        return None

    candidate = Path(local_appdata) / "WhoIsThePlayerFootball" / "data" / "players.seed.json"
    return candidate if candidate.exists() else None


def main() -> None:
    BUNDLED_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)

    if BUNDLED_DATASET_PATH.exists() and BUNDLED_DATASET_PATH.stat().st_size > 0:
        print(f"Using existing bundled players seed: {BUNDLED_DATASET_PATH}")
        return

    runtime_dataset = _local_runtime_dataset()
    if runtime_dataset is not None:
        BUNDLED_DATASET_PATH.write_text(runtime_dataset.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Bundled players seed from local runtime: {runtime_dataset}")
        return

    players = generate_players()
    BUNDLED_DATASET_PATH.write_text(json.dumps(players, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Bundled {len(players)} football players into {BUNDLED_DATASET_PATH}")


if __name__ == "__main__":
    main()
