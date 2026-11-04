"""진행 한 줄(focus, KAN-232): worker 허용 검사와 처리 함수의 라벨 정리."""

import pytest

from backend.agent.tft_worker import event_payload
from backend.api.platform.jobs.events import EventPayload
from backend.api.tft.worker_processor import focus


def test_focus_label_keeps_only_safe_text_and_caps_length():
    assert focus("section", "제7조  건식 <변압기>\n") == {
        "kind": "section",
        "label": "제7조 건식 변압기",
    }
    assert focus("found", "제7조", 9) == {"kind": "found", "label": "제7조", "n": 9}
    assert len(focus("section", "가" * 80)["label"]) == 40
    assert focus("ocr", "<<>>")["label"] == "문서"


def test_worker_passes_focus_that_platform_accepts():
    raw = {"step": "extract", "focus": focus("found", "제2조 공급 범위 (표)", 3), "raw": "원문"}
    payload = event_payload(raw)
    assert payload == {
        "step": "extract",
        "focus": {"kind": "found", "label": "제2조 공급 범위 (표)", "n": 3},
    }
    assert EventPayload.model_validate(payload).focus is not None


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "thought", "label": "제7조"},
        {"kind": "section", "label": "모델 응답: <b>"},
        {"kind": "section"},
        {"kind": "found", "label": "제7조", "n": True},
        {"kind": "section", "label": "제7조", "text": "원문"},
    ],
)
def test_worker_refuses_bad_focus(bad):
    with pytest.raises(ValueError, match="TFT_WORKER_BAD_EVENT"):
        event_payload({"step": "extract", "focus": bad})


def test_section_label_uses_korean_head_text_when_title_is_empty():
    from backend.api.tft.worker_processor import section_label
    from backend.engine.rfp.requirements import ClauseInput, ExtractionInput

    item = ExtractionInput(
        section_no="제7조",
        section_title=None,
        clauses=[ClauseInput(clause_no="제7조", path="", text="건식 변압기 (TR-01)", table_rows=[])],
    )
    assert section_label(item) == "제7조 건식 변압기 (TR-01)"
