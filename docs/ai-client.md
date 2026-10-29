# 공통 AI 클라이언트 사용

기본 제공자는 API 키로 호출하는 Gemini API, 초기 모델은 gemini-3.6-flash다. 최종 업무 모델 선정은 평가 결과를 따른다. 공통 모듈은 연결, 구조화 출력과 사용량 처리를 제공하며 업무 프롬프트 및 규칙은 각 담당 모듈에 둔다.

## 생성 호출

```python
from pydantic import BaseModel

from backend.shared.ai import AISettings, create_client, generate


class ConnectionResult(BaseModel):
    result: str


settings = AISettings(_env_file=".env.integrations")  # 로컬에서만 파일을 지정한다.
with create_client(settings) as client:
    answer = generate(
        client,
        settings,
        'Return JSON {"result":"ready"}.',
        response_schema=ConnectionResult,
    )
    parsed = ConnectionResult.model_validate_json(answer.text)
    usage = answer.usage
```

실행 설정은 [외부 연동 점검](integration-checks.md)을 따른다. 로컬은 GEMINI_API_KEY를 사용하고 Cloud Run의 C/S와 TFT는 Secret Manager의 gemini-api-key:1을 환경 변수로 받는다. 키는 서버에만 두며 gcloud ADC 준비는 필요 없다. fake 개발에서는 키를 요구하지 않고 실제 클라이언트를 만드는 시점에만 누락을 거부한다.

AI_PROVIDER=gemini, GEMINI_MODEL과 GEMINI_MAX_OUTPUT_TOKENS를 설정하면 같은 호출 코드로 모델을 비교할 수 있다. SDK는 실제 실행 시 생성하고 수명이 끝나면 close한다. 현재 generate와 embed는 동기 호출이므로 async 라우트에서 사용할 때는 스레드 실행 등으로 이벤트 루프를 막지 않도록 담당자가 통합한다. 앱 시작과 상태 점검에서 실제 모델을 호출하지 않는다.

response_schema는 SDK의 JSON 출력 요청이며, 반환 결과는 담당 기능의 Pydantic 모델로 다시 검증한다. 빈 응답이나 잘못된 스키마도 성공 처리하지 않는다. usage에는 입력, 답변, 추론, 캐시 입력과 총 토큰이 있고 미제공 값은 None이다. 추론 토큰 수만 기록하며 내부 추론 내용과 원문 오류는 로그에 저장하지 않는다.

## 사용량·시간·비용 (KAN-87)

- `Usage.raw`는 공급자 `usageMetadata`의 토큰 수를 응답 키 이름 그대로 담는다(입력·답변 원문은 담지 않는다). 정규화한 필드는 `input_tokens`·`output_tokens`·`thought_tokens`·`cached_input_tokens`·`total_tokens`이고, 공급자가 주지 않은 값은 None이다. 총합은 공급자 `total_tokens`만 쓰고 직접 더하지 않는다. 추론 토큰은 답변 토큰에 합치지 않는다.
- `billable_input_tokens`는 `input_tokens - cached_input_tokens`다. Gemini는 캐시를 쓰지 않은 호출에서 `cachedContentTokenCount`를 생략하므로(공급자 명세) 이때 캐시를 0으로 보고 계산한다. `cached_input_tokens`는 그대로 None이다. `input_tokens`가 없거나 캐시가 입력보다 크면(입력에 캐시가 포함된다는 명세와 어긋남) None이다.
- `Generation`과 `Embedding`에는 요청한 모델 ID `model`, SDK 호출 시간 `elapsed_ms`, 시도 횟수 `attempts`, 완료 시각 `finished_at`(UTC)이 붙는다. 자동 재시도가 없으므로(`HttpRetryOptions(attempts=1)`) `attempts`는 1이다.
- 단가표는 `backend/shared/ai_prices.json`이다. 저장소에는 빈 표 `{}`로 두며 수치를 추정해 넣지 않는다. 공식 가격표를 확인한 사람이 모델 ID마다 `input_per_million`·`output_per_million`·`cached_input_per_million`·`currency`·`as_of`(가격을 확인한 날짜)를 채운다.
  - 예: `{"MODEL_ID": {"input_per_million": "…", "output_per_million": "…", "cached_input_per_million": "…", "currency": "USD", "as_of": "YYYY-MM-DD"}}`
- `estimate_cost(usage, model, load_price_table())`는 항목별로 금액을 따로 계산한다. 청구 입력은 입력 단가, 캐시 입력은 캐시 단가, 답변과 추론은 각각 출력 단가를 쓴다. 같은 토큰을 두 번 세지 않는다.
  - 단가표에 모델이 없으면 모든 값이 None이다.
  - 토큰이 None인 항목은 None이다. `total`은 계산된 항목만 더하고, 하나도 없으면 None이다.
  - 금액은 반올림하지 않는다. 임베딩은 `Usage`가 없어 이 계산 대상이 아니다.

## 임베딩 호출

```python
from backend.shared.ai import AISettings, create_client, embed

settings = AISettings(_env_file=".env.integrations")
with create_client(settings, embedding=True) as client:
    result = embed(
        client,
        "gemini-embedding-001",
        "합성 연결 점검",
        task_type="RETRIEVAL_DOCUMENT",
    )
```

