#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
to_chart.py — 表格（CSV / TSV / 任意分隔文本）→ 折线图 / 柱状图 / 散点图

用法:
    python to_chart.py data.csv -o chart.png
    python to_chart.py data.csv -o chart.png --x 抽数 --y 概率,xP(x)
    python to_chart.py data.csv -o chart.png --x '#2' --y '#4,#9'      # 1 基列号
    python to_chart.py data.csv --list-columns                          # 只看列，不画图
    python to_chart.py data.csv -o c.png --kind bar --title 标题 --pct
    python to_chart.py data.csv -o c.png --x '#2' --y '#4,#9' --labels 特许池,重构池

列选择:
    表头名（首次匹配）或 '#N'（1 基列号；表头重名时必须用列号）
    默认 x = 第一个数值列；y = 其余数值列（可多列 → 多条曲线 + 图例）

行处理:
    默认 --drop-invalid：跳过 x/y 解析不出数字的行（说明行、汇总行、E(X) 行等）
    --keep-invalid 则保留（缺失点处曲线断开）

环境:
    D:\\mamba\\envs\\proaes\\python.exe（matplotlib + pandas 已装；本脚本只用 stdlib csv + matplotlib）

输出:
    按 -o 的扩展名决定格式（.png / .svg / .pdf）；不给 -o 时与输入同目录同名
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 指向 tools/
from _lib import common  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # 无显示环境也能出图
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

ENCODINGS = ("utf-8-sig", "utf-8", "gbk")
KINDS = ("line", "bar", "scatter")


# ─── 读表 ────────────────────────────────────────────────────────────────

def read_lines(path: Path, encoding: str | None):
    """按候选编码读全部文本行；返回 (lines, 实际编码)。"""
    for enc in ([encoding] if encoding else ENCODINGS):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read().splitlines(), enc
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"✗ 无法解码（试过 {', '.join(ENCODINGS)}）：{path}（可用 --encoding 指定）")


