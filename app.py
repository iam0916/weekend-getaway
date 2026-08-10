"""周末去哪玩 —— Streamlit 前端入口。

本地运行：
    streamlit run app.py
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from weekendgo.config import UserPreferences, load_llm_settings
from weekendgo.llm.client import LLMClient
from weekendgo.pipeline.orchestrator import TripPlanner
from weekendgo.render.html_renderer import render_itinerary_html
from weekendgo.render.pdf_renderer import html_to_pdf_bytes

st.set_page_config(page_title="周末去哪玩", page_icon="🧳", layout="wide")

# 全局样式微调：主题色在 .streamlit/config.toml 里配了，这里再补一点
# config.toml 管不到的细节（标题字体、卡片圆角、按钮观感），跟详细行程页
# 用的是同一套设计语言，两边不会看着像两个产品。
st.markdown(
    """
    <style>
    h1, h2, h3 { font-family: "Songti SC", "STSong", "Noto Serif SC", serif !important; }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px !important;
        border-color: rgba(28, 38, 34, 0.14) !important;
    }
    .stButton button {
        border-radius: 8px !important;
        font-weight: 600 !important;
    }
    .train-badge {
        display: inline-block; font-size: 12.5px; font-weight: 600;
        background: #dceae3; color: #0e6b57; padding: 2px 9px;
        border-radius: 999px; margin-left: 8px;
    }
    .score-badge {
        display: inline-block; font-size: 12.5px; font-weight: 600;
        background: rgba(169, 122, 48, 0.14); color: #a97a30; padding: 2px 9px;
        border-radius: 999px; margin-left: 6px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧳 周末去哪玩")
st.caption("按你的出发城市、时间约束、预算和偏好，自动生成一份短途旅行方案")


def _format_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes}分钟"
    hours, mins = divmod(minutes, 60)
    return f"{hours}小时{mins}分钟" if mins else f"{hours}小时"


def _show_friendly_error(prefix: str, error: Exception) -> None:
    """失败时不要把原始异常直接糊在页面上——先给一句人话，
    技术细节收进折叠框，需要排查的时候自己点开看，不吓唬人。
    """
    st.error(f"{prefix}，可能是网络或模型响应异常，重新点一次按钮通常就能恢复。")
    with st.expander("查看详细错误信息（排查用）"):
        st.code(f"{type(error).__name__}: {error}")


# ---- session state 初始化 ----
st.session_state.setdefault("candidates", None)
st.session_state.setdefault("last_prefs", None)
st.session_state.setdefault("itineraries", {})  # {city: Itinerary}

