"""Authored metadata and transaction fake only; no DB, gold, source bodies or models."""

import copy
import csv
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from scripts.local_business import assumed_units as mod


def write(path, rows):
    with path.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    units, refs, receipt_rows, tickets = [], [], [], {}
    source_hash = "a" * 64
    instance = "cs-development-v1:" + source_hash
    at = datetime(2026, 1, 1, tzinfo=UTC)
    for scenario in sorted(mod.SCENARIOS):
        n = 20 if scenario == "S09" else 2 if scenario in {"S04", "S06", "S07", "S10"} else 1
        product = "PRODUCT-" + scenario
        for i in range(n):
            units.append(
                dict(
                    scenario_id=scenario,
                    serial_no=f"UNIT-{scenario}-{i}",
                    customer_external_id="CUSTOMER-" + scenario,
                    product_code=product,
                    site_code="",
                    commissioning_date="",
                    commissioning_status="UNKNOWN",
                    unit_status="REPLACED" if n == 2 and i == 0 else "ACTIVE",
                    replaced_by_serial_no=f"UNIT-{scenario}-1" if n == 2 and i == 0 else "",
                    fw_version_last="",
                    known_at=at.isoformat(),
                )
            )
        for role in ("previous", "current"):
            external = scenario + "-" + role
            identifier = uuid5(NAMESPACE_URL, f"training_csv/{instance}/ticket/{external}")
            refs.append(
                dict(
                    scenario_id=scenario,
                    ticket_id=external,
                    role=role,
                    unit_serial_no=f"UNIT-{scenario}-0"
                    if role == "previous" and scenario != "S09"
                    else "",
                )
            )
            receipt_rows.append(
                dict(
                    external_id=external,
                    id=str(identifier),
                    customer_external_id="CUSTOMER-" + scenario,
                )
            )
            when = at if role == "previous" else at + timedelta(days=30)
            tickets[external] = dict(
                id=identifier,
                customer_external_id="CUSTOMER-" + scenario,
                product_code=product,
                received_at=when,
                known_at=when,
                data_origin="synthetic",
                derivation_kind="PROVIDED",
            )
    up, rp = tmp_path / "units.csv", tmp_path / "refs.csv"
    write(up, units)
    write(rp, refs)
    monkeypatch.setattr(mod, "UNITS_SHA256", mod.hashlib.sha256(up.read_bytes()).hexdigest())
    monkeypatch.setattr(mod, "REFS_SHA256", mod.hashlib.sha256(rp.read_bytes()).hexdigest())
    receipt = dict(
        committed=True,
        source="training_csv",
        source_instance=instance,
        hashes={"source_tickets": source_hash},
        tickets=receipt_rows,
    )
    return up, rp, receipt, tickets


class DB:
    def __init__(self, tickets):
        self.tickets = copy.deepcopy(tickets)
        self.tables = {"installed_units": [], "ticket_unit_links": []}
        self.queries = []
        self.fail_link = False

    @contextmanager
    def transaction(self):
        before = copy.deepcopy(self.tables)
        try:
            yield
        except BaseException:
            self.tables = before
            raise

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        rows = []
        if sql.startswith(("SET LOCAL", "LOCK TABLE")):
            pass
        elif "FROM cs.tickets " in sql:
            assert params[0] == "training_csv" and params[1] == "cs-development-v1:" + "a" * 64
            if params[2] in self.tickets:
                rows = [copy.deepcopy(self.tickets[params[2]])]
        elif sql.startswith("SELECT"):
            table = sql.split("FROM cs.")[1].split()[0]
            keys = (
                ("product_code", "serial_no")
                if table == "installed_units"
                else ("ticket_id", "unit_id")
            )
            rows = [
                copy.deepcopy(r)
                for r in self.tables[table]
                if tuple(r[k] for k in keys) == tuple(params)
            ]
        elif sql.startswith("INSERT INTO cs."):
            table = sql.split("cs.")[1].split()[0]
            assert table in self.tables, "Ticket mutation forbidden"
            columns = sql.split("(", 1)[1].split(")", 1)[0].split(", ")
            record = dict(zip(columns, params, strict=True))
            if table == "ticket_unit_links" and self.fail_link:
                raise RuntimeError("synthetic late FK failure")
            if table == "installed_units" and record["replaced_by_unit_id"]:
                assert any(r["id"] == record["replaced_by_unit_id"] for r in self.tables[table])
            self.tables[table].append(record)
        else:
            raise AssertionError(sql)
        return SimpleNamespace(fetchall=lambda: rows)


def test_pinned_scope_and_no_ticket_writes_exact_replay(bundle):
    up, rp, receipt, tickets = bundle
    plan = mod.prepare(up, rp, receipt)
    assert len(plan.units) == 34 and len(plan.refs) == 22
    db = DB(tickets)
    first = mod.apply(db, plan)
    second = mod.apply(db, plan)
    assert (
        first["tickets_reused"] == 22
        and first["units_inserted"] == 34
        and first["links_inserted"] == 10
    )
    assert (
        first["tickets_inserted"]
        == first["tickets_updated"]
        == first["confirmed_links_created"]
        == 0
    )
    assert second["units_inserted"] == second["links_inserted"] == 0
    assert second["units_skipped"] == 34 and second["links_skipped"] == 10
    assert first["input_hashes"] == {"units": mod.UNITS_SHA256, "refs": mod.REFS_SHA256}
    for row in db.tables["ticket_unit_links"]:
        assert row["link_status"] == "CANDIDATE" and row["derivation_kind"] == "ASSUMED"
        assert row["decided_at"] is row["decided_by"] is row["repeat_type"] is None
        assert row["ticket_id"] in {
            r["id"]
            for key, r in tickets.items()
            if key.endswith("previous") and not key.startswith("S09")
        }
    assert db.tickets == tickets
    assert not any(sql.startswith(("INSERT INTO cs.tickets ", "UPDATE ")) for sql, _ in db.queries)


