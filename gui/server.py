#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — pipeline GUI（本地 Web 界面后端）

纯标准库实现：http.server 提供静态页面 + JSON API，转换逻辑复用 tools/ 下现有 CLI 脚本。
仅监听 127.0.0.1，供本机人工操作。

启动:
    python server.py [--port 8765]          # 自动打开浏览器
    python server.py --no-browser           # 不自动开浏览器

API:
    GET  /                     → 静态页面（gui/static/index.html）
    GET  /api/health           → {"ok": true}
    GET  /api/browse?dir=...   → 列目录（仅限 jobs/ 根内，越界自动回落根）
    POST /api/convert          → {"results": [{file, ok, steps:[{step, ok, out, err}]}]}
        body: {files:[...], target: "pdf"|"tex"|"json"|"md"|"docx"|"lint"|"image",
               engine, outdir, open_pdf, recursive, force}
        输入类型 × 目标见 SUPPORTED_EXTS：md → pdf/tex/json/lint；tex → pdf；
        ppt/pptx → json/md/tex/docx/pdf（.ppt 先经 tools/common/ppt/to_pptx.py 转 OOXML）
        force：OCR 覆盖已存在产物；也用于 .ppt → .pptx 覆盖同名中间文件
        image 目标：files 为目录（批量 OCR），输出默认落源目录的下一转槽位
    outdir 留空时的默认输出目录：源在任务槽位目录 N_xxx/ 里（如 0_md/）→ 同级
    (N+1)_<目标类型>/（0_md/note.md 转 PDF → 1_pdf/，规则见 pipeline/jobs_template.md）；
    源不在槽位里 → 与源文件同目录。OCR 是目录级输入，源目录不在槽位里时回落 上级/2_md。
    POST /api/watch            → 开始/停止监听
        body: {action: "start"|"stop", file, outdir}
    GET  /api/watch            → {running, file, lines:[...]}
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent          # pipeline/gui/
ROOT = BASE.parent                              # pipeline/
TOOLS = ROOT / "tools"
STATIC = BASE / "static"

sys.path.append(str(TOOLS))                     # 复用 tools/_lib 的槽位解析（放末尾，避免遮挡同名模块）
from _lib import common as _common  # noqa: E402

PY = sys.executable

# 目标格式 → 槽位类型短名（源在 N_xxx/ 里时，默认输出到同级 (N+1)_<短名>/）
_TARGET_SLOT = {"pdf": "pdf", "tex": "tex", "json": "json", "md": "md",
                "docx": "docx", "lint": "json"}


def _resolve_py_tts() -> str:
    """TTS 工具需要装了 genie-tts 的解释器；它通常不是启动本 GUI 的那个。

    GUI 子进程用的是 `PY = sys.executable`，而 genie-tts 装在独立环境里，
    所以文本转语音这一路必须换解释器，否则界面看着正常、一转换就报缺库。
    """
    env = os.environ.get("PIPELINE_TTS_PY")
    if env and Path(env).is_file():
        return env
    default = Path(r"D:\mamba\envs\tts\python.exe")
    return str(default) if default.is_file() else PY


PY_TTS = _resolve_py_tts()
GENIE_ROOT = os.environ.get("GENIE_ROOT", r"D:\tools\genie")
TEXT_TO_AUDIO = TOOLS / "common" / "text" / "to_audio.py"
TO_PDF = TOOLS / "common" / "md" / "to_pdf.py"
TO_JSON = TOOLS / "common" / "md" / "to_json.py"
JSON_TO_TEX = TOOLS / "common" / "json" / "to_tex.py"
IMAGE_TO_MD = TOOLS / "common" / "images" / "to_md.py"
# PPT 链：.ppt 先转 OOXML，再由 python-pptx 提取 JSON，最后渲染目标格式
PPT_TO_PPTX = TOOLS / "common" / "ppt" / "to_pptx.py"
PPTX_TO_JSON = TOOLS / "common" / "pptx" / "to_json.py"
JSON_TO_MD = TOOLS / "common" / "json" / "to_md.py"
JSON_TO_DOCX = TOOLS / "common" / "json" / "to_docx.py"

# 文件浏览根目录：仅限 jobs/（任务归档）
JOBS_ROOT = ROOT / "jobs"

