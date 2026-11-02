"""KAN-190 A09: 14번 시연 A·B·C 경로 통합 시험(전용 PostgreSQL, 가짜 추출기).

업로드된 14번형 규격서를 처리 함수(rfp.process)로 끝까지 돌려, 저장된 요구 상태가 demo_cases.json(기획서 4.7~4.9)의
기대 상태와 같은지, 협력사 후보 등급이 기대 등급과 같은지, 질문 초안이 시연 C 기대와 같은지, 사람이 고친 상태가 재실행 뒤에도
남는지, 초안(검토본)과 내보내기 예약이 이어지는지 본다.

한계(보고에도 적는다): 모델 호출은 꺼져 있어 요구 추출은 이 파일의 가짜 추출기가 돌려주는 값이다. 그래서 이 시험은 추출 품질이
아니라 추출 이후 단계의 연결을 확인한다. 14번 원문 DOCX는 저장소에 없으므로 기본은 14번 2.4·2.5·2.6 조항 원문을 옮겨 만든
합성 Markdown이고, 환경변수 RFP_DEMO_14_PATH가 가리키는 DOCX가 있으면 같은 시험을 그 원문으로도 돌린다(없으면 그 시험만 건너뛴다).
"""

# ruff: noqa: F811

import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from backend.api.tft.partners.loader import load_profiles, profile_row
from backend.shared.ai import Generation, Usage
from tests.tft.test_rfp_http_base_db import SALES, admin, app_dsn  # noqa: F401
from tests.tft.test_worker_processor_db import make_project, run, worker_dsn  # noqa: F401

DEMO_CASES = Path("backend/api/tft/partners/demo_cases.json")
CASES = {c["case"]: c for c in json.loads(DEMO_CASES.read_text(encoding="utf-8"))["cases"]}

# 14번 원문의 현행 조항 문장(취소선 뺀 값). 조항 번호는 14번의 번호다.
SPEC14 = """# SECTION 26 11 16.11

PART 1 - GENERAL

1.1 SUMMARY

A. Section includes:

1. Unit substation consisting of:

a. Medium-voltage, metal-clad switchgear section.

PART 2 - PRODUCTS

2.1 MANUFACTURERS

A. Manufacturers: Subject to compliance with requirements, provide products by one of the following. Products that may be incorporated into the Work with Owner's approval include, but are not limited to, the following:

1. Eaton.

2. ABB.

2.2 SYSTEM DESCRIPTION

A. Product Requirements:

1. Electrical Components, Devices, and Accessories: Listed and labeled as defined in NFPA 70, by a qualified testing agency, and marked for intended location and application.

2.4 DUAL FUSIBLE LOAD INTERRUPTER SWITCHGEAR

A. Construction:

1. Deadfront, metal-enclosed, fixed-mount, arc-resistant, duplex, fusible interrupter switchgear assembly of vertical sections.

E. Ratings:

1. Medium-voltage switchgear ratings:

a. Rated Maximum Voltage and BIL: 15 kV, 95 kV.

c. Rated Power-Frequency Withstand Voltage: <Insert value> kV RMS.

e. Rated Short-Time Withstand Current (600 A Continuous Current Ratings): 38 kA SYM RMS for 2 s.

2.5 EPOXY CAST RESIN, DRY-TYPE TRANSFORMER SECTION

A. Style: Indoor, ventilated, insulated with epoxy, reinforced with glass fiber. Basis of design: Eaton Resibloc.

C. Cooling System shall contain all necessary components and wiring, including fans, for automatically increasing the kVA rating by:

1. 480Y/277V Secondary: 33% with fans at 115 degree C rise above 40 degree C and average ambient of 30 degree C.

E. Transformer mounted serial communications module shall render the following data via RS-485 ModBus interface, in addition to all parameters available from the temperature monitor and fan control unit:

G. Transformer Temperature Hi-Temp Alarm, Trip signal and mA current loop shall be wired to low-voltage equipment as standard.

1. Dualization of power transformer winding temperature sensor - Dual temperature sensors shall be applied to the winding of each phase of the transformer.

I. Insulation Materials: IEEE C57.12.01.

1. The electrical insulation system shall utilize Class F materials in a fully rated 155 degrees C system.

K. BIL: 95 kV.

L. Windings: The high and low voltage windings shall be aluminum.

M. Full-Capacity Voltage Taps: Four nominal 2.5 percent taps, two above and two below rated primary voltage.

N. Impedance: 5.75 percent, +/-10%.

O. The transformer shall comply with the sound level standards for dry transformers as defined in NEMA TR1. The measurement procedure shall be as specified in ANSI C57.12.90.

2.6 SECONDARY DISTRIBUTION SECTION SWITCHGEAR FOR LOW VOLTAGE

A. Switchgear shall be drawout, low-voltage switchgear, complying with IEEE C37.20.1 and UL 1558.

E. Circuit breakers:

1. Comply with IEEE C37.13 and UL 1066.

4. Trip units shall provide LSIG (long-time, short-time, instantaneous-trip and ground-fault) protection functions.

H. Arc flash protection and mitigation solutions: One of the following methods and technologies shall be implemented:

1. Zone-selective interlocking.

2. Energy-reducing maintenance switch with local status indicator.

PART 3 - EXECUTION

3.2 INSTALLATION

A. Install equipment according to manufacturer's written instructions.

3.6 FIELD QUALITY CONTROL

A. Perform tests and inspections.
"""


