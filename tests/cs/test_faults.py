"""고장 목록 조회(기획서 ⑧ 해결 기록의 고장 ID 선택). 제공 ref/fault_catalog.csv를 그대로 읽는다(DB 없음)."""

import csv
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.engine.cs import rules
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF")
SALES = Principal(user_id=uuid4(), roles={"sales"})


@pytest.fixture
def client():
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as test_client:
        yield test_client


def raw_rows() -> list[dict[str, str]]:
    path = rules.POLICY_DIR / "fault_catalog.csv"
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))


def test_catalog_is_read_unmodified():
    rows = raw_rows()
    faults = rules.fault_catalog()
    assert len(faults) == len(rows) == 97
    assert [f.fault_id for f in faults] == [r["fault_id"] for r in rows]
    first = faults[0]
    assert first.applies_to == tuple(rows[0]["applies_to"].split(";"))
    assert first.needs_onsite is (rows[0]["needs_onsite"] == "Y")


def test_edited_catalog_stops_the_service(monkeypatch, tmp_path):
    copy = tmp_path / "fault_catalog.csv"
    copy.write_text((rules.POLICY_DIR / "fault_catalog.csv").read_text(encoding="utf-8") + "\n")
    monkeypatch.setattr(rules, "POLICY_DIR", tmp_path)
    rules.fault_catalog.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="fault_catalog.csv changed"):
            rules.fault_catalog()
    finally:
        rules.fault_catalog.cache_clear()


def test_product_filter_returns_only_faults_that_apply(client):
    expected = [r["fault_id"] for r in raw_rows() if "PLC-XGI-CPUU" in r["applies_to"].split(";")]
    body = client.get(
        "/api/v1/cs/faults?product_code=PLC-XGI-CPUU", headers=principal_header(AGENT)
    ).json()
    assert [item["fault_id"] for item in body["data"]["items"]] == expected
    assert expected and body["data"]["source"] == "ref/fault_catalog.csv"
    assert all("PLC-XGI-CPUU" in item["applies_to"] for item in body["data"]["items"])


def test_all_faults_without_filter_and_empty_for_unknown_product(client):
    everything = client.get("/api/v1/cs/faults", headers=principal_header(STAFF)).json()
    assert len(everything["data"]["items"]) == 97
    unknown = client.get("/api/v1/cs/faults?product_code=NO-SUCH", headers=principal_header(AGENT))
    assert unknown.status_code == 200 and unknown.json()["data"]["items"] == []


def test_roles_without_c_s_scope_are_refused(client):
    assert client.get("/api/v1/cs/faults", headers=principal_header(SALES)).status_code == 403
    assert client.get("/api/v1/cs/faults").status_code == 401
