"""不需要 API Key 的网页搜索工具，供各 pipeline 代码直接调用来核实事实性信息
（车次、酒店、餐厅地址等），避免模型凭训练知识瞎编。

搜索词优先用完整的自然语言问句（例如"厦门到杭州高铁要几个小时"），实测比堆砌
关键词（例如"厦门 杭州 高铁 时长"）搜到的结果相关性明显更高。

这个 app 会在短时间内并发打出去很多次搜索（候选核实、酒店两个搜索词、去程/返程
车次、餐厅），恰好是免费 DDGS 接口最容易触发限流的用法——限流是"这次刚好被拒"，
不是"这条信息真的查不到"，所以失败了先退避一下重试几次，而不是第一次失败就
直接认定查不到、让上层把这次纯粹的限流误判成"未查到确切信息"。
"""
from __future__ import annotations

import time

from ddgs import DDGS


def search_web(query: str, max_results: int = 6, max_retries: int = 2) -> list[dict[str, str]]:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region="cn-zh", max_results=max_results))
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in results
            ]
        except Exception as e:  # noqa: BLE001 - 重试耗尽后要如实告诉模型，而不是让整个流程崩掉
            last_error = e
            if attempt < max_retries:
                time.sleep(0.6 * (attempt + 1))  # 简单线性退避，给限流一点恢复时间
    return [{"error": f"搜索失败: {last_error}"}]
