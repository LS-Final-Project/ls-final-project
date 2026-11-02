"""Standard application factories with only local provider/key and fixed HTTP routing injection."""

import json
import os
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from scripts.local_business.auth import (
    CS_ORIGIN,
    CS_SA,
    PLATFORM_ORIGIN,
    PLATFORM_SA,
    TFT_ORIGIN,
    TFT_SA,
    LocalIdentity,
)
from scripts.local_business.config import settings


def local_only():
    if os.environ.get("ENVIRONMENT") != "local" or "K_SERVICE" in os.environ:
        raise RuntimeError("LOCAL_RUNNER_REQUIRES_LOCAL_ENVIRONMENT")


def gate(state: Path, owner: str) -> bool:
    try:
        marker = state / "control" / "enabled.json"
        value = json.loads(marker.read_text())
        age = time.time() - marker.stat().st_mtime
        return (
            0 <= age < 5
            and value.get("mode") == "real"
            and value.get("owner") == owner
            and value.get("remaining_jobs", 0) > 0
        )
    except (OSError, ValueError, KeyError):
        return False


def job_settings():
    from backend.api.platform.jobs.cs import CsJobSettings
    from backend.api.platform.jobs.tft import TftJobSettings

    return (
        CsJobSettings(
            _env_file=None,
            enabled=True,
            service_url=PLATFORM_ORIGIN,
            invoker_email=CS_SA,
            processor_connected=True,
        ),
        TftJobSettings(
            _env_file=None,
            enabled=True,
            service_url=PLATFORM_ORIGIN,
            invoker_email=TFT_SA,
            processor_connected=True,
        ),
    )


def app_for(service: str, state: Path):
    local_only()
    config = settings(state)
    ports = config["ports"]
    identity = LocalIdentity(state, config["web_origin"])
    from backend.shared.settings import Settings

    common = Settings(
        _env_file=None,
        environment="local",
        service_name=service,
        dev_auth_enabled=False,
        business_relay_enabled=True,
        business_cs_url=CS_ORIGIN,
        business_tft_url=TFT_ORIGIN,
        business_platform_email=PLATFORM_SA,
        cs_manual_data_origin="demo",
        database_url=f"postgresql://{service}_app@127.0.0.1:5432/team4",
    )

    class ToPlatform(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            if str(request.url).split("/internal/", 1)[0] != PLATFORM_ORIGIN:
                raise RuntimeError("LOCAL_PLATFORM_TARGET_INVALID")
            request.url = request.url.copy_with(
                scheme="http", host="127.0.0.1", port=ports["platform"]
            )
            return await super().handle_async_request(request)

    if service == "platform":
        from backend.api.platform.auth.mailbox_settings import MailboxAuthSettings
        from backend.api.platform.evaluations.access import EvaluationAccessSettings
        from backend.api.platform.jobs.cs import configure_cs_jobs
        from backend.api.platform.jobs.sweep import JobsSweepSettings
        from backend.api.platform.jobs.tft import configure_tft_jobs
        from backend.api.platform.mail.send_worker import MailSendSettings
        from backend.api.platform.mail.worker import MailSyncSettings
        from backend.api.platform.main import create_app
        from backend.api.platform.notifications.push_settings import PushSettings
        from backend.api.platform.relay import forward

        cs, tft = job_settings()

        def off(cls):
            return cls(_env_file=None, enabled=False)

        app = create_app(
            common,
            identity.auth_settings(),
            evaluation_access=EvaluationAccessSettings(
                _env_file=None, local_admission_enabled=False
            ),
            mailbox_settings=off(MailboxAuthSettings),
            mail_sync_settings=off(MailSyncSettings),
            mail_send_settings=off(MailSendSettings),
            tft_job_settings=tft,
            cs_job_settings=cs,
            jobs_sweep_settings=off(JobsSweepSettings),
            push_settings=off(PushSettings),
        )
        configure_cs_jobs(app, cs, caller=identity.caller(PLATFORM_ORIGIN, CS_SA))
        configure_tft_jobs(app, tft, caller=identity.caller(PLATFORM_ORIGIN, TFT_SA))
        app.state.microsoft_oauth = identity.oauth()
        app.state.business_token_provider = lambda audience: identity.token(audience, PLATFORM_SA)

        def local_forward(method, url, headers, body):
            origin = next(
                (value for value in (CS_ORIGIN, TFT_ORIGIN) if url.startswith(value + "/")), None
            )
            if origin is None:
                raise HTTPException(503)
            target = (
                f"http://127.0.0.1:{ports['cs' if origin == CS_ORIGIN else 'tft']}"
                + url[len(origin) :]
            )
            return forward(method, target, headers, body)

        app.state.business_forwarder = local_forward

        @app.middleware("http")
        async def require_enabled_driver(request: Request, call_next):
            path = request.url.path
            paid = (
                path.endswith("/analysis")
                or path.endswith("/process")
                or path.endswith("/rewrite")
                or path.endswith("/exports")
            )
            if (
                request.method == "POST"
                and paid
                and not gate(state, "cs" if path.startswith("/api/v1/cs/") else "tft")
            ):
                return JSONResponse(
                    {
                        "error": {
                            "code": "LOCAL_REAL_DRIVER_DISABLED",
                            "message": "Start an explicit limited enable-real driver first.",
                        }
                    },
                    status_code=503,
                )
            return await call_next(request)
    elif service == "cs":
        from backend.api.cs.analysis.jobs import CsAnalysisSettings, configure_analysis_jobs
        from backend.api.cs.main import create_app
        from backend.api.cs.workflow.mail import CustomerMailSettings

        app = create_app(common, CustomerMailSettings(_env_file=None, enabled=False))
        configure_analysis_jobs(
            app,
            CsAnalysisSettings(_env_file=None, enabled=True, platform_url=PLATFORM_ORIGIN),
            token=lambda audience: identity.token(audience, CS_SA),
            transport=ToPlatform(),
        )
    elif service == "tft":
        from backend.api.tft.main import create_app
        from backend.api.tft.rfp.jobs import TftProcessingSettings, configure_processing_jobs

        processing = TftProcessingSettings(
            _env_file=None, enabled=True, platform_url=PLATFORM_ORIGIN
        )
        app = create_app(common, processing)
        configure_processing_jobs(
            app,
            processing,
            token=lambda audience: identity.token(audience, TFT_SA),
            transport=ToPlatform(),
        )
    else:
        raise ValueError("LOCAL_SERVICE_INVALID")
    if service in {"cs", "tft"}:
        app.state.platform_caller = identity.caller(
            CS_ORIGIN if service == "cs" else TFT_ORIGIN, PLATFORM_SA
        )
    return app


def main():
    local_only()
    state = Path(os.environ["LOCAL_BUSINESS_STATE"])
    service = os.environ["LOCAL_BUSINESS_SERVICE"]
    uvicorn.run(
        app_for(service, state),
        host="127.0.0.1",
        port=settings(state)["ports"][service],
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
