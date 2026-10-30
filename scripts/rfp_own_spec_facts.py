"""자사 사양 23종을 RFP 비교 형식으로 정규화한다(KAN-186). 제공 데이터는 읽기만 한다.

실행: uv run --frozen python -m scripts.rfp_own_spec_facts --ref <제공 데이터 ref 폴더> [--check]

입력은 ref/product_master.csv(제품 23종)와 ref/product_spec_facts.csv(169행)다. 출력은
backend/api/tft/rfp/own_spec_facts.json이고 협력사 프로필(partners/profiles.json)과 같은 열(속성·값·단위·조건·근거 ID·
절)을 쓴다. 자사와 협력사가 같은 비교 규칙(partners/grading.py)을 쓰기 때문이다.

- 정규화 범위: RFP 요구와 비교하는 정격·형식·통신·보호 속성만 정해진 이름·단위·조건으로 옮긴다(RULES). 같은 숫자라도
  다른 속성은 합치지 않는다(예: 정격 사용 전압 Ue와 정격 최대전압, 모선 정격 전류와 정격 전류). 나머지 행은
  `unmapped_facts`에 원문 그대로 보존하고 비교에 쓰지 않는다(미기재 ≠ 미충족: 그런 요구는 근거 부족이 된다).
- 인증: 제품 마스터의 인증 목록. "Type test report (…)"·"KEMA type test report"는 시험 성적(TEST_REPORT)이고, 그 밖의
  표시(UL·CE·KC·CB 등)는 등록·시험 구분이 적혀 있지 않아 표시(MARK)로 둔다. 비교는 표시만으로 충족을 확정하지 않는다.
  "(-U variant)"는 변형 모델에만 있는 인증이다.
- 상업 조건: 제품 마스터의 표준 보증 개월 수(warranty_months).
- 실제 LS 제품 성능 자료가 아니라 교육용 합성 데이터시트의 파생값(DERIVED)이다.
"""

import argparse
import csv
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

OUTPUT = (
    Path(__file__).resolve().parents[1] / "backend" / "api" / "tft" / "rfp" / "own_spec_facts.json"
)
NUMBER = re.compile(r"(\d+(?:\.\d+)?)")
Fact = dict[str, object]
Rule = Callable[[str], list[Fact]]


def fact(
    attribute: str, value: str, unit: str | None, conditions=(), note: str | None = None
) -> Fact:
    """정규화한 사양 하나(근거·원문은 호출자가 붙인다)."""
    item: Fact = {
        "attribute": attribute,
        "value": value,
        "unit": unit,
        "conditions": [dict(c) for c in conditions],
    }
    if note:
        item["note"] = note
    return item


def number(text: str, index: int = 0) -> str:
    found = NUMBER.findall(text)
    if len(found) <= index:
        raise ValueError(f"RFP_OWN_SPEC_NO_NUMBER: {text}")
    return found[index]


def cond(name: str, value: str, unit: str | None = None) -> dict[str, str]:
    return {"name": name, "value": value, **({"unit": unit} if unit else {})}


def kv(attribute: str) -> Rule:
    return lambda v: [fact(attribute, number(v), "kV")]


def ka(attribute: str) -> Rule:
    return lambda v: [fact(attribute, number(v), "kA")]


def amps(attribute: str) -> Rule:
    def rule(v: str) -> list[Fact]:
        values = [float(n) for n in NUMBER.findall(v.split("(")[0])]
        top = max(values)
        text = f"{top:g}"
        # 선택 가능한 정격이 여럿이면 가장 큰 값을 둔다(그 계열에서 고를 수 있는 상한).
        note = "선택 정격 중 최댓값" if len(values) > 1 else None
        if "up to" in v:
            note = f"기본값. 원문 '{v}'"
        return [fact(attribute, text, "A", note=note)]

    return rule


def withstand_with_duration(attribute: str, unit: str) -> Rule:
    def rule(v: str) -> list[Fact]:
        # "25 kA / 3 s", "28 kV / 1 min"
        duration = re.search(r"/\s*(\d+(?:\.\d+)?)\s*(s|min)\b", v)
        conditions = [cond("duration", duration.group(1), duration.group(2))] if duration else []
        return [fact(attribute, number(v), unit, conditions)]

    return rule


