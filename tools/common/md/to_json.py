#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
to_json.py — Markdown → JSON 结构化中间态

把带行内 LaTeX 数学公式的 Markdown 笔记解析为结构化 JSON（块级），
作为 md → tex / html 等渲染的统一中间态（对齐 pipeline 的 json 中心架构）。

块类型: heading / paragraph / math(display) / table / quote / list / hr / code
行内内容保持原始字符串（$...$ 数学段由各渲染出口再识别保护），仅块级结构显式化。

用法:
    python to_json.py input.md -o output.json    # md → JSON
    python to_json.py input.md --lint            # 转换并输出 md 规范化检查报告
    python to_json.py input.md --title "微积分"   # 指定标题（默认取第一个一级标题或文件名）

JSON 结构:
    {
      "meta": { "title": "...", "source": "..." },
      "blocks": [ { "type": "heading", "level": 1, "text": "..." }, ... ]
    }
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# 统一 UTF-8 输出（管道/文件重定向时避免依赖控制台代码页）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# ── 数学段保护 ────────────────────────────────────────────────
# 显示数学（可能跨行）先整体抽出为占位符，块解析后还原
_DISPLAY_MATH_RE = re.compile(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]")
_PLACEHOLDER_RE = re.compile(r"\x00M(\d+)\x00")


def _extract_display_math(md_text: str) -> tuple[str, list[str]]:
    math_blocks: list[str] = []

    def _pull(m: re.Match) -> str:
        idx = len(math_blocks)
        math_blocks.append(m.group(0))
        return f"\x00M{idx}\x00"

    return _DISPLAY_MATH_RE.sub(_pull, md_text), math_blocks


