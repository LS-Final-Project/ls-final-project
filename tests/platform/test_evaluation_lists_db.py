"""KAN-137 평가 실험·실행 목록을 실제 PostgreSQL에서: 본인 것만, 접수 가능 여부·사유, 숨김 필드."""

import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest

from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.evaluations.admission import CONFIG_FIELDS, canonical_hash
from backend.shared.access import Principal
from tests.platform.test_evaluation_access import client_for

BASE = "/api/v1/platform/evaluations"


def add_experiment(dsn, actor, *, stored_hash=None, **fields):
    """관리자 연결로 실험 하나를 만든다. 대상은 없다.

    Args:
        dsn: 관리자 연결.
        actor: 만든 사용자.
        stored_hash: 저장할 config_hash(없으면 필드로 계산).
        **fields: 기본 합성 smoke 값과 다른 구성 필드.

    Returns:
        tuple[UUID, str]: 실험 ID와 계산한 config_hash.
    """
    value = dict.fromkeys(CONFIG_FIELDS)
    value.update(
        purpose="smoke",
        data_origin="synthetic",
        dataset_hash=uuid4().hex * 2,
        model_id="fake",
        configuration={},
        target_set_hash="b" * 64,
    )
    value.update(fields)
    computed = canonical_hash({k: value[k] for k in CONFIG_FIELDS})
    experiment_id = uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO evaluation.experiments (id,name,purpose,data_origin,dataset_manifest,"
            "dataset_hash,data_split,gold_version,model_id,configuration,target_set_hash,"
            "config_hash,created_by_user_id) VALUES (%s,'합성 실험',%s,%s,'{}',%s,%s,%s,%s,'{}',%s,%s,%s)",
            (
                experiment_id,
                value["purpose"],
                value["data_origin"],
                value["dataset_hash"],
                value["data_split"],
                value["gold_version"],
                value["model_id"],
                value["target_set_hash"],
                stored_hash or computed,
                actor,
            ),
        )
    return experiment_id, computed


@pytest.fixture
def evaluator(settings, access, experiment, evaluation_database, monkeypatch):
    """지정 평가자 세션의 HTTP 클라이언트와 추가 실험 정리."""
    actor = experiment["actor"]
    client = client_for((actor,))
    client.app.state.settings = settings
    client.app.state.evaluation_access = access
    loader = AsyncMock(return_value=Principal(user_id=actor, roles={"sales"}))
    monkeypatch.setattr("backend.api.platform.evaluations.access.load_session_principal", loader)
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    yield client
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "DELETE FROM evaluation.experiments WHERE created_by_user_id=%s AND id<>%s",
            (actor, experiment["id"]),
        )


def listed(client, path):
    response = client.get(BASE + path)
    assert response.status_code == 200, response.text
    return response, response.json()["data"]["items"]


def test_experiments_show_admissibility_with_the_admission_rules(
    evaluator, access, experiment, evaluation_database
):
    dsn, actor = evaluation_database, experiment["actor"]
    final, final_hash = add_experiment(dsn, actor, data_split="test", gold_version="g1")
    unapproved, _ = add_experiment(dsn, actor)
    replay, replay_hash = add_experiment(
        dsn, actor, purpose="rule_replay", data_split="valid", gold_version="g1"
    )
    # 허용 목록에 있는 hash로 저장했지만 필드로 다시 계산하면 다르다.
    mismatch, _ = add_experiment(dsn, actor, stored_hash=hashlib.sha256(b"x").hexdigest())
    access.approved_config_hashes = access.approved_config_hashes | {
        final_hash,
        replay_hash,
        hashlib.sha256(b"x").hexdigest(),
    }
    stranger = uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (stranger, uuid4(), uuid4(), "s@example.invalid", "다른 평가자", "TS_AUTO"),
        )
    other, _ = add_experiment(dsn, stranger)
    try:
        response, items = listed(evaluator, "/experiments")
        reasons = {item["experiment_id"]: item["blocked_reason"] for item in items}
        assert reasons[str(experiment["id"])] is None
        assert reasons[str(final)] == "FINAL_TEST_LOCKED"
        assert reasons[str(unapproved)] == "EXPERIMENT_NOT_APPROVED"
        assert reasons[str(replay)] == "LOCAL_SMOKE_ONLY"
        assert reasons[str(mismatch)] == "EXPERIMENT_HASH_MISMATCH"
        assert str(other) not in reasons
        assert all(item["admissible"] is (item["blocked_reason"] is None) for item in items)
        mine = next(i for i in items if i["experiment_id"] == str(experiment["id"]))
        assert (mine["target_count"], mine["model_id"], mine["purpose"]) == (2, "fake", "smoke")
        for hidden in (experiment["config_hash"], experiment["target_set_hash"], "synthetic-1"):
            assert hidden not in response.text
        assert "dataset_manifest" not in response.text and "configuration" not in response.text
        access.local_admission_enabled = False
        _, closed = listed(evaluator, "/experiments")
        assert {i["blocked_reason"] for i in closed} == {"ADMISSION_UNAVAILABLE"}
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM evaluation.experiments WHERE id=%s", (other,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (stranger,))


def test_experiment_list_stops_at_fifty(evaluator, experiment, evaluation_database):
    for _ in range(50):
        add_experiment(evaluation_database, experiment["actor"])
    _, items = listed(evaluator, "/experiments")
    assert len(items) == 50


def test_runs_list_shows_own_admissions_and_hides_final_test(
    evaluator, experiment, evaluation_database
):
    evaluator.headers.update(
        {
            "Origin": "https://app.example.invalid",
            CSRF_HEADER: csrf_token("synthetic-session"),
            "Idempotency-Key": str(uuid4()),
        }
    )
    accepted = evaluator.post(BASE + "/runs", json={"experiment_id": str(experiment["id"])})
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()["data"]
    response, items = listed(evaluator, "/runs")
    assert [(i["run_id"], i["job_id"], i["status"]) for i in items] == [
        (run["run_id"], run["job_id"], "QUEUED")
    ]
    assert items[0]["experiment_id"] == str(experiment["id"]) and items[0]["requested_count"] == 2
    assert "raw_output" not in response.text and "gold" not in response.text
    # 최종 시험 실행은 상태 조회처럼 목록에서도 숨긴다.
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.experiments SET data_split='test', gold_version='g' WHERE id=%s",
            (experiment["id"],),
        )
    try:
        assert listed(evaluator, "/runs")[1] == []
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                "UPDATE evaluation.experiments SET data_split=NULL, gold_version=NULL WHERE id=%s",
                (experiment["id"],),
            )


def test_lists_are_for_designated_evaluators_only(settings, access, experiment, monkeypatch):
    client = client_for(())
    client.app.state.settings = settings
    loader = AsyncMock(return_value=Principal(user_id=experiment["actor"], roles={"sales"}))
    monkeypatch.setattr("backend.api.platform.evaluations.access.load_session_principal", loader)
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    for path in ("/experiments", "/runs"):
        assert client.get(BASE + path).status_code == 403
