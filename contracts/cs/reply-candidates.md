# C/S 회신 후보와 근거 계약 (KAN-235)

원본: `backend/api/cs/workflow/reply_candidates.py`(찾기), `backend/api/cs/workflow/internal_routes.py`(경로), 마이그레이션 `infra/migrations/cs/20261115_KAN-235_customer_contacts.sql`(C33), 넣기 `infra/sql/register_customer_contact.sql`. 플랫폼 쪽은 [메일 계약](../platform/mail.md) 회신 검토 API다.

**상태: 구현(합성 검사). 기획서 9쪽 "회신 연결"·22쪽 customer_contacts.** 시스템이 어느 문의의 답장인지 정하지 못한 받은 메일을 담당자가 고를 때, 붙일 만한 문의와 근거를 보인다.

## 원칙

- **후보와 근거는 사람이 고르는 데만 쓴다.** 자동 연결은 플랫폼에서 헤더·대화 묶음이 한 업무만 가리킬 때뿐이다. 주소·연락처·코드 일치·제목·AI 분류로는 자동 연결하지 않는다.
- 읽기만 하고 아무것도 바꾸지 않는다. 회신 본문은 코드 일치에만 쓰고 저장하지 않는다.

## 내부 경로 `POST /internal/v1/cs/tickets/reply-candidates`

플랫폼 실행 계정 토큰(다른 내부 경로와 같은 호출자 확인), 사용자 없음. OpenAPI·업무 중계에 없다.

입력(`extra` 금지): `from_address`(받은 메일의 보낸 주소, 선택), `text`(회신 본문 인용 제외, 50,000자 이하, 선택), `ticket_ids`(플랫폼이 이미 찾은 후보 문의, 20개 이하).

후보:

| 근거 코드 | 찾는 곳 |
| --- | --- |
| `ORIGIN_ADDRESS` | 문의를 접수한 메일의 보낸 주소(`cs.ticket_mail_origins.sender_address`)가 같음 |
| `SENT_TO_ADDRESS` | 우리가 이 주소로 보낸 메일이 있음(`cs.ticket_customer_sends`의 to·cc, 플랫폼에 넘긴 것) |
| `CUSTOMER_CONTACT` | 고객 연락처(C33)의 주소가 같거나 회사 도메인이 같은 고객의 **진행 중(종결 아님)** 문의 |

근거(후보마다, 위 셋에 더해):

| 근거 코드 | 뜻 |
| --- | --- |
| `WAITING_CUSTOMER` | 문의가 고객 회신 대기 상태 |
| `PRODUCT_MATCH` | 회신 본문에 문의의 제품 코드가 낱말로 있음(대소문자 무시) |
| `ERROR_CODE_MATCH` | 회신 본문에 문의의 보고된 오류 코드가 낱말로 있음 |

- 주소는 소문자로 비교한다. 형식이 아니면 주소·연락처 후보가 없다.
- 응답 `items`: `ticket_id`, `external_id`, `subject`, `state`, `customer_external_id`, `product_code`, `given`(플랫폼이 준 후보인지), `evidence`. 주어진 후보 먼저, 그다음 종결 아님·근거 많음·최근 접수 순으로 최대 10건. 없는 문의는 빠진다.

## 고객 연락처 (`cs.customer_contacts`, C33)

- 열: `customer_external_id`, `email` 또는 `domain`(둘 중 하나, 소문자), `active`, `data_origin`(`synthetic`·`demo`·`real`). 이름·전화·직책은 받지 않는다.
- **가정 자료**: LS 고객 연락처가 오기 전까지 시연값은 시연용 Outlook 계정 주소를 시나리오 고객 코드에 붙인 합성값이며 `data_origin=demo`다. API로 쓰지 않고 `infra/sql/register_customer_contact.sql`(postgres)로만 넣는다. 누구나 쓰는 메일 도메인(outlook.kr 등)은 도메인 행으로 넣지 않는다(그 도메인의 모든 사람이 그 고객이 된다).
- C/S 실행 계정(`cs_app`)은 읽기만 한다.

## 화면

메일함(MB0100)의 연결 후보에 근거 칩을 붙인다. 후보가 없으면 "이 주소로 주고받은 문의나 등록된 고객 연락처가 없어 후보를 찾지 못했습니다"를 보이고, 문의 번호로 찾은 후보 밖 문의도 골라 연결할 수 있다(플랫폼이 근거 `SEARCH`로 남김).

## 한계

- 판정은 메일당 한 번이다. 메일이 검토에 간 뒤 생긴 연락처·주소는 다음 후보 조회 때 보이지만, 검토로 가지 않은 메일(`NONE`)을 다시 판정하지 않는다.
- 회신 헤더가 없는 새 메일은 플랫폼이 주소를 주고받은 적이 있을 때만 검토로 간다(`ADDRESS_MATCH`). 연락처만 맞는 새 메일은 새 문의일 수 있어 검토로 보내지 않는다.
