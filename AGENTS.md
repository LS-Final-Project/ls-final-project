# 4조 업무지원 AI Agent — 에이전트 온보딩

작성 2026-10-20. 이 레포는 C/S·RFP 에이전트의 **코드 작업 저장소**다.
설계 문서·데이터·기획서는 `/home/canae/ls_main/`에 있다. 코드를 쓰기 전에 먼저 읽어라.

---

## 한 줄 요약

전력기기 제조사의 해외 C/S 문의 대응(프로젝트 1)과 전력사업 RFP 수주 지원(프로젝트 2).
AI는 초안·근거를 준비하고, 사람이 최종 확인·승인한다.

---

## 확정된 스택

| 항목 | 결정 |
|---|---|
| 런타임 | Python 3.11+ / FastAPI / GCP Cloud Run (서울 리전, `min-instances: 1`) |
| LLM | Gemini (모델 ID는 사용 키로 지원 목록 확인 후 확정) |
| 임베딩 | Gemini 다국어 임베딩 (모델 ID 확인 후 확정, 원문 직접 임베딩) |
| DB | Cloud SQL PostgreSQL + pgvector + FTS 하이브리드 검색 |
| LangGraph 상태 | `langgraph-checkpoint-postgres` |
| 파일 | Cloud Storage |
| 인증 | Microsoft OAuth (MSAL, PKCE) + `users` 테이블 역할 관리 |
| 프론트 | React + Vite + TypeScript + TanStack Query, UI 키트 없음 |
| 오케스트레이션 | LangChain + LangGraph + LangSmith |
| CI/CD | Cloud Build |
| 배치·비동기 작업 | Cloud Tasks (RFP 문서 처리) / Cloud Run Jobs (평가 실행, 데이터 초기화) |
| 스트리밍 | SSE |

Redis, Kubernetes, BigQuery, FAISS 없음.

---

## 데이터와 기획 자료

| 자료 | 위치 |
|---|---|
| C/S 학습 데이터 | `data/CS_AI_Agent_Training_Data_v1/` (Google Drive에서 받아 넣을 것) |
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

## 세션 역할과 소유 경계

| 세션 | 소유 경계 |
|---|---|
| 데이터·플랫폼 | 로더, 룰 엔진 (`backend/engine/`) |
| 백엔드 | API 계층 (`backend/api/`) |
| 프론트 | 상담원 콘솔 UI (`frontend/`) |
| 에이전트 | 프롬프트, LLM 호출 (`backend/agent/`) |
| 평가 | 지표, 리포트 (`eval/`) |

자기 소유 경계 밖은 고치지 않는다. 세션은 서로의 대화를 못 본다. 공유되는 것은 파일뿐이다.

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

- Gemini 모델 ID: 사용 키로 지원 목록 확인 후 확정
- RFP·시연 데이터: 수집 완료 후 `data/` 추가
- LangSmith 무료/유료 티어