def req(clause_no, quote, key, family, value, unit=None, operator="EQ", **over):
    """추출 요구 한 줄(기본은 시연 A의 변압기 요구)."""
    return {
        "clause_no": clause_no,
        "quote": quote,
        "kind": "SPEC",
        "equipment": over.pop("equipment", "dry-type transformer"),
        "task": None,
        "attribute": key.replace("_", " "),
        "attribute_key": key,
        "equipment_family": family,
        "value": value,
        "unit": unit,
        "operator": operator,
        "conditions": [],
        "standard_system": "NONE",
        "necessity": "MANDATORY",
        "designated_model": None,
        **over,
    }


def cert(clause_no, quote, standard, family, equipment, system, listed=False):
    return req(
        clause_no, quote, "OTHER", family, standard, None, "TEXT", kind="COMPLIANCE",
        equipment=equipment, standard_system=system, certification_listed=listed,
    )  # fmt: skip


TRF = "TRANSFORMER"
# 시연 A: demo_cases.json 요구 순서(table_row 1~9, 7·8행은 요구별로 나눈 것).
DEMO_A = {
    "탭 ±2×2.5%": req("2.5.M", "Full-Capacity Voltage Taps: Four nominal 2.5 percent taps, two above and two below rated primary voltage.", "tap", TRF, "±2×2.5%", operator="TEXT", canonical_value="±2x2.5%"),
    "절연 Class F": req("2.5.I.1", "The electrical insulation system shall utilize Class F materials in a fully rated 155 degrees C system.", "insulation_class", TRF, "Class F"),
    "임피던스 5.75% ±10%": req("2.5.N", "Impedance: 5.75 percent, +/-10%.", "impedance", TRF, "5.75", "%", tolerance="+/-10%"),
    "강제 공랭 시 용량 증대 33%(115℃ 상승)": req(
        "2.5.C.1", "480Y/277V Secondary: 33% with fans at 115 degree C rise above 40 degree C and average ambient of 30 degree C.",
        "forced_air_capacity_increase", TRF, "33", "%", "GTE", conditions=[{"name": "temperature_rise", "value": "115", "unit": "°C"}],
    ),
    "2차 전압 480Y/277V": req("2.5.C.1", "480Y/277V Secondary", "secondary_voltage", TRF, "480", "V"),
    "BIL 95kV": req("2.5.K", "BIL: 95 kV.", "bil", TRF, "95", "kV", "GTE"),
    "알루미늄 권선": req("2.5.L", "Windings: The high and low voltage windings shall be aluminum.", "winding_material", TRF, "aluminum", operator="TEXT"),
    "이중 온도센서": req("2.5.G.1", "Dual temperature sensors shall be applied to the winding of each phase of the transformer.", "winding_temperature_sensor", TRF, "dual", operator="TEXT"),
    "ModBus RS-485 연동": req("2.5.E", "via RS-485 ModBus interface", "communication_interface", TRF, "RS-485 ModBus", operator="TEXT"),
    "IEEE C57.12.01": cert("2.5.I", "Insulation Materials: IEEE C57.12.01.", "IEEE C57.12.01", TRF, "dry-type transformer", "IEEE"),
    "소음 NEMA TR1(측정 ANSI C57.12.90)": cert("2.5.O", "as defined in NEMA TR1", "NEMA TR1", TRF, "dry-type transformer", "NEMA"),
    "설계 기준: 타사 몰드 변압기 모델": req("2.5.A", "Basis of design: Eaton Resibloc", "OTHER", TRF, "Eaton Resibloc", operator="TEXT", basis_of_design=True),
    "부품 Listed and labeled(NFPA 70)": cert("2.2.A.1", "Listed and labeled as defined in NFPA 70", "NFPA 70", TRF, "dry-type transformer", "NONE", listed=True),
}  # fmt: skip
SWG = "MV_EQUIP"
MV = {"equipment": "medium-voltage switchgear"}
DEMO_B1 = {
    "형식: 금속 폐쇄형·고정식·퓨즈 부하개폐기": req("2.4.A.1", "Deadfront, metal-enclosed, fixed-mount, arc-resistant, duplex, fusible interrupter switchgear assembly of vertical sections.", "form", SWG, "metal-enclosed, fixed-mount, arc-resistant, duplex, fusible interrupter switchgear", operator="TEXT", **MV),
    "정격 최대전압 15kV / BIL 95kV": req("2.4.E.1.a", "Rated Maximum Voltage and BIL: 15 kV, 95 kV.", "rated_max_voltage", SWG, "15", "kV", "GTE", **MV),
    "단시간 내전류 38kA, 2s": req(
        "2.4.E.1.e", "Rated Short-Time Withstand Current (600 A Continuous Current Ratings): 38 kA SYM RMS for 2 s.",
        "short_time_withstand_current", SWG, "38", "kA", "GTE", conditions=[{"name": "duration", "value": "2", "unit": "s"}], **MV,
    ),
    "아크 내성 구조(2.4 A.1), 부품 등록 Listed and labeled(2.2)": req("2.4.A.1", "arc-resistant", "arc_resistant", SWG, "arc-resistant", operator="TEXT", **MV),
}  # fmt: skip
LVS, LVB = "LV_SWITCHGEAR", "LV_BREAKER"
DEMO_B2 = {
    "인출형, LSIG 보호": req("2.6.E.4", "LSIG (long-time, short-time, instantaneous-trip and ground-fault) protection functions", "protection_functions", LVB, "LSIG", operator="TEXT", equipment="drawout circuit breaker"),
    "배전반 IEEE C37.20.1 + UL 1558": cert("2.6.A", "complying with IEEE C37.20.1 and UL 1558", "UL 1558", LVS, "low-voltage switchgear", "UL", listed=True),
    "차단기 IEEE C37.13 + UL 1066": cert("2.6.E.1", "Comply with IEEE C37.13 and UL 1066", "UL 1066", LVB, "low-voltage circuit breaker", "UL", listed=True),
    "아크플래시 저감 4가지 방식 중 하나": req("2.6.H.2", "Energy-reducing maintenance switch", "arc_flash_reduction", LVS, "maintenance switch", operator="TEXT", equipment="low-voltage switchgear"),
}  # fmt: skip
# 시연 C의 질문 3개가 나오게 하는 요구(표에는 없고 질문 엔진 입력이다): 1.1 metal-clad ↔ 2.4 metal-enclosed(같은 장비·같은 속성의
# 값이 다름)와 2.1 제조사 목록 "with Owner's approval"(승인 절차 없음). 빈 값(2.4 E.1.c)은 조항 원문의 자리표시자에서 나온다.
DEMO_C = {
    # 불일치 질문은 두 요구의 인용에서 값만 가린 틀이 같아야 나온다. 모델이 값만 인용하는 경우를 가정한다(긴 문장째 인용하면 틀이 달라
    # 질문이 나오지 않는다: 추출 인용 단위에 달린 동작이라 보고서에 적는다).
    "1.1 metal-clad": req("1.1.A.1.a", "metal-clad", "form", SWG, "metal-clad", operator="TEXT", **MV),
    "2.4 metal-enclosed": req("2.4.A.1", "metal-enclosed", "form", SWG, "metal-enclosed", operator="TEXT", **MV),
    "2.1 제조사 승인 절차 없음": req(
        "2.1.A", "include, but are not limited to, the following",
        "OTHER", None, None, operator="TEXT", equipment="manufacturer list", approval_procedure_missing=True,
    ),
}  # fmt: skip
ALL = {**DEMO_A, **DEMO_B1, **DEMO_B2, **DEMO_C}


