# Team4 업무지원 AI

해외 C/S 문의 대응과 RFP 기반 TFT 구성을 지원하는 4조 프로젝트입니다.

## 시작하기

팀원은 **[공통 개발 온보딩](docs/team-start.md)** 하나에서 설치, 로컬 실행, 자기 작업 시작과 첫 PR까지 진행한다. 로컬 실행에는 클라우드 키가 필요하지 않다. 아래 문서는 해당 작업을 구현할 때 참고하는 상세 자료다.

| 확인할 내용 | 위치 |
| --- | --- |
| 처음 설치, 로컬 DB와 계정 선택, 프론트 실행, 문제 해결 | [팀원 개발 시작 안내](docs/team-start.md) |
| 이름별 첫 작업, WBS 개발 순서와 협업 경계 | [노션 개발 온보딩](https://www.notion.so/401910042c3781ed9768f13ef422a4f8) |
| 서비스별 API 원본과 생성 명령, 충돌 방지 규칙 | [계약 수정 안내](contracts/README.md) |
| 서비스별 CI, 이미지와 CD 및 공용 웹 준비 | [서비스별 배포 안내](docs/service-delivery.md) |
| 로그와 실제 백업 확인 | [기본 운영 확인](docs/operations-basics.md) |
| API 로컬 실행, 테스트, OpenAPI와 Docker 빌드 | [백엔드 실행 안내](backend/README.md) |
| GCP DB, Secret 및 연결 검증 | [인프라 안내](infra/README.md) |
| 프로젝트 목표, 현재 단계, 주요 링크 | [프로젝트 시작 안내](https://docs.google.com/document/d/1CI2gyXEogmcSIeAKceK9xsurv_ls8lJ3uOac45k6jqk/edit) |
| 담당 업무, 진행 상태, 완료 결과 | [Jira KAN 보드](https://lsteam4.atlassian.net/jira/software/projects/KAN/boards/1) |
| 문서·회의·검토·인수인계 운영 | [협업 운영 기준](https://docs.google.com/document/d/10xunkXSy_73YSvlRKBRzNNeRAIk23FMc4XmpT6ywv2Q/edit) |
| 기획서와 WBS 원본 | [Google Drive docs 폴더](https://drive.google.com/drive/folders/1qGourXRhyvCMKRd6wZtOylAx4iuQKSeS) |
| 작업 브랜치와 PR 작성 방법 | [기여 안내](CONTRIBUTING.md) |

개발 및 통합 배포 목표일은 **2026년 11월 3일**, 최종 발표일은 **11월 10일**입니다.
개별 담당·검토자와 세부 일정은 WBS 및 Jira의 최신 배정을 따릅니다.

## 협업 도구

- Google Docs·Drive에서 기획과 조사 문서의 최신 원본을 관리합니다.
- Jira에서 작업·일정·선행 관계·완료 조건을 관리합니다.
- 이 레포에서 개발 변경과 PR 검토를 관리합니다. 문서 원본은 복제하지 않고 링크합니다.
- Discord **오샤오치**에서 소통하고, GitHub 알림은 조직의 기존 **#github-log** 채널을 사용합니다.

작업은 Jira에서 시작하고, 관련 번호를 브랜치·커밋·PR에 넣습니다.
PR에서 동료가 결과를 검토한 뒤 Jira의 완료 조건을 확인합니다.

## 자료 사용 기준

공식 제품 문서, 기존 합성 데이터, 새 시연 가정을 구분합니다.
가격과 납기에는 적용 정책·가정·미확정 조건을 표시합니다.
개발·회귀 사례와 최종 평가 자료를 구분합니다.
