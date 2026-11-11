# 개발 안내

로컬 실행, 저장소 구조, 개발 문서와 협업 방식을 모았습니다. 서비스 설명과 성능은 [프로젝트 상세](PROJECT_OVERVIEW.md)에 있습니다.

## 빠른 시작

로컬 실행에는 GCP 권한, Gemini 키, Microsoft 시크릿이 필요하지 않습니다. Docker, Python 3.11 이상, uv, Node.js 22.12 이상을 준비한 뒤 [팀원 개발 시작 안내](team-start.md)의 1~5단계를 따르면 로컬 DB, API 3개, 프론트가 뜨고 합성 시연 계정으로 화면을 볼 수 있습니다. 기본 모드는 모의 응답이며, 실제 모델 호출은 같은 문서의 "실제 모델 호출" 절을 참고하세요.

## 저장소 구조

```text
backend/
  api/        cs · tft · platform FastAPI 앱과 공통 앱 설정
  engine/     규칙, 문서 파싱, 검색, C/S·RFP 단계별 로직
  agent/      worker 실행, LangGraph 실행기, 처리 등록
  shared/     설정, DB, 인증 검사, AI 제공자, 로그 등 공통 코드
frontend/     통합 웹(React)과 정적 서버·중계(server.mjs)
contracts/    서비스별 API·데이터 계약 문서
docs/         아키텍처, 온보딩, 배포·운영, 평가 명세, OpenAPI
eval/         평가 실행기와 지표, 정책 표
infra/        마이그레이션, Cloud Build, 배포·설정 스크립트
scripts/      로컬 실행(workspace.py), 검사, 측정 실행 도구
tests/        서비스별·공통 테스트
```

## 개발 문서

| 확인할 내용 | 위치 |
| --- | --- |
| 처음 설치, 로컬 DB와 계정 선택, 프론트 실행, 문제 해결 | [팀원 개발 시작 안내](team-start.md) |
| 이름별 첫 작업, WBS 개발 순서와 협업 경계 | [노션 개발 온보딩](https://www.notion.so/401910042c3781ed9768f13ef422a4f8) |
| 서비스 구조와 선택 근거 | [아키텍처](architecture.md), [의사결정](decisions.md) |
| 서비스별 API 원본과 생성 명령, 충돌 방지 규칙 | [계약 수정 안내](../contracts/README.md) |
| 기획서의 출력·출처 정의와 평가 계산법 | [C/S 계약](../contracts/cs/tickets.md), [데이터 계약](data-contract.md), [평가 명세](evaluation.md) |
| 서비스별 CI, 이미지와 CD 및 공용 웹 준비 | [서비스별 배포 안내](service-delivery.md) |
| 로그와 실제 백업 확인 | [기본 운영 확인](operations-basics.md) |
| 배포 뒤 확인, 이전 리비전 되돌리기, 백업 복구 훈련 | [배포 확인 런북](release-runbook.md) |
| 배포 순서와 승인 판정 | [배포 승인 점검표](release-approval.md) |
| API 로컬 실행, 테스트, OpenAPI와 Docker 빌드 | [백엔드 실행 안내](../backend/README.md) |
| GCP DB, Secret 및 연결 검증 | [인프라 안내](../infra/README.md) |
| 프로젝트 목표, 현재 단계, 주요 링크 | [프로젝트 시작 안내](https://docs.google.com/document/d/1CI2gyXEogmcSIeAKceK9xsurv_ls8lJ3uOac45k6jqk/edit) |
| 담당 업무, 진행 상태, 완료 결과 | [Jira KAN 보드](https://lsteam4.atlassian.net/jira/software/projects/KAN/boards/1) |
| 문서·회의·검토·인수인계 운영 | [협업 운영 기준](https://docs.google.com/document/d/10xunkXSy_73YSvlRKBRzNNeRAIk23FMc4XmpT6ywv2Q/edit) |
| 기획서와 WBS 원본 | [Google Drive docs 폴더](https://drive.google.com/drive/folders/1qGourXRhyvCMKRd6wZtOylAx4iuQKSeS) |
| 작업 브랜치와 PR 작성 방법 | [기여 안내](../CONTRIBUTING.md) |

## 협업 방식

작업은 Jira에서 시작하고 KAN 번호를 브랜치·커밋·PR에 넣습니다. 기획·조사 문서의 원본은 Google Docs·Drive에 두고 이 저장소에는 링크만 둡니다.

## 팀과 역할

| 담당 | 맡은 범위 |
| --- | --- |
| 김나영 | 메일, 사용자 등록·업무 권한, C/S API와 결정론적 규칙, 통합 회귀, RFP 데이터 구조·조항 추출·자사 비교·판정 |
| 오새결 | Microsoft 인증·서버 세션, 인프라와 배포, 입력 어댑터·검색, C/S·RFP의 AI 단계, 가상 협력사 후보·등급 규칙, RFP 대응표·초안·변경 제안, 평가 관리 API·화면·지표 |
| 김종하 | 프론트(평가·RFP 대응표 제외), RFP 조항 정리와 통합 시험 |

최신 배정과 수정 경계는 [AGENTS.md](../AGENTS.md#현재-담당과-수정-경계)와 Jira를 따릅니다.
