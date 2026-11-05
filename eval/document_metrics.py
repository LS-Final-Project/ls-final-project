"""문서 추출 결과를 잠긴 정답 텍스트·표로 채점한다(KAN-101). 파일·DB·모델에 접근하지 않는다.

정규화(공백·대소문자·유니코드)는 하지 않는다. 필요하면 호출자가 양쪽에 같은 정규화를 먼저 적용한다.
합격 판정과 원문은 결과에 넣지 않는다.
"""

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

Table = Sequence[Sequence[str | None]]


def cer(reference: str, hypothesis: str) -> float:
    """문자 오류율 = 편집 거리(치환·삽입·삭제 각 1) / 정답 길이.

    Args:
        reference: 정답 텍스트. 정규화하지 않는다.
        hypothesis: 추출 텍스트. 정규화하지 않는다.

    Returns:
        float: 0 이상(삽입이 많으면 1을 넘을 수 있다). 정답이 빈 문자열이면 추출도 비었을 때 0.0이고,
            아니면 EVAL_INVALID_INPUT ValueError다.
    """
    if not reference:
        if hypothesis:
            raise ValueError("EVAL_INVALID_INPUT")
        return 0.0
    previous = list(range(len(hypothesis) + 1))
    for i, ref_char in enumerate(reference, 1):
        current = [i]
        for j, hyp_char in enumerate(hypothesis, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref_char != hyp_char))
            )
        previous = current
    return previous[-1] / len(reference)


def summarize_text_extraction(pairs: Mapping[str, tuple[str, str]]) -> dict:
    """블록별 CER과 정확 일치를 모은다.

    Args:
        pairs: 블록 ID별 (정답, 추출) 텍스트.

    Returns:
        dict: 블록 수, CER 평균·중앙값, 정확 일치율(블록 0이면 None), 블록별 CER·일치 여부. 원문은 넣지 않는다.
    """
    per_block: list[dict[str, Any]] = [
        {"block_id": block_id, "cer": cer(reference, hypothesis), "exact": reference == hypothesis}
        for block_id, (reference, hypothesis) in pairs.items()
    ]
    scores = [row["cer"] for row in per_block]
    return {
        "quality_gate_evaluated": False,
        "blocks": len(per_block),
        "cer_mean": statistics.fmean(scores) if scores else None,
        "cer_median": statistics.median(scores) if scores else None,
        "exact_match_rate": (
            sum(row["exact"] for row in per_block) / len(per_block) if per_block else None
        ),
        "per_block": per_block,
    }


def summarize_table_structure(expected: Mapping[str, Table], actual: Mapping[str, Table]) -> dict:
    """표별 행·열 수 일치와 위치 기준 셀 정확 일치를 센다.

    Args:
        expected: 표 ID별 정답 표(행 목록). 열 수는 가장 긴 행 기준이고 None은 빈 셀이다.
        actual: 표 ID별 추출 표. 없는 표는 구조 불일치·셀 0개 일치로 센다.

    Returns:
        dict: 표 수, 구조 일치율, 셀 정확 일치율(정답 셀 전체 기준), 표별 행·열 수 일치와 셀 일치율.
            정답에 없는 표 ID가 오면 EVAL_INVALID_INPUT ValueError다. 셀 원문은 넣지 않는다.
    """
    if not set(actual) <= set(expected):
        raise ValueError("EVAL_INVALID_INPUT")

    def cell(table: Table, row: int, col: int) -> str:
        """위치의 셀 값. 범위 밖과 None은 빈 셀이다.

        Args:
            table: 표.
            row: 행 번호.
            col: 열 번호.

        Returns:
            str: 셀 문자열.
        """
        if row >= len(table) or col >= len(table[row]):
            return ""
        return table[row][col] or ""

    per_table: list[dict[str, Any]] = []
    for table_id, gold in expected.items():
        found = table_id in actual
        table = actual.get(table_id, [])
        shape = (len(gold), max(map(len, gold), default=0))
        got = (len(table), max(map(len, table), default=0))
        cells = [(r, c) for r, row in enumerate(gold) for c in range(shape[1])]
        matched = sum(cell(gold, r, c) == cell(table, r, c) for r, c in cells) if found else 0
        per_table.append(
            {
                "table_id": table_id,
                "found": found,
                "rows_match": found and shape[0] == got[0],
                "cols_match": found and shape[1] == got[1],
                "structure_match": found and shape == got,
                "cells": len(cells),
                "cells_matched": matched,
                "cell_accuracy": matched / len(cells) if cells else None,
            }
        )
    total_cells = sum(row["cells"] for row in per_table)
    return {
        "quality_gate_evaluated": False,
        "tables": len(per_table),
        "missing_tables": sum(not row["found"] for row in per_table),
        "structure_match_rate": (
            sum(row["structure_match"] for row in per_table) / len(per_table) if per_table else None
        ),
        "cell_accuracy": (
            sum(row["cells_matched"] for row in per_table) / total_cells if total_cells else None
        ),
        "per_table": per_table,
    }
