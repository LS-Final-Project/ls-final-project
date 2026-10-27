# 공통 데이터 계약

KAN-55 검토안. 외부 데이터는 어댑터에서 아래 형식으로 정규화한다. 영속 ID, 시간, 문서 판본과 근거는 두 업무에서 같은 의미로 사용한다.

## 식별자와 시간

| 필드 | 계약 |
| --- | --- |
| `id` | 서버 생성 UUID v4. URL과 내부 참조에 사용하며 외부 ID와 분리한다 |
| `source` | 원천 종류. `training_csv`, `demo_fixture`, `manual`, `microsoft_graph` 등 등록된 값 |
| `source_instance` | 같은 종류 안의 데이터셋 판본, 메일함 등 원천 인스턴스의 불변 키 |
| `external_id` | 원천이 부여한 ID. 문자열을 변형하지 않는다. 수동 입력은 null 허용 |
| `schema_version` | 데이터 계약 판본. 초기 `1.0`. 호환되지 않는 변경은 새 판본 |
| `version` | 업무 객체의 양의 정수 판본. 변경마다 증가하며 낙관적 동시성 검사에 사용 |
| `occurred_at` | 원천 사건 시각. 불명확하면 null. 접수 시각을 해결 시각으로 대체하지 않는다 |
| `known_at` | 해당 사실을 시스템이 이용할 수 있게 된 시각. 과거 재현의 입력 제한 기준 |
| `ingested_at` | 현재 환경에 적재한 실제 시각. 재적재 때 known_at을 이 값으로 덮지 않는다 |
| `provenance_kind` | `source_fact`, `human_input`, `ai_draft`, `system_record` |
| `data_origin` | `synthetic`, `demo`, `real` 중 하나. UI와 평가 결과에도 전달 |
| `derivation_kind` | 기획서의 출처 구분. `PROVIDED`, `DERIVED`, `SYSTEM`, `ASSUMED` 중 하나. 실제·합성 여부와 별개 |

### 기획서의 출처 표기와 API 연결

기획서 6.1에서 data_origin으로 부른 네 가지 출처 구분은 공통 업무 모델의 `derivation_kind`에 연결한다. 기존 API의 `data_origin`은 실제·합성 여부를 유지한다. 같은 필드의 enum을 서로 다르게 쓰거나 PROVIDED를 실제 회사 데이터로 해석하지 않는다. `provenance_kind`는 사실·사람 입력·AI 초안·시스템 기록의 작성 주체 구분이며 이 두 축을 대신하지 않는다.

| 예시 | `data_origin` | `derivation_kind` |
| --- | --- | --- |
| 제공된 교육용 C/S 티켓 | synthetic | PROVIDED |
| 합성 티켓에서 계산한 반복 고장 묶음 | synthetic | DERIVED |
| 시연을 위해 가정한 장비·가용성 | demo | ASSUMED |
| 시연 중 생성한 요청·회신·조치 기록 | demo | SYSTEM |
| 추후 제공받은 실제 회사 원본 | real | PROVIDED |

파생 레코드는 근거 ID와 각 근거의 출처를 보존한다. 합성·가정 근거를 포함한 결과를 real로 승격하지 않으며, 실제·합성 자료를 섞은 실행은 demo로 표시하고 실행 manifest에 원천별 구분을 남긴다. 외부 제공 자료나 가정 여부가 불명확하면 확인 필요로 남기며 임의의 기본값으로 확정하지 않는다.

이 명세는 KAN-55의 어댑터·저장·UI·평가 전달 계약이다. 현재 대표 목록 DTO에 derivation_kind가 구현된 것은 아니며 기존 data_origin을 깨지 않고 기능별 DTO와 합성 예시를 확장한 뒤 OpenAPI 및 소비자를 함께 검사한다.

시각은 RFC 3339 UTC의 `Z` 표기로 저장하고 전달한다. 기간과 날짜만 있는 필드는 `YYYY-MM-DD`와 기준 시간대를 별도로 둔다. 날짜를 임의의 정확한 시각으로 바꾸지 않는다. ID 유일성은 최소 `(source, source_instance, entity_type, external_id)`로 정의하고 null 외부 ID에는 적용하지 않는다.

