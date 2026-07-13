from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

APP_RUNTIME_DIRNAME = "WhoIsThePlayerFootball"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    data_dir: Path
    logs_dir: Path
    reports_dir: Path
    exports_dir: Path
    scan_results_dir: Path
    database_path: Path


def _default_runtime_root() -> Path:
    configured_root = os.getenv("LOCAL_RUNTIME_DIR", "").strip()
    if configured_root:
        return Path(configured_root).expanduser()

    if os.getenv("VERCEL", "").strip():
        return Path("/tmp") / APP_RUNTIME_DIRNAME

    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        return Path(local_appdata) / APP_RUNTIME_DIRNAME

    return Path.home() / ".authorized_network_assessment"


@lru_cache
def get_runtime_paths() -> RuntimePaths:
    root = _default_runtime_root()
    data_dir = root / "data"
    logs_dir = root / "logs"
    reports_dir = root / "reports"
    exports_dir = root / "exports"
    scan_results_dir = root / "scan_results"

    for directory in (
        root,
        data_dir,
        logs_dir,
        reports_dir,
        exports_dir,
        scan_results_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    return RuntimePaths(
        root=root,
        data_dir=data_dir,
        logs_dir=logs_dir,
        reports_dir=reports_dir,
        exports_dir=exports_dir,
        scan_results_dir=scan_results_dir,
        database_path=data_dir / "who_is_the_player_football.db",
    )
