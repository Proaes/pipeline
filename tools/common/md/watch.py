#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watch.py — 监听 Markdown 笔记，保存后自动"转换 + 编译 PDF"

纯轮询实现（无第三方依赖）：检测到 md 变化并等写入稳定后，调用 to_pdf.py 重建。
配合 SumatraPDF（--sumatra 以 -reuse-instance 刷新）可实现"左改右看"的实时预览：
保存笔记 → 自动出 PDF → 预览窗口毫秒级刷新。

用法:
    python watch.py note.md --sumatra "D:\\tools\\SumatraPDF\\SumatraPDF.exe"
    python watch.py notes_dir --engine xelatex --interval 1.0
    python watch.py note.md --outdir ./build --no-synctex --recursive

说明:
    - 启动时先做一次初始构建，之后监听 *.md 文件变化。
    - Ctrl+C 退出；--interval 控制轮询间隔（秒，默认 1.0）。
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# 统一 UTF-8 输出（管道/文件重定向时避免依赖控制台代码页）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def _sig(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def _wait_stable(path: Path, interval: float) -> bool:
    """等待文件写入稳定（mtime/size 连续两次相同）。"""
    s0 = _sig(path)
    time.sleep(interval)
    for _ in range(5):
        s1 = _sig(path)
        if s1 == s0:
            return True
        s0 = s1
        time.sleep(interval)
    return False


def build_one(py: str, to_pdf: Path, md: Path, args: argparse.Namespace) -> None:
    cmd = [py, str(to_pdf), str(md)]
    if args.outdir:
        cmd += ["-o", str(Path(args.outdir) / (md.stem + ".pdf"))]
    if args.engine:
        cmd += ["--engine", args.engine]
    if args.no_synctex:
        cmd += ["--no-synctex"]
    if args.open or args.sumatra:
        cmd += ["--open"]
    if args.sumatra:
        cmd += ["--sumatra", args.sumatra]
    if args.build_dir:
        cmd += ["--build-dir", args.build_dir]

    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    dt = time.time() - t0
    stamp = time.strftime("%H:%M:%S")
    if r.returncode == 0:
        print(f"[{stamp}] ✓ {md.name} 重建完成（{dt:.1f}s）", flush=True)
    else:
        print(f"[{stamp}] ✗ {md.name} 编译失败（{dt:.1f}s）", flush=True)
        tail = (r.stdout or "") + (r.stderr or "")
        print(tail[-800:])


def main():
    parser = argparse.ArgumentParser(
        description="Markdown 笔记监听：保存即自动转 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
        "  python watch.py note.md --sumatra D:\\tools\\SumatraPDF\\SumatraPDF.exe\n"
        "  python watch.py notes_dir --recursive --interval 0.5\n",
    )
    parser.add_argument("input", help="单个 .md 文件或目录")
    parser.add_argument("--outdir", default=None,
                        help="PDF 输出目录（默认: 源在 N_xxx/ 槽位里 → 同级 (N+1)_pdf/；否则与 md 同目录）")
    parser.add_argument("--engine", default=None, choices=["pdflatex", "xelatex", "lualatex"],
                        help="编译引擎（不给则沿用 to_pdf.py 的默认 xelatex；pdflatex 在中文笔记上会失败）")
    parser.add_argument("--no-synctex", action="store_true", help="不生成 synctex")
    parser.add_argument("--open", action="store_true", help="每次构建后用默认查看器打开")
    parser.add_argument("--sumatra", default=None, help="SumatraPDF 路径（-reuse-instance 刷新）")
    parser.add_argument("--build-dir", default=None, help="构建目录（默认由 to_pdf.py 自动创建）")
    parser.add_argument("--interval", type=float, default=1.0, help="轮询间隔秒数 [默认: 1.0]")
    parser.add_argument("--recursive", action="store_true", help="目录输入时递归监听子目录")
    args = parser.parse_args()

    src = Path(args.input).resolve()
    if src.is_dir():
        files = sorted(src.rglob("*.md")) if args.recursive else sorted(src.glob("*.md"))
    elif src.is_file():
        files = [src]
    else:
        sys.exit(f"错误: 路径不存在 — {src}")
    if not files:
        sys.exit("错误: 未找到任何 .md 文件")

    py = sys.executable
    to_pdf = SCRIPT_DIR / "to_pdf.py"
    print(f"监听 {len(files)} 个文件，Ctrl+C 退出:")
    for f in files:
        print(f"  - {f}")

    # 初始构建
    for f in files:
        build_one(py, to_pdf, f, args)

    sigs = {}
    for f in files:
        try:
            sigs[f] = _sig(f)
        except FileNotFoundError:
            pass

    try:
        while True:
            time.sleep(args.interval)
            for f in files:
                try:
                    s = _sig(f)
                except FileNotFoundError:
                    continue
                if s != sigs.get(f):
                    sigs[f] = s
                    if _wait_stable(f, args.interval):
                        build_one(py, to_pdf, f, args)
    except KeyboardInterrupt:
        print("\n已停止监听")


if __name__ == "__main__":
    main()
