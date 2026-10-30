"""KAN-200 문장별 검증 계약. 합성 초안·근거와 fake 공급자만 사용한다."""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.api.cs.analysis import service
from backend.engine.cs.drafting import DraftOutput, EvidenceSnippet
from backend.engine.cs.verification import build_verification_input, instruction, validate_output
from backend.engine.cs.verification_call import run_verification_attempt
from backend.shared.ai import Generation, Usage
from tests.cs.graph_fakes import draft_provider, verification_provider


def payload():
    draft = DraftOutput.model_validate_json(
        draft_provider(
            "\n\n입력:\n" + json.dumps({"evidence": [{"chunk_id": "c1"}], "request_info_codes": []})
        ).text
    )
    evidence = [
        EvidenceSnippet(
            chunk_id="c1", snippet="Synthetic answer.", section_path=[], document_sha256="a" * 64
        )
    ]
    return build_verification_input(draft, evidence)


def valid_raw():
    return verification_provider("\n\n입력:\n" + payload().model_dump_json()).text


def test_input_selects_only_cited_evidence_and_no_ticket_fields():
    item = payload()
    assert set(item.model_dump()) == {"sentences", "evidence"}
    assert len(item.sentences) == 3 and len(item.evidence) == 1
    draft = DraftOutput.model_validate_json(
        draft_provider(
            "\n\n입력:\n" + json.dumps({"evidence": [{"chunk_id": "c1"}], "request_info_codes": []})
        ).text
    )
    extra = item.evidence[0].model_copy(update={"chunk_id": "unused"})
    assert build_verification_input(draft, [*item.evidence, extra]) == item
    with pytest.raises(ValueError, match="CS_UNKNOWN_REFERENCE"):
        build_verification_input(draft, [])
    blocked = item.evidence[0].model_copy(update={"snippet": "FLT-042"})
    with pytest.raises(ValueError, match="CS_BLOCKED_EVIDENCE"):
        build_verification_input(draft, [blocked])


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "CS_VERIFICATION_SENTENCE_MISMATCH"),
        ("duplicate", "CS_VERIFICATION_SENTENCE_MISMATCH"),
        ("unknown_sentence", "CS_VERIFICATION_SENTENCE_MISMATCH"),
        ("unknown_ref", "CS_UNKNOWN_REFERENCE"),
        ("no_ref", "CS_EVIDENCE_REQUIRED"),
        ("duplicate_ref", "CS_UNKNOWN_REFERENCE"),
        ("blocked", "CS_BLOCKED_EVIDENCE"),
    ],
)
def test_output_requires_exact_sentences_and_their_cited_evidence(change, code):
    data = json.loads(valid_raw())
    rows = data["sentences"]
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "unknown_sentence":
        rows[0]["sentence_index"] = 999
    elif change == "unknown_ref":
        rows[0]["evidence_chunk_ids"] = ["not-cited"]
    elif change == "no_ref":
        rows[0]["evidence_chunk_ids"] = []
    elif change == "duplicate_ref":
        rows[0]["evidence_chunk_ids"] *= 2
    else:
        rows[0]["reason"] = "FLT-042"
    with pytest.raises(ValueError, match=f"^{code}$"):
        validate_output(json.dumps(data), payload=payload())


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"sentences": [], "sentences": []}',
        '{"sentences": []}',
        '{"sentences": [], "extra": 1}',
    ],
)
def test_invalid_schema_and_duplicate_json_keys_are_not_valid_verdicts(raw):
    result = run_verification_attempt(
        payload(), provider=lambda _: Generation(raw, Usage(1, 2), "fake", []), attempt_number=1
    )
    assert result.outcome == "SCHEMA_VIOLATION"
    assert result.usage.input_tokens == 1
    assert all(s.verdict == "UNVERIFIABLE" for s in result.output.sentences)


def test_attempt_preserves_usage_and_fingerprints_and_calls_once():
    calls = []

    def provider(text):
        calls.append(text)
        return Generation(
            valid_raw(), Usage(10, 5, 2, 1, 17), "fake-v1", ["STOP"], model="fake", attempts=1
        )

    attempt = run_verification_attempt(
        payload(), provider=provider, attempt_number=1, draft_id="draft-1"
    )
    assert attempt.outcome == "VALID" and len(calls) == 1
    assert attempt.draft_id == "draft-1" and attempt.call_attempts == 1
    assert attempt.usage.total_tokens == 17
    assert attempt.input_sha256 == hashlib.sha256(payload().model_dump_json().encode()).hexdigest()
    assert (
        attempt.instruction_fingerprint == hashlib.sha256(instruction().encode()).hexdigest()[:16]
    )


