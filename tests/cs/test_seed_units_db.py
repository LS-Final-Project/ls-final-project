"""KAN-205 고객 이력 가상 장비 적재를 실제 PostgreSQL에서 검사한다. 저장소의 장비·문의 CSV를 그대로 쓴다.

문의 본문은 저장소 밖 개발 분할에서 읽으므로 여기서는 같은 열을 가진 작은 원본을 만들어 쓴다.
"""

import csv
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings
from scripts import cs_seed_units as seed

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
UNITS = Path("eval/customer_history/units_demo.csv")
TICKETS = Path("eval/customer_history/tickets.csv")
# 접수 시각은 시나리오 시트의 값이다. 본문은 합성 한 줄이다(개발 분할 원문은 저장소에 두지 않는다).
RECEIVED = {
    "CSV1-000808": "2025-12-18T15:02:22Z",
    "CSV1-003124": "2026-08-25T12:38:48Z",
    "CSV1-000991": "2026-01-08T12:31:44Z",
    "CSV1-003317": "2026-09-11T14:22:24Z",
    "CSV1-000670": "2025-12-04T13:09:26Z",
    "CSV1-000791": "2025-12-17T07:30:00Z",
}
CUSTOMER = {
    "CSV1-000808": "CUST-0164",
    "CSV1-003124": "CUST-0164",
    "CSV1-000991": "CUST-0130",
    "CSV1-003317": "CUST-0130",
    "CSV1-000670": "CUST-0383",
    "CSV1-000791": "CUST-0383",
}
PRODUCT = {
    "CUST-0164": "ELCB-EBN-203c",
    "CUST-0130": "METER-LP3500-3P4W",
    "CUST-0383": "INV-LSLV0075-S100",
}


@pytest.fixture(scope="module", autouse=True)
def clean_seed(cs_database):
    """같은 검사 DB를 다시 쓸 때 지난 실행의 시연 장비·문의가 남아 있으면 지운다(적재는 다시 돌려도 같아야 한다)."""
    with psycopg.connect(cs_database, autocommit=True) as admin:
        admin.execute(
            "DELETE FROM cs.ticket_unit_links WHERE unit_id IN "
            "(SELECT id FROM cs.installed_units WHERE source = %s)",
            (seed.SOURCE,),
        )
        admin.execute("DELETE FROM cs.tickets WHERE source = %s", (seed.SOURCE,))
        admin.execute(
            "UPDATE cs.installed_units SET replaced_by_unit_id = NULL WHERE source = %s",
            (seed.SOURCE,),
        )
        admin.execute("DELETE FROM cs.installed_units WHERE source = %s", (seed.SOURCE,))


@pytest.fixture(scope="module")
def client(cs_database):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    """시나리오 문의 가운데 6건만 든 원본. 나머지 16건은 '원본에 없음'으로 건너뛰는 경로를 함께 검사한다."""
    path = tmp_path_factory.mktemp("seed") / "tickets.dev.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*seed.TICKET_INPUT, "category_code"])
        writer.writeheader()
        for ticket, received in RECEIVED.items():
            customer = CUSTOMER[ticket]
            writer.writerow(
                {
                    "ticket_id": ticket,
                    "received_at_utc": received,
                    "channel": "EMAIL",
                    "body_language": "en",
                    "customer_id": customer,
                    "customer_tier": "GOLD",
                    "product_code": PRODUCT[customer],
                    "inquiry_subject": f"Synthetic {ticket}",
                    "inquiry_body": "Synthetic body for the customer history seed test.",
                    "fw_version_reported": "",
                    "error_code_reported": "",
                    # 정답 열은 읽지 않아야 한다.
                    "category_code": "GOLD_SECRET",
                }
            )
    return path


def load(admin, source: Path, confirm_by: UUID | None) -> seed.Plan:
    units = seed.read_units(UNITS)
    refs = seed.read_ticket_refs(TICKETS, units)
    plan = seed.Plan(units=units, tickets=refs)
    seed.apply(
        admin,
        plan,
        data_origin="demo",
        source_instance="test-seed",
        ticket_inputs=seed.read_ticket_inputs(source, {r.ticket_id for r in refs}),
        confirm_by=confirm_by,
    )
    return plan


def ticket_id(admin, external: str) -> UUID:
    row = admin.execute(
        "SELECT id FROM cs.tickets WHERE source = %s AND source_instance = 'test-seed' "
        "AND external_id = %s",
        (seed.SOURCE, external),
    ).fetchone()
    assert row is not None, external
    return row[0]


