import os
import shutil
from pathlib import Path
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.runtime import get_runtime_paths

RUNTIME_PATHS = get_runtime_paths()
DATABASE_PATH = RUNTIME_PATHS.database_path
CONFIGURED_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BUNDLED_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "players.catalog.db"
BUNDLED_DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "players.seed.json"


def _bootstrap_runtime_catalog() -> None:
    if not os.getenv("VERCEL", "").strip():
        return

    if not DATABASE_PATH.exists() and BUNDLED_DATABASE_PATH.exists():
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUNDLED_DATABASE_PATH, DATABASE_PATH)

    runtime_dataset_path = RUNTIME_PATHS.data_dir / "players.seed.json"
    if not runtime_dataset_path.exists() and BUNDLED_DATASET_PATH.exists():
        runtime_dataset_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUNDLED_DATASET_PATH, runtime_dataset_path)


_bootstrap_runtime_catalog()
DATABASE_URL = CONFIGURED_DATABASE_URL or URL.create("sqlite", database=str(DATABASE_PATH))

ENGINE_OPTIONS: dict[str, object] = {
    "pool_pre_ping": True,
}
if str(DATABASE_URL).startswith("sqlite"):
    ENGINE_OPTIONS["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **ENGINE_OPTIONS)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
