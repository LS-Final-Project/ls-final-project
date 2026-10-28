"""Only platform feature registration changes here."""

from fastapi import APIRouter

from backend.api.platform.auth.http import router as login
from backend.api.platform.auth.mailbox_http import router as mailbox
from backend.api.platform.auth.routes import router as auth
from backend.api.platform.evaluations.routes import router as evaluations
from backend.api.platform.jobs.routes import router as jobs
from backend.api.platform.mail.links import router as mail_links
from backend.api.platform.mail.triage import router as mail_triage

router = APIRouter()
router.include_router(auth)
router.include_router(login)
router.include_router(mailbox)
router.include_router(mail_triage)
router.include_router(mail_links)
router.include_router(evaluations)
router.include_router(jobs)
