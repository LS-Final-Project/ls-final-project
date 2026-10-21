"""Dependency checks kept distinct from process liveness."""

from fastapi import HTTPException, Request
from pydantic import BaseModel

from backend.shared.database import connection
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.settings import ServiceName


class ReadinessData(BaseModel):
    service: ServiceName
    database: bool
    vector: bool
    fts: bool


async def readiness(request: Request) -> SuccessResponse[ReadinessData]:
    service = request.app.state.service
    schema = {"cs": "cs", "tft": "tft", "platform": "identity"}[service]
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT current_user = %s AS correct_user, "
            "has_schema_privilege(current_user, %s, 'USAGE') AS own_schema, "
            "EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') AS vector, "
            "to_tsvector('simple', 'setup') @@ plainto_tsquery('simple', 'setup') AS fts",
            (f"{service}_app", schema),
        )
        row = await cursor.fetchone()
    if not row or not all(row.values()):
        raise HTTPException(503, "Database setup is incomplete")
    return SuccessResponse(
        data=ReadinessData(service=service, database=True, vector=True, fts=True),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
