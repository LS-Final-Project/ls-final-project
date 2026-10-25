"""Only TFT feature registration changes here."""

from fastapi import APIRouter

from backend.api.tft.projects.routes import router as projects
from backend.api.tft.recommendations.routes import router as recommendations
from backend.api.tft.staffing_requests.routes import router as staffing_requests
from backend.api.tft.verification.routes import router as verification

router = APIRouter()
router.include_router(projects)
router.include_router(staffing_requests)
router.include_router(recommendations)
router.include_router(verification)
