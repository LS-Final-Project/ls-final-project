"""경로 7~10·12~14·17: 문서·조항·요구·비교·협력사 후보 읽기(KAN-188).

범위 밖 프로젝트·자원은 없는 것과 같이 404다(routes_questions.visible_project). 저장 행을 응답으로 바꾸는 규칙은
이 파일에만 둔다. 문서 경로는 문서의 최신 판본 기준이다(명세에 판본 지정 인자가 없다).
"""

import base64
import binascii
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import Request, Response
from psycopg import AsyncConnection

from backend.api.tft.partners import grading
from backend.api.tft.partners.loader import profile_from_row
from backend.api.tft.rfp import repository, storage
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.routes_questions import visible_project
from backend.api.tft.rfp.routes_supplement import requirement_project
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
PROJECT_NOT_FOUND = ("TFT_PROJECT_NOT_FOUND", "The project was not found.")
DOCUMENT_NOT_FOUND = ("TFT_DOCUMENT_NOT_FOUND", "The document was not found.")
RESULT_WORDS = {
    "MEETS": "충족",
    "DIFFERS": "차이",
    "NOT_COMPARABLE": "비교 불가",
    "NO_DATA": "자료 없음",
}


def encode_cursor(value: str | int) -> str:
    """쪽 커서를 만든다.

    Args:
        value: 마지막 행의 정렬 값.

    Returns:
        str: URL에 안전한 커서.
    """
    return base64.urlsafe_b64encode(json.dumps({"k": value}).encode()).decode()


def decode_cursor(value: str | None, kind: type) -> Any:
    """클라이언트가 보낸 커서를 읽는다.

    Args:
        value: 커서(없으면 None).
        kind: 정렬 값의 형식(str·int).

    Returns:
        Any: 정렬 값. 형식이 틀리면 400 TFT_INVALID_CURSOR다.
    """
    if value is None:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(value.encode()))
    except (binascii.Error, ValueError):
        data = None
    # bool은 int의 하위 형식이라 따로 막는다.
    if not isinstance(data, dict) or set(data) != {"k"} or type(data["k"]) is not kind:
        raise ApplicationError(400, "TFT_INVALID_CURSOR", "The page cursor is invalid.")
    return data["k"]


def document_out(row: Mapping[str, Any]) -> dict[str, Any]:
    """문서 판본 행 → 응답 칸.

    Args:
        row: list_document_versions·latest_document_version 행.

    Returns:
        dict: DocumentVersion 입력.
    """
    return {
        "version": row["version"],
        "data_origin": row["data_origin"],
        "derivation_kind": row["derivation_kind"],
        "known_at": row["known_at"],
        "document_id": row["document_id"],
        "document_version_id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "mime": row["mime"],
        "sha256": row["sha256"],
        "size": row["size"],
        "edit_trace_support": row["edit_trace_support"],
        "base_document_id": row["base_document_id"],
        "uploaded_by": row["uploaded_by"],
        "uploaded_at": row["uploaded_at"],
    }


async def visible_document(
    conn: AsyncConnection[Row], principal: Principal, document_id: UUID
) -> Row:
    """볼 수 있는 문서의 최신 판본. 없거나 범위 밖이면 404 TFT_DOCUMENT_NOT_FOUND다.

    Args:
        conn: dict_row 연결.
        principal: 요청 사용자.
        document_id: 문서 ID.

    Returns:
        Row: latest_document_version 행.
    """
    project_id = await repository.document_project(conn, document_id)
    if project_id is None:
        raise ApplicationError(404, *DOCUMENT_NOT_FOUND)
    await visible_project(conn, principal, project_id, *DOCUMENT_NOT_FOUND)
    row = await repository.latest_document_version(conn, document_id)
    if row is None:
        raise ApplicationError(404, *DOCUMENT_NOT_FOUND)
    return row


