# 검색 청킹 판본 (KAN-99·KAN-184)

원본: `backend/engine/search/chunking.py`(`chunk_document`, `INDEX_VERSION`). C/S(`cs.document_chunks`)와 RFP(`tft.document_chunks`) 검색 색인이 같은 규칙을 쓴다. 세부 규칙은 모듈 docstring이 원본이다.

## chunking-v2 (2026-10-30)

v1과 다른 점 세 가지다.

1. **힌트 없는 번호 조항 경계**: 제목·번호 힌트(`is_heading`·`numbering_level`·`list_text`)가 없는 문단은 줄머리 조항 번호로 섹션 경계를 찾는다. PDF에서 바꾼 DOCX(14번 261116.11)가 그렇다.
   - 패턴과 깊이는 조항 트리(`backend/engine/rfp/clauses.py`)의 정규식 상수(`SECTION_HEAD`·`PART`·`ARTICLE`·`LABELS`)를 그대로 쓴다: SECTION 머리 0, PART 1, 조(2.5) 2.
   - A.·1.·a. 같은 더 깊은 번호는 경계로 삼지 않고 본문 줄로 둔다(`CLAUSE_BOUNDARY_MAX = 2`). 14번에서 A.까지 경계로 삼으면 청크 140개 중 100자 미만 31개, 조까지면 82개 중 3개였다.
2. **제목만 청크 합치기**: 40자 이하이고 끝 문장부호(`. : ; ? !`)가 없는 TEXT 청크는 따로 두지 않고 다음 청크 앞에 합친다. 다음이 표면 표 청크 앞에 둔다(표제와 같은 캡션 문단은 블록만 넣고 글자는 되풀이하지 않음). 문서 끝이면 그대로 둔다.
3. **번호 칸 레이아웃 표**: 모든 행 첫 칸이 번호뿐인 표(예: `1. | ABB.`)는 목록을 표로 흉내 낸 것으로 본다. 행을 "번호 내용" 줄로 바꿔 다음 TEXT 청크 앞에 합친다. 다음이 표이거나 문서 끝이면 그 줄로 표 청크를 그대로 낸다.

합친 청크는 예산(1,200자)을 조금 넘을 수 있다.

## 비교(14번 DOCX와 C/S md 6종, v1 → v2)

| 문서 | 청크 수 | 섹션 경로 채움 | 제목만 TEXT 청크 | 번호 레이아웃 표 청크 |
| --- | --- | --- | --- | --- |
| 14_261116.11.docx | 77 → 82 | 0/77 → 81/82 | 1 → 0 | 9 → 0 |
| DS-TRF-DRY-630.md | 20 → 12 | 19/20 → 11/12 | 8 → 0 | 0 → 0 |
| FAQ-TRANSFORMER.md | 9 → 9 | 8/9 → 8/9 | 0 → 0 | 0 → 0 |
| IG-TRF-DRY-630.md | 11 → 10 | 10/11 → 9/10 | 1 → 0 | 0 → 0 |
| MG-TRF-DRY-630.md | 6 → 5 | 5/6 → 4/5 | 1 → 0 | 0 → 0 |
| POL-WARRANTY.md | 8 → 6 | 7/8 → 5/6 | 2 → 0 | 0 → 0 |
| TG-TRF-DRY-630.md | 12 → 10 | 11/12 → 9/10 | 2 → 0 | 0 → 0 |

md의 경로 없는 청크 1개는 문서 머리(front matter)다. 14번은 v1에서 '2.5 EPOXY CAST RESIN' 머리가 청크 중간에 있었고, v2에서는 2.5·2.6 머리가 청크 시작이다.

## 재색인

`chunk_id`는 문서 sha256·`INDEX_VERSION`·순서로 만든다. 판본이 바뀌면 모든 청크를 새로 만들어야 하고, v1 청크와 섞지 않는다.

- 검색 조건(`SearchFilters.index_version`)과 정책 문서 manifest(`CS_POLICY_DOCUMENTS_FILE`)는 `chunking-v2`여야 한다.
- 재색인 전 v1 색인만 있는 DB에서는 v2 검색 결과가 없다. v1 manifest를 쓰는 worker는 load 구간에서 `CS_POLICY_MANIFEST_INDEX_MISMATCH`로 실패한다.
- 로컬 재색인(`cs_index_docs`, 로컬 DB 전용):
  `uv run --frozen python -m scripts.cs_index_docs --docs <문서 디렉터리> --data-origin demo --doc-index <doc_index.csv> --execute --manifest-out <manifest.json>`