사용자 ID는 내부 UUID와 `(tid, oid)` 유일 키를 함께 가진다. 후보 인력 ID와 로그인 사용자 ID는 별개다. 로그인 계정이 없다는 이유로 후보를 누락시키거나, 후보를 등록했다는 이유로 로그인 권한을 주지 않는다.

TFT 시연 범위는 한 회사 내부의 부서 협업으로 확정했다. 구체적인 회사는 아직 선정하지 않았다. 회사 구분과 조직 ID를 보존하는 설계안을 사용하며, 조직에 여러 업무 분야 태그를 연결할 수 있게 한다. 설계, 토목/구조, 시공/운송, 품질/시험, 사업관리 등의 분류 태그를 실제 부서명이나 로그인 역할로 대체하지 않는다. Microsoft의 tid만으로 소속 회사를 추정하지 않는다.

학습 데이터처럼 과거 known_at이 없는 자료는 어댑터가 적용한 가정과 근거를 명세에 기록한다. 해결 기록은 해결 시각 이후에만 사용할 수 있다는 가정을 표시한다. 미래의 해결 내용, 회신과 수정판을 이전 시점의 판정에 넣지 않는다.

## 문서와 인용

`document_id`는 논리 문서의 ID이고 `document_version_id`는 불변 버전의 ID다. 새 파일을 기존 GCS 객체 위에 덮어쓰지 않는다. 버전에는 원본 SHA-256, GCS generation, MIME, 크기, 원천 판본과 정규화 텍스트 SHA-256을 둔다. 다른 정규화기나 OCR로 텍스트를 재생성하면 파생 텍스트 판본도 구분한다.

인용에는 AGENTS.md의 `doc_id`, `version`, `page`, `char_start`, `char_end`, `table_cell`, `image_region`을 유지한다. `doc_id`는 `document_id`, `version`은 `document_version_id`를 가리킨다. 인용 범위는 다음 기준을 따른다.

- `page`는 PDF 기준 1부터 시작한다. 페이지가 없는 Markdown은 null이다.
- `char_start`, `char_end`는 저장된 정규화 텍스트의 Unicode 코드 포인트 기준 반열린 구간이다. UTF-8 바이트나 JavaScript UTF-16 인덱스로 해석하지 않는다. 해당 텍스트 hash도 함께 저장한다.
- `table_cell`은 `table_id`와 0부터 시작하는 `row`, `column`이다. 병합 셀은 추출 시 매핑을 보존한다.
- `image_region`은 페이지 회전 보정 후 좌상단 원점의 0~1 정규화 좌표 `x`, `y`, `width`, `height`다. `page`가 필수이며 영역은 페이지 범위를 벗어날 수 없다.
- 텍스트 범위, 셀, 이미지 영역 중 하나 이상의 위치가 있어야 한다. 추적 가능한 위치가 없으면 인용으로 채택하지 않고 검토 필요로 남긴다.

`excerpt`는 UI 미리보기이며 원문 판본을 대신하지 않는다. 원본 열람 및 다운로드에도 권한을 다시 확인한다. 검색 결과에 GCS 공개 URL이나 OAuth 토큰을 넣지 않는다.

## 메일과 파일 연결

메일은 내부 메시지 ID와 메일함 범위의 Graph immutable ID를 매핑한다. 이동에 따라 바뀔 수 있는 기본 메시지 ID나 제목만으로 중복을 판단하지 않는다. Graph 요청에는 지원되는 경로에서 `Prefer: IdType="ImmutableId"`를 사용한다. 같은 메일함 안에서의 안정성 범위를 따른다. [Microsoft Graph 불변 ID](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)

`internetMessageId`, `conversationId`, `In-Reply-To`, `References`는 상관관계 후보 근거다. 제목이나 AI 분류만으로 문의, 프로젝트, 부서 질문을 확정 연결하지 않는다. 연결 후보와 사람이 확정한 연결을 분리하고, 여러 질문에 답하는 메일은 질문별 채택 기록을 둔다.

메일 수신자가 새로 생겨도 자동으로 서비스 사용자를 만들지 않는다. 메일함 접근 권한과 업무 객체 접근 권한을 각각 검사한다. 동기화 커서는 메일함과 폴더별로 저장하고, 메시지 저장 후 커서를 전진시킨다. 커서 무효화 시 재동기화하되 같은 메일을 중복 적재하지 않는다.

