"""KAN-199 2/2 RFP 처리 함수 등록 지점."""

from types import SimpleNamespace

import pytest

from backend.agent.processor_registry import PROCESSOR_MODULE, load_processor
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

SETTINGS = Settings(_env_file=None, environment="test")


def test_module_build_gives_the_processor():
    """고정 모듈의 build(settings)가 돌려준 처리 함수를 그대로 쓴다."""

    def processor(job, emit):
        return None

    names = []

    def module(name):
        names.append(name)
        return SimpleNamespace(build=lambda settings: processor)

    assert load_processor(SETTINGS, module) is processor
    assert names == [PROCESSOR_MODULE] == ["backend.api.tft.worker_processor"]


def test_missing_module_is_a_fixed_code_but_other_import_errors_surface():
    """모듈이 없으면 TFT_WORKER_PROCESSOR_MISSING이고, 모듈 안의 다른 import 실패는 숨기지 않는다."""
    with pytest.raises(ApplicationError) as error:
        load_processor(SETTINGS)
    assert error.value.code == "TFT_WORKER_PROCESSOR_MISSING"

    def inner_failure(name):
        raise ModuleNotFoundError("No module named 'somewhere_else'", name="somewhere_else")

    with pytest.raises(ModuleNotFoundError):
        load_processor(SETTINGS, inner_failure)