class DemoExtractor:
    """모든 절에 같은 요구 목록을 돌려준다(절 밖 조항은 엔진이 버린다). 모델은 부르지 않는다."""

    def __init__(self, items):
        self.items = items
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        # 절마다 그 절 원문에 인용이 있는 요구만 돌려준다(모델이 그 절을 읽고 뽑는 것처럼).
        mine = [item for item in self.items if item["quote"] in prompt]
        return Generation(
            text=json.dumps({"requirements": mine}),
            usage=Usage(),
            model_version="fake",
            finish_reasons=[],
        )


@pytest.fixture
def demo_profiles(admin):
    """가상 협력사 프로필 12개(P-A~C, L-A·B, 평가용)를 넣고 검사 뒤 지운다(다른 시험의 등급 검사에 남지 않게)."""
    profiles = load_profiles()
    inserted = []
    with psycopg.connect(admin) as conn:
        for profile in profiles:
            row = profile_row(profile)
            columns = list(row)
            found = conn.execute(
                "SELECT 1 FROM tft.partner_profiles WHERE candidate_code=%s AND profile_version=%s",
                (profile.candidate_code, profile.profile_version),
            ).fetchone()
            if found:
                continue
            conn.execute(
                f"INSERT INTO tft.partner_profiles ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})",
                [row[c] for c in columns],
            )
            inserted.append(profile.candidate_code)
    yield [p.candidate_code for p in profiles]
    with psycopg.connect(admin) as conn:
        conn.execute("DELETE FROM tft.partner_grades WHERE candidate_code = ANY(%s)", (inserted,))
        conn.execute("DELETE FROM tft.partner_profiles WHERE candidate_code = ANY(%s)", (inserted,))


