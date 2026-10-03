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

# 静态资源扩展名 → MIME。只放行这几类：前端拆成 .js/.css 后，浏览器必须能按正确类型取到它们。
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


def resolve_static(url_path: str):
    """把 URL 路径解析成 static 目录下的真实文件；不合法或不存在都返回 None。

    安全：① 只允许 STATIC 里的文件，`resolve()` 之后必须仍在 STATIC 之内（挡目录穿越，如 /static/../server.py）；
          ② 后缀必须在白名单里（挡 .py/.log 之类被当静态文件读走）。
    """
    rel = url_path[len("/static/"):].lstrip("/")
    if not rel:
        return None
    try:
        candidate = (STATIC / rel).resolve()
    except (OSError, ValueError):
        return None
    static_root = STATIC.resolve()
    if candidate != static_root and static_root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    if candidate.suffix.lower() not in MIME_TYPES:
        return None
    return candidate


def serve_static(handler, url_path: str) -> bool:
    """命中静态资源就回包并返回 True；否则返回 False，由调用方继续按 API 路径匹配。"""
    candidate = resolve_static(url_path)
    if candidate is None:
        return False
    try:
        body = candidate.read_bytes()
    except OSError as exc:
        handler._send_json({"error": f"读取静态文件失败: {exc}"}, 500)
        return True
    handler.send_response(200)
    handler.send_header("Content-Type", MIME_TYPES[candidate.suffix.lower()])
    handler.send_header("Content-Length", str(len(body)))
    # no-store 对 JS/CSS 同样重要：否则前端改了代码，浏览器还在用旧缓存，界面上"看起来没变"
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass  # 浏览器提前断开（切页面/刷新）不算错误
    return True


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
# GPT-SoVITS(api_v2) 服务：--engine gptsovits 走 HTTP 合成，需要它常驻；默认 127.0.0.1:9880
TTS_HOST = os.environ.get("PIPELINE_TTS_HOST", "127.0.0.1")
TTS_PORT = int(os.environ.get("PIPELINE_TTS_PORT", "9880"))
TTS_SERVE_HINT = ("powershell -NoProfile -ExecutionPolicy Bypass -File "
                  + str(ROOT / "output" / "tts" / "p10_serve_si.ps1"))
# 热切权重可能要在服务侧重新加载模型，给足时间；超时只报错，不阻塞界面线程池以外的逻辑
TTS_APPLY_TIMEOUT = float(os.environ.get("PIPELINE_TTS_APPLY_TIMEOUT", "180"))


def tts_service_alive(host: str = "", port: int = 0, timeout: float = 0.8) -> bool:
    """GPT-SoVITS 服务在不在：端口能连上就算（与 to_audio.py 的判据一致）。"""
    import socket  # 只有 TTS 这一路用得到，就近导入

    try:
        with socket.create_connection((host or TTS_HOST, port or TTS_PORT), timeout=timeout):
            return True
    except OSError:
        return False

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

# 界面里「启动服务」按钮跑的就是它；换别的服务脚本用 PIPELINE_TTS_SERVE_SCRIPT 指定
TTS_SERVE_SCRIPT = Path(os.environ.get("PIPELINE_TTS_SERVE_SCRIPT",
                                       str(ROOT / "output" / "tts" / "p10_serve_si.ps1")))
_tts_serve_proc: subprocess.Popen | None = None


