# 검색 청킹 판본 (KAN-99·KAN-184)

원본: `backend/engine/search/chunking.py`(`chunk_document`, `INDEX_VERSION`). C/S(`cs.document_chunks`)와 RFP(`tft.document_chunks`) 검색 색인이 같은 규칙을 쓴다. 세부 규칙은 모듈 docstring이 원본이다.

## chunking-v3 (2026-10-31)

v2와 다른 점은 둘이다.

1. **Markdown 머리말을 색인하지 않는다.** 문서 맨 앞 YAML 머리말(`---`로 둘러싼 블록)과 바로 뒤 인용 문단(`>`로 시작, 합성 안내 줄)은 청크에 넣지 않고 `skipped_block_ids`에 둔다. 닫는 `---`가 없으면 머리말로 보지 않는다.
2. **제목 블록만 든 청크는 길이와 상관없이 다음 청크에 합친다.** v2의 40자 규칙은 긴 문서 제목을 놓쳐 제목 혼자 청크가 됐다. 이 규칙은 DOCX·PDF에도 적용된다.

`chunk_id`가 판본을 포함하므로 이미 적재한 색인은 C/S·RFP 모두 다시 만들어야 한다. 14번 DOCX는 82 → 81청크다(문서 제목 합침).

문서 메타데이터는 청크 본문이 아니라 칸으로 유지한다: `document_kind`·`product_family`·`version`(`doc_index.csv`)과 색인 manifest의 `doc_id`·`source_name`·`sha256`.

머리말 청크는 제품 코드가 몰려 있어 제품 우선 검색 상위를 차지했다(603건 근거 3,011조각 중 2,370개).

## chunking-v2 (2026-10-30)

v1과 다른 점 세 가지다.

1. **힌트 없는 번호 조항 경계**: 제목·번호 힌트(`is_heading`·`numbering_level`·`list_text`)가 없는 문단은 줄머리 조항 번호로 섹션 경계를 찾는다. PDF에서 바꾼 DOCX(14번 261116.11)가 그렇다.
   - 패턴과 깊이는 조항 트리(`backend/engine/rfp/clauses.py`)의 정규식 상수(`SECTION_HEAD`·`PART`·`ARTICLE`·`LABELS`)를 그대로 쓴다: SECTION 머리 0, PART 1, 조(2.5) 2.
   - 제목꼴(80자 이하, 끝 문장부호 없음)인 줄만 경계로 본다. "2.5 mm 이상의 전선은 …"처럼 소수로 시작하는 본문 문장은 경계가 아니다.
   - A.·1.·a. 같은 더 깊은 번호는 경계로 삼지 않고 본문 줄로 둔다(`CLAUSE_BOUNDARY_MAX = 2`). 14번에서 A.까지 경계로 삼으면 청크 140개 중 100자 미만 31개, 조까지면 82개 중 3개였다.
2. **제목만 청크 합치기**: 40자 이하이고 끝 문장부호(`. : ; ? !`)가 없는 TEXT 청크는 따로 두지 않고 다음 청크 앞에 합친다. 다음이 표면 표 청크 앞에 둔다(표제와 같은 캡션 문단은 블록만 넣고 글자는 되풀이하지 않음). 섹션 머리가 아닌 짧은 줄과 레이아웃 표는 다음 청크가 같은 섹션일 때만 합치고, 아니면 제 섹션 경로로 그대로 낸다. 문서 끝이면 그대로 둔다.
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

md의 경로 없는 청크 1개는 문서 머리(front matter)다(v3에서 뺐다). 14번은 v1에서 '2.5 EPOXY CAST RESIN' 머리가 청크 중간에 있었고, v2에서는 2.5·2.6 머리가 청크 시작이다.

## 재색인

`chunk_id`는 문서 sha256·`INDEX_VERSION`·순서로 만든다. 판본이 바뀌면 모든 청크를 새로 만들어야 하고, v1 청크와 섞지 않는다.

- 검색 조건(`SearchFilters.index_version`)과 정책 문서 manifest(`CS_POLICY_DOCUMENTS_FILE`)는 현재 `INDEX_VERSION`(`chunking-v3`)이어야 한다.
- 재색인 전 옛 판본 색인만 있는 DB에서는 검색 결과가 없다. 옛 manifest를 쓰는 worker는 load 구간에서 `CS_POLICY_MANIFEST_INDEX_MISMATCH`로 실패한다.
- 로컬 재색인(`cs_index_docs`, 로컬 DB 전용):
  `uv run --frozen python -m scripts.cs_index_docs --docs <문서 디렉터리> --data-origin demo --doc-index <doc_index.csv> --execute --manifest-out <manifest.json>`

## C/S 청크 임베딩 (KAN-99, C20)

