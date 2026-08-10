"""完整行程生成：酒店价格校验/兜底、车次号溯源校验、并发正确性。"""
from __future__ import annotations

import time
from unittest.mock import patch

from weekendgo.config import UserPreferences
from weekendgo.domain.models import Hotel, Itinerary, TrainOption
from weekendgo.pipeline import itinerary_builder as ib

from conftest import FakeLLM


def _prefs(**overrides) -> UserPreferences:
    base = dict(
        departure_city="厦门",
        depart_window="周五",
        return_deadline="周日",
        max_train_hours=4.0,
        budget_min=600,
        budget_max=800,
    )
    base.update(overrides)
    return UserPreferences(**base)


def test_hotel_price_validation_catches_fabricated_price():
    """复现真实报过的 bug：预算 600-800，模型编了 1900-2000。"""
    hotel = Hotel(name="x", address="x", price_range="1900-2000元/晚")
    result = ib._validate_hotel_price(hotel, budget_min=600, budget_max=800)
    assert result.price_verified is False


def test_hotel_price_validation_accepts_in_range_price():
    hotel = Hotel(name="x", address="x", price_range="650-750元/晚")
    result = ib._validate_hotel_price(hotel, budget_min=600, budget_max=800)
    assert result.price_verified is True


def test_hotel_price_validation_tolerates_small_overage():
    hotel = Hotel(name="x", address="x", price_range="1100元/晚")  # 800*1.5=1200，容差内
    result = ib._validate_hotel_price(hotel, budget_min=600, budget_max=800)
    assert result.price_verified is True


def test_hotel_price_validation_handles_no_number():
    hotel = Hotel(name="x", address="x", price_range="未查到具体价格")
    result = ib._validate_hotel_price(hotel, budget_min=600, budget_max=800)
    assert result.price_verified is False  # 老实承认没查到，同样算"没能确认"


def test_fallback_hotel_never_shows_a_fabricated_number():
    """复现真实报过的 bug：兜底推荐提示词的示例文案被模型抄成"预算数字=查到的价格"。
    不管模型这次输出了什么，价格字段都必须被代码强制覆盖。
    """

    def respond(messages, schema):
        return Hotel(name="某酒店", address="某地址", price_range="预计1900元上下，仅供参考")

    llm = FakeLLM(respond)
    hotel = ib._recommend_fallback_hotel(llm, "桂林", budget_min=1900, budget_max=2000)

    assert hotel.price_range == "未查到价格"
    assert hotel.price_verified is False
    assert "1900" not in hotel.price_range


def test_train_grounding_clears_fabricated_train_no():
    option = TrainOption(train_no="G9999", duration="2h")
    result = ib._validate_train_grounding(option, "厦门到杭州大概要4-5小时，没有提到具体车次")
    assert result.train_no == ""
    assert "未能在搜索结果里核实到" in result.note


def test_train_grounding_keeps_grounded_train_no():
    option = TrainOption(train_no="G921", duration="4h")
    result = ib._validate_train_grounding(option, "厦门站乘G921次高铁出发")
    assert result.train_no == "G921"


def test_filters_out_search_results_from_a_different_city():
    """复现真实报过的 bug：给"长沙"生成行程，搜索结果里混进了一条其实是天津
    某家店的内容（DDGS 召回不准），结果模型直接把它当成"长沙本地推荐"写了进去。
    修复后，完全没提到目的地城市名的搜索结果，在喂给模型之前就该被过滤掉。
    """
    results = [
        {"title": "长沙老字号餐厅推荐", "url": "https://example.com/a", "snippet": "长沙本地人常去的...".strip()},
        {"title": "天津老字号饭馆盘点", "url": "https://example.com/b", "snippet": "天津卫的老饕都知道..."},
    ]
    relevant, dropped = ib._filter_relevant_to_destination(results, "长沙")
    assert len(relevant) == 1
    assert relevant[0]["title"] == "长沙老字号餐厅推荐"
    assert dropped == 1


def test_gather_facts_retries_with_a_different_query_before_giving_up():
    """第一次搜索词过滤完一条不剩时，应该换个说法再搜一次，而不是直接放弃。"""
    calls: list[str] = []

    def fake_search(query, max_results=6):
        calls.append(query)
        if "大众点评" in query:
            return [{"title": "长沙美食清单", "url": "https://example.com/c", "snippet": "长沙必吃的..."}]
        return [{"title": "天津老字号饭馆盘点", "url": "https://example.com/b", "snippet": "天津卫的老饕都知道..."}]

    with patch("weekendgo.pipeline.itinerary_builder.search_web", fake_search):
        context = ib._gather_facts("长沙")

    assert len(calls) == 2  # 第一次查完全不相关，触发了第二次补搜
    assert "长沙美食清单" in context


