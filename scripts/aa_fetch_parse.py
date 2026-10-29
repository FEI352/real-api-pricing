# -*- coding: utf-8 -*-
"""AA models 榜 HTML → 载荷 JSON（RSC flight push 解码）。

从 `https://artificialanalysis.ai/leaderboards/models` 的原始 HTML 里解出转义 JSON，
扫描所有以 `[{"` 开头的 JSON 数组，取含 name/slug/intelligenceIndex 的最大者作为
models 载荷。输出是纯数组 JSON，供 `scripts/aa_build_snapshot.py --models-json` 消费。

用法（Python 用仓库 .venv）：
  .venv/bin/python scripts/aa_fetch_parse.py /tmp/aa_models.html /tmp/aa_models_raw.json

退出码：0 = 解析成功；1 = 文件读不到或找不到载荷数组。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DEC = json.JSONDecoder()


def decode_rsc(html: str) -> str:
    """把所有 self.__next_f.push([1,"..."]) 的字符串参数解码后拼接。"""
    parts, i = [], 0
    marker = "self.__next_f.push([1,"
    while True:
        i = html.find(marker, i)
        if i < 0:
            break
        j = i + len(marker)
        while j < len(html) and html[j] in " \t\n":
            j += 1
        if html[j] != '"':
            i = j
            continue
        try:
            chunk, end = DEC.raw_decode(html[j:])
        except ValueError:
            i = j
            continue
        parts.append(chunk)
        i = j + end
    return "".join(parts)


def biggest_models_array(text: str) -> list[dict]:
    best: list[dict] = []
    for m in re.finditer(r'\[\{"', text):
        try:
            arr, _ = DEC.raw_decode(text[m.start():])
        except ValueError:
            continue
        if not isinstance(arr, list) or not arr or not isinstance(arr[0], dict):
            continue
        if not {"name", "slug", "intelligenceIndex"} <= set(arr[0]):
            continue
        if len(arr) > len(best):
            best = arr
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("html", help="models 榜原始 HTML（curl 落盘的那个文件）")
    parser.add_argument("out", help="输出载荷 JSON 路径")
    parser.add_argument("--quiet", action="store_true", help="不打印 haiku 行明细")
    args = parser.parse_args()

    src = Path(args.html)
    if not src.is_file():
        print(f"ERROR: 读不到 {src}；先用 curl 抓 models 榜原始 HTML。", file=sys.stderr)
        return 1
    raw = src.read_bytes()
    html = raw.decode("utf-8", errors="replace")
    decoded = decode_rsc(html)
    rows = biggest_models_array(decoded)
    if not rows:
        print(f"ERROR: {src} 里找不到含 name/slug/intelligenceIndex 的载荷数组（页面结构可能变了）。", file=sys.stderr)
        return 1

    for r in rows:
        if r.get("terminalBench40") is None and isinstance(r.get("scores"), dict):
            if "terminalbench-4-0" in r["scores"]:
                r["terminalBench40"] = r["scores"]["terminalbench-4-0"]

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"decoded rsc chars: {len(decoded)}", file=sys.stderr)
    print(f"models rows: {len(rows)}", file=sys.stderr)
    print(f"input sha256: {hashlib.sha256(raw).hexdigest()}", file=sys.stderr)
    print(f"written {args.out}", file=sys.stderr)
    if not args.quiet:
        for r in rows:
            if "haiku" in r.get("slug", ""):
                print(f"  {r['slug']}: idx={r['intelligenceIndex']} est={r['intelligenceIndexIsEstimated']} "
                      f"tb40={r.get('terminalBench40')} in={r.get('price1mInputTokens')} "
                      f"out={r.get('price1mOutputTokens')} cache={r.get('cacheHitPrice')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
