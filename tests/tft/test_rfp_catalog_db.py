"""KAN-246 제품 목록 경로를 HTTP로, 실제 마이그레이션의 전용 PostgreSQL에 tft_app으로 확인한다."""

# ruff: noqa: F811

import asyncio
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.api.tft.main import create_app
from backend.api.tft.partners.loader import load_document_pools, load_profiles, profile_row
from backend.api.tft.rfp import catalog, comparison, repository
from backend.api.tft.worker_processor import outside_pool
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings
from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401

SALES = Principal(user_id=uuid4(), roles=frozenset({"sales"}))
CS = Principal(user_id=uuid4(), roles=frozenset({"cs_agent"}))
FACT = {"attribute": "rated_current", "value": "630", "unit": "A"}


@pytest.fixture
def client(app_dsn):
    return TestClient(
        create_app(Settings(_env_file=None, environment="test", database_url=app_dsn))
    )


def _clean(conn):
    """이 파일이 만든 등록 협력사(R-?)·자사 판본(T-?)을 지운다. 공유 DB의 다른 시험은 시드 프로필만 본다."""
    conn.execute("DELETE FROM tft.partner_grades WHERE candidate_code LIKE 'R-%'")
    conn.execute("DELETE FROM tft.partner_profiles WHERE candidate_code LIKE 'R-%'")
    conn.execute("DELETE FROM tft.catalog_edits WHERE code LIKE 'R-%' OR code LIKE 'T-%'")
    conn.execute("DELETE FROM tft.own_products WHERE product_code LIKE 'T-%'")


@pytest.fixture
def seeded(admin):
    """시드 협력사 P-A를 넣고, 시험 앞뒤로 이 파일이 만든 행을 지운다."""
    profile = next(p for p in load_profiles() if p.candidate_code == "P-A")
    row = profile_row(profile)
    with psycopg.connect(admin) as conn:
        conn.execute(
            f"INSERT INTO tft.partner_profiles ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))}) "
            "ON CONFLICT DO NOTHING",
            list(row.values()),
        )
        _clean(conn)
    yield profile
    with psycopg.connect(admin) as conn:
        _clean(conn)


def call(client, method, path, who=SALES, body=None):
    return client.request(
        method, "/api/v1/tft/catalog" + path, json=body, headers=principal_header(who)
    )


def partner_body(**extra):
    return {
        "expected_version": 0,
        "display_name": "등록 협력사",
        "manufacturer_name": "등록 제조사",
        "product_family": "몰드 변압기",
        "products": [{"product_code": "T-TR1", "model": "TR-1000", "scope": ["SUPPLY"]}],
        "integrator_capable": {
            "assembly": None,
            "test": None,
            "integration": None,
            "warranty": None,
        },
        "spec_facts": [
            {
                **FACT,
                "product_code": "T-TR1",
                "conditions": None,
                "evidence_id": "업체 카탈로그",
                "section": "3",
            }
        ],
        "certifications": [],
        "supply_regions": ["KR"],
        **extra,
    }


def own_body(**extra):
    return {
        "expected_version": 0,
        "family": "LV_BREAKER",
        "lifecycle_status": "ACTIVE",
        "spec_facts": [FACT],
        **extra,
    }


def own_now(app_dsn):
    async def read():
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
            return await catalog.own_products(conn)

    return asyncio.run(read())


def test_own_products_list_file_rows_then_stack_versions(client, seeded, app_dsn):
    listed = call(client, "GET", "/own-products").json()["data"]
    base = comparison.load_own_products()
    assert [p["product_code"] for p in listed][: len(base)] == [p.product_code for p in base]
    first = listed[0]
    assert first["source"] in ("FILE", "REGISTERED")

    code = f"T-{uuid4().hex[:6]}"
    made = call(client, "PUT", f"/own-products/{code}", body=own_body())
    assert made.status_code == 200, made.text
    data = made.json()["data"]
    assert (data["version"], data["source"], data["edited_by"]) == (
        1,
        "REGISTERED",
        str(SALES.user_id),
    )
    assert data["spec_facts"][0]["product_code"] == code
    assert own_now(app_dsn)[-1].product_code == code

    stale = call(client, "PUT", f"/own-products/{code}", body=own_body())
    assert stale.status_code == 409
    gone = call(
        client,
        "PUT",
        f"/own-products/{code}",
        body=own_body(expected_version=1, lifecycle_status="DISCONTINUED"),
    )
    assert gone.json()["data"]["version"] == 2
    # 단종 판본은 목록에는 남고 비교 후보에서는 빠진다.
    assert code in {p["product_code"] for p in call(client, "GET", "/own-products").json()["data"]}
    assert code not in {p.product_code for p in own_now(app_dsn)}


def test_editing_a_file_product_overrides_it_in_place(seeded):
    base = comparison.load_own_products()
    row = {
        "product_code": base[1].product_code,
        "family": base[1].family,
        "lifecycle_status": "MATURE",
        "standards": [],
        "certifications": [],
        "spec_facts": [],
    }
    new = {**row, "product_code": "A-NEW"}
    merged = catalog.effective(base, [new, row])
    assert [p.product_code for p in merged] == [p.product_code for p in base] + ["A-NEW"]
    assert merged[1].lifecycle_status == "MATURE"


