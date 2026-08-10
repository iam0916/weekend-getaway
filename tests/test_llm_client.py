"""chat_structured 的 JSON 自我修正重试逻辑。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from weekendgo.llm.client import LLMClient


class Foo(BaseModel):
    name: str
    age: int


class _RepliesLLM(LLMClient):
    """跳过真实 OpenAI 初始化，按顺序吐出固定的回复文本。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self.replies.pop(0)


def test_succeeds_first_try():
    llm = _RepliesLLM(['{"name": "阿明", "age": 30}'])
    result = llm.chat_structured([{"role": "user", "content": "x"}], Foo)
    assert result == Foo(name="阿明", age=30)
    assert llm.calls == 1


def test_recovers_from_non_json_output():
    llm = _RepliesLLM(["抱歉我不知道", '{"name": "小红", "age": 25}'])
    result = llm.chat_structured([{"role": "user", "content": "x"}], Foo, max_repair_attempts=2)
    assert result.name == "小红"
    assert llm.calls == 2


def test_recovers_from_missing_field():
    llm = _RepliesLLM(['{"name": "老王"}', '{"name": "老王", "age": 40}'])
    result = llm.chat_structured([{"role": "user", "content": "x"}], Foo, max_repair_attempts=2)
    assert result.age == 40


def test_raises_after_exhausting_retries():
    llm = _RepliesLLM(["not json", "still not json", "nope"])
    with pytest.raises(ValueError):
        llm.chat_structured([{"role": "user", "content": "x"}], Foo, max_repair_attempts=2)