파일에는 별도 내부 ID를 둔다. 본문, 첨부, 증빙, 인용은 파일 ID와 불변 버전을 참조한다. 같은 hash라는 이유만으로 다른 프로젝트의 파일 접근 권한을 합치지 않는다. 업로드 완료 검증 전의 파일은 AI 처리나 다운로드에 사용하지 않는다.

## 업무별 데이터와 결정

| 업무 | 별도 기록 |
| --- | --- |
| C/S | 접수 입력, AI 분류, 규칙 계산, 사람 수정, 답변 초안, 발송 승인과 결과 |
| 개선요청 | 문제 묶음, 후보 근거, 담당자 검토, 전달, 회신, 조치 반영 시각, 추가 발생 및 재요청 |
| TFT | 프로젝트, RFP 요구, 필요한 역할, 부서 인력 요청, 추천 후보, 경험 근거, 가용성 확인, 구성안 판본, 지정 책임자 결정 |

LLM 분류 출력은 `category_code`, `secondary_category`, `urgency_flag`, `provided_info`, `return_subtype` 다섯 필드다. 정의와 허용값은 [C/S 계약](../contracts/cs/tickets.md), 계산법과 정답 검수는 [평가 명세](evaluation.md)를 따른다. `priority`, `assigned_dept`, `product_family`는 규칙 결과로 별도 저장한다. `build_input()`의 입력 화이트리스트를 통해서만 LLM 입력을 만든다. 이 일반 API 계약이 입력 화이트리스트를 확장하지 않는다.

TFT는 미기재, 미확인, 미충족을 구분한다. 가용성은 대상 기간, 투입률, 확인 주체, 확인 시각과 근거가 있어야 확인 완료로 취급한다. 후보 추천과 책임자 확정은 각각 판본을 참조하며 이전 구성안을 덮어쓰지 않는다.

프로젝트의 `responsible_user_id`는 로그인 가능한 내부 사용자 ID를 참조한다. 해당 프로젝트의 `sales` 또는 `manager` 중에서 관리 권한이 있는 `manager`가 지정 및 변경한다. 추천 후보 인력 ID나 이메일을 책임자 사용자 ID 대신 사용하지 않는다. 변경 이력에는 프로젝트 ID, 변경자 ID, 이전 및 신규 책임자 ID, 사유와 변경 시각을 둔다. 최종 확정 기록에는 확정자 ID와 대상 구성안 판본을 보존한다.

## 교체 가능한 인터페이스

| 포트 | 입력과 결과 | 교체 시 검증 |
| --- | --- | --- |
| `SourceAdapter` | 원천 레코드에서 식별자, 출처, 시점과 정규화 객체 생성 | 누락 및 오매핑, 재적재 중복, 과거 시점 재현 |
| `PolicyProvider` | 정책 판본과 결정론적 규칙표 조회 | 같은 입력과 판본에서 결과 재현 |
| `DocumentReader` | 파일 버전에서 본문, 표, 이미지와 위치 생성 | OCR 실패 및 부분 처리, 원문 역추적 |
| `EmbeddingProvider` | 원문 청크에서 모델 판본, 차원, 벡터 생성 | 다국어 검색, 인덱스 분리, 사용량 |
| `Retriever` / `Reranker` | 접근 범위가 제한된 후보에서 근거 목록 반환 | 권한 누출, FTS와 벡터 결합, 순위 품질 |
| `ModelProvider` | 단계별 입력과 출력 스키마에서 구조화 결과 생성 | 품질, 스키마, 토큰, 비용, 지연, 재시도 |
| `MailGateway` | 메일함 동기화, 승인된 발송 의도 제출 및 상태 조회 | 중복 수신, 불명확한 발송 결과, 권한 |

초기 구현은 Python 타입과 DTO로 포트를 정의한다. 프레임워크 종류나 외부 제공자의 응답 객체가 업무 API로 직접 새어 나오지 않도록 한다. 실제 회사 연동은 이번 범위에 없으며, 발표에서는 어댑터 교체와 검증 지점으로 설명한다.

## C/S 검수 입력 인수 조건 (KAN-83·91)

