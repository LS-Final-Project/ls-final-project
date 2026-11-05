"""LangSmith 추적 전송 요청 여부 판정(KAN-89). 전송 정책이 정해질 때까지 설정 검증이 이 판정으로 시작을 막는다."""

from collections.abc import Mapping

# langsmith.utils.get_env_var가 보는 이름(LANGSMITH_·LANGCHAIN_ 접두 × TRACING_V2·TRACING)과 API 키.
TRACING_SWITCHES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)
TRACING_KEYS = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")
TRACING_BLOCKED = "LangSmith tracing is disabled until the transfer policy is decided (KAN-89)"


def tracing_requested(env: Mapping[str, str]) -> bool:
    """환경에서 LangSmith 추적 전송이 켜질 수 있는지 본다.

    langsmith는 환경 변수를 lru_cache로 한 번만 읽으므로 그 판정 함수 대신 환경 사전을 직접 받는다.

    Args:
        env: 환경 변수 사전(보통 os.environ).

    Returns:
        bool: 추적 스위치가 참값('true'·'1', 대소문자·앞뒤 공백 무시)이거나 API 키가 비어 있지 않으면 True.
    """
    return any(
        env.get(name, "").strip().casefold() in {"true", "1"} for name in TRACING_SWITCHES
    ) or any(env.get(name, "").strip() for name in TRACING_KEYS)
