# 플랫폼 평가 저장 계약 (KAN-85)

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
