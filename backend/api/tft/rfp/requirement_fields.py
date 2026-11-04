"""KAN-227 요구 화면 서버 필드: 자사 보증값과 프로젝트 설비 목록(읽기 전용).

자사 보증값은 저장된 비교에서만 읽는다(새 계산 없음). 요구 상세의 비교(routes_read.comparison_of)와 같은 출처다:
지금 판본의 재비교 사본이 있으면 그것, 없으면 비교가 저장된 가장 최근 판본의 후보별 최신 비교.
설비 목록은 최신 맥락 결과(KAN-216)의 설비 묶음이다. 맥락이 없으면 요구 판본의 설비 칸을 모은다(태그 없음).
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from uuid import UUID

from fastapi import Request
from psycopg import AsyncConnection

from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.routes_questions import visible_project
from backend.shared.access import Principal
from backend.shared.database import connection

Row = dict[str, Any]
# 대표 후보: 충족 → 다름 → 비교 불가 → 자료 없음, 같으면 제품 코드 순. 자사 값이 없는 후보는 고르지 않는다.
RANK = {"MEETS": 0, "DIFFERS": 1, "NOT_COMPARABLE": 2, "NO_DATA": 3}


def pick(candidates: Iterable[Mapping[str, Any]]) -> s.OwnValue | None:
    """비교 후보(product_code·variant·result·our_value·our_unit·our_conditions) 중 대표 하나."""
    usable = [c for c in candidates if c.get("our_value")]
    if not usable:
        return None
    best = min(
        usable, key=lambda c: (RANK.get(c["result"], 9), c["product_code"], c["variant"] or "")
    )
    return s.OwnValue(
        product_code=best["product_code"],
        variant=best["variant"],
        value=best["our_value"],
        unit=best["our_unit"],
        conditions=[
            s.Condition(name=c["name"], value=c["value"], unit=c.get("unit"))
            for c in best["our_conditions"]
        ],
        result=best["result"],
    )


def flat(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """재비교 사본(Comparison JSON)의 후보 → 비교 행과 같은 칸."""
    match = candidate["attribute_match"]
    return {
        "product_code": candidate["product_code"],
        "variant": candidate.get("variant"),
        "result": match["result"],
        "our_value": match.get("our_value"),
        "our_unit": match.get("our_unit"),
        "our_conditions": match.get("our_conditions") or [],
    }


async def own_values(
    conn: AsyncConnection[Row], project_id: UUID, requirement_ids: Sequence[UUID]
) -> dict[UUID, s.OwnValue]:
    """요구별 자사 보증값(비교에 쓴 대표 후보). 비교가 없거나 자사 값이 없으면 빠진다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        requirement_ids: 이 요구만.

    Returns:
        dict: 요구 ID → 자사 보증값.
    """
    params = {"p": project_id, "ids": list(requirement_ids)}
    cursor = await conn.execute(
        "SELECT DISTINCT ON (rc.requirement_id) rc.requirement_id, rc.result "
        "FROM tft.requirement_recomparisons rc JOIN tft.requirements req ON req.id = rc.requirement_id "
        "AND rc.requirement_version = req.latest_version "
        "WHERE req.project_id = %(p)s AND req.id = ANY(%(ids)s) "
        "ORDER BY rc.requirement_id, rc.created_at DESC, rc.id DESC",
        params,
    )
    snapshots = {r["requirement_id"]: r["result"] for r in await cursor.fetchall()}
    cursor = await conn.execute(
        "SELECT DISTINCT ON (p.requirement_id, p.product_code, p.variant) p.requirement_id, "
        "p.product_code, p.variant, p.result, p.our_value, p.our_unit, p.our_conditions "
        "FROM tft.product_comparisons p JOIN tft.requirements req ON req.id = p.requirement_id "
        "WHERE req.project_id = %(p)s AND req.id = ANY(%(ids)s) AND p.requirement_version = "
        "(SELECT max(x.requirement_version) FROM tft.product_comparisons x "
        "WHERE x.requirement_id = p.requirement_id) "
        "ORDER BY p.requirement_id, p.product_code, p.variant, p.version DESC",
        params,
    )
    stored: dict[UUID, list[Row]] = {}
    for row in await cursor.fetchall():
        stored.setdefault(row["requirement_id"], []).append(row)
    out: dict[UUID, s.OwnValue] = {}
    for rid in requirement_ids:
        snapshot = snapshots.get(rid)
        candidates = (
            [flat(c) for c in snapshot.get("candidates", [])] if snapshot else stored.get(rid, [])
        )
        chosen = pick(candidates)
        if chosen is not None:
            out[rid] = chosen
    return out


async def list_equipment(
    request: Request, principal: Principal, project_id: UUID
) -> s.EquipmentList:
    """프로젝트 설비 목록(직접 추가 칸의 설비 선택용).

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.

    Returns:
        s.EquipmentList: 최신 맥락의 설비 묶음(source=context), 맥락이 없으면 요구 설비 칸(source=requirements).
    """
    async with connection(request.app.state.settings) as conn:
        await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        cursor = await conn.execute(
            "SELECT payload FROM tft.context_versions WHERE project_id = %s "
            "ORDER BY version DESC LIMIT 1",
            (project_id,),
        )
        context = await cursor.fetchone()
        if context is not None:
            seen: dict[tuple[str | None, str], s.Equipment] = {}
            for group in context["payload"].get("groups", []):
                if group.get("kind") != "EQUIPMENT":
                    continue
                item = s.Equipment(tag=group.get("tag"), name=group["name"])
                seen.setdefault((item.tag, item.name), item)
            return s.EquipmentList(source="context", items=sorted(seen.values(), key=order))
        cursor = await conn.execute(
            "SELECT DISTINCT v.equipment FROM tft.requirements req "
            "JOIN tft.requirement_versions v ON v.requirement_id = req.id AND v.version = req.latest_version "
            "WHERE req.project_id = %s AND v.equipment IS NOT NULL AND btrim(v.equipment) <> ''",
            (project_id,),
        )
        names = [r["equipment"] for r in await cursor.fetchall()]
        return s.EquipmentList(
            source="requirements",
            items=sorted((s.Equipment(tag=None, name=n) for n in names), key=order),
        )


def order(item: s.Equipment) -> tuple[bool, str, str]:
    return (item.tag is None, item.tag or "", item.name)
