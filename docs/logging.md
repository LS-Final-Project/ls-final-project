# 로그 규칙 (KAN-70)

세 서비스는 `backend/shared/logs.py`로 표준 출력에 **한 줄 JSON**을 남긴다. Cloud Run이 그대로 Cloud Logging에 넣는다.

## 자동으로 붙는 값

| 필드 | 내용 |
| --- | --- |
| `severity`, `time`, `logger`, `message` | 기본 값. `message`는 아래 가림 규칙을 거친다 |
| `service`, `request_id` | 요청마다 공통 앱이 붙인다. `request_id`는 응답의 `X-Request-ID`와 같다 |
| `trace_id` | Cloud Run의 `X-Cloud-Trace-Context`에서 읽는다. `GOOGLE_CLOUD_PROJECT`가 있으면 `logging.googleapis.com/trace`도 붙여 Cloud Trace와 연결한다 |
| `method`, `path`, `status`, `duration_ms` | 요청이 끝날 때 `backend.request` 로거가 한 줄 남긴다. `path`에 쿼리 문자열은 없다 |

Uvicorn 자체 접근 로그는 끈다. 쿼리 문자열을 그대로 남기고 업무 ID가 없어서다.

## 업무·작업·실험 ID 연결

로그에 붙일 수 있는 ID는 `ticket_id`, `project_id`, `improvement_request_id`, `job_id`, `experiment_id`, `evaluation_run_id`뿐이다. 다른 이름은 `ValueError`로 거부한다.

- 경로에 ID가 있는 API(C/S 문의, TFT 프로젝트)는 라우터의 `Depends(bind_path_ids)`가 자동으로 붙인다. 새 업무 라우터도 같은 의존성을 단다.
- 경로에 없는 ID는 만든 직후 `bind(ticket_id=...)`로 붙인다(문의 등록 참고).
- 요청 밖에서 도는 작업(워커, 평가 실행)은 시작할 때 `with log_context(service=..., job_id=...)` 또는 `experiment_id=..., evaluation_run_id=...`로 맥락을 연다. 작업 기반은 KAN-69, 평가 실행은 해당 평가 티켓에서 이 방식을 쓴다.

## 남기지 않는 것

- 정해진 필드 외의 `extra=` 값은 버린다. 요청·메일 본문, 고객 원문, 토큰은 필드로 넘겨도 기록되지 않는다.
- 예외는 종류(`error_type`)만 남기고 메시지와 traceback은 남기지 않는다. 공급자 응답이나 자격 증명이 섞일 수 있다.
- `message`에서 Bearer 토큰, JWT, `token`·`code`·`state`·`secret`·`password`·`cookie` 등의 값, 팀 세션 쿠키, 이메일 주소를 가린다. 2,000자를 넘으면 자른다.
- 가림은 실수를 줄이는 마지막 단계다. 본문·토큰을 메시지 문자열에 넣지 않는 것이 원칙이다.