def test_partners_register_and_edit_as_new_versions(client, seeded):
    made = call(client, "PUT", "/partners/R-A", body=partner_body())
    assert made.status_code == 200, made.text
    profile = made.json()["data"]["profile"]
    assert (profile["profile_version"], profile["profile_source"], profile["data_origin"]) == (
        1,
        "registered",
        "real",
    )

    edited = call(
        client,
        "PUT",
        "/partners/R-A",
        body=partner_body(expected_version=1, display_name="이름 바꿈"),
    )
    assert edited.json()["data"]["profile"]["profile_version"] == 2
    listed = {
        p["profile"]["candidate_code"]: p for p in call(client, "GET", "/partners").json()["data"]
    }
    assert listed["R-A"]["profile"]["display_name"] == "이름 바꿈"
    assert listed["R-A"]["edited_by"] == str(SALES.user_id)
    assert listed["P-A"]["profile"]["profile_source"] == "synthetic"

    # 시드 협력사 수정도 새 판본(registered)으로 쌓고 시드 판본은 그대로 둔다.
    seed = call(
        client, "PUT", "/partners/P-A", body=partner_body(expected_version=seeded.profile_version)
    )
    assert seed.status_code == 409  # T-TR1은 이미 R-A가 쓴다.
    assert seed.json()["error"]["code"] == "TFT_PRODUCT_CODE_TAKEN"


def test_partner_input_is_checked(client, seeded):
    assert call(client, "PUT", "/partners/RA", body=partner_body()).status_code == 422
    bad_family = call(client, "PUT", "/partners/R-B", body=partner_body(product_family="없는 계열"))
    assert bad_family.status_code == 422
    stray = partner_body(
        spec_facts=[
            {**FACT, "product_code": "X", "conditions": None, "evidence_id": "e", "section": ""}
        ]
    )
    assert call(client, "PUT", "/partners/R-B", body=stray).status_code == 422
    assert (
        call(client, "PUT", "/partners/R-B", body=partner_body(expected_version=3)).status_code
        == 409
    )
    assert call(client, "GET", "/partners", who=CS).status_code == 403


def test_registered_partners_skip_document_pools(seeded):
    pooled = sorted({c for pool in load_document_pools().values() for c in pool})
    other = next(c for c in pooled if c != pooled[0])
    pool = frozenset({pooled[0]})
    seed = seeded.model_copy(update={"candidate_code": other})
    assert outside_pool(seed, pool)
    # 풀에 있는 시드 협력사는 화면에서 고쳐도 원래 풀을 따른다.
    assert outside_pool(seed.model_copy(update={"profile_source": "registered"}), pool)
    # 시드 프로필은 풀에 없으면 채점하지 않고(P-Z), 새로 등록한 협력사만 모든 문서에서 채점한다.
    assert outside_pool(seed.model_copy(update={"candidate_code": "Z-Z"}), pool)
    assert not outside_pool(
        seed.model_copy(update={"candidate_code": "Z-Z", "profile_source": "registered"}), pool
    )
    assert not outside_pool(seed, None)


def test_options_follow_the_vocabulary(client):
    data = call(client, "GET", "/options").json()["data"]
    assert "rated_current" in data["attributes"]
    assert "몰드 변압기" in data["partner_families"]
    assert data["lifecycle_statuses"] == ["ACTIVE", "MATURE", "DISCONTINUED"]


def test_editing_a_partner_retires_its_stored_grades_until_regrading(
    client, seeded, admin, chain, app_dsn
):
    """협력사를 고치면 이전 판본 등급은 기록으로 남고 현행 등급(LATEST_GRADES)에서 빠진다."""
    assert (
        call(
            client,
            "PUT",
            "/partners/R-C",
            body=partner_body(
                products=[{"product_code": "T-TR3", "model": "TR-3", "scope": ["SUPPLY"]}],
                spec_facts=[],
            ),
        ).status_code
        == 200
    )
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.partner_grades (id, project_id, candidate_code, profile_version, requirement_id, "
            "requirement_version, grade, reasons, exclusion_reasons, mandatory_check, data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,'R-C',1,%s,1,'REVIEW',%s,%s,%s,'synthetic','DERIVED',now())",
            (
                uuid4(),
                chain["project"],
                chain["requirement"],
                Jsonb(["사유"]),
                Jsonb([]),
                Jsonb({"passed": [], "failed": [], "unknown": []}),
            ),
        )

    def current():
        async def read():
            async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
                return {
                    g["candidate_code"]
                    for g in await repository.project_grades(conn, chain["project"])
                }

        return asyncio.run(read())

    assert "R-C" in current()
    body = partner_body(
        expected_version=1,
        products=[{"product_code": "T-TR3", "model": "TR-3", "scope": ["SUPPLY"]}],
        spec_facts=[],
    )
    assert call(client, "PUT", "/partners/R-C", body=body).status_code == 200
    assert "R-C" not in current()
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.partner_grades WHERE candidate_code='R-C' AND project_id=%s",
                (chain["project"],),
            ).fetchone()[0]
            == 1
        )
