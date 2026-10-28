"""Only TFT feature registration changes here."""

from fastapi import APIRouter

from backend.api.tft.projects.routes import router as projects
from backend.api.tft.rfp.routes import router as rfp

router = APIRouter()
router.include_router(projects)
router.include_router(rfp)
