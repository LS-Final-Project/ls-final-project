# C/S 규칙표 (KAN-104)

`backend/engine/cs/rules.py`가 읽는 정책표다. 제공 자료 `ref/`의 파일을 **수정 없이** 복사했다(2026-10-27). 고객·문의 데이터는 두지 않는다.

| 파일 | 내용 | 원본 |
| --- | --- | --- |
| `priority_rules.csv` | 우선순위 규칙 7개(PR-01~07, 위에서부터 처음 맞는 규칙) | `ref/priority_rules.csv` |
| `routing_rules.csv` | 제품군×분류 담당 부서, 제품군 예외, 비불량 반품 예외, 에스컬레이션 후보 | `ref/routing_rules.csv` |
| `sla_policy.csv` | 분류×고객 등급×우선순위별 첫 응답·해결 목표 시간(24x7 달력 시간) | `ref/sla_policy.csv` |
| `product_master.csv` | 제품 코드 → 제품군 | `ref/product_master.csv` |
| `firmware_history.csv` | 제품별 펌웨어 판본·배포일. 개선 후보 펌웨어 규칙의 입력(KAN-202, 2026-10-31 복사) | `ref/firmware_history.csv` |

`firmware_history.csv`는 평가 정답이 아니다. 제공 자료의 참조 표이고 평가 코드와 C/S 정답 분리본은 이 파일을 쓰지 않는다. 규칙표가 아니라 입력 자료라서 해시는 `POLICY_FILES`가 아니라 `REFERENCE_FILES`로 잠근다. 그래서 이 파일은 `POLICY_VERSION`을 바꾸지 않는다. 해시가 다르면 앱이 시작하지 않는 것은 같다.

## 바꿀 때

1. 원본 `ref/` 파일을 고치고 여기에 다시 복사한다. 이 폴더에서 직접 고치지 않는다.
2. `rules.py`의 `POLICY_FILES`(참조 자료는 `REFERENCE_FILES`) 해시를 새 값(`shasum -a 256 *.csv`)으로 바꾼다. 해시가 다르면 C/S 앱이 시작하지 않는다(`backend/api/cs/main.py`).
3. 우선순위 규칙 ID·순서·결과, SLA 시계(`24x7 calendar hours`)·위험 기준(75%)을 바꾸면 `rules.py`의 코드도 함께 바꿔야 한다. 표와 코드가 다르면 C/S 앱 시작 시 실패한다.
4. `POLICY_VERSION`이 바뀌므로 규칙 결과를 저장·평가할 때 판본이 구분된다.
