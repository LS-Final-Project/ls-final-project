"""합성 snapshot과 fake 공급자만 사용한다. 네트워크/키/실제 모델을 호출하지 않는다."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.api.tft.rfp import schemas as s
from backend.engine.rfp.draft import DISCLAIMER
from backend.engine.rfp.draft_rewrite import fake_provider, rewrite
from backend.shared.ai import Generation, Usage


def draft() -> s.Draft:
    row = s.Sentence(
        sentence_id=UUID(int=3),
        text="제어반 사양 차이. 요구 1건. 대표 항목: 「정격 1,250 A, IEC 61439-1, 제품 QX-8」.",
        evidence=[s.Evidence(type="requirement", id=str(UUID(int=2)))],
        conditions=["주위온도: 35 °C", "고객 확인 필요"],
        unconfirmed=True,
        needs_rereview=False,
        evidence_missing=False,
        provenance_kind="system_record",
    )
    return s.Draft(
        project_id=UUID(int=1),
        version=1,
        data_origin="synthetic",
        derivation_kind="DERIVED",
        known_at=datetime(2026, 10, 31, tzinfo=UTC),
        sections=[s.Section(section="OUR_RESPONSE", sentences=[row])],
        disclaimer=DISCLAIMER,
        evidence_missing_count=0,
    )


def response(text, finish="STOP"):
    return Generation(
        text=json.dumps(text, ensure_ascii=False),
        usage=Usage(10, 20, 3, 0, 33),
        model_version="fake-v1",
        finish_reasons=[finish],
        model="fake",
        elapsed_ms=2,
    )


def mutate(change):
    def provider(prompt):
        result = fake_provider(prompt)
        rows = json.loads(result.text)
        for row in rows:
            row["text"] = change(row["text"])
        return response(rows)

    return provider


@pytest.mark.parametrize(
    "change",
    [
        lambda t: t.replace("1,250", "900"),
        lambda t: t + " " + str(UUID(int=2)),
        lambda t: t.replace("고객 확인 필요", ""),
        lambda t: t + " rated_current가 필요하다",
        lambda t: t + " EVIDENCE_FOUND",
        lambda t: t + " 공급 가능합니다",
        lambda t: t + " 충족합니다",
        lambda t: t + " 보장",
        lambda t: t + " 확약합니다",
        lambda t: t.replace("사양 차이", "검토"),
        lambda t: t.replace("IEC 61439-1", "IEC 61439-2"),
        lambda t: t.replace("QX-8", "QX-9"),
        lambda t: t.replace(" A", " kA"),
        lambda t: t + " $100",
        lambda t: "가" * 4001,
        lambda t: "",
    ],
)
def test_invalid_sentence_is_retried_once_and_falls_back_without_changing_facts(change):
    original = draft()
    before = deepcopy(original)
    result = rewrite(original, mutate(change))
    assert len(result.calls) == 2 and [call.attempt for call in result.calls] == [1, 2]
    assert result.rewritten == 0 and result.fallback == 1
    row = result.draft.sections[0].sentences[0]
    assert row.text == original.sections[0].sentences[0].text
    assert row.provenance_kind == "system_record"
    assert row.evidence == before.sections[0].sentences[0].evidence
    assert row.conditions == before.sections[0].sentences[0].conditions
    assert original == before


def test_valid_text_keeps_ids_flags_order_template_and_trace_input():
    original = draft()
    result = rewrite(original, mutate(lambda t: t.replace("1,250", "1250")))
    assert len(result.calls) == 1 and result.rewritten == 1
    row = result.draft.sections[0].sentences[0]
    assert row.template_text == original.sections[0].sentences[0].text
    assert row.provenance_kind == "ai_draft" and "1250 A" in row.text
    for field in (
        "sentence_id",
        "evidence",
        "conditions",
        "unconfirmed",
        "needs_rereview",
        "evidence_missing",
    ):
        assert getattr(row, field) == getattr(original.sections[0].sentences[0], field)
    assert result.draft.evidence_missing_count == original.evidence_missing_count
    assert result.draft.disclaimer == DISCLAIMER


@pytest.mark.parametrize("kind", ["missing", "duplicate", "extra", "invalid_json"])
def test_numbering_and_json_failure_falls_back_after_one_retry(kind):
    def provider(prompt):
        rows = json.loads(fake_provider(prompt).text)
        if kind == "missing":
            rows = []
        elif kind == "duplicate":
            rows *= 2
        elif kind == "extra":
            rows.append({"no": 9, "text": "추가"})
        else:
            return Generation("bad-json", Usage(), None, ["STOP"])
        return response(rows)

    result = rewrite(draft(), provider)
    assert len(result.calls) == 2 and result.fallback == 1


@pytest.mark.parametrize("failure", ["error", "timeout", "truncated"])
def test_call_failure_falls_back_whole_section_without_retry(failure):
    def provider(prompt):
        if failure == "error":
            raise RuntimeError("private error must not leak")
        if failure == "timeout":
            raise TimeoutError("private")
        return response([], "MAX_TOKENS")

    result = rewrite(draft(), provider)
    assert len(result.calls) == 1 and result.fallback == 1
    assert "private" not in str(result.calls)


def test_retry_contains_only_invalid_sentences_but_second_call_error_discards_whole_section():
    source = draft()
    source.sections[0].sentences.append(
        source.sections[0].sentences[0].model_copy(update={"sentence_id": UUID(int=4)})
    )
    received = []

    def provider(prompt):
        data = json.loads(prompt.split("\n", 1)[1])
        received.append(data)
        if len(received) == 2:
            assert [row["no"] for row in data["sentences"]] == [2]
            assert data["failures"] == {"2": "TEXT_LENGTH"}
            raise TimeoutError()
        rows = json.loads(fake_provider(prompt).text)
        rows[1]["text"] = ""
        return response(rows)

    result = rewrite(source, provider)
    assert result.rewritten == 0 and result.fallback == 2
    assert all(row.provenance_kind == "system_record" for row in result.draft.sections[0].sentences)


def test_human_and_forbidden_inputs_are_never_sent_and_skip_is_not_a_call():
    source = draft()
    human = source.sections[0].sentences[0].model_copy(deep=True)
    human.provenance_kind, human.text = "human_input", "사람의 원문 유지"
    human.sentence_id = UUID(int=8)
    source.sections[0].sentences.append(human)
    source.sections[0].sentences[0].conditions.append("협력사 SYN-PARTNER 확인")

    def forbidden_call(_):
        raise AssertionError("must not call")

    result = rewrite(source, forbidden_call)
    assert result.calls[0].outcome == "skipped" and result.calls[0].attempt == 0
    assert result.calls[0].usage.input_tokens == 0 and result.calls[0].provider_attempts == 0
    assert result.draft.sections[0].sentences[1] == human
    assert result.fallback == 1


def test_prompt_whitelist_has_no_project_sentence_or_evidence_ids_and_lease_loss_propagates():
    source = draft()

    def provider(prompt):
        for identifier in (
            str(source.project_id),
            str(source.sections[0].sentences[0].sentence_id),
            str(UUID(int=2)),
        ):
            assert identifier not in prompt
        payload = json.loads(prompt.split("\n", 1)[1])
        assert set(payload["sentences"][0]) == {
            "no",
            "title",
            "representatives",
            "conditions",
            "unconfirmed",
        }
        return fake_provider(prompt)

    assert rewrite(source, provider).rewritten == 1

    def lost():
        raise RuntimeError("LEASE_LOST")

    with pytest.raises(RuntimeError, match="LEASE_LOST"):
        rewrite(source, provider, progress=lost)


def test_units_cannot_be_swapped_between_existing_values_and_model_digits_cannot_be_invented():
    source = draft()
    source.sections[0].sentences[0].text = "사양 차이. 대표 항목: 「1 A와 2 kA, 모델 QX-8」."
    source.sections[0].sentences[0].conditions = []
    result = rewrite(
        source, mutate(lambda text: text.replace("1 A", "1 kA").replace("2 kA", "2 A"))
    )
    assert result.fallback == 1 and result.calls[-1].reasons == {1: "UNIT_CHANGED"}
    result = rewrite(source, mutate(lambda text: text + " 추가 모델 B9"))
    assert result.fallback == 1 and result.calls[-1].reasons == {1: "NUMBER_CHANGED"}


def test_six_sections_have_at_most_twelve_calls_and_only_failed_rows_are_repaired():
    source = draft()
    source.sections = [
        s.Section(
            section=name,
            sentences=[
                source.sections[0].sentences[0].model_copy(update={"sentence_id": UUID(int=20 + i)})
            ],
        )
        for i, name in enumerate(
            [
                "CUSTOMER_GOAL_SCOPE",
                "OUR_RESPONSE",
                "DEVIATIONS",
                "PARTNER_SUPPLEMENT",
                "ASSUMPTIONS_EXCLUSIONS",
                "QUESTIONS_NEXT_ACTIONS",
            ]
        )
    ]
    result = rewrite(source, mutate(lambda text: ""))
    assert len(result.calls) == 12 and result.fallback == 6
    attempts = {}

    def repair(prompt):
        data = json.loads(prompt.split("\n", 1)[1])
        section = data["section"]
        attempts[section] = attempts.get(section, 0) + 1
        rows = json.loads(fake_provider(prompt).text)
        if attempts[section] == 1:
            rows[0]["text"] = ""
        return response(rows)

    result = rewrite(source, repair)
    assert len(result.calls) == 12 and result.rewritten == 6 and result.fallback == 0


def test_input_partition_cannot_silently_drop_numeric_representatives_and_product_names_stay_literal():
    source = draft()
    row = source.sections[0].sentences[0]
    row.text = "사양 차이. 대표 항목: 「1 A」 / 「2 A」 / 「3 A」 / 「4 A」."

    def forbidden_call(_):
        raise AssertionError("incomplete input must not call")

    result = rewrite(source, forbidden_call)
    assert result.calls[0].attempt == 0 and result.calls[0].reasons == {1: "INPUT_INCOMPLETE"}
    source = draft()
    row = source.sections[0].sentences[0]
    row.text += " LumenPanel"
    row.evidence.append(s.Evidence(type="product", id="LumenPanel"))
    result = rewrite(source, mutate(lambda text: text.replace("LumenPanel", "다른 제품")))
    assert result.fallback == 1 and result.calls[-1].reasons == {1: "IDENTIFIER_CHANGED"}
