"""Only platform feature registration changes here."""

from fastapi import APIRouter

from backend.api.platform.auth.http import router as login
from backend.api.platform.auth.routes import router as auth
from backend.api.platform.evaluations.routes import router as evaluations

router = APIRouter()
router.include_router(auth)
router.include_router(login)
router.include_router(evaluations)
