# PC별 로컬 업무 실행 (KAN-159)

이 도구는 현재 구현 중이다. 기존 `workspace.py setup/up`이나 조회 스택을 바꾸지 않는다.
메일·실제 Microsoft 인증·공개 서버·evaluation 실행은 1차 범위 밖이다.
원자료 준비·명시 적재 안내는 별도 `local-business-data.md`에서 다룬다.

업무 상태·멱등·lease는 기존 `runtime.jobs/job_control/dispatch_outbox`가 유일한 기준이다.
로컬 SQLite는 단일 driver의 실행 허가와 모델 호출 비용 기록만 저장한다.
같은 job의 실행 허가는 중복 발급하지 않고, 호출 전에 예약을 commit한다.
실패·timeout·사용량/가격 불명은 예약을 유지하고 이후 호출/작업을 막는다.
재시작으로 이 기록을 지우거나 미완료 작업을 자동 재실행하지 않는다.

예약액은 사용자가 정하는 호출 허용 기준이며 공급자 과금의 USD hard cap이 아니다.
최악 입력/출력·추론 토큰과 최신 공급자 가격을 보장하지 않은 추정치를 상한이라고 부르지 않는다.
알려진 초과 비용도 전체를 기록하며 잘라서 예산 내 성공으로 표시하지 않는다.
기본 목표는 $1.50, 허용 예산은 $3 미만이다. 실제 과금/실패는 감독과 단일 driver가 대조한다.

첫 준비 단위는 `scripts/local_business/permit.py`와 offline 회귀 검사다.
아직 실행 CLI·새 DB setup·UI→worker 통합·real smoke가 완료됐다는 뜻은 아니다.
실제 키는 각 PC의 명시적인 개인 파일에서 worker에만 공급하며 값은 출력/커밋하지 않는다.
