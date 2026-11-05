"""검토본을 요약서 DOCX와 입찰 대응 XLSX(KAN-221)로 만들고 같은 원문 저장소에 보관한다(KAN-188).

PDF 내보내기는 지원하지 않는다. 요약서 DOCX는 검토본에 고정한 판본만, XLSX는 내보낼 때의 판정·결정 기록을 싣는다.
"""

import asyncio
import hashlib
import io
import zipfile
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid5
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from backend.agent.processor_registry import Emit, TftJob
from backend.api.tft.rfp import bid_outputs
from backend.api.tft.rfp import docx_layout as layout
from backend.api.tft.rfp import draft_repository as db
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.commands import audit
from backend.api.tft.rfp.context_report import append_context_report
from backend.api.tft.rfp.context_schemas import ContextResponse
from backend.api.tft.rfp.storage import DocumentStore, DocumentUnavailable, read_document
from backend.engine.rfp.draft import DISCLAIMER
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME = {"DOCX": DOCX_MIME, "XLSX": bid_outputs.XLSX_MIME}
MAX_EXPORT_BYTES = 25 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
KST = ZoneInfo("Asia/Seoul")
# docs/data-contract.md: data_origin은 산출물까지 전달한다. 합성 표시는 지우지 않는다.
ORIGIN_TEXT = {"synthetic": "합성 자료", "demo": "시연 자료(합성·가정 포함)", "real": "실제 자료"}
EVIDENCE_TEXT = {
    "requirement": "요구",
    "clause": "조항",
    "product": "자사 제품",
    "partner": "가상 협력사",
    "question": "질문",
}
SECTION_TITLES: dict[s.DraftSection, str] = {
    "CUSTOMER_GOAL_SCOPE": "고객 목표와 범위",
    "OUR_RESPONSE": "자사 대응",
    "DEVIATIONS": "차이 사항",
    "PARTNER_SUPPLEMENT": "협력사 보완",
    "ASSUMPTIONS_EXCLUSIONS": "가정과 제외",
    "QUESTIONS_NEXT_ACTIONS": "질문과 다음 행동",
}


def require_supported(format: s.ExportFormat) -> None:
    """PDF enum은 유지하되 예약·접수·파일 생성 전에 명시적으로 거부한다."""
    if format not in MIME:
        raise ApplicationError(
            422, "TFT_EXPORT_FORMAT_UNAVAILABLE", "Only DOCX and XLSX export are supported."
        )


def file_name(id: UUID, format: s.ExportFormat) -> str:
    """내려받기·목록이 같이 쓰는 파일 이름."""
    return f"rfp-{id}.{format.lower()}"


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
    """실제 검토본이 있는 예약만 만든다. 같은 요청의 원래 예약은 재사용한다."""
    require_supported(payload.format)
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
        "INSERT INTO tft.exports (id,project_id,review_version,format,created_by,title) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (
            id,
            project_id,
            payload.review_version,
            payload.format,
            actor,
            await db.project_title(conn, project_id),
        ),
    )
    await audit(
        conn,
        project_id=project_id,
        actor=actor,
        entity="export",
        entity_id=id,
        action="export.reserve",
        detail={"review_version": payload.review_version, "format": payload.format},
    )
    return id


def yes_no(value: bool) -> str:
    return "예" if value else "아니오"


def evidence_lines(row: s.Sentence, labels: Mapping[str, str]) -> list[str]:
    """근거를 종류별 한 줄로 묶는다. 번호를 모르는 ID는 빼지 않고 그대로 둔다.

    Args:
        row: 문장.
        labels: 근거 ID → 표시 번호.

    Returns:
        list[str]: 근거 종류 순서를 지킨 줄.
    """
    groups: dict[str, list[str]] = {}
    for evidence in row.evidence:
        groups.setdefault(evidence.type, []).append(labels.get(evidence.id, evidence.id))
    return [
        f"근거 {EVIDENCE_TEXT[kind]} {len(names)}건: {', '.join(names)}"
        for kind, names in groups.items()
    ]


