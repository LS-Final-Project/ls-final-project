"""입찰 대응 XLSX(편차표·질의서·현황표·요구별, KAN-221).

시트 칸은 작업 공간 출력 탭(보드)과 같다. 판정·결정은 KAN-220 `decisions.states`, 중복은 검토본에 고정한 맥락의
KAN-223 `duplicates`(대표 한 행 + 출처 조항), 뺀 요구(KAN-225)는 넣지 않는다. 결정·편차·질의는 내보낼 때의 기록이다.
XLSX는 표준 라이브러리 zip·XML로 만든다(새 의존성 없음). 같은 입력이면 같은 바이트다.
"""

import io
import re
import zipfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from backend.api.tft.rfp import draft_repository as db
from backend.api.tft.rfp import drafts
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.decisions import VERDICT_WORDS, gate, states
from backend.api.tft.rfp.docx_layout import check_text
from backend.api.tft.rfp.grouping import equipment_groups

Row = dict[str, Any]
Cell = str | int
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
KST = ZoneInfo("Asia/Seoul")
ORIGIN_TEXT = {"synthetic": "합성 자료", "demo": "시연 자료(합성·가정 포함)", "real": "실제 자료"}
NECESSITY = {
    "MANDATORY": "필수",
    "CONDITION_BOUND": "조건부 필수",
    "PREFERRED": "선호",
    "UNDETERMINED": "미정",
}
OWNER = {"SALES": "영업", "TECH": "기술지원", "PURCHASE": "구매", "CUSTOMER": "고객", "RND": "R&D"}
OPERATOR = {"GTE": " 이상", "LTE": " 이하"}
QUESTION_STATE = {"OPEN": "회신 대기", "REPLIED": "회신 받음"}
COMMON = "공통"


@dataclass(frozen=True)
class Sheet:
    name: str
    headers: tuple[str, ...]
    widths: tuple[int, ...]
    rows: list[list[Cell]]


@dataclass(frozen=True)
class Outputs:
    meta: str
    sheets: list[Sheet]


