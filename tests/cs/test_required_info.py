"""KAN-107 필요 정보 표와 부족 정보 계산(DB 없음)."""

from backend.engine.cs.required_info import blocking_by_family, missing_info, required_by_family

ROW = {"DEFECT": frozenset({"product_model", "serial_no", "photos"})}


def test_missing_is_required_minus_provided_minus_already_asked():
    """초안 입력과 같은 식: 필요 − 준 정보 − 이미 고객에게 요청한 정보. 표 밖 코드는 셈에 영향이 없다."""
    result = missing_info(
        "DEFECT",
        ["serial_no", "invoice_scan"],
        required_by_category=ROW,
        already_asked=["photos"],
    )
    assert result is not None
    assert (result.required, result.provided, result.already_asked, result.missing) == (
        ("photos", "product_model", "serial_no"),
        ("invoice_scan", "serial_no"),
        ("photos",),
        ("product_model",),
    )


def test_unknown_category_needs_nothing_and_no_category_cannot_be_counted():
    """표에 없는 유형은 필요 정보가 없다. 주분류가 없으면 셀 수 없다(None)."""
    other = missing_info("TECH_SPEC", None, required_by_category=ROW, already_asked=())
    assert other is not None and (other.required, other.missing) == ((), ())
    assert missing_info(None, ["serial_no"], required_by_category=ROW, already_asked=()) is None


def test_the_policy_table_has_a_row_per_family_and_category():
    table = required_by_family()
    assert table["TRANSFORMER"]["WARRANTY"] == {
        "serial_no",
        "purchase_doc",
        "install_date",
        "failure_description",
        "photos",
    }


def test_blocking_missing_is_the_missing_part_of_the_blocking_fields():
    """기획서 4.1 ②: 처리를 막는 누락만 따로 센다. 막는 정보도 이미 받았거나 요청했으면 세지 않는다."""
    blocking = {"DEFECT": frozenset({"serial_no", "product_model"})}
    only_optional = missing_info(
        "DEFECT",
        ["serial_no", "product_model"],
        required_by_category=ROW,
        already_asked=(),
        blocking_by_category=blocking,
    )
    assert only_optional is not None
    assert (only_optional.missing, only_optional.blocking_missing) == (("photos",), ())
    both = missing_info(
        "DEFECT",
        ["photos"],
        required_by_category=ROW,
        already_asked=["product_model"],
        blocking_by_category=blocking,
    )
    assert both is not None
    assert (both.missing, both.blocking_missing) == (("serial_no",), ("serial_no",))
    # 막는 정보 표를 주지 않으면 비어 있다(초안 입력 등 기존 호출).
    plain = missing_info("DEFECT", [], required_by_category=ROW, already_asked=())
    assert plain is not None and plain.blocking_missing == ()


def test_the_policy_blocking_fields_are_part_of_the_required_fields():
    required, blocking = required_by_family(), blocking_by_family()
    assert blocking["METER"]["DEFECT"] == {"serial_no", "error_code"}
    for family, rows in blocking.items():
        for category, codes in rows.items():
            assert codes <= required[family][category], (family, category)
