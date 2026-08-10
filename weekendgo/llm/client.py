"""统一的 LLM 客户端：封装 OpenAI 兼容协议的调用。

用 OpenAI SDK 而不是某个厂商专用 SDK，是因为智谱 GLM-4、Moonshot、DeepSeek 等
国内大模型的 API 基本都兼容 OpenAI 协议，只需要换 base_url 和 api_key，
方便以后切换供应商而不用改业务代码。

早期版本这里还有一套完整的 function calling 工具调用循环，让模型自己决定
"要不要搜索、搜几次"。实测发现这个设计有两个问题：小模型经常"假装调用了工具"
但没真的根据结果调整答案；强模型则会为了不停确认而反复调用，轮数不封顶的话
几分钟都收敛不了。后来把所有 pipeline 都改成了"代码决定搜什么、模型只负责
基于搜索结果生成结构化输出"，工具调用循环就没有调用方了，索性直接删掉，
不留着一套没人用、以后容易被误当成"还在用"的机制。
"""
from __future__ import annotations

import json
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        if not api_key:
            raise ValueError("缺少 API Key：请在侧边栏填写，或在 .env 中配置 LLM_API_KEY")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.4,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """发起一次对话，返回模型的文本回复。

        response_format 支持传 {"type": "json_object"} 强制模型输出合法 JSON——
        实测发现小模型光靠提示词要求"只输出JSON"并不可靠，容易输出成自然语言列表，
        必须用这个参数硬约束。
        """
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            response_format=response_format or None,
        )
        return resp.choices[0].message.content or ""

    def chat_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[ModelT],
        max_repair_attempts: int = 2,
        **kwargs: Any,
    ) -> ModelT:
        """调用 chat() 拿到 JSON 并校验成 schema 指定的 Pydantic 模型。

        如果输出不是合法 JSON、或者字段不满足 schema，把具体的错误信息喂回模型，
        让它自己修正，最多重试 max_repair_attempts 次。实测这类"结构化输出+自我修正"
        比单纯指望模型一次就写对要稳定得多——JSON 语法错误、漏字段这些问题
        很大一部分模型看到明确的错误提示后自己就能修好。
        """
        msgs = list(messages)
        last_error: Exception | None = None

        for attempt in range(max_repair_attempts + 1):
            content = self.chat(msgs, response_format={"type": "json_object"}, **kwargs)
            try:
                data = json.loads(content)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
                if attempt >= max_repair_attempts:
                    break
                msgs = msgs + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            f"你上一次的输出有问题，无法解析或校验：{e}\n"
                            "请只输出修正后的合法 JSON 对象，严格符合要求的字段结构，"
                            "不要有任何其他文字、不要用 markdown 代码块包裹。"
                        ),
                    },
                ]

        raise ValueError(f"结构化输出连续 {max_repair_attempts + 1} 次失败: {last_error}")
