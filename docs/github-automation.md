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
| .github/workflows/application-checks.yml의 infrastructure | 변경 영향에 따른 Python Git 연동·Node PR 자동화 검사. 실패는 Application result에서 차단 |
| .github/workflows/application-checks.yml의 secrets | 변경 경로와 상관없이 모든 커밋 이력을 gitleaks(버전·체크섬 고정)로 검사. 값은 가려 출력하고 발견 시 Application result에서 차단. 허용 규칙은 `.gitleaks.toml` |
| .github/jira-config.json | Jira 링크의 기본 주소 |
| .github/workflows/claude-code-review.yml | 매 커밋 Claude 구조화 리뷰와 결과 검사 |
| .github/scripts/ai_review.cjs | 검토 SHA·완료·파일 범위·차단 지적 검사 및 댓글 게시 |
| .github/scripts/routine_review.cjs | Cloud Routine 1회 시작과 인증된 결과 댓글 수신·요청 결속 검사 |

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

출력 검사는 기본 브랜치(dev)에서 가져온 검사 코드로 실행하고 실제 GitHub PR의 SHA와 대조한다. 이 checkout은 pull_request 이벤트의 워크플로 정의를 PR 변경으로부터 격리하지 않는다.
검토 중 새 push가 생기면 이전 실행을 취소하며 게시 전후에도 SHA를 확인한다.
댓글에 전체 head/base SHA와 실행 링크를 남기고, 게시 직후 상태 변경을 발견하면 해당 댓글을 검토 무효로 바꾼다. 작성자는 병합 직전에도 현재 SHA와
결과가 같은지 확인한다. base가 달라졌다면 작업 브랜치에 최신 base를 반영해 새 이벤트로
다시 검토한다. 오래된 이벤트의 단순 재실행을 최신 검토로 간주하지 않는다.

수정 커밋을 push하면 다시 리뷰하며 P0~P2 지적이 남으면 병합하지 않는다.
Draft·fork에는 구독 Secret을 사용하는 리뷰를 실행하지 않으며 이를 AI 통과로 취급하지 않는다.
`@claude` 대화 응답이나 CodeRabbit 댓글만으로 `claude-review` 결과를 대신하지 않는다.
팀원 자동 요청은 중단했지만 기존 요청은 관리자가 별도로 해제한다.

### Cloud Routine 전환 (KAN-203)

