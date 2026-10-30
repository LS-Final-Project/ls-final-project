"""불변 검토본을 실제 DOCX로 만들고 같은 원문 저장소에 보관한다(KAN-188). PDF는 후속이다."""

import asyncio
import hashlib
import io
import zipfile
from typing import Any
from uuid import UUID, uuid5
from xml.etree import ElementTree as ET

from psycopg import AsyncConnection

from backend.agent.processor_registry import Emit, TftJob
from backend.api.tft.rfp import draft_repository as db
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.drafts import audit
from backend.api.tft.rfp.storage import DocumentStore, DocumentUnavailable, read_document
from backend.engine.rfp.draft import DISCLAIMER
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_EXPORT_BYTES = 25 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SECTION_TITLES: dict[s.DraftSection, str] = {
    "CUSTOMER_GOAL_SCOPE": "고객 목표·제안 검토 범위",
    "OUR_RESPONSE": "자사 후보",
    "DEVIATIONS": "사양 차이·대체",
    "PARTNER_SUPPLEMENT": "협력사 보완",
    "ASSUMPTIONS_EXCLUSIONS": "전제·제외·미확인",
    "QUESTIONS_NEXT_ACTIONS": "질문·다음 행동",
}


def require_docx(format: s.ExportFormat) -> None:
    """PDF enum은 유지하되 예약·접수·파일 생성 전에 명시적으로 거부한다."""
    if format != "DOCX":
        raise ApplicationError(
            422, "TFT_EXPORT_FORMAT_UNAVAILABLE", "PDF export is not available yet."
        )


def export_id(project_id: UUID, actor: UUID, key: str) -> UUID:
    """재전송·동시 접수에 같은 예약을 쓰는 식별자. 본문 차이는 예약 검사로 거부한다."""
    return uuid5(project_id, f"export:{actor}:{key}")


def input_version(id: UUID) -> str:
    """플랫폼 작업과 예약을 연결하는 불변 입력 판본."""
    return hashlib.sha256(str(id).encode()).hexdigest()


async def reserve(
    conn: AsyncConnection[Row],
    project_id: UUID,
    actor: UUID,
    key: str,
    payload: s.ExportRequest,
) -> UUID:
    """실제 검토본이 있는 DOCX 예약만 만든다. 같은 요청의 원래 예약은 재사용한다."""
    require_docx(payload.format)
    id = export_id(project_id, actor, key)
    await db.lock(conn, project_id)
    cursor = await conn.execute("SELECT * FROM tft.exports WHERE id=%s", (id,))
    existing = await cursor.fetchone()
    if existing:
        if (
            existing["review_version"] != payload.review_version
            or existing["format"] != payload.format
        ):
            raise ApplicationError(
                409, "IDEMPOTENCY_CONFLICT", "The key was used for another export."
            )
        return id
    cursor = await conn.execute(
        "SELECT 1 FROM tft.review_versions WHERE project_id=%s AND review_version=%s",
        (project_id, payload.review_version),
    )
    if await cursor.fetchone() is None:
        raise ApplicationError(
            404, "TFT_REVIEW_VERSION_NOT_FOUND", "The review version was not found."
        )
    await conn.execute(
        "INSERT INTO tft.exports (id,project_id,review_version,format,created_by) VALUES (%s,%s,%s,%s,%s)",
        (id, project_id, payload.review_version, payload.format, actor),
    )
    await audit(
        conn,
        project_id,
        actor,
        "export",
        str(id),
        "export.reserve",
        {"review_version": payload.review_version, "format": payload.format},
    )
    return id


