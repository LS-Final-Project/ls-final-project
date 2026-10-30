"""KAN-191 RFP 후보 비교 하네스·실행기. 합성 조항·가짜 모델·합성 자사 제품·합성 협력사로 확인한다(평가 문서 미사용)."""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from backend.api.tft.rfp.comparison import Product
from backend.engine.documents.model import build_document
from backend.engine.rfp.clauses import build_clause_tree
from eval.adoption import RFP_METRIC_KEYS
from eval.rfp_candidates import GoldFile, result_file, run_document, summarize
from scripts import rfp_candidate_run
from tests.tft.test_partner_grading import fact, profile
from tests.tft.test_rfp_requirements import gen, p, req

QUOTE_A = "Rated maximum voltage shall be 38 kV"
QUOTE_B = "Perform factory testing of the assembled unit"


def tree():
    blocks = [
        p("PART 2 - PRODUCTS"),
        p("2.6 SWITCHGEAR"),
        p(f"A. {QUOTE_A}."),
        p(f"B. {QUOTE_B}."),
        p("2.7 METERING"),
        p("A. Provide a power meter."),
    ]
    return build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))


def provider(text):
    """평가 절 2.6만 부른다는 것도 함께 확인한다(2.7은 정답의 평가 절이 아니다)."""
    assert "power meter" not in text
    return gen(
        [
            req("2.6.A", QUOTE_A, "rated_max_voltage", "38", "kV", operator="GTE"),
            req("2.6.B", QUOTE_B, "factory test", operator="TEXT", task="FACTORY_TEST"),
        ]
    )


OWN = [
    Product(
        product_code="OWN-15",
        family="switchgear",
        lifecycle_status="ACTIVE",
        standards=[],
        certifications=[],
        spec_facts=[
            {
                "product_code": "OWN-15",
                "attribute": "rated_max_voltage",
                "value": "15",
                "unit": "kV",
                "conditions": None,
                "evidence_id": "OWN-DS",
                "section": "1",
            }
        ],
    )
]
PROFILES = [
    profile("P-A", facts=[fact("rated_max_voltage", "38", "kV")]),
    # 다른 속성 근거만 있어 정격 전압은 확인 필요(검토 후보).
    profile("P-B", facts=[fact("bil", "150", "kV")]),
    profile("P-Z", facts=[fact("rated_max_voltage", "38", "kV")]),
]
GOLD = GoldFile.model_validate(
    {
        "gold_version": "synthetic-v1",
        "documents": [
            {
                "document_number": 14,
                "sections": ["2.6"],
                "requirements": [
                    {
                        "clause_no": "2.6.A",
                        "attribute": "rated_max_voltage",
                        "expected_status": "SPEC_GAP",
                        "necessity": "MANDATORY",
                    },
                    {
                        "clause_no": "2.6.B",
                        "attribute": "factory test",
                        "expected_status": "INSUFFICIENT_EVIDENCE",
                        "necessity": "MANDATORY",
                    },
                ],
                "candidates": {
                    "P-A": {"grade": "MANDATORY_CONFIRMED"},
                    "P-B": {"grade": "REVIEW", "unconfirmed": ["rated_max_voltage"]},
                },
                "assembly_clauses": ["2.6.B"],
            }
        ],
    }
)