def tts_serve_start() -> dict:
    """在界面这一侧拉起 GPT-SoVITS 服务（模型加载要几十秒，看状态栏刷新）。"""
    global _tts_serve_proc

    if tts_service_alive():
        return {"started": False, "alive": True,
                "message": f"服务已经在跑（{TTS_HOST}:{TTS_PORT}），不用重复启动"}
    if not TTS_SERVE_SCRIPT.is_file():
        return {"started": False, "alive": False,
                "message": (f"找不到启动脚本: {TTS_SERVE_SCRIPT}"
                            f"（可用环境变量 PIPELINE_TTS_SERVE_SCRIPT 指定）")}

    log_path = TTS_SERVE_SCRIPT.parent / "p10_serve_si.log"
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(TTS_SERVE_SCRIPT), "-Port", str(TTS_PORT)]
    # 注意：这里不能用 DETACHED_PROCESS —— 实测该标志下子进程会立刻死掉（连日志都不写）；
    # CREATE_NO_WINDOW 够用：窗口不弹、进程照常活着。
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        with open(log_path, "ab") as fh:
            _tts_serve_proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                stdout=fh, stderr=subprocess.STDOUT, creationflags=flags)
    except OSError as exc:
        return {"started": False, "alive": False, "message": f"启动失败: {exc}"}
    return {"started": True, "alive": False, "pid": _tts_serve_proc.pid, "log": str(log_path),
            "message": (f"已启动（PID {_tts_serve_proc.pid}），模型加载要几十秒；"
                        f"日志: {log_path}")}


def tts_serve_stop() -> dict:
    """停掉「本界面启动的」服务；别的窗口起的服务不越权去杀。"""
    global _tts_serve_proc

    if _tts_serve_proc is None or _tts_serve_proc.poll() is not None:
        _tts_serve_proc = None
        if tts_service_alive():
            return {"stopped": False,
                    "message": "这个服务不是本界面启动的；请到它自己的窗口按 Ctrl+C"}
        return {"stopped": False, "message": "服务没在跑"}
    pid = _tts_serve_proc.pid
    _kill_tree(pid)
    # powershell 被杀掉后，它起的 python(uvicorn) 可能变成孤儿继续占着端口 —— 按端口补一刀
    time.sleep(1.0)
    orphans = [p for p in _pids_on_port(TTS_PORT) if p != pid]
    for op in orphans:
        _kill_tree(op)
    time.sleep(0.5)
    if tts_service_alive():
        # 说实话：kill 可能因权限不足而失败，别报「已停止」骗人
        return {"stopped": False,
                "message": (f"发了停止命令，但 {TTS_HOST}:{TTS_PORT} 仍被占用"
                            f"（PID {pid}{'、' + '、'.join(map(str, orphans)) if orphans else ''}）——"
                            f"多为权限不足；请用任务管理器结束该 PID，或到它的窗口按 Ctrl+C")}
    _tts_serve_proc = None
    msg = f"已停止本界面启动的服务（PID {pid} 及其子进程）"
    if orphans:
        msg += f"；另清理了占着 {TTS_PORT} 端口的孤儿进程 {orphans}"
    return {"stopped": True, "message": msg}


