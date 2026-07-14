from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Request, Response, status

from app.core.runtime import get_runtime_paths

PASSWORD_HASH_ITERATIONS = 310_000
DEFAULT_USERNAME = "minu-admin"
DEFAULT_SESSION_COOKIE = "minu_session"
DEFAULT_SESSION_HOURS = 24
CARD_LINK_TTL_MINUTES = 15
DEFAULT_MATCH_LINK_HOURS = 24


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _hash_password(password: str, *, salt: bytes | None = None) -> str:
    chosen_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        chosen_salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}"
        f"${_urlsafe_b64encode(chosen_salt)}${_urlsafe_b64encode(digest)}"
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        _, iteration_text, salt_text, digest_text = stored_hash.split("$", 3)
        iterations = int(iteration_text)
        salt = _urlsafe_b64decode(salt_text)
        expected_digest = _urlsafe_b64decode(digest_text)
    except (TypeError, ValueError):
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual_digest, expected_digest)


@dataclass(frozen=True)
class AuthMaterial:
    username: str
    password_hash: str
    session_secret: str
    session_cookie_name: str
    session_ttl_seconds: int
    credentials_file_path: Path
    secret_file_path: Path


class AuthManager:
    def __init__(self) -> None:
        runtime_paths = get_runtime_paths()
        self._secrets_dir = runtime_paths.root / "secrets"
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        self._secret_file = self._secrets_dir / "minu_auth.json"
        self._credentials_file = self._secrets_dir / "minu_admin_credentials.txt"

    def _generate_password(self) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        core = "".join(secrets.choice(alphabet) for _ in range(14))
        return f"Minu-{core}"

    def _build_material(
        self,
        *,
        username: str,
        password_hash: str,
        session_secret: str,
    ) -> AuthMaterial:
        session_hours_raw = os.getenv("MINU_SESSION_HOURS", "").strip()
        try:
            session_hours = max(1, int(session_hours_raw)) if session_hours_raw else DEFAULT_SESSION_HOURS
        except ValueError:
            session_hours = DEFAULT_SESSION_HOURS

        return AuthMaterial(
            username=username,
            password_hash=password_hash,
            session_secret=session_secret,
            session_cookie_name=os.getenv("MINU_SESSION_COOKIE", DEFAULT_SESSION_COOKIE).strip() or DEFAULT_SESSION_COOKIE,
            session_ttl_seconds=session_hours * 3600,
            credentials_file_path=self._credentials_file,
            secret_file_path=self._secret_file,
        )

    def _write_local_files(self, *, username: str, password: str, password_hash: str, session_secret: str) -> None:
        self._secret_file.write_text(
            json.dumps(
                {
                    "username": username,
                    "password_hash": password_hash,
                    "session_secret": session_secret,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._credentials_file.write_text(
            "\n".join(
                [
                    "Minu admin login",
                    f"username={username}",
                    f"password={password}",
                    "login_url=/",
                ]
            ),
            encoding="utf-8",
        )

    @lru_cache
    def get_material(self) -> AuthMaterial:
        env_username = os.getenv("MINU_ADMIN_USERNAME", "").strip()
        env_password = os.getenv("MINU_ADMIN_PASSWORD", "").strip()
        env_password_hash = os.getenv("MINU_ADMIN_PASSWORD_HASH", "").strip()
        env_session_secret = os.getenv("MINU_SESSION_SECRET", "").strip()

        if env_username and env_password and env_session_secret:
            return self._build_material(
                username=env_username,
                password_hash=_hash_password(env_password),
                session_secret=env_session_secret,
            )

        if env_username and env_password_hash and env_session_secret:
            return self._build_material(
                username=env_username,
                password_hash=env_password_hash,
                session_secret=env_session_secret,
            )

        if self._secret_file.exists():
            saved = json.loads(self._secret_file.read_text(encoding="utf-8"))
            return self._build_material(
                username=str(saved.get("username") or DEFAULT_USERNAME),
                password_hash=str(saved.get("password_hash") or ""),
                session_secret=str(saved.get("session_secret") or ""),
            )

        password = self._generate_password()
        username = DEFAULT_USERNAME
        password_hash = _hash_password(password)
        session_secret = secrets.token_urlsafe(48)
        self._write_local_files(
            username=username,
            password=password,
            password_hash=password_hash,
            session_secret=session_secret,
        )
        return self._build_material(
            username=username,
            password_hash=password_hash,
            session_secret=session_secret,
        )

    def verify_login(self, username: str, password: str) -> bool:
        material = self.get_material()
        normalized_username = username.strip()
        return normalized_username == material.username and _verify_password(password, material.password_hash)

    def create_session_token(self, username: str) -> str:
        material = self.get_material()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=material.session_ttl_seconds)
        payload = {
            "u": username,
            "exp": int(expires_at.timestamp()),
        }
        payload_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        payload_token = _urlsafe_b64encode(payload_text.encode("utf-8"))
        signature = hmac.new(
            material.session_secret.encode("utf-8"),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return f"{payload_token}.{_urlsafe_b64encode(signature)}"

    def create_card_token(self, payload: dict[str, object]) -> str:
        material = self.get_material()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=CARD_LINK_TTL_MINUTES)
        token_payload = {
            "p": payload,
            "exp": int(expires_at.timestamp()),
        }
        payload_text = json.dumps(token_payload, separators=(",", ":"), ensure_ascii=False)
        payload_token = _urlsafe_b64encode(payload_text.encode("utf-8"))
        signature = hmac.new(
            material.session_secret.encode("utf-8"),
            f"card:{payload_token}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return f"{payload_token}.{_urlsafe_b64encode(signature)}"

    def read_card_token(self, token: str) -> dict[str, object] | None:
        material = self.get_material()
        try:
            payload_token, signature_token = token.split(".", 1)
        except ValueError:
            return None

        expected_signature = hmac.new(
            material.session_secret.encode("utf-8"),
            f"card:{payload_token}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_urlsafe_b64encode(expected_signature), signature_token):
            return None

        try:
            payload = json.loads(_urlsafe_b64decode(payload_token).decode("utf-8"))
            content = payload["p"]
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

        if expires_at < int(datetime.now(timezone.utc).timestamp()):
            return None

        if not isinstance(content, dict):
            return None

        return content

    def create_match_token(self, payload: dict[str, object]) -> str:
        material = self.get_material()
        match_hours_raw = os.getenv("MINU_MATCH_LINK_HOURS", "").strip()
        try:
            match_hours = max(1, int(match_hours_raw)) if match_hours_raw else DEFAULT_MATCH_LINK_HOURS
        except ValueError:
            match_hours = DEFAULT_MATCH_LINK_HOURS

        expires_at = datetime.now(timezone.utc) + timedelta(hours=match_hours)
        token_payload = {
            "p": payload,
            "exp": int(expires_at.timestamp()),
        }
        payload_text = json.dumps(token_payload, separators=(",", ":"), ensure_ascii=False)
        payload_token = _urlsafe_b64encode(payload_text.encode("utf-8"))
        signature = hmac.new(
            material.session_secret.encode("utf-8"),
            f"match:{payload_token}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return f"{payload_token}.{_urlsafe_b64encode(signature)}"

    def read_match_token(self, token: str) -> dict[str, object] | None:
        material = self.get_material()
        try:
            payload_token, signature_token = token.split(".", 1)
        except ValueError:
            return None

        expected_signature = hmac.new(
            material.session_secret.encode("utf-8"),
            f"match:{payload_token}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_urlsafe_b64encode(expected_signature), signature_token):
            return None

        try:
            payload = json.loads(_urlsafe_b64decode(payload_token).decode("utf-8"))
            content = payload["p"]
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

        if expires_at < int(datetime.now(timezone.utc).timestamp()):
            return None

        if not isinstance(content, dict):
            return None

        return content

    def read_session_token(self, token: str) -> str | None:
        material = self.get_material()
        try:
            payload_token, signature_token = token.split(".", 1)
        except ValueError:
            return None

        expected_signature = hmac.new(
            material.session_secret.encode("utf-8"),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_urlsafe_b64encode(expected_signature), signature_token):
            return None

        try:
            payload = json.loads(_urlsafe_b64decode(payload_token).decode("utf-8"))
            username = str(payload["u"])
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

        if expires_at < int(datetime.now(timezone.utc).timestamp()):
            return None

        return username

    def is_authenticated(self, request: Request) -> bool:
        return self.get_current_username(request, raise_on_missing=False) is not None

    def get_current_username(self, request: Request, *, raise_on_missing: bool = True) -> str | None:
        material = self.get_material()
        token = request.cookies.get(material.session_cookie_name, "").strip()
        username = self.read_session_token(token) if token else None
        if username:
            return username

        if not raise_on_missing:
            return None

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="لازم تسجيل دخول",
        )

    def set_session_cookie(self, response: Response, request: Request, username: str) -> None:
        material = self.get_material()
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
        response.set_cookie(
            key=material.session_cookie_name,
            value=self.create_session_token(username),
            httponly=True,
            samesite="strict",
            secure=is_secure,
            max_age=material.session_ttl_seconds,
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        material = self.get_material()
        response.delete_cookie(
            key=material.session_cookie_name,
            path="/",
            httponly=True,
            samesite="strict",
        )


auth_manager = AuthManager()


def require_authenticated_user(request: Request) -> str:
    return auth_manager.get_current_username(request, raise_on_missing=True) or ""
