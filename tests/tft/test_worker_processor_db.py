"""RFP 처리 함수(KAN-199·KAN-188)를 가짜 추출기·합성 Markdown 원문·전용 PostgreSQL(tft_worker 권한)로 확인한다.

행과 원문은 모두 합성이다. tft 마이그레이션 적용은 tests/tft/test_rfp_http_base_db.py의 고정물을 쓴다.
"""

# ruff: noqa: F811

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from backend.agent.processor_registry import TftJob
from backend.api.tft.partners.loader import load_profiles
from backend.api.tft.projects.repository import integration_state
from backend.api.tft.rfp.integration import StoredRequirement, project_integration
from backend.api.tft.rfp.jobs import input_version
from backend.api.tft.rfp.storage import LocalDocumentStore
from backend.api.tft.worker_processor import draft_requirement, make_processor, sections_of
from backend.engine.documents.router import extract
from backend.engine.rfp.clauses import build_clause_tree
from backend.engine.rfp.questions import QuestionDraft, QuestionSource
from backend.engine.rfp.requirements import ExtractedRequirement
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings

# 고정물을 가져와 쓴다(pytest가 이름으로 찾는다).
from tests.tft.test_rfp_http_base_db import SALES, admin  # noqa: F401
from tests.tft.test_rfp_schema_db import apply

SPEC = b"""# SECTION 26 12 19

PART 2 - PRODUCTS

2.1 TRANSFORMERS

A. The transformer rating shall be 1500 kVA minimum.

B. The impedance shall be 5.75 percent as Eaton Resibloc.

2.2 CERTIFICATION

A. The transformer shall be UL 1561 listed.
"""
REQUIREMENTS = [
    {
        "clause_no": "2.1.A",
        # 자사 변압기(630·1000 kVA)가 같은 계열 안에서 미달인 요구(SPEC_GAP → 보완 선택지·협력사 등급).
        "quote": "The transformer rating shall be 1500 kVA minimum.",
        "kind": "SPEC",
        "equipment": "dry-type transformer",
        "task": None,
        "attribute": "rating",
        "attribute_key": "rated_power",
        "equipment_family": "TRANSFORMER",
        "value": "1500",
        "unit": "kVA",
        "operator": "GTE",
        "conditions": [],
        "standard_system": "IEEE",
        "necessity": "MANDATORY",
        "designated_model": None,
    },
    {
        "clause_no": "2.1.B",
        "quote": "The impedance shall be 5.75 percent as Eaton Resibloc.",
        "kind": "SPEC",
        "equipment": "dry-type transformer",
        "task": None,
        "attribute": "impedance",
        "attribute_key": "impedance",
        "equipment_family": "TRANSFORMER",
        "value": "5.75",
        "unit": "%",
        "operator": "EQ",
        "conditions": [],
        "standard_system": "IEEE",
        "necessity": "MANDATORY",
        "designated_model": "Resibloc",
        "designated_manufacturer": "Eaton",
    },
    {
        "clause_no": "2.2.A",
        "quote": "The transformer shall be UL 1561 listed.",
        "kind": "COMPLIANCE",
        "equipment": "dry-type transformer",
        "task": None,
        "attribute": "UL 1561",
        "equipment_family": "TRANSFORMER",
        "value": None,
        "unit": None,
        "operator": "TEXT",
        "conditions": [],
        "standard_system": "UL",
        "necessity": "MANDATORY",
        "designated_model": None,
    },
]
STEPS = ["parse", "extract", "search", "compare", "supplement", "candidates", "draft"]


