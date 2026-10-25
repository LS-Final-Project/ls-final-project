# 플랫폼 평가 저장 계약 (KAN-85)

## KAN-90 접근 정책 API — 초기 구현

사용자 결정으로 KAN-90과 평가 화면 KAN-137·138·139는 오새결 담당이다. KAN-85 완료 이력은 유지한다.

`GET /api/v1/platform/evaluations/policy`는 활성 Microsoft 운영 세션을 재조회하고 서버의 `EVALUATION_EVALUATOR_USER_IDS` JSON UUID 배열에 현재 내부 사용자 ID가 있는지 검사한다. 기본 배열은 비어 있어 모두 거부한다. 이메일·업무 역할·클라이언트 사용자 헤더·개발 계정 선택으로 허용하지 않는다. 서버 배포 설정으로만 지정하며 이번 구현에서 실제 오새결 UUID나 계정 권한을 등록하지 않았다.

운영 인증 비활성/세션 누락·철회는401, 미지정 사용자는403, 세션 조회 장애는503이다. 성공·오류 응답 모두 캐시를 금지한다. 데이터·프로젝트별 접근 검사는 후속 조회/실행 API에서도 별도로 필요하다. 이 정책 응답은 원문·정답·대상 목록을 포함하지 않는다.

2026-10-25 사용자 결정에 따라 `primary_quality_view=first_attempt`, `final_result_reported_separately=true`를 반환한다. 해당 AI 품질 합격선은 최초 호출에 적용하고 모든 원시 응답의 스키마 위반0 기준을 유지한다. 실제 복구 정책·신규 합격선 확정은 별도다.

실제 모델 평가 실행기는 미연결이므로 `execution_available=false`, `execution_unavailable_reason=RUNNER_NOT_CONNECTED`, `final_test_available=false`, `raw_output_available=false`를 유지한다. `admission_available`은 아래 로컬 합성 **저장 접수** 가능 여부다. `local_smoke_execution_available`은 같은 명시 설정에서 CLI 합성 worker 사용 가능 여부이며 실제 모델 실행 가능 여부와 다르다. 합성 실행의 취소·재개는 아래 local/test 제어 계약을 따른다. 실험 등록·평가 결과 조회·화면은 아직 미구현이다.

## KAN-90 로컬 합성 저장 접수 — 운영 실행 미연결

`POST /api/v1/platform/evaluations/runs`는 `experiment_id` UUID 하나와 8~128자의 `Idempotency-Key`를 받는다. split·모델·명령·설정 덮어쓰기 필드는422로 거부한다. 현재 세션·지정 평가자 검사 후 `Origin`이 서버 app_origin과 일치하고 `X-CSRF-Token`이 세션에 결속됐는지 확인한다. 실패는403이며 기존 `/me`의 CSRF 발급 방식을 재사용한다.

기본 접수는 비활성이다. **local/test** 환경에서만 서버 `EVALUATION_LOCAL_ADMISSION_ENABLED=true`와 `EVALUATION_APPROVED_CONFIG_HASHES` JSON 배열을 명시하여 검수한 구성 hash를 허용할 수 있다. production에서는 이 설정을 켜도503이다. 이번 작업에서 실제 계정·실험을 허용하거나 공용 설정을 변경하지 않았다.

현재 지원 범위는 요청자가 생성한 실험 중 `purpose=smoke`, `data_origin=synthetic`, `model_id=fake`, 빈 configuration·dataset_manifest뿐이다. 모든 대상은 synthetic이며 attributes가 비어 있어야 한다. 일반 실험·실제 데이터·외부 모델은 거부한다. `data_split=test`는 허용 hash에 있어도403 FINAL_TEST_LOCKED로 거부하며 대상·원문·정답을 읽지 않는다. 다른 생성자나 없는 실험은404다. 합성 테스트에서 이 조건들을 검증하며 실제 시험 자료는 사용하지 않는다.

구성 hash를 기존 canonical JSON 규칙으로 재계산하고, 비어 있지 않은 대상 집합의 hash를 확인한다. 이때 읽고 검증한 대상 snapshot만 사례 생성에 사용한다. 등록 이후 대상 추가/구성 변경으로 hash가 달라지면409다. 자료의 출처 진실성이나 검수 품질을 hash만으로 증명하지 않는다. 사전 검수된 허용 목록 등록은 별도 운영 책임이며 실험 등록·데이터 검수 API는 미구현이다.

### 한 번의 저장과 재전송

