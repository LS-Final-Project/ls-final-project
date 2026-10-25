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

response_schema는 SDK의 JSON 출력 요청이며, 반환 결과는 담당 기능의 Pydantic 모델로 다시 검증한다. 빈 응답이나 잘못된 스키마도 성공 처리하지 않는다. usage에는 입력, 답변, 추론, 캐시 입력과 총 토큰이 있고 미제공 값은 None이다. 추론 토큰 수만 기록하며 내부 추론 내용과 원문 오류는 로그에 저장하지 않는다. 금액은 가격표를 등록한 후 계산한다.

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