def test_gather_facts_tells_model_to_admit_when_both_queries_are_irrelevant():
    with patch(
        "weekendgo.pipeline.itinerary_builder.search_web",
        lambda q, max_results=6: [{"title": "天津老字号饭馆盘点", "url": "https://example.com/b", "snippet": "天津卫的老饕都知道..."}],
    ):
        context = ib._gather_facts("长沙")

    assert "没有一条明确提到" in context
    assert "不要挪用其他城市的信息" in context


def test_hotel_search_filters_out_results_from_a_different_city():
    """复现餐厅那个 bug 的酒店版本：酒店搜索结果里混进了别的城市的酒店，
    过滤逻辑跟餐厅共用同一个 _filter_relevant_to_destination，这里验证它真的接进了
    酒店搜索路径——不相关的搜索结果不该被喂给模型当成目的地的酒店候选。
    """
    seen_prompts = []

    def respond(messages, schema):
        prompt = messages[-1]["content"]
        seen_prompts.append(prompt)
        return Hotel(name="真实酒店", address="长沙某地址", price_range="650元/晚")

    def fake_search(query, max_results=6):
        return [
            {"title": "长沙性价比酒店推荐", "url": "https://example.com/a", "snippet": "长沙这几家酒店口碑不错"},
            {"title": "天津酒店大盘点", "url": "https://example.com/b", "snippet": "天津这几家性价比很高"},
        ]

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.itinerary_builder.search_web", fake_search):
        ib._verify_hotel_primary(llm, "长沙", 600, 800)

    prompt = seen_prompts[0]
    assert "长沙性价比酒店推荐" in prompt
    assert "天津酒店大盘点" not in prompt


def _fake_search(query, max_results=6):
    return [{"title": "t", "url": "https://example.com/x", "snippet": "约3小时"}]


def test_full_build_runs_four_paths_concurrently():
    def respond(messages, schema):
        time.sleep(0.15)
        prompt = messages[-1]["content"]
        if schema is Hotel:
            if "免费网页搜索经常查不到" in prompt:
                return Hotel(name="兜底(不该被选中)", address="x", price_range="")
            return Hotel(name="真实酒店", address="真实地址", price_range="650元/晚")
        if schema is TrainOption:
            return TrainOption(dep_station="A", arr_station="B", duration="3h")
        return Itinerary(
            destination="桂林",
            train_out=TrainOption(),
            train_back=TrainOption(),
            hotel=Hotel(name="占位", address="占位", price_range="占位"),
        )

    llm = FakeLLM(respond)
    with patch("weekendgo.pipeline.itinerary_builder.search_web", _fake_search):
        t0 = time.time()
        it = ib._build_itinerary_uncached(llm, _prefs(), "桂林")
        elapsed = time.time() - t0

    assert elapsed < 0.5  # 四路并发：接近单次 0.15s，串行相加会接近 0.6s
    assert it.hotel.name == "真实酒店"


def test_progress_callback_fires_for_each_step():
    def respond(messages, schema):
        prompt = messages[-1]["content"]
        if schema is Hotel:
            if "免费网页搜索经常查不到" in prompt:
                return Hotel(name="兜底", address="x", price_range="")
            return Hotel(name="酒店", address="地址", price_range="650元/晚")
        if schema is TrainOption:
            return TrainOption(dep_station="A", arr_station="B", duration="2h")
        return Itinerary(
            destination="桂林",
            train_out=TrainOption(),
            train_back=TrainOption(),
            hotel=Hotel(name="占位", address="占位", price_range="占位"),
        )

    llm = FakeLLM(respond)
    seen: list[str] = []
    with patch("weekendgo.pipeline.itinerary_builder.search_web", _fake_search):
        ib._build_itinerary_uncached(llm, _prefs(), "桂林", on_progress=seen.append)

    assert any("酒店" in m for m in seen)
    assert any("去程车次" in m for m in seen)
    assert any("返程车次" in m for m in seen)
    assert any("行程和餐厅" in m for m in seen)
