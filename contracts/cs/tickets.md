# C/S 문의 계약 시작점

원본: backend/api/cs/tickets/schemas.py 및 routes.py. 예시: backend/api/cs/examples.py.

GET /api/v1/cs/tickets의 응답은 SuccessResponse[TicketList]다. 시작 모델에는 내부 UUID, 제목, data_origin과 목록 커서만 있다. 문의 상태, 분류 및 권한 모델은 기능 작업에서 이 서비스의 파일에 추가한다. 현재 실제 경로는 501이며 로컬 브라우저의 모의 응답만 200 예시다.

## 문의 분류 출력 계약

최종 C/S 기획서 4.2의 다섯 필드를 사용한다. 아래는 KAN-103에서 구현할 업무 계약이며 현재 목록 stub에 분류 기능이 구현됐다는 뜻은 아니다. priority, assigned_dept, product_family, 누락 정보와 SLA는 프로그램이 계산한다. 보조 유형이 있다고 별도 문의를 생성하거나 담당 부서를 자동 변경하지 않는다.

| 필드 | 형식과 의미 |
| --- | --- |
| `category_code` | 주된 처리 의도 하나. `TECH_SPEC`, `INSTALL`, `WARRANTY`, `ORDER_LEAD`, `MANUAL`, `DEFECT`, `COMPLIANCE`, `QUOTE`, `FW_UPDATE`, `RETURN` 중 하나 |
| `secondary_category` | 원문에 별도의 처리 의도가 명시된 경우 보조 유형 하나. 주분류와 같은 enum이며 주분류와 같은 값은 금지. 복합 의도가 없으면 JSON null. 여러 보조 의도 중 선택 및 정답 검수 기준은 KAN-95에서 잠금 |
| `urgency_flag` | urgency_codebook의 `SAFETY`, `SITE_DOWN`, `PROD_IMPACT`, `COMMISSION`, `DEADLINE`, `NONE` 중 하나. 우선순위 자체를 생성하지 않음 |
| `provided_info` | 원문에서 제공 여부를 확인한 정보 항목 코드의 중복 없는 배열. info_field_codebook에 있는 코드만 사용하며 없으면 `[]`. 시리얼 원값이나 개인정보를 반환하지 않음 |
| `return_subtype` | 반품 의도가 확인되면 품질·불량 반품 `DEFECT` 또는 비불량 반품 `NON_DEFECT`. 반품 의도가 없거나 근거가 부족하면 null. POL-RETURN 및 routing_rules의 NON_DEFECT 예외에 맞춰 KAN-95에서 정답 매핑 검수 |

다섯 키는 모두 존재해야 하며 허용되지 않은 키·enum, 키 누락과 잘못된 타입은 원시 스키마 위반으로 기록한다. 코드 값은 표시용 한글 이름과 분리한다. 원천 CSV의 빈 값은 어댑터에서 null 또는 빈 배열로 정규화하고 정답 원본을 수정하지 않는다. 분류 실패·보류는 실행 상태로 기록하며 category에 임의의 UNKNOWN 값을 추가하거나 성공으로 숨기지 않는다. return_subtype이 null인 반품은 비불량으로 간주하지 않는다.

규칙 평가는 주분류와 긴급도 및 확인된 입력을 사용한다. 반품 하위유형이 불명확하면 예외 부서를 추측하지 않고 기본 정책과 사람의 확인 절차를 사용한다. secondary_category를 규칙 입력으로 추가하는 변경은 별도 계약과 회귀 검증이 필요하다.

## 입력과 검증

build_input() 한 곳에서 채널, 본문 언어, 제목, 본문만 분류 입력으로 만든다. 제목 없는 채팅은 본문으로 판정한다. 정답 category·secondary_category·urgency·provided_info와 사후 해결 기록, 별도 에러코드·펌웨어 칸을 입력에 섞지 않는다. PHONE 본문의 `Urgency: <정답>` 주석은 원본을 보존한 채 모델용 입력에서 제거하며 처리 판본을 기록한다. 개인정보·시리얼 원값을 마스킹하되 정보 제공 여부를 판정할 수 있는 표시는 보존한다.

KAN-103의 완료에는 정상·빈 보조 유형·복합 의도·불명확한 반품, 잘못된 enum·키·타입, 정답 주입과 PHONE 주석, 시리얼 비노출 및 FLT-042 인용 차단 회귀가 포함된다. 원시 출력과 재시도·폴백 후 결과를 구분하며, 품질 계산과 분모는 [평가 명세](../../docs/evaluation.md)를 따른다. 이 계약의 확장이 입력 화이트리스트나 안전 정책을 확장하지 않는다.

수정 후 `python3 scripts/workspace.py contracts cs`를 실행한다.
