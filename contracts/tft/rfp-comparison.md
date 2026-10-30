# RFP 자사 비교·네 가지 상태·보완 경로 (KAN-186 A05, 중간)

원본: `backend/api/tft/rfp/comparison.py`(비교·상태·혼동표), `backend/api/tft/rfp/supplement.py`(보완 경로), 자사 사양 `backend/api/tft/rfp/own_spec_facts.json`. 기획서(RFP 최종본 V_1) 4.2~4.4와 RFP API 명세 v1.3(2026-10-30) §2·§5를 따른다. **상태: 순수 함수와 시연 A 검사.** API 응답(`Comparison`·`SupplementOptions`)과 저장(`product_comparisons` 등)에 붙이는 일은 A07(KAN-188)이다.

## 후보 찾기와 비교 판정을 나눈다

- **후보 찾기**(`backend/api/tft/rfp/candidates.py` `find_candidates`): 제품 마스터 계열 필터와 문서 검색(R10 하이브리드 `run_hybrid`)을 함께 쓴다. **검색 엔진(R08~R11: 청킹·FTS·벡터·RRF·리랭킹)은 다른 담당자 구현이고, 이 티켓은 그 결과를 후보 제품과 검색 범위로 바꿔 판정 근거로 잇고 "후보 없음"과 "근거 부족"을 구분하는 소비자 쪽이다.**
  - 범위: 계열 필터(`family=`)와 검색 조건(색인 판본·상한·문서 해시 수)을 `search_trace.filters`에 그대로 남긴다. "후보 없음"은 compare 사유에 이 범위와 문서 수를 넣어 그 범위의 결과로만 말한다.
  - 문서 → 제품: 청크에는 제품 코드가 없어 색인 manifest의 문서 해시 → 제품 코드 표로 잇는다. 표에 없는 문서, 자사 후보 목록에 없는 제품은 후보로 만들지 않는다.
  - 질의는 요구 항목·속성·값·단위·인증 규격의 토큰 OR이고, 규격 코드 일치(예: `UL 1066`)는 원문을 따로 넘긴다.
  - 검색이 없으면 계열 필터만으로 동작한다. 검색이 실패하면 계열 후보로 계속 비교하고 범위에 `document_search=failed`를 남기며, 후보가 하나도 없을 때만 `search_failed`로 판정을 보류한다(검색 실패 ≠ 제품 없음).
  - 검색으로 찾은 후보에도 같은 속성·단위·조건 규칙과 IEC→IEEE 확장 금지가 그대로 적용된다(`tests/tft/test_rfp_candidates.py`).
- **비교 판정**: 같은 속성·같은 단위·같은 조건끼리만 규칙으로 비교한다. 값 비교 함수는 가상 협력사 등급(KAN-187 `partners/grading.py`)의 `_value_verdict`·`_condition_gaps`를 그대로 써서 자사와 협력사가 같은 규칙을 쓴다(decisions.md 가상 협력사 결정).
- **고객 지정 모델 요구**(KAN-187, 기획서 V_1 ⑧): 가상 협력사 후보 등급 `grade_candidate`는 `designated_model`이 있는 요구를 채점하지 않고 후보 요약 `designated_requirements`(`requirement_id`·`designated_model`·`clause_no`·`note`='고객 지정 조달')로 따로 남긴다. 협력사 후보 추천과 섞지 않기 위해서다. 남은 요구가 없으면 `overall_grade`는 `REVIEW`이고 `conditions_to_confirm`에 '모든 요구가 고객 지정 모델이라 협력사 채점 대상이 없음.'을 넣는다.
- **나눈 이유**(Jira 주의 사항): 검색은 비슷한 것을 찾는 일이라 15 kV 요구에 자사 12 kV 개폐장치도 후보로 나온다. 그 후보를 충족으로 볼지는 유사도가 아니라 정격전압 15 kV 이상인지로 정해야 한다. 그래서 비교는 숫자·단위·조건 규칙으로만 하고 점수를 만들지 않는다(검사 `test_search_finds_similar_products_but_rules_compare_only_same_unit`).

