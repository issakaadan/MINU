from fastapi import APIRouter

from app.api.routes import admin, auth, game

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(game.router, prefix="/game", tags=["game"])
api_router.include_router(game.public_router, prefix="/game", tags=["game-public"])
