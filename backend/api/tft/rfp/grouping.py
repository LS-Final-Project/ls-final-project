"""KAN-231 요구 화면의 묶음·중복 표시(읽기 전용, 모델 호출 없음).

최신 맥락 결과(KAN-216 consolidate)에서 요구가 속한 설비 묶음과 KAN-223 중복 묶음을 읽는다. 맥락 이후 요구의
비교 칸(문서 판본·설비·속성·값·단위)이 바뀐 요구는 `context_stale`이고 중복에서 뺀다. 결정은 요구 판단만 바꾸므로
판본이 올라가도 이 칸이 같으면 맥락은 그대로 유효하다. 설비 계열 묶음(bucket)은 맥락이 없어도 낸다.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from backend.api.tft.rfp import schemas as s
from backend.engine.rfp.context_models import ContextGroup, ContextResult

Row = dict[str, Any]
# 중복 판단에 쓰는 칸(context_graph._duplicates와 같은 입력). 판단(status·reason 등)은 보지 않는다.
CONTENT = ("document_version_id", "equipment", "attribute", "value", "unit")
# 설비 계열(vocabulary.EquipmentFamily) → 화면 묶음. 나머지 계열은 OTHER, 계열 없음·과업·제출물은 COMMON.
BUCKETS = {"MV_EQUIP": "MV", "TRANSFORMER": "TR", "LV_BREAKER": "LV", "LV_SWITCHGEAR": "LV"}


@dataclass(frozen=True)
class Marks:
    analyzed: bool = False
    groups: dict[UUID, s.RequirementGroup] = field(default_factory=dict)
    duplicates: dict[UUID, s.DuplicateInfo] = field(default_factory=dict)
    stale: frozenset[UUID] = frozenset()
    buckets: dict[UUID, s.Bucket] = field(default_factory=dict)

    def apply(self, item: s.RequirementSummary | s.RequirementDetail) -> None:
        """목록·상세 응답에 칸을 채운다."""
        rid = item.requirement_id
        item.bucket = self.buckets.get(rid, "COMMON")
        if not self.analyzed:
            return
        item.context_stale = rid in self.stale
        item.group = self.groups.get(rid)
        item.duplicate = self.duplicates.get(rid)

    def representative_of(self, requirement_id: UUID) -> UUID | None:
        """중복 묶음의 구성원(대표 아님)이면 대표 ID."""
        found = self.duplicates.get(requirement_id)
        return None if found is None or found.is_representative else found.representative_id


def equipment_groups(result: ContextResult) -> dict[str, ContextGroup]:
    """요구 → 설비 묶음. 배정의 첫 설비 묶음, 배정에 없으면 근거로 든 첫 설비 묶음(출력 KAN-221과 공유)."""
    groups = {g.entity_id: g for g in result.groups if g.kind == "EQUIPMENT"}
    out: dict[str, ContextGroup] = {}
    for assignment in result.assignments:
        found = next((groups[i] for i in assignment.group_ids if i in groups), None)
        if found:
            out[assignment.requirement_id] = found
    for group in groups.values():
        for requirement_id in group.evidence_requirement_ids:
            out.setdefault(requirement_id, group)
    return out


def bucket(row: Mapping[str, Any]) -> s.Bucket:
    if row["kind"] in ("TASK", "DOCUMENT") or row["task"]:
        return "COMMON"
    family = row["equipment_family"]
    if family in (None, "OTHER"):
        return "COMMON"
    return BUCKETS.get(family, "OTHER")  # type: ignore[return-value]


def _same(snapshot: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    return all(str(snapshot.get(k) or "") == str(current[k] or "") for k in CONTENT)


async def marks(conn: AsyncConnection[Row], project_id: UUID) -> Marks:
    """프로젝트 요구 전체의 묶음·중복·계열 표시.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        Marks: 맥락이 없으면 analyzed=False이고 계열 묶음만 있다.
    """
    cursor = await conn.execute(
        "SELECT req.id, v.document_version_id, v.equipment, v.attribute, v.value, v.unit, "
        "v.equipment_family, v.kind, v.task FROM tft.requirements req "
        "JOIN tft.requirement_versions v ON v.requirement_id = req.id AND v.version = req.latest_version "
        "WHERE req.project_id = %s",
        (project_id,),
    )
    current = {r["id"]: r for r in await cursor.fetchall()}
    buckets = {rid: bucket(r) for rid, r in current.items()}
    cursor = await conn.execute(
        "SELECT payload, source_snapshot FROM tft.context_versions WHERE project_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (project_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return Marks(buckets=buckets)
    result = ContextResult.model_validate(row["payload"])
    snapshot = {r["requirement_id"]: r for r in row["source_snapshot"].get("requirements", [])}
    stale = frozenset(
        rid
        for rid, r in current.items()
        if str(rid) not in snapshot or not _same(snapshot[str(rid)], r)
    )
    groups = {
        UUID(rid): s.RequirementGroup(group_id=g.entity_id, tag=g.tag, name=g.name)
        for rid, g in equipment_groups(result).items()
        if UUID(rid) in current and UUID(rid) not in stale
    }
    duplicates: dict[UUID, s.DuplicateInfo] = {}
    for dup in result.duplicates:
        representative = UUID(dup.canonical_requirement_id)
        members = [
            UUID(m) for m in dup.requirement_ids if UUID(m) in current and UUID(m) not in stale
        ]
        # 대표가 바뀌었거나 남은 구성원이 하나면 중복으로 보지 않는다(다시 맥락을 돌리면 새로 묶인다).
        if representative not in members or len(members) < 2:
            continue
        for member in members:
            duplicates[member] = s.DuplicateInfo(
                representative_id=representative,
                member_count=len(members),
                is_representative=member == representative,
            )
    return Marks(True, groups, duplicates, stale, buckets)
