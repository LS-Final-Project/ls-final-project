# 기획서에 맞춘 평가 명세

최종 C/S 기획서 3.2·4.2·5장과 RFP 기획서 3.2를 개발에서 측정하는 기준이다. 기획서는 수정하지 않는다. KAN-82의 지표 잠금, KAN-83의 데이터 감사, KAN-95의 정답 교차 검수와 KAN-103·104의 구현에서 사용한다. 이 명세는 평가기 구현이나 모델 합격을 뜻하지 않는다. 기존 eval/smoke.py는 합성 연결 점검이며 업무 품질 평가가 아니다.

## 분모와 계산법

실험 전에 데이터 manifest와 hash, split, 정답 판본, 정책·프롬프트·모델·색인 판본 및 대상 ID를 고정한다. 모델별로 유리한 사례만 골라 분모를 바꾸지 않는다. 최초 요청 수, 유효 출력 수, 보류·실패·재시도 수와 제외 사유별 건수를 함께 기록한다. gold 누락·충돌은 사전 검수로 별도 집계하며, 모델 실패를 이유로 평가 대상에서 제외하지 않는다.

단일 분류의 precision은 TP/(TP+FP), recall은 TP/(TP+FN), F1은 2TP/(2TP+FP+FN)이다. 분모가 0인 해당 클래스 점수는 0으로 두고 표본 수를 표시한다. macro-F1은 잠근 클래스 목록의 F1을 같은 가중치로 평균한다. micro accuracy는 정답과 같은 예측 수/전체 평가 대상 수이며 macro-F1과 별도로 보고한다. 보류·타임아웃·잘못된 출력은 정답으로 인정하지 않고 정답 클래스의 FN에 포함한다. 정답 주입, 미래 자료 접근이나 시험셋 재사용이 발견된 실험은 정상 품질 결과로 채택하지 않는다.

| 지표 | 대상 및 계산 | 합격 기준 |
| --- | --- | --- |
| 주 문의 유형 | category_code의 10개 고정 클래스 macro-F1. 기획서의 유형별 평균 목표를 이 지표로 구체화하고 클래스별 P/R/F1·표본 수도 보고 | ≥0.90 |
| CRITICAL 누락 | gold urgency가 SAFETY 또는 SITE_DOWN인 대상에서 예측이 두 유형 중 어느 것도 아니거나 보류·실패인 수. 두 gold 유형별 누락과 SAFETY↔SITE_DOWN 혼동을 별도 기록 | 0건 |
| 긴급도 | urgency_codebook의 6개 클래스 macro-F1. 비NONE 재현율은 gold 비NONE 중 예측도 비NONE인 수/전체 gold 비NONE 수. 정확한 긴급도 유형의 재현율은 별도 보고 | macro-F1 ≥0.70, 비NONE 이진 재현율 ≥0.85 |
| 우선순위 규칙 | 검수된 정답 분류·긴급도 등 입력으로 순수 룰을 실행하고 priority_adjusted_by_agent=N인 대상의 priority와 정확 일치. 예측 입력을 넣는 전체 파이프라인과 분리 | 1.0000 |
| 담당부서 규칙 | 검수된 입력·확정 정책으로 룰 결과를 정책상 기대값과 대조. 수동 변경·정답 충돌은 이유와 대상 ID를 잠그고 별도 보고 | 정책 재현 100% |
| 전체 담당부서 | AI 예측 입력부터 계산한 assigned_dept와 검수된 정답의 정확 일치율. LLM/룰 오류를 구분 | ≥0.93 |
| 원시 스키마 위반 | 파싱·enum·키·타입 위반 응답 수/반환된 원시 응답 수. 응답 없는 전송 실패는 별도 비율로 기록 | 0 |
| 근거 문서 Hit@5 | gold 근거가 있는 대상에서 상위 5개 중 유효한 정답 문서·판본·절이 하나 이상 있는 대상 수/고정 대상 수. 미검색·실패는 미적중 | ≥0.90 |

기획서의 규칙 적용 건 100%는 검수된 입력의 정책 재현 지표다. 우선순위 수동 조정 제외를 담당부서·분류 평가에 일괄 적용하지 않는다. 문서의 전체 120건을 split별 분모에서 고정 차감하지 않고 대상 레코드의 플래그로 제외한다. 전체 정답 priority와의 일치율은 참고 지표로 별도 보고하며 기존 96.65% 한계와 adjusted=N 합격선을 같은 분모로 해석하지 않는다.

재시도 전 원시 출력, 최종 출력과 폴백 사용을 구분한다. 최종 결과가 정상이어도 최초 스키마 위반을 지우지 않는다. 원시 응답이 0건이면 스키마 위반율은 null이며 합격으로 표시하지 않는다. 원문·정답·토큰·개인정보를 로그에 무단 저장하지 않고, 감사용 출력은 접근이 제한된 마스킹 산출물로 보관한다.

## 보조 분류와 정보 추출

다섯 출력의 의미·enum·null은 [C/S 계약](../contracts/cs/tickets.md)을 따른다. secondary_category는 주분류와 독립 채점한다. 전체 정확 일치율(null 포함), gold가 있는 복합 의도 대상의 macro-F1 및 없는 보조 의도를 생성한 건수를 각각 보고한다. null이 많은 데이터에서 전체 정확도만으로 복합 의도 성능을 주장하지 않는다. 정답은 원천 secondary_category를 검수해 사용하며 primary를 복사해 만들지 않는다.

