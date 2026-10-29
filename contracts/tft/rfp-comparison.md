# RFP 자사 비교·네 가지 상태·보완 경로 (KAN-186 A05, 중간)

원본: `backend/api/tft/rfp/comparison.py`(비교·상태·혼동표), `backend/api/tft/rfp/supplement.py`(보완 경로), 자사 사양 `backend/api/tft/rfp/own_spec_facts.json`. 기획서(RFP 최종본) 4.2~4.4를 따른다. **상태: 순수 함수와 시연 A 검사.** API 응답(`Comparison`·`SupplementOptions`)과 저장(`product_comparisons` 등)에 붙이는 일은 A07(KAN-188)이다.

## 후보 찾기와 비교 판정을 나눈다

- **후보 찾기**: 제품 마스터 계열 필터(`master_filter`)로 자사 후보를 넓게 찾는다. 문서 검색(R10 하이브리드, #184)은 같은 자리에 붙는다(A07). 찾은 범위는 `search_trace`(필터·문서 수·실패 여부)로 같이 돌려준다.
- **비교 판정**: 같은 속성·같은 단위·같은 조건끼리만 규칙으로 비교한다. 값 비교 함수는 가상 협력사 등급(KAN-187 `partners/grading.py`)의 `_value_verdict`·`_condition_gaps`를 그대로 써서 자사와 협력사가 같은 규칙을 쓴다(decisions.md 가상 협력사 결정).
- **나눈 이유**(Jira 주의 사항): 검색은 비슷한 것을 찾는 일이라 15 kV 요구에 자사 12 kV 개폐장치도 후보로 나온다. 그 후보를 충족으로 볼지는 유사도가 아니라 정격전압 15 kV 이상인지로 정해야 한다. 그래서 비교는 숫자·단위·조건 규칙으로만 하고 점수를 만들지 않는다(검사 `test_search_finds_similar_products_but_rules_compare_only_same_unit`).

## 네 가지 상태

| 상태 | 언제 | 결과(`result`) | 다음 담당 |
| --- | --- | --- | --- |
| `EVIDENCE_FOUND` | 같은 속성 근거가 있고 값·조건이 맞음, 또는 요구 인증을 그대로 보유 | `MEETS` | 없음 |
| `CONDITIONAL` | 값은 맞지만 요구 조건에서의 값이라는 근거가 없음(조건 차이), 단위가 달라 비교 못 함(`NOT_COMPARABLE`), 타사 지정 모델(조달·대체 경로가 있고 발주처 승인이 남음) | `MEETS`·`NOT_COMPARABLE` | `TECH`(지정 모델은 `CUSTOMER`) |
| `SPEC_GAP` | 같은 속성·단위로 비교해 미달 근거가 있음. 부족 원인(`RATING`·`CERTIFICATION`·`FORM`·`TASK`)을 함께 냄 | `DIFFERS` | `TECH` |
| `INSUFFICIENT_EVIDENCE` | 요구값을 모름(도면 참조 등, `CUSTOMER`), 같은 속성 근거가 없음, 요구 인증 보유 자료가 없음, 검색 범위에 후보 없음, 검색 실패 | `NO_DATA` | `CUSTOMER`·`TECH` |

- **미기재 ≠ 미충족**: 자료에 없는 값은 `SPEC_GAP`이 아니라 `INSUFFICIENT_EVIDENCE`다.
- **"제품 없음"은 검색 범위의 결과**: 사유에 필터와 문서 수를 적고 "회사 전체 미보유나 참여 불가를 뜻하지 않음"을 붙인다.
- **인증**: IEC 시험 성적을 UL·IEEE 요구의 충족으로 넓히지 않는다(기획서 4.2).
  - 같은 인증의 보유 자료가 없으면 근거 부족이고, 사유에 자사가 가진 규격 체계를 적는다.
  - 등록(Listed)을 요구하는데(`certification_listed=true`) 자사는 시험 성적만 있으면 미충족 근거가 있는 **사양 차이**(부족 원인 `CERTIFICATION`)다. 보완 경로는 인증 보유 변형 → 인증 보유 협력사 조달 → 대체 사양 순이다.
  - 인증이 변형 모델(예: `-U`)에만 있으면 기본 모델로 넓히지 않고 **조건부**(변형 모델 선택 확인)다.
- **판매 상태**: 근거가 맞아도 판매 중(`ACTIVE`)이 아닌 제품(`MATURE` 등)은 조건부로 내리고 "공급 지속 여부 확인"을 남긴다. 상태가 같으면 판매 중 후보를 먼저 본다.
- **비교하지 못한 값**: 단위가 다르면 "단위 확인", 숫자로 읽지 못했거나 문자열 값이 다르면 "값 확인"을 미확인 항목에 남긴다(`NOT_COMPARABLE`이면서 미확인 항목이 비지 않게).
- **후보가 여럿이면** 가장 좋은 후보의 상태가 요구의 상태이고, 모든 후보의 비교를 `candidates`에 남긴다.
- **혼동표**: `confusion((기대, 판정) 목록)`이 4×4 표를 만든다(행 정답, 열 판정). 평가(KAN-191)가 이것으로 상태별 결과를 공개한다.

## 보완 경로 (기획서 4.4)

`options_for(비교 결과, 고객 허용, designated)`는 `SPEC_GAP`과 타사 지정 모델에만 경로를 낸다. 근거 있음·조건부·근거 부족은 경로가 아니라 다음 행동(조건 확인·자료 요청)이다.

| 부족 원인 | 경로(순서) |
| --- | --- |
| 정격 `RATING` | 다른 자사 제품 → 외부 제품 조달 |
| 인증 `CERTIFICATION` | 다른 자사 제품(인증 보유 변형) → 외부 제품 조달(인증 보유 협력사) → 대체 사양 |
| 형식 `FORM` | 외부 제품 조달 → 대체 사양 |
| 과업 `TASK` | 과업 위탁(계약 범위 확인 조건) |
| 타사 지정 모델 | 지정품 조달 → 대체 제안(발주처 승인 조건) |

- 외부 경로(조달·위탁·공동 제안)는 RFP가 금지하면(`FORBIDDEN`) `allowed=false`·`blocked_reason`으로 남겨 추천하지 않는다. 허용 여부가 적혀 있지 않으면(`UNSTATED`) "발주처 확인" 확인 조건을 붙인다. 대체 사양은 늘 발주처 승인 조건이 붙는다.
- 고를 수 있는 경로가 하나도 없으면 `NONE`(보류 의견과 해결할 조건)을 덧붙인다.
- 상업 조건 차이(보증 기간 등)와 제공 자료에 핵심 기기가 없는 경우(기획서 표 마지막 두 줄)는 아직 판정하지 않는다.

## 자사 사양 (`own_spec_facts.json`)

제공 데이터 `ref/product_spec_facts.csv`·`product_master.csv`의 문장을 협력사 프로필과 같은 열(속성·값·단위·조건·근거 ID·절)로 옮긴 파생값(`derivation_kind=DERIVED`)이다. 항목마다 원래 문장(`source_value`)과 바꾼 규칙(`note`)을 남긴다. 예: `630 kVA (AN) / 880 kVA (AF)` → 강제 공랭 증가 39.7%(온도 상승 조건은 AN 기준 100 K뿐이라 조건 차이로 남음). 지금은 시연 A의 TRF-DRY-630만 있다. 23종 전체 정규화는 A07과 함께 한다. 실제 LS 제품 성능 자료가 아니다.

## 시연 A와 입출력 예시

- 14번 2.5절 몰드 변압기 요구 9개 행(`demo_cases.json` 시연 A 표)의 기대 상태와 판정이 모두 같다: 근거 있음 2, 조건부 3, 사양 차이 1, 근거 부족 3. 혼동표 대각선 9/9.
- 입출력 예시: [`backend/api/tft/rfp/examples/comparison-demo-a.json`](../../backend/api/tft/rfp/examples/comparison-demo-a.json)(백엔드 검사 이미지에 들어가도록 백엔드 폴더에 둔다). 입력은 요구 9개와 후보 필터, 출력은 요구마다 `comparison`(상태·사유·미확인·다음 담당·부족 원인·후보별 근거)과 `supplement`(경로). 검사 `test_the_contract_example_is_the_current_engine_output`가 이 파일이 지금 엔진 결과와 같은지 확인한다. 다시 만들 때는 `python -m tests.tft.test_rfp_comparison`.
- 요구값은 14번 원문(개발 문서)에서 옮겼고 평가 문서(10·11·12·18)는 쓰지 않았다.

## 남은 것 (KAN-186 완료 조건)

- 원문 근거 링크: 요구 쪽은 지금 조항 번호만 있다. 문서 판본·조항 ID 연결은 A04 저장 결과를 받는 A07에서 붙인다.
- 문서 검색(R10)으로 후보를 찾는 연결과 자사 사양 23종 정규화.
- 상업 조건 차이·핵심 기기 부재의 판정.
