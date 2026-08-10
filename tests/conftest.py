"""共享测试装置：假 LLM 客户端 + 假搜索结果，全部离线运行，不消耗真实 API。

这几天调试踩过的坑（酒店价格编造、候选数量丢失、车次号编造、并发正确性）
都是先手写一个一次性的假 LLM 脚本验证完就扔。这里把这套模式沉淀成正式的
pytest fixture，以后改代码跑一下 `pytest` 就知道有没有把这些坑又踩回去。
"""
from __future__ import annotations

from typing import Callable

import pytest
from pydantic import BaseModel

from weekendgo.cache.store import clear_all


class FakeLLM:
    """用一个 respond(messages, schema) -> BaseModel 的回调控制假模型的行为，
    比给每个测试写一个专门的假类更灵活、复用度更高。
    """

    model = "fake-model"

    def __init__(self, respond: Callable[[list[dict], type[BaseModel]], BaseModel]):
        self.respond = respond
        self.calls: list[tuple[list[dict], type[BaseModel]]] = []

    def chat_structured(self, messages, schema, **kwargs):
        self.calls.append((messages, schema))
        return self.respond(messages, schema)


def make_fake_search(default_snippet: str = "没有具体数字信息", overrides: dict[str, str] | None = None):
    """返回一个可以直接 patch 到 search_web 位置的假搜索函数。

    overrides: {查询词里的子串: 对应返回的 snippet 文本}，命中哪个子串就返回哪条，
    都不命中就返回 default_snippet。
    """
    overrides = overrides or {}

    def fake_search(query: str, max_results: int = 6):
        for key, snippet in overrides.items():
            if key in query:
                return [
                    {"title": f"关于{key}的搜索结果", "url": f"https://example.com/{key}", "snippet": snippet}
                ]
        return [{"title": "无相关结果", "url": "https://example.com/x", "snippet": default_snippet}]

    return fake_search


@pytest.fixture(autouse=True)
def _isolated_cache():
    """每个测试跑之前/之后都清空本地文件缓存，避免测试之间互相污染
    （比如上一个测试写的缓存被下一个测试意外命中，导致断言对着假数据校验）。
    """
    clear_all()
    yield
    clear_all()
