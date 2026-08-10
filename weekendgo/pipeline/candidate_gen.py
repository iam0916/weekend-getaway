"""第一阶段：脑暴 + 逐个核实候选目的地。

拆成两步，而不是把"要不要搜索、搜几次"完全交给模型自己在一个开放的
tool-calling 循环里决定：

1. brainstorm：纯知识性脑暴候选城市名单，不需要工具，一次调用，快。
2. verify：对每个候选城市，代码直接发起一次针对性搜索
   （"出发地 到 候选地 高铁 时长"），再让模型基于搜索结果给出时长估计和四维度打分。
   这一步对每个候选城市互相独立，用线程池并发执行，把总耗时从
   "候选数量 × 单次耗时" 降到接近"单次耗时"。

实测发现：把搜索的主动权完全交给模型，快模型（glm-4-flash）经常"假装搜了"但
其实没根据结果调整答案；强模型（glm-4-plus）则会为了不停确认而反复搜索，
轮数不封顶的话几分钟都收敛不了。拆成"代码决定搜什么、模型只负责基于结果打分"
之后，两个问题都解决了，而且延迟可控。

另外用 chat_structured 做了 JSON 自我修正重试，并且把结果按输入参数缓存在本地，
短时间内重复同样的规划请求不用再次调用模型。

有三个环节会让最终候选数量悄悄少于用户要求的数量、又不报错：脑暴模型没给够、
某个候选城市名是空的被跳过、核实出来车程明显超时长上限被过滤掉。踩过这个坑
之后改成了"能补位的循环"：一轮凑不够就再脑暴一轮补差额（排除已经试过的城市，
不会重复），最多补 2 轮——正常情况下用户选几个就该看到几个；如果约束确实太严
（比如时长上限设得很短），两轮下来还凑不够，那是真实反映"确实没那么多符合
条件的地方"，不是丢数据。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from ..cache.store import cache_age_seconds, cached_list
from ..config import UserPreferences
from ..domain.models import Candidate, CityItem, CityList
from ..llm.client import LLMClient
from ..search.duckduckgo import search_web

ProgressCallback = Callable[[str], None]

MAX_BACKFILL_ROUNDS = 2

BRAINSTORM_SYSTEM_PROMPT = """你是短途旅行规划助手。请根据出发城市和限制条件，
脑暴若干个风格不同的候选目的地城市（不需要给理由，只给城市名单，且尽量避免出发城市所在省份）。
输出 JSON 对象：{"cities": [{"city": "...", "province": "..."}, ...]}
"""

VERIFY_PROMPT_TEMPLATE = """你是短途旅行规划助手。以下是关于"{departure} 到 {city} 高铁时长"的搜索结果：

{search_results}

请基于以上信息给这个候选城市打分（如果没查到明确时长，train_hours_estimate 如实填 0，
并在 reason 里说明"未查到确切时长，需自行核实"，不要编造数字）。

背景信息：
出发时间窗口：{depart_window}
返程要求：{return_deadline}
酒店预算：{budget_min}-{budget_max} 元/晚

输出 JSON 对象，字段：city, province, reason, train_hours_estimate（小时，数字），
est_scenery_score, est_food_score, est_walk_intensity, est_uniqueness_score（均为 0~1 的小数）。
- est_scenery_score：当地景观/人文的独特性和吸引力
- est_food_score：美食吸引力
- est_walk_intensity：游玩强度，0=几乎不用走路，1=需要大量步行或爬山
- est_uniqueness_score：不可替代性，是否有出发城市体验不到的东西
  （限时活动/节日/独特地貌建筑）；如果只是"好吃"但出发城市大概率也有类似的，这项要打低分
