"""KAN-90 실험 등록 API를 실제 PostgreSQL에서: 서버 hash 계산·동결 등록·거부 규칙·승인 분리."""

import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.evaluations.admission import canonical_hash, config_payload
from backend.shared.access import Principal
from tests.platform.test_evaluation_access import client_for

BASE = "/api/v1/platform/evaluations"
FILE = "c" * 64


def body(**changes):
    """합성 smoke 등록 요청을 만든다.

    Args:
        **changes: 바꿀 필드.

    Returns:
        dict: 요청 본문.
    """
    value = {
        "name": "합성 등록 실험",
        "purpose": "smoke",
        "data_origin": "synthetic",
        "model_id": "fake",
        "stage": "draft",
        "knobs": {"prompt_version": "v2", "temperature": 0.2},
        "dataset_manifest": {
            "sources": [
                {
                    "source": "synthetic",
                    "source_instance": "fixture-v1",
                    "file_sha256": FILE,
                    "count": 2,
                    "data_origin": "synthetic",
                }
            ]
        },
        "targets": [
            {"target_ref": ref, "target_kind": "smoke", "data_origin": "synthetic"}
            for ref in ("syn-b", "syn-a")
        ],
    }
    return value | changes


@pytest.fixture
def evaluator(settings, access, experiment, evaluation_database, monkeypatch):
    """쓰기 헤더를 갖춘 지정 평가자 HTTP 클라이언트와 등록한 실험 정리."""
    actor = experiment["actor"]
    client = client_for((actor,))
    client.app.state.settings = settings
    client.app.state.evaluation_access = access
    loader = AsyncMock(return_value=Principal(user_id=actor, roles={"sales"}))
    monkeypatch.setattr("backend.api.platform.evaluations.access.load_session_principal", loader)
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    client.headers.update(
        {"Origin": "https://app.example.invalid", CSRF_HEADER: csrf_token("synthetic-session")}
    )
    client.loader = loader
    yield client
    with psycopg.connect(evaluation_database) as conn:
        mine = "SELECT id FROM evaluation.experiments WHERE created_by_user_id=%s AND id<>%s"
        conn.execute(
            f"DELETE FROM evaluation.experiment_targets WHERE experiment_id IN ({mine})",
            (actor, experiment["id"]),
        )
        conn.execute(
            f"DELETE FROM evaluation.experiments WHERE id IN ({mine})", (actor, experiment["id"])
        )


def register(client, payload):
    return client.post(BASE + "/experiments", json=payload)


def test_registration_freezes_experiment_with_server_hashes(evaluator, evaluation_database):
    response = register(evaluator, body())
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["target_set_hash"] == hashlib.sha256(b"syn-a\nsyn-b").hexdigest()
    assert data["dataset_hash"] == hashlib.sha256(FILE.encode()).hexdigest()
    assert (data["target_count"], data["admissible"], data["blocked_reason"]) == (
        2,
        False,
        "EXPERIMENT_NOT_APPROVED",
    )
    with psycopg.connect(evaluation_database, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM evaluation.experiments WHERE id=%s", (data["experiment_id"],)
        ).fetchone()
        targets = conn.execute(
            "SELECT target_ref FROM evaluation.experiment_targets WHERE experiment_id=%s "
            "ORDER BY target_ref",
            (data["experiment_id"],),
        ).fetchall()
    assert row["config_hash"] == data["config_hash"] == canonical_hash(config_payload(row))
    assert (row["derivation_kind"], row["stage"], row["knobs"]) == (
        "DERIVED",
        "draft",
        {"prompt_version": "v2", "temperature": 0.2},
    )
    assert [t["target_ref"] for t in targets] == ["syn-a", "syn-b"]
    listed = evaluator.get(BASE + "/experiments?stage=draft").json()["data"]["items"]
    [item] = [i for i in listed if i["experiment_id"] == data["experiment_id"]]
    assert (item["knobs"], item["target_count"]) == (
        {"prompt_version": "v2", "temperature": 0.2},
        2,
    )
    assert data["config_hash"] not in evaluator.get(BASE + "/experiments").text


