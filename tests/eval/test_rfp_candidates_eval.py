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
            # 추출 v3 의미 칸: 비교는 attribute_key·equipment_family로 한다(KAN-199 #329).
            req(
                "2.6.A",
                QUOTE_A,
                "rated_max_voltage",
                "38",
                "kV",
                operator="GTE",
                attribute_key="rated_max_voltage",
                equipment_family="MV_EQUIP",
            ),
            req("2.6.B", QUOTE_B, "factory test", operator="TEXT", task="FACTORY_TEST"),
        ]
    )


OWN = [
    Product(
        product_code="OWN-15",
        # 자사 사양 계열 어휘(vocabulary.EquipmentFamily) 값이어야 계열 안 비교가 고른다.
        family="MV_EQUIP",
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


def mv_profile(code, **extra):
    return profile(code, product_family="15kV 퓨즈 부하개폐기반", **extra)


PROFILES = [
    mv_profile("P-A", facts=[fact("rated_max_voltage", "38", "kV")]),
    # 다른 속성 근거만 있어 정격 전압은 확인 필요(검토 후보).
    mv_profile("P-B", facts=[fact("bil", "150", "kV")]),
    mv_profile("P-Z", facts=[fact("rated_max_voltage", "38", "kV")]),
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
                    # 과업 요구는 어휘 키가 없어 정답 속성이 null이다(정답 작성 규칙).
                    {
                        "clause_no": "2.6.B",
                        "attribute": None,
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


def test_the_evaluation_compares_inside_the_family_like_the_processor():
    """평가는 운영과 같은 파이프라인을 잰다(compare_in_family): 다른 계열 제품의 충족 값은 근거가 아니다."""
    other_family = OWN[0].model_copy(
        update={
            "product_code": "OWN-LV",
            "family": "LV_BREAKER",
            "spec_facts": [
                f.model_copy(update={"product_code": "OWN-LV", "value": "40"})
                for f in OWN[0].spec_facts
            ],
        }
    )
    run = run_document(
        tree(), GOLD.documents[0], provider, [*OWN, other_family], PROFILES, date(2026, 11, 20)
    )
    assert [(r.clause_no, r.status) for r in run.predicted][0] == ("2.6.A", "SPEC_GAP")


def test_an_assembly_clause_reaches_the_integration_measure():
    """조항 표시(assembly_clause)도 통합 책임 측정의 근거다(운영 읽기 계산과 같은 입력, KAN-199)."""

    def marked(text):
        return gen(
            [
                req(
                    "2.6.A",
                    QUOTE_A,
                    "rated_max_voltage",
                    "38",
                    "kV",
                    operator="GTE",
                    attribute_key="rated_max_voltage",
                    equipment_family="MV_EQUIP",
                    assembly_clause=True,
                ),
                req("2.6.B", QUOTE_B, "factory test", operator="TEXT", task="FACTORY_TEST"),
            ]
        )

    # 통합 책임 능력이 확인되지 않은 후보. 조립품 조항이 없으면 확정 후보다.
    unsure = mv_profile("P-A", facts=[fact("rated_max_voltage", "38", "kV")], integration=None)
    run = run_document(tree(), GOLD.documents[0], marked, OWN, [unsure], date(2026, 11, 20))
    assert run.integration["basis_clause_nos"] == ["2.6.A", "2.6.B"]
    # 정답 맥락이 False여도 처리 함수처럼 추출 결과로 통합 책임을 본다(확인 전에는 확정 후보가 아니다).
    assert GOLD.documents[0].context.requires_integrator is False
    assert run.grades["P-A"] == "REVIEW"


def test_the_evaluation_grades_insufficient_requirements_like_the_processor():
    """근거 부족 요구도 평가에서 채점한다(처리 함수와 같은 대상)."""

    def unknown(text):
        return gen(
            [
                req(
                    "2.6.A",
                    QUOTE_A,
                    "BIL",
                    "38",
                    "kV",
                    operator="GTE",
                    attribute_key="bil",
                    equipment_family="MV_EQUIP",
                )
            ]
        )

    run = run_document(tree(), GOLD.documents[0], unknown, OWN, PROFILES, date(2026, 11, 20))
    assert {r.status for r in run.predicted} == {"INSUFFICIENT_EVIDENCE"}
    assert set(run.grades) == {"P-A", "P-B"}


def test_predictions_are_keyed_by_vocabulary_attribute_not_clause_wording():
    """정답 키는 어휘 키다. 추출의 attribute(원문 문구 "BIL")가 아니라 attribute_key("bil")로 맞추고,
    인증·규격은 certification, 어휘 밖(OTHER)은 null이다(KAN-191, 정답 v1 작성 규칙)."""

    def wording(text):
        return gen(
            [
                req(
                    "2.6.A",
                    QUOTE_A,
                    "BIL",
                    "38",
                    "kV",
                    operator="GTE",
                    attribute_key="bil",
                    equipment_family="MV_EQUIP",
                ),
                req(
                    "2.6.A",
                    QUOTE_A,
                    "IEEE C37.20.1 listed",
                    operator="TEXT",
                    kind="COMPLIANCE",
                    standard_system="IEEE",
                    attribute_key="OTHER",
                ),
                req("2.6.B", QUOTE_B, "factory test", operator="TEXT", task="FACTORY_TEST"),
            ]
        )

    gold = GoldFile.model_validate(
        {
            "gold_version": "synthetic-v1",
            "documents": [
                {
                    "document_number": 14,
                    "sections": ["2.6"],
                    "requirements": [
                        {
                            "clause_no": "2.6.A",
                            "attribute": "bil",
                            "expected_status": "INSUFFICIENT_EVIDENCE",
                            "necessity": "MANDATORY",
                        },
                        {
                            "clause_no": "2.6.A",
                            "attribute": "certification",
                            "expected_status": "INSUFFICIENT_EVIDENCE",
                            "necessity": "MANDATORY",
                        },
                        {
                            "clause_no": "2.6.B",
                            "attribute": None,
                            "expected_status": "INSUFFICIENT_EVIDENCE",
                            "necessity": "MANDATORY",
                        },
                    ],
                    "candidates": {},
                }
            ],
        }
    )
    run = run_document(tree(), gold.documents[0], wording, OWN, PROFILES, date(2026, 11, 20))
    assert [(r.clause_no, r.attribute) for r in run.predicted] == [
        ("2.6.A", "bil"),
        ("2.6.A", "certification"),
        ("2.6.B", None),
    ]
    summary = summarize(gold, [run], "FIRST")
    # 글자가 다른 원문 문구를 키로 썼다면 세 요구 모두 미추출로 세어 재현율이 0이 됐을 것이다.
    extraction = summary["extraction"]["extraction"]
    assert (extraction["recall_count"], extraction["precision_count"]) == ("3/3", "3/3")
    # 상태 일치율은 사유를 비교하지 않은 참고 값이다(정답 사유가 비어 있으면 게이트 값은 None).
    assert summary["extraction"]["status_match"]["count"] == "3/3"
    assert summary["extraction"]["status_match"]["reason_compared"] is False


def test_a_document_runs_the_processor_path_and_writes_the_adoption_result():
    run = run_document(tree(), GOLD.documents[0], provider, OWN, PROFILES, date(2026, 11, 20))
    assert run.section_outcomes == {"2.6": "VALID"}
    assert [(r.clause_no, r.status) for r in run.predicted][0] == ("2.6.A", "SPEC_GAP")
    # 정격 미달(SPEC_GAP) 요구만 협력사 등급 대상이다.
    assert run.grades["P-A"] == "MANDATORY_CONFIRMED" and run.grades["P-B"] == "REVIEW"
    assert run.unconfirmed["P-B"] == ["rated_max_voltage"]
    summary = summarize(GOLD, [run], "FIRST")
    # 14번 풀 밖 후보(P-Z)는 처리 함수처럼 채점하지 않는다.
    assert "P-Z" not in run.grades
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
    # 근거 정확성은 측정 도구가 없고, 초안 추적률은 초안을 넘겨야 잰다(NOT_EVALUABLE).
    assert row["rfp_evidence_accuracy"] is None and row["rfp_draft_trace_rate"] is None
    meta = data["meta"]
    # 개발 문서의 FIRST는 공식 점수가 아니다(공식은 평가 문서 첫 실행만).
    assert (meta["documents"], meta["official"], meta["counts"]["recall"]) == (1, False, "2/2")
    # 정답 사유가 없으면 상태 일치 수는 참고 값이고 게이트 값은 None이다.
    assert meta["counts"]["status_match"] == "2/2"
    assert meta["counts"]["status_match_reason_compared"] is False
    assert row["rfp_status_match_rate"] is None
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


def test_gold_document_numbers_must_be_unique():
    data = GOLD.model_dump()
    data["documents"] = data["documents"] * 2
    with pytest.raises(ValueError, match="EVAL_GOLD_DUPLICATE_DOCUMENT"):
        GoldFile.model_validate(data)


def test_a_result_appearing_during_the_run_is_not_overwritten(tmp_path):
    out = tmp_path / "result.json"
    assert rfp_candidate_run.write_new(out, "first") == out
    other = rfp_candidate_run.write_new(out, "second")
    assert other != out and other.parent == tmp_path
    assert (out.read_text(), other.read_text()) == ("first", "second")


def evaluation_run(tmp_path, monkeypatch, *extra, provider="gemini", patch_gemini=True, gold=None):
    """합성 평가 문서(10번)를 main까지 돌린다. 원문 해석은 합성 조항 트리, 'gemini' 공급자는 실제 호출 없는
    가짜 응답(fake_provider)으로 바꿔 끼운다. --provider fake는 공식 실행이 될 수 없어 공식 경로는 이렇게 본다."""
    data = manifest(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    gold = gold or GOLD.model_dump()
    gold["documents"][0]["document_number"] = 10
    (tmp_path / "gold.json").write_text(json.dumps(gold))
    if patch_gemini:
        monkeypatch.setattr(
            rfp_candidate_run, "gemini", lambda _env: ("model-x", rfp_candidate_run.fake_provider)
        )
    monkeypatch.setattr(rfp_candidate_run, "extract", lambda *_a, **_k: None)
    monkeypatch.setattr(rfp_candidate_run, "build_clause_tree", lambda _doc: tree())
    return rfp_candidate_run.main(
        [
            "--manifest", str(tmp_path / "manifest.json"),
            "--docs", str(tmp_path),
            "--documents", "10",
            "--gold", str(tmp_path / "gold.json"),
            "--provider", provider,
            "--confirm-evaluation",
            *extra,
        ]
    )  # fmt: skip


def test_the_official_first_run_is_recorded_once_before_the_model_is_called(
    tmp_path, monkeypatch, capsys
):
    ledger = tmp_path / "official.jsonl"
    first = ["--run-label", "FIRST", "--out"]
    # 공식 실행은 기록 파일 없이는 시작하지 않는다.
    assert evaluation_run(tmp_path, monkeypatch, *first, str(tmp_path / "a.json")) == 1
    assert "EVAL_OFFICIAL_LEDGER_REQUIRED" in capsys.readouterr().err
    assert not (tmp_path / "a.json").exists()
    with_ledger = ["--official-ledger", str(ledger)]
    assert (
        evaluation_run(tmp_path, monkeypatch, *first, str(tmp_path / "a.json"), *with_ledger) == 0
    )
    started, completed = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert (started["event"], completed["event"], started["document_numbers"]) == (
        "STARTED",
        "COMPLETED",
        [10],
    )
    result = (tmp_path / "a.json").read_bytes()
    assert completed["result_sha256"] == hashlib.sha256(result).hexdigest()
    assert json.loads(result)["meta"]["official"] is True
    # 같은 문서의 두 번째 FIRST는 모델을 부르기 전에 거부한다. 재측정은 참고값으로 돈다.
    assert (
        evaluation_run(tmp_path, monkeypatch, *first, str(tmp_path / "b.json"), *with_ledger) == 1
    )
    assert "EVAL_OFFICIAL_ALREADY_RECORDED" in capsys.readouterr().err
    assert not (tmp_path / "b.json").exists() and len(ledger.read_text().splitlines()) == 2
    again = ["--run-label", "REMEASURE", "--out", str(tmp_path / "c.json"), *with_ledger]
    assert evaluation_run(tmp_path, monkeypatch, *again) == 0
    assert json.loads((tmp_path / "c.json").read_text())["meta"]["official"] is False


def test_a_failed_first_run_still_uses_up_the_official_slot(tmp_path):
    ledger = tmp_path / "official.jsonl"
    claim = ("m1", [10], "2026-11-04T00:00:00+00:00", "abc")
    assert rfp_candidate_run.claim_official(ledger, *claim) is None
    # 완료 기록 없이 끝난(실패한) 첫 실행 뒤에도 다시 공식으로 돌 수 없다. 다른 manifest 판본은 별개다.
    assert rfp_candidate_run.claim_official(ledger, *claim) == "EVAL_OFFICIAL_ALREADY_RECORDED"
    assert rfp_candidate_run.claim_official(ledger, "m2", [10], "t", "abc") is None


def test_a_setup_failure_does_not_use_up_the_official_slot(tmp_path, monkeypatch, capsys):
    """모델 설정이 깨져 첫 호출 전에 실패하면 공식 기록을 남기지 않는다(#322 리뷰 P2)."""
    ledger = tmp_path / "official.jsonl"

    def broken(_env_file):
        raise KeyError("GEMINI_API_KEY")

    monkeypatch.setattr(rfp_candidate_run, "gemini", broken)
    args = ["--run-label", "FIRST", "--official-ledger", str(ledger)]
    with pytest.raises(KeyError):
        evaluation_run(
            tmp_path, monkeypatch, *args, "--out", str(tmp_path / "a.json"), patch_gemini=False
        )
    assert not ledger.exists() or not ledger.read_text().strip()
    # 고친 뒤 다시 돌리면 공식 실행이 된다.
    assert evaluation_run(tmp_path, monkeypatch, *args, "--out", str(tmp_path / "b.json")) == 0
    assert json.loads((tmp_path / "b.json").read_text())["meta"]["official"] is True


@pytest.mark.parametrize("line", ["{not json", '{"event": "STARTED"}', "[1]"])
def test_a_broken_ledger_line_stops_the_official_run(tmp_path, line):
    ledger = tmp_path / "official.jsonl"
    ledger.write_text(f"\n{line}\n")
    code = rfp_candidate_run.claim_official(ledger, "m1", [10], "t", "abc")
    assert code == "EVAL_OFFICIAL_LEDGER_CORRUPT"
    assert ledger.read_text() == f"\n{line}\n"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (
            lambda d: d["requirements"].append(d["requirements"][0]),
            "EVAL_GOLD_DUPLICATE_REQUIREMENT",
        ),
        (
            lambda d: d["candidates"]["P-B"].update(unconfirmed=None),
            "EVAL_GOLD_REVIEW_UNCONFIRMED_MISSING",
        ),
    ],
    ids=["duplicate-requirement", "review-without-unconfirmed"],
)
def test_gold_errors_stop_the_run_before_any_model_call_or_ledger_write(
    tmp_path, monkeypatch, capsys, change, code
):
    """채점 단계에서야 걸리던 정답 오류를 읽을 때 막아, 비용과 공식 실행 기회를 쓰지 않는다(Fable 2차 리뷰 P1)."""
    gold = GOLD.model_dump()
    change(gold["documents"][0])
    with pytest.raises(ValueError, match=code):
        GoldFile.model_validate(gold)
    called = []
    monkeypatch.setattr(
        rfp_candidate_run,
        "gemini",
        lambda _env: called.append(1) or ("m", rfp_candidate_run.fake_provider),
    )
    ledger = tmp_path / "official.jsonl"
    args = [
        "--run-label",
        "FIRST",
        "--official-ledger",
        str(ledger),
        "--out",
        str(tmp_path / "a.json"),
    ]
    assert evaluation_run(tmp_path, monkeypatch, *args, gold=gold, patch_gemini=False) == 1
    assert code in capsys.readouterr().err
    assert called == [] and not ledger.exists() and not (tmp_path / "a.json").exists()


def test_the_gold_precheck_scores_an_empty_run_before_the_model():
    from eval.rfp_candidates import precheck

    assert precheck(GOLD, [14], "FIRST") is None
    # 정답에 없는 문서는 채점할 수 없다.
    assert precheck(GOLD, [99], "FIRST") == "EVAL_GOLD_UNSCORABLE"


def test_an_evaluation_remeasure_needs_a_recorded_official_run(tmp_path, monkeypatch, capsys):
    """공식 실행 없이 재측정부터 돌려 점수를 본 뒤 FIRST로 돌리는 우회를 막는다(Fable 2차 리뷰 P1)."""
    ledger = tmp_path / "official.jsonl"
    remeasure = ["--run-label", "REMEASURE", "--official-ledger", str(ledger)]
    assert evaluation_run(tmp_path, monkeypatch, *remeasure, "--out", str(tmp_path / "a.json")) == 1
    assert "EVAL_OFFICIAL_NOT_RECORDED" in capsys.readouterr().err
    assert not (tmp_path / "a.json").exists() and not ledger.exists()
    # 기록 파일 없이도 돌릴 수 없다.
    plain = ["--run-label", "REMEASURE", "--out", str(tmp_path / "b.json")]
    assert evaluation_run(tmp_path, monkeypatch, *plain) == 1
    assert "EVAL_OFFICIAL_LEDGER_REQUIRED" in capsys.readouterr().err
    # 다른 manifest 판본의 기록은 이 문서의 공식 실행이 아니다.
    ledger.write_text(
        json.dumps({"event": "STARTED", "manifest_version": "other", "document_numbers": [10]})
        + "\n"
    )
    assert rfp_candidate_run.official_recorded(ledger, "m1", [10]) == "EVAL_OFFICIAL_NOT_RECORDED"
    assert rfp_candidate_run.official_recorded(ledger, "other", [10]) is None
    assert (
        rfp_candidate_run.official_recorded(ledger, "other", [10, 11])
        == "EVAL_OFFICIAL_NOT_RECORDED"
    )


def test_a_fake_provider_run_can_never_be_official(tmp_path, monkeypatch, capsys):
    """가짜 모델 경로 점검이 공식 실행 기회를 쓰지 않는다(Fable 2차 리뷰 P2). 공식 기록에는 모델 이름이 남는다."""
    ledger = tmp_path / "official.jsonl"
    first = ["--run-label", "FIRST", "--official-ledger", str(ledger), "--out"]
    assert (
        evaluation_run(tmp_path, monkeypatch, *first, str(tmp_path / "a.json"), provider="fake")
        == 1
    )
    assert "EVAL_FAKE_CANNOT_BE_OFFICIAL" in capsys.readouterr().err
    assert not ledger.exists()
    assert evaluation_run(tmp_path, monkeypatch, *first, str(tmp_path / "b.json")) == 0
    started, completed = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert started["model"] == completed["model"] == "model-x"


def test_summary_takes_the_processed_draft_for_the_trace_rate():
    """초안을 넘기면 게이트 값이 채워지고, 실행한 문서의 초안이 빠지면 거절한다."""
    run = run_document(tree(), GOLD.documents[0], provider, OWN, PROFILES, date(2026, 11, 1))
    number = str(GOLD.documents[0].document_number)
    draft = {
        "references": [["requirement", "r1"]],
        "sentences": [
            {
                "section": "OUR_RESPONSE",
                "evidence": [{"type": "requirement", "id": "r1"}],
                "unconfirmed": False,
            }
        ],
    }
    summary = summarize(GOLD, [run], "REMEASURE", {number: draft})
    assert summary["gates"]["rfp_draft_trace_rate"] == 1.0
    assert summary["draft_trace"]["denominator"] == 1
    with pytest.raises(ValueError, match="EVAL_INVALID_INPUT"):
        summarize(GOLD, [run], "REMEASURE", {})
