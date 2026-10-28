# 플랫폼 평가 저장 계약 (KAN-85)

## KAN-90 접근 정책 API — 초기 구현

사용자 결정으로 KAN-90과 평가 화면 KAN-137·138·139는 오새결 담당이다. KAN-85 완료 이력은 유지한다.

`GET /api/v1/platform/evaluations/policy`는 활성 Microsoft 운영 세션을 재조회하고 서버의 `EVALUATION_EVALUATOR_USER_IDS` JSON UUID 배열에 현재 내부 사용자 ID가 있는지 검사한다. 기본 배열은 비어 있어 모두 거부한다. 이메일·업무 역할·클라이언트 사용자 헤더·개발 계정 선택으로 허용하지 않는다. 서버 배포 설정으로만 지정하며 이번 구현에서 실제 오새결 UUID나 계정 권한을 등록하지 않았다.

운영 인증 비활성/세션 누락·철회는401, 미지정 사용자는403, 세션 조회 장애는503이다. 성공·오류 응답 모두 캐시를 금지한다. 데이터·프로젝트별 접근 검사는 후속 조회/실행 API에서도 별도로 필요하다. 이 정책 응답은 원문·정답·대상 목록을 포함하지 않는다.

2026-10-25 사용자 결정에 따라 `primary_quality_view=first_attempt`, `final_result_reported_separately=true`를 반환한다. 해당 AI 품질 합격선은 최초 호출에 적용하고 모든 원시 응답의 스키마 위반0 기준을 유지한다. 실제 복구 정책·신규 합격선 확정은 별도다.

실제 모델 평가 실행기는 미연결이므로 `execution_available=false`, `execution_unavailable_reason=RUNNER_NOT_CONNECTED`, `final_test_available=false`, `raw_output_available=false`를 유지한다. `admission_available`은 아래 로컬 합성 **저장 접수** 가능 여부다. `local_smoke_execution_available`은 같은 명시 설정에서 CLI 합성 worker 사용 가능 여부이며 실제 모델 실행 가능 여부와 다르다. 합성 실행의 취소·재개는 아래 local/test 제어 계약을 따른다. 실험 등록·품질 결과 조회는 미구현이며 상태 조회와 제어 화면 범위는 아래를 따른다.

## KAN-90 로컬 합성 저장 접수 — 운영 실행 미연결

`POST /api/v1/platform/evaluations/runs`는 `experiment_id` UUID 하나와 8~128자의 `Idempotency-Key`를 받는다. split·모델·명령·설정 덮어쓰기 필드는422로 거부한다. 현재 세션·지정 평가자 검사 후 `Origin`이 서버 app_origin과 일치하고 `X-CSRF-Token`이 세션에 결속됐는지 확인한다. 실패는403이며 기존 `/me`의 CSRF 발급 방식을 재사용한다.

기본 접수는 비활성이다. **local/test** 환경에서만 서버 `EVALUATION_LOCAL_ADMISSION_ENABLED=true`와 `EVALUATION_APPROVED_CONFIG_HASHES` JSON 배열을 명시하여 검수한 구성 hash를 허용할 수 있다. production에서는 이 설정을 켜도503이다. 이번 작업에서 실제 계정·실험을 허용하거나 공용 설정을 변경하지 않았다.

현재 지원 범위는 요청자가 생성한 실험 중 `purpose=smoke`, `data_origin=synthetic`, `model_id=fake`, 빈 configuration 또는 아래 고정 C/S 합성 프로필이며 dataset_manifest는 비어 있어야 한다. 모든 대상은 synthetic이며 attributes가 비어 있어야 한다. 일반 실험·실제 데이터·외부 모델은 거부한다. `data_split=test`는 허용 hash에 있어도403 FINAL_TEST_LOCKED로 거부하며 대상·원문·정답을 읽지 않는다. 다른 생성자나 없는 실험은404다. 합성 테스트에서 이 조건들을 검증하며 실제 시험 자료는 사용하지 않는다.

구성 hash를 기존 canonical JSON 규칙으로 재계산하고, 비어 있지 않은 대상 집합의 hash를 확인한다. 이때 읽고 검증한 대상 snapshot만 사례 생성에 사용한다. 등록 이후 대상 추가/구성 변경으로 hash가 달라지면409다. 자료의 출처 진실성이나 검수 품질을 hash만으로 증명하지 않는다. 사전 검수된 허용 목록 등록은 별도 운영 책임이며 실험 등록·데이터 검수 API는 미구현이다.

### 한 번의 저장과 재전송

KAN-69의 작업 유일 키를 먼저 확보한 뒤 신규 요청만 run·고정 대상별 PENDING case·run_control·최초 응답 request ID를 동일 transaction에 저장한다. 작업·최초 이벤트·outbox를 포함한 어느 단계에서든 실패하면 모두 rollback한다. commit 후에만202를 반환한다. 동일 키·동일 정규화 요청은 기존 run/job과 최초 응답 body·Location·Retry-After를 재생한다. 다른 experiment_id 또는 If-Match면409다. If-Match는 신규 생성의 판본 전제조건이 아니며 멱등 요청 구분에 포함한다. HTTP X-Request-ID 헤더는 현재 요청 추적 ID이고 body meta.request_id는 최초 접수의 ID다.

202의 data는 run_id·job_id·status=QUEUED·status_url이다. Location은 실제 구현한 `GET /api/v1/platform/evaluations/runs/{run_id}`이며 조회는 현재 평가 권한·실험 생성자·요청자를 다시 검사하고 test split은 숨긴다. 구성 hash 허용이 철회돼도 담당자가 보류 원인과 최신 판본을 확인해 취소할 수 있도록 제한된 상태 조회는 유지한다. 평가자 지정이나 세션 권한이 철회되면 조회도 거부한다. 접수 재전송·재개는 구성 허용을 다시 검사한다. 조회는 실행/job ID·상태·요청 건수·version·needs_review·failure_code만 반환하고 원시 출력·정답을 노출하지 않는다.

접수 응답은 최초 응답 재생을 유지하기 위해 events_url을 추가하지 않은 **local/test 전용 부분 계약**이다. job_id로 작업 상태 및 SSE 경로를 구성할 수 있으며 상세는 [작업 계약](jobs.md)을 따른다. 실제 운영 접수 활성화 전에 공개 웹 중계·운영 실행 인증과 통합 검증을 완료해야 한다. 접수 자체는 QUEUED만 만든다. 아래 로컬 worker를 명시적으로 실행하면 RUNNING→SUCCEEDED로 진행한다. Cloud Run Jobs dispatcher는 아직 없다. 이 접수의 provider=fake, runner_kind=test, scoring_version=smoke-not-scored-v1은 미채점 연결 검사 표시이며 실제 품질 점수를 의미하지 않는다.

DB 대기 상한은 초기 로컬 보호값으로 lock_timeout=2초, statement_timeout=5초다. Retry-After=5초도 조회 안내값이며 실측 SLA가 아니다. DB 오류/commit 불명확 시503이며 같은 키로 재확인한다. 호출자가 새 키를 발급해 중복 접수하지 않아야 한다.

### 마이그레이션과 남은 범위

KAN-85 → `20261025_KAN-69_job_admission.sql` → `20261025_KAN-90_run_admission.sql` 순으로 적용한다. run/job의 일대일 및 동일 business ID를 FK로 검증한다. 기존 non-null job_id가 runtime.jobs와 맞지 않으면 마이그레이션은 실패하므로 기존 레코드를 임의 수정하지 말고 이관 근거를 확인한다. run_control·admission_receipts는 platform_app에 SELECT·INSERT만 부여한다. generation=0/holder=null은 미실행 상태다. 아래 보완 migration과 로컬 worker는 합성 lease 획득·갱신을 제공하며 사용자 취소·재개는 아래 user_controls migration을 추가로 적용해야 한다.

최초 응답 저장은 구조화 ID만 보존한다. 실제 모델 실행·운영 dispatcher/worker·외부 호출 복구, SSE, 전체 평가 및 최종 시험 운영은 후속 작업이다. 로컬 합성 접수 성공으로 KAN-69/90 전체 완료를 판단하지 않는다.

