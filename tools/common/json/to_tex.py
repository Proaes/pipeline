#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
to_tex.py — JSON → LaTeX 渲染（统一入口，自动识别 JSON schema）

两种 JSON 结构:
  1) slides 结构（PPT 流程）: common/pptx/to_json.py 输出 → article / beamer 文档
  2) blocks 结构（笔记流程）: common/md/to_json.py 输出 → ctexart 文档

用法:
    # PPT（原用法，兼容）
    python to_tex.py slides.json -o slides.tex --mode article|beamer

    # 笔记（blocks 结构）
    python to_tex.py note.json -o note.tex [--title 微积分] [--no-toc] [--body]

容错:
    - 数学中的空 array 列格式（\\begin{array}{}）会按行内容自动补全列数并打印警告，
      JSON 中间态本身不被修改（兜底只发生在渲染层）。
    - 文本段的 LaTeX 特殊字符统一转义（含**反斜杠**：正文里的 `D:\\tools\\x`、`'\\0'`
      会变成 `\\textbackslash{}`，否则进 .tex 就是未定义控制序列、编译中止）；
      行内代码（反引号内）先切出来、只转义一次，避免 `snake_case` 变成 `snake\\{}_case`。
"""

import argparse
import json
import re
import sys
import os

# 统一 UTF-8 输出（管道/文件重定向时避免依赖控制台代码页）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# ── LaTeX 特殊字符转义（仅对非数学文本段生效）───────────────────
# 反斜杠必须一起转义：正文里的 Windows 路径（D:\tools\x）、C 代码里的 '\0'、'\t'
# 若原样进 .tex 会变成未定义控制序列，latexmk 带 -halt-on-error 直接中止、不出 PDF。
_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\^{}",
}
_ESCAPE_RE = re.compile(r"[\\&%$#_{}~^]")

# 行内数学：先匹配 $$...$$（同行的显示数学），再匹配 $...$（可跨行，段落已拼接为单行）
_INLINE_MATH_RE = re.compile(r"(\$\$[^$\n]*\$\$|\$[^$\n]*\$)")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*([^*\n]+)\*")

# 空 array 列格式（容错）
_ARRAY_EMPTY_RE = re.compile(r"\\begin\s*\{array\}\s*\{\s*\}")
# 数学段内的中文（含全角标点）：包进 \text{} 避免数学字体缺字（pdflatex/xelatex 通用）
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff\u3001-\u303f\uff01-\uff5e]+")

_HEADING_CMD = {
    1: "section",
    2: "subsection",
    3: "subsubsection",
    4: "paragraph",
    5: "subparagraph",
    6: "subparagraph",
}


def _escape(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group()], text)


def _url_escape(url: str) -> str:
    return url.replace("%", r"\%").replace("#", r"\#").replace("_", r"\_")


def _fix_empty_array(math_text: str) -> str:
    """自动补全 \\begin{array}{} 空列格式，按 array 体内 & 数推断列数。"""
    def _sub(m: re.Match) -> str:
        rest = math_text[m.end():]
        end = rest.find(r"\end{array}")
        body = rest[:end] if end != -1 else ""
        ncols = 1
        for seg in body.split("\\\\"):
            ncols = max(ncols, seg.count("&") + 1)
        print(
            f"[WARN] 空 array 自动补全列格式 -> \\begin{{array}}{{{'c' * ncols}}}",
            file=sys.stderr,
        )
        return "\\begin{array}{" + "c" * ncols + "}"

    return _ARRAY_EMPTY_RE.sub(_sub, math_text)


def _wrap_cjk(math_text: str) -> str:
    """数学段内的中文（含全角标点）用 \\text{} 包裹，避免数学字体缺字。"""
    return _CJK_RUN_RE.sub(lambda m: r"\text{" + m.group(0) + "}", math_text)


def _fix_math(math_text: str) -> str:
    """数学段统一兜底：空 array 补列 → 中文包 \\text{}。"""
    return _wrap_cjk(_fix_empty_array(math_text))


_TAG_RE = re.compile(r"\\tag\s*\{")


def _emit_display(source: str) -> str:
    """显示数学块输出：含 \\tag 用 equation 环境（\\tag 只在 amsmath 环境内合法），否则 \\[ ... \\]。"""
    if _TAG_RE.search(source):
        return "\\begin{equation}\n" + source + "\n\\end{equation}"
    return "\\[\n" + source + "\n\\]"


def _code_tex(code: str) -> str:
    """行内代码 → \\texttt{}：代码段内容只在这里转义一次。"""
    code = code.replace("\\", r"\textbackslash{}")
    code = code.replace("{", r"\{").replace("}", r"\}")
    code = code.replace("$", r"\$").replace("%", r"\%").replace("#", r"\#")
    code = code.replace("_", r"\_").replace("^", r"\^{}").replace("~", r"\textasciitilde{}")
    code = code.replace("&", r"\&")
    return r"\texttt{" + code + "}"


def _inline_text(text: str) -> str:
    """非数学文本段的行内转换：切出行内代码 → 其余段转义 → 图片/链接/粗斜体 → <br>。

    行内代码必须**先切出来**再转义：若先对整行 `_escape`（`_` → `\\_`），随后 `_code_tex`
    又把那个反斜杠当成代码内容转义一次，`snake_case` 就会渲染成 `snake\\{}_case` 这类垃圾字
    —— 编译不报错，只是内容悄悄坏掉。
    """
    out = []
    for idx, part in enumerate(_CODE_RE.split(text)):
        if idx % 2:  # 奇数段 = 反引号里的代码
            out.append(_code_tex(part))
            continue
        part = _escape(part)
        part = _IMG_RE.sub(lambda m: r"\includegraphics[width=0.8\linewidth]{" + m.group(2).strip() + "}", part)
        part = _LINK_RE.sub(lambda m: r"\href{" + _url_escape(m.group(2)) + "}{" + m.group(1) + "}", part)
        part = _BOLD_RE.sub(r"\textbf{\1}", part)
        part = _ITALIC_RE.sub(r"\textit{\1}", part)
        out.append(part)
    text = "".join(out)
    return text.replace("<br>", r"\\").replace("<br/>", r"\\")


def _inline_pass(text: str) -> str:
    """行内转换；$...$ 数学段先做空 array 兜底后原样保留。"""
    parts = _INLINE_MATH_RE.split(text)
    out = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            out.append(_fix_math(part))
        else:
            out.append(_inline_text(part))
    return "".join(out)


# ════════════════════════════════════════════════════════════
# blocks 结构（笔记流程）
# ════════════════════════════════════════════════════════════

_BLOCK_PREAMBLE = r"""\documentclass[12pt,a4paper]{ctexart}
\usepackage{amsmath,amssymb,amsthm}
\usepackage{booktabs,array,longtable}
\usepackage{geometry}
\geometry{left=2.5cm,right=2.5cm,top=2.5cm,bottom=2.5cm}
\usepackage{graphicx}
\usepackage{hyperref}
\hypersetup{colorlinks=true,linkcolor=blue,filecolor=magenta,urlcolor=cyan}

