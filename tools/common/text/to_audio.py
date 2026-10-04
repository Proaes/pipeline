#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text_to_audio.py — 文本转语音工具

把 Markdown / 纯文本读成音频：长文本先切句，逐句合成后拼成一整段，
音色从 pipeline/voices/<音色名>/ 取，也可以临时用一段参考音频。

合成后端（--engine）:
    genie      本地 GENIE（GPT-SoVITS 的 ONNX 引擎），纯 CPU、零显存，默认
    gptsovits  调本地 GPT-SoVITS 的 api_v2.py HTTP 服务（127.0.0.1:9880）
    qwen       Qwen3-TTS 0.6B 零样本克隆（进程内、走 GPU），音质最好但慢
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
    python tools/common/text/to_audio.py 稿子.txt --engine qwen --voice si_c   # 读稿
    python tools/common/text/to_audio.py 稿子.txt --engine qwen --voice si_c \\
        --qwen-ckpt pipeline-specialized/tts/model/qwen_ft/si/ckpt_ep3.pt
        # 读稿走「祀」的 Qwen 微调权重（我们自己的 state dict，叠在底座上；261003 定版 = ep3）
    python tools/common/text/to_audio.py 稿子.txt --engine gptsovits --voice si_ft \\
        --gpt-weights pipeline-specialized/tts/model/gpt-sovits/GPT_weights_v2ProPlus/si-e15.ckpt \\
        --sovits-weights pipeline-specialized/tts/model/gpt-sovits/SoVITS_weights_v2ProPlus/si_e8_s328.pth
        # 先让正在跑的服务热切到这套权重，再合成（切一次，不逐句切）

前置条件:
    genie 引擎：D:\\mamba\\envs\\tts 环境 + D:\\tools\\genie 下的资源（见 --check）
    gptsovits 引擎：先起本地服务 python api_v2.py -a 127.0.0.1 -p 9880
    qwen 引擎：D:\\mamba\\envs\\tts 环境（qwen-tts + CUDA 版 torch）+ 模型目录
        （见 --check）；实测约 4 倍实时，1000 字稿子约 13 分钟
    ffmpeg 需要在 PATH 中（后处理、拼接、转 mp3）

运行记录（261004 起，`--record-dir` 默认 `pipeline-specialized/tts/run/ledger/`）:
    每次合成自动写两份数据账，供事后统计「各方案良率 / 返工几次 / 失控率 / 成本」：
      · `detail/<run_id>.jsonl`：这一次的明细（run + 逐句 + 每一发的时长与被拒原因）；
      · `runs.jsonl`：全局账，一行一次运行（参数快照 + 汇总指标 + 逐句指针）。
    只记数据、不影响音频；`--no-record` 关掉，`--origin` / `--trigger-json` 用来标「谁点的、勾了什么」。
