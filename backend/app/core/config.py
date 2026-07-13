import os
from functools import lru_cache

from pydantic import BaseModel, Field

from app.core.runtime import get_runtime_paths

DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]


def parse_allowed_origins() -> list[str]:
    raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not raw_origins:
        return DEFAULT_ALLOWED_ORIGINS

    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


class AppConfig(BaseModel):
    app_name: str = Field(
        default_factory=lambda: os.getenv(
            "APP_NAME",
            "MINU | Football Edition",
        )
    )
    api_prefix: str = Field(default_factory=lambda: os.getenv("API_PREFIX", "/api"))
    allowed_origins: list[str] = Field(default_factory=parse_allowed_origins)
    runtime_root: str = Field(default_factory=lambda: str(get_runtime_paths().root))


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
