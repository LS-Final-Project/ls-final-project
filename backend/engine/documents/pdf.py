"""PDF 본문 추출(pypdf layout 모드). 페이지별 텍스트를 빈 줄(세로 간격) 기준 문단으로 나눈다.

PDF는 취소선·형광펜·변경 추적을 텍스트 층에서 알 수 없어 edit_trace_support=False이고
deleted_values·HIGHLIGHT·LINE_BREAK_REJOINED는 항상 비운다. 문단 안 줄 바꿈은 "\\n"으로 남긴다
(조항 "A." 줄 경계를 A04가 쓰게). 그림은 페이지 /Resources /XObject 중 /Subtype /Image만 센다
(Form XObject 안 중첩 그림은 세지 않는다). 주석·양식 필드는 읽지 않는다.

범위 밖: 글자 좌표(bbox)와 좌표 변환. layout 모드 텍스트에는 글자 좌표가 없어 위치는 page까지만 둔다
(좌표가 필요한 스캔·메모 PDF는 OCR R07 몫). 교차 참조는 DOCX와 같은 규칙(references.py)으로 뽑는다.
"""

import io
import re

from pypdf import PageObject, PdfReader

from backend.engine.documents.model import (
    PLACEHOLDER,
    Block,
    ExtractedDocument,
    Flag,
    Position,
    Unprocessed,
    build_document,
)
from backend.engine.documents.references import link_references

BLANK_LINE = re.compile(r"\n[ \t]*\n")
# 신뢰하지 않는 업로드의 시간·메모리 상한. 바이트는 명세 §4.2 업로드 상한과 같다.
MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 500


def _image_count(page: PageObject) -> int:
    """페이지 자원에 직접 걸린 그림 XObject 수를 센다.

    Args:
        page: pypdf PageObject.

    Returns:
        /Subtype /Image인 XObject 수.
    """
    resources = page.get("/Resources")
    resources = None if resources is None else resources.get_object()
    xobjects = None if resources is None else resources.get("/XObject")
    if xobjects is None:
        return 0
    xobjects = xobjects.get_object()
    return sum(xobjects[name].get_object().get("/Subtype") == "/Image" for name in xobjects)


def extract_pdf(data: bytes) -> ExtractedDocument:
    """PDF 바이트에서 페이지별 문단·그림 블록을 뽑는다.

    Args:
        data: PDF 파일 바이트.

    Returns:
        공통 표현 문서(edit_trace_support=False, 블록마다 page는 1부터).

    Raises:
        ValueError: PDF를 읽을 수 없거나, 암호화돼 있거나, 크기·페이지 수 상한을 넘을 때.
    """
    if len(data) > MAX_BYTES:
        raise ValueError("PDF가 크기 상한을 넘습니다.")
    blocks: list[Block] = []
    unprocessed: list[Unprocessed] = []
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("암호화된 PDF는 읽지 않습니다.")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("PDF가 페이지 수 상한을 넘습니다.")
        for number, page in enumerate(reader.pages, start=1):
            # 기본 모드는 문단 사이 빈 줄을 내지 않는다. layout 모드가 세로 간격을 빈 줄로 남긴다.
            text = page.extract_text(extraction_mode="layout") or ""
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            for chunk in BLANK_LINE.split(text):
                lines = [" ".join(line.split()) for line in chunk.split("\n")]
                paragraph = "\n".join(line for line in lines if line)
                if not paragraph:
                    continue
                flags: list[Flag] = (
                    ["TEMPLATE_PLACEHOLDER"] if PLACEHOLDER.search(paragraph) else []
                )
                blocks.append(
                    Block(
                        block_id=f"b{len(blocks) + 1:04d}",
                        kind="PARAGRAPH",
                        text=paragraph,
                        flags=flags,
                        position=Position(page=number),
                    )
                )
            for _ in range(_image_count(page)):
                block_id = f"b{len(blocks) + 1:04d}"
                blocks.append(
                    Block(block_id=block_id, kind="IMAGE", text="", position=Position(page=number))
                )
                unprocessed.append(
                    Unprocessed(kind="IMAGE", location=block_id, reason="그림 내용은 읽지 않음")
                )
    except ValueError:
        raise
    except Exception as error:  # pypdf는 PyPdfError 밖(RecursionError 등)으로도 실패한다.
        raise ValueError("PDF를 읽을 수 없습니다.") from error
    return build_document("PDF", data, link_references(blocks), unprocessed, tables=0)