- 표: `cs.chunk_embeddings(chunk_row_id → cs.document_chunks.id ON DELETE CASCADE, embedding_version, embedding vector(768), created_at)`, 기본 키는 (청크 행, embedding_version)이다. HNSW 코사인 인덱스를 둔다. 청크 표에 열을 더하지 않는다.
- `embedding_version`은 `모델:차원:task_type`(예: `gemini-embedding-2:768:RETRIEVAL_DOCUMENT`, 채택 KAN-144)이다. 모델·차원이 바뀌면 새 판본 행을 만들고 섞지 않는다. 청크 색인 판본(`index_version`)과는 따로 둔다.
- 재적재(`store_chunks`의 삭제·삽입)는 청크 행 ID를 바꾸므로 그 문서의 임베딩이 지워진다. 다시 임베딩해야 한다.
- 권한: `cs_app` SELECT·INSERT·DELETE(UPDATE 없음), `cs_worker` SELECT. pgvector 확장은 관리자가 설치하며 마이그레이션은 만들지 않는다.
- 적재: `scripts/cs_index_docs.py --embed`(로컬 DB만). 그 판본 임베딩이 없는 청크만 문자열마다 1회 호출해 넣는다. 첫 공급자 오류에서 사유 코드(`CS_EMBED_PROVIDER_ERROR`)로 멈추고, 다음 실행이 남은 청크부터 잇는다. `--execute` 없이 쓰면 문서를 다시 적재하지 않는다. 대상은 `--docs` 범위가 아니라 그 판본이 없는 DB 전체 청크이며, `--embed-limit`(기본 2000)을 넘으면 호출 없이 `CS_EMBED_OVER_LIMIT`로 멈춘다. manifest `embedding`에는 판본·대상 수·넣은 수·호출 수·문자 수·사유·근사 비용(문자/4 × `ai_prices.json`)이 들어간다.
- 검색: `repository.search_vector`는 `search_fts`와 같은 조건(문서 해시·product_family·document_kinds·limit)으로 코사인 거리 오름차순(같으면 문서·순번)이다.
- 검색기(KAN-100): `backend/agent/cs_search.VectorSearcher`가 트랜잭션마다 `SET LOCAL hnsw.iterative_scan = relaxed_order`(pgvector 0.8)를 켜고 `search_vector`를 부른 뒤 거리·문서·순번으로 다시 정렬한다. 좁은 필터가 HNSW 후보(`hnsw.ef_search`) 뒤에 걸려 limit보다 적게 나오지 않게 하기 위해서다. 질의는 FTS용 어휘 OR가 아니라 마스킹 원문(subject·body)이고, 같은 원문의 임베딩은 한 번만 부른다. 임베딩·DB 오류면 `[]`로 FTS 결과만 쓰고 `failures`를 세며(같은 원문의 임베딩 실패는 30초 동안 다시 시도하지 않아 한 문의의 단계 사이 중복 호출만 막고, 그 뒤나 다른 원문은 다시 시도한다; failures는 실제 시도마다, DB 오류는 단계마다) `CS_VECTOR_SEARCH_FAILED <예외 클래스>` 경고를 남긴다(원문·메시지 미기록).
- 결합: `scoped_then_global`은 좁힌 단계(제품)·전체 단계에서 FTS와 벡터를 RRF로 합치고, 정책 예약 단계는 FTS만 쓴다. 그래프(`build_graph(vector_searcher=)`)·worker·`cs_evidence_build --vector`가 같은 경로다.
- 설정(`CsVectorSettings`, 접두사 `CS_`): `CS_VECTOR_SEARCH_ENABLED`(코드 기본 false, `.env.example`은 로컬·시연용 true; worker는 켤 때만 임베딩 클라이언트를 만든다), `CS_EMBEDDING_MODEL`(gemini-embedding-2), `CS_EMBEDDING_DIMENSION`(768만), `CS_EMBEDDING_QUERY_TASK_TYPE`(RETRIEVAL_DOCUMENT). 청크 판본은 `{모델}:768:RETRIEVAL_DOCUMENT`이다.
- gemini-embedding-2는 현재 호출 경로(`EmbedContentConfig.task_type`)에서 task_type을 반영하지 않는다(2026-10-30 실측, 같은 질의를 두 설정으로 임베딩한 코사인 1.0, gemini-embedding-001은 0.923). 그래서 질의 기본값을 문서 쪽과 같은 값으로 둔다.
- 운영 주의: 재적재로 지운 임베딩 행은 VACUUM 전까지 HNSW 그래프에 죽은 노드로 남아 탐색 재현율을 낮출 수 있다(검사에서 확인). 대량 재적재 뒤에는 `VACUUM cs.chunk_embeddings`를 돌린다.
