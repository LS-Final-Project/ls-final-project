"""합성 snapshot과 fake 공급자만 사용한다. 네트워크/키/실제 모델을 호출하지 않는다."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.api.tft.rfp import schemas as s
from backend.engine.rfp import draft_rewrite
from backend.engine.rfp.draft import DISCLAIMER
from backend.engine.rfp.draft_rewrite import fake_provider, rewrite
from backend.engine.rfp.vocabulary import DERIVED_SOURCE_NOTE
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
        lambda t: t.replace("IEC", "KS"),
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
        data = json.loads(prompt.rsplit("\n", 1)[1])
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
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        assert set(payload["sentences"][0]) == {
            "no",
            "title",
            "items",
            "context_only",
            "must_keep",
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
        data = json.loads(prompt.rsplit("\n", 1)[1])
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

    sent = []

    def provider(prompt):
        sent.append(json.loads(prompt.rsplit("\n", 1)[1])["sentences"][0]["items"])
        return fake_provider(prompt)

    # 예전에는 대표 항목 3개까지만 보내 입력 누락으로 막았다. 이제 항목을 모두 보낸다.
    assert rewrite(source, provider).rewritten == 1
    assert sent == [["1 A", "2 A", "3 A", "4 A"]]
    source = draft()
    row = source.sections[0].sentences[0]
    row.text += " LumenPanel"
    row.evidence.append(s.Evidence(type="product", id="LumenPanel"))
    result = rewrite(source, mutate(lambda text: text.replace("LumenPanel", "다른 제품")))
    assert result.fallback == 1 and result.calls[-1].reasons == {1: "IDENTIFIER_CHANGED"}


@pytest.mark.parametrize(
    "text",
    [
        "Medium-voltage switchgear duration form tap frequency impedance",
        "ratings derived from the Derived Table",
        "BIL: 15 kV, 95 kV",
    ],
)
def test_customer_words_are_not_internal_codes(text):
    """고객 원문의 일반 단어는 내부 코드가 아니다."""
    assert not draft_rewrite.CODE_WORDS.search(text)
    assert not draft_rewrite.forbidden(text, draft().sections[0].sentences[0])


@pytest.mark.parametrize(
    "text",
    [
        "rated_max_voltage 값",
        "integrator_capable.assembly 확인",
        "SPEC_GAP 상태",
        "상태 PENDING",
        "KAN-188 참조",
        "SYN-001 참조",
        "DS-TRF-630 참조",
        "11111111-1111-4111-8111-111111111111 참조",
        "가격 $100",
        "단가 100 USD",
        "금액 3억원",
        "PARTNER-1 후보",
        "합성 데이터시트 문장을 정규화한 값(DERIVED)",
        "정격은 정규화한 값입니다",
        "데이터시트 문장 기준",
        "DERIVED값",
        "제품 마스터 표준 조건(합성)",
    ],
)
def test_internal_codes_money_ids_and_partner_codes_stay_blocked(text):
    """식별자 꼴·금액·UUID·근거 ID·협력사 코드는 계속 막는다."""
    row = draft().sections[0].sentences[0]
    row.evidence.append(s.Evidence(type="partner", id="PARTNER-1"))
    assert draft_rewrite.forbidden(text, row)


def keep_row():
    row = draft().sections[0].sentences[0]
    row.text = row.template_text = (
        "제어반 자사 후보 비교 · 사양 차이. 전체 요구 충족이나 공급 확약이 아님. 요구 1건. "
        "대표 항목: 「정격 1,250 A, 제품 QX-800」."
    )
    return row


def test_must_keep_lists_condition_values_status_and_negation_once():
    row = keep_row()
    row.conditions = ["주위온도: 35 °C", "고객 확인 필요", "고객 확인 필요"]
    item = draft_rewrite.input_of(row, 1)
    kept = draft_rewrite.must_keep(item)
    # 조건은 화면의 조건 칸이 보여 준다. 문장에 다시 쓰라고 요구하지 않는다.
    assert "35 °C" not in kept and "고객 확인 필요" not in kept
    assert "사양 차이" in kept and "확약이 아님" in kept and "요구 1건" in kept
    assert len(kept) == len(set(kept))


def test_must_keep_lists_every_count_phrase():
    row = keep_row()
    row.text = row.template_text = (
        "제어반 비교. 요구 3건 · 자사 후보 2건 · 가상 협력사 5건. 대표 항목: 「정격 1,250 A」."
    )
    kept = draft_rewrite.must_keep(draft_rewrite.input_of(row, 1))
    assert {"요구 3건", "후보 2건", "협력사 5건"} <= set(kept)


def test_a_sentence_with_every_must_keep_phrase_passes_and_losing_any_one_fails():
    row = keep_row()
    item = draft_rewrite.input_of(row, 1)
    kept = draft_rewrite.must_keep(item)
    assert draft_rewrite.validate(" ".join(kept), item, row) is None
    for phrase in kept:
        rest = [p for p in kept if p != phrase]
        assert draft_rewrite.validate(" ".join(rest), item, row) is not None


def test_the_prompt_sends_must_keep_and_keeps_english_values():
    row = keep_row()
    item = draft_rewrite.input_of(row, 1)
    text = draft_rewrite.prompt("OUR_RESPONSE", [item], {})
    data = json.loads(text.rsplit("\n", 1)[1])
    assert data["sentences"][0]["must_keep"] == draft_rewrite.must_keep(item)
    assert "must_keep의 문구는 띄어쓰기까지 글자 그대로" in text
    assert "글자 그대로 둡니다" in text
    assert "'대표 항목'·'요구 n건' 같은 입력 구조의 말" in text
    assert "context_only" in text and "옮겨 적지 마세요" in text
    assert "'~가 아니라 ~다' 대비" in text
    assert "must_keep" not in item


def test_source_note_left_in_conditions_is_never_sent_to_the_model():
    """옛 조건 칸에 출처 표시가 남아도 모델 입력이 되지 않고 양식이 유지된다."""
    source = draft()
    row = source.sections[0].sentences[0]
    row.conditions.append(DERIVED_SOURCE_NOTE)

    def forbidden_call(_):
        raise AssertionError("must not call")

    result = rewrite(source, forbidden_call)
    assert result.calls[0].reasons == {1: "INPUT_BLOCKED"} and result.fallback == 1


def test_must_keep_lists_values_identifiers_and_bare_numbers_without_repeating_fragments():
    row = keep_row()
    row.text = row.template_text = (
        "제어반 비교. 요구 2건. 대표 항목: 「정격 40 kA」 / 「탭 +2x-4x2.5%」 / 「지정 모델 ORBIT-Z9」."
    )
    row.conditions = ["duration 3 s"]
    kept = draft_rewrite.must_keep(draft_rewrite.input_of(row, 1))
    assert {"40 kA", "ORBIT-Z9", "요구 2건"} <= set(kept)
    assert "duration 3 s" not in kept and "3 s" not in kept
    numbers = draft_rewrite.numeric_values(" ".join(kept))
    assert (
        draft_rewrite.numeric_values(draft_rewrite.source_text(draft_rewrite.input_of(row, 1)))
        <= numbers
    )


def test_a_title_with_the_word_product_is_not_a_product_name():
    row = keep_row()
    row.text = row.template_text = (
        "저압 차단기의 외부 제품 조달 검토 · 미선택 선택지. 요구 1건. 대표 항목: 「정격 100 A」."
    )
    item = draft_rewrite.input_of(row, 1)
    assert draft_rewrite.identifiers_of(item) == []
    row.text = row.template_text = "비교. 요구 1건. 대표 항목: 「제품 QX-800 · 요구 100 A」."
    assert "QX-800" in draft_rewrite.identifiers_of(draft_rewrite.input_of(row, 1))
    assert (
        draft_rewrite.validate("요구 1건, 100 A", draft_rewrite.input_of(row, 1), row) is not None
    )


def new_row(text: str, conditions: list[str] | None = None) -> s.Sentence:
    row = draft().sections[0].sentences[0]
    row.text = row.template_text = text
    row.conditions = conditions or []
    return row


def test_new_format_lists_every_item_and_subject_and_keeps_conditions_out_of_the_text():
    row = new_row(
        "자사 자료에서 대응 근거를 아직 찾지 못한 요구: 154kV 변압기 · 설비 효율 / DC간선 · 전압강하.",
        ["판단 상태: 근거 부족(미충족 확정 아님)", "고객 확인 필요"],
    )
    item = draft_rewrite.input_of(row, 1)
    assert item["representatives"] == ["154kV 변압기 · 설비 효율", "DC간선 · 전압강하"]
    assert item["subjects"] == ["154kV 변압기", "DC간선"]
    good = (
        "154kV 변압기의 설비 효율과 DC간선의 전압강하는 자사 자료에서 대응 근거를 아직 찾지 못했다."
    )
    assert draft_rewrite.validate(good, item, row) is None
    dropped = "154kV 변압기의 설비 효율은 자사 자료에서 대응 근거를 아직 찾지 못했다."
    assert draft_rewrite.validate(dropped, item, row) == "SUBJECT_MISSING"
    found = "154kV 변압기와 DC간선은 자사 자료에 대응 근거가 있다."
    assert draft_rewrite.validate(found, item, row) == "NEGATION_MISSING"


def test_quoted_scope_sentence_keeps_its_equipment_and_numbers():
    row = new_row("154kV 변압기: 「154kV변압기 설비 효율 : 99%이상」.")
    item = draft_rewrite.input_of(row, 1)
    assert item["subjects"] == ["154kV 변압기"]
    assert item["representatives"] == ["154kV변압기 설비 효율 : 99%이상"]
    assert (
        draft_rewrite.validate("154kV 변압기는 설비 효율 99% 이상을 요구한다.", item, row) is None
    )
    assert draft_rewrite.validate("154kV 변압기는 설비 효율 98% 이상을 요구한다.", item, row)
    assert draft_rewrite.validate("변압기는 설비 효율 99% 이상을 요구한다.", item, row)


def test_unselected_alternative_must_stay_unselected_and_product_names_survive_brackets():
    row = new_row("검토 가능한 대안(미선택) — 과업 위탁 검토: 부품 공장 조립 / 극성표시.")
    item = draft_rewrite.input_of(row, 1)
    plain = "부품 공장 조립과 극성표시는 과업 위탁을 검토할 수 있다."
    assert draft_rewrite.validate(plain, item, row) == "NEGATION_MISSING"
    kept = "부품 공장 조립과 극성표시는 과업 위탁을 검토할 수 있으나 아직 선택하지 않았다."
    assert draft_rewrite.validate(kept, item, row) is None
    compared = new_row(
        "GIS — 자사 후보와 차이가 있는 비교 항목: "
        "「제품 SWGR-MV-12kV · 요구 170 kV, 후보 12 kV · 속성 정격전압」."
    )
    assert "SWGR-MV-12kV" in draft_rewrite.identifiers_of(draft_rewrite.input_of(compared, 1))


def test_sections_longer_than_one_batch_are_sent_in_batches_not_skipped():
    source = draft()
    base = source.sections[0].sentences[0]
    source.sections[0].sentences = [
        base.model_copy(update={"sentence_id": UUID(int=100 + n)}) for n in range(20)
    ]
    sizes = []

    def provider(prompt):
        sizes.append(len(json.loads(prompt.rsplit("\n", 1)[1])["sentences"]))
        return fake_provider(prompt)

    result = rewrite(source, provider)
    assert sizes == [8, 8, 4] and result.rewritten == 20 and result.fallback == 0
    assert not any(call.outcome == "skipped" for call in result.calls)
    # 호출 기록 키는 (작업, 절, 시도)다. 묶음마다 절 이름이 달라야 기록이 겹쳐 사라지지 않는다.
    keys = [(call.section, call.attempt) for call in result.calls]
    assert keys == [("OUR_RESPONSE", 1), ("OUR_RESPONSE:2", 1), ("OUR_RESPONSE:3", 1)]
    assert len(set(keys)) == len(keys)
