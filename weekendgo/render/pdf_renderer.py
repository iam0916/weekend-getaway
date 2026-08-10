"""把渲染好的 HTML 转成 PDF 字节流，用于 Streamlit 的下载按钮。

用 weasyprint 而不是指望用户自己拿浏览器"打印为 PDF"——weasyprint 是纯 Python
方案（依赖 pango/cairo 这些系统库，通过 Homebrew 装），不需要装 Chrome/Node
这类重量级无头浏览器，能直接在应用内生成，用户点一下下载按钮就能拿到 PDF。

真报过的 bug：中文行程 PDF 在 macOS 预览、iPhone 自带的 PDF 查看器（都是苹果自家
PDFKit/CoreGraphics 渲染栈）里打开显示乱码/错位/字符消失，但用其他渲染器（Chrome、
pymupdf）打开、或者直接提取文本，内容完全正常。排查走了两轮弯路才定位到真正原因：

1. 一度怀疑是字体子集化引擎的问题（怀疑退回到了不够健壮的 fonttools 兜底路径），
   实测发现系统本来就有 libharfbuzz-subset 可用、weasyprint 一直在用推荐的那条
   路径，这个猜测被排除了。
2. 又怀疑是 `.stale-hint` 用了 `font-style: italic`（中文字体没有真正的斜体字形，
   会逼渲染引擎伪造一个倾斜变体）——去掉之后乱码依旧，说明这最多是次要因素。

后来用 `qlmanage -t`（macOS 自带 Quick Look 缩略图工具，跟预览/iPhone 用的是
同一套 PDFKit 渲染栈）把 PDF 在本机就能复现出乱码，不用每次都靠用户在真机上
测——才终于能快速试出真正的根因：用一段远超 256 个不重复汉字的纯文本压力测试，
换不同中文字体分别渲染对比，发现问题精确锁定在 "PingFang SC" 这一个字体文件
本身（用 fc-list 能看到它是 CFF/PostScript 轮廓字体）——用它排版大量不重复汉字时，
weasyprint 生成的字体子集会被苹果自家的 PDFKit 解析错，换成 "Songti SC"（同样是
CFF）或 "Heiti SC"（TrueType 轮廓字体）排同样的内容，乱码完全消失。目前看是
"PingFang SC" 这个具体字体文件的子集化兼容性问题，不是字体格式（CFF vs
TrueType）本身的通病。解决办法：html_renderer.py 的正文字体从 "PingFang SC"
换成 "Heiti SC"，避开这个具体字体。
"""
from __future__ import annotations

from weasyprint import HTML


def html_to_pdf_bytes(html_content: str) -> bytes:
    return HTML(string=html_content).write_pdf()