임베딩 모델과 production 차원은 평가 후 확정한다. 실제 수집에서는 담당자가 모델과 차원을 선택해 DB 인덱스와 일치시키고 dimensions에 명시한다. 빈 벡터, 비유한 값과 요청 차원 불일치는 실패다. 제공자가 잘림 메타데이터를 반환하면 함께 검사한다. Gemini API는 auto_truncate 옵션을 받지 않으므로 수집 담당자가 선택 모델의 입력 토큰 한도에 맞춰 청킹 및 길이 검사를 적용한다.

모델 교체 비교는 같은 합성 입력과 설정으로 `uv run --frozen python -m eval.smoke --provider gemini --env-file .env --model MODEL_ID`를 실행하고 생성된 JSON 및 HTML을 확인한다. 외부 호출의 자동 재시도와 제공자 자동 전환은 없다. 실제 문의 분류와 검색 품질 평가는 별도 담당 작업이며 최종 test 597건을 연결 점검에 쓰지 않는다.

## C/S 분류 단일 시도 (KAN-84 일부)

`backend/engine/cs/classification_call.py`의 `run_classification_attempt`는 KAN-103의
`build_input`과 원시 5필드 검사를 공통 `Generation`·`Usage`에 연결한다. 호출자는 정규화된
record, 명시적 sensitive_values·allowed_info_codes, 1 이상의 attempt_number와
`Callable[[ClassificationInput], Generation]` 제공자를 전달한다. 제공자는 정제된 네 필드만
받는다. 과제별 프롬프트·모델 설정은 제공자 측 책임이며, 이 함수는 SDK나 기본 모델을 만들지 않는다.
현재 연결 검증은 단일 호출 회귀와 아래 고정 C/S 합성 worker 프로필의 fake로 수행한다.

- 입력 실패는 `HELD`와 고정 오류 코드이며 호출하지 않는다. hash·제거 건수·호출 지연은 null이다.
- 입력 통과 후 제공자를 한 번 호출한다. 정제 입력 JSON을 UTF-8, 키 정렬, 공백 없는 구분자로
  직렬화한 `input_sha256`, 입력 판본과 PHONE 주석 제거 건수를 남긴다(제거는 분석용
  `strip_phone_annotation=True`일 때만 하며 기본은 0이다). hash는 제공자 측
  시스템 프롬프트나 전체 HTTP 요청의 hash가 아니며 구성/프롬프트 판본 고정을 대신하지 않는다.
- 응답은 원시 JSON 그대로 검사한다. `VALID`는 스키마 통과만 의미하며 의미상 정답이나 모델
  완료·품질 합격은 아니다. 잘못된/빈 응답은 `SCHEMA_VIOLATION`이고 응답 당시 사용량을 보존한다.
- 제공자 어댑터는 `ClassificationCallError`로 고정 코드(`CS_RATE_LIMITED`,
  `CS_PROVIDER_TIMEOUT`, `CS_PROVIDER_ERROR`), 알려진 HTTP 상태와 사용량을 전달할 수 있다.
  그 밖의 일반 예외는 원문·예외 이름을 복제하지 않고 `CS_PROVIDER_ERROR`로 기록한다.
  TimeoutError와 정규화된 타임아웃은 `TIMEOUT`, 나머지는 `TRANSPORT_FAILURE`다.
- 호출 중 예외는 `execution_uncertain=true`다. 이는 실패를 원격 미실행의 증거로 삼지 않기
  위한 보수적인 표시다. 알려진 사용량만 복사하고 미제공 토큰은 null로 유지한다. 금액은 계산하지 않는다.
- 자동 재시도·수선·폴백은 없다. 다음 시도는 외부 실행기가 새 번호로 명시 호출하며, 기존 시도
  결과를 덮어쓰지 않는다. KeyboardInterrupt 등 중단은 상위 실행기로 전파한다.

반환값은 **메모리 내 내부 기록**이다. `raw_response`는 제공자의 미마스킹 응답이므로 일반
API·로그·DB에 그대로 내보내면 안 된다. repr에서는 원시 응답·예측·제공자 문자열 메타데이터를
숨기지만, `asdict` 같은 명시적 직렬화는 보호 수단이 아니다. 제한된 감사 저장이 필요할 때는
[평가 저장 계약](../contracts/platform/evaluations.md)의 마스킹 후 `raw_output` 저장·접근 권한을
별도로 구현해야 한다. 이 함수 자체에는 영속 저장과 비용 계산이 없다. 아래 합성 worker가 제한된 DB 저장을 수행한다.

실제 SDK/업무 API/외부 모델 worker에는 아직 연결하지 않았다. 연결 전 KAN-92의 입력·민감값 식별,
KAN-95의 코드북, 모델별 설정·실제 지원 검증, 호출 전 intent 영속화와 UNKNOWN 복구,
사용량 보존·취소·동시 시도 번호 할당을 확인해야 한다. 기존 synthetic worker의 DB transaction
안에 외부 호출을 넣는 방식으로 연결하지 않는다. 이 경계만으로 KAN-84 전체 완료가 되지 않는다.

KAN-86의 고정 합성 C/S 프로필은 이 시도를 기존 평가 worker의 잠금 안에서 실행하고
마스킹한 저장값을 연결한다. 세부 저장·실패·재개 경계는
[평가 계약의 고정 C/S 합성 저장 프로필](../contracts/platform/evaluations.md#고정-cs-합성-저장-프로필-kan-86)을 따른다.
외부 제공자 호출이나 범용 시도 저장 API가 아니며 실제 호출의 intent/UNKNOWN은 여전히 미구현이다.
