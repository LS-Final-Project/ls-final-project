"""KAN-89 LangSmith 추적 전송 차단. 환경 사전 판정과 Settings 시작 검증을 검사한다."""

import pytest
from pydantic import ValidationError

from backend.shared.settings import TRACING_BLOCKED, Settings
from backend.shared.tracing import TRACING_KEYS, TRACING_SWITCHES, tracing_requested

SECRET = "lsv2-fixture-secret"


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, False),
        ({"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "0"}, False),
        ({"LANGSMITH_API_KEY": "  "}, False),
        ({"LANGSMITH_ENDPOINT": "https://example.test"}, False),
        ({"LANGSMITH_TRACING": "true"}, True),
        ({"LANGCHAIN_TRACING_V2": " TRUE "}, True),
        ({"LANGSMITH_TRACING_V2": "1"}, True),
        ({"LANGCHAIN_TRACING": "True"}, True),
        ({"LANGSMITH_API_KEY": SECRET}, True),
        ({"LANGCHAIN_API_KEY": SECRET}, True),
    ],
)
def test_tracing_requested_reads_the_given_environment(env, expected):
    """스위치 참값이나 비어 있지 않은 API 키가 있으면 추적 요청으로 본다.

    Args:
        env: 환경 사전.
        expected: 기대 판정.

    Returns:
        None: 판정을 확인한다.
    """
    assert tracing_requested(env) is expected


def clear(monkeypatch):
    """추적 관련 환경 변수를 모두 지운다.

    Args:
        monkeypatch: pytest monkeypatch.

    Returns:
        None: 환경을 비운다.
    """
    for name in (*TRACING_SWITCHES, *TRACING_KEYS, "AI_TRACING_ALLOWED"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "tracing,environment,allowed,starts",
    [
        (None, "local", False, True),
        (None, "production", False, True),
        (("LANGSMITH_TRACING", "true"), "local", False, False),
        (("LANGSMITH_TRACING", "true"), "local", True, True),
        (("LANGSMITH_TRACING", "true"), "test", True, False),
        (("LANGSMITH_TRACING", "true"), "production", True, False),
        (("LANGCHAIN_API_KEY", SECRET), "local", False, False),
        (("LANGCHAIN_API_KEY", SECRET), "production", True, False),
    ],
)
def test_settings_block_tracing_unless_local_and_allowed(
    monkeypatch, tracing, environment, allowed, starts
):
    """추적이 요청되면 local에서 명시적으로 허용한 경우만 시작하고, 오류에 키 값을 내지 않는다.

    Args:
        monkeypatch: pytest monkeypatch.
        tracing: 설정할 (이름, 값) 또는 None.
        environment: ENVIRONMENT.
        allowed: ai_tracing_allowed.
        starts: 시작해야 하면 True.

    Returns:
        None: 시작·거부와 메시지를 확인한다.
    """
    clear(monkeypatch)
    if tracing:
        monkeypatch.setenv(*tracing)
    if starts:
        Settings(_env_file=None, environment=environment, ai_tracing_allowed=allowed)
        return
    with pytest.raises(ValidationError, match="KAN-89") as error:
        Settings(_env_file=None, environment=environment, ai_tracing_allowed=allowed)
    assert SECRET not in str(error.value) and TRACING_BLOCKED in str(error.value)


def test_allowed_flag_comes_from_environment_and_dotenv_is_not_tracing(monkeypatch, tmp_path):
    """AI_TRACING_ALLOWED는 환경에서 읽고, .env에만 있는 추적 변수는 프로세스 환경이 아니라 막지 않는다.

    Args:
        monkeypatch: pytest monkeypatch.
        tmp_path: 임시 디렉터리.

    Returns:
        None: 플래그 읽기와 .env 무시를 확인한다.
    """
    clear(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("AI_TRACING_ALLOWED", "true")
    assert Settings(_env_file=None, environment="local").ai_tracing_allowed is True
    clear(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"LANGSMITH_TRACING=true\nLANGSMITH_API_KEY={SECRET}\n", encoding="utf-8")
    assert Settings(_env_file=dotenv, environment="production").ai_tracing_allowed is False
