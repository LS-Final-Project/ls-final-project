"""범위 밖 메모 PDF는 접수·모델·DB 부수효과 없이 명시적으로 거절한다."""

import asyncio
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.agent.processor_registry import TftJob
from backend.api.tft.main import create_app
from backend.api.tft.rfp import jobs
from backend.api.tft.rfp import schemas as s
from backend.api.tft.worker_processor import make_processor
from backend.shared.access import Principal, principal_header
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


def test_annotation_http_refuses_before_any_job_or_database_side_effect():
    user = Principal(user_id=UUID(int=1), roles=frozenset({"sales"}))
    payload = s.AnnotationProcess(
        document_version_id=UUID(int=2), base_document_version_id=UUID(int=3)
    )
    app = create_app(Settings(_env_file=None, environment="test"))
    with patch.object(jobs, "connection", side_effect=AssertionError("no database")):
        with pytest.raises(ApplicationError) as error:
            asyncio.run(jobs.admit(None, user, UUID(int=4), "rfp.annotate", payload, "memo-key"))
        assert (error.value.status_code, error.value.code) == (422, "TFT_ANNOTATION_UNAVAILABLE")
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/tft/projects/{UUID(int=4)}/annotations/process",
                json=payload.model_dump(mode="json"),
                headers={**principal_header(user), "Idempotency-Key": "memo-pdf-key"},
            )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TFT_ANNOTATION_UNAVAILABLE"


def test_previously_queued_annotation_never_reports_success_or_calls_model():
    def forbidden(*args):
        pytest.fail("annotation must not invoke processing or emit success")

    processor = make_processor(
        Settings(_env_file=None, environment="test"), provider=forbidden, store=None, products=[]
    )
    job = TftJob(
        job_id=UUID(int=1),
        kind="rfp.annotate",
        project_id=UUID(int=2),
        input_version="a" * 64,
        actor_id=UUID(int=3),
    )
    with pytest.raises(ValueError, match="TFT_ANNOTATION_UNAVAILABLE"):
        processor(job, forbidden)
