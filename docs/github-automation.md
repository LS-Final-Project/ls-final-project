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

1. Jira에서 실제 티켓의 범위, 담당자, 선행 작업을 확인한다.
2. `python3 scripts/dev.py start KAN-64 frontend-routes`처럼 작업 브랜치를 만든다. 예시 번호 대신 자신의 실제 티켓을 사용한다.
3. 자신의 변경 파일만 stage하고 `git commit -m "feat: 화면 라우팅 추가"`처럼 내용을 작성한다. 훅이 KAN-64를 앞에 추가한다.
4. `git push -u origin HEAD` 후 GitHub에서 dev 대상 Draft PR을 만든다. 변경 이유와 실제 검증 결과는 직접 작성한다.
5. 검토할 준비가 되면 Ready for review를 누른다. 팀원 자동 지정 없이 최신 head/base SHA의 AI 결과를 확인한다.
6. 검토를 반영한 뒤 dev에 Squash merge로 병합하고 Jira의 완료 조건을 확인한다. 티켓 전체를 끝낸 최종 PR에만 아래 완료 표시를 사용한다.

작업 시작 명령은 origin/dev를 가져와 새 브랜치를 만들며 원격에 쓰거나 PR을 자동 생성하지 않는다. 변경 중인 파일과 기존 같은 이름의 브랜치가 있으면 중단한다. 복구를 위해 파일을 지우거나 자동 stash하지 않는다.

## 자동화의 범위

| 구성 | 역할 |
| --- | --- |
| scripts/dev.py | 최초 설정, Jira 작업 브랜치 생성과 커밋 번호 처리 |
| .githooks/prepare-commit-msg | 로컬 Git 커밋 시 Python 도우미 호출 |
| .github/workflows/pr-metadata.yml | PR 제목과 링크 보완, 번호 불일치 검사(팀원 배정 없음) |
| .github/workflows/collaboration-checks.yml | Python Git 연동 테스트와 Node PR 자동화 테스트 |
| .github/jira-config.json | Jira 링크의 기본 주소 |
| .github/workflows/claude-code-review.yml | 매 커밋 Claude 구조화 리뷰와 결과 검사 |
| .github/scripts/ai_review.cjs | 검토 SHA·완료·파일 범위·차단 지적 검사 및 댓글 게시 |

PR 제목에 번호가 없으면 추가하고 번호가 다르면 오류를 표시한다. 본문은 jira-links 시작 및 끝 표시 사이만 갱신하며 그 밖의 설명은 보존한다. 표시가 손상되거나 중복되면 임의로 본문을 지우지 않고 오류를 표시한다. 자동 수정과 검사를 같은 실행에서 수행하므로 자동 수정의 후속 edited 이벤트에 의존하지 않는다.

Git 훅은 일반 커밋의 제목을 다룬다. 본문의 선행 작업 번호는 자유롭게 참조할 수 있다. 이미 같은 번호가 있으면 추가하지 않고 다른 번호이면 확인을 요청한다. 여러 번호가 있는 브랜치는 해당 번호들을 모두 추가한다. Git이 생성하는 merge 및 squash 메시지, 재사용한 기존 커밋 메시지, rebase와 autosquash 메시지는 보존한다. 훅은 저장소 복제만으로 설치되지 않으며 각 팀원의 최초 설정이 필요하다.

번호 검사는 형식을 확인하며 Jira API에 접속해 티켓 존재나 담당 변경을 조회하지 않는다. 별도 Jira API 토큰은 사용하지 않는다.

## AI 코드 리뷰

2026-10-22 사용자 결정으로 팀원 리뷰어 자동 지정과 동료 승인 조건을 제거했다.
Jira/WBS의 담당자·일정·의존성과 과거 검토자 기록은 임의로 변경하지 않는다.
코드 리뷰 정책만 바꾸며 제품의 메일 발송·최종 업무 승인과 사용자의 main 배포 판단은 유지한다.

Ready PR의 생성·새 커밋·Draft 해제·재오픈 때 Claude가 전체 diff와 계약을 검토한다.
기존 Claude 댓글이 있어도 건너뛰지 않고 `head_sha`, `base_sha`, `review_complete`,
`reviewed_files`, `summary`, `findings`를 구조화 출력한다. P0~P2는 수정 전 병합 차단,
P3는 참고 사항이다. 실행 오류·빈 결과·미완료·SHA 불일치·파일 누락도 검사 실패다.
모델 판단의 정확성을 보장하는 장치는 아니므로 CI·업무 완료 조건과 함께 확인한다.

출력 검사는 신뢰한 기본 브랜치(dev) 코드로 실행하고 실제 GitHub PR의 SHA와 대조한다.
검토 중 새 push가 생기면 이전 실행을 취소하며 게시 전후에도 SHA를 확인한다.
댓글에 전체 head/base SHA와 실행 링크를 남긴다. 작성자는 병합 직전에도 현재 SHA와
결과가 같은지 확인한다. base가 달라졌다면 작업 브랜치에 최신 base를 반영해 새 이벤트로
다시 검토한다. 오래된 이벤트의 단순 재실행을 최신 검토로 간주하지 않는다.

