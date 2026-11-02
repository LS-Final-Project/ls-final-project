# 고객 이력 가상 장비 시연 데이터 (KAN-205)

기획서 4.3 고객 이력 프로필과 6.1의 가정 테이블 `installed_units`를 채우는 자료다. 원본은
`프로젝트1_고객이력_가상시나리오.xlsx`(시나리오 15개, 2026-10 작성)이며, 그 가운데 **개발 분할에 두 문의가 모두 있는
11개**(S02·S03·S04·S05·S06·S07·S09·S10·S11·S13·S15)만 쓴다. S01·S08·S12·S14는 한쪽 문의가 시험 분할(597건)에 있어
넣지 않는다.

| 파일 | 내용 | 누가 읽나 |
| --- | --- | --- |
| `units_demo.csv` | 장비 34대. **이번 문의 접수 전에 회사가 알 수 있는 장비만**(이전 문의가 제공한 시리얼, 그 뒤 발송한 교체품, S09의 20대). `data_origin=demo`, `derivation_kind=ASSUMED` | `scripts/cs_seed_units.py` |
| `tickets.csv` | 시나리오 문의 22건의 ID와 역할(previous·current), 이전 문의가 제공한 시리얼. 본문은 저장소 밖 개발 분할에서 읽는다 | `scripts/cs_seed_units.py` |
| `same_unit_gold.csv` | 같은 장비 여부 정답(기대 `repeat_type`·장비). QA 채점과 평가에만 쓴다 | 사람·평가기. 적재 도구는 읽지 않는다 |

## 시트를 장비 표로 옮길 때 정한 것

- 시트의 "가상 설정"은 문장이라 칸으로 옮겼다. 인계 **예정**일(S02)은 `commissioning_status=PLANNED`, 설치일이 날짜로 적힌
  것(S04)은 `CONFIRMED`, "2024-05"처럼 월만 있는 것(S13)은 1일로 두고 `basis`에 적었다. 날짜가 없는 장비는 `UNKNOWN`이다.
- 교체품은 원장비의 RMA·보증 처리 뒤에 안 것으로 두어 `known_at`을 이전 문의의 해결 시각으로 했다(이전 문의의 접수 시각보다
  뒤). 원장비는 `unit_status=REPLACED`, `replaced_by_serial_no`로 교체품을 가리킨다.
- **회신 때 공개되는 장비는 넣지 않는다**(S13의 VSN-MTR-1302). 미리 넣으면 화면에 정답 장비가 후보로 먼저 보인다. S09의 20대는 설치
  문의 때 목록을 받았다고 가정해 모두 넣는다(어느 1대인지는 미상이 정답).
- 이전 문의의 시리얼 연결은 `--confirm-by <cs_agent 사용자>`를 주면 `CONFIRMED`(SERIAL_PROVIDED), 없으면 `CANDIDATE`로 남긴다.
  이번 문의에는 연결을 만들지 않는다(담당자가 화면에서 확정한다).

## 범위 밖 (기록)

AI 초안은 아직 장비 이력을 입력으로 쓰지 않는다. 기획서 ②의 "지난번 알려 주신 시리얼 장비가 맞나요?" 확인 질문은 담당자가 화면에서
장비를 확정한 뒤 이력을 보고 직접 쓴다. 시트가 적어 둔 데이터 문제(교체품 보증 규정 없음 S04·S06·S07·S10, S15 라벨 오류)는 그대로다.

## 실행

```bash
# 로컬 compose DB(관리자 연결). 기본은 dry-run.
uv run --frozen python -m scripts.cs_seed_units \
  --admin-url postgresql://postgres:<LOCAL_DB_ADMIN_PASSWORD>@127.0.0.1:5433/team4 \
  --tickets-source ../cs-development-20261027/tickets.dev.csv \
  --confirm-by 00000000-0000-4000-8000-000000000001 --execute
```

공용 DB는 운영자 터미널에서 `--allow-remote`로만 넣는다([배포 승인 점검표](../../docs/release-approval.md) G).