# 14번 원문의 조항 번호가 합성 Markdown과 다른 항목(원문은 1.1 A.2, 합성은 1.1 A.1.a). 실제 모델은 읽은 번호를 쓰므로 원문 시험은 원문 번호로 준다.
DOCX_CLAUSES = {"metal-clad": "1.1.A.2"}


def docx_items():
    return [
        {**item, "clause_no": DOCX_CLAUSES.get(item["quote"], item["clause_no"])}
        for item in ALL.values()
    ]


def stored_status(admin, project):
    """저장된 요구의 (인용, 상태) 집합. 같은 인용이 두 번 저장될 수 있어 집합으로 모은다."""
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT DISTINCT v.quote, v.status FROM tft.requirement_versions v "
            "JOIN tft.requirements r ON r.id = v.requirement_id WHERE r.project_id=%s",
            (project["project"],),
        ).fetchall()
    by_quote: dict[str, set[str]] = {}
    for quote, status in rows:
        by_quote.setdefault(quote, set()).add(status)
    return by_quote


GROUPS = {"A": DEMO_A, "B-1": DEMO_B1, "B-2": DEMO_B2}
# demo_cases.json에 있지만 이 시험이 요구로 만들 수 없는 항목. 시연 A "1차 전압·용량(도면 참조)"은 원문에 값과 인용할 문장이 없다
# (도면 참조, 시연 사례 설정이다). 추출 요구는 인용이 필수라 가짜 추출기로도 만들 수 없다.
NOT_EXTRACTABLE = {("A", "1차 전압·용량(도면 참조)")}
# 처리 함수가 기대 상태와 다른 결과를 저장하는 항목(2026-11-02 dev 9df5089 관찰). 값이 바뀌면 시험이 알려 준다.
KNOWN_DEVIATIONS = {
    # 요구 계열(MV_EQUIP)의 자사 후보 전체를 비교해 VCB-25kV-1250(25.8 kV)이 15 kV 이상을 충족한다. 기획서 4.8의 기대(자사 SWGR-MV-12kV
    # 12 kV 기준 사양 차이)와 다르다. 단위 시험(test_rfp_comparison)은 후보를 SWGR 하나로 좁혀 이 차이를 만나지 않는다.
    ("B-1", "정격 최대전압 15kV / BIL 95kV"): "EVIDENCE_FOUND",
}


def expected_states():
    return {
        (case, r["item"]): r["expected_status"]
        for case in GROUPS
        for r in CASES[case]["requirements"]
    }


def processed(worker_dsn, admin, tmp_path, extractor=None):
    project = make_project(admin, tmp_path, SPEC14.encode())
    run(worker_dsn, project, extractor or DemoExtractor(list(ALL.values())))
    return project


