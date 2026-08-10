"""本地文件缓存：命中/不命中、单个模型 vs 列表、强制刷新、缓存年龄查询。"""
from __future__ import annotations

from pydantic import BaseModel

from weekendgo.cache.store import cache_age_seconds, cached_list, cached_model


class Foo(BaseModel):
    name: str
    value: int


def test_cached_model_hits_on_same_key_misses_on_different_key():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return Foo(name="test", value=42)

    r1 = cached_model("ns_test", {"k": "a"}, Foo, compute)
    r2 = cached_model("ns_test", {"k": "a"}, Foo, compute)  # 同 key，应该命中缓存
    r3 = cached_model("ns_test", {"k": "b"}, Foo, compute)  # 不同 key，应该重新计算

    assert calls["n"] == 2
    assert r1 == r2 == Foo(name="test", value=42)
    assert r3 == Foo(name="test", value=42)


def test_cached_list_round_trips():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return [Foo(name="a", value=1), Foo(name="b", value=2)]

    l1 = cached_list("ns_list", {"k": "x"}, Foo, compute)
    l2 = cached_list("ns_list", {"k": "x"}, Foo, compute)

    assert calls["n"] == 1
    assert l1 == l2 == [Foo(name="a", value=1), Foo(name="b", value=2)]


def test_force_refresh_bypasses_cache_hit():
    """强制刷新按钮的核心保证：即使有命中缓存的结果，force_refresh=True 也要重新算。"""
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return Foo(name="fresh", value=calls["n"])

    r1 = cached_model("ns_refresh", {"k": "a"}, Foo, compute)
    r2 = cached_model("ns_refresh", {"k": "a"}, Foo, compute, force_refresh=True)

    assert calls["n"] == 2
    assert r1.value == 1
    assert r2.value == 2  # 没有偷懒直接返回缓存里的旧值


def test_cache_age_seconds_none_when_no_cache_and_present_after_write():
    payload = {"k": "age-test"}
    assert cache_age_seconds("ns_age", payload) is None

    cached_model("ns_age", payload, Foo, lambda: Foo(name="x", value=1))

    age = cache_age_seconds("ns_age", payload)
    assert age is not None
    assert 0 <= age < 5  # 刚写入，年龄应该接近0秒


def test_cache_age_seconds_respects_ttl():
    payload = {"k": "ttl-test"}
    cached_model("ns_ttl", payload, Foo, lambda: Foo(name="x", value=1))

    assert cache_age_seconds("ns_ttl", payload, ttl_seconds=3600) is not None
    assert cache_age_seconds("ns_ttl", payload, ttl_seconds=0) is None  # 已经"过期"了