## 네 가지 상태

| 상태 | 언제 | 결과(`result`) | 다음 담당 |
| --- | --- | --- | --- |
| `EVIDENCE_FOUND` | 같은 속성 근거가 있고 값·조건이 맞음, 또는 요구 인증을 그대로 보유 | `MEETS` | 없음 |
| `CONDITIONAL` | 값은 맞지만 요구 조건에서의 값이라는 근거가 없음(조건 차이), 단위가 달라 비교 못 함(`NOT_COMPARABLE`), 타사 지정 모델(조달·대체 경로가 있고 발주처 승인이 남음) | `MEETS`·`NOT_COMPARABLE` | `TECH`(지정 모델은 `CUSTOMER`) |
| `SPEC_GAP` | 같은 속성·단위로 비교해 미달 근거가 있음. 부족 원인(`RATING`·`CERTIFICATION`·`FORM`·`TASK`·`COMMERCIAL`)을 함께 냄 | `DIFFERS` | `TECH`(상업 조건은 `SALES`) |
| `INSUFFICIENT_EVIDENCE` | 요구값을 모름(도면 참조 등, `CUSTOMER`), 같은 속성 근거가 없음, 요구 인증 보유 자료가 없음, 검색 범위에 후보 없음, 검색 실패. 핵심 기기(`core_equipment`)가 검색 범위에 없으면 부족 원인 `CORE_EQUIPMENT_MISSING` | `NO_DATA` | `CUSTOMER`·`TECH`(핵심 기기는 `SALES`) |

- **미기재 ≠ 미충족**: 자료에 없는 값은 `SPEC_GAP`이 아니라 `INSUFFICIENT_EVIDENCE`다.
- **"제품 없음"은 검색 범위의 결과**: 사유에 필터와 문서 수를 적고 "회사 전체 미보유나 참여 불가를 뜻하지 않음"을 붙인다.
- **인증**: IEC 시험 성적을 UL·IEEE 요구의 충족으로 넓히지 않는다(기획서 4.2).
  - 같은 인증의 보유 자료가 없으면 근거 부족이고, 사유에 자사가 가진 규격 체계를 적는다.
  - 등록(Listed)을 요구하는데(`certification_listed=true`) 자사는 시험 성적만 있으면 미충족 근거가 있는 **사양 차이**(부족 원인 `CERTIFICATION`)다. 보완 경로는 인증 보유 변형 → 인증 보유 협력사 조달 → 대체 사양 순이다.
  - 인증이 변형 모델(예: `-U`)에만 있으면 기본 모델로 넓히지 않고 **조건부**(변형 모델 선택 확인)다.
- **판매 상태**: 근거가 맞아도 판매 중(`ACTIVE`)이 아닌 제품(`MATURE` 등)은 조건부로 내리고 "공급 지속 여부 확인"을 남긴다. 상태가 같으면 판매 중 후보를 먼저 본다.
- **비교하지 못한 값**: 단위가 다르면 "단위 확인", 숫자로 읽지 못했거나 문자열 값이 다르면 "값 확인"을 미확인 항목에 남긴다(`NOT_COMPARABLE`이면서 미확인 항목이 비지 않게).
- **후보가 여럿이면** 가장 좋은 후보의 상태가 요구의 상태이고, 모든 후보의 비교를 `candidates`에 남긴다.
- **상업 조건**: 계약 조건 속성(`warranty_months`, 제품 마스터 표준 보증)이 미달이면 부족 원인 `COMMERCIAL`이고 다음 담당은 영업이다. 근거 한계는 "제품 마스터 표준 조건(합성)"이다.
- **핵심 기기 부재**: 사람이 `core_equipment=true`로 정한 요구(예: 154 kV GIS)가 검색 범위에 후보가 없으면, 상태는 근거 부족 그대로 두고(미기재 ≠ 미충족) 원인 `CORE_EQUIPMENT_MISSING`과 "자사 자료 보완 요청"을 남긴다.
- **타사 지정 모델(지정품)**: 조건부 상태에 부족 원인 `DESIGNATED_MODEL`을 남긴다(명세 v1.3 §5.1). 협력사 후보와 섞지 않는다.
- **혼동표**: `confusion((기대, 판정) 목록)`이 4×4 표를 만든다(행 정답, 열 판정). 평가(KAN-191)가 이것으로 상태별 결과를 공개한다.