def client_for(app_dsn):
    from fastapi.testclient import TestClient

    from backend.api.tft.main import create_app
    from backend.shared.settings import Settings

    return TestClient(
        create_app(Settings(_env_file=None, environment="test", database_url=app_dsn))
    )


def sales_headers(key=None):
    from backend.shared.access import Principal, principal_header

    headers = principal_header(Principal(user_id=SALES, roles=frozenset({"sales"})))
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_stored_states_match_the_planned_demo_a_b1_b2(worker_dsn, admin, tmp_path, demo_profiles):
    """시연 A 12/13, B-1 3/4, B-2 4/4가 처리 함수를 거친 뒤 DB에 기대 상태로 저장된다(나머지는 위 상수에 이유와 함께)."""
    by_quote = stored_status(admin, processed(worker_dsn, admin, tmp_path))
    mismatches = {}
    checked = 0
    for (case, item), want in expected_states().items():
        if (case, item) in NOT_EXTRACTABLE:
            continue
        got = by_quote.get(GROUPS[case][item]["quote"])
        checked += 1
        if (case, item) in KNOWN_DEVIATIONS:
            assert got == {KNOWN_DEVIATIONS[(case, item)]}, (case, item, got)
        elif got != {want}:
            mismatches[(case, item)] = (want, got)
    assert mismatches == {}
    # 모수: 기대 항목 A 14 + B-1 4 + B-2 4 = 22 중 추출 불가 1건을 뺀 21건을 확인했다.
    assert len(expected_states()) == 22 and checked == 21


@pytest.mark.xfail(
    strict=True,
    reason="KAN-190 관찰: 자사 후보 전체(VCB-25kV-1250 포함)와 비교해 15 kV 요구가 충족으로 저장된다. 기획서 4.8 기대는 사양 차이",
)
def test_b1_voltage_follows_the_plan_for_the_own_product(
    worker_dsn, admin, tmp_path, demo_profiles
):
    by_quote = stored_status(admin, processed(worker_dsn, admin, tmp_path))
    item = "정격 최대전압 15kV / BIL 95kV"
    assert by_quote[DEMO_B1[item]["quote"]] == {expected_states()[("B-1", item)]}


def overall(grades):
    """후보 등급: 하나라도 EXCLUDED면 제외, 아니면 요구별 등급 중 가장 낮은 단계(grading.RANK)."""
    from backend.api.tft.partners.grading import RANK

    if "EXCLUDED" in grades:
        return "EXCLUDED"
    return min(grades, key=RANK.index)


def test_partner_grades_match_the_planned_b1_and_b2_candidates(
    worker_dsn, admin, tmp_path, demo_profiles
):
    project = processed(worker_dsn, admin, tmp_path)
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT candidate_code, grade FROM tft.partner_grades WHERE project_id=%s",
            (project["project"],),
        ).fetchall()
    per: dict[str, list[str]] = {}
    for code, grade in rows:
        per.setdefault(code, []).append(grade)
    expected = {
        c["candidate_code"]: c["expected_grade"]
        for case in ("B-1", "B-2")
        for c in CASES[case]["candidates"]
    }
    assert expected == {
        "P-A": "REVIEW", "P-B": "EXCLUDED", "P-C": "REFERENCE_ONLY", "L-A": "REVIEW", "L-B": "EXCLUDED",
    }  # fmt: skip
    assert {code: overall(per[code]) for code in expected} == expected


def test_question_drafts_match_the_planned_demo_c(worker_dsn, admin, tmp_path, demo_profiles):
    project = processed(worker_dsn, admin, tmp_path)
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT q.issue_type, q.status, c.clause_no FROM tft.customer_questions q "
            "LEFT JOIN tft.clauses c ON c.id = q.clause_id WHERE q.project_id=%s ORDER BY 1",
            (project["project"],),
        ).fetchall()
    types = {r["issue_type"]: r["clause_no"] for r in CASES["C"]["questions"]}
    # 유형은 시연 C 기대와 같다. 문구는 엔진이 일반 표준 문장으로 만들어 기대 문구(고객에게 보낼 초안)와 다르다.
    assert sorted(t for t, _, _ in rows) == sorted(types)
    assert {status for _, status, _ in rows} == {"DRAFT"}
    by_type = {t: clause for t, _, clause in rows}
    assert by_type["EMPTY_VALUE"] == "2.4.E.1.c"
    assert by_type["CONDITION_MISSING"] == "2.1.A"
    assert by_type["CONFLICT"] in {"1.1.A.1.a", "2.4.A.1"}


