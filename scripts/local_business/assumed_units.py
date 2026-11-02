"""Pinned KAN-205 metadata only; reuse PROVIDED tickets, seed ASSUMED candidates.

Host must prove namespace/source/loader/actual DB target and no active driver before apply.
No connection creation, ticket writes, gold inputs, model calls or confirmation option.
"""

import csv
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from scripts.cs_seed_units import Unit, read_ticket_refs, read_units

UNITS_SHA256 = "a4b4432086cb26e6fda04188de72fc9cefe6ae39f3b51467970c10b3768174df"
REFS_SHA256 = "93706b3f57df6796c5f95c9ffd8206ec664909d1eb8d02ae79a7171b54a9d36b"
SCENARIOS = frozenset({"S02", "S03", "S04", "S05", "S06", "S07", "S09", "S10", "S11", "S13", "S15"})
SOURCE = "customer-history"
INSTANCE = "kan205-assumed-bootstrap-v1"


def require(ok, code):
    if not ok:
        raise ValueError("LOCAL_ASSUMED_UNITS_" + code)


@dataclass(frozen=True)
class Plan:
    units: tuple[Unit, ...]
    refs: tuple
    scenarios: dict[tuple[str, str], str]
    ticket_ids: dict[str, UUID]
    customers: dict[str, str]
    ticket_source_instance: str
    hashes: dict[str, str]


def prepare(units_path: Path, refs_path: Path, receipt: dict) -> Plan:
    """Read only approved pinned metadata; receipt is the committed history-import receipt."""
    for path, expected in ((units_path, UNITS_SHA256), (refs_path, REFS_SHA256)):
        require(not path.is_symlink() and path.is_file(), "INPUT_PATH")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "INPUT_HASH")
    # These parsers read equipment/ID metadata only, never ticket bodies or answer files.
    units = tuple(read_units(units_path))
    refs = tuple(read_ticket_refs(refs_path, list(units)))
    with units_path.open(newline="", encoding="utf-8") as handle:
        scenarios = {
            (r["product_code"], r["serial_no"]): r["scenario_id"] for r in csv.DictReader(handle)
        }
    require(len(units) == len(scenarios) == 34 and len(refs) == 22, "SCOPE")
    require(set(scenarios.values()) == {r.scenario_id for r in refs} == SCENARIOS, "SCOPE")
    require(sum(r.unit_serial_no is not None for r in refs) == 10, "LINK_SCOPE")
    for scenario in SCENARIOS:
        require(
            sorted(r.role for r in refs if r.scenario_id == scenario) == ["current", "previous"],
            "REF_SCOPE",
        )
    require(all("1302" not in u.serial_no for u in units), "EXCLUDED_UNIT")
    require(receipt.get("committed") is True and receipt.get("source") == "training_csv", "RECEIPT")
    source_hash = receipt.get("hashes", {}).get("source_tickets", "")
    require(isinstance(source_hash, str) and re.fullmatch(r"[0-9a-f]{64}", source_hash), "RECEIPT")
    instance = "cs-development-v1:" + source_hash
    require(receipt.get("source_instance") == instance, "RECEIPT")
    ids, customers = {}, {}
    for row in receipt["tickets"]:
        external = row["external_id"]
        require(external not in ids, "RECEIPT_DUPLICATE")
        identifier = UUID(row["id"])
        require(
            str(identifier) == row["id"]
            and identifier == uuid5(NAMESPACE_URL, f"training_csv/{instance}/ticket/{external}"),
            "RECEIPT_ID",
        )
        ids[external], customers[external] = identifier, row["customer_external_id"]
    wanted = {r.ticket_id for r in refs}
    require(wanted <= ids.keys() and len(wanted) == 22, "RECEIPT_MISSING")
    return Plan(
        units,
        refs,
        scenarios,
        {k: ids[k] for k in wanted},
        {k: customers[k] for k in wanted},
        instance,
        {"units": UNITS_SHA256, "refs": REFS_SHA256},
    )


def _unit_id(key):
    return uuid5(NAMESPACE_URL, f"{SOURCE}/{INSTANCE}/{key[0]}/{key[1]}")


