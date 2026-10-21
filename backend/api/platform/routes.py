"""Only platform feature registration changes here."""

from fastapi import APIRouter

from backend.api.platform.auth.routes import router as auth

router = APIRouter()
router.include_router(auth)
