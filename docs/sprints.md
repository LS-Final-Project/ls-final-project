# 스프린트 계획

기준일: 2026년 10월 21일. 기존 WBS를 반영한 Jira의 신규 계획 118건을 완료 예정일과 Blocks 선후관계로 나눴다. 개발 및 통합 배포 완료는 11월 3일, 발표는 11월 10일이다. 날짜는 한국 시간 기준이다.

## 배정 기준과 현재 적용 상태

스프린트 1은 10월 27일까지 완료하는 기반 작업, 스프린트 2는 11월 3일까지 완료하는 개발 및 검증 작업, 스프린트 3은 이후 안정화와 발표 작업이다. 담당자, 작업별 시작일과 기한, 검토자 및 선후관계는 유지한다. 실제 인프라는 오새결이 진행하되 공식 WBS 담당 배정은 유지한다.

계획 118건 중 실행 작업은 116건이고 예비시간은 2건이다. 완료 이력, 에픽과 현재 WBS 계획에 포함되지 않은 과거 SETUP 작업은 새 스프린트의 개발 실적으로 합산하지 않는다. 예비시간 KAN-169와 KAN-170은 3차 계획에 연결하지만 필요할 때만 실제 스프린트로 가져온다.

기존 KAN 보드 1의 실제 Sprint 필드에 1차 42건, 2차 66건, 3차 8건을 배정하고 조회로 검증했다. 1차는 ID 1 'KAN 1 스프린트', 2차는 ID 2 'KAN 1 스프린트 2', 3차는 ID 3 'KAN 1 스프린트 3'이다. 예비시간 2건은 스프린트 미배정 상태로 백로그에 남아 있다. 담당자, 작업별 시작일과 기한, 상태 및 기존 라벨이 유지된 것도 확인했다.

사용자가 화면에서 설정한 기간은 한국시간 기준 각 시작일 00:00부터 마지막 날 23:59까지이며 API의 UTC 시각을 변환해 확인했다. 세 스프린트 모두 아직 시작 전이다. 최종 검증 결과는 sprint-plan.json에 보관한다. 일부 작업의 plan-sprint-1 등 라벨은 보조 정보이며 전체 배정과 진행률은 Sprint 필드로 확인한다.

## 일정과 완료 조건

| 구분 | 기간 | 작업 수 | 완료 시 확인할 결과 |
| --- | --- | --- | --- |
| 1 공통 기반과 평가 실행 | 10/21~10/27 | 42 | 공통 플랫폼, C/S와 TFT 실행 골격 및 첫 배포, OAuth와 Graph 조기 확인, 평가 실행 및 토큰 계측 경로 |
| 2 기능 완성과 통합 배포 | 10/28~11/3 | 66 | 실메일과 C/S 및 TFT 전체 흐름, 평가 화면과 모델 비교, 권한 및 실패 복구와 E2E 검증, 배포 완료 판정 |
| 3 안정화와 발표 준비 | 11/4~11/10 | 8, 별도 예비시간 2 | 동결 구성 평가, 결함 회귀, 기획서와 소스 및 발표자료 정합화, 리허설과 최종 제출 점검 |

이렇게 나눈 이유는 모델 비교를 마지막에 몰지 않고 평가 실행 기반부터 만든 뒤 업무 구현과 함께 품질 및 비용을 비교하기 위해서다. 11월 3일은 기능 완성과 배포 판정의 경계로 유지하고 마지막 주를 미완성 기능 개발의 연장으로 사용하지 않는다.

1차의 TFT 화면 및 인력요청 API는 계약과 업무 상태를 검증하는 단계다. OCR, 실제 RFP 분석과 실메일을 모두 연결한 전체 흐름의 완성은 2차 목표다. 평가 콘솔 화면도 2차에 완성하며 1차에는 실행기, 저장, 계측과 관리 API를 준비한다.

KAN-79는 10/27~10/28, KAN-103은 10/27~10/29 작업이므로 1차에서 착수하고 2차에서 완료한다. 완료 예정일에 따라 2차에 배정하며 1차 완료 실적으로 잡지 않는다. 배정된 작업보다 뒤 스프린트에 선행 작업이 놓이는 경우는 0건이다.

## 공수와 병목

아래는 기존 작업 설명에 있는 AI 활용 기준 추정치이며 실제 소요 시간이나 팀원의 확약 시간이 아니다. 실행과 별도 검토를 합산하고, 기한이 있는 실행 작업으로 예비시간을 합산하지 않는다.

