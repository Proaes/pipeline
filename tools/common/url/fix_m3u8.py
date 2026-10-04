#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_m3u8.py — 修正本地 m3u8 文件中的 TS 路径，使其指向本地文件

用法:
    python fix_m3u8.py                    # 处理 input/ 下所有 m3u8 文件
    python fix_m3u8.py input/xxx.m3u8     # 处理单个文件

问题:
    小鹅通等平台下载的 m3u8 文件中，TS 路径指向手机内部存储路径，
    如 /storage/emulated/0/Android/data/...，ffmpeg 无法识别。

解决方案:
    将路径替换为相对于 m3u8 文件的本地路径（同名文件夹）。
"""

import sys
import re
import os
from pathlib import Path


def fix_m3u8(m3u8_path: Path) -> Path:
    """修正 m3u8 文件中的 TS 路径，生成新的本地 m3u8 文件。"""
    content = m3u8_path.read_text(encoding='utf-8')

    # 提取 TS 文件夹名（.m3u8 文件名 + _0）
    # 注意：TS 文件夹名前面有个隐藏的点号 "."
    # 例如: .840b9b41-xxx.m3u8_0
    ts_dir_name = f".{m3u8_path.name}_0"
    ts_dir = m3u8_path.parent / ts_dir_name

    if not ts_dir.exists():
        # 也试试不带点号的版本
        ts_dir_name_no_dot = f"{m3u8_path.name}_0"
        ts_dir_no_dot = m3u8_path.parent / ts_dir_name_no_dot
        if ts_dir_no_dot.exists():
            ts_dir_name = ts_dir_name_no_dot
            ts_dir = ts_dir_no_dot
        else:
            print(f"  [警告] TS 文件夹不存在: {ts_dir}")
            print(f"  [提示] 也检查了: {ts_dir_no_dot}")
            return None

    # 替换路径：将手机路径替换为本地相对路径
    # /storage/emulated/0/Android/data/com.xiaoe.client/files/Download/.xxx.m3u8_0/0.ts
    # → .xxx.m3u8_0/0.ts (带点号)
    pattern = r'/storage/emulated/0/Android/data/com\.xiaoe\.client/files/Download/\.?[^/]+/(\d+\.ts)'
    replacement = f'{ts_dir_name}/\\1'

    new_content = re.sub(pattern, replacement, content)

    # 写入修正后的 m3u8 文件
    fixed_path = m3u8_path.parent / f"{m3u8_path.stem}_fixed.m3u8"
    fixed_path.write_text(new_content, encoding='utf-8')

    return fixed_path


def convert_m3u8_to_mp4(fixed_m3u8_path: Path, output_dir: Path) -> bool:
    """将修正后的 m3u8 文件转换为 mp4。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{fixed_m3u8_path.stem.replace('_fixed', '')}.mp4"

    if output_file.exists():
        print(f"  [跳过] {output_file.name} 已存在")
        return True

    cmd = [
        "ffmpeg", "-i", str(fixed_m3u8_path),
        "-c", "copy",
        "-y", str(output_file)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

    if result.returncode != 0:
        print(f"  [失败] {result.stderr.strip()[:200]}")
        return False

    print(f"  [完成] -> {output_file.name}")
    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="修正本地 m3u8 文件路径并转换为 mp4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python fix_m3u8.py                           # 处理 input/ 下所有 m3u8
  python fix_m3u8.py input/xxx.m3u8            # 处理单个文件
  python fix_m3u8.py --convert                 # 修正并转换为 mp4
  python fix_m3u8.py --convert --outdir output_mp4 # 指定输出目录
        """,
    )
    parser.add_argument("input", nargs="?", default="input", help="m3u8 文件或目录 [默认: input]")
    parser.add_argument("--convert", action="store_true", help="修正后自动转换为 mp4")
    parser.add_argument("--outdir", default="output_mp4", help="mp4 输出目录 [默认: output_mp4]")
    args = parser.parse_args()

    input_path = Path(args.input)

    if input_path.is_file():
        m3u8_files = [input_path]
    else:
        m3u8_files = sorted(input_path.glob("*.m3u8"))

    if not m3u8_files:
        print(f"[提示] 未找到 m3u8 文件")
        return

    print(f"找到 {len(m3u8_files)} 个 m3u8 文件")

    success = 0
    failed = 0

    for m3u8 in m3u8_files:
        if "_fixed" in m3u8.name:
            continue

        print(f"\n处理: {m3u8.name}")

        # 修正路径
        fixed = fix_m3u8(m3u8)
        if not fixed:
            failed += 1
            continue

        print(f"  [修正] -> {fixed.name}")

        # 转换为 mp4
        if args.convert:
            import subprocess
            output_dir = Path(args.outdir)
            if convert_m3u8_to_mp4(fixed, output_dir):
                success += 1
            else:
                failed += 1
            # 清理临时文件
            # fixed.unlink()
        else:
            success += 1

    print(f"\n{'=' * 60}")
    print(f"完成! 成功: {success}, 失败: {failed}")

    if not args.convert:
        print(f"\n提示: 使用 --convert 参数可自动转换为 mp4")
        print(f"例如: python fix_m3u8.py --convert --outdir output")


if __name__ == "__main__":
    import subprocess
    main()
