#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_cookies.py — 从浏览器复制的 Cookie 字符串转换为 Netscape 格式

用法:
    1. 在 B站登录后，F12 → Network → 复制任意请求的 Cookie 值
    2. 运行: python export_cookies.py
    3. 粘贴 Cookie 字符串，回车，再按一次回车结束
    4. 自动生成 cookies.txt
"""

import sys
from datetime import datetime, timedelta


def cookie_string_to_netscape(cookie_str: str) -> str:
    """将浏览器复制的 Cookie 字符串转换为 Netscape 格式。"""
    lines = ["# Netscape HTTP Cookie File", f"# Generated: {datetime.now().isoformat()}", ""]

    expires = int((datetime.now() + timedelta(days=30)).timestamp())

    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        value = value.strip()
        # 格式: domain  flag  path  secure  expires  name  value
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t{expires}\t{name}\t{value}")

    return "\n".join(lines) + "\n"


def main():
    print("请粘贴从浏览器复制的 Cookie 字符串:")
    print("（F12 → Network → 任意请求 → Headers → Cookie 值）")
    print("粘贴后按回车，再按一次回车结束:\n")

    cookie_str = ""
    try:
        while True:
            line = input()
            if line == "":
                if cookie_str:
                    break
                continue
            cookie_str += line
    except EOFError:
        pass

    if not cookie_str.strip():
        print("[错误] 未输入 Cookie")
        sys.exit(1)

    # 保存到文件
    output_path = "cookies.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(cookie_string_to_netscape(cookie_str))

    print(f"\n[OK] Cookie 已保存到 {output_path}")
    print(f"现在可以运行:")
    print(f'  python download_video.py "BVxx" --cookies {output_path}')


if __name__ == "__main__":
    main()
