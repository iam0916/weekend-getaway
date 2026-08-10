"""第二阶段：针对选中的目的地，生成完整行程（车次/酒店/每日安排/餐厅）。

跟 candidate_gen 一样，不把"搜什么、搜几次"交给模型自己在 tool-calling
循环里决定——实测发现即使是 glm-4-air，只要开着完全自主的搜索循环，
偶尔也会来回反复搜索、迟迟不收敛。这里改成：代码先把几类事实性信息
并发搜好，再把搜索结果整体喂给模型，让它一次性组织成完整行程。只有
"每日行程怎么排"这种不涉及具体数字/地址的创造性部分，才放心交给模型
用自己的知识来编排。

酒店价格和车次信息是这里面最容易出问题的字段——搜索引擎摘要经常压根不带
具体数字/车次号，但模型会"看着差不多就编一个"。两个字段都做了同一套防护
（先各自拆成独立调用，再在代码层面校验模型有没有老实交代"查不到"）：

**酒店**（_verify_hotel）：
1. 单独一次调用去核实，提示词里把"没有具体数字就必须承认没查到"当成最高
   优先级规则单独强调；就算这样还是编了数字，_validate_hotel_price 会把
   结果跟预算区间比对，明显对不上就标记"未验证"。
2. 免费搜索对酒店价格的召回本来就弱，"核实"这条路径几乎每次都失败、
   走到兜底推荐——所以"核实"和"经验兜底推荐"（_recommend_fallback_hotel）
   从一开始就并发跑，不等失败了再补调用，用多一次 API 调用换掉一整轮等待。
   兜底推荐的价格字段完全不信任模型输出，代码强制覆盖成固定文案。

**车次**（_verify_train）：跟酒店同一个思路，但校验方式不同——车次号是个
具体的字符串（比如 G1234），可以直接检查它有没有在搜索结果原文里出现过；
没出现过大概率是编的，代码会把它清空并加提醒，而不是假装这是查到的车次号。

另一个绕不开的问题是信息时效性：网页搜索到的酒店/餐厅可能已经关门、改名、搬迁，
页面不会自动更新，模型和代码都没法凭空判断"这条信息现在还准不准"。能做的是把
信息来源（标题+链接）和来源里提到的时间线索留痕，让用户自己判断可信度、
需要的话点进去核实，而不是假装自己能验证时效性。
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from ..cache.store import cache_age_seconds, cached_model
from ..config import UserPreferences
from ..domain.models import Hotel, Itinerary, TrainOption
from ..llm.client import LLMClient
from ..search.duckduckgo import search_web

ProgressCallback = Callable[[str], None]

_STEP_LABELS = {
    "hotel": "酒店",
    "train_out": "去程车次",
    "train_back": "返程车次",
    "main": "行程和餐厅",
}

MAIN_SYSTEM_PROMPT = """你是一个严谨的短途旅行规划助手。现在需要针对已经选定的目的地城市，
基于下面提供的搜索结果，组织出一份完整、可执行的行程方案。

强制要求：
1. 餐厅地址这类事实信息，只能从下面提供的搜索结果里提取；如果搜索结果里没有
   明确提到，就在对应字段的 note 里如实写"未查到确切信息，出发前请自行核实"，
   绝对不能自己编造具体的门牌号。
2. 每日行程可以基于你自己对目的地的了解来编排（景点、顺序、大致游玩时间），
   但要精确到时间点，并标明交通方式和大致耗时。
3. 餐厅推荐优先选搜索结果里提到的本地人常去、评价好的选项；【必须确认这家店真的
   在目的地城市】——免费搜索引擎偶尔会混进跟目的地毫不相关的结果（比如查"长沙餐厅"
   却混进了天津某家店），如果某条搜索结果压根没提到目的地城市名、或者明显在说
   另一个城市，绝对不能把它当成目的地的推荐写进去，宁可少写一条或如实说"未查到
   确切的本地餐厅推荐"。如果条件允许，判断一下这道菜/这家店在出发城市是否已经有
   类似的替代选择，写进 note。
