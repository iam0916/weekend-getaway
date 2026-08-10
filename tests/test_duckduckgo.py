"""search_web 的失败退避重试：免费 DDGS 接口在并发多的时候容易触发限流，
限流是"这次刚好被拒"，不该被上层当成"真的查不到"处理。"""
from __future__ import annotations

from unittest.mock import patch

from weekendgo.search import duckduckgo as ddg


class _FlakyDDGS:
    """前几次调用抛异常（模拟限流），之后成功。"""

    attempts = {"n": 0}
    fail_until = 1  # 第几次尝试（0-indexed）之前都失败

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, region="cn-zh", max_results=6):
        n = _FlakyDDGS.attempts["n"]
        _FlakyDDGS.attempts["n"] += 1
        if n < _FlakyDDGS.fail_until:
            raise RuntimeError("rate limited")
        return [{"title": "t", "href": "https://example.com/x", "body": "s"}]


class _AlwaysFailDDGS:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, region="cn-zh", max_results=6):
        raise RuntimeError("still limited")


def test_search_web_retries_transient_failure_then_succeeds():
    _FlakyDDGS.attempts = {"n": 0}
    _FlakyDDGS.fail_until = 1

    with patch("weekendgo.search.duckduckgo.DDGS", _FlakyDDGS), \
         patch("weekendgo.search.duckduckgo.time.sleep", lambda s: None):
        results = ddg.search_web("query", max_retries=2)

    assert _FlakyDDGS.attempts["n"] == 2  # 第一次失败，第二次成功，没有再多试
    assert results == [{"title": "t", "url": "https://example.com/x", "snippet": "s"}]


def test_search_web_gives_up_after_max_retries_and_reports_error():
    with patch("weekendgo.search.duckduckgo.DDGS", _AlwaysFailDDGS), \
         patch("weekendgo.search.duckduckgo.time.sleep", lambda s: None):
        results = ddg.search_web("query", max_retries=2)

    assert len(results) == 1
    assert "error" in results[0]
    assert results[0]["error"].startswith("搜索失败")
