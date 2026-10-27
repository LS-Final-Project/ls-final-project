"""KAN-123 범위, 검증, 판본 및 멱등성 HTTP 회귀 검사."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.recommendations import repository, routes
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

REQUEST, ROLE, ORG, CANDIDATE, USER, PROJECT = [UUID(int=i) for i in range(1, 7)]
HEADERS = principal_header(Principal(user_id=USER, roles={"dept_staff"}, dept="FIELD_SVC"))
PATH = f"/api/v1/tft/staffing-requests/{REQUEST}"


@pytest.fixture
def store(monkeypatch):
    row = dict(
        id=REQUEST,
        project_id=PROJECT,
        required_role_id=ROLE,
        organization_id=ORG,
        dept_code="FIELD_SVC",
        organization_company="HOST",
        company_code="HOST",
        status="ISSUED",
        project_status="STAFFING",
        version=1,
        data_origin="synthetic",
        project_origin="real",
    )
    entries = {}
    commands = {}
    revisions = []

    class Conn:
        async def execute(self, *args):
            return None

    @asynccontextmanager
    async def connection(settings):
        previous = deepcopy((row, entries, commands, revisions))
        try:
            yield Conn()
        except Exception:
            for current, before in zip((row, entries, commands, revisions), previous, strict=True):
                current.clear()
                current.update(before) if isinstance(current, dict) else current.extend(before)
            raise

    async def scope(conn, request_id):
        return dict(row) if request_id == REQUEST else None

    async def person(conn, candidate_id):
        return dict(
            id=candidate_id,
            organization_id=ORG if candidate_id == CANDIDATE else uuid4(),
            data_origin="real",
        )

    async def parent(conn, recommendation_id):
        item = entries.get(recommendation_id)
        return {**item, "organization_id": ORG} if item else None

    async def replay(conn, actor, request_id, operation, key):
        return commands.get((actor, request_id, operation, key))

    async def remember(conn, actor, request_id, operation, key, payload, result):
        commands[actor, request_id, operation, key] = dict(payload=payload, result=result)

    async def insert(conn, table, values):
        entries[values["id"]] = {**values, "table": table}

    async def exists(conn, request_id, candidate_id):
        return any(x.get("candidate_id") == candidate_id for x in entries.values())

    async def update(conn, request_id, version, values):
        assert version == row["version"]
        row.update(values, version=version + 1)
        return datetime.now(UTC)

    async def revision(*args):
        revisions.append(args[3:])

    monkeypatch.setattr(routes, "connection", connection)
    for name, fn in (
        ("request_scope", scope),
        ("candidate", person),
        ("recommendation", parent),
        ("replay", replay),
        ("remember", remember),
        ("insert", insert),
        ("exists_candidate", exists),
    ):
        monkeypatch.setattr(repository, name, fn)
    monkeypatch.setattr(routes.requests, "update_request", update)
    monkeypatch.setattr(routes.requests, "insert_revision", revision)
    return row, entries, commands, revisions


@pytest.fixture
def client(store):
    with TestClient(create_app(Settings(service_name="tft", environment="test"))) as client:
        yield client


def post(
    client, suffix="/recommendations", body=None, key="recommend-1", version=1, headers=HEADERS
):
    return client.post(
        PATH + suffix,
        json=body
        if body is not None
        else dict(candidate_id=str(CANDIDATE), rationale="유사 시공 업무"),
        headers={**headers, "Idempotency-Key": key, "If-Match": f'"{version}"'},
    )


def test_recommend_preserves_origin_and_replays_first_response(client, store):
    first = post(client)
    assert first.status_code == 200
    assert first.headers["etag"] == '"2"'
    assert post(client).json() == first.json()
    row, records, commands, revisions = store
    assert len(records) == len(commands) == len(revisions) == 1
    record = next(iter(records.values()))
    assert record["recommended_by_user_id"] == USER
    assert record["data_origin"] == "synthetic"
    assert row["status"] == "ANSWERED"
    assert row["project_status"] == "STAFFING"
    assert (
        post(client, body=dict(candidate_id=str(CANDIDATE), rationale="다른 입력")).status_code
        == 409
    )
    assert post(client, key="new", version=2).status_code == 409


@pytest.mark.parametrize(
    "change", [dict(dept_code="OTHER"), dict(status="DRAFT"), dict(organization_company="OTHER")]
)
def test_out_of_scope_is_404(client, store, change):
    store[0].update(change)
    assert post(client).status_code == 404
    assert not store[1]


def test_permission_and_candidate_scope(client, store):
    assert post(client, headers={}).status_code == 401
    manager = principal_header(
        Principal(user_id=USER, roles={"manager"}, managed_projects={PROJECT})
    )
    assert post(client, headers=manager).status_code == 403
    assert post(client, body=dict(candidate_id=str(uuid4()), rationale="추천")).status_code == 404
    assert not store[1]


@pytest.mark.parametrize("change", [dict(status="CLOSED"), dict(project_status="CONFIRMED")])
def test_closed_or_final_project_rejects_new_write(client, store, change):
    store[0].update(change)
    assert post(client).status_code == 409
    assert not store[1]


def test_version_and_missing_key(client, store):
    assert post(client, version=0).status_code == 412
    assert (
        client.post(
            PATH + "/recommendations",
            json=dict(candidate_id=str(CANDIDATE), rationale="추천"),
            headers={**HEADERS, "Idempotency-Key": "x"},
        ).status_code
        == 428
    )
    assert (
        client.post(
            PATH + "/recommendations",
            json=dict(candidate_id=str(CANDIDATE), rationale="추천"),
            headers=HEADERS,
        ).status_code
        == 422
    )
    assert not store[1]


def test_verification_separate_from_recommendation_and_identity_is_server_owned(client, store):
    rec = post(client).json()["data"]["id"]
    experience = post(
        client,
        f"/recommendations/{rec}/experience",
        dict(summary="실제 해저 시공 업무", status="CONFIRMED", basis="프로젝트 수행 기록"),
        key="exp",
        version=2,
    )
    assert experience.status_code == 200
    availability = post(
        client,
        f"/recommendations/{rec}/availability",
        dict(
            status="CONFIRMED",
            basis="부서 확인",
            period_start="2027-01-01",
            period_end="2027-02-01",
            allocation_rate=50,
        ),
        key="avail",
        version=3,
    )
    assert availability.status_code == 200
    records = list(store[1].values())
    assert [x["table"] for x in records] == [
        "candidate_recommendations",
        "experience_evidence",
        "availability_checks",
    ]
    assert records[1]["verified_by_user_id"] == records[2]["checked_by_user_id"] == USER
    assert all(x["data_origin"] == "synthetic" for x in records)
    assert store[0]["project_status"] == "STAFFING"
    assert (
        post(
            client,
            f"/recommendations/{rec}/availability",
            dict(status="NEEDS_CHECK", checked_by_user_id=str(uuid4())),
            key="forged",
            version=4,
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "payload",
    [
        dict(status="CONFIRMED"),
        dict(status="UNMET"),
        dict(status="CONFIRMED", basis="확인", allocation_rate=50),
        dict(period_start="2027-02-01", period_end="2027-01-01"),
        dict(allocation_rate=101),
    ],
)
def test_invalid_verification_has_no_side_effect(client, store, payload):
    rec = post(client).json()["data"]["id"]
    assert (
        post(
            client, f"/recommendations/{rec}/availability", payload, key="invalid", version=2
        ).status_code
        == 422
    )
    assert len(store[1]) == 1 and store[0]["version"] == 2


def test_scope_rechecked_before_replay(client, store):
    assert post(client).status_code == 200
    store[0]["dept_code"] = "OTHER"
    assert post(client).status_code == 404


def test_failed_receipt_rolls_back_records_and_version(client, store, monkeypatch):
    async def fail(*args):
        from fastapi import HTTPException

        raise HTTPException(503)

    monkeypatch.setattr(repository, "remember", fail)
    assert post(client).status_code == 503
    assert not store[1] and not store[2] and not store[3]
    assert store[0]["version"] == 1