with st.sidebar:
    st.header("① 模型设置")
    use_env = st.checkbox("使用 .env 中的配置", value=True)

    if use_env:
        env_settings = load_llm_settings()
        st.text_input(
            "API Key",
            value=("已从 .env 读取（不显示明文）" if env_settings.api_key else "未在 .env 中找到"),
            disabled=True,
        )
        base_url = st.text_input("Base URL", value=env_settings.base_url)
        model = st.text_input("模型名称", value=env_settings.model)
        api_key = env_settings.api_key
    else:
        api_key = st.text_input(
            "API Key", type="password", help="只保存在本次会话内存中，不会写入任何文件"
        )
        base_url = st.text_input("Base URL", value="https://open.bigmodel.cn/api/paas/v4")
        model = st.text_input("模型名称", value="glm-4-air")

    st.divider()
    st.header("② 出行约束")
    departure_city = st.text_input("出发城市", value="厦门")
    depart_window = st.text_input("出发时间窗口", value="周五晚上或周六一早")
    return_deadline = st.text_input("返程要求", value="周日晚上或周一之前到家")
    max_train_hours = st.slider("高铁时长上限（小时，单程）", 1.0, 8.0, 3.0, 0.5)
    budget = st.slider("酒店预算（元/晚）", 100, 2000, (400, 800), 50)
    num_candidates = st.slider("候选目的地数量", 2, 6, 4)

    st.divider()
    st.header("③ 个性化权重")
    st.caption("拖动滑块表达偏好，权重越高代表越看重这一项")
    w_scenery = st.slider("景色权重", 0.0, 1.0, 0.5)
    w_food = st.slider("美食权重", 0.0, 1.0, 0.5)
    w_walk_tolerance = st.slider("能接受走路/爬山的程度", 0.0, 1.0, 0.3)
    w_uniqueness = st.slider("独特性 / 时效性权重", 0.0, 1.0, 0.5)

    # 提前把 prefs 建出来（用当前控件的实时取值），一是submit时候要用，
    # 二是可以在按按钮之前就查一下这组条件是不是已经命中缓存、缓存有多新——
    # 用户据此判断要不要勾"强制重新查询"，而不是点了按钮才发现是秒开的旧结果。
    prefs = UserPreferences(
        departure_city=departure_city,
        depart_window=depart_window,
        return_deadline=return_deadline,
        max_train_hours=max_train_hours,
        budget_min=budget[0],
        budget_max=budget[1],
        num_candidates=num_candidates,
        w_scenery=w_scenery,
        w_food=w_food,
        w_walk_tolerance=w_walk_tolerance,
        w_uniqueness=w_uniqueness,
    )

    candidates_cache_age = None
    if api_key:
        try:
            candidates_cache_age = TripPlanner(
                LLMClient(api_key=api_key, base_url=base_url, model=model)
            ).candidates_cache_age_seconds(prefs)
        except Exception:  # noqa: BLE001 - 只是个提示性查询，查不出来就不显示，不影响主流程
            candidates_cache_age = None

    st.divider()
    st.header("④ 缓存")
    if candidates_cache_age is not None:
        st.caption(f"这组条件命中缓存，生成于 {_format_age(candidates_cache_age)}前，点击「开始规划」会直接秒开。")
    force_refresh_candidates = st.checkbox(
        "忽略缓存，强制重新联网查询",
        value=False,
        help="候选目的地缓存 24 小时内有效；勾选后本次会重新调用模型和搜索，不使用之前缓存的结果（比如怀疑信息已经过时时用）。",
    )

    submit = st.button("开始规划", type="primary", use_container_width=True)

if submit:
    if not api_key:
        st.error("没有可用的 API Key：请在 .env 中配置 LLM_API_KEY，或取消勾选后手动填写。")
        st.stop()

    try:
        llm = LLMClient(api_key=api_key, base_url=base_url, model=model)
        planner = TripPlanner(llm)
        with st.status("正在生成候选目的地…", expanded=True) as status:
            candidates = planner.get_candidates(
                prefs, on_progress=lambda msg: status.write(f"✅ {msg}"), force_refresh=force_refresh_candidates
            )
            status.update(label="候选目的地生成完成", state="complete")
    except Exception as e:  # noqa: BLE001 - 顶层兜底，把错误直接展示给用户而不是让 app 崩溃
        _show_friendly_error("候选目的地生成失败", e)
        st.stop()

    st.session_state.candidates = candidates
    st.session_state.last_prefs = prefs
    st.session_state.itineraries = {}  # 换了一轮新偏好，清空之前生成的详细行程缓存

# ---- 渲染候选目的地 + 按需展开详细行程 ----
candidates = st.session_state.candidates
prefs = st.session_state.last_prefs

if not candidates:
    st.info("在左侧填好偏好，点击「开始规划」。首次运行会实际调用大模型 API 并联网搜索，请耐心等待。")
