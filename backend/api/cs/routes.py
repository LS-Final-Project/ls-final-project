"""Only C/S feature registration changes here."""

from fastapi import APIRouter

from backend.api.cs.tickets.routes import router as tickets
from backend.api.cs.units.routes import router as units
from backend.api.cs.workflow.routes import router as workflow

router = APIRouter()
router.include_router(tickets)
router.include_router(units)
router.include_router(workflow)
