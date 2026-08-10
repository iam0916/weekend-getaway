"""HTML 渲染：字符集声明、来源标注、未核实价格的警示样式、常驻免责声明。"""
from __future__ import annotations

from weekendgo.domain.models import DayPlan, DayStep, FoodSpot, Hotel, Itinerary, TrainOption
from weekendgo.render.html_renderer import render_itinerary_html


def _sample_itinerary(hotel: Hotel | None = None) -> Itinerary:
    return Itinerary(
        destination="桂林",
        train_out=TrainOption(
            train_no="G1",
            dep_station="长沙",
            arr_station="桂林",
            duration="3h",
            source_title="12306官方时刻表",
            source_url="https://www.12306.cn/x",
            source_date_hint="2026年",
        ),
        train_back=TrainOption(),
        hotel=hotel or Hotel(name="测试酒店", address="测试地址", price_range="650元/晚"),
        days=[DayPlan(label="Day 1", steps=[DayStep(time="10:00", action="抵达")])],
        food_spots=[FoodSpot(name="测试餐厅", dish="测试菜", category="午餐")],
        uniqueness_verdict="测试判断",
        walk_intensity_score=0.3,
        caveats="地址、酒店、餐厅信息基于网络搜索生成，可能已停业、改名或搬迁，出发前请自行核实最新状态。",
    )


def test_renders_with_charset_declaration():
    # 早期版本没带 <meta charset>，本地文件直接打开会因为编码猜测错误显示乱码，踩过这个坑。
    html_out = render_itinerary_html(_sample_itinerary())
    assert html_out.startswith('<meta charset="UTF-8">')


def test_content_present():
    html_out = render_itinerary_html(_sample_itinerary())
    assert "测试酒店" in html_out
    assert "测试餐厅" in html_out


def test_unverified_price_gets_warning_style_not_confident_gold():
    hotel = Hotel(name="未核实酒店", address="x", price_range="未查到价格")
    hotel.price_verified = False
    html_out = render_itinerary_html(_sample_itinerary(hotel=hotel))
    assert "unverified" in html_out
    assert "未核实" in html_out


def test_source_citation_rendered_as_clickable_link():
    html_out = render_itinerary_html(_sample_itinerary())
    assert "https://www.12306.cn/x" in html_out
    assert "12306官方时刻表" in html_out
    assert "2026年" in html_out


def test_staleness_disclaimer_always_present_near_hotel_and_food():
    html_out = render_itinerary_html(_sample_itinerary())
    assert "酒店信息可能已过时" in html_out
    assert "餐厅信息可能已过时" in html_out


def test_css_never_uses_italic_on_chinese_text():
    """真报过的 bug：中文行程 PDF 在苹果自家的 PDF 查看器（macOS 预览、
    iPhone 自带查看器）里打开显示乱码，根因是 CSS 里给中文文字用了
    font-style: italic——中文字体没有真正的斜体字形，逼着渲染引擎伪造一个
    倾斜变体，PDF 导出时会多生成一个不规范的字体子集，苹果的 PDFKit 解析
    这种字体子集比其他阅读器更严格，就显示乱码了。这条规则不是针对某一处
    样式，而是整体约束：这个页面全是中文内容，CSS 里就不该出现 italic。
    """
    from weekendgo.render.html_renderer import _BASE_CSS

    assert "font-style" not in _BASE_CSS


def test_css_never_uses_pingfang_sc():
    """真报过的 bug（第二次排查才找到根因）：中文行程 PDF 在苹果自家的 PDF 查看器
    （macOS 预览、iPhone 自带查看器）里打开显示乱码/字符消失，用 qlmanage（跟
    预览/iPhone 同一套 PDFKit 渲染引擎）在本机复现后，逐个字体反复测试才定位到：
    问题精确锁定在 "PingFang SC" 这一个字体文件本身——排大量不重复汉字时，
    weasyprint 生成的字体子集会被 PDFKit 解析错，换成 "Heiti SC" 或 "Songti SC"
    排同样的内容完全正常。这条规则约束的是这一个具体字体，不是笼统的字体格式。
    """
    from weekendgo.render.html_renderer import _BASE_CSS

    assert "PingFang" not in _BASE_CSS
