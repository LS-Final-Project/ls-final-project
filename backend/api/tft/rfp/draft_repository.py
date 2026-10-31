"""RFP 초안·검토본·내보내기의 불변 판본 저장(KAN-188). 커밋은 호출자가 한다."""

from typing import Any, get_args
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.repository import NEEDS_REREVIEW, version_conflict
from backend.engine.rfp.draft import DISCLAIMER

Row = dict[str, Any]


async def lock(conn: AsyncConnection[Row], project_id: UUID) -> None:
    """초안 생성·수정·검토본 저장을 프로젝트별로 직렬화한다."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 188))", (str(project_id),)
    )


async def head(conn: AsyncConnection[Row], project_id: UUID) -> int:
    """최신 판본 번호. 처음 만들기 전이면 0이다."""
    cursor = await conn.execute(
        "SELECT latest_version FROM tft.drafts WHERE project_id = %s FOR UPDATE", (project_id,)
    )
    row = await cursor.fetchone()
    return row["latest_version"] if row else 0


async def generated(conn: AsyncConnection[Row], project_id: UUID, job_id: UUID) -> int | None:
    """같은 처리 작업이 이미 만든 판본을 찾는다. 사람 편집 이후 재시도도 새 판본을 만들지 않는다."""
    cursor = await conn.execute(
        "SELECT version FROM tft.draft_versions WHERE project_id = %s AND source_job_id = %s",
        (project_id, job_id),
    )
    row = await cursor.fetchone()
    return row["version"] if row else None


async def load(
    conn: AsyncConnection[Row], project_id: UUID, version: int | None = None
) -> s.Draft | None:
    """지정 판본(또는 최신)에 현재 재검토 표시를 합산해 읽는다. 표시·검토본 검사용이다."""
    return await _load(conn, project_id, version, live_review=True)


async def load_snapshot(
    conn: AsyncConnection[Row], project_id: UUID, version: int
) -> s.Draft | None:
    """편집 저장·내보내기용 불변 판본. 현재 요구/mark를 조회하지 않고 저장된 flags만 반환한다."""
    return await _load(conn, project_id, version, live_review=False)


async def _load(
    conn: AsyncConnection[Row], project_id: UUID, version: int | None, *, live_review: bool
) -> s.Draft | None:
    cursor = await conn.execute(
        "SELECT v.* FROM tft.drafts d JOIN tft.draft_versions v ON v.project_id = d.project_id "
        "AND v.version = coalesce(%s, d.latest_version) WHERE d.project_id = %s",
        (version, project_id),
    )
    metadata = await cursor.fetchone()
    if metadata is None:
        return None
    review_sql = "s.needs_rereview"
    if live_review:
        review_sql += (
            " OR EXISTS (SELECT 1 FROM tft.rereview_marks m "
            "WHERE m.project_id = s.project_id AND m.target_kind = 'draft_sentence' "
            "AND m.target_id = s.sentence_key AND m.cleared_at IS NULL) "
            "OR EXISTS (SELECT 1 FROM tft.draft_sentence_evidence e "
            "JOIN tft.requirements req ON e.evidence_id = req.id::text "
            "JOIN tft.requirement_versions v ON v.requirement_id = req.id AND v.version = req.latest_version "
            "WHERE e.sentence_id = s.id AND e.evidence_type = 'requirement' AND req.project_id = s.project_id "
            f"AND (v.needs_review OR ({NEEDS_REREVIEW.format(requirement='req.id')}))) "
        )
    cursor = await conn.execute(
        f"SELECT s.*, {review_sql} AS review_needed "
        "FROM tft.draft_sentences s WHERE s.project_id = %s AND s.draft_version = %s "
        "ORDER BY s.section, s.ordinal",
        (project_id, metadata["version"]),
    )
    rows = await cursor.fetchall()
    cursor = await conn.execute(
        "SELECT e.* FROM tft.draft_sentence_evidence e JOIN tft.draft_sentences s "
        "ON s.id = e.sentence_id WHERE s.project_id = %s AND s.draft_version = %s "
        "ORDER BY e.sentence_id,e.ordinal",
        (project_id, metadata["version"]),
    )
    proofs: dict[UUID, list[s.Evidence]] = {}
    for proof in await cursor.fetchall():
        proofs.setdefault(proof["sentence_id"], []).append(
            s.Evidence(type=proof["evidence_type"], id=proof["evidence_id"])
        )
    sections: dict[s.DraftSection, list[s.Sentence]] = {key: [] for key in get_args(s.DraftSection)}
    for row in rows:
        evidence = proofs.get(row["id"], [])
        sections[row["section"]].append(
            s.Sentence(
                sentence_id=row["sentence_key"],
                text=row["text"],
                evidence=evidence,
                conditions=row["conditions"],
                unconfirmed=(row["unconfirmed"] or row["review_needed"])
                if live_review
                else row["unconfirmed"],
                needs_rereview=row["review_needed"],
                evidence_missing=(row["evidence_missing"] or not evidence)
                if live_review
                else row["evidence_missing"],
                provenance_kind=row["provenance_kind"],
            )
        )
    return s.Draft(
        project_id=project_id,
        version=metadata["version"],
        data_origin=metadata["data_origin"],
        derivation_kind=metadata["derivation_kind"],
        known_at=metadata["known_at"],
        sections=[s.Section(section=key, sentences=items) for key, items in sections.items()],
        disclaimer=DISCLAIMER,
        evidence_missing_count=sum(
            row.evidence_missing for items in sections.values() for row in items
        ),
    )


async def save(
    conn: AsyncConnection[Row],
    draft: s.Draft,
    *,
    actor: UUID,
    source_job_id: UUID | None = None,
    reasons: dict[UUID, str] | None = None,
) -> None:
    """로직이 검증한 새 판본을 추가한다. 기존 판본·공개 문장 키는 고치지 않는다."""
    current = await head(conn, draft.project_id)
    if draft.version != current + 1:
        raise version_conflict()
    if any(
        not row.text or len(row.text) > 4000 for part in draft.sections for row in part.sentences
    ):
        raise ValueError("TFT_DRAFT_SENTENCE_LENGTH")
    if current == 0:
        await conn.execute(
            "INSERT INTO tft.drafts (project_id,latest_version,data_origin,derivation_kind,known_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (
                draft.project_id,
                draft.version,
                draft.data_origin,
                draft.derivation_kind,
                draft.known_at,
            ),
        )
    else:
        await conn.execute(
            "UPDATE tft.drafts SET latest_version = %s WHERE project_id = %s",
            (draft.version, draft.project_id),
        )
    await conn.execute(
        "INSERT INTO tft.draft_versions (project_id,version,provenance_kind,created_by,"
        "data_origin,derivation_kind,known_at,source_job_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            draft.project_id,
            draft.version,
            "system_record" if source_job_id else "human_input",
            actor,
            draft.data_origin,
            draft.derivation_kind,
            draft.known_at,
            source_job_id,
        ),
    )
    for part in draft.sections:
        for ordinal, row in enumerate(part.sentences):
            physical_id = uuid4()
            await conn.execute(
                "INSERT INTO tft.draft_sentences (id,project_id,draft_version,section,ordinal,"
                "sentence_key,text,conditions,unconfirmed,evidence_missing,provenance_kind,edit_reason,"
                "needs_rereview) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    physical_id,
                    draft.project_id,
                    draft.version,
                    part.section,
                    ordinal,
                    row.sentence_id,
                    row.text,
                    Jsonb(row.conditions),
                    row.unconfirmed,
                    row.evidence_missing,
                    row.provenance_kind,
                    (reasons or {}).get(row.sentence_id),
                    row.needs_rereview,
                ),
            )
            for index, evidence in enumerate(row.evidence):
                await conn.execute(
                    "INSERT INTO tft.draft_sentence_evidence (sentence_id,ordinal,evidence_type,"
                    "evidence_id) VALUES (%s,%s,%s,%s)",
                    (physical_id, index, evidence.type, evidence.id),
                )


async def human_lineage(
    conn: AsyncConnection[Row], project_id: UUID, version: int, keys: list[UUID]
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    """사람 편집 이유와 자동 문장을 수정한 이력을 읽는다. 직접 추가는 자동 영역을 대체하지 않는다."""
    if not keys:
        return {}, {}
    cursor = await conn.execute(
        "SELECT DISTINCT ON (s.sentence_key) s.sentence_key,s.edit_reason, "
        "(SELECT old.text FROM tft.draft_sentences old WHERE old.project_id=s.project_id "
        "AND old.sentence_key=s.sentence_key AND old.draft_version<=%s "
        "AND old.provenance_kind<>'human_input' ORDER BY old.draft_version DESC LIMIT 1) AS original_text "
        "FROM tft.draft_sentences s WHERE s.project_id=%s AND s.draft_version<=%s "
        "AND s.sentence_key=ANY(%s) AND s.edit_reason IS NOT NULL "
        "ORDER BY s.sentence_key,s.draft_version DESC",
        (version, project_id, version, keys),
    )
    rows = await cursor.fetchall()
    return (
        {row["sentence_key"]: row["edit_reason"] for row in rows},
        {
            row["sentence_key"]: row["original_text"]
            for row in rows
            if row["original_text"] is not None
        },
    )
