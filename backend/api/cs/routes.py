"""Only C/S feature registration changes here."""

from fastapi import APIRouter

from backend.api.cs.comparisons.routes import router as comparisons
from backend.api.cs.improvements.routes import router as improvements
from backend.api.cs.improvements.transition_routes import router as improvement_transitions
from backend.api.cs.stats.routes import router as stats
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
router.include_router(improvement_transitions)
router.include_router(comparisons)
router.include_router(stats)