async def list_documents(
    request: Request, principal: Principal, project_id: UUID
) -> s.DocumentList:
    """7. 문서·판본 목록.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.

    Returns:
        s.DocumentList: 판본 전부.
    """
    async with connection(request.app.state.settings) as conn:
        await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        rows = await repository.list_document_versions(conn, project_id)
    return s.DocumentList(items=[s.DocumentVersion(**document_out(row)) for row in rows])


async def get_document(
    request: Request, principal: Principal, document_id: UUID
) -> s.DocumentDetail:
    """8. 문서 최신 판본과 조항·삭제 값 수.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        document_id: 문서 ID.

    Returns:
        s.DocumentDetail: 문서 상세.
    """
    async with connection(request.app.state.settings) as conn:
        row = await visible_document(conn, principal, document_id)
    return s.DocumentDetail(
        **document_out(row),
        clauses_count=row["clauses_count"],
        deleted_values_count=row["deleted_values_count"],
    )


def clause_out(row: Mapping[str, Any], numbers: Mapping[str, UUID]) -> s.Clause:
    """조항 행 → 응답 모델. 취소선 값은 deleted_values로만 낸다.

    Args:
        row: list_clauses 행.
        numbers: 같은 판본의 조항 번호 → ID.

    Returns:
        s.Clause: 응답 조항.
    """
    return s.Clause(
        clause_id=row["id"],
        clause_no=row["clause_no"],
        parent_clause_id=row["parent_clause_id"],
        depth=row["depth"],
        title=row["title"],
        text=row["text"],
        page=row["page"],
        deleted_values=row["deleted_values"],
        flags=row["flags"],
        cross_references=[
            s.CrossReference(
                target=ref["target_hint"],
                resolved_clause_id=numbers.get(ref["target_hint"]) if ref["resolved"] else None,
            )
            for ref in row["cross_references"]
        ],
        requirement_ids=row["requirement_ids"],
    )


async def clause_source(conn: AsyncConnection[Row], principal: Principal, target: UUID) -> Row:
    """조항을 읽을 판본. 요구는 인용한 판본 ID로 오고, 문서 ID면 최신 판본이다.

    Args:
        conn: dict_row 연결.
        principal: 요청 사용자.
        target: 문서 판본 ID 또는 문서 ID.

    Returns:
        Row: 문서 판본 행. 없거나 범위 밖이면 404다.
    """
    version = await repository.document_version_row(conn, target)
    if version is None:
        return await visible_document(conn, principal, target)
    await visible_project(conn, principal, version["project_id"], *DOCUMENT_NOT_FOUND)
    return version


async def list_clauses(
    request: Request,
    principal: Principal,
    document_id: UUID,
    *,
    parent: UUID | None,
    limit: int,
    cursor: str | None,
) -> s.ClauseList:
    """9. 최신 판본의 조항. parent가 없으면 전체 조항을 문서 순서로 낸다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        document_id: 문서 ID.
        parent: 이 조항의 바로 아래만.
        limit: 쪽 크기.
        cursor: 쪽 커서.

    Returns:
        s.ClauseList: 조항과 다음 커서.
    """
    after = decode_cursor(cursor, int)
    async with connection(request.app.state.settings) as conn:
        document = await clause_source(conn, principal, document_id)
        rows = await repository.list_clauses(
            conn, document["id"], parent=parent, after=after, limit=limit
        )
        numbers = await repository.clause_ids_by_no(conn, document["id"])
    page = rows[:limit]
    more = len(rows) > limit
    return s.ClauseList(
        items=[clause_out(row, numbers) for row in page],
        next_cursor=encode_cursor(page[-1]["ordinal"]) if more else None,
    )