def _kill_tree(pid: int) -> bool:
    """taskkill /T /F；返回是否成功（权限不足时会 False）。"""
    try:
        r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _pids_on_port(port: int) -> list[int]:
    """谁在听这个端口。用 netstat —— Get-NetTCPConnection 在受限环境里可能被拒。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}") and parts[4].isdigit():
                pids.append(int(parts[4]))
    return sorted(set(pids))

# 文件浏览根目录：仅限 jobs/（任务归档）
JOBS_ROOT = ROOT / "jobs"

MAX_LOG = 2000  # 单步日志截断长度


# 单步子进程超时。默认 600 s：PDF / OCR / PPT 那几条链都够用。
# 语音合成这条链不在这个量级 —— Qwen 读稿实测 RTF≈4、每句另有约 10 s 固定开销，
# 一篇 900 字的稿子（25 句）光生成就要十几分钟，600 s 必被 kill
# （261003 八次实测：md 改版后重跑撞墙，只合成出 7 句就被终止）。
# 所以音频步骤单独给一个更宽的可配超时，别的步骤行为不变。
STEP_TIMEOUT = float(os.environ.get("PIPELINE_STEP_TIMEOUT", "600"))
TTS_STEP_TIMEOUT = float(os.environ.get("PIPELINE_TTS_STEP_TIMEOUT", "3600"))
# TTS 子进程的临时目录：不设的话逐句缓存落在系统 %TEMP%，命令行跑同一篇稿子时对不上，
# 界面这一路也就永远吃不到缓存（每次都得从头合成十几分钟）。缺省指到项目里，与 CLI 一致。
TTS_TMPDIR = ROOT / "output" / "tts" / "tmp"
# numba 的 JIT 缓存目录：不指定时 numba 会往 site-packages 里 librosa 自己的 __pycache__
# 写缓存文件；那个目录在工作区外、受限环境下写不进去，于是 numba 在 ensure_cache_path 里
# 死等 —— 实测子进程 100% CPU 转 10 分钟、零产物（260929 老坑）。指到项目里既避开它，
# 又能复用命令行跑热的那份缓存。
TTS_NUMBA_DIR = ROOT / "output" / "tts" / "numba_cache"


def _run(cmd: list[str], timeout: float = STEP_TIMEOUT,
         env: dict | None = None) -> dict:
    """跑一个子进程，返回 {ok, out, err}。timeout 按步骤给（音频那步长得多）。

    `env` 只在需要给子进程补环境变量时传（TTS 的缓存落点），传的是**完整环境**，
    不是补丁 —— 调用方负责 `dict(os.environ)` 之后再改。
    """
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        return {
            "ok": r.returncode == 0,
            "out": (r.stdout or "")[-MAX_LOG:],
            "err": (r.stderr or "")[-MAX_LOG:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": f"超时（>{int(timeout)}s）被终止"}


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
        tts_engine = str(opts.get("tts_engine") or "gptsovits")
        # 「模型」下拉（gui/tts_models.json）优先：它带齐了这套权重该用的引擎/音色/权重文件
        model = find_tts_model(str(opts.get("tts_model") or "")) if opts.get("tts_model") else None
        if model:
            tts_engine = model["engine"]
            if model["voice"]:
                opts = dict(opts, voice=model["voice"])
        label = {"gptsovits": "TTS/GPT-SoVITS 微调", "qwen": "TTS/Qwen3-TTS 读稿",
                 "sapi": "TTS/SAPI"}.get(tts_engine, "TTS/GENIE")
        if model:
            label += f" · {model['label']}"
        step = {"step": f"文本 → {fmt.upper()}（{label}）",
                "cmd": [PY_TTS, str(TEXT_TO_AUDIO), str(file),
                        "-o", str(out / f"{stem}.{fmt}"),
                        "--engine", tts_engine, "--format", fmt, "--genie-root", GENIE_ROOT],
                # 合成是长活（Qwen 读稿一篇几分钟到十几分钟）⇒ 单独放宽超时；
                # 另把逐句缓存的根与 numba 的 JIT 缓存目录补进子进程环境，
                # 与命令行跑同一篇稿子共用缓存、也不再撞「numba 写不进 site-packages」
                "timeout": TTS_STEP_TIMEOUT,
                "env": {"PIPELINE_TTS_TMPDIR": str(TTS_TMPDIR),
                        "NUMBA_CACHE_DIR": str(TTS_NUMBA_DIR)}}
        cmd = step["cmd"]
        if model:
            cmd += ["--model", model["id"]]
        if tts_engine == "qwen" and model and model["qwen_model"]:
            cmd += ["--qwen-model", model["qwen_model"]]
        if tts_engine == "qwen" and model and model.get("qwen_ckpt"):
            # 我们自己的微调权重（ckpt_epN.pt）叠在底座上；261003 起读稿可以走微调版
            cmd += ["--qwen-ckpt", model["qwen_ckpt"]]
        if tts_engine == "gptsovits":
            cmd += ["--host", TTS_HOST, "--port", str(TTS_PORT)]
            if model and model["gpt_weights"]:
                # 权重热切交给 to_audio 做（它在合成前切一次）；这里不切，免得「按钮点了没反应」
                cmd += ["--gpt-weights", model["gpt_weights"],
                        "--sovits-weights", model["sovits_weights"]]
            # 服务没起就直接给建议，不等到子进程报连接失败
            if not tts_service_alive():
                step["precheck"] = (f"GPT-SoVITS 服务没在跑（{TTS_HOST}:{TTS_PORT}）。\n"
                                    f"    先起服务：{TTS_SERVE_HINT}\n"
                                    f"    或把 TTS 引擎切回 GENIE（纯 CPU，无需服务）")
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
        if opts.get("tts_draw"):
            cmd.append("--draw")  # 抽卡模式：每跑一次出一份新文件，不覆盖也不跳过
        try:
            n_ver = int(opts.get("tts_count") or 1)  # 一次出几个版本（>1 自动抽卡命名）
        except (TypeError, ValueError):
            n_ver = 1
        if n_ver > 1:
            cmd += ["--count", str(n_ver)]
        return [step]

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
    if steps and steps[0].get("precheck"):
        # 前置条件不满足（如 TTS 服务没起）：直接回一条带建议的失败，不跑子进程
        return {"file": str(file), "ok": False, "steps": [
            {"step": steps[0]["step"], "ok": False, "out": "", "err": steps[0]["precheck"]}]}
    if not steps:
        ok_exts = "、".join(SUPPORTED_EXTS.get(target, ())) or "（无）"
        err = (f"不支持: {file.name} + 目标 {target}"
               f"（目标 {target} 支持的输入类型: {ok_exts}；"
               f"当前输入: {file.suffix.lower() or '无扩展名'}）")
        return {"file": str(file), "ok": False, "steps": [
            {"step": "校验", "ok": False, "out": "", "err": err}]}

    results = []
    for s in steps:
        env = None
        if s.get("env"):
            # "补"而不是"覆盖"：用户自己设了 PIPELINE_TTS_TMPDIR 就听他的
            env = dict(os.environ)
            for k, v in s["env"].items():
                env.setdefault(k, v)
        r = _run(s["cmd"], s.get("timeout") or STEP_TIMEOUT, env)
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


# ── TTS 模型注册表（真源 = gui/tts_models.json；261002 六次·用户要「模型可选」） ──
# 一个「模型」= 一个引擎 + 一套权重。文件里用相对路径（相对 ROOT）或绝对路径都行；
# 界面对「原版权重 / 微调权重」这类区别只按这个文件的字段显示，不在代码里写死任何模型名。
TTS_MODELS_FILE = ROOT / "gui" / "tts_models.json"


def _tts_models_registry() -> dict:
    """读注册表；文件缺失或坏掉时回一份空表 + 原因，绝不让 500 冒到界面上。"""
    import json  # 只有这一路用得到
    try:
        data = json.loads(TTS_MODELS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"engines": [], "_error": f"找不到注册表 {TTS_MODELS_FILE}"}
    except (OSError, ValueError) as exc:
        return {"engines": [], "_error": f"注册表读不了：{exc}"}
    if not isinstance(data, dict) or not isinstance(data.get("engines"), list):
        return {"engines": [], "_error": "注册表结构不对：顶层要有 engines 数组"}
    return data


def _abs_from_registry(value: str) -> str:
    """注册表里的路径 → 绝对路径（相对路径按 ROOT 解析），空值原样返回。"""
    if not value:
        return ""
    p = Path(value)
    return str(p if p.is_absolute() else ROOT / p)


def _tts_engine_list() -> tuple[list[dict], str]:
    """注册表 → 规范化后的引擎/模型清单（`(engines, error)`）。

    只碰文件、不碰服务 —— `tts_models()` 与 `_tts_service_weights()` 都用它，
    所以**这里绝不能回头调 `tts_models()`**（261002 实测：那样会无限递归到 RecursionError）。
    """
    data = _tts_models_registry()
    engines: list[dict] = []
    for eng in data.get("engines", []):
        if not isinstance(eng, dict) or not eng.get("id"):
            continue
        eid = str(eng["id"])
        out_models: list[dict] = []
        for model in eng.get("models", []):
            if not isinstance(model, dict) or not model.get("id"):
                continue
            gpt = _abs_from_registry(str(model.get("gpt_weights") or ""))
            sov = _abs_from_registry(str(model.get("sovits_weights") or ""))
            onnx = _abs_from_registry(str(model.get("onnx_dir") or ""))
            qwen = _abs_from_registry(str(model.get("qwen_model") or ""))
            qwen_ckpt = _abs_from_registry(str(model.get("qwen_ckpt") or ""))
            exists: bool | None
            if gpt or sov:
                exists = all(Path(p).is_file() for p in (gpt, sov) if p)
            elif onnx or qwen:
                exists = Path(onnx or qwen).exists()
                if exists and qwen_ckpt:          # 微调权重也要在，否则界面别标「可用」
                    exists = Path(qwen_ckpt).is_file()
            else:
                exists = None
            out_models.append({
                "id": str(model["id"]),
                "label": str(model.get("label") or model["id"]),
                "note": str(model.get("note") or ""),
                "engine": str(model.get("engine") or eid),
                "voice": str(model.get("voice") or eng.get("voice") or ""),
                "gpt_weights": gpt,
                "sovits_weights": sov,
                "onnx_dir": onnx,
                "qwen_model": qwen,
                "qwen_ckpt": qwen_ckpt,
                "exists": exists,
            })
        engines.append({
            "id": eid,
            "label": str(eng.get("label") or eid),
            "summary": str(eng.get("summary") or ""),
            "models": out_models,
        })
    return engines, str(data.get("_error") or "")


def _tts_engine_list_safe() -> tuple[list[dict], str]:
    """读注册表的兜底包装：任何意外都变成「空表 + 原因」，不让界面拿到 500。"""
    try:
        return _tts_engine_list()
    except Exception as exc:  # noqa: BLE001 - 兜底就是要把一切吃成一条消息
        return [], f"读模型注册表出错：{exc}"


def tts_models() -> dict:
    """GET /api/tts/models：引擎 + 模型清单（界面下拉的数据源，契约 §2.7）。

    - engines[]：`{id,label,summary,models[{id,label,note,voice,engine,exists,...}]}`。
    - exists：权重文件在不在（原版权重/微调权重都能一眼看出哪条现在真能用）；
      null = 这一项不由本地文件决定（qwen 的底座目录由工具自己按环境变量找）。
    - current：正在跑的服务实际加载的那套权重（`_tts_service_weights()`）——
      服务没起就是 null，界面据此提示「点『切到该模型』」。
    """
    engines, error = _tts_engine_list_safe()
    result: dict = {"ok": not error, "engines": engines,
                    "file": str(TTS_MODELS_FILE),
                    "current": _tts_service_weights() if tts_service_alive() else None}
    if error:
        result["message"] = error
    return result


def find_tts_model(model_id: str) -> dict | None:
    """按 id 在注册表里找一个模型（找不到返回 None）。只读文件，不问服务。"""
    engines, _ = _tts_engine_list_safe()
    for eng in engines:
        for model in eng["models"]:
            if model["id"] == model_id:
                return model
    return None


def _tts_service_weights() -> dict | None:
    """正在跑的服务实际加载的权重（读它自己的运行时配置）；读不到 → None。

    `p10_serve_si.ps1` 写的 t2s/vits 是**相对 `gpt-sovits/GPT_SoVITS/` 的相对路径**，
    所以先补成绝对路径再和注册表比对。只读文件，不启动也不改任何东西。
    """
    gsv = ROOT / "output" / "tts" / "gpt-sovits" / "GPT_SoVITS"
    config = ROOT / "output" / "tts" / "tmp" / "tts_infer_si.yaml"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    t2s = vits = ""
    section = ""
    # 只认顶层 `custom:` 段 —— 这个文件里 v1/v2/v2Pro/v2ProPlus/v3/v4 各段都有同名字段，
    # 不认段的话后一段会把前面的盖掉（261002 七次实测：v4 段盖成 s2Gv4.pth，于是永远认不出）
    for line in text.splitlines():
        if not line[:1].isspace() and line.strip().endswith(":"):
            section = line.split(":", 1)[0].strip()
            continue
        if section != "custom":
            continue
        s = line.strip()
        if s.startswith("t2s_weights_path:"):
            t2s = s.split(":", 1)[1].strip()
        elif s.startswith("vits_weights_path:"):
            vits = s.split(":", 1)[1].strip()
    if not t2s and not vits:
        return None

    def abspath(p: str) -> str:
        q = Path(p)
        return str((q if q.is_absolute() else gsv / q).resolve()).replace("\\", "/").lower()

    want_g, want_v = abspath(t2s), abspath(vits)
    engines, _ = _tts_engine_list_safe()
    hit = None
    for eng in engines:
        for model in eng["models"]:
            g = model["gpt_weights"].replace("\\", "/").lower()
            v = model["sovits_weights"].replace("\\", "/").lower()
            if g and v and (want_g, want_v) == (g, v):
                hit = model["id"]
                break
        if hit:
            break
    return {"gpt": t2s, "sovits": vits, "model": hit, "config": str(config)}


def tts_apply_model(model_id: str) -> dict:
    """POST /api/tts/model/apply：把服务热切到该模型的权重（只切权重，不重启服务）。

    服务没在跑时**不算失败** —— 权重信息照样回给界面，合成时由 `--gpt-weights /
    --sovits-weights` 顺带切（`to_audio.apply_gptsovits_weights`），只是那时才能生效。
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    model = find_tts_model(model_id)
    if not model:
        return {"ok": False, "error": f"注册表里没有模型 {model_id!r}"}
    gpt, sov = model["gpt_weights"], model["sovits_weights"]
    res: dict = {"ok": True, "id": model["id"], "label": model["label"],
                 "engine": model["engine"], "voice": model["voice"],
                 "gpt_weights": gpt, "sovits_weights": sov, "switched": False}
    if model["engine"] != "gptsovits" or not (gpt or sov):
        if model["engine"] == "genie":
            res["message"] = "GENIE 走的是已导出的 ONNX 目录，没有「切权重」这一步"
        else:
            res["message"] = f"{model['label']} 不需要切 GPT-SoVITS 权重"
        return res
    if not tts_service_alive():
        res["message"] = (f"GPT-SoVITS 服务没在跑；界面已记下 {model['label']} 的权重，"
                          f"等合成时会自动切（或先点「▶ 启动 GPT-SoVITS 服务」）")
        return res
    steps: list[str] = []
    for name, path, endpoint in (("GPT", gpt, "set_gpt_weights"),
                                 ("SoVITS", sov, "set_sovits_weights")):
        if not path:
            continue
        url = (f"http://{TTS_HOST}:{TTS_PORT}/{endpoint}?weights_path="
               + urllib.parse.quote(path, safe=""))
        try:
            with urllib.request.urlopen(url, timeout=TTS_APPLY_TIMEOUT) as resp:
                code = resp.status
            steps.append(f"{name} ✓（HTTP {code}）")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            res.update({"ok": False, "steps": steps,
                        "error": f"切换{name}权重失败：HTTP {exc.code} {detail}"})
            return res
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            res.update({"ok": False, "steps": steps,
                        "error": f"切换{name}权重失败：{exc}"})
            return res
    res.update({"switched": True, "steps": steps,
                "message": f"服务权重已切到 {model['label']}（{'、'.join(steps)}）"})
    return res


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
            "builtin": builtin, "py": PY_TTS, "genie_root": GENIE_ROOT,
            "service": {"host": TTS_HOST, "port": TTS_PORT, "alive": tts_service_alive()}}


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
        elif serve_static(self, path):
            pass  # 静态资源已回包
        elif path == "/api/health":
            self._send_json({"ok": True})
        elif path == "/api/tts/status":
            # 廉价轮询用：只探端口与本地进程，不扫音色列表（契约见 _shared/INTERFACES.md §2.2）
            self._send_json(tts_status())
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
        elif path == "/api/tts/models":
            # 引擎/模型下拉的数据源（契约 §2.7）；只读注册表 + 权重文件在不在
            self._send_json(tts_models())
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
        elif path == "/api/tts/serve/start":
            # 应答 = start 的原有字段（started/log/…）+ status 字段（补 ready/stage），
            # 让这一个响应体也能当 status 用（契约 §2.2）
            self._send_json(tts_status(tts_serve_start()))
        elif path == "/api/tts/serve/stop":
            self._send_json(tts_serve_stop())
        elif path == "/api/tts/model/apply":
            body = self._read_body()
            self._send_json(tts_apply_model(str(body.get("id") or "")))
        else:
            self._send_json({"error": "not found"}, 404)


