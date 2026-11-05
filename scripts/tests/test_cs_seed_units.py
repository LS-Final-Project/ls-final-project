"""KAN-205 장비 적재 CLI. 입력 검사·DSN 경계·dry-run을 DB 없이 검사한다."""

import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from scripts import cs_seed_units as seed

UNIT_ROW = {
    "scenario_id": "S99",
    "serial_no": "VSN-T-0001",
    "customer_external_id": "CUST-0001",
    "product_code": "UPS-LSU-10K",
    "site_code": "",
    "commissioning_date": "",
    "commissioning_status": "UNKNOWN",
    "unit_status": "ACTIVE",
    "replaced_by_serial_no": "",
    "fw_version_last": "",
    "known_at": "2026-01-01T00:00:00Z",
    "basis": "test",
}


def write(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


class SeedUnitsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def units(self, *rows: dict[str, str]) -> Path:
        return write(self.dir / "units.csv", [UNIT_ROW | row for row in rows])

    def test_repository_csvs_are_consistent(self) -> None:
        """저장소의 장비·문의 CSV가 서로 맞고, 이번 문의에는 시리얼(정답)이 없다."""
        units = seed.read_units(seed.DEFAULT_UNITS)
        refs = seed.read_ticket_refs(seed.DEFAULT_TICKETS, units)
        self.assertEqual(len(units), 34)
        self.assertEqual(sum(u.replaced_by_serial_no is not None for u in units), 4)
        self.assertEqual(len(refs), 22)
        self.assertTrue(all(r.unit_serial_no is None for r in refs if r.role == "current"))
        # 적재 코드는 정답 파일을 알지 못한다.
        self.assertNotIn("same_unit_gold", Path(seed.__file__).read_text(encoding="utf-8"))

    def test_replaced_units_need_a_replacement_in_the_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "REPLACED"):
            seed.read_units(self.units({"unit_status": "REPLACED"}))
        with self.assertRaisesRegex(ValueError, "같은 파일에 없음"):
            seed.read_units(
                self.units({"unit_status": "REPLACED", "replaced_by_serial_no": "VSN-T-9999"})
            )
        with self.assertRaisesRegex(ValueError, "먼저 알 수 없음"):
            seed.read_units(
                self.units(
                    {
                        "unit_status": "REPLACED",
                        "replaced_by_serial_no": "VSN-T-0002",
                        "known_at": "2026-02-01T00:00:00Z",
                    },
                    {"serial_no": "VSN-T-0002"},
                )
            )

    def test_commissioning_date_and_status_must_agree(self) -> None:
        with self.assertRaisesRegex(ValueError, "날짜가 없음"):
            seed.read_units(self.units({"commissioning_status": "CONFIRMED"}))
        with self.assertRaisesRegex(ValueError, "CONFIRMED·PLANNED"):
            seed.read_units(self.units({"commissioning_date": "2026-01-01"}))
        with self.assertRaisesRegex(ValueError, "시간대"):
            seed.read_units(self.units({"known_at": "2026-01-01T00:00:00"}))

    def test_current_tickets_may_not_carry_a_serial(self) -> None:
        units = seed.read_units(self.units({}))
        tickets = write(
            self.dir / "tickets.csv",
            [
                {
                    "scenario_id": "S99",
                    "ticket_id": "CSV1-000001",
                    "role": "current",
                    "unit_serial_no": "VSN-T-0001",
                }
            ],
        )
        with self.assertRaisesRegex(ValueError, "정답"):
            seed.read_ticket_refs(tickets, units)

    def test_remote_dsn_is_refused_without_the_flag(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = seed.main(["--admin-url", "postgresql://u:p@db.example.invalid/team4"])
        self.assertEqual(code, 2)
        self.assertIn("--allow-remote", err.getvalue())

    def test_dry_run_reports_missing_tickets_and_writes_nothing(self) -> None:
        source = write(
            self.dir / "dev.csv",
            [
                dict.fromkeys(seed.TICKET_INPUT, "")
                | {
                    "ticket_id": "CSV1-000808",
                    "received_at_utc": "2025-12-18T15:02:22Z",
                    "channel": "PHONE",
                    "body_language": "en",
                    "customer_id": "CUST-0164",
                    "product_code": "ELCB-EBN-203c",
                    "inquiry_body": "x",
                }
            ],
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = seed.main(
                [
                    "--admin-url",
                    "postgresql://postgres:x@127.0.0.1:1/team4",
                    "--tickets-source",
                    str(source),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("찾음 1건, 없음 21건", out.getvalue())
        self.assertIn("dry-run", out.getvalue())


if __name__ == "__main__":
    unittest.main()
