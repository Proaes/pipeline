#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_to_text.py — 视频语音转文本工具

从视频中提取音频，调用 ASR 模型识别语音，输出文本文件。

用法:
    python video_to_text.py input.mp4                        # 默认: SenseVoice
    python video_to_text.py input.mp4 --model qwen3asr       # 使用 Qwen3-ASR
    python video_to_text.py input.mp4 --model whisper         # 使用 Whisper
    python video_to_text.py ./videos/ --recursive              # 批量处理文件夹

前置条件:
    pip install funasr torch torchaudio
    pip install openai-whisper  (仅 Whisper 需要)
    ffmpeg 需要在 PATH 中

支持的视频格式:
    .mp4, .mkv, .avi, .mov, .flv, .wmv, .webm, .ts

支持的 ASR 模型:
    sensevoice  — SenseVoice-Small (默认, 最轻量, 中文优秀)
    qwen3asr    — Qwen3-ASR-0.6B (最新, 中文最强)
    paraformer  — FunASR Paraformer (速度极快, 带标点时间戳)
    whisper     — Whisper-large-v3 (OpenAI, 多语言)
"""

import subprocess
import sys
import os
import argparse
import time
import tempfile
from pathlib import Path
from datetime import datetime

# ── 支持的格式 ────────────────────────────────────────────────────
VIDEO_FORMATS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.ts', '.m4v'}
ASR_MODELS = ['sensevoice', 'qwen3asr', 'paraformer', 'whisper']


def check_ffmpeg() -> bool:
    """检查 ffmpeg 是否可用。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("[错误] 未找到 ffmpeg，请安装: https://ffmpeg.org/download.html")
        return False


