"""Only C/S feature registration changes here."""

from fastapi import APIRouter

from backend.api.cs.analysis.routes import router as analysis
from backend.api.cs.assignments.routes import router as assignments
from backend.api.cs.comparisons.routes import router as comparisons
from backend.api.cs.faults.routes import router as faults
from backend.api.cs.improvements.ai_draft import router as improvement_ai_draft
from backend.api.cs.improvements.candidates import router as improvement_candidates
from backend.api.cs.improvements.followup_routes import router as improvement_followups
from backend.api.cs.improvements.routes import router as improvements
from backend.api.cs.improvements.transition_routes import router as improvement_transitions
from backend.api.cs.reply_analysis.routes import router as reply_analysis
from backend.api.cs.stats.routes import router as stats
from backend.api.cs.summary.routes import router as summary
from backend.api.cs.tickets.routes import router as tickets
from backend.api.cs.translation.routes import router as translation
from backend.api.cs.units.routes import router as units
from backend.api.cs.workflow.internal_routes import router as customer_mail_internal
from backend.api.cs.workflow.mail_routes import router as customer_mail
from backend.api.cs.workflow.routes import case_router
from backend.api.cs.workflow.routes import router as workflow

router = APIRouter()
router.include_router(tickets)
router.include_router(analysis)
router.include_router(summary)
router.include_router(translation)
router.include_router(reply_analysis)
router.include_router(units)
router.include_router(workflow)
router.include_router(case_router)
router.include_router(customer_mail)
router.include_router(customer_mail_internal)
router.include_router(improvements)
router.include_router(improvement_transitions)
router.include_router(improvement_followups)
router.include_router(improvement_candidates)
router.include_router(improvement_ai_draft)
router.include_router(comparisons)
router.include_router(faults)
router.include_router(stats)
router.include_router(assignments)
