"""KAN-107 필요 정보 표와 부족 정보 계산(DB 없음)."""

from backend.engine.cs.required_info import missing_info, required_by_family

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
