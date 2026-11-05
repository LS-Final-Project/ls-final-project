#!/usr/bin/env python3
"""처리된 프로젝트의 최신 초안을 초안 추적률 입력으로 내보낸다(KAN-188). 모델을 부르지 않는다.

`--project 14=<프로젝트 UUID>`처럼 문서 번호마다 프로젝트를 준다. 결과는
`rfp_candidate_run --drafts`로 넘긴다. 문장 본문은 넣지 않는다.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api.tft.rfp.drafts import valid_references  # noqa: E402
from backend.shared.database import connection  # noqa: E402
from backend.shared.settings import Settings  # noqa: E402


async def trace_input(conn: Any, project_id: UUID) -> dict[str, Any]:
    """프로젝트의 최신 초안 문장과 프로젝트에 있는 근거를 읽는다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        dict: summarize_draft_trace 입력 하나. 초안이 없으면 문장이 비어 있다.
    """
    cursor = await conn.execute(
        "SELECT s.id, s.section, s.unconfirmed FROM tft.draft_sentences s "
        "JOIN tft.drafts d ON d.project_id = s.project_id AND d.latest_version = s.draft_version "
        "WHERE s.project_id = %s ORDER BY s.section, s.ordinal",
        (project_id,),
    )
    rows = await cursor.fetchall()
    cursor = await conn.execute(
        "SELECT sentence_id, evidence_type, evidence_id FROM tft.draft_sentence_evidence "
        "WHERE sentence_id = ANY(%s) ORDER BY sentence_id, ordinal",
        ([row["id"] for row in rows],),
    )
    evidence: dict[Any, list[dict[str, str]]] = {}
    for row in await cursor.fetchall():
        evidence.setdefault(row["sentence_id"], []).append(
            {"type": row["evidence_type"], "id": row["evidence_id"]}
        )
    return {
        "sentences": [
            {
                "section": row["section"],
                "unconfirmed": row["unconfirmed"],
                "evidence": evidence.get(row["id"], []),
            }
            for row in rows
        ],
        "references": sorted(await valid_references(conn, project_id)),
    }


async def export(projects: dict[str, UUID]) -> dict[str, Any]:
    """문서 번호마다 입력을 읽는다.

    Args:
        projects: 문서 번호 문자열 → 프로젝트 ID.

    Returns:
        dict: rfp_candidate_run --drafts 입력.
    """
    async with connection(Settings()) as conn:
        return {number: await trace_input(conn, project) for number, project in projects.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="append", required=True, help="문서번호=프로젝트UUID")
    parser.add_argument("--out", type=Path, required=True, help="저장소 밖 결과 파일")
    arguments = parser.parse_args()
    pairs = dict(item.split("=", 1) for item in arguments.project)
    data = asyncio.run(export({number: UUID(project) for number, project in pairs.items()}))
    arguments.out.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"문서 {len(data)}개, 문장 {sum(len(d['sentences']) for d in data.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
