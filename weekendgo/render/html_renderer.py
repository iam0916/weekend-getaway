"""把 Itinerary 渲染成跟参考设计一致的完整 HTML 页面。

CSS 设计系统沿用同一位作者之前给这个用户做的"厦门周末出省指南"页面
（配色、字体、卡片布局、时间线样式都是同一套 token），保持视觉一致性，
不是随手另起一套风格。
"""
from __future__ import annotations

import html

from ..domain.models import Candidate, DayPlan, FoodSpot, Itinerary, TrainOption

_BASE_CSS = """
:root {
  --paper: #eff1e9;
  --paper-card: #f8f8f3;
  --ink: #1c2622;
  --ink-soft: #4b554f;
  --accent: #0e6b57;
  --accent-soft: #dceae3;
  --gold: #a97a30;
  --warn: #b8562f;
  --warn-soft: rgba(184, 86, 47, 0.12);
  --line: rgba(28, 38, 34, 0.14);
  --line-strong: rgba(28, 38, 34, 0.28);
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #111815;
    --paper-card: #161f1b;
    --ink: #edefe7;
    --ink-soft: #b7c0b8;
    --accent: #3fbfa0;
    --accent-soft: #1b2e28;
    --gold: #d6a65c;
    --warn: #e08a5c;
    --warn-soft: rgba(224, 138, 92, 0.16);
    --line: rgba(237, 239, 231, 0.16);
    --line-strong: rgba(237, 239, 231, 0.3);
  }
}
:root[data-theme="dark"] {
  --paper: #111815; --paper-card: #161f1b; --ink: #edefe7; --ink-soft: #b7c0b8;
  --accent: #3fbfa0; --accent-soft: #1b2e28; --gold: #d6a65c;
  --warn: #e08a5c; --warn-soft: rgba(224, 138, 92, 0.16);
  --line: rgba(237, 239, 231, 0.16); --line-strong: rgba(237, 239, 231, 0.3);
}
:root[data-theme="light"] {
  --paper: #eff1e9; --paper-card: #f8f8f3; --ink: #1c2622; --ink-soft: #4b554f;
  --accent: #0e6b57; --accent-soft: #dceae3; --gold: #a97a30;
  --warn: #b8562f; --warn-soft: rgba(184, 86, 47, 0.12);
  --line: rgba(28, 38, 34, 0.14); --line-strong: rgba(28, 38, 34, 0.28);
}
* { box-sizing: border-box; }
body {
  background: var(--paper); color: var(--ink);
  font-family: "Heiti SC", "STHeiti", "Microsoft YaHei", -apple-system, sans-serif;
  line-height: 1.6; margin: 0; padding: 24px 20px 60px;
}
.wrap { max-width: 760px; margin: 0 auto; }
h1, h2, h3 {
  font-family: "Songti SC", "STSong", "Noto Serif SC", serif;
  font-weight: 600; text-wrap: balance; margin: 0;
}
header { padding: 8px 0 24px; border-bottom: 1px solid var(--line); }
.eyebrow {
  font-size: 12.5px; letter-spacing: 0.14em; color: var(--accent);
  font-weight: 600; text-transform: uppercase;
}
h1 { font-size: 28px; margin-top: 10px; color: var(--ink); }
header p { color: var(--ink-soft); font-size: 14.5px; margin-top: 10px; max-width: 62ch; }
section.dest {
  margin: 24px 0; border: 1px solid var(--line); border-radius: 14px;
  background: var(--paper-card); overflow: hidden;
}
.dest-head { padding: 26px 26px 20px; border-bottom: 1px solid var(--line); }
.dest-head h2 {
  font-size: 25px; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
}
.dest-head h2 .tag {
  font-family: "Heiti SC", sans-serif; font-size: 13px; font-weight: 600;
  color: var(--gold); background: rgba(169, 122, 48, 0.14);
  padding: 3px 10px; border-radius: 999px;
}
.route {
  margin-top: 14px; display: flex; align-items: center; gap: 10px;
  font-size: 13px; color: var(--ink-soft); flex-wrap: wrap;
}
.route svg { flex-shrink: 0; }
.route .dur { font-variant-numeric: tabular-nums; color: var(--accent); font-weight: 600; }
.route-note { margin-top: 8px; font-size: 12.5px; color: var(--ink-soft); }
.dest-body { padding: 22px 26px 26px; display: grid; gap: 22px; }
@media (min-width: 620px) {
  .dest-body { grid-template-columns: 1fr 1fr; }
  .dest-body .full { grid-column: 1 / -1; }
}
.block h3 {
  font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-soft);
  font-family: "Heiti SC", sans-serif; margin-bottom: 12px;
}
.hotel { padding: 0; margin-bottom: 14px; }
.hotel:last-child { margin-bottom: 0; }
.hotel-name { font-weight: 600; font-size: 14.5px; }
.hotel-price {
  float: right; font-variant-numeric: tabular-nums; color: var(--gold);
  font-weight: 600; font-size: 14px;
}
.hotel-price.unverified { color: var(--warn); }
.hotel-price .unverified-tag {
  display: inline-block; font-size: 11px; font-weight: 700; margin-left: 6px;
  background: var(--warn-soft); color: var(--warn); padding: 1px 7px; border-radius: 999px;
  vertical-align: middle;
}
.hotel-addr { font-size: 13px; color: var(--ink-soft); margin-top: 3px; }
.hotel-note { font-size: 13px; color: var(--ink-soft); margin-top: 4px; }
.hotel-note.warn { color: var(--warn); }
.day { margin-bottom: 16px; }
.day:last-child { margin-bottom: 0; }
.day-label { font-size: 12.5px; font-weight: 600; color: var(--accent); margin-bottom: 8px; }
.steps { list-style: none; margin: 0; padding: 0; }
.steps li {
  display: flex; gap: 12px; font-size: 14px; padding: 6px 0;
  border-top: 1px dashed var(--line);
}
.steps li:first-child { border-top: none; padding-top: 0; }
.steps .t {
  flex-shrink: 0; width: 44px; font-variant-numeric: tabular-nums;
  font-weight: 700; color: var(--ink); font-size: 13px; padding-top: 1px;
}
.steps .how { display: block; font-size: 12px; color: var(--accent); margin-top: 2px; font-weight: 500; }
.foods { display: flex; flex-wrap: wrap; gap: 8px; }
.foods span {
  font-size: 13px; background: var(--accent-soft); color: var(--accent);
  padding: 5px 11px; border-radius: 999px; font-weight: 500;
}
.tip {
  border-left: 3px solid var(--gold); padding: 10px 16px; font-size: 13.5px;
  color: var(--ink-soft); background: rgba(169, 122, 48, 0.07); border-radius: 0 8px 8px 0;
}
.tip b { color: var(--ink); }
.score-row { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: var(--ink-soft); }
.score-row b { color: var(--ink); }
.source-line { font-size: 12px; color: var(--ink-soft); margin-top: 4px; }
.source-line a { color: var(--accent); text-decoration: none; }
.source-line a:hover { text-decoration: underline; }
.stale-hint {
  font-size: 12px; color: var(--ink-soft); margin-bottom: 10px; letter-spacing: 0.01em;
}
/* 中文字体没有真正的斜体字形，用斜体样式会逼着渲染引擎伪造一个倾斜变体、
   在 PDF 导出时多生成一个不规范的字体子集（真报过的乱码 bug 根因就在这里），
   所以这里改用左侧圆点 + 弱化颜色来做视觉区分，不碰斜体这个属性。 */
.stale-hint::before { content: "· "; color: var(--line-strong); }
"""