async def download_document(request: Request, principal: Principal, document_id: UUID) -> Response:
    """10. 최신 판본 원문. 접근 검사는 프로젝트 조회와 같고, 저장 해시·크기가 맞을 때만 낸다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        document_id: 문서 ID.

    Returns:
        Response: 항상 내려받기(attachment·nosniff). 저장소를 못 쓰면 503 TFT_DOCUMENT_UNAVAILABLE이고 경로는
            응답에 넣지 않는다.
    """
    async with connection(request.app.state.settings) as conn:
        row = await visible_document(conn, principal, document_id)
    store = storage.document_store(storage.DocumentStorageSettings())
    unavailable = ApplicationError(
        503, "TFT_DOCUMENT_UNAVAILABLE", "The document file is not available."
    )
    if store is None:
        raise unavailable
    try:
        data = await storage.read_document(store, row["storage_key"], row["sha256"], row["size"])
    except storage.DocumentUnavailable:
        raise unavailable from None
    return Response(
        content=data,
        media_type=row["mime"],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row['title'], safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def requirement_cursor(value: str | None) -> tuple[int, str] | None:
    """요구 목록 커서 값(상태 순위|code)을 읽는다.

    Args:
        value: decode_cursor 결과(없으면 None).

    Returns:
        tuple[int, str] | None: (상태 순위, code). 형식이 틀리면 400 TFT_INVALID_CURSOR다.
    """
    if value is None:
        return None
    rank, separator, code = value.partition("|")
    if not separator or not rank.isdecimal() or not code:
        raise ApplicationError(400, "TFT_INVALID_CURSOR", "The page cursor is invalid.")
    return int(rank), code


async def summary_of(conn: AsyncConnection[Row], row: Mapping[str, Any]) -> s.RequirementSummary:
    """요구 행 → 목록 칸. 요구 응답에 칸을 더할 때는 이 함수와 detail_of만 고친다.

    Args:
        conn: dict_row 연결.
        row: list_requirements 행.

    Returns:
        s.RequirementSummary: 목록 행.
    """
    # ponytail: 요구당 질의 1회(쪽 크기만큼). 목록이 느려지면 repository.rnd_check를 묶음 질의로 바꾼다.
    state, _ = await repository.rnd_check(conn, row["requirement_id"])
    value = " ".join(part for part in (row["value"], row["unit"]) if part) or None
    return s.RequirementSummary(
        requirement_id=row["requirement_id"],
        code=row["code"],
        equipment=row["equipment"],
        task=row["task"],
        attribute=row["attribute"],
        attribute_key=row["attribute_key"],
        value_text=value,
        necessity=row["necessity"],
        status=row["status"],
        path=row["path"],
        open_questions=row["open_questions"],
        needs_rereview=row["needs_rereview"],
        needs_review=row["needs_review"],
        version=row["version"],
        rnd_check=state,
        is_designated_item=row["designated_model"] is not None,
        provenance_kind=row["provenance_kind"],
    )


async def list_requirements(
    request: Request,
    principal: Principal,
    project_id: UUID,
    *,
    filters: Mapping[str, Any],
    limit: int,
    cursor: str | None,
) -> s.RequirementList:
    """12. 요구 목록·필터 7개.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        filters: repository.list_requirements 필터.
        limit: 쪽 크기.
        cursor: 쪽 커서.

    Returns:
        s.RequirementList: 상태·조항 순 요구와 다음 커서. 커서에 상태 순위를 함께 담아, 쪽을 넘기는
            사이 상태가 바뀌어도 위치가 밀리지 않는다.
    """
    after = requirement_cursor(decode_cursor(cursor, str))
    async with connection(request.app.state.settings) as conn:
        await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        rows = await repository.list_requirements(
            conn, project_id, filters=filters, after=after, limit=limit
        )
        page = rows[:limit]
        items = [await summary_of(conn, row) for row in page]
    more = len(rows) > limit
    return s.RequirementList(
        items=items,
        next_cursor=encode_cursor(f"{page[-1]['status_rank']}|{page[-1]['code']}")
        if more
        else None,
    )


def comparison_summary(results: Sequence[str]) -> str | None:
    """비교 결과 요약 문장.

    Args:
        results: 후보별 결과.

    Returns:
        str | None: '후보 2개: 충족 1, 차이 1'. 후보가 없으면 None.
    """
    if not results:
        return None
    counts = Counter(results)
    parts = ", ".join(f"{word} {counts[key]}" for key, word in RESULT_WORDS.items() if counts[key])
    return f"후보 {len(results)}개: {parts}"


def detail_of(
    requirement: Mapping[str, Any],
    parts: Mapping[str, Any],
    *,
    state: s.RndCheck,
    rnd_question: UUID | None,
    needs_rereview: bool,
) -> s.RequirementDetail:
    """요구 판본 → 상세 응답.

    Args:
        requirement: latest_requirement_version 행.
        parts: requirement_detail_parts 결과.
        state: 계산한 R&D 확인 상태.
        rnd_question: 기준 R&D 질문 ID.
        needs_rereview: 재검토 필요 여부.

    Returns:
        s.RequirementDetail: 상세.
    """
    model = requirement["designated_model"]
    designated = None
    if model is not None:
        procurement = requirement["designated_procurement"] or "SPECIFIED_PROCUREMENT"
        designated = s.DesignatedItem(
            manufacturer=requirement["designated_manufacturer"],
            model=model,
            clause_id=requirement["designated_clause_id"] or requirement["clause_id"],
            procurement=procurement,
        )
    return s.RequirementDetail(
        version=requirement["version"],
        data_origin=requirement["data_origin"],
        derivation_kind=requirement["derivation_kind"],
        known_at=requirement["known_at"],
        requirement_id=requirement["requirement_id"],
        code=requirement["code"],
        origin=s.RequirementOrigin(
            document_version_id=requirement["document_version_id"],
            clause_id=requirement["clause_id"],
            clause_no=requirement["clause_no"],
            page=requirement["page"],
            quote=requirement["quote"],
            deleted_values=parts["deleted_values"],
            edit_trace_support=parts["edit_trace_support"],
        ),
        spec=s.RequirementSpec(
            equipment=requirement["equipment"],
            task=requirement["task"],
            attribute=requirement["attribute"],
            attribute_key=requirement["attribute_key"],
            value=requirement["value"],
            unit=requirement["unit"],
            tolerance=requirement["tolerance"],
            operator=requirement["operator"],
            conditions=parts["conditions"],
            standard_system=requirement["standard_system"],
            necessity=requirement["necessity"],
            designated_model=model,
            undetermined_items=requirement["undetermined_items"],
            designated_item=designated,
        ),
        judgement=s.Judgement(
            status=requirement["status"],
            reason=requirement["reason"],
            unresolved=requirement["unresolved"],
            path=requirement["path"],
            next_owner_field=requirement["next_owner_field"],
            provenance_kind=requirement["provenance_kind"],
            decided_by=requirement["decided_by"],
            decided_at=requirement["decided_at"],
            rnd_check=state,
            rnd_question_id=rnd_question,
        ),
        links=s.RequirementLinks(
            comparison_summary=comparison_summary(parts["comparison_results"]),
            supplement_summary=parts["selected_path"],
            partner_candidate_ids=parts["partner_candidate_ids"],
            question_ids=parts["question_ids"],
            pending_proposal_ids=parts["pending_proposal_ids"],
            draft_sentence_ids=parts["draft_sentence_ids"],
        ),
        needs_rereview=needs_rereview,
        needs_review=requirement["needs_review"],
    )


async def latest_visible_requirement(
    conn: AsyncConnection[Row], principal: Principal, requirement_id: UUID
) -> Row:
    """볼 수 있는 요구의 최신 판본. 없거나 범위 밖이면 404 TFT_REQUIREMENT_NOT_FOUND다.

    Args:
        conn: dict_row 연결.
        principal: 요청 사용자.
        requirement_id: 요구 ID.

    Returns:
        Row: latest_requirement_version 행.
    """
    project = await requirement_project(conn, principal, requirement_id)
    requirement = await repository.latest_requirement_version(conn, project["id"], requirement_id)
    assert requirement is not None
    return requirement


async def requirement_out(
    conn: AsyncConnection[Row], requirement: Mapping[str, Any]
) -> s.RequirementDetail:
    """최신 판본 행 → 상세(경로 13과 승인 응답이 같이 쓴다).

    Args:
        conn: dict_row 연결.
        requirement: latest_requirement_version 행.

    Returns:
        s.RequirementDetail: 상세.
    """
    requirement_id = requirement["requirement_id"]
    parts = await repository.requirement_detail_parts(conn, requirement)
    state, question = await repository.rnd_check(conn, requirement_id)
    rereview = await repository.needs_rereview(conn, requirement_id)
    return detail_of(
        requirement, parts, state=state, rnd_question=question, needs_rereview=rereview
    )


async def get_requirement(
    request: Request, principal: Principal, requirement_id: UUID
) -> s.RequirementDetail:
    """13. 원문·요구·판단·연결.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        requirement_id: 요구 ID.

    Returns:
        s.RequirementDetail: 상세.
    """
    async with connection(request.app.state.settings) as conn:
        requirement = await latest_visible_requirement(conn, principal, requirement_id)
        return await requirement_out(conn, requirement)


def candidate_out(
    row: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]
) -> s.ComparisonCandidate:
    """비교 행 → 후보 응답.

    Args:
        row: product_comparisons 행.
        evidence: 그 비교의 근거 행.

    Returns:
        s.ComparisonCandidate: 후보.
    """
    return s.ComparisonCandidate(
        product_code=row["product_code"],
        variant=row["variant"],
        scope=row["scope"],
        lifecycle_status=row["lifecycle_status"],
        attribute_match=s.AttributeMatch(
            matched_attribute=row["matched_attribute"],
            our_value=row["our_value"],
            our_unit=row["our_unit"],
            our_conditions=[
                s.Condition(name=c["name"], value=c["value"], unit=c.get("unit"))
                for c in row["our_conditions"]
            ],
            comparable=row["comparable"],
            result=row["result"],
            evidence=[
                s.ComparisonEvidence(
                    document=e["document"],
                    clause=e["clause"],
                    version=e["document_version"],
                    applicability=e["applicability"],
                    limits=e["limits"],
                )
                for e in evidence
            ],
        ),
    )


