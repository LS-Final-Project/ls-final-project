"""KAN-225 원문에서 요구 직접 추가와 요구에서 빼기·되살리기(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

from unittest.mock import Mock
from uuid import uuid4

import psycopg

from tests.tft.test_rfp_decisions_db import CHIEF, decide, ok, project_gate
from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OUTSIDER, OWNER, call, client  # noqa: F401
from tests.tft.test_worker_processor_db import (  # noqa: F401
    SPEC,
    FakeExtractor,
    make_project,
    run,
    worker_dsn,
)


def add(client, project, who=OWNER, key=None, **body):
    return call(client, "POST", f"/projects/{project}/requirements", who, body, key or uuid4().hex)


def bil(chain, **extra):
    return {
        "clause_id": str(chain["clause"]),
        "quote": "BIL  95 kV",
        "kind": "SPEC",
        "equipment": "변압기",
        "attribute": "BIL",
        "value": "95",
        "unit": "kV",
        "operator": "GTE",
        **extra,
    }


def listed(client, project, **query):
    path = f"/projects/{project}/requirements"
    if query:
        path += "?" + "&".join(f"{k}={str(v).lower()}" for k, v in query.items())
    got = call(client, "GET", path, OWNER)
    assert got.status_code == 200, got.text
    return {item["requirement_id"]: item for item in got.json()["data"]["items"]}


def test_added_text_must_be_in_the_clause_and_the_value_in_the_text(client, chain):
    """추출과 같은 근거 검사. 공백은 정규화하고, 값은 고른 글 안에 있어야 한다."""
    project = chain["project"]
    missing = add(client, project, **bil(chain, quote="BIL 110 kV"))
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "TFT_QUOTE_NOT_IN_CLAUSE"
    wrong = add(client, project, **bil(chain, value="110"))
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "TFT_VALUE_NOT_IN_QUOTE"
    other = add(client, project, **bil(chain, clause_id=str(uuid4())))
    assert other.status_code == 404


def test_added_requirement_is_human_input_judged_by_rule_and_needs_review(client, chain, admin):
    key = uuid4().hex
    made = ok(add(client, chain["project"], key=key, **bil(chain)))
    assert made["action"] == "add_requirement" and made["requirement_version"] == 1
    # 같은 키 재전송은 같은 결과, 다른 본문은 409다.
    assert ok(add(client, chain["project"], key=key, **bil(chain))) == made
    assert add(client, chain["project"], key=key, **bil(chain, unit="V")).status_code == 409
    requirement = made["requirement_id"]
    with psycopg.connect(admin) as conn:
        row = conn.execute(
            "SELECT r.derivation_kind, v.provenance_kind, v.decided_by IS NOT NULL, v.quote, v.clause_id, "
            "v.status FROM tft.requirements r JOIN tft.requirement_versions v ON v.requirement_id=r.id "
            "WHERE r.id=%s",
            (requirement,),
        ).fetchone()
        audits = conn.execute(
            "SELECT action FROM tft.rfp_audit_events WHERE entity_id=%s", (requirement,)
        ).fetchall()
    assert row[:5] == ("PROVIDED", "human_input", True, "BIL  95 kV", chain["clause"])
    # 계열을 고르지 않았으므로 자사 비교를 하지 않는다(모델 호출 없이 근거 부족).
    assert row[5] == "INSUFFICIENT_EVIDENCE"
    assert audits == [("decision.add_requirement",)]
    item = listed(client, chain["project"])[requirement]
    assert item["provenance_kind"] == "human_input"
    assert item["decision"]["manually_added"] is True
    assert item["decision"]["change_action"] == "add_requirement"
    assert (
        listed(client, chain["project"])[str(chain["requirement"])]["decision"]["manually_added"]
        is False
    )
    # 추가는 책임자 확인 대상이다.
    gate = project_gate(client, chain)
    assert gate["review_pending"] == 1
    target = {"requirement": requirement, "project": chain["project"]}
    refused = decide(client, target, "review_confirm", decision_id=made["decision_id"])
    assert refused.status_code == 403
    ok(decide(client, target, "review_confirm", who=CHIEF, decision_id=made["decision_id"]))
    assert project_gate(client, chain)["review_pending"] == 0
    detail = call(client, "GET", f"/requirements/{requirement}", OWNER).json()["data"]
    assert detail["decision"]["change_reviewed"] is True


def test_same_equipment_attribute_and_value_warns_before_adding(client, chain, admin):
    project = chain["project"]
    first = ok(add(client, project, **bil(chain)))
    again = add(client, project, **bil(chain, quote="BIL 95 kV", equipment=" 변압기 "))
    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "TFT_REQUIREMENT_DUPLICATE"
    assert [d["reason"] for d in error["details"]] == [first["requirement_id"]]
    second = ok(add(client, project, allow_duplicate=True, **bil(chain)))
    assert second["requirement_id"] != first["requirement_id"]
    with psycopg.connect(admin) as conn:
        codes = conn.execute(
            "SELECT code FROM tft.requirements WHERE id = ANY(%s) ORDER BY code",
            ([first["requirement_id"], second["requirement_id"]],),
        ).fetchall()
    assert [c[0].rsplit(":", 1)[1] for c in codes] == ["m1", "m2"]


def test_only_the_sales_owner_adds_and_excludes(client, chain):
    for who, status in ((CHIEF, 403), (OUTSIDER, 404)):
        assert add(client, chain["project"], who=who, **bil(chain)).status_code == status
        refused = decide(client, chain, "exclude_requirement", who=who, text="중복 조항")
        assert refused.status_code == status


def test_excluded_requirement_moves_to_its_own_list_and_restores(client, chain, admin):
    requirement = str(chain["requirement"])
    assert decide(client, chain, "exclude_requirement").status_code == 422
    before = project_gate(client, chain)
    assert before["undecided"] == 1
    made = ok(decide(client, chain, "exclude_requirement", text="일반 조항, 견적 대상 아님"))
    assert made["requirement_version"] == 1
    assert requirement not in listed(client, chain["project"])
    gone = listed(client, chain["project"], excluded=True)[requirement]
    assert gone["decision"]["excluded"] is True
    assert gone["decision"]["excluded_reason"] == "일반 조항, 견적 대상 아님"
    # 뺀 요구는 판정·결정 대상이 아니다. 빼기 자체는 책임자 확인 대상이다.
    gate = project_gate(client, chain)
    assert (gate["undecided"], gate["deviation_missing"], gate["review_pending"]) == (0, 0, 1)
    blocked = decide(client, chain, "record_deviation", text="기준온도 120 °C 값만 보유")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "TFT_REQUIREMENT_EXCLUDED"
    assert decide(client, chain, "exclude_requirement", text="다시").status_code == 409
    # 책임자가 되돌려 보내면 되살아난다.
    back = ok(decide(client, chain, "review_return", who=CHIEF, decision_id=made["decision_id"]))
    assert back["summary"] == "책임자가 되돌림: 요구에서 뺌"
    assert requirement in listed(client, chain["project"])
    assert project_gate(client, chain)["undecided"] == 1
    # 되살리기는 뺀 요구에만 한다. 빼고 되살리면 다시 목록에 있다.
    refused = decide(client, chain, "restore_requirement")
    assert refused.json()["error"]["code"] == "TFT_REQUIREMENT_NOT_EXCLUDED"
    ok(decide(client, chain, "exclude_requirement", text="중복"))
    restored = ok(decide(client, chain, "restore_requirement"))
    assert restored["summary"] == "되살림"
    item = listed(client, chain["project"])[requirement]
    assert (item["decision"]["excluded"], item["decision"]["change_action"]) == (
        False,
        "restore_requirement",
    )
    with psycopg.connect(admin) as conn:
        # 요구 행은 지우지 않았다.
        assert conn.execute(
            "SELECT count(*) FROM tft.requirement_versions WHERE requirement_id=%s",
            (chain["requirement"],),
        ).fetchone() == (1,)


def test_returning_an_added_requirement_excludes_it(client, chain):
    made = ok(add(client, chain["project"], **bil(chain)))
    target = {"requirement": made["requirement_id"], "project": chain["project"]}
    ok(decide(client, target, "review_return", who=CHIEF, decision_id=made["decision_id"]))
    gone = listed(client, chain["project"], excluded=True)
    assert gone[made["requirement_id"]]["decision"]["excluded"] is True


def test_reprocessing_keeps_added_and_excluded_requirements(client, worker_dsn, admin, tmp_path):
    """처리를 다시 돌려도 사람이 추가한 요구와 뺀 요구가 그대로이고, 뺀 요구는 초안에 들어가지 않는다."""
    project = make_project(admin, tmp_path, SPEC)
    run(worker_dsn, project, FakeExtractor())
    with psycopg.connect(admin) as conn:
        clause = conn.execute(
            "SELECT id FROM tft.clauses WHERE document_version_id=%s AND clause_no='2.1.A'",
            (project["docver"],),
        ).fetchone()[0]
        impedance = conn.execute(
            "SELECT r.id FROM tft.requirements r JOIN tft.requirement_versions v "
            "ON v.requirement_id=r.id WHERE r.project_id=%s AND v.attribute='impedance'",
            (project["project"],),
        ).fetchone()[0]
    body = {
        "clause_id": str(clause),
        "quote": "The transformer rating shall be 1500 kVA minimum.",
        "kind": "SPEC",
        "equipment": "dry-type transformer",
        "attribute": "rating",
        "value": "1500",
        "unit": "kVA",
        "operator": "GTE",
        "attribute_key": "rated_power",
        "equipment_family": "TRANSFORMER",
    }
    # 추출 요구와 설비·속성·값이 같다.
    assert add(client, project["project"], **body).status_code == 409
    made = ok(add(client, project["project"], allow_duplicate=True, **body))
    ok(
        decide(
            client,
            {"requirement": impedance, "project": project["project"]},
            "exclude_requirement",
            text="지정품 조항은 별도 검토",
        )
    )

    def snapshot():
        with psycopg.connect(admin) as conn:
            return conn.execute(
                "SELECT r.id, r.latest_version, v.provenance_kind, v.status FROM tft.requirements r "
                "JOIN tft.requirement_versions v ON v.requirement_id=r.id "
                "AND v.version=r.latest_version WHERE r.project_id=%s ORDER BY r.id",
                (project["project"],),
            ).fetchall()

    before = snapshot()
    with psycopg.connect(admin) as conn:
        compared = conn.execute(
            "SELECT count(*) FROM tft.product_comparisons WHERE requirement_id=%s",
            (made["requirement_id"],),
        ).fetchone()[0]
    # 규칙 비교로 자사 후보를 바로 비교했다.
    assert compared > 0
    no_model = Mock(side_effect=AssertionError("재처리 모델 호출 금지"))
    run(worker_dsn, project, no_model)
    no_model.assert_not_called()
    assert snapshot() == before
    excluded = listed(client, project["project"], excluded=True)
    assert list(excluded) == [str(impedance)]
    assert made["requirement_id"] in listed(client, project["project"])
    with psycopg.connect(admin) as conn:
        cited = {
            row[0]
            for row in conn.execute(
                "SELECT e.evidence_id FROM tft.draft_sentence_evidence e JOIN tft.draft_sentences s "
                "ON s.id=e.sentence_id JOIN tft.drafts d ON d.project_id=s.project_id "
                "AND d.latest_version=s.draft_version WHERE s.project_id=%s "
                "AND e.evidence_type='requirement'",
                (project["project"],),
            ).fetchall()
        }
    assert str(impedance) not in cited
