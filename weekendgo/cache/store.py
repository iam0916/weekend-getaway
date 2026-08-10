"""基于本地文件的简单缓存，带 TTL。

目的：同样的偏好组合短时间内重复规划，不用每次都重新调用 LLM + 联网搜索，
省时间也省 API 调用次数。不需要数据库，一个 JSON 文件对应一个 key 就够用了；
项目本地跑、单用户使用，没必要上重的方案。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
DEFAULT_TTL_SECONDS = 24 * 3600

ModelT = TypeVar("ModelT", bound=BaseModel)


def _cache_path(namespace: str, payload: dict[str, Any]) -> Path:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{namespace}__{digest}.json"


def _read(path: Path, ttl_seconds: int) -> Any | None:
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - stored.get("ts", 0) > ttl_seconds:
        return None
    return stored.get("value")


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps({"ts": time.time(), "value": value}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cached_model(
    namespace: str,
    payload: dict[str, Any],
    schema: type[ModelT],
    compute: Callable[[], ModelT],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    force_refresh: bool = False,
) -> ModelT:
    """缓存单个 Pydantic 模型的结果（比如一份 Itinerary）。

    force_refresh=True 时跳过读缓存、强制重新计算——给界面上"强制重新查询"按钮用，
    避免用户在信息可能已经过时（酒店关门/餐厅换了地址）时只能干等 TTL 过期。
    重新算出来的结果照样会写回缓存，刷新它的时间戳。
    """
    path = _cache_path(namespace, payload)
    if not force_refresh:
        hit = _read(path, ttl_seconds)
        if hit is not None:
            return schema.model_validate(hit)
    value = compute()
    _write(path, value.model_dump())
    return value


def cached_list(
    namespace: str,
    payload: dict[str, Any],
    schema: type[ModelT],
    compute: Callable[[], list[ModelT]],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    force_refresh: bool = False,
) -> list[ModelT]:
    """缓存一组 Pydantic 模型的结果（比如候选目的地列表）。force_refresh 见 cached_model。"""
    path = _cache_path(namespace, payload)
    if not force_refresh:
        hit = _read(path, ttl_seconds)
        if hit is not None:
            return [schema.model_validate(item) for item in hit]
    value = compute()
    _write(path, [item.model_dump() for item in value])
    return value


def cache_age_seconds(
    namespace: str, payload: dict[str, Any], ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> float | None:
    """这个 key 对应的缓存已经存在多久了（秒）；没命中（不存在/已过期/文件损坏）返回 None。

    只读，不会触发计算——给界面展示"这是缓存结果，生成于多久之前"用，
    在真正调用 generate_candidates/build_itinerary 之前就能知道会不会命中缓存。
    """
    path = _cache_path(namespace, payload)
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    age = time.time() - stored.get("ts", 0)
    if age > ttl_seconds:
        return None
    return age


def clear_all() -> int:
    """清空缓存目录，返回删除的文件数。主要给调试/手动排障用。"""
    if not CACHE_DIR.exists():
        return 0
    count = 0
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()
        count += 1
    return count
