#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_video.py — 视频下载工具

支持 B站、YouTube 等主流视频网站，基于 yt-dlp。

用法:
    python download_video.py "BVxxxxxxxxxx"                         # 下载 B站视频
    python download_video.py "https://www.bilibili.com/video/BVxx"  # 完整链接
    python download_video.py "https://www.youtube.com/watch?v=xxx"  # YouTube
    python download_video.py "BVxx" --output ./videos/               # 指定输出目录
    python download_video.py "BVxx" --audio-only                     # 仅下载音频
    python download_video.py "BVxx" --transcribe                     # 下载后自动转文字

前置条件:
    pip install yt-dlp
    pip install funasr torch torchaudio  (仅 --transcribe 需要)

支持的网站:
    B站 (bilibili.com)、YouTube、抖音、西瓜视频、爱奇艺、优酷 等
    完整列表: https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md

python download_video.py "https://www.bilibili.com/video/BV1PpbZ6yEdZ/?spm_id_from=333.337.search-card.all.click&vd_source=31718b165e8ad5f16162c06a62707fe6" --cookies cookies.txt --quality 720p

python download_video.py "https://www.bilibili.com/video/BV1ee8N6JEJ7/?spm_id_from=333.1007.top_right_bar_window_history.content.click" --cookies cookies.txt --quality 720p