def clause_key(clause: str) -> tuple[tuple[int, int | str], ...]:
    """원문 조항 순서. "제6조.6"·"2.1.A"를 숫자는 숫자로 비교한다."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.findall(r"\d+|[^\d.]+", clause)
    )


def gate_text(found: s.DecisionGate) -> str:
    left = [
        (name, n)
        for name, n in (
            ("판정 안 된 요구", found.undecided),
            ("편차표 누락", found.deviation_missing),
            ("회신 대기 질의", found.open_queries),
            ("책임자 확인 대기", found.review_pending),
        )
        if n
    ]
    if not left:
        return "저장 조건 충족"
    return "저장 조건 미충족: " + ", ".join(f"{name} {n}" for name, n in left)


def requirement_value(req: s.RequirementDetail) -> str:
    spec = req.spec
    if not spec.value:
        return ""
    unit = " " + spec.unit if spec.unit else ""
    tolerance = " " + spec.tolerance if spec.tolerance else ""
    return f"{spec.value}{unit}{tolerance}{OPERATOR.get(spec.operator, '')}"


async def collect(
    conn: AsyncConnection[Row], project_id: UUID, review_version: int, as_of: datetime, title: str
) -> Outputs:
    """네 시트를 저장된 기록에서 만든다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        review_version: 내보내는 검토본 판본(맥락·중복은 이 판본에 고정한 것).
        as_of: 머리 줄의 기준 시각(내보내기 예약 시각).
        title: 머리 줄의 프로젝트 이름(예약 때 고정한 값, worker는 프로젝트 이름 열을 읽지 않는다).

    Returns:
        Outputs: 머리 줄과 시트 넷.
    """
    requirements = await drafts.requirements_of(conn, project_id)  # 뺀 요구는 이미 빠져 있다.
    found = await states(conn, project_id)
    context = await db.load_review_context(conn, project_id, review_version)
    result = context.result if context else None
    sources: dict[str, list[str]] = {}
    hidden: set[str] = set()
    for dup in result.duplicates if result else []:
        sources[dup.canonical_requirement_id] = dup.clause_nos
        hidden |= set(dup.requirement_ids) - {dup.canonical_requirement_id}
    # 화면 묶음(KAN-231 grouping.equipment_groups)과 같은 규칙: 배정의 첫 설비 묶음, 없으면 근거로 든 설비 묶음.
    equipment = {
        rid: g.tag or g.name for rid, g in (equipment_groups(result) if result else {}).items()
    }
    rows = sorted(
        (r for r in requirements if str(r.requirement_id) not in hidden),
        key=lambda r: (clause_key(r.origin.clause_no or ""), r.code),
    )
    # 질의는 합친 중복 요구에 걸려 있어도 남긴다. 뺀 요구(requirements_of에 없음)의 질의만 빠진다.
    known = {r.requirement_id: r for r in requirements}

    def eq(req: s.RequirementDetail) -> str:
        return equipment.get(str(req.requirement_id)) or req.spec.equipment or COMMON

    cursor = await conn.execute(
        "SELECT DISTINCT ON (d.requirement_id) d.requirement_id, d.text, d.state, d.requirement_version, "
        "req.latest_version FROM tft.deviations d JOIN tft.requirements req ON req.id = d.requirement_id "
        "WHERE d.project_id = %s ORDER BY d.requirement_id, d.created_at DESC, d.id DESC",
        (project_id,),
    )
    deviations = {
        d["requirement_id"]: d for d in await cursor.fetchall() if d["state"] == "RECORDED"
    }
    deviation_rows: list[list[Cell]] = []
    for req in rows:
        d = deviations.get(req.requirement_id)
        if d is None:
            continue
        deviation_rows.append(
            [
                len(deviation_rows) + 1,
                req.origin.clause_no or "",
                eq(req),
                req.spec.attribute or req.spec.task or "",
                requirement_value(req) or req.origin.quote,
                req.links.comparison_summary or "",
                d["text"],
                "" if d["requirement_version"] == d["latest_version"] else "요구가 바뀜, 다시 확인",
            ]
        )

    cursor = await conn.execute(
        "SELECT q.id, q.requirement_id, q.question_text, q.status, q.follows_question_id, "
        "r.reply_text, r.received_at FROM tft.customer_questions q "
        "LEFT JOIN tft.question_replies r ON r.question_id = q.id "
        "WHERE q.project_id = %s AND q.target = 'CUSTOMER' AND q.status IN ('OPEN', 'REPLIED') "
        "AND NOT EXISTS (SELECT 1 FROM tft.question_withdrawals w WHERE w.question_id = q.id) "
        "ORDER BY q.created_at, q.id",
        (project_id,),
    )
    # 요구 없는 질의는 조항을 비운다.
    questions = [
        q
        for q in await cursor.fetchall()
        if q["requirement_id"] is None or q["requirement_id"] in known
    ]
    numbers: dict[UUID, str] = {}
    follow_count: Counter[UUID] = Counter()
    top = 0
    query_rows: list[list[Cell]] = []
    open_by_eq: Counter[str] = Counter()
    for q in questions:
        parent = q["follows_question_id"]
        if parent in numbers:
            follow_count[parent] += 1
            numbers[q["id"]] = f"{numbers[parent]}-{follow_count[parent] + 1}"
        else:
            top += 1
            numbers[q["id"]] = f"TQ-{top}"
        asked = known.get(q["requirement_id"])
        if q["status"] == "OPEN":
            open_by_eq[eq(asked) if asked else COMMON] += 1
        received = q["received_at"]
        query_rows.append(
            [
                numbers[q["id"]],
                asked.origin.clause_no or "" if asked else "",
                eq(asked) if asked else COMMON,
                q["question_text"],
                QUESTION_STATE[q["status"]],
                q["reply_text"] or "",
                received.astimezone(KST).strftime("%Y-%m-%d") if received else "",
            ]
        )

    tally: dict[str, Counter[str]] = {}
    for req in rows:
        verdict, state = found[req.requirement_id]
        counts = tally.setdefault(eq(req), Counter())
        counts["MET" if verdict == "CONFIRM" else verdict] += 1
        counts["left"] += state.needed and not state.decided
    columns = ("MET", "DEVIATION", "QUERY", "SUPPLEMENT", "ASSIGN", "left")
    status_rows: list[list[Cell]] = [
        [
            name,
            *(counts[c] for c in columns),
            f"회신 대기 질의 {open_by_eq[name]}" if open_by_eq[name] else "",
        ]
        for name, counts in sorted(tally.items(), key=lambda kv: (kv[0] == COMMON, kv[0]))
    ]
    total = sum(tally.values(), Counter())
    status_rows.append(["합계", *(total[c] for c in columns), ""])

    requirement_rows: list[list[Cell]] = []
    for number, req in enumerate(rows, 1):
        verdict, state = found[req.requirement_id]
        review = "확인함" if state.reviewed else "확인 대기" if state.decided else ""
        requirement_rows.append(
            [
                number,
                req.code,
                req.origin.clause_no or "",
                eq(req),
                req.spec.attribute or req.spec.task or "",
                requirement_value(req),
                NECESSITY[req.spec.necessity],
                req.origin.quote,
                VERDICT_WORDS[verdict],
                state.summary or "",
                review,
                req.judgement.reason,
                OWNER.get(req.judgement.next_owner_field or "", ""),
                "예" if state.manually_added else "",
                ", ".join(sources.get(str(req.requirement_id), [])),
            ]
        )

    project = await conn.execute(
        "SELECT data_origin FROM tft.projects WHERE id = %s", (project_id,)
    )
    origin = (await project.fetchone() or {}).get("data_origin", "real")
    decided_gate = await gate(conn, project_id)
    now = as_of.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    meta = (
        f"{title + ' | ' if title else ''}검토본 {review_version} | "
        f"{ORIGIN_TEXT[origin]} | 기준 {now} KST | {gate_text(decided_gate)}"
    )
    return Outputs(
        meta=meta,
        sheets=[
            Sheet(
                "편차표",
                ("No", "조항", "설비", "항목", "요구", "자사 제안", "편차 내용", "확인"),
                (6, 12, 16, 22, 26, 36, 50, 18),
                deviation_rows,
            ),
            Sheet(
                "질의서",
                ("No", "조항", "설비", "질의", "상태", "회신", "회신 받은 날"),
                (9, 12, 16, 60, 11, 50, 13),
                query_rows,
            ),
            Sheet(
                "현황표",
                ("설비", "충족", "편차", "질의", "보완", "배정", "남은 결정", "남은 조건"),
                (24, 8, 8, 8, 8, 8, 10, 22),
                status_rows,
            ),
            Sheet(
                "요구별",
                (
                    "No",
                    "요구 번호",
                    "조항",
                    "설비",
                    "항목",
                    "요구값",
                    "필수",
                    "원문",
                    "판정",
                    "결정",
                    "책임자 확인",
                    "판단 근거",
                    "다음 담당",
                    "직접 추가",
                    "같은 요구 출처",
                ),
                (6, 12, 12, 16, 22, 16, 10, 50, 10, 30, 10, 40, 10, 9, 24),
                requirement_rows,
            ),
        ],
    )


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def cell(row: ET.Element, ref: str, value: Cell, style: int) -> None:
    node = ET.SubElement(row, "c", r=ref, s=str(style))
    if isinstance(value, int):
        ET.SubElement(node, "v").text = str(value)
        return
    node.set("t", "inlineStr")
    text = ET.SubElement(ET.SubElement(node, "is"), "t")
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = check_text(value)


def worksheet(sheet: Sheet, meta: str) -> ET.Element:
    """1행 머리(검토본·자료 구분·저장 조건), 2행 칸 이름, 3행부터 자료. 칸 이름 행까지 고정한다."""
    root = ET.Element("worksheet", xmlns=MAIN)
    view = ET.SubElement(ET.SubElement(root, "sheetViews"), "sheetView", workbookViewId="0")
    ET.SubElement(
        view, "pane", ySplit="2", topLeftCell="A3", activePane="bottomLeft", state="frozen"
    )
    cols = ET.SubElement(root, "cols")
    for i, width in enumerate(sheet.widths, 1):
        ET.SubElement(cols, "col", min=str(i), max=str(i), width=str(width), customWidth="1")
    data = ET.SubElement(root, "sheetData")
    lines: list[tuple[Sequence[Cell], int]] = [([meta], 3), (sheet.headers, 1)]
    lines += [(values, 2) for values in sheet.rows]
    for number, (values, style) in enumerate(lines, 1):
        row = ET.SubElement(data, "row", r=str(number))
        for i, value in enumerate(values):
            cell(row, f"{column(i)}{number}", value, style)
    if sheet.headers:
        last = column(len(sheet.headers) - 1)
        ET.SubElement(root, "autoFilter", ref=f"A2:{last}{max(len(lines), 2)}")
    return root


STYLES = (
    f'<styleSheet xmlns="{MAIN}">'
    '<fonts count="2"><font><sz val="10"/><name val="맑은 고딕"/></font>'
    '<font><b/><sz val="10"/><name val="맑은 고딕"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFEAF0F6"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def render_xlsx(outputs: Outputs) -> bytes:
    """네 시트를 한 XLSX로 만든다. 날짜를 고정해 같은 입력은 같은 바이트다."""
    parts: dict[str, bytes] = {}

    def put(name: str, root: ET.Element) -> None:
        parts[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    types = ET.Element(
        "Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types"
    )
    ET.SubElement(
        types,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    ET.SubElement(types, "Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(
        types,
        "Override",
        PartName="/xl/workbook.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    ET.SubElement(
        types,
        "Override",
        PartName="/xl/styles.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
    )
    book = ET.Element("workbook", {"xmlns": MAIN, "xmlns:r": REL})
    sheets = ET.SubElement(book, "sheets")
    links = ET.Element("Relationships", xmlns=PKG)
    for i, sheet in enumerate(outputs.sheets, 1):
        ET.SubElement(
            types,
            "Override",
            PartName=f"/xl/worksheets/sheet{i}.xml",
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
        )
        ET.SubElement(sheets, "sheet", {"name": sheet.name, "sheetId": str(i), "r:id": f"rId{i}"})
        ET.SubElement(
            links,
            "Relationship",
            Id=f"rId{i}",
            Type=REL + "/worksheet",
            Target=f"worksheets/sheet{i}.xml",
        )
        put(f"xl/worksheets/sheet{i}.xml", worksheet(sheet, outputs.meta))
    ET.SubElement(links, "Relationship", Id="rIdStyles", Type=REL + "/styles", Target="styles.xml")
    root_links = ET.Element("Relationships", xmlns=PKG)
    ET.SubElement(
        root_links,
        "Relationship",
        Id="rId1",
        Type=REL + "/officeDocument",
        Target="xl/workbook.xml",
    )
    put("[Content_Types].xml", types)
    put("_rels/.rels", root_links)
    put("xl/workbook.xml", book)
    put("xl/_rels/workbook.xml.rels", links)
    parts["xl/styles.xml"] = b'<?xml version="1.0" encoding="utf-8"?>' + STYLES.encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in sorted(parts.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return out.getvalue()
