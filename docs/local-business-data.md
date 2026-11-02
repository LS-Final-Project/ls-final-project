# 팀원 PC 업무검증용 데이터 준비 (KAN-159)

각 팀원 PC에서 같은 코드와 기존에 받은 자료로 검증한다. 1차 목표는 **신규 C/S를 UI에서 등록하고 RFP 문서를 UI로 업로드한 뒤 실제 처리하는 것**이다. 문의 4,000건·해결사례 800건 전량 적재를 선행 조건으로 삼지 않는다. 공개 공유서버나 기존 담당자 PC의 절대 경로에 의존하지 않는다.

이 안내의 코드 기준은 `f3bcb9d195074adb17d78ce7e93c857d2e2470cf`다. 실행도구·개인별 인증·정규 worker 연결은 coder2 담당이며, 이 문서는 기존 데이터 CLI와 준비 공백을 다룬다. 아래 적재 명령은 **작성자가 실행한 결과가 아니다**. 실행도구의 완료나 실기능 전체 검증을 뜻하지 않는다.

## 1. 각 PC에 준비할 것

- Git 및 저장소 접근 권한, Docker 엔진·Compose, Python 3.11 이상과 uv, Node 22.12 이상·npm(Node 24 LTS 권장). Python 실행 기준은 3.12다. 상세 설치는 [공통 온보딩](team-start.md)을 따른다.
- 기존 작업을 보존한 별도 코드 경로와 동일한 Git SHA. 기존 DB를 초기화하거나 다른 사람의 환경 파일을 복사하지 않는다.
- 본인이 받은 원자료 경로. 원자료가 없는 PC는 미확보로 기록하고 제공자에게 받는다. `data/`를 생성·수정하거나 이 안내를 이유로 원본을 이동·덮어쓰지 않는다. 코드 밖 기존 자료 경로를 지정할 수 있다.
- 검색용 문서 묶음의 `docs/`와 그 안의 **`doc_index.csv`**. C/S real worker에는 적재 후 생성한 정책 manifest도 필요하다.
- 자사 23종과 합성 협력사는 아래 커밋 파일을 사용한다. 자사 원천 CSV는 재생성·원천 대조가 필요할 때 별도로 받는다.

기존 온보딩의 `python3 scripts/workspace.py setup`과 `up`은 의존성·로컬 DB/서비스 준비다. 개발 계정 4개의 seed는 계정 선택·권한 확인용이며 문의 처리 결과·분석·비교 결과를 미리 채우는 seed와 구분한다. 이 기본 기동만으로 검색 문서·협력사 적재나 real worker 준비가 끝났다고 판단하지 않는다. 새 실행도구와 기존 스택의 포트·DB를 혼용하지 않는다.

## 2. 입력 종류와 인수 위치

| 자료 | 확인된 위치 또는 공유 근거 | 용도·준비 상태 |
| --- | --- | --- |
| 제공 문의 | 제공 묶음 `CS_AI_Agent_Training_Data_v1/tickets.csv` | 교육용 합성 `synthetic/PROVIDED`. 통합본은 개발 전용 승인본이 아니다. 1차에는 UI에 새 문의를 직접 입력한다. |
| 제공 해결사례 | 같은 묶음 `resolution_cases.csv` | 해결 후 근거이며 최초 문의와 분리한다. `known_at` 확인이 필요하다. 업무 검색 저장소로 넣는 기존 CSV importer는 확인되지 않았다. |
| 검색 문서 | 같은 묶음 `docs/` 및 `docs/doc_index.csv` | 기존 문서 적재 CLI가 지원하는 MD/DOCX/PDF 입력. 문서 ID·판본·출처 연결을 위해 실제 doc_index를 함께 지정한다. |
| 자사 23종 원천 | 같은 묶음 `ref/product_master.csv`, `ref/product_spec_facts.csv` | 제공된 교육용 합성 데이터시트이며 실제 제품 성능 자료가 아니다. 원천 대조/재생성용이다. |
| 자사 23종 파생값 | `backend/api/tft/rfp/own_spec_facts.json` | 코드에 포함된 `DERIVED` 파일을 비교 구현이 사용한다. DB 선적재 CLI가 아니라 파일 기반 입력이다. 손으로 수정하지 않는다. |
| 가상 협력사 | `backend/api/tft/partners/profiles.json` | 제공 원자료에 협력사는 없다. 팀이 만든 `profile_source=synthetic` 후보 사실이며 별도 DB 적재가 필요하다. 등급·선택 결과는 실행 시 계산한다. |
| RFP 문서 | 각자 기존에 받은 승인된 문서 파일 | UI 업로드 후 추출·조항·요구·비교 등 실제 처리 결과를 생성한다. 원문 14번 문서의 내용은 이 조사에서 열람하지 않았다. |
| 개발 분리본 | 관리자가 인수한 train/valid와 manifest | 개발 실험 시에만 사용한다. 통합 문의를 대신 승인하지 않는다. 평가 gold·시험 자료는 이 PC 준비 조사/안내에서 열람하지 않는다. |
| 새 장비 시연 자료 | #544의 `demo/ASSUMED` 장비 자료 | 기존 제공 원자료와 별개다. 본 문서 조사 당시 열린 PR이며 기본 seed·필수 선행에 넣지 않는다. |
| generated demo-en/smoke | 기존 `artifacts/` 아래 합성 시연 후보 | 내부 disposable 검증을 위한 별도 후보다. 팀원 기본 DB에 넣지 않으며 사전 시연 결과도 입력으로 넣지 않는다. |

