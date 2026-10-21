from uuid import UUID

from backend.api.tft.projects.schemas import ProjectList, ProjectSummary
from backend.shared.models import ResponseMeta, SuccessResponse


def examples() -> list[dict]:
    response: SuccessResponse[ProjectList] = SuccessResponse(
        data=ProjectList(
            items=[
                ProjectSummary(
                    id=UUID("33333333-3333-4333-8333-333333333333"),
                    name="개발용 TFT 예시",
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
            "path": "/api/v1/tft/projects",
            "status": 200,
            "body": response.model_dump(mode="json"),
        }
    ]