else:
    st.subheader("候选目的地排名")

    try:
        llm_for_detail = LLMClient(api_key=api_key, base_url=base_url, model=model)
        planner_for_detail = TripPlanner(llm_for_detail)
    except ValueError:
        llm_for_detail = None
        planner_for_detail = None

    for c in candidates:
        with st.container(border=True):
            title_col, badge_col = st.columns([5, 2])
            with title_col:
                st.markdown(f"### {c.city}（{c.province}）")
            with badge_col:
                hours_badge = f'<span class="train-badge">单程约{c.train_hours_estimate:.1f}h</span>' if c.train_hours_estimate else ""
                st.markdown(
                    f'<div style="text-align:right;padding-top:14px;">'
                    f'<span class="score-badge">综合得分 {c.total_score:.2f}</span>{hours_badge}</div>',
                    unsafe_allow_html=True,
                )

            st.write(c.reason)
            cols = st.columns(4)
            cols[0].metric("景色", f"{c.est_scenery_score:.2f}")
            cols[1].metric("美食", f"{c.est_food_score:.2f}")
            cols[2].metric("游玩强度", f"{c.est_walk_intensity:.2f}")
            cols[3].metric("独特性", f"{c.est_uniqueness_score:.2f}")

            already_built = c.city in st.session_state.itineraries
            btn_label = "重新生成详细行程" if already_built else "生成详细行程"

            itinerary_cache_age = None
            if planner_for_detail is not None and not already_built:
                try:
                    itinerary_cache_age = planner_for_detail.itinerary_cache_age_seconds(prefs, c.city)
                except Exception:  # noqa: BLE001 - 提示性查询，失败就不显示
                    itinerary_cache_age = None
            if itinerary_cache_age is not None:
                st.caption(f"命中缓存，生成于 {_format_age(itinerary_cache_age)}前，点击按钮会直接秒开。")

            btn_col, refresh_col = st.columns([2, 3])
            with btn_col:
                gen_clicked = st.button(btn_label, key=f"gen_{c.city}", use_container_width=True)
            with refresh_col:
                force_refresh_itinerary = st.checkbox(
                    "忽略缓存重新查询",
                    key=f"force_{c.city}",
                    help="行程缓存 24 小时内有效；勾选后重新联网核实，不使用之前缓存的结果。",
                )

            if gen_clicked:
                if planner_for_detail is None:
                    st.error("没有可用的 API Key。")
                else:
                    try:
                        with st.status(f"正在为「{c.city}」生成详细行程…", expanded=True) as status:
                            itinerary = planner_for_detail.get_itinerary(
                                prefs,
                                c.city,
                                on_progress=lambda msg: status.write(f"✅ {msg}"),
                                force_refresh=force_refresh_itinerary,
                            )
                            status.update(label="详细行程生成完成", state="complete")
                        st.session_state.itineraries[c.city] = itinerary
                    except Exception as e:  # noqa: BLE001
                        _show_friendly_error(f"「{c.city}」详细行程生成失败", e)

            if c.city in st.session_state.itineraries:
                itinerary = st.session_state.itineraries[c.city]
                itinerary_html = render_itinerary_html(itinerary, departure_city=prefs.departure_city, candidate=c)

                components.html(itinerary_html, height=2400, scrolling=True)

                dl_col1, dl_col2 = st.columns(2)
                with dl_col1:
                    st.download_button(
                        "下载 HTML",
                        data=itinerary_html.encode("utf-8"),
                        file_name=f"{c.city}_行程.html",
                        mime="text/html",
                        key=f"dl_html_{c.city}",
                        use_container_width=True,
                    )
                with dl_col2:
                    try:
                        pdf_bytes = html_to_pdf_bytes(itinerary_html)
                        st.download_button(
                            "下载 PDF",
                            data=pdf_bytes,
                            file_name=f"{c.city}_行程.pdf",
                            mime="application/pdf",
                            key=f"dl_pdf_{c.city}",
                            use_container_width=True,
                        )
                    except Exception as e:  # noqa: BLE001 - PDF 生成失败不该挡住 HTML 下载
                        st.caption(f"PDF 生成失败（{e}），可以先下载 HTML，用浏览器「打印为 PDF」代替。")
