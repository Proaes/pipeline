#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
to_pdf.py — Markdown / LaTeX → PDF 编译

.md 输入按项目链路处理：md → json（to_json.py）→ tex（json/to_tex.py）→ latexmk 编译；
编译在临时构建目录进行，产物（PDF / synctex.gz）拷贝回输出位置，源目录保持干净。

用法:
    python to_pdf.py input.md -o output.pdf --open
    python to_pdf.py input.tex --engine xelatex
    python to_pdf.py input.md --sumatra "D:\\tools\\SumatraPDF\\SumatraPDF.exe"
    python to_pdf.py input.md --no-synctex

说明:
    - 默认引擎 pdflatex（ctexart 中文支持已验证）；中文亦可选 xelatex/lualatex。
    - --open 用系统默认 PDF 查看器打开；--sumatra 指定 SumatraPDF 时以
      -reuse-instance 复用窗口刷新（配合 watch.py 实现"左改右看"）。
    - 生成的 .synctex.gz 会与 PDF 放在一起，供编辑器正反向定位。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import to_json  # noqa: E402

# 统一 UTF-8 输出（管道/文件重定向时避免依赖控制台代码页）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

_ENGINE_FLAG = {
    "pdflatex": "-pdf",
    "xelatex": "-xelatex",
    "lualatex": "-lualatex",
}


def _find_tool(name: str) -> str:
    p = shutil.which(name)
    if not p:
        sys.exit(f"错误: 未找到 {name}，请确认 TeX Live 已安装并加入 PATH")
    return p


def _default_build_dir() -> Path:
    """构建目录默认放在 pipeline/output/md_build（output/ 为不入库的数据层）。

    不放在系统临时目录：workspace-write 沙箱下系统 temp 可能不可写。
    """
    base = SCRIPT_DIR.parent.parent.parent / "output" / "md_build"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run_", dir=str(base)))


def _first_heading(data: dict) -> str:
    """从 blocks 结构取第一个一级标题。"""
    for b in data.get("blocks", []):
        if b.get("type") == "heading" and b.get("level") == 1:
            return b.get("text", "")
    return ""


def compile_tex(tex_path: Path, engine: str, synctex: bool, build_dir: Path) -> tuple[bool, str]:
    """latexmk 编译，返回 (是否成功, 日志尾部)。"""
    latexmk = _find_tool("latexmk")
    tex_abs = tex_path.resolve()
    build_abs = build_dir.resolve()
    cmd = [latexmk, _ENGINE_FLAG[engine], "-interaction=nonstopmode", "-halt-on-error"]
    if synctex:
        cmd.append("-synctex=1")
    cmd += ["-outdir=" + str(build_abs), str(tex_abs)]

    try:
        r = subprocess.run(
            cmd, cwd=str(tex_abs.parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "编译超时（>300s）"

    log = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, log


def main():
    parser = argparse.ArgumentParser(
        description="Markdown / LaTeX → PDF 编译（latexmk）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
        "  python to_pdf.py note.md --open\n"
        "  python to_pdf.py note.tex --engine xelatex\n"
        "  python to_pdf.py note.md --sumatra D:\\tools\\SumatraPDF\\SumatraPDF.exe\n",
    )
    parser.add_argument("input", help="输入 .md 或 .tex 文件")
    parser.add_argument("-o", "--output", default=None, help="输出 .pdf 路径（默认: input.pdf）")
    parser.add_argument("--engine", choices=list(_ENGINE_FLAG), default="xelatex",
                        help="编译引擎 [默认: xelatex，pdflatex 可能有 CJK 兼容问题]")
    parser.add_argument("--no-synctex", action="store_true", help="不生成 synctex（默认生成）")
    parser.add_argument("--title", default=None, help="md 输入时的文档标题（默认自动）")
    parser.add_argument("--author", default="", help="md 输入时的作者")
    parser.add_argument("--no-toc", action="store_true", help="md 输入时不生成目录")
    parser.add_argument("--open", action="store_true", help="编译后打开/刷新 PDF")
    parser.add_argument("--sumatra", default=None,
                        help="SumatraPDF 路径，用 -reuse-instance 刷新（推荐实时预览）")
    parser.add_argument("--build-dir", default=None, help="指定构建目录（默认系统临时目录）")
    parser.add_argument("--keep-build", action="store_true", help="保留构建目录（调试用）")
    args = parser.parse_args()

    src = Path(args.input).resolve()
    if not src.exists():
        sys.exit(f"错误: 文件不存在 — {src}")

    build_dir = Path(args.build_dir) if args.build_dir else _default_build_dir()
    build_dir.mkdir(parents=True, exist_ok=True)

    # ── 1) md → json → tex ──
    if src.suffix.lower() == ".md":
        json_path = build_dir / (src.stem + ".json")
        tex_path = build_dir / (src.stem + ".tex")
        md_text = src.read_text(encoding="utf-8")
        data = to_json.parse_md(md_text)
        if args.title is None:
            args.title = _first_heading(data) or src.stem
        data["meta"] = {"title": args.title, "source": str(src)}
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[1/3] Markdown → JSON: {json_path}")

        renderer = SCRIPT_DIR.parent / "json" / "to_tex.py"
        rcmd = [sys.executable, str(renderer), str(json_path), "-o", str(tex_path),
                "--title", args.title, "--author", args.author]
        if args.no_toc:
            rcmd.append("--no-toc")
        r = subprocess.run(rcmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.stdout:
            print(r.stdout.strip())
        if r.stderr:
            print(r.stderr.strip(), file=sys.stderr)
        if r.returncode != 0:
            print("错误: JSON → LaTeX 渲染失败", file=sys.stderr)
            if not args.keep_build:
                shutil.rmtree(build_dir, ignore_errors=True)
            sys.exit(1)
        print(f"[2/3] JSON → LaTeX: {tex_path}")
    elif src.suffix.lower() == ".tex":
        tex_path = src
    else:
        sys.exit("错误: 输入需为 .md 或 .tex 文件")

    # ── 2) 编译 ──
    print(f"[3/3] latexmk 编译（{args.engine}）...")
    ok, log = compile_tex(tex_path, args.engine, not args.no_synctex, build_dir)
    pdf = build_dir / (tex_path.stem + ".pdf")

    if not ok or not pdf.exists():
        print("编译失败，日志尾部：")
        print(log[-1500:] if log else "（无日志输出）")
        if not args.keep_build:
            shutil.rmtree(build_dir, ignore_errors=True)
        sys.exit(1)

    # ── 3) 产物拷回输出位置 ──
    output = Path(args.output) if args.output else src.with_suffix(".pdf")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, output)
    synctex = build_dir / (tex_path.stem + ".synctex.gz")
    if synctex.exists():
        shutil.copy2(synctex, output.with_suffix(output.suffix + ".synctex.gz"))
    print(f"[OK] PDF → {output}")

    # ── 4) 打开/刷新预览 ──
    if args.sumatra:
        subprocess.Popen([args.sumatra, "-reuse-instance", str(output)])
    elif args.open:
        os.startfile(str(output))  # type: ignore[attr-defined]

    if not args.keep_build:
        shutil.rmtree(build_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
