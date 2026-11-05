"""시험용: 검토본 저장 전에 남은 결정을 담당 sales가 하고 책임자가 확인한다(KAN-220 저장 조건).

규칙을 우회하지 않고 결정 경로(POST /requirements/{id}/decision)를 그대로 부른다.
판본을 바꾸지 않는 편차표 기재로 모두 결정한다(초안 문장 재검토 표시가 생기지 않게).
"""

from uuid import UUID, uuid4

import psycopg

from backend.shared.access import Principal, principal_header


def _call(client, who, method, path, body=None):
    headers = principal_header(who)
    if body is not None:
        headers["Idempotency-Key"] = uuid4().hex
    response = client.request(method, "/api/v1/tft" + path, json=body, headers=headers)
    assert response.status_code < 300, response.text
    return response.json()["data"]


def _requirements(client, who, project_id):
    items, cursor = [], None
    while True:
        query = f"?limit=100&cursor={cursor}" if cursor else "?limit=100"
        page = _call(client, who, "GET", f"/projects/{project_id}/requirements{query}")
        items += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            return items


def settle_decisions(client, admin, project_id: UUID, text="시험 결정: 편차표 기재") -> int:
    """결정이 필요한 요구를 모두 결정하고 책임자가 확인한다. 고객 질의는 건드리지 않는다.

    Returns:
        int: 이번에 편차표에 기재한 요구 수.
    """
    with psycopg.connect(admin) as conn:
        row = conn.execute(
            "SELECT sales_user_id, responsible_user_id FROM tft.projects WHERE id=%s",
            (project_id,),
        ).fetchone()
    assert row is not None
    sales = Principal(user_id=row[0], roles=frozenset({"sales"}))
    responsible = Principal(user_id=row[1], roles=frozenset({"manager"}))
    recorded = 0
    for req in _requirements(client, sales, project_id):
        state = req["decision"] or {}
        if state.get("needed") and not state.get("decided"):
            recorded += 1
            body = {"version": req["version"], "action": "record_deviation", "text": text}
            _call(client, sales, "POST", f"/requirements/{req['requirement_id']}/decision", body)
    for req in _requirements(client, sales, project_id):
        state = req["decision"] or {}
        if state.get("decided") and not state.get("reviewed"):
            body = {
                "version": req["version"],
                "action": "review_confirm",
                "decision_id": state["decision_id"],
            }
            _call(
                client, responsible, "POST", f"/requirements/{req['requirement_id']}/decision", body
            )
    return recorded
