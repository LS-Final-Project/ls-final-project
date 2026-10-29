"""KAN-101 문서 추출 채점기. 합성 텍스트·표만 쓰고 실제 데이터·정답 파일은 쓰지 않는다."""

import pytest

from eval.document_metrics import cer, summarize_table_structure, summarize_text_extraction


@pytest.mark.parametrize(
    "reference,hypothesis,expected",
    [
        ("abc", "abd", 1 / 3),  # 치환 1
        ("abc", "abxc", 1 / 3),  # 삽입 1
        ("abc", "ac", 1 / 3),  # 삭제 1
        ("kitten", "sitting", 3 / 6),
        ("abc", "abc", 0.0),
        ("", "", 0.0),
        ("ab", "abxyz", 3 / 2),
    ],
)
def test_cer_matches_hand_calculation(reference, hypothesis, expected):
    """편집 거리 / 정답 길이 손계산과 같다.

    Args:
        reference: 정답.
        hypothesis: 추출.
        expected: 기대 CER.

    Returns:
        None: 값을 확인한다.
    """
    assert cer(reference, hypothesis) == pytest.approx(expected)


def test_cer_rejects_text_against_empty_reference_and_does_not_normalize():
    """빈 정답에 글자가 오면 거부하고, 대소문자·공백을 정규화하지 않는다.

    Args:
        없음.

    Returns:
        None: 오류와 비정규화를 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        cer("", "x")
    assert cer("Ab", "ab") == 0.5 and cer("a b", "ab") == pytest.approx(1 / 3)


def test_text_extraction_summary_has_no_source_text():
    """블록별 CER 평균·중앙값·정확 일치율을 내고 원문은 넣지 않는다.

    Args:
        없음.

    Returns:
        None: 집계 값과 원문 비포함을 확인한다.
    """
    result = summarize_text_extraction(
        {"b1": ("SECRET-A", "SECRET-A"), "b2": ("abc", "abd"), "b3": ("abcd", "")}
    )
    assert result["blocks"] == 3
    assert result["cer_mean"] == pytest.approx((0 + 1 / 3 + 1) / 3)
    assert result["cer_median"] == pytest.approx(1 / 3)
    assert result["exact_match_rate"] == pytest.approx(1 / 3)
    assert "SECRET" not in str(result)
    assert summarize_text_extraction({})["cer_mean"] is None


def test_table_structure_counts_shape_cells_and_missing_tables():
    """행·열 수 일치, 위치 기준 셀 일치, 누락 표를 센다.

    Args:
        없음.

    Returns:
        None: 표별·전체 값을 확인한다.
    """
    expected = {
        "t1": [["A", "B"], ["1", None]],
        "t2": [["x", "y"]],
        "t3": [["only"]],
    }
    actual = {"t1": [["A", "B"], ["1", ""]], "t2": [["x"], ["y"]]}
    result = summarize_table_structure(expected, actual)
    rows = {row["table_id"]: row for row in result["per_table"]}
    assert rows["t1"]["structure_match"] and rows["t1"]["cell_accuracy"] == 1.0
    assert not rows["t2"]["structure_match"]
    assert (rows["t2"]["rows_match"], rows["t2"]["cols_match"]) == (False, False)
    assert rows["t2"]["cell_accuracy"] == 0.5
    assert not rows["t3"]["found"] and not rows["t3"]["structure_match"]
    assert result["missing_tables"] == 1
    assert result["structure_match_rate"] == pytest.approx(1 / 3)
    assert result["cell_accuracy"] == pytest.approx(5 / 7)
    assert "only" not in str(result)
    [narrow] = summarize_table_structure({"t": [["a", "b"]]}, {"t": [["a"]]})["per_table"]
    assert narrow["rows_match"] and not narrow["cols_match"] and not narrow["structure_match"]
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_table_structure(expected, actual | {"extra": [["z"]]})