def _esc(s: str) -> str:
    return html.escape(s or "")


def _source_line(fact) -> str:
    """把 SourcedFact 的来源信息渲染成一行小字：链接可点、日期线索仅供参考。
    没有任何来源信息时返回空字符串，不占位置。
    """
    parts = []
    if getattr(fact, "source_url", ""):
        label = _esc(getattr(fact, "source_title", "")) or "查看来源"
        parts.append(f'<a href="{_esc(fact.source_url)}" target="_blank" rel="noopener">{label} ↗</a>')
    elif getattr(fact, "source_title", ""):
        parts.append(_esc(fact.source_title))
    if getattr(fact, "source_date_hint", ""):
        parts.append(f"来源信息约为{_esc(fact.source_date_hint)}")
    if not parts:
        return ""
    return f'<div class="source-line">{" · ".join(parts)}</div>'


def _route_svg() -> str:
    return (
        '<svg width="100" height="14" viewBox="0 0 120 14">'
        '<circle cx="4" cy="7" r="3" fill="currentColor"/>'
        '<line x1="8" y1="7" x2="112" y2="7" stroke="currentColor" stroke-width="1.2" stroke-dasharray="3 3"/>'
        '<circle cx="116" cy="7" r="3" fill="currentColor"/>'
        "</svg>"
    )


def _train_line(t: TrainOption) -> str:
    no = f" <b>{_esc(t.train_no)}</b>" if t.train_no else ""
    dur = f'<span class="dur">{_esc(t.duration)}</span>' if t.duration else ""
    return (
        f"{_esc(t.dep_station)} → {_esc(t.arr_station)}{no} "
        f"{_esc(t.dep_time)}→{_esc(t.arr_time)} {dur}"
    )


def _render_days(days: list[DayPlan]) -> str:
    if not days:
        return '<p style="color:var(--ink-soft);font-size:13px;">（没有生成具体行程步骤）</p>'
    blocks = []
    for day in days:
        steps_html = "\n".join(
            f'<li><span class="t">{_esc(s.time)}</span>'
            f"<div>{_esc(s.action)}"
            + (f'<span class="how">{_esc(s.transport_mode)} · {_esc(s.duration_note)}</span>' if s.transport_mode else "")
            + "</div></li>"
            for s in day.steps
        )
        blocks.append(
            f'<div class="day"><div class="day-label">{_esc(day.label)}</div>'
            f'<ul class="steps">{steps_html}</ul></div>'
        )
    return "\n".join(blocks)