provided_info는 항목 코드의 set precision/recall/F1과 완전 일치율을 보고한다. 원문에 정보가 있다는 사실과 그 값이 정확한지는 구분하며 이미 제공한 정보를 재요청한 건수를 별도 채점한다. return_subtype은 [반품 하위유형 정답 잠금](#cs-반품-하위유형-정답-잠금-kan-95)의 판본으로 채점한다. 이 지표는 **고객이 접수 시 밝힌 반품 사유를 원문에서 읽어 내는 품질**이고, 검사 뒤 반품 사유를 맞게 판정했는지(확정 품질)가 아니다(KAN-193). 확정값은 사람이 업무 상태에 기록하며 LLM 지표로 채점하지 않는다. 이 정답은 팀 합의로 제공 데이터의 routing_rule_id에서 변환했으며(역산 금지 원칙의 반품 하위유형 예외), LLM 출력에서 정답을 만들지 않는다. 다른 필드에는 담당부서 정답 역산 금지를 그대로 적용한다. 미확인과 비해당 null은 검수 메타데이터에서 구분한다.

보조 분류의 선택 기준은 아래 2026-10-27 사용자 결정에 따른다. provided_info·반품 하위유형의 추가 합격선은 KAN-82·95에서 잠근 뒤 본실험을 시작한다. 반품 하위유형 gold는 [정답 잠금](#cs-반품-하위유형-정답-잠금-kan-95)에서, provided_info gold와 채점 범위는 [제공 정보 정답 잠금](#cs-제공-정보-정답-잠금-kan-95)에서 잠갔다. 아직 잠그지 않은 점수를 임의로 합격 처리하지 않는다.

2026-10-27 사용자 결정: 보조 유형 후보는 주분류와 다른 명시적 처리 요청이다. 하나이면
그 후보를 선택하고, 여러 개이면 고객이 우선이라고 명시한 의도를 선택한다. 우선 근거가
없거나 동률이면 임의 선택하지 않고 검수 보류한다. 보조 의도 없음은 `null` 정답으로,
미판정은 별도 `pending` 상태로 기록한다. 보류를 null 정답이나 오답 없는 사례로 바꾸지
않으며 전체 대상·채점 가능·보류 건수를 함께 보고한다. 이 선택 기준 확정은 사례별 gold
검수나 합격선 확정을 뜻하지 않는다.

## 데이터와 안전 조건

- 학습 2,800건, 검증 603건, 시험 597건을 구분한다. 선정·튜닝은 학습·검증 자료에서 수행한다. 시험셋은 사용 이력을 먼저 확인하고 동결 구성의 최종 1회만 사용한다. 이미 노출됐거나 사용 여부가 불명확하면 독립 최종 성능으로 주장하지 않는다. 이 문서 보완에서는 시험셋을 읽거나 실행하지 않았다.
- PHONE의 상담원 표기(`Urgency: <값>`)는 입력에 포함한다. 실제 입력에도 상담원 표기가 들어오므로 제품·평가 모두 제목과 본문 전체를 넣는다(사용자 결정 2026-10-29, `cs-classification-input-v4`). 합성 데이터에서는 표기=정답이라 PHONE 긴급도는 PHONE/비PHONE으로 분리 보고하고 언어별 점수도 분리한다. 합격선 분모는 바꾸지 않는다. 실제 데이터에서는 메모 내용으로 재측정한다. 표기를 지운 입력(`cs-classification-input-v3`)은 분석용 옵션(`--strip-phone-annotation`)으로만 만들고 채택 점수에 섞지 않는다.
- 정답 필드와 현재 문의 자신의 해결 기록, 판정 당시 없던 해결·회신·수정판은 입력·검색에서 제외한다. 반복 문제 평가는 해당 시점까지 알려진 기록만 사용한다.
- FLT-042는 원천 후보·검색·프롬프트·인용·최종 응답 경계에서 차단한다. 문서/사례 ID와 근거 연결로 해당 오류의 재유입을 검증하며 문자열 하나가 없다는 검사만으로 차단 완료라고 하지 않는다.
- 시리얼 원값, RMA 번호·방문일·보증 승인값의 AI 생성 금지, SAFETY·SITE_DOWN 희소 사례 및 부서/프로젝트 접근 범위를 회귀 검증한다. 평균 점수가 높아도 안전 조건 위반을 상쇄하지 않는다.
- 출처는 [데이터 계약](data-contract.md)의 실제·합성 여부와 생성 경위를 함께 기록하며 합성 평가를 현업 성과로 표시하지 않는다.

## C/S 사용 이력과 검수 준비 (KAN-83)

2026-10-27 감사에서 [KAN-104 댓글10248](https://lsteam4.atlassian.net/browse/KAN-104?focusedCommentId=10248)과
[PR70](https://github.com/LS-Final-Project/ls-final-project/pull/70)의 **작성자 보고**를 확인했다.
작업 시작 시 규칙 재현율 확인 목적으로 전체4,000건(시험597건 포함)의 priority,
routing_rule_id, assigned_dept, SLA 정답 열을 한 번 집계했고 규칙 수정·튜닝에는 사용하지
않았다는 기록이다. 노출 사실의 보고와 튜닝 미사용 진술을 함께 보존한다. 실제 대상 파일의
hash·정확한 실행 시각·출력 열람자·다른 실행 이력은 이번에 확인하지 않았다.

이 기록 이후에는 기존 시험597건을 **미노출 독립 최종셋으로 주장하지 않는다**. 규칙·모델
튜닝에 쓰지 않았다는 진술만으로 독립성을 회복시키지 않는다. 신규 독립 최종셋 확보 여부는
미확인이다. 향후 평가 여부·용도는 별도 결정하며, 이미 노출된 자료를 다시 쓰는 경우 최종
독립 성능이 아닌 회귀 결과로 구분해야 한다. 이 문서 변경은 재실행 허가가 아니다.

10/27까지의 `tests/cs/test_rules_reproduction.py`는 `CS_TRAINING_DATA_DIR/tickets.csv`를 열고
`csv.DictReader`로 행을 만든 **뒤** train/valid 여부를 검사했다. 따라서 평가 계산에 시험 행을
사용하지 않는 것과 시험 본문·정답을 파싱하지 않는 것은 다르다. 이후 이 테스트는
`CS_DEV_MANIFEST`의 분리된 개발 파일만 받으며, 행을 읽기 전에 파일 SHA-256을 manifest와 비교하고
시험 분할이 든 manifest나 시험 행이 있는 파일을 거부한다(`tests/cs/cs_dev_data.py`). hash는 파일 무결성을 확인할 뿐 분리 승인을 증명하지 않는다. 잘못 승인된 통합 파일의
시험 행은 파싱 후 거부하므로 비접근을 보장할 수 없다. 분리 자료와 manifest의 인수는
데이터 관리자가 별도 기록으로 확인한다.
KAN-83 감사(오새결)에서는 해당 테스트와 과거 audit.py를 실행하지 않았다. 변경 전 테스트를 통합 파일로 실행한 10/27 김나영 세션의 이력은 KAN-104 댓글 10248·10261에 있다.

앞선 [10/25 감사 기록](https://lsteam4.atlassian.net/browse/KAN-83?focusedCommentId=10140)의
과거 audit.py 실행 여부 미확인과 이번10/27 노출 보고는 별도 사건이다. 전체 사용 횟수를1회로
확정하거나 이전 사건까지 미실행으로 해석하지 않는다. 노출 열의 집계는 모델 품질 평가1회와도
동일하지 않다. 원본 보존·접근 이력·분리된 개발 manifest의 인수 조건은
[데이터 계약](data-contract.md#cs-검수-입력-인수-조건-kan-8391)을 따른다.

프로젝트 2 재기획 중에는 C/S 검수 준비와 C/S 정답/분모 잠금을 먼저 수행한다. 기존 합격선과
다섯 출력 계약은 유지하며, KAN-82·95의 C/S 미정 기준을 임의로 확정하지 않는다. 검수 전
반품 하위유형·누락 정보 코드를 추측해 본실험을 시작하지 않는다.

## C/S 반품 하위유형 정답 잠금 (KAN-95)

2026-10-27에 개발 반품 298건(학습 245·검증 53)의 하위유형 정답을 잠갔다. 정답의 뜻은 "접수 시 고객이 밝힌 반품 사유 유형"이다(KAN-193). 검사 후 확정과 구분하며, 이 구분 때문에 정답 값·판본·해시는 바꾸지 않는다. 시험 분할은 쓰지 않았다.
정답 파일은 문의 정답 열에서 만든 데이터라 저장소에 넣지 않고, 아래 판본과 hash로만 기록한다.

| 항목 | 값 |
| --- | --- |
| 판본 | `routing-derived-v1-team-agreed-20261027` (manifest `return_subtype_gold.review_version`) |
| 정답 파일 SHA-256 | `4dfe4ac67708003d4ca12f4ac1976494c4126c2e3cbab2d9514768c89a1b76c1` |
| 형식 | `ticket_id,return_subtype` 298행, 빈 값 0 |
| 건수 | DEFECT 147(학습 123·검증 24), NON_DEFECT 151(학습 122·검증 29) |
| 변환 규칙 | `RT-ALL-RETURN-NON_DEFECT` → NON_DEFECT, `RT-<제품군>-RETURN` → DEFECT |
| 합의 | [KAN-95 댓글 10324 제안·10325 잠금](https://lsteam4.atlassian.net/browse/KAN-95?focusedCommentId=10325), 오새결·김종하 동의(사용자 전달) |

[데이터 계약](data-contract.md)의 역산 금지 원칙에서 반품 하위유형만 예외로 둔 판본이다. LLM 입력은
채널·언어·제목·본문뿐이라 이 정답으로 LLM의 하위유형 판정을 채점해도 순환되지 않는다.

**원문 대조**: routing_rule_id 대조 전에 고정한 AI(Claude) 원문 판정(v1)과 248건 일치했다.
불일치 50건은 모두 배송 중 파손이다. 반품 정책 판 C에 분류가 없어, 제공 데이터의 처리 관행(제품군 반품
규칙, 품질보증 창구)을 따라 DEFECT로 둔다. 원문과 정면으로 모순되는 건은 0건이다.

**검수 수준**: 제공 데이터 처리 결과 기반 + AI 두 개(Claude 1차 판정, Codex 보조 검토)의 원문 대조 +
팀 합의. 사람 298건 독립 검수는 하지 않았다. 배송 파손 중 "포장 OK" 12건의 사람 확인은 기록되지 않았다.

**사용 범위와 한계**
- 이 정답으로 낸 KAN-104 반품 경로 재현 일치는 정의상 일치이며 정책표 연결 확인이다. 독립 검증이 아니다.
- 제공 데이터의 처리 자체가 틀린 건은 걸러지지 않는다.
- 정책 판 C가 배송 파손 분류를 정하거나 현업 기준이 확인되면 판본을 새로 만든다. 이 판본을 덮어쓰지 않는다.

**KAN-95 남은 범위**: 독립 최종셋 확보 여부 결정.
주분류·보조 분류·긴급도·제공 정보 정답과 규칙 평가·LLM 분류 분모는 아래 절에서 잠갔다.

## C/S 주분류 정답 잠금 (KAN-95)

2026-10-28에 개발 문의 3,403건의 category_code 정답을 잠갔다. 시험 분할은 쓰지 않았다. 정답 파일은 저장소에 넣지 않는다.

- 판본: `provided-verified-v1-20261028`
- 정답 파일 SHA-256: `76022e37107aea5564b58a8bc48509a697175f0a4bbeafbce9fc50dba25d2c25`(`ticket_id,category_code`)
- 건수(학습+검증): TECH_SPEC 510, DEFECT 468, INSTALL 383, MANUAL 340, COMPLIANCE 298, FW_UPDATE 298, ORDER_LEAD 298, RETURN 298, QUOTE 255, WARRANTY 255
- 검증 분할: TECH_SPEC 90, DEFECT 83, INSTALL 68, MANUAL 60, COMPLIANCE·FW_UPDATE·ORDER_LEAD·RETURN 각 53, QUOTE·WARRANTY 각 45
- 출처: 제공 데이터의 category_code를 원문과 대조해 그대로 채택했다

**원문 대조**(AI(Claude), 개발 자료 원문만)
- 문의 첫 문장(인사말 제외, 제품명·숫자·날짜를 지움)이 두 번 이상 나오는 1,895건: 같은 문장은 모두 한 분류에만 쓰였다(여러 분류에 걸친 문장 0종). 영어 문장의 뜻도 분류와 맞는다
- 원문으로 확인된 제공 항목(아래 제공 정보 채점 범위 FULL 3,204건)이 제품군·분류별 필수 정보 목록 중 정답 분류 하나에만 들어맞는 건: 2,795건. 나머지 409건도 정답 분류가 들어맞는 후보에 있다. 정답 분류와 어긋나는 건은 0건이다
- 두 근거가 모두 없는 293건(채팅 172건 포함, 15개 언어)은 원문 첫 문장을 직접 읽었고 정답과 어긋나는 건은 없었다
- 사람의 건별 독립 검수는 하지 않았다

데이터가 따르는 경계 기준(원문 대조로 확인한 관행): 설치·시운전 중 생긴 고장("installing … now, problem", "startup issue")은 INSTALL,
사용 기간을 말하며 보증을 묻는 고장은 WARRANTY, 초기 불량(DOA)·오배송·취소 반품은 RETURN이다.
데이터시트·도면·펌웨어 릴리스 노트만 따로 요청하면 MANUAL이고, 펌웨어 자체·업데이트 문의는 FW_UPDATE다.

## C/S 제공 정보 정답 잠금 (KAN-95)

2026-10-28에 개발 문의 3,403건의 provided_info 정답과 채점 범위를 잠갔다. 시험 분할은 쓰지 않았다. 정답 파일은 저장소에 넣지 않는다.
정답 값은 제공 데이터 그대로이며 고치지 않았다. 원문과 충돌하는 건은 채점 범위만 줄였다.

- 판본: `provided-verified-v1-20261028`
- 정답 파일 SHA-256: `78abf59b398c3443cf623e027d33e88eed8bf9e0c75cb919f4504f07f211595d`(`ticket_id,provided_info,scoring_scope`, `;` 구분, 빈 값은 `[]` 정답 56건)
- 채점 범위: `FULL` 3,204건(학습 2,643·검증 561), `PRECISION_ONLY` 199건(학습 157·검증 42, 모두 채팅)

**원문 대조**(AI(Claude), 개발 자료 원문만)
- 전화 506건: 상담원 메모의 `Provided:` 항목과 정답이 모두 같다
- 이메일·웹·포털 2,430건: Details 항목 이름(번역 포함)을 항목 코드로 바꿔 대조했고 집합이 다른 건은 0건이다. 데이터만으로 짝이 정해지지 않은 번역 이름 59종(103건)은 뜻을 하나씩 확인했다. 값이 `-`인 항목도 제공 데이터의 처리대로 제공으로 본다
- 채팅 467건: 끝 문단의 값 목록과 정답 항목을 값 하나에 항목 하나씩 짝지었다. 268건은 정확히 짝이 맞는다. 199건은 정답 항목 726개 중 328개에 해당하는 원문 값이 없다(사진 56, 희망 납기 29, 운전 조건 27, 배선도 24, 오류 코드 20, 로그 19 등). 원문 값이 정답보다 많은 건은 0건이다
- 사람의 건별 독립 검수는 하지 않았다

**채점 방법**: 항목 단위로 합산(micro)해 precision·recall·F1을 계산하고, 문의 단위 완전 일치율을 따로 보고한다.
- `FULL` 3,204건: precision·recall·F1·완전 일치율 모두 채점한다
- `PRECISION_ONLY` 199건: precision만 채점해 별도 줄로 보고하고 FULL의 precision과 합산하지 않는다. 정답이 원문보다 항목이 많을 뿐 원문 항목은 모두 포함하므로,
  모델이 낸 항목이 정답에 있는지는 공정하게 볼 수 있다. 원문에 없는 항목을 못 찾았다고 감점하게 되는 recall·F1·완전 일치율은 채점하지 않는다
- 한계: 199건은 정답에 문제가 있는 건만 골라 뺀 것이라 무작위 제외가 아니다. 채팅 recall은 268건으로만 계산되어 실제보다 높게 나올 수 있다. 보고서에 이 점을 함께 적는다
- 199건의 recall까지 채점하려면 원문 근거가 있는 항목만 남긴 팀 보정본을 원본과 구분된 판본으로 만들고 팀 결정을 받는다
- 이미 제공한 정보를 재요청한 건수는 아래 [재요청 지표](#cs-이미-제공한-정보-재요청-지표-kan-95)에서 정했다

## C/S 이미 제공한 정보 재요청 지표 (KAN-95)

2026-10-28에 위 정보 추출 절의 "이미 제공한 정보를 재요청한 건수"의 대상과 계산을 정했다. 합격선은 KAN-82에서 기획서 목표와 연결해 정하며 여기서 정하지 않는다.

**재요청이 생기는 경로**(개발 자료 3,403건에서 확인한 제공 데이터의 규칙)
- 누락 정보는 `required_info.csv`의 제품군·주분류별 필수 항목에서 제공 항목을 뺀 것이다(missing_info 3,403건 일치)
- 추가 정보 요청은 그중 핵심 항목(`blocking_fields`)이 빠졌을 때 보내고, 요청 메시지에는 빠진 핵심 항목만 적는다(요청 754건 모두 핵심 항목 누락. 핵심 항목이 빠졌는데 요청이 없는 6건은 모두 처리 전 `NEW`)
- 따라서 시스템에서는 모델이 낸 주분류·provided_info로 프로그램이 요청 항목을 계산한다. 모델이 이미 제공된 핵심 항목을 놓치거나 주분류를 틀려 다른 필수 목록을 쓰면 재요청이 생긴다

**계산**
- 재요청 항목: 시스템이 요청한 항목 중 gold provided_info에 있는 항목
- 재요청 문의: 재요청 항목이 하나 이상인 문의
- 보고: 재요청 문의 수와 비율(재요청 문의/대상 문의), 재요청 항목 수와 비율(재요청 항목/대상 문의의 gold 제공 항목 전체 9,519개(학습 7,876·검증 1,643)). 주분류를 틀리면 gold 기준으로는 핵심이 아닌 제공 항목도 요청될 수 있어 분모를 핵심 항목으로 줄이지 않는다
- 원인을 나눠 기록한다: 예측 주분류가 gold와 같으면 정보 추출 오류, 다르면 분류 오류

**대상**: 제공 정보 채점 범위 `FULL` 3,204건(학습 2,643·검증 561)
- `PRECISION_ONLY` 199건은 뺀다. gold에 원문 근거 없는 항목이 제공으로 들어 있어, 그 항목을 정상 요청해도 재요청으로 잘못 세기 때문이다(recall 제외와 같은 이유)
- 참고: 대상 중 gold 주분류 기준 핵심 항목을 하나 이상 제공한 문의 2,874건(학습 2,364·검증 510), 그 핵심 항목 3,953개(학습 3,264·검증 689). 정보 추출 오류로 재요청이 생길 수 있는 범위다

**실패·보류**: 출력 실패·보류·스키마 위반으로 요청이 계산되지 않은 문의는 재요청 0건으로 세지 않는다.
대상에서 빼지 않고 실패 건수를 따로 적으며, 재요청 비율은 정상 출력 건수와 함께 보고한다.

**기준선 검사**: gold 주분류·provided_info를 넣으면 재요청 0건, 요청이 필요한 문의 706건(학습 584·검증 122)이 나와야 한다. 평가기 구현 시 이 값으로 먼저 확인한다.

**정하지 않은 것**: 합격선(KAN-82). 필요한 핵심 항목을 요청하지 않은 반대 방향 오류는 이 지표에 넣지 않았다.

**평가기 입력**(2026-10-28 KAN-88)
- 필수 항목표: 제공 자료 `CS_AI_Agent_Training_Data_v1/ref/required_info.csv`를 바이트 그대로 `eval/policy/cs_required_info.csv`에 복사했다. 정답이 아닌 규칙표다. 110행(11 제품군 × 10 주분류), SHA-256 `c3a026def3d3229b5c9fe68a1d332bfc4346ebcc0965dc869dd4a61b0826a339`. 원본이 CRLF라 `.gitattributes`로 줄바꿈 변환을 막는다
- 제품군: 개발 분리본(`manifest.json`의 `partitions`가 SHA·행 수를 잠근 `tickets.train.csv`·`tickets.valid.csv`)에서 `ticket_id`·`product_code`만 꺼내 정책표 `product_master.csv`로 제품군을 정한다. 정답 열은 결과에 남기지 않는다
- 분할별 고정 수(`eval/cs_gold_locks.py` `REREQUEST_LOCK`): 대상 학습 2,643·검증 561, gold 제공 항목 학습 7,876·검증 1,643, 기준선 요청 필요 학습 584·검증 122
- 저장 행은 아래 [잠금 정답 품질 저장 행](#kan-88-잠금-정답-품질-저장-행)의 재요청 항목에 있다

## C/S 보조 분류 정답 잠금 (KAN-95)

2026-10-27에 개발 문의 3,403건(학습 2,800·검증 603)의 secondary_category 정답을 잠갔다. 시험 분할은 쓰지 않았다.
정답 파일은 저장소에 넣지 않고 판본과 hash로만 기록한다.

- 판본: `provided-verified-v1-20261027`
- 정답 파일 SHA-256: `fcf4596dae592b80ca1d5f716a86e4e522f8a35a1856792b8841502dd08e0386`(`ticket_id,secondary_category`, 빈 값은 null 정답)
- 건수: 보조 분류 있음 293건(학습 242·검증 51), null 3,110건. MANUAL 101, COMPLIANCE 54, QUOTE 46, FW_UPDATE 38, ORDER_LEAD 27, WARRANTY 27
- 출처: 제공 데이터의 secondary_category를 원문과 대조해 그대로 채택했다. 주분류에서 복사하지 않았다(주분류와 같은 값 0건)

**원문 대조**(AI(Claude), 원문만 사용)
- 보조 분류가 있는 293건은 모두 본문과 Details 목록 사이에 별도 문단으로 보조 요청 문장이 있고, 뜻이 분류와 맞는다. 언어별 문장은 69종이며 한 문장이 두 분류에 쓰인 경우는 없다
- 보조 분류가 null인 3,110건에는 위 문장이 하나도 없다. 언어별 접속 표현으로 다시 찾았을 때 걸린 것은 아래 데이터시트 요청뿐이다
- 사람의 건별 독립 검수는 하지 않았다

**판정 기준 보완**(2026-10-27 사용자 결정): 사양 질문 문장 안에 붙은 데이터시트 요청("최신 데이터시트도 보내 주세요")은
TECH_SPEC 처리의 일부로 보고 보조 분류를 붙이지 않는다. 데이터시트가 TECH_SPEC 정답값의 근거 문서이기 때문이다.
매뉴얼·인증서·설명서처럼 따로 요청한 문서만 보조 분류(MANUAL·COMPLIANCE)로 본다. 개발 자료에서 이 경우는
TECH_SPEC 54건이며, 그중 53건은 null이고 1건의 MANUAL은 별도 매뉴얼 요청 문단 때문이다.

## C/S 긴급도 정답 잠금 (KAN-95)

2026-10-27에 개발 문의 3,403건의 urgency_flag 정답을 잠갔다. 시험 분할은 쓰지 않았다. 정답 파일은 저장소에 넣지 않는다.

- 판본: `provided-verified-v1-20261027`
- 정답 파일 SHA-256: `9beda3c54d31f2398a10aba293582e999c97e05734863e74231c9392f9bec22e`(`ticket_id,urgency_flag`)
- 건수: NONE 2,470, DEADLINE 380, COMMISSION 211, PROD_IMPACT 209, SITE_DOWN 107(학습 84·검증 23), SAFETY 26(학습 22·검증 4)
- 출처: 제공 데이터의 urgency_flag를 원문과 대조해 그대로 채택했다

**원문 대조**(AI(Claude), 개발 자료 원문만)
- 전화 506건: 상담원 메모의 `Urgency:` 값과 정답이 모두 같다. 이 메모는 정답을 그대로 담고 있으므로 계약대로 모델 입력에서 지운다
- 전화가 아닌 긴급도 문의 768건(DEADLINE 325, PROD_IMPACT 180, COMMISSION 176, SITE_DOWN 67, SAFETY 20): 모두 원문에 해당 신호 문장이 있다.
  데이터에서 한 긴급도에만 반복되는 문장(날짜만 지움) 100종으로 667건, 언어별 신호 표현으로 91건, 원문 직접 확인으로 10건을 확인했다
- 전화가 아닌 NONE 2,129건: 위 신호 문장이 없다. 신호 표현 재검색에서 걸린 45건은 모두 Details의 "설치/시운전 날짜" 칸이나 시운전 작업 설명이었다
- 한 문의에 두 긴급도의 신호가 함께 있는 경우는 없었다
- 사람의 건별 독립 검수는 하지 않았다

신호 문장의 예(영어): SAFETY는 발연·타는 냄새, 안전을 위한 격리, 아크 플래시, "안전 문제로 처리" 요청.
SITE_DOWN은 라인 전체 또는 공장 전체 정지. PROD_IMPACT는 생산 영향 또는 하루 여러 번 반복.
COMMISSION은 시운전·고객 인도 일정 차질. DEADLINE은 입찰·제출 기한까지의 회신 요청.

## C/S 규칙 평가 분모와 제외 사유 (KAN-95)

2026-10-27 개발 자료(학습 2,800·검증 603, `tickets.dev.csv` SHA-256 `c3fd2b60…`)에서 직접 집계해 잠근
규칙 평가(`tests/cs/test_rules_reproduction.py`)의 분모다. 시험 분할은 포함하지 않는다. 제외는 해당 지표에만
적용하고 다른 지표의 분모에서 함께 빼지 않는다.

| 지표 | 분모(학습+검증) | 학습 | 검증 | 제외와 사유 |
| --- | --- | --- | --- | --- |
| 우선순위 정확 일치 | 3,303 | 2,709 | 594 | 담당자가 우선순위를 수동 조정한 `priority_adjusted_by_agent=Y` 100건(학습 91·검증 9). 규칙이 아니라 사람 판단이 정답이라 비교하지 않는다 |
| 제품군 | 3,403 | 2,800 | 603 | 없음 |
| 담당부서·규칙 ID(반품 외) | 3,105 | 2,555 | 550 | 없음 |
| 담당부서·규칙 ID(반품) | 298 | 245 | 53 | 없음. 하위유형은 [정답 잠금](#cs-반품-하위유형-정답-잠금-kan-95) 판본을 입력으로 쓴다 |
| SLA 목표 시간 | 3,403 | 2,800 | 603 | 없음 |
| SLA 경과 시간·상태 | 3,400 | 2,798 | 602 | 고객 대기 시간이 음수인 3건(학습 CSV1-003890·CSV1-003946, 검증 CSV1-003951). 첫 응답이 기준일(2026-10-01) 뒤로 기록돼 `customer_wait_hours`가 음수다. 규칙은 이 입력을 `RuleInputError`로 거부하며, 검사는 거부되는지를 따로 확인한다 |

수동 조정 100건과 음수 대기 3건은 겹치지 않는다. 두 제외 목록은 제공 데이터의 플래그·ID로 고정하며,
결과가 나쁘다는 이유로 대상을 추가로 빼지 않는다. 우선순위 수동 조정 제외는 분류·긴급도·담당부서 평가에
적용하지 않는다. 음수 대기 3건도 분류·긴급도 평가 분모에서는 빼지 않는다.

## C/S LLM 분류 평가 분모 (KAN-95)

2026-10-28 개발 자료(학습 2,800·검증 603)에서 직접 집계해 잠근 LLM 분류 지표의 분모다. 위 정답 판본을 쓰며 시험 분할은 포함하지 않는다.
모델 실행 전에 고정하며, 보류·타임아웃·잘못된 출력도 분모에 남겨 오답으로 센다. 규칙 평가의 제외(수동 조정 100건, 음수 대기 3건)는 여기에 적용하지 않는다.

| 지표 | 분모(학습+검증) | 학습 | 검증 | 제외와 사유 |
| --- | --- | --- | --- | --- |
| 주분류 macro-F1·정확도 | 3,403 | 2,800 | 603 | 없음 |
| 긴급도 macro-F1 | 3,403 | 2,800 | 603 | 없음 |
| 긴급도 비NONE 재현율 | 933 | 754 | 179 | gold NONE 제외(지표 정의) |
| CRITICAL 누락 | 133 | 106 | 27 | gold SAFETY·SITE_DOWN만 대상(지표 정의) |
| 보조 분류 전체 정확 일치(null 포함) | 3,403 | 2,800 | 603 | 없음 |
| 보조 분류 복합 의도 macro-F1 | 293 | 242 | 51 | gold null 제외(지표 정의) |
| 없는 보조 의도 생성 수 | 3,110 | 2,558 | 552 | gold 보조 분류 있음 제외(지표 정의) |
| 제공 정보 precision(주 지표) | 3,204 | 2,643 | 561 | `PRECISION_ONLY` 199건은 아래 줄로 따로 보고. 두 줄을 합산한 값은 내지 않는다 |
| 제공 정보 precision(`PRECISION_ONLY`) | 199 | 157 | 42 | 채팅 중 정답에 원문 근거 없는 항목이 있는 건만 대상 |
| 제공 정보 recall·F1·완전 일치율 | 3,204 | 2,643 | 561 | 정답에 원문 근거 없는 항목이 있는 채팅 199건(학습 157·검증 42). [제공 정보 정답 잠금](#cs-제공-정보-정답-잠금-kan-95) |
| 이미 제공한 정보 재요청 | 3,204 | 2,643 | 561 | `PRECISION_ONLY` 199건. [재요청 지표](#cs-이미-제공한-정보-재요청-지표-kan-95) |
| 반품 하위유형 | 298 | 245 | 53 | 반품 아님 제외(지표 정의) |

정답 판본이 바뀌면 이 표도 새 판본 기준으로 다시 집계하고 이전 값을 덮어쓰지 않는다. 결과를 본 뒤 대상을 추가로 빼지 않는다.

## C/S 정답 묶음 판본 (KAN-88)

실험의 `gold_version`은 값 하나지만 위 잠금 정답은 지표마다 판본과 파일이 다르다(판본 이름이 같아도 파일과
hash는 다르다). 잠금 정답으로 품질을 저장하는 실험은 `gold_version`에 아래 묶음 ID를 쓰고, 지표별 판본과
정답 파일 SHA-256은 저장 행에 따로 남긴다. 묶음은 위 잠금 절의 값을 모은 것이며 새 정답을 만들지 않는다.

- 묶음 ID: `cs-gold-bundle-20261028-v1`
- 표의 지표 판본이 하나라도 바뀌면 새 묶음 ID를 만들고 이 묶음을 덮어쓰지 않는다.
- 학습·검증은 [LLM 분류 평가 분모](#cs-llm-분류-평가-분모-kan-95)의 지표별 대상 수다. 지표 정의에 따른
  제외(gold NONE, null 등)는 대상에서 빼지 않고 지표 계산에서만 적용한다. 시험 분할은 없다.
- 표와 `eval/cs_gold_locks.py` 상수는 테스트로 완전 일치를 확인한다. 이 묶음만으로 실제 품질이
  연결되지는 않는다. 저장 경로는 [잠금 정답 품질 저장 행](#kan-88-잠금-정답-품질-저장-행)이며 집계 API에는
  아직 연결하지 않았다(`quality_available=false` 유지).

| 지표 | 정답 판본 | 정답 파일 SHA-256 | 열 | 채점 범위 | 학습 | 검증 |
| --- | --- | --- | --- | --- | --- | --- |
| `category` | `provided-verified-v1-20261028` | `76022e37107aea5564b58a8bc48509a697175f0a4bbeafbce9fc50dba25d2c25` | `ticket_id,category_code` | `ALL` | 2,800 | 603 |
| `urgency` | `provided-verified-v1-20261027` | `9beda3c54d31f2398a10aba293582e999c97e05734863e74231c9392f9bec22e` | `ticket_id,urgency_flag` | `ALL` | 2,800 | 603 |
| `secondary` | `provided-verified-v1-20261027` | `fcf4596dae592b80ca1d5f716a86e4e522f8a35a1856792b8841502dd08e0386` | `ticket_id,secondary_category` | `ALL` | 2,800 | 603 |
| `return_subtype` | `routing-derived-v1-team-agreed-20261027` | `4dfe4ac67708003d4ca12f4ac1976494c4126c2e3cbab2d9514768c89a1b76c1` | `ticket_id,return_subtype` | `ALL` | 245 | 53 |
| `provided_info` | `provided-verified-v1-20261028` | `78abf59b398c3443cf623e027d33e88eed8bf9e0c75cb919f4504f07f211595d` | `ticket_id,provided_info,scoring_scope` | `FULL` | 2,643 | 561 |
| `provided_info` | `provided-verified-v1-20261028` | `78abf59b398c3443cf623e027d33e88eed8bf9e0c75cb919f4504f07f211595d` | `ticket_id,provided_info,scoring_scope` | `PRECISION_ONLY` | 157 | 42 |

## RFP 및 나머지 지표 잠금

RFP 기획서의 역할 precision/recall, 근거 정확도, 요청서 필수값, 가상 경력·가용성 생성, 공백 탐지 및 상태 전이를 각각 검수한다. 미기재·미확인·미충족을 구분하고 부서 검증과 지정 책임자 확정을 합치지 않는다. OCR·검색·초안, 개선요청 기준선·조건 비교·재요청·완료 후 재개, p95·부하 및 비용의 정확한 대상·계산식·합격선은 KAN-82에서 기획서 목표에 연결해 잠근다. 이 문서만으로 KAN-82 전체 완료를 선언하지 않는다.

### KAN-88 최초 주분류 집계 첫 단위

`eval.cs_metrics.summarize_category_first_attempts(gold, attempts)`는 사전 잠금 대상 ID→주분류
정답과 ID→`ClassificationAttempt`를 받는 순수 집계다. 실제 정답 검수/사용 승인은 호출자
책임이며 파일이나 DB를 읽거나 모델을 호출하지 않는다. 기존 10개 주분류 enum을 고정 평균
목록으로 사용하고 클래스별 support/TP/FP/FN/P/R/F1, 정오와 혼동행렬을 반환한다.

최초 시도(번호1)만 허용하며 재시도·추가 대상 ID·불명 정답·상태/예측 모순은 오류다.
고정 대상에 시도 기록이 없으면 NOT_STARTED, 보류/실패는 해당 상태로 유지하며 모두
무예측 열(NO_PREDICTION)과 정답 클래스 FN에 포함한다. 유효 출력만 추려 분모를 줄이지
않는다. 예측 없는 값을 NONE 긴급도나 null 정답으로 치환하지 않는다. 대상이 아예 없으면
accuracy/macro_f1은 null이며 합격 판정을 만들지 않는다. 비어 있는 클래스의 F1은 0이다.

반환 사례 기록은 ID·정답/예측 코드·상태·정오이며 원시 응답·오류 원문·토큰을 포함하지
않는다. 이 집계의 quality_gate_evaluated는 항상 false다. 이번 검증은 합성 사례의 계산
회귀이며 실제 품질 합격 근거가 아니다. 최초/최종
비교, 지연·사용량·비용·퇴행, 실제 DB/화면 연결과 잠긴 정답 인수는 남아 있다.


### KAN-88 최초 긴급도·위험 누락 집계

`eval.cs_metrics.summarize_urgency_first_attempts(gold, attempts)`는 같은 최초 시도 계약으로
긴급도 6클래스의 P/R/F1·macro-F1·정오·혼동행렬을 계산한다. 비NONE의 support는 gold가
NONE이 아닌 전체 대상이며, detected는 이 중 유효 예측도 NONE이 아닌 수다. 실패·보류·
미실행은 detected에 포함하지 않는다. support=0이면 비NONE recall은 null이다.

CRITICAL은 gold SAFETY/SITE_DOWN 전체와 유형별 support/missed를 함께 반환한다.
SAFETY↔SITE_DOWN 혼동은 일반 긴급도 오분류로 계산하지만 CRITICAL 누락은 아니며,
두 방향의 혼동 수를 별도로 기록한다. 다른 비NONE 긴급도로 예측한 경우 비NONE 탐지는
성공이어도 CRITICAL 누락이다. 사례별 critical_missed로 누락 대상을 찾을 수 있다.
CRITICAL support=0/missed=0은 위험 누락 검증 성공을 뜻하지 않는다. 합격 판정은 여전히
수행하지 않으며 실제 정답·모델·DB·화면 연결을 완료한 결과로 사용하지 않는다.


### KAN-88 최초 보조 분류 집계

`eval.cs_metrics.summarize_secondary_first_attempts(gold, attempts)`의 gold는 검수된 유형
또는 의도 없음(None)이다. 미검수·충돌 상태를 None으로 바꾸면 안 되며, 빈 문자열과
내부 행렬 표식은 정답으로 허용하지 않는다. 최초 시도 검사와 실패 분모는 기존과 같다.

전체 accuracy는 null 정답도 포함한 정확 일치율이다. 유효 응답의 null은 행렬의
NO_SECONDARY, 실패/보류/미실행은 NO_PREDICTION 열로 구분한다. 사례별 prediction은
둘 다 null일 수 있으므로 outcome과 correct를 함께 사용한다. null 정답이라도 실행 실패는
오답이다. 없는 의도 생성은 null 정답 대상 중 유효한 비null 예측 수이며 실패와 섞지 않는다.

composite는 gold가 비null인 대상 행만 분모로 사용하고 고정 10개 유형의 P/R/F1·macro-F1을
반환한다. 이 부분집합에서 null 예측·실패도 FN이다. null 정답 행의 잘못된 의도 생성은
composite FP에 포함하지 않고 absent.invented와 전체 행렬에 별도로 보존한다. 전체
per_class는 null 정답 행까지 포함하므로 composite.per_class와 분모가 다르다.
복합 의도 대상0이면 composite.macro_f1=null이며, null 포함 전체 macro-F1은 제공하지
않는다. 합격선/실제 정답 잠금 및 품질 합격 판정은 별도이며 자동 승인하지 않는다.


### KAN-88 최초 반품 하위유형 집계

`eval.cs_metrics.summarize_return_subtype_first_attempts(gold, attempts)`의 gold는 위 KAN-95
잠금 판본의 반품 대상 ID→DEFECT/NON_DEFECT다. 정답 파일의 판본·hash 인수는 호출자 책임이며
이 함수는 파일을 읽지 않는다. null·빈 값·내부 표식은 정답으로 허용하지 않고, 최초 시도 검사와
실패 분모는 주분류와 같다. 두 클래스의 P/R/F1·macro-F1·정확 일치율을 반환한다.

유효 응답의 null 예측은 행렬의 NO_SUBTYPE 열과 null_predictions, 실패·보류·미실행은
NO_PREDICTION 열로 구분하며 둘 다 정답 클래스의 FN이다. 계약대로 반품의 null을 비불량으로
간주하지 않는다. 비반품 대상의 null 정답은 잠기지 않았으므로 비반품에 하위유형을 만든 건수는
이 집계에 없다. 정답 판본의 한계(독립 검증 아님)와 합격선 미정은 그대로이며
quality_gate_evaluated는 false다.

저장 실행 경로(`eval.cs_stored_metrics`의 최초 집계·최초/최종 비교·기준선 비교, `eval.cs_strata`,
`summarize_final_attempts`)도 metric=`return_subtype`을 받는다. **부분집합 허용은 return_subtype에
한정한다.** 이 지표만 gold가 실험 고정 대상의 비어 있지 않은 부분집합(잠긴 반품 대상)이면 받고,
그 대상만 채점한다. gold에 실험 밖 ID가 있으면 거부한다. 주분류·긴급도·보조 분류는 기존처럼 gold와
실험 대상이 정확히 같아야 한다. 저장 최초 집계는 target_count(실험 대상)와
scored_target_count(채점 대상)를 함께 반환해 분모 축소를 숨기지 않는다.
`eval.cs_report.build_stored_report`는 gold에 return_subtype 키가 있을 때만 quality.return_subtype을
추가한다. 기존 세 지표 키는 필수이며 키가 없을 때의 보고서는 바뀌지 않는다. 합성 CLI는 반품
하위유형을 보고하지 않는다. 플랫폼 집계 API·화면 연결은 남아 있다.


### KAN-88 합성 품질 저장 행

`eval.cs_report.store_synthetic_return_subtype_metrics(conn, run_id, gold_csv, gold_sha256=...,
gold_version=..., scoring_version=...)`는 운영자 도구가 정답 파일 바이트를 넘겨 반품 하위유형 최초
집계를 KAN-85의 추가 전용 `evaluation.metrics`에 **실행 단위 행으로만** 저장한다. 플랫폼 서버는 정답
파일을 읽지 않고 이후 이 행만 조회한다. 새 테이블·마이그레이션은 없다.

- 한 REPEATABLE READ 트랜잭션에서 실행 행을 `FOR SHARE`로 잠그고 종료 상태를 재확인한 뒤 같은
  스냅샷의 저장 집계로 행을 넣는다. 어떤 거부·오류든 전체 rollback이며 일부 행을 남기지 않는다.
- 행(case_id null, segment `all`): `return_subtype_first_accuracy`(numerator=정답 수, denominator=채점
  대상 수), `return_subtype_first_macro_f1`, `return_subtype_first_null_predictions`, 클래스별
  `return_subtype_first_f1`(class_label DEFECT/NON_DEFECT, support, details의 tp/fp/fn/precision/recall).
  집계 행 details는 report_version·data_origin·gold_version·정답 파일 SHA-256·실행 상태·target_count·
  scored_target_count·outcomes만 담는다. 정답 파일 경로, 사례 ID, 사례별 정답·예측, 원시 출력은
  넣지 않는다. threshold·comparison·passed는 항상 null이며 합격 판정을 하지 않는다.
- 거부: 정답 파일 hash 불일치(`CS_QUALITY_GOLD_HASH_MISMATCH`), 시험 분할
  (`CS_QUALITY_FINAL_TEST_LOCKED`, 대상·정답 파싱 전), 고정 합성 프로필의 local/test fake 실행이 아님
  (`CS_QUALITY_SYNTHETIC_ONLY`), 실험 gold_version 불일치, 실행 미종료, 정답 파일 형식 오류, 정답이 실험
  대상의 비어 있지 않은 부분집합이 아님, 같은 실행·scoring_version의 기존 행(UNIQUE로 덮어쓰기 거부).
  저장 집계 검증 오류는 기존 코드 그대로 전달한다: 저장 행이 실험 고정 대상·요청 건수와 어긋나거나
  저장 출력이 계약에 맞지 않으면 `CS_STORED_METRICS_INVALID_INPUT` 또는 `CS_METRICS_INVALID_INPUT`이다.
- 현재는 합성 실행만 저장한다. 합성 정답으로 낸 값은 실제 품질이 아니다.

운영자 명령(local/test, DB는 `EVALUATION_REPORT_DATABASE_URL`):

```bash
uv run --frozen python -m eval.cs_report store-return-subtype --run-id <UUID> \
  --gold-file <정답 CSV> --gold-sha256 <hex> [--gold-version <판본>] --scoring-version <판본>
```

정답 파일은 바이트로 읽어 넘기며 경로는 출력·저장하지 않는다. 성공하면 stdout에
`{"run_id":…,"scoring_version":…,"stored_rows":5}` JSON 한 줄, exit 0이다. 실패하면 exit 1이고
stderr에 코드 한 줄만 쓴다: 위 `CS_QUALITY_*`·저장 집계 검증 코드, 파일 읽기 실패
`CS_QUALITY_GOLD_UNREADABLE`, local/test 설정 누락 `CS_REPORT_LOCAL_CONFIGURATION_REQUIRED`, 그 밖의
DB·예상 밖 오류는 원문 없이 `CS_QUALITY_STORE_FAILED`. 원문·스택은 출력하지 않는다. 필수 인자 누락·
잘못된 UUID 같은 인자 형식 오류는 argparse 기본대로 exit 2와 사용법을 출력한다. 기존 보고서
내보내기 명령(`--run-id … --output …`, 오류 exit 2)은 그대로다.

**미결(운영 쓰기 역할)**: KAN-85 권한에서는 `platform_app`도 `evaluation.metrics`에 INSERT할 수 있다.
이 행을 운영자 전용 역할만 쓰도록 분리할지는 운영 활성화 전에 정한다. 이번 변경은 권한을 바꾸지
않았다.

플랫폼 집계 API(`GET …/runs/{run_id}/summary`)는 기존 권한 검사 뒤 이 행만 읽어 선택 필드
`synthetic_quality`로 투영한다(최신 판본 하나, `quality_available=false` 유지). 계약은
[플랫폼 평가 계약](../contracts/platform/evaluations.md#kan-88-합성-품질-선택-필드)을 따른다. 화면 표시는 후속 단위다.

실행 간 비교(KAN-138)는 요약을 실행마다 조회한 뒤 소비자가 나란히 놓는다. 비교 가능 조건은
`compare_stored_runs`와 같은 7개이며, API는 원값 대신 이를 해시한 `comparison_key`(판본
`cs-comparison-key-v1`)만 낸다. 키가 다르거나 한쪽이 미종료면 비교하지 않는다. 계약은
[실행 비교 키](../contracts/platform/evaluations.md#kan-138-실행-비교-키)를 따른다.


### KAN-88 잠금 정답 품질 저장 행

`eval.cs_report.store_locked_quality_metrics(conn, run_id, metric, gold_csv, scoring_version=...)`는
종료된 비합성 실행의 지표 하나를 [정답 묶음](#cs-정답-묶음-판본-kan-88)의 잠금 정답으로 채점해
`evaluation.metrics`에 실행 단위 행으로만 저장한다. 지표는 `category`·`urgency`·`secondary`·
`return_subtype`·`provided_info`다(provided_info는 아래 별도 항목). 한 번 실행에 지표 하나, 트랜잭션 하나다. 트랜잭션·실행 행
`FOR SHARE`·종료 재확인·덮어쓰기 거부는 합성 저장과 같은 내부 함수를 쓴다. 정답 판본·hash는 인수로 받지
않고 `eval/cs_gold_locks.py`에서 읽는다.

- 채점 대상: 정답 파일(학습+검증) 중 실험 대상에 든 행. 반품 하위유형이 아닌 지표는 실험 대상 전부가
  정답에 있어야 한다. 채점 대상 수가 실험 split의 잠금 분모와 다르면 거부한다. 결과를 본 뒤 대상을 빼지 않는다.
- 행(case_id null, segment `all`, metric_key 접두 `cs_locked_<지표>_first_`): `accuracy`, 클래스별 `f1`,
  `macro_f1`(보조 분류는 대신 null 정답 제외 `composite_macro_f1`과 없는 보조 의도 생성 `invented`),
  긴급도 `non_none_recall`·`critical_missed`, 반품 하위유형 `null_predictions`. 분모가 다른 지표는
  numerator·denominator·support에 그 분모를 쓴다.
- 집계 행 details: `report_version`(`cs-locked-quality-v1`)·`bundle_id`·지표의 `gold_version`·
  `gold_sha256`·`data_origin`·`data_split`·`run_status`·`locked_denominator`·`target_count`·
  `scored_target_count`·`excluded_by_reason`(반품 하위유형은 `NOT_RETURN` 건수)·`outcomes`. 정답 파일
  경로, 사례 ID, 사례별 정답·예측, 원시 출력은 넣지 않는다. threshold·comparison·passed는 항상 null이다.
- 거부(전체 rollback): 지원하지 않는 지표(`CS_QUALITY_METRIC_UNSUPPORTED`), 정답 파일 hash 불일치
  (`CS_QUALITY_GOLD_HASH_MISMATCH`, 행 파싱 전), 시험 분할(`CS_QUALITY_FINAL_TEST_LOCKED`, 대상·정답 파싱
  전), smoke 목적·fake provider/model·고정 합성 프로필(`CS_QUALITY_LOCKED_RUN_REQUIRED`), 실험
  gold_version이 묶음 ID가 아님(`CS_QUALITY_BUNDLE_MISMATCH`), 실행 미종료(`CS_QUALITY_RUN_NOT_TERMINAL`),
  정답 형식 오류(`CS_QUALITY_GOLD_INVALID`), 분모 불일치(`CS_QUALITY_DENOMINATOR_MISMATCH`), 같은
  실행·scoring_version의 기존 행(`CS_QUALITY_ALREADY_STORED`). 저장 집계 검증 코드는 합성 저장과 같다.
- 합성 저장의 `CS_QUALITY_SYNTHETIC_ONLY`와 반대 경계라 한 실행에 두 경로의 행이 함께 남지 않는다.
  접두도 달라 합성 품질 요약(`synthetic_quality`)은 이 행을 읽지 않는다.

**provided_info**(`eval.cs_metrics.summarize_provided_info_first_attempts`): 예측은 기존 분류 출력의
`provided_info` 배열 그대로다. 항목 단위로 합산(micro)해 TP=예측∩정답, FP=예측−정답, FN=정답−예측을 센다.

- 실패·보류·스키마 위반·미실행은 빈 예측으로 본다. 정답 항목은 모두 FN이고, 정답이 `[]`여도 완전 일치가
  아니다. precision은 유효 출력의 항목만으로 계산되므로 실패가 precision을 낮추지 않는다. 이를 숨기지 않도록
  행 details에 `valid_count`와 `outcomes`를 함께 둔다.
- 분모가 0이면 값은 null이다: TP+FP=0이면 precision, 정답 항목이 0이면 recall, TP·FP·FN이 모두 0이면 F1이다.
- `scope=FULL`: `cs_locked_provided_info_first_precision`(TP/TP+FP)·`_recall`(TP/TP+FN)·`_f1`·
  `_exact_match`(완전 일치 문의/FULL 문의). `scope=PRECISION_ONLY`: `_precision` 한 행만. 두 범위를 합한
  행은 없다. support는 범위 문의 수다.
- details: 위 공통 키에 `scope`·범위의 `locked_denominator`·`scope_target_count`·`valid_count`·
  `items`(tp·fp·fn 집계 수. PRECISION_ONLY는 tp·fp)·`recall_scored`·`excluded_by_reason`(FULL 행은
  `PRECISION_ONLY` 건수)·범위의 `outcomes`. 항목 코드 목록은 넣지 않는다.
- 분모: 범위별 채점 대상 수가 실험 split의 잠금 분모(FULL 2,643/561, PRECISION_ONLY 157/42)와 모두 같아야
  하며, 실험 대상 전부가 정답에 있어야 한다.
- 정답 파일은 [제공 정보 정답 잠금](#cs-제공-정보-정답-잠금-kan-95)의 기록대로 읽는다: 헤더
  `ticket_id,provided_info,scoring_scope`, 항목은 `;` 구분, 빈 정답은 `[]`, 범위는 `FULL`·`PRECISION_ONLY`.
  빈 칸·빈 항목·앞뒤 공백·중복 항목·중복 ID는 거부한다. 항목 코드의 허용값은 코드북을 읽지 않아 검사하지
  않는다. **실제 파일의 표기가 이 기록과 다르면 추정해 읽지 않고 `CS_QUALITY_GOLD_INVALID`로 멈춘다.**
- 언어·채널별 집계는 계산 경로에 있지만 행으로 저장하지 않는다. 최종 시도 집계는 없다.

재요청 지표(`store_locked_rerequest_metrics`, 정의는 [재요청 지표](#cs-이미-제공한-정보-재요청-지표-kan-95)):
- 입력: category·provided_info 잠금 정답(각 SHA 대조), 필수 항목표 사본(SHA 대조), 개발 분리본의 제품 코드.
  거부 코드와 `cs-locked-quality-v1`, 비합성 경계·묶음·종료 검사는 위와 같다.
- 순서: 제품 입력의 분할이 실행 split과 다르면 `CS_QUALITY_GOLD_INVALID`. 대상 제품 코드 누락·미등록 제품·
  규칙표에 없는 (제품군, 주분류)도 같은 코드다. 이어서 FULL 대상 수와 gold 제공 항목 수가 잠금 수와 다르면
  `CS_QUALITY_DENOMINATOR_MISMATCH`.
- 기준선 관문: gold 주분류·provided_info를 예측으로 넣은 요청 필요 문의 수가 잠금 값과 다르면
  `CS_QUALITY_GOLD_INVALID`로 저장하지 않는다(재요청은 구성상 0).
- 행(접두 `cs_locked_rerequest_first_`, segment `all`): `rerequest_ticket_count`·`rerequest_ticket_ratio`
  (분모 FULL 대상), `rerequest_item_count`·`rerequest_item_ratio`(분모 gold 제공 항목), `cause_extraction`·
  `cause_classification`(재요청 문의 중 원인별 문의 수), `request_ticket_count`(요청이 생긴 문의 수, 분모 정상 출력).
- details: 묶음 ID, 두 정답 판본·SHA, 규칙표·제품 파일 SHA, data_origin·split·상태, 고정 분모, gold 항목 수,
  `excluded_by_reason`(`PRECISION_ONLY` 수), `valid_count`·`outcomes`, 원인별 항목 수, 기준선 요청 필요 수.
  사례 ID·항목 코드·제품 코드는 넣지 않는다. 실패·보류는 요청을 계산하지 않고 `outcomes`에만 센다.

운영자 명령(local/test, DB는 `EVALUATION_REPORT_DATABASE_URL`):

```bash
uv run --frozen python -m eval.cs_report store-locked-quality --run-id <UUID> \
  --metric <category|urgency|secondary|return_subtype|provided_info> --gold-file <정답 CSV> \
  --scoring-version <판본>
uv run --frozen python -m eval.cs_report store-locked-rerequest --run-id <UUID> \
  --category-gold <정답 CSV> --info-gold <정답 CSV> --dev-manifest <manifest.json> \
  --split <train|valid> --scoring-version <판본>
```

재요청 명령은 개발 분리본 로더(`tests/cs/cs_dev_data.py`)를 지연 import하므로 저장소 전체가 있는 운영자 환경에서만
실행한다. 분리본을 읽지 못하거나 SHA·행이 다르면 `CS_QUALITY_PRODUCT_INVALID`다.

출력·오류 규칙은 합성 저장 명령과 같다(성공 JSON 한 줄 exit 0, 오류 코드 한 줄 exit 1, 인자 형식 오류
exit 2, 경로·DSN·원문 비출력). 실제 실행이 아직 없어 이 경로는 격리 DB의 검사용 실행·잠금표로만
검증했다. 집계 API·화면 연결, 합격선, 운영 쓰기 역할은 정하지 않았다.

### KAN-88 언어·채널별 최초 집계

`eval.cs_strata.summarize_first_attempts_by_group(gold, attempts, metadata, metric=...)`는
category/urgency/secondary 중 한 지표를 기존 집계 함수로 계산한다. metadata는 고정 대상
ID별 channel/body_language만 받으며 본문·정답·임의 필드를 추가하면 오류다. 전체 지표는
고정 대상 전체에서 직접 계산하고 그룹 점수의 단순 평균으로 대체하지 않는다.

각 축(channel, body_language, phone)은 대상마다 정확히 한 그룹을 부여한다. 메타데이터
행·키 누락 또는 None은 UNKNOWN으로 남기며, 채널 미확인을 NON_PHONE에 합치지 않는다.
빈 문자열·불명 채널·언어 태그 형식 위반은 누락으로 보정하지 않고 오류다. 언어는 기존
입력 형식(en/ko/en-US 등, 최대32자)을 따르며 표기를 자동 합치지 않는다. 대상 밖
메타데이터/시도나 재시도는 그룹 필터로 숨기기 전에 거부한다.

전체/각 그룹은 미실행·실패를 포함한 기존 지표 계약을 유지한다. 각 축의 total 합은 전체
total과 같고 missing_metadata는 채널/언어 누락 건수다. 축 간 그룹은 중복되므로 서로
더하지 않는다. 나타나지 않은 그룹은 생성하지 않으며 대상0은 빈 그룹 목록과 null 점수다.
PHONE 표기는 기본 입력에 포함하며, 분석용 표기 제거 비교의 판본·처리 건수는 입력 감사 기록에서 별도로 확인한다.
이 집계만으로 누설 제거·정답 잠금·실제 품질 합격이 검증되지는 않는다.


### KAN-88 저장된 최초 시도 집계

`eval.cs_stored_metrics.summarize_stored_first_attempts(conn, run_id, gold,
gold_version=..., metric=...)`는 내부 평가 도구용 읽기 함수다. 호출자가 먼저 실행 조회
권한과 정답 인수/잠금을 확인한 DB 연결을 전달해야 한다. 공개 API 인증·평가자 권한을
대신하지 않으며 새 API나 DB 권한을 추가하지 않는다. 실제 정답 잠금 여부를 이 함수가
보증하지 않는다.

한 SELECT에서 실행·실험의 고정 대상·최초 classify 시도를 읽고, gold ID 집합과
requested_count 및 gold_version이 일치하지 않으면 거부한다. 없는 run도 오류다.
실험 대상의 attributes 중 channel/body_language만 가져오며 누락은 UNKNOWN이다.
사례나 최초 시도가 없더라도 고정 대상은 NOT_STARTED로 남긴다. 진행 중 결과는 해당
조회 시점의 스냅샷이며 run_status와 함께 반환한다. 후속 시도만 있는 비정상 기록 역시
최초 시도가 없는 상태로 남고 후속 성공으로 대체하지 않는다.

attempt=1, step=classify만 선택하며 is_final·cases.final_output을 채점에 사용하지 않는다.
저장된 parsed_output의 5필드 스키마/관계와 outcome 일관성을 재검사한다. 원시 응답,
오류 원문, 입력 본문과 사용량은 조회하지 않는다. 출력에는 기존 집계의 정답/예측 코드가
포함되므로 일반 사용자 응답으로 그대로 공개하면 안 된다. 원시 출력 잠금은 유지한다.
정답 판본 일치는 정답 내용의 교차검수 증명이 아니며 실제 품질 합격 판정은 하지 않는다.
연결의 트랜잭션과 권한 수명은 호출자가 관리한다. DB 오류를 빈 집계로 숨기지 않는다.


### KAN-88 최초/최종 정오 비교

`eval.cs_stored_metrics.compare_stored_attempts(conn, run_id, gold, gold_version=..., metric=...)`
는 최초 집계와 최종 시도 정오를 같은 SQL 스냅샷에서 비교한다. 기존 내부 조회 권한·정답
인수 책임과 원시 출력 잠금을 유지한다. `first`는 기존 최초 집계 계약 그대로이며 주 품질
지표다. `final`은 별도의 정확 일치율·상태별 건수이며 최초 macro-F1이나 실패를 대체하지
않는다. 최종 품질 지표 확대는 아래 최종 품질 절을 따른다. 재시도 실행 기능은 포함하지 않는다.

최종 선택은 classify의 `is_final=true` 행만 따른다. 표시가 없으면 NOT_FINAL이며 최신
시도나 cases.final_output으로 추정하지 않는다. 최종 정확도의 분모도 고정 대상 전체다.
미확정/실패는 정답으로 세지 않고, 보조 분류 null 정답도 유효 null 출력일 때만 맞는다.
진행 중 실행에서는 조회 시점의 잠정 비교다. 후속 처리가 끝났다는 승인 의미는 없다.

전이는 RECOVERED(최초 오답→최종 정답), REGRESSED(정답→오답), STILL_CORRECT,
STILL_INCORRECT, FINAL_PENDING(최종 표시 없음)으로 나눈다. 다섯 건수 합은 전체 대상
수와 같다. 사례별 최종 시도 번호·예측 코드·정오를 반환한다. 최초 시도가 없는데 최종만
있거나, 더 뒤의 classify 시도가 있는데 이전 행이 최종이거나, 최종 행에 종료 시각이
없으면 오류다. 최종 스키마/상태 모순도 거부한다. 번호가 가장 크다는 이유로 최종을
자동 선택하거나 실패를 지우지 않는다. 다른 처리 단계의 시도는 비교 대상이 아니다.


### KAN-88 저장 시도의 지연·토큰·비용

`eval.cs_usage.summarize_stored_usage(conn, run_id)`는 조회 권한을 확인한 내부 연결에서
분류(classify) 시도만 읽는다. 한 SQL 스냅샷의 first(번호1), retry(번호2 이상), all을
분리하며 is_final이나 성공 여부로 사용량을 버리지 않는다. 고정 대상 수와 requested_count를
대조하고 최초 행 없는 대상 수도 반환한다. 없는 실행은 오류다. 원시 출력·정답은 읽지 않는다.

stored_attempt_count는 저장 행 수이며 외부 호출 수가 아니다. 현 저장 필드만으로 호출
여부·미저장 호출을 확정할 수 없다. HELD/실패와 미종료 행도 보존하고 outcome별 건수와
unfinished_attempt_count를 제공한다. 실행 중 결과는 조회 시점의 잠정 값이다.

토큰 5종은 각각 reported_count/missing_count/known_sum/total을 반환한다. 일부가 null이면
known_sum은 확인된 부분합이고 total은 null이다. 관측값이 전혀 없거나 시도0이면 두 합계도
null이며, 보고된 0은 그대로 0이다. total_tokens를 다른 항목에서 유도하지 않으며 캐시·추론
토큰을 입력/출력에 다시 더하지 않는다. 실패 시 이미 저장된 사용량도 동일하게 합산한다.

지연은 보고된 latency_ms만 사용해 정렬 후 (n-1)q 위치에서 선형 보간한다(p50:q=.5,
p95:q=.95). 표본0은 null, 표본1은 그 값이다. 누락 수와 미종료 행 수를 함께 해석해야 한다.
저장된 시도 지연이며 실제 API 전체 응답 시간이나 성공 호출의 지연으로 주장하지 않는다.

비용도 같은 누락 규칙을 적용하고 USD Decimal을 정확한 십진 문자열로 반환한다. 저장된
비용의 pricing_versions를 함께 제공하며 서로 다른 가격표의 저장 USD 금액은 합산하되
재가격 계산은 하지 않는다. 가격표가 없거나 빈 값인 비용·비유한 비용은 오류다. null 비용을
무료/0으로 보정하거나 미보고 토큰으로 추정하지 않는다. 이 내부 집계는 청구서 검증·실제
모델 사용량 검증·운영 성능 합격·공개 API/UI 연결 완료를 의미하지 않는다.

각 묶음은 저장 시도의 `requested_models`(요청 모델 ID 정렬 목록), `call_attempts`(같은 누락 규칙의 합계), `usage_raw`(공급자 사용량 원문의 보고·누락 수, 원문 자체는 넣지 않음)도 함께 반환한다(KAN-86).

여기서 total은 선택된 **저장 시도**가 모두 보고한 경우의 합계다. 미저장 시도·미실행 대상의
비용까지 알려졌다는 뜻이 아니다. target_count/targets_without_first_attempt와 실행 상태를
함께 확인해야 하며, 일부 대상만 실행된 0 합계를 전체 실행 무료로 해석하지 않는다.


### KAN-88 기준선 대비 최초 품질 비교

`eval.cs_stored_metrics.compare_stored_runs(conn, baseline_run_id, candidate_run_id, gold,
gold_version=..., metric=...)`는 사용자가 지정한 서로 다른 종료 실행의 최초 시도만 비교한다.
두 실행을 한 SQL 스냅샷에서 읽고 동일한 현재 집계 코드로 다시 계산한다. 호출자는 두 실행의
조회 권한과 고정 정답 인수를 확인해야 한다. 이 함수는 승인·배포·합격 여부를 결정하지 않는다.

purpose/data_origin/dataset_hash/data_split/gold_version/target_set_hash/policy_version,
실제 대상 ID 집합과 대상별 channel/body_language가 일치해야 한다. 각 실행의 요청 건수와
인수 gold 대상도 기존 최초 집계에서 다시 대조한다. 불일치를 필터링하거나 교집합으로
축소하지 않는다. 진행 중/없는 실행/자기 자신과 비교는 거부한다. 종료한 실패·취소 실행은
비교할 수 있으며 미실행과 실패도 분모에 남긴다.

모델·프롬프트·입력 변환 판본·구성 hash는 비교 변수일 수 있으므로 일치를 강제하지 않고
각 실행의 settings에 기록한다. 그 밖의 비교 조건은 conditions에 남긴다. 같은 판본 표기는
정답 내용의 교차검수 증명이 아니며 실제 잠금 확인은 호출자 책임이다.

baseline/candidate는 기존 최초 지표·언어/채널 보고서다. delta는 **후보-기준**으로 계산하며
accuracy/macro-F1(보조 분류는 composite_macro_f1), 비NONE 재현율/CRITICAL 누락 또는
불필요한 보조 의도 생성 수를 비교한다. 누락/오류 건수는 양수일수록 악화이고 점수는 반대다.
어느 쪽이든 계산 불가(null)이면 차이도 null이다. 점수 평균 차이만으로 회귀가 없다고
판정하지 않는다. 사례별 IMPROVED/REGRESSED/STILL_CORRECT/STILL_INCORRECT와 긴급도
새 CRITICAL 누락·복구를 함께 반환한다. 전이 건수 합은 고정 대상 수와 같다.
이번 비교는 최초 품질에 한정하며 최종 시도 품질·사용량/지연 차이·통계적 유의성·자동
합격선·공개 API/UI는 포함하지 않는다. 정답/예측 코드가 포함된 내부 보고서를 일반 응답에
그대로 공개하면 안 된다.


### KAN-88 최종 시도 품질 지표

`compare_stored_attempts`의 v2는 기존 first와 전이/cases 필드를 유지하고 final에 주분류·긴급도
macro-F1, 혼동행렬·클래스별 P/R/F1, 비NONE 재현율·CRITICAL 누락/유형별 누락/상호 혼동,
보조 분류의 복합 의도 macro-F1·없는 의도 생성 수를 추가한다. 최초 지표는 계속 주 품질
지표이며, 최종 개선으로 최초 오류를 덮어쓰지 않는다. 최종 지표는 전체 집계이며 언어·채널별
최종 세분화와 실행 간 최종 비교는 이번 단위에 포함하지 않는다.

순수 계산 함수 `eval.cs_metrics.summarize_final_attempts(gold, attempts, metric=...)`는
호출자가 선택·검증한 최종 시도를 받는다. DB의 is_final/최신 번호/종료/출력 일관성은 기존
저장 비교 함수가 확인한다. 순수 함수 자체가 권한이나 최종 표시를 검증했다고 주장하지 않는다.
최초 함수들은 계속 번호1만 허용하며 기존 판본/응답을 유지한다. 수식은 최초·최종이 공유한다.

최종 보고서는 `cs-{category|urgency|secondary}-final-attempt-v1`, selection=is_final을 쓰고
사례별 attempt_number는 실제 번호(미확정이면 null)다. 번호를1로 바꿔 최초 보고서로 포장하지
않는다. 미확정은 NOT_FINAL·무예측으로 고정 분모와 정답 클래스 FN에 포함한다. 유효한 보조
분류 null과 실패·미확정의 null은 outcome과 정오로 구분한다. 전체 null 포함 보조 macro-F1은
제공하지 않고 기존 복합 의도 부분집합 계산을 그대로 사용한다. 대상0/비NONE0/복합대상0은
해당 점수null이며 합격 판정은 없다. 원시 출력·정답 접근 잠금과 호출자의 인수 책임은 유지한다.


### KAN-88 원시 응답·스키마·사례 폴백 감사

`eval.cs_response_audit.summarize_stored_response_audit(conn, run_id)`는 권한을 확인한 내부
연결에서 classify 시도의 **원시/구조화 출력 존재 여부만 SQL에서 계산**한다. 원시·구조화
출력 내용, 정답, 오류 원문은 DB 연결 밖으로 가져오지 않는다. 첫 시도/재시도/전체를
분리하며 final 표시나 재시도 성공으로 최초 스키마 위반을 지우지 않는다.

스키마 위반율은 저장된 SCHEMA_VIOLATION 수/저장 원시 응답 수다. 빈 문자열 응답도
저장값이 null이 아니면 응답1건이다. 분모0은 null이다. VALID/SCHEMA_VIOLATION인데
원시 증거가 없거나, 다른 상태에 원시 증거가 있거나, VALID 여부와 구조화 출력 존재가
모순되거나, 시도가 미종료이면 해당 묶음의 위반율을 null로 보류한다. 이때 위반 건수와
관측 분모는 그대로 반환하고 incomplete_evidence_attempt_count와 문제 유형별 건수를
함께 보여준다. 유형별 문제는 중복 가능하며 합이 시도 수라는 의미는 아니다.

이는 저장 기록의 존재/상태 감사이며 원문을 재파싱하는 검사가 아니다. 실제 응답이 있었어도
저장되지 않았거나 삭제된 자료는 이 집계로 복원할 수 없다. 전송 실패·타임아웃·보류는 상태별
건수로 분리하며 저장 시도 수를 실제 외부 호출 수로 간주하지 않는다. 합격 판정은 하지 않는다.

case_fallback은 고정 대상별 사례 플래그를 중복 없이 집계한다. recorded_used와
recorded_not_used는 현재 기록값이며 missing_case와 함께 target_count를 이룬다.
pending_case는 그 안의 아직 진행 중인 사례 수다. 기본false인 미실행 사례가 있다고 해서
최종 폴백 미사용이 확정된 것은 아니다. 플래그는 사례 전체의 것으로 분류 단계별 폴백
발생이나 폴백 결과의 정답 여부를 증명하지 않는다. 원시 위반과 최종 성공·폴백은 서로 다른
축으로 유지한다. 폴백 실행기·품질 결과 API/UI 연결·실제 호출 검증은 별도 작업이다.


### KAN-88 한 실행의 통합 보고서와 합성 CLI

`eval.cs_report.build_stored_report(conn, run_id, gold, gold_version=...)`는 지표별 정답 맵
(category/urgency/secondary)을 받아 최초/최종 품질 3종·사용량·응답 감사를 한 보고서로 묶는다.
새 `REPEATABLE READ READ ONLY` 트랜잭션을 직접 열므로 모든 집계가 같은 DB 스냅샷을 본다.
다른 연결이 중간에 commit해도 다음 보고서부터 반영된다. 이미 트랜잭션이 진행 중인 연결은
호출자의 commit/rollback을 대신하지 않고 거부한다. 보고서 오류는 전체 실패이며 부분 성공
보고서를 반환하지 않는다. 종료 후 연결은 계속 사용할 수 있다.

보고서 판본은 cs-run-report-v1이며 실행/실험 ID·상태·분모·판본/구성 hash를 기록한다.
generated_at은 트랜잭션 시작 시각이며 실시간 최신 상태를 보증하지 않는다. 구성 JSON 전체,
원시 출력·정답 원문은 내보내지 않지만 정답/예측 코드가 포함된 내부 보고서이므로 공개 API
응답으로 그대로 사용하면 안 된다. 조회 권한과 실제 정답 잠금 확인은 호출자 책임이다.

다음 CLI는 local/test의 기존 고정 C/S 합성 실행만 읽는다. 실행을 생성하거나 모델을 호출하지
않으며 production·실자료·시험 분할·다른 프로필은 거부한다. 운영 세션 API가 아닌 DB 접근권을
가진 내부 검사 도구이며 새 사용자 권한을 부여하지 않는다.

```sh
# 전용 로컬 검사 DB의 연결 문자열을 환경에 설정한다(공용 DB 사용 안 함).
export ENVIRONMENT=test
export EVALUATION_REPORT_DATABASE_URL='<전용 검사 DB 연결 문자열>'
mkdir -p artifacts
python -m eval.cs_report --run-id '<기존 합성 실행 UUID>' --output artifacts/cs-run-report.json
```

CLI 기대 정답은 모든 합성 대상의 category=RETURN, urgency=NONE, secondary=null로 지정한
연결 검사용 값이다. 실제 라벨/품질 결과가 아니며 synthetic_gold_fixture=cs-report-demo-gold-v1로
표시한다. 실험 gold_version을 임의로 잠그거나 실제 미검수 값을 대체하지 않는다. 결과는
소유자 전용(0600) 새 JSON 파일에만 저장하고 기존 파일/심볼릭 링크를 덮어쓰지 않는다.
오류는 고정 코드로만 출력하며 DB 접속 정보나 오류 원문을 노출하지 않는다. 이 기능은
보고서를 내부에서 확인하는 경로이며 공개 웹 결과 화면·실제 평가 실행·품질 합격은 별도다.
