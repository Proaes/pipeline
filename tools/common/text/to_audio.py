#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text_to_audio.py — 文本转语音工具

把 Markdown / 纯文本读成音频：长文本先切句，逐句合成后拼成一整段，
音色从 pipeline/voices/<音色名>/ 取，也可以临时用一段参考音频。

合成后端（--engine）:
    genie      本地 GENIE（GPT-SoVITS 的 ONNX 引擎），纯 CPU、零显存，默认
    gptsovits  调本地 GPT-SoVITS 的 api_v2.py HTTP 服务（127.0.0.1:9880）
    sapi       Windows 自带 SAPI 语音，零依赖、不需要参考音频，只用来验证链路

音色（--voice）按以下顺序解析:
    1. --ref-audio / --ref-text（临时，不注册）
    2. pipeline/voices/<音色名>/（自己的音色库）
    3. <genie 根目录>/CharacterModels/v2ProPlus/<音色名>/（GENIE 自带预设角色）

用法:
    python tools/common/text/to_audio.py note.md -o out/note.mp3
    python tools/common/text/to_audio.py note.md --voice feibi
    python tools/common/text/to_audio.py note.md --srt          # 同时出 .srt 时间轴
    python tools/common/text/to_audio.py --list-voices
    python tools/common/text/to_audio.py --check
    python tools/common/text/to_audio.py note.md --engine sapi --format mp3
    python tools/common/text/to_audio.py note.md --dry-run
    python tools/common/text/to_audio.py note.md --trim-tail-ms 60 --tail-pad 0.6

前置条件:
    genie 引擎：D:\\mamba\\envs\\tts 环境 + D:\\tools\\genie 下的资源（见 --check）
    gptsovits 引擎：先起本地服务 python api_v2.py -a 127.0.0.1 -p 9880
    ffmpeg 需要在 PATH 中（后处理、拼接、转 mp3）
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 指向 tools/
from _lib import common  # noqa: E402

# ── 常量 ──────────────────────────────────────────────────────────
TEXT_EXTS = {".md", ".markdown", ".txt"}
ENGINES = ("genie", "gptsovits", "sapi")
FORMATS = ("mp3", "wav")

VOICES_DIR = common.PROJECT_ROOT / "voices"
DEFAULT_GENIE_ROOT = Path(r"D:\tools\genie")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9880
REF_AUDIO_NAMES = ("ref.wav", "ref.mp3", "ref.flac", "ref.m4a", "ref.ogg")

DEFAULT_RATE = 32000
DEFAULT_GAP = 0.35          # 句间静音（秒）
DEFAULT_HEAD_PAD = 0.12     # 开头静音
DEFAULT_TAIL_PAD = 0.55     # 结尾静音 —— 尾音生硬时靠它收住
DEFAULT_FADE = 0.025        # 每段首尾淡入淡出，消除爆音与突兀收尾
DEFAULT_TRIM_TAIL_MS = 0    # 裁掉结尾多少毫秒（0 = 不裁）
DEFAULT_SRT_WIDTH = 20      # .srt 每行字数上限（0 = 不换行）
DEFAULT_MAX_CHARS = 120
DEFAULT_MIN_CHARS = 6

# GENIE 预设角色 → 该角色的语言码
GENIE_CHAR_LANG = {"feibi": "zh", "mika": "jp", "thirtyseven": "en"}


class ToolError(Exception):
    """工具自身的可预期错误，直接打印 message、不吐 traceback。"""


# ── 文本预处理：Markdown → 可朗读文本 ─────────────────────────────
_FENCE_RE = re.compile(r"```.*?```", re.S)
_MATH_BLOCK_RE = re.compile(r"\$\$.*?\$\$", re.S)
_MATH_INLINE_RE = re.compile(r"\$[^$\n]+\$")
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_RE = re.compile(r"<[^>]+>")
_HR_RE = re.compile(r"^\s*([-*_]\s*){3,}\s*$", re.M)
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.M)
_EMPH_STRONG_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_EMPH_UNDER_RE = re.compile(r"__(.+?)__", re.S)
_EMPH_STAR_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.S)
_CODE_INLINE_RE = re.compile(r"`([^`]*)`")
_LINE_MARK_RE = re.compile(r"^\s{0,3}(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)", re.M)
_SENT_RE = re.compile(r"[^。！？!?；;…\n]+[。！？!?；;…]*")
_CLAUSE_RE = re.compile(r"(?<=[，,、：:])")


