#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
to_webp.py — 按编号范围批量抓取 webp 图片

图站的图片通常按编号命名（00000.webp、00001.webp …）。给一个 URL 模板（编号处写
{i}）或者一张真实样本链接，再给个范围，就能多线程把整段图抓下来。

用法:
    # 模板模式：{i} 换成 5 位补零编号（--digits 可改）
    python tools/common/url/to_webp.py "https://example.com/gallery/a/{i}.webp" -n 120

    # 样本模式：给一张真实链接，自动认出末段编号与位数
    python tools/common/url/to_webp.py "https://example.com/gallery/a/00007.webp" -n 120

    # 从 3 号下到 200 号、16 线程、输出到指定目录
    python tools/common/url/to_webp.py "https://example.com/a/{i}.webp" --start 3 -n 200 -w 16 -o ./download/abc

    # 站点校验防盗链时再补 Referer / Cookie（默认直连，先不加）
    python tools/common/url/to_webp.py "https://example.com/a/{i}.webp" -n 120 --referer auto
    python tools/common/url/to_webp.py "https://example.com/a/{i}.webp" -n 120 --cookie "k=v; k2=v2"

    # 只打印会请求哪些 URL，不落盘
    python tools/common/url/to_webp.py "https://example.com/a/{i}.webp" -n 120 --dry-run

约定:
    - 编号从 --start（默认 0）到 -n（含端点），模板模式下默认补零到 5 位。
    - 已存在且非空的文件默认跳过（断点续传），--force 才重下。
    - 先写 .part 再原子改名；返回内容不是 webp 判失败（挡掉 200 的报错页）。
    - 默认 trust_env=False：本机代理环境变量会干扰 requests，需要走系统代理时加 --env-proxy。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import http.cookiejar
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import requests

# ── 默认配置 ──────────────────────────────────────────────────────
DEFAULT_DIGITS = 5          # 模板模式下的补零位数（00000.webp）
DEFAULT_WORKERS = 8
DEFAULT_RETRIES = 3
DEFAULT_TIMEOUT = 20.0
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

RIFF = b"RIFF"
WEBP = b"WEBP"

_TPL_RE = re.compile(r"\{i(?::([^}]*))?\}")
_BAD_NAME_RE = re.compile(r'[\\/:*?"<>|]')
_DIGITS_TAIL_RE = re.compile(r"(\d+)(\D*)$")

_tls = threading.local()


class Retryable(Exception):
    """可重试的失败（5xx / 429 / 空响应）。"""


# ── URL 与文件名 ──────────────────────────────────────────────────

def build_maker(url: str, digits: int | None):
    """解析 URL，返回 (make_url, 补零位数)。

    模板模式：URL 里含 {i}，逐号替换；{i:03d} 这种 format 写法原样交给 format。
    样本模式：取末段路径里最后一个数字串当编号，位数照抄样本（除非显式给了 --digits）。
    """
    if "{i" in url:
        width = digits or DEFAULT_DIGITS
        m = _TPL_RE.search(url)
        if m and m.group(1):
            def make(idx: int) -> str:
                return url.format(i=idx)
        else:
            def make(idx: int) -> str:
                return url.replace("{i}", f"{idx:0{width}d}")
        return make, width

    sp = urlsplit(url)
    head, _, seg = sp.path.rpartition("/")
    m = _DIGITS_TAIL_RE.search(seg)
    if not m:
        sys.exit(
            "[错误] 这个链接里找不到编号。两种给法：\n"
            "  ① 模板：把编号换成 {i}，如 https://example.com/a/{i}.webp\n"
            "  ② 样本：给一张以数字结尾的图片链接，如 https://example.com/a/00007.webp"
        )
    pre, post = seg[: m.start()], m.group(2)
    width = digits or len(m.group(1))

    def make(idx: int) -> str:
        path = f"{head}/{pre}{idx:0{width}d}{post}"
        return urlunsplit((sp.scheme, sp.netloc, path, sp.query, sp.fragment))

    return make, width


