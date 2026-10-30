"""RFP 처리 함수(KAN-199·KAN-188)를 가짜 추출기·합성 Markdown 원문·전용 PostgreSQL(tft_worker 권한)로 확인한다.

행과 원문은 모두 합성이다. tft 마이그레이션 적용은 tests/tft/test_rfp_http_base_db.py의 고정물을 쓴다.
"""

# ruff: noqa: F811

import hashlib
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.agent.processor_registry import TftJob
from backend.api.tft.partners.loader import load_profiles
from backend.api.tft.rfp.jobs import input_version
from backend.api.tft.worker_processor import make_processor, sections_of
from backend.engine.documents.router import extract
from backend.engine.rfp.clauses import build_clause_tree
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
    row = source.model_dump(mode="json") | {"candidate_code": code, "profile_version": 1}
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
        document_root=project["root"],
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
            "SELECT v.designated_model, v.designated_manufacturer FROM tft.requirement_versions v "
            "JOIN tft.requirements r ON r.id = v.requirement_id "
            "WHERE r.project_id=%s AND v.designated_model IS NOT NULL",
            (project["project"],),
        ).fetchall() == [("Resibloc", "Eaton")]
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


def test_export_and_annotate_report_their_step_only(worker_dsn, project):
    """내보내기·메모 해석은 단계가 아직 없어 그 단계 counts 0만 남긴다."""
    for kind, step in (("rfp.export", "export"), ("rfp.annotate", "annotate")):
        summary, events = run(worker_dsn, project, FakeExtractor(), kind=kind)
        assert events == [{"step": step, "counts": {"requirements": 0, "proposals": 0}}]
        assert summary["needs_review"] is False


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
    processor = make_processor(settings, provider=FakeExtractor(), document_root=project["root"])
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
    processor = make_processor(settings, provider=extractor, document_root=project["root"])
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
    worker_dsn, admin, tmp_path, printed, review
):
    """KAN-199: 추출은 VALID여도 요구별 검토 표시(원문의 허용 오차가 빠짐, KAN-185)가 있으면 작업 요약이 검토다."""
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
