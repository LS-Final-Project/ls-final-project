"""블록 텍스트에서 다른 절·도면·문서 참조를 뽑고, 같은 문서 안 절 참조만 해결한다(KAN-98).

종류와 패턴(영문 키워드는 대소문자 무시):
- DRAWING: "Drawing E-101", "Drawings No. E101"
- DOCUMENT: "Addendum B", "Section 26 13 26"·"Section 261116.11"(MasterFormat 6자리는 다른 규격서),
  "Appendix A"·"Attachment 2"·"Exhibit C"
- OTHER: "Division 26"
- CLAUSE: 키워드(Section·Article·Paragraph·Subparagraph·Clause·Part·Item·see·refer to) + "2.4"·"2.4.E"·
  "2.4 E.1.c", 키워드 없이는 하위 문자까지 있는 "2.4 E.1.c" 꼴만. 맨 "2.4"는 수치(2.5% 등)와 구분되지 않아
  뽑지 않는다. 블록 맨 앞의 번호는 그 블록 자신의 번호라 참조로 보지 않는다.

해결: CLAUSE의 target_hint 전체가 같은 문서 블록의 hints.list_text(끝의 "."·"-"·공백 제거) 또는 text 맨 앞
번호와 같을 때만 resolved=True. "2.4 E.1.c" 같은 하위 경로는 조항 트리(A04)가 있어야 풀리므로 미해결로 두고
A04가 target_hint로 다시 푼다. DRAWING·DOCUMENT·OTHER는 문서 밖이라 해결하지 않는다. 문서가 자기 번호
("Section 261116.11")를 가리켜도 자기 문서 번호를 알 수 없으므로 DOCUMENT 미해결로 그대로 둔다.
"""

import re

from backend.engine.documents.model import Block, CrossReference, ReferenceKind

SUB = r"(?:\s[A-Z]\.\d{1,2}(?:\.[a-z])?|\.[A-Z](?:\.\d{1,2}(?:\.[a-z])?)?\b)"
CLAUSE_NO = rf"\d{{1,2}}\.\d{{1,2}}(?:\.\d{{1,2}})*(?:{SUB})?"
PATTERNS: list[tuple[ReferenceKind, re.Pattern[str]]] = [
    (
        "DRAWING",
        re.compile(r"(?i:\bdrawings?\s+(?:no\.?\s*)?)([A-Z]{1,3}-?\d{1,4}(?:\.\d{1,2})?)\b"),
    ),
    ("DOCUMENT", re.compile(r"(?i:\baddendum\s+(?:no\.?\s*)?)([A-Z0-9]{1,3})\b")),
    ("DOCUMENT", re.compile(r"(?i:\bsection\s+)(\d{2} ?\d{2} ?\d{2}(?:\.\d{2})?)\b")),
    ("DOCUMENT", re.compile(r"(?i:\b(?:appendix|attachment|exhibit)\s+)([A-Z0-9]{1,3})\b")),
    ("OTHER", re.compile(r"(?i:\bdivision\s+)(\d{2})\b")),
    (
        "CLAUSE",
        re.compile(
            r"(?i:\b(?:section|article|paragraph|subparagraph|clause|part|item|see|refer\s+to)\s+)"
            rf"({CLAUSE_NO})"
        ),
    ),
    ("CLAUSE", re.compile(r"\b(\d{1,2}\.\d{1,2}\s[A-Z]\.\d{1,2}(?:\.[a-z])?)\b")),
]
LEADING_NUMBER = re.compile(r"^(\d+(?:\.\d+)+)\b")


def find_references(text: str) -> list[tuple[str, ReferenceKind, str]]:
    """텍스트에서 참조를 찾는다. 앞 패턴이 먼저 차지한 구간과 겹치는 일치는 버린다.

    Args:
        text: 블록 현행 텍스트.

    Returns:
        (일치 텍스트, 대상 종류, 대상 힌트)를 텍스트 순서대로.
    """
    taken: list[tuple[int, int, str, ReferenceKind, str]] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if start == 0 and kind == "CLAUSE" and match.group(0) == match.group(1):
                continue  # 블록 자신의 번호
            if any(start < other_end and other_start < end for other_start, other_end, *_ in taken):
                continue
            taken.append((start, end, match.group(0), kind, " ".join(match.group(1).split())))
    return [(found, kind, hint) for _, _, found, kind, hint in sorted(taken)]


def _clause_key(block: Block) -> list[str]:
    keys = []
    if block.hints is not None and block.hints.list_text:
        keys.append(block.hints.list_text.strip().rstrip(".-").strip())
    leading = LEADING_NUMBER.match(block.text)
    if leading:
        keys.append(leading.group(1))
    return keys


def link_references(blocks: list[Block]) -> list[Block]:
    """블록마다 cross_references를 채운다.

    Args:
        blocks: 문서 순서의 블록.

    Returns:
        cross_references를 채운 새 블록 목록(같은 순서).
    """
    targets: dict[str, str] = {}
    for block in blocks:
        for key in _clause_key(block):
            targets.setdefault(key, block.block_id)
    linked = []
    for block in blocks:
        references = []
        for found, kind, hint in find_references(block.text):
            target = targets.get(hint) if kind == "CLAUSE" else None
            references.append(
                CrossReference(
                    text=found,
                    target_kind=kind,
                    target_hint=hint,
                    resolved=target is not None,
                    target_block_id=target,
                )
            )
        linked.append(
            block.model_copy(update={"cross_references": references}) if references else block
        )
    return linked
