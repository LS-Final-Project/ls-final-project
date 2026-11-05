"""초안 문장과 인용 근거의 지지 여부를 검사한다(KAN-200). 모델 호출은 하지 않는다.

지시문 v2(KAN-161)는 EVIDENCE가 아닌 문장까지 모두 받아 문장마다 확정 약속(firm_commitment)을 표시한다. 금지
약속 정규식(drafting.find_forbidden_commitment)은 영어·한국어만 잡아서, 고객 언어로 쓴 초안을 언어와 관계없이
거르려고 더했다. 표시된 문장은 normalize 단계에서 UNSUPPORTED·FORBIDDEN_REASON 판정으로 바꾼다. 그래서 상태
계산·재작성·저장·조회는 v1과 같은 VerificationOutput 모양을 그대로 쓴다.
"""

import json
from typing import Any, Literal

from pydantic import Field, ValidationError

from backend.engine.cs.classification import _unique_object
from backend.engine.cs.drafting import (
    FORBIDDEN_REASON,
    DraftOutput,
    EvidenceSnippet,
    OptionLabel,
    SentenceKind,
    _Strict,
)
from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS, get_prompt
from backend.engine.search.hybrid import BLOCKED_CODES, mentions_code

VERIFICATION_INPUT_VERSION = "cs-verification-input-v2"
# v2 지시문 입력은 EVIDENCE가 아닌 문장도 담는다(확정 약속 표시용).
VERIFICATION_INPUT_VERSION_ALL = "cs-verification-input-v3"
# 문장마다 확정 약속을 표시하는 지시문 판본.
COMMITMENT_VERSIONS = frozenset({"v2"})
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


class SentenceCheck(SentenceVerdict):
    """v2 원답 문장. firm_commitment는 승인·확정을 이미 된 것처럼 단정한 문장이다(언어 무관)."""

    firm_commitment: bool


class CommitmentCheckOutput(_Strict):
    """v2 원답. 입력 문장 전부(EVIDENCE 아닌 문장 포함)를 한 번씩 담는다. 저장 전에 normalize로 바꾼다."""

    sentences: list[SentenceCheck] = Field(min_length=1)


def output_model(prompt_version: str = DEFAULT_VERSIONS["verification"]) -> type[_Strict]:
    """공급자 응답 스키마(Gemini response_schema)로 쓸 원답 모델."""
    return CommitmentCheckOutput if prompt_version in COMMITMENT_VERSIONS else VerificationOutput


def input_version(prompt_version: str = DEFAULT_VERSIONS["verification"]) -> str:
    return (
        VERIFICATION_INPUT_VERSION_ALL
        if prompt_version in COMMITMENT_VERSIONS
        else VERIFICATION_INPUT_VERSION
    )


def build_verification_input(
    draft: DraftOutput,
    evidence: list[EvidenceSnippet],
    *,
    prompt_version: str = DEFAULT_VERSIONS["verification"],
) -> VerificationInput:
    """EVIDENCE 문장과 그 문장이 인용한 근거만 선택한다. 문의 원문은 추가하지 않는다.

    확정 약속을 표시하는 판본(v2)은 EVIDENCE가 아닌 문장도 넣는다. 근거는 그대로 EVIDENCE 문장이 인용한 것만이다.
    """
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
    if prompt_version in COMMITMENT_VERSIONS:
        sentences = every
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


_SCHEMA_V2 = json.dumps(
    CommitmentCheckOutput.model_json_schema(), ensure_ascii=False, sort_keys=True
)


def instruction(prompt_version: str = DEFAULT_VERSIONS["verification"]) -> str:
    schema = _SCHEMA_V2 if prompt_version in COMMITMENT_VERSIONS else _SCHEMA
    return get_prompt("verification", prompt_version) + schema


def render_prompt(
    payload: VerificationInput, *, prompt_version: str = DEFAULT_VERSIONS["verification"]
) -> str:
    return instruction(prompt_version) + "\n\n입력:\n" + payload.model_dump_json()


def _load(raw: str, model: type[_Strict]) -> Any:
    try:
        return model.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
    except (ValueError, ValidationError, RecursionError):
        raise ValueError("CS_INVALID_VERIFICATION_OUTPUT") from None