MAX_LOG = 2000  # 单步日志截断长度


def _run(cmd: list[str]) -> dict:
    """跑一个子进程，返回 {ok, out, err}。"""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        return {
            "ok": r.returncode == 0,
            "out": (r.stdout or "")[-MAX_LOG:],
            "err": (r.stderr or "")[-MAX_LOG:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": "超时（>600s）被终止"}


# 各目标格式可接受的输入扩展名（前端 TARGET_EXTS 与此保持一致）
SUPPORTED_EXTS = {
    "pdf": (".md", ".tex", ".ppt", ".pptx"),
    "tex": (".md", ".ppt", ".pptx"),
    "json": (".md", ".ppt", ".pptx"),
    "md": (".ppt", ".pptx"),
    "docx": (".ppt", ".pptx"),
    "lint": (".md",),
    "audio": (".md", ".markdown", ".txt"),
}


def _ppt_steps(file: Path, target: str, engine: str, out: Path,
               open_pdf: bool, force: bool) -> list[dict]:
    """PPT 链：(.ppt → .pptx) → pptx → JSON → 目标格式。JSON 是共用中间态。"""
    stem = file.stem
    steps: list[dict] = []
    pptx = file

    if file.suffix.lower() == ".ppt":
        # 老格式是 OLE2 复合文档，python-pptx 读不了：先转 OOXML
        # （后端顺序 PowerPoint COM → WPS COM → LibreOffice，见 tools/common/ppt/to_pptx.py）
        pptx = out / f"{stem}.pptx"
        cmd = [PY, str(PPT_TO_PPTX), str(file), "-o", str(pptx)]
        if force:
            cmd.append("-f")
        steps.append({"step": "ppt → pptx（Office COM）", "cmd": cmd})

    js = out / f"{stem}.json"
    steps.append({"step": "pptx → JSON",
                  "cmd": [PY, str(PPTX_TO_JSON), str(pptx), "-o", str(js)]})

    if target == "json":
        return steps
    if target == "md":
        steps.append({"step": "JSON → MD",
                      "cmd": [PY, str(JSON_TO_MD), str(js), "-o", str(out / f"{stem}.md")]})
        return steps
    if target == "docx":
        steps.append({"step": "JSON → DOCX",
                      "cmd": [PY, str(JSON_TO_DOCX), str(js), "-o", str(out / f"{stem}.docx")]})
        return steps
    if target in ("tex", "pdf"):
        tex = out / f"{stem}.tex"
        steps.append({"step": "JSON → TEX（slides 结构，article 模式）",
                      "cmd": [PY, str(JSON_TO_TEX), str(js), "-o", str(tex)]})
        if target == "tex":
            return steps
        cmd = [PY, str(TO_PDF), str(tex), "-o", str(out / f"{stem}.pdf"), "--engine", engine]
        if open_pdf:
            cmd.append("--open")
        steps.append({"step": "TEX → PDF", "cmd": cmd})
        return steps
    return []


def _steps_for(file: Path, target: str, engine: str, outdir: str,
               open_pdf: bool, force: bool = False,
               opts: dict | None = None) -> list[dict]:
    """按目标格式拼装子进程步骤（返回空列表 = 该「输入类型 + 目标」组合不支持）。

    已接入: md（json/tex/pdf/lint/audio）、tex（pdf）、ppt/pptx（json/md/tex/docx/pdf）、
            md/txt（audio，文本转语音，走独立解释器 PY_TTS）。
    预留扩展位: sql → drawio（tools/common/sql/to_drawio.py，需再加一个 drawio 目标）。
    """
    opts = opts or {}
    stem = file.stem
    ext = file.suffix.lower()
    # 留空 outdir = 自动槽位：源在 N_xxx/ 里 → 同级 (N+1)_<目标类型>/；否则与源同目录
    if target == "audio":
        slot_suffix = str(opts.get("audio_format") or "mp3")
    else:
        slot_suffix = _TARGET_SLOT.get(target, target)
    out = Path(outdir) if outdir else (_common.slot_dir(file, slot_suffix) or file.parent)

    if ext in (".ppt", ".pptx"):
        return _ppt_steps(file, target, engine, out, open_pdf, force)

    if target == "audio":
        if ext not in (".md", ".markdown", ".txt"):
            return []
        fmt = opts.get("audio_format") or "mp3"
        cmd = [PY_TTS, str(TEXT_TO_AUDIO), str(file),
               "-o", str(out / f"{stem}.{fmt}"),
               "--format", fmt, "--genie-root", GENIE_ROOT]
        if force:
            cmd.append("-f")
        if opts.get("voice"):
            cmd += ["--voice", str(opts["voice"])]
        for key, flag in (("audio_gap", "--gap"), ("audio_tail_pad", "--tail-pad"),
                          ("audio_head_pad", "--head-pad"), ("audio_fade", "--fade"),
                          ("audio_trim_tail", "--trim-tail-ms")):
            val = opts.get(key)
            if val not in (None, ""):
                cmd += [flag, str(val)]
        if opts.get("audio_srt"):
            cmd.append("--srt")
        return [{"step": f"文本 → {fmt.upper()}（TTS）", "cmd": cmd}]

    if target == "pdf":
        if ext in (".md", ".tex"):
            cmd = [PY, str(TO_PDF), str(file), "-o", str(out / f"{stem}.pdf"), "--engine", engine]
            if open_pdf:
                cmd.append("--open")
            return [{"step": "md/tex → PDF", "cmd": cmd}]
        return []
    if target == "tex":
        if ext == ".md":
            return [
                {"step": "md → JSON", "cmd": [PY, str(TO_JSON), str(file), "-o", str(out / f"{stem}.json")]},
                {"step": "JSON → TEX", "cmd": [PY, str(JSON_TO_TEX), str(out / f"{stem}.json"), "-o", str(out / f"{stem}.tex")]},
            ]
        return []
    if target == "json":
        if ext == ".md":
            return [{"step": "md → JSON", "cmd": [PY, str(TO_JSON), str(file), "-o", str(out / f"{stem}.json")]}]
        return []
    if target == "lint":
        if ext == ".md":
            return [{"step": "md lint 检查", "cmd": [PY, str(TO_JSON), str(file), "--lint", "-o", str(out / f"{stem}.json")]}]
        return []
    return []


def convert_one(file: Path, target: str, engine: str, outdir: str,
                open_pdf: bool, force: bool = False,
                opts: dict | None = None) -> dict:
    steps = _steps_for(file, target, engine, outdir, open_pdf, force, opts)
    if not steps:
        ok_exts = "、".join(SUPPORTED_EXTS.get(target, ())) or "（无）"
        err = (f"不支持: {file.name} + 目标 {target}"
               f"（目标 {target} 支持的输入类型: {ok_exts}；"
               f"当前输入: {file.suffix.lower() or '无扩展名'}）")
        return {"file": str(file), "ok": False, "steps": [
            {"step": "校验", "ok": False, "out": "", "err": err}]}

    results = []
    for s in steps:
        r = _run(s["cmd"])
        results.append({"step": s["step"], "ok": r["ok"], "out": r["out"], "err": r["err"]})
        if not r["ok"]:
            break
    return {"file": str(file), "ok": all(x["ok"] for x in results), "steps": results}


_IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif", ".gif"}


def _count_images(p: Path, recursive: bool) -> int:
    """统计目录下图片数量（顶层或递归）。"""
    if recursive:
        return sum(1 for x in p.rglob("*") if x.is_file() and x.suffix.lower() in _IMG_EXTS)
    return sum(1 for x in p.iterdir() if x.is_file() and x.suffix.lower() in _IMG_EXTS)


def convert_image_dirs(dirs: list[str], outdir: str, recursive: bool, force: bool) -> list[dict]:
    """图片 → MD（OCR）：目录级批量，输出默认落到源目录的下一转槽位。

    源目录名是 N_xxx/（如 0_jpg/）→ 同级 (N+1)_md/；不在槽位里时回落上级 2_md/。
    """
    results = []
    for d in dirs:
        p = Path(d)
        if not p.is_dir():
            tip = "已选的是文件而非目录：图片→MD 需要选「含图片的目录」——进入该目录后点顶部「📥 选当前目录」，或在上级目录点目录行尾 ＋"
            results.append({"file": d, "ok": False, "steps": [
                {"step": "校验", "ok": False, "out": "", "err": tip}]})
            continue

        # 转换前预扫：无图片直接报错，避免脚本"成功但无产出"的静默失败
        n_img = _count_images(p, recursive)
        if n_img == 0:
            hint = "（图片可能在子目录：请选择含图片的目录，或勾选“递归子目录”）"
            results.append({"file": d, "ok": False, "steps": [
                {"step": "图片检查", "ok": False, "out": "", "err": f"目录下未找到图片{hint}"}]})
            continue

        out = Path(outdir) if outdir else (_common.slot_dir(p, "md", is_dir=True)
                                          or (p.parent / "2_md"))
        before = len(list(out.glob("*.md"))) if out.exists() else 0
        cmd = [PY, str(IMAGE_TO_MD), "-i", str(p), "-o", str(out)]
        if recursive:
            cmd.append("-r")
        if force:
            cmd.append("-f")
        r = _run(cmd)

        # 结束后校验产出：声称成功但一个 md 都没新增 → 视为失败
        after = len(list(out.glob("*.md"))) if out.exists() else 0
        if r["ok"] and after <= before:
            results.append({"file": d, "ok": False, "steps": [
                {"step": "图片 → MD（OCR）", "ok": False, "out": r["out"],
                 "err": f"共 {n_img} 张图片，但未生成任何 .md 文件（详见上方日志，可能为 Ollama 识别失败）"}]})
            continue

        results.append({"file": d, "ok": r["ok"], "steps": [
            {"step": f"图片 → MD（OCR，{n_img} 张）", "ok": r["ok"], "out": r["out"], "err": r["err"]}]})
    return results


# ── TTS 音色 ────────────────────────────────────────────────
def tts_voices() -> dict:
    """列出可用音色（voices/ 注册的 + GENIE 预设角色），并报告 TTS 环境是否就绪。"""
    registry: list[str] = []
    builtin: list[str] = []
    vdir = ROOT / "voices"
    if vdir.is_dir():
        registry = sorted(p.name for p in vdir.iterdir()
                          if p.is_dir() and not p.name.startswith("_"))
    chardir = Path(GENIE_ROOT) / "CharacterModels" / "v2ProPlus"
    if chardir.is_dir():
        builtin = sorted(p.name for p in chardir.iterdir()
                         if (p / "tts_models").is_dir())

    ok, message = True, ""
    if not Path(PY_TTS).is_file():
        ok, message = False, f"TTS 解释器不可用: {PY_TTS}"
    elif not TEXT_TO_AUDIO.is_file():
        ok, message = False, f"找不到 {TEXT_TO_AUDIO}"
    elif not (Path(GENIE_ROOT) / "GenieData").is_dir():
        ok, message = False, f"GENIE 资源缺失: {GENIE_ROOT}\\GenieData"

    return {"ok": ok, "message": message, "registry": registry,
            "builtin": builtin, "py": PY_TTS, "genie_root": GENIE_ROOT}


# ── watch 状态 ──────────────────────────────────────────────
_watch_lock = threading.RLock()  # RLock：watch_start 内部会调用 watch_stop，需可重入
_watch_proc: subprocess.Popen | None = None
_watch_file: str = ""
_watch_lines: deque = deque(maxlen=200)


def _watch_reader(proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        _watch_lines.append(line.rstrip("\n"))


def watch_start(file: str, outdir: str) -> dict:
    global _watch_proc, _watch_file
    with _watch_lock:
        watch_stop()
        cmd = [PY, str(TOOLS / "common" / "md" / "watch.py"), file]
        if outdir:
            cmd += ["--outdir", outdir]
        _watch_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        _watch_file = file
        _watch_lines.clear()
        threading.Thread(target=_watch_reader, args=(_watch_proc,), daemon=True).start()
        return {"ok": True, "file": file}


def watch_stop() -> dict:
    global _watch_proc
    with _watch_lock:
        if _watch_proc and _watch_proc.poll() is None:
            _watch_proc.terminate()
            try:
                _watch_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _watch_proc.kill()
        _watch_proc = None
        return {"ok": True}


def watch_status() -> dict:
    with _watch_lock:
        running = bool(_watch_proc and _watch_proc.poll() is None)
        return {"running": running, "file": _watch_file, "lines": list(_watch_lines)}


# ── OCR 前置检查 / 一键启动（让"图片→MD 为什么失败"一目了然）──────

_OCR_MODEL = "qwen2.5vl:7b"
_ocr_pull_lock = threading.Lock()
_ocr_pulling = False
_ocr_pull_lines: deque = deque(maxlen=20)


def _pull_worker() -> None:
    global _ocr_pulling
    try:
        r = subprocess.run(
            ["ollama", "pull", _OCR_MODEL], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1800,
        )
        _ocr_pull_lines.append("完成: " + ("成功" if r.returncode == 0 else "失败"))
        tail = (r.stdout or "").strip()[-400:]
        if tail:
            _ocr_pull_lines.append(tail)
        tail2 = (r.stderr or "").strip()[-400:]
        if tail2:
            _ocr_pull_lines.append(tail2)
    except subprocess.TimeoutExpired:
        _ocr_pull_lines.append("拉取超时（>30 分钟）")
    finally:
        _ocr_pulling = False


def ocr_check() -> dict:
    """检查 Ollama 与视觉模型是否就绪。"""
    try:
        r = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except FileNotFoundError:
        return {"running": False, "model": False, "pulling": _ocr_pulling,
                "message": "未找到 ollama 命令，请先安装 Ollama: https://ollama.com/download"}
    except subprocess.TimeoutExpired:
        return {"running": False, "model": False, "pulling": _ocr_pulling,
                "message": "Ollama 响应超时，可能未启动"}
    if r.returncode != 0:
        return {"running": False, "model": False, "pulling": _ocr_pulling,
                "message": "Ollama 未运行：请先启动（下方「🚀 启动 Ollama」按钮或命令行 ollama serve）"}
    ok_model = _OCR_MODEL in r.stdout
    msg = "Ollama 就绪" + ("" if ok_model else f"，但缺少视觉模型 {_OCR_MODEL}，请执行拉取")
    return {"running": True, "model": ok_model, "pulling": _ocr_pulling,
            "pull_lines": list(_ocr_pull_lines), "message": msg}


def ocr_start() -> dict:
    """Detached 启动 Ollama 服务，随后返回检查结果。"""
    exe = shutil.which("ollama")
    if not exe:
        return {"started": False, "message": "未找到 ollama，请先安装 Ollama"}
    out_log = ROOT / "output"
    out_log.mkdir(parents=True, exist_ok=True)
    logf = open(out_log / "ollama_serve.log", "ab")
    try:
        subprocess.Popen(
            [exe, "serve"], stdout=logf, stderr=logf,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as e:
        logf.close()
        return {"started": False, "message": f"启动失败: {e}"}
    time.sleep(4)  # 等服务就绪
    info = ocr_check()
    return {"started": True, "message": info["message"]}


def ocr_pull() -> dict:
    """后台线程拉取视觉模型（耗时较长，不阻塞请求）。"""
    global _ocr_pulling
    with _ocr_pull_lock:
        if _ocr_pulling:
            return {"started": False, "message": "模型已在拉取中，请稍候"}
        if not shutil.which("ollama"):
            return {"started": False, "message": "未找到 ollama，请先安装"}
        _ocr_pulling = True
        _ocr_pull_lines.clear()
        threading.Thread(target=_pull_worker, daemon=True).start()
        return {"started": True, "message": f"开始拉取 {_OCR_MODEL}（可能需要几分钟，页面会自动刷新状态）"}


# ── 目录浏览（浏览器无法获取本地绝对路径，由服务端列目录）──────────

def _clamp_to_jobs(p: Path) -> Path:
    """把目录钳制在 jobs/ 根内（越界一律回落根）。"""
    try:
        p = p.resolve()
        root = JOBS_ROOT.resolve()
        if p != root and root not in p.parents:
            return root
        return p
    except OSError:
        return JOBS_ROOT


def browse(dir_path: str, depth: int = 0) -> dict:
    """列出目录内容，支持递归预取子目录树。

    depth=0: 仅当前目录（原行为）
    depth>0: 递归返回子目录的 entries（children 字段）
    depth=-1: 递归到叶子（慎用，目录树很大时会很慢）
    """
    # jobs/ 是浏览根，但它在 .gitignore 里 —— 新 clone 或刚删过数据时目录可能不存在，
    # 此时直接 iterdir() 会抛 FileNotFoundError（前端表现为"加载失败"）。这里先补建一个空目录。
    if not JOBS_ROOT.exists():
        try:
            JOBS_ROOT.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    p = Path(dir_path).expanduser() if dir_path else JOBS_ROOT
    if not p.is_dir():
        p = JOBS_ROOT
    p = _clamp_to_jobs(p)

    _SKIP_NAMES = {"readme.md", "readme.txt", "readme.rst", "readme"}

    def _scan(dir_path: Path, remaining_depth: int) -> list[dict]:
        entries = []
        try:
            for item in sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if item.name.startswith("."):
                    continue
                if item.name.lower() in _SKIP_NAMES:
                    continue
                try:
                    size = "" if item.is_dir() else _fmt_size(item.stat().st_size)
                except OSError:
                    size = ""
                entry = {"name": item.name, "is_dir": item.is_dir(), "size": size}
                if item.is_dir() and remaining_depth != 0:
                    entry["children"] = _scan(item, remaining_depth - 1 if remaining_depth > 0 else -1)
                entries.append(entry)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            pass
        return entries

    entries = _scan(p, depth)
    return {"dir": str(p), "root": str(JOBS_ROOT), "entries": entries}


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ── HTTP ────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默默认访问日志
        pass

    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            html = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")  # 避免浏览器缓存旧页面
            self.end_headers()
            self.wfile.write(html)
        elif path == "/api/health":
            self._send_json({"ok": True})
        elif path == "/api/watch":
            self._send_json(watch_status())
        elif path == "/api/browse":
            query = urlparse(self.path).query
            dir_arg = ""
            depth = 0
            for part in query.split("&"):
                if part.startswith("dir="):
                    from urllib.parse import unquote
                    dir_arg = unquote(part[4:])
                elif part.startswith("depth="):
                    try:
                        depth = int(part[6:])
                    except ValueError:
                        depth = 0
            self._send_json(browse(dir_arg, depth))
        elif path == "/api/tts/voices":
            self._send_json(tts_voices())
        elif path == "/api/ocr/check":
            self._send_json(ocr_check())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/convert":
            body = self._read_body()
            target = body.get("target", "pdf")
            if target == "image":
                # 不过滤：非目录项也逐项返回明确错误，避免静默失败
                results = convert_image_dirs(
                    [f for f in body.get("files", [])],
                    body.get("outdir", ""),
                    bool(body.get("recursive")),
                    bool(body.get("force")),
                )
            else:
                files = [f for f in body.get("files", []) if Path(f).is_file()]
                engine = body.get("engine", "xelatex")
                outdir = body.get("outdir", "")
                open_pdf = bool(body.get("open_pdf"))
                force = bool(body.get("force"))
                results = [
                    convert_one(Path(f), target, engine, outdir, open_pdf, force, body)
                    for f in files
                ]
            self._send_json({"results": results})
        elif path == "/api/watch":
            body = self._read_body()
            action = body.get("action", "")
            if action == "start":
                file = body.get("file", "")
                if file and Path(file).is_file():
                    self._send_json(watch_start(file, body.get("outdir", "")))
                else:
                    self._send_json({"ok": False, "error": "文件不存在"}, 400)
            elif action == "stop":
                self._send_json(watch_stop())
            else:
                self._send_json({"ok": False, "error": "未知 action"}, 400)
        elif path == "/api/ocr/start":
            self._send_json(ocr_start())
        elif path == "/api/ocr/pull":
            self._send_json(ocr_pull())
        else:
            self._send_json({"error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="pipeline GUI 本地服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        sys.exit(
            f"错误: 端口 {args.port} 被占用（可能有残留的 GUI 服务）。\n"
            f"  处理: 关闭旧实例后重试，或用 --port 换端口。详情: {e}"
        )
    url = f"http://127.0.0.1:{args.port}/"
    print(f"pipeline GUI 已启动: {url}  （Ctrl+C 退出）")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        watch_stop()
        print("\n已退出")


if __name__ == "__main__":
    main()
