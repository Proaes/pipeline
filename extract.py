#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract.py — 从 PPTX 提取内容为结构化 JSON

用法:
    python extract.py input.pptx [-o output.json]

输出 JSON 结构:
    {
      "meta": { "source": "...", "slide_count": N, "extracted_at": "..." },
      "slides": [
        {
          "index": 1,
          "shapes": [ ... ],
          "notes": "..."
        }
      ]
    }

每个 shape:
    { "type": "text", "content": "...", "bold": true, "font_size": 14, ... }
    { "type": "table", "headers": [...], "rows": [[...], ...] }
    { "type": "list", "items": ["...", "..."] }
"""

import json
import sys
import os
from datetime import datetime
from typing import Any


def extract_pptx(pptx_path: str) -> dict:
    """从 PPTX 文件中提取所有幻灯片内容，返回结构化 dict。"""
    try:
        from pptx import Presentation
    except ImportError:
        sys.exit("错误: 需要安装 python-pptx。运行: pip install python-pptx")

    prs = Presentation(pptx_path)
    slides_data = []

    for i, slide in enumerate(prs.slides, 1):
        slide_entry: dict[str, Any] = {"index": i, "shapes": []}

        for shape in slide.shapes:
            extracted = _extract_shape(shape)
            if extracted:
                slide_entry["shapes"].append(extracted)

        # 备注
        try:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                slide_entry["notes"] = notes
        except Exception:
            pass

        slides_data.append(slide_entry)

    return {
        "meta": {
            "source": os.path.basename(pptx_path),
            "slide_count": len(slides_data),
            "extracted_at": datetime.now().isoformat(timespec="seconds"),
        },
        "slides": slides_data,
    }


def _extract_shape(shape) -> dict | None:
    """提取单个 shape 的内容。"""

    # --- 表格 ---
    if shape.has_table:
        return _extract_table(shape.table)

    # --- 组合 ---
    if shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
        children = []
        for child in shape.shapes:
            child_data = _extract_shape(child)
            if child_data:
                children.append(child_data)
        if children:
            return {"type": "group", "children": children}
        return None

    # --- 文本框 / 占位符 ---
    if shape.has_text_frame:
        text_data = _extract_text_frame(shape.text_frame)
        if text_data and text_data.get("content", "").strip():
            # 附加位置信息用于排序
            text_data["_top"] = round(shape.top / 914400, 2)  # EMU → cm
            text_data["_left"] = round(shape.left / 914400, 2)
            return text_data
        return None

    # 图片、图形等其他 shape 跳过
    return None


def _get_font_color(font) -> str | None:
    """安全获取字体颜色，兼容 scheme color 等无 rgb 的类型。"""
    try:
        if font.color and font.color.rgb:
            return str(font.color.rgb)
    except AttributeError:
        pass
    return None


def _extract_text_frame(tf) -> dict | None:
    """提取文本框内容，保留格式。"""
    paragraphs = []
    for para in tf.paragraphs:
        runs_data = []
        for run in para.runs:
            if run.text.strip():
                runs_data.append(
                    {
                        "text": run.text,
                        "bold": run.font.bold,
                        "italic": run.font.italic,
                        "size": (
                            round(run.font.size / 12700, 1)
                            if run.font.size
                            else None
                        ),
                        "color": _get_font_color(run.font),
                    }
                )
        if runs_data:
            paragraphs.append(
                {
                    "runs": runs_data,
                    "alignment": (
                        str(para.alignment)
                        if para.alignment
                        else None
                    ),
                }
            )

    if not paragraphs:
        # 无格式的纯文本（无 run 的情况）
        raw = tf.text.strip()
        if raw:
            return {"type": "text", "content": raw}
        return None

    # 平坦化为 content + spans
    all_text = []
    spans = []
    for p in paragraphs:
        for r in p["runs"]:
            all_text.append(r["text"])
            span = {"text": r["text"]}
            if r["bold"]:
                span["bold"] = True
            if r["italic"]:
                span["italic"] = True
            if r["size"]:
                span["size"] = r["size"]
            spans.append(span)

    return {
        "type": "text",
        "content": "".join(all_text),
        "spans": spans if any("bold" in s or "italic" in s or "size" in s for s in spans) else [],
    }


def _extract_table(table) -> dict:
    """提取表格，首行作为 header。"""
    rows = []
    for row in table.rows:
        rows.append([cell.text.strip() for cell in row.cells])

    if not rows:
        return {"type": "table", "headers": [], "rows": []}

    # 如果第一行全都不为空，当作 header
    headers = rows[0]
    data_rows = rows[1:]
    return {"type": "table", "headers": headers, "rows": data_rows}


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PPTX → JSON 提取工具")
    parser.add_argument("input", help="输入 PPTX 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 JSON 文件路径（默认: input.json）")
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进（默认 2，设为 0 压缩）")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"错误: 文件不存在 — {args.input}")

    data = extract_pptx(args.input)

    output_path = args.output or os.path.splitext(args.input)[0] + ".json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=args.indent or None)

    print(f"[OK] 已提取 {data['meta']['slide_count']} 张幻灯片 -> {output_path}")