## 보완 경로 (기획서 4.4)

`options_for(비교 결과, 고객 허용, designated, rnd_check, approval_question)`는 `SPEC_GAP`, 타사 지정 모델, 핵심 기기 부재에만 경로를 낸다. 근거 있음·조건부·근거 부족은 경로가 아니라 다음 행동(조건 확인·자료 요청)이다.

- **순위 `rank`(path_rank, 저장 안 함)**: 1 자사 계열(`OTHER_OWN_PRODUCT`, `OWN_TERMS_REVIEW`) → 2 자사 개발·수행 확인(`OWN_DEVELOPMENT`, R&D) → 3 협력사 계열(`EXTERNAL_PRODUCT`·`TASK_SUBCONTRACT`·`JOINT_PROPOSAL`·`ALTERNATIVE_SPEC`) → 4 고객 지정품 조달(`CUSTOMER_SPECIFIED_PROCUREMENT`) → 9 `NONE`. 선택지는 이 순서로 정렬하고, 같은 순위 안은 아래 원인별 표의 순서다. 협력사는 자사 대응 가능성 확인보다 앞서지 않는다.
- **게이트 `gate`**: 협력사 계열 4개는 `requires=OWN_DEVELOPMENT_CHECK`, 나머지는 `NONE`. 자사 개발로 풀 수 없는 원인(상업 조건 `COMMERCIAL`, 지정품 `DESIGNATED_MODEL`)의 경로에는 게이트를 걸지 않는다. `status`는 요구의 R&D 확인 상태 `rnd_check`(`NOT_REQUIRED`·`PENDING`·`CONFIRMED_FEASIBLE`·`CONFIRMED_NOT_FEASIBLE`·`CONFIRMED_SCHEDULE_MISMATCH`·`WAIVED`)다. `CONFIRMED_FEASIBLE`이면 협력사 계열은 `allowed=false`, `blocked_reason=OWN_DEVELOPMENT_FEASIBLE`(자사 개발이 되는데 협력사로 가지 않음).
- **R&D 확인 흐름**(`backend/api/tft/rfp/rnd.py`, 명세 v1.3 §5.2·§5.3). 저장·HTTP 연결은 A07(KAN-188)·A08(KAN-189)이 이 함수를 부른다.
  - `rnd_check_of(R&D 질문들, 면제 시각)`: 질문이 없으면 `NOT_REQUIRED`, 열린 질문이 있으면 `PENDING`. 마지막 답이 `FEASIBLE`이면 `CONFIRMED_FEASIBLE`, `NOT_FEASIBLE`이면 `CONFIRMED_NOT_FEASIBLE`, `FEASIBLE_WITH_SCHEDULE`이면 `schedule_fits`가 참이면 `CONFIRMED_FEASIBLE`·거짓이면 `CONFIRMED_SCHEDULE_MISMATCH`이고, 그 밖(`UNDETERMINED`, 일정 미기재)은 `PENDING` 유지다. 면제 뒤 새 질문이 없으면 `WAIVED`다. 기준 질문 ID(`rnd_question_id`)를 함께 돌려준다.
  - `rnd_question_for`: `OWN_DEVELOPMENT`를 고르면 검토 부서 중 분류 `RND`인 첫 부서에 내부 질문(`target=INTERNAL`, `issue_type=RND_FEASIBILITY`, 본문은 요구 요약)을 만든다. 호출자는 `next_owner_field=RND`, `rnd_check=PENDING`으로 두고 요구 상태는 바꾸지 않는다. R&D 부서가 없으면 422 `VALIDATION_ERROR`("검토 부서에 R&D 부서를 추가하세요").
  - `check_selection`: 막혔거나 선택지에 없는 경로는 409 `TFT_INVALID_TRANSITION`. 게이트가 있는 협력사 경로를 `rnd_check=PENDING`에서 고르면 면제 사유(20자 이상)가 필요하고, 없으면 422 `TFT_RND_CHECK_REQUIRED`(details에 `{field: rnd_question_id, reason: 열린 질문 ID}`, ID를 모르면 빈 목록)다. 사유가 있으면 선택 판본에 `WAIVED`가 남는다(초안 ASSUMPTIONS 문장은 KAN-188).
  - `check_reply_fields`: `rnd_feasibility`·`earliest_available_on`·`schedule_fits`는 분류 `RND` 부서 대상 내부 질문에만 받는다(아니면 422 `TFT_QUESTION_FIELD_NOT_ALLOWED`). 일정 칸만 있고 개발 가능성이 없으면 422 `VALIDATION_ERROR`.
  - 부서 분류는 `tft.department_kinds`이고 매핑이 없는 부서는 `TECH`다. 시드는 시연 계정 부서(`SALES_OPS` 영업, `RND_FW` R&D, `TS_TRF`·`TS_MV`·`TS_LV`·`CERT` 기술, `SCM` 구매)뿐이다.
