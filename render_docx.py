#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_docx.py — 将结构化 JSON 渲染为 Word 文档 (.docx)

用法:
    python render_docx.py input.json [-o output.docx]
"""

import json
import sys
import os

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn


# ── 默认样式常量 ──────────────────────────────────────────────────
FONT_NAME = "宋体"
TITLE_SIZE = Pt(18)
H1_SIZE = Pt(16)
H2_SIZE = Pt(14)
BODY_SIZE = Pt(12)
TABLE_FONT_SIZE = Pt(10)


def render_docx(data: dict, output_path: str):
    """将 JSON 渲染为 .docx 文件。"""
    doc = Document()
    _setup_styles(doc)
    meta = data.get("meta", {})

    # ── 封面 ──
    _add_cover(doc, meta)

    # ── 正文：每张幻灯片 → 一个章节 ──
    for slide in data.get("slides", []):
        idx = slide["index"]

        # Section 标题
        title = _slide_title(slide)
        doc.add_heading(title or f"幻灯片 {idx}", level=1)

        shapes = sorted(
            slide.get("shapes", []),
            key=lambda s: (s.get("_top", 999), s.get("_left", 0)),
        )

        for shape in shapes:
            _render_shape(doc, shape)

        # 备注
        notes = slide.get("notes")
        if notes:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(1)
            run = p.add_run(f"[备注] {notes}")
            run.font.size = Pt(9)
            run.font.italic = True
            run.font.color.rgb = RGBColor(128, 128, 128)

        doc.add_page_break()

    doc.save(output_path)


# ── 封面 ──────────────────────────────────────────────────────────

def _add_cover(doc, meta):
    """生成封面的文档结构。"""
    for _ in range(6):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(meta.get("source", "Untitled"))
    run.font.size = Pt(26)
    run.bold = True

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"{meta.get('slide_count', '?')} 张幻灯片 · 提取时间: {meta.get('extracted_at', '-')}")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(128, 128, 128)

    doc.add_page_break()


# ── Shape 渲染调度 ────────────────────────────────────────────────

def _render_shape(doc, shape: dict):
    stype = shape.get("type", "text")

    if stype == "text":
        _render_text(doc, shape)
    elif stype == "table":
        _render_table(doc, shape)
    elif stype == "group":
        for child in shape.get("children", []):
            _render_shape(doc, child)


def _render_text(doc, shape: dict):
    content = shape.get("content", "").strip()
    if not content:
        return

    spans = shape.get("spans", [])
    if spans:
        p = doc.add_paragraph()
        _set_paragraph_spacing(p)
        for s in spans:
            run = p.add_run(s["text"])
            run.font.size = BODY_SIZE
            _set_font(run)
            if s.get("bold"):
                run.bold = True
            if s.get("italic"):
                run.italic = True
            if s.get("size"):
                run.font.size = Pt(s["size"])
    else:
        # 无格式文本：按换行分段
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            p = doc.add_paragraph()
            _set_paragraph_spacing(p)
            run = p.add_run(line)
            run.font.size = BODY_SIZE
            _set_font(run)


def _render_table(doc, shape: dict):
    headers = shape.get("headers", [])
    rows = shape.get("rows", [])
    if not headers and not rows:
        return

    n_cols = len(headers) if headers else max(len(r) for r in rows) if rows else 1
    n_rows = (1 if headers else 0) + len(rows)

    table = doc.add_table(rows=n_rows, cols=n_cols, style="Table Grid")

    # Header row
    row_idx = 0
    if headers:
        for col, h in enumerate(headers):
            cell = table.cell(row_idx, col)
            cell.text = h
            for p in cell.paragraphs:
                for run in p.runs:
                    run.bold = True
                    run.font.size = TABLE_FONT_SIZE
                    _set_font(run)
        row_idx = 1

    # Data rows
    for r, row_data in enumerate(rows):
        for col, val in enumerate(row_data):
            if col < n_cols:
                cell = table.cell(row_idx + r, col)
                cell.text = str(val)
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.font.size = TABLE_FONT_SIZE
                        _set_font(run)

    doc.add_paragraph()  # 表格后空一行


# ── 辅助 ──────────────────────────────────────────────────────────

def _setup_styles(doc):
    """设置默认文档样式。"""
    style = doc.styles["Normal"]
    style.font.name = FONT_NAME
    style.font.size = BODY_SIZE
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)

    # 页边距
    for section in doc.sections:
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(2.54)
        section.right_margin = Cm(2.54)


def _set_font(run):
    """设置 run 的中日韩字体。"""
    run.font.name = FONT_NAME
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)


def _set_paragraph_spacing(p):
    """段落间距：段前段后归零，1.5倍行距。"""
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.5


def _slide_title(slide: dict) -> str | None:
    """从幻灯片中提取简短标题。"""
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


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JSON -> Word (.docx) 渲染工具")
    parser.add_argument("input", help="输入 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 .docx 文件路径（默认: input.docx）")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"错误: 文件不存在 — {args.input}")

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    output_path = args.output or os.path.splitext(args.input)[0] + ".docx"
    render_docx(data, output_path)

    print(f"[OK] 已渲染 -> {output_path}")