def test_the_repository_csvs_load_and_reloading_changes_nothing(cs_database, source):
    with psycopg.connect(cs_database, autocommit=True) as admin:
        first = load(admin, source, confirm_by=AGENT.user_id)
        assert first.counts["units_inserted"] == 34 and first.counts["units_updated"] == 0
        assert first.counts["tickets_inserted"] == 6
        # 원본에 없는 16건은 건너뛴다. 원본에 든 이전 문의 3건 중 시리얼이 있는 2건만 연결된다(S09는 20대라 없음).
        assert len(first.missing_tickets) == 16 and first.counts["links_inserted"] == 2
        again = load(admin, source, confirm_by=AGENT.user_id)
        assert again.counts == {
            "units_inserted": 0,
            "units_updated": 34,
            "tickets_inserted": 0,
            "tickets_skipped": 6,
            "links_inserted": 0,
            "links_skipped": 2,
        }
        rows = admin.execute(
            "SELECT count(*), count(*) FILTER (WHERE replaced_by_unit_id IS NOT NULL), "
            "count(*) FILTER (WHERE derivation_kind = 'ASSUMED' AND data_origin = 'demo') "
            "FROM cs.installed_units WHERE source_instance = 'test-seed'"
        ).fetchone()
        assert rows == (34, 4, 34)
        # 정답 열은 문의 행 어디에도 들어가지 않는다.
        assert admin.execute(
            "SELECT count(*) FROM cs.tickets WHERE source_instance = 'test-seed' "
            "AND body || coalesce(subject, '') LIKE '%GOLD_SECRET%'"
        ).fetchone() == (0,)


def test_the_current_ticket_sees_candidates_but_no_answer(client, admin):
    """S13: 2024-05 설치 장비는 후보로만 보이고, 회신 때 공개되는 다른 장비는 없다. 이력은 확정 전까지 비어 있다."""
    current = ticket_id(admin, "CSV1-003317")
    listed = client.get(f"/api/v1/cs/tickets/{current}/units").json()["data"]["items"]
    assert [item["unit"]["serial_no"] for item in listed] == ["VSN-MTR-1301"]
    assert listed[0]["matches_ticket"] is True and listed[0]["link"] is None
    assert listed[0]["unit"]["commissioning_date"] == "2024-05-01"
    history = client.get(f"/api/v1/cs/tickets/{current}/unit-history").json()["data"]
    assert history["status"] == "NO_CONFIRMED_UNIT" and history["items"] == []


def test_confirming_the_replacement_shows_the_replaced_units_ticket(client, admin):
    """S04: 교체품을 확정하면 원장비(0401)에 확정된 이전 보증 문의가 REPLACED_UNIT로 보인다."""
    current = ticket_id(admin, "CSV1-003124")
    previous = ticket_id(admin, "CSV1-000808")
    listed = client.get(f"/api/v1/cs/tickets/{current}/units").json()["data"]["items"]
    by_serial = {item["unit"]["serial_no"]: item for item in listed}
    assert set(by_serial) == {"VSN-ELCB-0401", "VSN-ELCB-0402"}
    assert by_serial["VSN-ELCB-0401"]["unit"]["unit_status"] == "REPLACED"
    replacement = by_serial["VSN-ELCB-0402"]["unit"]["id"]
    response = client.put(
        f"/api/v1/cs/tickets/{current}/units/{replacement}",
        json={
            "link_status": "CONFIRMED",
            "link_basis": "CUSTOMER_CONFIRMED",
            "repeat_type": "REPLACEMENT",
            "version": None,
        },
    )
    assert response.status_code == 200, response.text
    history = client.get(f"/api/v1/cs/tickets/{current}/unit-history").json()["data"]
    assert [(item["id"], item["relation"]) for item in history["items"]] == [
        (str(previous), "REPLACED_UNIT")
    ]


def test_without_a_confirmer_previous_links_are_candidates(cs_database, source):
    with psycopg.connect(cs_database, autocommit=True) as admin:
        units = seed.read_units(UNITS)
        refs = seed.read_ticket_refs(TICKETS, units)
        plan = seed.Plan(units=units, tickets=refs)
        seed.apply(
            admin,
            plan,
            data_origin="demo",
            source_instance="test-seed-candidates",
            ticket_inputs=seed.read_ticket_inputs(source, {r.ticket_id for r in refs}),
            confirm_by=None,
        )
        statuses = admin.execute(
            "SELECT DISTINCT l.link_status, l.decided_by FROM cs.ticket_unit_links l "
            "JOIN cs.tickets t ON t.id = l.ticket_id WHERE t.source_instance = 'test-seed-candidates'"
        ).fetchall()
        assert statuses == [("CANDIDATE", None)]