def extract_audio(video_path: Path, audio_path: Path) -> bool:
    """从视频中提取音频为 WAV 格式。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", "-y", str(audio_path)],
            capture_output=True, text=True, timeout=600
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("  [错误] 音频提取超时")
        return False


def get_audio_duration(audio_path: Path) -> float:
    """获取音频时长（秒）。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def split_audio(audio_path: Path, chunk_dir: Path, chunk_duration: int = 60) -> list[Path]:
    """将音频按指定时长（秒）切分成多个片段。chunk_duration <= 0 表示禁用切分。"""
    # 【D1 修复】禁用切分必须在这里就返回：否则下面 `start += chunk_duration` 恒不前进，
    # while 会死循环并无限 spawn ffmpeg（--chunk 0 在 help 里写的是"禁用"，实测会打爆机器）。
    if chunk_duration <= 0:
        return [audio_path]
    chunk_dir.mkdir(parents=True, exist_ok=True)
    duration = get_audio_duration(audio_path)
    if duration <= 0:
        return [audio_path]

    chunks = []
    start = 0
    idx = 0
    while start < duration:
        chunk_path = chunk_dir / f"chunk_{idx:04d}.wav"
        try:
            subprocess.run(
                ["ffmpeg", "-i", str(audio_path), "-ss", str(start),
                 "-t", str(chunk_duration), "-acodec", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", "-y", str(chunk_path)],
                capture_output=True, text=True, timeout=120
            )
            if chunk_path.exists() and chunk_path.stat().st_size > 0:
                chunks.append(chunk_path)
        except Exception:
            pass
        start += chunk_duration
        idx += 1

    return chunks if chunks else [audio_path]


def load_asr_model(model_name: str):
    """加载 ASR 模型，返回 (model, transcribe_func)。"""
    print(f"  正在加载模型 '{model_name}'...")

    if model_name == "sensevoice":
        from funasr import AutoModel
        model = AutoModel(model="iic/SenseVoiceSmall")
        def transcribe(audio_path):
            result = model.generate(input=str(audio_path), language="zh")
            return result[0]["text"]
        return model, transcribe

    elif model_name == "qwen3asr":
        from funasr import AutoModel
        model = AutoModel(model="Qwen/Qwen3-ASR-0.6B")
        def transcribe(audio_path):
            result = model.generate(input=str(audio_path))
            return result[0]["text"]
        return model, transcribe

    elif model_name == "paraformer":
        from funasr import AutoModel
        model = AutoModel(model="paraformer-zh", model_revision="v2.0.4",
                          vad_model="fsmn-vad", punc_model="ct-punc")
        def transcribe(audio_path):
            result = model.generate(input=str(audio_path))
            return result[0]["text"]
        return model, transcribe

    elif model_name == "whisper":
        import whisper
        model = whisper.load_model("large-v3")
        def transcribe(audio_path):
            result = model.transcribe(str(audio_path), language="zh")
            return result["text"]
        return model, transcribe

    else:
        raise ValueError(f"不支持的模型: {model_name}")


def collect_videos(input_dir: Path, recursive: bool) -> list[Path]:
    """收集所有视频文件。"""
    videos = []
    if recursive:
        for fmt in VIDEO_FORMATS:
            videos.extend(input_dir.rglob(f"*{fmt}"))
            videos.extend(input_dir.rglob(f"*{fmt.upper()}"))
    else:
        for fmt in VIDEO_FORMATS:
            videos.extend(input_dir.glob(f"*{fmt}"))
            videos.extend(input_dir.glob(f"*{fmt.upper()}"))
    return sorted(set(videos))


def process_single(video_path: Path, output_path: Path, transcribe_func,
                    keep_audio: bool, audio_dir: Path | None,
                    chunk_duration: int = 60) -> bool:
    """处理单个视频文件。"""
    # 提取音频
    if audio_dir:
        audio_path = audio_dir / f"{video_path.stem}.wav"
    else:
        audio_path = Path(tempfile.mktemp(suffix=".wav"))

    print(f"  提取音频...")
    if not extract_audio(video_path, audio_path):
        print(f"  [失败] 音频提取失败")
        return False

    # 获取音频时长
    duration = get_audio_duration(audio_path)
    print(f"  音频时长: {duration:.0f}秒 ({duration/60:.1f}分钟)")

    # 切分音频
    if chunk_duration > 0 and duration > chunk_duration + 10:
        print(f"  音频较长，按 {chunk_duration} 秒切分...")
        chunk_dir = Path(tempfile.mkdtemp(prefix="asr_chunks_"))
        chunks = split_audio(audio_path, chunk_dir, chunk_duration)
        print(f"  切分为 {len(chunks)} 段")
    else:
        chunks = [audio_path]

    # 逐段识别
    all_text = []
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            print(f"  识别第 {i}/{len(chunks)} 段...")
        else:
            print(f"  正在识别...")

        try:
            text = transcribe_func(chunk)
            all_text.append(text)
        except Exception as e:
            print(f"  [警告] 第 {i} 段识别失败: {e}")
            all_text.append(f"[第{i}段识别失败]")

    # 合并文本
    full_text = "\n".join(all_text)

    # 写入文本文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# {video_path.stem}\n\n")
        f.write(f"> - 来源: `{video_path.name}`\n")
        f.write(f"> - 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> - 模型: `{args.model if 'args' in dir() else 'unknown'}`\n")
        if chunk_duration:
            f.write(f"> - 分段: {chunk_duration}秒/段\n")
        f.write("\n---\n\n")
        f.write(full_text)
        f.write("\n")

    # 清理临时音频和分段
    if not keep_audio and audio_path.exists():
        audio_path.unlink()
    # 清理分段临时文件
    if len(chunks) > 1:
        for chunk in chunks:
            if chunk.exists() and chunk.parent.name.startswith("asr_chunks_"):
                chunk.unlink()
        try:
            chunk_dir.rmdir()
        except Exception:
            pass

    return True


def main():
    parser = argparse.ArgumentParser(
        description="视频语音转文本 — 提取音频 + ASR 识别",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python video_to_text.py input.mp4                        # 默认 SenseVoice
  python video_to_text.py input.mp4 --model qwen3asr       # Qwen3-ASR
  python video_to_text.py input.mp4 --model whisper         # Whisper
  python video_to_text.py ./videos/ --recursive              # 批量处理
  python video_to_text.py input.mp4 --keep-audio             # 保留提取的音频
  python video_to_text.py input.mp4 --chunk 30               # 每30秒切一段（适合长视频）
        """,
    )
    parser.add_argument("input", help="输入视频文件或目录")
    parser.add_argument("-o", "--output", default=None, help="输出文本路径（单文件模式）")
    parser.add_argument("--outdir", default="./output", help="输出目录（批量模式） [默认: ./output]")
    parser.add_argument("-m", "--model", default="sensevoice", choices=ASR_MODELS,
                        help=f"ASR 模型 [默认: sensevoice]")
    parser.add_argument("--chunk", type=int, default=60,
                        help="音频切分时长（秒）[默认: 60，设为 0 禁用切分]")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="递归扫描子文件夹")
    parser.add_argument("--keep-audio", action="store_true",
                        help="保留提取的音频文件")
    parser.add_argument("--audio-dir", default=None,
                        help="音频文件保存目录（默认临时目录）")
    parser.add_argument("--check", action="store_true",
                        help="仅检查环境")
    args = parser.parse_args()

    # 检查 ffmpeg
    print("检查环境...")
    if not check_ffmpeg():
        sys.exit(1)
    print("[OK] ffmpeg 可用")

    if args.check:
        # 尝试导入 ASR 库
        try:
            if args.model in ["sensevoice", "qwen3asr", "paraformer"]:
                import funasr
                print(f"[OK] funasr 已安装")
            elif args.model == "whisper":
                import whisper
                print(f"[OK] openai-whisper 已安装")
        except ImportError:
            print(f"[错误] 未安装依赖，请运行: pip install funasr")
            sys.exit(1)
        print("环境检查通过！")
        return

    input_path = Path(args.input).resolve()

    # ── 单文件模式 ──
    if input_path.is_file():
        outdir = Path(args.outdir).resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        output_path = Path(args.output) if args.output else outdir / f"{input_path.stem}.md"
        audio_dir = Path(args.audio_dir) if args.audio_dir else None
        if audio_dir:
            audio_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n输入: {input_path}")
        print(f"输出: {output_path}")
        print(f"模型: {args.model}")
        print(f"分段: {args.chunk}秒" if args.chunk > 0 else "分段: 禁用")
        print()

        _, transcribe_func = load_asr_model(args.model)
        print()

        start = time.time()
        if process_single(input_path, output_path, transcribe_func,
                          args.keep_audio, audio_dir, args.chunk):
            elapsed = time.time() - start
            print(f"\n[完成] -> {output_path} ({elapsed:.1f}s)")
        else:
            print(f"\n[失败] 处理失败")
            sys.exit(1)

    # ── 批量模式 ──
    elif input_path.is_dir():
        videos = collect_videos(input_path, args.recursive)
        if not videos:
            print(f"\n[提示] 在 {input_path} 中未找到视频文件")
            return

        outdir = Path(args.outdir) if args.outdir else input_path / "output_text"
        outdir.mkdir(parents=True, exist_ok=True)
        audio_dir = Path(args.audio_dir) if args.audio_dir else None
        if audio_dir:
            audio_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'=' * 60}")
        print(f"  视频语音转文本工具")
        print(f"{'=' * 60}")
        print(f"  输入目录: {input_path}")
        print(f"  输出目录: {outdir}")
        print(f"  模型:     {args.model}")
        print(f"  视频数量: {len(videos)}")
        print(f"{'=' * 60}\n")

        _, transcribe_func = load_asr_model(args.model)
        print()

        success = 0
        failed = 0
        total_start = time.time()

        for i, video in enumerate(videos, 1):
            try:
                rel_path = video.relative_to(input_path)
            except ValueError:
                rel_path = video.name

            if args.recursive:
                output_path = outdir / rel_path.with_suffix('.md')
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_path = outdir / f"{video.stem}.md"

            print(f"[{i}/{len(videos)}] {rel_path}")

            start = time.time()
            if process_single(video, output_path, transcribe_func,
                              args.keep_audio, audio_dir, args.chunk):
                elapsed = time.time() - start
                print(f"  [完成] -> {output_path.name} ({elapsed:.1f}s)")
                success += 1
            else:
                failed += 1

        total_elapsed = time.time() - total_start
        print(f"\n{'=' * 60}")
        print(f"  处理完成!")
        print(f"  成功: {success}  失败: {failed}")
        print(f"  总耗时: {total_elapsed:.1f}s")
        print(f"  输出目录: {outdir}")
        print(f"{'=' * 60}")

    else:
        print(f"[错误] 输入路径不存在: {input_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
