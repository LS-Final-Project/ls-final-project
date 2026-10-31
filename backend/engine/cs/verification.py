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

VERIFICATION_INPUT_VERSION = "cs-verification-input-v2"
NO_EVIDENCE_SENTENCES = "CS_NO_EVIDENCE_SENTENCES"
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
    """EVIDENCE 문장과 그 문장이 인용한 근거만 선택한다. 문의 원문은 추가하지 않는다."""
    every = [
        VerificationSentence(
            label=option.label,
            sentence_index=index,
            **sentence.model_dump(),
        )
        for option in draft.options
        for index, sentence in enumerate(option.sentences)
    ]
    # 조건·미확인 문장은 근거를 달지 않아 판정할 수 없다. 참조·차단어 검사는 전부에 한다.
    sentences = [s for s in every if s.kind == "EVIDENCE"]
    known = {e.chunk_id for e in evidence}
    if len(known) != len(evidence) or not {r for s in every for r in s.evidence_chunk_ids} <= known:
        raise ValueError("CS_UNKNOWN_REFERENCE")
    cited = {ref for sentence in sentences for ref in sentence.evidence_chunk_ids}
    selected = [e for e in evidence if e.chunk_id in cited]
    if any(
        mentions_code(text, code)
        for text in [
            *(s.text for s in every),
            *(t for e in selected for t in (e.snippet, *e.section_path)),
        ]
        for code in BLOCKED_CODES
    ):
        raise ValueError("CS_BLOCKED_EVIDENCE")
    if not sentences:
        raise ValueError(NO_EVIDENCE_SENTENCES)
    return VerificationInput(sentences=sentences, evidence=selected)


def case_status(output: VerificationOutput) -> VerificationStatus:
    """모든 EVIDENCE 문장이 지지돼야 VERIFIED다.

    Args:
        output: 유효한 검증 결과.

    Returns:
        VerificationStatus: VERIFIED 또는 NEEDS_REVIEW.
    """
    good = all(s.verdict == "SUPPORTED" for s in output.sentences)
    return "VERIFIED" if good else "NEEDS_REVIEW"


def rewrite_targets(output: VerificationOutput) -> list[SentenceVerdict]:
    """재작성할 문장. UNVERIFIABLE은 고쳐도 근거가 늘지 않아 표시만 한다.

    Args:
        output: 유효한 검증 결과.

    Returns:
        list[SentenceVerdict]: UNSUPPORTED 문장.
    """
    return [s for s in output.sentences if s.verdict == "UNSUPPORTED"]


_SCHEMA = json.dumps(VerificationOutput.model_json_schema(), ensure_ascii=False, sort_keys=True)


def instruction(prompt_version: str = "v1") -> str:
    return get_prompt("verification", prompt_version) + _SCHEMA


def render_prompt(payload: VerificationInput, *, prompt_version: str = "v1") -> str:
    return instruction(prompt_version) + "\n\n입력:\n" + payload.model_dump_json()


def validate_output(raw: str, *, payload: VerificationInput) -> VerificationOutput:
    """모든 EVIDENCE 문장을 정확히 한 번 판정하고 해당 문장이 인용한 근거만 참조하도록 검사한다."""
    try:
        output = VerificationOutput.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except (ValueError, ValidationError, RecursionError):
        raise ValueError("CS_INVALID_VERIFICATION_OUTPUT") from None
    # 저장 경로는 초안 전체 문장으로 payload를 만든다. 판정 대상은 EVIDENCE뿐이다.
    expected = {
        (s.label, s.sentence_index): set(s.evidence_chunk_ids)
        for s in payload.sentences
        if s.kind == "EVIDENCE"
    }
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
