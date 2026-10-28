# HTTP 공통 규칙

공통 모델은 backend/shared/models.py다. 성공은 data와 meta.request_id, 오류는 error와 meta.request_id를 사용한다. 목록은 해당 서비스의 data.items와 data.next_cursor다. 요청 ID는 UUID 형식만 이어받고 인증이나 멱등성 키로 사용하지 않는다.

공통 HTTP 규칙과 작업 및 SSE 설계는 [API 계약](../../docs/api-contract.md)을 따른다. 작업 저장·멱등 접수·상태·SSE의 구현 범위는 [플랫폼 작업 계약](../platform/jobs.md)이 기준이며 현재 플랫폼 평가 실행에만 연결돼 있다. 메일 발송은 아직 구현하지 않았다. 서비스별 실제 제공 경로는 docs/openapi 아래 생성 문서가 기준이다. HTTP 문서는 로컬에만 노출하며 production에서는 숨긴다.

기능 오류는 ApplicationError에 정적인 코드와 문구를 전달한다. 고객 원문, 비밀번호, 토큰 및 외부 제공자의 오류 본문은 넣지 않는다. 타입과 응답 검사는 공통 변경 시 3개 서비스를 함께 수행한다.
