#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
to_pptx.py — 旧版 .ppt（PowerPoint 97-2003，OLE2 复合文档）→ .pptx（OOXML）

为什么需要这一步：
    python-pptx 只认 OOXML 的 .pptx，读不了老 .ppt（magic D0 CF 11 E0 A1 B1 1A E1）。
    pipeline 的 PPT 链（common/pptx/to_json.py → common/json/to_md.py|to_tex.py|to_docx.py）
    全部从 .pptx 起步，所以 .ppt 必须先过这一道格式转换。

用法:
    python to_pptx.py input.ppt                     # 同目录生成 input.pptx
    python to_pptx.py input.ppt -o out/x.pptx       # 指定输出文件
    python to_pptx.py input.ppt --outdir out/       # 指定输出目录
    python to_pptx.py input.ppt --backend wps       # 指定后端
    python to_pptx.py input.ppt -f                  # 覆盖已存在的 .pptx

后端（--backend auto 时按序尝试，用第一个成功的）:
    1. powerpoint  — Microsoft PowerPoint COM（需 pywin32 + 已装 Office）
    2. wps         — WPS 演示 COM（KWPP.Application，需 pywin32 + 已装 WPS）
    3. libreoffice — soffice --headless --convert-to pptx（需装 LibreOffice）

说明:
    - 输入若已是 .pptx（ZIP/OOXML）→ 不启动 Office，直接复制到目标路径。
    - COM 用 DispatchEx 起**独立实例**，不会动用户桌面上已打开的 PowerPoint 窗口。
    - 已验证（2026-09-07，Windows + Office 16，proaes 环境 pywin32 311）：
      powerpoint 后端可把 307 KB 的 .ppt（15 页）转成 200 KB .pptx，
      再经 pptx/to_json.py 正常提取全部 15 页文字。
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ── 常量 ────────────────────────────────────────────────────
MAGIC_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # 复合文档（.ppt/.doc/.xls）
MAGIC_ZIP = b"PK\x03\x04"                            # OOXML（.pptx/.docx/.xlsx）
PP_SAVE_AS_OPENXML = 24                              # ppSaveAsOpenXMLPresentation

# COM 后端: 名字 → (ProgID, 展示名)
COM_BACKENDS = {
    "powerpoint": ("PowerPoint.Application", "Microsoft PowerPoint COM"),
    "wps": ("KWPP.Application", "WPS 演示 COM"),
}
AUTO_ORDER = ("powerpoint", "wps", "libreoffice")

SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


# ── 输入类型判别 ─────────────────────────────────────────────

def sniff(path: Path) -> str:
    """按 magic 判别真实格式: ooxml / ole2 / unknown（扩展名会说谎）。"""
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(MAGIC_ZIP):
        return "ooxml"
    if head.startswith(MAGIC_OLE2):
        return "ole2"
    return "unknown"


# ── 各后端实现 ───────────────────────────────────────────────

def _com_convert(src: Path, dst: Path, progid: str, label: str) -> None:
    """用 COM 自动化把 src 另存为 dst(.pptx)。失败抛 RuntimeError。"""
    try:
        import pythoncom
        from win32com.client import DispatchEx
    except ImportError as e:
        raise RuntimeError(
            f"缺少 pywin32（{e}）。安装: python -m pip install pywin32"
        ) from e

    pythoncom.CoInitialize()
    app = None
    pres = None
    try:
        app = DispatchEx(progid)  # 独立实例，避免影响用户已打开的窗口
        try:
            app.DisplayAlerts = 1  # ppAlertsNone：不弹兼容性与另存提示
        except Exception:
            pass  # WPS 等实现可能没有该属性，忽略
        # Open(FileName, ReadOnly=True, Untitled=False, WithWindow=False)
        pres = app.Presentations.Open(str(src), True, False, False)
        pres.SaveAs(str(dst), PP_SAVE_AS_OPENXML)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"{label} 转换失败: {e}") from e
    finally:
        if pres is not None:
            try:
                pres.Close()
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _soffice_path() -> str | None:
    exe = shutil.which("soffice")
    if exe:
        return exe
    for cand in SOFFICE_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


