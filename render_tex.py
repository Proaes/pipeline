#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_tex.py — 将结构化 JSON 渲染为 LaTeX (.tex)

用法:
    python render_tex.py input.json [-o output.tex] [--mode article|beamer]

输出可直接用 xelatex/lualatex 编译（需要 ctex 宏包支持中文）。
"""

import json
import sys
import os
import re

# ── LaTeX 特殊字符转义 ─────────────────────────────────────────────
_ESCAPE_MAP = {
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
_ESCAPE_RE = re.compile(r"[&%$#_{}~^]")


def _escape(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group()], text)


# ── 渲染 ──────────────────────────────────────────────────────────

def render_tex(data: dict, mode: str = "article") -> str:
    """渲染为完整 LaTeX 文档。"""
    meta = data.get("meta", {})

    if mode == "beamer":
        return _render_beamer(data, meta)
    else:
        return _render_article(data, meta)


def _render_article(data: dict, meta: dict) -> str:
    lines = []

    # Preamble
    lines.append(r"\documentclass[12pt,a4paper]{ctexart}")
    lines.append(r"\usepackage{geometry}")
    lines.append(r"\geometry{left=2.54cm,right=2.54cm,top=2.54cm,bottom=2.54cm}")
    lines.append(r"\usepackage{longtable,array,hyperref}")
    lines.append(r"\usepackage{graphicx}")
    lines.append(r"\title{" + _escape(meta.get("source", "Untitled")) + "}")
    lines.append(r"\date{\today}")
    lines.append(r"")
    lines.append(r"\begin{document}")
    lines.append(r"\maketitle")
    lines.append(r"\tableofcontents")
    lines.append(r"\newpage")
    lines.append("")

    # Slides → Sections
    for slide in data.get("slides", []):
        idx = slide["index"]
        # 以第一段短文本作为 section 标题
        title = _extract_title(slide)
        lines.append(r"\section{" + _escape(title or f"幻灯片 {idx}") + "}")
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


def _render_beamer(data: dict, meta: dict) -> str:
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
    """从幻灯片提取一个简短的标题文本。"""
    shapes = sorted(
        slide.get("shapes", []),
        key=lambda s: (s.get("_top", 999), s.get("_left", 0)),
    )
    for shape in shapes:
        if shape.get("type") == "text":
            content = shape.get("content", "").strip()
            if content and len(content) <= 60:
                return content
    # fallback: 第一个 text 的前60字
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
        return _render_table_tex(shape)
    elif stype == "group":
        children = shape.get("children", [])
        return "\n\n".join(_render_shape_tex(c) for c in children if _render_shape_tex(c))
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

    # 无格式：先检查是否为列表项
    lines = content.split("\n")
    if len(lines) > 1 and all(l.strip().startswith(("•", "·", "•", "-", "1.", "2.", "3.")) for l in lines if l.strip()):
        # 列表
        env = "itemize" if any(l.strip()[:1] in ("•", "·", "•", "-") for l in lines if l.strip()) else "enumerate"
        items = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                # 去掉列表标记
                for prefix in ("• ", "· ", "• ", "- ", "1. ", "2. ", "3. ", "4. ", "5. "):
                    if stripped.startswith(prefix):
                        stripped = stripped[len(prefix):]
                        break
                items.append(r"  \item " + _escape(stripped))
        if items:
            return r"\begin{" + env + "}\n" + "\n".join(items) + "\n" + r"\end{" + env + "}"

    return _escape(content)


def _render_table_tex(shape: dict) -> str:
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


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JSON → LaTeX 渲染工具")
    parser.add_argument("input", help="输入 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 .tex 文件路径（默认: input.tex）")
    parser.add_argument(
        "--mode",
        choices=["article", "beamer"],
        default="article",
        help="文档模式: article(论文/报告) 或 beamer(幻灯片) [默认: article]",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"错误: 文件不存在 — {args.input}")

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    tex = render_tex(data, mode=args.mode)

    output_path = args.output or os.path.splitext(args.input)[0] + ".tex"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(tex)

    print(f"[OK] 已渲染 ({args.mode} 模式) -> {output_path}")