"""


def _cache_payload(llm: LLMClient, prefs: UserPreferences) -> dict:
    return {
        "departure": prefs.departure_city,
        "max_hours": prefs.max_train_hours,
        "budget": [prefs.budget_min, prefs.budget_max],
        "n": prefs.num_candidates,
        "model": llm.model,
    }


def candidates_cache_age_seconds(llm: LLMClient, prefs: UserPreferences) -> float | None:
    """只读查询：这组条件对应的候选列表缓存存在多久了，没命中返回 None。
    给界面在用户点「开始规划」之前就提示"这是缓存结果"用。
    """
    return cache_age_seconds("candidates", _cache_payload(llm, prefs))


def generate_candidates(
    llm: LLMClient,
    prefs: UserPreferences,
    on_progress: ProgressCallback | None = None,
    force_refresh: bool = False,
) -> list[Candidate]:
    cache_payload = _cache_payload(llm, prefs)
    # 命中缓存就不会真的跑生成逻辑，on_progress 也不会被调用——缓存命中本来就是秒开，
    # 没有"进度"这回事。force_refresh=True 时跳过读缓存，强制重新生成。
    return cached_list(
        "candidates",
        cache_payload,
        Candidate,
        compute=lambda: _generate_candidates_uncached(llm, prefs, on_progress),
        force_refresh=force_refresh,
    )


def _generate_candidates_uncached(
    llm: LLMClient, prefs: UserPreferences, on_progress: ProgressCallback | None = None
) -> list[Candidate]:
    accepted: list[Candidate] = []
    accepted_cities: set[str] = set()  # 只认"最终真的被接受了"的城市，独立于 tried_cities
    all_verified: list[Candidate] = []  # 万一每一轮都凑不够合格的，最后拿这个兜底展示
    tried_cities: set[str] = set()

    for round_idx in range(MAX_BACKFILL_ROUNDS):
        still_needed = prefs.num_candidates - len(accepted)
        if still_needed <= 0:
            break

        # 多要一个当缓冲——脑暴给出的候选里总有几个会因为城市名重复/空值/
        # 车程超标被筛掉，多要一个能减少触发第二轮补位的概率。
        city_items = _brainstorm(llm, prefs, count=still_needed + 1, exclude=tried_cities)
        if on_progress:
            round_label = "" if round_idx == 0 else f"（补位第{round_idx}轮）"
            on_progress(f"脑暴候选城市完成{round_label}")

        # 去重要防两种情况：跟"之前几轮"已经试过的城市重复（查 tried_cities），
        # 以及"这一次脑暴自己返回的列表里"内部就有重复（模型偶尔会给出
        # 重复的城市名，比如同一批里出现两次"广州"）——只查 tried_cities 拦不住
        # 第二种。
        seen_in_batch: set[str] = set()
        new_items = []
        for item in city_items:
            if not item.city or item.city in tried_cities or item.city in seen_in_batch:
                continue
            new_items.append(item)
            seen_in_batch.add(item.city)
        if not new_items:
            break  # 模型给不出新候选了（比如城市本来就没几个可选），再试也没用
        tried_cities.update(item.city for item in new_items)

        verified = _verify_many(llm, prefs, new_items, on_progress)
        for v in verified:
            all_verified.append(v)
            # 最后一道保险：不管这个重复是从哪条路径漏进来的（脑暴没听话、
            # verify_one 返回的 city 跟请求的不一致等等），只要这个城市名已经
            # 被接受过，就不再重复收——这一步不依赖猜测具体是哪个环节出的漏洞，
            # 直接在"进入最终结果"这个关口把关。
            if v.city in accepted_cities:
                continue
            if _within_tolerance(v, prefs.max_train_hours):
                accepted.append(v)
                accepted_cities.add(v.city)

    if not accepted:
        # 真的一轮都凑不出符合时长要求的候选：好歹把核实过的结果给用户看，
        # 附着真实的车程数字，让用户自己判断，而不是展示一个空列表。
        # 同样按城市名去重一次，不能因为走了这条兜底分支就把去重规则放松。
        accepted = _dedupe_by_city(all_verified)

    return accepted[: prefs.num_candidates]


def _dedupe_by_city(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for c in candidates:
        if c.city in seen:
            continue
        seen.add(c.city)
        result.append(c)
    return result


def _brainstorm(
    llm: LLMClient, prefs: UserPreferences, count: int, exclude: set[str] | None = None
) -> list[CityItem]:
    exclude = exclude or set()
    exclude_line = f"\n不要再重复给出这些已经出现过的城市：{'、'.join(sorted(exclude))}" if exclude else ""
    user_prompt = (
        f"出发城市：{prefs.departure_city}\n"
        f"高铁时长上限：约 {prefs.max_train_hours} 小时（单程，仅供参考，稍后会逐个核实）\n"
        f"请给出 {count} 个候选城市。{exclude_line}"
    )
    result = llm.chat_structured(
        messages=[
            {"role": "system", "content": BRAINSTORM_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        schema=CityList,
    )
    return result.cities[:count]


def _verify_many(
    llm: LLMClient,
    prefs: UserPreferences,
    city_items: list[CityItem],
    on_progress: ProgressCallback | None = None,
) -> list[Candidate]:
    results: list[Candidate] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(city_items)))) as pool:
        futures = {
            pool.submit(_verify_one, llm, prefs, item.city, item.province): item for item in city_items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append(future.result())
            except Exception:  # noqa: BLE001 - 单个候选核实失败不该拖垮整批结果
                results.append(
                    Candidate(
                        city=item.city,
                        province=item.province,
                        reason="核实这个候选城市时出错，评分为兜底默认值，请自行判断是否考虑",
                    )
                )
            if on_progress:
                on_progress(f"核实「{item.city}」完成")
    return results


def _verify_one(llm: LLMClient, prefs: UserPreferences, city: str, province: str) -> Candidate:
    results = search_web(f"{prefs.departure_city}到{city}高铁要几个小时")
    snippet_text = "\n".join(
        f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in results if "error" not in r
    )
    prompt = VERIFY_PROMPT_TEMPLATE.format(
        departure=prefs.departure_city,
        city=city,
        search_results=snippet_text or "（没有搜到相关结果）",
        depart_window=prefs.depart_window,
        return_deadline=prefs.return_deadline,
        budget_min=prefs.budget_min,
        budget_max=prefs.budget_max,
    )
    candidate = llm.chat_structured(messages=[{"role": "user", "content": prompt}], schema=Candidate)
    if not candidate.city:
        candidate.city = city
    if not candidate.province:
        candidate.province = province
    return candidate


def _within_tolerance(candidate: Candidate, max_hours: float) -> bool:
    """留 20% 容差（搜索结果本身就有误差），卡太死容易把候选筛得太狠。"""
    if max_hours <= 0:
        return True
    return candidate.train_hours_estimate <= max_hours * 1.2 or candidate.train_hours_estimate == 0