def icw(v: str) -> list[Fact]:
    # 속성 이름에 시간이 있다: "Short-time withstand current Icw (1 s)"
    return [fact("short_time_withstand_current", number(v), "kA", [cond("duration", "1", "s")])]


def icu(v: str) -> list[Fact]:
    return [
        fact(
            "breaking_capacity_icu",
            number(v),
            "kA",
            [cond("voltage", "415", "V"), cond("current_type", "AC")],
        )
    ]


def ics(v: str) -> list[Fact]:
    return [fact("breaking_capacity_ics", number(v), "% of Icu")]


def operational_voltage(v: str) -> list[Fact]:
    return [fact("rated_operational_voltage", number(v), "V", [cond("current_type", "AC")])]


def internal_arc(v: str) -> list[Fact]:
    # "IAC AFLR 25 kA 1 s": 등급과 시험 전류·시간. IEC 내부 아크 등급이며 IEEE 아크 내성(arc_resistant)과 다른 속성이다.
    grade = re.search(r"IAC\s+([A-Z]+)", v)
    current = re.search(r"(\d+(?:\.\d+)?)\s*kA", v)
    duration = re.search(r"(\d+(?:\.\d+)?)\s*s\b", v)
    return [
        fact(
            "internal_arc_classification",
            f"IAC {grade.group(1)}" if grade else v,
            None,
            [
                *([cond("arc_current", current.group(1), "kA")] if current else []),
                *([cond("duration", duration.group(1), "s")] if duration else []),
            ],
        )
    ]


def frame(v: str) -> list[Fact]:
    return [fact("frame_size", number(v), "AF")]


def text(attribute: str) -> Rule:
    return lambda v: [fact(attribute, " ".join(v.split()), None)]


def protection_functions(v: str) -> list[Fact]:
    # "L (long-time), S (short-time), I (instantaneous), G (ground fault)" → LSIG
    letters = "".join(re.findall(r"\b([LSIG])\s*\(", v))
    return [fact("protection_functions", letters or v, None, note="기능 머리글자만 남김")]


def rated_power(v: str, rows: dict[str, str]) -> list[Fact]:
    """변압기 냉각 방식별 용량, 구동기·전원의 정격 출력. 강제 공랭 정격이 있으면 증가율도 낸다."""
    pairs = re.findall(r"(\d+(?:\.\d+)?)\s*(kVA|kW)\s*(?:\(([^)]*)\))?", v)
    if not pairs:
        raise ValueError(f"RFP_OWN_SPEC_NO_NUMBER: {v}")
    value, unit, label = pairs[0]
    cooling = (label or "").split(",")[0].strip()
    facts = [
        fact(
            "rated_power",
            value,
            unit,
            [cond("cooling", cooling)] if cooling else [],
            note=None if len(pairs) == 1 or cooling else f"원문 '{v}'의 첫 값",
        )
    ]
    forced = [p for p in pairs[1:] if (p[2] or "").startswith("AF")]
    if cooling == "AN" and forced:
        increase = round((float(forced[0][0]) / float(value) - 1) * 100, 1)
        rise = re.search(r"(\d+)\s*K", rows.get("Insulation class / temperature rise", ""))
        facts.append(
            fact(
                "forced_air_capacity_increase",
                f"{increase:g}",
                "%",
                [cond("temperature_rise", rise.group(1), "K")] if rise else [],
                note=(
                    f"{forced[0][0]}/{value}-1 = {increase:g}%. 온도 상승 조건은 같은 시트 2.2의 "
                    f"{rise.group(1) if rise else '?'} K(AN 기준)이며 AF 정격의 온도 상승은 시트에 없다"
                ),
            )
        )
    return facts


def rated_output(duty: str) -> Rule:
    def rule(v: str) -> list[Fact]:
        return [fact("rated_power", number(v), "kW", [cond("duty", duty)])]

    return rule