"""

import subprocess
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime

# ── 默认配置 ──────────────────────────────────────────────────────
DEFAULT_OUTPUT = "./download"
DEFAULT_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
DEFAULT_COOKIES = None  # 填入 cookies 文件路径，如 "E:/cookies.txt"


def check_ytdlp() -> bool:
    """检查 yt-dlp 是否可用。"""
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("[错误] 未找到 yt-dlp，请先安装: pip install yt-dlp")
        return False


def get_video_info(url: str) -> dict | None:
    """获取视频信息（标题、时长等）。"""
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return None
        import json
        return json.loads(result.stdout)
    except Exception:
        return None


def download_video(url: str, output_dir: Path, audio_only: bool = False,
                   quality: str = "best", cookies: str = None) -> Path | None:
    """下载视频，返回下载文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 构建命令
    cmd = ["yt-dlp"]

    # 输出模板
    cmd += ["-o", str(output_dir / "%(title)s.%(ext)s")]

    # 画质
    if audio_only:
        cmd += ["-x", "--audio-format", "mp3"]
    else:
        if quality == "best":
            cmd += ["-f", DEFAULT_FORMAT]
        elif quality == "720p":
            cmd += ["-f", "bestvideo[height<=720]+bestaudio/best[height<=720]"]
        elif quality == "1080p":
            cmd += ["-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"]
        elif quality == "480p":
            cmd += ["-f", "bestvideo[height<=480]+bestaudio/best[height<=480]"]

    # 使用 ffmpeg 作为音频下载器（绕过 B站音频 CDN 限速）
    cmd += ["--downloader", "ffmpeg"]

    # Cookies
    if cookies:
        cmd += ["--cookies", cookies]

    # 其他选项
    cmd += ["--no-playlist", "--merge-output-format", "mp4",
            "--socket-timeout", "60", "--geo-bypass", url]

    print(f"  正在下载...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

    if result.returncode != 0:
        print(f"  [失败] {result.stderr.strip()}")
        return None

    # 查找下载的文件
    for f in sorted(output_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix in ['.mp4', '.mkv', '.mp3', '.m4a', '.webm']:
            return f

    return None


def transcribe_video(video_path: Path, output_path: Path, model: str = "qwen3asr") -> bool:
    """调用 video_to_text.py 进行语音识别。"""
    script_dir = Path(__file__).resolve().parent
    video_to_text = script_dir / "video_to_text.py"

    if not video_to_text.exists():
        print(f"  [错误] 未找到 video_to_text.py")
        return False

    cmd = [sys.executable, str(video_to_text), str(video_path),
           "-o", str(output_path), "--model", model]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(
        description="视频下载工具 — 支持 B站、YouTube 等",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python download_video.py "https://www.bilibili.com/video/BVxx"     # 下载 B站视频
  python download_video.py "https://www.bilibili.com/video/BVxx" --cookies cookies.txt  # 带 Cookie 下载
  python download_video.py "https://www.youtube.com/watch?v=xxx"     # YouTube
  python download_video.py "https://..." -o ./videos/                 # 指定输出目录
  python download_video.py "https://..." --quality 720p               # 指定画质
  python download_video.py "https://..." --audio-only                 # 仅下载音频
  python download_video.py "https://..." --transcribe                 # 下载后自动转文字

注意: 必须传入完整 URL (https://...)，不支持纯 BV 号
        """,
    )
    parser.add_argument("url", help="视频完整链接 (必须是 https:// 开头的完整 URL，不支持纯 BV 号)")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help=f"输出目录 [默认: {DEFAULT_OUTPUT}]")
    parser.add_argument("-q", "--quality", default="best",
                        choices=["best", "1080p", "720p", "480p"],
                        help="视频画质 [默认: best]")
    parser.add_argument("--audio-only", action="store_true",
                        help="仅下载音频 (MP3)")
    parser.add_argument("--transcribe", action="store_true",
                        help="下载后自动转文字 (需要 funasr)")
    parser.add_argument("--model", default="qwen3asr",
                        choices=["qwen3asr", "sensevoice", "paraformer", "whisper"],
                        help="ASR 模型 [默认: qwen3asr]")
    parser.add_argument("--cookies", default=None,
                        help="Cookies 文件路径 (用于需要登录的视频)")
    parser.add_argument("--check", action="store_true",
                        help="仅检查环境")
    args = parser.parse_args()

    # 检查环境
    print("检查环境...")
    if not check_ytdlp():
        if args.check:
            sys.exit(1)
        return
    print("[OK] yt-dlp 可用")

    if args.transcribe:
        try:
            import funasr
            print("[OK] funasr 可用")
        except ImportError:
            print("[警告] 未安装 funasr，无法自动转文字")
            print("  安装命令: pip install funasr torch torchaudio")

    if args.check:
        print("环境检查通过！")
        return

    # 处理 URL
    url = args.url.strip()
    if not url.startswith("http"):
        print(f"[错误] 请输入完整的 URL (以 https:// 开头)")
        print(f"  示例: python download_video.py \"https://www.bilibili.com/video/BVxx\"")
        sys.exit(1)

    # 获取视频信息
    print(f"\n{'=' * 60}")
    print(f"  视频下载工具")
    print(f"{'=' * 60}")
    print(f"  链接: {url}")
    print(f"  画质: {args.quality}")
    print(f"  输出: {Path(args.output).resolve()}")
    if args.audio_only:
        print(f"  模式: 仅音频")
    if args.transcribe:
        print(f"  转文字: 是 (模型: {args.model})")
    print(f"{'=' * 60}")

    info = get_video_info(url)
    if info:
        title = info.get("title", "未知")
        duration = info.get("duration", 0)
        uploader = info.get("uploader", "未知")
        print(f"\n  标题: {title}")
        print(f"  UP主: {uploader}")
        print(f"  时长: {duration // 60}分{duration % 60}秒")
    print()

    # 下载
    output_dir = Path(args.output).resolve()
    start = time.time()

    downloaded = download_video(
        url, output_dir,
        audio_only=args.audio_only,
        quality=args.quality,
        cookies=args.cookies or DEFAULT_COOKIES
    )

    elapsed = time.time() - start

    if not downloaded:
        print(f"\n[失败] 下载失败")
        sys.exit(1)

    print(f"\n[完成] 下载成功 ({elapsed:.1f}s)")
    print(f"  文件: {downloaded}")

    # 自动转文字
    if args.transcribe and not args.audio_only:
        text_path = downloaded.with_suffix('.md')
        print(f"\n{'=' * 60}")
        print(f"  开始语音转文字...")
        print(f"{'=' * 60}")

        if args.model in ["qwen3asr", "sensevoice", "paraformer", "whisper"]:
            script_dir = Path(__file__).resolve().parent
            video_to_text = script_dir / "video_to_text.py"

            if video_to_text.exists():
                cmd = [sys.executable, str(video_to_text), str(downloaded),
                       "-o", str(text_path), "--model", args.model]
                subprocess.run(cmd)
            else:
                print("  [错误] 未找到 video_to_text.py，跳过转文字")
        else:
            print("  [错误] 未知 ASR 模型，跳过转文字")

    elif args.transcribe and args.audio_only:
        # 音频文件直接用 audio_to_text.py
        text_path = downloaded.with_suffix('.md')
        print(f"\n{'=' * 60}")
        print(f"  开始语音转文字...")
        print(f"{'=' * 60}")

        script_dir = Path(__file__).resolve().parent
        audio_to_text = script_dir / "audio_to_text.py"

        if audio_to_text.exists():
            cmd = [sys.executable, str(audio_to_text), str(downloaded),
                   "-o", str(text_path), "--model", args.model]
            subprocess.run(cmd)
        else:
            print("  [错误] 未找到 audio_to_text.py，跳过转文字")


if __name__ == "__main__":
    main()
