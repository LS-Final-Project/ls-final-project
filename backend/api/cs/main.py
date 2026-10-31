from fastapi import FastAPI

from backend.api.common.application import create_application
from backend.api.cs.analysis.jobs import CsAnalysisSettings, configure_analysis_jobs
from backend.api.cs.routes import router
from backend.api.cs.summary.routes import CsSummarySettings, configure_summary
from backend.api.cs.workflow.mail import CustomerMailSettings, configure_customer_mail
from backend.engine.cs.rules import firmware_releases, load_policy
from backend.shared.settings import Settings


def create_app(
    settings: Settings | None = None, customer_mail: CustomerMailSettings | None = None
) -> FastAPI:
    """C/S 앱을 만들고 인증 중계 활성화 시 수동 문의 출처의 명시 설정을 요구한다.

    Args:
        settings: 공통 설정. 없으면 환경에서 읽는다.
        customer_mail: 기본 비활성 고객 메일 발송 설정(KAN-110). 없으면 환경에서 읽는다.

    Returns:
        FastAPI: 수동 문의 출처와 호출자 검증 설정을 확인한 C/S 앱.
    """
    # An edited or mismatched policy table stops the service here, not at the first ticket.
    load_policy()
    firmware_releases()
    app = create_application("cs", settings)
    if (
        app.state.settings.business_relay_enabled
        and app.state.settings.cs_manual_data_origin is None
    ):
        raise ValueError("Business relay requires an explicit CS_MANUAL_DATA_ORIGIN")
    configure_customer_mail(app, customer_mail or CustomerMailSettings())
    configure_analysis_jobs(app, CsAnalysisSettings())
    configure_summary(app, CsSummarySettings())
    app.include_router(router)
    return app