# ── TTS 服务状态（冻结契约见 _shared/INTERFACES.md §2.2 + §2.6） ──────────
TTS_STAGES = ("down", "starting", "ready", "error")  # stage 只许这四个值
TTS_PROBES = ("port", "health", "none")              # probe 只许这三个值

# 就绪判据升级（§2.6）：光「端口能连」不算就绪 —— 半启动的服务也在听端口。
# 本仓库的 api_v2.py 是 FastAPI，`/openapi.json` 必在；它能答 2xx 就说明 HTTP 应用已完整装配。
TTS_HEALTH_PATH = "/openapi.json"
TTS_HEALTH_TIMEOUT = 1.0  # 秒：探测必须短，别把轮询拖慢
TTS_HEALTH_TTL = 1.5      # 秒：前端 1.5–3 s 轮一次，短缓存免得把服务打爆
_tts_health_lock = threading.Lock()
_tts_health_cache: dict = {"key": None, "at": 0.0, "verdict": None}


def _tts_health_verdict(host: str, port: int) -> bool | None:
    """对 TTS 服务做一次轻量健康探测：True=已能服务，False=没就绪，None=没有可探的健康端点。

    - 2xx → True：HTTP 应用已在答。对本仓库的启动方式（api_v2.py 先 `TTS(...)` 载权重、
      再 `uvicorn.run`）来说，能答 2xx 就意味着权重已载入；若有人用 `uvicorn api_v2:APP`
      那种「先绑端口、后导入模块」的起法，这里也能挡住「端口在听、模型还没好」。
    - 404/405/501 → None：这个服务没有健康端点，退回「端口可连」的弱判据（§2.6）。
    - 其它状态码 / 连不上 / 超时 / 响应不是 HTTP → False：明确判「没就绪」。
    任何异常都在这里吃掉：探测失败不该让 /api/tts/status 变成 500。
    """
    import http.client  # 只有 TTS 这一路用得到，就近导入（同 tts_service_alive）

    try:
        conn = http.client.HTTPConnection(host, port, timeout=TTS_HEALTH_TIMEOUT)
        try:
            conn.request("GET", TTS_HEALTH_PATH, headers={"Connection": "close"})
            resp = conn.getresponse()
            resp.read(4096)  # 小响应读完就关，别把连接吊着
            status = resp.status
        finally:
            conn.close()
    except Exception:
        return False
    if 200 <= status < 300:
        return True
    if status in (404, 405, 501):
        return None
    return False


