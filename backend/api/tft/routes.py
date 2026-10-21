"""Only TFT feature registration changes here."""

from fastapi import APIRouter

from backend.api.tft.projects.routes import router as projects

router = APIRouter()
router.include_router(projects)