"""

import argparse
import base64
import hashlib
import importlib.util
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
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 指向 tools/
from _lib import common  # noqa: E402

# ── 常量 ──────────────────────────────────────────────────────────
TEXT_EXTS = {".md", ".markdown", ".txt"}
ENGINES = ("genie", "gptsovits", "qwen", "sapi")
FORMATS = ("mp3", "wav")

VOICES_DIR = common.PROJECT_ROOT / "voices"
DEFAULT_GENIE_ROOT = Path(r"D:\tools\genie")
DEFAULT_QWEN_MODEL = Path(r"D:\tools\qwen3tts\Qwen3-TTS-12Hz-0.6B-Base")
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

# Qwen3-TTS 的语种名（模型 config.json 的 talker_config.codec_language_id 只认这些，
# 不含方言键）——本工具的语种码（--text-lang / meta 的 ref_lang）先在这里翻译。
# 默认 sdpa：实测 flash_attention_2 在本模型（talker → code_predictor 两层嵌套
# 自回归）上更慢（RTF 4.08 对 3.13），所以不随 flash-attn 是否安装而变。
DEFAULT_QWEN_ATTN = "sdpa"
# Qwen 的采样默认值（261003 七次实测定版，见 ISSUES/09 §14 / §19）：
#   ① talker 温度收到 0.7 / top_p 0.9 —— 官方默认 0.9/1.0 会「颤」（用户听感原话）；
#   ② **sub-talker 温度 0.5** —— 12 Hz 的 16 个码本里只有 1 个由 talker 出、另 **15 个**由
#      `code_predictor` 出，而它的采样参数一直是包内默认 0.9；成对实测（同种子 4 句 2 发）
#      **0.9 合格 4/8、0.5 合格 8/8**，用户另说 0.5 那一列的「语气非常贴近」；
#   ③ 单句上限 600 帧（≈48 s）—— 包内默认 2048（≈164 s）时，一旦模型进重复循环会空转十几分钟；
#      600 仍然远大于正常句（`--max-chars 120` ≈ 22 s 音频），但把一次失控的代价从 ~5 分钟压到 ~3 分钟
#      （261003 七次实测：生产那批 3 发里有 2 发被失控句拖到 83 / 87 s，虽然都被 `--screen` 挡下，
#      但每发白烧了五分钟）。
DEFAULT_QWEN_TEMP = 0.7
DEFAULT_QWEN_TOP_P = 0.9
DEFAULT_QWEN_SUB_TEMP = 0.5
DEFAULT_QWEN_MAX_NEW = 600
# ── 生产数据账（261004 一次）──────────────────────────────────────
# 用户要「在实际运用中记录使用的数据，用来后续微调以及优化」（各方案良率、返工几次……）。
# 每次运行写两份：明细 `<根>/detail/<run_id>.jsonl`（一行 = 这次运行，含逐句与每一发），
# 以及全局账 `<根>/runs.jsonl`（追加一行 = 一次运行，seg 不带每发明细，方便整表读进来 group by）。
# 落点是 TTS 那个「家」的 run/ledger/ —— 它在工作区 `pipeline-specialized/tts/` 下、**不在公开仓里**
# （261004 二次：`pipeline/output/tts/` 已按用户要求整体搬走并删除），所以稿子内容与音频数据不外流。
TTS_HOME = Path(os.environ.get("PIPELINE_TTS_HOME")
                or (common.PROJECT_ROOT.parent / "pipeline-specialized" / "tts"))
DEFAULT_RECORD_ROOT = TTS_HOME / "run" / "ledger"
LEDGER_NAME = "runs.jsonl"
QWEN_LANG_ALIASES = {
    "zh": "Chinese", "cn": "Chinese", "zh-cn": "Chinese", "chinese": "Chinese",
    "en": "English", "english": "English",
    "jp": "Japanese", "ja": "Japanese", "japanese": "Japanese",
    "ko": "Korean", "korean": "Korean",
    "de": "German", "german": "German",
    "fr": "French", "french": "French",
    "ru": "Russian", "russian": "Russian",
    "es": "Spanish", "spanish": "Spanish",
    "it": "Italian", "italian": "Italian",
    "pt": "Portuguese", "portuguese": "Portuguese",
    "auto": "Auto",
}


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


def qwen_model_dir(opts: argparse.Namespace) -> Path:
    """Qwen3-TTS 模型目录：命令行 > 环境变量 > 默认。"""
    if opts.qwen_model:
        return Path(opts.qwen_model)
    env = os.environ.get("QWEN_TTS_MODEL")
    return Path(env) if env else DEFAULT_QWEN_MODEL


def qwen_language(code: str) -> str:
    """把本工具的语种码（zh / jp / en …）翻成 Qwen 认的语种名。"""
    key = (code or "").strip().lower().replace("_", "-")
    if key in QWEN_LANG_ALIASES:
        return QWEN_LANG_ALIASES[key]
    raise ToolError(f"Qwen 不认识语种 '{code}'；可用: "
                    f"zh / en / jp / ko / de / fr / ru / es / it / pt / auto")


def qwen_installed() -> bool:
    """只看包在不在，**不导入** —— 导入 qwen_tts 会拉起 numba，
    而 numba 要写临时目录，在本沙箱里会卡死；问「装没装」不值得冒这个险。
    """
    try:
        return importlib.util.find_spec("qwen_tts") is not None
    except (ImportError, ValueError):
        return False


def qwen_model_languages(model_dir: Path) -> list[str]:
    """从模型 config.json 读它支持的语种名（纯读文件，不加载模型）。"""
    cfg_path = model_dir / "config.json"
    if not cfg_path.is_file():
        return []
    try:
        cfg = json.loads(read_text(cfg_path))
        ids = cfg.get("talker_config", {}).get("codec_language_id", {})
    except (json.JSONDecodeError, AttributeError):
        return []
    return sorted(k for k in ids if "dialect" not in k)


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


def apply_gptsovits_weights(gpt: str, sovits: str, opts: argparse.Namespace) -> list[str]:
    """把正在跑的服务热切到指定权重；返回每步的中文回执（供 GUI 写进「步骤输出」）。

    服务是 `api_v2.py`，它自带 `/set_gpt_weights` 与 `/set_sovits_weights`，切权重不用重启
    （省掉一次几十秒的模型加载）。两个路径给谁就只切谁 —— 只想换 SoVITS 时 gpt 传空串即可。
    路径能是相对 `GPT_SoVITS/` 的相对路径，也能是绝对路径，解析由服务自己负责。
    """
    steps: list[str] = []
    base = f"http://{opts.host}:{opts.port}"
    for name, path, endpoint in (("GPT", gpt, "set_gpt_weights"),
                                 ("SoVITS", sovits, "set_sovits_weights")):
        if not path:
            continue
        url = (f"{base}/{endpoint}?weights_path="
               + urllib.parse.quote(str(path), safe=""))
        try:
            with urllib.request.urlopen(url, timeout=opts.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")[:200]
            steps.append(f"服务权重已切到{name}：{Path(path).name}（{resp.status} {body}）")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise ToolError(f"切换{name}权重失败：HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"切换{name}权重失败：连不上 {base}（{exc.reason}）") from exc
    return steps


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


# ── 后端 4：Qwen3-TTS（进程内，GPU 优先） ─────────────────────────
class QwenEngine:
    """Qwen3-TTS 零样本克隆：模型与音色提示各准备一次，之后逐句复用。

    261002 实测（RTX 4060 8G / bf16 / sdpa）：93 字 → 75.8s 生成 / 17.6s 音频，
    RTF ≈ 4.2；每句另有约 10s 固定开销，所以切句多少对总时长影响很小。
    non_streaming_mode=True 实测不更快（77.2s），故保持默认 False。
    瓶颈是 talker → code_predictor 的两层嵌套自回归，适合离线批量出成品。
    """

    def __init__(self, opts: argparse.Namespace, voice: dict):
        self.opts = opts
        self.voice = voice
        self.torch = None
        self.sf = None
        self.model = None
        self.prompt = None          # create_voice_clone_prompt 的产物，逐句复用
        self.lang = "Chinese"

    def prepare(self) -> None:
        model_dir = qwen_model_dir(self.opts)
        if not (model_dir / "config.json").is_file():
            raise ToolError(
                f"找不到 Qwen3-TTS 模型目录: {model_dir}\n"
                f"    用 --qwen-model 指定，或设环境变量 QWEN_TTS_MODEL"
            )
        ref = self.voice.get("prompt_wav")
        if not ref:
            raise ToolError("qwen 引擎需要参考音频（--ref-audio 或 --voice）")

        t0 = time.time()
        import soundfile as sf  # noqa: PLC0415
        import torch  # noqa: PLC0415
        from qwen_tts import Qwen3TTSModel  # noqa: PLC0415
        self.torch, self.sf = torch, sf
        print(f"  import qwen_tts      {time.time() - t0:6.2f}s")

        device = self.opts.qwen_device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        # CPU 上 bf16 没意义也慢，统一用 float32；CUDA 用 bf16（fp16 会数值溢出崩）
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        if device.startswith("cuda"):
            print(f"  设备 {device}（{torch.cuda.get_device_name(device)}）"
                  f"| dtype {dtype} | attn {self.opts.qwen_attn}")
        else:
            print(f"  设备 cpu | dtype {dtype} | attn {self.opts.qwen_attn}"
                  f"（没看到 CUDA，会比 GPU 慢一个量级）")

        t0 = time.time()
        self.model = Qwen3TTSModel.from_pretrained(
            str(model_dir), device_map=device, dtype=dtype,
            attn_implementation=self.opts.qwen_attn)
        print(f"  加载模型             {time.time() - t0:6.2f}s")

        # 微调权重：我们自己的 ckpt_epN.pt（只含可训参数）叠在底座上。
        # 未命中的都是不可训部分（speaker_encoder 等），不是错误；打印出来便于发现「权重其实没加载」。
        ckpt_path = (self.opts.qwen_ckpt or "").strip()
        if ckpt_path:
            cp = Path(ckpt_path)
            if not cp.is_file():
                raise ToolError(f"找不到 Qwen 微调权重: {cp}")
            t0 = time.time()
            blob = torch.load(cp, weights_only=False, map_location="cpu")
            state = blob["state"] if isinstance(blob, dict) and "state" in blob else blob
            missing, unexpected = self.model.model.load_state_dict(state, strict=False)
            ep = blob.get("epoch", "?") if isinstance(blob, dict) else "?"
            print(f"  载入微调权重         {time.time() - t0:6.2f}s"
                  f"  ({cp.name}｜epoch {ep}｜{len(state)} 个张量｜"
                  f"未命中 {len(missing)}（不可训部分）｜多余 {len(unexpected)}）")

        # 参考音频的编码（ref_code + 说话人向量）在这里一次做完，之后逐句复用；
        # 没给逐字文本时只能走 x_vector_only_mode（只用音色向量，丢掉 ICL 条件）。
        ref_text = (self.voice.get("prompt_text") or "").strip()
        self.lang = qwen_language(self.opts.text_lang)
        t0 = time.time()
        self.prompt = self.model.create_voice_clone_prompt(
            ref_audio=str(ref), ref_text=ref_text or None,
            x_vector_only_mode=not ref_text)
        mode = (f"ICL（逐字文本 {len(ref_text)} 字）" if ref_text
                else "仅音色向量（没给 ref_text，音色像但语气弱）")
        print(f"  音色提示             {time.time() - t0:6.2f}s"
              f"  ({mode}；目标文本 {self.lang})")
        print(f"  采样                 talker temp {self.opts.qwen_temp} / top_p {self.opts.qwen_top_p}"
              f"｜sub-talker temp {self.opts.qwen_sub_temp}"
              f"｜上限 {self.opts.qwen_max_new_tokens} 帧")

    def synth(self, text: str, out_wav: Path) -> None:
        # 这条链路是随机解码（do_sample=True），--seed >= 0 时固定一下便于复现
        if self.opts.seed is not None and self.opts.seed >= 0:
            self.torch.manual_seed(self.opts.seed)
        # 采样参数必须显式传：不传就用包内 hard defaults（talker 0.9/1.0、sub-talker 0.9/1.0、
        # max_new_tokens 2048），而那套实测颤、且遇到重复循环会空转（见 DEFAULT_QWEN_* 的注释）。
        wavs, sr = self.model.generate_voice_clone(
            text=text, language=self.lang, voice_clone_prompt=self.prompt,
            temperature=self.opts.qwen_temp,
            top_p=self.opts.qwen_top_p,
            subtalker_temperature=self.opts.qwen_sub_temp,
            max_new_tokens=self.opts.qwen_max_new_tokens)
        if not wavs:
            raise ToolError("Qwen 没有产出音频")
        self.sf.write(str(out_wav), wavs[0], sr)


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
    qdir = qwen_model_dir(opts)
    print(f"  Qwen 模型       : {qdir}  "
          f"{'存在' if (qdir / 'config.json').is_file() else '不存在'}")
    print(f"  qwen_tts        : {'已安装' if qwen_installed() else '未安装'}"
          f"（{sys.executable}）")
    qlangs = qwen_model_languages(qdir)
    if qlangs:
        print(f"  Qwen 支持语种   : {'、'.join(qlangs)}（--text-lang 用 zh/en/jp… 即可）")
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


def split_sentences_with_para(text: str, max_chars: int = DEFAULT_MAX_CHARS,
                              min_chars: int = DEFAULT_MIN_CHARS) -> tuple[list[str], list[int]]:
    """`split_sentences` 的结果 + 每句属于源稿的第几段（段落按空行分）。

    与直接调 `split_sentences(text)` **完全等价**（同一套规则逐段跑，段内没有空行），
    多出来的 `para_of` 只用来在段与段之间插更长的静音（`--para-gap`）——
    诗体 / 分行写的稿子，行间的呼吸比句内的停顿重要得多。
    """
    chunks: list[str] = []
    para_of: list[int] = []
    for pi, para in enumerate(re.split(r"\n\s*\n", text)):
        sub = split_sentences(para, max_chars, min_chars)
        chunks.extend(sub)
        para_of.extend([pi] * len(sub))
    return chunks, para_of


def file_sig(p) -> str:
    """文件指纹（大小 + 修改秒），进缓存键：权重 / 参考音频换了就必须重抽。

    没有参考音频（sapi / 无音色）时给 `-` —— `Path(None)` 抛的是 TypeError 不是 OSError，
    261004 被数据账那条路踩到过一次（`--engine sapi` 同理），所以两种都吃掉。
    """
    if not p:
        return "-"
    try:
        st = Path(p).stat()
        return f"{st.st_size}-{int(st.st_mtime)}"
    except (OSError, TypeError, ValueError):
        return "-"


def _key_path(p) -> str:
    """缓存键里的文件路径统一成绝对路径。

    不归一的话，同一个参考音频 / 同一份 ckpt 会因为「命令行写相对路径、界面传绝对路径」
    算出两个不同的键 ⇒ 两边的逐句缓存互相看不见（261003 八次实测：命令行刚跑完 25 句，
    界面再跑同一篇稿子，命中 0 句、整篇从头合成）。绝对化之后相对路径按当前工作目录解析。
    """
    if not p:
        return ""
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(p)


def seg_cache_key(opts: argparse.Namespace, voice: dict) -> str:
    """逐句缓存的公共键：引擎 / 音色 / 参考 / ckpt / 采样 / seed / 抽卡作用域。

    句子的指纹**只由句文本决定**（见 `SegCache.path`）⇒ 同一句在「先跑 6 句验证、
    再跑全篇」两次运行里命中同一个文件，验证那一遍不算白跑。
    """
    key = hashlib.sha1()
    for part in (opts.engine, voice.get("name", ""), _key_path(voice.get("prompt_wav")),
                 file_sig(voice.get("prompt_wav")), voice.get("prompt_text", ""),
                 _key_path(opts.qwen_ckpt), file_sig(opts.qwen_ckpt) if opts.qwen_ckpt else "-",
                 f"{opts.qwen_temp}/{opts.qwen_top_p}/{opts.qwen_sub_temp}/{opts.qwen_max_new_tokens}",
                 f"seed={opts.seed}", getattr(opts, "draw_scope", "single")):
        key.update(str(part).encode("utf-8"))
        key.update(b"|")
    return key.hexdigest()[:16]


# ── 生产数据账：run / seg / draw 三层 ─────────────────────────────
def text_fp(text: str) -> str:
    """句子指纹（与逐句缓存的文件名同源）——账里的句子和缓存里的 wav 能对上号。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def sha1_file(p) -> str:
    """文件内容哈希：源稿与产物各留一个，事后能核对「是不是同一份东西」。"""
    if not p:
        return ""
    try:
        h = hashlib.sha1()
        with open(p, "rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


def git_rev() -> str:
    """项目代码版本（短哈希，取不到就是空）——账要能回答「哪版代码跑出来的」。"""
    try:
        r = subprocess.run(["git", "-C", str(common.PROJECT_ROOT), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def new_run_id() -> str:
    """运行编号：`年月日时分秒-4 位随机`，可按时间排序又不会撞。"""
    return time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]


def parse_trigger(raw: str):
    """触发侧上下文（界面勾选项等）：是 JSON 就解出来，不是就原样留着。"""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def record_root(opts: argparse.Namespace) -> Path:
    """账本落点：`--record-dir` > 环境变量 `PIPELINE_TTS_RECORD_DIR` > pipeline-specialized/tts/run/ledger。"""
    raw = str(opts.record_dir or os.environ.get("PIPELINE_TTS_RECORD_DIR") or "").strip()
    return Path(raw) if raw else DEFAULT_RECORD_ROOT


def append_jsonl(path: Path, obj: dict) -> None:
    """追加一行 JSONL；整行一次 write（并发下尽量不交错），失败只警告不打断合成。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def params_snapshot(opts: argparse.Namespace, voice: dict) -> dict:
    """这一次运行的全部旋钮 —— 良率差异的归因全靠它；别在别处再抄一份。"""
    return {
        "engine": opts.engine, "format": opts.format, "seed": opts.seed,
        "raw": bool(opts.raw), "no_cache": bool(opts.no_cache),
        "cond_mode": "icl" if voice.get("prompt_text") else "xvec",
        "qwen": {"model": str(opts.qwen_model or DEFAULT_QWEN_MODEL),
                 "ckpt": _key_path(opts.qwen_ckpt),
                 "temp": opts.qwen_temp, "top_p": opts.qwen_top_p,
                 "sub_temp": opts.qwen_sub_temp,
                 "max_new_tokens": opts.qwen_max_new_tokens,
                 "attn": opts.qwen_attn, "device": opts.qwen_device or "auto"},
        # 这三项我们从来没传过，走的是包内默认 ⇒ 单独记一笔，免得事后误以为「调过」
        "qwen_pkg_defaults": {"top_k": 50, "sub_top_p": 1.0, "sub_top_k": 50,
                              "repetition_penalty": 1.05},
        "voice": {"name": voice.get("name") or "", "ref": _key_path(voice.get("prompt_wav")),
                  "ref_sig": file_sig(voice.get("prompt_wav")),
                  "prompt_text": voice.get("prompt_text") or ""},
        "split": {"max_chars": opts.max_chars, "min_chars": opts.min_chars},
        "screen": {"rate": opts.screen_rate, "factor": opts.screen_factor,
                   "retry_draws": opts.retry_draws, "retry_factor": opts.retry_factor,
                   "shortest_rule": "字数/15"},
        "post": {"gap": opts.gap, "para_gap": opts.para_gap, "head_pad": opts.head_pad,
                 "tail_pad": opts.tail_pad, "fade": opts.fade,
                 "trim_tail_ms": opts.trim_tail_ms, "srt": bool(opts.srt)},
        "redo_lines": sorted(int(x) for x in opts.redo_lines.replace("，", ",").split(",")
                             if x.strip().isdigit()),
        "service": f"{opts.host}:{opts.port}" if opts.engine == "gptsovits" else "",
    }


def emit_run_safe(*args, **kwargs) -> dict:
    """`emit_run` 的兜底包装：**数据账绝不能把合成搞挂**，出任何意外都只警告。"""
    try:
        return emit_run(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 记录是旁路，任何异常都不该影响出音频
        print(f"  [警告] 写运行记录失败（{type(exc).__name__}: {exc}）—— 不影响这次合成")
        return {}


def emit_run(opts: argparse.Namespace, voice: dict, src, out_path, report: list[dict],
             run_id: str, t_start: float, dur: float, n_para: int = 0,
             ok: bool = True, reason: str = "") -> dict:
    """把一次运行落成记录（明细 + 全局账各一份），并返回记录对象。

    `report` = `synth_all` 的逐句行（含每发的 `draw_log`）。指标口径：
      · `ok_rate`          合格句 / 总句数（含缓存命中）
      · `first_pass_rate`  **新合成**的句子里「第一发就落在窗口内」的比例 ← 这就是良率
      · `runaway_draws`    被时长窗口判为「偏长（疑似失控）」的发数 ← 这是返工的主要来源
    """
    t_end = time.time()
    segs: list[dict] = []
    for r in report:
        draws = r.get("draw_log") or []
        segs.append({
            "i": r["i"], "fp": r.get("fp", ""), "chars": r["chars"], "text": r["text"],
            "expect": r.get("expect"), "lo": r.get("lo"), "hi": r.get("hi"),
            "dur": r.get("dur"), "draws": r.get("draws", 0), "wall": r.get("wall"),
            "source": r.get("source", ""), "status": r.get("status", ""),
            "reasons": [d.get("reason", "") for d in draws if d.get("reason")],
            "draw_log": draws,
        })

    def rate(num, den):
        return round(num / den, 4) if den else None

    gen = [r for r in report if r.get("source") == "生成"]
    hit = [r for r in report if r.get("source") == "缓存"]
    kept = [r for r in report if r.get("dur") is not None]
    ok_rows = [r for r in report if r.get("status") == "ok"]
    first_pass = [r for r in gen if r.get("draws") == 1 and r.get("status") == "ok"]
    all_draws = [d for r in report for d in (r.get("draw_log") or [])]
    chars = sum(r["chars"] for r in report)
    loop_wall = round(sum(r.get("wall") or 0 for r in report), 2)
    total_wall = round(t_end - t_start, 2)

    summary = {
        "ok": bool(ok), "reason": reason,
        "n_seg": len(report), "n_para": n_para, "chars": chars,
        "n_ok": len(ok_rows), "ok_rate": rate(len(ok_rows), len(report)),
        "n_susp": len([r for r in report if (r.get("status") or "ok") != "ok"]),
        "n_fail": len(report) - len(kept),
        "n_gen": len(gen), "n_cache": len(hit), "cache_hit_rate": rate(len(hit), len(report)),
        "first_pass": len(first_pass), "first_pass_rate": rate(len(first_pass), len(gen)),
        "redraw_segs": len([r for r in gen if r.get("draws", 0) > 1]),
        "redraws_total": sum(max(0, r.get("draws", 0) - 1) for r in gen),
        "draws_total": len(all_draws),
        "runaway_draws": len([d for d in all_draws if d.get("reason") == "偏长"]),
        "short_draws": len([d for d in all_draws if d.get("reason") == "偏短"]),
        "err_draws": len([d for d in all_draws if d.get("reason") == "异常"]),
        "cap_guess_draws": len([d for d in all_draws if d.get("cap_guess")]),
        "audio_dur": round(dur, 2),
        "loop_wall": loop_wall, "total_wall": total_wall,
        "rtf_loop": rate(loop_wall, round(dur, 2)) if dur else None,
        "rtf_total": rate(total_wall, round(dur, 2)) if dur else None,
        "chars_per_sec": rate(chars, round(dur, 2)) if dur else None,
    }
    rec = {
        "kind": "run", "run_id": run_id,
        "ts_start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t_start)),
        "ts_end": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t_end)),
        "origin": getattr(opts, "origin", "") or "cli",
        "trigger": parse_trigger(getattr(opts, "trigger_json", "")),
        "src": str(src) if src else "", "src_sha1": sha1_file(src),
        "out": str(out_path) if out_path else "", "out_sha1": sha1_file(out_path),
        "out_bytes": out_path.stat().st_size if (out_path and out_path.is_file()) else 0,
        "params": params_snapshot(opts, voice),
        "summary": summary,
        "argv": [str(x) for x in sys.argv[1:]],
        "code": {"tool": "to_audio", "git": git_rev()},
        "segs": segs,
    }
    if getattr(opts, "no_record", False):
        return rec
    root = record_root(opts)
    try:
        detail = root / "detail" / f"{run_id}.jsonl"
        detail.parent.mkdir(parents=True, exist_ok=True)
        detail.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        light = dict(rec, segs=[{k: v for k, v in s.items() if k != "draw_log"} for s in segs],
                     detail=str(detail))
        append_jsonl(root / LEDGER_NAME, light)
        print(f"  [记录] {root / LEDGER_NAME}（run {run_id}；明细 {detail.name}）")
    except OSError as exc:
        print(f"  [警告] 写运行记录失败（{exc}）—— 不影响这次合成")
    return rec


class SegCache:
    """逐句缓存：`<根>/<公共键>/<句子指纹>.wav`。

    公共键含引擎 / 音色 / 参考 / ckpt / 采样 / seed / **抽卡作用域**；
    句子指纹只由**句文本**决定 ⇒ **同一句在「先跑前 6 句验证、再跑全篇」两次运行里命中同一个文件**，
    验证那一遍不算白跑（早先我把整篇文本与输出文件名也塞进键里，那会让两次运行互不复用）。

    抽卡作用域（`opts.draw_scope`）：`--count N` 或 `--draw` 时每一发各自一个作用域
    （用输出文件名，天然唯一），否则统一 `single` —— 这样抽卡每发独立，
    而**重跑同一发**或只 `--redo-lines` 几句时又全部秒过。
    根目录默认取 `PIPELINE_TTS_SEG_CACHE` → `PIPELINE_TTS_TMPDIR/read_cache` → 系统临时目录，
    所以在本项目里它落在 `pipeline-specialized/tts/run/tmp/read_cache/`（持久，不会被每次运行的临时夹删掉）。
    """

    def __init__(self, root: Path | None, key: str):
        self.dir = (Path(root) / key) if root else None
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def default_root() -> Path | None:
        env = os.environ.get("PIPELINE_TTS_SEG_CACHE")
        root = Path(env) if env else Path(os.environ.get("PIPELINE_TTS_TMPDIR") or tempfile.gettempdir())
        return root / "read_cache"

    def path(self, text: str) -> Path | None:
        if self.dir is None:
            return None
        return self.dir / (text_fp(text) + ".wav")


# ── 主流程 ────────────────────────────────────────────────────────
def synth_all(chunks: list[str], opts: argparse.Namespace, voice: dict,
              workdir: Path, cache: "SegCache | None" = None,
              redo: frozenset = frozenset()) -> tuple[list[tuple[str, Path]], list[dict]]:
    """逐句合成。返回 ([(句子, 原始 wav)], 逐句报告)。

    长文本（几十上百句）在原来那套「整段拼完再用 `--screen` 看总时长」里有两个死穴：
    ① 一句失控（本会话实测句级 5%～13%）就能毁掉整段，还得人耳回听定位；
    ② 整段重抽一遍要十几分钟，而问题通常只在 1～2 句。
    所以这里做三件事：
      · **逐句筛**：`预期 = 字数 / 语速`；音频落在 `[字数/15, max(预期×retry_factor, 预期+4)]` 之外
        就算可疑（太长 = 重复/失控，太短 = 语种或参考文本不对）；
      · **就地重抽**：可疑就换一发重抽（`--retry-draws`，默认 3 次），抽到落在窗口内就停；
        全都不合格时取最接近预期的一发并在报告里标红；
      · **逐句缓存**：合格的一发落进 `SegCache`，重跑 / 只补几句时直接复用。
    """
    engine = None
    if opts.engine == "genie":
        engine = GenieEngine(opts, voice)
        engine.prepare()
    elif opts.engine == "qwen":
        engine = QwenEngine(opts, voice)
        engine.prepare()
    elif opts.engine == "gptsovits" and not service_alive(opts.host, opts.port):
        raise ToolError(f"连不上 GPT-SoVITS 服务 {opts.host}:{opts.port}；"
                        f"先起 python api_v2.py -a {opts.host} -p {opts.port}")

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    wavs: list[tuple[str, Path]] = []
    report: list[dict] = []
    failed = 0
    total = len(chunks)
    rate = max(0.5, opts.screen_rate)
    for i, chunk in enumerate(chunks, 1):
        out = raw_dir / f"{i:04d}.wav"
        preview = chunk if len(chunk) <= 26 else chunk[:26] + "…"
        print(f"  [{i}/{total}] {preview}")
        t0 = time.time()

        expect = len(chunk) / rate
        lo = max(0.3, len(chunk) / 15.0)                      # 快于 15 字/秒 = 垃圾音频
        hi = max(expect * max(1.0, opts.retry_factor), expect + 4.0)
        cpath = cache.path(chunk) if cache is not None else None
        row = {"i": i, "chars": len(chunk), "expect": round(expect, 2), "text": chunk,
               "fp": text_fp(chunk), "lo": round(lo, 2), "hi": round(hi, 2),
               "draws": 0, "status": "ok", "source": "", "draw_log": []}

        if cpath is not None and cpath.is_file() and i not in redo:
            shutil.copy2(cpath, out)
            dur = probe_duration(out)
            row.update(draws=0, source="缓存", dur=round(dur, 2),
                       wall=round(time.time() - t0, 2))
            if not (lo <= dur <= hi):
                row["status"] = "可疑(缓存)"
            wavs.append((chunk, out))
            print(f"        {dur:5.2f}s（缓存，预期 {expect:.1f}s）")
            report.append(row)
            continue

        # 帧上限打满的旁证：600 帧 ≈ 48 s，正常句远短于它 ⇒ 逼近上限多半是重复循环
        cap_s = (opts.qwen_max_new_tokens / 12.5) if opts.engine == "qwen" else 0.0
        best: tuple[float, float] | None = None                 # (是否合格, 时长)
        for attempt in range(1, max(1, opts.retry_draws) + 2):
            t1 = time.time()
            try:
                if engine is not None:      # genie / qwen：引擎实例已加载好
                    engine.synth(chunk, out)
                elif opts.engine == "gptsovits":
                    synth_gptsovits(chunk, out, opts, voice)
                else:
                    synth_sapi(chunk, out, opts, voice)
            except ToolError as exc:
                row["draw_log"].append({"i": i, "attempt": attempt, "ok": False,
                                        "reason": "异常", "wall": round(time.time() - t1, 2),
                                        "err": str(exc).splitlines()[0][:160]})
                if attempt <= max(1, opts.retry_draws) + 1:
                    print(f"        重试（{str(exc).splitlines()[0][:70]}）")
                    time.sleep(1.0)
                    continue
                print(f"        [失败] {str(exc)[:200]}")
                out = None
                break
            dur = probe_duration(out)
            ok = lo <= dur <= hi
            row["draws"] = attempt
            row["draw_log"].append({
                "i": i, "attempt": attempt, "dur": round(dur, 2), "ok": bool(ok), "wall": round(time.time() - t1, 2),
                "reason": "" if ok else ("偏长" if dur > hi else "偏短"),
                "cap_guess": bool(cap_s and dur >= 0.9 * cap_s),
            })
            if ok:
                best = (1.0, dur)
                break
            worse = "偏长（疑似失控）" if dur > hi else "偏短（疑似语种/参考文本不对）"
            print(f"        第 {attempt} 发 {dur:.2f}s {worse}（预期 {expect:.1f}s，窗口 "
                  f"{lo:.1f}–{hi:.1f}s）")
            best = (0.0, dur) if best is None else (0.0, min(dur, best[1]) if dur > hi else best[1])

        if out is not None and out.is_file():
            dur = probe_duration(out)
            row["dur"] = round(dur, 2)
            row["source"] = "生成"
            if best is not None and best[0] < 1.0:
                row["status"] = "可疑(已重抽仍不合格)"
                print(f"        [警告] 抽了 {row['draws']} 发都不在窗口内，取了 {dur:.2f}s"
                      f"（预期 {expect:.1f}s）—— 这一句建议 --redo-lines {i} 再抽，或改文本")
            if dur < lo:
                print(f"        [警告] 只合成出 {dur:.2f}s（{len(chunk)} 字），短得离谱 ——"
                      f" 多半是参考音频的语种或逐字文本不对，检查 --prompt-lang / meta 的 ref_lang")
            if cpath is not None and row["status"] == "ok":
                shutil.copy2(out, cpath)                         # 只有合格发才进缓存
            wavs.append((chunk, out))
            row["wall"] = round(time.time() - t0, 2)
            print(f"        {row['wall']:5.2f}s")
        else:
            failed += 1
        report.append(row)

    if failed:
        print(f"  [警告] {failed}/{total} 句合成失败，已跳过")
    return wavs, report


def process_one(src: Path, out_path: Path, opts: argparse.Namespace,
                voice: dict) -> bool:
    """处理单个文本文件。"""
    run_id = new_run_id()                       # 这一次运行的编号（数据账的主键）
    t_start = time.time()
    print(f"  读取: {src}")
    speakable = read_text(src)
    if not opts.raw:
        speakable = md_to_speakable(speakable)
    chunks, para_of = split_sentences_with_para(speakable, opts.max_chars, opts.min_chars)
    if not chunks:
        print("  [跳过] 洗掉 Markdown 标记后没有可朗读的文字")
        emit_run_safe(opts, voice, src, None, [], run_id, t_start, 0.0,
                 ok=False, reason="清洗后没有可朗读的文字")
        return False
    n_para = (max(para_of) + 1) if para_of else 0
    print(f"  切句: {len(chunks)} 句，共 {sum(len(c) for c in chunks)} 字"
          + (f"，{n_para} 段" if n_para else ""))

    if opts.dry_run:
        for i, c in enumerate(chunks, 1):
            print(f"    {i:>3}. {c}")
        return True

    key = seg_cache_key(opts, voice)
    cache = None
    if not opts.no_cache:
        cache = SegCache(Path(opts.seg_cache) if opts.seg_cache else SegCache.default_root(), key)
        print(f"  逐句缓存: {cache.dir}")
    redo = frozenset(int(x) for x in opts.redo_lines.replace("，", ",").split(",") if x.strip().isdigit())
    if redo:
        print(f"  --redo-lines: 强制重抽第 {sorted(redo)} 句（其余走缓存）")

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
        try:
            pairs, report = synth_all(chunks, opts, voice, workdir, cache=cache, redo=redo)
        except ToolError as exc:
            # 引擎加载 / 连服务失败这种「一句都没跑起来」的也算一次运行，记下来
            emit_run_safe(opts, voice, src, None, [], run_id, t_start, 0.0, n_para=n_para,
                     ok=False, reason=f"合成中断：{str(exc).splitlines()[0][:200]}")
            raise
        if not pairs:
            print("  [失败] 没有一句合成成功")
            emit_run_safe(opts, voice, src, None, report, run_id, t_start, 0.0, n_para=n_para,
                     ok=False, reason="没有一句合成成功")
            return False

        # 段与段之间用更长的静音（`--para-gap`；不给就与 `--gap` 相同 ⇒ 行为与以前一致）
        para_gap = opts.gap if opts.para_gap is None else opts.para_gap
        kept_idx = [r["i"] for r in report if "dur" in r]
        gaps = [para_gap if para_of[kept_idx[k] - 1] != para_of[kept_idx[k - 1] - 1] else opts.gap
                for k in range(1, len(kept_idx))]

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
        pgap = workdir / "_pgap.wav"
        head = workdir / "_head.wav"
        tail = workdir / "_tail.wav"
        if opts.gap > 0 and any(g == opts.gap for g in gaps):
            make_silence(opts.gap, rate, gap)
        if para_gap > 0 and abs(para_gap - opts.gap) > 1e-6 and any(g != opts.gap for g in gaps):
            make_silence(para_gap, rate, pgap)
        if opts.head_pad > 0:
            make_silence(opts.head_pad, rate, head)
        if opts.tail_pad > 0:
            make_silence(opts.tail_pad, rate, tail)

        parts: list[Path] = []
        if opts.head_pad > 0:
            parts.append(head)
        for i, w in enumerate(shaped):
            if i > 0 and gaps[i - 1] > 0:
                parts.append(gap if abs(gaps[i - 1] - opts.gap) < 1e-6 else pgap)
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
                t = end + (gaps[idx - 1] if idx < len(pairs) else 0.0)
            srt_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"  [时间轴] {srt_path}（{len(pairs)} 条，{fmt_ts(t)} 结束）")

        # ── 逐句报告：长文本靠它定位「第几句有问题」，不用整段回听 ──
        rep = out_path.with_name(out_path.stem + "_report.txt")
        exp_total = sum(r["expect"] for r in report) + sum(gaps) + (opts.head_pad + opts.tail_pad)
        susp = [r for r in report if r.get("status", "ok") != "ok"]
        hits = [r for r in report if r.get("source") == "缓存"]
        rl = [f"稿子: {src}", f"输出: {out_path.name}（{dur:.2f}s，按字数+静音预期 {exp_total:.1f}s）",
              f"引擎: {opts.engine}｜音色: {voice.get('name', '')}｜ckpt: {opts.qwen_ckpt or '-'}",
              f"采样: talker {opts.qwen_temp}/{opts.qwen_top_p}｜sub-talker {opts.qwen_sub_temp}"
              f"｜上限 {opts.qwen_max_new_tokens} 帧｜seed {opts.seed}",
              f"缓存: {cache.dir if cache else '（--no-cache）'}｜命中 {len(hits)} 句"
              f"｜可疑 {len(susp)} 句｜失败 {len(report) - len(kept_idx)} 句",
              "", f"{'#':>3} {'字数':>4} {'预期':>6} {'实际':>6} {'抽数':>4} {'来源':<4} 状态   文本"]
        for r in report:
            rl.append(f"{r['i']:>3} {r['chars']:>4} {r['expect']:>6.2f} "
                      f"{r.get('dur', 0):>6.2f} {r['draws']:>4} {r.get('source', ''):<4} "
                      f"{r.get('status', ''):<8} {r['text'][:34]}")
        if susp:
            rl += ["", "可疑句（建议 --redo-lines 后重抽这几句）：",
                   "  " + ", ".join(str(r["i"]) for r in susp)]
        rep.write_text("\n".join(rl) + "\n", encoding="utf-8")
        print(f"  [逐句报告] {rep}"
              + (f"（可疑 {len(susp)} 句：{', '.join(str(r['i']) for r in susp)}）" if susp else "（全部合格）"))

        # ── 生产数据账：这次运行的参数、逐句结果、每一发的时长与被拒原因 ──
        emit_run_safe(opts, voice, src, out_path, report, run_id, t_start, dur, n_para=n_para)
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


def screen_draws(outs: list[Path], src: Path, opts: argparse.Namespace) -> None:
    """抽卡自动筛：这条链路最贵的失败模式是「重复 / 失控生成」，特征是**时长明显超长**
    （261003 实测：2.29 s 的一句被生成成 31.92 s，撞在生成上限上；而正常发与真人的
    时长差都在 ±30% 以内）。

    按字数估一条上限（`字数 / 语速 × 倍数`，再加句间静音），超限的那发**改名加 `_rej` 标记**
    并在结尾汇总 —— 免得把垃圾发当成品听或交付。判据只看时长，不用再加载模型，四种引擎通用。
    """
    text = read_text(src)
    if not opts.raw:
        text = md_to_speakable(text)
    chunks = split_sentences(text, opts.max_chars, opts.min_chars)
    chars = sum(len(c) for c in chunks)
    if not chars:
        return
    expect = chars / max(0.5, opts.screen_rate) + max(0, len(chunks) - 1) * DEFAULT_GAP
    limit = expect * max(1.0, opts.screen_factor)
    print(f"\n[筛发] {chars} 字 / {len(chunks)} 句 ⇒ 预期约 {expect:.1f}s，上限 {limit:.1f}s")
    kept, rejected = [], []
    for p in outs:
        dur = probe_duration(p)
        if dur > limit and dur - expect > 8.0:
            new = p.with_name(f"{p.stem}_rej_{dur:.0f}s{p.suffix}")
            try:
                p.rename(new)
                rejected.append((new, dur))
                print(f"  ⚠ 疑似失控：{p.name} {dur:.1f}s（预期 {expect:.1f}s）→ 改名为 {new.name}")
            except OSError as exc:                                   # noqa: BLE001
                rejected.append((p, dur))
                print(f"  ⚠ 疑似失控：{p.name} {dur:.1f}s（改名失败，请看时长自行判断：{exc}）")
        else:
            kept.append(p)
    print(f"  合格 {len(kept)} 发" + (f"，疑似失控 {len(rejected)} 发（文件名带 _rej）" if rejected else ""))
    if not kept:
        print("  [注意] 这一批全被判为疑似失控：建议整批重抽，别硬用")


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
  python tools/common/text/to_audio.py 稿子.txt --engine qwen --voice si_c -o test/稿子.mp3
  python tools/common/text/to_audio.py 稿子.txt --engine qwen --voice si_q --no-record   # 不记数据账
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
    parser.add_argument("--screen", action="store_true",
                        help="抽卡自动筛：按「字数 → 预期时长」算上限，超限的那发改名加 `_rej` 标记"
                             "（261003 实测这链路会失控生成，2.29 s 的句子能出 31.92 s；建议配 --count 用）")
    parser.add_argument("--screen-rate", type=float, default=5.5,
                        help="预期语速（字/秒），只用来估上限 [默认: 5.5]")
    parser.add_argument("--screen-factor", type=float, default=2.5,
                        help="超过「预期 × 该倍数」且长出 8 秒以上 ⇒ 判为疑似失控 [默认: 2.5]")
    parser.add_argument("--retry-draws", type=int, default=3, metavar="N",
                        help="逐句筛：一句的时长落在窗口外（太长 = 重复/失控，太短 = 语种/参考文本不对）时"
                             "就地重抽，最多再抽 N 次 [默认: 3；0 = 不重抽，只报告]")
    parser.add_argument("--retry-factor", type=float, default=2.0,
                        help="逐句「太长」的判据：超过 max(预期 × 该倍数, 预期 + 4 s) [默认: 2.0]")
    parser.add_argument("--redo-lines", default="", metavar="句号",
                        help="只强制重抽这几句（逗号分隔，1 起），其余句走逐句缓存 —— "
                             "长文本里定位到第 12 句不行时用它，不用整段重跑")
    parser.add_argument("--seg-cache", default=None,
                        help="逐句缓存目录 [默认: 环境变量 PIPELINE_TTS_SEG_CACHE 或 "
                             "PIPELINE_TTS_TMPDIR/read_cache，本项目里即 pipeline-specialized/tts/run/tmp/read_cache]")
    parser.add_argument("--no-cache", action="store_true",
                        help="关掉逐句缓存（每次全部重新合成）")
    parser.add_argument("--check", action="store_true", help="仅检查环境")
    parser.add_argument("--list-voices", action="store_true", help="列出可用音色")
    parser.add_argument("--register", metavar="音色名",
                        help="把 --ref-audio 注册成音色（写入 voices/<音色名>/）；"
                             "配合 --ref-text 与 --prompt-lang 指定逐字文本与参考语种")
    parser.add_argument("--dry-run", action="store_true", help="只做清洗与切句，不合成")
    parser.add_argument("--raw", action="store_true",
                        help="原样朗读：跳过 Markdown 清洗。稿子里有 $ 公式、**强调**、"
                             "「1.」编号行时用它，免得那些字符被静默吃掉")

    d = parser.add_argument_group("数据账（生产用的运行记录）")
    d.add_argument("--record-dir", default=None,
                   help="运行记录落点 [默认: 环境变量 PIPELINE_TTS_RECORD_DIR 或 "
                        f"{DEFAULT_RECORD_ROOT}]；每次运行写 detail/<run_id>.jsonl 并追加 {LEDGER_NAME}")
    d.add_argument("--no-record", action="store_true", help="这次运行不写记录")
    d.add_argument("--origin", default="cli",
                   help="触发来源标签（界面会传 gui）[默认: cli]")
    d.add_argument("--trigger-json", default="",
                   help="触发侧上下文（界面勾了哪些选项等）的 JSON，原样存进这次运行的记录")

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
    g.add_argument("--model", default=None,
                   help="模型代号（只用于日志与交接，例如 GUI 传的 si-ft；不影响权重）")
    g.add_argument("--gpt-weights", default=None,
                   help="gptsovits：合成前把服务的 GPT(t2s) 权重热切到该路径（绝对路径或相对 "
                        "GPT_SoVITS/ 的相对路径）。服务没在跑就直接报错，不会静默忽略")
    g.add_argument("--sovits-weights", default=None,
                   help="gptsovits：同上，换 SoVITS(vits) 权重；只想换一个时另一个留空即可")
    g.add_argument("--speed", type=float, default=1.0,
                   help="语速倍率（仅 gptsovits；qwen / genie 不支持）[默认: 1.0]")
    g.add_argument("--seed", type=int, default=-1,
                   help="随机种子（gptsovits / qwen）[默认: -1 随机]")
    g.add_argument("--qwen-model", default=None,
                   help=f"Qwen3-TTS 模型目录 [默认: 环境变量 QWEN_TTS_MODEL 或 {DEFAULT_QWEN_MODEL}]")
    g.add_argument("--qwen-ckpt", default=None,
                   help="Qwen 微调权重（本项目的 ckpt_epN.pt：只含可训参数的 state dict，"
                        "叠加到 --qwen-model 的底座上；命中/未命中/多余张量数会打印出来）")
    g.add_argument("--qwen-device", default=None,
                   help="Qwen 推理设备 [默认: 有 CUDA 用 cuda:0，否则 cpu]")
    g.add_argument("--qwen-attn", default=DEFAULT_QWEN_ATTN,
                   choices=("sdpa", "flash_attention_2", "eager"),
                   help=f"Qwen 的 attention 实现 [默认: {DEFAULT_QWEN_ATTN}"
                        f" —— 实测比 flash_attention_2 快，装了 FA2 也别切]")
    g.add_argument("--qwen-temp", type=float, default=DEFAULT_QWEN_TEMP,
                   help=f"Qwen talker 采样温度 [默认: {DEFAULT_QWEN_TEMP}"
                        f" —— 官方默认 0.9 会「颤」，实测收到 0.7 明显好]")
    g.add_argument("--qwen-top-p", type=float, default=DEFAULT_QWEN_TOP_P,
                   help=f"Qwen talker top_p [默认: {DEFAULT_QWEN_TOP_P}]")
    g.add_argument("--qwen-sub-temp", type=float, default=DEFAULT_QWEN_SUB_TEMP,
                   help=f"Qwen sub-talker（code_predictor，管 16 个码本里的 15 个）采样温度"
                        f" [默认: {DEFAULT_QWEN_SUB_TEMP} —— 成对实测 0.9 合格 4/8、0.5 合格 8/8]")
    g.add_argument("--qwen-max-new-tokens", type=int, default=DEFAULT_QWEN_MAX_NEW,
                   help=f"Qwen 单句生成长度上限（codec 帧，12.5 帧/秒）"
                        f" [默认: {DEFAULT_QWEN_MAX_NEW} ≈ {DEFAULT_QWEN_MAX_NEW / 12.5:.0f} s"
                        f" —— 包内默认 2048（≈164 s），遇到重复循环会空转十几分钟]")

    v = parser.add_argument_group("音色")
    v.add_argument("--voice", default=None, help="音色名（voices/<名>/ 或 GENIE 预设角色名）")
    v.add_argument("--ref-audio", default=None,
                   help="临时参考音频（覆盖 --voice；sapi 不用）")
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
    p.add_argument("--para-gap", type=float, default=None,
                   help="**段落之间**的静音秒数（段落 = 源稿里用空行分开的块）。诗体 / 分行写的稿子"
                        "行间的呼吸比句内停顿重要；不给则与 --gap 相同（行为与以前一致）")
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

    if opts.engine == "qwen" and not opts.dry_run:
        # 提前打招呼：实测 RTF ≈ 4，长稿要等十几分钟，不然会被当成卡死
        print("[提示] Qwen 实测约 4 倍实时（音频时长的 4 倍，另有每句约 10 秒固定开销）；"
              "1000 字稿子约 13 分钟。按句打印进度，请耐心等\n")

    voice = resolve_voice(opts)
    if voice.get("name"):
        print(f"音色: {voice['name']}（{voice['kind']}）")
        if voice.get("prompt_wav"):
            print(f"  参考音频: {Path(voice['prompt_wav']).name}")

    if opts.engine == "gptsovits" and (opts.gpt_weights or opts.sovits_weights):
        # 只在这里切一次：切权重要几秒到几十秒，绝不能每句都切；--dry-run 不切（不动服务）
        if opts.dry_run:
            print("[跳过] --dry-run：不切服务权重")
        elif not service_alive(opts.host, opts.port):
            raise ToolError(f"要切权重但连不上服务 {opts.host}:{opts.port}；"
                            f"先起 python api_v2.py -a {opts.host} -p {opts.port}")
        else:
            for line in apply_gptsovits_weights(opts.gpt_weights, opts.sovits_weights, opts):
                print("  " + line)

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
        if opts.model:
            print(f"模型: {opts.model}")
        if not draw and outs[0].exists() and not opts.force and not opts.dry_run:
            print(f"[跳过] {outs[0].name} 已存在，本次什么都没生成"
                  f"（加 -f 覆盖，或加 --draw／--count 每跑一次出新文件）")
            sys.exit(0)
        all_ok = True
        made: list[Path] = []
        for i, out_p in enumerate(outs, 1):
            if len(outs) > 1:
                print(f"── 版本 {i}/{len(outs)}：{out_p.name}")
            # 抽卡作用域：多版本 / --draw 时每发各自独立（用输出名区分），否则共用 single
            opts.draw_scope = out_p.name if draw else "single"
            if not process_one(input_path, out_p, opts, voice):
                all_ok = False
            elif out_p.exists():
                made.append(out_p)
        if opts.screen and made and not opts.dry_run:
            screen_draws(made, input_path, opts)
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
        ok_one = True
        for out_p in outs:
            # 抽卡作用域同上：多版本时每发各自独立，否则共用 single
            opts.draw_scope = out_p.name if draw else "single"
            if not process_one(src, out_p, opts, voice):
                ok_one = False
        if ok_one:
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
