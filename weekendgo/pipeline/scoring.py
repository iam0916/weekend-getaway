"""按用户权重给候选目的地打分排序。

打分逻辑是可解释的线性加权，不是黑箱：每个维度分数乘以对应权重再求和。
"能接受走路的程度"这一项比较特殊——权重越低代表用户越怕走路，
就应该奖励"游玩强度低"的目的地；权重越高则不刻意惩罚高强度目的地。
"""
from __future__ import annotations

from ..config import UserPreferences
from ..domain.models import Candidate


def score_candidate(c: Candidate, prefs: UserPreferences) -> float:
    weights = [prefs.w_scenery, prefs.w_food, prefs.w_uniqueness]
    total_w = sum(weights) or 1.0

    base = (
        prefs.w_scenery * c.est_scenery_score
        + prefs.w_food * c.est_food_score
        + prefs.w_uniqueness * c.est_uniqueness_score
    ) / total_w

    # 怕走路的用户（w_walk_tolerance 小）会被"游玩强度低"的目的地加分；
    # 很能走的用户（w_walk_tolerance 大）这一项影响很小，不惩罚也不额外加分。
    low_walk_bonus = (1 - c.est_walk_intensity) * (1 - prefs.w_walk_tolerance)

    score = 0.75 * base + 0.25 * low_walk_bonus
    return round(min(max(score, 0.0), 1.0), 3)


def rank_candidates(candidates: list[Candidate], prefs: UserPreferences) -> list[Candidate]:
    for c in candidates:
        c.total_score = score_candidate(c, prefs)
    return sorted(candidates, key=lambda c: c.total_score, reverse=True)
