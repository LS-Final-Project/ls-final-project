"""가상 협력사 프로필 형식(KAN-184·KAN-187 공유 형식 §1, §5 속성 어휘).

프로필에는 사실과 근거만 둔다. 등급·점수는 KAN-187이 후보 × 요구마다 계산하며 여기에 두지 않는다.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.rfp.vocabulary import AttributeKey

Scope = Literal["SUPPLY", "ASSEMBLY", "INSTALL", "TEST", "CALC", "TRAINING"]
# 속성 어휘는 엔진 쪽 단일 원본(KAN-185)이다. 추출 요구의 attribute_key와 같은 목록이다.
Attribute = AttributeKey


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Product(Strict):
    product_code: str
    model: str
    scope: list[Scope] = Field(min_length=1)


class IntegratorCapable(Strict):
    """조립품 공급 요구에서 필수로 보는 통합 책임 항목. null은 미확인이다."""

    assembly: bool | None
    test: bool | None
    integration: bool | None
    warranty: bool | None


class Condition(Strict):
    name: str
    value: str
    unit: str | None = None


class SpecFact(Strict):
    """product_spec_facts와 같은 열. 값은 문자열이고 단위는 unit에 따로 둔다."""

    product_code: str
    attribute: Attribute
    value: str
    unit: str | None
    conditions: list[Condition] | None
    evidence_id: str
    section: str


class Certification(Strict):
    """인증은 spec_facts가 아니라 이 배열로 판정한다. as_of는 정보용이다."""

    standard: str
    applicable_models: list[str] = Field(min_length=1)
    kind: Literal["LISTED", "TEST_REPORT"]
    as_of: date
    valid_until: date | None
    evidence_id: str


class LeadTime(Strict):
    value: int = Field(gt=0)
    unit: Literal["weeks"]
    ex_works: bool


class Confirmation(Strict):
    """업체 확인 상태. 기술 근거와 별개이며 시연에서는 UNCONFIRMED로 둔다."""

    status: Literal["UNCONFIRMED", "CONTACT_PLANNED", "CONFIRMED"]
    as_of: date
    reviewer: str
    follow_up_questions: list[str]


class PartnerProfile(Strict):
    candidate_code: str = Field(pattern=r"^[A-Z]-[A-Z]$")
    display_name: str
    manufacturer_name: str
    profile_source: Literal["synthetic"]
    profile_version: int = Field(ge=1)
    data_origin: Literal["synthetic"]
    derivation_kind: Literal["PROVIDED"]
    known_at: date
    product_family: str
    products: list[Product] = Field(min_length=1)
    integrator_capable: IntegratorCapable
    spec_facts: list[SpecFact]
    certifications: list[Certification]
    similar_tasks: list[str]
    supply_regions: list[str] = Field(min_length=1)
    lead_time: LeadTime | None
    warranty: str | None
    confirmation: Confirmation
    notes: str | None
