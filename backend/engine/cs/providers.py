"""엔진 입력을 실제 Gemini 호출(backend.shared.ai.generate)로 잇는 얇은 공급자 어댑터(KAN-144).

어댑터는 모델 ID와 프롬프트 판본을 닫힌 변수로 가져 후보 비교 하네스의 Candidate(name, provider,
model, prompt_version)에 그대로 꽂힌다. 비밀값(API 키)은 client 안에만 있고 어댑터가 쥔 설정에서는 지운다.
SDK 예외는 원문을 버리고 ClassificationCallError 코드로만 올린다(분류·초안 시도 경계가 같은 예외를 받는다).
429는 CS_RATE_LIMITED, 타임아웃은 CS_PROVIDER_TIMEOUT, 그 밖의 HTTP 오류는 상태와 함께 CS_PROVIDER_ERROR,
나머지는 CS_PROVIDER_ERROR다. 임베딩 검색기는 벡터 열이 없어 KAN-144 2차로 미룬다.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from backend.engine.cs.classification import ClassificationInput, ClassificationOutput
from backend.engine.cs.classification_call import CallErrorCode, ClassificationCallError
from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS, get_prompt
from backend.shared.ai import AISettings, Generation, failed_attempts, generate

MODEL = re.compile(r"[a-zA-Z0-9._-]+")
THINKING_LEVELS = frozenset(level.value for level in types.ThinkingLevel)


@dataclass(frozen=True)
class GenerationKnobs:
    """후보별 생성 조절 항목. None이면 보내지 않는다(설정 기본값·공급자 기본 thinking).

    max_output_tokens는 thinking 토큰을 포함한 출력 상한이다(실측: MAX_TOKENS로 끝난 응답이 thinking 980 + 답변
    25 ≈ 1024). 그래서 분류 JSON에도 1024는 부족할 수 있다. thinking_budget과 thinking_level은 함께 줄 수 없고,
    모델이 지원하지 않는 항목은 공급자 400(CS_PROVIDER_ERROR)으로 드러난다.
    """

    thinking_budget: int | None = None
    thinking_level: str | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        """값 범위와 동시 지정을 검사한다.

        Returns:
            None: 위반은 CS_INVALID_GENERATION ValueError다.
        """
        budget, level, limit = self.thinking_budget, self.thinking_level, self.max_output_tokens
        if (
            (budget is not None and level is not None)
            or (budget is not None and (type(budget) is not int or budget < -1))
            or (level is not None and level not in THINKING_LEVELS)
            or (limit is not None and (type(limit) is not int or not 1 <= limit <= 65536))
        ):
            raise ValueError("CS_INVALID_GENERATION")


def _settings(settings: AISettings, model: str) -> AISettings:
    """모델만 바꾸고 API 키를 지운 호출 설정을 만든다.

    Args:
        settings: 기본 AI 설정(출력 토큰 상한 등).
        model: 호출할 모델 ID.

    Returns:
        AISettings: gemini_model=model, gemini_api_key=None인 사본. 잘못된 모델 ID는 CS_INVALID_MODEL
            ValueError다.
    """
    if not isinstance(model, str) or not MODEL.fullmatch(model):
        raise ValueError("CS_INVALID_MODEL")
    return settings.model_copy(update={"gemini_model": model, "gemini_api_key": None})


def _call(
    client: genai.Client,
    settings: AISettings,
    prompt: str,
    response_schema: type[BaseModel] | None,
    generation: GenerationKnobs | None = None,
) -> Generation:
    """generate를 한 번 부르고 실패를 원문 없는 ClassificationCallError로 바꾼다.

    Args:
        client: create_client로 만든 SDK 클라이언트.
        settings: 호출 설정.
        prompt: 완성 프롬프트.
        response_schema: JSON 응답 스키마(없으면 텍스트).
        generation: 생성 조절 항목(없으면 기본값).

    Returns:
        Generation: 공급자 응답 그대로. 실패는 ClassificationCallError이며 원래 예외를 연결하지 않는다.
    """
    code: CallErrorCode = "CS_PROVIDER_ERROR"
    status: int | None = None
    attempts: int | None = None
    try:
        knobs = generation or GenerationKnobs()
        return generate(
            client,
            settings,
            prompt,
            response_schema=response_schema,
            thinking_budget=knobs.thinking_budget,
            thinking_level=knobs.thinking_level,
            max_output_tokens=knobs.max_output_tokens,
        )
    except errors.APIError as error:
        known = error.code if type(error.code) is int and 100 <= error.code <= 599 else None
        code, status = ("CS_RATE_LIMITED" if known == 429 else "CS_PROVIDER_ERROR"), known
        attempts = failed_attempts(error)
    except (httpx.TimeoutException, TimeoutError) as error:
        code, attempts = "CS_PROVIDER_TIMEOUT", failed_attempts(error)
    except Exception:
        pass
    # except 블록 밖에서 올려 원래 예외(요청·키·제공자 메시지)가 __context__로도 남지 않게 한다.
    raise ClassificationCallError(code, http_status=status, call_attempts=attempts)


def gemini_classification_provider(
    client: genai.Client,
    settings: AISettings,
    *,
    model: str,
    allowed_info_codes: frozenset[str],
    prompt_version: str = DEFAULT_VERSIONS["classification"],
    generation: GenerationKnobs | None = None,
) -> Callable[[ClassificationInput], Generation]:
    """분류 시도(run_classification_attempt)에 꽂을 Gemini 공급자를 만든다.

    Args:
        client: create_client로 만든 SDK 클라이언트(API 키는 여기에만 있다).
        settings: 기본 AI 설정.
        model: 호출할 모델 ID.
        allowed_info_codes: 프롬프트에 보여줄 허용 정보 코드(정렬해 넣는다).
        prompt_version: 프롬프트 레지스트리의 classification 판본. 기본은 DEFAULT_VERSIONS(채택 판본 v3)다.
        generation: thinking 예산·수준·출력 상한(thinking 포함). 분류 출력도 thinking을 포함한 상한이라 1024는
            부족할 수 있다.

    Returns:
        Callable[[ClassificationInput], Generation]: 판본 본문 + ClassificationOutput 스키마 + 허용 코드 +
            입력 JSON으로 부르는 공급자. 모르는 판본·잘못된 모델·코드는 만들 때 ValueError다.
    """
    body = get_prompt("classification", prompt_version)
    if not isinstance(allowed_info_codes, frozenset) or not all(
        isinstance(code, str) and code for code in allowed_info_codes
    ):
        raise ValueError("CS_INVALID_INPUT")
    configured = _settings(settings, model)
    instruction = (
        body
        + json.dumps(ClassificationOutput.model_json_schema(), ensure_ascii=False, sort_keys=True)
        + "\n허용 정보 코드: "
        + json.dumps(sorted(allowed_info_codes), ensure_ascii=False)
    )

    def provider(payload: ClassificationInput) -> Generation:
        """정제 입력 하나를 분류 프롬프트로 보낸다.

        Args:
            payload: build_input의 정제 입력.

        Returns:
            Generation: 공급자 응답. 실패는 ClassificationCallError다.
        """
        prompt = instruction + "\n\n입력:\n" + payload.model_dump_json()
        return _call(client, configured, prompt, ClassificationOutput, generation)

    return provider


def gemini_text_provider(
    client: genai.Client,
    settings: AISettings,
    *,
    model: str,
    response_schema: type[BaseModel] | None = None,
    generation: GenerationKnobs | None = None,
) -> Callable[[str], Generation]:
    """완성 프롬프트 문자열을 받는 엔진(초안·회신·개선)에 꽂을 Gemini 공급자를 만든다.

    Args:
        client: create_client로 만든 SDK 클라이언트.
        settings: 기본 AI 설정.
        model: 호출할 모델 ID.
        response_schema: 엔진 출력 스키마(예 DraftOutput). 판본은 엔진 render_prompt가 프롬프트에 넣는다.
        generation: thinking 예산·수준·출력 상한(thinking 포함).

    Returns:
        Callable[[str], Generation]: 프롬프트를 그대로 보내는 공급자. 실패는 ClassificationCallError다.
    """
    configured = _settings(settings, model)

    def provider(prompt: str) -> Generation:
        """엔진이 만든 프롬프트를 그대로 보낸다.

        Args:
            prompt: 엔진 render_prompt 결과.

        Returns:
            Generation: 공급자 응답.
        """
        return _call(client, configured, prompt, response_schema, generation)

    return provider
