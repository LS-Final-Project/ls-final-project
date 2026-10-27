# C/S 규칙표 (KAN-104)

`backend/engine/cs/rules.py`가 읽는 정책표다. 제공 자료 `ref/`의 파일을 **수정 없이** 복사했다(2026-10-27). 고객·문의 데이터는 두지 않는다.

| 파일 | 내용 | 원본 |
| --- | --- | --- |
| `priority_rules.csv` | 우선순위 규칙 7개(PR-01~07, 위에서부터 처음 맞는 규칙) | `ref/priority_rules.csv` |
| `routing_rules.csv` | 제품군×분류 담당 부서, 제품군 예외, 비불량 반품 예외, 에스컬레이션 후보 | `ref/routing_rules.csv` |
| `sla_policy.csv` | 분류×고객 등급×우선순위별 첫 응답·해결 목표 시간(24x7 달력 시간) | `ref/sla_policy.csv` |
| `product_master.csv` | 제품 코드 → 제품군 | `ref/product_master.csv` |

## 바꿀 때

1. 원본 `ref/` 파일을 고치고 여기에 다시 복사한다. 이 폴더에서 직접 고치지 않는다.
2. `rules.py`의 `POLICY_FILES` 해시를 새 값(`shasum -a 256 *.csv`)으로 바꾼다. 해시가 다르면 규칙 엔진이 시작하지 않는다.
3. 우선순위 규칙 ID·순서·결과, SLA 시계(`24x7 calendar hours`)·위험 기준(75%)을 바꾸면 `rules.py`의 코드도 함께 바꿔야 한다. 표와 코드가 다르면 시작 시 실패한다.
4. `POLICY_VERSION`이 바뀌므로 규칙 결과를 저장·평가할 때 판본이 구분된다.
