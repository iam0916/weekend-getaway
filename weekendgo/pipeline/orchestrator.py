"""顶层入口：把候选生成、打分排序、按需生成完整行程串起来。

拆成两个独立方法而不是一个"一次性把所有事情都做完"的 plan()：
UI 上用户先看候选目的地排名，再自己挑一个（或几个）点开看完整行程——
没必要每次都把 num_candidates 个目的地的完整行程全部生成一遍，
那样既慢又浪费 API 调用。

两个方法都接受可选的 on_progress 回调，在每个并发子任务完成时报告一句话——
UI 层可以用它给用户展示"现在在核实哪一部分"，而不是一个不知道在做什么的转圈。
"""
from __future__ import annotations

from typing import Callable

from ..config import UserPreferences
from ..domain.models import Candidate, Itinerary
from ..llm.client import LLMClient
from .candidate_gen import candidates_cache_age_seconds, generate_candidates
from .itinerary_builder import build_itinerary, itinerary_cache_age_seconds
from .scoring import rank_candidates

ProgressCallback = Callable[[str], None]


class TripPlanner:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def candidates_cache_age_seconds(self, prefs: UserPreferences) -> float | None:
        return candidates_cache_age_seconds(self.llm, prefs)

    def itinerary_cache_age_seconds(self, prefs: UserPreferences, destination: str) -> float | None:
        return itinerary_cache_age_seconds(self.llm, prefs, destination)

    def get_candidates(
        self,
        prefs: UserPreferences,
        on_progress: ProgressCallback | None = None,
        force_refresh: bool = False,
    ) -> list[Candidate]:
        candidates = generate_candidates(self.llm, prefs, on_progress=on_progress, force_refresh=force_refresh)
        return rank_candidates(candidates, prefs)

    def get_itinerary(
        self,
        prefs: UserPreferences,
        destination: str,
        on_progress: ProgressCallback | None = None,
        force_refresh: bool = False,
    ) -> Itinerary:
        return build_itinerary(
            self.llm, prefs, destination, on_progress=on_progress, force_refresh=force_refresh
        )
