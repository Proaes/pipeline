#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_to_text.py — 纯音频转文本工具

直接对音频文件进行语音识别，无需提取步骤。
支持 .wav, .mp3, .flac, .m4a, .ogg, .wma 等格式。

用法:
    python audio_to_text.py input.wav                           # 默认: Qwen3-ASR
    python audio_to_text.py input.mp3 --model sensevoice        # 使用 SenseVoice
    python audio_to_text.py ./recordings/ --recursive            # 批量处理
    python audio_to_text.py input.wav --output transcript.md     # 指定输出路径

前置条件:
    pip install funasr torch torchaudio
    ffmpeg 需要在 PATH 中 (处理 mp3/m4a 等压缩格式)
"""

import subprocess
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime

# ── 支持的格式 ────────────────────────────────────────────────────
AUDIO_FORMATS = {'.wav', '.mp3', '.flac', '.m4a', '.ogg', '.wma', '.aac', '.opus'}
ASR_MODELS = ['qwen3asr', 'sensevoice', 'paraformer', 'whisper']

# ── 默认提示词 ────────────────────────────────────────────────────
DEFAULT_PROMPT = ""  # ASR 模型不需要提示词


def check_ffmpeg() -> bool:
    """检查 ffmpeg 是否可用。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def convert_to_wav(audio_path: Path, wav_path: Path) -> bool:
    """将音频转换为 WAV 格式（16kHz, 单声道）。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(audio_path), "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", "-y", str(wav_path)],
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("  [错误] 音频转换超时")
        return False


def load_asr_model(model_name: str):
    """加载 ASR 模型，返回 transcribe 函数。"""
    print(f"  正在加载模型 '{model_name}'...")

    if model_name == "qwen3asr":
        from funasr import AutoModel
        model = AutoModel(model="Qwen/Qwen3-ASR-0.6B")
        def transcribe(audio_path):
            result = model.generate(input=str(audio_path))
            return result[0]["text"]
        return transcribe

    elif model_name == "sensevoice":
        from funasr import AutoModel
        model = AutoModel(model="iic/SenseVoiceSmall")
        def transcribe(audio_path):
            result = model.generate(input=str(audio_path), language="zh")
            return result[0]["text"]
        return transcribe

    elif model_name == "paraformer":
        from funasr import AutoModel
        model = AutoModel(model="paraformer-zh", model_revision="v2.0.4",
                          vad_model="fsmn-vad", punc_model="ct-punc")
        def transcribe(audio_path):
            result = model.generate(input=str(audio_path))
            return result[0]["text"]
        return transcribe

    elif model_name == "whisper":
        import whisper
        model = whisper.load_model("large-v3")
        def transcribe(audio_path):
            result = model.transcribe(str(audio_path), language="zh")
            return result["text"]
        return transcribe

    else:
        raise ValueError(f"不支持的模型: {model_name}")


def collect_audio(input_dir: Path, recursive: bool) -> list[Path]:
    """收集所有音频文件。"""
    files = []
    if recursive:
        for fmt in AUDIO_FORMATS:
            files.extend(input_dir.rglob(f"*{fmt}"))
            files.extend(input_dir.rglob(f"*{fmt.upper()}"))
    else:
        for fmt in AUDIO_FORMATS:
            files.extend(input_dir.glob(f"*{fmt}"))
            files.extend(input_dir.glob(f"*{fmt.upper()}"))
    return sorted(set(files))


def process_single(audio_path: Path, output_path: Path, transcribe_func) -> bool:
    """处理单个音频文件。"""
    # 如果不是 WAV 格式，先转换
    wav_path = audio_path
    need_convert = audio_path.suffix.lower() != '.wav'

    if need_convert:
        import tempfile
        wav_path = Path(tempfile.mktemp(suffix=".wav"))
        print(f"  转换格式 -> WAV...")
        if not convert_to_wav(audio_path, wav_path):
            print(f"  [失败] 格式转换失败")
            return False

    # 语音识别
    print(f"  正在识别...")
    try:
        text = transcribe_func(wav_path)
    except Exception as e:
        print(f"  [失败] 识别出错: {e}")
        if need_convert and wav_path.exists():
            wav_path.unlink()
        return False

    # 写入文本文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# {audio_path.stem}\n\n")
        f.write(f"> - 来源: `{audio_path.name}`\n")
        f.write(f"> - 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> - 模型: `Qwen3-ASR-0.6B`\n")
        f.write("\n---\n\n")
        f.write(text)
        f.write("\n")

    # 清理临时文件
    if need_convert and wav_path.exists():
        wav_path.unlink()

    return True


def main():
    parser = argparse.ArgumentParser(
        description="音频转文本 — 使用 ASR 模型识别语音",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python audio_to_text.py input.wav                           # 默认 Qwen3-ASR
  python audio_to_text.py input.mp3 --model sensevoice        # SenseVoice
  python audio_to_text.py ./recordings/ --recursive            # 批量处理
  python audio_to_text.py input.wav --output transcript.md     # 指定输出
  python audio_to_text.py --check                              # 检查环境
        """,
    )
    parser.add_argument("input", help="输入音频文件或目录")
    parser.add_argument("-o", "--output", default=None, help="输出文本路径（单文件模式）")
    parser.add_argument("--outdir", default="./output", help="输出目录（批量模式） [默认: ./output]")
    parser.add_argument("-m", "--model", default="qwen3asr", choices=ASR_MODELS,
                        help=f"ASR 模型 [默认: qwen3asr]")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="递归扫描子文件夹")
    parser.add_argument("--check", action="store_true",
                        help="仅检查环境")
    parser.add_argument("-f", "--force", action="store_true",
                        help="强制覆盖已有文件")
    args = parser.parse_args()

    # 检查环境
    print("检查环境...")
    has_ffmpeg = check_ffmpeg()
    try:
        if args.model in ["qwen3asr", "sensevoice", "paraformer"]:
            import funasr
            print(f"[OK] funasr 已安装")
        elif args.model == "whisper":
            import whisper
            print(f"[OK] openai-whisper 已安装")
    except ImportError:
        print(f"[错误] 未安装依赖，请运行: pip install funasr torch torchaudio")
        if args.check:
            sys.exit(1)
        return

    if not has_ffmpeg:
        print("[警告] ffmpeg 未找到，仅支持 WAV 格式")

    if args.check:
        print("环境检查通过！")
        return

    input_path = Path(args.input).resolve()

    # ── 单文件模式 ──
    if input_path.is_file():
        outdir = Path(args.outdir).resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        output_path = Path(args.output) if args.output else outdir / f"{input_path.stem}.md"
        print(f"\n输入: {input_path}")
        print(f"输出: {output_path}")
        print(f"模型: {args.model}\n")

        transcribe_func = load_asr_model(args.model)
        print()

        start = time.time()
        if process_single(input_path, output_path, transcribe_func):
            elapsed = time.time() - start
            print(f"\n[完成] -> {output_path} ({elapsed:.1f}s)")
        else:
            print(f"\n[失败]")
            sys.exit(1)

    # ── 批量模式 ──
    elif input_path.is_dir():
        files = collect_audio(input_path, args.recursive)
        if not files:
            print(f"\n[提示] 在 {input_path} 中未找到音频文件")
            return

        outdir = Path(args.outdir) if args.outdir else input_path / "output_text"
        outdir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'=' * 60}")
        print(f"  音频转文本工具 (Qwen3-ASR-0.6B)")
        print(f"{'=' * 60}")
        print(f"  输入目录: {input_path}")
        print(f"  输出目录: {outdir}")
        print(f"  音频数量: {len(files)}")
        print(f"{'=' * 60}\n")

        transcribe_func = load_asr_model(args.model)
        print()

        success = 0
        skipped = 0
        failed = 0
        total_start = time.time()

        for i, audio in enumerate(files, 1):
            try:
                rel_path = audio.relative_to(input_path)
            except ValueError:
                rel_path = audio.name

            if args.recursive:
                output_path = outdir / rel_path.with_suffix('.md')
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_path = outdir / f"{audio.stem}.md"

            print(f"[{i}/{len(files)}] {rel_path}")

            if output_path.exists() and not args.force:
                print(f"  [跳过] {output_path.name} 已存在")
                skipped += 1
                continue

            start = time.time()
            if process_single(audio, output_path, transcribe_func):
                elapsed = time.time() - start
                print(f"  [完成] -> {output_path.name} ({elapsed:.1f}s)")
                success += 1
            else:
                failed += 1

        total_elapsed = time.time() - total_start
        print(f"\n{'=' * 60}")
        print(f"  处理完成!")
        print(f"  成功: {success}  跳过: {skipped}  失败: {failed}")
        print(f"  总耗时: {total_elapsed:.1f}s")
        print(f"  输出目录: {outdir}")
        print(f"{'=' * 60}")

    else:
        print(f"[错误] 输入路径不存在: {input_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