def apply(conn, plan: Plan) -> dict:
    """Caller supplies verified dict-row connection; one transaction, no ticket mutation.

    Return counts after transaction context succeeds. With an outer transaction the host
    must commit before reporting success; this function never commits the host transaction.
    """
    counts = {
        "tickets_reused": 0,
        "tickets_inserted": 0,
        "tickets_updated": 0,
        "units_inserted": 0,
        "units_skipped": 0,
        "links_inserted": 0,
        "links_skipped": 0,
        "confirmed_links_created": 0,
    }
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cs_migrator")
        conn.execute(
            "LOCK TABLE cs.installed_units, cs.ticket_unit_links IN SHARE ROW EXCLUSIVE MODE"
        )
        tickets = {}
        for ref in plan.refs:
            rows = conn.execute(
                "SELECT id, customer_external_id, product_code, received_at, known_at, data_origin, derivation_kind "
                "FROM cs.tickets WHERE source=%s AND source_instance=%s AND external_id=%s FOR SHARE",
                ("training_csv", plan.ticket_source_instance, ref.ticket_id),
            ).fetchall()
            require(len(rows) == 1, "TICKET_MISSING_OR_AMBIGUOUS")
            row = rows[0]
            require(
                row["id"] == plan.ticket_ids[ref.ticket_id]
                and row["customer_external_id"] == plan.customers[ref.ticket_id]
                and row["data_origin"] == "synthetic"
                and row["derivation_kind"] == "PROVIDED"
                and row["known_at"] == row["received_at"],
                "TICKET_MISMATCH",
            )
            tickets[ref.ticket_id] = row
        counts["tickets_reused"] = len(tickets)
        units = {(u.product_code, u.serial_no): u for u in plan.units}
        records = {}
        for key, unit in units.items():
            scenario = plan.scenarios[key]
            pair = [tickets[r.ticket_id] for r in plan.refs if r.scenario_id == scenario]
            current = tickets[
                next(
                    r.ticket_id
                    for r in plan.refs
                    if r.scenario_id == scenario and r.role == "current"
                )
            ]
            require(
                all(
                    t["customer_external_id"] == unit.customer_external_id
                    and t["product_code"] == unit.product_code
                    for t in pair
                ),
                "UNIT_TICKET_MISMATCH",
            )
            require(unit.known_at <= current["received_at"], "FUTURE_UNIT")
            replacement = (
                (unit.product_code, unit.replaced_by_serial_no)
                if unit.replaced_by_serial_no
                else None
            )
            if replacement:
                require(
                    replacement in units
                    and plan.scenarios[replacement] == scenario
                    and units[replacement].customer_external_id == unit.customer_external_id,
                    "REPLACEMENT_SCOPE",
                )
            values = asdict(unit)
            del values["replaced_by_serial_no"]
            records[key] = {
                "id": _unit_id(key),
                "source": SOURCE,
                "source_instance": INSTANCE,
                "external_id": ":".join(key),
                "data_origin": "synthetic",
                "derivation_kind": "ASSUMED",
                **values,
                "replaced_by_unit_id": _unit_id(replacement) if replacement else None,
                "confirmed_at": None,
                "ship_date": None,
                "lot_no": None,
            }
        links = []
        for ref in plan.refs:
            if ref.unit_serial_no is None:
                continue
            require(ref.role == "previous", "CURRENT_LINK_FORBIDDEN")
            ticket = tickets[ref.ticket_id]
            key = (ticket["product_code"], ref.unit_serial_no)
            require(key in units and plan.scenarios[key] == ref.scenario_id, "LINK_UNIT_MISMATCH")
            require(units[key].known_at <= ticket["received_at"], "FUTURE_LINK")
            links.append(
                {
                    "id": uuid5(NAMESPACE_URL, f"{INSTANCE}/link/{ticket['id']}/{_unit_id(key)}"),
                    "ticket_id": ticket["id"],
                    "unit_id": _unit_id(key),
                    "data_origin": "synthetic",
                    "derivation_kind": "ASSUMED",
                    "link_status": "CANDIDATE",
                    "link_basis": "SERIAL_PROVIDED",
                    "decided_by": None,
                    "decided_at": None,
                    "repeat_type": None,
                    "version": 1,
                }
            )
        done, visiting = set(), set()

        def insert_unit(key):
            if key in done:
                return
            require(key not in visiting, "REPLACEMENT_CYCLE")
            visiting.add(key)
            unit = units[key]
            if unit.replaced_by_serial_no:
                insert_unit((unit.product_code, unit.replaced_by_serial_no))
            _insert_or_verify(
                conn,
                "installed_units",
                records[key],
                "product_code=%s AND serial_no=%s",
                key,
                counts,
                "units",
            )
            visiting.remove(key)
            done.add(key)

        for key in units:
            insert_unit(key)
        for record in links:
            _insert_or_verify(
                conn,
                "ticket_unit_links",
                record,
                "ticket_id=%s AND unit_id=%s",
                (record["ticket_id"], record["unit_id"]),
                counts,
                "links",
            )
    return {**counts, "input_hashes": plan.hashes}


def _insert_or_verify(conn, table, record, predicate, params, counts, name):
    columns = tuple(record)
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM cs.{table} WHERE {predicate}", params
    ).fetchall()
    require(len(rows) <= 1, "EXISTING_AMBIGUOUS")
    if rows:
        require(all(rows[0][k] == record[k] for k in columns), "EXISTING_CONFLICT")
        counts[name + "_skipped"] += 1
    else:
        conn.execute(
            f"INSERT INTO cs.{table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
            tuple(record.values()),
        )
        counts[name + "_inserted"] += 1