마이그레이션: `infra/migrations/platform/20261023_KAN-85_evaluation_storage.sql`. 스키마는 `evaluation`(소유 `platform_migrator`, 실행 계정 `platform_app`)이며 `cs_app`·`tft_app`은 접근할 수 없다. 분모·계산법·안전 조건의 원본은 [평가 명세](../../docs/evaluation.md), 출처 축은 [데이터 계약](../../docs/data-contract.md)을 따른다. 이 문서는 저장 구조의 계약이며 평가 API(KAN-90)나 지표 계산기(KAN-86·88·89)의 구현 완료를 뜻하지 않는다.

기존 로컬 DB에는 다음 명령으로 명시적으로 적용한다. 공용 DB 변경·배포는 별도다.

```bash
python3 scripts/workspace.py migrate platform 20261023_KAN-85_evaluation_storage.sql
```

## 테이블과 역할

| 테이블 | 단위 | 보존하는 것 | 쓰기 |
| --- | --- | --- | --- |
| `experiments` | 실험(동결 구성) | 데이터 manifest·hash, split, 정답 판본, 정책·프롬프트·모델·색인·입력 빌더 판본, 비밀 아닌 실행 설정, 대상 집합 hash, 구성 hash | INSERT만. UPDATE·DELETE 불가 |
| `experiment_targets` | 실험의 대상 1건 | 대상 외부 ID, 종류, 출처, 보고·제외용 속성 | INSERT만 |
| `runs` | 실험의 실행 1회 | 종료 상태, 실행 주체, 채점기·코드·가격표 판본, 최초 요청·유효·보류·실패·재시도 수와 제외 사유별 건수, 시각 | INSERT·UPDATE(상태 전이) |
| `cases` | 실행 안의 대상 1건 | 사례 상태, 최종 출력, 폴백 사용, 정답 코드 스냅샷 | INSERT·UPDATE |
| `case_attempts` | 단계×시도 1건 | 원시 응답(마스킹), 파싱 결과, 결과 유형, 지연, 토큰, 비용, 모델 판본, 입력 hash | INSERT만 (추가 전용) |
| `metrics` | 실행 또는 사례의 점수 1건 | 지표 값과 분자·분모·표본 수, 클래스·구간, 합격선과 판정, 채점 버전 | INSERT만 (추가 전용) |
| `usage_summaries` | 실행×단계×제공자×모델 | 호출 수, 실패 호출 수, 토큰 합, 지연 합·p50·p95·max, 비용 | INSERT·UPDATE |

`case_attempts`·`metrics`는 `cs.audit_events`와 같은 추가 전용 테이블이다. 재채점은 새 `scoring_version`의 metrics 행을 추가하며 기존 행을 고치지 않는다. 실험을 바꾸려면 새 실험을 등록한다.

## 실험과 구성 hash

`purpose`는 `smoke`(합성 연결 점검), `rule_replay`(검수된 입력으로 순수 룰 재현), `pipeline`(AI 예측 입력부터의 전체 파이프라인)이다. 평가 명세의 규칙 단독 평가와 전체 파이프라인을 같은 실험에 섞지 않는다. `smoke`가 아니면 `data_split`과 `gold_version`이 필수다.

- `dataset_manifest`: 원천(`source`, `source_instance`)별 파일 SHA-256, 건수, `data_origin` 구분. 실제·합성 자료를 섞은 실행은 실험의 `data_origin`을 `demo`로 두고 manifest에 원천별 구분을 남긴다. `experiment_targets.data_origin`은 대상별 값이다.
- `dataset_hash`: manifest가 가리키는 파일 hash를 정렬해 이은 문자열의 SHA-256.
- `target_set_hash`: `target_ref`를 정렬해 `\n`으로 이은 문자열의 SHA-256. 분모를 고정하는 값이며 모델별로 유리한 사례만 골라 바꾸지 않는다.
- `config_hash`: 아래 필드를 키 순서로 정렬한 canonical JSON(`separators=(",", ":")`, `ensure_ascii=False`)의 SHA-256. `purpose`, `data_origin`, `dataset_hash`, `data_split`, `gold_version`, `policy_version`, `prompt_version`, `model_id`, `index_version`, `input_builder_version`, `configuration`, `target_set_hash`. 이름·생성자·시각은 포함하지 않는다. 같은 `config_hash`는 같은 실험이므로 새 실행만 추가한다.
- `configuration`: `max_output_tokens`, `location`, `attempts` 같은 비밀 아닌 설정만 둔다. `gemini_api_key`, 프로젝트 자격증명, 토큰은 어떤 키로도 넣지 않는다.
- `derivation_kind`는 `DERIVED` 또는 `SYSTEM`이다. 평가 기록은 제공 자료가 아니므로 `PROVIDED`를 쓰지 않는다.
- `experiment_targets.attributes`: `channel`, `language`, `priority_adjusted_by_agent` 등 보고 분리와 제외 플래그. 평가 명세대로 120건을 분모에서 고정 차감하지 않고 이 플래그로 사례를 `EXCLUDED` 처리하며 `runs.excluded_counts`에 사유별 건수를 남긴다. 원문·정답·개인정보는 넣지 않는다.
- `data_split='test'` 실험은 실행 전에 기존 test 실행 이력을 조회(인덱스 `experiments_test_split`)해 최종 1회 사용 원칙을 확인한다. 이 확인은 KAN-90의 실행 요청 API가 수행한다.

## 실행 종료 상태

`runs.status`는 `QUEUED`, `RUNNING`, `SUCCEEDED`, `PARTIAL`, `FAILED`, `CANCELLED`, `TIMED_OUT`이다. 앞의 둘만 진행 상태이며 나머지는 종료 상태로 `finished_at`이 필수다(`QUEUED`·`RUNNING`은 `finished_at`이 null). `PARTIAL`은 일부 사례만 결과가 있는 종료다. 시작 전에 취소되면 `started_at` 없이 `CANCELLED`가 된다. [API 계약](../../docs/api-contract.md)의 작업 상태에는 `TIMED_OUT`이 없으므로 KAN-90의 작업 상태로는 `FAILED`로 전달하고 실행 상태에서 구분한다. `needs_review`는 확인 필요 여부이며 상태와 별개다. `failure_code`는 예외 클래스명이나 코드만 둔다.

`runner_kind`는 `cloud_run_job`, `local`, `test`이고 `runner_ref`는 Job 실행 이름 등 식별자다. `requested_by_user_id`는 `identity.users`를 참조하며 CLI·Job 실행에서는 null일 수 있다. `job_id`는 KAN-90의 작업 ID이며 `runtime` 스키마가 아직 없어 FK를 두지 않는다.

## 사례·단계·시도

`cases.status`는 `PENDING`, `VALID`(최종 출력이 스키마를 통과), `INVALID_OUTPUT`(재시도·폴백 후에도 스키마 위반), `HELD`(보류), `FAILED`(응답 없음·타임아웃), `EXCLUDED`(플래그 제외, `exclusion_reason` 필수)이다. `VALID`만 정답 판정 대상이고 나머지는 평가 명세대로 정답 클래스의 FN에 포함한다. 복합 FK로 사례는 자기 실행의 실험과 그 실험의 동결 대상 집합에만 속한다.

`case_attempts`는 단계(`classify`, `retrieve`, `rules`, `draft`, `reply_analysis`, `condition_compare`, `dept_question`, `smoke`)와 시도 번호(1부터)로 유일하다. 단계마다 `is_final`인 시도는 하나다.

