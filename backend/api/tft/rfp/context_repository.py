"""Project-scoped immutable context snapshots. Callers own commit and authorization."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.api.tft.rfp import draft_repository
from backend.api.tft.rfp.context_schemas import ContextResponse, SourceReference
from backend.api.tft.rfp.repository import EXCLUDED
from backend.engine.rfp.context_graph import consolidate, heading_tags, validate_context
from backend.engine.rfp.context_models import ContextRequirement, ContextResult
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


def encoded(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode()


@dataclass(frozen=True)
class SourceBundle:
    requirements: list[ContextRequirement]
    source_refs: list[SourceReference]
    fingerprint: str
    snapshot: dict[str, Any]
    diagram_source_ids: list[str]
    base_context_version: int = 0


async def load_source(conn: AsyncConnection[Row], project_id: UUID) -> SourceBundle:
    """Read public analysis fields only; no source files, gold, embeddings or model calls."""
    cursor = await conn.execute(
        """
        SELECT
        coalesce((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.requirement_id) FROM (
          SELECT r.id::text AS requirement_id,r.latest_version AS version,r.code,
          v.document_version_id::text,v.clause_id::text,v.page,v.quote,v.equipment,v.task,
          v.attribute,v.value,v.unit,v.operator,v.standard_system,v.necessity,v.status,
          v.reason,v.unresolved,v.undetermined_items,v.path,v.next_owner_field,
          d.id::text AS document_id,d.project_id::text AS document_project,
          coalesce((SELECT jsonb_agg(jsonb_build_object('name',c.name,'value',c.value,'unit',c.unit) ORDER BY c.ordinal)
            FROM tft.requirement_conditions c WHERE c.requirement_id=r.id AND c.version=v.version),'[]') AS conditions
          FROM tft.requirements r JOIN tft.requirement_versions v ON v.requirement_id=r.id AND v.version=r.latest_version
          JOIN tft.document_versions dv ON dv.id=v.document_version_id JOIN tft.documents d ON d.id=dv.document_id
          WHERE r.project_id=%(p)s AND NOT EXCLUDED_R) q),'[]') AS requirements,
        coalesce((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.document_id,q.version) FROM (
          SELECT d.id::text AS document_id,d.kind,d.title,d.latest_version,
          v.id::text AS document_version_id,v.version,v.sha256
          FROM tft.documents d JOIN tft.document_versions v ON v.document_id=d.id WHERE d.project_id=%(p)s
        ) q),'[]') AS documents,
        coalesce((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.document_version_id,q.ordinal,q.id) FROM (
          SELECT c.id::text,c.document_version_id::text,c.parent_clause_id::text,c.clause_no,c.title,c.text,c.page,c.version,c.ordinal
          FROM tft.clauses c JOIN tft.document_versions v ON v.id=c.document_version_id
          JOIN tft.documents d ON d.id=v.document_id WHERE d.project_id=%(p)s
        ) q),'[]') AS clauses,
        coalesce((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.document_version_id) FROM (
          SELECT g.document_version_id::text,g.source_sha256,g.payload FROM tft.document_diagrams g
          JOIN tft.document_versions v ON v.id=g.document_version_id JOIN tft.documents d ON d.id=v.document_id WHERE d.project_id=%(p)s
        ) q),'[]') AS diagrams,
        coalesce((SELECT max(version) FROM tft.context_versions WHERE project_id=%(p)s),0) AS base_context_version
        """.replace("EXCLUDED_R", EXCLUDED.format(requirement="r.id")),
        {"p": project_id},
    )
    collected = await cursor.fetchone()
    assert collected is not None
    rows, documents, clauses, diagrams = (
        collected[k] for k in ("requirements", "documents", "clauses", "diagrams")
    )
    if any(row["document_project"] != str(project_id) for row in rows):
        raise ValueError("TFT_CONTEXT_SOURCE_PROJECT_MISMATCH")
    by_id = {row["id"]: row for row in clauses}
    diagram_ids = sorted(
        {
            f"{row['document_version_id']}:{item['source_id']}"
            for row in diagrams
            for item in row["payload"]["items"]
            if isinstance(item.get("source_id"), str)
        }
    )
    diagram_by_version: dict[str, set[str]] = {}
    for row in diagrams:
        diagram_by_version.setdefault(row["document_version_id"], set()).update(
            item["source_id"]
            for item in row["payload"]["items"]
            if isinstance(item.get("source_id"), str)
        )
    requirements, refs = [], []
    for row in rows:
        clause = by_id.get(row["clause_id"])
        chain, visited = [], set()
        exact_diagrams: set[str] = set()
        current = clause
        while current is not None:
            if (
                current["id"] in visited
                or current["document_version_id"] != row["document_version_id"]
            ):
                raise ValueError("TFT_CONTEXT_CLAUSE_CHAIN_INVALID")
            visited.add(current["id"])
            if current["clause_no"] in diagram_by_version.get(row["document_version_id"], set()):
                exact_diagrams.add(f"{row['document_version_id']}:{current['clause_no']}")
            chain.append(" ".join(str(p) for p in (current["clause_no"], current["title"]) if p))
            current = by_id.get(current["parent_clause_id"])
        requirements.append(
            ContextRequirement(
                requirement_id=row["requirement_id"],
                version=row["version"],
                document_version_id=row["document_version_id"],
                clause_id=row["clause_id"],
                clause_no=clause["clause_no"] if clause else "",
                section_path=list(reversed(chain)),
                quote=row["quote"],
                equipment=row["equipment"],
                task=row["task"],
                attribute=row["attribute"],
                value=row["value"],
                unit=row["unit"],
                status=row["status"],
                necessity=row["necessity"],
                conditions=[
                    " ".join(str(p) for p in (c["name"], c["value"], c["unit"]) if p)
                    for c in row["conditions"]
                ],
                diagram_source_ids=sorted(exact_diagrams),
            )
        )
        refs.append(SourceReference(**{key: row[key] for key in SourceReference.model_fields}))
    snapshot = {
        "requirements": [r.model_dump(mode="json") for r in requirements],
        "source_refs": [r.model_dump(mode="json") for r in refs],
        "requirement_fields": [
            {key: value for key, value in row.items() if key != "document_project"} for row in rows
        ],
        "documents": documents,
        "clauses": clauses,
        "diagrams": diagrams,
        "diagram_source_ids": diagram_ids,
    }
    raw = encoded(snapshot)
    if len(raw) > 32 * 1024 * 1024:
        raise ApplicationError(
            413, "TFT_CONTEXT_SOURCE_TOO_LARGE", "The context source exceeds the supported size."
        )
    return SourceBundle(
        requirements,
        refs,
        hashlib.sha256(raw).hexdigest(),
        json.loads(raw),
        diagram_ids,
        collected["base_context_version"],
    )


async def lock_source(conn: AsyncConnection[Row], project_id: UUID) -> None:
    """Serialize context/review writers. Source checks are optimistic, not global writer locks."""
    await draft_repository.lock(conn, project_id)


async def latest_row(
    conn: AsyncConnection[Row], project_id: UUID, version: int | None = None
) -> Row | None:
    cursor = await conn.execute(
        "SELECT * FROM tft.context_versions WHERE project_id=%s AND (%s::integer IS NULL OR version=%s) ORDER BY version DESC LIMIT 1",
        (project_id, version, version),
    )
    return await cursor.fetchone()


def response(project_id: UUID, row: Row | None, source: SourceBundle) -> ContextResponse:
    if row is None:
        return ContextResponse(
            project_id=project_id,
            status="NOT_ANALYZED",
            current_source_fingerprint=source.fingerprint,
            requirements=source.requirements,
            source_refs=source.source_refs,
        )
    snap = row["source_snapshot"]
    return ContextResponse(
        project_id=project_id,
        version=row["version"],
        status="CURRENT" if source.fingerprint == row["source_fingerprint"] else "STALE",
        source_fingerprint=row["source_fingerprint"],
        current_source_fingerprint=source.fingerprint,
        provenance=row["provenance"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        reason=row["reason"],
        result=ContextResult.model_validate(row["payload"]),
        requirements=snap["requirements"],
        source_refs=snap["source_refs"],
    )


async def load_context(
    conn: AsyncConnection[Row], project_id: UUID, version: int | None = None
) -> ContextResponse:
    row = await latest_row(conn, project_id, version)
    if version is not None and row is None:
        raise ApplicationError(404, "TFT_CONTEXT_NOT_FOUND", "The context version was not found.")
    return response(project_id, row, await load_source(conn, project_id))


def checked_result(
    result: ContextResult, source: SourceBundle, *, human: bool = False
) -> ContextResult:
    # 안전 검사(validate_context)는 근거 문장의 태그만 보므로 원문 제목 태그로 만든 결정적 정리(KAN-216 설비 묶음,
    # KAN-223 중복)를 지운다. 검사한 결과에 같은 정리를 다시 적용한다(사람 수정도 같은 기준으로 비교한다).
    validated = consolidate(
        validate_context(result, source.requirements, diagram_source_ids=source.diagram_source_ids),
        source.requirements,
        heading_tags(source.snapshot.get("clauses", [])),
    )
    if human and validated.model_dump() != result.model_dump():
        raise ApplicationError(
            422,
            "TFT_CONTEXT_REVIEW_INVALID",
            "Correction contains invalid or incomplete source references.",
        )
    if len(encoded(validated.model_dump(mode="json"))) > 16 * 1024 * 1024:
        raise ApplicationError(
            413, "TFT_CONTEXT_RESULT_TOO_LARGE", "The context result exceeds the supported size."
        )
    return validated


async def append(
    conn: AsyncConnection[Row],
    project_id: UUID,
    actor: UUID,
    source: SourceBundle,
    result: ContextResult,
    *,
    job_id: UUID | None = None,
    reason: str | None = None,
    request_key: str | None = None,
    request_hash: str | None = None,
) -> Row:
    head = await latest_row(conn, project_id)
    version = head["version"] + 1 if head else 1
    cursor = await conn.execute(
        "INSERT INTO tft.context_versions(project_id,version,source_job_id,source_fingerprint,source_snapshot,payload,provenance,created_by,reason,request_key,request_hash) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            project_id,
            version,
            job_id,
            source.fingerprint,
            Jsonb(source.snapshot),
            Jsonb(result.model_dump(mode="json")),
            "ai_draft" if job_id else "human_input",
            actor,
            reason,
            request_key,
            request_hash,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def save_generated(
    conn: AsyncConnection[Row],
    project_id: UUID,
    actor: UUID,
    job_id: UUID,
    source: SourceBundle,
    result: ContextResult,
) -> ContextResponse:
    await lock_source(conn, project_id)
    cursor = await conn.execute(
        "SELECT * FROM tft.context_versions WHERE source_job_id=%s", (job_id,)
    )
    previous = await cursor.fetchone()
    current = await load_source(conn, project_id)
    if previous:
        if (
            previous["project_id"] != project_id
            or previous["source_fingerprint"] != source.fingerprint
        ):
            raise ApplicationError(
                409, "TFT_CONTEXT_JOB_CONFLICT", "The job is bound to another context input."
            )
        return response(project_id, previous, current)
    if current.fingerprint != source.fingerprint:
        raise ApplicationError(
            409,
            "TFT_CONTEXT_SOURCE_CHANGED",
            "Source requirements changed. Run context analysis again.",
        )
    if current.base_context_version != source.base_context_version:
        raise ApplicationError(
            409, "VERSION_CONFLICT", "Context was reviewed while analysis was running."
        )
    result = checked_result(result, current)
    row = await append(conn, project_id, actor, current, result, job_id=job_id)
    return response(project_id, row, current)
