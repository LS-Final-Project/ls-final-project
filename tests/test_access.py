"""KAN-78 user context: services trust it only in local/test and reject anything malformed."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.shared.access import PRINCIPAL_HEADER, Principal, principal_header
from backend.shared.settings import Settings

PATH = "/api/v1/cs/tickets"


def client(environment: str = "test") -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, environment=environment)))


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        json.dumps({"roles": ["cs_agent"]}),
        json.dumps({"user_id": str(uuid4()), "roles": []}),
        json.dumps({"user_id": str(uuid4()), "roles": ["admin"]}),
        json.dumps({"user_id": str(uuid4()), "roles": ["cs_agent"], "approved": True}),
        json.dumps({"user_id": str(uuid4()), "roles": ["dept_staff"]}),
        json.dumps(
            {"user_id": str(uuid4()), "roles": ["cs_agent"], "managed_departments": ["GCS_L1"]}
        ),
    ],
)
def test_missing_or_invalid_user_context_is_unauthenticated(value):
    with client() as test_client:
        result = test_client.get(PATH, headers={PRINCIPAL_HEADER: value})
        assert result.status_code == 401
        assert result.json()["error"]["code"] == "AUTH_REQUIRED"


def test_no_user_context_is_unauthenticated():
    with client() as test_client:
        assert test_client.get(PATH).status_code == 401


def test_production_ignores_a_caller_supplied_user_context():
    header = principal_header(Principal(user_id=uuid4(), roles={"cs_agent"}))
    with client("production") as production:
        result = production.get(PATH, headers=header)
        assert result.status_code == 404
        assert result.json()["error"]["code"] == "NOT_FOUND"
