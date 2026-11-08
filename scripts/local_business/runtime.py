"""Standard application factories with only local provider/key and fixed HTTP routing injection."""

import contextlib
import json
import os
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from scripts.local_business import push as local_push
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
from scripts.local_business.mail import load as load_mail

UNLIMITED_PROTOCOL = 1
CONCURRENCY_PROTOCOL = 1


def local_only():
    if os.environ.get("ENVIRONMENT") != "local" or "K_SERVICE" in os.environ:
        raise RuntimeError("LOCAL_RUNNER_REQUIRES_LOCAL_ENVIRONMENT")


def gate(state: Path, owner: str) -> bool:
    try:
        marker = state / "control" / "enabled.json"
        value = json.loads(marker.read_text())
        age = time.time() - marker.stat().st_mtime
        if not isinstance(value, dict) or owner not in {"cs", "tft"}:
            return False
        limit_mode = value.get("limit_mode", "limited")
        remaining = value.get("remaining_jobs")
        admitted = (limit_mode == "limited" and type(remaining) is int and remaining > 0) or (
            limit_mode == "unlimited" and "remaining_jobs" in value and remaining is None
        )
        return (
            0 <= age < 5
            and value.get("mode") == "real"
            and value.get("owner") == owner
            and admitted
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
        push_keys = local_push.load(state, config)

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
            push_settings=local_push.api_settings(push_keys) if push_keys else off(PushSettings),
        )
        configure_cs_jobs(app, cs, caller=identity.caller(PLATFORM_ORIGIN, CS_SA))
        configure_tft_jobs(app, tft, caller=identity.caller(PLATFORM_ORIGIN, TFT_SA))
        if push_keys:
            # KAN-201 로컬 OS 알림: 운영 Scheduler·Job 대신 같은 push_worker를 짧은 주기로 돈다.
            inner_lifespan = app.router.lifespan_context

            @contextlib.asynccontextmanager
            async def push_lifespan(application):
                local_push.start_worker(common, push_keys)
                async with inner_lifespan(application) as value:
                    yield value

            app.router.lifespan_context = push_lifespan
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
        mail = load_mail(state)
        if mail is not None:
            from scripts.local_business.mail import configure_platform

            objects = Path(os.environ.get("TFT_DOCUMENT_ROOT", state / "files")) / ".mail-objects"
            configure_platform(app, mail, identity, ports, objects, config["web_origin"])

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
        from backend.api.cs.faults.suggestion import (
            CsFaultSuggestionSettings,
            configure_fault_suggestion,
        )
        from backend.api.cs.improvements.ai_draft import (
            CsImprovementDraftSettings,
            configure_improvement_draft,
        )
        from backend.api.cs.main import create_app
        from backend.api.cs.reply_analysis.routes import (
            CsReplyAnalysisSettings,
            configure_reply_analysis,
        )
        from backend.api.cs.summary.routes import CsSummarySettings, configure_summary
        from backend.api.cs.translation.routes import (
            CsTranslationSettings,
            configure_translation,
        )
        from backend.api.cs.workflow.mail import CustomerMailSettings
        from scripts.local_business.summary import bridge_provider

        app = create_app(common, CustomerMailSettings(_env_file=None, enabled=False))
        mail = load_mail(state)
        if mail is not None and mail.get("send"):
            from backend.api.cs.workflow.mail import configure_customer_mail

            # Approved replies go to the local platform send worker, which applies the allow list.
            configure_customer_mail(
                app,
                CustomerMailSettings(_env_file=None, enabled=True, platform_url=PLATFORM_ORIGIN),
                token=lambda audience: identity.token(audience, CS_SA),
                transport=ToPlatform(),
            )
        # No model key/client in the API. Cache reads and reuse work without a driver.
        configure_summary(
            app, CsSummarySettings(_env_file=None, enabled=True), provider=bridge_provider(state)
        )
        configure_improvement_draft(
            app,
            CsImprovementDraftSettings(_env_file=None, enabled=True),
            provider=bridge_provider(state, "/improvement-draft"),
        )
        configure_translation(
            app,
            CsTranslationSettings(_env_file=None, enabled=True),
            provider=bridge_provider(state, "/translation"),
        )
        configure_reply_analysis(
            app,
            CsReplyAnalysisSettings(_env_file=None, enabled=True),
            provider=bridge_provider(state, "/reply-analysis"),
        )
        configure_fault_suggestion(
            app,
            CsFaultSuggestionSettings(_env_file=None, enabled=True),
            provider=bridge_provider(state, "/fault-suggestion"),
        )
        configure_analysis_jobs(
            app,
            CsAnalysisSettings(_env_file=None, enabled=True, platform_url=PLATFORM_ORIGIN),
            token=lambda audience: identity.token(audience, CS_SA),
            transport=ToPlatform(),
        )
    elif service == "tft":
        from backend.api.tft.main import create_app
        from backend.api.tft.rfp.jobs import TftProcessingSettings, configure_processing_jobs
        from backend.api.tft.rfp.routes_questions import TftReplySettings

        processing = TftProcessingSettings(
            _env_file=None, enabled=True, platform_url=PLATFORM_ORIGIN
        )
        # KAN-224: 회신 AI 읽기는 상태 settings.json의 "reply_ai": true일 때만 worker 작업으로 접수한다.
        reply = TftReplySettings(_env_file=None, enabled=config.get("reply_ai") is True)
        app = create_app(common, processing, reply)
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
