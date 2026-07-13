from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

from app.api.router import api_router
from app.assistant_service import seed_assistant_catalog
from app.core.auth import auth_manager
from app.core.config import get_config
from app.core.database import Base, SessionLocal, engine
from app.core.runtime import BASE_DIR, get_runtime_paths
from app.models import Player
from app.seed import seed_database

logger = logging.getLogger(__name__)
FRONTEND_DIST_DIR = Path(
    os.getenv(
        "FRONTEND_DIST_DIR",
        str(BASE_DIR.parent / "frontend" / "dist"),
    )
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_database(db)
        seed_assistant_catalog(db)
        player_count = db.scalar(select(func.count(Player.id))) or 0
    runtime_paths = get_runtime_paths()
    auth_material = auth_manager.get_material()
    logger.warning(
        "Football game startup complete. Runtime root: %s | Database: %s | Players: %s | Minu login file: %s",
        runtime_paths.root,
        runtime_paths.database_path,
        player_count,
        auth_material.credentials_file_path,
    )
    yield


config = get_config()
app = FastAPI(
    title=config.app_name,
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=config.api_prefix)


@app.middleware("http")
async def enforce_same_origin_for_api_writes(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(f"{config.api_prefix}/"):
        sec_fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if sec_fetch_site and sec_fetch_site not in {"same-origin", "same-site", "none"}:
            return JSONResponse(status_code=403, content={"detail": "الطلب مرفوض"})

        origin = request.headers.get("origin", "").strip()
        if origin:
            normalized_allowed_origins = {allowed_origin.rstrip("/") for allowed_origin in config.allowed_origins}
            forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
            forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
            proto = forwarded_proto or request.url.scheme
            host = forwarded_host or request.headers.get("host", "").strip() or request.url.netloc
            expected_origin = f"{proto}://{host}".rstrip("/")
            normalized_origin = origin.rstrip("/")
            if normalized_origin != expected_origin and normalized_origin not in normalized_allowed_origins:
                return JSONResponse(status_code=403, content={"detail": "الطلب مرفوض"})

    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "connect-src 'self' https://en.wikipedia.org https://*.wikipedia.org https://www.wikidata.org; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "frame-ancestors 'self'; "
        "img-src 'self' data: https:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _frontend_index_path() -> Path:
    return FRONTEND_DIST_DIR / "index.html"


def _safe_frontend_path(full_path: str) -> Path | None:
    requested = (FRONTEND_DIST_DIR / full_path).resolve()
    try:
        requested.relative_to(FRONTEND_DIST_DIR.resolve())
    except ValueError:
        return None

    if requested.is_file():
        return requested

    return None


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> Response:
    index_path = _frontend_index_path()
    if not index_path.exists():
        return Response("Frontend build is not ready.", media_type="text/plain", status_code=503)
    return FileResponse(index_path)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str) -> Response:
    if full_path == "health" or full_path == "api" or full_path.startswith("api/"):
        return Response(status_code=404)

    for legacy_slug in ("minu", "menu"):
        if full_path == legacy_slug:
            return RedirectResponse(url="/")
        if full_path.startswith(f"{legacy_slug}/"):
            suffix = full_path.removeprefix(legacy_slug).lstrip("/")
            return RedirectResponse(url=f"/{suffix}" if suffix else "/")

    index_path = _frontend_index_path()
    if not index_path.exists():
        return Response("Frontend build is not ready.", media_type="text/plain", status_code=503)

    requested_file = _safe_frontend_path(full_path)
    if requested_file is not None:
        return FileResponse(requested_file)

    return FileResponse(index_path)
