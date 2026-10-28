from fastapi import FastAPI

from backend.api.common.application import create_application
from backend.api.platform.auth.http import configure_auth
from backend.api.platform.auth.mailbox_http import configure_mailbox_auth
from backend.api.platform.auth.mailbox_settings import MailboxAuthSettings
from backend.api.platform.auth.routes import dev_router
from backend.api.platform.auth.settings import MicrosoftAuthSettings
from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.mail.send_http import router as mail_send_user_router
from backend.api.platform.mail.send_worker import MailSendSettings, configure_mail_send
from backend.api.platform.mail.send_worker import router as mail_send_router
from backend.api.platform.mail.worker import MailSyncSettings, configure_mail_sync
from backend.api.platform.mail.worker import router as mail_worker_router
from backend.api.platform.relay import configure_relay
from backend.api.platform.routes import router
from backend.shared.settings import Settings


def create_app(
    settings: Settings | None = None,
    auth_settings: MicrosoftAuthSettings | None = None,
    evaluation_access: EvaluationAccessSettings | None = None,
    mailbox_settings: MailboxAuthSettings | None = None,
    mail_sync_settings: MailSyncSettings | None = None,
    mail_send_settings: MailSendSettings | None = None,
) -> FastAPI:
    """플랫폼 앱과 기본 비활성 Microsoft 로그인 설정을 준비한다.

    Args:
        settings: 공통 런타임 설정. 없으면 환경에서 읽는다.
        auth_settings: Microsoft 로그인 설정. 없으면 전용 접두사 환경에서 읽는다.
        evaluation_access: 지정 평가자 UUID 설정. 없으면 서버 환경에서 읽는다.
        mailbox_settings: 기본 비활성 사서함 HTTP·전용 키 설정.
        mail_sync_settings: 기본 비활성 메일 수집 워커 설정(KAN-72).
        mail_send_settings: 기본 비활성 승인 메일 발송 설정(KAN-74).

    Returns:
        FastAPI: 로그인 계약과 기존 개발 경로를 포함한 플랫폼 앱.
    """
    app = create_application("platform", settings)
    configure_auth(app, auth_settings or MicrosoftAuthSettings())
    configure_mailbox_auth(app, mailbox_settings or MailboxAuthSettings())
    configure_mail_sync(app, mail_sync_settings or MailSyncSettings())
    configure_mail_send(app, mail_send_settings or MailSendSettings())
    app.state.evaluation_access = evaluation_access or EvaluationAccessSettings()
    app.include_router(router)
    app.include_router(mail_worker_router)
    app.include_router(mail_send_router)
    app.include_router(mail_send_user_router)
    if app.state.settings.dev_auth_enabled and app.state.settings.environment in {"local", "test"}:
        app.include_router(dev_router)
    configure_relay(app)
    return app
