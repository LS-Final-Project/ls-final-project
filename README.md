# Team4 업무지원 AI

해외 고객의 C/S 문의와 전력 사업 제안요청서(RFP) 대응을 돕는 두 AI Agent 서비스입니다. LS·성균관대 AI Agent 교육 과정 4조 최종 프로젝트입니다.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.x-1C3C3C?logo=langchain&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-3.6%20flash-8E75B2?logo=googlegemini&logoColor=white)
![Cloud Run](https://img.shields.io/badge/Google%20Cloud%20Run-Seoul-4285F4?logo=googlecloud&logoColor=white)

<table>
  <tr>
    <th width="50%">프로젝트 1 · 해외 고객 C/S</th>
    <th width="50%">프로젝트 2 · RFP 대응·협력사 보완</th>
  </tr>
  <tr>
    <td><a href="docs/assets/readme/p1-hero.gif"><img src="docs/assets/readme/p1-hero.gif" alt="C/S 화면에서 AI 분류, 부서 답변 기반 회신 초안, 같은 고장 추천, 개선요청 초안이 차례로 나오는 장면" width="100%"></a></td>
    <td><a href="docs/assets/readme/p2-hero.gif"><img src="docs/assets/readme/p2-hero.gif" alt="RFP 문서에서 요구를 찾고, 자사 사양과 비교한 판정 이유와 기술 부서 회신을 보고, 검토본 XLSX를 내보내는 장면" width="100%"></a></td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <p>해외 고객의 문의 접수부터 회신, 반복 고장에 대한 개선요청까지 C/S 업무를 지원합니다.</p>
      <ul>
        <li>AI가 문의 유형과 긴급 여부를 분류하고, 정책에 따른 규칙으로 우선순위·담당 부서·응답 기한을 정합니다.</li>
        <li>제품·정책 문서를 근거로 회신 초안을 작성하고, 각 문장의 근거와 확정적인 약속이 포함됐는지 검증합니다.</li>
        <li>해결 기록을 기존 고장과 비교해 같은 고장을 추천합니다. 같은 고장이 90일 안에 서로 다른 고객 세 곳 이상에서 발생하면 개선요청 초안을 작성해 담당 부서에 전달합니다.</li>
        <li>고객 회신은 담당자가 확인한 뒤에만 발송합니다.</li>
      </ul>
    </td>
    <td width="50%" valign="top">
      <p>메일로 받은 제안요청서(RFP)에서 요구사항을 추출해 자사 사양과 비교하고, 부족한 부분의 보완 방안과 검토본 초안을 준비합니다.</p>
      <ul>
        <li>DOCX·PDF를 조항 단위로 분석하고, 변경 추적에 남은 삭제된 값도 보존합니다.</li>
        <li>요구값과 자사 사양의 속성·단위·조건이 같은 경우에만 규칙으로 비교하고, 결과를 네 가지로 판정합니다.</li>
        <li>요구를 충족하지 못한 부분에는 다른 자사 제품, 자사 개발, 가상 협력사, 대체 사양 등의 보완 경로를 검토할 순서대로 제시합니다.</li>
        <li>사람이 승인한 변경만 반영하고, 검토본을 DOCX 요약서와 XLSX 대응 현황으로 내보냅니다.</li>
      </ul>
    </td>
  </tr>
</table>

## 아키텍처

![C/S · RFP 통합 AI 서비스 아키텍처](docs/assets/readme/architecture.png)

## 기술 스택

<table>
  <tr>
    <td width="33%" align="center" valign="top">
      <h3>백엔드</h3>
      <p>
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/python/python-original.svg" alt="Python" width="44" height="44">
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/fastapi/fastapi-original.svg" alt="FastAPI" width="44" height="44">
      </p>
      <p><b>Python 3.11+ · FastAPI 0.115+</b><br><sub>API · 입력 검증 · 업무 규칙</sub></p>
    </td>
    <td width="33%" align="center" valign="top">
      <h3>프론트엔드</h3>
      <p>
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/react/react-original.svg" alt="React" width="44" height="44">
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/typescript/typescript-original.svg" alt="TypeScript" width="44" height="44">
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/vitejs/vitejs-original.svg" alt="Vite" width="44" height="44">
      </p>
      <p><b>React 19 · TypeScript 5 · Vite 7</b><br><sub>TanStack Query 5 · React Router 7</sub></p>
    </td>
    <td width="33%" align="center" valign="top">
      <h3>AI</h3>
      <p>
        <img src="https://cdn.jsdelivr.net/npm/simple-icons@16.0.0/icons/googlegemini.svg" alt="Gemini" width="44" height="44">
        <img src="https://cdn.jsdelivr.net/npm/simple-icons@16.0.0/icons/langgraph.svg" alt="LangGraph" width="44" height="44">
      </p>
      <p><b>Gemini · LangGraph 1.x</b><br><sub>분류 · 요구 추출 · 초안 생성</sub></p>
    </td>
  </tr>
  <tr>
    <td width="33%" align="center" valign="top">
      <h3>데이터 · 검색</h3>
      <p>
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/postgresql/postgresql-original.svg" alt="PostgreSQL" width="44" height="44">
      </p>
      <p><b>PostgreSQL · pgvector · FTS</b><br><sub>Cloud SQL · 하이브리드 검색</sub></p>
    </td>
    <td width="33%" align="center" valign="top">
      <h3>인프라 · 배포</h3>
      <p>
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/googlecloud/googlecloud-original.svg" alt="Google Cloud" width="44" height="44">
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/githubactions/githubactions-original.svg" alt="GitHub Actions" width="44" height="44">
      </p>
      <p><b>Google Cloud · GitHub Actions</b><br><sub>Cloud Run · Cloud Build · Storage</sub></p>
    </td>
    <td width="33%" align="center" valign="top">
      <h3>테스트 · 품질</h3>
      <p>
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/pytest/pytest-original.svg" alt="pytest" width="44" height="44">
        <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/playwright/playwright-original.svg" alt="Playwright" width="44" height="44">
      </p>
      <p><b>pytest · Playwright</b><br><sub>Vitest · Ruff · mypy</sub></p>
    </td>
  </tr>
</table>

<details>
<summary><strong>기술 상세</strong></summary>

| 구분 | 사용 기술 |
| --- | --- |
| 백엔드 | Python 3.11 이상(실행 이미지 3.12), FastAPI 0.115+, Uvicorn, Pydantic Settings, psycopg 3·psycopg-pool |
| AI | Google Gen AI SDK(Gemini, 운영 기본 `gemini-3.6-flash`), 임베딩 `gemini-embedding-2`, LangGraph 1.x와 PostgreSQL 체크포인트 |
| 검색 | PostgreSQL FTS, pgvector |
| 문서 처리 | DOCX 직접 파싱, pypdf, 그림 OCR(검토용 근거), XLSX·DOCX 내보내기(표준 라이브러리 zip) |
| 인증·메일 | Microsoft Entra ID(MSAL, PKCE), 서버 세션 쿠키, Microsoft Graph 메일 읽기·발송, Web Push 알림 |
| 프론트 | React 19, TypeScript 5, Vite 7, TanStack Query 5, React Router 7, openapi-typescript로 API 타입 생성 |
| 데이터 | Cloud SQL PostgreSQL(로컬은 PostgreSQL 17 + pgvector 0.8.7), Cloud Storage |
| 인프라 | Cloud Run(서울 리전) 서비스 4개와 Cloud Run Jobs worker, Cloud Scheduler, Cloud Tasks, Secret Manager, Cloud Build |
| 개발 도구 | uv, Ruff, mypy, pytest, Vitest, Playwright, GitHub Actions |

</details>

## 성능

교육용 합성 데이터로 평가한 결과입니다. 지시문이나 규칙 조정에 사용하지 않은 시험 자료로 측정했습니다.

- **C/S**: 시험 597건에서 문의 유형 macro-F1 .967, 안전·현장 정지 긴급 문의 누락 0/26, 근거 문서 hit@5 .985
- **같은 고장 판단**: 해결 기록 104건에서 AI의 기존 고장 정답률은 81%, 다른 고장으로 잘못 합친 비율은 2.8%(임베딩 최근접 방식은 각각 65%, 46%)
- **RFP 요구 추출**: 조정에 쓰지 않은 문서 5건에서 재현율 71.1%

자세한 측정 조건과 결과는 [프로젝트 상세](docs/PROJECT_OVERVIEW.md#성능-평가)에 있습니다.

## 문서

- [프로젝트 상세](docs/PROJECT_OVERVIEW.md): 기능별 설명과 화면, 성능 표, 데이터 고지
- [개발 안내](docs/DEVELOPMENT.md): 로컬 실행, 저장소 구조, 개발 문서 목록, 협업 방식
- [아키텍처](docs/architecture.md) · [의사결정](docs/decisions.md) · [평가 명세](docs/evaluation.md) · [배포 안내](docs/service-delivery.md)

화면과 평가 결과에는 LS가 교육용으로 제공한 합성 데이터와 시연용 가상 인물·협력사를 사용했습니다. 실제 LS 제품·고객·업체와는 관계가 없습니다.
