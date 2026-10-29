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

    def test_test_split_exits_with_code_2(self):
        """test 분할 manifest는 종료 코드 2다.

        Returns:
            None: 종료 코드를 확인한다.
        """
        self.write_manifest(["train", "test"], (self.root / "t.csv").read_bytes())
        self.assertEqual(self.run_main()[0], 2)


if __name__ == "__main__":
    unittest.main()