class FakeExtractor:
    """모든 절에 같은 요구 목록을 돌려준다(절 밖 조항은 엔진이 UNKNOWN_CLAUSE로 버린다)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, _prompt):
        self.calls += 1
        return Generation(
            text=json.dumps({"requirements": REQUIREMENTS}),
            usage=Usage(),
            model_version="fake",
            finish_reasons=[],
        )


@pytest.fixture
def worker_dsn(admin):
    """tft_worker 권한만 상속하는 로그인(이 PR의 마이그레이션 적용)."""
    with psycopg.connect(admin, autocommit=True) as conn:
        apply(conn, Path("infra/migrations/tft/20261104_KAN-199_tft_worker.sql"))
        apply(conn, Path("infra/migrations/tft/20261109_KAN-188_draft_execution.sql"))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='test_tft_proc'").fetchone():
            conn.execute("CREATE ROLE test_tft_proc LOGIN PASSWORD 'test-only'")
            conn.execute("GRANT tft_worker TO test_tft_proc")
    return make_conninfo(admin, user="test_tft_proc", password="test-only")


@pytest.fixture
def project(admin, tmp_path):
    """요구가 없는 프로젝트와 원문 폴더에 둔 Markdown 규격서 판본 하나."""
    return make_project(admin, tmp_path, SPEC)


def make_project(admin, tmp_path, spec):
    """원문 spec(Markdown 바이트)으로 프로젝트·문서·판본을 만든다.

    Args:
        admin: 관리자 DSN.
        tmp_path: 원문 폴더.
        spec: 규격서 원문.

    Returns:
        dict: project·document·docver ID와 root.
    """
    ids = {k: uuid4() for k in ("project", "document", "docver")}
    key = f"rfp/{ids['docver']}.md"
    (tmp_path / "rfp").mkdir()
    (tmp_path / key).write_bytes(spec)
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.projects (id, source, source_instance, data_origin, derivation_kind, "
            "known_at, company_code, name, sales_user_id, responsible_user_id, review_departments) "
            "VALUES (%s,'test','kan199','synthetic','PROVIDED',now(),'HOST','합성',%s,%s,%s)",
            (ids["project"], SALES, uuid4(), ["RND_FW"]),
        )
        conn.execute(
            "INSERT INTO tft.documents (id, project_id, kind, title, data_origin, derivation_kind, "
            "known_at, created_by) VALUES (%s,%s,'RFP_SPEC','합성 규격서','synthetic','PROVIDED',now(),%s)",
            (ids["document"], ids["project"], uuid4()),
        )
        conn.execute(
            "INSERT INTO tft.document_versions (id, document_id, version, mime, storage_key, sha256, "
            "size, edit_trace_support, data_origin, derivation_kind, known_at, uploaded_by) "
            "VALUES (%s,%s,1,'text/markdown',%s,%s,%s,false,'synthetic','PROVIDED',now(),%s)",
            (
                ids["docver"],
                ids["document"],
                key,
                hashlib.sha256(spec).hexdigest(),
                len(spec),
                uuid4(),
            ),
        )
    return {**ids, "root": tmp_path}


@pytest.fixture
def profile(admin):
    """합성 협력사 프로필 한 판본을 DB에 넣고 검사 뒤 지운다."""
    source = load_profiles()[0]
    code = "Q-" + chr(ord("A") + uuid4().int % 26)
    # 합성 문서의 요구가 변압기라 같은 계열 후보로 넣는다(후보는 자기 계열 요구만 채점한다).
    row = source.model_dump(mode="json") | {
        "candidate_code": code,
        "profile_version": 1,
        "product_family": "몰드 변압기",
    }
    columns = [k for k in row if k != "known_at"]
    with psycopg.connect(admin) as conn:
        conn.execute(
            f"INSERT INTO tft.partner_profiles ({', '.join(columns)}, known_at) "
            f"VALUES ({', '.join(['%s'] * len(columns))}, now())",
            [
                json.dumps(row[k])
                if isinstance(row[k], dict | list)
                and k != "similar_tasks"
                and k != "supply_regions"
                else row[k]
                for k in columns
            ],
        )
    yield code
    with psycopg.connect(admin) as conn:
        conn.execute("DELETE FROM tft.partner_grades WHERE candidate_code=%s", (code,))
        conn.execute("DELETE FROM tft.partner_profiles WHERE candidate_code=%s", (code,))


def run(worker_dsn, project, extractor, kind="rfp.process", version=None):
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(worker_dsn))
    processor = make_processor(
        settings,
        provider=extractor,
        store=LocalDocumentStore(project["root"]),
        today=lambda: date(2026, 10, 30),
    )
    job = TftJob(
        job_id=uuid4(),
        kind=kind,
        project_id=project["project"],
        input_version=version or input_version([project["docver"]]),
        actor_id=SALES,
    )
    events = []
    summary = processor(job, events.append)
    return summary, events


def stored(admin, project):
    with psycopg.connect(admin) as conn:
        one = lambda q: conn.execute(q, (project["project"],)).fetchone()[0]  # noqa: E731
        return {
            "clauses": one(
                "SELECT count(*) FROM tft.clauses c JOIN tft.document_versions v ON v.id = "
                "c.document_version_id JOIN tft.documents d ON d.id = v.document_id "
                "WHERE d.project_id=%s"
            ),
            "requirements": one("SELECT count(*) FROM tft.requirements WHERE project_id=%s"),
            "ai_drafts": one(
                "SELECT count(*) FROM tft.requirement_versions v JOIN tft.requirements r "
                "ON r.id = v.requirement_id WHERE r.project_id=%s AND v.provenance_kind='ai_draft' "
                "AND v.clause_id IS NOT NULL"
            ),
            "comparisons": one(
                "SELECT count(*) FROM tft.product_comparisons p JOIN tft.requirements r "
                "ON r.id = p.requirement_id WHERE r.project_id=%s"
            ),
            "options": one(
                "SELECT count(*) FROM tft.supplement_options o JOIN tft.requirements r "
                "ON r.id = o.requirement_id WHERE r.project_id=%s"
            ),
            "gaps": one(
                "SELECT count(*) FROM tft.requirement_versions v JOIN tft.requirements r "
                "ON r.id = v.requirement_id WHERE r.project_id=%s AND v.status='SPEC_GAP'"
            ),
            "grades": one("SELECT count(*) FROM tft.partner_grades WHERE project_id=%s"),
        }


def test_sections_are_the_top_numbered_groups():
    """PART 2 아래의 2.1·2.2처럼 번호로 하위를 묶는 가장 위 조항이 절이다(2.1.A처럼 그 안의 묶음은 아니다)."""
    nested = b"PART 2 - PRODUCTS\n\n2.1 TRANSFORMERS\n\nA. Windings:\n\n1. The winding shall be copper.\n"
    for text in (SPEC, nested):
        tree = build_clause_tree(extract(text, mime="text/markdown"))
        assert sections_of(tree)[0] == "2.1" and "2.1.A" not in sections_of(tree)


def test_process_stores_every_step_once_and_a_rerun_reuses_it(worker_dsn, project, admin, profile):
    """7단계를 순서대로 보고하고 조항·요구·비교·선택지·등급을 저장한다. 다시 돌리면 모델도 행도 그대로다."""
    extractor = FakeExtractor()
    summary, events = run(worker_dsn, project, extractor)
    # extract는 모델 호출마다(counts 없음)와 절(2.1·2.2)마다(누적 counts) 남는다(lease 연장).
    assert [e["step"] for e in events] == ["parse", *["extract"] * 4, *STEPS[2:]]
    assert events[0] == {"step": "parse", "document_version_id": str(project["docver"])}
    assert [e.get("counts", {}).get("requirements") for e in events[1:5]] == [None, 2, None, 3]
    assert all(e["counts"] == {"requirements": 3, "proposals": 0} for e in events[5:])
    assert summary == {"counts": {"requirements": 3, "proposals": 0}, "needs_review": False}
    assert extractor.calls == 2  # 절 두 개, 빠진 요구 문장 없음.
    first = stored(admin, project)
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.draft_versions WHERE project_id=%s AND source_job_id IS NOT NULL",
                (project["project"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.draft_sentences WHERE project_id=%s",
                (project["project"],),
            ).fetchone()[0]
            > 0
        )
    with psycopg.connect(admin) as conn:
        # 인증 요구(2.2.A) 비교는 속성 칸을 비운다.
        compliance = conn.execute(
            "SELECT count(*), count(p.matched_attribute) FROM tft.product_comparisons p "
            "JOIN tft.requirement_versions v ON v.requirement_id = p.requirement_id "
            "JOIN tft.requirements r ON r.id = v.requirement_id "
            "WHERE r.project_id=%s AND v.quote LIKE %s",
            (project["project"], "%UL 1561%"),
        ).fetchone()
    assert compliance[0] > 0 and compliance[1] == 0
    with psycopg.connect(admin) as conn:
        # 추출 v2(#309)의 지정품 제조사를 판본에 남긴다.
        assert conn.execute(
            "SELECT v.designated_model, v.designated_manufacturer, v.designated_procurement, "
            "v.designated_clause_id = v.clause_id FROM tft.requirement_versions v "
            "JOIN tft.requirements r ON r.id = v.requirement_id "
            "WHERE r.project_id=%s AND v.designated_model IS NOT NULL",
            (project["project"],),
        ).fetchall() == [("Resibloc", "Eaton", "SPECIFIED_PROCUREMENT", True)]
        # 지정 모델이 없는 요구에는 지정품 상세를 두지 않는다.
        assert conn.execute(
            "SELECT count(*) FROM tft.requirement_versions v "
            "JOIN tft.requirements r ON r.id = v.requirement_id WHERE r.project_id=%s "
            "AND v.designated_model IS NULL "
            "AND (v.designated_clause_id IS NOT NULL OR v.designated_procurement IS NOT NULL)",
            (project["project"],),
        ).fetchone() == (0,)
    assert first["clauses"] == 6 and first["requirements"] == 3 and first["ai_drafts"] == 3
    assert first["comparisons"] > 0 and first["gaps"] >= 1
    assert first["options"] >= first["gaps"] and first["grades"] >= 1

    again, events = run(worker_dsn, project, extractor)
    assert [e["step"] for e in events] == STEPS
    assert extractor.calls == 2
    assert again["counts"] == {"requirements": 0, "proposals": 0}
    assert stored(admin, project) == first


def test_a_transformer_rating_is_not_met_by_breaker_evidence_on_the_worker_path(
    worker_dsn, admin, tmp_path
):
    """재현(KAN-199 #329 리뷰): 처리 함수가 변압기 rated_current 400 A 요구를 MCCB 600 A 근거로 EVIDENCE_FOUND로
    두지 않는다. 요구 계열(TRANSFORMER) 후보만 비교하고, 비교 행의 search_trace에 family가 남는다."""
    quote = "The transformer rated current shall be 400 A minimum."
    spec = f"# SECTION 26 12 19\n\nPART 2 - PRODUCTS\n\n2.1 TRANSFORMERS\n\nA. {quote}\n".encode()
    wanted = {
        **REQUIREMENTS[0],
        "quote": quote,
        "attribute": "rated current",
        "attribute_key": "rated_current",
        "value": "400",
        "unit": "A",
    }

    class OneRequirement(FakeExtractor):
        def __call__(self, _prompt):
            self.calls += 1
            return Generation(
                text=json.dumps({"requirements": [wanted]}),
                usage=Usage(),
                model_version="fake",
                finish_reasons=[],
            )

    project = make_project(admin, tmp_path, spec)
    run(worker_dsn, project, OneRequirement())
    with psycopg.connect(admin) as conn:
        status = conn.execute(
            "SELECT v.status FROM tft.requirement_versions v JOIN tft.requirements r ON r.id = v.requirement_id "
            "WHERE r.project_id=%s",
            (project["project"],),
        ).fetchall()
        compared = conn.execute(
            "SELECT p.product_code, p.search_trace FROM tft.product_comparisons p "
            "JOIN tft.requirements r ON r.id = p.requirement_id WHERE r.project_id=%s",
            (project["project"],),
        ).fetchall()
    assert status == [("INSUFFICIENT_EVIDENCE",)]
    assert {code for code, _ in compared} == {"TRF-DRY-630", "TRF-OIL-1000"}
    assert all(trace["filters"] == ["family=TRANSFORMER"] for _, trace in compared)


def test_annotate_reports_its_pending_step_only(worker_dsn, project):
    """메모 해석은 아직 단계가 없어 counts 0을 남긴다. export 실제 동작은 draft routes DB 검사에서 확인한다."""
    summary, events = run(worker_dsn, project, FakeExtractor(), kind="rfp.annotate")
    assert events == [{"step": "annotate", "counts": {"requirements": 0, "proposals": 0}}]
    assert summary["needs_review"] is False


def test_export_without_a_reservation_fails_instead_of_reporting_success(worker_dsn, project):
    with pytest.raises(ValueError, match="TFT_EXPORT_NOT_FOUND"):
        run(worker_dsn, project, FakeExtractor(), kind="rfp.export")


def test_unknown_input_or_changed_file_fails(worker_dsn, project, admin):
    """접수 입력 판본을 찾지 못하거나 원문 해시가 다르면 처리하지 않는다(worker가 FAILED로 끝낸다)."""
    with pytest.raises(ValueError, match="TFT_WORKER_INPUT_NOT_FOUND"):
        run(worker_dsn, project, FakeExtractor(), version="f" * 64)
    (project["root"] / f"rfp/{project['docver']}.md").write_bytes(SPEC + b"\nchanged\n")
    with pytest.raises(ValueError, match="TFT_DOCUMENT_MISMATCH"):
        run(worker_dsn, project, FakeExtractor())
    assert stored(admin, project)["requirements"] == 0


def test_a_stop_after_compare_leaves_no_requirement_rows(worker_dsn, project, admin):
    """조항~candidates는 한 트랜잭션이다. 그 사이에 멈추면(소유권 상실·취소) 조항도 요구 행도 남지 않는다."""
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(worker_dsn))
    processor = make_processor(
        settings, provider=FakeExtractor(), store=LocalDocumentStore(project["root"])
    )
    job = TftJob(
        job_id=uuid4(),
        kind="rfp.process",
        project_id=project["project"],
        input_version=input_version([project["docver"]]),
        actor_id=SALES,
    )

    def emit(values):
        if values["step"] == "supplement":
            raise RuntimeError("LEASE_LOST")

    with pytest.raises(RuntimeError):
        processor(job, emit)
    after = stored(admin, project)
    # 조항도 같은 트랜잭션이라 남지 않는다(조항 = 끝까지 처리했다는 표시).
    assert (after["clauses"], after["requirements"], after["comparisons"], after["options"]) == (
        0,
        0,
        0,
        0,
    )


def test_a_document_without_requirements_is_not_extracted_again(worker_dsn, project, admin):
    """요구가 0건인 문서도 조항이 저장돼 처리됨으로 남고, 다시 돌리면 모델을 부르지 않는다."""

    class Empty(FakeExtractor):
        def __call__(self, _prompt):
            self.calls += 1
            return Generation(
                text='{"requirements": []}', usage=Usage(), model_version="fake", finish_reasons=[]
            )

    extractor = Empty()
    summary, _ = run(worker_dsn, project, extractor)
    assert summary["counts"]["requirements"] == 0 and extractor.calls >= 2
    calls = extractor.calls
    assert stored(admin, project)["clauses"] == 6
    run(worker_dsn, project, extractor)
    assert extractor.calls == calls


def test_every_model_call_extends_the_lease_and_a_stop_is_not_swallowed(worker_dsn, project, admin):
    """모델 호출마다 진행을 남긴다. 그 진행에서 멈추면(취소·소유권 상실) 엔진이 삼키지 않고 바로 멈춘다."""

    class Stop(Exception):
        pass

    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(worker_dsn))
    extractor = FakeExtractor()
    processor = make_processor(
        settings, provider=extractor, store=LocalDocumentStore(project["root"])
    )
    job = TftJob(
        job_id=uuid4(),
        kind="rfp.process",
        project_id=project["project"],
        input_version=input_version([project["docver"]]),
        actor_id=SALES,
    )
    seen = []

    def emit(values):
        seen.append(values)
        if values == {"step": "extract"}:
            raise Stop

    with pytest.raises(Stop):
        processor(job, emit)
    assert extractor.calls == 1 and seen[-1] == {"step": "extract"}
    assert stored(admin, project)["requirements"] == 0


@pytest.mark.parametrize(
    ("printed", "review"), [("5.75 percent ±10%", True), ("5.75 percent", False)]
)
def test_a_requirement_level_review_reaches_the_job_summary(
    worker_dsn, admin, tmp_path, profile, printed, review
):
    """KAN-199: 추출은 VALID여도 요구별 검토 표시(원문의 허용 오차가 빠짐, KAN-185)가 있으면 작업 요약이 검토다.

    프로필이 없으면 그것만으로 검토가 되므로 profile을 넣어 요구별 표시만 본다.
    """
    quote = f"The impedance shall be {printed}."
    spec = f"# SECTION 26 12 19\n\nPART 2 - PRODUCTS\n\n2.1 TRANSFORMERS\n\nA. {quote}\n".encode()
    wanted = {**REQUIREMENTS[1], "clause_no": "2.1.A", "quote": quote, "designated_model": None}
    del wanted["designated_manufacturer"]

    class OneRequirement(FakeExtractor):
        def __call__(self, _prompt):
            self.calls += 1
            return Generation(
                text=json.dumps({"requirements": [wanted]}),
                usage=Usage(),
                model_version="fake",
                finish_reasons=[],
            )

    summary, _ = run(worker_dsn, make_project(admin, tmp_path, spec), OneRequirement())
    assert summary["needs_review"] is review


def test_insufficient_requirements_are_graded_and_an_assembly_needs_an_integrator(
    worker_dsn, project, admin, profile
):
    """근거 부족 요구도 등급 대상이고, 조립품 조항이 있으면 통합 책임이 후보 제약으로 들어간다."""
    wanted = {
        **REQUIREMENTS[0],
        "attribute_key": "arc_flash_reduction",
        "assembly_clause": True,
    }

    class OneRequirement(FakeExtractor):
        def __call__(self, _prompt):
            self.calls += 1
            return Generation(
                text=json.dumps({"requirements": [wanted]}),
                usage=Usage(),
                model_version="fake",
                finish_reasons=[],
            )

    run(worker_dsn, project, OneRequirement())
    with psycopg.connect(admin) as conn:
        status, grade, reasons = conn.execute(
            "SELECT v.status, g.grade, g.reasons FROM tft.partner_grades g "
            "JOIN tft.requirement_versions v ON v.requirement_id = g.requirement_id "
            "WHERE g.project_id=%s AND g.candidate_code=%s",
            (project["project"], profile),
        ).fetchone()
    assert status == "INSUFFICIENT_EVIDENCE"
    assert grade != "MANDATORY_CONFIRMED"
    assert "통합 책임" in " ".join(reasons)


def test_an_assembly_clause_is_stored_and_read_back_for_the_project(worker_dsn, admin, tmp_path):
    """KAN-199: 추출 assembly_clause를 판본에 저장하고, 프로젝트 읽기 질의가 근거 조항을 돌려준다(읽을 때 계산)."""
    quote = "The unit shall be supplied as one assembly."
    spec = f"# SECTION 26 12 19\n\nPART 2 - PRODUCTS\n\n2.1 UNIT\n\nA. {quote}\n".encode()
    wanted = {
        **REQUIREMENTS[0],
        "quote": quote,
        "attribute": "assembly",
        "attribute_key": "OTHER",
        "value": None,
        "unit": None,
        "operator": "TEXT",
        "assembly_clause": True,
    }

    class OneRequirement(FakeExtractor):
        def __call__(self, _prompt):
            self.calls += 1
            return Generation(
                text=json.dumps({"requirements": [wanted]}),
                usage=Usage(),
                model_version="fake",
                finish_reasons=[],
            )

    project = make_project(admin, tmp_path, spec)
    run(worker_dsn, project, OneRequirement())

    async def read():
        # 운영 API 역할(tft_app)의 실제 권한으로 읽는다.
        async with await psycopg.AsyncConnection.connect(
            make_conninfo(admin, user="tft_app", password="test-only"), row_factory=dict_row
        ) as conn:
            return await integration_state(conn, project["project"])

    holder, rows = asyncio.run(read())
    assert holder["integration_holder"] == "UNRESOLVED"
    assert [(r["clause_no"], r["assembly_clause"]) for r in rows] == [("2.1.A", True)]
    result = project_integration([StoredRequirement.model_validate(r) for r in rows], "UNRESOLVED")
    assert result["required"] is True and result["basis_clause_ids"] == [rows[0]["clause_id"]]


class OneResponse(FakeExtractor):
    """정해진 요구 목록을 돌려준다."""

    def __init__(self, requirements):
        super().__init__()
        self.requirements = requirements

    def __call__(self, _prompt):
        self.calls += 1
        return Generation(
            text=json.dumps({"requirements": self.requirements}),
            usage=Usage(),
            model_version="fake",
            finish_reasons=[],
        )


def spec_of(*sentences):
    body = "\n\n".join(f"{letter}. {text}" for letter, text in zip("ABCD", sentences, strict=False))
    return f"# SECTION 26 12 19\n\nPART 2 - PRODUCTS\n\n2.1 UNIT\n\n{body}\n".encode()


def placeholder(clause_no, quote):
    return {
        **REQUIREMENTS[0],
        "clause_no": clause_no,
        "quote": quote,
        "attribute": "voltage",
        "attribute_key": "OTHER",
        "value": "<Insert value>",
        "unit": "kV",
        "operator": "EQ",
    }


def drafts_of(admin, project):
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT status, issue_type, target, derivation_kind, requirement_id, clause_id, draft_key, "
            "internal_assumption FROM tft.customer_questions WHERE project_id=%s ORDER BY created_at",
            (project["project"],),
        ).fetchall()


def test_question_drafts_are_stored_as_drafts_with_the_tft_worker_role(worker_dsn, admin, tmp_path):
    """KAN-199: 처리 함수(tft_worker 권한)가 미정 항목 질문을 DRAFT로 저장한다. 다시 돌려도 늘지 않는다."""
    quote = "The voltage shall be <Insert value> kV."
    project = make_project(admin, tmp_path, spec_of(quote))
    extractor = OneResponse([placeholder("2.1.A", quote)])
    run(worker_dsn, project, extractor)
    [draft] = drafts_of(admin, project)
    assert (draft["status"], draft["issue_type"], draft["target"], draft["derivation_kind"]) == (
        "DRAFT",
        "EMPTY_VALUE",
        "CUSTOMER",
        "DERIVED",
    )
    assert draft["requirement_id"] is not None and draft["clause_id"] is not None
    # 회신 전 메모와 출처 조항은 InternalAssumption 형식으로 남는다.
    assert draft["internal_assumption"]["basis_clause"] == "2.1.A"
    assert draft["internal_assumption"]["value"].startswith("미정")
    run(worker_dsn, project, extractor)
    assert len(drafts_of(admin, project)) == 1


def test_a_new_document_version_does_not_repeat_the_same_question(worker_dsn, admin, tmp_path):
    """같은 조항 번호·같은 문장이면 판본이 달라도 같은 질문이다(draft_key). 문장이 바뀌면 새 질문이다."""
    quote = "The voltage shall be <Insert value> kV."
    project = make_project(admin, tmp_path, spec_of(quote))
    run(worker_dsn, project, OneResponse([placeholder("2.1.A", quote)]))

    def new_version(number, text):
        document_version = uuid4()
        key = f"rfp/{document_version}.md"
        body = spec_of(text)
        (project["root"] / key).write_bytes(body)
        with psycopg.connect(admin) as conn:
            conn.execute(
                "INSERT INTO tft.document_versions (id, document_id, version, mime, storage_key, sha256, "
                "size, edit_trace_support, data_origin, derivation_kind, known_at, uploaded_by) "
                "VALUES (%s,%s,%s,'text/markdown',%s,%s,%s,false,'synthetic','PROVIDED',now(),%s)",
                (
                    document_version,
                    project["document"],
                    number,
                    key,
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    uuid4(),
                ),
            )
            conn.execute(
                "UPDATE tft.documents SET latest_version=%s WHERE id=%s",
                (number, project["document"]),
            )
        return document_version

    same = new_version(2, quote)
    run(
        worker_dsn,
        project,
        OneResponse([placeholder("2.1.A", quote)]),
        version=input_version([same]),
    )
    assert len(drafts_of(admin, project)) == 1
    changed = "The voltage shall be <Insert value> kV at 60 Hz."
    other = new_version(3, changed)
    run(
        worker_dsn,
        project,
        OneResponse([placeholder("2.1.A", changed)]),
        version=input_version([other]),
    )
    assert len(drafts_of(admin, project)) == 2


def test_a_draft_links_only_the_requirement_its_source_quotes(worker_dsn, admin, tmp_path):
    """KAN-199: 한 조항의 요구가 둘이면 원문이 맞는 요구에만 연결한다(조항의 첫 요구로 넘겨짚지 않는다)."""
    first, second = "The rating shall be 1500 kVA minimum", "Alternates need Owner approval"
    project = make_project(admin, tmp_path, spec_of(f"{first}. {second}."))
    requirements = [
        {**REQUIREMENTS[0], "quote": first},
        {
            **REQUIREMENTS[0],
            "quote": second,
            "attribute": "alternates",
            "attribute_key": "OTHER",
            "value": None,
            "unit": None,
            "operator": "TEXT",
            "approval_procedure_missing": True,
        },
    ]
    run(worker_dsn, project, OneResponse(requirements))
    [draft] = drafts_of(admin, project)
    assert draft["issue_type"] == "CONDITION_MISSING"
    with psycopg.connect(admin) as conn:
        linked = conn.execute(
            "SELECT quote FROM tft.requirement_versions WHERE requirement_id=%s",
            (draft["requirement_id"],),
        ).fetchone()
    assert linked == (second,)


def test_a_placeholder_covering_two_requirements_stays_clause_only(worker_dsn, admin, tmp_path):
    """미정 항목 출처는 조항 전체 문장이라 요구 둘이 모두 포함되면 갈린다: 넘겨짚지 않고 조항만 있는 질문이다."""
    first, second = "The rating shall be 1500 kVA minimum", "The voltage shall be <Insert value> kV"
    project = make_project(admin, tmp_path, spec_of(f"{first}. {second}."))
    run(
        worker_dsn,
        project,
        OneResponse([{**REQUIREMENTS[0], "quote": first}, placeholder("2.1.A", second)]),
    )
    [draft] = drafts_of(admin, project)
    assert draft["requirement_id"] is None and draft["clause_id"] is not None


def test_a_draft_stays_clause_only_when_the_requirement_is_not_settled():
    """연결 규칙: 같은 조항에 원문이 맞는 요구가 없거나 둘 이상으로 갈리면 조항만 있는 질문이다."""
    source = QuestionDraft(
        kind="PLACEHOLDER",
        sources=[QuestionSource(clause_no="2.1.A", quote="voltage <Insert value> kV")],
        question="q",
        pre_reply_note="n",
    )

    def item(clause_no, quote):
        return ExtractedRequirement.model_validate(
            {**placeholder(clause_no, quote), "conditions": []}
        )

    a, b = uuid4(), uuid4()
    other_quote = (a, item("2.1.A", "rating 1500 kVA"))
    assert draft_requirement(source, [other_quote]) is None
    other_clause = (a, item("2.1.B", "voltage <Insert value> kV"))
    assert draft_requirement(source, [other_clause]) is None
    same = (a, item("2.1.A", "voltage <Insert value> kV"))
    assert draft_requirement(source, [same, other_quote]) == a
    assert draft_requirement(source, [same, (b, same[1])]) is None


def test_the_semantic_fields_are_stored_with_the_requirement_version(worker_dsn, admin, tmp_path):
    """KAN-188(T15): 허용 오차·검토 표시·속성 키·계열을 판본에 저장한다(값은 원문 그대로)."""
    quote = "The impedance shall be 5.75 percent ±10%."
    wanted = {
        **placeholder("2.1.A", quote),
        "attribute": "impedance",
        "attribute_key": "impedance",
        "value": "5.75",
        "unit": "%",
        "tolerance": "±10%",
    }
    missing = "The BIL shall be 95 kV +/- 5."
    without = {
        **wanted,
        "clause_no": "2.1.B",
        "quote": missing,
        "attribute_key": "bil",
        "value": "95",
        "unit": "kV",
        "tolerance": None,
        "equipment_family": "OTHER",
    }
    project = make_project(admin, tmp_path, spec_of(quote, missing))
    run(worker_dsn, project, OneResponse([wanted, without]))
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT v.value, v.tolerance, v.needs_review, v.attribute_key, v.equipment_family "
            "FROM tft.requirement_versions v JOIN tft.requirements r ON r.id = v.requirement_id "
            "WHERE r.project_id=%s ORDER BY v.value",
            (project["project"],),
        ).fetchall()
    assert rows == [
        ("5.75", "±10%", False, "impedance", "TRANSFORMER"),
        # 인용에 허용 오차 표기가 있는데 칸이 비면 코드가 검토로 둔다.
        ("95", None, True, "bil", "OTHER"),
    ]


def test_reprocessing_keeps_human_sentence_without_a_model_call(worker_dsn, project, admin):
    from backend.api.tft.rfp import draft_repository as db
    from backend.api.tft.rfp import drafts
    from backend.api.tft.rfp import schemas as s

    extractor = FakeExtractor()
    run(worker_dsn, project, extractor)
    calls = extractor.calls

    async def human_edit():
        async with await psycopg.AsyncConnection.connect(admin, row_factory=dict_row) as conn:
            await db.lock(conn, project["project"])
            current = await db.load_snapshot(conn, project["project"], 1)
            row = next(row for part in current.sections for row in part.sentences)
            updated, reasons = drafts.edit(
                current,
                s.DraftEdit.model_validate(
                    {
                        "version": 1,
                        "edits": [
                            {
                                "sentence_id": row.sentence_id,
                                "text": "사람의 검토 문장 원형",
                                "reason": "근거 확인",
                                "evidence": row.evidence,
                            }
                        ],
                    }
                ),
                await drafts.valid_references(conn, project["project"]),
            )
            await db.save(conn, updated, actor=SALES, reasons=reasons)
            return row.sentence_id

    key = asyncio.run(human_edit())
    run(worker_dsn, project, extractor)
    assert extractor.calls == calls
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT latest_version FROM tft.drafts WHERE project_id=%s", (project["project"],)
            ).fetchone()[0]
            == 3
        )
        assert conn.execute(
            "SELECT text,provenance_kind,edit_reason FROM tft.draft_sentences WHERE project_id=%s AND draft_version=3 AND sentence_key=%s",
            (project["project"], key),
        ).fetchone() == ("사람의 검토 문장 원형", "human_input", "근거 확인")


@pytest.mark.parametrize("human_race", [False, True])
def test_rewrite_worker_uses_fake_provider_and_human_save_wins(
    worker_dsn, project, admin, human_race
):
    from backend.api.tft.rfp import draft_repository as db
    from backend.api.tft.rfp import draft_rewrite, drafts
    from backend.api.tft.rfp import schemas as s
    from backend.engine.rfp.draft_rewrite import fake_provider as fake_rewrite
    from scripts.rfp_draft_trace_input import trace_input

    run(worker_dsn, project, FakeExtractor())
    key = uuid4().hex

    async def prepare():
        async with await psycopg.AsyncConnection.connect(admin, row_factory=dict_row) as conn:
            frozen = await draft_rewrite.reserve(conn, project["project"], SALES, key, 1)
            trace = await trace_input(conn, project["project"])
            return frozen, trace

    frozen, before = asyncio.run(prepare())
    called = []

    async def human_save():
        async with await psycopg.AsyncConnection.connect(admin, row_factory=dict_row) as conn:
            await conn.execute("SET LOCAL lock_timeout='1s'")
            await db.lock(conn, project["project"])
            current = await db.load_snapshot(conn, project["project"], 1)
            row = next(row for part in current.sections for row in part.sentences)
            updated, reasons = drafts.edit(
                current,
                s.DraftEdit.model_validate(
                    {
                        "version": 1,
                        "edits": [
                            {
                                "sentence_id": row.sentence_id,
                                "text": "모델 호출 중 사람이 저장한 문장 원형",
                                "reason": "먼저 확인",
                                "evidence": row.evidence,
                            }
                        ],
                    }
                ),
                await drafts.valid_references(conn, project["project"]),
            )
            await db.save(conn, updated, actor=SALES, reasons=reasons)

    def provider(prompt):
        if human_race and not called:
            asyncio.run(human_save())
        called.append(prompt)
        return fake_rewrite(prompt)

    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(worker_dsn))
    processor = make_processor(
        settings, provider=FakeExtractor(), draft_provider=provider, store=None
    )
    job = TftJob(
        job_id=uuid4(),
        kind="rfp.draft_rewrite",
        project_id=project["project"],
        input_version=frozen,
        actor_id=SALES,
    )
    processor(job, lambda _: None)
    assert called  # 입력이 전부 차단되어 무호출로 통과한 것과 구분한다.

    async def result():
        async with await psycopg.AsyncConnection.connect(admin, row_factory=dict_row) as conn:
            return await db.load_snapshot(conn, project["project"], 2), await trace_input(
                conn, project["project"]
            )

    latest, after = asyncio.run(result())
    with psycopg.connect(admin) as conn:
        state = conn.execute(
            "SELECT state FROM tft.draft_rewrites WHERE project_id=%s AND request_key=%s",
            (project["project"], key),
        ).fetchone()[0]
        assert state == ("discarded" if human_race else "saved")
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.draft_rewrite_calls WHERE rewrite_id=(SELECT id FROM tft.draft_rewrites WHERE project_id=%s AND request_key=%s)",
                (project["project"], key),
            ).fetchone()[0]
            > 0
        )
    if human_race:
        assert any(
            row.text == "모델 호출 중 사람이 저장한 문장 원형"
            and row.provenance_kind == "human_input"
            for part in latest.sections
            for row in part.sentences
        )
    else:
        assert before == after
        assert any(
            row.provenance_kind == "ai_draft" for part in latest.sections for row in part.sentences
        )
    calls = len(called)
    processor(job, lambda _: None)
    assert len(called) == calls