KAN-69의 작업 유일 키를 먼저 확보한 뒤 신규 요청만 run·고정 대상별 PENDING case·run_control·최초 응답 request ID를 동일 transaction에 저장한다. 작업·최초 이벤트·outbox를 포함한 어느 단계에서든 실패하면 모두 rollback한다. commit 후에만202를 반환한다. 동일 키·동일 정규화 요청은 기존 run/job과 최초 응답 body·Location·Retry-After를 재생한다. 다른 experiment_id 또는 If-Match면409다. If-Match는 신규 생성의 판본 전제조건이 아니며 멱등 요청 구분에 포함한다. HTTP X-Request-ID 헤더는 현재 요청 추적 ID이고 body meta.request_id는 최초 접수의 ID다.

202의 data는 run_id·job_id·status=QUEUED·status_url이다. Location은 실제 구현한 `GET /api/v1/platform/evaluations/runs/{run_id}`이며 조회는 현재 평가 권한·실험 생성자·요청자를 다시 검사하고 test split은 숨긴다. 구성 hash 허용이 철회돼도 담당자가 보류 원인과 최신 판본을 확인해 취소할 수 있도록 제한된 상태 조회는 유지한다. 평가자 지정이나 세션 권한이 철회되면 조회도 거부한다. 접수 재전송·재개는 구성 허용을 다시 검사한다. 조회는 실행/job ID·상태·요청 건수·version·needs_review·failure_code만 반환하고 원시 출력·정답을 노출하지 않는다.

현재는 일반 jobs/SSE 경로가 구현되지 않았으므로 공통 비동기 응답의 events_url을 제공하지 않는 **local/test 전용 부분 계약**이다. 실제 운영 접수 활성화 전에 공통 jobs/SSE 연결과 권한 검증을 완료해야 한다. 접수 자체는 QUEUED만 만든다. 아래 로컬 worker를 명시적으로 실행하면 RUNNING→SUCCEEDED로 진행한다. Cloud Run Jobs dispatcher는 아직 없다. 이 접수의 provider=fake, runner_kind=test, scoring_version=smoke-not-scored-v1은 미채점 연결 검사 표시이며 실제 품질 점수를 의미하지 않는다.

DB 대기 상한은 초기 로컬 보호값으로 lock_timeout=2초, statement_timeout=5초다. Retry-After=5초도 조회 안내값이며 실측 SLA가 아니다. DB 오류/commit 불명확 시503이며 같은 키로 재확인한다. 호출자가 새 키를 발급해 중복 접수하지 않아야 한다.

### 마이그레이션과 남은 범위

KAN-85 → `20261025_KAN-69_job_admission.sql` → `20261025_KAN-90_run_admission.sql` 순으로 적용한다. run/job의 일대일 및 동일 business ID를 FK로 검증한다. 기존 non-null job_id가 runtime.jobs와 맞지 않으면 마이그레이션은 실패하므로 기존 레코드를 임의 수정하지 말고 이관 근거를 확인한다. run_control·admission_receipts는 platform_app에 SELECT·INSERT만 부여한다. generation=0/holder=null은 미실행 상태다. 아래 보완 migration과 로컬 worker는 합성 lease 획득·갱신을 제공하며 사용자 취소·재개는 아래 run_actions migration을 추가로 적용해야 한다.

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

**이 복구는 외부 효과가 없는 FakeProvider 전용이다.** transaction 중 실제 모델 네트워크를 호출하는 구현으로 확장하지 않는다. 외부 모델은 intent/dispatch 결과 불명확(UNKNOWN)/사용량 보존 규약이 먼저 필요하다. 중단된 합성 함수를 다시 호출할 수는 있지만 commit된 사례·시도는 하나만 남는다. 이를 실제 모델 호출의 exactly-once 보장으로 해석하지 않는다.

검사에는 HTTP 접수→outbox→합성 실행→완료 조회, 종료 뒤 최초 접수 응답 재생, 동시 소유권 경합, 저장 단계 실패, 만료 worker 차단, 사례 일부 완료 후 재개, 종료 직전 재개, 별도 프로세스의 transaction 중 비정상 종료를 포함한다. 실제 배포·SSE·운영 worker 인증·실제 모델 품질은 별도 검증 대상이다. 합성 실행의 취소·재개 검사는 아래와 같다.


## KAN-90 합성 실행 보류·취소·재개

이 절은 local/test의 합성 실행 제어 구현 계약이다. production 활성화나 실제 모델 호출 취소 보장이 아니다. `20261025_KAN-90_smoke_worker.sql` 다음에 `20261025_KAN-90_run_actions.sql`을 적용한다. 새 migration은 이번 작업의 임시 검사 DB에서만 적용하며 공용 DB에는 적용하지 않았다. 기존 접수·worker migration을 재적용할 필요는 없다.

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
