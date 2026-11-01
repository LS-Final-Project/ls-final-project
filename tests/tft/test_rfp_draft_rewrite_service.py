"""실제 호출 없이 fake DB 경계로 잠금/재시도/CAS 및 사용량 기록을 검증한다."""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.agent.processor_registry import TftJob
from backend.api.tft.rfp import draft_rewrite as service
from backend.engine.rfp.draft_rewrite import fake_provider
from backend.shared.ai import Generation, Usage
from tests.tft.test_rfp_draft_rewrite import draft


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.in_transaction = False
        self.version = 1
        self.generated = None
        self.saved = None
        self.calls = []
        self.record = {
            "id": UUID(int=7),
            "actor_id": UUID(int=8),
            "job_id": None,
            "state": "reserved",
            "base_version": 1,
        }

    @asynccontextmanager
    async def transaction(self):
        assert not self.in_transaction
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    async def execute(self, sql, params):
        assert self.in_transaction
        if sql.startswith("SELECT * FROM tft.draft_rewrites"):
            return Cursor(self.record)
        if "SET state='discarded'" in sql:
            self.record["state"] = "discarded"
        if "SET state='saved'" in sql:
            self.record["state"] = "saved"
        if sql.startswith("SELECT EXISTS"):
            return Cursor(
                {
                    "fallback": any(
                        c[3] in ("skipped", "error", "truncated", "fallback") for c in self.calls
                    )
                }
            )
        if sql.startswith("INSERT INTO tft.draft_rewrite_calls"):
            self.calls.append(params)
        return Cursor(None)


