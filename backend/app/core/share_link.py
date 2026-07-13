from __future__ import annotations

import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from fastapi import Request

PUBLIC_URL_PATTERN = re.compile(r"https://[^\s]+")
CANONICAL_PUBLIC_SHARE_URL = "https://minu-theta.vercel.app"
CANONICAL_PUBLIC_SHARE_HOST = urlparse(CANONICAL_PUBLIC_SHARE_URL).netloc.lower()
SHARE_LOG_DIR = Path(tempfile.gettempdir()) / "authorized-network-assessment-logs"
SHARE_URL_FILES = (
    SHARE_LOG_DIR / "public-share-url.txt",
    SHARE_LOG_DIR / "cloudflared-public.out.log",
    SHARE_LOG_DIR / "cloudflared-public.err.log",
)


def _public_url_is_transient(value: str) -> bool:
    host = urlparse(value).netloc.lower()
    if not host:
        return False

    if re.search(r"trycloudflare\.com|lhr\.life|localhost\.run", host, re.IGNORECASE):
        return True

    return host.endswith(".vercel.app") and host != CANONICAL_PUBLIC_SHARE_HOST


def _public_url_is_live(value: str) -> bool:
    try:
        with urlopen(value, timeout=4) as response:
            sample = response.read(512).decode("utf-8", errors="ignore").lower()
    except (HTTPError, URLError, OSError, TimeoutError):
        return False

    return "no tunnel here" not in sample and "tunnel unavailable" not in sample


def read_public_share_url() -> str | None:
    for path in SHARE_URL_FILES:
        if not path.exists():
            continue

        content = path.read_text(encoding="utf-8", errors="ignore")
        candidates = [content.strip()] if path.suffix == ".txt" else PUBLIC_URL_PATTERN.findall(content)
        for candidate in reversed(candidates):
            normalized = candidate.strip().rstrip("/.,")
            if (
                normalized
                and "127.0.0.1" not in normalized
                and "localhost" not in normalized
                and not _public_url_is_transient(normalized)
                and _public_url_is_live(normalized)
            ):
                return normalized

    if _public_url_is_live(CANONICAL_PUBLIC_SHARE_URL):
        return CANONICAL_PUBLIC_SHARE_URL
    return None


def request_public_base_url(request: Request) -> str | None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    proto = forwarded_proto or request.url.scheme
    host = forwarded_host or request.headers.get("host", "").strip() or request.url.netloc
    if not proto or not host:
        return None

    candidate = f"{proto}://{host}".rstrip("/")
    if "127.0.0.1" in candidate or "localhost" in candidate:
        return None

    return candidate
