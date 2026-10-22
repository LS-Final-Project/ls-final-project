# 4조 업무지원 AI Agent — 에이전트 온보딩

갱신 2026-10-22. 이 레포는 C/S·RFP 에이전트의 코드 작업 저장소다. 이 파일은 AI 코딩 도구용 규칙이며 사람의 설치 안내는 [공통 온보딩](docs/team-start.md)이다.

작업 전 이 파일과 사용자가 지정한 Jira 티켓, 자기 이름의 노션 안내에서 첫 작업과 담당 범위를 확인한다. 로컬 실행이 필요하면 공통 온보딩의 1~5를 따른다. 모든 설계 및 운영 문서를 먼저 읽을 필요는 없다. 설계는 [아키텍처](docs/architecture.md), API 변경은 [계약 수정 안내](contracts/README.md)를 해당 작업에서 참고한다.

기획 원본은 아래 링크를 사용한다. 인프라 담당자 PC의 `/home/canae/ls_main/`은 보조 자료 위치일 뿐 다른 팀원의 필수 경로가 아니다. 폴더가 없다고 작업을 중단하거나 같은 경로를 생성하지 않는다.

---

## 한 줄 요약

전력기기 제조사의 해외 C/S 문의 대응(프로젝트 1)과 전력사업 RFP 수주 지원(프로젝트 2).
AI는 초안·근거를 준비하고, 사람이 최종 확인·승인한다.

---

## 확정된 스택

| 항목 | 결정 |
|---|---|
| 런타임 | Python 3.12 실행 기준 / FastAPI / GCP Cloud Run (서울 리전, `min-instances: 1`) |
| LLM | Vertex AI `global`의 `gemini-3.6-flash`로 연결 검증. 최종 업무 모델은 평가 후 결정 |
| 임베딩 | Gemini 다국어 임베딩. 모델과 차원은 검색 평가 후 확정 |
| DB | Cloud SQL PostgreSQL + pgvector + FTS 하이브리드 검색 |
| LangGraph 상태 | `langgraph-checkpoint-postgres` |
| 파일 | Cloud Storage |
| 인증 | Microsoft OAuth (MSAL, PKCE) + `users` 테이블 역할 관리 |
| 프론트 | React + Vite + TypeScript + TanStack Query, UI 키트 없음 |
| 오케스트레이션 | LangChain + LangGraph + LangSmith |
| CI/CD | GitHub Actions 검사 / Cloud Build 이미지 및 CD. dev 개발 통합, main 배포 |
| 배치·비동기 작업 | Cloud Tasks (RFP 문서 처리) / Cloud Run Jobs (평가 실행, 데이터 초기화) |
| 스트리밍 | SSE |

Redis, Kubernetes, BigQuery, FAISS 없음.

---

## 데이터와 기획 자료

