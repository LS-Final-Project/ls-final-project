"""문서 형식 판별과 추출기 선택. 매직바이트가 기준이고 확장자·MIME은 교차 확인에만 쓴다."""

import io
import zipfile
from pathlib import Path

from backend.engine.documents.docx import extract_docx
from backend.engine.documents.model import DOCX_MIME, ExtractedDocument, SourceKind
from backend.engine.documents.pdf import MAX_BYTES, extract_pdf

DECLARED: dict[str, SourceKind] = {
    ".docx": "DOCX",
    ".pdf": "PDF",
    DOCX_MIME: "DOCX",
    "application/pdf": "PDF",
}


def _sniff(data: bytes) -> SourceKind:
    """바이트 앞부분과 zip 목록으로 형식을 정한다.

    Args:
        data: 파일 바이트.

    Returns:
        "DOCX" 또는 "PDF".

    Raises:
        ValueError: 둘 다 아닐 때(구 .doc·xlsx 같은 다른 zip 포함).
    """
    if data.startswith(b"%PDF-"):
        return "PDF"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "DOCX"
        except zipfile.BadZipFile:
            pass
    raise ValueError("DOCX·PDF만 추출합니다.")


def extract(
    source: Path | bytes, mime: str | None = None, source_name: str | None = None
) -> ExtractedDocument:
    """파일 경로나 바이트를 형식에 맞는 추출기로 보낸다.

    Args:
        source: 파일 경로(확장자도 확인) 또는 바이트.
        mime: 업로드 때 받은 MIME(선택).
        source_name: 원래 파일 이름(선택). 경로면 생략 시 경로의 파일 이름.

    Returns:
        공통 표현 문서.

    Raises:
        ValueError: 크기 상한을 넘거나, 지원하지 않는 형식이거나, 확장자·MIME이 실제 내용과 다를 때.
    """
    # 경로는 읽기 전에 크기부터 본다(큰 파일을 메모리에 올리지 않게).
    if isinstance(source, Path) and source.stat().st_size > MAX_BYTES:
        raise ValueError("문서가 크기 상한(25 MiB)을 넘습니다.")
    data = source.read_bytes() if isinstance(source, Path) else source
    # 명세 §4.2 업로드 상한을 DOCX·PDF에 같이 적용한다.
    if len(data) > MAX_BYTES:
        raise ValueError("문서가 크기 상한(25 MiB)을 넘습니다.")
    kind = _sniff(data)
    # MIME 파라미터(; charset=…)는 떼고, 형식을 말하지 않는 일반 타입은 확인하지 않는다.
    media_type = (mime or "").split(";", 1)[0].strip().lower()
    declared = [media_type] if media_type and media_type != "application/octet-stream" else []
    if isinstance(source, Path):
        declared.append(source.suffix.lower())
    for value in declared:
        if DECLARED.get(value.lower()) != kind:
            raise ValueError(f"선언한 형식({value})이 실제 내용({kind})과 다릅니다.")
    document = extract_docx(data) if kind == "DOCX" else extract_pdf(data)
    if source_name is None and isinstance(source, Path):
        source_name = source.name
    return document.model_copy(update={"source_name": source_name})
