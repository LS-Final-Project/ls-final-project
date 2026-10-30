"""KAN-201 E1 가짜 트랜잭션: finish 원자성·안전 상호배제·RFP 접수자 범위를 확인한다."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.agent import cs_worker, job_runtime
from backend.agent.job_runtime import Lease, LeaseLost


def lease(kind="rfp.process"):
    return Lease(uuid4(), kind, uuid4(), "a" * 64, uuid4(), uuid4(), 1)


class Connection:
    def __init__(self, owner=None):
        self.owner = owner
        self.writes = []
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @contextmanager
    def transaction(self):
        snapshot = list(self.writes)
        try:
            yield
        except Exception:
            self.writes = snapshot
            self.rolled_back = True
            raise

    def execute(self, sql, params=()):
        if sql.startswith("SELECT responsible"):
            return SimpleNamespace(fetchone=lambda: {"responsible_user_id": self.owner})
        if sql.startswith("SELECT owner"):
            return SimpleNamespace(fetchone=lambda: {"owner_user_id": self.owner})
        self.writes.append((sql, params))
        return SimpleNamespace(rowcount=1)


def setup(monkeypatch, owner=None):
    conn = Connection(owner)
    monkeypatch.setattr(job_runtime, "connect", lambda _: conn)
    monkeypatch.setattr(job_runtime, "held", lambda *_: False)
    monkeypatch.setattr(job_runtime, "append", lambda c, *args: c.writes.append(("event", args)))
    return conn


@pytest.mark.parametrize("kind", ["rfp.process", "cs.analyze"])
@pytest.mark.parametrize("failure", ["missing_table", "insert_error", "lookup_error"])
def test_notification_savepoint_failure_preserves_succeeded_job_and_event(
    monkeypatch, caplog, kind, failure
):
    import psycopg

    conn = setup(monkeypatch)
    current = lease(kind)
    execute = conn.execute

    def broken(sql, params=()):
        if failure == "lookup_error" and sql.startswith("SELECT"):
            raise psycopg.errors.InsufficientPrivilege("private owner lookup failure")
        if sql.startswith("INSERT INTO notify.notification_intents"):
            if failure == "missing_table":
                raise psycopg.errors.UndefinedTable("private missing table")
            conn.writes.append(("notification_partial", params))
            raise RuntimeError("private insert failure")
        return execute(sql, params)

    conn.execute = broken
    job_runtime.finish(
        None,
        current,
        "SUCCEEDED",
        {},
        notification_kind="cs.review_wait" if kind == "cs.analyze" else None,
    )
    assert conn.rolled_back
    assert (
        "UPDATE runtime.jobs SET status=%s WHERE id=%s",
        ("SUCCEEDED", current.job_id),
    ) in conn.writes
    assert ("event", (current.job_id, "job.completed", {"state": "SUCCEEDED"})) in conn.writes
    assert not any(
        sql.startswith("INSERT") or sql == "notification_partial" for sql, _ in conn.writes
    )
    assert [record.getMessage() for record in caplog.records] == ["NOTIFICATION_INTENT_FAILED"]
    assert "private" not in caplog.text


@pytest.mark.parametrize("failure", ["job_update", "terminal_event"])
def test_primary_job_errors_still_propagate_and_roll_back(monkeypatch, caplog, failure):
    conn = setup(monkeypatch)
    if failure == "job_update":
        execute = conn.execute

        def broken(sql, params=()):
            if sql.startswith("UPDATE runtime.jobs"):
                raise RuntimeError("primary job failure")
            return execute(sql, params)

        conn.execute = broken
    else:

        def broken_event(*args):
            raise RuntimeError("primary event failure")

        monkeypatch.setattr(job_runtime, "append", broken_event)
    with pytest.raises(RuntimeError, match="primary"):
        job_runtime.finish(None, lease(), "SUCCEEDED", {})
    assert conn.rolled_back and conn.writes == []
    assert not caplog.records


@pytest.mark.parametrize("state", ["SUCCEEDED", "FAILED"])
def test_rfp_includes_initiator_as_recipient_without_actor_exclusion(monkeypatch, state):
    current = lease()
    conn = setup(monkeypatch, owner=current.actor_id)
    # Real helper collapses the two identical user scopes before INSERT.
    job_runtime.finish(None, current, state, {"needs_review": True})
    insert = [
        p for sql, p in conn.writes if sql.startswith("INSERT INTO notify.notification_intents")
    ]
    assert len(insert) == 1
    assert insert[0][5].obj == [{"user": str(current.actor_id)}]
    assert insert[0][6] is None
    assert insert[0][2] == ("rfp.done" if state == "SUCCEEDED" else "rfp.failed")


@pytest.mark.parametrize("kind", ["cs.review_wait", "cs.safety"])
@pytest.mark.parametrize("has_owner", [True, False])
def test_cs_one_intent_and_owner_fallback_and_safety_expansion(monkeypatch, kind, has_owner):
    current = lease("cs.analyze")
    owner = uuid4() if has_owner else None
    conn = setup(monkeypatch, owner)
    job_runtime.finish(None, current, "SUCCEEDED", {}, notification_kind=kind)
    insert = [
        p for sql, p in conn.writes if sql.startswith("INSERT INTO notify.notification_intents")
    ]
    assert len(insert) == 1 and insert[0][2] == kind
    audience = insert[0][5].obj
    if has_owner:
        assert {"user": str(owner)} in audience
    if kind == "cs.safety":
        assert {"role": "cs_agent"} in audience and {"manager_dept": "GCS_L1"} in audience
        assert insert[0][-1] == f"cs.safety:{current.subject_id}"
    elif not has_owner:
        assert audience == [{"role": "cs_agent"}]


def test_cancelled_or_lost_lease_never_enqueues(monkeypatch):
    conn = setup(monkeypatch)
    job_runtime.finish(None, lease(), "CANCELLED", {})
    assert not any(sql.startswith("INSERT") for sql, _ in conn.writes)
    writer = Mock()
    monkeypatch.setattr(job_runtime, "enqueue_notification_sync", writer)

    def lost(*_):
        raise LeaseLost

    monkeypatch.setattr(job_runtime, "held", lost)
    with pytest.raises(LeaseLost):
        job_runtime.finish(None, lease(), "SUCCEEDED", {})
    writer.assert_not_called()


@pytest.mark.parametrize(
    "next_nodes,urgency,expected",
    [
        (("human_review",), "SAFETY", "cs.safety"),
        (("human_review",), "NONE", "cs.review_wait"),
        ((), None, None),
        (("reverify",), "SAFETY", None),
    ],
)
def test_final_checkpoint_boundary_includes_safety_but_excludes_classification_end(
    next_nodes, urgency, expected
):
    # get_state reads the actual final checkpoint, including resumed and rewritten runs.
    graph = SimpleNamespace(
        get_state=lambda _: SimpleNamespace(
            next=next_nodes,
            values={"classification": {"urgency_flag": urgency} if urgency else None},
        )
    )
    assert cs_worker.review_notification(graph, lease("cs.analyze")) == expected


def test_real_graph_safety_after_rewrite_and_resume_and_failed_classification_boundary():
    import json

    from langgraph.checkpoint.memory import InMemorySaver

    from backend.engine.cs.graph import initial_state, thread_config
    from backend.shared.ai import Generation, Usage
    from tests.cs.graph_fakes import classification_provider, verification_provider
    from tests.cs.test_graph import make

    current = lease("cs.analyze")
    config = thread_config(str(current.job_id))

    def safety(payload):
        generation = classification_provider(payload)
        body = json.loads(generation.text)
        body["urgency_flag"] = "SAFETY"
        return Generation(json.dumps(body), generation.usage, "fake", [])

    checks = []

    def verify(text):
        checks.append(text)
        generation = verification_provider(text)
        if len(checks) == 1:
            body = json.loads(generation.text)
            for sentence in body["sentences"]:
                sentence["verdict"] = "UNSUPPORTED"
            return Generation(json.dumps(body), generation.usage, "fake", [])
        return generation

    graph, _ = make(InMemorySaver(), classification_provider=safety, verification_provider=verify)
    graph.invoke(initial_state("t-1"), config)
    assert len(checks) == 2
    assert cs_worker.review_notification(graph, current) == "cs.safety"
    # A worker restart sees the same final checkpoint without invoking the model again.
    assert cs_worker.run_graph(graph, current, lambda _: True)
    assert cs_worker.review_notification(graph, current) == "cs.safety" and len(checks) == 2
    failed, _ = make(
        InMemorySaver(),
        classification_provider=lambda _: Generation("bad JSON", Usage(), "fake", []),
    )
    failed.invoke(initial_state("t-1"), config)
    assert failed.get_state(config).next == ()
    assert cs_worker.review_notification(failed, current) is None
