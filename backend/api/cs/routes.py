"""Only C/S feature registration changes here."""

from fastapi import APIRouter

from backend.api.cs.tickets.routes import router as tickets
from backend.api.cs.units.routes import router as units

router = APIRouter()
router.include_router(tickets)
router.include_router(units)