def md_to_speakable(text: str) -> str:
    """把 Markdown 源码洗成适合朗读的纯文本。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _FENCE_RE.sub("\n", text)          # 代码块读出来没意义
    text = _MATH_BLOCK_RE.sub(" ", text)      # 展示公式
    text = _IMG_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)          # 链接只留可见文字
    text = _HTML_RE.sub(" ", text)
    text = _HR_RE.sub("\n", text)
    text = _TABLE_SEP_RE.sub("", text)
    text = _MATH_INLINE_RE.sub(" ", text)     # 行内公式
    text = _EMPH_STRONG_RE.sub(r"\1", text)
    text = _EMPH_UNDER_RE.sub(r"\1", text)
    text = _EMPH_STAR_RE.sub(r"\1", text)
    text = _CODE_INLINE_RE.sub(r"\1", text)
    text = _LINE_MARK_RE.sub("", text)
    text = text.replace("|", "，")
    text = re.sub(r"\[[ xX]\]", " ", text)    # 待办框
    return text


def _split_long(chunk: str, max_chars: int) -> list[str]:
    """超长句按从句标点再切，仍然超长就硬切。"""
    if len(chunk) <= max_chars:
        return [chunk]
    packed, buf = [], ""
    for part in _CLAUSE_RE.split(chunk):
        if not part:
            continue
        if buf and len(buf) + len(part) > max_chars:
            packed.append(buf)
            buf = part
        else:
            buf += part
    if buf:
        packed.append(buf)
    final = []
    for piece in packed:
        while len(piece) > max_chars:
            final.append(piece[:max_chars])
            piece = piece[max_chars:]
        if piece:
            final.append(piece)
    return final


def split_sentences(text: str, max_chars: int = DEFAULT_MAX_CHARS,
                    min_chars: int = DEFAULT_MIN_CHARS) -> list[str]:
    """按标点切句；碎句并进前一句，超长句再细分。

    切句不只是为了稳，也是尾音质量的关键：整段一次生成时模型只在末尾收一次尾，
    切句后每句都收一次尾，再配上段间静音，听感明显更自然。
    """
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        buf = ""
        for piece in _SENT_RE.findall(para):
            piece = piece.strip()
            if not piece:
                continue
            buf += piece
            if len(buf) >= min_chars:
                chunks.append(buf)
                buf = ""
        if buf:
            if chunks:
                chunks[-1] += buf
            else:
                chunks.append(buf)
    out: list[str] = []
    for c in chunks:
        out.extend(_split_long(c, max_chars))
    return [c.strip() for c in out if c.strip()]


def read_text(path: Path) -> str:
    """读 UTF-8 文本，容忍 BOM。"""
    return path.read_text(encoding="utf-8-sig")


# ── 音色解析 ──────────────────────────────────────────────────────
def registry_voices() -> list[str]:
    """列出 voices/ 下的音色名（下划线开头的是模板）。"""
    if not VOICES_DIR.is_dir():
        return []
    return sorted(p.name for p in VOICES_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def genie_root(opts: argparse.Namespace) -> Path:
    """GENIE 资源根目录：命令行 > 环境变量 > 默认。"""
    if opts.genie_root:
        return Path(opts.genie_root)
    env = os.environ.get("GENIE_ROOT")
    return Path(env) if env else DEFAULT_GENIE_ROOT


def builtin_characters(root: Path) -> list[str]:
    """GENIE 自带的预设角色。"""
    d = root / "CharacterModels" / "v2ProPlus"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and (p / "tts_models").is_dir())


def character_language(name: str, char_dir: Path) -> str:
    """推断角色语言：先查表，再从参考音频文件名前缀猜。"""
    if name in GENIE_CHAR_LANG:
        return GENIE_CHAR_LANG[name]
    pj = char_dir / "prompt_wav.json"
    if pj.is_file():
        try:
            cfg = json.loads(read_text(pj))
            first = cfg.get("Normal") or next(iter(cfg.values()))
            wav = first.get("wav", "")
            if wav.startswith("zh_"):
                return "zh"
            if wav.startswith("En_"):
                return "en"
        except (json.JSONDecodeError, StopIteration, AttributeError):
            pass
    return "zh"


def load_character_dir(char_dir: Path, name: str) -> dict:
    """把 GENIE 角色目录解析成音色描述。"""
    onnx = char_dir / "tts_models"
    if not onnx.is_dir():
        raise ToolError(f"角色 '{name}' 缺少 tts_models/ 目录：{char_dir}")

    wav, text = None, ""
    pj = char_dir / "prompt_wav.json"
    if pj.is_file():
        cfg = json.loads(read_text(pj))
        # 有 Normal 就用 Normal，否则取第一个情绪
        first = cfg.get("Normal") or next(iter(cfg.values()))
        wav = char_dir / "prompt_wav" / first["wav"]
        text = first.get("text", "")
    if wav is None or not wav.is_file():
        cands = sorted((char_dir / "prompt_wav").glob("*.wav")) if (char_dir / "prompt_wav").is_dir() else []
        if not cands:
            raise ToolError(f"角色 '{name}' 找不到参考音频（prompt_wav/）")
        wav = cands[0]

    return {"name": name, "kind": "genie", "genie_onnx_dir": onnx,
            "prompt_wav": wav, "prompt_text": text.strip(),
            "language": character_language(name, char_dir)}


def default_onnx_dir(root: Path) -> Path | None:
    """取任一预设角色的 tts_models 当基座。

    注册自己的音色时复用它即可：说话人身份不是存在这些权重里的，而是运行时由
    「说话人验证模型」从 ref.wav 算出 sv_emb、再经 prompt_encoder 融进 global_emb
    喂给声码器（见 genie_tts/Audio/ReferenceAudio.py 的 update_global_emb）。
    所以换 ref.wav 就是换音色。
    """
    chardir = root / "CharacterModels" / "v2ProPlus"
    if not chardir.is_dir():
        return None
    for p in sorted(chardir.iterdir()):
        if (p / "tts_models").is_dir():
            return p / "tts_models"
    return None


def resolve_voice(opts: argparse.Namespace) -> dict:
    """按 --ref-audio > voices/<名> > GENIE 内置角色 的顺序解析音色。"""
    # 1) 命令行直接给的参考音频
    if opts.ref_audio:
        wav = Path(opts.ref_audio).resolve()
        if not wav.is_file():
            raise ToolError(f"参考音频不存在: {wav}")
        return {"name": "inline", "kind": "inline",
                "genie_onnx_dir": default_onnx_dir(genie_root(opts)),
                "prompt_wav": wav, "prompt_text": (opts.ref_text or "").strip(),
                "language": opts.prompt_lang}

    if not opts.voice:
        return {"name": None, "kind": "none", "genie_onnx_dir": None,
                "prompt_wav": None, "prompt_text": "", "language": opts.prompt_lang}

    name = opts.voice

    # 2) 自己的音色库
    vdir = VOICES_DIR / name
    if vdir.is_dir():
        meta = {}
        if (vdir / "meta.json").is_file():
            try:
                meta = json.loads(read_text(vdir / "meta.json"))
            except json.JSONDecodeError as exc:
                raise ToolError(f"音色 '{name}' 的 meta.json 不是合法 JSON: {exc}") from exc

        # 指向外部 GENIE 角色时，直接复用角色目录的解析
        if meta.get("genie_char_dir"):
            return load_character_dir(Path(meta["genie_char_dir"]), name)

        ref = next((vdir / n for n in REF_AUDIO_NAMES if (vdir / n).is_file()), None)
        if ref is None:
            raise ToolError(f"音色 '{name}' 里找不到参考音频"
                            f"（把 wav 命名为 {'/'.join(REF_AUDIO_NAMES)} 放进该目录）")
        prompt_text = meta.get("prompt_text", "")
        if not prompt_text and (vdir / "ref.txt").is_file():
            prompt_text = read_text(vdir / "ref.txt").strip()
        onnx = (Path(meta["genie_onnx_dir"]) if meta.get("genie_onnx_dir")
                else default_onnx_dir(genie_root(opts)))
        return {"name": name, "kind": "registry", "genie_onnx_dir": onnx,
                "prompt_wav": ref, "prompt_text": prompt_text.strip(),
                # language / ref_lang = **参考音频**的语种（与 --text-lang 相互独立）
                "language": meta.get("ref_lang") or meta.get("language", opts.prompt_lang),
                "meta": meta}

    # 3) GENIE 自带角色
    root = genie_root(opts)
    char_dir = root / "CharacterModels" / "v2ProPlus" / name
    if char_dir.is_dir():
        return load_character_dir(char_dir, name)

    avail = registry_voices() + builtin_characters(root)
    tip = ("，可用音色：" + "、".join(avail)) if avail else "（没有可用音色）"
    raise ToolError(f"找不到音色 '{name}'{tip}")


# ── 后端 1：GENIE（进程内，纯 CPU） ───────────────────────────────
class GenieEngine:
    """GENIE 只加载一次角色，之后逐句复用。"""

    def __init__(self, opts: argparse.Namespace, voice: dict):
        self.opts = opts
        self.voice = voice
        self.genie = None
        self.name = "voice"

    def prepare(self) -> None:
        root = genie_root(self.opts)
        data = root / "GenieData"
        if not data.is_dir():
            raise ToolError(
                f"找不到 GENIE 资源目录: {data}\n"
                f"    用 --genie-root 指定，或设环境变量 GENIE_ROOT"
            )
        onnx = self.voice.get("genie_onnx_dir")
        if onnx is None:
            raise ToolError(
                "找不到可用的 ONNX 基座模型目录。\n"
                f"    检查 {root / 'CharacterModels' / 'v2ProPlus'} 下是否有预设角色"
                f"（feibi / mika / thirtyseven），或用 --genie-root 指定资源根目录"
            )
        # 必须在 import genie_tts 之前设置，否则它会去连 HuggingFace
        os.environ["GENIE_DATA_DIR"] = str(data)

        # genie_tts/Internal.py 在模块顶层调 logging.basicConfig()（默认输出到 stderr），
        # 于是它的正常信息在 pipeline GUI 里会被当成报错标红。basicConfig 只在根
        # logger 没有 handler 时才生效，所以这里先占位，把日志改到 stdout。
        import logging  # noqa: PLC0415
        logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                            format="  %(message)s", force=True)

        t0 = time.time()
        import genie_tts as genie  # noqa: PLC0415
        self.genie = genie
        print(f"  import genie_tts     {time.time() - t0:6.2f}s")

        # GENIE 内部按角色名索引模型，这里清成 ASCII 安全名；
        # 纯中文名清洗后会全是下划线，彼此撞名，所以补一段原名哈希。
        safe = re.sub(r"[^0-9A-Za-z_]", "_", self.voice.get("name") or "voice")
        if not re.search(r"[0-9A-Za-z]", safe):
            safe = "v_" + hashlib.md5(
                (self.voice.get("name") or "voice").encode("utf-8")).hexdigest()[:8]
        self.name = safe

        t0 = time.time()
        # load_character 的 language = **待合成文本**的语种（决定目标文本走哪套 G2P）；
        # set_reference_audio 的 language = **参考音频**的语种。两者独立，
        # 所以「日语音色 + 中文文本」这种跨语种克隆是原生支持的。
        genie.load_character(character_name=self.name,
                             onnx_model_dir=str(onnx),
                             language=self.opts.text_lang)
        ref_lang = self.voice.get("language") or self.opts.prompt_lang
        print(f"  load_character       {time.time() - t0:6.2f}s  "
              f"({self.name}；目标文本 {self.opts.text_lang} / 参考音频 {ref_lang})")

        if self.voice.get("prompt_wav"):
            t0 = time.time()
            genie.set_reference_audio(character_name=self.name,
                                      audio_path=str(self.voice["prompt_wav"]),
                                      audio_text=self.voice.get("prompt_text", ""),
                                      language=ref_lang)
            print(f"  set_reference_audio  {time.time() - t0:6.2f}s")

    def synth(self, text: str, out_wav: Path) -> None:
        # 切句已在本工具做过，这里不再让 GENIE 二次切分
        self.genie.tts(character_name=self.name, text=text,
                       play=False, split_sentence=False, save_path=str(out_wav))
        if not out_wav.is_file() or out_wav.stat().st_size == 0:
            raise ToolError("GENIE 没有产出音频")


# ── 后端 2：GPT-SoVITS HTTP 服务 ──────────────────────────────────
def service_alive(host: str, port: int, timeout: float = 1.5) -> bool:
    """端口能连上就算服务在跑。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def synth_gptsovits(text: str, out_wav: Path, opts: argparse.Namespace,
                    voice: dict) -> None:
    """POST /tts，拿回 wav 字节写盘。"""
    ref_audio = voice.get("prompt_wav")
    if not ref_audio:
        raise ToolError("gptsovits 引擎需要参考音频（--ref-audio 或 --voice）")
    body = {
        "text": text,
        "text_lang": opts.text_lang,
        "ref_audio_path": str(Path(ref_audio).resolve()),
        "prompt_text": voice.get("prompt_text", ""),
        "prompt_lang": voice.get("language", opts.prompt_lang),
        "text_split_method": "cut0",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": opts.speed,
        "seed": opts.seed,
        "parallel_infer": True,
        "repetition_penalty": 1.35,
    }
    req = urllib.request.Request(
        f"http://{opts.host}:{opts.port}/tts",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=opts.timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise ToolError(f"服务返回 HTTP {exc.code}: "
                        f"{exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(
            f"连不上 GPT-SoVITS 服务 {opts.host}:{opts.port}（{exc.reason}）。\n"
            f"    先起服务：python api_v2.py -a {opts.host} -p {opts.port}"
        ) from exc
    if not data:
        raise ToolError("服务返回了空音频")
    out_wav.write_bytes(data)


# ── 后端 3：Windows SAPI（零依赖，验证链路用） ────────────────────
def _ps_quote(s: str) -> str:
    """PowerShell 单引号字符串字面量（$ 不参与插值，单引号翻倍转义）。"""
    return "'" + s.replace("'", "''") + "'"


def _ps_run(script: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """走 -EncodedCommand，绕开命令行引号地狱。"""
    head = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
    encoded = base64.b64encode((head + script).encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def sapi_voices() -> list[str]:
    """列出本机 SAPI 已安装语音。"""
    script = ("Add-Type -AssemblyName System.Speech\n"
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
              "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }\n"
              "$s.Dispose()\n")
    try:
        r = _ps_run(script, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()] if r.returncode == 0 else []


def synth_sapi(text: str, out_wav: Path, opts: argparse.Namespace,
               voice: dict) -> None:
    """用 SAPI 合成；音色来自 --sapi-voice 或音色目录 meta.json 的 sapi_voice。"""
    name = opts.sapi_voice or voice.get("meta", {}).get("sapi_voice") or ""
    script = "Add-Type -AssemblyName System.Speech\n"
    script += "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
    if name:
        script += f"$s.SelectVoice({_ps_quote(name)})\n"
    script += f"$s.Rate = {int(opts.sapi_rate)}\n"
    script += f"$s.SetOutputToWaveFile({_ps_quote(str(out_wav))})\n"
    script += f"$s.Speak({_ps_quote(text)})\n"
    script += "$s.Dispose()\n"
    try:
        r = _ps_run(script, timeout=opts.timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError("SAPI 合成超时") from exc
    except OSError as exc:
        raise ToolError(f"调 PowerShell 失败: {exc}") from exc
    if r.returncode != 0 or not out_wav.is_file():
        raise ToolError(f"SAPI 合成失败: {(r.stderr or '').strip()[:300]}")


# ── 音频后处理 ────────────────────────────────────────────────────
def ffmpeg(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                          capture_output=True, text=True, timeout=timeout)


def check_ffmpeg() -> bool:
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def probe_duration(wav: Path) -> float:
    """取时长（秒）。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(wav)],
                           capture_output=True, text=True, timeout=60)
        return float((r.stdout or "0").strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def fmt_ts(sec: float) -> str:
    """SRT 时间戳：HH:MM:SS,mmm"""
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap_subtitle(text: str, width: int) -> list[str]:
    """字幕换行；width <= 0 表示不换行。"""
    text = text.strip()
    if width <= 0 or len(text) <= width:
        return [text]
    return [text[i:i + width] for i in range(0, len(text), width)]


def shape_chunk(src: Path, dst: Path, opts: argparse.Namespace, rate: int) -> None:
    """单段收尾处理：可选裁尾 + 首尾淡入淡出 + 统一采样率。

    尾音生硬是这类模型的通病（GENIE 按 token 边界硬截后直接过声码器，
    不做任何收尾），所以这里补上淡出；若尾音整段是杂音，用 --trim-tail-ms 裁掉。
    """
    dur = probe_duration(src)
    if dur <= 0:
        raise ToolError(f"读不出音频时长: {src}")
    keep = max(0.05, dur - opts.trim_tail_ms / 1000.0)

    filters = []
    fade = opts.fade
    if fade > 0:
        if keep <= 2 * fade:          # 太短就只在开头淡入
            filters.append(f"afade=t=in:st=0:d={fade:.3f}")
        else:
            filters.append(f"afade=t=in:st=0:d={fade:.3f}")
            filters.append(f"afade=t=out:st={keep - fade:.3f}:d={fade:.3f}")

    cmd = ["-i", str(src), "-t", f"{keep:.3f}"]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += ["-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    r = ffmpeg(cmd)
    if r.returncode != 0:
        raise ToolError(f"后处理失败({src.name}): {(r.stderr or '').strip()[:200]}")


def make_silence(seconds: float, rate: int, dst: Path) -> None:
    r = ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
                "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(dst)])
    if r.returncode != 0:
        raise ToolError(f"生成静音段失败: {(r.stderr or '').strip()[:200]}")


def assemble(parts: list[Path], out_wav: Path, workdir: Path, rate: int) -> None:
    """按顺序拼接（含静音段），整体重编码为 PCM。"""
    listfile = workdir / "_concat.txt"
    lines = []
    for p in parts:
        safe = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    listfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listfile),
                "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)])
    if r.returncode != 0:
        raise ToolError(f"拼接失败: {(r.stderr or '').strip()[:300]}")


def encode_audio(src_wav: Path, dst: Path, fmt: str) -> None:
    """wav 直接复制，mp3 走 libmp3lame。"""
    common.ensure_parent(dst)
    if fmt == "wav":
        if src_wav.resolve() != dst.resolve():
            shutil.copyfile(src_wav, dst)
        return
    r = ffmpeg(["-i", str(src_wav), "-codec:a", "libmp3lame", "-q:a", "2", str(dst)])
    if r.returncode != 0:
        raise ToolError(f"转 mp3 失败: {(r.stderr or '').strip()[:300]}")


# ── 环境检查 ──────────────────────────────────────────────────────
def env_check(opts: argparse.Namespace) -> None:
    root = genie_root(opts)
    print("环境检查")
    print(f"  ffmpeg          : {'可用' if check_ffmpeg() else '缺失（只能出 wav，不能转 mp3 / 后处理）'}")
    print(f"  GENIE 根目录    : {root}  {'存在' if root.is_dir() else '不存在'}")
    data = root / "GenieData"
    print(f"  GENIE_DATA_DIR  : {data}  {'存在' if data.is_dir() else '不存在'}")
    chars = builtin_characters(root)
    print(f"  GENIE 预设角色  : {'、'.join(chars) if chars else '（无）'}")
    print(f"  音色库          : {VOICES_DIR}")
    rv = registry_voices()
    print(f"  已注册音色      : {'、'.join(rv) if rv else '（空）'}")
    alive = service_alive(opts.host, opts.port)
    print(f"  GPT-SoVITS 服务 : {opts.host}:{opts.port} {'在跑' if alive else '未启动'}")
    sv = sapi_voices()
    print(f"  SAPI 语音       : {'、'.join(sv) if sv else '查询失败或无'}")

    # 注意：导入 genie_tts 时它会读 GENIE_DATA_DIR；若该目录不存在，
    # 它会在模块顶层直接 input() 问"要不要联网下载"，非交互下就是 EOFError。
    # 所以必须先设好环境变量、且在资源缺失时干脆不导入。
    if not data.is_dir():
        print("  genie_tts       : 跳过导入（资源目录不存在，导入会触发联网下载提示）")
        return
    os.environ["GENIE_DATA_DIR"] = str(data)
    try:
        import genie_tts  # noqa: F401
        print(f"  genie_tts       : 已安装（{sys.executable}）")
    except ImportError:
        print(f"  genie_tts       : 未安装 —— 当前解释器 {sys.executable}")


# ── 主流程 ────────────────────────────────────────────────────────
def synth_all(chunks: list[str], opts: argparse.Namespace, voice: dict,
              workdir: Path) -> list[tuple[str, Path]]:
    """逐句合成，单句失败重试一次。返回 [(句子, 原始 wav)]，失败的不在列表里。"""
    engine = None
    if opts.engine == "genie":
        engine = GenieEngine(opts, voice)
        engine.prepare()
    elif opts.engine == "gptsovits" and not service_alive(opts.host, opts.port):
        raise ToolError(f"连不上 GPT-SoVITS 服务 {opts.host}:{opts.port}；"
                        f"先起 python api_v2.py -a {opts.host} -p {opts.port}")

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    wavs: list[tuple[str, Path]] = []
    failed = 0
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        out = raw_dir / f"{i:04d}.wav"
        preview = chunk if len(chunk) <= 26 else chunk[:26] + "…"
        print(f"  [{i}/{total}] {preview}")
        t0 = time.time()

        for attempt in (1, 2):
            try:
                if opts.engine == "genie":
                    engine.synth(chunk, out)
                elif opts.engine == "gptsovits":
                    synth_gptsovits(chunk, out, opts, voice)
                else:
                    synth_sapi(chunk, out, opts, voice)
                break
            except ToolError as exc:
                if attempt == 1:
                    print(f"        重试（{str(exc).splitlines()[0][:70]}）")
                    time.sleep(1.0)
                else:
                    print(f"        [失败] {str(exc)[:200]}")
                    out = None
        if out is not None and out.is_file():
            # 兜底：语种标错（例如日语参考文本没给 --prompt-lang jp）时，
            # 模型会静默产出一段极短的垃圾音频。快于 15 字/秒 就不可能正常。
            dur = probe_duration(out)
            if dur < max(0.3, len(chunk) / 15.0):
                print(f"        [警告] 只合成出 {dur:.2f}s（{len(chunk)} 字），短得离谱 ——"
                      f" 多半是参考音频的语种或逐字文本不对，检查 --prompt-lang / meta 的 ref_lang")
            wavs.append((chunk, out))
            print(f"        {time.time() - t0:5.2f}s")
        else:
            failed += 1

    if failed:
        print(f"  [警告] {failed}/{total} 句合成失败，已跳过")
    return wavs


def process_one(src: Path, out_path: Path, opts: argparse.Namespace,
                voice: dict) -> bool:
    """处理单个文本文件。"""
    print(f"  读取: {src}")
    speakable = md_to_speakable(read_text(src))
    chunks = split_sentences(speakable, opts.max_chars, opts.min_chars)
    if not chunks:
        print("  [跳过] 洗掉 Markdown 标记后没有可朗读的文字")
        return False
    print(f"  切句: {len(chunks)} 句，共 {sum(len(c) for c in chunks)} 字")

    if opts.dry_run:
        for i, c in enumerate(chunks, 1):
            print(f"    {i:>3}. {c}")
        return True

    # 临时工作目录：tempfile.mkdtemp 建出来的是 0o700，在沙箱里于其下再建子目录
    # 会被拒（WinError 5 拒绝访问），因此允许用环境变量指定一个普通权限的目录。
    _tmp_root = os.environ.get("PIPELINE_TTS_TMPDIR")
    if _tmp_root:
        _root = Path(_tmp_root)
        _root.mkdir(parents=True, exist_ok=True)
        workdir = _root / ("pipeline_tts_" + next(tempfile._get_candidate_names()))
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="pipeline_tts_"))
    try:
        pairs = synth_all(chunks, opts, voice, workdir)
        if not pairs:
            print("  [失败] 没有一句合成成功")
            return False

        rate = DEFAULT_RATE
        print(f"  后处理 {len(pairs)} 段"
              f"（淡入淡出 {opts.fade * 1000:.0f}ms"
              f"{'，裁尾 %dms' % opts.trim_tail_ms if opts.trim_tail_ms else ''}）…")
        shaped: list[Path] = []
        for i, (_text, w) in enumerate(pairs):
            dst = workdir / f"shaped_{i:04d}.wav"
            shape_chunk(w, dst, opts, rate)
            shaped.append(dst)

        gap = workdir / "_gap.wav"
        head = workdir / "_head.wav"
        tail = workdir / "_tail.wav"
        if opts.gap > 0 and len(shaped) > 1:
            make_silence(opts.gap, rate, gap)
        if opts.head_pad > 0:
            make_silence(opts.head_pad, rate, head)
        if opts.tail_pad > 0:
            make_silence(opts.tail_pad, rate, tail)

        parts: list[Path] = []
        if opts.head_pad > 0:
            parts.append(head)
        for i, w in enumerate(shaped):
            if i > 0 and opts.gap > 0:
                parts.append(gap)
            parts.append(w)
        if opts.tail_pad > 0:
            parts.append(tail)

        print(f"  拼接 {len(parts)} 段…")
        merged = workdir / "_merged.wav"
        assemble(parts, merged, workdir, rate)
        encode_audio(merged, out_path, opts.format)

        dur = probe_duration(out_path) if out_path.is_file() else 0
        size_mb = out_path.stat().st_size / 1024 / 1024 if out_path.is_file() else 0
        print(f"  [完成] {out_path}  ({size_mb:.2f} MB, {dur:.2f}s)")

        if opts.srt:
            # 时间轴按拼接顺序推：开头静音 + 逐段时长 + 段间静音（末段后不补 gap）
            srt_path = out_path.with_suffix(".srt")
            durs = [probe_duration(p) for p in shaped]
            t = opts.head_pad if opts.head_pad > 0 else 0.0
            lines: list[str] = []
            for idx, ((text, _w), d) in enumerate(zip(pairs, durs), 1):
                start, end = t, t + d
                lines.append(str(idx))
                lines.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
                lines.extend(wrap_subtitle(text, opts.srt_width))
                lines.append("")
                t = end + (opts.gap if idx < len(pairs) else 0.0)
            srt_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"  [时间轴] {srt_path}（{len(pairs)} 条，{fmt_ts(t)} 结束）")
        return True
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def draw_paths(base: Path, n: int) -> list[Path]:
    """抽卡落点：`<名>_d01.<ext>`、`_d02`… 取接下来 n 个不存在的编号（不覆盖、不跳过）。

    同一段文本多抽几发才能挑最好的那发 —— 这链路的解码是随机的，每发都不一样。
    """
    out: list[Path] = []
    i = 1
    while len(out) < n:
        cand = base.with_name(f"{base.stem}_d{i:02d}{base.suffix}")
        if not cand.exists():
            out.append(cand)
        i += 1
    return out


def collect_texts(input_dir: Path, recursive: bool) -> list[Path]:
    """收集文本文件。"""
    files: list[Path] = []
    for ext in TEXT_EXTS:
        files.extend(input_dir.rglob(f"*{ext}") if recursive else input_dir.glob(f"*{ext}"))
        files.extend(input_dir.rglob(f"*{ext.upper()}") if recursive else input_dir.glob(f"*{ext.upper()}"))
    return sorted(set(files))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="文本转语音 — 长文本切句合成，支持音色库与尾音后处理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tools/common/text/to_audio.py note.md -o out/note.mp3
  python tools/common/text/to_audio.py note.md --voice feibi
  python tools/common/text/to_audio.py note.md --srt
  python tools/common/text/to_audio.py ./notes/ -r --voice feibi
  python tools/common/text/to_audio.py --list-voices
  python tools/common/text/to_audio.py --check
  python tools/common/text/to_audio.py note.md --trim-tail-ms 60 --tail-pad 0.6
  python tools/common/text/to_audio.py note.md --engine sapi --format mp3
  python tools/common/text/to_audio.py note.md --dry-run
        """,
    )
    parser.add_argument("input", nargs="?", default=None, help="输入文本文件或目录")
    parser.add_argument("-o", "--output", default=None, help="输出音频路径（单文件模式）")
    parser.add_argument("--outdir", default=None, help="输出目录（批量模式）")
    parser.add_argument("-f", "--force", action="store_true", help="强制覆盖已有文件")
    parser.add_argument("--draw", action="store_true",
                        help="抽卡模式：每跑一次出一份新文件（<名>_d01.<ext>、_d02…），既不覆盖也不跳过；"
                             "同一段文本多抽几发挑最好（这链路的解码是随机的）")
    parser.add_argument("--count", type=int, default=1, metavar="N",
                        help="一次生成 N 个版本（N>1 时自动走抽卡命名 <名>_d01…，便于并排挑最好的）[默认: 1]")
    parser.add_argument("--check", action="store_true", help="仅检查环境")
    parser.add_argument("--list-voices", action="store_true", help="列出可用音色")
    parser.add_argument("--register", metavar="音色名",
                        help="把 --ref-audio 注册成音色（写入 voices/<音色名>/）；"
                             "配合 --ref-text 与 --prompt-lang 指定逐字文本与参考语种")
    parser.add_argument("--dry-run", action="store_true", help="只做清洗与切句，不合成")

    g = parser.add_argument_group("合成")
    g.add_argument("--engine", default="genie", choices=ENGINES,
                   help="合成后端 [默认: genie]")
    g.add_argument("--format", default="mp3", choices=FORMATS, help="输出格式 [默认: mp3]")
    g.add_argument("--genie-root", default=None,
                   help=f"GENIE 资源根目录 [默认: 环境变量 GENIE_ROOT 或 {DEFAULT_GENIE_ROOT}]")
    g.add_argument("--host", default=DEFAULT_HOST, help=f"gptsovits 服务地址 [默认: {DEFAULT_HOST}]")
    g.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"gptsovits 服务端口 [默认: {DEFAULT_PORT}]")
    g.add_argument("--timeout", type=int, default=300, help="单句合成超时秒数 [默认: 300]")
    g.add_argument("--speed", type=float, default=1.0, help="语速倍率（仅 gptsovits）[默认: 1.0]")
    g.add_argument("--seed", type=int, default=-1, help="随机种子（仅 gptsovits）[默认: -1]")

    v = parser.add_argument_group("音色")
    v.add_argument("--voice", default=None, help="音色名（voices/<名>/ 或 GENIE 预设角色名）")
    v.add_argument("--ref-audio", default=None, help="临时参考音频（仅 sapi/gptsovits 生效）")
    v.add_argument("--ref-text", default=None, help="参考音频的逐字文本")
    v.add_argument("--text-lang", default="zh", help="待合成文本语种 [默认: zh]")
    v.add_argument("--prompt-lang", default="zh", help="参考文本语种 [默认: zh]")
    v.add_argument("--sapi-voice", default=None, help="SAPI 用的系统语音名（见 --check）")
    v.add_argument("--sapi-rate", type=int, default=0, help="SAPI 语速 -10..10 [默认: 0]")

    t = parser.add_argument_group("切句")
    t.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                   help=f"单句字数上限 [默认: {DEFAULT_MAX_CHARS}]")
    t.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS,
                   help=f"碎句合并阈值 [默认: {DEFAULT_MIN_CHARS}]")
    t.add_argument("-r", "--recursive", action="store_true", help="递归扫描子文件夹")

    p = parser.add_argument_group("后处理（尾音生硬时调这几个）")
    p.add_argument("--gap", type=float, default=DEFAULT_GAP,
                   help=f"句间静音秒数 [默认: {DEFAULT_GAP}]")
    p.add_argument("--head-pad", type=float, default=DEFAULT_HEAD_PAD,
                   help=f"开头静音秒数 [默认: {DEFAULT_HEAD_PAD}]")
    p.add_argument("--tail-pad", type=float, default=DEFAULT_TAIL_PAD,
                   help=f"结尾静音秒数 [默认: {DEFAULT_TAIL_PAD}]")
    p.add_argument("--fade", type=float, default=DEFAULT_FADE,
                   help=f"每段首尾淡入淡出秒数，0 关闭 [默认: {DEFAULT_FADE}]")
    p.add_argument("--trim-tail-ms", type=int, default=DEFAULT_TRIM_TAIL_MS,
                   help=f"裁掉每段结尾多少毫秒，用于削掉杂音尾 [默认: {DEFAULT_TRIM_TAIL_MS}]")
    p.add_argument("--srt", action="store_true",
                   help="同时输出同名 .srt 时间轴（按拼接后的真实时序）")
    p.add_argument("--srt-width", type=int, default=DEFAULT_SRT_WIDTH,
                   help=f".srt 每行字数上限，0 = 不换行 [默认: {DEFAULT_SRT_WIDTH}]")

    opts = parser.parse_args()

    if opts.list_voices:
        root = genie_root(opts)
        rv = registry_voices()
        bc = builtin_characters(root)
        print(f"音色库 {VOICES_DIR}:")
        if rv:
            for name in rv:
                try:
                    info = resolve_voice(argparse.Namespace(
                        voice=name, ref_audio=None, ref_text=None,
                        prompt_lang=opts.prompt_lang, genie_root=opts.genie_root))
                except ToolError as exc:
                    print(f"  {name}  [读取失败] {str(exc)[:60]}")
                    continue
                marks = [info["kind"]]
                if info.get("prompt_wav"):
                    marks.append(Path(info["prompt_wav"]).name)
                print(f"  {name}  ({'; '.join(marks)})")
        else:
            print("  （空）照 voices/_example/ 建一个目录即可")
        print(f"\nGENIE 预设角色 {root / 'CharacterModels' / 'v2ProPlus'}:")
        if bc:
            for name in bc:
                print(f"  {name}  (语言 {character_language(name, root / 'CharacterModels' / 'v2ProPlus' / name)})")
        else:
            print("  （无；检查 --genie-root）")
        return

    if opts.register:
        if not opts.ref_audio:
            parser.error("--register 需要同时给 --ref-audio")
        src = Path(opts.ref_audio).resolve()
        if not src.is_file():
            raise ToolError(f"参考音频不存在: {src}")
        vdir = VOICES_DIR / opts.register
        if vdir.exists():
            raise ToolError(f"音色 '{opts.register}' 已存在: {vdir}"
                            f"（换个名字，或先把该目录移走）")
        vdir.mkdir(parents=True)
        shutil.copyfile(src, vdir / f"ref{src.suffix.lower()}")
        (vdir / "ref.txt").write_text((opts.ref_text or "").strip() + "\n", encoding="utf-8")
        meta = {"name": opts.register, "ref_lang": opts.prompt_lang}
        if opts.ref_text:
            meta["prompt_text"] = opts.ref_text.strip()
        (vdir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[注册] {opts.register}  ->  {vdir}")
        print(f"  ref{src.suffix.lower()}  {src.stat().st_size:,} B  参考语种 {opts.prompt_lang}")
        if opts.ref_text:
            print(f"  ref.txt   {opts.ref_text.strip()[:50]}")
        print(f"\n  用法: --voice {opts.register} --text-lang zh")
        return

    if opts.check:
        env_check(opts)
        return

    if not opts.input:
        parser.error("需要给出输入文件或目录（或用 --check / --list-voices）")

    if not check_ffmpeg():
        print("[错误] 需要 ffmpeg（后处理、拼接、转 mp3），但 PATH 里找不到")
        sys.exit(1)

    voice = resolve_voice(opts)
    if voice.get("name"):
        print(f"音色: {voice['name']}（{voice['kind']}）")
        if voice.get("prompt_wav"):
            print(f"  参考音频: {Path(voice['prompt_wav']).name}")

    input_path = Path(opts.input).resolve()
    if not input_path.exists():
        print(f"[错误] 输入路径不存在: {input_path}")
        sys.exit(1)

    # ── 单文件 ──
    if input_path.is_file():
        base_out = common.resolve_output(input_path, opts.output, opts.outdir, opts.format)
        n_ver = max(1, opts.count)
        draw = (opts.draw or n_ver > 1) and not opts.dry_run
        outs = draw_paths(base_out, n_ver) if draw else [base_out]
        print(f"输入: {input_path}")
        print(f"输出: {outs[0]}" if len(outs) == 1
              else f"输出: {len(outs)} 个版本（{outs[0].name} … {outs[-1].name}）")
        print(f"引擎: {opts.engine}" + ("（抽卡模式）" if draw else "") + "\n")
        if not draw and outs[0].exists() and not opts.force and not opts.dry_run:
            print(f"[跳过] {outs[0].name} 已存在，本次什么都没生成"
                  f"（加 -f 覆盖，或加 --draw／--count 每跑一次出新文件）")
            sys.exit(0)
        all_ok = True
        for i, out_p in enumerate(outs, 1):
            if len(outs) > 1:
                print(f"── 版本 {i}/{len(outs)}：{out_p.name}")
            if not process_one(input_path, out_p, opts, voice):
                all_ok = False
        sys.exit(0 if all_ok else 1)

    # ── 批量 ──
    files = collect_texts(input_path, opts.recursive)
    if not files:
        print(f"[提示] 在 {input_path} 中没找到文本文件（{'、'.join(sorted(TEXT_EXTS))}）")
        return

    outdir = Path(opts.outdir) if opts.outdir else (input_path / "output_audio")
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"{'=' * 60}")
    print(f"  文本转语音（{opts.engine} → {opts.format}）")
    print(f"{'=' * 60}")
    print(f"  输入目录: {input_path}")
    print(f"  输出目录: {outdir}")
    print(f"  文件数量: {len(files)}")
    print(f"{'=' * 60}\n")

    success = skipped = failed = 0
    start_all = time.time()
    for i, src in enumerate(files, 1):
        try:
            rel = src.relative_to(input_path)
        except ValueError:
            rel = Path(src.name)
        base_out = (outdir / rel).with_suffix(f".{opts.format}")
        n_ver = max(1, opts.count)
        draw = (opts.draw or n_ver > 1) and not opts.dry_run
        outs = draw_paths(base_out, n_ver) if draw else [base_out]
        base_out.parent.mkdir(parents=True, exist_ok=True)
        print(f"[{i}/{len(files)}] {rel}" + (f"（每篇 {n_ver} 个版本）" if n_ver > 1 else ""))
        if not draw and outs[0].exists() and not opts.force and not opts.dry_run:
            print(f"  [跳过] {outs[0].name} 已存在，本次没重新生成"
                  f"（加 -f 覆盖，或加 --draw／--count 每跑一次出新文件）")
            skipped += 1
            continue
        if all(process_one(src, out_p, opts, voice) for out_p in outs):
            success += 1
        else:
            failed += 1
        print()

    print(f"{'=' * 60}")
    print(f"  成功: {success}  跳过: {skipped}  失败: {failed}")
    print(f"  总耗时: {time.time() - start_all:.1f}s")
    print(f"  输出目录: {outdir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    try:
        main()
    except ToolError as exc:
        print(f"[错误] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[中断] 用户取消")
        sys.exit(130)