수정 커밋을 push하면 다시 리뷰하며 P0~P2 지적이 남으면 병합하지 않는다.
Draft·fork에는 구독 Secret을 사용하는 리뷰를 실행하지 않으며 이를 AI 통과로 취급하지 않는다.
`@claude` 대화 응답이나 CodeRabbit 댓글만으로 `claude-review` 결과를 대신하지 않는다.
팀원 자동 요청은 중단했지만 기존 요청은 관리자가 별도로 해제한다.

워크플로 자체를 최초로 변경하는 PR은 기본 브랜치에 새 검사 코드가 없어 실행이 실패할 수 있다.
이때 실패를 통과로 바꾸지 않는다. 해당 설치 PR의 diff를 Claude로 별도 검토하고 기존 CI를
확인한 근거를 PR에 남겨 dev에 통합한 뒤, 실제 열린 기능 PR에서 새 파이프라인을 검증한다.
설치 예외를 일반 기능 PR의 리뷰 생략이나 자동 병합 권한으로 확대하지 않는다.

## 연결과 권한

GitHub for Atlassian으로 이 저장소와 Jira KAN이 연결돼 있다. Jira 번호가 있는 브랜치·커밋·PR은 티켓의 Development 영역에서 추적한다. 팀원이 앱을 다시 설치하거나 별도 Jira 토큰을 추가할 필요는 없다.

PR 자동화는 GITHUB_TOKEN의 `contents:read`, `pull-requests:write`를 사용하고 대상 브랜치의 신뢰할 수 있는 코드만 실행한다. PR의 임의 코드를 권한 있는 워크플로에서 실행하지 않는다. 테스트는 별도 읽기 전용 워크플로에서 수행한다.

브랜치 보호의 서버 강제는 현재 적용되지 않는다. 병합자는 CI 성공과 최신 SHA AI 리뷰 완료·P0~P2 지적 해소를 직접 확인한다. 기능 PR은 dev에 Squash merge, dev → main 배포와 main → dev 동기화는 merge commit이다. 장기 브랜치 간 PR도 AI 리뷰를 확인하고 Jira 완료 표시를 넣지 않는다.

자동화 자체를 수정할 때는 다음 검사를 실제 실행한다. 임시 Git 저장소를 사용하며 클라우드나 data에 접속하지 않는다.

```bash
python3 -m unittest discover -s scripts/tests -v
node --test .github/tests/*.test.cjs
```

## Jira 자동 완료

KAN의 `KAN 최종 PR dev 병합 시 자동 완료` 규칙이 활성화돼 있다. 티켓 전체 완료 조건을 만족한 PR만 다음 표시를 사용하고 병합 후 실제 Jira 상태를 확인한다.

| PR 구분 | 제목 예시 | dev 병합 후 Jira |
| --- | --- | --- |
| 중간 결과 | `[KAN-64] 라우팅 초안` | 상태 유지 |
| 티켓 전체 완료 | `[KAN-64] [완료 KAN-64] 화면 라우팅 완료` | KAN-64 완료 전환 |
| dev/main 통합 | `배포: 화면 라우팅` | 상태 유지 |

완료 표시는 제목에 하나만 넣는다. 참고 티켓은 본문에 링크한다. 완료 조건 충족 여부와 CI 및 최신 SHA AI 리뷰 통과는 병합 전에 작성자가 확인한다. GitHub 요금제상 강제 차단과 이 Jira 규칙의 자동 품질 검사는 제공되지 않는다. PR 메타데이터 워크플로가 완료 표시를 자동으로 붙이지는 않는다.

트리거는 Pull request merged다. 다음 조건을 모두 만족한 현재 티켓만 완료로 전환한다.

- `{{pullRequest.destinationBranch}}` = `dev`
- `{{pullRequest.title.match("\[완료 (KAN-[0-9]+)\]")}}` = `{{issue.key}}`
- `{{pullRequest.url.substringBefore("/pull/")}}` = `https://github.com/LS-Final-Project/ls-final-project`
- `{{pullRequest.sourceBranch.match("^(KAN-[0-9]+)-")}}` = `{{issue.key}}`
- JQL: `project = KAN AND statusCategory != Done`

dev → main 배포와 main → dev 동기화는 소스 브랜치 조건에서 제외된다. 완료된 티켓을 다시 열거나 담당자와 일정을 바꾸지 않는다. Jira 설정은 [프로젝트 자동화](https://lsteam4.atlassian.net/jira/software/projects/KAN/settings/automation)에서 확인한다. 전환이 누락되면 해당 규칙의 Audit log에서 조건과 권한 오류를 확인하며 성공으로 가정하지 않는다.

스마트 값은 [Atlassian 개발 정보](https://support.atlassian.com/cloud-automation/docs/jira-smart-values-development/) 및 [문자열 함수](https://support.atlassian.com/cloud-automation/docs/jira-smart-values-text-fields/)를 따른다. 배포 방법은 [서비스별 CI와 CD](service-delivery.md#오새결의-평소-배포-dev에서-main으로)를 참고한다.

Claude의 구조화 출력은 [공식 Action 인터페이스](https://github.com/anthropics/claude-code-action/blob/6b6870e649b5ff408753befeaee5fc7989d4e6a9/action.yml)를 사용한다. 현재 비공개 저장소에서는 브랜치 보호 API가 요금제 제한으로 403을 반환한다. 검사 실패가 서버에서 병합을 강제 차단한다고 보고하지 않는다.