- `OWN_TERMS_REVIEW`는 명세 v1.3에 없고 추가를 요청 중이다(KAN-186 댓글, 제안 순위 1).

| 부족 원인 | 경로(순서) |
| --- | --- |
| 정격 `RATING` | 다른 자사 제품 → 자사 개발 확인 → 외부 제품 조달 |
| 인증 `CERTIFICATION` | 다른 자사 제품(인증 보유 변형) → 외부 제품 조달(인증 보유 협력사) → 대체 사양 |
| 형식 `FORM` | 자사 개발 확인 → 외부 제품 조달 → 대체 사양 |
| 과업 `TASK` | 자사 수행 확인(`OWN_DEVELOPMENT`) → 과업 위탁(계약 범위 확인 조건) |
| 상업 조건 `COMMERCIAL` | 자사 조건 변경 검토(`OWN_TERMS_REVIEW`, 영업·보증) → 대체 조건 제안(`ALTERNATIVE_SPEC`, Deviation). 협력사로 풀지 않는다 |
| 핵심 기기 `CORE_EQUIPMENT_MISSING` | 공동 제안 → 보완 경로 없음(`NONE`, 보류 의견). 자사 자료 보완 요청은 미확인 항목이다. 단순 외부 조달로 내지 않는다 |
| 타사 지정 모델 `DESIGNATED_MODEL` | 지정품 조달(`CUSTOMER_SPECIFIED_PROCUREMENT`) → 대체 제안(`ALTERNATIVE_SPEC`, "고객 승인", 승인 질문이 없으면 "고객 질문 작성 필요"). `OTHER_OWN_PRODUCT`·`OWN_DEVELOPMENT`·`EXTERNAL_PRODUCT`·`TASK_SUBCONTRACT`·`JOINT_PROPOSAL`은 `allowed=false`, `blocked_reason=DESIGNATED_ITEM`(명세 v1.3 본문 §4 #15·§5.1 표기) |

- 외부 경로(조달·위탁·공동 제안)는 RFP가 금지하면(`FORBIDDEN`) `allowed=false`·`blocked_reason`으로 남겨 추천하지 않는다. 허용 여부가 적혀 있지 않으면(`UNSTATED`) "발주처 확인" 확인 조건을 붙인다. 대체 사양은 늘 발주처 승인 조건이 붙는다.
- 고를 수 있는 경로가 하나도 없으면 `NONE`(보류 의견과 해결할 조건)을 덧붙인다.

## 자사 사양 (`own_spec_facts.json`)

제공 데이터 `ref/product_spec_facts.csv`·`product_master.csv`의 문장을 협력사 프로필과 같은 열(속성·값·단위·조건·근거 ID·절)로 옮긴 파생값(`derivation_kind=DERIVED`)이다. 항목마다 원래 문장(`source_value`)과 바꾼 규칙(`note`)을 남긴다. 예: `630 kVA (AN) / 880 kVA (AF)` → 강제 공랭 증가 39.7%(온도 상승 조건은 AN 기준 100 K뿐이라 조건 차이로 남음). 지금은 시연 A의 TRF-DRY-630만 있다. 23종 전체 정규화는 A07과 함께 한다. 실제 LS 제품 성능 자료가 아니다.

## 시연 A와 입출력 예시

- 14번 2.5절 몰드 변압기 요구 **14개**(`demo_cases.json` 시연 A)의 기대 상태와 판정이 모두 같다: 근거 있음 2, 조건부 3, 사양 차이 1, 근거 부족 8. 혼동표 대각선 14/14.
  - 기획서 4.7 표는 9개 행이고, 묶인 두 행(7: BIL·알루미늄 권선·이중 온도센서·ModBus, 8: IEEE C57.12.01·소음 NEMA TR1·Listed and labeled)을 요구별로 나눴다(`table_row`로 표의 행을 남김, 2026-10-30 결정). 조항은 14번 원문 2.5.K·L·G.1·E·I·O와 2.2.A.1이다.
  - "시연 A 결과"의 확인 조건 ①~⑪은 근거 있음 두 요구(탭·절연)를 뺀 12개 요구에 하나씩 대응한다(⑨ IEEE 시험·UL 등록이 두 요구). 검사 `test_the_eleven_demo_conditions_each_remain_open`.
- 입출력 예시: [`backend/api/tft/rfp/examples/comparison-demo-a.json`](../../backend/api/tft/rfp/examples/comparison-demo-a.json)(백엔드 검사 이미지에 들어가도록 백엔드 폴더에 둔다). 입력은 요구 14개와 후보 필터, 출력은 요구마다 `comparison`(상태·사유·미확인·다음 담당·부족 원인·후보별 근거)과 `supplement`(경로). 검사 `test_the_contract_example_is_the_current_engine_output`가 이 파일이 지금 엔진 결과와 같은지 확인한다. 다시 만들 때는 `python -m tests.tft.test_rfp_comparison`.
- 요구값은 14번 원문(개발 문서)에서 옮겼고 평가 문서(10·11·12·18)는 쓰지 않았다.

## 원문·제품 근거 링크

- **요구 쪽**: 입력 요구의 `origin`(문서, 문서 판본 ID, 조항 ID, 조항 번호, 취소선을 뺀 현행 인용, 메모)을 판정 결과 `origin`으로 그대로 돌려준다. 원문에 값이 없으면 인용은 null이고 메모에 이유를 적는다(예: 1차 전압·용량은 도면 참조). API에서는 `RequirementOrigin`(`document_version_id`·`clause_id`·`quote`)으로 옮긴다(A07). A04 추출 결과의 조항·인용을 그대로 넣으면 된다.
- **제품 쪽**: 후보마다 `candidates[].evidence`(데이터시트 문서·절·적용 조건·한계)다.
- 시연 A 14개 판정 모두 14번 원문 위치를 갖고, 13개는 원문 인용이 있다(검사 `test_every_demo_judgement_links_back_to_the_rfp_clause`).

## 남은 것 (KAN-186 완료 조건)

- 자사 사양 23종 정규화(지금 `own_spec_facts.json`은 TRF-DRY-630뿐). 문서 해시 → 제품 코드 표는 제품 문서 색인 manifest에서 만든다(A07·KAN-199 처리 함수).