- `outcome`: `VALID`, `SCHEMA_VIOLATION`(파싱·enum·키·타입 위반), `TRANSPORT_FAILURE`(응답 없음, `raw_output`은 null), `TIMEOUT`, `HELD`. 재시도 후 결과가 정상이어도 첫 시도의 `SCHEMA_VIOLATION`은 그대로 남는다. 스키마 위반율 = `SCHEMA_VIOLATION` 수 / 원시 응답이 있는 시도 수, 전송 실패율은 별도다.
- `raw_output`: 마스킹을 거친 원시 응답 텍스트. 고객 원문 echo, 시리얼 원값, 개인정보, 정답 필드가 들어가지 않도록 작성 측(평가 Job)이 마스킹한 뒤 저장한다. 감사용 접근 제한 자료로 취급하며 목록 API 응답에 그대로 노출하지 않는다.
- `input_sha256`: 모델에 보낸 입력의 hash. 입력 자체는 저장하지 않으며 누설 감사는 hash 재계산으로 한다.
- `error_code`·`http_status`: `eval/smoke.py`와 같이 예외 클래스명과 상태 코드만 둔다. 제공자 메시지는 자격증명·요청 본문을 포함할 수 있어 저장하지 않는다.
- 토큰 5종은 `backend.shared.ai.Usage`와 같고 null은 제공자가 알려주지 않은 값이다. `cost_usd`는 `pricing_version`이 있어야 하며 가격표가 없으면 null로 두고 0으로 적지 않는다.
- `usage_raw`·`requested_model`·`call_attempts`(KAN-86)는 공급자 사용량 원문(토큰 수 메타데이터, `Usage.raw`), 요청한 모델 ID(`Generation.model`), 호출 시도 수(`Generation.attempts`)다. 응답 없이 실패한 시도는 null이다. `cost_usd`는 `backend/shared/ai_prices.json`에 그 모델의 USD 단가가 있을 때만 `estimate_cost`의 total로 저장하고 `pricing_version`은 `모델@as_of`다. 단가가 없거나 USD가 아니면 둘 다 null이며 numeric(14,6)이라 소수 6자리로 반올림된다.
- `cases.gold_labels`는 `gold_version` 시점의 정답 코드(`category_code` 등)만 담는다. `final_reply`, 본문, 담당자 등 원천 기록은 넣지 않는다.
- `cases.final_output`은 폴백·재시도 후 채점에 쓴 구조화 출력(코드값)이다. 폴백 고정 문장을 썼으면 `fallback_used=true`다.

## 지표와 채점 버전

`metrics`는 `(run_id, case_id, scoring_version, metric_key, segment, class_label)`로 유일하다(null도 같은 값으로 취급). `case_id`가 null이면 실행 단위다.

- `scoring_version`: 채점기 코드의 판본 문자열(예: `cs-scorer-1.0`). 계산식·클래스 목록·합격선 해석이 바뀌면 올린다. `runs.scoring_version`은 실행을 처음 채점한 판본이며 재채점은 다른 판본의 행을 추가한다. 같은 실행의 다른 판본 점수를 섞어 보고하지 않는다.
- `metric_key` 예: `category_macro_f1`, `category_micro_accuracy`, `critical_miss_count`, `urgency_macro_f1`, `urgency_non_none_recall`, `priority_rule_exact`, `dept_rule_policy_match`, `dept_pipeline_exact`, `schema_violation_rate`, `transport_failure_rate`, `hit_at_5`. 클래스별 P/R/F1은 `class_label`에 클래스를 두고 `details`에 `tp`·`fp`·`fn`을 둔다. 분모 0인 클래스는 `value=0`, `support`에 표본 수를 둔다.
- `segment`: `all`, `channel=PHONE`, `channel=non_PHONE`, `language=ko` 등. PHONE 누설 분리 보고에 사용한다.
- `value`가 null이면 계산 불가(예: 원시 응답 0건의 스키마 위반율)이며 CHECK로 `passed`를 둘 수 없다. `threshold`·`comparison`은 KAN-82·95에서 잠근 합격선이고 잠그기 전에는 null이며 `passed`도 null이다. 임의로 합격 처리하지 않는다.

## 사용량

`usage_summaries`는 실행 종료 시 `case_attempts`에서 집계한 캐시다. 같은 단계의 LLM 호출이 실제로 두 번 실행됐으면 두 호출을 모두 센다. 원본은 항상 `case_attempts`이며 불일치 시 attempts를 기준으로 재집계한다.

## 저장 금지와 마스킹

- 자격증명: API 키, Gemini·GCP 자격증명, Microsoft 토큰, 세션 토큰, `Authorization` 헤더, 비밀번호는 어떤 컬럼(jsonb·text 포함)에도 넣지 않는다. DB 제약으로 막을 수 없으므로 KAN-90의 기록 코드가 `tests/test_eval_smoke.py`처럼 "민감 문자열이 저장 payload에 없음" 회귀를 가진다.
- 원문·개인정보: 문의 본문, 고객 식별자, 시리얼 원값, 메일 주소는 저장하지 않는다. `raw_output`은 마스킹 후 저장한다.
- 정답: `gold_labels`의 코드값과 `gold_version`만 둔다. 원천 해결 기록·회신 본문은 `cs` 스키마에 남긴 채 참조하지 않는다.
- 오류: 클래스명·코드·HTTP 상태만 둔다.
- 노출: `raw_output`·`gold_labels`는 평가 결과 조회 API에서 기본 제외하며 감사 목적의 제한된 경로에서만 반환한다.

## 후속 티켓의 경계

| 티켓 | 사용 범위 |
| --- | --- |
| KAN-90 평가 Jobs·실행 관리 API | 실험 등록(hash 계산), 실행 요청(202 작업, test split 사용 이력 확인), 상태 전이, 결과 조회 DTO. `runtime` 작업과 `runs.job_id` 연결 |
| KAN-86·88·89 지표·평가 실행 | 평가 Job이 `cases`·`case_attempts`·`usage_summaries`를 기록하고 `metrics`를 `scoring_version`과 함께 추가. 마스킹과 민감 문자열 회귀 |
| 프론트 평가 화면 | `runs`·`metrics` 조회. `raw_output`은 표시하지 않음 |

평가 Job이 `platform_app`이 아닌 DB 역할로 쓰면 해당 역할에 같은 테이블 권한을 별도 마이그레이션으로 부여해야 한다. 단계·상태 enum 확장은 새 마이그레이션으로 CHECK를 바꾸고 이 문서와 소비자를 함께 수정한다.


## 로컬 합성 worker — 접수부터 완료와 중단 복구

`20261025_KAN-90_smoke_worker.sql`을 접수 migration 다음에 적용한다. platform_app에는 run_control의 generation/holder/lease/version과 jobs의 status 열만 UPDATE를 추가한다. 시도·이벤트·최초 응답 기록의 수정/삭제 권한은 늘리지 않는다. 공용 DB에는 이번 작업에서 적용하지 않았다.

로컬 관리자 설정으로 허용된 합성 outbox 작업 **한 건**을 처리한다. `EVALUATION_EVALUATOR_USER_IDS`, `EVALUATION_APPROVED_CONFIG_HASHES`, `EVALUATION_LOCAL_ADMISSION_ENABLED`와 local/test 환경 및 플랫폼 DB 설정이 필요하다. 서버의 DB 자격과 명시 설정을 사용하는 CLI이며 무인증 HTTP worker 경로는 만들지 않았다.

```bash
uv run --frozen python -m backend.api.platform.evaluations.smoke_worker
```

작업이 없으면 NO_ELIGIBLE_WORK, 완료하면 run UUID만 출력한다. 반복 처리·스케줄러·Cloud Run Jobs 배포는 제공하지 않는다. dispatcher는 outbox의 비종료 작업을 읽지만 실제 실행 허가는 run_control의 단일 소유권으로 결정한다. outbox 원본은 삭제하지 않으며 종료 상태를 조회해 재실행을 건너뛴다.

- claim은 run_control 행을 잠그고 현재 지정 평가자·활성 사용자·실험 생성자·허용 hash·합성 구성·고정 사례 집합을 재검증한다. 활성 lease가 있거나 종료된 run이면 인수하지 않는다.
- 미소유 또는 만료된 run만 generation을 증가시키고 새 holder를 부여한다. DB 시각으로 lease를 판단하며 기본30초, 설정 범위1~300초는 합성 처리의 초기 보호값이다. 실측 SLA가 아니다.
- 사례마다 새 transaction에서 동일 조건과 holder/generation/lease를 재검사한다. 기존 `eval.smoke.FakeProvider`와 `Prediction`을 재사용해 고정 합성 프롬프트를 처리한다. 원천 자료나 정답은 읽지 않는다.
- 시도1·최종 합성 출력·사례 VALID·실행의 유효 출력 수·progress 이벤트·lease 갱신을 함께 commit한다. 완료한 사례는 다시 처리하지 않는다. raw_output에는 고정 합성 응답만 들어가며 API에는 노출하지 않는다. 실제 모델 토큰/비용은 기록하지 않고 null을 유지한다.
- 사례가 모두 VALID이고 고정 분모와 일치하면 run/job SUCCEEDED·종료 시각·completed 이벤트·lease 해제를 함께 저장한다. 이는 연결 검사 성공이며 품질 합격/실측 점수가 아니다. metrics는 생성하지 않는다.

