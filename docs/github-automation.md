# GitHub 협업 자동화

관련 작업: [KAN-60](https://lsteam4.atlassian.net/browse/KAN-60).

팀원이 작업 시작 시 Jira 번호를 한 번 지정하면 브랜치, 커밋과 PR에서 같은 번호를 사용한다. 기존 규칙을 유지하면서 반복 입력을 줄이는 선택이다. GitHub Actions는 협업 자동화와 서비스별 CI를 실행하고 Cloud Build는 앱 이미지 생성과 별도 CD를 맡는다.

## 처음 한 번 설정

자동화 변경이 dev에 반영된 뒤 최신 코드를 받고 저장소에서 실행한다. Python 3.11 이상과 Git이 필요하다.

```sh
python3 scripts/dev.py setup
```

이 설정은 현재 저장소 폴더에 유지되므로 컴퓨터나 터미널 재시작 후 다시 실행하지 않는다. 다른 컴퓨터나 새 폴더에 clone했거나 Python 실행 경로가 바뀐 경우 다시 실행한다. 새 Jira 작업에만 start를 사용하며 같은 작업을 이어갈 때는 기존 브랜치에서 계속한다.

Windows에서는 [공통 온보딩](team-start.md)의 WSL Ubuntu 환경에서 실행한다. 설치는 현재 저장소의 로컬 Git 설정만 변경한다. 기존 core.hooksPath나 prepare-commit-msg 훅이 있으면 덮어쓰지 않고 안내한다. Python 설치 위치가 바뀌면 설정 명령을 다시 실행한다. 다른 IDE나 AI 도구도 이 저장소의 Git 커밋을 사용하면 같은 훅이 적용된다.

## 매일 작업하는 순서

1. Jira에서 실제 티켓의 범위, 담당자, 선행 작업과 검토자를 확인한다.
2. `python3 scripts/dev.py start KAN-63 backend-skeleton`처럼 작업 브랜치를 만든다. 예시 번호 대신 자신의 실제 티켓을 사용한다.
3. 자신의 변경 파일만 stage하고 `git commit -m "feat: 실행 골격 추가"`처럼 내용을 작성한다. 훅이 KAN-63을 앞에 추가한다.
4. `git push -u origin HEAD` 후 GitHub에서 dev 대상 Draft PR을 만든다. 변경 이유와 실제 검증 결과는 직접 작성한다.
5. 검토할 준비가 되면 Ready for review를 누른다. 등록된 Jira 검토자에게 요청하며 누락 및 자기 검토 상황은 직접 확인한다.
6. 검토를 반영한 뒤 dev에 Squash merge로 병합하고 Jira의 완료 조건을 확인한다. 병합만으로 최종 완료가 되지는 않는다.

작업 시작 명령은 origin/dev를 가져와 새 브랜치를 만들며 원격에 쓰거나 PR을 자동 생성하지 않는다. 변경 중인 파일과 기존 같은 이름의 브랜치가 있으면 중단한다. 복구를 위해 파일을 지우거나 자동 stash하지 않는다.

## 자동화의 범위

| 구성 | 역할 |
| --- | --- |
| scripts/dev.py | 최초 설정, Jira 작업 브랜치 생성과 커밋 번호 처리 |
| .githooks/prepare-commit-msg | 로컬 Git 커밋 시 Python 도우미 호출 |
| .github/workflows/pr-metadata.yml | PR 제목과 링크 보완, 번호 불일치 검사와 검토자 요청 |
| .github/workflows/collaboration-checks.yml | Python Git 연동 테스트와 Node PR 자동화 테스트 |
| .github/jira-reviewers.json | 현재 WBS의 Jira 티켓별 검토자 배정 |

PR 제목에 번호가 없으면 추가하고 번호가 다르면 오류를 표시한다. 본문은 jira-links 시작 및 끝 표시 사이만 갱신하며 그 밖의 설명은 보존한다. 표시가 손상되거나 중복되면 임의로 본문을 지우지 않고 오류를 표시한다. 자동 수정과 검사를 같은 실행에서 수행하므로 자동 수정의 후속 edited 이벤트에 의존하지 않는다.

Git 훅은 일반 커밋의 제목을 다룬다. 본문의 선행 작업 번호는 자유롭게 참조할 수 있다. 이미 같은 번호가 있으면 추가하지 않고 다른 번호이면 확인을 요청한다. 여러 번호가 있는 브랜치는 해당 번호들을 모두 추가한다. Git이 생성하는 merge 및 squash 메시지, 재사용한 기존 커밋 메시지, rebase와 autosquash 메시지는 보존한다. 훅은 저장소 복제만으로 설치되지 않으며 각 팀원의 최초 설정이 필요하다.

번호 검사는 형식을 확인하며 Jira API에 접속해 티켓 존재나 담당 변경을 조회하지 않는다. 별도 Jira API 토큰은 사용하지 않는다.

## 검토 배정

배정표는 2026년 10월 21일 WBS의 신규 계획 118건을 공식 담당 기준으로 옮겼다.

| 공식 담당 | 기본 검토자 | GitHub 검토 계정 |
| --- | --- | --- |
| 김나영 | 김종하 | jongddugi |
| 오새결 | 김나영 | kny2383 |
| 김종하 | 오새결 | oseguri |

실제 인프라를 오새결이 수행해도 공식 담당 김나영의 작업이면 기존 검토자 김종하를 유지한다. WBS나 Jira 검토자 변경 시 배정표도 갱신한다. 새 티켓은 표에 추가하거나 PR에서 검토자를 직접 지정한다. 여러 티켓이면 각 티켓 검토자의 중복을 제거해 요청한다. 작성자 자신에게는 요청하지 않으며 다른 사람을 임의로 대신 지정하지 않는다.

Draft에는 검토를 요청하지 않는다. 기존 요청 및 기존 검토 이력을 확인해 같은 요청을 반복하지 않는다. 수정 후 다시 검토가 필요하면 GitHub에서 재검토를 요청한다. 이 자동화가 승인이나 리뷰 자체를 수행하지는 않는다.

## GitHub와 Jira 화면 설정

공식 앱 연결은 Codex의 Atlassian 플러그인 연결과 별개다. GitHub for Atlassian 설치 여부와 조직 연결을 확인해야 하며 Jira 사이트 관리자와 GitHub 조직 소유자 권한이 필요할 수 있다.

1. Jira의 앱 탐색에서 GitHub for Atlassian을 찾아 연결한다.
2. GitHub Cloud와 LS-Final-Project 조직을 선택한다.
3. 저장소 범위를 Only select repositories로 두고 ls-final-project를 선택한다.
4. Jira의 앱 관리에서 조직과 저장소 연결을 확인한다.
5. 번호가 있는 실제 브랜치와 PR을 사용해 Jira 티켓의 Development 영역에 표시되는지 확인한다. 동기화에는 시간이 걸릴 수 있다.

2026년 10월 21일 GitHub 저장소에서 Squash merge 허용과 squash_merge_commit_title=PR_TITLE을 확인했다. 제목 설정은 이번 작업에서 적용했으며 Squash 병합 시 Jira 번호가 있는 PR 제목을 최종 커밋 제목으로 사용한다. 다른 병합 방식과 자동 브랜치 삭제 설정은 변경하지 않았다. PR 자동화는 GITHUB_TOKEN의 contents:read와 pull-requests:write만 요청하며 별도 개인 토큰을 추가하지 않는다.

현재 비공개 저장소의 브랜치 보호 및 규칙 조회는 요금제 제한으로 거절됐다. 따라서 자동 검사 실패가 표시돼도 병합 강제 차단을 보장하지 않는다. 필수 승인 및 검사 통과를 강제하려면 조직의 GitHub Team 또는 교육 혜택 적용 여부를 먼저 확인한다. GitHub 유료 autolink 기능에 의존하지 않고 PR 본문에 실제 Jira 링크를 작성한다.

## 최초 적용과 검증

이 변경의 PR에서는 Collaboration checks로 자동화 테스트를 실행한다. PR 메타데이터 워크플로는 신뢰할 수 있는 PR 대상 브랜치의 코드만 실행하므로 최초 자동화 PR에는 아직 적용되지 않는다. 이 PR이 검토 후 대상 브랜치에 병합돼야 이후 PR에서 자동 입력과 검토 요청을 확인할 수 있다.

권한이 있는 워크플로는 PR 코드 대신 base SHA의 자동화 파일만 읽는다. PR 코드의 테스트는 별도 읽기 전용 워크플로에서 수행한다. 외부 배포나 Jira 최종 완료 상태를 변경하지 않는다.

로컬에서는 다음 명령으로 확인한다. 테스트는 임시 Git 저장소를 사용하고 실제 data 디렉터리나 클라우드에 접속하지 않는다.

```sh
python3 -m unittest discover -s scripts/tests -v
node .github/tests/pr_metadata.test.cjs
```

원격 적용 후에는 실제 번호가 있는 Draft PR의 제목과 링크, Draft 해제 시 검토자 요청, Jira 개발 이력 연결을 확인한다. 이 단계가 확인되기 전에는 로컬 테스트 통과를 전체 연동 완료로 보고하지 않는다.

## 근거

- [Atlassian 개발 작업의 Jira 번호 연결](https://support.atlassian.com/jira-software-cloud/docs/reference-issues-in-your-development-work/)
- [GitHub Cloud와 Jira 연결](https://support.atlassian.com/jira-cloud-administration/docs/integrate-with-github/)
- [Git 훅](https://git-scm.com/docs/githooks)
- [GitHub 토큰에 의해 발생한 이벤트의 실행 조건](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [GitHub 브랜치 보호](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches)

## 2026-10-22 dev 기본 브랜치 전환

기능 개발과 일반 PR의 기준은 dev, 배포 기준은 main이다. CI 및 Jira PR 자동화는 두 브랜치 모두 지원한다. 변경 범위는 각 브랜치의 마지막 성공 push CI와 비교하므로 dev의 성공이 main의 실패를 건너뛰게 하지 않는다. 기존 작업 브랜치는 자동 rebase하거나 삭제하지 않는다. 새 작업은 scripts/dev.py start를 사용한다.

같은 저장소의 dev → main 배포 PR과 main → dev 동기화 PR은 번호 없는 장기 브랜치를 허용한다. 이 경우 자동화는 제목과 본문을 보존하고 수동 검토자 지정과 merge commit 사용을 안내한다. 일반 작업 및 fork의 번호 없는 브랜치에는 이 예외를 적용하지 않는다. 검토자를 자동으로 새 요청하거나 Jira 상태를 변경하지 않는다.

저장소에서는 Squash와 Merge commit을 허용한다. 기능 작업은 dev에 Squash, 두 장기 브랜치 사이는 merge commit으로 병합한다. 기본 브랜치 변경은 요금제 제한이 있는 보호 규칙을 활성화하는 작업과 별개다. 보호 규칙에 의한 서버 강제 차단은 설정하지 않았다.
