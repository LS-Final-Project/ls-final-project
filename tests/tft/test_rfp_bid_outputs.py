"""KAN-221 입찰 대응 XLSX 생성기: 시트·칸·행이 그대로 열리고 같은 입력은 같은 바이트다(DB 없음)."""

import hashlib
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from backend.api.tft.rfp import bid_outputs as out
from backend.api.tft.rfp import schemas as s

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx(data: bytes) -> dict[str, list[list[str]]]:
    """시트 이름 → 행(셀 글자). 숫자 셀은 글자로 읽는다."""
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        book = ET.fromstring(package.read("xl/workbook.xml"))
        names = [sheet.get("name") for sheet in book.iter(M + "sheet")]
        result = {}
        for i, name in enumerate(names, 1):
            root = ET.fromstring(package.read(f"xl/worksheets/sheet{i}.xml"))
            result[name] = [
                ["".join(c.itertext()) for c in row.iter(M + "c")] for row in root.iter(M + "row")
            ]
    return result


def sample() -> out.Outputs:
    return out.Outputs(
        meta="RFP-91 | 검토본 1 | 합성 자료 | 기준 2026-11-04 10:00 KST | 확정",
        sheets=[
            out.Sheet(
                "편차표", ("No", "편차 내용"), (6, 40), [[1, "75 °C 기준 임피던스\n성적서 확인"]]
            ),
            out.Sheet("질의서", ("No", "질의"), (8, 40), []),
            out.Sheet("현황표", ("설비", "충족"), (20, 8), [["MV-SWG-01", 6], ["합계", 6]]),
            out.Sheet("요구별", ("No", "원문"), (6, 40), [[1, "A"], [2, "B"]]),
        ],
    )


def test_four_sheets_open_with_meta_headers_and_rows():
    data = out.render_xlsx(sample())
    sheets = read_xlsx(data)
    assert list(sheets) == ["편차표", "질의서", "현황표", "요구별"]
    for rows in sheets.values():
        assert rows[0][0].endswith("확정")
    assert sheets["편차표"][1] == ["No", "편차 내용"]
    assert sheets["편차표"][2] == ["1", "75 °C 기준 임피던스\n성적서 확인"]
    assert len(sheets["질의서"]) == 2  # 머리·칸 이름만
    assert sheets["현황표"][3] == ["합계", "6"]
    assert len(sheets["요구별"]) == 4
    assert out.render_xlsx(sample()) == data
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        assert package.testzip() is None
        assert "[Content_Types].xml" in package.namelist()


def test_a_control_character_is_refused_not_dropped():
    bad = sample()
    bad.sheets[3].rows.append([3, "bell\x07"])
    with pytest.raises(ValueError, match="TFT_EXPORT_INVALID_CHARACTER"):
        out.render_xlsx(bad)


def test_clause_order_compares_numbers_as_numbers():
    clauses = ["제10조.1", "제2조.10", "제2조.2", "2.1.A", "2.1.B"]
    assert sorted(clauses, key=out.clause_key) == [
        "2.1.A",
        "2.1.B",
        "제2조.2",
        "제2조.10",
        "제10조.1",
    ]


def test_gate_text_says_draft_or_final():
    """KAN-253: 남은 것이 없으면 확정, 책임자 확인만 남으면 초안이다. 확인 대기는 저장 조건 미충족이 아니다."""
    assert out.gate_text(s.DecisionGate()) == "확정"
    assert out.gate_text(s.DecisionGate(review_pending=2)) == "초안 (책임자 확인 2건 대기)"
    assert (
        out.gate_text(s.DecisionGate(undecided=3, review_pending=2))
        == "초안 (저장 조건 미충족: 판정 안 된 요구 3, 책임자 확인 2건 대기)"
    )


def test_column_letters():
    assert [out.column(i) for i in (0, 25, 26, 27, 51, 52)] == ["A", "Z", "AA", "AB", "AZ", "BA"]


def test_an_xlsx_retry_after_the_file_was_stored_reuses_that_file(tmp_path):
    """저장 직후 DB 기록 전에 멈춘 XLSX 재시도는 결정이 바뀌었어도 처음 만든 파일을 쓴다(다시 만들지 않는다)."""
    import asyncio
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from uuid import UUID

    from backend.agent.processor_registry import TftJob
    from backend.api.tft.rfp import draft_exports
    from backend.api.tft.rfp.storage import LocalDocumentStore

    export_id, project = UUID(int=99), UUID(int=1)
    job = TftJob(
        job_id=UUID(int=98),
        kind="rfp.export",
        project_id=project,
        input_version=draft_exports.input_version(export_id),
        actor_id=UUID(int=97),
    )
    row = dict(
        id=export_id, review_version=1, job_id=None, created_by=job.actor_id, format="XLSX",
        completed_at=None, created_at=datetime(2026, 11, 4, tzinfo=UTC), title="합성",
    )  # fmt: skip
    conn = AsyncMock()

    async def execute(sql, params):
        if "FROM tft.exports" in sql:
            return SimpleNamespace(fetchall=AsyncMock(return_value=[row]))
        if "FROM tft.review_versions" in sql:
            return SimpleNamespace(fetchone=AsyncMock(return_value={"draft_version": 1}))
        return AsyncMock()

    conn.execute.side_effect = execute
    store = LocalDocumentStore(tmp_path)
    first = out.render_xlsx(sample())
    asyncio.run(store.put(f"rfp/{project}/exports/{export_id}.xlsx", first))
    with (
        patch.object(out, "collect", new=AsyncMock(side_effect=AssertionError("regenerated"))),
        patch.object(draft_exports, "audit", new_callable=AsyncMock),
    ):
        asyncio.run(draft_exports.process(conn, job, lambda event: None, store))
    update = next(c for c in conn.execute.await_args_list if "UPDATE tft.exports" in c.args[0])
    assert update.args[1][2] == hashlib.sha256(first).hexdigest()


def test_customer_columns_hide_internal_codes():
    """40444 흐름 점검: 표에 OCR-DOCX-0001.7, 판본 앞 8자, 근거 ID, 영문 조건 이름이 그대로 나갔다."""
    assert out.clause_label("OCR-DOCX-0001.7") == "본문 이미지 1"
    assert out.clause_label("OCR-PDF-P3.1") == "PDF 3쪽"
    assert out.clause_label("제7조.2") == "제7조.2"
    assert out.requirement_label("ada13520:OCR-DOCX-0001.3:1") == "본문 이미지 1-1"
    assert out.requirement_label("ada13520:제7조.2:2") == "제7조.2-2"
    # 원문에서 직접 추가한 요구(KAN-225)의 순번 m1도 내부 ID로 나가지 않는다(40444 P26-0003).
    assert out.requirement_label("f4cc6a52:제3조.2:m1") == "제3조.2 직접 추가 1"
    assert (
        out.reason_text(
            "원문 제2조.1 임피던스 요구 6 %, 자사 6 %(근거 DS-TRF-DRY-630 2.2): 값은 충족하나 조건이 달라 "
            "확인 필요. 조건 차이: reference temperature 요구 75 °C, 후보 120 °C."
        )
        == "요구 6 %, 자사 6 %: 값은 맞지만 조건이 다릅니다. 기준온도 차이: 요구 75 °C, 자사 120 °C."
    )
    assert (
        out.reason_text(
            "이미지 전사 원문 확인 필요. 원문 OCR-DOCX-0001.7 정격전압 요구 11 kV, 자사 25.8 kV(근거 X 2.1): 값 다름."
        )
        == "요구 11 kV, 자사 25.8 kV. 값이 다릅니다."
    )
