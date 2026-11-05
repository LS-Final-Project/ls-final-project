"""Review-only single-line drawing observations, never an electrical design approval.

Coordinates refer to the complete supplied image/PDF page, normalized to 0..1000.
Source bytes, their digest, page and document identity are assigned by the caller,
not by model output. Geometry is approximate and every connection remains a candidate.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiagramValue(BaseModel):
    """Strict runtime input without additionalProperties in the provider JSON schema."""

    model_config = ConfigDict(strict=True)

    @model_validator(mode="before")
    @classmethod
    def known_fields(cls, value: Any) -> Any:
        if isinstance(value, dict) and set(value) - set(cls.model_fields):
            raise ValueError("DIAGRAM_UNKNOWN_FIELD")
        return value


class Point(DiagramValue):
    x: float = Field(ge=0, le=1000, allow_inf_nan=False)
    y: float = Field(ge=0, le=1000, allow_inf_nan=False)


class BBox(DiagramValue):
    x_min: float = Field(ge=0, le=1000, allow_inf_nan=False)
    y_min: float = Field(ge=0, le=1000, allow_inf_nan=False)
    x_max: float = Field(ge=0, le=1000, allow_inf_nan=False)
    y_max: float = Field(ge=0, le=1000, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("DIAGRAM_BBOX_ORDER")
        return self


NodeKind = Literal[
    "TRANSFORMER",
    "BREAKER",
    "BUS",
    "DISCONNECTOR",
    "FUSE",
    "LOAD",
    "GENERATOR",
    "GROUND",
    "METER",
    "TERMINAL",
    "OTHER",
    "UNKNOWN",
]
Connection = Literal["CONTINUOUS_LINE", "JUNCTION_DOT", "UNRESOLVED", "CROSS_PAGE"]


class DiagramNode(DiagramValue):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    tag: str | None = Field(default=None, max_length=160)
    label: str = Field(min_length=1, max_length=320)
    kind: NodeKind
    bbox: BBox | None = None
    ratings: list[str] = Field(default_factory=list, max_length=16)
    evidence: str = Field(min_length=1, max_length=1000)
    uncertainties: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def bounded_text(self) -> Self:
        if not self.label.strip() or not self.evidence.strip():
            raise ValueError("DIAGRAM_EMPTY_EVIDENCE")
        if any(not value.strip() or len(value) > 160 for value in self.ratings):
            raise ValueError("DIAGRAM_RATING_LIMIT")
        if any(not value.strip() or len(value) > 500 for value in self.uncertainties):
            raise ValueError("DIAGRAM_UNCERTAINTY_LIMIT")
        return self


class DiagramEdge(DiagramValue):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    source: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=64)
    connection: Connection
    path: list[Point] = Field(default_factory=list, max_length=32)
    evidence: str = Field(min_length=1, max_length=1000)
    off_page_reference: str | None = Field(default=None, max_length=320)
    uncertainties: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def visible_connection_only(self) -> Self:
        if not self.evidence.strip():
            raise ValueError("DIAGRAM_EMPTY_EVIDENCE")
        if len(self.path) == 1:
            raise ValueError("DIAGRAM_PATH_NEEDS_TWO_POINTS")
        if any(not value.strip() or len(value) > 500 for value in self.uncertainties):
            raise ValueError("DIAGRAM_UNCERTAINTY_LIMIT")
        if self.connection == "CROSS_PAGE":
            if self.target is not None or not (self.off_page_reference or "").strip():
                raise ValueError("DIAGRAM_CROSS_PAGE_UNRESOLVED")
        elif self.off_page_reference is not None:
            raise ValueError("DIAGRAM_CROSS_PAGE_KIND_REQUIRED")
        if self.connection in {"CONTINUOUS_LINE", "JUNCTION_DOT"} and self.target is None:
            raise ValueError("DIAGRAM_ENDPOINT_REQUIRED")
        if self.connection == "UNRESOLVED" and not self.uncertainties:
            raise ValueError("DIAGRAM_UNRESOLVED_REASON_REQUIRED")
        return self


class DiagramGraph(DiagramValue):
    diagram_type: Literal["SINGLE_LINE", "OTHER", "UNKNOWN"]
    title: str = Field(default="", max_length=320)
    nodes: list[DiagramNode] = Field(default_factory=list, max_length=80)
    edges: list[DiagramEdge] = Field(default_factory=list, max_length=160)
    uncertainties: list[str] = Field(default_factory=list, max_length=64)
    review_required: Literal[True] = True

    @model_validator(mode="before")
    @classmethod
    def never_confirmed(cls, value: Any) -> Any:
        if isinstance(value, dict) and "review_required" in value:
            if value["review_required"] is not True:
                raise ValueError("DIAGRAM_REVIEW_REQUIRED")
        return value

    @model_validator(mode="after")
    def checked_graph(self) -> Self:
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or len({edge.id for edge in self.edges}) != len(self.edges):
            raise ValueError("DIAGRAM_DUPLICATE_ID")
        if self.diagram_type != "SINGLE_LINE" and self.edges:
            raise ValueError("DIAGRAM_CONNECTIONS_REQUIRE_SINGLE_LINE")
        if any(not value.strip() or len(value) > 500 for value in self.uncertainties):
            raise ValueError("DIAGRAM_UNCERTAINTY_LIMIT")
        for edge in self.edges:
            if edge.source not in ids or (edge.target is not None and edge.target not in ids):
                raise ValueError("DIAGRAM_ENDPOINT_NOT_FOUND")
            if edge.source == edge.target:
                raise ValueError("DIAGRAM_SELF_CONNECTION")
        return self


def diagram_instructions() -> str:
    """Keep drawing interpretation separate from literal OCR and requirement extraction."""
    return (
        "Also return diagram=null when no drawing is present, or a diagram object. "
        "Only a clearly recognizable electrical SINGLE_LINE diagram may have edges. "
        "For other/unclear drawing types use OTHER/UNKNOWN, preserve observable nodes and "
        "uncertainties, but leave edges empty. Never infer circuit design, adequacy, ratings, "
        "voltage, flow direction, or unseen wiring from engineering conventions. "
        "All nodes and edges are unconfirmed review candidates; review_required must be true. "
        "Copy tags and ratings exactly as printed; no invented identifier in tag (local node id "
        "is allowed). Keep visible labels even if their symbol is UNKNOWN. Associate a rating "
        "with a node only when its printed placement clearly identifies that node; otherwise "
        "put the ambiguity in uncertainties. Preserve unreadable areas and references. "
        "Use full original image/page coordinates from 0 to 1000, origin top-left; bbox and "
        "edge path are approximate visual locations, not exact measured geometry. "
        "Use null bbox or empty path if location is unclear; do not fabricate coordinates. "
        "Trace only visible continuous lines or an explicit junction dot. Crossing lines "
        "without an explicit join are not proof of connection: omit the edge or use "
        "UNRESOLVED with the uncertainty. Broken/occluded traces also remain UNRESOLVED. "
        "For CROSS_PAGE, target is null and off_page_reference is the printed reference; "
        "do not join another page automatically. Keep bus sections distinct unless a visible "
        "line joins them. Edge evidence must explain the visible line/junction, not engineering "
        "assumptions. At most 80 nodes/160 edges/32 path points per edge; if the drawing exceeds "
        "these bounds, state omitted regions in uncertainties rather than implying completeness. "
    )
