"""고객 이력 가상 장비(installed_units) 적재 어댑터(KAN-205, 기획서 4.3·6.1 가정 테이블).

KAN-102 장비 표는 "어댑터가 적재하고 API는 읽기만" 하는 구조라(cs_app에 INSERT 권한이 없다) 관리자·마이그레이터
연결(--admin-url)로 넣는다. 기본은 --dry-run이며 --execute일 때만 쓴다. DSN 호스트가 로컬이 아니면 --allow-remote
없이는 거부한다(공용 DB는 운영자 터미널 단계에서만).

입력
- 장비 CSV(eval/customer_history/units_demo.csv): 시나리오별로 "이번 문의 접수 전에 회사가 알 수 있는 장비"만 담는다.
  같은 장비 여부 정답 파일은 읽지 않는다. 열: serial_no, customer_external_id, product_code,
  site_code, commissioning_date, commissioning_status, unit_status, replaced_by_serial_no(같은 파일의 시리얼),
  fw_version_last, known_at.
- 문의 목록 CSV(eval/customer_history/tickets.csv, 선택): 시나리오 문의 ID와 이전 문의가 제공한 시리얼. 본문은
  저장소 밖 개발 분할(--tickets-source, cs-development의 tickets.dev.csv)에서 입력 열만 읽어 원본 접수 시각으로
  등록한다. 정답·사후 열(category_code·assigned_dept 등)은 읽지 않는다. 이전 문의의 시리얼은 그 문의와 장비의
  연결로 남긴다(--confirm-by가 있으면 CONFIRMED·SERIAL_PROVIDED, 없으면 CANDIDATE).

같은 (product_code, serial_no)는 갱신하고, 같은 (source, source_instance, external_id) 문의는 건너뛰어 다시 돌려도
안전하다. 실행: uv run --frozen python -m scripts.cs_seed_units --admin-url postgresql://postgres:…@127.0.0.1:5433/team4
--tickets-source ../cs-development-20261027/tickets.dev.csv [--confirm-by <cs_agent uuid>] [--execute]
"""

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg

from scripts.cs_index_docs import is_local

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNITS = ROOT / "eval/customer_history/units_demo.csv"
DEFAULT_TICKETS = ROOT / "eval/customer_history/tickets.csv"
SOURCE = "customer-history"
UNIT_COLUMNS = (
    "serial_no",
    "customer_external_id",
    "product_code",
    "site_code",
    "commissioning_date",
    "commissioning_status",
    "unit_status",
    "replaced_by_serial_no",
    "fw_version_last",
    "known_at",
)
COMMISSIONING = frozenset({"CONFIRMED", "PLANNED", "UNKNOWN"})
UNIT_STATUS = frozenset({"ACTIVE", "REPLACED", "RETIRED"})
# 개발 분할에서 읽는 입력 열. 정답·사후 열은 여기 없다.
TICKET_INPUT = (
    "ticket_id",
    "received_at_utc",
    "channel",
    "body_language",
    "customer_id",
    "customer_tier",
    "product_code",
    "inquiry_subject",
    "inquiry_body",
    "fw_version_reported",
    "error_code_reported",
)
TIERS = frozenset({"STANDARD", "GOLD", "PLATINUM"})


@dataclass(frozen=True)
class Unit:
    serial_no: str
    customer_external_id: str
    product_code: str
    site_code: str | None
    commissioning_date: date | None
    commissioning_status: str
    unit_status: str
    replaced_by_serial_no: str | None
    fw_version_last: str | None
    known_at: datetime


@dataclass(frozen=True)
class TicketRef:
    scenario_id: str
    ticket_id: str
    role: str
    unit_serial_no: str | None


@dataclass
class Plan:
    """적재 계획과 결과 수. dry-run은 계획만, execute는 결과까지 채운다."""

    units: list[Unit]
    tickets: list[TicketRef]
    counts: dict[str, int] = field(default_factory=dict)
    missing_tickets: list[str] = field(default_factory=list)