프로세스가 사례 transaction 중 죽으면 미commit 결과는 rollback된다. 다음 명시적 CLI 실행은 lease 만료 후 새 generation으로 인수하고 남은 PENDING 사례부터 처리한다. 이미 모든 사례가 끝났고 종료 저장만 남았다면 사례를 재실행하지 않고 종료만 확정한다. 오래되거나 만료된 worker의 저장은409 STALE_WORKER로 거부한다. 오류를 성공으로 숨기거나 임의로 종료 상태를 덮어쓰지 않는다. 허용 목록 철회·사용자 비활성화 시에도 다음 단계가 거부되며 해당 실행은 관리자 확인 전까지 진행되지 않는다.


### 고정 C/S 합성 저장 프로필 (KAN-86)

`configuration={"task":"cs_classification","fixture_version":"cs-smoke-v1"}`도 지원한다.
`backend/api/platform/evaluations/cs_smoke.py`의 고정 세 사례(정상·스키마 위반·타임아웃)만
사용하며 `dataset_hash`, `target_set_hash`, `input_builder_version`, `prompt_version`이 모듈의
고정 값과 일치해야 한다. 임의 입력·응답·제공자·파일 경로 설정은 받지 않는다. 기존 승인
hash·평가자·local/test·시험 잠금은 그대로 적용하고 인수/사례 저장 시 다시 검사한다.

- 실제 C/S `build_input → 단일 fake 호출 → validate_output`을 거쳐 `step=classify`, 시도1을 저장한다.
  `VALID`는 스키마 통과일 뿐 품질 정답 판정이 아니다. 코드북·gold·채점은 연결하지 않는다.
- 검사 전 원시 응답은 메모리에만 존재한다. 저장할 때 응답 전체를 `[REDACTED]`로 바꾸며,
  응답 없음은 SQL NULL이다. 이 프로필의 원시 본문 감사는 불가능하며 고정 fixture로만 재현한다.
  parsed/final_output은 검증을 통과한 다섯 코드 필드만 저장하고 실패 시 NULL로 둔다.
- 모델 판본·종료 사유·입력 hash·오류 코드·지연(정수 ms)·알려진 토큰을 보존한다.
  토큰은 **고정 합성 수치**이고 비용은 NULL이다. 모르는 사용량은 NULL, 알려진0은0이다.
  입력 판본은 동결 실험에 보존한다. 메모리의 removed_annotations/call_started/
  execution_uncertain은 현재 DB에 저장하지 않는다. 실제 외부 호출의 intent/UNKNOWN 기록을 대체하지 않는다.
- 최초 스키마 위반·타임아웃을 자동 재시도하거나 성공으로 바꾸지 않는다. 사례·시도·카운터·
  진행 이벤트를 기존 잠금 transaction에서 함께 저장한다. 이미 완료된 사례는 재개 시 건너뛴다.
- 모든 사례 처리 뒤 전부 유효하면 SUCCEEDED, 일부 유효하면 PARTIAL, 유효 사례가 없으면 FAILED다.
  runs와 jobs의 상태를 함께 저장하고 종료 이벤트를 보낸다. 고정 세 사례는 PARTIAL로 끝난다.
  SSE 진행 payload의 step은 기존 `smoke`를 유지하며 원문·코드 출력·토큰을 추가하지 않는다.
- 공개 API 필드는 추가하지 않는다. 최종 시험·원시 출력 접근·운영 실행 잠금을 유지한다.
- `20261027_KAN-90_worker_usage_KAN-86.sql`은 기존 worker_role SQL **다음**에 적용한다.
  전용 worker의 시도 메타데이터·토큰 INSERT와 실행 실패/보류 카운터 SELECT·UPDATE만 추가한다.
  시도 원문 SELECT·UPDATE·DELETE 권한은 부여하지 않는다. 이번 적용은 격리 검사 DB에 한정한다.

**이 복구는 외부 효과가 없는 FakeProvider 전용이다.** transaction 중 실제 모델 네트워크를 호출하는 구현으로 확장하지 않는다. 외부 모델은 intent/dispatch 결과 불명확(UNKNOWN)/사용량 보존 규약이 먼저 필요하다. 중단된 합성 함수를 다시 호출할 수는 있지만 commit된 사례·시도는 하나만 남는다. 이를 실제 모델 호출의 exactly-once 보장으로 해석하지 않는다.

검사에는 HTTP 접수→outbox→합성 실행→완료 조회, 종료 뒤 최초 접수 응답 재생, 동시 소유권 경합, 저장 단계 실패, 만료 worker 차단, 사례 일부 완료 후 재개, 종료 직전 재개, 별도 프로세스의 transaction 중 비정상 종료를 포함한다. 실제 배포·SSE·운영 worker 인증·실제 모델 품질은 별도 검증 대상이다. 합성 실행의 취소·재개 검사는 아래와 같다.


## KAN-90 합성 실행 보류·취소·재개

이 절은 local/test의 합성 실행 제어 구현 계약이다. production 활성화나 실제 모델 호출 취소 보장이 아니다. `20261025_KAN-90_smoke_worker.sql` 다음에 `20261025_KAN-90_user_controls.sql`을 적용한다. 새 migration은 이번 작업의 임시 검사 DB에서만 적용하며 공용 DB에는 적용하지 않았다. 기존 접수·worker migration을 재적용할 필요는 없다.

### 요청과 판본

- `GET /evaluations/runs/{run_id}`는 현재 `version`과 `ETag: "<version>"`을 반환한다. 경로 접두사는 `/api/v1/platform`이다.
- `POST /evaluations/runs/{run_id}/cancel` 및 `/resume`는 현재 지정 평가자의 활성 세션, Origin, 세션 결속 CSRF와 8~128자 `Idempotency-Key`를 요구한다. 본문 없이 조회 ETag를 `If-Match`에 전달한다.
- If-Match 누락은428 VERSION_REQUIRED, 최신 판본과 다르면412 VERSION_CONFLICT다. 다른 소유자·없는 실행은404다. 현재 접수 활성 조건(local/test 및 명시 활성 설정)을 만족해야 한다.
- 한 실행의 run_control 행 잠금 아래 상태 변경·job·이벤트·판본·최초 응답을 같은 transaction에 저장한다. receipt 저장 실패도 전부 rollback한다. 멱등 범위는 실행·사용자·동작·키이며 동일 요청은 최초 body와 ETag를 재생한다. 같은 키에 다른 If-Match는409 IDEMPOTENCY_CONFLICT다. 재생 ETag는 최초 결과의 판본이므로 새 작업 전 GET으로 최신 상태를 확인한다.
- 재전송도 현재 세션·평가자·소유권을 검사한다. resume은 구성 hash·사용자 활성·고정 합성 대상도 다시 검사한다. DB 실패/commit 불명확 시 기존503 경계를 따르며 동일 키로 재확인한다.

### 상태별 동작

| 요청 | 현재 상태 | 처리 |
| --- | --- | --- |
| cancel | QUEUED 또는 RUNNING | CANCELLED와 finished_at, job CANCELLED, 취소자·시각, completed 이벤트를 함께 저장하고 needs_review를 해제한다. 기존 failure_code는 진단 이력으로 유지한다. |
| cancel | 종료 상태 | 상태와 판본을 바꾸지 않고 기존 상태를 반환한다. 취소 성공으로 완료 상태를 덮어쓰지 않는다. |
| resume | 비종료, needs_review=true 또는 활성 lease 없음 | 현재 허용 조건을 재검사하고 needs_review·failure_code를 지운다. 새 소유권 획득은 다음 worker 실행에 맡긴다. |
| resume | 종료 상태 또는 보류 아닌 활성 lease | 409 INVALID_TRANSITION. 완료·취소 상태를 RUNNING으로 되돌리지 않는다. |

비종료 실행 제어 시 generation과 version을 증가시키고 holder·lease를 해제한다. 오래된 worker는 STALE_WORKER로 저장이 거부된다. 합성 worker와 취소가 같은 제어 행을 잠그므로 이미 commit한 사례·시도는 보존하고 이후 처리를 막는다. 외부 호출이 없는 fake 처리에만 적용되며 실제 모델의 실행 중 요청 취소·UNKNOWN 처리로 확장하지 않는다.

