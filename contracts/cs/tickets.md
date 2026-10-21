# C/S 문의 계약 시작점

원본: backend/api/cs/tickets/schemas.py 및 routes.py. 예시: backend/api/cs/examples.py.

GET /api/v1/cs/tickets의 응답은 SuccessResponse[TicketList]다. 시작 모델에는 내부 UUID, 제목, data_origin과 목록 커서만 있다. 문의 상태, 분류 및 권한 모델은 기능 작업에서 이 서비스의 파일에 추가한다. 현재 실제 경로는 501이며 로컬 브라우저의 모의 응답만 200 예시다.

문의 분류의 LLM 필드는 category_code, urgency_flag, provided_info, return_subtype 네 개를 유지한다. priority, assigned_dept와 product_family는 결정론적 규칙이 계산한다. 이 계약의 확장이 LLM 입력 화이트리스트나 안전 정책을 확장하지 않는다.

수정 후 `python3 scripts/workspace.py contracts cs`를 실행한다.
