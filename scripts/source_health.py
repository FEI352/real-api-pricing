#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取前的数据源健康预检：可达性 + 页面形态（结构标记 + 响应体体积）。

动机（2026-10-08）：抓取失败今天靠人肉发现（runbook-smoke 演练里 curl 超时只能人工
重试）；更危险的是**静默形态变化**——AA 页面改版导致 `aa_fetch_parse.py` 解析出 0 行或
字段缺失，脚本「成功」返回却产出残缺快照（round5 的 rawRecords 被裁字段就是这么发生的，
几天后才被发现）。本脚本只做轻量预检，不解析内容：

  1. HTTP 200；
  2. 页面含预期结构标记（各源标记取自解析器真正依赖的载荷字段，见 SOURCES 注释）；
  3. 响应体大小落在实测区间内。

**超时 / 网络错误 / 非 200 一律报 `undetermined`（无法判定），不许静默当健康。**

退出码：0 = 全部 healthy；2 = 存在 malformed（形态异常）或 undetermined（无法判定）。
runbook §0 规定：exit 2 时本轮不进入抓取。

用法（Python 用仓库 .venv）：
  .venv/bin/python scripts/source_health.py
  .venv/bin/python scripts/source_health.py --sources aa_models,aa_coding_agents
  .venv/bin/python scripts/source_health.py --json /tmp/source_health.json
  # 测试用：把某个源指向本地 mock（不改脚本常量）
  .venv/bin/python scripts/source_health.py --url-map aa_models=http://127.0.0.1:8099/mock.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0"          # 与 runbook §0 的 curl -A 保持一致
DEFAULT_TIMEOUT = 90        # 与 runbook §0 的 curl -m 90 保持一致

# 每个源：id / url / 必须出现的标记 / 体积区间（2026-10-08 实测 + 余量）/ 是否要求原始 HTML。
SOURCES = (
    dict(
        id="aa_models",
        url="https://artificialanalysis.ai/leaderboards/models",
        markers=("intelligenceIndex", "terminalBench40"),
        min_bytes=1_200_000, max_bytes=6_000_000,
        require_html=True,
        note="AA models 榜原始 HTML（RSC 载荷内嵌模型行）。标记依据：aa_fetch_parse.py 依赖的载荷字段 "
             "intelligenceIndex / terminalBench40；2026-10-08 实测 2,456,763 B。",
    ),
    dict(
        id="aa_coding_agents",
        url="https://artificialanalysis.ai/agents/coding-agents",
        markers=("displayLabel", "indexScore"),
        min_bytes=400_000, max_bytes=3_000_000,
        require_html=True,
        note="AA coding-agents 页原始 HTML（必须非 JSON）。标记依据：aa_build_snapshot.coding_rows() 依赖的 "
             "displayLabel / indexScore（2026-10-16 AA 载荷改版剥离 hostModelSlug 字段）；2026-10-08 实测 931,961 B。",
    ),
    dict(
        id="anthropic_pricing",
        url="https://www.anthropic.com/claude-haiku-5-5",
        markers=("$0.10", "claude-haiku-5-5"),
        min_bytes=50_000, max_bytes=1_500_000,
        require_html=True,
        note="Anthropic 官方定价页（list-prices-claude-haiku55-round1-2026-10-08.json 的 source）。"
             "标记依据：三段价 $0.10 与官方 API id；2026-10-08 实测 221,135 B。",
    ),
)


def fetch(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def check_source(source: dict, timeout: float, url: str | None = None) -> dict:
    row = dict(id=source["id"], url=url or source["url"], status=None, bytes=0, sha256=None,
               markers_found=[], missing_markers=[], verdict="undetermined", reason="")
    try:
        status, body = fetch(row["url"], timeout)
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        # 超时/网络错误：明确标成「无法判定」，绝不当健康。
        row["reason"] = f"请求失败或超时（{exc}）：无法判定，不当健康"
        return row

    row["status"] = status
    row["bytes"] = len(body)
    row["sha256"] = hashlib.sha256(body).hexdigest()

    if status != 200:
        row["reason"] = f"HTTP {status}（非 200）：无法判定"
        return row

    if source.get("require_html") and not looks_like_html(body):
        row["verdict"] = "malformed"
        row["reason"] = "响应体不是原始 HTML（页面可能已改成 JSON、跳转或人机校验页）"
        return row

    text = body.decode("utf-8", "replace")
    row["markers_found"] = [m for m in source["markers"] if m in text]
    row["missing_markers"] = [m for m in source["markers"] if m not in text]

    problems = []
    if row["missing_markers"]:
        problems.append(f"缺结构标记 {row['missing_markers']}")
    if not (source["min_bytes"] <= len(body) <= source["max_bytes"]):
        problems.append(f"体积 {len(body)} 越界 [{source['min_bytes']}, {source['max_bytes']}]")
    if problems:
        row["verdict"] = "malformed"
        row["reason"] = "；".join(problems)
    else:
        row["verdict"] = "healthy"
        row["reason"] = "OK"
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"每源超时秒数（默认 {DEFAULT_TIMEOUT}，与 runbook curl -m 一致）")
    parser.add_argument("--json", dest="json_path", help="结构化结果写入该路径")
    parser.add_argument("--sources", help="逗号分隔的源 id 白名单（默认全部）")
    parser.add_argument("--url-map", action="append", default=[],
                        help="测试用：id=URL 覆盖某个源的地址（可重复）")
    args = parser.parse_args()

    wanted = [s.strip() for s in args.sources.split(",")] if args.sources else None
    overrides = {}
    for item in args.url_map:
        if "=" not in item:
            print(f"ERROR: --url-map 需要 id=URL 形式，收到 {item!r}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        overrides[key.strip()] = value.strip()

    selected = [s for s in SOURCES if not wanted or s["id"] in wanted]
    unknown = sorted(set(wanted or []) - {s["id"] for s in SOURCES})
    if unknown:
        print(f"ERROR: 未知源 id {unknown}（可用：{[s['id'] for s in SOURCES]}）", file=sys.stderr)
        return 2

    print(f"source health: {len(selected)} source(s), timeout {args.timeout}s, UA {UA!r}")
    rows = []
    for source in selected:
        row = check_source(source, args.timeout, overrides.get(source["id"]))
        rows.append(row)
        mark = f"{len(row['markers_found'])}/{len(source['markers'])}"
        print(f"  [{row['verdict']:<12}] {row['id']:<18} http={row['status']!s:<4} "
              f"bytes={row['bytes']:<9} markers={mark:<4} {row['reason']}")

    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in ("healthy", "malformed", "undetermined")}
    failed = counts["malformed"] + counts["undetermined"]
    payload = dict(
        checkedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        timeout=args.timeout, userAgent=UA,
        sources=rows, summary=counts,
        verdict="healthy" if failed == 0 else "degraded",
    )
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        print(f"json -> {args.json_path}")

    print(f"summary: {counts['healthy']} healthy, {counts['malformed']} malformed, "
          f"{counts['undetermined']} undetermined -> exit {2 if failed else 0}")
    if failed:
        print("exit 2: 本轮不要进入抓取；把失败写进 daily-log 并标 [需人工介入]。")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
