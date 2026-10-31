"""사람 입력 제약. 미제공(null)과 명시된 목록/정책을 구분한다."""

import pytest
from pydantic import ValidationError

from backend.api.tft.rfp.schemas import ProjectCreate, ProjectUpdate


@pytest.mark.parametrize("model", [ProjectCreate, ProjectUpdate])
def test_constraints_default_unknown_and_only_accept_explicit_nonempty_values(model):
    base = (
        {"version": 1}
        if model is ProjectUpdate
        else {"name": "합성", "company_code": "HOST", "data_origin": "synthetic"}
    )
    unknown = model(**base)
    assert unknown.supply_region is None
    assert unknown.customer_manufacturer_list is None
    assert unknown.off_list_permission == "UNSTATED"
    known = model(
        **base,
        supply_region=" 한국 ",
        customer_manufacturer_list=[" 제조사 A "],
        off_list_permission="FORBIDDEN",
    )
    assert known.supply_region == "한국"
    assert known.customer_manufacturer_list == ["제조사 A"]
    assert known.off_list_permission == "FORBIDDEN"
    for bad in (
        {"customer_manufacturer_list": []},
        {"customer_manufacturer_list": [""]},
        {"customer_manufacturer_list": [" "]},
        {"customer_manufacturer_list": [None]},
        {"supply_region": " "},
        {"off_list_permission": None},
        {"off_list_permission": "허용"},
    ):
        with pytest.raises(ValidationError):
            model(**base, **bad)


def test_sparse_patch_preserves_omitted_inputs_and_explicit_null_clears_them():
    assert ProjectUpdate(version=2, name="이름만 변경").model_dump(exclude_unset=True) == {
        "version": 2,
        "name": "이름만 변경",
    }
    assert ProjectUpdate(version=2, supply_region=None, customer_manufacturer_list=None).model_dump(
        exclude_unset=True
    ) == {
        "version": 2,
        "supply_region": None,
        "customer_manufacturer_list": None,
    }


def test_existing_create_requests_do_not_gain_new_required_fields_in_api_schema():
    required = ProjectCreate.model_json_schema()["required"]
    assert not set(required) & {
        "supply_region",
        "customer_manufacturer_list",
        "off_list_permission",
    }


# API와 실제 DB CHECK가 같은 계약 사례를 독립적으로 검증한다.
CONSTRAINT_CASES = [
    pytest.param({}, True, id="omitted"),
    pytest.param({"supply_region": None, "customer_manufacturer_list": None}, True, id="null"),
    pytest.param({"supply_region": " \t\n"}, False, id="blank-region"),
    pytest.param({"supply_region": "\u00a0\u2003"}, False, id="unicode-blank-region"),
    pytest.param({"supply_region": "가" * 200}, True, id="region-limit"),
    pytest.param({"supply_region": "가" * 201}, False, id="region-over-limit"),
    pytest.param({"supply_region": " \t" + "가" * 200 + "\n "}, True, id="region-trimmed-length"),
    pytest.param({"customer_manufacturer_list": []}, False, id="empty-list"),
    pytest.param({"customer_manufacturer_list": ["A", " "]}, False, id="blank-item-after-valid"),
    pytest.param({"customer_manufacturer_list": ["\t\n"]}, False, id="blank-item"),
    pytest.param({"customer_manufacturer_list": ["\u00a0\u2003"]}, False, id="unicode-blank-item"),
    pytest.param({"customer_manufacturer_list": [None]}, False, id="null-item"),
    pytest.param({"customer_manufacturer_list": ["가" * 201]}, False, id="item-over-limit"),
    pytest.param(
        {"customer_manufacturer_list": [" \t" + "가" * 200 + "\n "]}, True, id="trimmed-item-limit"
    ),
    pytest.param({"customer_manufacturer_list": ["A"] * 100}, True, id="list-count-limit"),
    pytest.param({"customer_manufacturer_list": ["A"] * 101}, False, id="list-over-count"),
    pytest.param({"customer_manufacturer_list": [["A"]]}, False, id="nested-list"),
]


@pytest.mark.parametrize("fields,valid", CONSTRAINT_CASES)
def test_project_constraint_api_text_and_item_boundaries(fields, valid):
    base = {"name": "합성", "company_code": "HOST", "data_origin": "synthetic"}
    if valid:
        ProjectCreate(**base, **fields)
    else:
        with pytest.raises(ValidationError):
            ProjectCreate(**base, **fields)
