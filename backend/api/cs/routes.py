"""Only C/S feature registration changes here."""

from fastapi import APIRouter

from backend.api.cs.improvements.routes import router as improvements
from backend.api.cs.tickets.routes import router as tickets
from backend.api.cs.units.routes import router as units
from backend.api.cs.workflow.routes import case_router
from backend.api.cs.workflow.routes import router as workflow

router = APIRouter()
router.include_router(tickets)
router.include_router(units)
router.include_router(workflow)
router.include_router(case_router)
router.include_router(improvements)