| 자료 | 위치 |
|---|---|
| C/S 학습 데이터 | `data/`의 실제 위치와 이름을 확인. 데이터 기능 구현 시 필요한 자료만 Google Drive에서 받음 |
| RFP·시연 데이터 | `data/` 하위 (수집 완료 후 추가 예정) |
| C/S 최신 기획서 | [Google Docs](https://docs.google.com/document/d/1DhnpC5vyPEIhk3QJ3IRSTLX5f-U-HfpRMdCaGDVnGpg/edit) |
| RFP 최신 기획서 | [Google Docs](https://docs.google.com/document/d/1Zz_ps2r9oRM5wTt75L-RboCc1knqJ1WYGZZ9DZc09fQ/edit) |
| 스토리보드 | [Google Docs](https://docs.google.com/document/d/1fprJf4b1P0dqKcv7KbFFSxnX2xn_cZM0/edit) |

`data/`는 **읽기 전용**. 수정·생성·삭제 금지.

---

## 인증과 역할

- 사용자 식별: JWT의 `tid + oid` (이메일은 표시용만)
- 역할 조회: `users` 테이블 (`tid`, `oid`, `role`, `dept`, `email`)
- 등록되지 않은 사용자에게 역할 자동 부여 금지

| 역할 | 설명 |
|---|---|
| `cs_agent` | C/S 문의 처리 담당자 |
| `dept_staff` | 전문부서 담당자 (소속 부서 필드 필수) |
| `manager` | 관리자 (승인 범위 별도 정의) |
| `sales` | 수주·견적 담당자 |

**개발 모드**: DB에 시연 계정을 미리 생성하고 로그인 없이 계정 선택. `environment=production`이면 개발 모드 진입 불가.

---

## 현재 담당과 수정 경계

현재 개발 분담은 아래 실명 담당을 기준으로 한다. 과거 데이터·플랫폼, 백엔드, 프론트, 에이전트, 평가의 5개 세션 구분은 폴더 책임을 설명한 것이며 담당자가 여러 영역을 수정하는 것을 금지하는 별도 인원 배정이 아니다. 이 표와 [공통 안내의 담당 경계](docs/team-start.md#담당-경계와-공통-작업)를 함께 적용한다. 이후 사용자가 지정한 담당 범위와 Jira의 최신 배정이 우선한다.

| 담당 | 소유 기능 | 주로 수정할 경로 |
| --- | --- | --- |
| 김나영 | 인증·메일, C/S API와 결정론적 규칙, 통합 회귀 | `backend/api/platform/auth/`, `mail/`, `backend/api/cs/`, `backend/engine/cs/`의 규칙, 관련 마이그레이션·테스트 |
| 오새결 | 입력 어댑터·데이터·검색, C/S 및 TFT의 AI, 평가 실행·지표, 인프라 셋업 | `backend/engine/`의 어댑터·검색, `backend/agent/`, `backend/shared/ai.py`, `eval/`, 인프라 설정 |
| 김종하 | 프론트, TFT API, 플랫폼 평가 관리 API와 평가 화면 | `frontend/`, `backend/api/tft/`, `backend/api/platform/evaluations/`, 관련 마이그레이션·테스트 |

아직 없는 기능 폴더는 작업 시 추가하는 제안 경로다. `backend/engine/`과 플랫폼 앱은 기능 단위로 나눠 사용한다. 규칙은 김나영, 어댑터·검색은 오새결이며 플랫폼 auth/mail은 김나영, evaluations는 김종하다. 같은 최상위 폴더를 사용한다는 이유로 담당 기능의 수정을 막지 않는다.

자기 티켓에 필요한 기능·테스트·계약 산출물만 수정한다. 다른 담당 기능과 공용 라우터·설정·DTO를 바꿔야 하면 먼저 영향과 계약을 확인하고 변경을 작게 유지한다. 다른 사람의 미커밋 변경을 덮어쓰지 않는다. 세션 간 대화를 공유한다고 가정하지 않고 파일과 Jira에 결과를 남긴다.

---

## 절대 하지 말 것

- `data/` 수정·생성·삭제
- LLM에게 `priority`, `assigned_dept`, `product_family` 계산 요청
  → 결정론적 룰로 100% 재현됨
- LLM 입력에 정답 필드 포함 (누설 금지)
  → 입력 화이트리스트는 `build_input()` 한 곳에만
- **FLT-042 인용** — 인버터 누전차단기 감도 100/300mA 권고는 안전사고 유발 오류
- RMA 번호·방문일·보증 승인을 AI가 생성
- test 597건 2회 이상 사용
- `environment=production`에서 개발 모드(시연 계정 선택 화면) 활성화
- OpenDesign 프로토타입 파일 직접 수정 (수정 작업 중)

---

## 설계 원칙

- **문의 분류 단계** LLM 출력 4개: `category_code`, `urgency_flag`, `provided_info`, `return_subtype`
  조건 비교·부서 질문·회신 분석·초안 작성은 별도 AI 처리 단계
- `rule_engine`: 순수 Python, LLM 금지
- 청킹: MarkdownHeaderTextSplitter (섹션 단위)
- 인용 메타데이터: `doc_id`, `version`, `page`, `char_start`, `char_end`, `table_cell`, `image_region`
  RFP 이미지 본문도 원문 위치로 역추적 가능해야 함
- LLM 폴백: 스키마 위반 시 고정 문장으로 항목만 나열
- Gemini Explicit Cache: 문서 컨텍스트 반복 주입 비용 절감
- RFP 문서 처리(OCR·임베딩): Cloud Tasks로 분리, 처리 상태를 PostgreSQL에 저장

---

## 핵심 데이터 함정

1. **긴급도 라벨 누설**: PHONE 채널 티켓 180건 본문에 `Urgency: <정답>` 문자열이 박혀 있음.
   PHONE 비NONE 긴급도 100% 해당. 반드시 채널별 분리 보고.
2. **FLT-042 안전 오류**: 인용 차단 목록 항목.
3. **우선순위 정확도 상한**: test 기준 96.65%. 크게 넘으면 과적합 의심.
4. **SAFETY 표본 부족**: 전체 28건. CRITICAL 누락 0건 기준.

---

## 협업 규칙

- 브랜치·커밋·PR 제목에 Jira 번호 포함 (`KAN-xx`)
- PR은 검토자 1명 지정 후 병합
- 상세 규칙: `CONTRIBUTING.md`

---

## 확정 합격선

| 지표 | 기준 |
|---|---|
| 문의 유형 분류 (macro-F1) | ≥ 0.90 |
| CRITICAL(SAFETY+SITE_DOWN) 누락 | 0건 |
| 우선순위 (`adjusted=N` 부분집합) | 1.0000 |
| 긴급도 (macro-F1) | ≥ 0.70, 비NONE 재현율 ≥ 0.85 |
| 담당부서 | ≥ 0.93 |
| 출력 스키마 위반율 | 0 |

---

## 미확정 항목

- 최종 생성 모델, 임베딩 모델과 차원: 초기 연결 검증과 별도로 품질·토큰·지연 평가 후 확정
- RFP·시연 데이터의 범위와 판본: Jira와 데이터 목록을 확인. 에이전트는 `data/`를 변경하지 않음
- LangSmith 무료/유료 티어
