"""제품 목록(KAN-246): 자사 제품·협력사 프로필 조회와 등록·수정.

자사 제품 기본 자료는 배포 파일(own_spec_facts.json)이고, 화면에서 고치거나 더한 제품은 tft.own_products에
판본으로 쌓는다. 비교는 파일 순서 그대로 각 제품의 최신 판본을 덮어 쓰고 새 제품을 뒤에 붙인 목록을 쓴다.
단종(DISCONTINUED) 판본은 비교 후보에서 뺀다. 파일 제품은 판본 0이다.

협력사는 tft.partner_profiles에 새 판본(profile_source=registered)을 더한다. 과거 등급은 그때 판본을 가리킨다.
고친 사람·시각은 tft.catalog_edits에 둔다. 어느 쪽도 행을 고치거나 지우지 않는다.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, get_args
from uuid import UUID, uuid4

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from backend.api.tft.partners import schemas as ps
from backend.api.tft.partners.loader import PROFILE_FAMILIES, profile_from_row, profile_row
from backend.api.tft.rfp import comparison, repository
from backend.engine.rfp.vocabulary import EquipmentFamily
from backend.shared.access import Principal
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
Lifecycle = Literal["ACTIVE", "MATURE", "DISCONTINUED"]
# 협력사 product_family는 표시 문구이고 채점 계열은 loader.PROFILE_FAMILIES가 정한다. 그 밖의 문구는 채점되지 않는다.


def _partner_family(value: str) -> str:
    if value not in PROFILE_FAMILIES:
        raise ValueError(f"제품 계열은 {', '.join(PROFILE_FAMILIES)} 중 하나입니다.")
    return value


PartnerFamily = Annotated[str, AfterValidator(_partner_family)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, Field(max_length=2000)]


# 경로에 들어가는 코드. platform 중계 허용표(relay.PRODUCT_PATH)와 같은 형식이다.
CODE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
# 협력사 코드. PartnerProfile.candidate_code와 같다.
PARTNER_CODE = r"^[A-Z]-[A-Z]$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactInput(Strict):
    """사양 한 줄. 제품 코드는 경로·상위 제품에서 채운다."""

    attribute: ps.Attribute
    value: Text
    unit: Annotated[str, Field(max_length=32)] | None = None
    conditions: list[ps.Condition] | None = None
    evidence_id: Text = "제품 목록 등록"
    section: Annotated[str, Field(max_length=64)] = ""


class OwnProductInput(Strict):
    # 낙관적 잠금: 지금 판본(파일 제품·새 제품은 0). 다르면 409.
    expected_version: int = Field(ge=0)
    family: EquipmentFamily
    lifecycle_status: Lifecycle
    standards: list[Text] = Field(default_factory=list, max_length=20)
    certifications: list[comparison.Certification] = Field(default_factory=list, max_length=50)
    spec_facts: list[FactInput] = Field(default_factory=list, max_length=100)


class OwnProduct(Strict):
    product_code: str
    version: int
    source: Literal["FILE", "REGISTERED"]
    family: str
    lifecycle_status: str | None
    standards: list[str]
    certifications: list[comparison.Certification]
    spec_facts: list[ps.SpecFact]
    edited_at: datetime | None
    edited_by: UUID | None


class PartnerInput(Strict):
    expected_version: int = Field(ge=0)
    display_name: Text
    manufacturer_name: Text
    product_family: PartnerFamily
    products: list[ps.Product] = Field(min_length=1, max_length=20)
    integrator_capable: ps.IntegratorCapable
    spec_facts: list[ps.SpecFact] = Field(default_factory=list, max_length=200)
    certifications: list[ps.Certification] = Field(default_factory=list, max_length=50)
    similar_tasks: list[Text] = Field(default_factory=list, max_length=20)
    supply_regions: list[Text] = Field(min_length=1, max_length=20)
    lead_time: ps.LeadTime | None = None
    warranty: Annotated[str, Field(max_length=200)] | None = None
    notes: LongText | None = None
    confirmation_status: Literal["UNCONFIRMED", "CONTACT_PLANNED", "CONFIRMED"] = "UNCONFIRMED"
    follow_up_questions: list[Text] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _references(self) -> "PartnerInput":
        codes = [p.product_code for p in self.products]
        if len(codes) != len(set(codes)):
            raise ValueError("제품 코드가 겹칩니다.")
        models = {p.model for p in self.products}
        if any(f.product_code not in codes for f in self.spec_facts):
            raise ValueError("사양이 이 협력사 제품에 없는 제품 코드를 가리킵니다.")
        if any(not set(c.applicable_models) <= models for c in self.certifications):
            raise ValueError("인증 적용 모델이 이 협력사 제품에 없습니다.")
        return self


class Partner(Strict):
    profile: ps.PartnerProfile
    edited_at: datetime | None
    edited_by: UUID | None


class Options(Strict):
    """등록 화면의 선택지. 어휘 원본(vocabulary·partners.schemas)에서 만든다."""

    attributes: list[str]
    own_families: list[str]
    partner_families: list[str]
    lifecycle_statuses: list[str]
    scopes: list[str]
    own_certification_kinds: list[str]
    partner_certification_kinds: list[str]
    condition_names: list[str]


def options() -> Options:
    from backend.engine.rfp.vocabulary import ConditionName

    return Options(
        attributes=list(get_args(ps.Attribute)),
        own_families=list(get_args(EquipmentFamily)),
        partner_families=list(PROFILE_FAMILIES),
        lifecycle_statuses=list(get_args(Lifecycle)),
        scopes=list(get_args(ps.Scope)),
        own_certification_kinds=list(
            get_args(comparison.Certification.model_fields["kind"].annotation)
        ),
        partner_certification_kinds=list(
            get_args(ps.Certification.model_fields["kind"].annotation)
        ),
        condition_names=list(get_args(ConditionName)),
    )


def _product(row: Mapping[str, Any]) -> comparison.Product:
    return comparison.Product(
        product_code=row["product_code"],
        family=row["family"],
        lifecycle_status=row["lifecycle_status"],
        standards=list(row["standards"]),
        certifications=[comparison.Certification(**c) for c in row["certifications"]],
        spec_facts=[ps.SpecFact(**f) for f in row["spec_facts"]],
    )


async def _latest_own_rows(conn: AsyncConnection[Row]) -> list[Row]:
    cursor = await conn.execute(
        "SELECT DISTINCT ON (product_code) * FROM tft.own_products "
        "ORDER BY product_code, version DESC"
    )
    return list(await cursor.fetchall())


def effective(
    base: Sequence[comparison.Product], rows: Sequence[Mapping[str, Any]]
) -> list[comparison.Product]:
    """파일 제품 위에 최신 판본을 덮어 쓴 비교 후보. 파일 순서를 지키고(같은 상태면 앞 후보가 대표) 새 제품은 코드 순으로 뒤에 붙인다."""
    by = {p.product_code: p for p in base}
    for row in sorted(rows, key=lambda r: r["product_code"]):
        by[row["product_code"]] = _product(row)
    return [p for p in by.values() if p.lifecycle_status != "DISCONTINUED"]


async def own_products(conn: AsyncConnection[Row]) -> list[comparison.Product]:
    """비교에 쓰는 자사 제품(파일 + 화면 판본)."""
    return effective(comparison.load_own_products(), await _latest_own_rows(conn))


async def _edits(conn: AsyncConnection[Row], kind: str) -> dict[tuple[str, int], Row]:
    cursor = await conn.execute(
        "SELECT code, version, actor_user_id, at FROM tft.catalog_edits WHERE kind = %s", (kind,)
    )
    return {(r["code"], r["version"]): r for r in await cursor.fetchall()}


async def list_own(conn: AsyncConnection[Row]) -> list[OwnProduct]:
    """목록 화면용 자사 제품(단종 포함). 파일 순서, 새 제품은 뒤에."""
    rows = {r["product_code"]: r for r in await _latest_own_rows(conn)}
    edits = await _edits(conn, "own_product")
    codes = [p.product_code for p in comparison.load_own_products()]
    codes += sorted(set(rows) - set(codes))
    base = {p.product_code: p for p in comparison.load_own_products()}
    items = []
    for code in codes:
        row = rows.get(code)
        product = _product(row) if row else base[code]
        edit = edits.get((code, row["version"])) if row else None
        items.append(
            OwnProduct(
                **product.model_dump(),
                version=row["version"] if row else 0,
                source="REGISTERED" if row else "FILE",
                edited_at=edit["at"] if edit else None,
                edited_by=edit["actor_user_id"] if edit else None,
            )
        )
    return items


async def _current_version(conn: AsyncConnection[Row], sql: str, code: str) -> int:
    cursor = await conn.execute(sql, (code,))
    row = await cursor.fetchone()
    return row["v"] if row and row["v"] is not None else 0


def _conflict(current: int) -> ApplicationError:
    return ApplicationError(
        409,
        "TFT_VERSION_CONFLICT",
        f"다른 사용자가 먼저 고쳤습니다(현재 판본 {current}). 목록을 새로 읽은 뒤 다시 저장하세요.",
    )


async def _record(
    conn: AsyncConnection[Row], principal: Principal, kind: str, code: str, version: int
) -> None:
    await conn.execute(
        "INSERT INTO tft.catalog_edits (id, kind, code, version, actor_user_id) VALUES (%s, %s, %s, %s, %s)",
        (uuid4(), kind, code, version, principal.user_id),
    )


def require_writer(principal: Principal) -> None:
    """제품 목록 등록·수정은 manager만 한다(KAN-247). 조회는 제안 지원 역할 모두다."""
    if not principal.has("manager"):
        raise ApplicationError(
            403, "TFT_CATALOG_WRITE_FORBIDDEN", "제품 목록은 관리자만 등록하거나 고칠 수 있습니다."
        )


async def save_own(
    conn: AsyncConnection[Row], principal: Principal, code: str, payload: OwnProductInput
) -> OwnProduct:
    """자사 제품 새 판본. 파일 제품을 고치면 판본 1부터 쌓는다."""
    require_writer(principal)
    async with conn.transaction():
        current = await _current_version(
            conn, "SELECT max(version) AS v FROM tft.own_products WHERE product_code = %s", code
        )
        if current != payload.expected_version:
            raise _conflict(current)
        version = current + 1
        facts = [
            ps.SpecFact(product_code=code, **f.model_dump()).model_dump(mode="json")
            for f in payload.spec_facts
        ]
        try:
            await conn.execute(
                "INSERT INTO tft.own_products (product_code, version, family, lifecycle_status, standards, "
                "certifications, spec_facts) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    code,
                    version,
                    payload.family,
                    payload.lifecycle_status,
                    list(payload.standards),
                    Jsonb([c.model_dump(mode="json") for c in payload.certifications]),
                    Jsonb(facts),
                ),
            )
        except psycopg.errors.UniqueViolation:
            raise _conflict(version) from None
        await _record(conn, principal, "own_product", code, version)
    return next(p for p in await list_own(conn) if p.product_code == code)


async def list_partners(conn: AsyncConnection[Row]) -> list[Partner]:
    """협력사마다 최신 판본."""
    edits = await _edits(conn, "partner")
    items = []
    for row in await repository.partner_profiles(conn):
        edit = edits.get((row["candidate_code"], row["profile_version"]))
        items.append(
            Partner(
                profile=profile_from_row(dict(row)),
                edited_at=edit["at"] if edit else None,
                edited_by=edit["actor_user_id"] if edit else None,
            )
        )
    return items


async def save_partner(
    conn: AsyncConnection[Row], principal: Principal, code: str, payload: PartnerInput
) -> Partner:
    """협력사 새 판본(등록·수정 모두). 수정해도 이전 판본과 그 판본으로 매긴 등급은 남는다."""
    require_writer(principal)
    async with conn.transaction():
        # 협력사 저장을 한 줄로 세운다. 제품 코드 중복 검사(아래 taken)가 동시 저장 둘을 함께 통과시키지 않게 한다.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('tft.partner_profiles'))")
        current = await _current_version(
            conn,
            "SELECT max(profile_version) AS v FROM tft.partner_profiles WHERE candidate_code = %s",
            code,
        )
        if current != payload.expected_version:
            raise _conflict(current)
        taken = {
            p.product_code
            for row in await repository.partner_profiles(conn)
            if row["candidate_code"] != code
            for p in profile_from_row(dict(row)).products
        }
        if clash := sorted(taken & {p.product_code for p in payload.products}):
            raise ApplicationError(
                409,
                "TFT_PRODUCT_CODE_TAKEN",
                f"다른 협력사가 쓰는 제품 코드입니다: {', '.join(clash)}",
            )
        now = datetime.now(UTC)
        profile = ps.PartnerProfile(
            candidate_code=code,
            profile_source="registered",
            profile_version=current + 1,
            data_origin="real",
            derivation_kind="PROVIDED",
            known_at=now.date(),
            **payload.model_dump(
                exclude={"expected_version", "confirmation_status", "follow_up_questions"}
            ),
            confirmation=ps.Confirmation(
                status=payload.confirmation_status,
                as_of=now.date(),
                reviewer=str(principal.user_id),
                follow_up_questions=list(payload.follow_up_questions),
            ),
        )
        values = {**profile_row(profile), "known_at": now}
        columns = list(values)
        try:
            await conn.execute(
                f"INSERT INTO tft.partner_profiles ({', '.join(columns)}) "  # noqa: S608 - 열 이름은 모델 필드
                f"VALUES ({', '.join(['%s'] * len(columns))})",
                [values[c] for c in columns],
            )
        except psycopg.errors.UniqueViolation:
            raise _conflict(current + 1) from None
        await _record(conn, principal, "partner", code, profile.profile_version)
    return next(p for p in await list_partners(conn) if p.profile.candidate_code == code)
