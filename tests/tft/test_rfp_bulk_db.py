"""KAN-228 일괄 결정·묶음 되돌리기·질의서 발송 기록·목록의 남은 일(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

from uuid import uuid4

import psycopg

from backend.shared.access import Principal
from tests.tft.test_rfp_http_base_db import RESPONSIBLE, admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OUTSIDER, OWNER, call, client  # noqa: F401


def second(admin, chain):
    """같은 프로젝트·조항에 정격 부족 요구 하나를 더 둔다."""
    requirement = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,'R-2','synthetic','DERIVED',now())",
            (requirement, chain["project"]),
        )
        conn.execute(
            "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
            "clause_id, quote, operator, standard_system, necessity, status, reason, "
            "provenance_kind, data_origin, derivation_kind, known_at) VALUES (%s,1,%s,%s,"
            "'BIL 95 kV','GTE','IEEE','MANDATORY','SPEC_GAP','자사 정격 부족','ai_draft',"
            "'synthetic','DERIVED',now())",
            (requirement, chain["docver"], chain["clause"]),
        )
    return requirement


def state(admin, *requirements):
    with psycopg.connect(admin) as conn:
        versions = [
            conn.execute(
                "SELECT v.version, v.status FROM tft.requirements r JOIN tft.requirement_versions v "
                "ON v.requirement_id=r.id AND v.version=r.latest_version WHERE r.id=%s",
                (r,),
            ).fetchone()
            for r in requirements
        ]
        decisions = conn.execute(
            "SELECT count(*) FROM tft.requirement_decisions WHERE requirement_id = ANY(%s)",
            (list(requirements),),
        ).fetchone()[0]
    return versions, decisions


RESPONSIBLE_P = Principal(user_id=RESPONSIBLE, roles=frozenset({"sales"}))


def bulk(client, chain, items, key=None, who=OWNER):
    return call(
        client,
        "POST",
        f"/projects/{chain['project']}/decisions/bulk",
        who,
        {"items": items},
        key or uuid4().hex,
    )


def item(requirement, version=1, action="confirm_met", **extra):
    return {"requirement_id": str(requirement), "version": version, "action": action, **extra}


def test_bulk_confirm_is_one_transaction_and_replays(client, admin, chain):
    other = second(admin, chain)
    key = uuid4().hex
    first = bulk(client, chain, [item(chain["requirement"]), item(other)], key)
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert [i["summary"] for i in data["items"]] == ["충족 확정", "충족 확정"]
    assert state(admin, chain["requirement"], other) == (
        [(2, "EVIDENCE_FOUND"), (2, "EVIDENCE_FOUND")],
        2,
    )
    again = bulk(client, chain, [item(chain["requirement"]), item(other)], key).json()["data"]
    assert again == data
    with psycopg.connect(admin) as conn:
        batches = conn.execute(
            "SELECT DISTINCT detail->>'batch_id' FROM tft.requirement_decisions "
            "WHERE project_id=%s",
            (chain["project"],),
        ).fetchall()
    assert batches == [(data["batch_id"],)]


def test_one_failure_rolls_back_every_item(client, admin, chain):
    other = second(admin, chain)
    stale = bulk(client, chain, [item(chain["requirement"]), item(other, version=9)])
    assert stale.status_code == 409, stale.text
    error = stale.json()["error"]
    assert error["details"][0] == {
        "index": "1",
        "requirement_id": str(other),
        "reason": error["code"],
    }
    assert state(admin, chain["requirement"], other) == ([(1, "SPEC_GAP"), (1, "SPEC_GAP")], 0)


def test_bulk_follows_single_decision_permissions_and_actions(client, admin, chain):
    other = second(admin, chain)
    responsible = Principal(user_id=RESPONSIBLE, roles=frozenset({"sales"}))
    denied = bulk(client, chain, [item(chain["requirement"]), item(other)], who=responsible)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "TFT_DECISION_SALES_ONLY"
    hidden = bulk(client, chain, [item(chain["requirement"])], who=OUTSIDER)
    assert hidden.status_code == 404
    undo = bulk(client, chain, [item(other, action="undo", decision_id=str(uuid4()))])
    assert undo.status_code == 422
    assert state(admin, chain["requirement"], other) == ([(1, "SPEC_GAP"), (1, "SPEC_GAP")], 0)


def test_responsible_confirms_all_in_one_call_and_it_rolls_back_together(client, admin, chain):
    # 2026-11-05 흐름 점검: "모두 확인"이 요구마다 한 번씩 불러 65건에 4초가 걸렸다.
    other = second(admin, chain)
    decided = bulk(client, chain, [item(chain["requirement"]), item(other)]).json()["data"]
    responsible = Principal(user_id=RESPONSIBLE, roles=frozenset({"sales"}))
    reviews = [
        item(
            d["requirement_id"],
            d["requirement_version"],
            "review_confirm",
            decision_id=d["decision_id"],
        )
        for d in decided["items"]
    ]
    owner = bulk(client, chain, reviews)
    assert owner.status_code == 403
    assert owner.json()["error"]["code"] == "TFT_DECISION_RESPONSIBLE_ONLY"
    broken = [*reviews[:1], {**reviews[1], "decision_id": str(uuid4())}]
    failed = bulk(client, chain, broken, who=responsible)
    assert failed.status_code == 404 and failed.json()["error"]["details"][0]["index"] == "1"
    with psycopg.connect(admin) as conn:
        count = "SELECT count(*) FROM tft.requirement_decisions WHERE project_id=%s AND action=%s"
        assert conn.execute(count, (chain["project"], "review_confirm")).fetchone()[0] == 0
    done = bulk(client, chain, reviews, who=responsible)
    assert done.status_code == 200, done.text
    assert [i["action"] for i in done.json()["data"]["items"]] == ["review_confirm"] * 2
    with psycopg.connect(admin) as conn:
        assert conn.execute(count, (chain["project"], "review_confirm")).fetchone()[0] == 2
    # 확인 묶음은 되돌릴 판단이 없다.
    url = f"/projects/{chain['project']}/decisions/bulk/{done.json()['data']['batch_id']}/undo"
    assert call(client, "POST", url, RESPONSIBLE_P, None, uuid4().hex).status_code == 404


def test_batch_undo_reverts_every_decision_once(client, admin, chain):
    other = second(admin, chain)
    batch = bulk(client, chain, [item(chain["requirement"]), item(other)]).json()["data"]
    url = f"/projects/{chain['project']}/decisions/bulk/{batch['batch_id']}/undo"
    key = uuid4().hex
    undone = call(client, "POST", url, OWNER, None, key)
    assert undone.status_code == 200, undone.text
    assert [i["action"] for i in undone.json()["data"]["items"]] == ["undo", "undo"]
    assert state(admin, chain["requirement"], other) == ([(3, "SPEC_GAP"), (3, "SPEC_GAP")], 4)
    # 다시 보내면 같은 되돌림을 돌려주고 더 쓰지 않는다.
    again = call(client, "POST", url, OWNER, None, uuid4().hex)
    assert {i["decision_id"] for i in again.json()["data"]["items"]} == {
        i["decision_id"] for i in undone.json()["data"]["items"]
    }
    assert state(admin, chain["requirement"], other)[1] == 4
    missing = call(
        client, "POST", f"/projects/{chain['project']}/decisions/bulk/{uuid4()}/undo", OWNER, None,
        uuid4().hex,
    )  # fmt: skip
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TFT_BATCH_NOT_FOUND"


def question(admin, chain, target="CUSTOMER"):
    question_id = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.customer_questions (id, project_id, requirement_id, issue_type, "
            "question_text, target, target_dept, data_origin, derivation_kind, known_at, "
            "created_by) VALUES (%s,%s,%s,%s,'값을 알려 주십시오',%s,%s,'synthetic','PROVIDED',"
            "now(),%s)",
            (
                question_id,
                chain["project"],
                chain["requirement"],
                "EMPTY_VALUE" if target == "CUSTOMER" else "RND_FEASIBILITY",
                target,
                None if target == "CUSTOMER" else "RND_FW",
                uuid4(),
            ),
        )
    return question_id


def test_sent_questions_keep_the_first_send_and_show_waiting_days(client, admin, chain):
    asked = question(admin, chain)
    url = f"/projects/{chain['project']}/questions/sent"
    first = call(client, "POST", url, OWNER, {"question_ids": [str(asked)], "mail_ref": "m-1"})
    assert first.status_code == 200, first.text
    sent = first.json()["data"]["items"][0]
    assert (sent["question_id"], sent["mail_ref"]) == (str(asked), "m-1")
    again = call(client, "POST", url, OWNER, {"question_ids": [str(asked)], "mail_ref": "m-2"})
    assert again.json()["data"]["items"][0] == sent
    listed = call(client, "GET", f"/projects/{chain['project']}/questions", OWNER)
    row = next(q for q in listed.json()["data"]["items"] if q["question_id"] == str(asked))
    assert (row["sent_at"], row["waiting_days"]) == (sent["sent_at"], 0)
    internal = question(admin, chain, target="INTERNAL")
    assert call(client, "POST", url, OWNER, {"question_ids": [str(internal)]}).status_code == 422
    responsible = Principal(user_id=RESPONSIBLE, roles=frozenset({"sales"}))
    assert call(client, "POST", url, responsible, {"question_ids": [str(asked)]}).status_code == 403
    assert call(client, "POST", url, OUTSIDER, {"question_ids": [str(asked)]}).status_code == 404


def test_project_list_shows_due_date_and_remaining_work(client, admin, chain):
    patched = call(
        client,
        "PATCH",
        f"/projects/{chain['project']}",
        OWNER,
        {"version": 1, "submission_due_at": "2026-11-20"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["submission_due_at"] == "2026-11-20"

    def row():
        listed = call(client, "GET", "/projects?limit=100", OWNER)
        assert listed.status_code == 200, listed.text
        return next(p for p in listed.json()["data"]["items"] if p["id"] == str(chain["project"]))

    before = row()
    assert (before["submission_due_at"], before["decisions_remaining"]) == ("2026-11-20", 1)
    assert before["review_pending"] == 0
    decided = bulk(client, chain, [item(chain["requirement"])])
    assert decided.status_code == 200, decided.text
    after = row()
    assert (after["decisions_remaining"], after["review_pending"]) == (0, 1)
