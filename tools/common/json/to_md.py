#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_md.py — 将结构化 JSON 渲染为 Markdown

用法:
    python render_md.py input.json [-o output.md]
"""

import json
import sys
import os


def render_md(data: dict) -> str:
    """将提取后的 JSON 数据渲染为 Markdown 文本。"""
    lines = []
    meta = data.get("meta", {})
    lines.append(f"# {meta.get('source', 'Untitled')}")
    lines.append(f"*{meta['slide_count']} 张幻灯片 - 提取时间: {meta.get('extracted_at', '-')}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    for slide in data.get("slides", []):
        idx = slide["index"]
        lines.append(f"## 幻灯片 {idx}")
        lines.append("")

        # 按从上到下排序
        shapes = sorted(slide.get("shapes", []), key=lambda s: (s.get("_top", 999), s.get("_left", 0)))

        for shape in shapes:
            rendered = _render_shape(shape)
            if rendered:
                lines.append(rendered)
                lines.append("")

        # 备注
        if slide.get("notes"):
            lines.append(f"> **备注:** {slide['notes']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _render_shape(shape: dict) -> str:
    stype = shape.get("type", "text")

    if stype == "text":
        return _render_text(shape)

    elif stype == "table":
        return _render_table(shape)

    elif stype == "group":
        children = shape.get("children", [])
        return "\n\n".join(_render_shape(c) for c in children if _render_shape(c))

    return ""


def _render_text(shape: dict) -> str:
    content = shape.get("content", "")
    if not content.strip():
        return ""

    # 如果有 spans，用 Markdown 内联格式
    spans = shape.get("spans", [])
    if spans:
        result = []
        for s in spans:
            text = s["text"]
            if s.get("bold"):
                text = f"**{text}**"
            if s.get("italic"):
                text = f"*{text}*"
            result.append(text)
        return "".join(result)

    return content


def _render_table(shape: dict) -> str:
    headers = shape.get("headers", [])
    rows = shape.get("rows", [])
    if not headers and not rows:
        return ""

    lines = []
    # Header
    if headers:
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    else:
        lines.append("|" + "|".join(["---"] * len(rows[0])) + "|")

    # Rows
    for row in rows:
        cols = [str(c).replace("|", "\\|").replace("\n", "<br>") for c in row]
        # 补齐列数
        expected = len(headers) if headers else len(rows[0])
        while len(cols) < expected:
            cols.append("")
        lines.append("| " + " | ".join(cols) + " |")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JSON → Markdown 渲染工具")
    parser.add_argument("input", help="输入 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 Markdown 文件路径（默认: input.md）")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"错误: 文件不存在 — {args.input}")

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    md_text = render_md(data)

    output_path = args.output or os.path.splitext(args.input)[0] + ".md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    print(f"[OK] 已渲染 -> {output_path}")
