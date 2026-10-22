# GitHub 협업 자동화

관련 작업: [KAN-60](https://lsteam4.atlassian.net/browse/KAN-60).

팀원이 작업 시작 시 Jira 번호를 한 번 지정하면 브랜치, 커밋과 PR에서 같은 번호를 사용한다. 기존 규칙을 유지하면서 반복 입력을 줄이는 선택이다. GitHub Actions는 협업 자동화와 서비스별 CI를 실행하고 Cloud Build는 앱 이미지 생성과 별도 CD를 맡는다.

## 처음 한 번 설정

최신 dev를 받은 저장소에서 실행한다. Python 3.11 이상과 Git이 필요하다.

```sh
python3 scripts/dev.py setup
```

이 설정은 현재 저장소 폴더에 유지되므로 컴퓨터나 터미널 재시작 후 다시 실행하지 않는다. 다른 컴퓨터나 새 폴더에 clone했거나 Python 실행 경로가 바뀐 경우 다시 실행한다. 새 Jira 작업에만 start를 사용하며 같은 작업을 이어갈 때는 기존 브랜치에서 계속한다.

Windows에서는 [공통 온보딩](team-start.md)의 WSL Ubuntu 환경에서 실행한다. 설치는 현재 저장소의 로컬 Git 설정만 변경한다. 기존 core.hooksPath나 prepare-commit-msg 훅이 있으면 덮어쓰지 않고 안내한다. Python 설치 위치가 바뀌면 설정 명령을 다시 실행한다. 다른 IDE나 AI 도구도 이 저장소의 Git 커밋을 사용하면 같은 훅이 적용된다.

## 매일 작업하는 순서

1. Jira에서 실제 티켓의 범위, 담당자, 선행 작업과 검토자를 확인한다.
2. `python3 scripts/dev.py start KAN-64 frontend-routes`처럼 작업 브랜치를 만든다. 예시 번호 대신 자신의 실제 티켓을 사용한다.
3. 자신의 변경 파일만 stage하고 `git commit -m "feat: 화면 라우팅 추가"`처럼 내용을 작성한다. 훅이 KAN-64를 앞에 추가한다.
4. `git push -u origin HEAD` 후 GitHub에서 dev 대상 Draft PR을 만든다. 변경 이유와 실제 검증 결과는 직접 작성한다.
5. 검토할 준비가 되면 Ready for review를 누른다. 등록된 Jira 검토자에게 요청하며 누락 및 자기 검토 상황은 직접 확인한다.
6. 검토를 반영한 뒤 dev에 Squash merge로 병합하고 Jira의 완료 조건을 확인한다. 티켓 전체를 끝낸 최종 PR에만 아래 완료 표시를 사용한다.

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

티켓별 검토자는 `.github/jira-reviewers.json`과 Jira의 배정을 따른다. 별도 배정이 없는 새 작업의 기본 순서는 다음과 같다.

| 공식 담당 | 기본 검토자 | GitHub 검토 계정 |
| --- | --- | --- |
| 김나영 | 김종하 | jongddugi |
| 오새결 | 김나영 | kny2383 |
| 김종하 | 오새결 | oseguri |

KAN-77·80·81의 담당자는 오새결이며 티켓에 지정된 검토자 김종하를 유지한다. WBS나 Jira 검토자 변경 시 배정표도 갱신한다. 새 티켓은 표에 추가하거나 PR에서 검토자를 직접 지정한다. 여러 티켓이면 각 티켓 검토자의 중복을 제거해 요청한다. 작성자 자신에게는 요청하지 않으며 다른 사람을 임의로 대신 지정하지 않는다.

Draft에는 검토를 요청하지 않는다. 기존 요청 및 기존 검토 이력을 확인해 같은 요청을 반복하지 않는다. 수정 후 다시 검토가 필요하면 GitHub에서 재검토를 요청한다. 이 자동화가 승인이나 리뷰 자체를 수행하지는 않는다.

## 연결과 권한

GitHub for Atlassian으로 이 저장소와 Jira KAN이 연결돼 있다. Jira 번호가 있는 브랜치·커밋·PR은 티켓의 Development 영역에서 추적한다. 팀원이 앱을 다시 설치하거나 별도 Jira 토큰을 추가할 필요는 없다.

PR 자동화는 GITHUB_TOKEN의 `contents:read`, `pull-requests:write`를 사용하고 대상 브랜치의 신뢰할 수 있는 코드만 실행한다. PR의 임의 코드를 권한 있는 워크플로에서 실행하지 않는다. 테스트는 별도 읽기 전용 워크플로에서 수행한다.

브랜치 보호의 서버 강제는 현재 적용되지 않는다. 병합자는 CI 성공과 검토자 승인을 직접 확인한다. 기능 PR은 dev에 Squash merge, dev → main 배포와 main → dev 동기화는 merge commit이다. 장기 브랜치 간 PR은 검토자를 직접 지정하고 Jira 완료 표시를 넣지 않는다.

자동화 자체를 수정할 때는 다음 검사를 실제 실행한다. 임시 Git 저장소를 사용하며 클라우드나 data에 접속하지 않는다.

```bash
python3 -m unittest discover -s scripts/tests -v
node .github/tests/pr_metadata.test.cjs
```

## Jira 자동 완료

KAN의 `KAN 최종 PR dev 병합 시 자동 완료` 규칙이 활성화돼 있다. 티켓 전체 완료 조건을 만족한 PR만 다음 표시를 사용하고 병합 후 실제 Jira 상태를 확인한다.

| PR 구분 | 제목 예시 | dev 병합 후 Jira |
| --- | --- | --- |
| 중간 결과 | `[KAN-64] 라우팅 초안` | 상태 유지 |
| 티켓 전체 완료 | `[KAN-64] [완료 KAN-64] 화면 라우팅 완료` | KAN-64 완료 전환 |
| dev/main 통합 | `배포: 화면 라우팅` | 상태 유지 |

완료 표시는 제목에 하나만 넣는다. 참고 티켓은 본문에 링크한다. 완료 조건 충족 여부와 CI 및 동료 승인은 병합 전에 사람이 확인한다. GitHub 요금제상 강제 차단과 이 Jira 규칙의 자동 품질 검사는 제공되지 않는다. PR 메타데이터 워크플로가 완료 표시를 자동으로 붙이지는 않는다.

트리거는 Pull request merged다. 다음 조건을 모두 만족한 현재 티켓만 완료로 전환한다.

- `{{pullRequest.destinationBranch}}` = `dev`
- `{{pullRequest.title.match("\[완료 (KAN-[0-9]+)\]")}}` = `{{issue.key}}`
- `{{pullRequest.url.substringBefore("/pull/")}}` = `https://github.com/LS-Final-Project/ls-final-project`
- `{{pullRequest.sourceBranch.match("^(KAN-[0-9]+)-")}}` = `{{issue.key}}`
- JQL: `project = KAN AND statusCategory != Done`

dev → main 배포와 main → dev 동기화는 소스 브랜치 조건에서 제외된다. 완료된 티켓을 다시 열거나 담당자와 일정을 바꾸지 않는다. Jira 설정은 [프로젝트 자동화](https://lsteam4.atlassian.net/jira/software/projects/KAN/settings/automation)에서 확인한다. 전환이 누락되면 해당 규칙의 Audit log에서 조건과 권한 오류를 확인하며 성공으로 가정하지 않는다.

스마트 값은 [Atlassian 개발 정보](https://support.atlassian.com/cloud-automation/docs/jira-smart-values-development/) 및 [문자열 함수](https://support.atlassian.com/cloud-automation/docs/jira-smart-values-text-fields/)를 따른다. 배포 방법은 [서비스별 CI와 CD](service-delivery.md#오새결의-평소-배포-dev에서-main으로)를 참고한다.
