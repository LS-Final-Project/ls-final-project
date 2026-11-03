"""KAN-188: compact synthetic Korean clauses and all-skipped extraction input are not success."""

import pytest

from backend.api.tft.worker_processor import document_inputs, sections_of
from backend.engine.rfp.requirements import build_inputs
from tests.tft.test_rfp_clause_tree import p, tree_of


def test_independent_numeric_section_is_not_hidden_by_chapter_prefix_group():
    tree = tree_of(
        p("제8장 규격"),
        p("7. 기존 자료"),
        p("8. 기존 확인"),
        p("1.1범위"),
        p("1.1.1정격을 확인하여야 한다."),
        p("1.1.2기록을 제출하여야 한다."),
    )
    assert "1.1" in sections_of(tree)
    item = build_inputs(tree, ["1.1"])[0]
    assert {c.clause_no for c in item.clauses} >= {"1.1.1", "1.1.2"}


def test_parent_body_is_not_discarded_when_korean_section_has_children():
    tree = tree_of(
        p("제8장 규격"),
        p("1.1범위 1.1.1보호 장치를 공급하여야 한다."),
        p("1.1.2기록을 제출하여야 한다."),
        p("1.1.3시운전을 수행하여야 한다."),
    )
    item = build_inputs(tree, ["1.1"])[0]
    assert any("보호 장치를 공급" in c.text for c in item.clauses)


@pytest.mark.parametrize(
    "tree",
    [
        tree_of(p("표지")),
        tree_of(p("PART 1 GENERAL"), p("1.1 REQUIREMENTS"), p("A. " + "x" * 12001)),
    ],
)
def test_no_usable_input_is_rejected_before_model_and_storage(tree):
    with pytest.raises(ValueError, match="^TFT_DOCUMENT_EXTRACTION_INPUT_INVALID$"):
        document_inputs(tree)


def test_one_invalid_section_preserves_valid_input_and_marks_review():
    tree = tree_of(
        p("PART 1 GENERAL"),
        p("1.1 OVERSIZED"),
        p("A. " + "x" * 12001),
        p("1.2 VALID"),
        p("A. Supply the meter."),
    )
    inputs, skipped = document_inputs(tree)
    assert skipped is True
    assert [x.section_no for x in inputs] == ["1.2"]
    assert inputs[0].clauses[0].text == "Supply the meter."


def test_process_preflights_all_new_documents_before_any_provider_or_store(monkeypatch):
    import asyncio
    from datetime import date
    from types import SimpleNamespace
    from uuid import uuid4

    from backend.api.tft import worker_processor as wp

    trees = {
        b"valid": tree_of(p("PART 1 GENERAL"), p("1.1 VALID"), p("A. Supply a meter.")),
        b"invalid": tree_of(p("PART 1 GENERAL"), p("1.1 LARGE"), p("A. " + "x" * 12001)),
    }
    documents = [
        dict(
            id=uuid4(),
            latest=True,
            kind="RFP_SPEC",
            mime="text/markdown",
            storage_key=key.decode(),
            sha256="",
            size=0,
        )
        for key in trees
    ]

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            pytest.fail("preflight must precede transaction or business writes")

    async def connect(*args, **kwargs):
        return Connection()

    async def versions(*args):
        return documents

    async def unprocessed(*args):
        return False

    async def read(store, key, *args):
        return key.encode()

    def provider(*args):
        pytest.fail("all documents must pass preflight before the first model call")

    monkeypatch.setattr(wp.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(wp.repository, "all_document_versions", versions)
    monkeypatch.setattr(wp.repository, "document_processed", unprocessed)
    monkeypatch.setattr(wp, "read_document", read)
    monkeypatch.setattr(wp, "extract", lambda data, **kwargs: trees[data])
    monkeypatch.setattr(wp, "build_clause_tree", lambda tree: tree)
    events = []
    job = SimpleNamespace(
        project_id=uuid4(), input_version=wp.input_version([d["id"] for d in documents])
    )
    with pytest.raises(ValueError, match="^TFT_DOCUMENT_EXTRACTION_INPUT_INVALID$"):
        asyncio.run(
            wp.process(
                SimpleNamespace(database_url=None),
                job,
                events.append,
                provider,
                object(),
                [],
                date(2026, 1, 1),
            )
        )
    assert [e["step"] for e in events] == ["parse", "parse"]


def test_parent_requirement_text_is_kept_alongside_child_input():
    tree = tree_of(p("제1조(범위) 정격을 확인하여야 한다."), p("1. 시험 기록을 제출하여야 한다."))
    inputs, skipped = document_inputs(tree)
    assert skipped is False
    assert [c.clause_no for c in inputs[0].clauses] == ["제1조", "제1조.1"]
    assert inputs[0].clauses[0].text.endswith("정격을 확인하여야 한다.")
