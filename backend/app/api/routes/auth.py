from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.auth import auth_manager
from app.schemas import AuthLoginRequest, AuthSessionRead

router = APIRouter()


@router.get("/session", response_model=AuthSessionRead)
def read_session(request: Request, response: Response) -> AuthSessionRead:
    username = auth_manager.get_current_username(request, raise_on_missing=False)
    response.headers["Cache-Control"] = "no-store"
    return AuthSessionRead(
        authenticated=bool(username),
        username=username,
    )


@router.post("/login", response_model=AuthSessionRead)
def login(payload: AuthLoginRequest, request: Request, response: Response) -> AuthSessionRead:
    if not auth_manager.verify_login(payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="اليوزر أو الباس غلط",
        )

    username = auth_manager.get_material().username
    auth_manager.set_session_cookie(response, request, username)
    response.headers["Cache-Control"] = "no-store"
    return AuthSessionRead(authenticated=True, username=username)


@router.post("/logout", response_model=AuthSessionRead)
def logout(response: Response) -> AuthSessionRead:
    auth_manager.clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return AuthSessionRead(authenticated=False, username=None)
