#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — PPTX 提取与渲染一键串联

用法:
    python pipeline.py input.pptx                        # 默认: JSON + Markdown
    python pipeline.py input.pptx --to json md tex       # 指定输出格式
    python pipeline.py input.pptx --to all               # 全部格式

输出文件放在 input 同目录或 --outdir 指定目录。
"""

import sys
import os
import argparse
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def run_step(script: str, args: list[str], description: str) -> bool:
    """运行一个步骤，返回是否成功。"""
    script_path = SCRIPT_DIR / script
    cmd = [sys.executable, str(script_path)] + args
    print(f"  [{description}] {script} {' '.join(args)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    失败: {result.stderr.strip()}")
        return False
    print(f"    {result.stdout.strip()}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="pptx-pipeline — PPTX 提取 & 多格式渲染",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python pipeline.py slides.pptx                    # 提取 JSON + 渲染 Markdown
  python pipeline.py slides.pptx --to json md tex   # 指定三种格式
  python pipeline.py slides.pptx --to all           # 全部格式
  python pipeline.py slides.pptx --outdir ./out     # 指定输出目录
        """,
    )
    parser.add_argument("input", help="输入 PPTX 文件路径")
    parser.add_argument(
        "--to",
        nargs="+",
        default=["json", "md"],
        choices=["json", "md", "tex", "docx", "all"],
        help="输出格式 [默认: json md]",
    )
    parser.add_argument(
        "--tex-mode",
        choices=["article", "beamer"],
        default="article",
        help="LaTeX 模式 [默认: article]",
    )
    parser.add_argument(
        "--outdir",
        default=None,
        help="输出目录 [默认: input 所在目录]",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON 缩进 [默认: 2]",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"错误: 文件不存在 — {args.input}")

    # 解析格式
    targets = set(args.to)
    if "all" in targets:
        targets = {"json", "md", "tex", "docx"}

    # 输出目录
    input_path = Path(args.input).resolve()
    if args.outdir:
        out_dir = Path(args.outdir).resolve()
    else:
        out_dir = input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = input_path.stem
    print(f"输入: {input_path}")
    print(f"输出: {out_dir}")
    print(f"格式: {', '.join(sorted(targets))}")
    print()

    json_path = out_dir / f"{stem}.json"

    # ── Step 1: Extract JSON ──
    print(">> Step 1: 提取 PPTX -> JSON")
    extract_args = [str(input_path), "-o", str(json_path), "--indent", str(args.indent)]
    if not run_step("common/pptx/to_json.py", extract_args, "提取"):
        sys.exit(1)
    print()

    # ── Step 2: Render ──
    results = {"json": str(json_path)}

    if "md" in targets:
        md_path = out_dir / f"{stem}.md"
        print(">> Step 2a: JSON -> Markdown")
        if run_step("common/json/to_md.py", [str(json_path), "-o", str(md_path)], "Markdown"):
            results["md"] = str(md_path)
        print()

    if "tex" in targets:
        tex_path = out_dir / f"{stem}.tex"
        print(">> Step 2b: JSON -> LaTeX")
        tex_args = [str(json_path), "-o", str(tex_path), "--mode", args.tex_mode]
        if run_step("common/json/to_tex.py", tex_args, "LaTeX"):
            results["tex"] = str(tex_path)
        print()

    if "docx" in targets:
        docx_path = out_dir / f"{stem}.docx"
        print(">> Step 2c: JSON -> Word")
        if run_step("common/json/to_docx.py", [str(json_path), "-o", str(docx_path)], "Word"):
            results["docx"] = str(docx_path)
        print()

    # ── Summary ──
    print("=" * 50)
    print("完成! 输出文件:")
    for fmt, path in results.items():
        print(f"  [{fmt.upper():>4s}] {path}")


if __name__ == "__main__":
    main()