4. 【信息来源标注】每一条 food_spots 都要填 source_title 和 source_url——写你
   实际参考的那条搜索结果的标题和链接，方便用户点进去自己核实，不要编一个不存在的
   链接。如果确实没有参考任何具体搜索结果，就留空，不要瞎填。同时看一下参考的
   搜索结果标题/摘要里有没有提到具体年份或日期，有的话填进 source_date_hint
   （比如"2023年"），没有就留空，不要猜测编造日期。
5. uniqueness_verdict 字段要给出一句诚实的判断：这趟出行有没有出发城市体验不到的东西，
   如果没有找到有说服力的理由，也要如实说明，不要为了鼓励用户出行而夸大。
6. walk_intensity_score 是 0~1 的数字，代表这份行程整体的游玩强度，
   要结合用户对走路的接受程度来安排具体景点（很怕走路就避开需要大量爬台阶/登山的点）。
7. caveats 字段先总结这份行程里有哪些具体信息是"没查到、需要用户自己核实"的，
   然后固定加上这句话（一字不改地包含进去）："地址、酒店、餐厅信息基于网络搜索生成，
   可能已停业、改名或搬迁，出发前请自行核实最新状态。"——不管其他信息查得齐不齐全，
   这句话都要带上，这是通用提醒，不是"出问题了才提醒"。
8. hotel、train_out、train_back 这三个字段都会由系统另外单独核实、之后整体覆盖替换掉，
   你在这里不用认真处理，随便填占位内容即可，不要在这几个字段上花心思或编造数字。
9. 输出一个 JSON 对象，字段结构必须严格符合下面的 schema：

{schema}
"""

HOTEL_SYSTEM_PROMPT = """你是短途旅行规划助手，现在只负责一件事：基于搜索结果推荐一家合适的酒店。

规则（按重要性排序，第2条是这个任务里最容易出错、也最重要的一条）：
1. 酒店名称、地址只从搜索结果里提取；搜索结果里有具体门牌号/街道就写，
   没有的话写大致区域（比如"XX区"），并在 note 里说明"具体地址未查到，请出发前核实"。
   同样要注意搜索结果里可能混进了别的城市的酒店（免费搜索引擎召回不准），
   如果某条结果没有明确提到目的地城市，不要把它当成目的地的酒店推荐。
2. 【最重要】价格：只有当搜索结果的文字里【明确出现具体数字】的价格时，才把它写进
   price_range 字段；如果搜索结果里完全没有具体数字，price_range 必须原样写
   "未查到具体价格"这几个字，绝对不能自己编一个"看起来合理"的数字——
   哪怕你觉得根据经验能猜出大概价位，也不允许写进这个字段，因为用户会把这个数字
   当真、按它做预算决策。宁可承认没查到，也不能给一个假数字。
3. 【信息来源】填 source_title 和 source_url——写你实际参考的那条搜索结果的标题和链接，
   方便用户点进去自己核实这家酒店现在是否还在营业、信息是否准确。如果搜索结果标题/摘要
   里提到了具体年份，填进 source_date_hint，没有就留空，不要编。
4. 输出一个 JSON 对象，字段：name, address, price_range, note, source_title, source_url,
   source_date_hint。
"""

TRAIN_SYSTEM_PROMPT = """你是短途旅行规划助手，现在只负责一件事：基于搜索结果确定{direction}的高铁信息
（{dep_city} → {arr_city}）。

规则（按重要性排序，第2条是这个任务里最容易出错、也最重要的一条）：
1. 出发/到达车站，能从搜索结果里明确看出来就填，看不出来就留空，不要瞎猜具体站名。
2. 【最重要】车次号：只有当搜索结果的文字里【明确出现】类似 G1234、D567 这种具体
   车次编号时，才把它写进 train_no 字段；搜索结果里没有具体车次号，train_no 必须留空，
   绝对不能自己编一个"看起来像"的车次号——车次号编错了，用户到时候会在车站找错车。
