"""One explicit UI-job driver with optional caps. Runtime DB is the only job/dispatch ledger."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs import dispatch
from backend.shared.settings import Settings
from scripts.local_business.config import policy_mapping, save
from scripts.local_business.permit import Busy, Denied, Permit
from scripts.local_business.runtime import job_settings, local_only

KINDS = {
    "cs": ["cs.analyze"],
    "tft": ["rfp.process", "rfp.draft_rewrite", "rfp.export", "rfp.context"],
}
UNLIMITED_PROTOCOL = 1
CONCURRENCY_PROTOCOL = 1


def candidate(settings, owner, after):
    with psycopg.connect(settings.database_url.get_secret_value(), row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT j.id,j.owner FROM runtime.jobs j JOIN runtime.dispatch_outbox o ON o.job_id=j.id "
            "WHERE j.status='QUEUED' AND o.state='PENDING' AND o.attempt_no=0 "
            "AND j.kind=ANY(%s) AND j.created_at>=%s ORDER BY j.created_at,j.id LIMIT 1",
            (KINDS[owner], after),
        ).fetchone()


async def claim_send(settings, job_id):
    cs, tft = job_settings()
    access = EvaluationAccessSettings(_env_file=None, local_admission_enabled=False)
    limits = dispatch.DispatchSettings(enabled=True, max_attempts=1, lease_seconds=300)
    lease = await dispatch.claim_next(settings, access, limits, cs, tft, job_id=UUID(job_id))
    if lease is None or not await dispatch.begin_send(settings, access, limits, lease, cs, tft):
        raise Denied("LOCAL_DISPATCH_NOT_CLAIMED")
    return lease, access, limits, cs, tft


async def sent(settings, bundle, accepted):
    lease, access, limits, cs, tft = bundle
    # No fabricated Google operation. ACCEPTED means the actual local child was started.
    await dispatch.record_result(
        settings, access, limits, lease, "ACCEPTED" if accepted else "UNKNOWN", cs=cs, tft=tft
    )


def corpus_check(dsn: str):
    from backend.engine.search.chunking import INDEX_VERSION

    mapping = policy_mapping(Path("/local/corpus/cs-index-execute.json"))
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        for sha in mapping.values():
            if not conn.execute(
                "SELECT EXISTS(SELECT 1 FROM cs.document_chunks WHERE document_sha256=%s AND index_version=%s)",
                (sha, INDEX_VERSION),
            ).fetchone()[0]:
                raise Denied("LOCAL_POLICY_CHUNKS_NOT_LOADED")


def worker_environment(state, control, token, owner, job, credentials):
    return {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": "/workspace",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/tmp",
        "ENVIRONMENT": "local",
        "LOCAL_BUSINESS_MODE": "real",
        "LOCAL_BUSINESS_STATE": str(state),
        "LOCAL_BUSINESS_CONTROL": str(control),
        "LOCAL_DRIVER_TOKEN": token,
        "LOCAL_JOB_OWNER": owner,
        "LOCAL_JOB_ID": job,
        "LOCAL_MODEL_KEY_FILE": "/run/secrets/gemini-api-key",
        "LOCAL_PRICES_FILE": "/local/prices.json",
        "DATABASE_URL": credentials["local_cs_worker" if owner == "cs" else "local_tft_worker"],
        "SERVICE_NAME": owner,
        "CS_WORKER_ENABLED": "1",
        "TFT_WORKER_ENABLED": "1",
        "CS_JOB_ID": job,
        "TFT_JOB_ID": job,
        "CS_WORKER_LEASE_SECONDS": "900",
        "TFT_WORKER_LEASE_SECONDS": "900",
        "CS_VECTOR_SEARCH_ENABLED": "false",
        "CS_POLICY_DOCUMENTS_FILE": "/local/corpus/cs-index-execute.json",
        "TFT_DOCUMENT_ROOT": "/local/files",
        "GEMINI_TRANSIENT_RETRIES": "0",
        "GEMINI_MODEL": os.environ["LOCAL_MODEL"],
        "TFT_EXTRACTION_MODEL": os.environ["LOCAL_MODEL"],
        "TFT_DRAFT_REWRITE_MODEL": os.environ["LOCAL_MODEL"],
        "TFT_EXTRACTION_PROVIDER": "gemini",
        "TFT_DRAFT_REWRITE_PROVIDER": "gemini",
    }


def main():
    local_only()
    raw_timeout = os.environ.get("LOCAL_WORKER_TIMEOUT_SECONDS", "1800")
    if not raw_timeout.isascii() or not raw_timeout.isdecimal() or int(raw_timeout) <= 0:
        raise ValueError("LOCAL_WORKER_TIMEOUT_INVALID")
    worker_timeout = int(raw_timeout)
    state = Path(os.environ["LOCAL_BUSINESS_STATE"])
    control = Path(os.environ["LOCAL_BUSINESS_CONTROL"])
    owner = os.environ["LOCAL_JOB_OWNER"]
    if owner not in KINDS:
        raise ValueError("LOCAL_OWNER_INVALID")
    token = os.environ["LOCAL_DRIVER_TOKEN"]
    credentials = json.loads(Path("/local/secrets/worker-db.json").read_text())
    settings = Settings(
        _env_file=None,
        environment="local",
        service_name="platform",
        database_url=credentials["platform_app"],
    )
    permit = Permit(control / "permit.sqlite3")
    marker = control / "enabled.json"
    stopped = False
    # Each started process remains owned here until wait() and log.close() finish.
    children = {}
    failure = None

    def stop(_signal, _frame):
        nonlocal stopped
        stopped = True

    def halt(reason):
        nonlocal failure
        if failure is None:
            failure = reason
            marker.unlink(missing_ok=True)
            permit.halt(token)

    def finish(job):
        child, log, _started = children[job]
        code = child.wait()
        log.close()
        del children[job]
        permit.finish_job(token, job)
        return code

    def terminate_job(job):
        child, log, _started = children[job]
        try:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        finally:
            log.close()
        del children[job]
        permit.finish_job(token, job)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    stage = "LOCAL_DRIVER_CORPUS_INIT_FAILED"
    try:
        if owner == "cs":
            corpus_check(credentials["local_cs_worker"])
        stage = "LOCAL_DRIVER_DATABASE_INIT_FAILED"
        with psycopg.connect(credentials["platform_app"]) as conn:
            after = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        stage = "LOCAL_DRIVER_PERMIT_INIT_FAILED"
        with permit.transaction() as db:
            permission = permit.driver(db, token)
            limit_mode = permission["limit_mode"]
            limit = None if limit_mode == "unlimited" else permission["job_limit"]
            used = db.execute("SELECT count(*) FROM grants WHERE token=?", (token,)).fetchone()[0]

        def heartbeat(permission):
            save(
                marker,
                {
                    "mode": "real",
                    "owner": owner,
                    "started_after": after.isoformat(),
                    "limit_mode": limit_mode,
                    "remaining_jobs": None if limit is None else limit - used,
                    "active_jobs": len(children),
                    "concurrent_jobs": permission["concurrent_jobs"],
                    "concurrent_calls": permission["concurrent_calls"],
                },
            )

        stage = "LOCAL_DRIVER_FAILED"
        while True:
            if stopped:
                if children:
                    halt("LOCAL_WORKER_INTERRUPTED_REVIEW_REQUIRED")
                break
            for job, (child, _log, started) in list(children.items()):
                code = child.poll()
                if code is not None:
                    if code:
                        halt("LOCAL_WORKER_FAILED_REVIEW_REQUIRED")
                    finish(job)
                elif time.monotonic() - started > worker_timeout:
                    halt("LOCAL_WORKER_INTERRUPTED_REVIEW_REQUIRED")
                    terminate_job(job)
            if stopped:
                if children:
                    halt("LOCAL_WORKER_INTERRUPTED_REVIEW_REQUIRED")
                break
            if failure is not None:
                # halt() denies new SDK reservations, but existing calls can settle.
                # Keep original per-worker deadlines while the other jobs drain.
                if not children:
                    break
                time.sleep(0.5)
                continue
            try:
                permit.check_ready(token)
                with permit.transaction() as db:
                    permission = permit.driver(db, token)
                    concurrency = permission["concurrent_jobs"]
            except Denied as error:
                halt(str(error))
                continue
            heartbeat(permission)
            if limit is not None and used >= limit:
                if not children:
                    break
                time.sleep(0.5)
                continue
            if len(children) >= concurrency:
                time.sleep(0.5)
                continue
            try:
                row = candidate(settings, owner, after)
            except Exception:
                halt(stage)
                continue
            if row is None:
                time.sleep(0.5)
                continue
            if stopped:
                continue
            job = str(row["id"])
            try:
                permit.register_job(token, job)
            except Busy:
                time.sleep(0.5)
                continue
            except Denied as error:
                halt(str(error))
                continue
            bundle = None
            try:
                permit.grant(token, job)
                used += 1
                bundle = asyncio.run(claim_send(settings, job))
                log = (control / ("worker-" + job + ".log")).open("ab")
                try:
                    child = subprocess.Popen(
                        [sys.executable, "-m", "scripts.local_business.worker"],
                        env=worker_environment(state, control, token, owner, job, credentials),
                        cwd="/local/run",
                        stdout=log,
                        stderr=log,
                    )
                except BaseException:
                    log.close()
                    raise
                children[job] = (child, log, time.monotonic())
                permit.register_worker(token, job, child.pid)
                heartbeat(permission)
                asyncio.run(sent(settings, bundle, True))
            except Exception as error:
                # A failed/uncertain dispatch stops admissions, not the already
                # running SDK calls in other jobs. Those retain their own deadlines.
                halt(str(error) if isinstance(error, Denied) else stage)
                if job not in children:
                    permit.finish_job(token, job)
                    if bundle is not None:
                        try:
                            asyncio.run(sent(settings, bundle, False))
                        except Exception:
                            pass  # Keep the runtime SENDING/uncertain result for review.
        if failure is not None:
            raise Denied(failure)
    except Exception as error:
        reason = str(error) if isinstance(error, Denied) else stage
        # All failures stop new calls before cleanup. No provider payload or DSN is logged.
        try:
            halt(reason)
        except Exception:
            marker.unlink(missing_ok=True)
        save(
            control / "driver-result.json",
            {
                "reason": reason,
                "unfinished_jobs_require_review": True,
            },
        )
        raise SystemExit(reason) from None
    finally:
        marker.unlink(missing_ok=True)
        # Explicit stop/timeout/exception: reap every child before closing permission.
        # Reservations remain untouched when a process cannot finish its call.
        for child, _log, _started in children.values():
            try:
                child.terminate()
            except ProcessLookupError:
                pass
        for job in list(children):
            terminate_job(job)
        permit.close_driver(token)


if __name__ == "__main__":
    main()
