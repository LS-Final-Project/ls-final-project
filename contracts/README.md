# API 계약 수정 안내

계약의 원본은 각 기능의 Pydantic 모델과 FastAPI 라우터다. OpenAPI, 브라우저 예시와 TypeScript 타입은 원본에서 생성한다. 서비스별 파일을 나눠 C/S를 바꿀 때 TFT 및 플랫폼 계약 파일이 함께 수정되지 않도록 구성했다.

## 수정할 파일

| 변경 대상 | 직접 수정 | 명령으로 생성 |
| --- | --- | --- |
| C/S 문의 | backend/api/cs/tickets/schemas.py, routes.py, backend/api/cs/examples.py | docs/openapi/cs.json, frontend/src/mocks/generated/cs.json |
| TFT 프로젝트 | backend/api/tft/projects/schemas.py, routes.py, backend/api/tft/examples.py | docs/openapi/tft.json, frontend/src/mocks/generated/tft.json |
| TFT 부서 인력요청 | backend/api/tft/staffing_requests/schemas.py, routes.py, backend/api/tft/authz.py, backend/api/tft/examples.py | docs/openapi/tft.json, frontend/src/mocks/generated/tft.json |
| 플랫폼 사용자 | backend/api/platform/auth/schemas.py, routes.py | docs/openapi/platform.json |
| 프론트 연결 | frontend/src/api/cs.ts, tft.ts, platform.ts 중 해당 서비스 | frontend/src/api/generated/ 아래 해당 TypeScript 파일 |
| HTTP 공통 형식 | backend/shared/models.py, errors.py, backend/api/common/ | 공통 변경은 3개 서비스 산출물에 영향 |

담당 인원은 Jira의 최신 배정을 따른다. 이 경로 구분이 인원을 새로 배정하는 것은 아니다. OpenDesign 프로토타입은 수정하지 않는다.

최종 기획서를 기준으로 [C/S 다섯 분류 필드](cs/tickets.md), [출처 구분](../docs/data-contract.md#기획서의-출처-표기와-api-연결), [평가 계산법과 안전 조건](../docs/evaluation.md)을 적용한다. 명세를 추가한 것과 실제 DTO·평가기 구현 완료를 구분한다.

## 예시: C/S 필드 추가

1. tickets/schemas.py에서 응답 모델을 수정한다. 처음 확장하는 호환 필드는 기본값이 있는 선택 필드로 시작한다.
2. cs/examples.py에 합성 예시를 추가한다. 예시는 같은 Pydantic 모델로 검증되므로 필수 필드 누락은 생성 시 실패한다.
3. 저장소 루트에서 실행한다.

```bash
python3 scripts/workspace.py contracts cs
git diff -- backend/api/cs docs/openapi/cs.json frontend/src/mocks/generated/cs.json frontend/src/api/cs.ts
```

이 명령은 C/S OpenAPI 및 예시만 갱신하고 프론트 타입과 소비자 컴파일을 확인한다. 프론트 TypeScript 생성물은 Git에서 제외했다. 다른 팀원이 만든 타입도 빌드 시 로컬에서 다시 생성하므로 타입 생성물 충돌을 해결할 필요가 없다.

```bash
python3 scripts/workspace.py contracts cs --check
```

검사 모드에서는 OpenAPI와 예시 JSON을 수정하지 않고 원본과 일치하는지 확인한다. TypeScript 타입은 로컬에서 생성한다. TFT는 cs 대신 tft, 플랫폼은 platform을 사용한다. 공통 형식을 바꾼 경우에만 all을 사용한다.

## 충돌을 줄이는 규칙

- 새 기능은 해당 서비스 아래 `기능명/schemas.py`, `routes.py`와 테스트 파일로 추가한다. 모든 DTO를 공통 파일 하나에 모으지 않는다.
- 새 라우터는 자기 서비스의 routes.py에만 등록한다. 다른 앱의 main.py와 공통 application.py는 건드리지 않는다.
- operation_id는 `cs_list_tickets`, `tft_list_projects`, `platform_get_me`처럼 서비스와 동사를 포함해 명시한다. Python 함수 이름 변경으로 프론트 계약이 바뀌는 일을 줄인다.
- 서비스별 업무 상태와 오류는 해당 기능에 둔다. 공통 오류 enum을 계속 늘리지 않는다. `ApplicationError(409, "CS_TICKET_VERSION_CONFLICT", "The ticket has changed.")`처럼 정적인 안전 문구로 오류를 확장한다.
- 기존 필드 삭제, 이름 및 타입 변경, 필수화와 enum 변경은 소비자의 수정과 함께 처리한다. 필요한 경우 새 경로 또는 새 API 버전을 추가해 이전 계약을 유지한다.
- 기능 변경과 무관한 전체 포맷, 의존성 갱신 및 다른 서비스 산출물 재생성을 한 커밋에 섞지 않는다.
- 생성 JSON이 충돌하면 어느 한쪽 파일을 고르는 대신 schemas.py와 routes.py의 변경을 합친 뒤 자기 서비스만 다시 생성한다.
- 공통 HTTP 형식 변경은 3개 서비스 생성과 프론트 컴파일을 함께 확인한다. 공통 패키지에 업무별 모델을 넣거나 서비스끼리 직접 DTO를 import하지 않는다.

기존 [API와 작업 계약](../docs/api-contract.md)은 공통 규칙의 설명이다. 매번 업무 필드를 추가할 때 이 문서를 함께 고칠 필요는 없다. [공통 규칙](common/http.md), [C/S 문의](cs/tickets.md), [TFT 프로젝트](tft/projects.md), [TFT 부서 인력요청](tft/staffing-requests.md), [플랫폼 인증](platform/auth.md), [플랫폼 평가 저장](platform/evaluations.md)을 참고한다. 역할·범위별 권한 행렬, C/S·TFT 업무 상태 전이와 화면별 행동은 [화면·상태·권한](platform/permissions.md)(KAN-57 설계 문서)을 참고한다. 제안이 포함돼 있으므로 확정·제안·미확정 구분은 해당 문서의 표기를 확인한다.

## 실제 API와 모의 응답

문의(KAN-102)와 프로젝트(KAN-119) 목록·상세는 DB를 읽는 `partial` 구현이며 기본 설정에서 `local`·`test`에만 응답하고 production에서는 404로 닫는다. KAN-178의 명시 활성화 JSON 중계는 [인증 계약](platform/auth.md#업무-json-중계와-호출자-검증-kan-178)의 플랫폼 호출자 검증과 기존 KAN-78 업무 권한 검사를 함께 요구한다. 로컬 프론트의 모의 응답 모드에서는 생성된 합성 예시를 반환한다. 실제 API 실패 시 자동으로 모의 응답으로 전환하지 않는다. 로그인 계정과 DB 준비 상태는 모의 응답을 사용하지 않는다. production 프론트 빌드는 모의 응답을 활성화할 수 없으며 백엔드 개발 계정 선택도 production에서 등록되지 않는다.
