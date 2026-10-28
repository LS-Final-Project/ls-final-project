"""KAN-95에서 잠근 C/S 개발 정답의 판본·hash·분모와 묶음 ID. 정답 파일은 저장소 밖에 둔다."""

from dataclasses import dataclass

GOLD_BUNDLE_ID = "cs-gold-bundle-20261028-v1"


@dataclass(frozen=True)
class GoldLock:
    """지표 하나의 잠금 정답 파일 판본과 고정 대상 수.

    denominators는 (채점 범위, 학습 대상 수, 검증 대상 수)다. 시험 분할은 없다.
    """

    gold_version: str
    gold_sha256: str
    columns: tuple[str, ...]
    denominators: tuple[tuple[str, int, int], ...]


# docs/evaluation.md "C/S 정답 묶음 판본" 표와 같아야 한다(tests/test_cs_gold_locks.py).
GOLD_LOCKS: dict[str, GoldLock] = {
    "category": GoldLock(
        "provided-verified-v1-20261028",
        "76022e37107aea5564b58a8bc48509a697175f0a4bbeafbce9fc50dba25d2c25",
        ("ticket_id", "category_code"),
        (("ALL", 2800, 603),),
    ),
    "urgency": GoldLock(
        "provided-verified-v1-20261027",
        "9beda3c54d31f2398a10aba293582e999c97e05734863e74231c9392f9bec22e",
        ("ticket_id", "urgency_flag"),
        (("ALL", 2800, 603),),
    ),
    "secondary": GoldLock(
        "provided-verified-v1-20261027",
        "fcf4596dae592b80ca1d5f716a86e4e522f8a35a1856792b8841502dd08e0386",
        ("ticket_id", "secondary_category"),
        (("ALL", 2800, 603),),
    ),
    "return_subtype": GoldLock(
        "routing-derived-v1-team-agreed-20261027",
        "4dfe4ac67708003d4ca12f4ac1976494c4126c2e3cbab2d9514768c89a1b76c1",
        ("ticket_id", "return_subtype"),
        (("ALL", 245, 53),),
    ),
    "provided_info": GoldLock(
        "provided-verified-v1-20261028",
        "78abf59b398c3443cf623e027d33e88eed8bf9e0c75cb919f4504f07f211595d",
        ("ticket_id", "provided_info", "scoring_scope"),
        (("FULL", 2643, 561), ("PRECISION_ONLY", 157, 42)),
    ),
}