| 담당자 | 1차 실행 / 검토 | 2차 실행 / 검토 | 3차 실행 / 검토, 예비 포함 |
| --- | --- | --- | --- |
| 김나영 | 19.75h / 2.75h | 26.75h / 6.5h | 4.5h / 0.75h |
| 오새결 | 12.5h / 3.25h | 28.75h / 4.75h | 6h / 1h |
| 김종하 | 16h / 5h | 26.5h / 6.75h | 9.5h / 0.75h |

1차는 팀 실행 48.25h와 검토 11h, 2차는 실행 82h와 검토 18h다. 3차는 실행 14h와 검토 2h에 예비 실행 6h 및 검토 0.5h를 별도로 확보한 계획이다. 검토 공수는 해당 작업의 검토자에게 합산했다.

가장 위험한 구간은 2차다. 검색 및 문서 처리에서 AI 초안으로 이어지는 경로와, 인증 및 메일에서 업무 발송으로 이어지는 경로가 통합 검증에 모인다. 김종하는 프론트와 TFT 백엔드 및 평가 관리 API를 함께 맡으므로 API 계약과 모의 응답을 먼저 맞춰 대기를 줄인다. 매일 막힌 선행 작업과 검토 대기를 확인하고 실제 속도에 따라 재계획한다.

공수표는 공식 담당자 기준이다. 오새결이 실제 인프라 세팅을 맡으므로 실제 수행 현황에서 인프라 시간을 별도로 확인해 오새결의 가용 시간을 과대평가하지 않는다.

## 중간 점검과 동결

| 시점 | 점검할 내용 | 미충족 시 처리 |
| --- | --- | --- |
| 10/23 | 데이터 및 API 계약, 로컬 실행, 실제 OAuth 및 Graph 조기 검증 결과 | 인증과 데이터 접근 장애를 우선 해결하고 후속 작업의 모의 연결 범위를 기록 |
| 10/27 | 첫 배포, 사용자 및 권한 기반, 평가 실행과 계측 경로 | 2차에 들어가기 전 막힌 작업의 담당 및 해소 시점을 정하고 후속 영향을 공개 |
| 10/30 | C/S 및 TFT 핵심 경로 연결, 검색 및 모델 비교, 메일 연결 | 추가 기능보다 전체 흐름의 실패와 권한 및 근거 결함을 우선 처리 |
| 11/3 | KAN-149~KAN-160의 회귀, E2E, 부하, 복구 및 배포 판정 | 미완료를 숨기거나 기한을 자동 연장하지 않고 막힌 완료 조건과 복구 계획을 기록 |
| 11/6 | 발표 자료, 제출 묶음과 전체 리허설 | 결함과 시연 위험을 분류하고 예비시간 투입 여부 결정 |
| 11/9 | 최종 배포, 계정 및 제출 링크 점검 | 검증한 구성과 백업 시연 기록으로 발표 준비 |