def url_basename(url: str) -> str:
    """取 URL 路径末段当文件名；空白或非法字符做清洗。"""
    seg = urlsplit(url).path.rpartition("/")[2]
    name = _BAD_NAME_RE.sub("_", unquote(seg)).strip(" .")
    return name[:180]


def count_name_collisions(make, start: int, end: int, width: int) -> dict[str, int]:
    """预扫一遍文件名，供重名时追加编号（编号不在文件名里的站点会用到）。"""
    counts: dict[str, int] = {}
    for idx in range(start, end + 1):
        name = url_basename(make(idx)) or f"{idx:0{width}d}.webp"
        counts[name] = counts.get(name, 0) + 1
    return counts


def output_name(url: str, idx: int, width: int, counts: dict[str, int]) -> str:
    name = url_basename(url) or f"{idx:0{width}d}.webp"
    if counts.get(name, 0) > 1:
        p = Path(name)
        name = f"{p.stem}_{idx:0{width}d}{p.suffix}"
    return name


# ── HTTP ──────────────────────────────────────────────────────────

def load_cookies_file(path: str):
    """读 Netscape 格式 cookies.txt（export_cookies.py 的产物就是这个格式）。"""
    jar = http.cookiejar.MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:
        # 退化成每行 name=value 的简陋格式
        jar = http.cookiejar.CookieJar()
        for raw in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            jar.set_cookie(http.cookiejar.Cookie(
                0, k.strip(), v.strip(), None, False, "", False, False,
                "/", True, False, None, True, None, None, {},
            ))
        return jar