def kw(v: str) -> list[Fact]:
    return [fact("rated_power", number(v), "kW")]


def voltage_ratio(v: str) -> list[Fact]:
    # "11 kV / 0.415 kV" → 1차 11 kV, 2차 415 V(협력사 프로필의 2차 전압 단위 V에 맞춤)
    primary, secondary = (float(n) for n in NUMBER.findall(v)[:2])
    return [
        fact("primary_voltage", f"{primary:g}", "kV"),
        fact(
            "secondary_voltage",
            f"{secondary * 1000:g}",
            "V",
            note=f"{secondary:g} kV를 V 단위로 옮김",
        ),
    ]


def impedance(v: str) -> list[Fact]:
    temperature = re.search(r"at\s+(\d+(?:\.\d+)?)\s*°C", v)
    return [
        fact(
            "impedance",
            number(v),
            "%",
            [cond("reference_temperature", temperature.group(1), "°C")] if temperature else [],
        )
    ]


def tap(v: str) -> list[Fact]:
    value = re.sub(r"\s*x\s*", "×", v).replace(" ", "")
    return [fact("tap", value, None, [cond("tap_type", "off-circuit")], note="x를 ×로, 공백 제거")]


def insulation(v: str) -> list[Fact]:
    grade = re.search(r"Class\s+([A-Z])", v)
    rise = re.search(r"(\d+)\s*K", v)
    return [
        fact(
            "insulation_class",
            grade.group(1) if grade else v,
            None,
            [cond("temperature_rise", rise.group(1), "K")] if rise else [],
        )
    ]


COMMUNICATION = (
    "Communication",
    "Built-in communication",
    "Communication options",
    "Interfaces",
    "SCADA interface",
    "Trip unit communication",
)
# 원천 속성 이름 → 정규화 규칙. 여기에 없는 행은 unmapped_facts에 원문 그대로 남는다.
RULES: dict[str, Rule] = {
    "Rated voltage": kv("rated_max_voltage"),
    "Rated operational voltage (Ue)": operational_voltage,
    "Rated current (In)": amps("rated_current"),
    "Rated normal current": amps("rated_current"),
    "Rated busbar current": amps("busbar_rated_current"),
    "Rated short-time withstand": withstand_with_duration("short_time_withstand_current", "kA"),
    "Short-time withstand current Icw (1 s)": icw,
    "Lightning impulse withstand (BIL)": kv("bil"),
    "Lightning impulse withstand": kv("bil"),
    "Power frequency withstand": withstand_with_duration("power_frequency_withstand", "kV"),
    "Internal arc classification": internal_arc,
    "Ultimate breaking capacity Icu at AC 415 V": icu,
    "Service breaking capacity Ics": ics,
    "Rated short-circuit breaking current": ka("rated_short_circuit_breaking_current"),
    "Frame size": frame,
    "Number of poles": text("number_of_poles"),
    "Trip unit protection functions": protection_functions,
    "Rated output (heavy duty)": rated_output("heavy"),
    "Rated output (normal duty)": rated_output("normal"),
    "Rated AC power": kw,
    "Rated AC output": kw,
    "Voltage ratio": voltage_ratio,
    "Vector group": text("vector_group"),
    "Impedance voltage": impedance,
    "Off-circuit tap range": tap,
    "Insulation class / temperature rise": insulation,
    "Protection degree": text("protection_degree"),
    # 같은 제품의 온도 상승 행이 필요해 build()가 rated_power(value, rows)로 따로 부른다.
    "Rated power": lambda v: rated_power(v, {}),
    **{name: text("communication_interface") for name in COMMUNICATION},
}


