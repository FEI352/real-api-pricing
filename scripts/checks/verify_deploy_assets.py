#!/usr/bin/env python3
"""守卫「部署一致性」：线上目录必须就是刚构建出来的那份 dist。

背景（2026-11-07 血证）：`real-api-pricing-cli update` 末尾的 `cp -r web/dist/. /var/www/real-plan/`
间歇性以退出码 1 中断，而管线并不因此失败——结果是**构建成功、线上没更新**。
本会话至少两次靠人工补 `cp` 才生效，用户看到的是「改了但没变化」。
后来部署改成「临时目录 + 原子换名」，这个守卫负责证明它真的对齐了。

判据（排除 data/ 与 _headers 这类受保护/静态项）：
  1. dist 里每个文件的相对路径都出现在部署目录，且字节数一致；
  2. 部署目录里不允许残留 dist 中已不存在的 assets/*.js|css（旧 hash 垃圾）；
  3. index.html 引用的入口脚本名必须与 dist 里实际存在的文件名匹配。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "web" / "dist"
DEPLOY = Path("/var/www/real-plan")
PROTECTED = {"data"}
SKIP_SUFFIX = {".map"}

problems: list[str] = []

if not (DIST / "index.html").is_file():
    problems.append(f"dist 不完整：缺 {DIST / 'index.html'}（先跑 npm run build）")
if not (DEPLOY / "index.html").is_file():
    problems.append(f"部署目录不完整：缺 {DEPLOY / 'index.html'}")

if not problems:
    for src in DIST.rglob("*"):
        if not src.is_file() or src.suffix in SKIP_SUFFIX:
            continue
        rel = src.relative_to(DIST)
        if rel.parts and rel.parts[0] in PROTECTED:
            continue
        dst = DEPLOY / rel
        if not dst.is_file():
            problems.append(f"线上缺文件：{rel.as_posix()}")
        elif dst.stat().st_size != src.stat().st_size:
            problems.append(f"文件大小不一致：{rel.as_posix()}（dist {src.stat().st_size} vs 线上 {dst.stat().st_size}）")

    dist_assets = {p.name for p in (DIST / "assets").glob("*") if p.is_file()}
    for p in (DEPLOY / "assets").glob("*"):
        if p.is_file() and p.name not in dist_assets and p.suffix in {".js", ".css"}:
            problems.append(f"线上残留旧资产：assets/{p.name}")

    html = (DIST / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'assets/(index-[A-Za-z0-9_-]+\.(?:js|css))', html):
        if ref not in dist_assets:
            problems.append(f"index.html 引用的 {ref} 不在 dist/assets 中")

if problems:
    print(f"FAIL: {len(problems)} 项部署不一致")
    for p in problems[:12]:
        print(f"  - {p}")
    if len(problems) > 12:
        print(f"  …另有 {len(problems) - 12} 项")
    sys.exit(1)

print("PASS: 线上目录与 dist 逐文件一致，无旧资产残留，入口脚本匹配")