@pytest.mark.parametrize(
    "field,value",
    [("source", "customer-history"), ("committed", False), ("source_instance", "other")],
)
def test_receipt_rejected(bundle, field, value):
    up, rp, receipt, _ = bundle
    receipt[field] = value
    with pytest.raises(ValueError):
        mod.prepare(up, rp, receipt)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong_uuid"])
def test_receipt_mapping_must_be_complete_and_deterministic(bundle, kind):
    up, rp, receipt, _ = bundle
    if kind == "missing":
        receipt["tickets"].pop()
    elif kind == "duplicate":
        receipt["tickets"].append(receipt["tickets"][0])
    else:
        receipt["tickets"][0]["id"] = str(uuid4())
    with pytest.raises(ValueError):
        mod.prepare(up, rp, receipt)


def test_hash_tamper_fails_without_other_files(bundle):
    up, rp, receipt, _ = bundle
    up.write_bytes(up.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="INPUT_HASH"):
        mod.prepare(up, rp, receipt)


@pytest.mark.parametrize("change", ["missing", "uuid", "customer", "product", "origin", "known_at"])
def test_db_receipt_and_scope_mismatch_before_writes(bundle, change):
    up, rp, receipt, tickets = bundle
    plan = mod.prepare(up, rp, receipt)
    db = DB(tickets)
    key = next(iter(db.tickets))
    row = db.tickets[key]
    if change == "missing":
        del db.tickets[key]
    elif change == "uuid":
        row["id"] = uuid4()
    elif change == "customer":
        row["customer_external_id"] = "wrong"
    elif change == "product":
        row["product_code"] = "wrong"
    elif change == "origin":
        row["data_origin"] = "demo"
    else:
        row["known_at"] += timedelta(days=1)
    with pytest.raises(ValueError):
        mod.apply(db, plan)
    assert all(not v for v in db.tables.values())


def test_late_failure_and_existing_conflict_roll_back(bundle):
    up, rp, receipt, tickets = bundle
    plan = mod.prepare(up, rp, receipt)
    db = DB(tickets)
    db.fail_link = True
    with pytest.raises(RuntimeError):
        mod.apply(db, plan)
    assert all(not v for v in db.tables.values())
    db.fail_link = False
    mod.apply(db, plan)
    db.tables["ticket_unit_links"][-1]["link_status"] = "CONFIRMED"
    before = copy.deepcopy(db.tables)
    with pytest.raises(ValueError, match="EXISTING_CONFLICT"):
        mod.apply(db, plan)
    assert db.tables == before


def test_provided_equipment_is_not_overwritten(bundle):
    up, rp, receipt, tickets = bundle
    plan = mod.prepare(up, rp, receipt)
    db = DB(tickets)
    mod.apply(db, plan)
    db.tables["installed_units"][0]["derivation_kind"] = "PROVIDED"
    before = copy.deepcopy(db.tables)
    with pytest.raises(ValueError, match="EXISTING_CONFLICT"):
        mod.apply(db, plan)
    assert db.tables == before


def test_current_links_future_units_and_missing_replacements_refused(bundle):
    up, rp, receipt, tickets = bundle
    plan = mod.prepare(up, rp, receipt)
    first = plan.units[0]
    future = replace(first, known_at=datetime(2030, 1, 1, tzinfo=UTC))
    bad = replace(plan, units=(future, *plan.units[1:]))
    with pytest.raises(ValueError, match="FUTURE_UNIT"):
        mod.apply(DB(tickets), bad)
    replacement = replace(first, unit_status="REPLACED", replaced_by_serial_no="EXCLUDED-1302")
    with pytest.raises(ValueError, match="REPLACEMENT_SCOPE"):
        mod.apply(DB(tickets), replace(plan, units=(replacement, *plan.units[1:])))
    ref = next(r for r in plan.refs if r.role == "current")
    refs = tuple(replace(r, unit_serial_no=first.serial_no) if r == ref else r for r in plan.refs)
    with pytest.raises(ValueError, match="CURRENT_LINK_FORBIDDEN"):
        mod.apply(DB(tickets), replace(plan, refs=refs))


def test_same_serial_across_products_keeps_composite_identity(bundle):
    up, rp, receipt, tickets = bundle
    plan = mod.prepare(up, rp, receipt)
    # Two single-unit scenarios share a serial but remain different product keys.
    original = next(u for u in plan.units if plan.scenarios[(u.product_code, u.serial_no)] == "S03")
    other = plan.units[0]
    changed = replace(original, serial_no=other.serial_no)
    scenarios = dict(plan.scenarios)
    scenario = scenarios.pop((original.product_code, original.serial_no))
    scenarios[(changed.product_code, changed.serial_no)] = scenario
    refs = tuple(
        replace(r, unit_serial_no=changed.serial_no)
        if r.scenario_id == scenario and r.role == "previous"
        else r
        for r in plan.refs
    )
    modified = replace(
        plan,
        units=tuple(changed if u == original else u for u in plan.units),
        scenarios=scenarios,
        refs=refs,
    )
    db = DB(tickets)
    result = mod.apply(db, modified)
    assert result["units_inserted"] == 34 and result["links_inserted"] == 10
    assert len({r["unit_id"] for r in db.tables["ticket_unit_links"]}) == 10
