from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import DATABASE_PATH, SessionLocal
from app.player_catalog_service import REFRESH_REPORT_PATH, refresh_players_from_source
from app.seed import DATASET_PATH

BUNDLED_DATASET_PATH = PROJECT_ROOT / "backend" / "data" / "players.seed.json"
BUNDLED_DATABASE_PATH = PROJECT_ROOT / "backend" / "data" / "players.catalog.db"
DEPLOY_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "deploy-vercel-prod.ps1"


def copy_runtime_catalog_to_bundle() -> None:
    BUNDLED_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DATASET_PATH.exists():
        shutil.copyfile(DATASET_PATH, BUNDLED_DATASET_PATH)
    if DATABASE_PATH.exists():
        shutil.copyfile(DATABASE_PATH, BUNDLED_DATABASE_PATH)


def deploy_to_vercel() -> None:
    subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(DEPLOY_SCRIPT_PATH),
        ],
        check=True,
        cwd=str(PROJECT_ROOT),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh MINU player records from source data.")
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="Copy the refreshed runtime catalog into backend/data for packaging.",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy to Vercel after refresh if player records changed.",
    )
    parser.add_argument(
        "--deploy-always",
        action="store_true",
        help="Deploy even if no player records changed.",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        summary = refresh_players_from_source(db)

    if args.bundle:
        copy_runtime_catalog_to_bundle()

    changed = int(summary.get("updated_players") or 0) > 0 or int(summary.get("removed_players") or 0) > 0
    if args.deploy and (changed or args.deploy_always):
        deploy_to_vercel()

    print(f"Refresh report: {REFRESH_REPORT_PATH}")
    print(summary)


if __name__ == "__main__":
    main()