### 자동 보류와 복구

worker가 현재 평가자 지정/활성 사용자/구성 승인/구성 hash/합성 대상 검증에 실패하면 안전한 허용 오류 코드만 failure_code에 기록하고 needs_review=true로 보류한다. 상태는 기존 QUEUED/RUNNING을 유지하며 새 상태 enum을 만들지 않는다. job.progress 이벤트와 판본을 함께 저장하고 dispatcher는 보류 실행을 자동 선택하지 않는다. 원문·정답·예외 메시지는 저장하지 않는다.

기록 가능한 코드는 FORBIDDEN, SMOKE_ACCESS_DENIED, FINAL_TEST_LOCKED, EXPERIMENT_NOT_APPROVED, LOCAL_SMOKE_ONLY, EXPERIMENT_HASH_MISMATCH, TARGET_SET_MISMATCH, SMOKE_INCOMPLETE다. DB 일시 장애·STALE_WORKER는 이 목록에 넣지 않으며 lease 만료 후 복구 규칙을 따른다. 이전 worker의 오류가 새 holder를 보류시키지 않도록 generation/holder를 비교한다.

담당자는 보류 사유 확인 → 허용 조건 복구 → 최신 ETag 조회 → 명시 resume 순으로 진행한다. 구성 승인 철회 상태에서는 제한 상태 조회와 cancel만 허용하고 resume은 거부한다. 평가자 지정/계정 활성 자체가 철회됐으면 먼저 운영자가 계정 정책을 해결해야 한다. 플랫폼 API가 역할이나 허용 hash를 자동 복구하지 않는다.

검증 범위는 실제 PostgreSQL의 취소·재전송, 동시 동일 취소, 판본 충돌, 저장 실패 rollback, 옛 worker 차단, 보류 반복 방지와 조건 복구 후 재개, HTTP CSRF·ETag다. 합성 성공은 실제 품질 평가나 운영 배포 완료 증거가 아니다.

### 작업 ID로 상태 복구