1차에서 잠그는 것은 개발용 평가 사례와 판본이다. 최종 test 597건을 조기 튜닝이나 반복 비교에 사용하지 않는다. 3차 최종 평가는 사용 이력을 확인한 동결 구성으로 수행하고, test를 2회 이상 사용하지 않는 기준을 지킨다. C/S 합격선과 안전 기준은 [의사결정 문서](decisions.md#평가와-품질)를 따른다.

최소 인스턴스, 리소스 생성이나 배포 승인은 이 일정표를 채웠다는 이유로 완료 처리하지 않는다. 실제 실행 및 동료 검토를 통과한 작업만 완료한다. 새 결함은 재현 조건, 영향과 회귀 근거를 연결하고 예비시간 자체를 기능 완료로 집계하지 않는다.

## 작업별 배정표

Jira 작업 번호의 링크에서 기존 완료 조건과 선행 작업을 확인할 수 있다. 아래 제목은 조회 시점 값이며 '두 FastAPI 앱'처럼 이전 실행 구조 표현이 남은 작업도 최신 확정 구조인 업무 앱 두 개와 공통 플랫폼 앱 한 개로 수행한다.

### 스프린트 1 공통 기반과 평가 실행

기간: 2026-10-21~2026-10-27. 목표: 세 서비스 실행 기반과 로그인 연결, 평가 실행 및 계측 경로를 준비한다.

| Jira | 작업 | 담당 | 기존 일정 | 구분 |
| --- | --- | --- | --- | --- |
| [KAN-11](https://lsteam4.atlassian.net/browse/KAN-11) | [D01] 최신 범위, 요구사항 추적표 정합화 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-54](https://lsteam4.atlassian.net/browse/KAN-54) | [D02] C/S 출력 필드, 개선 임계값 충돌 해소 | 오새결 | 10-22~10-22 | 실행 |
| [KAN-55](https://lsteam4.atlassian.net/browse/KAN-55) | [D03] 공통 ID, 데이터 계약, 소유 스키마 정의 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-56](https://lsteam4.atlassian.net/browse/KAN-56) | [D04] OpenAPI, 오류, 작업, SSE 계약 작성 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-57](https://lsteam4.atlassian.net/browse/KAN-57) | [D05] 화면, 업무 상태 전이, 권한 행렬 정리 | 김종하 | 10-22~10-22 | 실행 |
| [KAN-58](https://lsteam4.atlassian.net/browse/KAN-58) | [D06] 미완료 스토리보드, TFT 화면 개정 | 김종하 | 10-22~10-22 | 실행 |
| [KAN-59](https://lsteam4.atlassian.net/browse/KAN-59) | [D07] 기획, PPT, 현업 질문 제출 상태 확인 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-60](https://lsteam4.atlassian.net/browse/KAN-60) | [D08] WBS 작업, Jira 번호, PR 추적 연결 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-61](https://lsteam4.atlassian.net/browse/KAN-61) | [D09] 기획 원본, Notion의 현재 범위, 설계 정합화 | 김종하 | 10-22~10-22 | 실행 |
| [KAN-62](https://lsteam4.atlassian.net/browse/KAN-62) | [P01] 교육장 GCP 서비스, IAM, 할당량 확인 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-63](https://lsteam4.atlassian.net/browse/KAN-63) | [P02] 두 FastAPI 앱, 공통 패키지 실행 골격 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-64](https://lsteam4.atlassian.net/browse/KAN-64) | [P03] React, Vite, TS, Query 실행 골격 | 김종하 | 10-22~10-22 | 실행 |
| [KAN-65](https://lsteam4.atlassian.net/browse/KAN-65) | [P04] 로컬 PostgreSQL, 초기 마이그레이션 | 김나영 | 10-23~10-23 | 실행 |
| [KAN-66](https://lsteam4.atlassian.net/browse/KAN-66) | [P05] Cloud SQL, Storage, Secret Manager 구성 | 김나영 | 10-23~10-23 | 실행 |
| [KAN-67](https://lsteam4.atlassian.net/browse/KAN-67) | [P06] 컨테이너, Cloud Build 기본 CI | 김나영 | 10-23~10-23 | 실행 |
| [KAN-68](https://lsteam4.atlassian.net/browse/KAN-68) | [P07] 통합 환경 웹, CS, TFT 첫 배포 | 김나영 | 10-26~10-26 | 실행 |
| [KAN-69](https://lsteam4.atlassian.net/browse/KAN-69) | [P08] Tasks, Jobs, 서비스 계정, SSE 기반 | 김나영 | 10-26~10-26 | 실행 |
| [KAN-70](https://lsteam4.atlassian.net/browse/KAN-70) | [P09] 구조화 로그, 오류, 요청ID, 추적 설정 | 김나영 | 10-26~10-26 | 실행 |
| [KAN-76](https://lsteam4.atlassian.net/browse/KAN-76) | [U00] 학교 계정, 실제 앱 OAuth/Graph 조기 검증 | 김나영 | 10-22~10-22 | 실행 |
| [KAN-77](https://lsteam4.atlassian.net/browse/KAN-77) | [U01] MSAL, PKCE, OAuth 연결 경계 구현 | 김나영 | 10-23~10-26 | 실행 |
| [KAN-78](https://lsteam4.atlassian.net/browse/KAN-78) | [U02] 사용자, 다중 역할, 프로젝트 권한 구현 | 김나영 | 10-26~10-26 | 실행 |
| [KAN-80](https://lsteam4.atlassian.net/browse/KAN-80) | [U04] 학교 OAuth, Graph 읽기 연결 검증 | 김나영 | 10-27~10-27 | 실행 |
| [KAN-82](https://lsteam4.atlassian.net/browse/KAN-82) | [V01] 과제별 지표, 합격선, 성능 목표 사전 확정 | 오새결 | 10-22~10-22 | 실행 |
| [KAN-83](https://lsteam4.atlassian.net/browse/KAN-83) | [V02] 데이터, 검수, 계열, 사용 이력 감사 | 오새결 | 10-22~10-22 | 실행 |
| [KAN-84](https://lsteam4.atlassian.net/browse/KAN-84) | [V03] 모델 공급자, 과제별 호출 인터페이스 | 오새결 | 10-23~10-23 | 실행 |
| [KAN-85](https://lsteam4.atlassian.net/browse/KAN-85) | [V04] 실험, 실행, 사례, 사용량 저장 스키마 | 김종하 | 10-23~10-23 | 실행 |
| [KAN-86](https://lsteam4.atlassian.net/browse/KAN-86) | [V05] 공통 평가 실행기, 설정 로딩 구현 | 오새결 | 10-23~10-23 | 실행 |
| [KAN-87](https://lsteam4.atlassian.net/browse/KAN-87) | [V06] 토큰, 비용, 시간, 캐시, 재시도 계측 | 오새결 | 10-26~10-26 | 실행 |
| [KAN-88](https://lsteam4.atlassian.net/browse/KAN-88) | [V07] 기준선, 원시/폴백, 집계 리포트 | 오새결 | 10-27~10-27 | 실행 |
| [KAN-89](https://lsteam4.atlassian.net/browse/KAN-89) | [V08] LangSmith 연결, 권한, 전송 정책 확인 | 오새결 | 10-26~10-26 | 실행 |
| [KAN-90](https://lsteam4.atlassian.net/browse/KAN-90) | [V09] 평가 Jobs, 실행 관리 API 구현 | 김종하 | 10-27~10-27 | 실행 |
| [KAN-91](https://lsteam4.atlassian.net/browse/KAN-91) | [R01] 데이터 목록, 출처, 스냅샷, 이용 범위 정리 | 오새결 | 10-23~10-23 | 실행 |
| [KAN-92](https://lsteam4.atlassian.net/browse/KAN-92) | [R02] 공통 입력 어댑터, 검증, 격리 구현 | 김종하 | 10-26~10-26 | 실행 |
| [KAN-93](https://lsteam4.atlassian.net/browse/KAN-93) | [R03] C/S, 메일 정답/경계 사례 교차검수 준비 | 오새결 | 10-26~10-26 | 실행 |
| [KAN-94](https://lsteam4.atlassian.net/browse/KAN-94) | [R04] TFT 역할, 후보, 가용성 검수 사례 작성 | 김종하 | 10-23~10-23 | 실행 |
| [KAN-95](https://lsteam4.atlassian.net/browse/KAN-95) | [R05] 평가 정답, 출처, 분모 교차 검수, 잠금 | 김나영 | 10-27~10-27 | 실행 |
| [KAN-96](https://lsteam4.atlassian.net/browse/KAN-96) | [R06] 네이티브 추출, 영역 라우팅, 공통 표현 | 오새결 | 10-26~10-26 | 실행 |
| [KAN-119](https://lsteam4.atlassian.net/browse/KAN-119) | [T01] TFT 프로젝트, 역할, 요청, 후보 스키마/API | 김종하 | 10-23~10-23 | 실행 |
| [KAN-122](https://lsteam4.atlassian.net/browse/KAN-122) | [T04] 초기 검토팀, 부서 인력요청 확정 API | 김종하 | 10-26~10-27 | 실행 |
| [KAN-123](https://lsteam4.atlassian.net/browse/KAN-123) | [T05] 부서 후보 추천, 경험 근거, 가용성 API | 김종하 | 10-26~10-26 | 실행 |
| [KAN-129](https://lsteam4.atlassian.net/browse/KAN-129) | [F01] 공통 레이아웃, Query, 오류, 상태 UI | 김종하 | 10-23~10-23 | 실행 |
| [KAN-134](https://lsteam4.atlassian.net/browse/KAN-134) | [F06] TFT 프로젝트, RFP, 역할 검토 UI | 김종하 | 10-27~10-27 | 실행 |

### 스프린트 2 기능 완성과 통합 배포

기간: 2026-10-28~2026-11-03. 목표: 실메일과 두 업무의 전체 흐름 및 평가 화면을 연결하고 합격선과 통합 검증을 거쳐 배포한다.

| Jira | 작업 | 담당 | 기존 일정 | 구분 |
| --- | --- | --- | --- | --- |
| [KAN-71](https://lsteam4.atlassian.net/browse/KAN-71) | [M01] 공통 메일 수집, 동기화, 첨부 적재 | 김나영 | 10-28~10-28 | 실행 |
| [KAN-72](https://lsteam4.atlassian.net/browse/KAN-72) | [M02] 메일 워커, 스케줄, 중복 작업 방지 | 김나영 | 10-28~10-28 | 실행 |
| [KAN-73](https://lsteam4.atlassian.net/browse/KAN-73) | [M03] 메일 유형 분류, 업무 연결 추천 | 오새결 | 10-28~10-28 | 실행 |
| [KAN-74](https://lsteam4.atlassian.net/browse/KAN-74) | [M04] 검토본 발송, 결과, 감사 이력 | 김나영 | 10-28~10-29 | 실행 |
| [KAN-75](https://lsteam4.atlassian.net/browse/KAN-75) | [M05] 회신, 스레드, 업무 링크 연결 | 김나영 | 10-29~10-29 | 실행 |
| [KAN-79](https://lsteam4.atlassian.net/browse/KAN-79) | [U03] 개발 계정 선택, 시연 환경 차단 | 김나영 | 10-27~10-28 | 실행 |
| [KAN-81](https://lsteam4.atlassian.net/browse/KAN-81) | [U05] 사서함 토큰 보관, 연결 해제 | 김나영 | 10-28~10-28 | 실행 |
| [KAN-97](https://lsteam4.atlassian.net/browse/KAN-97) | [R07] OCR, 표 구조 후보 비교 및 처리 구현 | 오새결 | 10-28~10-28 | 실행 |
| [KAN-98](https://lsteam4.atlassian.net/browse/KAN-98) | [R08] 원문 위치, 판본, 관련 절 연결 구현 | 오새결 | 10-28~10-28 | 실행 |
| [KAN-99](https://lsteam4.atlassian.net/browse/KAN-99) | [R09] 섹션 청킹, 임베딩, FTS 색인 구현 | 오새결 | 10-28~10-29 | 실행 |
| [KAN-100](https://lsteam4.atlassian.net/browse/KAN-100) | [R10] 하이브리드 검색, RRF, 리랭킹 구현 | 오새결 | 10-29~10-29 | 실행 |
| [KAN-101](https://lsteam4.atlassian.net/browse/KAN-101) | [R11] 검색, 문서 처리 채점기와 분리 실험 | 오새결 | 10-29~10-29 | 실행 |
| [KAN-102](https://lsteam4.atlassian.net/browse/KAN-102) | [C01] C/S 문의, 설비, 사례 스키마/API | 김나영 | 10-28~10-29 | 실행 |
| [KAN-103](https://lsteam4.atlassian.net/browse/KAN-103) | [C02] build_input, 분류 스키마, 안전 차단 구현 | 오새결 | 10-27~10-29 | 실행 |
| [KAN-104](https://lsteam4.atlassian.net/browse/KAN-104) | [C03] 우선순위, 부서, 제품군, SLA 순수 룰 | 김나영 | 10-29~10-29 | 실행 |
| [KAN-105](https://lsteam4.atlassian.net/browse/KAN-105) | [C04] 문의 분류, 긴급도 후보 비교 | 오새결 | 10-29~10-29 | 실행 |
| [KAN-106](https://lsteam4.atlassian.net/browse/KAN-106) | [C05] 추가정보, 질문, 답변초안 생성 | 오새결 | 10-29~10-29 | 실행 |
| [KAN-107](https://lsteam4.atlassian.net/browse/KAN-107) | [C06] 문의 처리 작업, 체크포인트, SSE 연결 | 김나영 | 10-29~10-29 | 실행 |
| [KAN-108](https://lsteam4.atlassian.net/browse/KAN-108) | [C07] 담당 검토, 수정, 부서 협의, 종결 API | 김나영 | 10-29~10-29 | 실행 |
| [KAN-109](https://lsteam4.atlassian.net/browse/KAN-109) | [C08] 회신 해석, 이력 대조, 초안 갱신 | 오새결 | 10-30~10-30 | 실행 |
| [KAN-110](https://lsteam4.atlassian.net/browse/KAN-110) | [C09] C/S 실메일, 회신, 사례 연결 | 김나영 | 10-29~10-30 | 실행 |
| [KAN-111](https://lsteam4.atlassian.net/browse/KAN-111) | [C10] 고객, 설비 이력, 처리/개선 현황 집계 API | 김나영 | 10-30~10-30 | 실행 |
| [KAN-112](https://lsteam4.atlassian.net/browse/KAN-112) | [I01] 개선 요청, 근거, 부서 회신, 조치 모델/API | 김나영 | 10-30~10-30 | 실행 |
| [KAN-113](https://lsteam4.atlassian.net/browse/KAN-113) | [I02] 반복, 펌웨어, 문서 후보 결정 규칙 | 김나영 | 10-30~10-30 | 실행 |
| [KAN-114](https://lsteam4.atlassian.net/browse/KAN-114) | [I03] 조건 비교, 반복 이슈 의미 보조 분석 | 오새결 | 10-30~10-30 | 실행 |
| [KAN-115](https://lsteam4.atlassian.net/browse/KAN-115) | [I04] 개선 요청, 부서 질문 초안 | 오새결 | 11-02~11-02 | 실행 |
| [KAN-116](https://lsteam4.atlassian.net/browse/KAN-116) | [I05] 요청 검토, 전달, 회신, 조치 추적 | 김나영 | 10-30~10-30 | 실행 |
| [KAN-117](https://lsteam4.atlassian.net/browse/KAN-117) | [I06] 재요청, 조치 후 재발, 재개방 규칙 | 김나영 | 11-02~11-02 | 실행 |
| [KAN-118](https://lsteam4.atlassian.net/browse/KAN-118) | [I07] 개선 회신 해석, 후속 행동 연결 | 오새결 | 11-02~11-02 | 실행 |
| [KAN-120](https://lsteam4.atlassian.net/browse/KAN-120) | [T02] RFP 업로드, 문서처리 작업, 상태 API | 김종하 | 10-29~10-30 | 실행 |
| [KAN-121](https://lsteam4.atlassian.net/browse/KAN-121) | [T03] RFP 과업, 역할, 필수/우대 도출 | 오새결 | 10-29~10-29 | 실행 |
| [KAN-124](https://lsteam4.atlassian.net/browse/KAN-124) | [T06] 추천 후보 근거 비교, 검증 질문 | 오새결 | 10-30~10-30 | 실행 |
| [KAN-125](https://lsteam4.atlassian.net/browse/KAN-125) | [T07] 부서 검증, 중복투입, 팀 공백 규칙 | 김종하 | 10-28~10-28 | 실행 |
| [KAN-126](https://lsteam4.atlassian.net/browse/KAN-126) | [T08] TFT 구성안, 공백, 다음 행동 초안 | 오새결 | 11-02~11-02 | 실행 |
| [KAN-127](https://lsteam4.atlassian.net/browse/KAN-127) | [T09] 책임자 승인, 반려, 변경 영향 API | 김종하 | 10-29~10-30 | 실행 |
| [KAN-128](https://lsteam4.atlassian.net/browse/KAN-128) | [T10] TFT AI작업, 메일, 회신 연결 | 김종하 | 11-02~11-02 | 실행 |
| [KAN-130](https://lsteam4.atlassian.net/browse/KAN-130) | [F02] 로그인, 계정, 메일 연결 UI | 김종하 | 10-29~11-02 | 실행 |
| [KAN-131](https://lsteam4.atlassian.net/browse/KAN-131) | [F03] C/S 홈, 목록, 원문, 분류 상세 | 김종하 | 10-28~10-29 | 실행 |
| [KAN-132](https://lsteam4.atlassian.net/browse/KAN-132) | [F04] C/S 근거, 초안, 회신, 설비 이력 UI | 김종하 | 10-30~11-02 | 실행 |
| [KAN-133](https://lsteam4.atlassian.net/browse/KAN-133) | [F05] 개선 후보, 요청, 부서 회신, 조치 UI | 김종하 | 10-30~11-02 | 실행 |
| [KAN-135](https://lsteam4.atlassian.net/browse/KAN-135) | [F07] 후보 추천, 경험, 가용성, 부서 검증 UI | 김종하 | 10-28~10-29 | 실행 |
| [KAN-136](https://lsteam4.atlassian.net/browse/KAN-136) | [F08] TFT 구성안, 공백, 책임자 결정 UI | 김종하 | 11-02~11-02 | 실행 |
| [KAN-137](https://lsteam4.atlassian.net/browse/KAN-137) | [F09] 평가 설정, 실행, 진행 화면 | 김종하 | 10-28~10-28 | 실행 |
| [KAN-138](https://lsteam4.atlassian.net/browse/KAN-138) | [F10] 실험 품질, 토큰, 비용, 지연 비교 UI | 김종하 | 10-29~10-29 | 실행 |
| [KAN-139](https://lsteam4.atlassian.net/browse/KAN-139) | [F11] 사례 상세, 근거, trace, CI 결과 UI | 김종하 | 10-30~11-02 | 실행 |
| [KAN-140](https://lsteam4.atlassian.net/browse/KAN-140) | [F12] 실 API, SSE, 권한, 상태 UI 통합 | 김종하 | 11-02~11-03 | 실행 |
| [KAN-141](https://lsteam4.atlassian.net/browse/KAN-141) | [F13] 고객 이력, 관리자 현황 화면 | 김종하 | 11-02~11-02 | 실행 |
| [KAN-142](https://lsteam4.atlassian.net/browse/KAN-142) | [V10] C/S, 메일, 초안 평가기 구현 | 오새결 | 10-30~10-30 | 실행 |
| [KAN-143](https://lsteam4.atlassian.net/browse/KAN-143) | [V11] TFT 역할, 후보, 공백 평가기 구현 | 오새결 | 10-28~10-29 | 실행 |
| [KAN-144](https://lsteam4.atlassian.net/browse/KAN-144) | [V12] 모델, OCR, 임베딩, 리랭커 후보 비교 | 오새결 | 10-30~10-30 | 실행 |
| [KAN-145](https://lsteam4.atlassian.net/browse/KAN-145) | [V13] 상위 후보 반복, 캐시, 예산 비교 | 오새결 | 10-30~10-30 | 실행 |
| [KAN-146](https://lsteam4.atlassian.net/browse/KAN-146) | [V14] 과제별 모델, 검색, 처리 설정 채택 | 오새결 | 11-02~11-02 | 실행 |
| [KAN-147](https://lsteam4.atlassian.net/browse/KAN-147) | [V15] PR, 정기, 설정변경 평가 CI 연결 | 김나영 | 11-02~11-02 | 실행 |
| [KAN-148](https://lsteam4.atlassian.net/browse/KAN-148) | [V16] 평가 콘솔, 리포트 수치 대조 | 오새결 | 11-02~11-02 | 실행 |
| [KAN-149](https://lsteam4.atlassian.net/browse/KAN-149) | [Q01] 인증, 객체권한, 승인, 입력보호 회귀 | 김나영 | 11-02~11-02 | 실행 |
| [KAN-150](https://lsteam4.atlassian.net/browse/KAN-150) | [Q02] 재시도, 동시요청, 중단, 복구 테스트 | 김나영 | 11-02~11-03 | 실행 |
| [KAN-151](https://lsteam4.atlassian.net/browse/KAN-151) | [Q03] 원문, 데이터 교체, 인용 안전 회귀 | 오새결 | 11-02~11-03 | 실행 |
| [KAN-152](https://lsteam4.atlassian.net/browse/KAN-152) | [Q04] C/S 문의, 개선 전체 흐름 API 검증 | 김나영 | 11-02~11-03 | 실행 |
| [KAN-153](https://lsteam4.atlassian.net/browse/KAN-153) | [Q05] TFT 전체 흐름, 변경, 승인 검증 | 김종하 | 11-03~11-03 | 실행 |
| [KAN-154](https://lsteam4.atlassian.net/browse/KAN-154) | [Q06] 주요 화면 E2E, 브라우저 검증 | 김종하 | 11-03~11-03 | 실행 |
| [KAN-155](https://lsteam4.atlassian.net/browse/KAN-155) | [Q07] 일반 API, 비동기, 동시사용 부하 검증 | 김나영 | 11-03~11-03 | 실행 |
| [KAN-156](https://lsteam4.atlassian.net/browse/KAN-156) | [Q08] 품질, 토큰, 비용, 지연 회귀와 결함 분류 | 오새결 | 11-03~11-03 | 실행 |
| [KAN-157](https://lsteam4.atlassian.net/browse/KAN-157) | [Q09] 시연 환경, 이전 리비전, DB 복구 검증 | 김나영 | 11-03~11-03 | 실행 |
| [KAN-158](https://lsteam4.atlassian.net/browse/KAN-158) | [Q10] 대표 데이터, 시나리오, 평가 구성 동결 | 오새결 | 11-03~11-03 | 실행 |
| [KAN-159](https://lsteam4.atlassian.net/browse/KAN-159) | [Q11] 11/3 개발 완료, 배포 승인 점검 | 김나영 | 11-03~11-03 | 실행 |
| [KAN-160](https://lsteam4.atlassian.net/browse/KAN-160) | [Q12] 개발 인수인계, 실행 안내, 소스 태그 | 김종하 | 11-03~11-03 | 실행 |

### 스프린트 3 안정화와 발표 준비

기간: 2026-11-04~2026-11-10. 목표: 기능 범위를 동결한 상태에서 결함 수정, 최종 평가, 제출물과 발표 리허설을 마친다.

| Jira | 작업 | 담당 | 기존 일정 | 구분 |
| --- | --- | --- | --- | --- |
| [KAN-161](https://lsteam4.atlassian.net/browse/KAN-161) | [Z01] 동결 구성 최종 평가, 한계 보고 | 오새결 | 11-04~11-04 | 실행 |
| [KAN-162](https://lsteam4.atlassian.net/browse/KAN-162) | [Z02] 최종검증 결함 수정, 영향 회귀 | 김나영 | 11-04~11-04 | 실행 |
| [KAN-163](https://lsteam4.atlassian.net/browse/KAN-163) | [Z03] 기획서, 아키텍처, 화면, WBS 최종 정합화 | 김종하 | 11-04~11-04 | 실행 |
| [KAN-164](https://lsteam4.atlassian.net/browse/KAN-164) | [Z04] 프로젝트별 발표 자료, 평가 비교표 완성 | 김종하 | 11-05~11-05 | 실행 |
| [KAN-165](https://lsteam4.atlassian.net/browse/KAN-165) | [Z05] 소스, 실행환경, 기획서, 발표 제출 묶음 | 김나영 | 11-04~11-04 | 실행 |
| [KAN-166](https://lsteam4.atlassian.net/browse/KAN-166) | [Z06] 발표 시나리오, 질문, 백업 기록 준비 | 오새결 | 11-05~11-05 | 실행 |
| [KAN-167](https://lsteam4.atlassian.net/browse/KAN-167) | [Z07] 전체 리허설, 시간, 역할, 화면 점검 | 김종하 | 11-06~11-06 | 실행 |
| [KAN-168](https://lsteam4.atlassian.net/browse/KAN-168) | [Z08] 11/9 최종 배포, 계정, 제출 링크 점검 | 김나영 | 11-09~11-09 | 실행 |
| [KAN-169](https://lsteam4.atlassian.net/browse/KAN-169) | [Z09] AI, 검색 결함 수정, 영향 평가 예비시간 | 오새결 | 11-04~11-04 | 예비, 기본 배정 제외 |
| [KAN-170](https://lsteam4.atlassian.net/browse/KAN-170) | [Z10] 화면, 권한, 문서 결함 수정 예비시간 | 김종하 | 11-04~11-04 | 예비, 기본 배정 제외 |

## 연결 자료

- [Jira 백로그](https://lsteam4.atlassian.net/jira/software/projects/KAN/boards/1/backlog)
- [기존 WBS](https://docs.google.com/spreadsheets/d/11X85xRDncd_xg3EPhcoke6dhF1fhTByKzhpRxLk6ow8/edit#gid=1680447802)
- [배정 데이터](sprint-plan.json)
- [스프린트 기능 활성화 안내](https://support.atlassian.com/jira-software-cloud/docs/enable-sprints/)

사용자가 기존 팀 관리형 KAN의 기능 설정에서 백로그와 스프린트를 켠 뒤 추가 스프린트 두 개를 만들고 날짜를 설정했다. 첫 종료시간이 마지막 날 00:00으로 저장돼 있었으므로 해당 날짜 23:59로 수정한 뒤 다시 확인했다. 연결 도구는 스프린트 기능 활성화, 생성과 날짜 편집을 지원하지 않아 이 단계는 사용자 화면 조작으로 처리했고, 작업 배정과 검증은 도구로 수행했다. 기존 프로젝트와 이슈 키를 유지했다. 설정 경로는 위 공식 안내를 참고한다.
