#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_lib/common.py — 工具共享脚手架

供 tools/ 下各 to_*.py 复用的公共函数：路径解析、JSON 读写、输出目录处理等。
各工具脚本保持独立可运行，本模块仅提供可选复用的公共逻辑。

用法:
    from _lib import common
    # 或（在 tools/ 下运行时）
    import sys; sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from _lib import common
"""

import json
import re
import sys
from pathlib import Path

# 项目根目录（pipeline/）—— 即 tools/ 的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 任务归档目录
JOBS_DIR = PROJECT_ROOT / "jobs"

# 数据层目录（不入库）
DATA_DIRS = ("jobs", "input", "output", "output_text", "download", "other_files")


def ensure_parent(path: Path) -> Path:
    """确保输出文件的父目录存在，返回原路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data, indent: int = 2) -> Path:
    """写 JSON 文件（UTF-8，保留中文）。"""
    path = ensure_parent(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    return path


def read_json(path: Path) -> dict:
    """读 JSON 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_output(input_path: Path, output: str | None, outdir: str | None,
                   suffix: str) -> Path:
    """统一解析输出路径。

    优先级: -o 指定 > --outdir/输入同名 > 输入同目录。
    suffix 不带点，如 "md"。
    """
    input_path = Path(input_path)
    if output:
        return ensure_parent(Path(output))
    if outdir:
        return ensure_parent(Path(outdir) / f"{input_path.stem}.{suffix}")
    return ensure_parent(input_path.with_suffix(f".{suffix}"))


# 任务槽位目录名：<槽位号>_<类型>，见 pipeline/jobs_template.md（0_源 / 1_一转 / 2_二转 …）。
# 槽位号限 1–2 位：任务目录本身（如 20260829171857_gmath）也是"数字_名字"，不能当槽位。
_SLOT_RE = re.compile(r"^(\d{1,2})_(.+)$")


def slot_dir(src: Path, suffix: str, is_dir: bool = False) -> Path | None:
    """按「源槽位号 + 1」算输出槽位目录；源不在 N_xxx/ 槽位里时返回 None。

    src 是文件时看它所在的目录名（0_md/note.md → 同级 1_pdf/），
    is_dir=True 时看 src 自己的目录名（0_jpg/ → 同级 1_md/）。
    suffix 是目标类型的短名（pdf / tex / json / md / docx / mp3…），不带点。
    返回 None 表示调用方该回落到自己的默认值（与源同目录）。
    """
    src = Path(src)
    d = src if is_dir else src.parent
    m = _SLOT_RE.match(d.name)
    if not m:
        return None
    return d.parent / f"{int(m.group(1)) + 1}_{suffix}"


def print_summary(results: dict[str, str]) -> None:
    """打印结果摘要。results: {格式: 路径}"""
    if not results:
        print("  ✗ 未生成任何文件")
        return
    print(f"  ✓ 生成 {len(results)} 个文件:")
    for fmt, path in results.items():
        print(f"    [{fmt.upper():>6s}] {path}")


def main_guard() -> None:
    """给工具脚本用的 main 调用守卫（可选项，保持脚本风格自由）。"""
    pass