def render_docx(draft: s.Draft, review_version: int) -> bytes:
    """고지·모든 절/문장·조건·근거·검토 표시를 실제 OOXML에 보존한다. 파일 생성은 결정적이다."""
    root = ET.Element(W + "document")
    body = ET.SubElement(root, W + "body")

    def paragraph(text: str) -> None:
        if any(
            not (
                c in "\n\r\t"
                or 0x20 <= ord(c) <= 0xD7FF
                or 0xE000 <= ord(c) <= 0xFFFD
                or 0x10000 <= ord(c) <= 0x10FFFF
            )
            for c in text
        ):
            raise ValueError("TFT_EXPORT_INVALID_CHARACTER")
        para = ET.SubElement(body, W + "p")
        run = ET.SubElement(para, W + "r")
        for index, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
            if index:
                ET.SubElement(run, W + "br")
            for tab_index, part in enumerate(line.split("\t")):
                if tab_index:
                    ET.SubElement(run, W + "tab")
                ET.SubElement(
                    run, W + "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}
                ).text = part

    paragraph(DISCLAIMER)
    paragraph(f"프로젝트 {draft.project_id} / 검토본 {review_version} / 초안 {draft.version}")
    paragraph(f"데이터 출처: {draft.data_origin} / 기준 시각: {draft.known_at.isoformat()}")
    for section in draft.sections:
        paragraph(SECTION_TITLES[section.section])
        for row in section.sentences:
            paragraph(row.text)
            paragraph(f"문장 ID: {row.sentence_id}")
            for condition in row.conditions:
                paragraph(f"조건·미확인: {condition}")
            for evidence in row.evidence:
                paragraph(f"근거 [{evidence.type}]: {evidence.id}")
            paragraph(
                f"미확약: {row.unconfirmed} / 재검토: {row.needs_rereview} / 근거 누락: {row.evidence_missing}"
            )
    paragraph(DISCLAIMER)
    entries = {
        "[Content_Types].xml": b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    data = out.getvalue()
    if len(data) > MAX_EXPORT_BYTES:
        raise ValueError("TFT_EXPORT_TOO_LARGE")
    return data


async def process(
    conn: AsyncConnection[Row], job: TftJob, emit: Emit, store: DocumentStore | None
) -> Row:
    """예약의 검토 판본만 내보낸다. 재시도는 같은 파일/완료 행을 재사용한다."""
    if store is None:
        raise ValueError("TFT_DOCUMENT_ROOT_MISSING")
    await db.lock(conn, job.project_id)
    cursor = await conn.execute(
        "SELECT * FROM tft.exports WHERE project_id=%s ORDER BY id FOR UPDATE", (job.project_id,)
    )
    rows = [row for row in await cursor.fetchall() if input_version(row["id"]) == job.input_version]
    if len(rows) != 1:
        raise ValueError("TFT_EXPORT_NOT_FOUND")
    row = rows[0]
    if row["job_id"] not in (None, job.job_id) or row["created_by"] != job.actor_id:
        raise ValueError("TFT_EXPORT_JOB_MISMATCH")
    require_docx(row["format"])
    emit({"step": "export", "counts": {"requirements": 0, "proposals": 0}})
    if row["completed_at"] is not None:
        await read_document(store, row["storage_key"], row["sha256"], row["size"])
        return {"counts": {"requirements": 0, "proposals": 0}, "needs_review": False}
    cursor = await conn.execute(
        "SELECT draft_version FROM tft.review_versions WHERE project_id=%s AND review_version=%s",
        (job.project_id, row["review_version"]),
    )
    review = await cursor.fetchone()
    if review is None:
        raise ValueError("TFT_REVIEW_VERSION_NOT_FOUND")
    draft = await db.load(conn, job.project_id, review["draft_version"])
    if draft is None:
        raise ValueError("TFT_DRAFT_NOT_FOUND")
    data = await asyncio.to_thread(render_docx, draft, row["review_version"])
    digest = hashlib.sha256(data).hexdigest()
    key = f"rfp/{job.project_id}/exports/{row['id']}.docx"
    try:
        await store.put(key, data)
    except DocumentUnavailable:
        # 생성 후 DB commit 전에 중단됐으면 객체만 남을 수 있다. 다른 바이트를 덮어쓰지 않는다.
        if await store.get(key) != data:
            raise DocumentUnavailable("TFT_EXPORT_STORAGE_MISMATCH") from None
    emit({"step": "export", "counts": {"requirements": 0, "proposals": 0}})
    await conn.execute(
        "UPDATE tft.exports SET job_id=%s,storage_key=%s,sha256=%s,size=%s,completed_at=now() WHERE id=%s",
        (job.job_id, key, digest, len(data), row["id"]),
    )
    await audit(
        conn,
        job.project_id,
        job.actor_id,
        "export",
        str(row["id"]),
        "export.complete",
        {"job_id": str(job.job_id), "sha256": digest, "size": len(data)},
    )
    return {"counts": {"requirements": 0, "proposals": 0}, "needs_review": False}