def test_same_configuration_is_409_and_other_evaluators_get_no_id(
    evaluator, access, evaluation_database
):
    first = register(evaluator, body()).json()["data"]
    again = register(evaluator, body(name="이름만 다른 등록"))
    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "EVALUATION_EXPERIMENT_EXISTS"
    assert error["details"] == [{"experiment_id": first["experiment_id"]}]
    stranger = uuid4()
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (stranger, uuid4(), uuid4(), "r@example.invalid", "다른 평가자", "TS_AUTO"),
        )
    access.evaluator_user_ids = access.evaluator_user_ids | {stranger}
    evaluator.loader.return_value = Principal(user_id=stranger, roles={"sales"})
    try:
        other = register(evaluator, body())
        assert other.status_code == 409 and other.json()["error"]["details"] == []
        items = evaluator.get(BASE + "/experiments").json()["data"]["items"]
        assert first["experiment_id"] not in {i["experiment_id"] for i in items}
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("DELETE FROM identity.users WHERE id=%s", (stranger,))


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {"data_split": "test", "gold_version": "g1"},
            "EVALUATION_TEST_SPLIT_REGISTRATION_BLOCKED",
        ),
        ({"purpose": "pipeline", "data_split": "valid"}, "EVALUATION_EXPERIMENT_INCOMPLETE"),
        ({"configuration": {"provider": {"API_KEY": "x"}}}, "EVALUATION_CONFIGURATION_SECRET"),
        ({"knobs": {"gemini_token": "x"}}, "EVALUATION_KNOB_SECRET"),
        (
            {
                "targets": [
                    {
                        "target_ref": "a",
                        "target_kind": "k",
                        "data_origin": "synthetic",
                        "attributes": {"secret_note": 1},
                    }
                ]
            },
            "EVALUATION_TARGET_ATTRIBUTE_FORBIDDEN",
        ),
        (
            {
                "targets": [
                    {
                        "target_ref": "a",
                        "target_kind": "k",
                        "data_origin": "synthetic",
                        "attributes": {"flags": {"gold_label": "x"}},
                    }
                ]
            },
            "EVALUATION_TARGET_ATTRIBUTE_FORBIDDEN",
        ),
        (
            {"targets": [{"target_ref": "a", "target_kind": "k", "data_origin": "synthetic"}] * 2},
            "EVALUATION_TARGET_DUPLICATE",
        ),
        (
            {
                "dataset_manifest": {
                    "sources": [
                        {
                            "source": "csv",
                            "source_instance": "v1",
                            "file_sha256": FILE,
                            "count": 1,
                            "data_origin": "real",
                        }
                    ]
                }
            },
            "EVALUATION_DATA_ORIGIN_MISMATCH",
        ),
    ],
)
def test_registration_rules_are_422_with_codes(evaluator, evaluation_database, changes, code):
    response = register(evaluator, body(**changes))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    with psycopg.connect(evaluation_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM evaluation.experiments WHERE name='합성 등록 실험'"
        ).fetchone() == (0,)


def test_mixed_sources_register_only_as_demo(evaluator):
    mixed = body(
        data_origin="demo",
        dataset_manifest={
            "sources": [
                {
                    "source": "csv",
                    "source_instance": "v1",
                    "file_sha256": FILE,
                    "count": 1,
                    "data_origin": "real",
                },
                {
                    "source": "syn",
                    "source_instance": "v1",
                    "file_sha256": "d" * 64,
                    "count": 1,
                    "data_origin": "synthetic",
                },
            ]
        },
    )
    registered = register(evaluator, mixed)
    assert registered.status_code == 201
    expected = hashlib.sha256(f"{FILE}\n{'d' * 64}".encode()).hexdigest()
    assert registered.json()["data"]["dataset_hash"] == expected
    assert register(evaluator, mixed | {"data_origin": "real", "name": "x"}).json()["error"][
        "code"
    ] == ("EVALUATION_DATA_ORIGIN_MISMATCH")


@pytest.mark.parametrize(
    "changes",
    [
        {"dataset_manifest": {"files": []}},
        {"targets": []},
        {"data_split": "holdout"},
        {"config_hash": "f" * 64},
    ],
)
def test_malformed_requests_are_rejected(evaluator, changes):
    assert register(evaluator, body(**changes)).status_code == 422


def test_registered_experiment_still_needs_operator_approval(evaluator):
    registered = register(evaluator, body()).json()["data"]
    evaluator.headers["Idempotency-Key"] = str(uuid4())
    run = evaluator.post(BASE + "/runs", json={"experiment_id": registered["experiment_id"]})
    assert run.status_code == 403
    assert run.json()["error"]["code"] == "EXPERIMENT_NOT_APPROVED"


def test_registration_needs_write_headers_and_open_admission(evaluator, access):
    del evaluator.headers[CSRF_HEADER]
    assert register(evaluator, body()).status_code == 403
    evaluator.headers[CSRF_HEADER] = csrf_token("synthetic-session")
    access.local_admission_enabled = False
    response = register(evaluator, body())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EVALUATION_RUNNER_NOT_CONNECTED"