def test_draft_review_version_and_docx_export_follow_the_process(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles, monkeypatch
):
    """자사 대응(OUR_RESPONSE)과 협력사 보완(PARTNER_SUPPLEMENT) 두 경로가 이어진 초안을 검토본으로 저장하고 DOCX로 내보낸다."""
    import asyncio
    import io
    import zipfile

    from psycopg.rows import dict_row

    from backend.agent.processor_registry import TftJob
    from backend.api.tft.rfp import draft_exports
    from backend.api.tft.rfp.storage import LocalDocumentStore

    monkeypatch.setenv("TFT_DOCUMENT_ROOT", str(tmp_path))
    monkeypatch.setenv("TFT_DOCUMENT_BUCKET", "")
    project = processed(worker_dsn, admin, tmp_path)
    base = f"/api/v1/tft/projects/{project['project']}/draft"
    with client_for(app_dsn) as client:
        draft = client.get(base, headers=sales_headers())
        assert draft.status_code == 200, draft.text
        sections = {sec["section"]: sec["sentences"] for sec in draft.json()["data"]["sections"]}
        assert sections["OUR_RESPONSE"] and sections["PARTNER_SUPPLEMENT"]
        assert draft.json()["data"]["evidence_missing_count"] == 0
        saved = client.post(
            f"{base}/review-versions", json={"version": 1}, headers=sales_headers("a09-review")
        )
        assert saved.status_code == 201, saved.text
        assert saved.json()["data"]["output_status"] == "REVIEW_DRAFT"
        # 미해결 필수 조건이 있어 판단 상태는 "조건 확인 필요"로 남는다(기획서 4.1).
        assert saved.json()["data"]["judgement_status"] == "CONDITION_CHECK"

        class Jobs:
            def __init__(self):
                self.calls = []
                self.id = uuid4()

            async def post(self, path, body):
                self.calls.append(body)
                return {
                    "job_id": str(self.id),
                    "status": "QUEUED",
                    "status_url": f"/api/v1/platform/jobs/{self.id}",
                }

        jobs = Jobs()
        client.app.state.processing_jobs = jobs
        payload = {"review_version": 1, "format": "DOCX"}
        received = client.post(f"{base}/exports", json=payload, headers=sales_headers("a09-export"))
        assert received.status_code == 202, received.text
        export_id = received.json()["data"]["export_id"]
        job = TftJob(
            job_id=jobs.id, kind="rfp.export", project_id=project["project"],
            input_version=jobs.calls[0]["input_version"], actor_id=SALES,
        )  # fmt: skip

        async def execute():
            async with await psycopg.AsyncConnection.connect(
                worker_dsn, row_factory=dict_row
            ) as conn:
                return await draft_exports.process(
                    conn, job, lambda event: None, LocalDocumentStore(tmp_path)
                )

        asyncio.run(execute())
        file = client.get(f"{base}/exports/{export_id}/file", headers=sales_headers())
        assert file.status_code == 200, file.text
        assert zipfile.is_zipfile(io.BytesIO(file.content))
        with zipfile.ZipFile(io.BytesIO(file.content)) as package:
            assert "검토용 초안입니다" in package.read("word/document.xml").decode()


def latest(admin, requirement_id):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT version, status, provenance_kind FROM tft.requirement_versions "
            "WHERE requirement_id=%s ORDER BY version DESC LIMIT 1",
            (requirement_id,),
        ).fetchone()


