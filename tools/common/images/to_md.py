#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_ocr.py — 批量 OCR：扫描文件夹中的图片，调用 Ollama 视觉模型生成 Markdown

用法:
    python batch_ocr.py                           # 使用默认配置
    python batch_ocr.py -i ./images -o ./output_md   # 指定输入输出目录
    python batch_ocr.py --prompt "提取表格数据"     # 自定义提示词
    python batch_ocr.py --recursive                # 递归扫描子文件夹

前置条件:
    1. Ollama 已安装并运行 (ollama serve)
    2. 视觉模型已拉取 (ollama pull qwen2.5vl:7b)

支持的图片格式:
    .jpg, .jpeg, .png, .bmp, .webp, .tiff, .tif, .gif
"""

import subprocess
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime

# ── 默认配置 ──────────────────────────────────────────────────────
DEFAULT_INPUT = "input"
DEFAULT_OUTPUT = "output_md"
DEFAULT_MODEL = "qwen2.5vl:7b"
DEFAULT_PROMPT = (
    "请仔细阅读此图片内容，并将其完整转换为Markdown格式。\n"
    "要求：\n"
    "1. 保留原文档的标题、段落、列表等结构\n"
    "2. 表格请使用Markdown表格语法\n"
    "3. 如果有图片或图表，请用 [图片] 或 [图表] 标注\n"
    "4. 数学公式请用 LaTeX 语法 ($...$ 或 $$...$$)\n"
    "5. 只输出Markdown内容，不要添加额外说明"
)

SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.tif', '.gif'}


def check_ollama(model: str) -> bool:
    """检查 Ollama 是否运行且模型可用。"""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            print("[错误] Ollama 未运行，请先执行: ollama serve")
            return False

        if model not in result.stdout:
            print(f"[错误] 模型 '{model}' 未找到，请先执行: ollama pull {model}")
            print(f"\n已安装的模型:\n{result.stdout}")
            return False

        return True
    except FileNotFoundError:
        print("[错误] 未找到 ollama 命令，请先安装 Ollama: https://ollama.com/download")
        return False
    except subprocess.TimeoutExpired:
        print("[错误] Ollama 响应超时，请检查 Ollama 是否正常运行")
        return False


def ocr_single(image_path: Path, model: str, prompt: str) -> str | None:
    """对单张图片进行 OCR，返回 Markdown 文本。"""
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt, str(image_path)],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            print(f"  [错误] OCR 失败: {result.stderr.strip()}")
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"  [错误] OCR 超时 (5分钟)")
        return None
    except Exception as e:
        print(f"  [错误] {e}")
        return None


def collect_images(input_dir: Path, recursive: bool) -> list[Path]:
    """收集所有图片文件。"""
    images = []
    if recursive:
        for fmt in SUPPORTED_FORMATS:
            images.extend(input_dir.rglob(f"*{fmt}"))
            images.extend(input_dir.rglob(f"*{fmt.upper()}"))
    else:
        for fmt in SUPPORTED_FORMATS:
            images.extend(input_dir.glob(f"*{fmt}"))
            images.extend(input_dir.glob(f"*{fmt.upper()}"))

    # 去重并排序
    return sorted(set(images))


def batch_ocr(input_dir: str, output_dir: str, model: str, prompt: str,
              recursive: bool, force: bool):
    """批量 OCR 主函数。"""
    input_path = Path(input_dir).resolve()
    output_path = Path(output_dir).resolve()

    # 检查输入目录
    if not input_path.exists():
        print(f"[错误] 输入目录不存在: {input_path}")
        sys.exit(1)

    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)

    # 收集图片
    images = collect_images(input_path, recursive)
    if not images:
        print(f"[提示] 在 {input_path} 中未找到图片文件")
        print(f"  支持的格式: {', '.join(sorted(SUPPORTED_FORMATS))}")
        return

    # 打印统计
    print("=" * 60)
    print(f"  批量 OCR 工具")
    print("=" * 60)
    print(f"  输入目录: {input_path}")
    print(f"  输出目录: {output_path}")
    print(f"  模型:     {model}")
    print(f"  图片数量: {len(images)}")
    print(f"  递归扫描: {'是' if recursive else '否'}")
    print(f"  强制覆盖: {'是' if force else '否'}")
    print("=" * 60)
    print()

    success = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    for i, img in enumerate(images, 1):
        # 计算相对路径（用于递归模式下保持目录结构）
        try:
            rel_path = img.relative_to(input_path)
        except ValueError:
            rel_path = img.name

        # 输出文件路径
        if recursive:
            md_path = output_path / rel_path.with_suffix('.md')
            md_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            md_path = output_path / f"{img.stem}.md"

        # 打印进度
        print(f"[{i}/{len(images)}] {rel_path}")

        # 检查是否已存在
        if md_path.exists() and not force:
            print(f"  [跳过] {md_path.name} 已存在 (使用 --force 强制覆盖)")
            skipped += 1
            continue

        # OCR
        print(f"  正在识别...")
        ocr_start = time.time()
        markdown = ocr_single(img, model, prompt)
        ocr_time = time.time() - ocr_start

        if markdown:
            # 写入 Markdown 文件
            with open(md_path, 'w', encoding='utf-8') as f:
                # 写入元信息头
                f.write(f"# {img.stem}\n\n")
                f.write(f"> - 来源: `{img.name}`\n")
                f.write(f"> - 模型: `{model}`\n")
                f.write(f"> - 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"> - 耗时: {ocr_time:.1f}s\n")
                f.write("\n---\n\n")
                f.write(markdown)
                f.write("\n")

            print(f"  [完成] -> {md_path.name} ({ocr_time:.1f}s)")
            success += 1
        else:
            print(f"  [失败]")
            failed += 1

    # 打印总结
    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"  处理完成!")
    print(f"  成功: {success}  跳过: {skipped}  失败: {failed}")
    print(f"  总耗时: {elapsed:.1f}s")
    print(f"  输出目录: {output_path}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="批量 OCR — 调用 Ollama 视觉模型将图片转换为 Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python batch_ocr.py                                  # 使用默认配置
  python batch_ocr.py -i ./images -o ./output_md          # 指定输入输出目录
  python batch_ocr.py --model qwen2.5vl:7b             # 指定模型
  python batch_ocr.py --prompt "提取表格数据为CSV格式"   # 自定义提示词
  python batch_ocr.py --recursive                       # 递归扫描子文件夹
  python batch_ocr.py --force                           # 强制覆盖已有文件
        """,
    )
    parser.add_argument("-i", "--input", default=DEFAULT_INPUT,
                        help=f"输入图片目录 [默认: {DEFAULT_INPUT}]")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help=f"输出 Markdown 目录 [默认: {DEFAULT_OUTPUT}]")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL,
                        help=f"Ollama 模型名称 [默认: {DEFAULT_MODEL}]")
    parser.add_argument("-p", "--prompt", default=DEFAULT_PROMPT,
                        help="OCR 提示词 [默认: 完整文档转Markdown]")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="递归扫描子文件夹")
    parser.add_argument("-f", "--force", action="store_true",
                        help="强制覆盖已存在的输出文件")
    parser.add_argument("--check", action="store_true",
                        help="仅检查 Ollama 和模型状态")
    args = parser.parse_args()

    # 检查 Ollama
    print(f"检查 Ollama 和模型 '{args.model}'...")
    if not check_ollama(args.model):
        sys.exit(1)
    print("[OK] Ollama 运行正常，模型已就绪\n")

    if args.check:
        print("环境检查通过！可以开始批量 OCR。")
        return

    # 执行批量 OCR
    batch_ocr(args.input, args.output, args.model, args.prompt,
              args.recursive, args.force)


if __name__ == "__main__":
    main()
