"""요구 추출 채점(KAN-185, 기획서 3.2: 평가 절 고정, 필수 요구 전수 표시, 재현율·정밀도 각 90%).

정답(`GoldRequirement`)은 사람이 원문을 보고 표시한다. 원문이 들어가므로 저장소 밖에 둔다.
짝짓기(같은 조항 안):
1. 값이 있는 정답: 정규화한 값이 같거나 한쪽이 다른 쪽을 품고, 단위도 같거나 한쪽이 다른 쪽을 품는다
   (`kA`와 `kA ASYM RMS`는 같은 단위로 본다).
2. 값이 없는 정답: 아직 짝이 없는 예측 중 정답 속성 낱말과 예측 속성·인용 낱말이 가장 많이 겹치는 것.
   겹침은 정답 낱말의 1/3 이상이어야 한다. 비율과 함께 건수(예: 19/21)를 낸다.

- 엄격(`strict`): 정답 하나에 예측 하나만 짝짓는다(먼저 온 것).
- 관대(`lenient`): 한 예측이 여러 정답을 덮을 수 있다(모델이 목록형 특징을 한 요구로 묶은 경우). 재현율은
  어느 예측과든 짝지어진 정답 수, 정밀도는 정답 하나 이상과 짝지어진 예측 수다. 두 값을 함께 보고한다.
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from backend.engine.rfp.requirements import ExtractedRequirement


class GoldRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_no: str
    attribute: str
    value: str | None = None
    unit: str | None = None
    mandatory: bool = True
    note: str | None = None


@dataclass(frozen=True)
class Score:
    mode: str
    gold: int
    predicted: int
    matched_gold: int
    matched_predicted: int
    missed: list[GoldRequirement]
    extra: list[ExtractedRequirement]

    @property
    def recall(self) -> str:
        return f"{self.matched_gold}/{self.gold}"

    @property
    def precision(self) -> str:
        return f"{self.matched_predicted}/{self.predicted}"


WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "of", "and", "a", "an", "for", "to", "with", "in", "on", "shall", "be"}


def _norm_value(value: str | None) -> str:
    text = (value or "").casefold().replace("percent", "%").replace(" ", "")
    return text.replace("degreesc", "c").replace("degreec", "c").rstrip(".")


def _words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.casefold()) if w not in STOP}


def _value_match(gold: GoldRequirement, pred: ExtractedRequirement) -> bool:
    if gold.clause_no != pred.clause_no or gold.value is None:
        return False
    g, p = _norm_value(gold.value), _norm_value(pred.value)
    if not p or not (g == p or g in p or p in g):
        return False
    gu, pu = _norm_value(gold.unit), _norm_value(pred.unit)
    return not gu or not pu or gu == pu or gu in pu or pu in gu


def _overlap(gold: GoldRequirement, pred: ExtractedRequirement) -> int:
    """값이 없는 정답과 예측의 낱말 겹침. 1/3에 못 미치면 0."""
    if gold.clause_no != pred.clause_no or gold.value is not None:
        return 0
    words = _words(gold.attribute)
    hit = len(words & _words(pred.attribute + " " + pred.quote))
    return hit if words and hit * 3 >= len(words) else 0


def _same(gold: GoldRequirement, pred: ExtractedRequirement) -> bool:
    return _value_match(gold, pred) or _overlap(gold, pred) > 0


def score(
    gold: list[GoldRequirement], predicted: list[ExtractedRequirement], *, lenient: bool = False
) -> Score:
    """정답과 예측을 짝지어 재현율·정밀도 건수를 낸다.

    Args:
        gold: 사람이 표시한 정답(평가 절 안 전수).
        predicted: 검사를 통과한 예측.
        lenient: 참이면 한 예측이 여러 정답을 덮을 수 있다.

    Returns:
        Score: 분자·분모와 놓친 정답·남는 예측.
    """
    if lenient:
        hits = [[i for i, p in enumerate(predicted) if _same(g, p)] for g in gold]
        covered = {i for hit in hits for i in hit}
        return Score(
            mode="lenient",
            gold=len(gold),
            predicted=len(predicted),
            matched_gold=sum(bool(hit) for hit in hits),
            matched_predicted=len(covered),
            missed=[g for g, hit in zip(gold, hits, strict=True) if not hit],
            extra=[p for i, p in enumerate(predicted) if i not in covered],
        )
    used: set[int] = set()
    matched: set[int] = set()
    # 값이 있는 정답을 먼저 짝짓고, 남은 예측에서 값 없는 정답을 겹침이 큰 순서로 짝짓는다.
    for gi, g in enumerate(gold):
        hit = next(
            (i for i, p in enumerate(predicted) if i not in used and _value_match(g, p)), None
        )
        if hit is not None:
            used.add(hit)
            matched.add(gi)
    for gi, g in enumerate(gold):
        if gi in matched or g.value is not None:
            continue
        ranked = sorted(
            ((_overlap(g, p), i) for i, p in enumerate(predicted) if i not in used),
            key=lambda x: (-x[0], x[1]),
        )
        if ranked and ranked[0][0] > 0:
            used.add(ranked[0][1])
            matched.add(gi)
    missed = [g for gi, g in enumerate(gold) if gi not in matched]
    extra = [p for i, p in enumerate(predicted) if i not in used]
    return Score(
        mode="strict",
        gold=len(gold),
        predicted=len(predicted),
        matched_gold=len(gold) - len(missed),
        matched_predicted=len(used),
        missed=missed,
        extra=extra,
    )
