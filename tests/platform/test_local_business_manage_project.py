"""로컬 시연 관리자에게 RFP 프로젝트를 부여하는 서버 밖 등록 명령(KAN-188)."""

import pytest

from scripts.local_business import __main__ as cli


@pytest.mark.parametrize("number", ["P26-1", "x' OR 1=1 --", "P26-00021"])
def test_manage_project_rejects_anything_but_a_project_number(number, tmp_path):
    with pytest.raises(ValueError, match="LOCAL_PROJECT_NUMBER_INVALID"):
        cli.manage_project(tmp_path, number)


def test_manage_project_inserts_for_the_demo_manager(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "owned", lambda state: {})
    monkeypatch.setattr(
        cli, "docker", lambda *args: calls.append(args) or b"ea12bdd8-ea9c-4651-a4bf-9cd026685dc3\n"
    )
    cli.manage_project(tmp_path / "ns", "P26-0002")
    sql = calls[0][-1]
    assert calls[0][1] == "ns-pg"
    assert cli.DEMO_MANAGER in sql and "external_id = 'P26-0002'" in sql
