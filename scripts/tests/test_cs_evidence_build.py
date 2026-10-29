"""KAN-144 근거 스니펫 생성기. 합성 manifest와 검색기 대역으로 질의·스니펫·출력 형식을 검사한다. DB 없음."""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg

from backend.engine.cs.drafting import EvidenceSnippet
from backend.engine.search.model import Candidate
from scripts import cs_evidence_build
from scripts.tests.test_cs_draft_run import write_csv

SERIAL = "ZX-SECRET-77"
SECRET_DSN = "postgresql://secret-user:secret-pass@db.invalid/cs"


def candidate(chunk_id, text, position, sha="a"):
    """FTS 후보 하나.

    Args:
        chunk_id: 청크 ID.
        text: 청크 본문(None이면 본문 없음).
        position: 순위.
        sha: 문서 해시 반복 문자.

    Returns:
        Candidate: FTS 후보.
    """
    return Candidate(
        chunk_id=chunk_id,
        document_sha256=sha * 64,
        ordinal=position,
        section_path=["Warranty", chunk_id],
        snippet=f"<b>{chunk_id}</b>",
        text_excerpt=text,
        kind="TEXT",
        table_title=None,
        page_start=None,
        source="FTS",
        position=position,
        score=1.0 / position,
    )


class FakeSearcher:
    """받은 질의·조건을 기록하고 정해진 후보를 돌려주는 검색기 대역."""

    def __init__(self, results=None, error=None):
        """대역을 만든다.

        Args:
            results: 질의에 들어 있는 낱말 → 후보 목록. 맞는 낱말이 없으면 빈 목록이다.
            error: 호출 때 낼 예외.
        """
        self.results = results or {}
        self.error = error
        self.calls = []

    def __call__(self, query, filters):
        """질의를 기록하고 후보를 돌려준다.

        Args:
            query: 검색어.
            filters: 검색 조건.

        Returns:
            list[Candidate]: 후보.
        """
        if self.error:
            raise self.error
        self.calls.append((query, filters))
        return next((rows for word, rows in self.results.items() if word in query), [])


class FilteredSearcher:
    """조건의 document_sha256s가 있으면 제품 후보를, 없으면 전체 후보를 돌려주는 검색기 대역."""

    def __init__(self, *, product, everything):
        """대역을 만든다.

        Args:
            product: 제품 문서 제한 검색 결과.
            everything: 전역 검색 결과.
        """
        self.product = product
        self.everything = everything
        self.calls = []

    def __call__(self, query, filters):
        """조건을 기록하고 후보를 돌려준다.

        Args:
            query: 검색어.
            filters: 검색 조건.

        Returns:
            list[Candidate]: 후보(문서 범위 안의 것만).
        """
        self.calls.append((query, filters))
        rows = (
            self.product
            if filters.document_sha256s and "a" * 64 in filters.document_sha256s
            else self.everything
        )
        allowed = filters.document_sha256s
        return [row for row in rows if allowed is None or row.document_sha256 in allowed]


