"""Validated synthetic examples, exported only for local frontend mocking."""

from uuid import UUID

from backend.api.cs.tickets.schemas import TicketList, TicketSummary
from backend.shared.models import ResponseMeta, SuccessResponse


def examples() -> list[dict]:
    response: SuccessResponse[TicketList] = SuccessResponse(
        data=TicketList(
            items=[
                TicketSummary(
                    id=UUID("22222222-2222-4222-8222-222222222222"),
                    subject="개발용 문의 예시",
                    data_origin="demo",
                )
            ],
            next_cursor=None,
        ),
        meta=ResponseMeta(request_id=UUID("11111111-1111-4111-8111-111111111111")),
    )
    return [
        {
            "method": "GET",
            "path": "/api/v1/cs/tickets",
            "status": 200,
            "body": response.model_dump(mode="json"),
        }
    ]