3. 大致时长（duration）相对宽松一些：只要搜索结果里提到了大致耗时（比如"1.5小时"、
   "约3小时"），哪怕没有具体车次号，也可以把这个大致时长填进 duration 字段，
   并在 note 里说明"具体车次未查到，仅供参考大致时长"。
4. 如果搜索结果里完全没有任何时长或车次信息，duration/train_no/dep_time/arr_time
   都留空，note 写"未查到确切车次信息，出发前请自行核实"。
5. 【信息来源】填 source_title 和 source_url——写你实际参考的那条搜索结果的标题和链接。
   如果标题/摘要里提到了具体年份，填进 source_date_hint，没有就留空，不要编。
6. 输出一个 JSON 对象，字段：train_no, dep_station, dep_time, arr_station, arr_time,
   duration, is_direct, note, source_title, source_url, source_date_hint。
"""


def _cache_payload(llm: LLMClient, prefs: UserPreferences, destination: str) -> dict:
    return {
        "departure": prefs.departure_city,
        "destination": destination,
        "budget": [prefs.budget_min, prefs.budget_max],
        "walk_tolerance_bucket": round(prefs.w_walk_tolerance * 4) / 4,  # 分桶，避免微小差异全部 cache miss
        "model": llm.model,
    }


def itinerary_cache_age_seconds(llm: LLMClient, prefs: UserPreferences, destination: str) -> float | None:
    """只读查询：这个目的地对应的详细行程缓存存在多久了，没命中返回 None。
    给界面在用户点「生成详细行程」之前就提示"这是缓存结果"用。
    """
    return cache_age_seconds("itinerary", _cache_payload(llm, prefs, destination))


def build_itinerary(
    llm: LLMClient,
    prefs: UserPreferences,
    destination: str,
    on_progress: ProgressCallback | None = None,
    force_refresh: bool = False,
) -> Itinerary:
    cache_payload = _cache_payload(llm, prefs, destination)
    # 命中缓存的话不会真的跑那几个子任务，on_progress 也就不会被调用——这是对的，
    # 缓存命中本来就没有"进度"可言，秒开就完事了。force_refresh=True 时跳过读缓存，
    # 强制重新生成（比如用户怀疑酒店/餐厅信息已经过时，想主动刷新）。
    return cached_model(
        "itinerary",
        cache_payload,
        Itinerary,
        compute=lambda: _build_itinerary_uncached(llm, prefs, destination, on_progress),
        force_refresh=force_refresh,
    )


def _build_itinerary_uncached(
    llm: LLMClient,
    prefs: UserPreferences,
    destination: str,
    on_progress: ProgressCallback | None = None,
) -> Itinerary:
    # 酒店核实、去程车次核实、返程车次核实、"车次+餐厅+行程"大调用，四件事互相独立，
    # 全部并发跑（各自内部可能还会再并发发搜索请求，嵌套的线程池对 I/O 密集型任务没问题）。
    # 用 as_completed 而不是按固定顺序调用 .result()，这样进度提示反映的是真实的
    # 完成顺序，不是"代码写的时候随手排的顺序"。
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_verify_hotel, llm, destination, prefs.budget_min, prefs.budget_max): "hotel",
            pool.submit(_verify_train, llm, prefs.departure_city, destination, "去程"): "train_out",
            pool.submit(_verify_train, llm, destination, prefs.departure_city, "返程"): "train_back",
            pool.submit(_build_main_itinerary, llm, prefs, destination): "main",
        }
        results: dict[str, object] = {}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()
            if on_progress:
                on_progress(f"{_STEP_LABELS[key]}核实完成")

    itinerary = results["main"]
    itinerary.hotel = results["hotel"]
    itinerary.train_out = results["train_out"]
    itinerary.train_back = results["train_back"]
    return itinerary


def _build_main_itinerary(llm: LLMClient, prefs: UserPreferences, destination: str) -> Itinerary:
    search_context = _gather_facts(destination)
    schema_hint = json.dumps(Itinerary.model_json_schema(), ensure_ascii=False, indent=2)

    user_prompt = f"""
