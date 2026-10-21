"""Only C/S feature registration changes here."""

from fastapi import APIRouter

from backend.api.cs.tickets.routes import router as tickets

router = APIRouter()
router.include_router(tickets)