프로젝트 2 재기획 중에는 C/S와 공통 입력 경계만 먼저 다룬다. 아래는 KAN-92 입력
어댑터와 KAN-93 검수표에 넘길 자료의 인수 조건이며, 새 API 필드나 잠긴 실험 manifest가
아니다. KAN-95의 C/S 검수를 TFT 사례(KAN-94) 전체 완료와 묶어 대기시키지 않는다.
혼합 티켓의 프로젝트 2 잔여 범위를 삭제하거나 C/S 부분만으로 전체 완료 처리하지 않는다.

### 확인한 원천과 확인하지 않은 내용

2026-10-27 Drive의 **파일 메타데이터와 안내 README만** 확인했다. 문의·해결 사례·정답·
과거 감사 결과·upload_manifest 본문은 읽지 않았다. 다음 크기와 수정 시각은 원격 메타데이터이며
본문 SHA-256, 원본 간 내용 일치, 행 수 또는 split 무결성을 검증한 값이 아니다.

| 원천 | Drive 파일 ID | 크기(bytes) | 수정 시각(UTC) | 인수 시 구분 |
| --- | --- | ---: | --- | --- |
| tickets.csv | `1pc1DPs0RAtQlFTCPhgeXNRL0AmI7uB-q` | 4053369 | 2026-10-20T05:37:28.813Z | 통합 문의 제공본. 개발 전용 파일로 승인하지 않음 |
| tickets.jsonl | `1pp1xa0nJ9DPzhFDqtb0I6p4JampgC_gv` | 9134861 | 2026-10-20T05:37:31.985Z | 같은 문의의 다른 표현이라는 제공자 설명. 별도 표본으로 합산하지 않음 |
| cs_tickets_resolution_v1.xlsx | `1IUp8ShB13AXGUdIM0D-pwz4RHrppRv_3` | 2470115 | 2026-10-20T05:37:23.635Z | 문의·해결 사례 열람용 제공본. CSV와 중복 집계하지 않음 |
| resolution_cases.csv | `1xGaJQCUZVeWs8pCQ3UcZ_6f_Z44fHeKQ` | 575329 | 2026-10-20T05:37:25.985Z | 해결 후 근거. 최초 문의 입력과 분리, known_at 확인 필요 |
| upload_manifest.json | `1LALzuyCTk5_QbNexQBESDsbSug_AdPKw` | 10183 | 2026-10-21T01:25:21.684Z | 존재만 확인. split/family/사용 이력 manifest로 인정하지 않음 |

