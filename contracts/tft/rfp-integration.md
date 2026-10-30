# RFP 조립품 통합 책임 (KAN-186 A05)

원본: `backend/api/tft/rfp/integration.py`. RFP API 명세 v1.3 §7과 기획서 최종본 V_1 4.8(시연 B 결과)을 따른다. **상태: 순수 함수, 측정 도구, 읽기 연결(KAN-199).** 책임자 PATCH(경로 4)·검토본 저장 조건(경로 27)·초안 문장 연결은 A08(KAN-189)이다.

## 규칙

- **필요 여부 `required`(서버 계산)**: 요구 과업이 `ASSEMBLY`·`FACTORY_TEST`·`INTERCONNECTION`·`WARRANTY`이거나, 사람이 조립품 공급 조항으로 표시한 요구(`assembly_clause`, 예: 14번 2.3 A 하나의 조립품, 2.5 F 신호선 사전 결선, 2.9 B 유닛 공장 시험)가 하나라도 있으면 참이다. 근거 조항(`basis_clause_nos`)을 함께 낸다.
- **저장과 계산(KAN-199)**: 추출 `assembly_clause`를 `tft.requirement_versions.assembly_clause`(T12)에 저장한다. `required`·근거 조항은 `tft.projects`에 저장하지 않고, 프로젝트를 읽을 때 최신 요구 판본의 `task`·`assembly_clause`로 계산한다(`project_integration`). 사람이 요구 판본을 고치면 다음 읽기에 바로 반영된다. `tft_get_project` 응답 `integration_responsibility`가 `required`·`basis_clause_ids`·`basis_clause_nos`·`fallback_paths`·`unresolved`와 사람 입력 칸(`holder` 등)을 함께 낸다. 조항에 묶이지 않은 판본은 근거가 되지 않는다. T12 전에 저장한 판본은 재처리 전까지 `false`다.
- **필수 확인 항목**: 필요하면 "통합 책임자 확인(조립·공장 시험·연동·보증)"을 항상 올린다(`check_item_listed`).
- **책임자 `holder`(사람이 정함)**: `OWN`·`SINGLE_PARTNER`·`JOINT`·`UNRESOLVED`(기본). 시스템의 조립·시험·보증은 제안 주체(제조사)의 책임이다.
- **미확보 시 처리 경로**(`fallback_paths`, 확인 순서): ① 자사 통합(`OWN`) ② 유닛 전체를 공급·통합할 단일 협력사(`SINGLE_PARTNER`, 자사는 기기 납품 검토) ③ 공동 제안(`JOINT`) ④ 참여 보류 검토(`HOLD`). 책임자가 정해지면 빈 목록이다.
- **검토본 저장**: 필요한데 `UNRESOLVED`면 `review_save_blocked=true`이다. API는 422 `TFT_DRAFT_INCOMPLETE`, `details.integration_responsibility=UNRESOLVED`로 막는다(경로 27). 장비별 후보가 있어도 유닛 공급 가능으로 표시하지 않는다.
- **초안 전제 문장**: "유닛 전체의 조립·공장 시험·연동·보증을 맡을 통합 책임자: {자사|단일 협력사(유닛 일괄 공급·통합)|공동 제안|미확보}"(ASSUMPTIONS_EXCLUSIONS, 서버 생성).
- 후보 등급의 `integrator_capable` 검사는 협력사 등급(KAN-187 `partners/grading.py`)에 이미 있다.

## 측정 (`rfp_integration_responsibility_rate`)

`eval.rfp_metrics.summarize_integration_responsibility(정답, 예측)`. 정답은 문서별 조립품 공급 요구의 조항 번호, 예측은 문서별 `integration_responsibility` 결과다. 조항마다 예측이 필수 확인 항목을 올리고, 근거 조항에 그 조항이 있고, 책임자가 정해졌거나 처리 경로를 냈으면 분자다. 미예측 문서는 분자에 들지 않는다. 결과는 `rate`·`count`(n/m)·`denominator`·`missed`이고, `gate_values(…, integration)`가 게이트 값으로 꺼낸다(분모 0이면 None). 합격선은 `gates-rfp-v1` `== 1.0`이다.
