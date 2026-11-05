"""KAN-108 workflow routes: checks that need no database."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = principal_header(Principal(user_id=uuid4(), roles={"cs_agent"}))
PATH = f"/api/v1/cs/tickets/{uuid4()}/workflow"


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("transfer", {"version": 1, "dept_code": "GCS_L1", "reason": "x"}),
        ("transfer", {"version": 1, "dept_code": "TS_TRF", "reason": ""}),
        ("recall", {"version": -1, "reason": "x"}),
        ("dept-result", {"version": 1, "result": "x", "extra": 1}),
    ],
)
def test_invalid_actions_are_rejected_before_any_lookup(action, body):
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        assert client.post(f"{PATH}/{action}", json=body, headers=AGENT).status_code == 422


def test_workflow_routes_are_closed_in_production():
    app = create_app(Settings(_env_file=None, environment="production"))
    with TestClient(app) as client:
        assert client.get(PATH, headers=AGENT).status_code == 404
        assert client.post(f"{PATH}/close", json={"version": 1}, headers=AGENT).status_code == 404