접수된 합성 실행은 [작업 상태 조회](jobs.md#평가-작업-상태-조회--kan-69-첫-조회-단위)의 `GET /api/v1/platform/jobs/{job_id}`로도 제한 조회할 수 있다. 현재 세션·지정 평가자와 작업/실행/실험의 소유권을 검사하며 최종 시험 잠금은 유지한다. 기존 run 상태 URL과 제어 ETag 계약은 유지하고, 작업 스냅샷은 같은 실행 판본과 마지막 이벤트 sequence를 구분하여 반환한다. 같은 job_id의 `/api/v1/platform/jobs/{job_id}/events`는 local/test 합성 저장 이벤트 재생과 연결 중 권한 재검사를 제공한다. 상세 커서/종료/허용 payload는 작업 계약을 따른다. 웹은 별도 기본 비활성 EVALUATION_READ_RELAY_ENABLED 설정으로 job 상태·SSE GET만 중계한다. 실제 공용 활성화와 접수 중계는 미구현이다. 취소·재개 중계는 아래 별도 기본 비활성 설정을 따른다. 평가 조회 화면 범위는 아래와 같다.


### KAN-137 평가 실행 조회 화면

`/evaluations?job={job_id}`에서 위 작업 상태 API를 직접 조회한다. 개발 메뉴의 평가 링크 또는 직접 URL로 진입한다. 웹 중계의 기본 비활성과 서버의 평가자·소유권·최종 시험 잠금은 유지한다. 개발 화면의 모의 응답 모드와 무관하게 실제 API에 요청하며 실패를 합성 성공으로 대체하지 않는다.

- 작업 ID 입력, 실행 상태, 실행 ID, 검토 필요 여부와 마지막 조회 시각을 표시한다. `run_status`를 사용하여 시간 초과도 구별한다.
- 새로고침과 화면 재진입/포커스 복귀 시 조회한다. 조회 중 또는 조회 실패 시 이전 상세를 숨긴다. 작업 변경·화면 이탈·탭 숨김 시 이전 요청/구독을 취소하며 상태 조회 제한 시간은15초다. 탭 복귀 시 상태부터 재조회한다.
- 401은 로그인 재확인,403은 평가자 권한,404는 작업 ID/접근 권한,503은 조회 비활성 또는 서비스 불가로 안내한다. 서버의 원문 오류나 원시 출력은 표시하지 않는다.
- 상태는 조회 시점의 스냅샷이며 아래 SSE 신호로 자동 갱신한다. 목록, 설정 선택과 접수는 후속 범위이며 취소·보류 해제는 아래 계약을 따른다. 서버에 없는 완료 비율·품질 점수·토큰·비용을 만들어 표시하지 않는다.

화면 구성은 [MLflow의 실행별 메타데이터](https://mlflow.org/docs/latest/ml/tracking/)와 [LangSmith의 실험 상세 및 진행 표시](https://docs.langchain.com/langsmith/analyze-an-experiment)를 참고했다. 현재 계약에서 제공하는 상태와 식별 정보만 요약 영역에 적용한다. 실험 비교는 KAN-138의 별도 범위이며 원문·정답 표시 패턴은 적용하지 않는다.


### KAN-137 SSE 자동 갱신과 재접속

화면은 상태 GET 후 비종료 실행에 한해 snapshot의 `last_event_sequence`를 `after`로 구독한다. 실제 세션을 사용하며 공통 모의 응답 모드를 우회한다. SSE 연결 상태(연결 중/연결됨/재접속 중/갱신 중단)는 실행 상태와 별도로 표시한다.

- heartbeat는 상태 변경으로 해석하지 않는다. 작업 ID·SSE id·JSON sequence·event type 일치를 확인하고 같은 위치 이하의 중복은 건너뛴다. 더 큰 순번을 받으면 해당 스트림을 취소하고 GET으로 최신 상태를 읽는다. 순번 공백도 같은 방식으로 복구한다. 이벤트 payload를 화면 상태로 투영하거나 원문을 표시하지 않는다.
- 각 변경 신호마다 최신 GET→새 snapshot 커서 구독을 순차 실행한다. 중간 이벤트 이력의 완전 재생/타임라인 제공이 아니라 **현재 상태 갱신**이 목적이다. 종료 snapshot이면 구독을 멈춘다. 최종 시험 잠금·합성 평가 한정은 유지한다.
- EOF·204·410·네트워크 단절/시간 초과·HTTP500/502/504는 이전 상세를 숨기고1/2/4초 간격으로 최대3회 상태 GET부터 복구한다. 상태 GET만 성공했다고 재시도 횟수를 초기화하지 않는다. 정상 변경 신호를 받아야 초기화한다. 반복 실패 시 수동 새로고침을 안내한다.
- HTTP400/401/403/404/503과 잘못된 프레임/콘텐츠 형식은 자동 재시도하지 않고 상세를 제거한다. 헤더 이후 권한 회수로 스트림이 닫히면 다음 GET의401/403 등으로 중단한다. 연결 종료를 평가 실패/완료로 간주하지 않는다.
- fetch 스트림은 헤더15초, 다음 수신45초 제한을 두며 heartbeat 수신으로 유휴 제한을 갱신한다. 미완성 프레임은64Ki 문자를 넘으면 거부한다. 변경 수신·화면 이탈·재조회 시 reader와 요청·대기 타이머를 정리한다.

프론트 소비자 검사는 분할 CRLF/heartbeat/중복·순번 공백,204/410/EOF,권한 오류,재시도 상한,취소,헤더/유휴 제한을 포함한다. `tests/platform/test_web_job_events.py`는 실제 TypeScript 소비자→Node 웹→FastAPI→격리 PostgreSQL에서 취소 종료 복구와 세션 회수 뒤 상세 제거를 확인한다. 브라우저 배치는 합성 HTTP 스트림으로 별도 확인하며 공용 GCP 프록시·운영 세션·실제 모델 검증으로 해석하지 않는다.


### KAN-137 실행 취소·검토 보류 해제 화면과 중계

웹 `EVALUATION_CONTROL_RELAY_ENABLED`는 읽기 중계와 **별도로 기본false**다. 명시 활성화 시 정확한 UUID의 `GET /api/v1/platform/evaluations/runs/{run_id}`, 본문 없는 `POST .../{run_id}/cancel|resume`만 중계한다. 접수/policy/임의 동작은 열지 않는다. 현재 플랫폼의 local/test·지정 평가자·소유권·최종 시험 잠금과 현재 승인 검사도 그대로 적용한다. 실제 공용 설정은 이번 구현에서 변경하지 않는다.

- 쿼리/본문/다른 메서드를 거부하고 POST는 Origin·CSRF·멱등 키·따옴표 판본을 요구한다. Cookie는 세션만, 변경 헤더는 Origin/X-CSRF-Token/If-Match/Idempotency-Key만 전달한다. 서버 IAM은 웹 공급자를 사용하며 브라우저 Authorization·사용자/역할 헤더를 전달하지 않는다.
- 응답은 최대1MiB JSON,ETag/X-Request-ID/실제 HTTP 상태만 전달한다. Set-Cookie/Location은 제거하며 redirect/HTML/시간 초과는502다.15초 제한과 브라우저 연결 종료 취소를 적용한다. 중계가 요청을 취소했더라도 DB commit이 취소됐다는 뜻은 아니다.
- UI의 `실행 취소`는 미종료 상태에서만, `검토 보류 해제`는 미종료이면서 needs_review=true일 때만 선택한다. 취소는 terminal CANCELLED이며 재개할 수 없음을 확인 화면에서 알린다. 서버가 지원하는 미소유 QUEUED 재개까지 UI에서 확대하지 않는다.
- 선택 시 실행 GET의 job/run 식별자·ETag/판본을 확인한다. 화면 snapshot과 판본이 다르면 새 상태부터 확인한다. 동작·대상·ETag·UUID 멱등 키를 고정한 후 명시 확인을 받아 POST한다. 전송마다 실제 `/me`의 현재 CSRF 헤더를 얻으며 CSRF·세션·IAM 자격증명을 저장하지 않는다.
- 동시 클릭은 하나의 진행 요청으로 제한한다. POST 자동 재시도는 없다.409/412 등 확정 거부는 최신 상태를 다시 조회하도록 안내하며 임의로 판본을 바꿔 재실행하지 않는다. 처리 응답은 최초 응답 재생일 수 있으므로 성공 안내는 '요청 처리 확인'이고 현재 상태는 별도 GET/SSE로 확인한다.
- POST 전 현재 탭 sessionStorage에 `evaluation-command:{job_id}`로 대상/동작/ETag/멱등 키만 보존한다. 저장 실패 시 전송하지 않는다. 네트워크 단절·5xx·잘못된 성공 응답은 결과 불명확으로 남기고 다른 제어 명령을 차단한다. 같은 탭에서 이동/새로고침해도 수동 `같은 요청으로 재확인`만 허용한다. 인증 실패가 나도 이미 불명확했던 명령은 지우지 않는다. 확인된 성공 또는 처음 시도의 확정 거부에서만 보존 정보를 제거한다. 탭을 닫으면 이 정보의 보존을 보장하지 않는다.
- 저장된 정보가 손상되거나 읽을 수 없으면 제어를 잠근다. 세션이 바뀌어도 서버의 현재 소유권/평가자 검사를 통과해야 하며 저장 정보 자체로 권한을 부여하지 않는다.

실제 PostgreSQL 통합 검사는 TS 제어 소비자→웹→플랫폼에서 취소·보류 해제 commit 뒤 응답 유실/동일 명령 재전송 시 판본이 한 번만 증가함을 확인한다. 실제 CSRF/Origin/판본 거부도 검증한다. 브라우저 합성 스트림 검사는 확인 단계·중복 클릭·불명확 결과·새로고침 복원·판본 충돌·취소 종료/재개 차단을 확인한다. 운영 배포·실제 모델·새 접수 UI·dispatcher/worker 인증은 이 단위의 완료 범위가 아니다.

### KAN-88 합성 실행 집계 조회

`GET /api/v1/platform/evaluations/runs/{run_id}/summary`는 명시적으로 접수를 켠 local/test에서
현재 지정 평가자 본인이 생성하고 요청한 고정 C/S synthetic/fake 실행만 조회한다.
실제 세션·등록 사용자·업무 역할을 매 요청 재검사한다. 없는 실행·다른 소유자·test split은404,
평가자/구성 승인 철회는403, 구성 hash/대상 변조는409, 운영/비활성/DB·집계 장애는503이다.
구성 철회 후 기존 제한 상태 GET·취소는 유지하지만 이 요약은 허용하지 않는다.
`cs-run-summary-v2`부터는 아래 "KAN-88 잠금 정답 품질 요약"의 비합성 잠금 경로도 함께 조회한다.

실험 소유권·현재 승인 구성 검증과 사용량·응답 감사를 하나의 `REPEATABLE READ READ ONLY`
트랜잭션에서 처리한다. 응답의 generated_at은 트랜잭션 시작 시각이다. 집계 중 다른 연결의
commit은 다음 조회에 반영된다. 성공/오류 모두 no-store이며 상태 제어용 ETag를 집계 캐시
판본으로 재사용하지 않는다. 읽기 쿼리에는 5초 제한, 연결에는 3초 제한을 적용한다.

`cs-run-summary-v1`은 실행 ID·상태·고정 대상 수·최초 시도 미저장 대상 수와
first/retry/all의 저장 시도 수·미종료 시도 수, 토큰 5종의 보고/누락 수·부분합·전건 합계,
지연 표본 수·누락 수·p50/p95, 원시 응답 존재 수·스키마 위반 수/비율·증거 불완전 시도 수만
반환한다. 모든 값은 저장된 합성 기록이며 실제 호출량/품질 측정으로 해석하지 않는다.
미보고 합계·표본0의 지연·응답0 또는 증거 불완전의 위반율은 null이다.

품질은 `quality_available=false`, `quality_unavailable_reason=GOLD_NOT_CONNECTED`로 표시한다.
정답 코드·예측·사례/대상 ID·입력/출력 본문·실험 구성·가격표 문자열은 내보내지 않는다.
내부 보고서 전체를 그대로 직렬화하지 않고 DTO에 열거한 집계만 투영한다. 비용은 아래
선택적 집계로 연결하며 폴백 상세·정답 인수·품질 점수·실제 모델 실행·운영 활성화는 후속 범위다.

### KAN-137 실험·실행 목록과 새 접수 화면

- `GET /api/v1/platform/evaluations/experiments`: 지정 평가자 본인이 만든 실험 최근 50개(커서 없음). `experiment_id`·`name`·`purpose`·`data_split`·`data_origin`·`model_id`·`target_count`·`created_at`·`admissible`·`blocked_reason`. `blocked_reason`은 접수와 같은 `validate_smoke_experiment()`를 그대로 불러 얻은 코드(`FINAL_TEST_LOCKED`·`EXPERIMENT_NOT_APPROVED`·`LOCAL_SMOKE_ONLY`·`EXPERIMENT_HASH_MISMATCH`)이고, 서버 접수가 꺼져 있으면 `ADMISSION_UNAVAILABLE`이다. 대상 집합 hash 대조는 접수 때 한다. 구성 원문·`dataset_manifest`·hash·대상 ID는 응답에 넣지 않는다.
- `GET /api/v1/platform/evaluations/runs`: 본인 접수 실행 최근 50개(`run_id`·`job_id`·`experiment_id`·`status`·`requested_count`·`needs_review`·`failure_code`·`created_at`). `GET /runs/{run_id}`와 같은 조건(본인 요청·본인 실험·접수 기록·제어 행)이며 최종 시험(`data_split='test'`) 실행은 숨긴다. 원시 출력·정답은 없다.
- 화면의 **재시도**는 같은 실험을 새 `Idempotency-Key`로 다시 접수하는 것이다(새 run). 기존 실행을 다시 돌리지 않는다. 같은 접수 시도 안에서 응답을 받기 전 재전송할 때는 같은 키를 다시 써서 서버가 최초 응답을 재생한다.
- 화면(`frontend/src/pages/RunAdmission.tsx`, 평가 실행 화면 위쪽): 정책의 `admission_available`이 false면 폼을 닫고 "실제 모델·실제 데이터 접수는 실행기 연결 뒤"를 안내한다. 실험 선택은 접수 가능한 실험만 고를 수 있고 나머지는 사유(최종 시험 잠금 등)와 함께 표시한다. 모델·설정은 실험에 고정된 값을 읽기 전용으로 보여준다. 접수는 `/me`의 CSRF와 시도마다 새로 만든 멱등 키로 실험 ID 하나만 보낸다. 결과가 불명확(네트워크·5xx·응답 형식 오류)하면 같은 키로 다시 확인하고, 거절(4xx)이면 다음 접수는 새 키다. 결과를 모르는 접수가 남아 있는 동안에는 실험 변경과 '같은 실험으로 다시 접수'를 막아 앞 접수의 키를 잃지 않는다. 성공하면 작업 ID로 기존 상태 화면을 연다. '내 실행' 표에서 열기와, 실패·취소·시간 초과·일부 완료 실행의 같은 실험 재접수를 제공한다.
- 웹 중계(`frontend/evaluation-relay.mjs`, 둘 다 기본 비활성): 읽기 플래그(`EVALUATION_READ_RELAY_ENABLED`)에 `GET /policy`·`GET /experiments`·`GET /runs`(쿼리·본문 없음)를 더했다. 접수 `POST /runs`는 제어 플래그(`EVALUATION_CONTROL_RELAY_ENABLED`) 아래 별도 분기로, JSON 본문 1~1024바이트·`Content-Type: application/json`·`Idempotency-Key`·`Origin`·`X-CSRF-Token` 필수, `If-Match`는 선택(있으면 그대로 전달), 쿼리 400이다. 같은 경로라도 GET은 읽기, POST는 제어로 나눈다. 응답의 `Location`은 실행 상태 경로일 때만, `Retry-After`는 그대로 넘긴다. `GET /runs/{run_id}`는 기존대로 제어 플래그 경로다.

### KAN-138 한 실행의 집계 화면

읽기 중계를 명시적으로 켜면 기존 `EVALUATION_READ_RELAY_ENABLED` 아래 정확한 UUID의
`GET /api/v1/platform/evaluations/runs/{run_id}/summary`를 허용한다. 기본 비활성은 유지한다.
쿼리·본문·다른 메서드·추가 하위 경로를 거부하고 기존 세션 쿠키/서버 IAM 전달,15초 제한,
최대1MiB JSON, 실제 오류 상태 보존을 재사용한다. 제어 중계나 운영 평가를 활성화하지 않는다.

평가 화면은 상태 GET으로 얻은 run_id를 사용해 집계를 조회한다. 상태 확인 시점이 바뀔 때만
새로 읽으며 SSE 연결 상태 변화만으로 중복 조회하지 않는다. 상태 재조회/재접속/오류/탭 숨김/
작업 변경/화면 이탈 시 표와 이전 요청을 정리한다. 늦은 응답은 표시하지 않는다.
집계 조회 실패 때도 이전 표를 숨기고 실제 상태에 맞는 안전한 오류를 안내한다. 모의 응답으로
대체하거나 자동 재시도하지 않으며 실행 현황 새로고침으로 상태·권한부터 다시 확인한다.

화면은 최초·재시도·전체 저장 시도,5종 토큰,지연 p50/p95 및 보고/미보고 표본 수,
스키마 위반 수/저장 응답 수·위반율·증거 불완전 시도 수를 표시한다. 집계 시점의 상태·시각을
실행 상태와 별도로 표시한다. null은 미보고 또는 판정 보류이며0으로 변환하지 않는다.
부분합에는 누락 건수를 붙인다. 합성 연결 검사이며 실제 품질/사용량 측정이 아님을 명시한다.
품질 점수는 정답 검수·연결 전으로 표시하고 실험 간 비교·CSV는 후속 범위다.


### KAN-138 저장 비용 연결

`cs-run-summary-v1`의 first/retry/all에 선택 필드 `cost_usd`를 추가한다. 구버전 응답에서
누락되거나 null이면 소비자는 비용 집계 미연결로 표시한다. 현재 서버는 각 묶음의 비용
보고/미보고 건수, `known_sum`(보고된 부분합), `total`(전건 보고한 합계),
`pricing_version_count`(보고된 비용의 서로 다른 가격표 판본 수)를 반환한다.

금액은 비음수 USD 십진 **문자열** 또는 null이다(최대128자, 지수/부호/공백 없음).
DB의 Decimal 합계를 float로 바꾸거나 브라우저에서 Number/통화 소수2자리로 반올림하지 않는다.
시도가 없거나 비용이 전부 미보고면 부분합과 합계 모두 null, 한 건이라도 미보고면 합계는null이다.
실제 기록된0은 `0.000000`처럼 문자열로 보존한다. 가격표가 없는데 무료라고 추정하지 않는다.
가격표 원문·제공자 진단은 노출하지 않으며 판본이 여럿이면 혼합임을 표시한다. 이 값은
각 시도에 저장된 비용의 합이지 공통 단가로 재계산하거나 검증한 청구액이 아니다.

최초 비용을 재시도 비용으로 대체하지 않는다. API는 기존 같은 스냅샷의 사용량 집계를
재사용하고 세션·소유권·승인·시험 잠금을 유지한다. 유효하지 않은 저장 금액/가격표는503이며
0으로 대체하지 않는다. 소비자도 금액 형식/보고 범위가 잘못되면 전체 집계 오류로 처리한다.
고정 합성 worker는 비용을 추정하지 않으므로 기본 비용은 미보고다. 비용 연결 검사는
격리 DB/화면 fixture의 명시적 합성 금액으로 수행하며 실제 모델·과금 검증이 아니다.


### KAN-88 합성 품질 선택 필드

`cs-run-summary-v1`에 선택 필드 `synthetic_quality`를 호환 추가한다. `quality_available=false`,
`quality_unavailable_reason=GOLD_NOT_CONNECTED`는 그대로다. 합성 정답으로 낸 값은 실제 정답 연결이나
품질 측정이 아니기 때문이다. 구버전 응답의 누락과 null은 모두 "합성 품질 미저장"이다.

서버는 위 요약 권한 검사(세션·평가자·소유권·승인 구성·합성 제한·대상 집합)를 모두 통과한 뒤 같은
읽기 전용 트랜잭션에서 `evaluation.metrics`의 실행 단위 `return_subtype_first_*` 행만 SELECT한다.
행은 운영자 명령(`python -m eval.cs_report store-return-subtype`)이 저장하며, 서버는 정답 파일·
`cases.gold_labels`·사례 시도를 읽지 않는다. 같은 실행에 채점 판본이 여럿이면 저장 시각이 가장 늦은
판본 하나(동률이면 판본 문자열 역순 첫째)를 보이고 `stored_scoring_version_count`로 판본 수를 알린다.
추가 전용 재채점은 새 판본을 쌓으므로 판본이 여럿이라는 이유로 요약 전체를 막지 않는다.

필드: `metric=return_subtype`, `attempt=first`, `data_origin=synthetic`, `quality_gate_evaluated=false`,
`scoring_version`, `stored_scoring_version_count`, `stored_at`, `gold_version`, `gold_sha256`(정답 파일
SHA-256), `run_status_at_scoring`, `target_count`, `scored_target_count`, `correct`, `accuracy`,
`macro_f1`, `null_predictions`, `outcomes`(VALID·SCHEMA_VIOLATION·TRANSPORT_FAILURE·TIMEOUT·HELD·
NOT_STARTED 건수), `per_class`(DEFECT·NON_DEFECT의 support·tp·fp·fn·precision·recall·f1).
사례/대상 ID, 사례별 정답·예측, 원시 출력, 정답 파일 경로는 없다. 합격 판정은 하지 않는다.

선택한 판본의 행이 저장 계약과 다르면(행 누락·추가, segment가 all이 아님, threshold·comparison·passed
기록, details 키·판본·data_origin·정답 판본 불일치, 분모·outcomes·클래스 support 합 불일치, 범위 밖
수치) 부분 표시 없이 503이다. 화면 표시는 후속 범위이며 현재 소비자는 이 필드를 읽지 않는다.


### KAN-138 실행 비교 키

`cs-run-summary-v1`에 선택 필드 `comparison_key`를 호환 추가한다. 두 실행의 요약을 각각 조회한
소비자가 실험 구성 원값을 받지 않고 비교 가능 여부를 판단하기 위한 값이다. 새 비교 엔드포인트는 없다.

키 판본은 `cs-comparison-key-v1`이다. 입력은 `key_version`과 실험의 `purpose`·`data_origin`·
`dataset_hash`·`data_split`·`gold_version`·`target_set_hash`·`policy_version` 7개로,
`eval.cs_stored_metrics.compare_stored_runs`의 비교 가능 조건과 같다. 이 객체를 키 정렬·구분자
`(",", ":")`·ASCII 이스케이프·null 그대로의 JSON으로 직렬화한 UTF-8 바이트의 SHA-256 소문자 hex가
키다. 조건 집합이나 직렬화를 바꾸면 키 판본을 올리며, 판본이 다른 키는 같은 조건이어도 다르다.

키는 실행 상태와 무관하게 실험 조건만으로 정해지며 미종료 실행에도 반환한다. 비교 가능 여부는
키 일치와 두 실행의 `status`가 종료 상태인지를 소비자가 함께 확인한다(조건과 상태를 한 값에 섞지
않는다). 7개 원값은 응답·로그에 내지 않는다. 키가 같다는 것은 위 조건이 같다는 뜻일 뿐 구성·모델·
프롬프트·가격표 판본이 같다는 뜻이 아니며, 품질 차이는 합성 품질의 정답 판본·채점 대상까지 같을 때만
계산한다. 개선·퇴행 판정은 하지 않는다.

키는 비밀이 아니며 원값 은닉을 보장하지 않는다. 입력 값의 범위가 좁아 후보를 대입해 추측할 수 있다.
요약 조회는 그 실험을 만든 평가자 본인의 실행으로 제한되므로 조회자에게 새로 노출되는 정보는 없다.
조회 범위를 넓히거나 실제 데이터로 확장할 때는 서버 비밀 기반 HMAC으로 키 판본을 올린다.

평가 화면은 현재 실행을 후보로, 사용자가 입력한 run_id를 기준으로 요약을 각각 조회해 최초 시도
집계를 기준·후보·차이(후보−기준) 3열로 나란히 둔다. 같은 실행, 키 누락(구버전), 키 불일치, 한쪽 미종료면
표 없이 이유만 표시한다. 토큰은 두 실행 모두 전건 합계(total)일 때만, 지연 p50/p95는 두 실행 모두
보고됐을 때만 차이를 계산하고 표본 수를 함께 보인다. 비용은 가격표 판본 동일성을 확인할 수 없어
나란히 표시만 하며 차이를 계산하지 않는다. 합성 품질 accuracy·macro-F1은 두 실행 모두 저장되고
gold_sha256·gold_version·scored_target_count가 같을 때만 차이를 계산한다. 차이는 부호 있는 숫자로만
표시하고 색·등급·개선/퇴행 표현을 쓰지 않는다.


### KAN-88 잠금 정답 품질 요약

2026-10-28 사용자 결정으로 요약 응답을 `cs-run-summary-v2`로 올리고 조회 대상을 넓힌다. 합성 smoke 경로의
요약과 기존 필드는 그대로이며 판본 문자열만 v2다. 소비자는 v1 응답을 집계 오류로 처리한다.

접근 경계는 두 경로 모두 local/test·접수 켬·지정 평가자·실험 생성자=요청자=본인·접수 영수증·
test split 404·승인 구성 hash(아니면 403 EXPERIMENT_NOT_APPROVED)·구성 hash 재계산(다르면 409)이다.
실험 purpose가 smoke이면 기존 합성 경로(고정 프로필·fake·local/test runner·고정 대상, 위반 403
LOCAL_SMOKE_ONLY·409 TARGET_SET_MISMATCH)를 따른다. 그 밖은 잠금 경로로, purpose pipeline·rule_replay,
실행 provider와 model_id가 fake가 아니고 합성 프로필이 아니며 data_split이 train·valid인 실행만 허용하고
위반은 403 LOCKED_RUN_REQUIRED다. 잠금 경로는 고정 대상 목록이 없으므로 실험 대상 ID를 정렬해
줄바꿈으로 이은 SHA-256이 `target_set_hash`와 다르면 409 TARGET_SET_MISMATCH다. production 활성화,
원문·모델 출력·정답·사례 ID 노출은 없다.

필드 변경: `data_origin`은 실험 값(synthetic·demo·real)이다. `quality_available`은 boolean,
`quality_unavailable_reason`은 GOLD_NOT_CONNECTED·GOLD_PARTIAL·null이다. 새 선택 필드
`locked_quality`={`bundle_id`, `attempt=first`, `quality_gate_evaluated=false`, `metrics`}이며 metrics는
category·urgency·secondary·return_subtype·provided_info 다섯 키를 항상 갖고 저장되지 않은 지표는 null이다.

| 조건 | quality_available | reason | locked_quality |
| --- | --- | --- | --- |
| 합성 smoke 경로 | false | GOLD_NOT_CONNECTED | null (synthetic_quality는 기존대로) |
| 잠금 경로, 실험 gold_version이 정답 묶음 ID가 아님 | false | GOLD_NOT_CONNECTED | null |
| 잠금 경로, 계약 행이 있는 지표 0개 | false | GOLD_NOT_CONNECTED | 다섯 지표 모두 null |
| 잠금 경로, 1~4개 | false | GOLD_PARTIAL | 저장 지표만 값 |
| 잠금 경로, 5개 모두 | true | null | 다섯 지표 모두 값 |

서버는 같은 읽기 전용 트랜잭션에서 지표마다 `cs_locked_<metric>_first_*` 실행 단위 행만 읽는다.
판본 선택은 합성 품질과 같이 저장 시각이 가장 늦은 채점 판본 하나이며 `stored_scoring_version_count`로
판본 수를 알린다. 지표 값: `scoring_version`, `stored_scoring_version_count`, `stored_at`, `gold_version`,
`gold_sha256`, `run_status_at_scoring`, `target_count`, `scored_target_count`, `segments`. segment는
provided_info만 `scope=FULL`·`scope=PRECISION_ONLY`이고 나머지는 `all`이다. 각 segment는
`locked_denominator`, `target_count`(그 범위의 채점 대상 수), `outcomes`, `excluded_by_reason`,
`values`(집계 키별 value·numerator·denominator·support), `per_class`(클래스 F1 행의 support·tp·fp·fn·
precision·recall·f1, provided_info는 빈 객체), `items`(provided_info만 집계 tp·fp·fn, PRECISION_ONLY는
tp·fp, 그 외 null)를 갖는다. 집계 키는 `docs/evaluation.md`의 "KAN-88 잠금 정답 품질 저장 행"과 같다.
두 provided_info 범위는 합하지 않는다. 합격선·판정은 없다.

선택 판본의 행이 계약과 다르면 부분 표시 없이 기존 집계 실패 규칙대로 503이다. 행 집합 누락·추가,
threshold·comparison·passed 기록, details 키·segment 간 공통값 불일치, 판본·묶음 ID·정답 판본·SHA-256이
`eval/cs_gold_locks.py` 잠금표와 다름, data_origin·data_split이 실험과 다름, 잠근 split 분모·outcomes 합·
제외 수·클래스 support 합 불일치, 범위 밖 수치가 해당한다. 서버 로그에는 run_id·지표·위반 종류
(`LOCKED_ROW_SET`, `LOCKED_JUDGEMENT_RECORDED`, `LOCKED_DETAILS`, `LOCKED_LOCK_MISMATCH`,
`LOCKED_DENOMINATOR`, `LOCKED_VALUE_INVALID`)만 남기고 저장 값·검증 메시지는 남기지 않는다.

잠금 경로는 서버 비밀 기반 HMAC 키 판본을 도입하기 전까지 `comparison_key=null`이며, 잠금 실행 간
비교는 운영자 내부 연결의 `eval.cs_stored_metrics.compare_stored_runs`(별도 명령줄 진입점 없음)로만 한다. 소비자는 연결 여부·사유를 저장
지표 수로 다시 계산해 응답과 다르면 집계 오류로 처리하고, synthetic_quality와 locked_quality가 함께
오면 거부한다. 화면은 사유에 맞는 품질 문구만 바꾸며 잠금 품질 수치 표시는 후속 범위다.