async def get_comparison(
    request: Request, principal: Principal, requirement_id: UUID
) -> s.Comparison:
    """14. 자사 비교. 비교가 저장된 가장 최근 판본을 낸다.

    후보가 없으면 저장된 search_trace도 없으므로 저장된 요구 계열로 범위를 다시 만든다(후보를 찾은 계열 범위와
    같은 표기, comparison.compare_in_family).

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        requirement_id: 요구 ID.

    Returns:
        s.Comparison: 후보별 비교와 검색 범위.
    """
    async with connection(request.app.state.settings) as conn:
        requirement = await latest_visible_requirement(conn, principal, requirement_id)
        return await comparison_of(conn, requirement)


async def comparison_of(conn: AsyncConnection[Row], requirement: Mapping[str, Any]) -> s.Comparison:
    """같은 연결에서 읽은 최신 요구에 대응하는 비교 응답을 만든다."""
    requirement_id = requirement["requirement_id"]
    rows = await repository.latest_comparisons(conn, requirement_id)
    evidence = await repository.comparison_evidence(conn, [row["id"] for row in rows])
    if not rows:
        family = requirement["equipment_family"] or "NONE"
        return s.Comparison(
            version=requirement["version"],
            data_origin=requirement["data_origin"],
            derivation_kind="DERIVED",
            known_at=requirement["known_at"],
            requirement_id=requirement_id,
            requirement_version=requirement["version"],
            candidates=[],
            search_trace=s.SearchTrace(
                filters=[f"family={family}"], documents_searched=0, search_failed=False
            ),
            provenance_kind=requirement["provenance_kind"],
        )
    first = rows[0]
    return s.Comparison(
        version=first["version"],
        data_origin=first["data_origin"],
        derivation_kind=first["derivation_kind"],
        known_at=first["known_at"],
        requirement_id=requirement_id,
        requirement_version=first["requirement_version"],
        candidates=[
            candidate_out(row, [e for e in evidence if e["comparison_id"] == row["id"]])
            for row in rows
        ],
        search_trace=s.SearchTrace(**first["search_trace"]),
        provenance_kind=first["provenance_kind"],
    )