def make_session(args, jar) -> requests.Session:
    s = requests.Session()
    # 本机的代理环境变量会干扰 requests，默认不读；要代理就用 --proxy 或 --env-proxy
    s.trust_env = bool(args.env_proxy)
    s.headers.update({
        "User-Agent": args.ua,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    if args.referer:
        s.headers["Referer"] = args.referer
    if args.cookie:
        s.headers["Cookie"] = args.cookie
    if jar is not None:
        for c in jar:
            s.cookies.set_cookie(c)
    if args.proxy:
        s.proxies = {"http": args.proxy, "https": args.proxy}
    for h in args.header or []:
        k, _, v = h.partition(":")
        if k.strip():
            s.headers[k.strip()] = v.strip()
    return s


def get_session(args, jar) -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = make_session(args, jar)
        _tls.s = s
    return s


def is_webp(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == RIFF and head[8:12] == WEBP


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


# ── 单个下载 ──────────────────────────────────────────────────────

def fetch_one(idx: int, url: str, dst: Path, args, jar) -> tuple[str, int, str]:
    """返回 (状态, 字节数, 说明)。状态：ok / skip / missing / invalid / fail。"""
    if dst.exists() and dst.stat().st_size > 0 and not args.force:
        return "skip", dst.stat().st_size, ""
    tmp = dst.with_name(dst.name + ".part")
    err = ""
    for attempt in range(1, args.retries + 1):
        try:
            with get_session(args, jar).get(url, stream=True, timeout=args.timeout) as resp:
                code = resp.status_code
                if code in (404, 410):
                    tmp.unlink(missing_ok=True)
                    return "missing", 0, f"HTTP {code}"
                if code in (401, 403):
                    tmp.unlink(missing_ok=True)
                    return "fail", 0, (
                        f"HTTP {code}（站点可能在查 Referer/Cookie，"
                        f"加 --referer auto 或 --cookie \"k=v\" 再试）"
                    )
                if code == 429 or code >= 500:
                    raise Retryable(f"HTTP {code}")
                if code >= 400:
                    tmp.unlink(missing_ok=True)
                    return "fail", 0, f"HTTP {code}"

                size = 0
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(1024 * 64):
                        if chunk:
                            f.write(chunk)
                            size += len(chunk)
                if size == 0:
                    raise Retryable("响应为空")

                if not args.skip_magic_check:
                    with open(tmp, "rb") as f:
                        head = f.read(12)
                    if not is_webp(head):
                        ctype = resp.headers.get("Content-Type", "?")
                        tmp.unlink(missing_ok=True)
                        return "invalid", size, (
                            f"返回的不是 webp（Content-Type: {ctype}），"
                            f"多半是验证页/报错页；确认站点给的是图片后加 --skip-magic-check"
                        )

                os.replace(tmp, dst)
                return "ok", size, ""
        except Retryable as e:
            err = str(e)
        except requests.RequestException as e:
            err = f"{type(e).__name__}: {e}"
        if attempt < args.retries:
            time.sleep(min(0.5 * (2 ** (attempt - 1)), 8))

    tmp.unlink(missing_ok=True)
    return "fail", 0, err or "未知错误"


# ── 进度与汇总 ────────────────────────────────────────────────────

class Reporter:
    def __init__(self, total: int, quiet: bool = False):
        self.total = total
        self.quiet = quiet
        self.done = 0
        self.lock = threading.Lock()
        self.stats = {"ok": 0, "skip": 0, "missing": 0, "invalid": 0, "fail": 0}
        self.problems: list[tuple[str, int, str, str]] = []

    def report(self, status: str, idx: int, url: str, dst: Path, size: int, msg: str) -> None:
        with self.lock:
            self.done += 1
            self.stats[status] = self.stats.get(status, 0) + 1
            n = self.done
            if status not in ("ok", "skip"):
                self.problems.append((status, idx, url, msg))
            if self.quiet:
                return
            if status == "ok":
                print(f"[ ok ] {n}/{self.total}  {dst.name}  {fmt_size(size)}", flush=True)
            elif status == "skip":
                print(f"[skip] {n}/{self.total}  {dst.name}  已存在", flush=True)
            elif status == "missing":
                print(f"[  - ] {n}/{self.total}  {url}  {msg}", flush=True)
            else:
                print(f"[{status:>4s}] {n}/{self.total}  {url}  {msg}", flush=True)

    def summary(self, out_dir: Path, elapsed: float) -> int:
        s = self.stats
        print()
        print("── 结果 ──")
        print(f"  成功 {s['ok']}   跳过 {s['skip']}   缺失 {s['missing']}   失败 {s['invalid'] + s['fail']}")
        print(f"  输出：{out_dir.resolve()}")
        print(f"  用时：{elapsed:.1f} s")
        if self.problems:
            print("  未拿到的编号：")
            for status, idx, url, msg in self.problems[:50]:
                print(f"    #{idx}  {status}  {url}  {msg}" if msg else f"    #{idx}  {status}  {url}")
            if len(self.problems) > 50:
                print(f"    … 另有 {len(self.problems) - 50} 条，略")
        return 0 if (s["ok"] + s["skip"]) == self.total else 1


# ── 入口 ──────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="按编号范围批量下载 webp 图片（多线程、断点续传、内容校验）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/common/url/to_webp.py \"https://example.com/a/{i}.webp\" -n 120\n"
            "  python tools/common/url/to_webp.py \"https://example.com/a/00007.webp\" -n 120\n"
            "  python tools/common/url/to_webp.py \"https://example.com/a/{i}.webp\" --start 1 -n 30 -o ./download/abc\n"
        ),
    )
    p.add_argument("url", help="图片链接模板（编号处写 {i}）或一张末尾带编号的样本链接")
    p.add_argument("-n", "--end", type=int, required=True, help="末编号（含）")
    p.add_argument("--start", type=int, default=0, help="起始编号（默认 0）")
    p.add_argument("-d", "--digits", type=int, default=None,
                   help=f"编号补零位数（模板模式默认 {DEFAULT_DIGITS}；样本模式默认照抄样本）")
    p.add_argument("-o", "--outdir", default=None,
                   help="输出目录（默认 ./download/<站点>）")
    p.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS, help="并发线程数（默认 8）")
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="单张重试次数（默认 3）")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="请求超时秒数（默认 20）")
    p.add_argument("--referer", default=None, help="Referer；写 auto 表示用同站点根地址")
    p.add_argument("--cookie", default=None, help="Cookie 头，形如 \"k=v; k2=v2\"")
    p.add_argument("--cookies", default=None, help="Netscape 格式 cookies.txt 路径")
    p.add_argument("--ua", default=DEFAULT_UA, help="User-Agent")
    p.add_argument("--header", action="append", default=None,
                   help="额外请求头，可重复：--header \"Origin: https://example.com\"")
    p.add_argument("--proxy", default=None, help="代理，形如 http://127.0.0.1:7890")
    p.add_argument("--env-proxy", action="store_true", help="允许读系统代理环境变量（默认关）")
    p.add_argument("--force", action="store_true", help="已存在的文件也重下")
    p.add_argument("--skip-magic-check", action="store_true", help="不校验返回内容是不是 webp")
    p.add_argument("--dry-run", action="store_true", help="只打印会请求的 URL，不下载")
    p.add_argument("-q", "--quiet", action="store_true", help="只打汇总，不逐张刷屏")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.start > args.end:
        sys.exit(f"[错误] --start {args.start} 大于 -n {args.end}")

    make, width = build_maker(args.url, args.digits)
    total = args.end - args.start + 1

    # 输出目录：默认 ./download/<站点>
    if args.outdir:
        out_dir = Path(args.outdir)
    else:
        host = urlsplit(make(args.start)).netloc or "webp"
        out_dir = Path("download") / _BAD_NAME_RE.sub("_", host)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.referer and args.referer.lower() == "auto":
        sp = urlsplit(make(args.start))
        args.referer = f"{sp.scheme}://{sp.netloc}/"

    jar = load_cookies_file(args.cookies) if args.cookies else None

    print(f"编号 {args.start}–{args.end}（共 {total} 张）")
    print(f"模板 {args.url}")
    print(f"输出 {out_dir.resolve()}   并发 {args.workers}")

    if args.dry_run:
        head = [make(i) for i in range(args.start, min(args.end, args.start + 4) + 1)]
        print("\n会请求：")
        for i, u in enumerate(head):
            print(f"  {args.start + i:>6d}  {u}")
        if total > len(head):
            print(f"  … 共 {total} 条，末条 {make(args.end)}")
        return 0

    counts = count_name_collisions(make, args.start, args.end, width)
    reporter = Reporter(total, quiet=args.quiet)
    t0 = time.time()

    def work(idx: int):
        url = make(idx)
        name = output_name(url, idx, width, counts)
        status, size, msg = fetch_one(idx, url, out_dir / name, args, jar)
        reporter.report(status, idx, url, out_dir / name, size, msg)

    ex = cf.ThreadPoolExecutor(max_workers=max(1, args.workers))
    pending: set[cf.Future] = set()
    it = iter(range(args.start, args.end + 1))

    def fill() -> None:
        while len(pending) < max(1, args.workers) * 2:
            try:
                idx = next(it)
            except StopIteration:
                return
            pending.add(ex.submit(work, idx))

    try:
        fill()
        while pending:
            done, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for f in done:
                exc = f.exception()
                if exc is not None:
                    print(f"[fail] 线程异常：{exc}", flush=True)
            fill()
    except KeyboardInterrupt:
        print("\n[中断] 正在收尾…", flush=True)
        for f in pending:
            f.cancel()
        ex.shutdown(wait=False, cancel_futures=True)
        return 130
    finally:
        ex.shutdown(wait=True)

    return reporter.summary(out_dir, time.time() - t0)


if __name__ == "__main__":
    raise SystemExit(main())