def normalize(checked: CommitmentCheckOutput, payload: VerificationInput) -> VerificationOutput:
    """v2 원답을 저장 모양으로 바꾼다. 확정 약속 문장은 UNSUPPORTED·FORBIDDEN_REASON이고, EVIDENCE가 아닌
    문장은 확정 약속일 때만 남긴다. UNSUPPORTED라 재작성 대상이 되고 VERIFIED가 될 수 없다(KAN-161)."""
    kinds = {(s.label, s.sentence_index): s.kind for s in payload.sentences}
    sentences = []
    for s in checked.sentences:
        evidence = kinds[(s.label, s.sentence_index)] == "EVIDENCE"
        if s.firm_commitment:
            sentences.append(
                SentenceVerdict(
                    label=s.label,
                    sentence_index=s.sentence_index,
                    verdict="UNSUPPORTED",
                    reason=FORBIDDEN_REASON,
                    evidence_chunk_ids=s.evidence_chunk_ids if evidence else [],
                )
            )
        elif evidence:
            sentences.append(SentenceVerdict(**s.model_dump(exclude={"firm_commitment"})))
    return VerificationOutput(sentences=sentences)


def commitment_flags(output: VerificationOutput) -> list[SentenceVerdict]:
    """확정 약속으로 표시된 문장(normalize가 남긴 FORBIDDEN_REASON 판정)."""
    return [s for s in output.sentences if s.reason == FORBIDDEN_REASON]


def validate_output(
    raw: str, *, payload: VerificationInput, prompt_version: str = "v1"
) -> VerificationOutput:
    """모든 EVIDENCE 문장을 정확히 한 번 판정하고 해당 문장이 인용한 근거만 참조하도록 검사한다.

    prompt_version 기본값 v1은 저장 모양(VerificationOutput) 검사다. 저장 경로(service.record_verification)는
    판본과 관계없이 이 모양을 다시 검사한다. EVIDENCE가 아닌 문장의 판정은 normalize가 남긴 확정 약속
    (UNSUPPORTED·FORBIDDEN_REASON·근거 없음)만 허용한다. v2는 원답을 검사한 뒤 normalize 결과를 낸다.
    """
    checks = prompt_version in COMMITMENT_VERSIONS
    output = _load(raw, CommitmentCheckOutput if checks else VerificationOutput)
    kinds = {(s.label, s.sentence_index): s.kind for s in payload.sentences}
    # 저장 경로는 초안 전체 문장으로 payload를 만든다. 판정 대상은 EVIDENCE뿐이다.
    expected = {
        (s.label, s.sentence_index): set(s.evidence_chunk_ids)
        for s in payload.sentences
        if s.kind == "EVIDENCE"
    }
    keys = [(s.label, s.sentence_index) for s in output.sentences]
    flagged = {
        (s.label, s.sentence_index)
        for s in output.sentences
        if s.verdict == "UNSUPPORTED" and s.reason == FORBIDDEN_REASON and not s.evidence_chunk_ids
    }
    extra = set(keys) - set(expected)
    if (
        len(set(keys)) != len(keys)
        or not set(expected) <= set(keys)
        or not extra <= set(kinds)
        or (set(keys) != set(kinds) if checks else not extra <= flagged)
    ):
        raise ValueError("CS_VERIFICATION_SENTENCE_MISMATCH")
    for sentence in output.sentences:
        if any(mentions_code(sentence.reason, code) for code in BLOCKED_CODES):
            raise ValueError("CS_BLOCKED_EVIDENCE")
        if (sentence.label, sentence.sentence_index) not in expected:
            # EVIDENCE가 아닌 문장의 판정·근거는 쓰지 않는다(v2는 firm_commitment만 본다).
            continue
        refs = sentence.evidence_chunk_ids
        if (
            len(set(refs)) != len(refs)
            or not set(refs) <= expected[(sentence.label, sentence.sentence_index)]
        ):
            raise ValueError("CS_UNKNOWN_REFERENCE")
        if sentence.verdict == "SUPPORTED" and not refs:
            raise ValueError("CS_EVIDENCE_REQUIRED")
    return normalize(output, payload) if checks else output


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
            if s.kind == "EVIDENCE"
        ]
    )
