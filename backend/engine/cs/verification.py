"""초안 문장과 인용 근거의 지지 여부를 검사한다(KAN-200). 모델 호출은 하지 않는다."""

import json
from typing import Literal

from pydantic import Field, ValidationError

from backend.engine.cs.classification import _unique_object
from backend.engine.cs.drafting import (
    DraftOutput,
    EvidenceSnippet,
    OptionLabel,
    SentenceKind,
    _Strict,
)
from backend.engine.cs.prompt_registry import get_prompt
from backend.engine.search.hybrid import BLOCKED_CODES, mentions_code

VERIFICATION_INPUT_VERSION = "cs-verification-input-v1"
Verdict = Literal["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]
VerificationStatus = Literal["NOT_VERIFIED", "VERIFIED", "NEEDS_REVIEW"]


class VerificationSentence(_Strict):
    label: OptionLabel
    sentence_index: int = Field(ge=0)
    text: str = Field(pattern=r"\S")
    kind: SentenceKind
    evidence_chunk_ids: list[str]


class VerificationInput(_Strict):
    sentences: list[VerificationSentence] = Field(min_length=1)
    evidence: list[EvidenceSnippet]


class SentenceVerdict(_Strict):
    label: OptionLabel
    sentence_index: int = Field(ge=0)
    verdict: Verdict
    reason: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    evidence_chunk_ids: list[str]


class VerificationOutput(_Strict):
    sentences: list[SentenceVerdict] = Field(min_length=1)


def build_verification_input(
    draft: DraftOutput, evidence: list[EvidenceSnippet]
) -> VerificationInput:
    """각 선택지의 문장과 그 문장이 인용한 근거만 선택한다. 문의 원문은 추가하지 않는다."""
    sentences = [
        VerificationSentence(
            label=option.label,
            sentence_index=index,
            **sentence.model_dump(),
        )
        for option in draft.options
        for index, sentence in enumerate(option.sentences)
    ]
    cited = {ref for sentence in sentences for ref in sentence.evidence_chunk_ids}
    known = {e.chunk_id for e in evidence}
    if len(known) != len(evidence) or not cited <= known:
        raise ValueError("CS_UNKNOWN_REFERENCE")
    selected = [e for e in evidence if e.chunk_id in cited]
    if any(
        mentions_code(text, code)
        for text in [
            *(s.text for s in sentences),
            *(t for e in selected for t in (e.snippet, *e.section_path)),
        ]
        for code in BLOCKED_CODES
    ):
        raise ValueError("CS_BLOCKED_EVIDENCE")
    return VerificationInput(sentences=sentences, evidence=selected)


_SCHEMA = json.dumps(VerificationOutput.model_json_schema(), ensure_ascii=False, sort_keys=True)


def instruction(prompt_version: str = "v1") -> str:
    return get_prompt("verification", prompt_version) + _SCHEMA


def render_prompt(payload: VerificationInput, *, prompt_version: str = "v1") -> str:
    return instruction(prompt_version) + "\n\n입력:\n" + payload.model_dump_json()


def validate_output(raw: str, *, payload: VerificationInput) -> VerificationOutput:
    """모든 입력 문장을 정확히 한 번 판정하고 해당 문장이 인용한 근거만 참조하도록 검사한다."""
    try:
        output = VerificationOutput.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except (ValueError, ValidationError, RecursionError):
        raise ValueError("CS_INVALID_VERIFICATION_OUTPUT") from None
    expected = {(s.label, s.sentence_index): set(s.evidence_chunk_ids) for s in payload.sentences}
    keys = [(s.label, s.sentence_index) for s in output.sentences]
    if len(set(keys)) != len(keys) or set(keys) != set(expected):
        raise ValueError("CS_VERIFICATION_SENTENCE_MISMATCH")
    for sentence in output.sentences:
        refs = sentence.evidence_chunk_ids
        if (
            len(set(refs)) != len(refs)
            or not set(refs) <= expected[(sentence.label, sentence.sentence_index)]
        ):
            raise ValueError("CS_UNKNOWN_REFERENCE")
        if sentence.verdict == "SUPPORTED" and not refs:
            raise ValueError("CS_EVIDENCE_REQUIRED")
        if any(mentions_code(sentence.reason, code) for code in BLOCKED_CODES):
            raise ValueError("CS_BLOCKED_EVIDENCE")
    return output


def unverified_output(payload: VerificationInput, reason: str) -> VerificationOutput:
    """모델 판정이 없는 문장은 미검증 사유로 표시한다. 호출 outcome과 함께 읽는다."""
    return VerificationOutput(
        sentences=[
            SentenceVerdict(
                label=s.label,
                sentence_index=s.sentence_index,
                verdict="UNVERIFIABLE",
                reason=reason,
                evidence_chunk_ids=[],
            )
            for s in payload.sentences
        ]
    )
