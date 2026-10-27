"""Validated synthetic examples, exported only for local frontend mocking.

Values come from the provided training data (CSV1-000044 / RC-0007, gasket leak demo case).
"""

from datetime import UTC, date, datetime
from uuid import UUID

from backend.api.cs.tickets.schemas import (
    AuditEvent,
    AuditList,
    ResolutionCase,
    TicketDetail,
    TicketList,
    TicketSummary,
)
from backend.api.cs.units.schemas import (
    InstalledUnit,
    TicketUnit,
    TicketUnitList,
    UnitHistory,
    UnitLink,
)
from backend.api.cs.workflow.schemas import DeptResult, Transfer, Workflow
from backend.shared.models import ResponseMeta, SuccessResponse

META = ResponseMeta(request_id=UUID("11111111-1111-4111-8111-111111111111"))
TICKET_ID = UUID("22222222-2222-4222-8222-222222222222")
SECOND_TICKET_ID = UUID("22222222-2222-4222-8222-222222222223")
RECEIVED_AT = datetime(2025, 10, 5, 16, 42, 59, tzinfo=UTC)
RESOLVED_AT = datetime(2025, 10, 6, 19, 32, 27, tzinfo=UTC)
SUBJECT = "Kurulum sorusu TRF-OIL-1000"
BODY = (
    "Oil-immersed Transformer 1000kVA için doğru kurulum prosedürü nedir? "
    "Bu model ile ilk projemiz.\n\nAyrıntılar:\n"
    "- Ürün modeli / sipariş kodu: TRF-OIL-1000\n"
    "- Seri numarası (etiket üzerindeki): **MASKED**\n"
    "- Saha koşulları (ortam sıcaklığı, rakım, nem): 50 °C, 300 m\n"
    "- Bağlantı / tek hat şeması: [attachment: SLD.pdf]"
)


MANUAL_ID = UUID("22222222-2222-4222-8222-222222222224")
MANUAL_AT = datetime(2026, 10, 23, 1, 0, 0, tzinfo=UTC)


def manual_ticket(version: int, product_code: str) -> TicketDetail:
    """A demo ticket entered through the API (no source ID, origin set by the server)."""
    return TicketDetail(
        id=MANUAL_ID,
        external_id=None,
        version=version,
        data_origin="demo",
        derivation_kind="SYSTEM",
        received_at=MANUAL_AT,
        known_at=MANUAL_AT,
        channel="EMAIL",
        body_language="en",
        customer_external_id="CUST-0374",
        product_code=product_code,
        subject="Oil leakage after delivery",
        body="Oil leakage at the gasket was found during installation.",
        fw_version_reported=None,
        error_code_reported=None,
        workflow_state="RECEIVED",
        resolution_case=None,
    )


def write_examples() -> list[dict]:
    path = f"/api/v1/cs/tickets/{MANUAL_ID}"
    history = AuditList(
        items=[
            AuditEvent(
                id=UUID("55555555-5555-4555-8555-555555555551"),
                action="create",
                version=1,
                actor_user_id=None,
                changes={
                    "channel": {"to": "EMAIL"},
                    "body_language": {"to": "en"},
                    "customer_external_id": {"to": "CUST-0374"},
                    "product_code": {"to": "TRF-OIL-1000"},
                    "subject": {"to": "Oil leakage after delivery"},
                    "fw_version_reported": {"to": None},
                    "error_code_reported": {"to": None},
                },
                occurred_at=MANUAL_AT,
            ),
            AuditEvent(
                id=UUID("55555555-5555-4555-8555-555555555552"),
                action="update",
                version=2,
                actor_user_id=None,
                changes={"product_code": {"from": "TRF-OIL-1000", "to": "TRF-OIL-2000"}},
                occurred_at=datetime(2026, 10, 23, 1, 5, 0, tzinfo=UTC),
            ),
        ]
    )
    responses = [
        ("POST", "/api/v1/cs/tickets", 201, manual_ticket(1, "TRF-OIL-1000")),
        ("PATCH", path, 200, manual_ticket(2, "TRF-OIL-2000")),
        ("GET", f"{path}/audit", 200, history),
    ]
    return [
        {
            "method": method,
            "path": route,
            "status": status,
            "body": SuccessResponse(data=data, meta=META).model_dump(mode="json"),
        }
        for method, route, status, data in responses
    ]


