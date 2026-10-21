# TFT 프로젝트 계약 시작점

원본: backend/api/tft/projects/schemas.py 및 routes.py. 예시: backend/api/tft/examples.py.

GET /api/v1/tft/projects의 응답은 SuccessResponse[ProjectList]다. 시작 모델에는 내부 UUID, 프로젝트 이름, data_origin과 목록 커서만 있다. 현재 실제 경로는 501이며 로컬 브라우저의 모의 응답만 200 예시다.

필요 역할, 부서 요청, 후보, 가용성, 구성안과 책임자 확정은 기능별 디렉터리로 추가한다. 후보 인력과 로그인 사용자를 혼합하지 않고 지정 책임자만 최종 확정하는 기존 결정을 유지한다. 임의의 업무 상태 enum을 공통 패키지에 추가하지 않는다.

수정 후 `python3 scripts/workspace.py contracts tft`를 실행한다.