def _restore(text: str, math_blocks: list[str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: math_blocks[int(m.group(1))], text)


def _display_inner(raw: str) -> str:
    """去掉 $$...$$ 或 \\[...\\] 定界符，取内部内容。"""
    raw = raw.strip()
    if raw.startswith("$$") and raw.endswith("$$"):
        return raw[2:-2].strip()
    if raw.startswith(r"\[") and raw.endswith(r"\]"):
        return raw[2:-2].strip()
    return raw


# ── 块级判定辅助 ──────────────────────────────────────────────

def _is_table_sep(line: str) -> bool:
    """GFM 表格分隔行：含 -，且含 | 或为纯 :?---:? 形式。"""
    s = line.strip()
    if "-" not in s:
        return False
    if "|" in s:
        return bool(re.match(r"^\|?[\s:|-]+\|?$", s))
    return bool(re.match(r"^:?-{3,}:?$", s))


def _is_hr(line: str) -> bool:
    return bool(re.match(r"^\s*([-*_])\s*(?:\1\s*){2,}$", line))


def _is_pure_html(line: str) -> bool:
    """整行只由 HTML 标签和空白组成（含 <p>...</p> 等多标签行）。"""
    s = line.strip()
    return bool(re.match(r"^(?:</?[a-zA-Z][^>]*>|\s)+$", s))


def _split_cells(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [c.strip() for c in cells]


# ── 解析 ──────────────────────────────────────────────────────

def parse_md(md_text: str) -> dict:
    """Markdown → JSON 结构化中间态。"""
    text, math_blocks = _extract_display_math(md_text)
    lines = text.split("\n")
    n = len(lines)
    blocks: list[dict] = []
    i = 0

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # HTML 注释（可能跨行）
        if stripped.startswith("<!--"):
            if "-->" in stripped:
                i += 1
                continue
            j = i + 1
            while j < n and "-->" not in lines[j]:
                j += 1
            i = j + 1
            continue

        # <style> / <script> 整块跳过
        if re.match(r"^<(style|script)\b", stripped, re.IGNORECASE):
            j = i + 1
            while j < n and not re.match(
                r"^</(style|script)\s*>", lines[j].strip(), re.IGNORECASE
            ):
                j += 1
            i = j + 1
            continue

        # 纯 HTML 标签行跳过
        if _is_pure_html(line):
            i += 1
            continue

        # 围栏代码块
        if stripped.startswith("```"):
            j = i + 1
            buf = []
            while j < n and not lines[j].strip().startswith("```"):
                buf.append(lines[j])
                j += 1
            blocks.append({"type": "code", "text": "\n".join(buf)})
            i = j + 1
            continue

        # 表格：当前行含 | 且下一行是分隔行
        if "|" in line and i + 1 < n and _is_table_sep(lines[i + 1]):
            j = i
            block = [lines[j]]
            while j + 1 < n and ("|" in lines[j + 1] or _is_table_sep(lines[j + 1])):
                j += 1
                block.append(lines[j])
            blocks.append(_parse_table_block(block, math_blocks))
            i = j + 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            blocks.append({
                "type": "heading",
                "level": len(m.group(1)),
                "text": _restore(m.group(2).strip(), math_blocks),
            })
            i += 1
            continue

        # 分隔线
        if _is_hr(stripped):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # 引用块
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append({
                "type": "quote",
                "text": _restore("\n".join(buf), math_blocks),
            })
            continue

        # 列表
        m1 = re.match(r"^\s*[-*+]\s+(.*)$", line)
        m2 = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m1 or m2:
            ordered = bool(m2)
            items: list[str] = []
            while i < n:
                a = re.match(r"^\s*[-*+]\s+(.*)$", lines[i])
                b = re.match(r"^\s*\d+[.)]\s+(.*)$", lines[i])
                if a or b:
                    items.append(_restore((a or b).group(1).strip(), math_blocks))
                    i += 1
                elif not lines[i].strip():
                    i += 1
                    break
                else:
                    break
            blocks.append({"type": "list", "ordered": ordered, "items": items})
            continue

        # 普通段落：收集到空行或下一个块级起点
        buf = [line]
        i += 1
        while i < n:
            s = lines[i].strip()
            if not s:
                break
            if re.match(r"^#{1,6}\s", s) or s.startswith("```") or s.startswith("<!--"):
                break
            if re.match(r"^<(style|script)\b", s, re.IGNORECASE) or _is_pure_html(s):
                break
            if re.match(r"^\s*[-*+]\s+", lines[i]) or re.match(r"^\s*\d+[.)]\s+", lines[i]):
                break
            if s.startswith(">"):
                break
            if "|" in lines[i] and i + 1 < n and _is_table_sep(lines[i + 1]):
                break
            if _is_hr(s):
                break
            buf.append(lines[i])
            i += 1

        para = " ".join(x.strip() for x in buf if x.strip())
        # 独立成段的显示数学 → math 块
        pm = _PLACEHOLDER_RE.fullmatch(para)
        if pm:
            blocks.append({
                "type": "math",
                "mode": "display",
                "source": _display_inner(math_blocks[int(pm.group(1))]),
            })
        else:
            blocks.append({"type": "paragraph", "text": _restore(para, math_blocks)})

    return {"blocks": blocks}


def _parse_table_block(block: list[str], math_blocks: list[str]) -> dict:
    header = block[0]
    sep = block[1] if len(block) > 1 and _is_table_sep(block[1]) else None
    rows = block[2:] if sep else block[1:]

    h = _split_cells(header)
    ncols = max(len(h), 1)

    if sep:
        aligns = []
        for c in _split_cells(sep):
            c = c.strip()
            if c.startswith(":") and c.endswith(":"):
                aligns.append("c")
            elif c.startswith(":"):
                aligns.append("l")
            elif c.endswith(":"):
                aligns.append("r")
            else:
                aligns.append("c")
        while len(aligns) < ncols:
            aligns.append("c")
    else:
        aligns = ["c"] * ncols

    out_rows = []
    for row in rows:
        if not row.strip():
            continue
        cells = _split_cells(row)
        while len(cells) < ncols:
            cells.append("")
        out_rows.append([_restore(c.replace("\\|", "|"), math_blocks) for c in cells[:ncols]])

    return {
        "type": "table",
        "align": aligns,
        "header": [_restore(c.replace("\\|", "|"), math_blocks) for c in h],
        "rows": out_rows,
    }


# ── lint（md 规范化检查）──────────────────────────────────────

_LINT_ARRAY_EMPTY = re.compile(r"\\begin\s*\{array\}\s*\{\s*\}")
_LINT_FENCE = re.compile(r"^\s*```")


def _visible_lines(text: str) -> tuple[list[tuple[int, str]], bool]:
    """取"可见"行（跳过 HTML 注释 / style / script / 围栏代码），返回 (行号, 内容) 列表与围栏是否未闭合。"""
    out: list[tuple[int, str]] = []
    lines = text.split("\n")
    in_fence = in_comment = False
    skip_tag: str | None = None
    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if skip_tag:
            if re.match(rf"^</{skip_tag}\s*>", s, re.IGNORECASE):
                skip_tag = None
            continue
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if _LINT_FENCE.match(s):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if s.startswith("<!--"):
            if "-->" not in s:
                in_comment = True
            continue
        m = re.match(r"^<(style|script)\b", s, re.IGNORECASE)
        if m:
            skip_tag = m.group(1).lower()
            continue
        if s:
            out.append((idx, s))
    return out, in_fence


def lint_md(md_text: str, blocks: list[dict]) -> list[dict]:
    """返回规范化检查报告：[{level, line, msg}]。只报告，不修改源。"""
    warns: list[dict] = []

    # 第一遍（原始文本）：空 array 列格式 / 内联 HTML 残留
    vis_orig, fence_unclosed = _visible_lines(md_text)
    for idx, s in vis_orig:
        if _LINT_ARRAY_EMPTY.search(s):
            warns.append({
                "level": "warn",
                "line": idx,
                "msg": "\\begin{array}{} 空列格式：应写明列数，如 \\begin{array}{cc}（转换器会按行内容自动补全，但建议修正源）",
            })
        if re.search(r"<[a-zA-Z/][^>]*>", s) and not _is_pure_html(s):
            warns.append({
                "level": "info",
                "line": idx,
                "msg": "行内含内联 HTML 标签（<br> 等会原样保留，如需换行建议改用 \\\\ 或空行）",
            })

    # 第二遍（剔除显示数学、保持行结构）：$ 配对状态检查（支持跨行 $...$）
    sanitized = _DISPLAY_MATH_RE.sub(lambda m: "\n" * m.group(0).count("\n"), md_text)
    vis_san, _ = _visible_lines(sanitized)
    open_line: int | None = None
    for idx, s in vis_san:
        for _m in re.finditer(r"(?<!\\)\$", s):
            open_line = None if open_line is not None else idx
    if open_line is not None:
        warns.append({
            "level": "warn",
            "line": open_line,
            "msg": "存在未闭合的 $（从本行开始；可能是跨行数学漏了结束符，或文本中的美元符号）",
        })

    if fence_unclosed:
        warns.append({"level": "warn", "line": len(md_text.split("\n")), "msg": "``` 围栏未闭合"})

    # 块级检查
    prev_level = 0
    for bi, blk in enumerate(blocks):
        if blk["type"] == "heading":
            level = blk["level"]
            if prev_level and level > prev_level + 1:
                warns.append({
                    "level": "info",
                    "line": 0,
                    "msg": f"标题层级跳跃（第 {bi + 1} 块：h{prev_level} → h{level}），建议保持逐级递进",
                })
            prev_level = level
        elif blk["type"] == "table":
            ncols = len(blk["header"])
            for r in blk["rows"]:
                if len(r) != ncols:
                    warns.append({
                        "level": "warn",
                        "line": 0,
                        "msg": f"表格第 {bi + 1} 块某行列数 {len(r)} ≠ 表头 {ncols}",
                    })

    return warns


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Markdown → JSON 结构化中间态（含可选 lint）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
        "  python to_json.py note.md -o note.json\n"
        "  python to_json.py note.md --lint\n",
    )
    parser.add_argument("input", help="输入 .md 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 .json 路径（默认: input.json）")
    parser.add_argument("--title", default=None, help="文档标题（默认: 第一个一级标题或文件名）")
    parser.add_argument("--lint", action="store_true", help="输出 md 规范化检查报告")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"错误: 文件不存在 — {src}")

    md_text = src.read_text(encoding="utf-8")
    blocks = parse_md(md_text)["blocks"]

    title = args.title
    if title is None:
        for blk in blocks:
            if blk["type"] == "heading" and blk["level"] == 1:
                title = blk["text"]
                break
        else:
            title = src.stem

    data = {
        "meta": {
            "title": title,
            "source": str(src),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "blocks": blocks,
    }

    output = Path(args.output) if args.output else src.with_suffix(".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] 已解析 {len(blocks)} 个块 -> {output}")

    if args.lint:
        warns = lint_md(md_text, blocks)
        if not warns:
            print("[LINT] 未发现问题")
        else:
            n_warn = sum(1 for w in warns if w["level"] == "warn")
            n_info = len(warns) - n_warn
            print(f"[LINT] 共 {len(warns)} 条（警告 {n_warn} / 提示 {n_info}）:")
            for w in warns:
                loc = f"第 {w['line']} 行" if w['line'] else "块级"
                print(f"  [{w['level'].upper():>4s}] {loc}: {w['msg']}")