def certifications(raw: str, datasheet: str | None) -> list[dict[str, object]]:
    """제품 마스터의 인증 목록을 비교용 인증으로 바꾼다."""
    items = []
    for entry in (e.strip() for e in raw.split(";") if e.strip()):
        variant = None
        match = re.search(r"\((-[A-Z]) variant\)", entry)
        if match:
            variant = match.group(1)
            entry = entry[: match.start()].strip()
        report = re.fullmatch(r"Type test report \((.+)\)", entry)
        if report:
            standard, kind = report.group(1), "TEST_REPORT"
        elif entry.endswith("type test report"):
            standard, kind = entry.removesuffix("type test report").strip(), "TEST_REPORT"
        else:
            # 등록·시험 구분이 적혀 있지 않은 인증 표시. 등록(Listed)으로도 시험 성적으로도 단정하지 않는다.
            standard, kind = entry, "MARK"
        item: dict[str, object] = {
            "standard": standard,
            "kind": kind,
            # 시험 성적은 데이터시트를 근거로 두고, 표시는 제품 마스터를 근거로 둔다.
            "evidence_id": datasheet if kind == "TEST_REPORT" and datasheet else "product_master",
        }
        if variant:
            item["variant"] = variant
        items.append(item)
    return items


def build(ref: Path) -> dict[str, object]:
    """제공 데이터에서 출력 JSON을 만든다.

    Args:
        ref: product_master.csv·product_spec_facts.csv가 있는 폴더.

    Returns:
        dict: own_spec_facts.json 내용. 원천에 없는 열이 있으면 KeyError, 값을 읽지 못하면 ValueError다.
    """
    with (ref / "product_master.csv").open(encoding="utf-8-sig", newline="") as handle:
        master = list(csv.DictReader(handle))
    with (ref / "product_spec_facts.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    products = []
    mapped = unmapped = 0
    for item in master:
        code = item["product_code"]
        own = [r for r in rows if r["product_code"] == code]
        by_attribute = {r["attribute"]: r["value"] for r in own}
        docs = [d for d in item["doc_ids"].split(";") if d]
        datasheet = next((d for d in docs if d.startswith("DS-")), None)
        facts: list[Fact] = []
        left: list[dict[str, str]] = []
        for row in own:
            rule = RULES.get(row["attribute"])
            if rule is None:
                left.append(
                    {
                        "source_attribute": row["attribute"],
                        "source_value": row["value"],
                        "evidence_id": row["doc_id"],
                        "section": row["section"],
                    }
                )
                unmapped += 1
                continue
            made = (
                rated_power(row["value"], by_attribute)
                if row["attribute"] == "Rated power"
                else rule(row["value"])
            )
            for one in made:
                facts.append(
                    {
                        **{k: v for k, v in one.items() if k != "note"},
                        "evidence_id": row["doc_id"],
                        "section": row["section"],
                        "source_value": row["value"],
                        **({"note": one["note"]} if one.get("note") else {}),
                    }
                )
            mapped += 1
        facts.append(
            {
                "attribute": "warranty_months",
                "value": item["warranty_months"],
                "unit": "month",
                "conditions": [],
                "evidence_id": "product_master",
                "section": "warranty_months",
                "source_value": f"warranty_months={item['warranty_months']}",
                "note": "제품 마스터의 표준 보증 개월 수(상업 조건 비교용)",
            }
        )
        products.append(
            {
                "product_code": code,
                "family": item["family"],
                "lifecycle_status": item["lifecycle_status"],
                "standards": [s.strip() for s in item["standards"].split(";") if s.strip()],
                "certifications": certifications(item["certifications"], datasheet),
                "spec_facts": facts,
                "unmapped_facts": left,
            }
        )
    return {
        "_note": (
            "KAN-186 자사 비교 입력. scripts/rfp_own_spec_facts.py가 제공 데이터 ref/product_master.csv·"
            "product_spec_facts.csv에서 만든다(손으로 고치지 않음). 협력사 프로필과 같은 열(속성·값·단위·조건·근거 ID·"
            "절)로 옮긴 파생값(derivation_kind=DERIVED)이고 source_value는 원래 문장, note는 바꾼 규칙이다. "
            "unmapped_facts는 비교 속성으로 옮기지 않은 원문 행이다. 실제 LS 제품 성능 자료가 아니라 교육용 합성 "
            "데이터시트다."
        ),
        "data_origin": "synthetic",
        "derivation_kind": "DERIVED",
        "coverage": {
            "products": len(products),
            "source_rows": len(rows),
            "mapped_rows": mapped,
            "unmapped_rows": unmapped,
        },
        "products": products,
    }


def rederived(fact_row: dict, rows: dict[str, str]) -> list[list[Fact]]:
    """사양 행의 원문(source_value)을 모든 규칙에 넣어 그 사실을 만드는 규칙의 출력을 모은다.

    Args:
        fact_row: 커밋된 사양 행.
        rows: 같은 제품의 원천 행(강제 공랭의 온도 상승을 읽는다).

    Returns:
        list[list[Fact]]: 그 행을 그대로 만든 규칙별 출력. 없으면 빈 목록이다.
    """
    keys = ("attribute", "value", "unit", "conditions", "note")
    wanted = {k: fact_row.get(k) for k in keys}
    outputs = []
    for name, rule in RULES.items():
        source = str(fact_row["source_value"])
        try:
            made = rated_power(source, rows) if name == "Rated power" else rule(source)
        except (ValueError, IndexError, AttributeError):
            continue  # 이 원문을 읽는 규칙이 아니다.
        if any({k: one.get(k) for k in keys} == wanted for one in made):
            outputs.append(made)
    return outputs


def stale_facts(data: dict) -> list[str]:
    """커밋된 JSON이 지금 RULES의 결과와 다른 곳을 찾는다. 제공 데이터 없이 CI에서 돈다.

    원천 속성 이름은 JSON에 없어 모든 규칙을 넣어 본다. 규칙이 행을 만들고, 그 규칙의 출력 전부가 같은 원문의 행에
    있어야 한다. 인증은 원문이 없어 여기서 보지 않는다(제공 데이터가 있는 --check가 본다).

    Args:
        data: own_spec_facts.json 내용.

    Returns:
        list[str]: 다른 곳 설명. 같으면 빈 목록이다.
    """
    problems: list[str] = []
    mapped = unmapped = 0
    for product in data["products"]:
        code = product["product_code"]
        facts = product["spec_facts"]
        rows = {
            "Insulation class / temperature rise": str(f["source_value"])
            for f in facts
            if f["attribute"] == "insulation_class"
        }
        for row in product["unmapped_facts"]:
            unmapped += 1
            if row["source_attribute"] in RULES:
                problems.append(f"{code}: 규칙이 생긴 미변환 행 {row['source_attribute']}")
        sources = set()
        for row in facts:
            if row["attribute"] == "warranty_months":
                if row["source_value"] != f"warranty_months={row['value']}":
                    problems.append(f"{code}: 보증 원문과 값이 다름")
                continue
            place = (row["evidence_id"], row["section"], row["source_value"])
            sources.add(place)
            same_source = [
                f for f in facts if (f["evidence_id"], f["section"], f["source_value"]) == place
            ]
            made = rederived(row, rows)
            if not any(
                all(
                    any(all(one.get(k) == f.get(k) for k in one) for f in same_source)
                    for one in output
                )
                for output in made
            ):
                problems.append(f"{code}: {row['attribute']} '{row['source_value']}'")
        mapped += len(sources)
    coverage = data["coverage"]
    if (
        coverage["products"] != len(data["products"])
        or coverage["unmapped_rows"] != unmapped
        or coverage["source_rows"] != coverage["mapped_rows"] + unmapped
        or coverage["mapped_rows"] != mapped
    ):
        problems.append("coverage 수가 행과 다름")
    return problems


def render(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", type=Path, required=True, help="제공 데이터 ref 폴더(읽기 전용)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="쓰지 않고 출력 파일과 같은지만 본다")
    args = parser.parse_args(argv)
    content = render(build(args.ref))
    if args.check:
        same = args.output.exists() and args.output.read_text(encoding="utf-8") == content
        print("RFP_OWN_SPEC_FACTS_UP_TO_DATE" if same else "RFP_OWN_SPEC_FACTS_STALE")
        return 0 if same else 1
    args.output.write_text(content, encoding="utf-8")
    coverage = json.loads(content)["coverage"]
    print(json.dumps(coverage, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
