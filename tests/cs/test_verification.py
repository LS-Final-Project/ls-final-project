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
from backend.engine.cs.verification import (
    NO_EVIDENCE_SENTENCES,
    VerificationInput,
    VerificationOutput,
    VerificationSentence,
    build_verification_input,
    case_status,
    instruction,
    rewrite_targets,
    validate_output,
)
from backend.engine.cs.verification_call import run_verification_attempt
from backend.engine.search.hybrid import BLOCKED_CODES
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


EVIDENCE = [
    EvidenceSnippet(
        chunk_id="c1", snippet="Synthetic answer.", section_path=[], document_sha256="a" * 64
    )
]


def mixed_draft(*kinds):
    """첫 선택지 앞에 비근거 문장을 끼운 합성 초안. 문장 번호가 건너뛰는지 본다."""
    data = json.loads(
        draft_provider(
            "\n\n입력:\n" + json.dumps({"evidence": [{"chunk_id": "c1"}], "request_info_codes": []})
        ).text
    )
    extra = [{"text": "합성 조건 문장", "kind": kind, "evidence_chunk_ids": []} for kind in kinds]
    data["options"][0]["sentences"] = [*extra, *data["options"][0]["sentences"]]
    return DraftOutput.model_validate(data)


def test_only_evidence_sentences_are_sent_and_keep_their_index():
    draft = mixed_draft("CONDITION", "UNCONFIRMED")
    item = build_verification_input(draft, EVIDENCE)
    assert {s.kind for s in item.sentences} == {"EVIDENCE"}
    first = draft.options[0].label
    assert [s.sentence_index for s in item.sentences if s.label == first] == [2]


def test_a_draft_without_evidence_sentences_has_nothing_to_verify():
    draft = mixed_draft("CONDITION")
    for option in draft.options:
        option.sentences[:] = [s for s in option.sentences if s.kind != "EVIDENCE"]
    data = draft.model_dump()
    for option in data["options"]:
        option["sentences"] = option["sentences"] or [
            {"text": "합성 조건 문장", "kind": "CONDITION", "evidence_chunk_ids": []}
        ]
    with pytest.raises(ValueError, match=NO_EVIDENCE_SENTENCES):
        build_verification_input(DraftOutput.model_validate(data), EVIDENCE)


def test_excluded_sentences_are_still_checked_for_references_and_blocked_codes():
    draft = mixed_draft("CONDITION").model_dump()
    draft["options"][0]["sentences"][0]["evidence_chunk_ids"] = ["missing"]
    with pytest.raises(ValueError, match="CS_UNKNOWN_REFERENCE"):
        build_verification_input(DraftOutput.model_validate(draft), EVIDENCE)
    draft = mixed_draft("CONDITION").model_dump()
    draft["options"][0]["sentences"][0]["text"] = f"합성 {sorted(BLOCKED_CODES)[0]} 문장"
    with pytest.raises(ValueError, match="CS_BLOCKED_EVIDENCE"):
        build_verification_input(DraftOutput.model_validate(draft), EVIDENCE)


def test_stored_path_payload_of_every_sentence_expects_only_evidence_verdicts():
    """저장 경로(service.record_verification)는 초안 전체 문장으로 payload를 만든다."""
    draft = mixed_draft("CONDITION")
    every = VerificationInput(
        sentences=[
            VerificationSentence(label=o.label, sentence_index=i, **s.model_dump())
            for o in draft.options
            for i, s in enumerate(o.sentences)
        ],
        evidence=[],
    )
    narrow = build_verification_input(draft, EVIDENCE)
    raw = verification_provider("\n\n입력:\n" + narrow.model_dump_json()).text
    assert validate_output(raw, payload=every).sentences
    data = json.loads(raw)
    data["sentences"].append(
        {
            "label": draft.options[0].label,
            "sentence_index": 0,
            "verdict": "UNVERIFIABLE",
            "reason": "합성",
            "evidence_chunk_ids": [],
        }
    )
    with pytest.raises(ValueError, match="CS_VERIFICATION_SENTENCE_MISMATCH"):
        validate_output(json.dumps(data), payload=every)


@pytest.mark.parametrize(
    ("verdicts", "status", "rewrite"),
    [
        (["SUPPORTED", "SUPPORTED"], "VERIFIED", 0),
        (["SUPPORTED", "UNVERIFIABLE"], "NEEDS_REVIEW", 0),
        (["UNSUPPORTED", "UNVERIFIABLE"], "NEEDS_REVIEW", 1),
    ],
)
def test_case_status_and_rewrite_follow_the_verdicts(verdicts, status, rewrite):
    output = VerificationOutput(
        sentences=[
            {
                "label": "INFO_FIRST",
                "sentence_index": index,
                "verdict": verdict,
                "reason": "합성",
                "evidence_chunk_ids": ["c1"] if verdict == "SUPPORTED" else [],
            }
            for index, verdict in enumerate(verdicts)
        ]
    )
    assert case_status(output) == status
    assert len(rewrite_targets(output)) == rewrite