현재 장비에서 **이름·위치만** 확인한 제공본은 `/home/canae/ls_main/data/CS_AI_Agent_Training_Data_v1/`이다. 이는 담당자 PC의 보조 위치이지 팀원의 필수 경로가 아니다. coder1 작업 경로에는 `data/`가 없고, 다른 팀원 PC의 보유 여부는 확인하지 않았다.

공유 원천은 [C/S 제공 폴더](https://drive.google.com/drive/folders/1I_aeMmjzbPNqWV9BDGF0ejL3O8RDCJOH), [문서 폴더](https://drive.google.com/drive/folders/1_FMpy_9uSdHGAusrCnWon4aDyeFU4IJp), [ref 폴더](https://drive.google.com/drive/folders/1okAJuJICExt79SZNhehdyoINR0oilZ-8)다. Drive 목록은 CSV를 누락할 수 있어 목록 부재를 파일 부재로 판단하지 않는다.

| 원천 | 파일 ID | 메타데이터 크기 | 수정 시각 UTC |
| --- | --- | ---: | --- |
| tickets.csv | [1pc1DPs0RAtQlFTCPhgeXNRL0AmI7uB-q](https://drive.google.com/file/d/1pc1DPs0RAtQlFTCPhgeXNRL0AmI7uB-q/view) | 4,053,369 B | 2026-10-20 05:37:28.813 |
| resolution_cases.csv | [1xGaJQCUZVeWs8pCQ3UcZ_6f_Z44fHeKQ](https://drive.google.com/file/d/1xGaJQCUZVeWs8pCQ3UcZ_6f_Z44fHeKQ/view) | 575,329 B | 2026-10-20 05:37:25.985 |

위 2건의 메타데이터는 이번에 재확인했으나 커넥터가 요청한 checksum/version을 반환하지 않았다. 현재 Drive 원본과 각 PC의 SHA-256 일치는 미확인이다. ref 폴더 목록에는 `reference_master.xlsx`가 있지만 원천 CSV 2종을 같은 판본으로 다시 받을 수 있는지는 미확인이다. XLSX를 CLI의 CSV 입력으로 확인 없이 대체하지 않는다.

각 PC에서 수령처·수령일·상대 파일명·크기·SHA-256·허용 용도·원본/파생 관계를 기록하고 관리자의 대상 판본 manifest와 대조한다. 파일명·수정 시각만으로 동일 판본이라 하지 않는다. hash는 파일 단위로 계산하며 gold를 포함한 디렉터리를 재귀 열람하지 않는다. 상세는 [데이터 계약](data-contract.md)을 따른다.

## 3. C/S 검색 문서: 확인 → 명시 적용 → worker 연결

아래 명령은 저장소 루트에서 실행하며 개인 PC의 실재 경로로 바꾼다. `CS_INPUT_DOCS`는 수령한 문서 폴더이고, `CS_INDEX_RUNTIME`은 원자료·`data/` 밖에 마련한 쓰기 가능한 실행 결과 폴더다. CLI가 이곳에 manifest를 출력한다.

```bash
export CS_INPUT_DOCS='/absolute/path/to/received/CS_AI_Agent_Training_Data_v1/docs'
export CS_INDEX_RUNTIME='/absolute/path/to/writable/runtime'
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.cs_index_docs \
  --docs "$CS_INPUT_DOCS" \
  --doc-index "$CS_INPUT_DOCS/doc_index.csv" \
  --data-origin synthetic --dry-run \
  --manifest-out "$CS_INDEX_RUNTIME/cs-index-dry-run.json"
```

dry-run도 원자료를 읽어 추출·청킹한다. 문서 작성자가 이를 재실행하지는 않았다. 종료코드 0만으로 개별 문서 성공을 판단하지 않는다. manifest의 전체 `documents`에서 `reason`·`chunks`를 확인하고 거부·추출 실패·누락을 해소한다. 원문과 자격정보를 로그로 공유하지 않는다.

**기존 실행 근거(감독 제공):** f3 기준 coder2 작업 경로·Python 3.12.3·env-i/키 없음·`-B`로 실행한 동등 dry-run은 rc0, 107문서/794청크, 모든 reason=null, `chunking-v3`였다. `POL-ORDER` 6개, `POL-RETURN` 2개, `POL-SERVICE` 5개, `POL-WARRANTY` 5개 청크와 각각의 sha를 확인했다. 근거: `/tmp/kan159-input-check/cs-dry-run.json`. 해당 PC의 기존 기록이며 모든 PC의 건수나 DB 적용을 보장하지 않는다. mode=dry-run manifest는 worker용 execute manifest를 대체할 수 없다.

다음은 명시적인 **로컬 DB 쓰기**다. 실행 담당이 대상을 확인하고 runner에서 해당 DB의 `cs_app`용 `DATABASE_URL`을 안전하게 전달한 뒤에만 실행한다. 비밀번호를 명령 이력에 적지 않고 환경 전체나 연결 문자열을 출력하지 않는다. CLI가 기존 `.env.local`을 자동으로 읽는다고 가정하지 않는다.

```bash
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.cs_index_docs \
  --docs "$CS_INPUT_DOCS" \
  --doc-index "$CS_INPUT_DOCS/doc_index.csv" \
  --data-origin synthetic --execute \
  --manifest-out "$CS_INDEX_RUNTIME/cs-index-execute.json"
```

적용 후 종료코드 외에도 manifest의 `mode=execute`, worker/검색 설정과 일치하는 `index_version`, 문서별 `reason=null`과 `stored`를 확인한다. 특히 위 필수 `POL-*` 4종 모두에 `doc_id → sha256`이 있고 실제 적재되어야 한다. 다른 DB에서 만든 manifest를 복사해 현재 DB 성공의 증거로 삼지 않는다. 문서별 store 실패도 manifest에 남으므로 실패를 무시하고 worker를 시작하지 않는다.

worker 설정은 아래와 같다. 컨테이너에서 실행한다면 해당 worker가 읽을 수 있는 경로로 manifest를 읽기 전용 연결하고 그 경로를 설정한다. 호스트 절대 경로를 그대로 쓸 수 있다고 가정하지 않는다.

```bash
export CS_POLICY_DOCUMENTS_FILE="$CS_INDEX_RUNTIME/cs-index-execute.json"
export CS_VECTOR_SEARCH_ENABLED=false
```

`CS_POLICY_DOCUMENTS_FILE` 누락, mode/판본 불일치, 필수 정책 문서 대응 누락 시 real 처리 준비 완료로 판단하지 않는다(`CS_POLICY_DOCUMENTS_MISSING` 등). 1차는 **FTS만 사용**하며 `--embed`를 실행하지 않는다. 벡터 검색도 명시 OFF로 하고 하이브리드 검색 품질 달성으로 보고하지 않는다. 추후 임베딩은 실제 모델 호출을 수반하는 별도 작업이다. RFP 실제 처리 모델 설정도 실행 담당이 별도로 확인한다.

## 4. 협력사와 자사 비교 입력

저장소의 합성 협력사 파일을 DB 연결 없이 검사하고 계획을 표시하는 기존 명령:

```bash
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.rfp_seed_partner_profiles --dry-run
```

**기존 실행 근거(감독 제공):** 같은 f3 기준·Python 3.12.3·env-i에서 rc0, 기본 tracked 프로필 12곳(P/L/M/T), synthetic, 모두 `PLANNED`였다. 로그는 `/tmp/kan159-input-check/partner-dry-run.log`다. 별도 demo-en Q-A~F 6곳을 기본 적재로 선택하지 않았다. DB·모델 호출이 없는 계획 확인이며 실제 DB 적재 성공을 의미하지 않는다.

runner가 대상 로컬 TFT DB의 `DATABASE_URL`을 안전하게 전달한 후 먼저 읽기 전용 대조를 한다. 기존 DB를 reset하지 않는다.

```bash
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.rfp_seed_partner_profiles --check
```

`--check`는 대상 토큰을 출력하지 않는다. 적용이 필요하면 먼저 confirm 없이 아래 명령을 한 번 실행한다. `TARGET`의 host/port/dbname 토큰을 확인한 후 confirm 불일치로 종료코드 1이 나오는 것이 예상 결과이며, 이 단계는 DB 접속 전에 거부된다. 예상 거부를 적재 실패나 적재 성공으로 기록하지 않는다.

```bash
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.rfp_seed_partner_profiles --apply
```

출력된 대상이 개인 PC의 의도한 로컬 TFT DB인지 설정과 대조한다. 대상이 맞을 때만 정확한 토큰으로 명시 적용하고, 이후 읽기 전용 대조를 다시 수행한다.

```bash
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.rfp_seed_partner_profiles \
  --apply --confirm-target '<확인한 대상 토큰>'
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.rfp_seed_partner_profiles --check
```

기존 판본을 덮어쓰지 않고 없는 판본만 추가한다. 기존 판본과의 `DIFFERENT`를 성공으로 취급하지 않는다. 미확인 후보 세트로 자동 교체하지 않는다. 문서 작성자는 DB 대조·적용을 실행하지 않았다.

자사 23종은 커밋된 `backend/api/tft/rfp/own_spec_facts.json`을 이용한다. 원천 CSV 2종을 받은 경우의 대조 명령은 아래와 같다. `--check`를 생략하면 기본 출력 파일에 쓰는 생성 처리가 되므로 각 PC 준비에서는 생략하지 않는다.

```bash
PYTHONPATH=. uv run --frozen --python 3.12 python -m scripts.rfp_own_spec_facts \
  --ref '/absolute/path/to/received/CS_AI_Agent_Training_Data_v1/ref' --check
```

**기존 실행 근거(감독 제공):** 같은 환경에서 `/home/canae/ls_main/data/CS_AI_Agent_Training_Data_v1/ref`를 대상으로 rc0, `RFP_OWN_SPEC_FACTS_UP_TO_DATE`였다. 로그는 `/tmp/kan159-input-check/own-facts-check.log`다. DB·모델 호출과 원자료 수정 없이 그 PC의 원천/커밋 파일 일치를 확인한 것으로, 다른 PC의 파일 확보를 뜻하지 않는다.

## 5. 1차 순서와 남은 지원 공백

1. 동일 코드·개별 의존 환경·빈 전용 로컬 DB/스키마·개발 계정 4개를 준비한다. 기존 업무 DB를 reset하지 않는다.
2. 수령한 검색 문서·실제 doc_index·선택한 RFP 파일의 존재와 대상 판본을 확인한다. 미수령 PC는 그 사실을 명시한다.
3. 검색 문서 dry-run → 명시 DB 적용 → 필수 정책 ID/문서별 성공 확인 → execute manifest의 worker 연결을 진행한다. 합성 협력사 명시 적용/대조와 커밋된 자사 비교 입력을 확인한다.
4. 실행도구 담당의 인증·권한·CSRF·단일 처리 dispatcher/정규 worker 준비를 확인한다.
5. UI에서 신규 CS 등록/RFP 업로드 후 해당 입력으로 실제 생성한 상태·분류·근거·비교/초안 등을 확인한다. 결과와 예상 결론을 사전 seed하지 않는다. 메일 발송·운영 반영은 별도 승인 대상이다.

지원 공백은 각 PC의 수령 파일과 공통 hash/판본 manifest, 원천 ref CSV의 현재 공유 경로, 기존 해결사례 CSV의 업무 검색 importer, runner와 execute manifest/DB/worker를 잇는 실제 연결 검증이다. `cs_ticket_import.py`는 합성 CSV를 접수 API에 보내는 별도 CLI이며 1차 기동 필수나 해결사례 importer의 대체재가 아니다. `cs_fault_match_run.py`는 개발 manifest와 해결사례를 사용하는 실험 도구이며 업무 DB 적재 도구가 아니다.

CLI 구현, 원자료 확보, 실제 DB 적용, 모델 처리 성공, 업무 전체 완료를 따로 기록한다. dry-run·고정 기대값·과거 시뮬레이션만으로 후자를 완료 처리하지 않는다.