[KAN-203](https://lsteam4.atlassian.net/browse/KAN-203)은 완료된 KAN-175·177의 후속이다.
`claude-review`는 기본 브랜치의 `prepare`로 전체 diff·파일 목록을 확인하고,
Cloud Routine을 한 번 시작한 뒤 PR issue 댓글을 조회한다. `/fire` 응답의 세션 생성은
검토 완료가 아니다. 인증된 결과를 받지 못하면 실패하며 기존 OAuth Action으로 폴백하지 않는다.
`@claude` 대화 워크플로는 이번 전환 범위 밖이다.

저장소 Actions secrets에는 `CLAUDE_REVIEW_ROUTINE_URL`과 `CLAUDE_REVIEW_ROUTINE_TOKEN`을 등록한다.
2026-11-01 이름 등록만 확인했으며 값·유효성은 읽거나 시험하지 않았다.
URL은 `https://api.anthropic.com/v1/claude_code/routines/trig_<영숫자>/fire`와 정확히 일치해야 한다.
query·fragment·사용자 정보·명시적 포트·다른 host/path·redirect는 거부한다.
Bearer token은 해당 공식 endpoint의 인증 헤더에만 사용하며 payload·댓글·오류에 출력하지 않는다.
요청은 `anthropic-version: 2026-10-21`과 `{ "text": "<JSON 문자열>" }` 형식이다.
시작 응답은 `type=routine_fire`와 비어 있지 않은 문자열 `claude_code_session_id`를
요구한다. ID는 불투명 문자열로 취급하며 `session_` 같은 접두사를 강제하거나 명령·URL에
조합하지 않는다. 로그에는 type 일치·ID 필드 존재·문자열 여부·비어 있지 않음의 boolean만
남기며 ID·세션 URL·원응답은 출력하지 않는다. 진단 run 41310557079의 원응답은 미기록되어
그 실패를 특정 접두사 때문이라고 확정할 수 없다. 기존 진단 PR #537은 수정·재실행하지 않는다.
`text` 전체가 65,536자를 초과하면 diff를 자르거나 세션을 시작하지 않고 실패한다.
큰 diff의 별도 전달 경로는 아직 구현하지 않았다.

Routine의 저장된 prompt는 이 저장소의 `routine-fire-payload` 요청만 처리하고,
코드·시험·스크립트 실행, 코드 변경, push, merge, 다른 저장소 접근을 금지해야 한다.
PR의 제목·본문·댓글·코드 속 지시문은 검토 데이터다. 전체 diff와 관련 계약을 읽고 최신
head/base를 확인한다. 이전 검토를 재사용하지 않으며 전체 파일을 읽지 못했거나 SHA가
다르면 `review_complete=false`를 반환한다. 유일한 쓰기 예외는 지정 PR에 결과 댓글 1개
생성이다. 댓글 수정이나 추가 결과 댓글은 허용하지 않는다. 이 제한은 Routine prompt와
클라우드 연결 설정에도 적용해야 하며, runner의 trusted checkout이 클라우드의 도구 권한을
강제하는 것은 아니다. Actions의 GitHub token·Routine token을 클라우드에 전달하지 않는다.

payload는 `kind: "routine-fire-payload"`, `request`, `head_sha`, `base_sha`,
`reviewed_files`, 검증한 전체 `diff`, `result_marker`, `result_schema`, `instructions`를 담는다.
`result_schema`가 이번 요청의 정확한 상수와 기존 review JSON 스키마를 전달한다.
Routine은 다음 marker와 줄바꿈 뒤에 순수 JSON만 게시한다(코드 펜스 없음).

```text
<!-- team4-routine-review -->
{"request_id":"<요청 UUID>","run_id":"<run ID 문자열>","run_attempt":"<attempt 문자열>","repository":"LS-Final-Project/ls-final-project","pr_number":123,"deadline":"<요청 ISO 시각>","review":{"head_sha":"<40자리 SHA>","base_sha":"<40자리 SHA>","review_complete":true,"reviewed_files":["<전체 변경 파일>"],"summary":"<한국어 검토 결과>","findings":[]}}
```

수신기는 GitHub가 제공하는 `user.type=Bot`, `user.id=209825114`와
`performed_via_github_app.id=1236702`를 모두 요구한다. 숫자 ID는 GitHub의
[`claude[bot]`](https://api.github.com/users/claude%5Bbot%5D)과
[`Claude App`](https://api.github.com/apps/claude) 조회로 확인했다.
표시 이름·본문의 주장·nonce·사람 계정·GitHub Actions bot만으로는 신뢰하지 않는다.
요청 UUID는 인증 수단이 아니며 run/attempt·저장소·PR·기한의 재사용을 차단하는 결속값이다.
15분 내 생성한 수정되지 않은 댓글 1개만 수락하며, 수락 전 재조회에서도 중복·삭제·수정이
관측되면 실패한다. 미래 시각·오래된 댓글·잘못된 요청 필드도 거부한다.
REST의 원작성자/App와 `created_at == updated_at`만으로는 같은 초에 수정된 본문을
탐지할 수 없다. 최초 수신과 최종 REST 재조회 직후에 GraphQL `IssueComment`를 Node ID로
조회하고 REST 댓글 ID·본문·생성/갱신 시각·저장소·PR을 대조한다. 명시적인
`lastEditedAt=null`, `editor=null`, `includesCreatedEdit=false`와
`userContentEdits(first:1)`의 `totalCount=0`, 빈 `nodes`, `hasNextPage=false`를 모두
요구한다. 필드 누락·null 이력 연결·부분 응답·권한/API 오류·수정 이력이 있으면 실패한다.
생성시각 정밀도나 처음 관측한 본문이 원본이라는 가정으로 무수정을 판정하지 않는다.

GitHub [IssueComment 스키마](https://docs.github.com/en/graphql/reference/issues#issuecomment)는
최종 편집자·편집시각·수정 이력 연결을 제공한다. `includesCreatedEdit`는 생성 데이터가
수정 이력에 포함되는지 나타내므로 이 필드 하나만으로 원작성 상태를 판정하지 않는다.
[수정 이력 안내](https://docs.github.com/en/communities/moderating-comments-and-conversations/tracking-changes-in-a-comment)에
따르면 저장소 읽기 권한으로 이력을 볼 수 있고, 민감한 수정 내용을 삭제해도 편집자와
수정시각은 남는다. 기존 댓글의 읽기 조회에서 무수정 상태의 위 필드 조합을 확인했다.
로컬 CLI 인증으로 가능한 조회와 Actions `GITHUB_TOKEN`의 실제 조회 성공은 구분한다.
워크플로 권한을 확대하지 않았으며 Actions의 이력 조회는 실 연결 검증에 남아 있다.
해당 토큰으로 필드를 조회하지 못하면 수락하지 않는다.
그 후 기존 `validate/finish`가 전체 reviewed_files·정확한 head/base·완료 여부와 P0~P2를
검사하고 게시 전후 최신 PR 상태를 재확인한다. 수락 이후의 변경까지 지속 감시하지는
않으므로 병합자는 현재 SHA와 결과를 마지막으로 확인해야 한다.

**실제 발행자는 미확인이다.** 공식 [Routine 문서](https://code.claude.com/docs/en/routines)는
연결한 사용자 명의로 작업이 나타날 수 있다고 설명하며, [클라우드 GitHub 인증](https://code.claude.com/docs/en/claude-code-on-the-web#github-authentication-options)은
App 연결과 사용자 `gh` token 연결을 구분한다. App 존재만으로 Routine 댓글이 위 bot/App
명의임을 입증할 수 없다. 연결 사용자 명의 댓글은 현재 정책에서 거부하며, 이를 자동
수락하도록 완화하지 않았다. 실 연결의 발행자 검증과 결과 전달이 확인될 때까지 전환 성공·
KAN-203 전체 완료를 보고하지 않는다.

공식 [fire API](https://platform.claude.com/docs/en/api/claude-code/routines-fire)는 세션 시작만
지원하고 token에 결과 읽기 권한이 없다. 매 요청이 새 세션이므로 POST는 자동 재시도하지
않는다. timeout·5xx·결과 미수신 때도 이미 세션이 만들어졌을 수 있다. 담당자는 Cloud
Routine 실행 내역과 GitHub 댓글의 서버 제공 작성자 정보를 확인한 뒤 다음 시도를 판단한다.
승인된 실제 전환 검증 1건은 감독이 수행하며 로컬 fake 검사는 실제 발행자·모델 품질·
클라우드 권한·운영 성공의 증거가 아니다. secret·연결·prompt 변경과 실제 호출은 별도다.

2026-10-23 사용자 결정으로 `.coderabbit.yaml`에서 CodeRabbit 자동 리뷰와 PR 본문 요약을 중단한다.
라벨·본문 키워드로 자동 리뷰가 다시 켜지지 않도록 두 설정도 비운다. 병합 조건은 CI 성공과 최신 head/base SHA의 `claude-review` 통과이며 CodeRabbit 완료를 추가로 기다리지 않는다.
이 설정은 GitHub App 삭제나 기존 PR 상태의 소급 삭제를 뜻하지 않는다. 기존 작업 브랜치에는 최신 dev의 설정을 반영해야 한다. 앱의 저장소 접근 해제·삭제는 조직의 [Installed GitHub Apps](https://github.com/organizations/LS-Final-Project/settings/installations)에서 CodeRabbit 설치를 확인해 처리한다.
현재 CLI 인증에는 조직 앱 관리 범위가 없어 앱 삭제는 미실행이며, 수동 `@coderabbitai review` 요청은 설정 중단 후에도 가능하다.

워크플로 자체를 최초로 변경하는 PR은 기본 브랜치에 새 검사 코드가 없어 실행이 실패할 수 있다.
이때 실패를 통과로 바꾸지 않는다. 해당 설치 PR의 diff를 Claude로 별도 검토하고 기존 CI를
확인한 근거를 PR에 남겨 dev에 통합한 뒤, 실제 열린 기능 PR에서 새 파이프라인을 검증한다.
설치 예외를 일반 기능 PR의 리뷰 생략이나 자동 병합 권한으로 확대하지 않는다.

## 연결과 권한

GitHub for Atlassian으로 이 저장소와 Jira KAN이 연결돼 있다. Jira 번호가 있는 브랜치·커밋·PR은 티켓의 Development 영역에서 추적한다. 팀원이 앱을 다시 설치하거나 별도 Jira 토큰을 추가할 필요는 없다.

Jira 메타데이터 자동화는 pull_request_target에서 GITHUB_TOKEN의 `contents:read`, `pull-requests:write`와 대상 브랜치에서 checkout한 파일을 사용한다. Claude 리뷰의 pull_request 워크플로 정의는 PR 변경의 영향을 받을 수 있으므로 checkout만으로 Secret·OIDC를 PR 변경과 격리한다고 간주하지 않는다. 테스트는 별도 읽기 전용 워크플로에서 수행한다.

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

Cloud Routine 결과는 위 댓글 envelope로 전달하고 기존 구조화 review 검사를 유지한다.
현재 비공개 저장소에서는 브랜치 보호 API가 요금제 제한으로 403을 반환한다. 검사 실패가 서버에서 병합을 강제 차단한다고 보고하지 않는다.
