"""One explicit bounded UI-job driver. Runtime DB remains the only job/dispatch ledger."""

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
from scripts.local_business.permit import Denied, Permit
from scripts.local_business.runtime import job_settings, local_only

KINDS = {"cs": ["cs.analyze"], "tft": ["rfp.process", "rfp.draft_rewrite", "rfp.export"]}


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


def main():
    local_only()
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
    child = None

    def stop(_signal, _frame):
        nonlocal stopped
        stopped = True
        if child is not None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    stage = "LOCAL_DRIVER_CORPUS_INIT_FAILED"
    try:
        if owner == "cs":
            corpus_check(credentials["local_cs_worker"])
        # Never resume anything queued/running before this explicit driver starts.
        stage = "LOCAL_DRIVER_DATABASE_INIT_FAILED"
        with psycopg.connect(credentials["platform_app"]) as conn:
            after = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        stage = "LOCAL_DRIVER_PERMIT_INIT_FAILED"
        with permit.transaction() as db:
            limit = permit.driver(db, token)["job_limit"]
        used = 0
        stage = "LOCAL_DRIVER_FAILED"
        while not stopped and used < limit:
            save(
                marker,
                {
                    "mode": "real",
                    "owner": owner,
                    "started_after": after.isoformat(),
                    "remaining_jobs": limit - used,
                },
            )
            row = candidate(settings, owner, after)
            if row is None:
                time.sleep(0.5)
                continue
            job = str(row["id"])
            permit.grant(token, job)
            used += 1
            bundle = asyncio.run(claim_send(settings, job))
            env = {
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
                "DATABASE_URL": credentials[
                    "local_cs_worker" if owner == "cs" else "local_tft_worker"
                ],
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
            log = (control / ("worker-" + job + ".log")).open("ab")
            child = subprocess.Popen(
                [sys.executable, "-m", "scripts.local_business.worker"],
                env=env,
                cwd="/local/run",
                stdout=log,
                stderr=log,
            )
            asyncio.run(sent(settings, bundle, True))
            started = time.monotonic()
            while child.poll() is None and not stopped:
                save(
                    marker,
                    {
                        "mode": "real",
                        "owner": owner,
                        "started_after": after.isoformat(),
                        "remaining_jobs": limit - used,
                    },
                )
                if time.monotonic() - started > 1800:
                    child.terminate()
                    stopped = True
                time.sleep(0.5)
            if stopped:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                raise Denied("LOCAL_WORKER_INTERRUPTED_REVIEW_REQUIRED")
            code = child.wait()
            child = None
            if code:
                raise Denied("LOCAL_WORKER_FAILED_REVIEW_REQUIRED")
            with permit.transaction() as db:
                permit.outstanding(db)
    except Exception as error:
        # Never log a DSN/provider body. Safe reason is a fixed locally generated code only.
        reason = str(error) if isinstance(error, Denied) else stage
        save(
            control / "driver-result.json",
            {"reason": reason, "unfinished_jobs_require_review": True},
        )
        raise SystemExit(reason) from None
    finally:
        marker.unlink(missing_ok=True)
        permit.close_driver(token)


if __name__ == "__main__":
    main()