def test_a_rerun_keeps_a_status_the_owner_approved(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    """사람이 제안·승인으로 바꾼 상태(human_input 새 판본)는 같은 입력을 다시 처리해도 되돌아가지 않고, 요구가 늘지 않는다."""
    project = processed(worker_dsn, admin, tmp_path)
    quote = DEMO_A["알루미늄 권선"]["quote"]
    with psycopg.connect(admin) as conn:
        requirement = conn.execute(
            "SELECT r.id FROM tft.requirements r JOIN tft.requirement_versions v ON v.requirement_id=r.id "
            "WHERE r.project_id=%s AND v.quote=%s",
            (project["project"], quote),
        ).fetchone()[0]
        before = conn.execute(
            "SELECT count(*) FROM tft.requirements WHERE project_id=%s", (project["project"],)
        ).fetchone()[0]
    assert latest(admin, requirement)[1] == "INSUFFICIENT_EVIDENCE"
    with client_for(app_dsn) as client:
        made = client.post(
            f"/api/v1/tft/projects/{project['project']}/proposals",
            json={
                "requirement_id": str(requirement),
                "proposed": {"status": "CONDITIONAL", "reason": "데이터시트 보완 요청 회신 확인"},
                "rationale": "고객 회신으로 알루미늄 권선 사양 확인",
            },
            headers=sales_headers(uuid4().hex),
        )
        assert made.status_code == 201, made.text
        proposal = made.json()["data"]
        approved = client.post(
            f"/api/v1/tft/proposals/{proposal['proposal_id']}/approve",
            json={"version": proposal["version"]},
            headers=sales_headers(uuid4().hex),
        )
        assert approved.status_code == 200, approved.text
    version, status, provenance = latest(admin, requirement)
    assert (status, provenance) == ("CONDITIONAL", "human_input") and version == 2
    # 같은 입력을 새 작업으로 다시 처리한다.
    run(worker_dsn, project, DemoExtractor(list(ALL.values())))
    assert latest(admin, requirement) == (2, "CONDITIONAL", "human_input")
    with psycopg.connect(admin) as conn:
        after = conn.execute(
            "SELECT count(*) FROM tft.requirements WHERE project_id=%s", (project["project"],)
        ).fetchone()[0]
    assert after == before


DEMO_14_DOCX = os.getenv("RFP_DEMO_14_PATH", "")


@pytest.mark.skipif(
    not (DEMO_14_DOCX and Path(DEMO_14_DOCX).is_file()),
    reason="RFP_DEMO_14_PATH가 가리키는 14번 DOCX(저장소에 없음)가 있을 때만 로컬에서 돈다",
)
def test_real_14_docx_keeps_the_planned_states(worker_dsn, admin, tmp_path, demo_profiles):
    """로컬 전용: 14번 원문 DOCX로 같은 흐름을 돌린다. 원문에 인용이 있는 요구만 추출되고 그 상태가 기대와 같다."""
    docx = Path(DEMO_14_DOCX).read_bytes()
    project = make_project(admin, tmp_path, docx)
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.document_versions SET mime=%s, storage_key=%s, edit_trace_support=true WHERE id=%s",
            (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                f"rfp/{project['docver']}.docx",
                project["docver"],
            ),
        )
    (tmp_path / "rfp" / f"{project['docver']}.docx").write_bytes(docx)
    run(worker_dsn, project, DemoExtractor(docx_items()))
    by_quote = stored_status(admin, project)
    wanted = [key for key in expected_states() if key not in NOT_EXTRACTABLE]
    found = [key for key in wanted if GROUPS[key[0]][key[1]]["quote"] in by_quote]
    # 원문에 그 인용이 없으면 가짜 추출기가 요구를 돌려주지 않는다. 14번 원문에는 "LSIG"라는 낱말이 없어(장단한·순시·지락 기능이
    # 따로 적힘) 그 값을 인용하는 B-2 보호 기능 요구가 빠진다. 값을 "LSIG"로 모으는 것은 모델의 몫이라 이 시험은 확인하지 못한다.
    assert [key for key in wanted if key not in found] == [("B-2", "인출형, LSIG 보호")]
    for key in found:
        want = KNOWN_DEVIATIONS.get(key, expected_states()[key])
        assert by_quote[GROUPS[key[0]][key[1]]["quote"]] == {want}, key
    # 취소선으로 지운 값은 현행 문장에 섞이지 않는다(2.5 C.1 "50" → "33%", 시연 C).
    with psycopg.connect(admin) as conn:
        text = conn.execute(
            "SELECT c.text FROM tft.clauses c WHERE c.document_version_id=%s AND c.clause_no='2.5.C.1'",
            (project["docver"],),
        ).fetchone()[0]
    assert "33%" in text and "5033" not in text
    # 질문 초안 세 가지: 원문의 "<Insert value>" 자리표시자, 1.1 metal-clad ↔ 2.4 metal-enclosed, 제조사 승인 절차 없음.
    with psycopg.connect(admin) as conn:
        kinds = {
            r[0]
            for r in conn.execute(
                "SELECT issue_type FROM tft.customer_questions WHERE project_id=%s",
                (project["project"],),
            ).fetchall()
        }
    assert kinds == {"EMPTY_VALUE", "CONFLICT", "CONDITION_MISSING"}
