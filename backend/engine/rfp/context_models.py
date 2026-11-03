"""Candidate-only RFP organization. Original requirements and decisions remain read-only."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "EQUIPMENT",
    "TASK",
    "DELIVERABLE",
    "INTERFACE",
    "QUALITY",
    "SCHEDULE",
    "QUALIFICATION",
    "COMMERCIAL",
    "UNCLASSIFIED",
]
AdditionalCategory = Literal[
    "EQUIPMENT",
    "TASK",
    "DELIVERABLE",
    "INTERFACE",
    "QUALITY",
    "SCHEDULE",
    "QUALIFICATION",
    "COMMERCIAL",
]
Relation = Literal["SAME_EQUIPMENT", "PART_OF", "APPLIES_TO", "RELATED"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ContextRequirement(Strict):
    requirement_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    document_version_id: str = Field(min_length=1, max_length=160)
    clause_id: str | None = Field(max_length=160)
    clause_no: str = Field(max_length=500)
    section_path: list[str] = Field(max_length=40)
    quote: str = Field(max_length=16000)
    equipment: str | None = None
    task: str | None = None
    attribute: str | None = None
    value: str | None = None
    unit: str | None = None
    conditions: list[str] = Field(default_factory=list, max_length=80)
    status: str
    necessity: str
    diagram_source_ids: list[str] = Field(default_factory=list, max_length=40)


class ContextGroup(Strict):
    entity_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=300)
    kind: Literal["SYSTEM", "EQUIPMENT", "TASK", "COMMON"]
    parent_id: str | None = None
    tag: str | None = None
    site: str | None = None
    evidence_requirement_ids: list[str] = Field(min_length=1, max_length=4000)
    diagram_source_ids: list[str] = Field(default_factory=list, max_length=40)
    needs_review: bool = True


class ContextRelation(Strict):
    group_id: str = Field(min_length=1, max_length=160)
    relation: Relation


class ContextAssignment(Strict):
    requirement_id: str = Field(min_length=1, max_length=160)
    primary_category: Category
    additional_categories: list[AdditionalCategory] = Field(default_factory=list, max_length=8)
    group_ids: list[str] = Field(default_factory=list, max_length=40)
    relations: list[ContextRelation] = Field(default_factory=list, max_length=40)
    reason: str = Field(max_length=4000)
    evidence_requirement_ids: list[str] = Field(default_factory=list, max_length=80)
    needs_review: bool = True


class ContextIssue(Strict):
    code: str
    requirement_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    message: str


class ContextResult(Strict):
    schema_version: Literal["rfp-context-v1"] = "rfp-context-v1"
    outcome: Literal["VALID", "PARTIAL"]
    groups: list[ContextGroup] = Field(max_length=4000)
    assignments: list[ContextAssignment] = Field(max_length=4000)
    issues: list[ContextIssue] = Field(default_factory=list)
    prompt_version: Literal["rfp-context-v1", "rfp-context-v2"] = "rfp-context-v2"


class ContextProposal(Strict):
    """Provider boundary. Runtime validation applies even with a lighter provider schema."""

    groups: list[ContextGroup] = Field(default_factory=list, max_length=4000)
    assignments: list[ContextAssignment] = Field(default_factory=list, max_length=4000)