def sentence_flags(row: s.Sentence) -> str:
    return (
        f"미확약: {yes_no(row.unconfirmed)} / 재검토: {yes_no(row.needs_rereview)} / "
        f"근거 누락: {yes_no(row.evidence_missing)}"
    )


# 요약서에서 요구를 하나씩 나열하는 절. 검토자가 읽을 분량만 싣고 나머지는 XLSX 요구별 시트로 넘긴다(2026-11-05).
# 편차·협력사 보완·질의는 결정할 항목이라 모두 싣는다.
# ponytail: 절마다 고정 상한, 문서가 아주 크면 절별 상한을 설정으로 뺀다.
LISTING_SECTIONS = {"CUSTOMER_GOAL_SCOPE", "OUR_RESPONSE", "ASSUMPTIONS_EXCLUSIONS"}
LISTING_LIMIT = 6


def shown(section: s.Section) -> list[s.Sentence]:
    """요약서에 실을 문장. 담당자 입력과 검토 표시가 있는 문장은 늘 싣고, 나머지는 상한까지 원래 순서로."""
    rows = section.sentences
    if section.section not in LISTING_SECTIONS or len(rows) <= LISTING_LIMIT:
        return rows
    must = {
        id(r)
        for r in rows
        if r.provenance_kind == "human_input" or r.needs_rereview or r.evidence_missing
    }
    room = max(LISTING_LIMIT - len(must), 0)
    keep = set(must)
    for r in rows:
        if room == 0:
            break
        if id(r) not in keep:
            keep.add(id(r))
            room -= 1
    return [r for r in rows if id(r) in keep]