"""


def _render_table_block(b: dict) -> str:
    aligns = b.get("align") or ["c"] * len(b.get("header", []))
    out = [
        r"\begin{center}",
        r"\begin{tabular}{" + "".join(aligns) + "}",
        r"\toprule",
    ]
    out.append(" & ".join(_inline_pass(c) for c in b.get("header", [])) + r" \\")
    out.append(r"\midrule")
    for row in b.get("rows", []):
        out.append(" & ".join(_inline_pass(c) for c in row) + r" \\")
    out.append(r"\bottomrule")
    out.append(r"\end{tabular}")
    out.append(r"\end{center}")
    return "\n".join(out)


def _render_blocks_body(blocks: list[dict]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "heading":
            cmd = _HEADING_CMD.get(b.get("level", 3), "subparagraph")
            out.append("\\" + cmd + "{" + _inline_pass(b.get("text", "")) + "}")
        elif t == "paragraph":
            out.append(_inline_pass(b.get("text", "")))
        elif t == "math":
            out.append(_emit_display(_fix_math(b.get("source", ""))))
        elif t == "table":
            out.append(_render_table_block(b))
        elif t == "quote":
            text = b.get("text", "").replace("\n", "<br>")
            out.append("\\begin{quote}")
            out.append(_inline_pass(text))
            out.append("\\end{quote}")
        elif t == "list":
            env = "enumerate" if b.get("ordered") else "itemize"
            out.append("\\begin{" + env + "}")
            for item in b.get("items", []):
                out.append("  \\item " + _inline_pass(item))
            out.append("\\end{" + env + "}")
        elif t == "hr":
            out.append(r"\begin{center}\rule{0.5\linewidth}{0.4pt}\end{center}")
        elif t == "code":
            out.append("\\begin{verbatim}")
            out.append(b.get("text", ""))
            out.append("\\end{verbatim}")
        out.append("")
    return out


def _render_blocks(data: dict, title: str, author: str, toc: bool, body_only: bool) -> str:
    body = "\n".join(_render_blocks_body(data.get("blocks", [])))
    if body_only:
        return body
    meta = data.get("meta", {})
    if not title:
        title = meta.get("title", "Untitled")
    lines = [
        _BLOCK_PREAMBLE,
        r"\title{" + _escape(title) + "}",
        r"\author{" + _escape(author) + "}",
        r"\date{\today}",
        "",
        r"\begin{document}",
        r"\maketitle",
    ]
    if toc:
        lines.append(r"\tableofcontents")
    lines += [r"\newpage", "", body, "", r"\end{document}"]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# slides 结构（PPT 流程，兼容原实现）
# ════════════════════════════════════════════════════════════


def _render_pptx(data: dict, mode: str) -> str:
    meta = data.get("meta", {})
    if mode == "beamer":
        return _render_pptx_beamer(data, meta)
    return _render_pptx_article(data, meta)


def _render_pptx_article(data: dict, meta: dict) -> str:
    lines = []
    lines.append(r"\documentclass[12pt,a4paper]{ctexart}")
    lines.append(r"\usepackage{geometry}")
    lines.append(r"\geometry{left=2.54cm,right=2.54cm,top=2.54cm,bottom=2.54cm}")
    lines.append(r"\usepackage{longtable,array,hyperref}")
    lines.append(r"\usepackage{graphicx}")
    lines.append(r"\title{" + _escape(meta.get("source", "Untitled")) + "}")
    lines.append(r"\date{\today}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\maketitle")
    lines.append(r"\tableofcontents")
    lines.append(r"\newpage")
    lines.append("")

    for slide in data.get("slides", []):
        title = _extract_title(slide)
        lines.append(r"\section{" + _escape(title or f"幻灯片 {slide['index']}") + "}")
        lines.append("")
        shapes = sorted(
            slide.get("shapes", []),
            key=lambda s: (s.get("_top", 999), s.get("_left", 0)),
        )
        for shape in shapes:
            rendered = _render_shape_tex(shape)
            if rendered:
                lines.append(rendered)
                lines.append("")
        if slide.get("notes"):
            lines.append(r"\begin{quote}\textbf{备注:} " + _escape(slide["notes"]) + r"\end{quote}")
            lines.append("")
        lines.append(r"\newpage")
        lines.append("")

    lines.append(r"\end{document}")
    return "\n".join(lines)


def _render_pptx_beamer(data: dict, meta: dict) -> str:
    lines = []
    lines.append(r"\documentclass[12pt]{ctexbeamer}")
    lines.append(r"\usetheme{Madrid}")
    lines.append(r"\title{" + _escape(meta.get("source", "Untitled")) + "}")
    lines.append(r"\date{\today}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\begin{frame}")
    lines.append(r"\titlepage")
    lines.append(r"\end{frame}")
    lines.append("")

    for slide in data.get("slides", []):
        title = _extract_title(slide)
        lines.append(r"\begin{frame}{" + _escape(title or f"幻灯片 {slide['index']}") + "}")
        lines.append("")
        shapes = sorted(
            slide.get("shapes", []),
            key=lambda s: (s.get("_top", 999), s.get("_left", 0)),
        )
        for shape in shapes:
            rendered = _render_shape_tex(shape)
            if rendered:
                lines.append(rendered)
                lines.append("")
        if slide.get("notes"):
            lines.append(r"\note{" + _escape(slide["notes"]) + "}")
        lines.append(r"\end{frame}")
        lines.append("")

    lines.append(r"\end{document}")
    return "\n".join(lines)


def _extract_title(slide: dict) -> str | None:
    shapes = sorted(
        slide.get("shapes", []),
        key=lambda s: (s.get("_top", 999), s.get("_left", 0)),
    )
    for shape in shapes:
        if shape.get("type") == "text":
            content = shape.get("content", "").strip()
            if content and len(content) <= 60:
                return content
    for shape in shapes:
        if shape.get("type") == "text":
            content = shape.get("content", "").strip()
            if content:
                return content[:60] + ("..." if len(content) > 60 else "")
    return None


def _render_shape_tex(shape: dict) -> str:
    stype = shape.get("type", "text")
    if stype == "text":
        return _render_text_tex(shape)
    elif stype == "table":
        return _render_pptx_table_tex(shape)
    elif stype == "group":
        return "\n\n".join(r for c in shape.get("children", []) if (r := _render_shape_tex(c)))
    return ""


def _render_text_tex(shape: dict) -> str:
    content = shape.get("content", "").strip()
    if not content:
        return ""
    spans = shape.get("spans", [])
    if spans:
        result = []
        for s in spans:
            text = _escape(s["text"])
            if s.get("bold"):
                text = r"\textbf{" + text + "}"
            if s.get("italic"):
                text = r"\textit{" + text + "}"
            result.append(text)
        return "".join(result)

    lines = content.split("\n")
    if len(lines) > 1 and all(
        l.strip().startswith(("•", "·", "-", "1.", "2.", "3.")) for l in lines if l.strip()
    ):
        env = "itemize" if any(l.strip()[:1] in ("•", "·", "-") for l in lines if l.strip()) else "enumerate"
        items = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                for prefix in ("• ", "· ", "- ", "1. ", "2. ", "3. ", "4. ", "5. "):
                    if stripped.startswith(prefix):
                        stripped = stripped[len(prefix):]
                        break
                items.append(r"  \item " + _escape(stripped))
        if items:
            return r"\begin{" + env + "}\n" + "\n".join(items) + "\n" + r"\end{" + env + "}"

    return _escape(content)


def _render_pptx_table_tex(shape: dict) -> str:
    headers = shape.get("headers", [])
    rows = shape.get("rows", [])
    if not headers and not rows:
        return ""
    n_cols = len(headers) if headers else max(len(r) for r in rows) if rows else 1

    lines = []
    lines.append(r"\begin{center}")
    lines.append(r"\begin{tabular}{|" + "|".join(["c"] * n_cols) + "|}")
    lines.append(r"\hline")
    if headers:
        lines.append(" & ".join(_escape(h) for h in headers) + r" \\ \hline")
    for row in rows:
        cells = [_escape(str(c)) for c in row]
        while len(cells) < n_cols:
            cells.append("")
        lines.append(" & ".join(cells[:n_cols]) + r" \\ \hline")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{center}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# 统一入口
# ════════════════════════════════════════════════════════════

def render_tex(
    data: dict,
    mode: str = "article",
    title: str = "",
    author: str = "",
    toc: bool = True,
    body_only: bool = False,
) -> str:
    """按 JSON 结构自动分派：slides → PPT 渲染；blocks → 笔记渲染。"""
    if "slides" in data:
        return _render_pptx(data, mode)
    return _render_blocks(data, title, author, toc, body_only)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="JSON → LaTeX 渲染（自动识别 slides/blocks 结构）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
        "  python to_tex.py slides.json -o slides.tex --mode article\n"
        "  python to_tex.py note.json -o note.tex --title 微积分\n",
    )
    parser.add_argument("input", help="输入 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 .tex 路径（默认: input.tex）")
    parser.add_argument(
        "--mode",
        choices=["article", "beamer"],
        default="article",
        help="slides 结构的文档模式: article 或 beamer [默认: article]",
    )
    parser.add_argument("--title", default=None, help="blocks 结构的文档标题（默认: meta.title 或文件名）")
    parser.add_argument("--author", default="", help="blocks 结构的作者 [默认: 空]")
    parser.add_argument("--no-toc", action="store_true", help="blocks 结构不生成目录（默认生成）")
    parser.add_argument("--body", action="store_true", help="blocks 结构仅输出正文片段，不带导言区")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"错误: 文件不存在 — {args.input}")

    with open(args.input, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if "slides" in data:
        tex = render_tex(data, mode=args.mode)
        desc = f"{args.mode} 模式"
    else:
        tex = render_tex(
            data, title=args.title or "", author=args.author,
            toc=not args.no_toc, body_only=args.body,
        )
        desc = "blocks 模式" + ("（正文片段）" if args.body else "")

    output_path = args.output or os.path.splitext(args.input)[0] + ".tex"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(tex)

    print(f"[OK] 已渲染 ({desc}) -> {output_path}")