def partner_candidate(
    row: Mapping[str, Any],
    grades: Sequence[Mapping[str, Any]],
    shown: Sequence[Mapping[str, Any]],
    *,
    manufacturer_list: list[str] | None = None,
) -> s.PartnerCandidate:
    """프로필과 저장 등급 → 후보 응답. 후보 등급은 저장된 요구별 등급 전체로 계산한다.

    Args:
        row: partner_profiles 행.
        grades: 그 후보의 저장 등급 전체.
        shown: 응답에 낼 등급(요구 필터 적용).

    Returns:
        s.PartnerCandidate: 후보.
    """
    profile = profile_from_row(dict(row))
    unknown = [name for g in grades for name in g["mandatory_check"]["unknown"]]
    return s.PartnerCandidate(
        candidate_code=profile.candidate_code,
        display_name=profile.display_name,
        profile_source=profile.profile_source,
        profile_version=profile.profile_version,
        scope=sorted({scope for product in profile.products for scope in product.scope}),
        spec_facts_attributes=sorted({fact.attribute for fact in profile.spec_facts}),
        certifications=[c.standard for c in profile.certifications],
        integrator_capable=profile.integrator_capable.model_dump(),
        on_customer_manufacturer_list=grading._manufacturer(
            profile,
            grading.RfpContext(
                manufacturer_list=manufacturer_list,
                off_list_permission="UNSTATED",
                supply_region=None,
                requires_integrator=False,
            ),
        )[0],
        lead_time=profile.lead_time,
        warranty=profile.warranty,
        confirmation_status=profile.confirmation.status,
        overall_grade=grading.overall_grade(g["grade"] for g in grades),
        conditions_to_confirm=list(dict.fromkeys(unknown)),
        grades=[
            s.CandidateGrade(
                requirement_version=g.get("requirement_version"),
                profile_version=g.get("profile_version"),
                version=g.get("version"),
                known_at=g.get("known_at"),
                requirement_id=g["requirement_id"],
                grade=g["grade"],
                reasons=g["reasons"],
                exclusion_reasons=g["exclusion_reasons"],
                mandatory_check=g["mandatory_check"],
            )
            for g in shown
        ],
    )