@pytest.mark.parametrize(
    "error,outcome,code",
    [
        (TimeoutError("private"), "TIMEOUT", "CS_PROVIDER_TIMEOUT"),
        (RuntimeError("private"), "TRANSPORT_FAILURE", "CS_PROVIDER_ERROR"),
    ],
)
def test_provider_failure_is_unverified_without_retry_or_private_error(error, outcome, code):
    calls = []

    def provider(text):
        calls.append(text)
        raise error

    attempt = run_verification_attempt(payload(), provider=provider, attempt_number=1)
    assert (attempt.outcome, attempt.error_code) == (outcome, code)
    assert len(calls) == 1 and attempt.execution_uncertain
    assert {s.reason for s in attempt.output.sentences} == {code}
    assert "private" not in repr(attempt)


def test_storage_checks_the_draft_ticket_job_and_sentence_mapping(monkeypatch):
    ticket, draft_id, job = uuid4(), uuid4(), uuid4()
    parsed = payload()
    # Reconstruct the draft used by the input fixture.
    draft = DraftOutput.model_validate_json(
        draft_provider(
            "\n\n입력:\n" + json.dumps({"evidence": [{"chunk_id": "c1"}], "request_info_codes": []})
        ).text
    )
    get = AsyncMock(
        return_value={
            "id": draft_id,
            "job_id": job,
            "outcome": "VALID",
            "draft": draft.model_dump(),
        }
    )
    insert = AsyncMock(return_value={"id": uuid4()})
    monkeypatch.setattr(service.repository, "get_draft", get)
    monkeypatch.setattr(service.repository, "insert_verification", insert)
    values = {
        "draft_id": draft_id,
        "job_id": job,
        "sentences": json.loads(valid_raw())["sentences"],
    }
    asyncio.run(service.record_verification(None, ticket, values))
    insert.assert_awaited_once()
    with pytest.raises(ValueError, match="CS_VERIFICATION_DRAFT_MISMATCH"):
        asyncio.run(service.record_verification(None, ticket, {**values, "job_id": uuid4()}))
    get.return_value = None
    with pytest.raises(ValueError, match="CS_VERIFICATION_DRAFT_MISMATCH"):
        asyncio.run(service.record_verification(None, ticket, values))
    assert len(parsed.sentences) == 3


def test_api_view_distinguishes_history_failures_and_supported_results():
    draft_id = uuid4()
    draft = {
        "id": draft_id,
        "seq": 1,
        "analysis_id": None,
        "job_id": None,
        "outcome": "VALID",
        "draft": {},
        "input_version": "v1",
        "model_version": "fake",
        "prompt_version": "v1",
        "error_code": None,
        "provenance_kind": "ai_draft",
        "created_at": datetime.now(UTC),
    }
    assert service.verified_draft(draft, []).verification_status == "NOT_VERIFIED"
    attempt = run_verification_attempt(payload(), provider=verification_provider, attempt_number=1)
    row = {
        "id": uuid4(),
        "draft_id": draft_id,
        "job_id": None,
        "attempt_number": 1,
        "outcome": "VALID",
        "sentences": attempt.output.model_dump()["sentences"],
        "input_version": attempt.input_version,
        "model_version": "fake",
        "prompt_version": "v1",
        "error_code": None,
        "created_at": datetime.now(UTC),
    }
    assert service.verified_draft(draft, [row]).verification_status == "VERIFIED"
    failed = replace(attempt, prediction=None, outcome="TIMEOUT", error_code="CS_PROVIDER_TIMEOUT")
    failure = {
        **row,
        "outcome": failed.outcome,
        "error_code": failed.error_code,
        "sentences": failed.output.model_dump()["sentences"],
    }
    view = service.verified_draft(draft, [row, failure])
    assert view.verification_status == "NOT_VERIFIED" and len(view.verifications) == 2
    unsupported = {**row, "sentences": [{**s, "verdict": "UNSUPPORTED"} for s in row["sentences"]]}
    assert service.verified_draft(draft, [unsupported]).verification_status == "NEEDS_REVIEW"
