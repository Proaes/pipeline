#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — 批量格式转换

读取 config.json，按配置批量转换文件。

用法:
    python main.py                  # 使用默认 config.json
    python main.py -c custom.json   # 使用自定义配置
    python main.py --list           # 列出配置中的所有任务

config.json 格式:
    {
      "defaults": { "outdir": "output", "lang": "zh" },
      "tasks": [
        { "input": "path/file.pptx",  "to": ["json", "md", "tex"] },
        { "input": "path/file.sql",   "to": ["drawio"] },
        { "input": "path/file.pptx",  "to": ["json", "md"], "outdir": "custom/" }
      ]
    }

支持的类型映射 (由文件后缀自动推断):
    .pptx → extract.py → json
    .pptx → json → render_md.py → md
    .pptx → json → render_tex.py → tex
    .sql  → sql2drawio.py → drawio
"""

import sys
import os
import json
import subprocess
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.json"

# 输出格式 → { 脚本, 是否依赖中间 json }
FORMAT_HANDLERS = {
    "json":    {"script": "extract.py",     "input_type": "pptx", "needs_json": False},
    "md":      {"script": "render_md.py",   "input_type": "pptx", "needs_json": True},
    "tex":     {"script": "render_tex.py",  "input_type": "pptx", "needs_json": True},
    "drawio":  {"script": "sql2drawio.py",  "input_type": "sql",  "needs_json": False},
}

# 文件后缀 → 输入类型
EXT_TO_TYPE = {
    ".pptx": "pptx",
    ".sql":  "sql",
}


def run(cmd: list[str], desc: str) -> bool:
    """运行一个子进程，返回是否成功。"""
    print(f"  [{desc}] {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    失败: {result.stderr.strip()}")
        return False
    out = result.stdout.strip()
    if out:
        print(f"    {out}")
    return True


def process_task(task: dict[str, Any], defaults: dict[str, Any]) -> bool:
    """处理单个转换任务。"""
    input_path = Path(task["input"])
    if not input_path.is_absolute():
        input_path = (SCRIPT_DIR / input_path).resolve()

    if not input_path.exists():
        print(f"  跳过: 文件不存在 — {input_path}")
        return False

    out_dir = Path(task.get("outdir", defaults.get("outdir", "output")))
    if not out_dir.is_absolute():
        out_dir = (SCRIPT_DIR / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = set(task.get("to", defaults.get("to", ["json", "md"])))
    lang = task.get("lang", defaults.get("lang", "en"))
    tex_mode = task.get("tex_mode", defaults.get("tex_mode", "article"))
    indent = task.get("indent", defaults.get("indent", 2))

    stem = input_path.stem
    ext = input_path.suffix.lower()

    if ext not in EXT_TO_TYPE:
        print(f"  跳过: 不支持的文件类型 — {ext}")
        return False

    input_type = EXT_TO_TYPE[ext]
    print(f"\n任务: {input_path.name}  →  {', '.join(sorted(targets))}")

    results: dict[str, str] = {}

    # ── 分出需要 json 的格式 ──
    json_formats = {f for f in targets if FORMAT_HANDLERS[f].get("needs_json")}
    direct_formats = targets - json_formats

    json_path = out_dir / f"{stem}.json"

    # ── Step 1: 如果需要 json，先提取 ──
    needs_extract = json_formats or ("json" in targets)
    if needs_extract and input_type == "pptx":
        extract_script = SCRIPT_DIR / "extract.py"
        cmd = [sys.executable, str(extract_script), str(input_path),
               "-o", str(json_path), "--indent", str(indent)]
        if not run(cmd, "提取"):
            return False
        if "json" in targets:
            results["json"] = str(json_path)

    # ── Step 2: 渲染需要 json 的格式 ──
    for fmt in sorted(json_formats):
        handler = FORMAT_HANDLERS[fmt]
        script = SCRIPT_DIR / handler["script"]
        out_file = out_dir / f"{stem}.{fmt}"
        cmd = [sys.executable, str(script), str(json_path), "-o", str(out_file)]
        if fmt == "tex":
            cmd += ["--mode", tex_mode]
        if run(cmd, fmt.upper()):
            results[fmt] = str(out_file)

    # ── Step 3: 直接转换的格式 ──
    for fmt in sorted(direct_formats):
        handler = FORMAT_HANDLERS[fmt]
        script = SCRIPT_DIR / handler["script"]
        out_file = out_dir / f"{stem}.{fmt}"
        # sql2drawio 特殊参数
        if fmt == "drawio":
            cmd = [sys.executable, str(script), str(input_path),
                   "-o", str(out_file), "--lang", lang]
        else:
            cmd = [sys.executable, str(script), str(input_path),
                   "-o", str(out_file)]
        if run(cmd, fmt.upper()):
            results[fmt] = str(out_file)

    # ── 结果 ──
    if results:
        print(f"  ✓ 生成 {len(results)} 个文件:")
        for fmt, path in results.items():
            print(f"    [{fmt.upper():>6s}] {path}")
        return True
    return False


def load_config(config_path: Path) -> dict:
    """加载配置文件。"""
    if not config_path.exists():
        sys.exit(f"错误: 配置文件不存在 — {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="批量格式转换 — 根据 config.json 运行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 使用 config.json
  python main.py -c my_config.json  # 自定义配置
  python main.py --list             # 列出任务
        """,
    )
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG),
                        help="配置文件路径 [默认: config.json]")
    parser.add_argument("--list", action="store_true",
                        help="仅列出配置中的任务，不执行")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)
    defaults = config.get("defaults", {})
    tasks = config.get("tasks", [])

    if not tasks:
        print("配置中没有任务。在 config.json 的 tasks 列表中添加条目。")
        return

    if args.list:
        print(f"配置文件: {config_path}")
        print(f"任务数: {len(tasks)}")
        print()
        for i, task in enumerate(tasks, 1):
            inp = task.get("input", "?")
            to = ", ".join(task.get("to", defaults.get("to", [])))
            print(f"  {i}. {inp}  →  {to}")
        return

    print(f"配置文件: {config_path}")
    print(f"任务数: {len(tasks)}")
    print("=" * 50)

    ok = 0
    fail = 0
    for task in tasks:
        if process_task(task, defaults):
            ok += 1
        else:
            fail += 1

    print()
    print("=" * 50)
    print(f"完成: {ok} 成功, {fail} 失败")


if __name__ == "__main__":
    main()
