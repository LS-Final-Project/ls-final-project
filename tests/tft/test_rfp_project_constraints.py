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
