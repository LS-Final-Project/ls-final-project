"""KAN-186 자사 사양 23종 정규화 스크립트. 합성 원천 행으로 규칙을 보고, 제공 데이터가 있으면 결과 파일이 최신인지 본다."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts import rfp_own_spec_facts as own

REF = Path(__file__).resolve().parents[3] / "ref"
MASTER = [
    "product_code",
    "family",
    "lifecycle_status",
    "standards",
    "certifications",
    "warranty_months",
    "doc_ids",
]
FACTS = ["product_code", "attribute", "value", "doc_id", "section"]


def write(folder: Path, master: list[dict], facts: list[dict]) -> None:
    for name, fields, rows in (
        ("product_master.csv", MASTER, master),
        ("product_spec_facts.csv", FACTS, facts),
    ):
        with (folder / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def row(code, attribute, value, section="2.1"):
    return {
        "product_code": code,
        "attribute": attribute,
        "value": value,
        "doc_id": f"DS-{code}",
        "section": section,
    }


class NormalizeTest(unittest.TestCase):
    def build(self, master, facts):
        with tempfile.TemporaryDirectory() as folder:
            write(Path(folder), master, facts)
            return own.build(Path(folder))

    def test_ratings_keep_their_own_attribute_unit_and_condition(self):
        data = self.build(
            [
                {
                    "product_code": "BRK",
                    "family": "LV_BREAKER",
                    "lifecycle_status": "ACTIVE",
                    "standards": "IEC 60947-2",
                    "certifications": "CE;UL (-U variant);KEMA type test report",
                    "warranty_months": "18",
                    "doc_ids": "DS-BRK",
                }
            ],
            [
                row("BRK", "Rated operational voltage (Ue)", "AC 690 V max"),
                row("BRK", "Rated current (In)", "15, 20, 100 A"),
                row("BRK", "Rated busbar current", "1250 A (up to 2500 A)"),
                row("BRK", "Rated short-time withstand", "25 kA / 3 s", "2.2"),
                row("BRK", "Ultimate breaking capacity Icu at AC 415 V", "37 kA", "2.2"),
                row("BRK", "Internal arc classification", "IAC AFLR 25 kA 1 s", "2.2"),
                row("BRK", "Mechanical endurance", "20,000 operations", "2.3"),
            ],
        )
        [product] = data["products"]
        facts = {f["attribute"]: f for f in product["spec_facts"]}
        # Ue is not the rated maximum voltage, and the busbar current is not the rated current.
        self.assertNotIn("rated_max_voltage", facts)
        self.assertEqual(
            (
                facts["rated_operational_voltage"]["value"],
                facts["rated_operational_voltage"]["unit"],
            ),
            ("690", "V"),
        )
        self.assertEqual(facts["rated_current"]["value"], "100")
        self.assertEqual(facts["rated_current"]["note"], "선택 정격 중 최댓값")
        self.assertEqual(facts["busbar_rated_current"]["value"], "1250")
        self.assertEqual(
            facts["short_time_withstand_current"]["conditions"],
            [{"name": "duration", "value": "3", "unit": "s"}],
        )
        self.assertEqual(
            facts["breaking_capacity_icu"]["conditions"][0],
            {"name": "voltage", "value": "415", "unit": "V"},
        )
        self.assertEqual(facts["internal_arc_classification"]["value"], "IAC AFLR")
        self.assertEqual(facts["warranty_months"]["value"], "18")
        # Every normalized fact keeps the original sentence and its datasheet location.
        self.assertTrue(all(f["source_value"] and f["evidence_id"] for f in product["spec_facts"]))
        # Not a comparison attribute: kept as it was, not compared.
        self.assertEqual(product["unmapped_facts"][0]["source_attribute"], "Mechanical endurance")
        self.assertEqual(
            data["coverage"],
            {"products": 1, "source_rows": 7, "mapped_rows": 6, "unmapped_rows": 1},
        )
        certs = {c["standard"]: c for c in product["certifications"]}
        self.assertEqual(
            (certs["CE"]["kind"], certs["CE"]["evidence_id"]), ("MARK", "product_master")
        )
        self.assertEqual((certs["UL"]["kind"], certs["UL"]["variant"]), ("MARK", "-U"))
        self.assertEqual(
            (certs["KEMA"]["kind"], certs["KEMA"]["evidence_id"]), ("TEST_REPORT", "DS-BRK")
        )

    def test_the_dry_transformer_gets_the_demo_a_facts(self):
        data = self.build(
            [
                {
                    "product_code": "TRF",
                    "family": "TRANSFORMER",
                    "lifecycle_status": "ACTIVE",
                    "standards": "IEC 60076-11",
                    "certifications": "Type test report (IEC 60076-11);KC",
                    "warranty_months": "24",
                    "doc_ids": "DS-TRF",
                }
            ],
            [
                row("TRF", "Rated power", "630 kVA (AN) / 880 kVA (AF, with fans)"),
                row("TRF", "Voltage ratio", "11 kV / 0.415 kV"),
                row("TRF", "Impedance voltage", "6% at 120 °C", "2.2"),
                row("TRF", "Off-circuit tap range", "±2 x 2.5%", "2.2"),
                row("TRF", "Insulation class / temperature rise", "Class F / 100 K", "2.2"),
            ],
        )
        facts = {f["attribute"]: f for f in data["products"][0]["spec_facts"]}
        self.assertEqual(facts["rated_power"]["conditions"], [{"name": "cooling", "value": "AN"}])
        # AF/AN − 1 with the AN temperature rise of the same sheet.
        increase = facts["forced_air_capacity_increase"]
        self.assertEqual((increase["value"], increase["conditions"][0]["value"]), ("39.7", "100"))
        self.assertEqual(
            (facts["secondary_voltage"]["value"], facts["secondary_voltage"]["unit"]), ("415", "V")
        )
        self.assertEqual(facts["impedance"]["conditions"][0]["value"], "120")
        self.assertEqual(facts["tap"]["value"], "±2×2.5%")
        self.assertEqual(data["products"][0]["certifications"][0]["kind"], "TEST_REPORT")

    def test_a_value_without_a_number_stops_the_build(self):
        with self.assertRaises(ValueError):
            self.build(
                [
                    {
                        "product_code": "X",
                        "family": "F",
                        "lifecycle_status": "ACTIVE",
                        "standards": "",
                        "certifications": "",
                        "warranty_months": "12",
                        "doc_ids": "",
                    }
                ],
                [row("X", "Rated voltage", "see drawing")],
            )

    @unittest.skipUnless((REF / "product_master.csv").exists(), "제공 데이터 ref/가 없는 환경")
    def test_the_committed_file_is_the_output_of_the_provided_data(self):
        self.assertEqual(own.render(own.build(REF)), own.OUTPUT.read_text(encoding="utf-8"))
        coverage = json.loads(own.OUTPUT.read_text(encoding="utf-8"))["coverage"]
        self.assertEqual((coverage["products"], coverage["source_rows"]), (23, 169))


if __name__ == "__main__":
    unittest.main()
