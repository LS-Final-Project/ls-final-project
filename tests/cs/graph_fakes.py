"""KAN-194 그래프 검사용 합성 사례·fake 공급자·fake 검색기(DB 검사와 공유). 실제 모델·데이터 없음."""

import json
from datetime import UTC, datetime

from backend.engine.cs.graph import TicketInputs
from backend.engine.search.model import Candidate, SearchFilters
from backend.shared.ai import Generation, Usage

SECRETS = ("ZX-PRIVATE", "person@example.test", "김고객", "GOLD_SECRET")
RECORD = {
    "channel": "EMAIL",
    "body_language": "en",
    "subject": "Breaker trips",
    "body": "Trips at 300 V. serial: ZX-PRIVATE, contact person@example.test 김고객",
    "category_code": "GOLD_SECRET",
}
TICKET = TicketInputs(
    record=RECORD,
    sensitive_values={"김고객": "PERSONAL"},
    product_code="TRF-OIL-1000",
    customer_tier="GOLD",
    received_at=datetime(2026, 10, 29, 9, tzinfo=UTC),
    required_info_codes=frozenset({"TEST_PHOTO"}),
    already_asked_codes=frozenset(),
)
FILTERS = SearchFilters(document_sha256s=None, index_version="chunking-v1")
# 7칸 들여쓰기는 상태 직렬화에 나올 수 없어 raw_response 유출 확인에 쓴다.
RAW_INDENT = "\n       "


def classification_provider(payload):
    """유효한 분류 JSON을 낸다.

    Args:
        payload: 정제 분류 입력.

    Returns:
        Generation: 합성 응답.
    """
    body = {
        "category_code": "WARRANTY",
        "secondary_category": None,
        "urgency_flag": "NONE",
        "provided_info": [],
        "return_subtype": None,
    }
    return Generation(json.dumps(body, indent=7), Usage(10, 2), "fake-v1", ["STOP"], model="fake")


def broken_provider(payload):
    """깨진 분류 응답을 낸다.

    Args:
        payload: 정제 분류 입력.

    Returns:
        Generation: 스키마 위반 응답.
    """
    return Generation("not json RAW_SECRET", Usage(1, 1), "fake-v1", ["STOP"], model="fake")


def draft_provider(prompt):
    """프롬프트 입력 JSON의 근거·요청 코드로 유효한 초안을 낸다.

    Args:
        prompt: 렌더링한 초안 프롬프트.

    Returns:
        Generation: 합성 응답.
    """
    payload = json.loads(prompt.split("\n\n입력:\n", 1)[1])
    payload = payload.get("input", payload)
    chunk = payload["evidence"][0]["chunk_id"]
    options = [
        {
            "label": label,
            "sentences": [
                {"text": "Synthetic answer.", "kind": "EVIDENCE", "evidence_chunk_ids": [chunk]}
            ],
            "info_requests": (
                [
                    {"info_code": c, "question": "Please send it."}
                    for c in payload["request_info_codes"]
                ]
                if label == "INFO_FIRST"
                else []
            ),
        }
        for label in ("CONCISE", "DETAILED", "INFO_FIRST")
    ]
    text = json.dumps({"options": options}, indent=7)
    return Generation(text, Usage(20, 5), "fake-v1", ["STOP"], model="fake")


def fts_searcher(query, filters):
    """근거 후보 두 개를 낸다.

    Args:
        query: 질의.
        filters: 검색 조건.

    Returns:
        list[Candidate]: 합성 후보.
    """
    return [
        Candidate(
            chunk_id=f"chunk-{n}",
            document_sha256="a" * 64,
            ordinal=n,
            section_path=["Warranty"],
            snippet=f"<b>Warranty</b> clause {n}.",
            # 실제 search_fts는 청크 본문(text)을 항상 돌려준다. retrieve는 이 본문을 근거로 쓴다.
            text_excerpt=f"Warranty clause {n}.",
            kind="TEXT",
            table_title=None,
            page_start=None,
            source="FTS",
            position=n,
            score=1.0 / n,
        )
        for n in (1, 2)
    ]


class AttemptStore:
    """record_attempt fake. 시도를 상태 밖에 보관하고 ID를 돌려준다."""

    def __init__(self) -> None:
        """빈 보관소.

        Returns:
            None: 초기화.
        """
        self.attempts: list[tuple[str, str, object]] = []

    def __call__(self, kind, ticket_id, attempt):
        """시도를 보관한다.

        Args:
            kind: classification 또는 draft.
            ticket_id: 문의 ID.
            attempt: 시도 기록.

        Returns:
            str: 시도 ID.
        """
        self.attempts.append((kind, ticket_id, attempt))
        return f"attempt-{len(self.attempts)}"


def verification_provider(prompt):
    """입력 문장마다 지원 판정을 내는 흐름 검사 fake. 실제 근거 품질의 증거가 아니다."""
    payload = json.loads(prompt.split("\n\n입력:\n", 1)[1])
    return Generation(
        json.dumps(
            {
                "sentences": [
                    {
                        "label": s["label"],
                        "sentence_index": s["sentence_index"],
                        "verdict": "SUPPORTED" if s["evidence_chunk_ids"] else "UNVERIFIABLE",
                        "reason": "Synthetic verification only.",
                        "evidence_chunk_ids": s["evidence_chunk_ids"],
                    }
                    for s in payload["sentences"]
                ]
            }
        ),
        Usage(10, 5),
        "fake-v1",
        ["STOP"],
        model="fake",
        attempts=1,
    )