def render_docx(
    draft: s.Draft,
    review_version: int,
    labels: Mapping[str, str] | None = None,
    title: str = "",
    context: ContextResponse | None = None,
) -> bytes:
    """고지·모든 절/문장·조건·근거·검토 표시를 실제 OOXML에 보존한다. 파일 생성은 결정적이다."""
    root = ET.Element(W + "document")
    body = ET.SubElement(root, W + "body")

    def paragraph(text: str, style: str = "Normal") -> None:
        layout.paragraph(body, text, style)

    paragraph(DISCLAIMER, "Notice")
    head = [title, f"검토본 {review_version}", f"초안 {draft.version}"]
    paragraph(" / ".join(part for part in head if part), "Title")
    known = draft.known_at.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    paragraph(f"자료 구분: {ORIGIN_TEXT[draft.data_origin]} / 기준 시각: {known} KST", "Meta")
    paragraph(
        "본문 S번호로 근거 부록을 확인하세요. 미확약·재검토·근거 누락의 ‘아니오’는 "
        "해당 표식이 없다는 뜻이며 적합·공급 가능·승인 완료를 의미하지 않습니다.",
        "Meta",
    )
    append_context_report(body, context)
    table_sections = {"OUR_RESPONSE", "DEVIATIONS", "PARTNER_SUPPLEMENT"}
    widths = (650, 3500, 2600, 2730)
    numbered: list[tuple[int, s.Sentence, bool]] = []
    for section in draft.sections:
        paragraph(SECTION_TITLES[section.section], "Heading1")
        if not section.sentences:
            paragraph("등록된 문장 없음", "Meta")
            continue
        table = (
            layout.table(body, ("문장", "대응 내용", "조건·확인사항", "근거 / 검토 표시"), widths)
            if section.section in table_sections
            else None
        )
        rows = shown(section)
        for row in rows:
            number = len(numbered) + 1
            numbered.append((number, row, table is not None))
            flag_text = sentence_flags(row)
            if table is not None:
                cells = layout.table_row(table, widths)
                layout.paragraph(cells[0], f"S{number}", "TableText")
                layout.sentence_link(cells[0], number)
                layout.paragraph(cells[1], row.text, "TableText")
                for condition in row.conditions or ["등록 조건 없음"]:
                    layout.paragraph(cells[2], condition, "TableText")
                for line in evidence_lines(row, labels or {}) or ["연결 근거 없음"]:
                    layout.paragraph(cells[3], line, "TableText")
                layout.paragraph(cells[3], flag_text, "TableText")
            else:
                paragraph(row.text)
                layout.sentence_link(body, number)
                for condition in row.conditions:
                    paragraph(f"조건·미확인: {condition}", "Meta")
        if len(rows) < len(section.sentences):
            paragraph(
                f"이 절의 나머지 {len(section.sentences) - len(rows)}건은 "
                "대응 현황표(XLSX)의 요구별 시트에서 확인합니다.",
                "Meta",
            )
        if table is not None:
            paragraph("", "Meta")
    paragraph("부록 · 문장별 근거와 검토 기록", "Heading1")
    # 2026-11-05 분량 정리: 본문에 있는 문장·조건과 표에 이미 쓴 근거는 되풀이하지 않는다.
    # 검토 표시는 하나라도 '예'인 문장만, 작성 유형은 AI 초안이 아닌 문장만 적는다.
    paragraph(
        "본문과 같은 S번호의 근거와 검토 표시입니다. 표에 근거를 쓴 문장은 번호만 있고, "
        "검토 표시가 없는 문장은 미확약·재검토·근거 누락이 모두 ‘아니오’입니다.",
        "Meta",
    )
    provenance = {
        "ai_draft": "AI 작성 초안",
        "human_input": "담당자 입력",
        "system_record": "양식·시스템 기록",
    }
    for number, row, in_table in numbered:
        layout.sentence_heading(body, number)
        if not in_table:
            for line in evidence_lines(row, labels or {}) or ["연결 근거 없음"]:
                paragraph(line, "Meta")
        if row.unconfirmed or row.needs_rereview or row.evidence_missing:
            paragraph(sentence_flags(row), "Meta")
        if row.provenance_kind != "ai_draft":
            paragraph(f"작성 유형: {provenance[row.provenance_kind]}", "Meta")
    paragraph(DISCLAIMER, "Notice")
    for node in root.iter(W + "t"):
        if node.text:
            node.text = bid_outputs.readable_text(node.text)
    entries = layout.package_parts(root, body, ORIGIN_TEXT[draft.data_origin])
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
    require_supported(row["format"])
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
    key = f"rfp/{job.project_id}/exports/{row['id']}.{row['format'].lower()}"
    if row["format"] == "XLSX":
        # XLSX는 내보낼 때의 판정·결정 기록이라 다시 만들면 달라질 수 있다. 저장 직후 DB 기록 전에 멈춘 재시도는
        # 이 내보내기로 이미 만든 파일을 그대로 쓴다(덮어쓰지 않는다). 기준 시각은 예약 시각이다.
        try:
            data = await store.get(key)
        except DocumentUnavailable:
            outputs = await bid_outputs.collect(
                conn, job.project_id, row["review_version"], row["created_at"], row["title"] or ""
            )
            data = await asyncio.to_thread(bid_outputs.render_xlsx, outputs)
    else:
        draft = await db.load_snapshot(conn, job.project_id, review["draft_version"])
        if draft is None:
            raise ValueError("TFT_DRAFT_NOT_FOUND")
        labels = await db.evidence_labels(conn, job.project_id)
        context = await db.load_review_context(conn, job.project_id, row["review_version"])
        data = await asyncio.to_thread(
            render_docx, draft, row["review_version"], labels, row["title"] or "", context
        )
    if len(data) > MAX_EXPORT_BYTES:
        raise ValueError("TFT_EXPORT_TOO_LARGE")
    digest = hashlib.sha256(data).hexdigest()
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
        project_id=job.project_id,
        actor=job.actor_id,
        entity="export",
        entity_id=row["id"],
        action="export.complete",
        detail={"job_id": str(job.job_id), "sha256": digest, "size": len(data)},
    )
    return {"counts": {"requirements": 0, "proposals": 0}, "needs_review": False}
