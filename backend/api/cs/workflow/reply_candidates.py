"""회신 후보와 근거(KAN-235). 플랫폼이 사람 검토 중인 받은 메일의 보낸 주소·본문을 주면 그 메일을
붙일 만한 문의와 근거를 돌려준다.

후보는 세 곳에서 온다. 플랫폼이 이미 찾은 후보(헤더·대화 묶음, `ticket_ids`), 이 주소와 주고받은 문의
(문의를 접수한 메일의 보낸 주소, 우리가 이 주소로 보낸 메일), 고객 연락처(C33)로 찾은 같은 고객의
진행 중 문의다. 근거는 후보마다 따로 붙인다(고객 회신 대기, 본문의 제품·오류 코드 일치 포함).

후보와 근거는 사람이 고르는 데 쓴다. 이 결과로 자동 연결하지 않는다(자동 연결은 플랫폼의 헤더·대화 묶음뿐,
contracts/cs/reply-candidates.md). 읽기만 하고 아무것도 바꾸지 않는다.
"""

import re
from typing import Any, Literal
from uuid import UUID

Evidence = Literal[
    "ORIGIN_ADDRESS",
    "SENT_TO_ADDRESS",
    "CUSTOMER_CONTACT",
    "WAITING_CUSTOMER",
    "PRODUCT_MATCH",
    "ERROR_CODE_MATCH",
]
# 화면이 한 번에 고를 수 있는 만큼만. 주어진 후보는 이 수와 관계없이 모두 돌려준다.
MAX_FOUND = 10
EVIDENCE_ORDER: tuple[Evidence, ...] = (
    "ORIGIN_ADDRESS",
    "SENT_TO_ADDRESS",
    "CUSTOMER_CONTACT",
    "WAITING_CUSTOMER",
    "PRODUCT_MATCH",
    "ERROR_CODE_MATCH",
)


def mentions(text: str, code: str | None) -> bool:
    """본문에 코드가 낱말로 있는지(대소문자 무시). 코드 앞뒤가 글자·숫자면 다른 코드로 본다."""
    if not code or not text:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(code.strip()) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def address_parts(address: str | None) -> tuple[str | None, str | None]:
    """소문자 주소와 도메인. 형식이 아니면 (None, None)."""
    value = (address or "").strip().lower()
    if value.count("@") != 1 or len(value) > 320:
        return None, None
    local, domain = value.split("@")
    if not local or "." not in domain:
        return None, None
    return value, domain


async def find_candidates(
    conn: Any, *, from_address: str | None, text: str | None, ticket_ids: list[UUID]
) -> list[dict[str, Any]]:
    """후보 문의와 근거를 돌려준다. 주어진 후보 먼저, 그다음 진행 중·최근 접수 순.

    Args:
        conn: C/S DB 연결.
        from_address: 받은 메일의 보낸 주소(없으면 주소·연락처 후보 없음).
        text: 회신 본문(인용 제외). 제품·오류 코드 일치에만 쓰고 저장하지 않는다.
        ticket_ids: 플랫폼이 이미 찾은 후보 문의(헤더·대화 묶음). 없는 문의는 빠진다.

    Returns:
        list[dict]: `ticket_id`, `external_id`, `subject`, `state`, `customer_external_id`,
        `product_code`, `given`(주어진 후보인지), `evidence`(근거 코드 목록).
    """
    address, domain = address_parts(from_address)
    found: dict[UUID, set[str]] = {}
    if address is not None:
        cursor = await conn.execute(
            "SELECT ticket_id, 'ORIGIN_ADDRESS' AS evidence FROM cs.ticket_mail_origins "
            "WHERE lower(sender_address) = %(a)s "
            "UNION SELECT ticket_id, 'SENT_TO_ADDRESS' FROM cs.ticket_customer_sends "
            "WHERE request_status = 'REGISTERED' AND (to_addresses ? %(a)s OR cc_addresses ? %(a)s) "
            "UNION SELECT t.id, 'CUSTOMER_CONTACT' FROM cs.customer_contacts c "
            "JOIN cs.tickets t ON t.customer_external_id = c.customer_external_id "
            "LEFT JOIN cs.ticket_workflows w ON w.ticket_id = t.id "
            "WHERE c.active AND (c.email = %(a)s OR c.domain = %(d)s) "
            "AND coalesce(w.state, 'RECEIVED') <> 'CLOSED'",
            {"a": address, "d": domain},
        )
        for row in await cursor.fetchall():
            found.setdefault(row["ticket_id"], set()).add(row["evidence"])
    given = list(dict.fromkeys(ticket_ids))
    ids = given + [i for i in found if i not in given]
    if not ids:
        return []
    cursor = await conn.execute(
        "SELECT t.id, t.external_id, t.subject, t.customer_external_id, t.product_code, "
        "t.error_code_reported, t.received_at, coalesce(w.state, 'RECEIVED') AS state "
        "FROM cs.tickets t LEFT JOIN cs.ticket_workflows w ON w.ticket_id = t.id "
        "WHERE t.id = ANY(%s)",
        (ids,),
    )
    rows = {r["id"]: r for r in await cursor.fetchall()}
    body = text or ""
    items: list[dict[str, Any]] = []
    for ticket_id in ids:
        row = rows.get(ticket_id)
        if row is None:
            continue
        evidence = set(found.get(ticket_id, set()))
        if row["state"] == "WAITING_CUSTOMER":
            evidence.add("WAITING_CUSTOMER")
        if mentions(body, row["product_code"]):
            evidence.add("PRODUCT_MATCH")
        if mentions(body, row["error_code_reported"]):
            evidence.add("ERROR_CODE_MATCH")
        items.append(
            {
                "ticket_id": ticket_id,
                "external_id": row["external_id"],
                "subject": row["subject"],
                "state": row["state"],
                "customer_external_id": row["customer_external_id"],
                "product_code": row["product_code"],
                "given": ticket_id in given,
                "evidence": [e for e in EVIDENCE_ORDER if e in evidence],
                "_received_at": row["received_at"],
            }
        )
    head = [i for i in items if i["given"]]
    rest = sorted(
        (i for i in items if not i["given"]),
        key=lambda i: (i["state"] == "CLOSED", -len(i["evidence"]), -i["_received_at"].timestamp()),
    )[:MAX_FOUND]
    for item in head + rest:
        item.pop("_received_at")
    return head + rest
