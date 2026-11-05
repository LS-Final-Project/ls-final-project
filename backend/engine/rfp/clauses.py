"""추출 문서(ExtractedDocument)에서 조항 트리를 복원한다(KAN-185, 기획서 5.2). DB·모델 호출이 없다.

영문 PART와 일반 한글 장·조·숫자 항목을 본문 번호로 읽는다.
제공 RFP는 제목 서식·자동 번호 없이 번호를 본문 글자로 적는다(14번은 모든 문단이 Normal). 그래서 문단 앞
번호 패턴으로 깊이를 정한다.

| 깊이 | 패턴 | 조항 번호 예 |
| --- | --- | --- |
| 1 | `PART 2 - PRODUCTS` | `PART 2` |
| 2 | `2.5 EPOXY …` | `2.5` |
| 3 | `C. …` | `2.5.C` |
| 4 | `1. …` | `2.5.C.1` |
| 5 | `a. …` | `2.5.C.1.a` |
| 6 | `1)`·`(1)` | `2.5.C.1.a.1` |
| 7 | `a)`·`(a)` | `2.5.C.1.a.1.a` |

- 한글은 장(깊이 1)·조(2)·숫자 항목(3)·가/나 항목(4) 순서다. 장/조/PART가 없는 문서의
  `1. 일반` 같은 한글 숫자 제목은 깊이 2 절로 시작한다. 조의 괄호 제목·본문은 text에 보존한다.
  한글 번호 문맥의 소수는 실제 번호 부모와 짧은 한글 제목이 있을 때만 계층으로 읽고 다음 영문 PART부터
  기존 영문 깊이로 돌아간다.
- Word 자동 번호(numPr) 문단은 본문 글자에 번호가 없다. DOCX 층이 계산한 `hints.list_text`("2.1"·"A."·"PART 2 -")가
  있고 글자 번호가 없으면 그 번호를 앞에 붙여 같은 패턴으로 읽는다(KAN-207, 평가 18번 유형). 글자 번호가 있는 문단과
  글머리표(•)는 그대로다. 삭제 값 위치는 붙인 번호만큼 옮긴 뒤 번호를 떼며 되돌린다.
- 번호 없는 문단은 바로 앞 조항에 붙인다. 앞 글이 문장 끝(`.`·`:`·`;`)이 아니거나 이번 문단이 소문자로
  시작하면 줄이 중간에서 끊긴 것으로 보고 공백으로 잇는다(`LINE_BREAK_REJOINED`). 아니면 줄을 바꿔 붙인다.
- 표: 한 행의 첫 글자 있는 칸이 번호만이면 그 행을 번호 조항으로 읽는다(14번 2.5 D·G·I…가 이런 배치다).
  그 밖의 표 행은 바로 앞 조항의 `table_rows`에 넣는다.
- 반복 문구: 숫자를 지운 모양이 3번 이상 나오는 번호 없는 짧은 문단이 **모양이 다른 같은 종류 문단과 붙어서**
  나올 때만(페이지 경계의 머리말·바닥글) 뺀다. 혼자 반복되는 `Provide the following:` 같은 본문은 남는다.
  삭제 값이 있는 문단은 빼지 않는다. 뺀 문단은 글과 함께 `excluded`에 남긴다. `END OF SECTION …`과 그 뒤
  문단(마지막 페이지 바닥글 등)은 절 밖이라 조항에 붙이지 않는다(`AFTER_END_OF_SECTION`).
  첫 PART 앞 문단은 머리말이다.
- 표 칸 하나가 `2.4 A.`처럼 절·항목 번호만이면 그 절과 항목을 연다. 절 번호와 항목 번호가 다른 칸(`2.1 | A. | …`)이어도
  같게 읽는다(KAN-207). `PART 1 GENERAL`처럼 대시가 없는 PART는 제목이 대문자일 때만 PART다. 같은 행 뒤가 본문이면 그 본문이
  항목 본문이고, 뒤가 번호 칸(`1.`·`ABB.`)이면 그 칸들은 앞 조항 목록의 이어짐이라 앞 조항에 두고, 절은 번호가
  `1`·`a`로 다시 시작하는 곳에서 연다(`DEFERRED_ARTICLE`).
- 한 문단에 항목 두 개가 붙어 있으면(PDF 변환, 14번 2.4 C.3 "… open. b. Expulsion …") 문장 끝 바로 뒤에
  **같은 깊이의 다음 번호**가 대문자 글 앞에 올 때만 나눈다(`INLINE_SPLIT`). 그 밖의 "No. 2." 같은 글은 두지 않는다.
- PDF 글자층은 빈 줄 없는 줄들을 한 문단으로 준다(`SECTION …\n2.1 …`, `A. …\nB. …`). 문단 안의 줄이 번호로
  시작하고 앞 줄이 문장 끝이거나 제목 줄(SECTION·PART·대문자 절)이면 그 줄에서 나눈다. 나눈 조각은 원래 block_id를
  그대로 쓴다(원문 강조 위치는 같은 블록이다). 앞 줄이 문장 끝이 아닌 `13.8 kV class` 같은 끊긴 줄은 나누지 않는다(KAN-229).
- PART 줄이 없어도 `SECTION …` 줄 뒤의 대문자 제목 절(`2.1 EQUIPMENT REQUIREMENTS`)은 절로 시작한다(도면 PDF, KAN-229).
- 절 번호(`2.5`)는 지금 PART 번호와 앞 자리가 같고 그 PART의 앞 절보다 클 때만 절이다. 끊긴 줄이
  `13.8 kV class`처럼 소수로 시작해도 절로 읽지 않는다. 미뤄 둔 절이 문서 끝이나 다음 절까지 열리지 않으면
  그 자리에서 열고 `numbering_issues`(`UNPLACED_ARTICLE`)에 남긴다.
- 파일에 `SECTION … - …` 줄이 둘 이상이면 두 번째부터 번호 추적을 새로 시작하고 `MULTIPLE_SECTIONS`로
  보고한다(조항 번호는 섹션을 붙이지 않으므로 같은 번호는 `~2`로 구분된다). 제공 RFP는 파일당 섹션 하나다.
- 같은 번호가 다시 나오면 `~2`를 붙여 구분하고, 건너뛴 번호(c. 다음 e.)와 함께 `numbering_issues`에 남긴다.
  번호를 고쳐 쓰지 않는다.
- 삭제 값(취소선)은 조항 text 기준 위치로 옮긴다. 번호 자리의 삭제 값은 `label_history`(예전 번호)다.
  해석은 edits.py가 한다.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.documents.model import Block, CrossReference, DeletedValue, ExtractedDocument

# v5: 공백 없는 번호 제목의 잡음 오인·장 항목 부모, 점선 목차 제외(KAN-215)
# v6: PDF 문단 안 번호 줄 나누기, PART 없는 SECTION 문서의 대문자 절 제목(KAN-229)
CLAUSE_TREE_VERSION = "clause-tree-v6"
# "PART 2 - PRODUCTS"(14번) 또는 대시 없는 "PART 1 GENERAL"(평가 11번 유형, KAN-207). 대시가 없으면 제목이 대문자일
# 때만 PART다(본문의 "PART 2 of …"를 PART로 읽지 않는다).
PART = re.compile(r"^PART\s+(\d+)(?:\s*[-–—]\s*|\s+(?=[A-Z][A-Z0-9 ,/&()'\-]*$))(.+)$")
KOREAN_CHAPTER = re.compile(r"^(제\s*(\d+)\s*장)(?=\s|[（(.:]|$)\s*(.*)$")
KOREAN_ARTICLE = re.compile(r"^(제\s*(\d+)\s*조)(?=\s|[（(.:]|$)\s*(.*)$")
KOREAN_NUMBER = re.compile(r"^(\d+)\.\s*([가-힣].*)$")
# 번호와 한글 제목은 DOCX run 경계에서 공백 없이 붙기도 한다("1.4.3성능보증", KAN-215).
KOREAN_DECIMAL = re.compile(r"^(\d+(?:\.\d+)+)\s*([가-힣].*)$")
# 한글 장 안에서 반복되는 1.1.1본문 형식만 별도 문맥으로 지원한다. 공용 LABELS는 검색 청킹과 공유하므로 유지한다.
COMPACT_KOREAN_DECIMAL = re.compile(r"^(\d+(?:\.\d+){1,5})\s*([가-힣].*)$")
COMPACT_KOREAN_DETAIL = re.compile(r"^\d+(?:\.\d+){2,5}(?=[가-힣])")
COMPACT_KOREAN_ITEM = re.compile(r"^(\(?\d{1,2}\))(?=[가-힣])")
COMPACT_DETAIL_ANY = re.compile(r"^(\d+(?:\.\d+){2,5})\s*([A-Za-z가-힣].*)$")
COMPACT_INLINE = re.compile(r"(?<!\S)(\d+(?:\.\d+){1,5})(?=[가-힣])")
ATTACHED_RATING = re.compile(r"^\d+(?:\.\d+)?(?:kV|kA|kVA|MVA|Hz|mm|V|A)(?=[^A-Za-z]|$)")
# 문단 앞 소수 수량/조항 참조를 제목으로 승격하지 않는다.
KOREAN_MEASURE = re.compile(
    r"^\d+(?:\.\d+)+\s*(?:밀리미터|센티미터|킬로미터|미터|킬로볼트|볼트|킬로암페어|암페어|퍼센트|"
    r"킬로그램|그램|리터|초|분|시간|일|개월|년|개|회|명|배)(?:\s|$)"
)
KOREAN_REFERENCE = re.compile(r"^(?:제\s*\d+\s*(?:장|조|항|호)|(?:조|항|호)(?:에|의|를))")
KOREAN_LETTERS = "가나다라마바사아자차카타파하"
ARTICLE = re.compile(r"^(\d+)\.(\d+)\s+(\S.*)$")
# PDF에서 바꾼 문서는 절 번호와 첫 항목 번호가 표 칸 하나에 함께 떠 있다(14번 "2.2 A.").
COMPOUND = re.compile(r"^(\d+)\.(\d+)\s+([A-Z])\.$")
# 표 칸에 절 번호만 있는 칸("2.1"). 다음 칸이 "A."뿐이면 두 칸을 "2.1 A."로 이어 읽는다(평가 18번 유형, KAN-207).
ARTICLE_ONLY = re.compile(r"^\d+\.\d+$")
LABELS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (3, re.compile(r"^([A-Z])\.(?:\s+|$)")),
    (4, re.compile(r"^(\d{1,2})\.(?:\s+|$)")),
    (5, re.compile(r"^([가나다라마바사아자차카타파하])\.(?:\s+|$)")),
    (5, re.compile(r"^([a-z])\.(?:\s+|$)")),
    (6, re.compile(r"^\(?(\d{1,2})\)(?:\s+|$)")),
    (7, re.compile(r"^\(?([가나다라마바사아자차카타파하])\)(?:\s+|$)")),
    (7, re.compile(r"^\(?([a-z])\)(?:\s+|$)")),
)
END_OF_SECTION = re.compile(r"^END OF SECTION\b", re.I)
# 목차 줄: 점선 리더(.....·…)로 끝나거나 그 뒤에 쪽 번호만 온다. 본문 조항이 아니다(KAN-215).
TABLE_OF_CONTENTS = re.compile(r"(?:\.{5,}|…{2,}|·{5,})\s*(?:\d+(?:\s*[-–]\s*\d+)?)?\s*$")
SECTION_HEAD = re.compile(r"^SECTION\s+\d[\d .]*\s*[-–—]\s*\S")
SENTENCE_END = (".", ":", ";", "?", "!")
BOILERPLATE_MAX = 160
BOILERPLATE_REPEAT = 3


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Clause(Strict):
    clause_no: str
    label: str = Field(description="문단 앞 번호 그대로(PART 2, 2.5, C., 1.).")
    label_history: list[str] = Field(default=[], description="취소선으로 지운 예전 번호.")
    depth: int = Field(ge=1, le=7)
    parent_no: str | None
    ordinal: int = Field(ge=0)
    title: str | None = Field(description="PART·절(깊이 1·2)만. 그 밖에는 null.")
    text: str = Field(description="번호를 뺀 현행 본문. 이어 붙인 문단 포함.")
    deleted_values: list[DeletedValue] = Field(description="text 기준 위치로 옮긴 삭제 값.")
    title_deleted_values: list[DeletedValue] = Field(
        default=[],
        description="PART·절 제목 줄의 삭제 값(title 기준 위치). 본문 삭제 값과 섞지 않는다.",
    )
    flags: list[str] = []
    block_ids: list[str]
    table_rows: list[list[str]] = []
    table_deleted_values: list["TableDeleted"] = []
    page: int | None = None
    cross_references: list[CrossReference] = []


def is_korean_section(clause: Clause) -> bool:
    """새로 지원한 한글 장·조·독립 숫자 절인지. 기존 영문 leaf 선정은 바꾸지 않는다."""
    return (
        "COMPACT_KOREAN_NUMBER" in clause.flags
        or (
            clause.depth >= 3
            and clause.clause_no == clause.label
            and re.fullmatch(r"\d+(?:\.\d+)+", clause.label) is not None
        )
        or (
            clause.depth <= 2
            and (
                KOREAN_CHAPTER.fullmatch(clause.label) is not None
                or KOREAN_ARTICLE.fullmatch(clause.label) is not None
                or (clause.depth == 2 and re.fullmatch(r"\d+\.", clause.label) is not None)
            )
        )
    )


class TableDeleted(Strict):
    row: int = Field(ge=0, description="table_rows 안 행 순번.")
    col: int = Field(ge=0, description="그 행의 글자 있는 칸 순번.")
    value: DeletedValue


class NumberingIssue(Strict):
    clause_no: str
    kind: str = Field(description="DUPLICATE(같은 번호 반복) 또는 GAP(건너뜀).")
    detail: str


class Excluded(Strict):
    block_id: str
    text: str
    reason: str = Field(
        description=(
            "PAGE_NOISE(페이지 머리말·바닥글 묶음), END_OF_SECTION, AFTER_END_OF_SECTION, "
            "TABLE_OF_CONTENTS(점선 리더 목차 줄)."
        )
    )


class ClauseTree(Strict):
    version: str = CLAUSE_TREE_VERSION
    sha256: str
    edit_trace_support: bool
    section_title: str | None
    clauses: list[Clause]
    preamble_block_ids: list[str]
    boilerplate_block_ids: list[str]
    excluded: list[Excluded]
    numbering_issues: list[NumberingIssue]


def _next(token: str) -> str | None:
    if token in KOREAN_LETTERS:
        at = KOREAN_LETTERS.index(token) + 1
        return KOREAN_LETTERS[at] if at < len(KOREAN_LETTERS) else None
    if token.isdigit():
        return str(int(token) + 1)
    if len(token) == 1 and token.isalpha() and token not in "zZ":
        return chr(ord(token) + 1)
    return None


@dataclass
class _Open:
    clause_no: str
    token: str
    label: str
    depth: int
    parent_no: str | None
    title: str | None
    text: str
    deleted: list[DeletedValue]
    history: list[str]
    block_ids: list[str]
    page: int | None
    flags: set[str] = field(default_factory=set)
    rows: list[list[str]] = field(default_factory=list)
    row_deleted: list[TableDeleted] = field(default_factory=list)
    refs: list[CrossReference] = field(default_factory=list)
    title_deleted: list[DeletedValue] = field(default_factory=list)
    # 이어 붙인 블록마다 text 안 시작 위치. 한 문단 나누기에서 블록 출처를 정한다.
    starts: list[tuple[int, str]] = field(default_factory=list)


def _shape(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip())


def _article_heading(tail: str) -> bool:
    """독립 조 제목만 인정한다. 괄호 뒤 조사·다른 항 참조는 본문이다."""
    if not tail:
        return True
    if tail.startswith(("(", "（")):
        return re.match(r"^[（(][^()（）]{1,80}[）)](?:\s|$)", tail) is not None
    return (
        len(tail) <= 80
        and not KOREAN_REFERENCE.match(tail)
        and not tail.endswith((".", "…", "다", ":", ";", "?", "!"))
    )


def _heading(text: str) -> bool:
    """줄 하나가 제목 줄(SECTION·PART·대문자 절 제목)인지."""
    if SECTION_HEAD.match(text) or PART.match(text):
        return True
    m = ARTICLE.match(text)
    return m is not None and _upper_title(m.group(3))


def _upper_title(title: str) -> bool:
    return title == title.upper() and any(ch.isalpha() for ch in title)


def _pdf_lines(block: Block) -> list[Block]:
    """PDF 문단 안에서 번호로 시작하는 줄을 따로 나눈다(KAN-229). 조각은 원래 block_id를 쓴다."""
    if block.kind != "PARAGRAPH" or "\n" not in block.text.strip() or block.deleted_values:
        return [block]
    pieces: list[list[str]] = []
    for line in block.text.strip().split("\n"):
        head = line.strip()
        previous = pieces[-1][-1].strip() if pieces else ""
        if pieces and not (
            (_label(head) is not None or SECTION_HEAD.match(head))
            and (previous.endswith(SENTENCE_END) or _heading(previous))
        ):
            pieces[-1].append(line)
        else:
            pieces.append([line])
    if len(pieces) == 1:
        return [block]
    texts = ["\n".join(lines) for lines in pieces]
    refs: list[list[CrossReference]] = [[] for _ in texts]
    for ref in block.cross_references:
        at = next((n for n, text in enumerate(texts) if ref.text in text), 0)
        refs[at].append(ref)
    return [
        block.model_copy(update={"text": text, "cross_references": found})
        for text, found in zip(texts, refs, strict=True)
    ]


def _label(text: str) -> tuple[int, str, str, str | None] | None:
    """문단 앞 번호를 읽는다.

    Returns:
        (깊이, 번호 토큰, 번호 글자 그대로, 제목) 또는 None. 제목은 PART·절만.
    """
    if m := KOREAN_CHAPTER.match(text):
        return 1, f"제{m.group(2)}장", m.group(1), m.group(3).strip() or None
    if (m := KOREAN_ARTICLE.match(text)) and _article_heading(m.group(3)):
        return 2, f"제{m.group(2)}조", m.group(1), None
    if m := PART.match(text):
        head = text[: m.start(2)].strip()
        return 1, f"PART {m.group(1)}", head, m.group(2).strip()
    if m := ARTICLE.match(text):
        return 2, f"{m.group(1)}.{m.group(2)}", f"{m.group(1)}.{m.group(2)}", m.group(3).strip()
    if m := KOREAN_NUMBER.match(text):
        return 4, m.group(1), f"{m.group(1)}.", None
    for depth, pattern in LABELS:
        if m := pattern.match(text):
            return depth, m.group(1), m.group(0).strip(), None
    return None


AUTO_PART = re.compile(r"^PART\s+(\d+)\s*[-–—]?\s*$")


def _auto_label(list_text: str) -> str | None:
    """DOCX 층이 계산한 자동 번호를 본문 앞에 붙일 글자로 바꾼다. 번호 패턴이 아니면(글머리표 등) None."""
    label = list_text.strip()
    if m := AUTO_PART.match(label):
        return f"PART {m.group(1)} - "
    for candidate in (label, label.rstrip(".") if "." in label[:-1] else label):
        if _label(candidate + " x") is not None:
            return candidate + " "
    return None


def _auto_numbered(block: Block) -> Block:
    """본문 글자에 번호가 없는 자동 번호 문단에 계산된 번호를 앞에 붙인다(KAN-207, 평가 18번 유형).

    글자 번호가 있는 문단(14·17번 전부)과 자동 번호가 없는 문단은 그대로 돌려준다. 삭제 값 위치는 붙인 길이만큼 옮긴다
    (_strip이 번호를 떼며 다시 되돌린다).
    """
    text = block.text.strip()
    if block.kind != "PARAGRAPH" or block.hints is None or not block.hints.list_text or not text:
        return block
    if _label(text) is not None or PART.match(text) or SECTION_HEAD.match(text):
        return block
    prefix = _auto_label(block.hints.list_text)
    if prefix is None:
        return block
    return block.model_copy(
        update={
            "text": prefix + block.text.lstrip(),
            "deleted_values": _shift(
                block.deleted_values, len(prefix) - (len(block.text) - len(block.text.lstrip()))
            ),
        }
    )


def _strip(
    text: str, deleted: list[DeletedValue], cut: int
) -> tuple[str, list[DeletedValue], list[str]]:
    """앞 cut 글자(번호)를 떼고 삭제 값 위치를 옮긴다. 번호 자리 삭제 값은 예전 번호로 돌려준다."""
    body = text[cut:]
    lead = len(body) - len(body.lstrip())
    body = body.lstrip()
    moved: list[DeletedValue] = []
    history: list[str] = []
    for value in deleted:
        if value.position == 0 and _label(value.text.strip() + " x") is not None:
            history.append(value.text.strip())
            continue
        position = max(0, value.position - cut - lead)
        moved.append(value.model_copy(update={"position": min(position, len(body))}))
    return body, moved, history


def _shift(deleted: list[DeletedValue], offset: int) -> list[DeletedValue]:
    return [v.model_copy(update={"position": v.position + offset}) for v in deleted]


def _row_label(cells: list[Block]) -> tuple[int, list[Block]] | None:
    """표 한 행의 첫 글자 있는 칸이 번호만이면 그 칸 위치와 나머지 칸을 돌려준다."""
    filled = [i for i, c in enumerate(cells) if c.text.strip()]
    if len(filled) < 2:
        return None
    first = cells[filled[0]]
    found = _label(first.text.strip() + " x")
    if found is None or found[0] < 3 or first.text.strip() != found[2]:
        return None
    return filled[0], [c for c in cells[filled[0] + 1 :] if c.text.strip()]


def _boilerplate(blocks: list[Block]) -> dict[str, str]:
    """뺄 문단과 이유. 페이지 머리말·바닥글은 모양이 다른 반복 문단끼리 붙어 나온다."""
    counts = Counter(
        _shape(b.text)
        for b in blocks
        if b.kind == "PARAGRAPH" and b.text.strip() and len(b.text) <= BOILERPLATE_MAX
    )
    noisy = {shape for shape, n in counts.items() if n >= BOILERPLATE_REPEAT}
    filled = [b for b in blocks if b.text.strip()]

    def candidate(b: Block) -> bool:
        text = b.text.strip()
        return (
            b.kind == "PARAGRAPH"
            and not b.deleted_values
            and _shape(text) in noisy
            and _label(text) is None
            # "1.4설계 및 제작 기술기준"처럼 공백 없이 붙은 번호 제목은 장마다 같은 모양으로 반복돼도 본문이다(KAN-215).
            and not (COMPACT_KOREAN_DECIMAL.match(text) and not KOREAN_MEASURE.match(text))
        )

    out: dict[str, str] = {}
    i = 0
    while i < len(filled):
        if not candidate(filled[i]):
            text = filled[i].text.strip()
            if filled[i].kind == "PARAGRAPH" and END_OF_SECTION.match(text):
                out[filled[i].block_id] = "END_OF_SECTION"
            elif filled[i].kind == "PARAGRAPH" and TABLE_OF_CONTENTS.search(text):
                out[filled[i].block_id] = "TABLE_OF_CONTENTS"
            i += 1
            continue
        j = i
        while j < len(filled) and candidate(filled[j]):
            j += 1
        run = filled[i:j]
        if len({_shape(b.text) for b in run}) >= 2:
            for b in run:
                out[b.block_id] = "PAGE_NOISE"
        i = j
    return out


def _chapter_item(open_: "_Open", number: str) -> bool:
    """장·조 바로 아래 "1.발전설비" 같은 숫자 항목인지. 그 아래 "1.2설비 구성"의 부모가 된다(KAN-215, D20 유형)."""
    return (
        "." not in number
        and open_.label == number + "."
        and (open_.parent_no or "").startswith("제")
    )


def _compact_sections(block: Block) -> list[Block]:
    """한 문단의 '절 제목 + 그 절의 첫 항목'만 나눈다. 원문 블록 ID·위치·삭제값을 보존한다."""
    if block.kind != "PARAGRAPH":
        return [block]
    matches = list(COMPACT_INLINE.finditer(block.text))
    cuts = {0, len(block.text)}
    for head, child in zip(matches, matches[1:]):
        if child.group(1) == head.group(1) + ".1" and child.start() - head.end() <= 80:
            cuts.update((head.start(), child.start()))
    positions = sorted(cuts)
    return [
        block.model_copy(
            update={
                "text": block.text[start:end],
                "deleted_values": [
                    value.model_copy(update={"position": value.position - start})
                    for value in block.deleted_values
                    if start <= value.position < end
                    or (end == len(block.text) and value.position == end)
                ],
            }
        )
        for start, end in zip(positions, positions[1:])
    ]


# 문단 앞 번호 종류 → 바로 아래 단계 번호 종류(A. → 1. → a.). 영문 번호 체계만 다룬다.
INLINE_CHILD = {"upper": "digit", "digit": "lower"}
INLINE_MARK = {
    "digit": re.compile(r"(?<=\s)(\d{1,2})\.\s+(?=[A-Z(\"'“])"),
    "lower": re.compile(r"(?<=\s)([a-z])\.\s+(?=[A-Z(\"'“])"),
}
# 번호 앞 단어가 이것이면 본문 속 참조다(예: "Section 2. ", "Table 1. ").
INLINE_REFERENCE_WORDS = {
    "section",
    "article",
    "paragraph",
    "item",
    "items",
    "table",
    "figure",
    "fig",
    "no",
    "part",
    "clause",
    "page",
    "note",
    "step",
    "type",
    "class",
    "zone",
    "phase",
    "level",
}
INLINE_TITLE_WORDS = 12


def _inline_items(block: Block) -> list[Block]:
    """'번호. 제목 a. 첫 항목 b. 다음 항목'처럼 한 문단에 붙은 하위 항목을 나눈다.

    변환된 규격서는 상위 항목 제목과 첫 하위 항목이 한 문단에 붙어 있어, 문단 앞 번호만 읽으면
    첫 하위 항목(.1·.a)이 상위 조항에 묻힌다. 하위 번호가 1·a부터 차례로 이어질 때만 나눈다.
    첫 하위 번호 앞은 짧은 제목(문장 끝 없음)이거나 문장 끝이어야 하고, 둘째부터는 문장 끝 뒤에 와야 한다.
    삭제값이 있는 문단은 나누지 않는다. 조각은 원래 block_id를 쓴다(_compact_sections와 같은 방식).
    """
    text = block.text
    if block.kind != "PARAGRAPH" or block.deleted_values:
        return [block]
    head = _label(text.strip())
    if head is None or head[0] not in (3, 4):
        return [block]
    token = head[1]
    kind = "upper" if token.isalpha() and token.isupper() else "digit" if token.isdigit() else ""
    child = INLINE_CHILD.get(kind)
    if child is None:
        return [block]
    body_start = len(text) - len(text.lstrip()) + len(head[2])
    expected = "1" if child == "digit" else "a"
    cuts: list[int] = []
    previous_end = body_start
    for m in INLINE_MARK[child].finditer(text, body_start):
        if m.group(1) != expected:
            continue
        before = text[previous_end : m.start()].strip()
        words = before.split()
        if words and words[-1].lower().rstrip(",") in INLINE_REFERENCE_WORDS:
            continue
        if not cuts:
            # 첫 하위 번호: 앞이 짧은 제목(안에 문장 끝 없음)이거나 문장 끝이어야 한다.
            title = (
                not any(w.endswith(SENTENCE_END) for w in words)
                and len(words) <= INLINE_TITLE_WORDS
            )
            if not before or not (before.endswith(SENTENCE_END) or title):
                continue
        elif not before.endswith(SENTENCE_END):
            continue
        cuts.append(m.start())
        previous_end = m.end()
        expected = _next(expected) or ""
    if not cuts:
        return [block]
    positions = [0, *cuts, len(text)]
    return [
        block.model_copy(
            update={
                "text": text[start:end].strip(),
                "cross_references": [
                    r for r in block.cross_references if r.text in text[start:end]
                ],
            }
        )
        for start, end in zip(positions, positions[1:], strict=False)
    ]


def build_clause_tree(document: ExtractedDocument) -> ClauseTree:
    """문서 순서대로 번호 패턴을 읽어 조항 트리를 만든다.

    Args:
        document: KAN-96 추출 결과.

    Returns:
        ClauseTree: 문서 순서의 조항, 머리말·반복 문구 블록, 번호 문제.
    """
    blocks = [_auto_numbered(b) for b in document.blocks]
    noise = _boilerplate(blocks)
    texts = {b.block_id: b.text.strip() for b in blocks}
    if document.source_kind == "PDF":
        blocks = [piece for block in blocks for piece in _pdf_lines(block)]
    clauses: list[Clause] = []
    issues: list[NumberingIssue] = []
    preamble: list[str] = []
    stack: list[_Open] = []
    current: _Open | None = None
    seen: Counter[str] = Counter()
    last_token: dict[tuple[str | None, int], str] = {}
    section_title: str | None = None
    started = False
    pending: tuple[str, str, Block] | None = None
    part_no: str | None = None
    korean = False
    compact_korean = (
        any(KOREAN_CHAPTER.match(b.text.strip()) for b in blocks)
        and sum(
            bool(COMPACT_KOREAN_DETAIL.match(b.text.strip()))
            for b in blocks
            if b.kind == "PARAGRAPH"
        )
        >= 2
    )
    if compact_korean:
        blocks = [part for block in blocks for part in _compact_sections(block)]
    else:
        blocks = [part for block in blocks for part in _inline_items(block)]
    # PART·장·조가 있는 문서는 그 앞 표지를 숫자 절로 승격하지 않는다.
    simple_korean = not any(
        PART.match(b.text.strip())
        or KOREAN_CHAPTER.match(b.text.strip())
        or (KOREAN_ARTICLE.match(b.text.strip()) and _label(b.text.strip()) is not None)
        for b in blocks
    )

    def contextual_label(text: str) -> tuple[int, str, str, str | None] | None:
        if korean and compact_korean:
            # 4.1.2 바로 뒤의 22.9kV를 조항 번호 4.1.222.9로 먹지 않는다. 실제 직전 형제와 단위가 필요하다.
            previous = next(
                (o for o in reversed(stack) if re.fullmatch(r"\d+(?:\.\d+){2,5}", o.token)), None
            )
            if previous is not None:
                prefix, last = previous.token.rsplit(".", 1)
                expected = prefix + "." + str(int(last) + 1)
                if text.startswith(expected) and ATTACHED_RATING.match(text[len(expected) :]):
                    return previous.depth, expected, expected, None
            if decimal := COMPACT_KOREAN_DECIMAL.match(text) or COMPACT_DETAIL_ANY.match(text):
                token, body = decimal.groups()
                parent_no = token.rsplit(".", 1)[0]
                numeric_parent = next(
                    (
                        o
                        for o in reversed(stack)
                        if o.clause_no == parent_no or _chapter_item(o, parent_no)
                    ),
                    None,
                )
                root_section = token.count(".") == 1 and (
                    token.endswith(".1")
                    or any(o.token.split(".")[0] == parent_no for o in stack if "." in o.token)
                )
                if (
                    not KOREAN_MEASURE.match(text)
                    and not KOREAN_REFERENCE.match(body)
                    and (numeric_parent is not None or root_section)
                ):
                    depth = numeric_parent.depth + 1 if numeric_parent else 2
                    if depth <= 7:
                        title = (
                            body
                            if depth == 2
                            and len(body) <= 80
                            and not body.endswith((".", "다", ":", ";", "?", "!"))
                            else None
                        )
                        return depth, token, token, title
                return None
            if item := COMPACT_KOREAN_ITEM.match(text):
                numeric_parent = next(
                    (o for o in reversed(stack) if COMPACT_KOREAN_DECIMAL.match(o.label + "가")),
                    None,
                )
                if numeric_parent is not None and numeric_parent.depth < 7:
                    label = item.group(1)
                    return numeric_parent.depth + 1, label.strip("()"), label, None
        if korean and (decimal := KOREAN_DECIMAL.match(text)):
            token, title = decimal.groups()
            depth = len(token.split(".")) + 1
            parent = token.rsplit(".", 1)[0]
            owner = next((o for o in reversed(stack) if _chapter_item(o, parent)), None)
            if owner is not None:
                depth = owner.depth + 1
            has_parent = (
                any(o.clause_no == parent for o in stack)
                or ("." not in parent and any(o.clause_no == f"제{parent}장" for o in stack))
                or owner is not None
            )
            if (
                depth <= 7
                and has_parent
                and len(title) <= 80
                and not KOREAN_MEASURE.match(text)
                and not KOREAN_REFERENCE.match(title)
                and not title.endswith((".", "…", "다", ":", ";", "?", "!"))
            ):
                return depth, token, token, title
            return None
        found = _label(text)
        if found is None:
            return None
        depth, token, label, title = found
        if simple_korean and KOREAN_NUMBER.match(text):
            return 2, token, label, title
        if korean:
            # 한글 장/조 문맥에서 소수를 영문 PART의 2.5 절로 오인하지 않는다.
            if depth == 2 and not KOREAN_ARTICLE.match(text):
                return None
            if (
                depth == 4
                or (depth == 5 and token in KOREAN_LETTERS)
                or depth == 6
                or (depth == 7 and token in KOREAN_LETTERS)
            ):
                depth -= 1
        return depth, token, label, title

    last_article: dict[str | None, int] = {}

    def article_ok(major: str, minor: str) -> bool:
        """절 번호가 지금 PART 안의 다음 절일 때만 참이다(끊긴 줄의 소수를 절로 읽지 않는다)."""
        if part_no is not None and major != part_no:
            return False
        return int(minor) > last_article.get(part_no, 0)

    def close() -> None:
        nonlocal current
        while current is not None and current.depth >= 3:
            nxt = _next(current.token)
            if nxt is None:
                break
            found = re.search(
                r"(?<=[.;:])\s+(" + re.escape(nxt) + r")\.\s+(?=[A-Z(<\"])", current.text
            )
            if found is None:
                break
            head_text, tail = current.text[: found.start()], current.text[found.end() :]
            cut = found.end()
            kept = [v for v in current.deleted if v.position <= len(head_text)]
            moved = [
                v.model_copy(update={"position": v.position - cut})
                for v in current.deleted
                if v.position >= cut
            ]
            # 나눈 자리(공백·번호) 안의 삭제 값도 버리지 않는다. 번호 모양이면 예전 번호, 아니면 다음 항목 첫머리.
            gap = [v for v in current.deleted if len(head_text) < v.position < cut]
            gap_history = [v.text.strip() for v in gap if _label(v.text.strip() + " x") is not None]
            moved = [
                v.model_copy(update={"position": 0})
                for v in gap
                if _label(v.text.strip() + " x") is None
            ] + moved
            starts = current.starts or [(0, current.block_ids[0])]
            split_at = found.start()
            origin = [bid for start, bid in starts if start <= split_at][-1:]
            later = [(start - cut, bid) for start, bid in starts if start > split_at]
            head_ids = [bid for start, bid in starts if start <= split_at]
            extra_ids = [bid for bid in current.block_ids if bid not in {b for _, b in starts}]
            sibling_ids = origin + [bid for _, bid in later]
            sibling_rows, sibling_row_deleted = current.rows, current.row_deleted
            current.rows, current.row_deleted = [], []
            current.block_ids = head_ids + extra_ids
            current.text, current.deleted = head_text, kept
            emit(current)
            base = f"{current.parent_no}.{nxt}" if current.parent_no else nxt
            last_token[(current.parent_no, current.depth)] = nxt
            seen[base] += 1
            clause_no = base if seen[base] == 1 else f"{base}~{seen[base]}"
            sibling = _Open(
                clause_no=clause_no,
                token=nxt,
                label=f"{nxt}.",
                depth=current.depth,
                parent_no=current.parent_no,
                title=None,
                text=tail,
                deleted=moved,
                history=[],
                block_ids=sibling_ids,
                page=current.page,
                flags={"INLINE_SPLIT"},
                rows=sibling_rows,
                row_deleted=sibling_row_deleted,
                starts=[(0, origin[0])] + [(max(0, start), bid) for start, bid in later]
                if origin
                else [],
            )
            sibling.history = gap_history
            if stack and stack[-1] is current:
                stack[-1] = sibling
            current = sibling
        if current is None:
            return
        emit(current)
        current = None

    def emit(item: _Open) -> None:
        current_ = item
        clauses.append(
            Clause(
                clause_no=current_.clause_no,
                label=current_.label,
                label_history=current_.history,
                depth=current_.depth,
                parent_no=current_.parent_no,
                ordinal=len(clauses),
                title=current_.title,
                text=current_.text,
                deleted_values=current_.deleted,
                title_deleted_values=current_.title_deleted,
                flags=sorted(current_.flags),
                block_ids=current_.block_ids,
                table_rows=current_.rows,
                table_deleted_values=current_.row_deleted,
                page=current_.page,
                cross_references=current_.refs,
            )
        )

    def open_clause(
        depth: int,
        token: str,
        label: str,
        title: str | None,
        block: Block,
        body: str,
        deleted: list[DeletedValue],
        history: list[str],
        extra_ids: list[str],
    ) -> None:
        nonlocal current, part_no
        close()
        if depth == 1:
            part_no = re.sub(r"\D", "", token)
        elif depth == 2 and "." in token:
            last_article[part_no] = int(token.split(".")[1])
        while stack and stack[-1].depth >= depth:
            stack.pop()
        parent = stack[-1] if stack else None
        if depth == 1:
            base = token
        elif depth == 2 or (
            korean
            and (
                KOREAN_DECIMAL.match(block.text.strip())
                or (compact_korean and re.fullmatch(r"\d+(?:\.\d+)+", token))
            )
            and label == token
        ):
            base = token
        else:
            base = f"{parent.clause_no}.{token}" if parent else token
        _check_sequence(parent.clause_no if parent else None, depth, token, base)
        seen[base] += 1
        clause_no = base if seen[base] == 1 else f"{base}~{seen[base]}"
        if seen[base] > 1:
            issues.append(
                NumberingIssue(clause_no=clause_no, kind="DUPLICATE", detail=f"{base} 반복")
            )
        current = _Open(
            clause_no=clause_no,
            token=token,
            label=label,
            depth=depth,
            parent_no=parent.clause_no if parent else None,
            title=title,
            text=body,
            deleted=deleted,
            history=history,
            block_ids=[block.block_id, *extra_ids],
            page=block.position.page,
            refs=list(block.cross_references),
            starts=[(0, block.block_id)],
        )
        if korean and compact_korean and re.fullmatch(r"\d+(?:\.\d+)+", token) and label == token:
            current.flags.add("COMPACT_KOREAN_NUMBER")
        stack.append(current)

    def _check_sequence(parent_no: str | None, depth: int, token: str, base: str) -> None:
        key = (parent_no, depth)
        previous = last_token.get(key)
        last_token[key] = token
        if previous is None or depth <= 2:
            return
        expected: str | None
        if previous.isdigit() and token.isdigit():
            expected = str(int(previous) + 1)
        elif previous in KOREAN_LETTERS and token in KOREAN_LETTERS:
            expected = _next(previous)
        elif len(previous) == 1 and len(token) == 1 and previous.isalpha():
            expected = chr(ord(previous) + 1)
        else:
            return
        if token != expected and token != previous:
            issues.append(
                NumberingIssue(clause_no=base, kind="GAP", detail=f"{previous} 다음 {token}")
            )

    def append(text: str, deleted: list[DeletedValue], block: Block) -> None:
        if current is None:
            preamble.append(block.block_id)
            return
        joined = current.text.rstrip()
        broken = bool(joined) and (not joined.endswith(SENTENCE_END) or text[:1].islower())
        sep = " " if broken else "\n"
        if not joined:
            sep = ""
        offset = len(joined) + len(sep)
        current.text = joined + sep + text
        current.deleted = current.deleted + _shift(deleted, offset)
        if current.block_ids[-1:] != [block.block_id]:
            current.block_ids.append(block.block_id)
        current.starts.append((offset, block.block_id))
        current.refs.extend(block.cross_references)
        if broken:
            current.flags.add("LINE_BREAK_REJOINED")

    def open_compound(
        article: str,
        letter: str,
        head: Block,
        body: str,
        deleted: list[DeletedValue],
        extra: list[str],
        flag: str | None,
    ) -> None:
        open_clause(2, article, article, None, head, "", [], [], [])
        open_clause(3, letter, f"{letter}.", None, head, body, deleted, [], extra)
        if current is not None:
            current.flags.add("FROM_TABLE_ROW")
            if flag:
                current.flags.add(flag)

    def apply_pending(depth: int, token: str) -> None:
        """미뤄 둔 절(2.2 A.)을 번호가 1·a로 다시 시작하는 곳에서 연다."""
        nonlocal pending
        if pending is None or depth != 4 or token != "1" or not stack:
            return
        parent = next((o for o in reversed(stack) if o.depth < depth), None)
        previous = last_token.get((parent.clause_no if parent else None, depth))
        if previous is None or previous == token:
            return
        article, letter, head = pending
        pending = None
        open_compound(article, letter, head, "", [], [], "DEFERRED_ARTICLE")

    def flush_pending() -> None:
        """다음 절·문서 끝까지 열리지 않은 미룬 절을 그 자리에서 열고 문제로 남긴다."""
        nonlocal pending
        if pending is None:
            return
        article, letter, head = pending
        pending = None
        open_compound(article, letter, head, "", [], [], "DEFERRED_ARTICLE")
        issues.append(
            NumberingIssue(
                clause_no=f"{article}.{letter}",
                kind="UNPLACED_ARTICLE",
                detail="표 칸에 떠 있던 절 번호를 번호가 다시 시작하는 곳에서 찾지 못함",
            )
        )

    def row_body(rest: list[Block]) -> tuple[str, list[DeletedValue]]:
        body, deleted = "", []
        for cell in rest:
            piece, moved, _h = _strip(cell.text, cell.deleted_values, 0)
            sep = " " if body else ""
            deleted += _shift(moved, len(body) + len(sep))
            body += sep + piece
        return body, deleted

    i = 0
    ended = False
    while i < len(blocks):
        block = blocks[i]
        text = block.text.strip()
        if noise.get(block.block_id) == "END_OF_SECTION":
            close()
            stack.clear()
            ended = True
        if block.block_id in noise:
            i += 1
            continue
        if ended and text and not PART.match(text) and not SECTION_HEAD.match(text):
            noise[block.block_id] = "AFTER_END_OF_SECTION"
            i += 1
            continue
        ended = ended and not text
        if block.kind == "TABLE_CELL":
            table, row = block.position.table_index, block.position.row
            cells = []
            while (
                i < len(blocks)
                and blocks[i].kind == "TABLE_CELL"
                and blocks[i].position.table_index == table
                and blocks[i].position.row == row
            ):
                cells.append(blocks[i])
                i += 1
            filled = [c for c in cells if c.text.strip()]
            compound = (
                COMPOUND.match(filled[0].text.strip())
                if filled and started and not korean
                else None
            )
            skip = 0
            if (
                compound is None
                and started
                and not korean
                and len(filled) >= 2
                and ARTICLE_ONLY.match(filled[0].text.strip())
            ):
                # 절 번호와 첫 항목 번호가 다른 칸에 나뉜 배치(2.1 | A. | 본문)는 같은 칸 "2.1 A."와 같게 읽는다.
                compound = COMPOUND.match(f"{filled[0].text.strip()} {filled[1].text.strip()}")
                skip = 1 if compound is not None else 0
            if compound is not None and not article_ok(compound.group(1), compound.group(2)):
                compound = None
            if compound is not None:
                flush_pending()
                article = f"{compound.group(1)}.{compound.group(2)}"
                rest = filled[1 + skip :]
                lead = _label(rest[0].text.strip() + " x") if rest else None
                if rest and lead is not None and rest[0].text.strip() == lead[2]:
                    # 뒤가 번호 칸이면 앞 목록의 이어짐이다. 절은 번호가 다시 시작할 때 연다.
                    pending = (article, compound.group(3), filled[0])
                    cells = rest
                else:
                    body, deleted = row_body(rest)
                    open_compound(
                        article,
                        compound.group(3),
                        filled[0],
                        body,
                        deleted,
                        [c.block_id for c in filled[1:]],
                        None,
                    )
                    continue
            labeled = _row_label(cells)
            if labeled is not None and started:
                at, rest = labeled
                head = cells[at]
                found = contextual_label(head.text.strip() + " x")
                assert found is not None
                depth, token, label, _ = found
                history = [v.text.strip() for v in head.deleted_values if v.position == 0]
                body, deleted = row_body(rest)
                apply_pending(depth, token)
                open_clause(
                    depth,
                    token,
                    label,
                    None,
                    head,
                    body,
                    deleted,
                    history,
                    [c.block_id for c in rest],
                )
                if current is not None:
                    current.flags.add("FROM_TABLE_ROW")
            elif started and current is not None:
                kept = [c for c in cells if c.text.strip()]
                if kept:
                    row_no = len(current.rows)
                    current.rows.append([c.text.strip() for c in kept])
                    for col, cell in enumerate(kept):
                        indent = len(cell.text) - len(cell.text.lstrip())
                        for value in cell.deleted_values:
                            position = max(0, value.position - indent)
                            current.row_deleted.append(
                                TableDeleted(
                                    row=row_no,
                                    col=col,
                                    value=value.model_copy(update={"position": position}),
                                )
                            )
                    current.block_ids.extend(c.block_id for c in kept)
            else:
                preamble.extend(c.block_id for c in cells)
            continue
        if block.kind != "PARAGRAPH" or not text:
            i += 1
            continue
        if SECTION_HEAD.match(text):
            if section_title is None:
                section_title = text
            else:
                # 한 파일에 섹션이 둘 이상이면 번호 추적을 새로 시작하고 보고한다(같은 번호는 ~2로 구분).
                flush_pending()
                close()
                stack.clear()
                part_no = None
                last_article.clear()
                last_token.clear()
                issues.append(
                    NumberingIssue(
                        clause_no=text[:40],
                        kind="MULTIPLE_SECTIONS",
                        detail="같은 파일의 다음 섹션",
                    )
                )
            ended = False
            preamble.append(block.block_id)
            i += 1
            continue
        if PART.match(text):
            korean = False
        elif (
            KOREAN_CHAPTER.match(text)
            or (KOREAN_ARTICLE.match(text) and _label(text) is not None)
            or (simple_korean and KOREAN_NUMBER.match(text))
        ):
            korean = True
        found = contextual_label(text)
        if (
            found is not None
            and found[0] == 2
            and ARTICLE.match(text)
            and not (korean and compact_korean)
        ):
            major, minor = found[1].split(".")
            # PART 없는 SECTION 문서(도면 PDF)는 대문자 제목 절이 시작이다(KAN-229).
            section_start = (
                not started
                and section_title is not None
                and part_no is None
                and not korean
                and _heading(text)
            )
            if not (started or section_start) or not article_ok(major, minor):
                found = None
        if found is not None and (
            found[0] == 1
            or started
            or (found[0] == 2 and ARTICLE.match(text) and not korean)
            or KOREAN_ARTICLE.match(text)
            or (simple_korean and KOREAN_NUMBER.match(text))
        ):
            if found[0] <= 2:
                flush_pending()
            depth, token, label, title = found
            started = True
            cut = block.text.find(label) + len(label)
            body, deleted, history = _strip(block.text, block.deleted_values, cut)
            compact_body = (
                korean
                and compact_korean
                and re.fullmatch(r"\d+(?:\.\d+)+", token)
                and title is None
            )
            if not compact_body and (
                (depth <= 2 and not KOREAN_ARTICLE.match(text))
                or (
                    korean
                    and (
                        KOREAN_DECIMAL.match(text)
                        or (compact_korean and COMPACT_KOREAN_DECIMAL.match(text))
                    )
                    and label == token
                )
            ):
                # 제목 줄의 삭제 값(예: PART 3의 "(NOT IN CONTRACT)")은 제목 기준으로 따로 둔다.
                open_clause(depth, token, label, body.strip() or title, block, "", [], history, [])
                if current is not None:
                    current.title_deleted = deleted
            else:
                apply_pending(depth, token)
                open_clause(depth, token, label, None, block, body, deleted, history, [])
            i += 1
            continue
        if not started:
            preamble.append(block.block_id)
        else:
            append(block.text.strip(), block.deleted_values, block)
        i += 1
    flush_pending()
    close()
    order = [b.block_id for b in blocks]
    boilerplate = sorted(noise, key=order.index)
    return ClauseTree(
        sha256=document.sha256,
        edit_trace_support=document.edit_trace_support,
        section_title=section_title,
        clauses=clauses,
        preamble_block_ids=list(dict.fromkeys(preamble)),
        boilerplate_block_ids=boilerplate,
        excluded=[Excluded(block_id=b, text=texts[b], reason=noise[b]) for b in boilerplate],
        numbering_issues=issues,
    )