[제공자 README](https://drive.google.com/file/d/16eiIE9byGh4UsfSYhktMOqbKtJnIxtcg/view)는
교육용 합성 자료이며 실제 고객 문의·고장 통계가 아니라고 명시한다. 문의4,000행·해결사례800행·
문서107개는 **제공자 설명**이며 이번 집계 결과가 아니다. 제공 원본은 `synthetic/PROVIDED`,
정제·분리 결과는 부모 판본과 처리 근거를 보존한 `synthetic/DERIVED`로 구분한다.
JSONL/XLSX/CSV 표현 간 동일성 설명과 개별 사례의 family 중복 검증은 별개다.
합성 제품을 이름이 비슷한 실제 제품과 동등한 것으로 취급하지 않는다.

조회한 [C/S 폴더](https://drive.google.com/drive/folders/1I_aeMmjzbPNqWV9BDGF0ejL3O8RDCJOH)의
직접 자식8개 목록에는 별도 train/valid 파일이 없었다. 이는 모든 저장소에 분리본이 없다는
뜻이 아니다. Drive 검색의 문서 MIME 제한 때문에 CSV/JSON 검색 결과만으로 부재를 판단하지
않았고, 파일 ID별 메타데이터와 폴더 목록으로 보완했다. 현재 커넥터 반환에는 요청한 MD5·
version이 없어 최신 checksum은 확인하지 못했다. 10/25 기록의 MD5를 최신 SHA-256으로 대체하지 않는다.

### 데이터 관리자가 제공할 최소 인수 묶음

원본과 `data/`는 수정하지 않는다. 2026-10-27 사용자는 원본 보존 후 train/valid만 복사하는
관리 작업을 허용했다. 이 예외는 `scripts/cs_dev_bundle.py`를 통한 기계적 분리이며, 통합
파일을 평가 소비자나 모델 입력으로 사용하는 승인이 아니다. 시험 행도 분리 과정의
프로그램 메모리에서는 읽으므로 접근 이력을 남기고, 본문·정답·시험 ID를 출력·저장하지
않는다. 수행자 오새결은 AI·평가 구현 담당이며 독립 검수자로 표시하지 않는다.

| 인수 항목 | 확인할 내용 | 미확보 시 처리 |
| --- | --- | --- |
| 물리적으로 분리한 train/valid 입력 | 시험 행·시험 정답을 포함하지 않는 별도 파일, 원천 ID·판본·분리 규칙과 수행자 | 통합 tickets 파일을 대체 입력으로 쓰지 않음 |
| 대상 manifest | 파일별 SHA-256·크기·split·건수·대상 ID, 생성 시각·검수자·허용 목적·원본/파생 연결 | 파일명·수정 시각·upload_manifest 존재만으로 승인하지 않음 |
| 입력/정답 분리 | 입력은 build_input 허용 필드만, gold와 사후 처리·해결 이력은 별도 파일/접근 경로 | 골드 파일을 모델·검색 입력에 포함하지 않음 |
| family 검수 | 반복 문의·동일 고장/장비·파생 문서의 연결 근거, split 간 겹침·미확인 건수 | ticket_id가 다르다는 이유로 독립 표본 확정 금지 |
| 시점과 출처 | data_origin/derivation_kind, known_at 근거·가정, 해결/회신/개정판의 사용 가능 시각 | modifiedTime을 known_at으로 대신하지 않음 |
| 사용 이력 | 실행자·시각·대상 파일 hash·접근 split/열·목적·출력 열람자·튜닝/선택 반영 여부 | 모르는 값은 미확인. 독립 최종 여부는 아래 평가 명세에 따름 |
| 검수 잠금 | 정답 판본·필드별 검수 수준·불일치 조정·보류 사유·지표별 분모/제외 목록 | 미검수 gold를 정답으로 확정하거나 누락 gold를 null 정답으로 만들지 않음 |

manifest와 파일 인수 이후에만 승인된 개발 자료에서 PHONE 주석 제거 판본·전후 hash·
제거/보류 건수와 채널별 분모를 검증한다. 현재 `cs-classification-input-v2` 합성 회귀 통과는
제공 개발 자료의 처리가 완료됐다는 증거가 아니다. 상세 [사용 이력 판정](evaluation.md#cs-사용-이력과-검수-준비-kan-83)을 함께 적용한다.

### KAN-93에서 준비하고 KAN-95에서 판정할 검수표

검수표에는 `case_id`, 원천 판본·split·family와 확인 수준, 입력 근거 위치, 필드별 원천값/
제안 gold/검수 상태, 작성자·검수자·판정 시각, 불일치·보류 사유를 남긴다. 검수용 정답과
해설은 모델용 입력에서 분리하며, 모델 예측을 보고 정답을 맞추지 않는다.

| 검수 대상 | KAN-93 준비 근거 | KAN-95 잠금 사항 |
| --- | --- | --- |
| category_code | 허용 개발 문의의 처리 의도와 근거 위치 | 주분류 코드·모호 사례 조정 |
| secondary_category | 주분류와 다른 명시적 의도 목록, 없다는 근거 | 다중 보조 의도 선택 기준, 비해당 null과 미검수 구분 |
| urgency_flag | PHONE 주석과 본문 근거 분리, PHONE/비PHONE·희소 SAFETY/SITE_DOWN 구분 | 본문 근거의 긴급도와 원천 라벨 불일치 조정 |
| provided_info | 입력에 실제 제공된 정보의 위치, 검수된 정보 코드북 판본 | 허용 코드 집합·정답 집합, 원값·시리얼 노출 없이 제공 여부 표시 |
| return_subtype | POL-RETURN과 문의 근거, 반품 여부·확인 가능성 | DEFECT/NON_DEFECT/비해당 null. 미확인은 별도 상태로 보존 |
| 회신·해결 이력 | 문의/장비/판본 연결과 당시 known_at, 아직 없는 정보 | 과거 사실·회신 주장·확인 필요 구분, 미래 정보 누설 차단 |
| 인용 안전 | 근거 문서 ID·판본·위치와 금지 인용 여부 | FLT-042 차단, RMA/방문/보증 승인 생성 금지 |
| 분모·제외 | 지표별 대상·원천 이상·검수 누락/충돌 목록 | 한 지표의 제외를 다른 지표로 전파하지 않음 |

[KAN-104의 기록](https://lsteam4.atlassian.net/browse/KAN-104?focusedCommentId=10248)에 따르면
반품298건의 하위유형 정답과 경과시간 이상3건의 처리가 검수 대상이다. 이는 기존 검사자의
보고이며 이번 원문 재검수 결과가 아니다. 부서 정답을 역산해 gold를 만들지 않는 것이 원칙이며,
반품 하위유형만 2026-10-27 팀 합의([KAN-95 댓글 10324·10325](https://lsteam4.atlassian.net/browse/KAN-95?focusedCommentId=10325))로
예외를 둔다. `RT-ALL-RETURN-NON_DEFECT`는 `NON_DEFECT`, `RT-<제품군>-RETURN`은 `DEFECT`로 변환하고,
대조 전에 고정한 원문 판정은 정답이 원문과 모순되지 않는다는 근거로 따로 보존한다. 이 정답으로 낸
KAN-104 반품 경로 일치는 정의상 일치이므로 독립 검증이 아니다. 경과시간 이상을 이유로 해당 문의를 분류·긴급도 분모에서도 일괄 제외하지 않는다.
검수 양식 준비와 실제 사례 작성·교차검수 완료는 구분한다.

KAN-104 소비자는 반품 하위유형 정답을 분리 자료 manifest의 `return_subtype_gold` 항목으로 받는다
(`tests/cs/cs_dev_data.py`). 파일은 UTF-8 CSV의 `ticket_id,return_subtype`이며 값은 `DEFECT`,
`NON_DEFECT`, 빈 값이다. 검수표 원본의 근거·판본·검수 상태를 이 두 열로 축소해 잃지 않도록
별도 보존한 뒤, 승인된 개발 반품 대상만 소비자 형식으로 내보낸다. 빈 값은 미확인으로 보고되므로
298건 전부의 재현 확정과 같지 않다. manifest의 `path`는 manifest 폴더 안을 가리켜야 하며 밖(상위 경로·절대 경로·밖으로 이어진 링크)은 거부한다. 소비자는 행을 읽기 전에 manifest의 SHA-256과 비교하고
검수 판본(`review_version`)이 없으면 거부한다. 열 이름, 중복 ID, 대상 반품 집합과의 정확한 일치(누락·추가
행), 허용값을 검사해 하나라도 어긋나면 실패한다.


### 개발 분리와 전건 검수 준비

`python scripts/cs_dev_bundle.py --source <읽기전용 원본> --output <새 artifacts 경로>
--source-ref <원천 판본 참조> --authorization-ref <분리 승인 참조>`를 한 명령으로 실행한다.
원본 전체를 한 번 읽어 train/valid만 남기고 불명 split·중복/누락 ID·건수 불일치는 실패한다.
출력 디렉터리는 기존 경로 및 data 하위를 거부하며 디렉터리0700·파일0600으로 생성한다.
Git 저장소 안에서는 `git check-ignore`로 제외 경로임을 확인하지 못하면 생성하지 않는다.
공개 저장소에는 실제 CSV·검수 결과를 커밋하지 않는다. 이 명령은 원격 전송을 하지 않는다.

출력은 `tickets.train.csv`, `tickets.valid.csv`, KAN-104 호환 `tickets.dev.csv`, `manifest.json`이다.
manifest의 `tickets`는 PR73 소비 형식을 유지하고, `partitions`와 `provenance`는 파일별 hash·
건수·원천 hash·승인 참조·처리 판본·시간·버린 시험 행 수를 보존한다. 검수 상태는 `pending`이다.
로컬 제공 패키지의 원천을 사용할 때 Drive 최신 파일과 바이트 동일성을 확인하지 않았다면
그 사실도 source_ref에 표시한다. 생성된 CSV는 규칙/정답 검수용으로 원천 정답 열이 포함되며,
그대로 모델·검색에 넣지 않는다. 실제 분리 결과와 전달 위치·접근 이력은 Jira에 기록한다.

전건 검수 양식은 아래 열을 사용한다. 실제 파일은 비공개 artifacts에 보관하고 공개 문서에는
사례 본문을 복사하지 않는다. 반품 대상은 개발 RETURN 전체, 보조 분류 대상은 null 오류도
찾도록 개발 문의 전체다. 빈 초안은 완료 gold가 아니며 `return_subtype_gold`로 연결하지 않는다.

```csv
case_id,field,source_ref,evidence_location,policy_version,candidate_value,review_status,reviewer,reviewed_at,reason,comparison_result
```

- 원문과 정책으로 먼저 후보·근거를 작성한다. 반품 후보 작성 단계에서는 assigned_dept와
  routing_rule_id를 가리고, 1차 결과의 판본/hash를 고정한 뒤에만 대조한다.
- 보조 분류는 [사용자 결정](evaluation.md#보조-분류와-정보-추출)을 따른다. `candidate_value`는
  JSON 값으로 기록해 `null`과 빈 셀(미작성)을 구분한다. 상태는 `pending`·`confirmed`·`disputed`로
  구분하고 미판정·정책 불명확 사유를 별도로 보존한다. 모델 후보는 사람 확정 전 pending이다.
- Gemini 전건 사전 검토는 선택 가능한 보조 수단이며 파일 전달·사람 라벨링의 선행 조건이
  아니다. 아직 실행 승인·모델·호출량이 확정된 것은 아니다.
  원천 라벨·담당부서·규칙 ID·사후 해결 정보는 모델에 전달하지 않는다. 모델 출력만으로
  정답 확정이나 사람 교차 검수 완료를 표시하지 않는다. 실제 호출 전에 입력 마스킹·정책
  판본·최초 호출/재시도 구분·사용량 기록 및 최대 호출량을 검증한다.
- 사람 교차 검수의 전건/표본 범위는 별도 결정한다. 불일치·근거 부족뿐 아니라 일치 건에도
  공통 오류가 있을 수 있다. S15/current_fw는 전달받은 오류 후보이며 원문 확인 전 확정하지 않는다.
- 우선순위 adjusted=N, 반품 미확인 보류, SLA 경과시간 전용 제외를 각각 기록한다. 원천 건수는
  인수 시 재확인하며 한 지표의 제외를 다른 지표에 전파하지 않는다.

### 개발 입력 정제 검수 도구 (KAN-93)

`python -m scripts.cs_review_inputs --manifest <인수 manifest.json> --manifest-sha256 <인수 기록 hash> --output <새 artifacts 경로>`는
KAN-83에서 승인된 개발 사본에만 적용한다. 통합 원본을 분리하거나 평가·모델 호출을 하지
않는다. 인수 manifest hash·개발 split·폴더 내부 경로를 확인하고 기존 KAN-104 개발 로더로
파일 hash·행 수·ID를 검사한다. 실행 중 manifest 변경은 실패하며 기존 전달 ZIP은 보존한다.

제공 CSV의 inquiry_subject/inquiry_body를 subject/body로 연결하고 channel/body_language와
함께 기존 `build_input`에 전달한다. 이 연결은 제공 자료 검수 전용이며 KAN-92의 공통
입력 어댑터·외부 ID 매핑·민감값 식별 구현을 대신하지 않는다. 라벨·정답·규칙 ID·사후 기록은
payload로 전달하지 않는다. 민감값 목록이 아직 인수되지 않아 `sensitive_values={}`를 명시하고
기존 이메일·라벨형 시리얼 패턴만 적용한다. 이름·주소·라벨 없는 식별자 등 전체 개인정보가
제거됐다는 보장은 없으며 **모든 정제 후보도 사람 개인정보 검수 pending, model_ready=0**이다.

| 파일 | 내용과 사용 경계 |
| --- | --- |
| candidates.jsonl | 성공 사례의 case_id·privacy_review_status와 정제 payload 4필드. 모델 실행 승인 아님 |
| audit.jsonl | 모든 사례의 ID·split·채널·성공/보류·안전한 오류 코드·주석 제거 수·원천 라벨 기반 검수 그룹. 본문·전체 gold 행은 포함하지 않음 |
| review.boundaries.csv | 전건 개인정보와 선정된 경계별 미검수 질문·원문/후보 위치·판정 기록 칸. 본문/정답을 복사하지 않는 사람 검수 전용 |
| summary.json | 입력/처리 판본·manifest/개발 파일 hash·출력 파일 hash·시간·전체/성공/보류·split/채널별 집계. 사례 ID 없음 |

검수 그룹은 PHONE, 원천 SAFETY/SITE_DOWN, 보조 의도·반품·회신 존재, 원천 FLT-042 참조다.
원천 라벨 기반 그룹은 확정 gold나 실제 위험 판정이 아니며 모델 payload와 별도로 저장한다.
회신 그룹은 customer_followups 양수 여부만 표시하고 회신 본문·시점·해결 이력을 아직
검수하지 않는다. 금지 문서 참조 표시 역시 실제 검색·인용 차단 검증을 뜻하지 않는다.
모든 그룹 건수는 중복될 수 있으므로 합을 전체 사례 수로 사용하지 않는다.

예상된 입력 오류는 사례별 보류로 남기고 원문을 저장하지 않는다. 예상 밖 오류는 안전한
코드로 작업 전체를 실패시키며 부분 결과를 성공으로 내보내지 않는다. 출력은 기존 분리
도구와 같은 Git 제외 경로·0700/0600·기존 파일 보존 규칙을 사용한다. KAN-95는 이 목록에서
원문과 정제 결과를 대조하고 오탐/누락·정책 판본·gold·family·known_at을 별도 검수한다.
원문 단위 사람 검수·오탐률·민감정보 완전성·실제 품질과 KAN-93 전체 완료는 아직 미확정이다.


검수 도구 v2의 `review.boundaries.csv`는 전건 INPUT_PRIVACY와 PHONE 주석 제거/제거 없음/입력 보류,
원천 SAFETY/SITE_DOWN·보조 의도·회신·금지 참조별로 한 행씩 준비한다. 반품 정답 양식은
기존 `review.return_subtype.csv`를 사용하며 중복 생성하지 않는다. 그룹은 겹칠 수 있고 선정에
원천 라벨을 사용하므로 독립적인 맹검 정답 판정용으로 사용하지 않는다. 그룹에 없는 사례도
위험·복합 의도가 없다고 판정한 것이 아니다. `summary.boundary_review.checks`는 0건 항목도
보존하며, 금지 참조 0건을 차단 검사 성공으로 해석하지 않는다.

- `case_id_json`은 ID의 JSON 문자열이다. CSV 셀에서 수식처럼 시작하는 외부 ID도 텍스트로
  보존하며, 프로그램에서 사용할 때 JSON으로 디코딩한다.
- `source_sha256`는 승인 개발 CSV의 판본, `source_record`는 헤더를 제외한 1부터 시작하는
  논리 레코드 번호다(멀티라인 본문이 있어 물리 줄 번호와 다름). `source_fields`는 확인할
  칼럼 위치이며 의미상 정답 근거 구절을 자동 선정한 값이 아니다.
- `candidate_record`는 함께 생성한 candidates.jsonl의 1부터 시작하는 줄 번호다. 입력 보류
  사례는 빈 값이며, 삭제된 행이나 검수 제외가 아니다. `input_status/input_reason`은 기계적
  정제 결과로 사람의 `review_status`와 구분한다.
- 작성자는 원문/후보를 대조한 뒤 reviewer/reviewed_at/evidence_location/finding/reason을
  기록한다. 생성 시 모두 빈 칸이고 review_status=pending, completed=0이다. 근거 위치에는
  필드와 실제 구절 범위를 적고 민감값 원문을 그대로 복사하지 않는다. 회신은 현재 횟수만
  인수됐으므로 본문·판본·known_at을 추가 확보하기 전 검수 완료로 바꾸지 않는다.

이 CSV 편집은 모델 실행 승인이나 KAN-95 gold 잠금으로 자동 연결되지 않는다. 산출물은
기존과 동일한 비공개 경로에 보관하며 실제 판정·인수 여부는 Jira에 기록한다.