出发城市：{prefs.departure_city}
目的地：{destination}
出发时间窗口：{prefs.depart_window}
返程要求：{prefs.return_deadline}
用户对"走路强度"的接受程度（0~1，越低越怕走）：{prefs.w_walk_tolerance}

以下是搜索到的相关信息，请基于这些信息生成完整方案：

{search_context}
"""
    return llm.chat_structured(
        messages=[
            {"role": "system", "content": MAIN_SYSTEM_PROMPT.format(schema=schema_hint)},
            {"role": "user", "content": user_prompt},
        ],
        schema=Itinerary,
    )


def _gather_facts(destination: str) -> str:
    # 车次由 _verify_train 单独处理、酒店由 _verify_hotel 单独处理，
    # 这个大调用现在只需要餐厅信息。
    query = f"{destination}本地人常去的餐厅有哪些推荐"
    text, used_query = _search_and_filter_food(destination, query)

    if text is None:
        # 第一个搜索词过滤完一条不剩：不直接放弃，换个说法再搜一次。免费搜索
        # 对措辞很敏感，跟酒店搜索用两个不同搜索词是同一个思路，只是这里没必要
        # 一开始就并发搜两次——绝大多数情况下第一次就够了，只有"过滤完一条不剩"
        # 这种少数情况才值得多花一次搜索。
        fallback_query = f"{destination}美食推荐 大众点评"
        text, used_query = _search_and_filter_food(destination, fallback_query)

    if text is None:
        # 真报过的 bug：搜"长沙本地餐厅"，DDGS 召回质量弱，混进了跟长沙毫不
        # 相关的结果（比如天津某家店），模型没做甄别就直接当成"长沙推荐"写了进去。
        # 两次搜索词换着搜都过滤完一条不剩，就直接告诉模型别编，如实说没查到。
        note = f"（两次搜索结果里都没有一条明确提到「{destination}」，可能是搜索引擎召回不准确，" \
               f"请如实告知用户未查到确切的本地餐厅推荐，不要挪用其他城市的信息来凑数。）"
        return f"【本地餐厅】搜索词：{query} / {fallback_query}\n  （没有搜到明确提到该城市的相关结果）\n{note}"

    return f"【本地餐厅】搜索词：{used_query}\n{text}"


def _search_and_filter_food(destination: str, query: str) -> tuple[str | None, str]:
    """搜索并过滤掉跟目的地城市毫不相关的结果。返回 None 表示过滤完一条不剩。"""
    results = search_web(query)
    relevant, dropped = _filter_relevant_to_destination(results, destination)
    if not relevant:
        return None, query
    text = _format_snippets(relevant)
    if dropped:
        text += f"\n（另有 {dropped} 条搜索结果因为完全没提到「{destination}」被过滤掉，避免张冠李戴推荐成别的城市。）"
    return text, query


def _filter_relevant_to_destination(results: list[dict], destination: str) -> tuple[list[dict], int]:
    """只留下标题/摘要里明确提到目的地城市名的搜索结果，其余的大概率是
    搜索引擎召回不准混进来的别的城市内容，不该被当成"本地信息"喂给模型。
    """
    if not destination:
        return results, 0
    relevant = []
    dropped = 0
    for r in results:
        if "error" in r:
            continue
        haystack = f"{r.get('title', '')} {r.get('snippet', '')}"
        if destination in haystack:
            relevant.append(r)
        else:
            dropped += 1
    return relevant, dropped


def _format_snippets(results: list[dict]) -> str:
    """保留标题和链接（不只是摘要文字），这样模型才有东西可以填进
    source_title / source_url，用户也才能真的点进去核实。
    """
    lines = []
    for r in results:
        if "error" in r:
            continue
        lines.append(f"  - 标题：{r.get('title', '')}｜链接：{r.get('url', '')}｜摘要：{r.get('snippet', '')}")
    return "\n".join(lines) or "  （没有搜到相关结果）"


def _verify_train(llm: LLMClient, dep_city: str, arr_city: str, direction_label: str) -> TrainOption:
    query = f"{dep_city}到{arr_city}高铁要几个小时"
    results = search_web(query)
    snippet_text = _format_snippets(results)

    prompt = f"""
{direction_label}：{dep_city} → {arr_city}