def _render_food(food_spots: list[FoodSpot]) -> str:
    if not food_spots:
        return '<p style="color:var(--ink-soft);font-size:13px;">（没有生成餐厅推荐）</p>'
    rows = []
    for f in food_spots:
        tag = "本地人常去" if f.locally_endorsed else "游客向"
        addr = f" —— {_esc(f.address)}" if f.address else ""
        note = f'<div class="hotel-note">{_esc(f.note)}</div>' if f.note else ""
        rows.append(
            f'<div class="hotel"><div class="hotel-name">{_esc(f.name)}'
            f'<span style="font-weight:400;color:var(--ink-soft);font-size:12.5px;"> · {_esc(f.category)} · {tag}</span></div>'
            f'<div class="hotel-addr">{_esc(f.dish)}{addr}</div>{note}{_source_line(f)}</div>'
        )
    return "\n".join(rows)


def render_itinerary_html(
    it: Itinerary,
    departure_city: str = "",
    candidate: Candidate | None = None,
) -> str:
    tag = ""
    if candidate is not None:
        tag = f"综合得分 {candidate.total_score:.2f}"
    route_prefix = f"{_esc(departure_city)} → " if departure_city else ""

    hotel_note_class = "hotel-note warn" if not it.hotel.price_verified else "hotel-note"
    hotel_note = f'<div class="{hotel_note_class}">{_esc(it.hotel.note)}</div>' if it.hotel.note else ""
    hotel_price_class = "hotel-price unverified" if not it.hotel.price_verified else "hotel-price"
    hotel_price_tag = '<span class="unverified-tag">未核实</span>' if not it.hotel.price_verified else ""
    caveats_html = f'<div class="tip" style="margin-top:14px;">⚠️ {_esc(it.caveats)}</div>' if it.caveats else ""

    score_row = ""
    if candidate is not None:
        score_row = (
            '<div class="score-row">'
            f"<span>景色 <b>{candidate.est_scenery_score:.2f}</b></span>"
            f"<span>美食 <b>{candidate.est_food_score:.2f}</b></span>"
            f"<span>游玩强度 <b>{candidate.est_walk_intensity:.2f}</b></span>"
            f"<span>独特性 <b>{candidate.est_uniqueness_score:.2f}</b></span>"
            "</div>"
        )

    body = f"""<meta charset="UTF-8">
<title>{_esc(it.destination)} 详细行程</title>
<style>{_BASE_CSS}</style>
<div class="wrap">
<header>
  <div class="eyebrow">周末去哪玩 · 自动生成</div>
  <h1>{_esc(it.destination)}</h1>
  {f'<p>{tag}</p>' if tag else ''}
</header>

<section class="dest">
  <div class="dest-head">
    <h2>{_esc(it.destination)}</h2>
    <div class="route">
      {_route_svg()}
      {route_prefix}{_train_line(it.train_out)}
    </div>
    {f'<div class="route-note">{_esc(it.train_out.note)}</div>' if it.train_out.note else ''}
    {_source_line(it.train_out)}
    {score_row}
  </div>
  <div class="dest-body">
    <div class="block">
      <h3>住哪</h3>
      <div class="stale-hint">酒店信息可能已过时（停业/改名/搬迁），建议核对来源链接或自行搜索确认</div>
      <div class="hotel">
        <span class="{hotel_price_class}">{_esc(it.hotel.price_range)}{hotel_price_tag}</span>
        <div class="hotel-name">{_esc(it.hotel.name)}</div>
        <div class="hotel-addr">{_esc(it.hotel.address)}</div>
        {hotel_note}
        {_source_line(it.hotel)}
      </div>
    </div>
    <div class="block">
      <h3>游玩强度</h3>
      <div class="tip">评分 <b>{it.walk_intensity_score:.2f}</b>（0=几乎不用走路，1=强度很高）</div>
    </div>
    <div class="block full">
      <h3>去哪吃</h3>
      <div class="stale-hint">餐厅信息可能已过时（停业/改名/搬迁），建议核对来源链接或自行搜索确认</div>
      {_render_food(it.food_spots)}
    </div>
    <div class="block full">
      <h3>行程</h3>
      {_render_days(it.days)}
    </div>
    <div class="block full">
      <h3>返程</h3>
      <div class="route">
        {_route_svg()}
        {_train_line(it.train_back)}
      </div>
      {f'<div class="route-note">{_esc(it.train_back.note)}</div>' if it.train_back.note else ''}
      {_source_line(it.train_back)}
    </div>
    <div class="block full">
      <h3>值不值得去</h3>
      <div class="tip">{_esc(it.uniqueness_verdict) or "（模型未给出判断）"}</div>
      {caveats_html}
    </div>
  </div>
</section>
</div>
"""
    return body
