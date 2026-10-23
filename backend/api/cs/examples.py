"""Validated synthetic examples, exported only for local frontend mocking.

Values come from the provided training data (CSV1-000044 / RC-0007, gasket leak demo case).
"""

from datetime import UTC, datetime
from uuid import UUID

from backend.api.cs.tickets.schemas import (
    AuditEvent,
    AuditList,
    ResolutionCase,
    TicketDetail,
    TicketList,
    TicketSummary,
)
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
    return [
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
    ] + write_examples()