class EvidenceBuildTests(unittest.TestCase):
    def setUp(self):
        """합성 manifest(train 3건)와 대상 ID 파일을 만든다.

        Returns:
            None: 경로를 준비한다.
        """
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        bodies = {
            "T1": f"Breaker tripped twice, warranty claim. serial: {SERIAL}",
            "T2": "Unrelated packaging question",
            "T3": "a",
        }
        tickets = [
            {
                "ticket_id": ticket_id,
                "data_split": "train",
                "channel": "EMAIL",
                "body_language": "en",
                "inquiry_subject": "Warranty" if ticket_id == "T1" else "",
                "inquiry_body": body,
            }
            for ticket_id, body in bodies.items()
        ]
        self.write_manifest(["train"], write_csv(self.root / "t.csv", list(tickets[0]), tickets))
        (self.root / "ids.txt").write_text("T1\nT2\nT3\n", encoding="utf-8")
        self.env = patch.dict(os.environ, {"DATABASE_URL": SECRET_DSN})
        self.env.start()

    def tearDown(self):
        """환경변수와 임시 디렉터리를 정리한다.

        Returns:
            None: 정리만 한다.
        """
        self.env.stop()
        self.temp.cleanup()

    def write_manifest(self, splits, raw):
        """manifest를 쓴다.

        Args:
            splits: 선언 분할.
            raw: tickets CSV 바이트.

        Returns:
            None: m.json을 쓴다.
        """
        (self.root / "m.json").write_text(
            json.dumps(
                {
                    "tickets": {
                        "path": "t.csv",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "splits": splits,
                        "rows": 3,
                    }
                }
            )
        )

    def run_main(self, *extra, searcher=None):
        """main을 부르고 종료 코드와 표준 출력을 돌려준다.

        Args:
            *extra: 추가 인자.
            searcher: 검색기 대역(없으면 빈 결과 대역).

        Returns:
            tuple[int, str]: 종료 코드, 표준 출력.
        """
        fake = searcher or FakeSearcher()
        argv = [
            "--manifest", str(self.root / "m.json"),
            "--ticket-ids", str(self.root / "ids.txt"),
            "--index-version", "cs-chunks-v1",
            "--out", str(self.root / "ev.json"),
            *extra,
        ]  # fmt: skip
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = cs_evidence_build.main(argv, searcher=lambda version: fake)
        return code, printed.getvalue()

    def test_query_terms_build_a_masked_or_query(self):
        """질의는 2자 이상 토큰을 중복 없이 OR로 잇고 마스킹 표식·'or'·41번째 이후 토큰을 뺀다.

        Returns:
            None: 질의 문자열을 확인한다.
        """
        self.assertEqual(
            cs_evidence_build.query_terms(
                "Trip [SERIAL_PROVIDED]",
                "trip or a b-c Breaker [PERSONAL_PROVIDED]",
                "subject+body",
            ),
            "Trip or Breaker",
        )
        self.assertEqual(cs_evidence_build.query_terms("Only", "body text", "subject"), "Only")
        self.assertEqual(cs_evidence_build.query_terms(None, "body text", "subject"), "")
        many = " ".join(f"w{i:02d}" for i in range(50))
        self.assertEqual(len(cs_evidence_build.query_terms(None, many, "body").split(" or ")), 40)

    def test_writes_draft_evidence_without_blocked_chunks_or_query_text(self):
        """FLT-042 청크는 빠지고 본문 앞부분이 스니펫이 되며, 질의는 마스킹되고 출력에 남지 않는다.

        Returns:
            None: 근거 파일·검색 호출·meta를 확인한다.
        """
        searcher = FakeSearcher(
            {
                "Breaker": [
                    candidate("blocked", "Use FLT-042 reset steps.", 1),
                    candidate("c1", "Warranty period is 18 months from delivery.", 2),
                    candidate("no-text", None, 3),
                    candidate("c2", "Claims need the serial plate photo.", 4, sha="b"),
                    candidate("c3", "Third useful chunk.", 5),
                ]
            }
        )
        code, printed = self.run_main(
            "--limit-per-ticket",
            "2",
            "--snippet-chars",
            "15",
            "--meta-out",
            str(self.root / "meta.json"),
            searcher=searcher,
        )
        self.assertEqual(code, 0)
        evidence = json.loads((self.root / "ev.json").read_text(encoding="utf-8"))
        self.assertEqual(list(evidence), ["T1", "T2", "T3"])
        self.assertEqual([row["chunk_id"] for row in evidence["T1"]], ["c1", "c2"])
        self.assertEqual(evidence["T1"][0]["snippet"], "Warranty period")
        for row in evidence["T1"]:
            EvidenceSnippet.model_validate(row)
        self.assertEqual((evidence["T2"], evidence["T3"]), ([], []))
        queries = [query for query, _ in searcher.calls]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(" or " in query and "SECRET" not in query for query in queries))
        self.assertEqual({filters.limit for _, filters in searcher.calls}, {12})
        meta = json.loads(printed)
        self.assertEqual(json.loads((self.root / "meta.json").read_text(encoding="utf-8")), meta)
        self.assertNotIn("T1", meta)
        self.assertEqual(
            {key: meta[key] for key in ("tickets", "with_evidence", "empty", "input_failed")},
            {"tickets": 3, "with_evidence": 1, "empty": 2, "input_failed": 1},
        )
        self.assertEqual(meta["dropped_blocked"], 1)
        self.assertEqual(
            meta["queries_sha256"],
            hashlib.sha256("\n".join(f"{t}\t{q}" for t, q in zip(["T1", "T2"], queries))
                           .encode()).hexdigest(),
        )  # fmt: skip
        written = (self.root / "ev.json").read_text(encoding="utf-8") + printed
        self.assertNotIn("SECRET", written)
        self.assertNotIn("tripped", written)
        self.assertEqual(os.stat(self.root / "ev.json").st_mode & 0o777, 0o600)

    def test_ticket_ids_accept_json_list_and_label_keys(self):
        """대상 ID는 JSON 배열과 라벨 JSON 객체의 키도 받는다.

        Returns:
            None: 출력 키를 확인한다.
        """
        for content in (["T2"], {"T2": {"category": "X"}}):
            with self.subTest(content=content):
                (self.root / "ids.txt").write_text(json.dumps(content), encoding="utf-8")
                self.assertEqual(self.run_main()[0], 0)
                evidence = json.loads((self.root / "ev.json").read_text(encoding="utf-8"))
                self.assertEqual(evidence, {"T2": []})

    def test_invalid_inputs_and_db_errors_exit_without_details(self):
        """모르는·중복 ID, 범위 밖 인자, DB 오류는 종료 코드 1이며 DSN·오류 원문을 출력하지 않는다.

        Returns:
            None: 종료 코드와 출력을 확인한다.
        """
        cases = [
            ("T1\nT9\n", (), None),
            ("T1\nT1\n", (), None),
            ("T1\n", ("--limit-per-ticket", "91"), None),
            ("T1\n", ("--snippet-chars", "0"), None),
            ("T1\n", (), FakeSearcher(error=psycopg.OperationalError(SECRET_DSN))),
        ]
        for ids, extra, searcher in cases:
            with self.subTest(ids=ids, extra=extra):
                (self.root / "ids.txt").write_text(ids, encoding="utf-8")
                code, printed = self.run_main(*extra, searcher=searcher)
                self.assertEqual(code, 1)
                self.assertNotIn("secret", printed)
                self.assertFalse((self.root / "ev.json").exists())

    def test_empty_document_sha256s_items_are_rejected(self):
        """--document-sha256s의 빈 항목(','·'a,,b'·빈 문자열)은 검색하지 않고 종료 코드 1이다(KAN-144 이연 P3).

        Returns:
            None: 종료 코드·검색 미호출·출력 파일 미생성을 확인한다.
        """
        (self.root / "ids.txt").write_text("T1\n", encoding="utf-8")
        for value in (",", "a" * 64 + ",," + "b" * 64, ""):
            with self.subTest(value=value):
                searcher = FakeSearcher()
                code, _ = self.run_main("--document-sha256s", value, searcher=searcher)
                self.assertEqual(code, 1)
                self.assertEqual(searcher.calls, [])
                self.assertFalse((self.root / "ev.json").exists())
        code, _ = self.run_main("--document-sha256s", "a" * 64, searcher=FakeSearcher())
        self.assertEqual(code, 0)

    def test_default_searcher_needs_database_url(self):
        """주입이 없으면 DATABASE_URL이 필요하고, 없으면 종료 코드 1이다.

        Returns:
            None: 종료 코드를 확인한다.
        """
        os.environ.pop("DATABASE_URL")
        argv = [
            "--manifest", str(self.root / "m.json"),
            "--ticket-ids", str(self.root / "ids.txt"),
            "--index-version", "cs-chunks-v1",
            "--out", str(self.root / "ev.json"),
        ]  # fmt: skip
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cs_evidence_build.main(argv), 1)

    def write_products(self):
        """제품 열이 있는 manifest(T1=P-1, T2=P-9, T3=P-1)와 색인 manifest를 쓴다.

        색인 문서: P-1 폴더 문서(a), 다른 제품(b), 이웃 코드 P-10(c), 적재 실패(d).

        Returns:
            None: m.json·t.csv·index.json을 쓴다.
        """
        bodies = {"T1": "Breaker warranty claim", "T2": "Breaker warranty question", "T3": "a"}
        products = {"T1": "P-1", "T2": "P-9", "T3": "P-1"}
        tickets = [
            {
                "ticket_id": ticket_id,
                "data_split": "train",
                "channel": "EMAIL",
                "body_language": "en",
                "inquiry_subject": "",
                "inquiry_body": body,
                "product_code": products[ticket_id],
            }
            for ticket_id, body in bodies.items()
        ]
        self.write_manifest(["train"], write_csv(self.root / "t.csv", list(tickets[0]), tickets))
        documents = [
            {"source_name": "P-1/DS-P-1.md", "sha256": "a" * 64, "reason": None},
            {"source_name": "P-2/DS-P-2.md", "sha256": "b" * 64, "reason": None},
            {"source_name": "P-10/DS-P-10.md", "sha256": "c" * 64, "reason": None},
            {"source_name": "P-1/IG-P-1.md", "sha256": None, "reason": "CS_INDEX_EXTRACT_FAILED"},
        ]
        (self.root / "index.json").write_text(json.dumps({"documents": documents}))

    def test_product_documents_are_searched_first_then_global_fills(self):
        """제품 문서 제한 검색을 먼저 하고 모자라면 전역으로 채우며(중복 제외), 제품 문서 없는 문의는 전역만 쓴다.

        Returns:
            None: 검색 호출 조건·근거 순서·meta 통계를 확인한다.
        """
        self.write_products()
        searcher = FilteredSearcher(
            product=[candidate("p1", "Product warranty text.", 1, sha="a")],
            everything=[
                candidate("g1", "Global policy text.", 1, sha="b"),
                candidate("p1", "Product warranty text.", 2, sha="a"),
                candidate("g2", "Neighbour product text.", 3, sha="c"),
            ],
        )
        code, printed = self.run_main(
            "--index-manifest", str(self.root / "index.json"), "--limit-per-ticket", "3",
            searcher=searcher,
        )  # fmt: skip
        self.assertEqual(code, 0, printed)
        evidence = json.loads((self.root / "ev.json").read_text(encoding="utf-8"))
        self.assertEqual([row["chunk_id"] for row in evidence["T1"]], ["p1", "g1", "g2"])
        self.assertEqual([row["chunk_id"] for row in evidence["T2"]], ["g1", "p1", "g2"])
        self.assertEqual(
            [filters.document_sha256s for _, filters in searcher.calls], [["a" * 64], None, None]
        )
        meta = json.loads(printed)
        self.assertEqual(
            {k: meta[k] for k in ("product_filter", "tickets_with_product_docs", "product_hits", "product_top1")},
            {"product_filter": True, "tickets_with_product_docs": 2, "product_hits": 1, "product_top1": 1},
        )  # fmt: skip

    def test_classification_reserves_one_policy_slot_only_for_policy_categories(self):
        """--classification이 RETURN이면 색인 manifest의 POL-RETURN에서 정책 청크 1개를 limit 안에 넣고, 다른 유형은 그대로다.

        Returns:
            None: 근거 순서·예약 검색 조건·meta를 확인한다.
        """
        self.write_products()
        index = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        index["documents"] += [
            {
                "source_name": "policy/POL-RETURN.md",
                "doc_id": "POL-RETURN",
                "sha256": "e" * 64,
                "reason": None,
            },
            {
                "source_name": "policy/POL-ORDER.md",
                "doc_id": "POL-ORDER",
                "sha256": "f" * 64,
                "reason": None,
            },
        ]
        (self.root / "index.json").write_text(json.dumps(index), encoding="utf-8")
        reserved = []

        def searcher(query, filters):
            if filters.document_kinds == ["POL"]:
                reserved.append(filters.document_sha256s)
                return [
                    candidate("pol", "Return policy text.", 1, sha=filters.document_sha256s[0][0])
                ]
            return [candidate(f"p{n}", "Product text.", n, sha="a") for n in range(1, 4)]

        labels = self.root / "labels.json"
        labels.write_text(
            json.dumps({"T1": {"category_code": "RETURN"}, "T2": {"category_code": "TECH_SPEC"}}),
            encoding="utf-8",
        )
        code, printed = self.run_main(
            "--classification", str(labels), "--limit-per-ticket", "3", searcher=searcher
        )
        self.assertEqual(code, 1, printed)  # --classification은 --index-manifest가 있어야 한다.
        code, printed = self.run_main(
            "--classification", str(labels), "--index-manifest", str(self.root / "index.json"),
            "--limit-per-ticket", "3", "--no-product-filter", searcher=searcher,
        )  # fmt: skip
        self.assertEqual(code, 0, printed)
        evidence = json.loads((self.root / "ev.json").read_text(encoding="utf-8"))
        self.assertEqual([row["chunk_id"] for row in evidence["T1"]], ["p1", "p2", "pol"])
        self.assertEqual([row["chunk_id"] for row in evidence["T2"]], ["p1", "p2", "p3"])
        self.assertEqual(reserved, [["e" * 64]])
        meta = json.loads(printed)
        self.assertEqual((meta["policy_slot"], meta["reserved_tickets"]), (True, 1))

    def test_enough_product_hits_skip_the_global_search_and_filter_can_be_turned_off(self):
        """제품 문서 결과로 상한을 채우면 전역 검색을 하지 않고, --no-product-filter는 전역만 쓰며 통계는 낸다.

        Returns:
            None: 호출 수와 meta를 확인한다.
        """
        self.write_products()
        searcher = FilteredSearcher(
            product=[candidate("p1", "Product warranty text.", 1, sha="a")],
            everything=[
                candidate("g1", "Global policy text.", 1, sha="b"),
                candidate("p1", "Product warranty text.", 2, sha="a"),
            ],
        )
        args = ("--index-manifest", str(self.root / "index.json"), "--limit-per-ticket", "1")
        code, printed = self.run_main(*args, searcher=searcher)
        self.assertEqual(code, 0, printed)
        self.assertEqual(
            [filters.document_sha256s for _, filters in searcher.calls], [["a" * 64], None]
        )
        searcher.calls.clear()
        code, printed = self.run_main(*args, "--no-product-filter", searcher=searcher)
        self.assertEqual(code, 0, printed)
        self.assertEqual([filters.document_sha256s for _, filters in searcher.calls], [None, None])
        evidence = json.loads((self.root / "ev.json").read_text(encoding="utf-8"))
        self.assertEqual([row["chunk_id"] for row in evidence["T1"]], ["g1"])
        meta = json.loads(printed)
        self.assertEqual(
            (meta["product_filter"], meta["product_hits"], meta["product_top1"]), (False, 0, 0)
        )
        self.assertEqual(self.run_main("--no-product-filter")[0], 1)

    def test_document_scope_limits_both_searches_and_bad_index_manifest_fails(self):
        """--document-sha256s를 주면 제품 집합은 교집합이고, 색인 manifest 형식이 틀리면 종료 코드 1이다.

        Returns:
            None: 호출 조건과 종료 코드를 확인한다.
        """
        self.write_products()
        searcher = FilteredSearcher(product=[], everything=[])
        args = ("--index-manifest", str(self.root / "index.json"))
        code, printed = self.run_main(*args, "--document-sha256s", "b" * 64, searcher=searcher)
        self.assertEqual(code, 0, printed)
        self.assertEqual(
            [filters.document_sha256s for _, filters in searcher.calls], [["b" * 64], ["b" * 64]]
        )
        self.assertEqual(json.loads(printed)["tickets_with_product_docs"], 0)
        (self.root / "index.json").write_text(json.dumps({"documents": [{"sha256": "a"}]}))
        self.assertEqual(self.run_main(*args)[0], 1)

    def test_product_document_match_uses_whole_path_parts(self):
        """제품 문서는 경로 조각 일치나 '-<코드>'로 끝나는 파일 이름으로만 판정한다.

        Returns:
            None: 판정 표를 확인한다.
        """
        for name, code, expected in [
            ("MCCB-ABS-103c/DS-MCCB-ABS-103c.md", "MCCB-ABS-103c", True),
            ("MCCB-ABS-103c/README.md", "MCCB-ABS-103c", True),
            ("faq/FAQ-MCCB-ABS-103c.md", "MCCB-ABS-103c", True),
            ("MCCB-ABS-403c/DS-MCCB-ABS-403c.md", "MCCB-ABS-103c", False),
            ("MCCB-ABS-103c-old/DS-X.md", "MCCB-ABS-103c", False),
            ("policy/POL-RETURN.md", "MCCB-ABS-103c", False),
            ("P-1/DS-P-1.md", "", False),
        ]:
            self.assertEqual(cs_evidence_build.is_product_document(name, code), expected, name)

    def test_test_split_exits_with_code_2(self):
        """test 분할 manifest는 종료 코드 2다.

        Returns:
            None: 종료 코드를 확인한다.
        """
        self.write_manifest(["train", "test"], (self.root / "t.csv").read_bytes())
        self.assertEqual(self.run_main()[0], 2)

    def test_code_matches_come_from_the_original_text(self):
        """질의는 토큰 OR이어도 코드 일치는 원문에서 뽑아 문의 코드를 담은 청크를 앞세운다.

        Returns:
            None: 근거 순서를 확인한다.
        """
        searcher = FakeSearcher(
            {
                "Breaker": [
                    candidate("plain", "Trip unit settings.", 1),
                    candidate("coded", "MCCB-ABS-103c trip unit settings.", 2),
                ]
            }
        )
        record = {
            "channel": "EMAIL",
            "body_language": "en",
            "subject": None,
            "body": "Breaker MCCB-ABS-103c tripped",
        }
        evidence, _ = cs_evidence_build.build_evidence(
            {"T1": record},
            searcher,
            index_version="cs-chunks-v1",
            document_sha256s=None,
            limit=5,
            snippet_chars=800,
            sensitive_values={},
            query_from="subject+body",
        )
        self.assertEqual([row["chunk_id"] for row in evidence["T1"]], ["coded", "plain"])


if __name__ == "__main__":
    unittest.main()