搜索结果：
{snippet_text}

请给出这段行程的高铁信息。
"""
    option = llm.chat_structured(
        messages=[
            {
                "role": "system",
                "content": TRAIN_SYSTEM_PROMPT.format(
                    direction=direction_label, dep_city=dep_city, arr_city=arr_city
                ),
            },
            {"role": "user", "content": prompt},
        ],
        schema=TrainOption,
    )
    option.dep_station = option.dep_station or dep_city
    option.arr_station = option.arr_station or arr_city
    return _validate_train_grounding(option, snippet_text)


def _validate_train_grounding(option: TrainOption, search_text: str) -> TrainOption:
    """兜底校验：车次号是个具体字符串，能直接检查它有没有真的出现在搜索结果原文里。
    模型编的车次号在原文里根本找不到，大概率是瞎编的——清空它，只保留大致时长
    （如果有的话），并明确提醒用户这个车次号没能核实。
    """
    if option.train_no and option.train_no not in search_text:
        warning = (
            f"⚠️ 未能在搜索结果里核实到「{option.train_no}」这个车次号，可能不准确，"
            "已隐藏，请自行在12306查询实际车次。"
        )
        option.train_no = ""
        option.note = f"{warning} {option.note}".strip() if option.note else warning
    return option


def _verify_hotel(llm: LLMClient, destination: str, budget_min: int, budget_max: int) -> Hotel:
    """核实酒店（_verify_hotel_primary）和经验兜底推荐（_recommend_fallback_hotel）
    从一开始就并发跑，不等核实失败了再补调用——两个都会真的调用模型，
    用哪个看核实结果，这是用"多花一次 API 调用"换"少等一整轮"的取舍。
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        primary_future = pool.submit(_verify_hotel_primary, llm, destination, budget_min, budget_max)
        fallback_future = pool.submit(_recommend_fallback_hotel, llm, destination, budget_min, budget_max)

        primary = _validate_hotel_price(primary_future.result(), budget_min, budget_max)
        if primary.price_verified:
            return primary

        # 核实没成功：用兜底推荐那次已经跑完的结果，不用再等。
        return fallback_future.result()


