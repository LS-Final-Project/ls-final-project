# RFP 자사 대응 요구 추출 (KAN-185, A04)

원본: [RFP API 명세 v1.3](https://docs.google.com/document/d/1lh6blP9IqACKvr3nyPp7NuU07jOmliIvIH_h87HDu9g) §4.3(extract 단계)·§4.4(요구 응답), 기획서 최종본 3.2(평가 기준)·4.2·5.2. 입력은 [조항 트리](rfp-clauses.md)이고 저장 구조는 [RFP 데이터 계약](rfp-data.md)의 `requirements`·`requirement_versions`·`requirement_conditions`다.

| 원본 | 파일 |
| --- | --- |
| 입력 구성·지시문·출력 검사·빠진 문장 보완 | `backend/engine/rfp/requirements.py` |
| 채점(재현율·정밀도, 건수 병기) | `backend/engine/rfp/scoring.py` |
| 로컬 평가 실행 | `scripts/rfp_requirements_eval.py` |
| 검사(가짜 모델) | `tests/tft/test_rfp_requirements.py` |

엔진은 순수 함수이고 모델 호출은 주입한 함수(`provider`)가 한다. 실제 연결은 `generate(client, settings, prompt, response_schema=ResponseSchema)`다. DB 저장과 처리 작업(rfp.process의 extract 단계) 연결은 A07(KAN-188)이다.

## 흐름

1. **입력** `build_inputs(tree, ["2.4", "2.5"])`: 절마다 하위 조항의 **현행 텍스트**와 상위 조항 문맥(`path`)을 모은다. 취소선으로 지운 값은 보내지 않는다. 반환 타입은 평탄한 `list[ExtractionInput]`이며 요청 절 순서와 절 안 원문 조항 순서를 유지한다. 길이에 관계없이 요청 절마다 입력 하나를 반환한다. 나눔과 부분 결과 합산은 `extract_section`이 맡는다. 모든 부분은 원래 `section_no`·`section_title`·`clause_no`·`path`를 유지한다.
2. **모델**: Gemini 구조화 출력으로 요구를 낸다. 요구 하나는 장비 하나의 속성 하나 또는 과업 하나다.
   - 출력 항목: `clause_no`, 근거 `quote`, `kind`, `equipment`, `task`, `attribute`, `value`, `unit`, `operator`
   - 값에 붙은 `conditions`(2차 전압별, 115℃ 상승 등), `standard_system`, `necessity`, `designated_model`, `designated_manufacturer`
   - 연산자 기준(v6): 최소 요구는 `GTE`(문구가 '이상'이거나, 내전류·차단 용량·연속 전류처럼 더 높은 정격이 요구를 만족하는 양), 최대 제한은 `LTE`, 고정된 지정값(계통 전압, 고정값으로 적힌 절연 수준·공칭 정격)은 `EQ`, 범위는 `RANGE`다.
   - 조건 이름(v6): `conditions.name`은 어휘(`vocabulary.ConditionName`) 안의 값 또는 `OTHER`다. 자사·협력사 사양 근거의 조건 이름도 이 어휘 안에 있어야 한다(검사가 확인). `OTHER`면 `value`에 원문 문구를 그대로 둔다. 응답 스키마는 enum 하나다.
   - v3 의미 칸(같은 호출 안, 추가 호출 없음). `value`·`unit`·`conditions`·`quote`는 원문 그대로다.
     - `attribute_key`: 같은 물리량인 내부 사양 항목 하나 또는 `OTHER`
     - `equipment_family`: 제품 계열 하나 또는 `OTHER`. 과업·문서 요구는 null
     - `tolerance`: 원문 허용 오차 표기 그대로 또는 null. 범위 계산은 코드가 한다
     - `certification_listed`: 원문이 Listed·labeled 등록을 요구할 때만 true
     - `assembly_clause`(v4): 여러 장비를 한 조립품·단일 공급 책임으로 납품하라는 요구일 때만 true, 장비가 나열되기만 하면 false. 통합 책임 판별(`rfp/integration.py`)의 입력이다. 처리 흐름 연결은 후속이다
     - `approval_procedure_missing`(v5): 대체품·동등품·목록 밖 제조사를 승인 조건부로 허용하는데 받은 본문 안에 승인 절차·제출 자료가 없을 때만 true. 대체 불허이거나 절차가 적혀 있으면 false. 고객 질문 판별의 입력이다(영어 정규식 대신 의미 판단). 한계: 긴 절은 부분별로 추출되므로 절차가 다른 부분에 있으면 true가 될 수 있다. 오탐은 질문이 하나 더 생기는 안전한 방향이다
     - `basis_of_design`(v7): 특정 제품을 대체 불가 지정이 아니라 설계 기준으로 들고 동등 이상을 허용할 때만 true다. 이때 제품명은 `value`(연산자 `TEXT`)에 두고 `designated_model`·`designated_manufacturer`는 null이다. 함께 보내면 `validate_output`이 지정 칸을 비운다.
     - `canonical_value`(v7): 문장으로 적힌 값의 비교용 표기 또는 null. `value`·`quote`는 원문 그대로다. 탭은 `±NxS%` 또는 `+Nx-MxS%`(곱하기는 영문 소문자 x), 보호 기능은 L·S·I·G 순서의 문자 집합이다(예 `LSI`). 표기는 `vocabulary.CANONICAL_FORMS` 한 곳에 있다. 그 속성의 표기가 아니면 `validate_output`이 지우고 `needs_review`로 둔다. 형식만 검사하고 의미 대조는 하지 않는다. 판정 사용은 후속 PR이다.
     - 어휘·계열은 `backend/engine/rfp/vocabulary.py` 단일 원본이다. 사양 사실 형식(`partners.schemas.Attribute`)도 같은 목록이고, 자사 사양 파일은 그 부분집합이다(검사가 확인)
     - 지시문은 목록과 '모르면 OTHER, 비슷한 말로 추측하지 않음'을 싣는다. 예시는 14번 문구를 쓰지 않는다
   - 명령문("Provide…", "Comply with…")도 요구로 읽는다.
   - `<Insert value>` 같은 빈 값은 값 그대로 남겨 고객 질문으로 보낸다.
   - 가격·표준 납기는 뽑지 않는다.
3. **검사** `validate_output`: 근거가 원문에 없으면 버리고 이유를 남긴다(`rejected`).
   - `UNKNOWN_CLAUSE`: 없는 조항
   - `QUOTE_NOT_IN_CLAUSE`: 인용이 그 조항 현행 텍스트에 없음(지운 값을 쓰면 여기서 걸린다)
   - `VALUE_NOT_IN_QUOTE`: 수치 값이 인용에 없음
   - 문장형 요구(`operator=TEXT`)의 값이 인용에 없으면 모델이 바꿔 쓴 말이므로 **값만 비우고 요구는 남긴다**(`value_dropped`).
   - 의미 칸 검사(v3). 요구를 버리지 않고 `needs_review`로 표시한다. `needs_review`는 코드만 정하며 응답 스키마에 없다.
     - 목록 밖 `attribute_key`·`equipment_family`·조건 이름은 엄격 검사 전에 `OTHER`로 바꾸고 검토로 둔다. 응답 전체를 스키마 위반으로 버리지 않는다
     - `tolerance`가 인용 안에 그대로 없으면 지우고 검토로 둔다
     - 값이 숫자인데 인용에 허용 오차 표기(±, +/-, +0/-5, tolerance, plus or minus)가 있고 `tolerance`가 비었으면 검토로 둔다
   - 지정 모델·제조사는 그 조항 현행 텍스트에 토큰으로 있을 때만 남긴다(앞뒤가 ASCII 영숫자면 다른 낱말이다). 모델명은 영숫자 2자 이상이고 숫자만이면 안 된다. 없으면 **그 칸만 비우고 요구는 남긴다**(`designated_dropped`). 모델명에서 제조사를 짐작한 값(SEL-751A → Schweitzer), 취소선으로 지운 옛 모델, 원문에 없는 모델이 여기서 걸린다. 모델이 비면 제조사도 비운다.
4. **빠진 문장 보완** `coverage`: 조항을 문장으로 나눠, 요구 문장마다 채택한 요구의 인용이 겹치는지 본다. `:`로 끝나는 머리글은 요구 문장에서 뺀다. 빠진 문장이 있으면 **그 문장만** 모아 한 번 더 부른다. 보완 결과도 원 조항 기준으로 같은 검사를 받는다. 그 밖의 자동 재시도는 없다.

모델에 주는 스키마(`ResponseSchema`)는 엄격 모델(`ExtractionOutput`)과 필드가 같다(코드만 정하는 `needs_review` 제외). 다른 점은 `extra=forbid`가 없다는 것이다. Gemini가 `additionalProperties`를 받지 않아서(400 INVALID_ARGUMENT) 이렇게 둔다. 받은 응답은 엄격 모델로 다시 검사한다.

## 지정품 (명세 v1.3 §5.1·§11, 판본 `rfp-requirements-v2`)

`designated_item(requirement)`이 `{manufacturer, model, clause_no}`를 돌려준다. `designated_model`이 null이면 null이다(API `designated_item`도 null).

- `manufacturer`는 원문에 제조사 이름이 적혀 있을 때만 채운다. 14번 2.6.E.5 `SEL-751A`, 2.7.B `PowerLogic ION 9000`은 조항에 제조사 이름이 없어 null이다.
- 응답의 `clause_id`는 저장(A07 KAN-188)이 `clause_no`로 `tft.clauses`에서 찾아 `requirement_versions.designated_clause_id`에 넣는다. `procurement`는 저장 기본값 `SPECIFIED_PROCUREMENT`이고 추출은 정하지 않는다.
- 허용 제조사 목록(2.1 MANUFACTURERS)은 지정품이 아니다.
- v2는 지시문과 출력 칸이 바뀌었다. 아래 측정치는 v1 결과다.
- 14번 실측(v2, gemini-3.6-flash, 1회, 2026-10-30): 2.6 지정품 1건 `SEL-751A`(2.6.E.5), 2.7 지정품 1건 `PowerLogic ION 9000`(2.7.B), 둘 다 제조사 null, 원문 대조로 비운 칸 0건. 2.6 채택 74·버림 16(모두 `VALUE_NOT_IN_QUOTE`), 요구 문장 포괄 65/69. 2.7 채택 39·버림 0, 포괄 35/36. 2.6·2.7은 정답을 만들지 않아 재현율·정밀도는 없다.

## 긴 절 나눠 부르기 (`split_input`)

`build_inputs`는 절 전체 길이와 관계없이 절마다 입력 하나를 반환한다. `extract_section`이 `split_input`으로 나눠 부르고 요구와 결과를 합산한다. `split_input`은 본문(표 행 포함)이 `PART_CHARS`(4000자)를 넘으면 첫 단계 하위 조항 묶음 단위로 순서대로 나눈다. 절 전체가 목표 길이 이하면 입력을 그대로 반환한다. 첫 단계 묶음이 목표 길이(`limit`, 기본 4000자)를 넘으면 그 묶음만 개별 하위 조항 경계로 같은 목표 길이에 맞춰 재묶는다. 개별 조항은 자르지 않으므로 목표 길이를 넘는 조항은 단독 부분이 된다. 조항 순서·번호·문맥을 보존하며 중복 호출하지 않는다. `split_input`은 `build_inputs`의 개별 조항 길이·부분 수 사전 검사를 거친 입력을 전제한다.

- 기존 절 전체 12000자 제한은 **개별 조항** 제한으로 이동한다. 개별 조항 본문(표 행 포함)이 12000자를 초과하면 문장·표를 강제로 자르지 않고 `RFP_SECTION_TOO_LONG`으로 호출 전에 거부한다.
- 절별 재분할 결과는 `MAX_SECTION_PARTS`(12부분)까지 허용하며 초과하면 `RFP_SECTION_TOO_MANY_PARTS`로 호출 전에 거부한다. `build_inputs`에서 `split_input(item)`의 실제 추출 부분 수를 검사한다. 최초 호출 최대 12회 + 부분별 빠진 문장 보완 최대 12회 = 절별 논리 호출 최대 24회다. 공급자 전송 재시도는 이 수와 별개로 공급자 정책을 따른다.

- `extract_section`은 원래 절의 모든 부분이 `VALID`일 때만 `VALID`다. 실패한 부분이 있으면 그 결과 코드이고, 성공한 부분의 요구는 남기며 `parts_failed`로 드러난다. 문장 포괄 분모는 실패한 부분까지 포함한 원래 절 전체 요구 문장 수다. 부분별 요구·실패·포괄 집계도 `extract_section`이 담당한다.
- 입력 생성의 `RFP_SECTION_TOO_LONG`·`RFP_SECTION_TOO_MANY_PARTS`는 `ValueError`다. worker는 해당 절을 건너뛰고 `needs_review`로 남기는 흐름을 유지한다.
- v1 측정 절(2.4 2510자, 2.5 3972자)은 상한 아래라 나뉘지 않는다. 2.6의 기존 실측은 2부분, 실패 0이며 목표 길이 초과 묶음 재분할 변경 후 실측은 미실행이다.

부분 응답이 출력 상한에 닿으면(`MAX_TOKENS`) 잘린 응답은 버리고 그 부분을 조항 경계로 둘로 나눠 한 번만 다시 부른다. 조항이 하나면 나눌 수 없어 실패로 드러난다. 반쪽이 또 잘려도 더 나누지 않는다. 재시도는 절 호출 상한 `MAX_SECTION_CALLS`(24) 안에서만 하고, 남은 부분마다 2회와 반쪽의 4회를 남길 수 없으면 하지 않는다. 그래서 부분이 많은 절의 앞쪽 부분은 재시도되지 않을 수 있다. 잘린 호출의 사용량은 합계에 든다.

## 채점

기획서 3.2대로 평가할 절을 미리 고정하고(14번은 2.4 개폐장치·2.5 몰드 변압기), 그 안의 요구를 사람이 전수 표시한다. 정답에는 원문 조각이 들어가므로 **저장소 밖**에 둔다.

- 짝짓기(같은 조항 안)
  - 값이 있는 정답: 값과 단위가 같거나 한쪽이 다른 쪽을 품는다(`kA`와 `kA ASYM RMS`는 같은 단위).
  - 값이 없는 정답: 남은 예측 중 낱말이 가장 많이 겹치는 것과 짝짓는다(정답 낱말의 1/3 이상).
- 엄격: 정답 하나에 예측 하나.
- 관대: 한 예측이 여러 정답을 덮는 것을 인정한다(모델이 목록형 특징을 한 요구로 묶은 경우). 둘 다 보고한다.
- 비율과 함께 건수(예: 65/68)를 적는다.

## 14번 측정 (2026-10-29, `gemini-3.6-flash`, 평가 절 2.4·2.5)

정답은 두 판본이다(저장소 밖 `rfp_eval/`, 판정 기록 포함, 사람 검수 전 초안).
- **v1(68건)**: 모델 실행 전에 원문만 보고 표시했다.
- **v2(72건)**: run1 예측을 본 뒤, 원문상 별개 요구인데 v1이 한 건으로 묶은 4건을 더했다(2.4.B 2건, 2.4.C.2.b, 2.4.C.3.b). v2는 예측을 본 뒤 고쳤으므로 유리하게 기울 수 있다.

| 실행 | 변경 | v1 재현율 | v1 정밀도 | v2 재현율 | v2 정밀도 | 버림 |
| --- | --- | --- | --- | --- | --- | --- |
| run1 | 첫 구현 | 55/68 | 55/65 | — | — | 6 |
| run1 재채점 | 채점기 단위 비교 버그 수정(모델 재호출 없음) | 60/68 | 60/65 | 64/72 | 64/65 | 6 |
| run2 | 문장형 값 비우기 | 55/68 | 55/59 | 56/72 | 56/59 | 1 |
| **run3** | **+ 빠진 문장 보완 호출** | **65/68** | **65/75** | **68/72** | **68/75** | 0 |
| **run4** | 같은 조건 반복 | **65/68** | **65/69** | **66/72** | **66/69** | 0 |
| **run5** | 같은 조건 반복 | **64/68** | **64/74** | **68/72** | **68/74** | 2 |

- **재현율**: 보완 호출을 넣은 뒤 3회 모두 94~96%(64~65/68)로 목표 90%를 넘었다.
- **정밀도**: 86~94%로 흔들린다. 남는 예측은 지어낸 것이 아니라, 모두 인용 검사를 통과한 **같은 조항의 더 잘게 나눈 요구**다(예: 2.4.C.2.c를 "교체 가능"과 "점검 가능"으로 나눔). 정답 단위를 어떻게 정하는지가 정밀도를 좌우한다.
- **요구 문장 포괄**(보완 전 → 후)
  - 2.4절: 30/31 → 31/31
  - 2.5절: 24~28/36 → 35/36. 나머지 1개는 요구가 아닌 설명 문장이다.
- **취소선**: 3회 모두 2.5.C.1은 현행 **33%**에 조건 4개를 붙여 냈다. 지운 값 "50"이 요구로 나온 경우는 **0건**이다.
- **빈 값**: 2.4.E.1.c `<Insert value>`는 3회 모두 값 그대로 남았다(고객 질문 대상).
- **사용량**(실행 1회, 두 절 + 보완 호출): 입력 약 5.9천, 출력 약 9.2천, 생각 1.1만~1.5만 토큰.

**한계**
- 문서 1건, 평가 절 2개, 실행 3회다.
- 정답은 검토자 검수 전 초안이다.
- 평가 문서(10·11·12·18번)와 PDF는 아직 재지 않았다.