# Customer history scenario S13: CUST-0130 owns two meters. The September ticket is about
# the second one, so the earlier warranty record (installed 2024-05) is not carried over.
S13_TICKET_ID = UUID("66666666-6666-4666-8666-666666666661")
EARLIER_UNIT_ID = UUID("77777777-7777-4777-8777-777777777771")
CURRENT_UNIT_ID = UUID("77777777-7777-4777-8777-777777777772")
UNIT_DECIDED_AT = datetime(2026, 9, 11, 3, 0, 0, tzinfo=UTC)
AGENT_ID = UUID("88888888-8888-4888-8888-888888888881")


def meter(unit_id: UUID, serial: str, commissioned: date | None) -> InstalledUnit:
    return InstalledUnit(
        id=unit_id,
        external_id=None,
        data_origin="demo",
        derivation_kind="ASSUMED",
        serial_no=serial,
        customer_external_id="CUST-0130",
        product_code="METER-LP3500-3P4W",
        site_code=None,
        commissioning_date=commissioned,
        commissioning_status="CONFIRMED" if commissioned else "UNKNOWN",
        ship_date=None,
        lot_no=None,
        fw_version_last=None,
        unit_status="ACTIVE",
        replaced_by_unit_id=None,
    )


def unit_examples() -> list[dict]:
    path = f"/api/v1/cs/tickets/{S13_TICKET_ID}"
    earlier = meter(EARLIER_UNIT_ID, "VSN-MTR-1301", date(2024, 5, 1))
    current = meter(CURRENT_UNIT_ID, "VSN-MTR-1302", None)
    confirmed = TicketUnit(
        unit=current,
        matches_ticket=True,
        link=UnitLink(
            id=UUID("99999999-9999-4999-8999-999999999992"),
            version=1,
            link_status="CONFIRMED",
            link_basis="SERIAL_PROVIDED",
            repeat_type=None,
            decided_by=AGENT_ID,
            decided_at=UNIT_DECIDED_AT,
        ),
    )
    rejected = TicketUnit(
        unit=earlier,
        matches_ticket=True,
        link=UnitLink(
            id=UUID("99999999-9999-4999-8999-999999999991"),
            version=1,
            link_status="REJECTED",
            link_basis="AGENT_REVIEW",
            repeat_type="DIFFERENT_UNIT",
            decided_by=AGENT_ID,
            decided_at=UNIT_DECIDED_AT,
        ),
    )
    responses = [
        ("GET", f"{path}/units", 200, TicketUnitList(items=[rejected, confirmed])),
        ("PUT", f"{path}/units/{CURRENT_UNIT_ID}", 200, confirmed),
        (
            "GET",
            f"{path}/unit-history",
            200,
            UnitHistory(status="CONFIRMED", unit=current, items=[]),
        ),
    ]
    return [
        {
            "method": method,
            "path": route,
            "status": status,
            "body": SuccessResponse(data=data, meta=META).model_dump(mode="json"),
        }
        for method, route, status, data in responses
    ]


DEPT_USER_ID = UUID("88888888-8888-4888-8888-888888888882")
TRANSFER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")


def workflow_example() -> dict:
    """The gasket leak ticket after the transformer department reported and C/S accepted."""
    transferred_at = datetime(2025, 10, 5, 18, 0, 0, tzinfo=UTC)
    reported_at = datetime(2025, 10, 6, 15, 0, 0, tzinfo=UTC)
    workflow = Workflow(
        ticket_id=SECOND_TICKET_ID,
        state="IN_REVIEW",
        version=4,
        owner_user_id=AGENT_ID,
        customer_sent_at=None,
        closed_at=None,
        closed_by=None,
        transfers=[
            Transfer(
                id=TRANSFER_ID,
                round=1,
                dept_code="TS_TRF",
                status="RESULT_SUBMITTED",
                reason="Oil leakage needs a transformer engineer.",
                transferred_by=AGENT_ID,
                transferred_at=transferred_at,
                ended_by=DEPT_USER_ID,
                ended_at=reported_at,
                end_reason=None,
            )
        ],
        dept_results=[
            DeptResult(
                id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"),
                transfer_id=TRANSFER_ID,
                dept_code="TS_TRF",
                version=2,
                result="Gasket damaged in transport. Field service replaces it on site.",
                reply_content="An engineer will replace the gasket and top up the oil.",
                submitted_by=DEPT_USER_ID,
                submitted_at=reported_at,
                review_status="ACCEPTED",
                reviewed_by=AGENT_ID,
                reviewed_at=datetime(2025, 10, 6, 16, 0, 0, tzinfo=UTC),
                review_note=None,
            )
        ],
    )
    return {
        "method": "GET",
        "path": f"/api/v1/cs/tickets/{SECOND_TICKET_ID}/workflow",
        "status": 200,
        "body": SuccessResponse(data=workflow, meta=META).model_dump(mode="json"),
    }


