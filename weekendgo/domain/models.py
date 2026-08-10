"""结构化数据模型。

所有"事实类"字段（车次、价格、地址）都应该由工具调用得到的信息填充；
LLM 只负责组织、判断、排序，提示词里会要求它不要直接臆造这些数字，
但这里仍然把字段都设了合理默认值，防止模型偶尔漏填导致校验直接失败。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SourcedFact(BaseModel):
    """带信息来源的字段。网页搜索天生有时效性问题——页面写的时候是对的，
    现实变了（店关了、改名了）页面不会跟着自动更新，模型和代码都没法凭空判断
    "这条信息现在还准不准"。能做到的是把来源留痕，让用户自己一眼判断可信度、
    需要的话能点进去核实，而不是假装自己能验证时效性。
    """

    source_title: str = ""  # 这条信息是从哪条搜索结果提炼的（标题）
    source_url: str = ""  # 对应的链接，方便用户点进去自己核实
    source_date_hint: str = ""  # 来源标题/摘要里提到的年份或日期（如果有），仅供参考，不保证准确


class TrainOption(SourcedFact):
    train_no: str = ""
    dep_station: str = ""
    dep_time: str = ""
    arr_station: str = ""
    arr_time: str = ""
    duration: str = ""
    is_direct: bool = True
    note: str = ""


class Hotel(SourcedFact):
    # 这三个字段特意给了默认值——虽然正常情况下模型应该都会填，但兜底推荐那条
    # 路径的提示词会明确告诉模型"价格不用你填"，如果这里是必填字段（没有默认值），
    # 模型照做省略掉就会直接导致 Pydantic 校验失败、整个请求崩掉。反正 price_range
    # 最终都会被代码强制覆盖，默认值是不是空字符串根本不影响最终展示结果，
    # 只影响"要不要因为一个可以不填的字段崩掉"。
    name: str = ""
    address: str = ""
    price_range: str = ""
    note: str = ""
    price_verified: bool = True  # False = 价格没能在搜索结果里核实到，或者跟预算差太多，别太信


class FoodSpot(SourcedFact):
    name: str
    address: str = ""
    dish: str = ""
    category: str = "其他"  # 早餐 / 午餐 / 晚餐 / 宵夜
    locally_endorsed: bool = True
    note: str = ""


class DayStep(BaseModel):
    time: str = ""
    action: str
    transport_mode: str = ""
    duration_note: str = ""


class DayPlan(BaseModel):
    label: str  # 例如 "Day 1 · 周五"
    steps: list[DayStep] = Field(default_factory=list)


class CityItem(BaseModel):
    """候选目的地脑暴阶段的最小单元，只有城市名，不含打分（打分在 verify 阶段做）。"""

    city: str
    province: str = ""


class CityList(BaseModel):
    cities: list[CityItem] = Field(default_factory=list)


class Candidate(BaseModel):
    # city 同样给了默认值——candidate_gen.py 里 _verify_one 会在模型漏填时
    # 用代码传入的城市名兜底补上（if not candidate.city: candidate.city = city），
    # 但那段兜底代码的前提是 chat_structured 得先成功返回一个对象；如果 city
    # 是必填字段，模型漏填会在校验这一步就直接崩掉，兜底代码根本执行不到。
    city: str = ""
    province: str = ""
    reason: str = ""
    train_hours_estimate: float = Field(default=0.0, ge=0)
    est_scenery_score: float = Field(default=0.5, ge=0, le=1)
    est_food_score: float = Field(default=0.5, ge=0, le=1)
    est_walk_intensity: float = Field(default=0.5, ge=0, le=1)
    est_uniqueness_score: float = Field(default=0.5, ge=0, le=1)
    total_score: float = 0.0


class Itinerary(BaseModel):
    destination: str
    train_out: TrainOption
    train_back: TrainOption
    hotel: Hotel
    days: list[DayPlan] = Field(default_factory=list)
    food_spots: list[FoodSpot] = Field(default_factory=list)
    uniqueness_verdict: str = ""
    walk_intensity_score: float = Field(default=0.5, ge=0, le=1)
    caveats: str = ""  # 提醒用户哪些信息需要临行前自己核实