def _verify_hotel_primary(llm: LLMClient, destination: str, budget_min: int, budget_max: int) -> Hotel:
    query1 = f"{destination}有哪些性价比高的酒店，{budget_min}到{budget_max}元一晚"
    query2 = f"{destination}酒店 {budget_min}元到{budget_max}元 推荐"

    # 两个搜索词一起并发发出去，而不是先搜一次、看没有价格数字再搜第二次——
    # 实测发现免费搜索对中文酒店价格的召回本来就弱，第二次搜索几乎总是会触发，
    # 与其"先串行搜一次，大概率没用，再串行搜第二次"，不如一开始就并行搜两次。
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(search_web, query1)
        f2 = pool.submit(search_web, query2)
        results1 = f1.result()
        results2 = f2.result()

    # 跟餐厅搜索同一个思路：完全没提到目的地城市名的结果，大概率是搜索引擎
    # 召回不准混进来的别的城市内容，先过滤掉再喂给模型，不指望模型自己甄别。
    relevant1, _ = _filter_relevant_to_destination(results1, destination)
    relevant2, _ = _filter_relevant_to_destination(results2, destination)

    snippet_text = (
        f"搜索词1：{query1}\n{_format_snippets(relevant1)}\n\n"
        f"搜索词2：{query2}\n{_format_snippets(relevant2)}"
    )

    prompt = f"""
目的地：{destination}
预算：{budget_min}-{budget_max} 元/晚

搜索结果：
{snippet_text}

请给出一家合适的酒店。
"""
    return llm.chat_structured(
        messages=[
            {"role": "system", "content": HOTEL_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        schema=Hotel,
    )


def _validate_hotel_price(hotel: Hotel, budget_min: int, budget_max: int) -> Hotel:
    """兜底校验：就算 _verify_hotel 的提示词没拦住，这里再做一次代码层面的把关。

    两种情况都算"没能确认"：
    1. 价格字段里完全没有数字（模型老实承认查不到）；
    2. 有数字，但全部明显偏离预算区间（留了50%容差，因为搜索本身就有误差）——
       这种情况很可能是编造的，附上清楚的警告，而不是让它带着"很确定"的样子展示出去。

    两种情况都会把 price_verified 设成 False，交给上层触发兜底推荐流程。
    """
    numbers = [int(n) for n in re.findall(r"\d+", hotel.price_range or "")]

    if not numbers:
        hotel.price_verified = False
        return hotel

    lower_bound = budget_min * 0.5
    upper_bound = budget_max * 1.5
    if all(n < lower_bound or n > upper_bound for n in numbers):
        hotel.price_verified = False
        warning = (
            f"⚠️ 原始价格「{hotel.price_range}」明显超出预算区间（{budget_min}-{budget_max}元/晚），"
            "很可能是模型编造或搜索信息不准确。"
        )
        hotel.note = f"{warning} {hotel.note}".strip() if hotel.note else warning

    return hotel


FALLBACK_HOTEL_PROMPT = """目的地：{destination}
预算：{budget_min}-{budget_max} 元/晚

免费网页搜索经常查不到具体的酒店价格数字。请不要依赖某一条具体搜索结果，
而是基于你对{destination}当地酒店市场水平（这类城市这个价位一般能订到什么
档次的酒店）的了解，推荐一家类型/档次上大致匹配这个预算区间的酒店。

输出 JSON 对象，字段：name, address, note。
【重要】只推荐名称和地址，绝对不要在任何字段里写具体价格数字或价格区间——
不管是"预计"、"大概"、"仅供参考"这类说法都不行，哪怕只是猜测也不允许写数字，
因为用户已经明确表示不希望看到没有真实依据的价格。价格字段会由系统统一处理，
不需要你填写。这是基于你的经验判断而不是某条具体搜索结果，所以也不用填
source_title / source_url / source_date_hint，留空就行。
note 字段说明推荐这家的理由（比如档次、地段），不要提具体价格。
"""


def _recommend_fallback_hotel(llm: LLMClient, destination: str, budget_min: int, budget_max: int) -> Hotel:
    # 注意：这个函数现在跟 _verify_hotel_primary 并发跑，不知道也不依赖
    # 核实那条路径的结果（不能引用"之前尝试的酒店是哪家"），两条路径完全独立。
    prompt = FALLBACK_HOTEL_PROMPT.format(
        destination=destination,
        budget_min=budget_min,
        budget_max=budget_max,
    )
    hotel = llm.chat_structured(messages=[{"role": "user", "content": prompt}], schema=Hotel)

    # 价格字段完全不信任模型这次的输出——不管它写了什么（哪怕只是想"帮个忙"
    # 编个大概数字），一律用固定文案覆盖掉。上一版就是栽在这里：提示词里给的
    # "预计{budget_min}元上下"这个示例被模型原样抄了一遍，等于把用户设的预算
    # 下限直接当成了"查到的价格"展示出去。价格这个字段，宁可结构上就不给
    # 模型自由发挥的空间。
    hotel.price_range = "未查到价格"
    hotel.price_verified = False
    # 这是经验推荐、不对应某条具体搜索结果，来源字段也不该留着上一次尝试的痕迹。
    hotel.source_title = ""
    hotel.source_url = ""
    hotel.source_date_hint = ""
    fallback_note = "没有查到确定符合预算的酒店，以下是基于当地酒店档次的经验推荐，不含价格信息，请自行在携程/美团搜索确认价格。"
    reason = hotel.note.strip() if hotel.note else ""
    hotel.note = f"{fallback_note} {reason}".strip() if reason else fallback_note
    return hotel