def test_a_document_runs_the_processor_path_and_writes_the_adoption_result():
    run = run_document(tree(), GOLD.documents[0], provider, OWN, PROFILES, date(2026, 11, 20))
    assert run.section_outcomes == {"2.6": "VALID"}
    assert [(r.clause_no, r.status) for r in run.predicted][0] == ("2.6.A", "SPEC_GAP")
    # 정격 미달(SPEC_GAP) 요구만 협력사 등급 대상이다.
    assert run.grades["P-A"] == "MANDATORY_CONFIRMED" and run.grades["P-B"] == "REVIEW"
    assert run.unconfirmed["P-B"] == ["rated_max_voltage"]
    summary = summarize(GOLD, [run], "FIRST")
    # 정답에 없는 후보(P-Z)는 채점하지 않는다.
    assert summary["grades"]["mandatory_confirmed_count"] == "1/1"
    assert summary["grades"]["review_unconfirmed_listed"]["count"] == "1/1"
    assert summary["integration"]["count"] == "1/1"
    data = result_file(
        summary,
        [run],
        name="rfp-fake",
        model="fake",
        run_label="FIRST",
        split="development",
        manifest_version="m1",
        gold_version=GOLD.gold_version,
        started_at="2026-10-30T00:00:00+00:00",
        git_sha="abc",
    )
    row = data["result"]["candidates"][0]
    assert tuple(k for k in row if k != "name") == RFP_METRIC_KEYS
    assert (
        row["rfp_requirement_recall"] == 1.0 and row["rfp_partner_recommendation_precision"] == 1.0
    )
    assert row["rfp_integration_responsibility_rate"] == 1.0
    # 측정 도구가 없는 두 키는 None(NOT_EVALUABLE)이다.
    assert row["rfp_evidence_accuracy"] is None and row["rfp_draft_trace_rate"] is None
    meta = data["meta"]
    # 개발 문서의 FIRST는 공식 점수가 아니다(공식은 평가 문서 첫 실행만).
    assert (meta["documents"], meta["official"], meta["counts"]["recall"]) == (1, False, "2/2")
    # 결과에는 원문 인용·값을 넣지 않는다.
    dumped = json.dumps(data, ensure_ascii=False)
    assert QUOTE_A not in dumped and QUOTE_B not in dumped and '"38"' not in dumped


def manifest(tmp_path, split="development"):
    document = tmp_path / "doc.docx"
    document.write_bytes(b"synthetic bytes")
    entry = {
        "document_number": 14,
        "filename": "doc.docx",
        "sha256": hashlib.sha256(b"synthetic bytes").hexdigest(),
        "split": split,
    }
    return {
        "manifest_version": "m1",
        "documents": [entry, {**entry, "document_number": 10, "split": "evaluation"}],
    }


@pytest.mark.parametrize(
    ("numbers", "confirm", "out_name", "code"),
    [
        ([14], False, "exists.json", "EVAL_OUTPUT_EXISTS"),
        ([99], False, "r.json", "EVAL_DOCUMENT_NOT_IN_MANIFEST"),
        ([14, 10], True, "r.json", "EVAL_SPLIT_MIXED"),
        ([10], False, "r.json", "EVAL_EVALUATION_NOT_CONFIRMED"),
        ([10], True, "r.json", "EVAL_GOLD_DOCUMENT_MISSING"),
        ([14, 14], False, "r.json", "EVAL_DOCUMENTS_INVALID"),
        ([14], False, "r.json", None),
    ],
)
def test_inputs_are_checked_before_any_model_call(tmp_path, numbers, confirm, out_name, code):
    (tmp_path / "exists.json").write_text("{}")
    found = rfp_candidate_run.check_inputs(
        manifest(tmp_path), numbers, GOLD, tmp_path / out_name, confirm_evaluation=confirm
    )
    assert found == code


def test_results_never_go_inside_the_repository_and_hashes_must_match(tmp_path):
    inside = Path(rfp_candidate_run.ROOT) / "eval" / "rfp-result.json"
    assert (
        rfp_candidate_run.check_inputs(
            manifest(tmp_path), [14], GOLD, inside, confirm_evaluation=False
        )
        == "EVAL_OUTPUT_INSIDE_REPOSITORY"
    )
    entry = manifest(tmp_path)["documents"][0]
    assert rfp_candidate_run.read_verified(tmp_path, entry) == b"synthetic bytes"
    (tmp_path / "doc.docx").write_bytes(b"changed")
    with pytest.raises(ValueError, match="EVAL_DOCUMENT_HASH_MISMATCH"):
        rfp_candidate_run.read_verified(tmp_path, entry)