async def list_partner_candidates(
    request: Request, principal: Principal, project_id: UUID, requirement_id: UUID | None
) -> s.PartnerCandidateList:
    """17. 가상 협력사 후보와 요구별 등급. 저장된 등급이 있는 후보만 낸다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        requirement_id: 이 요구의 등급만 낸다(후보 등급은 전체 기준이다).

    Returns:
        s.PartnerCandidateList: 후보 코드 순 후보.
    """
    async with connection(request.app.state.settings) as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        inputs = await repository.project_constraints(conn, project_id)
        cursor = await conn.execute("SELECT version FROM tft.projects WHERE id=%s", (project_id,))
        project_version = await cursor.fetchone()
        if project_version is None:
            raise ValueError("TFT_PROJECT_MISSING")
        rows = await repository.project_grades(conn, project_id)
        profiles = await repository.profiles_of(
            conn, [(r["candidate_code"], r["profile_version"]) for r in rows]
        )
    by_candidate: dict[str, list[Row]] = {}
    for row in rows:
        by_candidate.setdefault(row["candidate_code"], []).append(row)
    items = []
    for grades in by_candidate.values():
        shown = [g for g in grades if requirement_id in (None, g["requirement_id"])]
        if shown:
            latest = max(g["profile_version"] for g in grades)
            items.append(
                partner_candidate(
                    profiles[(grades[0]["candidate_code"], latest)],
                    grades,
                    shown,
                    manufacturer_list=inputs["customer_manufacturer_list"],
                )
            )
    return s.PartnerCandidateList(items=items, input_project_version=project_version["version"])