def setup(monkeypatch):
    conn = Connection()
    source = draft()
    job = TftJob(
        job_id=UUID(int=9),
        kind="rfp.draft_rewrite",
        project_id=source.project_id,
        input_version="a" * 64,
        actor_id=UUID(int=8),
    )

    async def head(c, p):
        return conn.version

    async def generated(c, p, id):
        return conn.generated

    async def load(c, p, v):
        return (conn.saved if v == conn.generated else source).model_copy(deep=True)

    async def save(c, result, **kwargs):
        assert conn.in_transaction and result.version == conn.version + 1
        assert kwargs["source_job_id"] == job.job_id
        conn.saved = result.model_copy(deep=True)
        conn.version = result.version
        conn.generated = result.version

    monkeypatch.setattr(service.db, "lock", AsyncMock())
    monkeypatch.setattr(service.db, "head", head)
    monkeypatch.setattr(service.db, "generated", generated)
    monkeypatch.setattr(service.db, "load_snapshot", load)
    monkeypatch.setattr(service.db, "save", save)
    monkeypatch.setattr(service.db, "human_lineage", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(service.commands, "audit", AsyncMock())
    return conn, source, job


@pytest.mark.parametrize("raced", [False, True])
def test_provider_runs_outside_transaction_and_human_edit_wins_cas(monkeypatch, raced):
    conn, original, job = setup(monkeypatch)
    before = deepcopy(original)

    def provider(prompt):
        assert not conn.in_transaction
        if raced:
            conn.version = 2  # 모델 호출 동안 별도 사람이 저장한 판본
        return fake_provider(prompt)

    outcome = asyncio.run(service.process(conn, job, lambda _: None, provider, "gemini-3.6-flash"))
    assert outcome["counts"] == {"requirements": 0, "proposals": 0}
    assert conn.record["state"] == ("discarded" if raced else "saved")
    if raced:
        assert outcome["needs_review"] is True
        assert conn.saved is None and conn.version == 2
    else:
        assert (
            conn.version == 2 and conn.saved.sections[0].sentences[0].provenance_kind == "ai_draft"
        )
    assert conn.calls and conn.calls[0][5] == "rfp-draft-rewrite-v3"
    assert original == before


def test_retry_after_saved_or_stale_head_never_calls_provider(monkeypatch):
    conn, _, job = setup(monkeypatch)
    first = asyncio.run(
        service.process(conn, job, lambda _: None, fake_provider, "gemini-3.6-flash")
    )
    assert first["needs_review"] is False
    called = len(conn.calls)

    def forbidden(_):
        raise AssertionError("must not call")

    replayed = asyncio.run(
        service.process(conn, job, lambda _: None, forbidden, "gemini-3.6-flash")
    )
    assert replayed == first
    assert conn.version == 2 and len(conn.calls) == called
    conn.generated = None
    conn.record["state"] = "reserved"
    conn.version = 3
    asyncio.run(service.process(conn, job, lambda _: None, forbidden, "gemini-3.6-flash"))
    assert conn.record["state"] == "discarded" and conn.version == 3


def test_failure_changing_nothing_makes_no_new_version(monkeypatch):
    conn, source, job = setup(monkeypatch)

    def fails(_):
        raise TimeoutError("do not persist private text")

    result = asyncio.run(service.process(conn, job, lambda _: None, fails, "gemini-3.6-flash"))
    assert conn.saved is None and conn.record["state"] == "saved"
    assert result["needs_review"] is True and conn.version == 1
    assert conn.calls[0][3] == "error" and conn.calls[0][9] is None
    assert conn.calls[0][14] is None  # 공급자 미보고 비용을 0으로 꾸미지 않는다.
    assert "private" not in str(conn.calls)


def test_failure_reverting_an_earlier_model_sentence_is_a_new_version(monkeypatch):
    """앞선 모델 문장이 양식으로 돌아가면 바뀐 문장이 있으므로 판본을 만든다."""
    conn, source, job = setup(monkeypatch)
    row = source.sections[0].sentences[0]
    row.template_text, row.text, row.provenance_kind = row.text, "앞선 모델 문장", "ai_draft"

    def fails(_):
        raise TimeoutError("fake")

    asyncio.run(service.process(conn, job, lambda _: None, fails, "gemini-3.6-flash"))
    assert conn.version == 2 and conn.saved.sections[0].sentences[0].text == row.template_text


def test_trace_input_payload_is_identical_before_and_after_rewrite(monkeypatch):
    from backend.engine.rfp.draft_rewrite import rewrite
    from scripts import rfp_draft_trace_input as trace

    source = draft()
    result = rewrite(source, fake_provider).draft
    monkeypatch.setattr(
        trace, "valid_references", AsyncMock(return_value={("requirement", str(UUID(int=2)))})
    )

    class Rows:
        def __init__(self, values):
            self.values = values

        async def fetchall(self):
            return self.values

    class TraceConnection:
        def __init__(self, data, physical):
            self.data, self.physical = data, physical

        async def execute(self, sql, params):
            row = self.data.sections[0].sentences[0]
            if sql.startswith("SELECT s.id"):
                return Rows(
                    [
                        {
                            "id": self.physical,
                            "section": "OUR_RESPONSE",
                            "unconfirmed": row.unconfirmed,
                        }
                    ]
                )
            return Rows(
                [
                    {"sentence_id": self.physical, "evidence_type": e.type, "evidence_id": e.id}
                    for e in row.evidence
                ]
            )

    before = asyncio.run(
        trace.trace_input(TraceConnection(source, UUID(int=100)), source.project_id)
    )
    after = asyncio.run(
        trace.trace_input(TraceConnection(result, UUID(int=101)), source.project_id)
    )
    assert before == after


@pytest.mark.parametrize("kind", ["fallback", "static_flag"])
def test_saved_redelivery_preserves_original_review_summary_without_provider(monkeypatch, kind):
    conn, source, job = setup(monkeypatch)
    if kind == "static_flag":
        source.sections[0].sentences[0].needs_rereview = True

    def provider(prompt):
        if kind == "fallback":
            raise TimeoutError("fake")
        return fake_provider(prompt)

    first = asyncio.run(service.process(conn, job, lambda _: None, provider, "gemini-3.6-flash"))
    assert first["needs_review"] is True
    conn.version = 3  # 나중 판본이 생겨도 저장 당시 결과를 반환한다.

    def forbidden(_):
        pytest.fail("saved redelivery called provider")

    before = len(conn.calls)
    assert (
        asyncio.run(service.process(conn, job, lambda _: None, forbidden, "gemini-3.6-flash"))
        == first
    )
    assert len(conn.calls) == before


@pytest.mark.parametrize("mixed", [False, True])
def test_redelivery_matches_successful_retry_and_mixed_fallback(monkeypatch, mixed):
    conn, source, job = setup(monkeypatch)
    if mixed:
        other = source.sections[0].sentences[0].model_copy(deep=True)
        other.sentence_id = UUID(int=99)
        other.text = "내부 검토. 대표 항목: 「rated_current」."
        source.sections[0].sentences.append(other)  # 입력 차단 문장 + 성공 문장의 혼합
    attempts = 0

    def provider(prompt):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Generation(
                text=json.dumps([]),
                usage=Usage(1, 1, 0, 0, 2),
                model_version="fake",
                finish_reasons=["STOP"],
            )
        return fake_provider(prompt)

    first = asyncio.run(service.process(conn, job, lambda _: None, provider, "gemini-3.6-flash"))
    assert attempts == 2
    assert first["needs_review"] is mixed
    assert [c[3] for c in conn.calls if c[2] != 0] == ["retry", "passed"]

    def forbidden(_):
        pytest.fail("redelivery must not call provider")

    assert (
        asyncio.run(service.process(conn, job, lambda _: None, forbidden, "gemini-3.6-flash"))
        == first
    )
    assert attempts == 2