def _tts_health_cached(host: str, port: int) -> bool | None:
    """带短缓存地取健康结论；TTL 内直接复用，前端 1.5 s 轮询不会把探测打爆。"""
    key = (host, port)
    with _tts_health_lock:  # 持锁探测：顺带挡住并发轮询的重复请求
        if (_tts_health_cache["key"] == key
                and time.time() - _tts_health_cache["at"] < TTS_HEALTH_TTL):
            return _tts_health_cache["verdict"]
        verdict = _tts_health_verdict(host, port)
        _tts_health_cache.update({"key": key, "at": time.time(), "verdict": verdict})
        return verdict


def tts_status(start_result: dict | None = None) -> dict:
    """TTS 服务的状态对象；前端按 stage 分四支显示，按 ready 决定能否合成。

    - alive：端口能连上（沿用 `tts_service_alive()` 的判据，与 to_audio.py 一致，不改语义）。
    - model_ready：`true`=健康探测确认服务能应答；`false`=端口在听但健康探测没过
      （半启动 / 还在载模型）；`null`=探不到（服务没起，或这个服务没有可探的健康端点）。
    - probe：就绪判据的来源 —— `health`(做过健康探测) / `port`(只有端口可连这条弱证据)
      / `none`(连端口都没连上)。
    - ready：能立刻接受合成请求。`model_ready===true` → true；`false` → false；
      `null` → 回退 `alive`（弱判据，前端据 `probe` 判断可信度）。
    - stage：`down`(没起) / `starting`(本界面刚拉起、或端口在听但健康探测没过)
      / `ready`(可用) / `error`(启动失败或进程已死)。
    - managed_by_gui：是否由本界面启动（对应现有 `_tts_serve_proc`），决定「停止」按钮可不可用。
    - start_result：`/api/tts/serve/start` 的原始应答。传进来时把它的原有字段并进结果，
      并据此把「脚本缺失 / 子进程起不来」判成 error（否则那种失败与「没启动」无从区分）；
      且 `message` 以 start 自己的文案为准（契约 §2.5），通用文案只给 `GET /api/tts/status`。
    """
    global _tts_serve_proc

    alive = tts_service_alive()
    if alive:
        # §2.6：端口在听只算弱证据，再补一次轻量健康探测
        model_ready = _tts_health_cached(TTS_HOST, TTS_PORT)
        probe = "port" if model_ready is None else "health"
        ready = bool(model_ready) if model_ready is not None else alive
    else:
        model_ready, probe, ready = None, "none", False
    proc = _tts_serve_proc
    exited = proc is not None and proc.poll() is not None
    managed = proc is not None and not exited  # 只有它还活着，「停止」才管得了
    pid = proc.pid if managed else None
    if pid is None and alive:
        # 服务不是本界面起的：报端口上真正的属主，拿不到就留 null（契约允许 null）
        owners = _pids_on_port(TTS_PORT)
        pid = owners[0] if owners else None
    log_path = TTS_SERVE_SCRIPT.parent / "p10_serve_si.log"

    if alive and ready:
        stage = "ready"
        message = f"服务在跑（{TTS_HOST}:{TTS_PORT}），可以合成"
    elif alive:
        # 端口在听但健康探测没过：当作「还在起」，别报就绪骗用户去点合成
        stage = "starting"
        message = (f"服务在监听 {TTS_HOST}:{TTS_PORT}，但健康探测（{TTS_HEALTH_PATH}）还没过；"
                   f"多半是模型还在载入；日志: {log_path}")
    elif managed:
        stage = "starting"
        message = (f"正在启动（PID {proc.pid}），模型加载要几十秒；"
                   f"日志: {log_path}")
    elif exited:
        stage = "error"
        message = (f"启动的进程已退出（PID {proc.pid}，退出码 {proc.returncode}）；"
                   f"日志: {log_path}")
    elif start_result is not None and not start_result.get("started"):
        # 脚本缺失 / Popen 失败：tts_serve_start 已经把原因写在 message 里
        stage = "error"
        message = str(start_result.get("message") or "启动失败")
    else:
        stage = "down"
        message = (f"服务未启动（{TTS_HOST}:{TTS_PORT}）；"
                   f"点界面上的「启动 GPT-SoVITS 服务」，或运行: {TTS_SERVE_HINT}")

    assert stage in TTS_STAGES, stage  # 冻结契约：只允许这四个值
    assert probe in TTS_PROBES, probe  # 冻结契约：probe 只允许这三个值
    status = {
        "alive": alive,
        "ready": ready,                # 语义见 §2.6：优先 model_ready，探不到才回退 alive
        "model_ready": model_ready,    # 新增（§2.6）：bool | null
        "probe": probe,                # 新增（§2.6）："port" | "health" | "none"
        "stage": stage,          # 恒为 TTS_STAGES 之一
        "message": message,
        "host": TTS_HOST,
        "port": TTS_PORT,
        "pid": pid,
        "managed_by_gui": managed,
    }
    if start_result is None:
        return status
    merged = dict(start_result)  # 原有字段（started/log/…）一个不丢
    merged.update(status)        # status 字段为准：alive 与 /api/tts/status 一致
    # §2.5：start 应答的 message 以「这次点击发生了什么」为准，不被 status 的通用文案覆盖
    own_message = start_result.get("message")
    if isinstance(own_message, str) and own_message.strip():
        merged["message"] = own_message
    return merged


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
