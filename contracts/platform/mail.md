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
- `GoogleStorageToken`은 실행 환경 기본 자격 증명(`devstorage.read_write` 범위)으로 access token을 받고 만료되면 갱신한다. 토큰은 요청 헤더에만 쓴다.
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

## 남은 작업

- 수집을 부르는 워커·정기 실행과 실행 간격·재시도 정책(KAN-72)
- Cloud Run 환경 변수 `MAIL_OBJECT_BUCKET` 적용과 운영 저장소 연결(KAN-72 워커와 함께), 실제 버킷 쓰기 확인
- 회신·스레드와 업무 연결(KAN-75), 수집한 메일을 C/S 문의로 바꾸는 연결