def _libre_convert(src: Path, dst: Path) -> None:
    """LibreOffice headless 转换。soffice 只能指定 --outdir，文件名由它决定。"""
    exe = _soffice_path()
    if not exe:
        raise RuntimeError(
            "未找到 LibreOffice（soffice）：请安装 LibreOffice，或改用 "
            "--backend powerpoint / --backend wps"
        )
    r = subprocess.run(
        [exe, "--headless", "--norestore", "--convert-to", "pptx",
         "--outdir", str(dst.parent), str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    produced = dst.parent / f"{src.stem}.pptx"
    if r.returncode != 0 or not produced.is_file():
        detail = (r.stderr or r.stdout or "").strip()[:400]
        raise RuntimeError(f"LibreOffice 转换失败: {detail or '无输出'}")
    if produced != dst:
        produced.replace(dst)


def _run_backend(name: str, src: Path, dst: Path) -> str:
    """执行指定后端，返回展示名；失败抛 RuntimeError。"""
    if name in COM_BACKENDS:
        progid, label = COM_BACKENDS[name]
        _com_convert(src, dst, progid, label)
        return label
    if name == "libreoffice":
        _libre_convert(src, dst)
        return "LibreOffice headless"
    raise RuntimeError(f"未知后端: {name}")


# ── 对外主函数 ───────────────────────────────────────────────

def convert(src: Path, dst: Path, backend: str = "auto", force: bool = False) -> str:
    """把 .ppt 转成 .pptx，返回实际使用的后端展示名。

    失败统一抛 RuntimeError（带可操作提示），由 CLI 转成退出码。
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        raise RuntimeError(f"输入文件不存在: {src}")
    if dst.exists() and not force:
        raise RuntimeError(f"输出已存在: {dst}（要覆盖请加 -f / --force）")
    if src.resolve() == dst.resolve():
        raise RuntimeError("输出路径与输入相同，拒绝原地覆盖 .ppt")

    kind = sniff(src)
    if kind == "unknown":
        with open(src, "rb") as f:
            head = f.read(8).hex(" ").upper()
        raise RuntimeError(
            f"既不是 .ppt（OLE2）也不是 .pptx（ZIP/OOXML），magic={head}；"
            "确认文件没损坏、或本就是别的格式"
        )

    dst.parent.mkdir(parents=True, exist_ok=True)

    if kind == "ooxml":
        # 扩展名写着 .ppt，内容其实已是 OOXML：复制即可，不必启动 Office
        if src != dst:
            shutil.copy2(src, dst)
        return "直接复制（内容已是 OOXML，无需转换）"

    order = AUTO_ORDER if backend == "auto" else (backend,)
    errors = []
    for name in order:
        try:
            return _run_backend(name, src, dst)
        except RuntimeError as e:
            errors.append(f"[{name}] {e}")
            if dst.exists():  # 失败残留清掉，别留半个文件
                try:
                    dst.unlink()
                except OSError:
                    pass
    raise RuntimeError(
        "所有后端都失败了：\n  " + "\n  ".join(errors) +
        "\n提示: 本机装了 PowerPoint 或 WPS 就能转（COM 后端需 pywin32）；"
        "都没有则装 LibreOffice 后用 --backend libreoffice"
    )


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="旧版 .ppt → .pptx 转换（PowerPoint COM / WPS COM / LibreOffice）",
    )
    parser.add_argument("input", help="输入 .ppt 文件路径（若已是 OOXML 则直接复制）")
    parser.add_argument("-o", "--output", default=None, help="输出 .pptx 路径（默认: 输入同目录同名）")
    parser.add_argument("--outdir", default=None, help="输出目录（与 -o 同时给时以 -o 为准）")
    parser.add_argument(
        "--backend", choices=["auto", *COM_BACKENDS, "libreoffice"], default="auto",
        help="转换后端 [默认: auto = powerpoint → wps → libreoffice]",
    )
    parser.add_argument("-f", "--force", action="store_true", help="覆盖已存在的输出文件")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"错误: 文件不存在 — {args.input}")

    if args.output:
        dst = Path(args.output)
    elif args.outdir:
        dst = Path(args.outdir) / f"{src.stem}.pptx"
    else:
        dst = src.with_suffix(".pptx")

    try:
        used = convert(src, dst, backend=args.backend, force=args.force)
    except RuntimeError as e:
        sys.exit(f"错误: {e}")

    print(f"[OK] {src.name} -> {dst}（后端: {used}）")
