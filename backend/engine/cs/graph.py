"""C/S 처리 그래프(LangGraph) 골격과 체크포인트(KAN-194). contracts/cs/graph.md가 계약이다.

분류 → 규칙 → 근거 검색 → 답변 초안 → 문장 검증(필요하면 재작성·재검증) → 사람 확인 순서로 실행한다. 사람 확인 직전에 멈추고(interrupt_before), 같은 thread_id로 invoke(None, config)를 부르면 끝난다.

주입은 build_graph 인자로만 한다. LangGraph는 config["configurable"]·["metadata"]의 문자열·수 값을 체크포인트
metadata에 그대로 복사하므로(langgraph.checkpoint.base.get_checkpoint_metadata), 원문이나 식별자를 configurable로
넘기면 DB에 남는다. 그래서 configurable에는 thread_id만 두고, 원문 레코드·민감값·고객 등급 같은 사례 입력은
노드 안에서 load_ticket으로만 꺼낸다. 상태에는 마스킹한 분류 입력과 결과·시도 ID만 두고 원문·정답·raw_response는
두지 않는다. 시도 기록(raw_response 포함)은 record_attempt가 상태 밖에 저장한다.

형식 위반 재호출(KAN-194 4차, 2026-10-29 사용자 결정): 분류·초안 시도 1이 SCHEMA_VIOLATION·CS_INVALID_OUTPUT이면
같은 입력으로 attempt_number=2를 한 번 더 부른다(invalid_output_retries=0이면 끔). 금지 약속·차단 근거·다른 검증
오류·입력 보류·전송 오류·타임아웃은 다시 부르지 않는다. 재호출은 노드 안에서 끝나므로 체크포인트 재개는 다시
부르지 않는다. 두 시도 모두 attempt_ids와 attempts(번호·결과·오류 코드·is_final, 원문 없음)에 남긴다.
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from backend.engine.cs.classification import (
    ClassificationInput,
    ClassificationOutput,
    build_input,
)
from backend.engine.cs.classification_call import ClassificationAttempt, run_classification_attempt
from backend.engine.cs.drafting import (
    DraftOutput,
    EvidenceSnippet,
    RevisionFeedback,
    RevisionSentence,
)
from backend.engine.cs.drafting_call import DraftAttempt, run_draft_attempt
from backend.engine.cs.rules import RuleInputError, decide, product_family
from backend.engine.cs.verification import build_verification_input, unverified_output
from backend.engine.cs.verification_call import VerificationAttempt, run_verification_attempt
from backend.engine.search.hybrid import Searcher, excerpt, scoped_then_global, token_or_query
from backend.engine.search.model import SearchFilters
from backend.shared.ai import Generation
from backend.shared.tracing import TRACING_BLOCKED, tracing_requested

Stage = Literal[
    "RECEIVED", "CLASSIFIED", "CLASSIFY_FAILED", "RULED", "RETRIEVED", "DRAFTED", "HUMAN_REVIEW"
]
HUMAN_REVIEW = "human_review"
RETRY_CODE = "CS_INVALID_OUTPUT"
# 근거에 1자리를 예약할 유형별 정책 문서 doc_id(KAN-194 10차). 실행 이미지에 eval/이 없어 상수로 두며,
# eval/policy/search_expected_v1.json의 유형별 POL-* 기호와 같아야 한다(tests/cs 동기화 테스트).
POLICY_SLOT_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "RETURN": ("POL-RETURN",),
    "WARRANTY": ("POL-WARRANTY", "POL-SERVICE"),
    "ORDER_LEAD": ("POL-ORDER",),
    "QUOTE": ("POL-ORDER",),
    "DEFECT": ("POL-SERVICE",),
}


def policy_slot(
    filters: SearchFilters, category: object, policy_documents: Mapping[str, str] | None
) -> tuple[SearchFilters, int] | None:
    """분류 유형의 정책 문서(doc_id → sha256로 지정)에서 1자리를 예약하는 조건을 만든다.

    청크 행에는 doc_id 열이 없어 문서는 sha256로 지정한다. 매핑이 없거나 그 유형의 정책 문서가 매핑·검색 범위에
    없으면 예약하지 않는다.

    Args:
        filters: 전체 검색 조건(제품 계열 없이 쓴다).
        category: 분류 결과 category_code.
        policy_documents: 정책 문서 doc_id(POL-RETURN 등) → document_sha256.

    Returns:
        tuple | None: scoped_then_global의 reserved 인자.
    """
    known = policy_documents or {}
    wanted = POLICY_SLOT_DOCUMENTS.get(category, ()) if isinstance(category, str) else ()
    shas = sorted({known[d] for d in wanted if d in known})
    if filters.document_sha256s is not None:
        shas = [sha for sha in shas if sha in filters.document_sha256s]
    if not shas:
        return None
    update = {"document_sha256s": shas, "document_kinds": ["POL"], "product_family": None}
    return filters.model_copy(update=update), 1


class CsState(TypedDict):
    """체크포인트에 남는 그래프 상태. 원문·정답·raw_response·키는 두지 않는다."""

    ticket_id: str
    # build_input으로 마스킹한 분류 입력(channel·body_language·subject·body)만 둔다.
    record: dict[str, Any] | None
    classification: dict[str, Any] | None
    attempt_ids: list[str]
    # 시도 요약 {kind, attempt_id, attempt_number, outcome, error_code, is_final}. 원문·raw는 없다. 이 키가 생기기
    # 전 체크포인트에는 없을 수 있다.
    attempts: list[dict[str, Any]]
    rules: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    draft: dict[str, Any] | None
    draft_attempt_id: str | None
    verification: dict[str, Any] | None
    draft_rewritten: bool
    errors: list[dict[str, str]]
    stage: Stage


@dataclass(frozen=True)
class TicketInputs:
    """load_ticket이 노드 안에서만 돌려주는 사례 입력. 상태·configurable에 넣지 않는다."""

    record: Mapping[str, object]
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]]
    product_code: str
    customer_tier: str
    received_at: datetime
    required_info_codes: frozenset[str]
    already_asked_codes: frozenset[str]
    # 문의 제품 계열의 유형별 필요 정보 코드(정책 cs_required_info.csv 행). 있으면 초안은 분류 결과 category_code의
    # 행을 쓰고(없는 유형은 빈 집합), 없으면 required_info_codes를 그대로 쓴다. 정답 유형을 미리 알 필요가 없다.
    required_info_by_category: Mapping[str, frozenset[str]] | None = None

    def required_for(self, category: str) -> frozenset[str]:
        """초안에 넘길 필요 정보 코드.

        Args:
            category: 분류 결과 category_code.

        Returns:
            frozenset[str]: required_info_by_category가 있으면 그 유형의 행(없으면 빈 집합), 없으면
                required_info_codes.
        """
        if self.required_info_by_category is None:
            return self.required_info_codes
        return self.required_info_by_category.get(category, frozenset())


def initial_state(ticket_id: str) -> CsState:
    """새 문의의 시작 상태.

    Args:
        ticket_id: 문의 ID. thread_id로도 쓴다.

    Returns:
        CsState: 빈 결과의 RECEIVED 상태.
    """
    return CsState(
        ticket_id=ticket_id,
        record=None,
        classification=None,
        attempt_ids=[],
        attempts=[],
        rules=None,
        evidence=[],
        draft=None,
        draft_attempt_id=None,
        verification=None,
        draft_rewritten=False,
        errors=[],
        stage="RECEIVED",
    )


def thread_config(thread_id: str) -> dict[str, Any]:
    """실행 config. configurable에는 thread_id만 둔다(나머지 값은 체크포인트 metadata로 복사되므로).

    Args:
        thread_id: 체크포인트 스레드 ID(문의 ID).

    Returns:
        dict: {"configurable": {"thread_id": thread_id}}.
    """
    return {"configurable": {"thread_id": thread_id}}


def route_after_classify(state: CsState) -> str:
    """분류 뒤 갈 곳을 고른다(순수 함수).

    Args:
        state: 분류 노드가 갱신한 상태.

    Returns:
        str: 분류 결과가 있으면 "rules", 없으면(분류 실패) END.
    """
    return "rules" if state["classification"] is not None else END


def _error(state: CsState, node: str, code: str) -> list[dict[str, str]]:
    """오류 목록에 원문 없는 코드 하나를 더한다.

    Args:
        state: 현재 상태.
        node: 노드 이름.
        code: 안전한 오류 코드.

    Returns:
        list[dict[str, str]]: 새 오류 목록.
    """
    return [*state["errors"], {"node": node, "code": code}]


def _plain(value: Any) -> dict[str, Any]:
    """dataclass를 체크포인트에 넣을 JSON 값으로 바꾼다. datetime은 ISO 문자열이다.

    Args:
        value: dataclass 인스턴스.

    Returns:
        dict: JSON으로 왕복 가능한 dict.
    """
    return json.loads(
        json.dumps(asdict(value), default=lambda v: v.isoformat() if isinstance(v, datetime) else v)
    )


def build_graph(
    *,
    classification_provider: Callable[[ClassificationInput], Generation],
    draft_provider: Callable[[str], Generation],
    fts_searcher: Searcher,
    search_filters: SearchFilters,
    allowed_info_codes: frozenset[str],
    load_ticket: Callable[[str], TicketInputs],
    record_attempt: Callable[
        [str, str, ClassificationAttempt | DraftAttempt | VerificationAttempt], str
    ],
    checkpointer: BaseCheckpointSaver,
    verification_provider: Callable[[str], Generation] | None = None,
    verification_enabled: bool = True,
    vector_searcher: Searcher | None = None,
    allow_tracing: bool = False,
    invalid_output_retries: int = 1,
    evidence_limit: int | None = None,
    policy_documents: Mapping[str, str] | None = None,
) -> Any:
    """C/S 처리 그래프를 만들어 사람 확인 직전 중단점과 함께 컴파일한다.

    Args:
        classification_provider: 정제 분류 입력을 받아 Generation을 돌려주는 공급자(ai.py·providers.py 어댑터).
        draft_provider: 초안 프롬프트를 받아 Generation을 돌려주는 공급자.
        verification_provider: 문장 검증 공급자. 없으면 미검증으로 넘긴다.
        verification_enabled: 기본 True. False면 검증 호출을 하지 않고 미검증으로 표시한다.
        fts_searcher: FTS 검색기. vector_searcher와 함께 hybrid.run_hybrid에 넘긴다.
        search_filters: 두 검색기에 똑같이 넘기는 검색 조건.
        allowed_info_codes: 검수된 정보 코드 집합.
        load_ticket: ticket_id로 TicketInputs를 돌려주는 콜러블. 노드 안에서만 부른다.
        record_attempt: (kind, ticket_id, attempt)를 받아 시도를 상태 밖에 저장하고 ID를 돌려준다.
            kind는 "classification", "draft", "verification"이다.
        checkpointer: MemorySaver(검사) 또는 checkpoint_cs 스키마의 PostgresSaver.
        vector_searcher: 벡터 검색기. None이면 FTS만 쓴다.
        allow_tracing: 호출자가 넘기는 Settings.ai_tracing_allowed. 추적 환경 변수가 있는데 False면
            Settings를 거치지 않는 진입점이라도 그래프를 만들지 않는다(KAN-89).
        invalid_output_retries: 1이면 시도 1의 CS_INVALID_OUTPUT에 시도 2를 한 번 부르고, 0이면 부르지 않는다.
            configurable에는 넣지 않는다(체크포인트 metadata 복사).
        evidence_limit: 초안에 넘길 근거 상위 개수. None이면 검색 결과를 모두 넘긴다. 있으면 검색 limit도 이 값을
            넘지 않게 해 정책 예약 자리가 잘리지 않게 한다.
        policy_documents: 정책 문서 doc_id → document_sha256. 있으면 정책 유형 문의에 그 유형의 정책 문서 1자리를
            예약한다(policy_slot). None이면 예약하지 않는다.

    Returns:
        CompiledStateGraph: interrupt_before=["human_review"]로 컴파일한 그래프. 주입은 이 인자로만 하고
            config["configurable"]에는 thread_id만 둔다(thread_config). 분류 실패는 END, 규칙·검색·초안 실패는
            errors에 코드를 남기고 계속한다. 추적이 요청됐는데 허용되지 않으면 TRACING_BLOCKED ValueError,
            invalid_output_retries가 0·1이 아니면 CS_GRAPH_INVALID_RETRIES, evidence_limit이 1 이상 정수가 아니면
            CS_GRAPH_INVALID_EVIDENCE_LIMIT ValueError다.
    """
    if tracing_requested(os.environ) and not allow_tracing:
        raise ValueError(TRACING_BLOCKED)
    if type(invalid_output_retries) is not int or invalid_output_retries not in (0, 1):
        raise ValueError("CS_GRAPH_INVALID_RETRIES")
    if evidence_limit is not None and (type(evidence_limit) is not int or evidence_limit < 1):
        raise ValueError("CS_GRAPH_INVALID_EVIDENCE_LIMIT")

    def attempt_with_retry(
        state: CsState,
        kind: str,
        call: Callable[[int], ClassificationAttempt | DraftAttempt | VerificationAttempt],
    ) -> tuple[Any, dict[str, Any]]:
        """시도 1을 부르고 CS_INVALID_OUTPUT이면 시도 2를 한 번 더 부른 뒤 두 시도를 모두 기록한다.

        Args:
            state: 현재 상태.
            kind: "classification" 또는 "draft".
            call: 시도 번호를 받아 엔진 시도를 돌려주는 함수.

        Returns:
            tuple: (마지막 시도, attempt_ids·attempts 상태 갱신).
        """
        tries = [call(1)]
        if (
            invalid_output_retries
            and tries[0].outcome == "SCHEMA_VIOLATION"
            and tries[0].error_code == RETRY_CODE
        ):
            tries.append(call(2))
        return tries[-1], record_tries(state, kind, tries)

    def record_tries(
        state: CsState,
        kind: str,
        tries: list[ClassificationAttempt | DraftAttempt | VerificationAttempt],
    ) -> dict[str, Any]:
        """호출 시도별 ID와 원문 없는 요약을 남긴다."""
        ids = list(state["attempt_ids"])
        log = list(state.get("attempts", []))
        for index, attempt in enumerate(tries):
            attempt_id = record_attempt(kind, state["ticket_id"], attempt)
            ids.append(attempt_id)
            log.append(
                {
                    "kind": kind,
                    "attempt_id": attempt_id,
                    "attempt_number": attempt.attempt_number,
                    "outcome": attempt.outcome,
                    "error_code": attempt.error_code,
                    "is_final": index == len(tries) - 1,
                    # 출력 잘림(MAX_TOKENS 등) 원인 확인용. 원문은 넣지 않는다.
                    "finish_reasons": list(attempt.finish_reasons),
                }
            )
        return {"attempt_ids": ids, "attempts": log}

    def classify(state: CsState) -> dict[str, Any]:
        """분류 시도를 실행하고(형식 위반이면 시도 2까지) 마스킹한 입력과 결과만 상태에 둔다.

        Args:
            state: 현재 상태.

        Returns:
            dict: 상태 갱신.
        """
        ticket = load_ticket(state["ticket_id"])
        attempt, recorded = attempt_with_retry(
            state,
            "classification",
            lambda number: run_classification_attempt(
                ticket.record,
                sensitive_values=ticket.sensitive_values,
                allowed_info_codes=allowed_info_codes,
                provider=classification_provider,
                attempt_number=number,
            ),
        )
        if attempt.prediction is None:
            code = attempt.error_code or attempt.outcome
            return {
                **recorded,
                "errors": _error(state, "classify", code),
                "stage": "CLASSIFY_FAILED",
            }
        masked = build_input(ticket.record, sensitive_values=ticket.sensitive_values)
        return {
            **recorded,
            "record": masked.payload.model_dump(),
            "classification": attempt.prediction.model_dump(mode="json"),
            "stage": "CLASSIFIED",
        }

    def rules(state: CsState) -> dict[str, Any]:
        """우선순위·담당 부서·SLA 규칙을 계산한다. 정책 밖 입력은 오류로 남기고 계속한다.

        Args:
            state: 분류가 끝난 상태.

        Returns:
            dict: 상태 갱신.
        """
        ticket = load_ticket(state["ticket_id"])
        result = state["classification"] or {}
        try:
            decision = decide(
                category=result["category_code"],
                urgency=result["urgency_flag"],
                product_code=ticket.product_code,
                customer_tier=ticket.customer_tier,
                return_subtype=result["return_subtype"],
                received_at=ticket.received_at,
            )
        except RuleInputError:
            return {"errors": _error(state, "rules", "CS_RULE_INPUT"), "stage": "RULED"}
        return {"rules": _plain(decision), "stage": "RULED"}

    def retrieve(state: CsState) -> dict[str, Any]:
        """마스킹한 제목·본문의 토큰 OR 질의로 제품 계열 문서를 먼저 찾고, 모자라면 전체로 보충해 근거로 바꾼다.

        policy_documents가 있고 분류 유형이 POLICY_SLOT_DOCUMENTS에 있으면 그 정책 문서 1자리를 예약한다(policy_slot).

        websearch_to_tsquery는 문의 전문을 넘기면 모든 어휘의 AND가 되어 거의 맞지 않으므로 hybrid.token_or_query를
        쓴다. 제품 계열은 문의 제품 코드의 rules.product_family이고 청크 메타데이터 product_family와 맞춘다. 모르는
        제품이면 전체만 찾는다. 스니펫은 청크 본문 앞 800자(hybrid.excerpt)이며 ts_headline(<b> 태그)은 쓰지 않는다.

        Args:
            state: 규칙 단계가 끝난 상태.

        Returns:
            dict: 상태 갱신. 질의 토큰이 없으면 검색하지 않고 근거 0건이다. 검색기 예외는 원문 없이
                CS_SEARCH_FAILED로 남긴다.
        """
        record = state["record"] or {}
        query = token_or_query(record.get("subject"), record.get("body"))
        # 코드 일치 가산(codes_in)은 토큰으로 쪼개지 않은 마스킹 원문에서 뽑는다.
        text = "\n".join(part for part in (record.get("subject"), record.get("body")) if part)
        if not query:
            return {"evidence": [], "stage": "RETRIEVED"}
        try:
            family: str | None = product_family(load_ticket(state["ticket_id"]).product_code)
        except RuleInputError:
            family = None
        scoped = search_filters.model_copy(update={"product_family": family}) if family else None
        category = (state["classification"] or {}).get("category_code")
        try:
            items, _ = scoped_then_global(
                query,
                search_filters,
                scoped,
                fts=fts_searcher,
                # 벡터는 FTS용 어휘 OR 문자열이 아니라 마스킹 원문을 임베딩한다(KAN-99, 임베딩 실측과 같은 입력).
                vector=(lambda _query, f: vector_searcher(text, f)) if vector_searcher else None,
                limit=min(search_filters.limit, evidence_limit or search_filters.limit),
                code_text=text,
                reserved=policy_slot(search_filters, category, policy_documents),
            )
        except Exception:
            return {"errors": _error(state, "retrieve", "CS_SEARCH_FAILED"), "stage": "RETRIEVED"}
        evidence = [
            EvidenceSnippet(
                chunk_id=item.candidate.chunk_id,
                # scoped_then_global이 본문 있는 후보만 남기므로 빈 값은 오지 않는다.
                snippet=excerpt(item.candidate) or "",
                section_path=list(item.candidate.section_path),
                document_sha256=item.candidate.document_sha256,
            ).model_dump()
            for item in items[:evidence_limit]
        ]
        return {"evidence": evidence, "stage": "RETRIEVED"}

    def draft(state: CsState) -> dict[str, Any]:
        """초안 시도를 실행한다(형식 위반이면 시도 2까지). 실패해도 사람 확인으로 넘긴다(사람이 직접 쓴다).

        Args:
            state: 근거 검색이 끝난 상태.

        Returns:
            dict: 상태 갱신.
        """
        ticket = load_ticket(state["ticket_id"])
        classification = ClassificationOutput.model_validate(state["classification"])
        evidence = [EvidenceSnippet.model_validate(e) for e in state["evidence"]]
        attempt, recorded = attempt_with_retry(
            state,
            "draft",
            lambda number: run_draft_attempt(
                ticket.record,
                classification=classification,
                required_info_codes=ticket.required_for(classification.category_code),
                already_asked_codes=ticket.already_asked_codes,
                evidence=evidence,
                sensitive_values=ticket.sensitive_values,
                provider=draft_provider,
                attempt_number=number,
            ),
        )
        if attempt.prediction is None:
            code = attempt.error_code or attempt.outcome
            return {**recorded, "errors": _error(state, "draft", code), "stage": "DRAFTED"}
        return {
            **recorded,
            "draft": attempt.prediction.model_dump(mode="json"),
            "draft_attempt_id": recorded["attempt_ids"][-1],
            "stage": "DRAFTED",
        }

    def verify(state: CsState, *, number: int = 1) -> dict[str, Any]:
        """문장을 한 번 검증한다. 호출 실패는 미검증으로 남기고 초안은 보존한다."""
        if state.get("draft") is None:
            return {
                "verification": {
                    "status": "NOT_VERIFIED",
                    "reason": "CS_DRAFT_MISSING",
                    "sentences": [],
                }
            }
        try:
            payload = build_verification_input(
                DraftOutput.model_validate(state["draft"]),
                [EvidenceSnippet.model_validate(e) for e in state["evidence"]],
            )
        except ValueError:
            return {
                "verification": {
                    "status": "NOT_VERIFIED",
                    "reason": "CS_VERIFICATION_INPUT_INVALID",
                    "sentences": [],
                }
            }
        if not verification_enabled or verification_provider is None:
            reason = (
                "CS_VERIFICATION_DISABLED"
                if not verification_enabled
                else "CS_VERIFICATION_UNAVAILABLE"
            )
            return {
                "verification": {
                    "status": "NOT_VERIFIED",
                    "reason": reason,
                    **unverified_output(payload, reason).model_dump(),
                }
            }
        # 이전 판본 체크포인트에는 draft_attempt_id가 없을 수 있다.
        draft_id = state.get("draft_attempt_id") or next(
            (a["attempt_id"] for a in reversed(state.get("attempts", [])) if a["kind"] == "draft"),
            None,
        )
        attempt = run_verification_attempt(
            payload,
            provider=verification_provider,
            attempt_number=number,
            draft_id=draft_id,
        )
        recorded = record_tries(state, "verification", [attempt])
        if attempt.prediction is None:
            code = attempt.error_code or attempt.outcome
            return {
                **recorded,
                "verification": {
                    "status": "NOT_VERIFIED",
                    "reason": code,
                    **unverified_output(payload, code).model_dump(),
                },
                "errors": _error(state, "verify", code),
            }
        verdicts = attempt.prediction
        status = (
            "VERIFIED"
            if all(s.verdict == "SUPPORTED" for s in verdicts.sentences)
            else "NEEDS_REVIEW"
        )
        return {
            **recorded,
            "verification": {"status": status, "reason": None, **verdicts.model_dump()},
        }

    def rewrite(state: CsState) -> dict[str, Any]:
        """검증 실패 이유로 초안을 모델 호출 한 번만 재작성한다. 실패하면 기존 초안·판정을 보존한다."""
        ticket = load_ticket(state["ticket_id"])
        classification = ClassificationOutput.model_validate(state["classification"])
        feedback = RevisionFeedback(
            prior_draft=DraftOutput.model_validate(state["draft"]),
            failed_sentences=[
                RevisionSentence(
                    label=s["label"], sentence_index=s["sentence_index"], reason=s["reason"]
                )
                for s in (state.get("verification") or {}).get("sentences", [])
                if s["verdict"] != "SUPPORTED"
            ],
        )
        attempt = run_draft_attempt(
            ticket.record,
            classification=classification,
            required_info_codes=ticket.required_for(classification.category_code),
            already_asked_codes=ticket.already_asked_codes,
            evidence=[EvidenceSnippet.model_validate(e) for e in state["evidence"]],
            sensitive_values=ticket.sensitive_values,
            provider=draft_provider,
            attempt_number=1,
            revision_feedback=feedback,
        )
        recorded = record_tries(state, "draft", [attempt])
        if attempt.prediction is None:
            return {
                **recorded,
                "draft_rewritten": False,
                "errors": _error(state, "rewrite", attempt.error_code or attempt.outcome),
            }
        return {
            **recorded,
            "draft": attempt.prediction.model_dump(mode="json"),
            "draft_attempt_id": recorded["attempt_ids"][-1],
            "draft_rewritten": True,
        }

    def after_verify(state: CsState) -> str:
        return (
            "rewrite"
            if (state.get("verification") or {}).get("status") == "NEEDS_REVIEW"
            else HUMAN_REVIEW
        )

    def after_rewrite(state: CsState) -> str:
        return "reverify" if state.get("draft_rewritten") else HUMAN_REVIEW

    def human_review(state: CsState) -> dict[str, Any]:
        """사람 확인 단계 표시만 남긴다. 확정·전송은 그래프 밖에서 사람이 한다.

        Args:
            state: 초안 단계가 끝난 상태.

        Returns:
            dict: stage=HUMAN_REVIEW.
        """
        return {"stage": "HUMAN_REVIEW"}

    graph = StateGraph(CsState)
    graph.add_node("classify", classify)
    graph.add_node("rules", rules)
    graph.add_node("retrieve", retrieve)
    graph.add_node("draft", draft)
    graph.add_node("verify", verify)
    graph.add_node("rewrite", rewrite)
    graph.add_node("reverify", lambda state: verify(state, number=2))
    graph.add_node(HUMAN_REVIEW, human_review)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", route_after_classify, ["rules", END])
    graph.add_edge("rules", "retrieve")
    graph.add_edge("retrieve", "draft")
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", after_verify, ["rewrite", HUMAN_REVIEW])
    graph.add_conditional_edges("rewrite", after_rewrite, ["reverify", HUMAN_REVIEW])
    graph.add_edge("reverify", HUMAN_REVIEW)
    graph.add_edge(HUMAN_REVIEW, END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=[HUMAN_REVIEW])
