"""应用配置：LLM 连接参数 + 用户偏好数据结构。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-air"


@dataclass
class LLMSettings:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL


def load_llm_settings(
    override_key: str | None = None,
    override_url: str | None = None,
    override_model: str | None = None,
) -> LLMSettings:
    """优先使用传入的覆盖值（比如 Streamlit 手填），否则回退到 .env / 环境变量。"""
    return LLMSettings(
        api_key=override_key or os.getenv("LLM_API_KEY", ""),
        base_url=override_url or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
        model=override_model or os.getenv("LLM_MODEL", DEFAULT_MODEL),
    )


@dataclass
class UserPreferences:
    departure_city: str
    depart_window: str
    return_deadline: str
    max_train_hours: float
    budget_min: int
    budget_max: int
    num_candidates: int = 4
    # 权重均为 0~1，越大代表越看重这个维度
    w_scenery: float = 0.5
    w_food: float = 0.5
    w_walk_tolerance: float = 0.5  # 越大代表越能接受走路/爬山
    w_uniqueness: float = 0.5
