# 초기 셋업 PR과 실행 순서

2026-10-21. 초기 셋업 9개 Jira 티켓을 연결한다. 모든 PR은 Draft로 게시하고 동료 검토 요청 및 병합은 하지 않았다. Draft는 검토 승인을 기다린다는 뜻이 아니라 이번 게시 시점의 구분이다. 현재 초기 셋업의 동료 검토는 사용자 결정으로 생략한다. 기술 조건을 충족하지 않은 티켓은 진행 중으로 유지한다.

| 범위 | PR | 남은 조건 |
| --- | --- | --- |
| KAN-60 협업 정책 | [PR 3](https://github.com/LS-Final-Project/ls-final-project/pull/3) | 초기 브랜치 검토 예외는 main 반영 후 적용. 실제 후속 PR에서 Jira 제목과 링크 보완 확인 |
| KAN-62 GCP 점검 | [PR 4](https://github.com/LS-Final-Project/ls-final-project/pull/4) | 모델 실제 호출 제약 및 서비스 할당량 확인 |
| KAN-65 DB 권한 | [PR 5](https://github.com/LS-Final-Project/ls-final-project/pull/5) | pgvector 및 FTS, 공유 스키마 정책과 업무 마이그레이션 |
| KAN-66, KAN-69 실행 기반 | [PR 6](https://github.com/LS-Final-Project/ls-final-project/pull/6) | 백업 실제 결과, 문서 작업 처리와 OIDC, DB 작업 상태 및 SSE 재접속 |
| KAN-63 API 골격 | [PR 7](https://github.com/LS-Final-Project/ls-final-project/pull/7) | main 병합 및 후속 기능 연결. 현재 health는 프로세스 상태만 확인 |
| KAN-67 빌드와 배포 준비 | [PR 8](https://github.com/LS-Final-Project/ls-final-project/pull/8) | 실행 계정과 소스 버킷 설정, 실제 원격 빌드 및 이미지 업로드 검증. 프론트 빌드는 후속 연결 |
| KAN-55, KAN-56 계약 | 현재 브랜치의 계약 PR | 상세 DTO와 업무 OpenAPI 및 계약 테스트 확장. 제안 항목은 확정과 구분 |

PR 3~7과 계약 PR의 기준은 main이다. PR 8은 PR 7의 `KAN-63-api-service-skeleton` 브랜치를 기준으로 만들었다. PR 7을 먼저 병합하고 PR 8의 base를 main으로 바꾼 뒤 검사를 확인한다. PR 8을 먼저 main으로 바꾸면 API 골격 변경이 중복으로 표시된다. 현재 협업 Actions는 main 대상 PR에서 실행되므로 PR 8은 이 기준 변경 후 원격 검사를 확인한다. Cloud Build의 앱 검사는 별도다.

현재 원본 작업 공간에는 다른 세션과 공유하던 코드 파일을 그대로 보존했다. 코드와 인프라 커밋은 `/tmp/team4-setup-prs/` 하위 별도 worktree에 있다. 따라서 원본 공간에서 보이는 미커밋 복사본을 미게시 작업으로 혼동하지 않는다. 해당 worktree를 삭제하기 전에는 원격 push와 PR 보존 및 빌드 실행 경로를 확인한다. 원본의 `.env`와 `data`는 커밋하거나 이동하지 않았다.

## 다음 사용자 실행

먼저 저장소 루트에서 빌드 기반 설정을 실행한다. `.env`를 source하지 않는다.

```bash
bash infra/setup_cloud_build.sh
```

에이전트가 실행 계정과 저장소 권한, 소스 버킷의 서울 위치와 비공개 설정을 읽기 전용으로 확인한 뒤 실제 빌드를 진행한다. 아직 main에 빌드 파일을 병합하지 않았다면 커밋된 PR 8 worktree에서 실행한다.

```bash
cd /tmp/team4-setup-prs/KAN-67-cloud-build-private-deployment
bash infra/submit_cloud_build.sh
```

사용자 실행 결과의 BUILD_ID로 원격 성공 및 이미지 digest를 확인한다. 비공개 배포 미리보기와 실제 배포는 [Cloud Build 및 배포 안내](../infra/cloud-build.md)를 따른다. 빌드와 배포 도우미의 작성 완료를 실제 클라우드 실행 완료로 기록하지 않는다.

검증: Docker에서 Ruff 및 포맷, mypy 15개 파일, 백엔드 14건과 OpenAPI 일치 검사 통과. DB Secret 도우미 4건 및 배포 계획 3건 통과. Git 연동 12건과 PR 자동화 12건 통과. 실제 소스 업로드 목록에서 `.env`, `data`, Git과 인프라 도우미 제외를 확인했다. Cloud Build SDK 파서로 단계 5개 및 이미지 태그 6개 구성을 확인했다. 샌드박스에서 멈춘 로컬 pytest는 종료했으며 백엔드 실행 결과의 기준은 통과한 Docker 검사다.
