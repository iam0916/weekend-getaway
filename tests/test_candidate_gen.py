"""候选目的地生成：并发核实、时长容差过滤、数量补位。"""
from __future__ import annotations

import re
import time
from unittest.mock import patch

from weekendgo.config import UserPreferences
from weekendgo.domain.models import Candidate, CityItem, CityList
from weekendgo.pipeline import candidate_gen as cg

from conftest import FakeLLM


def _prefs(**overrides) -> UserPreferences:
    base = dict(
        departure_city="厦门",
        depart_window="周五晚",
        return_deadline="周日",
        max_train_hours=3.0,
        budget_min=400,
        budget_max=800,
        num_candidates=4,
    )
    base.update(overrides)
    return UserPreferences(**base)


def _city_from_prompt(prompt: str) -> str:
    m = re.search(r"到 (\S+?) 高铁", prompt)
    return m.group(1) if m else "?"


def test_verification_runs_in_parallel():
    def respond(messages, schema):
        time.sleep(0.1)
        if schema is CityList:
            return CityList(cities=[CityItem(city=f"城市{i}", province="省") for i in range(4)])
        return Candidate(city=_city_from_prompt(messages[-1]["content"]), province="省", train_hours_estimate=2.0)

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        t0 = time.time()
        candidates = cg.generate_candidates(llm, _prefs())
        elapsed = time.time() - t0

    assert len(candidates) == 4
    assert elapsed < 0.4  # 4 个候选并发核实：接近单次 0.1s，串行会接近 0.4s+


def test_filters_out_candidates_exceeding_time_tolerance():
    def respond(messages, schema):
        if schema is CityList:
            return CityList(cities=[CityItem(city=f"城市{i}", province="省") for i in range(4)])
        city = _city_from_prompt(messages[-1]["content"])
        hours = 8.0 if city == "城市2" else 2.0  # 城市2 明显超出时长上限
        return Candidate(city=city, province="省", train_hours_estimate=hours)

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        candidates = cg.generate_candidates(llm, _prefs(max_train_hours=3.0))

    assert "城市2" not in [c.city for c in candidates]


def test_backfills_when_brainstorm_underdelivers():
    """复现真实报过的 bug：选了4个候选，脑暴只给3个，应该自动补一轮凑够。"""

    def respond(messages, schema):
        if schema is CityList:
            content = messages[-1]["content"]
            if "不要再重复给出" not in content:
                return CityList(cities=[CityItem(city=f"城市{i}", province="省") for i in range(3)])
            return CityList(cities=[CityItem(city="补位城市", province="省")])
        city = _city_from_prompt(messages[-1]["content"])
        return Candidate(city=city, province="省", train_hours_estimate=2.0)

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        candidates = cg.generate_candidates(llm, _prefs(num_candidates=4))

    assert len(candidates) == 4


def test_backfills_when_one_candidate_exceeds_tolerance():
    def respond(messages, schema):
        if schema is CityList:
            content = messages[-1]["content"]
            if "不要再重复给出" not in content:
                return CityList(cities=[CityItem(city=f"城市{i}", province="省") for i in range(5)])
            return CityList(cities=[CityItem(city="补位城市", province="省")])
        city = _city_from_prompt(messages[-1]["content"])
        hours = 10.0 if city == "城市3" else 2.0
        return Candidate(city=city, province="省", train_hours_estimate=hours)

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        candidates = cg.generate_candidates(llm, _prefs(num_candidates=4, max_train_hours=3.0))

    assert len(candidates) == 4
    assert "城市3" not in [c.city for c in candidates]


def test_dedupes_when_brainstorm_returns_duplicate_city_in_same_batch():
    """复现真实报过的 bug：一次脑暴自己返回的列表里，"广州"出现了两次。
    只查 tried_cities（跨轮次去重）拦不住这种同批次内部的重复，需要额外的
    批内去重，否则同一个城市会被核实两次，生成两张内容几乎一样的候选卡片。
    """

    def respond(messages, schema):
        if schema is CityList:
            return CityList(
                cities=[
                    CityItem(city="广州", province="广东省"),
                    CityItem(city="长沙", province="湖南省"),
                    CityItem(city="广州", province="广东省"),  # 模型自己给重复了
                    CityItem(city="武汉", province="湖北省"),
                    CityItem(city="南昌", province="江西省"),
                ]
            )
        city = _city_from_prompt(messages[-1]["content"])
        return Candidate(city=city, province="省", train_hours_estimate=2.0)

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        candidates = cg.generate_candidates(llm, _prefs(num_candidates=4))

    cities = [c.city for c in candidates]
    assert cities.count("广州") == 1
    assert len(candidates) == 4


def test_dedupes_across_rounds_even_when_brainstorm_strings_differ():
    """复现真实报过的 bug（第二次出现）：两轮补位分别脑暴出"长沙"和"长沙市"这种
    字符串不完全一样、但 verify_one 核实后落回同一个城市名的候选，只查
    tried_cities/seen_in_batch（原始字符串层面的去重）拦不住这种情况——
    需要在"最终接受"这一步再按 verify 之后的真实城市名兜底去重一次。
    """
    call_count = {"n": 0}

    def respond(messages, schema):
        if schema is CityList:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 第一轮：只给3个，触发补位
                return CityList(
                    cities=[
                        CityItem(city="长沙", province="湖南省"),
                        CityItem(city="武汉", province="湖北省"),
                        CityItem(city="南昌", province="江西省"),
                    ]
                )
            # 补位轮：脑暴换了个写法"长沙市"，跟已经接受的"长沙"字符串不同，
            # tried_cities/exclude 拦不住，但核实之后模型会把城市名规范化成"长沙"。
            return CityList(cities=[CityItem(city="长沙市", province="湖南省")])

        city = _city_from_prompt(messages[-1]["content"])
        normalized = "长沙" if city in ("长沙", "长沙市") else city
        return Candidate(city=normalized, province="省", train_hours_estimate=2.0)

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        candidates = cg.generate_candidates(llm, _prefs(num_candidates=4))

    cities = [c.city for c in candidates]
    assert cities.count("长沙") == 1


def test_force_refresh_bypasses_candidate_cache():
    calls = {"n": 0}

    def respond(messages, schema):
        if schema is CityList:
            calls["n"] += 1
            return CityList(cities=[CityItem(city=f"城市{calls['n']}_{i}", province="省") for i in range(4)])
        city = _city_from_prompt(messages[-1]["content"])
        return Candidate(city=city, province="省", train_hours_estimate=2.0)

    llm = FakeLLM(respond)
    prefs = _prefs(num_candidates=4)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        first = cg.generate_candidates(llm, prefs)
        assert cg.candidates_cache_age_seconds(llm, prefs) is not None

        second = cg.generate_candidates(llm, prefs, force_refresh=True)

    assert [c.city for c in first] != [c.city for c in second]  # 真的重新算了，不是拿缓存充数


def test_no_infinite_loop_when_genuinely_scarce():
    """约束太严、脑暴翻来覆去只给同一个候选：不该死循环，也不该返回空列表。"""

    def respond(messages, schema):
        if schema is CityList:
            return CityList(cities=[CityItem(city="唯一城市", province="省")])
        return Candidate(city="唯一城市", province="省", train_hours_estimate=20.0)

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.candidate_gen.search_web", lambda q, max_results=6: []):
        candidates = cg.generate_candidates(llm, _prefs(num_candidates=4, max_train_hours=3.0))

    assert len(candidates) == 1