def sniff_delimiter(lines, forced=None) -> str:
    if forced:
        return forced
    sample = "\n".join(lines[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except Exception:
        return ","


def read_table(path: Path, delimiter: str, encoding: str | None, has_header: bool):
    lines, enc = read_lines(path, encoding)
    delim = sniff_delimiter(lines, delimiter)
    rows = [r for r in csv.reader(lines, delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        raise SystemExit(f"✗ 文件没有可解析的内容行：{path}")
    if has_header:
        return rows[0], rows[1:], enc, delim
    return [f"列{i + 1}" for i in range(len(rows[0]))], rows, enc, delim


# ─── 取数与列选择 ────────────────────────────────────────────────────────

def to_number(s):
    """把单元格转成 float；失败返回 None。支持千分位逗号与百分号。"""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("，", "")
    if not t:
        return None
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    try:
        v = float(t)
    except ValueError:
        return None
    return v / 100 if pct else v


def numeric_ratio(rows, idx) -> float:
    if not rows:
        return 0.0
    ok = sum(1 for r in rows if idx < len(r) and to_number(r[idx]) is not None)
    return ok / len(rows)


def resolve_col(spec: str, header, ncols: int) -> int:
    """把 '列名' 或 '#N'（1 基）解析成 0 基列号。"""
    s = spec.strip()
    if s.startswith("#"):
        s = s[1:]
    if s.isdigit():
        i = int(s) - 1
        if not 0 <= i < ncols:
            raise SystemExit(f"✗ 列号越界：{spec}（共 {ncols} 列）")
        return i
    for i, name in enumerate(header):
        if name.strip() == s:
            return i
    raise SystemExit(f"✗ 找不到列：{spec}（可用 --list-columns 查看）")


def split_specs(value: str):
    return [p for p in (x.strip() for x in value.split(",")) if p]


# ─── 画图 ────────────────────────────────────────────────────────────────

def plot(xs, series, args, out: Path):
    fig, ax = plt.subplots(figsize=(args.width, args.height))

    if args.kind == "line":
        for label, ys in series:
            ax.plot(xs, ys, lw=1.6, marker="o" if args.markers else None,
                    ms=3 if args.markers else None, label=label)
    elif args.kind == "bar":
        n = len(series)
        width = 1.0 / (n + 0.5)
        for i, (label, ys) in enumerate(series):
            offset = [x + (i - (n - 1) / 2) * width for x in xs]
            ax.bar(offset, ys, width=width, label=label)
    else:
        for label, ys in series:
            ax.scatter(xs, ys, s=14, label=label)

    ax.set_title(args.title or out.stem, fontsize=14, pad=12)
    ax.set_xlabel(args.xlabel or "")
    ax.set_ylabel(args.ylabel or "")
    if args.pct:
        ax.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.2%}"))
    if args.log_y:
        ax.set_yscale("log")
    if args.grid:
        ax.grid(True, ls=":", lw=0.6, alpha=0.55)
        ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    if len(series) > 1 or args.legend:
        ax.legend(loc="best", frameon=False)

    fig.savefig(common.ensure_parent(out), dpi=args.dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ─── 主流程 ──────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="to_chart.py",
        description="表格（CSV/TSV）→ 折线图 / 柱状图 / 散点图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", help="输入表格（.csv / .tsv / 任意分隔文本）")
    p.add_argument("-o", "--output", help="输出图（.png/.svg/.pdf）；默认与输入同目录同名")
    p.add_argument("--outdir", help="输出目录（不给 -o 时用）")
    p.add_argument("--x", default=None, help="x 列：列名或 '#N'（1 基）；默认第一个数值列")
    p.add_argument("--y", default=None, help="y 列，逗号分隔；默认其余数值列")
    p.add_argument("--kind", choices=KINDS, default="line", help="图型（默认 line）")
    p.add_argument("--title", default=None, help="标题")
    p.add_argument("--xlabel", default=None)
    p.add_argument("--ylabel", default=None)
    p.add_argument("--labels", default=None,
                   help="图例名，逗号分隔（按 y 列顺序）；默认用表头名，重名时自动带 #列号")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--width", type=float, default=13.0)
    p.add_argument("--height", type=float, default=7.0)
    p.add_argument("--markers", action="store_true", help="折线图显示数据点标记")
    p.add_argument("--pct", action="store_true", help="y 轴按百分比显示")
    p.add_argument("--log-y", action="store_true", help="y 轴对数刻度")
    p.add_argument("--no-grid", dest="grid", action="store_false", help="不画网格")
    p.add_argument("--legend", action="store_true", help="即使只有一条曲线也显示图例")
    p.add_argument("--no-header", dest="has_header", action="store_false", help="首行不是表头")
    p.add_argument("--delimiter", default=None, help="分隔符；默认自动嗅探（, ; \\t |）")
    p.add_argument("--encoding", default=None, help="编码；默认 utf-8-sig → utf-8 → gbk")
    p.add_argument("--keep-invalid", dest="drop_invalid", action="store_false",
                   help="保留解析不出数字的行（默认跳过）")
    p.add_argument("--list-columns", action="store_true", help="只列出识别到的列，不画图")
    return p


def main():
    args = build_parser().parse_args()
    src = Path(args.input)
    if not src.is_file():
        raise SystemExit(f"✗ 输入文件不存在：{src}")

    header, rows, enc, delim = read_table(src, args.delimiter, args.encoding, args.has_header)
    ncols = len(header)
    ratios = [numeric_ratio(rows, i) for i in range(ncols)]
    numeric = [i for i, r in enumerate(ratios) if r >= 0.5]

    if args.list_columns:
        print(f"列数: {ncols}｜数据行: {len(rows)}｜编码: {enc}｜分隔符: {delim!r}")
        for i, name in enumerate(header):
            print(f"  #{i + 1:<3} {name:<24} 数值占比 {ratios[i]:.0%}"
                  f"{'  ← 数值列' if i in numeric else ''}")
        return

    if not numeric:
        raise SystemExit("✗ 没有识别到数值列（可用 --list-columns 查看，或检查分隔符/编码）")

    x_idx = resolve_col(args.x, header, ncols) if args.x else numeric[0]
    if args.y:
        y_idx = [resolve_col(s, header, ncols) for s in split_specs(args.y)]
    else:
        y_idx = [i for i in numeric if i != x_idx]
    if not y_idx:
        raise SystemExit("✗ 没有可画的 y 列")

    dups = {h for h in header if header.count(h) > 1}
    if dups and args.y is None:
        print(f"  ! 表头有重名 {sorted(dups)}，按名选择只会命中第一个；建议用 '#N' 指定列号")

    # 组数据
    xs, cols = [], {i: [] for i in y_idx}
    skipped = 0
    for r in rows:
        xv = to_number(r[x_idx]) if x_idx < len(r) else None
        yv = {i: (to_number(r[i]) if i < len(r) else None) for i in y_idx}
        if xv is None or any(v is None for v in yv.values()):
            if args.drop_invalid:
                skipped += 1
                continue
            if xv is None:
                continue
        xs.append(xv)
        for i in y_idx:
            cols[i].append(yv[i] if yv[i] is not None else float("nan"))

    if not xs:
        raise SystemExit("✗ 跳过无效行后没有剩余数据（试试 --keep-invalid）")

    order = sorted(range(len(xs)), key=lambda k: xs[k])
    xs = [xs[k] for k in order]

    labels = split_specs(args.labels) if args.labels else None
    if labels and len(labels) != len(y_idx):
        raise SystemExit(f"✗ --labels 给了 {len(labels)} 个，但有 {len(y_idx)} 条 y 列")
    series = []
    for n, i in enumerate(y_idx):
        name = header[i].strip() or f"列{i + 1}"
        if labels:
            name = labels[n]
        elif header.count(header[i]) > 1:  # 表头重名 → 带列号，否则图例分不清
            name = f"{name} (#{i + 1})"
        series.append((name, [cols[i][k] for k in order]))

    out = common.resolve_output(src, args.output, args.outdir, "png")
    plot(xs, series, args, out)

    print(f"  编码 {enc}｜分隔符 {delim!r}｜表头 {'有' if args.has_header else '无'}"
          f"｜数据行 {len(rows)}｜跳过 {skipped}")
    print(f"  x = #{x_idx + 1} {header[x_idx].strip()!r}｜"
          f"y = " + "、".join(f"#{i + 1} {header[i].strip()!r}" for i in y_idx))
    print("  图例: " + "、".join(f"{name} ← #{i + 1}" for (name, _), i in zip(series, y_idx)))
    print(f"  x 范围 {min(xs):g} – {max(xs):g}｜点数 {len(xs)}｜图型 {args.kind}")
    common.print_summary({out.suffix.lstrip(".") or "png": out})


if __name__ == "__main__":
    main()