def case_example() -> dict:
    """The case a C/S agent registers after closing, from the accepted department result."""
    registered_at = datetime(2025, 10, 7, 9, 0, 0, tzinfo=UTC)
    case = ResolutionCase(
        id=UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc1"),
        external_id=None,
        data_origin="synthetic",
        derivation_kind="SYSTEM",
        resolved_at=datetime(2025, 10, 7, 8, 30, 0, tzinfo=UTC),
        known_at=registered_at,
        product_code="TRF-OIL-1000",
        fault_id="FLT-067",
        fw_version_at_issue=None,
        symptom="Oil leakage at gasket",
        root_cause="Gasket damaged in transport",
        root_cause_type="WEAR_PART",
        action_taken="Replace gasket and top up oil",
        onsite_visit=True,
        rma_no=None,
        fw_fixed_version=None,
        resolved_by_dept="FIELD_SVC",
        recurrence_prevention=None,
        dept_result_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"),
    )
    return {
        "method": "POST",
        "path": f"/api/v1/cs/tickets/{SECOND_TICKET_ID}/resolution-case",
        "status": 201,
        "body": SuccessResponse(data=case, meta=META).model_dump(mode="json"),
    }


def examples() -> list[dict]:
    listing: SuccessResponse[TicketList] = SuccessResponse(
        data=TicketList(
            items=[
                TicketSummary(
                    id=SECOND_TICKET_ID,
                    external_id="CSV1-000120",
                    subject="Problem with Oil-immersed Transformer 1000kVA",
                    data_origin="synthetic",
                    derivation_kind="PROVIDED",
                    received_at=datetime(2025, 10, 14, 8, 18, 51, tzinfo=UTC),
                    channel="PHONE",
                    product_code="TRF-OIL-1000",
                    workflow_state="IN_REVIEW",
                ),
                TicketSummary(
                    id=TICKET_ID,
                    external_id="CSV1-000044",
                    subject=SUBJECT,
                    data_origin="synthetic",
                    derivation_kind="PROVIDED",
                    received_at=RECEIVED_AT,
                    channel="PARTNER_PORTAL",
                    product_code="TRF-OIL-1000",
                    workflow_state="CLOSED",
                ),
            ],
            next_cursor=None,
        ),
        meta=META,
    )
    detail: SuccessResponse[TicketDetail] = SuccessResponse(
        data=TicketDetail(
            id=TICKET_ID,
            external_id="CSV1-000044",
            version=1,
            data_origin="synthetic",
            derivation_kind="PROVIDED",
            received_at=RECEIVED_AT,
            known_at=RECEIVED_AT,
            channel="PARTNER_PORTAL",
            body_language="tr",
            customer_external_id="CUST-0374",
            product_code="TRF-OIL-1000",
            subject=SUBJECT,
            body=BODY,
            fw_version_reported=None,
            error_code_reported=None,
            workflow_state="CLOSED",
            resolution_case=ResolutionCase(
                id=UUID("44444444-4444-4444-8444-444444444444"),
                external_id="RC-0007",
                data_origin="synthetic",
                derivation_kind="PROVIDED",
                resolved_at=RESOLVED_AT,
                # Assumption for provided data: a case is usable once it was resolved.
                known_at=RESOLVED_AT,
                product_code="TRF-OIL-1000",
                fault_id="FLT-067",
                fw_version_at_issue=None,
                symptom="Oil leakage at gasket",
                root_cause="Gasket damage during transport",
                root_cause_type="WEAR_PART",
                action_taken="Replace gasket and top up oil",
                onsite_visit=True,
                rma_no=None,
                fw_fixed_version=None,
                resolved_by_dept="FIELD_SVC",
                recurrence_prevention="Quality feedback sent to factory for lot analysis.",
            ),
        ),
        meta=META,
    )
    return (
        [
            {
                "method": "GET",
                "path": "/api/v1/cs/tickets",
                "status": 200,
                "body": listing.model_dump(mode="json"),
            },
            {
                "method": "GET",
                "path": f"/api/v1/cs/tickets/{TICKET_ID}",
                "status": 200,
                "body": detail.model_dump(mode="json"),
            },
        ]
        + write_examples()
        + unit_examples()
    )
