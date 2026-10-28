# 플랫폼 메일 수집 계약 — KAN-71

사서함 하나의 받은편지함·보낸편지함을 Microsoft Graph에서 받아 메시지·첨부의 원문을 보관하는 서버 내부 함수다. HTTP API·정기 실행·운영 활성화는 아직 없다. 자동 검사는 가짜 Graph(httpx MockTransport)와 합성 토큰 함수로 하고, 실제 Outlook 사서함 수집은 [실제 사서함 확인](#실제-사서함-확인)에 따로 기록했다.

## 저장과 소유권

`infra/migrations/platform/20261028_KAN-71_mail_sync.sql`을 관리자 연결로 적용한다. KAN-81의 `20261027_KAN-81_mailbox_vault.sql`이 먼저 적용돼야 한다.

```bash
python3 scripts/workspace.py migrate platform 20261028_KAN-71_mail_sync.sql
```

사서함 연결은 KAN-81의 `mailbox_auth.connections`가 원본이다. 수집은 연결의 `owner_user_id`·`state`만 읽고 토큰·캐시 열은 읽지 않는다. 아래 표는 연결 ID를 `mailbox_id`로 참조하므로 연결 행을 지우지 않고 `state='disconnected'`로 남겨야 한다.

| 테이블 | 저장 단위와 제약 |
| --- | --- |
| `integration.mail_sync_runs` | 수집 실행 한 번. 사서함마다 `RUNNING`은 하나(부분 유일 인덱스). lease 만료 시각, 종료 시각, 고정 오류 코드, 저장·제거 건수 |
| `integration.mail_sync_cursors` | 사서함·폴더(`inbox`, `sentitems`)별 Graph delta 위치. `next_link`는 진행 중 페이지, `delta_link`는 마지막 완료 지점 |
| `integration.mail_messages` | 메시지. 사서함 안에서 `graph_id`(ImmutableId) 유일. 수신 시각 필수, 원문 MIME의 저장 키·SHA-256·크기, 처음 받은 실행, 삭제·이동 표시(`removed_at`) |
| `integration.mail_attachments` | 첨부. 메시지 안에서 Graph 첨부 ID 유일. 파일·항목 첨부는 저장 키·SHA-256·크기 필수, 링크형(reference)은 바이트가 없어 비움 |

`platform_app`은 네 표에 SELECT·INSERT·UPDATE만 가능하다. 메일 삭제도 행을 지우지 않는다. C/S·TFT 계정에는 권한이 없다.

원문·첨부 바이트는 DB가 아니라 객체 저장소에 둔다(`backend/api/platform/mail/storage.py`). 키는 사서함 연결 ID와 내용 SHA-256(`mail/<mailbox_id>/sha256/<앞 2자>/<hash>`)이라 같은 사서함 안에서 같은 내용은 한 번만 쓴다. 사서함마다 키를 나눠 한 사서함의 객체를 지우거나 접근을 좁혀도 다른 사서함 객체에 영향이 없다.

| 저장소 | 쓰는 곳 | 동작 |
| --- | --- | --- |
| `LocalObjectStore(root)` | 로컬 개발·검사 | 임시 파일에 쓴 뒤 이름을 바꿔 부분 쓰기를 남기지 않는다 |
| `GcsObjectStore(client, bucket, token)` | 운영(KAN-72) | 버킷은 `MAIL_OBJECT_BUCKET`(`infra/setup_mail_bucket.sh`). `ifGenerationMatch=0`으로 객체가 없을 때만 만들고, 보낸 바이트의 MD5(`x-goog-hash`)를 Cloud Storage가 확인한다. 이미 있다는 응답(412)이나 덮어쓰기 거부(403)는 같은 키의 객체 크기·MD5가 같을 때만 성공으로 본다 |

- 운영 실행 계정 `platform-runtime`은 이 버킷에 객체 생성·읽기만 할 수 있다. 삭제·덮어쓰기 권한이 없어 보관한 원문은 수집 코드로 바뀌지 않는다.
- `GoogleAccessToken`은 실행 환경 기본 자격 증명(`devstorage.read_write` 범위)으로 access token을 받고 만료되면 갱신한다. 토큰은 요청 헤더에만 쓴다.
- 저장소 장애·거부·내용 불일치는 `MAIL_STORAGE_UNAVAILABLE`(503)로 실행에 기록한다. 버킷·키·Cloud Storage 응답 본문은 오류에 넣지 않는다.

## 호출

```python
from backend.api.platform.mail.sync import sync_mailbox

result = await sync_mailbox(
    settings, mailbox_id, get_token=provider.get_mailbox_access_token, client=client, store=store
)
```

- `get_token`: KAN-81 `MailboxTokenProvider.get_mailbox_access_token`과 같은 호출 형태(`*, mailbox_id, owner_user_id, force_refresh=False` → `SecretStr`). `force_refresh=True`면 보관된 토큰을 쓰지 않고 새로 받는다. 수집 코드는 OAuth·refresh를 하지 않고, Graph 401 뒤에만 `force_refresh=True`로 부른다. 두 형태가 맞는지는 mypy가 검사한다.
- `owner_user_id`는 호출자가 아니라 저장된 연결에서 읽는다.
- `client`: 제한 시간을 둔 `httpx.AsyncClient`. 리디렉션은 따르지 않는다.
- 반환값 `SyncResult`: 실행 ID, `SUCCEEDED`/`FAILED`, 고정 오류 코드, 새로 저장한 메시지 수, 제거 표시 수.

## 처리 순서

1. 연결 행을 잠그고 상태를 확인한다. 만료된 `RUNNING` 실행은 `LEASE_EXPIRED`로 끝내고 새 실행을 만든다.
2. 저장된 소유자로 토큰 함수를 부른다. 빈 토큰은 실패로 본다.
3. 폴더마다 저장된 `next_link` → `delta_link` → 처음 주소 순으로 delta 페이지를 받는다. 모든 요청에 `Prefer: IdType="ImmutableId"`를 보낸다.
4. 새 메시지만 원문 MIME(`$value`)과 첨부(목록 → 각 `$value`)를 받아 저장소에 먼저 쓴다. 이미 보관한 메시지는 다시 받지 않고 폴더·제거 표시만 고친다.
5. 한 트랜잭션에서 연결 행을 공유 잠금(FOR SHARE)하고 실행 소유와 연결 상태를 다시 확인한 뒤, 메시지·첨부·커서·건수를 함께 기록한다. 중간에 멈추면 다음 실행이 마지막으로 기록한 페이지부터 이어 받는다.
6. delta의 `@removed`는 같은 폴더에 있는 행에만 `removed_at`을 기록한다. 다른 폴더로 옮겨 간 메시지는 그 폴더의 delta가 다시 알려 준다.

## 실패

| 상황 | 결과 | 실행 기록 |
| --- | --- | --- |
| 알 수 없는 연결 | `ApplicationError` 403 `MAILBOX_ACCESS_DENIED` | 만들지 않음 |
| 연결 해제 상태 | 409 `MAILBOX_NOT_CONNECTED` | 만들지 않음 |
| 같은 사서함 실행 중 | 409 `MAIL_SYNC_IN_PROGRESS` | 만들지 않음 |
| 토큰 함수 실패 | `FAILED`, 토큰 함수의 코드(`MAILBOX_RECONNECT_REQUIRED` 등) | 기록 |
| Graph 401 | 실행당 한 번만 `force_refresh=True`로 새 토큰을 받아 같은 요청을 다시 보냄. 다시 401이거나 이미 기회를 썼으면 `FAILED`, `GRAPH_UNAUTHORIZED` | 기록, 커서 유지 |
| 401 뒤 토큰 갱신 실패 | `FAILED`, 토큰 함수의 코드(`MAILBOX_RECONNECT_REQUIRED` 등). Graph를 다시 부르지 않음 | 기록, 커서 유지 |
| Graph 403/429/5xx | `FAILED`, `GRAPH_FORBIDDEN`/`GRAPH_THROTTLED`/`GRAPH_UNAVAILABLE`. 토큰 갱신·재시도 없음 | 기록, 커서 유지 |
| Graph가 동기화 상태를 버림(410, `syncStateNotFound`) | 폴더를 처음부터 한 번 다시 받음. 이미 보관한 원문은 다시 받지 않음 | 두 번째면 `GRAPH_SYNC_RESET` |
| delta 뒤 지워진 메시지(원문·첨부 목록 404) | 그 메시지만 건너뜀. 다음 delta가 제거를 알려 줌 | 성공 가능 |
| 메시지는 있고 첨부만 404 | 그 첨부 행만 건너뛰고 메시지는 저장. 원문 MIME에 첨부가 들어 있음 | 성공 가능 |
| 실행 중 연결 해제 | `FAILED`, `MAILBOX_NOT_CONNECTED` | 기록 |
| 50MB를 넘는 원문·첨부 | `FAILED`, `MAIL_OBJECT_TOO_LARGE` | 기록 |
| 객체 저장소 장애·거부·내용 불일치 | `FAILED`, `MAIL_STORAGE_UNAVAILABLE`. 그 페이지는 기록하지 않음 | 기록, 커서 유지 |
| Graph 밖을 가리키는 다음 주소 | `FAILED`, `GRAPH_LINK_REJECTED`. 그 주소로 요청하지 않음 | 기록 |
| 실행 중 작업 취소(`asyncio.CancelledError`) | `FAILED`, `SYNC_CANCELLED`. 기록 뒤 취소를 다시 전달. lease 만료를 기다리지 않고 다음 실행 가능. 모든 폴더를 받은 뒤 종료 기록 중에 취소되면 그 기록(`SUCCEEDED` 등)은 끝까지 쓴다 | 기록, 커서 유지 |

- 401 재시도 기회는 수집 실행 한 번 전체에 하나다. delta 페이지·원문·첨부 요청이 같은 기회를 쓰고, 페이지·메시지마다 새로 주지 않는다. 그래서 401이 반복돼도 요청이 늘지 않는다(KAN-81 댓글 10396 합의).
- 401만으로 재연결 필요로 바꾸지 않는다. 재연결 필요 여부는 토큰 제공자가 갱신 결과로 판단한다.
- 실행이 약 1시간(access token 수명)을 두 번 넘기면 두 번째 만료에서 실패한다. 다음 실행이 커서부터 이어 받는다.
- 실패를 빈 목록이나 성공으로 기록하지 않는다. 오류 코드에는 공급자 원문 설명·토큰·URL을 넣지 않는다.
- 사용량 제한(429)도 대기 없이 실패로 끝낸다. 재시도 간격은 실행을 부르는 쪽이 정한다.
- 동기화 상태가 만료돼 폴더를 다시 받을 때, 그 사이 Graph에서 지워진 메시지는 전체 목록에 나오지 않으므로 `removed_at`이 기록되지 않는다. 다시 받은 결과에 없는 행의 정리는 아직 하지 않는다.
- lease(300초)는 페이지 기록과 메시지 내려받기마다 늘린다. 메시지 한 건(최대 50MB)의 내려받기가 lease보다 오래 걸리면 다른 실행이 이어받을 수 있다.

## 실제 사서함 확인

2026-10-28 13:16 KST, 시연용 개인 Outlook 사서함 1개(가상 인물 계정)로 `sync_mailbox`를 두 번 실행했다.

- 방식: 로컬 로그인(`http://localhost:8765/callback`)으로 `Mail.Read` 토큰만 메모리에 받음. 새 로컬 PostgreSQL 17에 identity·KAN-81·KAN-71 마이그레이션을 적용하고 합성 사용자·연결 행으로 실행. 제목·주소·본문·ID·토큰은 출력하지 않았고 받은 원문은 확인 뒤 지웠다.
- 1회차(전체): `SUCCEEDED`, 8통 저장(받은편지함 7·보낸편지함 1)
- 2회차(delta): `SUCCEEDED`, 1통 신규 저장. 1회차 실행 중 도착한 메일이다(수신 시각이 1회차 실행 구간 안).
- 중복 ID 0건, 수신 시각 없음 0건, `internetMessageId` 없음 0건. 첨부 1개가 `hasAttachments` 메시지 1통과 일치. 저장 객체 10개의 SHA-256·크기 불일치 0개. 두 폴더 모두 delta 링크 저장.
- 이 확인은 PR #105 수집 코드로 했다. 이후 바뀐 취소 기록·사서함별 저장 키(PR #140)와 Graph 401 강제 갱신(PR #145)은 합성 검사로만 확인했다.
- 제품 사서함 연결 화면(KAN-81 HTTP)을 거친 흐름은 이 확인 범위가 아니다.

## 워커와 정기 실행 (KAN-72)

`backend/api/platform/mail/worker.py`. 두 내부 경로는 기본 비활성(404)이고 OpenAPI·웹 중계에 넣지 않는다.

```text
Cloud Scheduler(5분, OIDC) ──POST──▶ /internal/v1/platform/mail/dispatch
        연결된 사서함마다 Cloud Tasks 작업 1개 등록 (이름: mail-<mailbox_id>-<구간>)
Cloud Tasks(mail-sync 큐, OIDC) ──POST {"mailbox_id"}──▶ /internal/v1/platform/mail/sync
        sync_mailbox(KAN-81 토큰 제공자, GcsObjectStore)
```

| 설정(환경 변수) | 값 |
| --- | --- |
| `MAIL_SYNC_ENABLED` | 기본 `false`. 켜려면 `MAILBOX_AUTH_ENABLED`도 켜져 있어야 한다 |
| `MAIL_SYNC_QUEUE` | `projects/<p>/locations/<l>/queues/mail-sync` |
| `MAIL_SYNC_SERVICE_URL` | 플랫폼 Cloud Run 주소(https origin, 끝 `/` 없음). 작업 대상과 ID 토큰 audience |
| `MAIL_SYNC_INVOKER_EMAIL` | Scheduler·Tasks가 ID 토큰을 받는 호출 전용 계정(`mail-sync-invoker@…`) |
| `MAIL_SYNC_INTERVAL_SECONDS` | 작업 이름 구간 길이. 기본 300초(Scheduler 주기와 맞춘다) |
| `MAIL_OBJECT_BUCKET` | 메일 객체 버킷(KAN-66 댓글 10427) |

- **호출자 확인**: Cloud Run IAM(`run.invoker`)과 별도로, 컨테이너가 `Authorization`의 Google ID 토큰을 고정 Google 서명키·`MAIL_SYNC_SERVICE_URL` audience·`MAIL_SYNC_INVOKER_EMAIL`로 다시 검증한다(`PlatformCaller`). 실패하면 DB·큐·Graph를 부르기 전에 401이다. 본문은 호출자 확인 뒤에 읽어, 잘못된 본문이 비활성 404·인증 401을 가리지 않는다(본문 오류는 422 `VALIDATION_ERROR`).
- **같은 사서함 중복 방지**: 등록 단계는 작업 이름(사서함 ID + 시간 구간)으로, 같은 구간에 다시 불려도 Cloud Tasks가 409로 거절한다(기존으로 셈). 실행 단계는 KAN-71 실행 표의 `RUNNING` 부분 유일 인덱스로 막는다. 구간이 겹쳐 두 작업이 동시에 와도 하나는 `MAIL_SYNC_IN_PROGRESS`로 건너뛴다.
- **같은 메일 재수집**: 사서함 안 `graph_id` 유일 제약과 저장소의 같은 키 재사용으로, 커서를 잃고 전체를 다시 받아도 메시지·객체가 늘지 않는다.
- **응답 코드와 재시도**

| 결과 | 응답 | 큐·Scheduler |
| --- | --- | --- |
| 수집 성공 | 200 `SUCCEEDED` | 끝 |
| 진행 중·연결 해제·알 수 없는 사서함 | 200 `SKIPPED` + 코드 | 끝(다음 주기) |
| 일시 실패: `GRAPH_THROTTLED`·`GRAPH_UNAVAILABLE`·`MAILBOX_TOKEN_UNAVAILABLE`·`MAIL_STORAGE_UNAVAILABLE`·`LEASE_EXPIRED`·`SYNC_CANCELLED` | 503 `FAILED` | 큐 재시도(최대 3회, 30~300초) |
| 그 밖의 실패(`GRAPH_UNAUTHORIZED`·`GRAPH_FORBIDDEN`·`MAILBOX_RECONNECT_REQUIRED` 등) | 200 `FAILED` | 끝. 실행 기록에 남음 |
| 작업 등록 중 하나라도 실패 | 503 | Scheduler 재시도(최대 3회, 30~120초). 이미 등록된 작업은 중복되지 않음 |

- 큐·호출 계정·Scheduler 준비는 `infra/setup_mail_sync.sh`(인프라 담당자 실행). Scheduler 작업은 일시 중지로 만들고, 앱 설정을 켠 뒤 resume한다. 권한은 호출 계정에 플랫폼 `run.invoker`, `platform-runtime`에 큐 `cloudtasks.enqueuer`와 호출 계정 `iam.serviceAccountUser`만 준다.

## 승인 메일 발송 (KAN-74)

`backend/api/platform/mail/send.py`, 마이그레이션 `infra/migrations/platform/20261028_KAN-74_mail_send.sql`. 서버 내부 함수이며 HTTP 입구·운영 활성화는 아직 없다. 상태는 [공통 API 계약](../../docs/api-contract.md#메일-발송의-특별-규칙)의 `QUEUED`·`SUBMITTING`·`ACCEPTED`·`UNKNOWN`·`FAILED`다.

| 표 | 내용 |
| --- | --- |
| `integration.mail_send_intents` | 발송 의도. 발신 사서함, 업무 참조(`owner_service`, `business_ref`, `business_version`), 승인자·승인 시각, 제목·본문, 수신자(`to`·`cc`), 첨부(이름·형식·저장 키·SHA-256·크기), payload 해시, 상태, 시도 수(최대 3), 고정 오류 코드, 접수·보낸편지함 사본 확인 시각과 사본 ID. `(owner_service, business_ref, business_version)` 유일 |
| `integration.mail_send_events` | 감사 이력. `QUEUED`·`SUBMITTING`·`ACCEPTED`·`DEFERRED`·`UNKNOWN`·`FAILED`·`SENT_COPY_CONFIRMED`·`SENT_COPY_NOT_FOUND`·`RESEND_AUTHORIZED`, 처리자(비면 시스템), 코드, 사유. 추가만 가능 |

| 함수 | 동작 |
| --- | --- |
| `create_intent(settings, SendRequest)` | 형식 확인(NUL 문자 없음, 수신자 1~50명·중복 없음, 제목 한 줄, 본문 1MB 이하, 첨부 합계 2.8MB 이하, 제목·본문·수신자·첨부 이름을 모두 이스케이프한 JSON 길이 + base64 첨부 + 여유 64KB가 3.9MB 이하(`MAIL_TOO_LARGE`), 첨부 저장 키가 발신 사서함의 내용 키 `mail/<mailbox_id>/sha256/...`일 것) 뒤, 서버가 다시 계산한 payload 해시가 업무의 승인 해시(`approved_payload_sha256`)와 같을 때만 저장. 발신 사서함은 **승인자 본인의 연결 상태 사서함**이어야 한다. 같은 업무 판본을 같은 내용으로 다시 부르면 기존 의도, 다른 내용이면 409 `MAIL_SEND_CONFLICT` |
| `submit_intent(...)` | `QUEUED`→`SUBMITTING`을 커밋한 뒤 트랜잭션 밖에서 저장 내용의 해시와 첨부 바이트(해시·크기)를 다시 확인하고 Graph `sendMail`을 한 번 호출 |
| `confirm_sent_copy(...)` | 메일에 붙인 숨은 확장 속성(`INTENT_PROPERTY` = 발송 의도 ID)으로 보낸편지함 사본을 찾는다 |
| `authorize_resend(..., actor_user_id, reason)` | `UNKNOWN` 재발송 허가. 아래 조건 |
| `expire_submitting(settings, older_than)` | 오래 `SUBMITTING`에 남은 의도를 `UNKNOWN`(`MAIL_SUBMIT_INTERRUPTED`)으로. 나갔을 수 있어 다시 보내지 않는다 |

- **payload 해시**: `payload_sha256()`가 제목·본문 형식·본문·`to`·`cc`·첨부(이름·형식·SHA-256·크기)를 정렬 JSON으로 만들어 SHA-256을 낸다. 저장 키는 넣지 않는다. 업무는 승인할 때 같은 함수로 계산한 값을 승인 기록에 묶는다.
- **접수 ≠ 배달**: 202는 `ACCEPTED`(접수)다. 보낸편지함 사본 확인은 `sent_copy_confirmed_at`으로 따로 남기며 이것도 배달 확인이 아니다. 화면에 "배달 완료"로 표시하지 않는다.

| sendMail 결과 | 상태 | 다시 보내기 |
| --- | --- | --- |
| 202 | `ACCEPTED` | 없음 |
| 401 | 새 토큰(`force_refresh=True`)으로 한 번 더. 그래도 401이면 `FAILED` `GRAPH_UNAUTHORIZED`. 갱신이 일시 장애면 `QUEUED`(`DEFERRED`) | 401은 처리 전 거절이라 한 번 재요청 |
| 400·403·404·413·422 | `FAILED`(`GRAPH_BAD_REQUEST`·`GRAPH_FORBIDDEN`·`GRAPH_NOT_FOUND`·`MAIL_TOO_LARGE`) | 없음. 내용을 고쳐 새 판본으로 승인 |
| 429 | `QUEUED`(`DEFERRED`, `GRAPH_THROTTLED`) | 같은 의도를 나중에 다시 보낼 수 있음. 세 번째 시도까지 미뤄지면 `FAILED` `MAIL_SEND_ATTEMPTS_EXHAUSTED`로 마감 |
| 5xx | `UNKNOWN` `GRAPH_UNAVAILABLE` | **자동으로 안 함** |
| 응답 없음(연결 끊김·시간 초과) | `UNKNOWN` `GRAPH_NO_RESPONSE` | **자동으로 안 함** |
| 보내기 전 토큰·저장소 일시 장애 | `QUEUED`(`DEFERRED`) | 나가지 않았으므로 가능. 429와 같은 시도 한도 |
| 보내기 전 해시 불일치·재연결 필요 등 | `FAILED` | 없음 |
| 보내기 전 예상하지 못한 오류 | `FAILED` `MAIL_SEND_PREPARE_FAILED`(예외는 그대로 전달) | 없음. 나가지 않았으므로 `UNKNOWN`으로 두지 않음 |

- **결과 불명 재발송 통제**: `UNKNOWN`은 `confirm_sent_copy`로 먼저 대조한다. 사본이 있으면 실제로 나간 것으로 `ACCEPTED`가 된다. 사본이 없으면 `SENT_COPY_NOT_FOUND`를 **조회를 시작한 DB 시각**으로 남긴다. 재발송은 **마지막 전송 시작 10분 뒤의 "사본 없음" 확인**이 있고, **승인자 본인**이 사유(1~500자)를 적었을 때만 `QUEUED`로 되돌린다(`RESEND_AUTHORIZED`). 시도는 최대 3회다.
- **권한**: `platform_app`은 의도 SELECT·INSERT와 상태 열만 UPDATE(`20261029_KAN-74_mail_send_column_grants.sql`), 이력 SELECT·INSERT만. 승인 내용(본문·수신자·첨부·payload 해시)·이력 수정과 삭제 불가. `cs_app`·`tft_app`은 직접 접근하지 않는다.
- 문의 종결 조건(“`UNKNOWN` 발송 의도가 없어야 함”, [권한 문서](permissions.md))은 이 표의 상태로 판단한다. 발송 상태가 문의·개선요청 상태를 자동으로 바꾸지 않는다.

## 업무 유형 분류 (KAN-73, 부분 구현)

수집한 메일 한 통이 어느 업무로 가는지 분류해 **추천만** 저장한다. 문의·프로젝트에 자동으로 연결하거나 C/S 문의로 접수하지 않는다([데이터 계약](../../docs/data-contract.md#메일과-파일-연결): 제목이나 AI 분류만으로 확정 연결하지 않는다). 수집 코드(`sync.py`·`worker.py`)는 바꾸지 않았고 수집 뒤 자동 분류는 아직 연결하지 않았다.

| 항목 | 내용 |
| --- | --- |
| 유형(`mail_type`) | `NEW_RFP`(TFT 신규 RFP)·`EXISTING_CHANGE`(기존 프로젝트 변경)·`CANDIDATE_REPLY`(후보 회신)·`CS`(C/S 문의)·`OTHER` |
| `needs_review` | 모델 값, 또는 첨부 처리 대기·근거 없음·`OTHER`면 엔진이 true로 정한다 |
| `attachments_pending` | 링크형·inline이 아닌 첨부가 있으면 true(첨부 내용은 아직 처리하지 않음). 엔진이 정한다 |
| `evidence` | 텍스트 없는 위치만: 마스킹한 입력의 `subject`·`body` 글자 구간(`start`<`end`), `reply_header`(회신 메일일 때만). 최대 10개 |
| `link_suggestion` | 연결 후보일 뿐 확정 연결이 아니다. 유형별 허용: `NEW_RFP`→`NEW_PROJECT`, `EXISTING_CHANGE`·`CANDIDATE_REPLY`→`EXISTING_THREAD`(회신일 때만), `CS`→`NEW_CS_TICKET`·`EXISTING_THREAD`, `OTHER`→없음 |

- **엔진**(`backend/engine/mail_triage/`, 순수): `extract.build_input()`이 원문 MIME에서 text/plain(없으면 html의 보이는 텍스트)과 제목을 꺼내 인용 이력·`>` 줄을 자르고 C/S 분류와 같은 규칙(`mask_contacts_and_serials`)으로 이메일·라벨 시리얼을 가린 **뒤** 제목 500·본문 8,000자로 줄인다(경계에 걸친 주소·시리얼 조각이 남지 않게). 첨부는 종류·content type·inline만 넘기고 이름은 넘기지 않는다. `triage.validate_output()`은 정확히 네 필드를 엄격히 검사하고 근거 구간·연결 추천을 입력·유형과 대조한다(어기면 `MAIL_TRIAGE_INVALID_OUTPUT`). 제공자는 주입한 함수이며 **지금은 합성 제공자만** 쓴다. 실제 메일 본문을 외부 모델로 보내는 것은 개인정보 검수·승인 전까지 범위 밖이다.
- **저장**(`20261028_KAN-73_mail_triage.sql`, 공용 DB 미적용): `integration.mail_triage`에 판본마다 한 행(모델 분류 `ai_draft`는 입력·출력 판본, 사람 재분류 `human_input`은 사유·판정자 필수), `integration.mail_triage_latest`에 메일별 최신 판본. `platform_app`은 두 표에 SELECT·INSERT와 `latest_version` 열 UPDATE만 하고 고치거나 지우지 못한다. 메일 원문·본문·주소는 저장하지 않는다.
- **내부 함수** `classify_message(settings, message_id, raw, provider)`: 호출자가 객체 저장소에서 읽은 원문을 넘기고 저장된 SHA-256과 같아야 한다(다르면 409 `MAIL_TRIAGE_RAW_MISMATCH`). 제공자 호출은 트랜잭션 밖에서 하고 최신 판본 행을 잠근 뒤 다음 판본을 쓴다. 같은 메일의 첫 분류가 동시에 들어오면 먼저 쓴 쪽만 남고 나머지는 409 `MAIL_TRIAGE_VERSION_CONFLICT`다.
- **HTTP**(사서함 연결과 같은 `MAILBOX_AUTH_ENABLED`가 꺼져 있으면 404):

| 경로 | 동작 |
| --- | --- |
| `GET /api/v1/platform/mail/messages/{message_id}/triage?version=` | 메일함 소유자만. 최신(또는 지정) 판본과 `latest_version`·`is_latest`. 다른 사람 메일·없는 메일은 404 `MAIL_MESSAGE_NOT_FOUND`, 분류·판본이 없으면 404 `MAIL_TRIAGE_NOT_FOUND` |
| `POST /api/v1/platform/mail/messages/{message_id}/triage` | 메일함 소유자의 재분류. 동일 출처·세션 CSRF 필수. 본문 `version`(읽은 판본)·`mail_type`·`link_suggestion`(유형별 허용)·`needs_review`·`reason`(필수). 근거는 비우고 첨부 대기는 이전 값을 잇는다. 읽은 판본이 아니면 409 `MAIL_TRIAGE_VERSION_CONFLICT` |

- 응답에 제목·본문·보낸 사람은 없다. 권한 5.2의 '본인 메일함만'을 따르며, 업무에 연결된 뒤의 열람 범위는 연결 단위에서 정한다.
- **다음 단위**: 수집 뒤 자동 분류(수집 코드·객체 저장소 읽기, 김나영 합의), 메일 → C/S 문의 접수 경로(`source=microsoft_graph`, `external_id`=Graph ID, `known_at`=수신 시각, cs_agent 확인 뒤 플랫폼 → C/S 호출자 토큰), 화면, 실제 모델 호출·평가.

## 남은 작업

- KAN-74 후속: C/S 업무가 발송 의도를 만들고 상태를 조회하는 HTTP 입구, 의도 전송 실행(워커), 실제 사서함 발송 확인
- 운영 적용: `infra/setup_mail_sync.sh` 실행, Cloud Run 환경 변수(`MAIL_SYNC_*`, `MAIL_OBJECT_BUCKET`) 설정, Scheduler resume, 실제 Tasks·버킷·Graph 연결 확인
- 회신·스레드와 업무 연결(KAN-75), 수집한 메일을 C/S 문의로 바꾸는 연결(분류 추천은 위 KAN-73 절)
- 수집 뒤 자동 분류를 연결할 때(KAN-73 후속): 최신 판본이 사람 재분류(`human_input`)면 모델 분류를 건너뛴다(지금 `classify_message`는 항상 다음 모델 판본을 쓴다)