def _blank(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _timestamp(value: str, where: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{where}: 시각 형식 오류 {value!r}") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{where}: 시각에 시간대가 없음 {value!r}")
    return parsed.astimezone(UTC)


def read_units(path: Path) -> list[Unit]:
    """장비 CSV를 읽고 검사한다.

    Args:
        path: 장비 CSV.

    Returns:
        list[Unit]: 파일 순서의 장비. 시리얼 중복, 모르는 상태, 교체 관계 불일치, 날짜 오류는 ValueError다.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in UNIT_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"장비 CSV에 열이 없음: {missing}")
        rows = list(reader)
    units: list[Unit] = []
    serials: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=2):
        where = f"{path.name}:{index}"
        serial = _blank(row["serial_no"])
        customer = _blank(row["customer_external_id"])
        product = _blank(row["product_code"])
        if not serial or not customer or not product:
            raise ValueError(f"{where}: serial_no·customer_external_id·product_code는 필수")
        if (product, serial) in serials:
            raise ValueError(f"{where}: 시리얼 중복 {product} {serial}")
        serials.add((product, serial))
        status = _blank(row["commissioning_status"]) or "UNKNOWN"
        if status not in COMMISSIONING:
            raise ValueError(f"{where}: commissioning_status 값 오류 {status!r}")
        unit_status = _blank(row["unit_status"]) or "ACTIVE"
        if unit_status not in UNIT_STATUS:
            raise ValueError(f"{where}: unit_status 값 오류 {unit_status!r}")
        replaced_by = _blank(row["replaced_by_serial_no"])
        if (unit_status == "REPLACED") != (replaced_by is not None):
            raise ValueError(f"{where}: REPLACED 장비에만 replaced_by_serial_no를 둔다")
        commissioning = _blank(row["commissioning_date"])
        if status != "UNKNOWN" and commissioning is None:
            raise ValueError(f"{where}: 설치 상태가 {status}인데 날짜가 없음")
        if status == "UNKNOWN" and commissioning is not None:
            raise ValueError(f"{where}: 설치일이 있으면 상태를 CONFIRMED·PLANNED로 둔다")
        try:
            commissioning_date = date.fromisoformat(commissioning) if commissioning else None
        except ValueError:
            raise ValueError(f"{where}: 설치일 형식 오류 {commissioning!r}") from None
        known_at = _blank(row["known_at"])
        if known_at is None:
            raise ValueError(f"{where}: known_at은 필수(회사가 장비를 알게 된 시각)")
        units.append(
            Unit(
                serial_no=serial,
                customer_external_id=customer,
                product_code=product,
                site_code=_blank(row["site_code"]),
                commissioning_date=commissioning_date,
                commissioning_status=status,
                unit_status=unit_status,
                replaced_by_serial_no=replaced_by,
                fw_version_last=_blank(row["fw_version_last"]),
                known_at=_timestamp(known_at, where),
            )
        )
    by_product = {(u.product_code, u.serial_no): u for u in units}
    for unit in units:
        if unit.replaced_by_serial_no is None:
            continue
        target = by_product.get((unit.product_code, unit.replaced_by_serial_no))
        if target is None:
            raise ValueError(
                f"교체품 {unit.replaced_by_serial_no}이 같은 파일에 없음({unit.serial_no})"
            )
        if target.customer_external_id != unit.customer_external_id:
            raise ValueError(f"교체품 {target.serial_no}의 고객이 원장비 {unit.serial_no}와 다름")
        if target.known_at < unit.known_at:
            raise ValueError(f"교체품 {target.serial_no}을 원장비보다 먼저 알 수 없음")
    return units


def read_ticket_refs(path: Path, units: list[Unit]) -> list[TicketRef]:
    """시나리오 문의 목록을 읽는다. 이전 문의의 시리얼은 장비 CSV에 있어야 한다."""
    serials = {u.serial_no for u in units}
    refs: list[TicketRef] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=2):
            ticket = _blank(row.get("ticket_id"))
            role = _blank(row.get("role"))
            serial = _blank(row.get("unit_serial_no"))
            where = f"{path.name}:{index}"
            if not ticket or role not in {"previous", "current"}:
                raise ValueError(f"{where}: ticket_id와 role(previous|current)이 필요")
            if ticket in seen:
                raise ValueError(f"{where}: 문의 중복 {ticket}")
            seen.add(ticket)
            if serial is not None and serial not in serials:
                raise ValueError(f"{where}: 시리얼 {serial}이 장비 CSV에 없음")
            if role == "current" and serial is not None:
                raise ValueError(f"{where}: 이번 문의에는 시리얼을 두지 않는다(정답이 된다)")
            refs.append(TicketRef(row.get("scenario_id", "").strip(), ticket, role, serial))
    return refs


def read_ticket_inputs(source: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    """개발 분할 CSV에서 원하는 문의의 입력 열만 읽는다. 정답 열은 메모리에 남기지 않는다."""
    found: dict[str, dict[str, str]] = {}
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in TICKET_INPUT if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"문의 원본에 열이 없음: {missing}")
        for row in reader:
            if row["ticket_id"] in wanted:
                found[row["ticket_id"]] = {name: row[name] for name in TICKET_INPUT}
    return found


def ticket_values(row: dict[str, str], *, data_origin: str, source_instance: str) -> dict[str, Any]:
    """API 접수(routes.create_ticket)와 같은 열로 문의 행을 만든다. 접수 시각은 원본 값이다."""
    received = _timestamp(row["received_at_utc"], row["ticket_id"])
    tier = _blank(row["customer_tier"])
    return {
        "id": uuid4(),
        "source": SOURCE,
        "source_instance": source_instance,
        "external_id": row["ticket_id"],
        "data_origin": data_origin,
        "derivation_kind": "PROVIDED",
        "received_at": received,
        "known_at": received,
        "channel": row["channel"],
        "body_language": row["body_language"],
        "customer_external_id": row["customer_id"],
        "product_code": row["product_code"],
        "subject": _blank(row["inquiry_subject"]),
        "body": row["inquiry_body"],
        "fw_version_reported": _blank(row["fw_version_reported"]),
        "error_code_reported": _blank(row["error_code_reported"]),
        "customer_tier": tier if tier in TIERS else None,
    }


def apply(
    conn: psycopg.Connection[Any],
    plan: Plan,
    *,
    data_origin: str,
    source_instance: str,
    ticket_inputs: dict[str, dict[str, str]],
    confirm_by: UUID | None,
) -> None:
    """한 트랜잭션으로 장비·문의·연결을 넣는다. 다시 돌려도 결과가 같다."""
    counts = {
        "units_inserted": 0,
        "units_updated": 0,
        "tickets_inserted": 0,
        "tickets_skipped": 0,
        "links_inserted": 0,
        "links_skipped": 0,
    }
    with conn.transaction():
        unit_ids: dict[str, UUID] = {}
        for unit in plan.units:
            row = conn.execute(
                "INSERT INTO cs.installed_units (id, source, source_instance, external_id, "
                "data_origin, derivation_kind, known_at, serial_no, customer_external_id, "
                "product_code, site_code, commissioning_date, commissioning_status, "
                "fw_version_last, unit_status) VALUES (%s, %s, %s, %s, %s, 'ASSUMED', %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (product_code, serial_no) DO UPDATE SET "
                "customer_external_id = EXCLUDED.customer_external_id, "
                "site_code = EXCLUDED.site_code, "
                "commissioning_date = EXCLUDED.commissioning_date, "
                "commissioning_status = EXCLUDED.commissioning_status, "
                "fw_version_last = EXCLUDED.fw_version_last, unit_status = EXCLUDED.unit_status, "
                "known_at = EXCLUDED.known_at, updated_at = now() "
                "RETURNING id, (xmax = 0) AS inserted",
                (
                    uuid4(),
                    SOURCE,
                    source_instance,
                    f"{unit.product_code}:{unit.serial_no}",
                    data_origin,
                    unit.known_at,
                    unit.serial_no,
                    unit.customer_external_id,
                    unit.product_code,
                    unit.site_code,
                    unit.commissioning_date,
                    unit.commissioning_status,
                    unit.fw_version_last,
                    unit.unit_status,
                ),
            ).fetchone()
            assert row is not None
            unit_ids[unit.serial_no] = row[0]
            counts["units_inserted" if row[1] else "units_updated"] += 1
        for unit in plan.units:
            conn.execute(
                "UPDATE cs.installed_units SET replaced_by_unit_id = %s WHERE id = %s",
                (
                    unit_ids[unit.replaced_by_serial_no] if unit.replaced_by_serial_no else None,
                    unit_ids[unit.serial_no],
                ),
            )
        ticket_ids: dict[str, UUID] = {}
        for ref in plan.tickets:
            existing = conn.execute(
                "SELECT id FROM cs.tickets WHERE source = %s AND source_instance = %s "
                "AND external_id = %s",
                (SOURCE, source_instance, ref.ticket_id),
            ).fetchone()
            if existing:
                ticket_ids[ref.ticket_id] = existing[0]
                counts["tickets_skipped"] += 1
                continue
            row = ticket_inputs.get(ref.ticket_id)
            if row is None:
                plan.missing_tickets.append(ref.ticket_id)
                continue
            values = ticket_values(row, data_origin=data_origin, source_instance=source_instance)
            columns = ", ".join(values)
            conn.execute(
                f"INSERT INTO cs.tickets ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
                tuple(values.values()),
            )
            ticket_ids[ref.ticket_id] = values["id"]
            counts["tickets_inserted"] += 1
        for ref in plan.tickets:
            if ref.unit_serial_no is None or ref.ticket_id not in ticket_ids:
                continue
            exists = conn.execute(
                "SELECT 1 FROM cs.ticket_unit_links WHERE ticket_id = %s AND unit_id = %s",
                (ticket_ids[ref.ticket_id], unit_ids[ref.unit_serial_no]),
            ).fetchone()
            if exists:
                counts["links_skipped"] += 1
                continue
            status = "CONFIRMED" if confirm_by else "CANDIDATE"
            conn.execute(
                "INSERT INTO cs.ticket_unit_links (id, ticket_id, unit_id, data_origin, "
                "derivation_kind, link_status, link_basis, decided_by, decided_at) "
                "VALUES (%s, %s, %s, %s, 'ASSUMED', %s, 'SERIAL_PROVIDED', %s, %s)",
                (
                    uuid4(),
                    ticket_ids[ref.ticket_id],
                    unit_ids[ref.unit_serial_no],
                    data_origin,
                    status,
                    confirm_by,
                    datetime.now(UTC) if confirm_by else None,
                ),
            )
            counts["links_inserted"] += 1
    plan.counts = counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--units", type=Path, default=DEFAULT_UNITS, help="장비 CSV")
    parser.add_argument(
        "--tickets",
        type=Path,
        default=DEFAULT_TICKETS,
        help="시나리오 문의 목록 CSV(ID와 이전 시리얼)",
    )
    parser.add_argument(
        "--tickets-source",
        type=Path,
        help="문의 본문을 읽을 개발 분할 CSV(저장소 밖). 없으면 장비만 넣는다",
    )
    parser.add_argument(
        "--admin-url",
        required=True,
        help="관리자·마이그레이터 DSN(cs_app은 장비 INSERT 권한이 없다)",
    )
    parser.add_argument("--data-origin", choices=("demo", "synthetic"), default="demo")
    parser.add_argument("--source-instance", default="customer-history-v1")
    parser.add_argument(
        "--confirm-by",
        type=UUID,
        help="이전 문의의 시리얼 연결을 이 사용자가 확정한 것으로 둔다(없으면 후보)",
    )
    parser.add_argument(
        "--allow-remote", action="store_true", help="로컬이 아닌 DSN 허용(공용 DB는 운영자만)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="기본값. 읽고 검사만")
    mode.add_argument("--execute", action="store_true", help="DB에 적재")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not is_local(args.admin_url) and not args.allow_remote:
        print(
            "로컬이 아닌 DSN입니다. 공용 DB 적재는 --allow-remote와 운영자 확인이 필요합니다.",
            file=sys.stderr,
        )
        return 2
    try:
        units = read_units(args.units)
        refs = read_ticket_refs(args.tickets, units) if args.tickets_source else []
        inputs = (
            read_ticket_inputs(args.tickets_source, {r.ticket_id for r in refs})
            if args.tickets_source
            else {}
        )
    except (OSError, ValueError) as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return 2
    plan = Plan(units=units, tickets=refs)
    missing = sorted({r.ticket_id for r in refs} - set(inputs))
    print(
        f"장비 {len(units)}대(교체 관계 {sum(u.replaced_by_serial_no is not None for u in units)}건), "
        f"문의 {len(refs)}건(원본에서 찾음 {len(inputs)}건, 없음 {len(missing)}건), "
        f"이전 시리얼 연결 {sum(r.unit_serial_no is not None for r in refs)}건"
    )
    if missing:
        print("원본에 없는 문의(건너뜀): " + ", ".join(missing))
    if not args.execute:
        print("dry-run: 적재하지 않았습니다. --execute로 넣습니다.")
        return 0
    with psycopg.connect(args.admin_url) as conn:
        apply(
            conn,
            plan,
            data_origin=args.data_origin,
            source_instance=args.source_instance,
            ticket_inputs=inputs,
            confirm_by=args.confirm_by,
        )
    print(" ".join(f"{k}={v}" for k, v in plan.counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
