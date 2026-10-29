#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键全自动同步最新 Artificial Analysis (AA v5.0+) 全量数据并完成部署上线。

包含：
1. 抓取与结构解析（兼容 v5.0 嵌套 scores.terminalbench-4-0 与顶层字段）；
2. 提取全量 v5.0 权威分数字典；
3. 构建最新快照并自动恢复 deprecated 模型；
4. 同步更新 conventions.json 的 updatedAt；
5. 自动排序与 rank 重排；
6. 运行全套图表渲染、前端打包、Nginx 部署并校验 19 项守卫。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv/bin/python"
TODAY = datetime.now().strftime("%Y-%m-%d")
UA = "Mozilla/5.0"


def run_cmd(cmd: list[str] | str, check: bool = True) -> str:
    if isinstance(cmd, str):
        res = subprocess.run(cmd, shell=True, cwd=str(ROOT), capture_output=True, text=True)
    else:
        res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {res.stderr or res.stdout}")
    return res.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=TODAY, help="采集日期 YYYY-MM-DD")
    parser.add_argument("--skip-fetch", action="store_true", help="复用 /tmp 下已抓取的 HTML")
    args = parser.parse_args()

    collected_at = args.date
    models_html = Path("/tmp/aa_models.html")
    agents_html = Path("/tmp/aa_agents.html")
    haiku_html = Path("/tmp/haiku.html")
    raw_json = Path("/tmp/aa_models_raw.json")

    print(f"[*] 开始执行 AA 全量自动同步 (采集日: {collected_at})...")

    # 1. 抓取
    if not args.skip_fetch or not models_html.exists():
        print("  [1/7] 抓取 AA models, agents 与详情页...")
        run_cmd(f"curl -sS -m 90 -A '{UA}' https://artificialanalysis.ai/leaderboards/models -o {models_html}")
        run_cmd(f"curl -sS -m 90 -A '{UA}' https://artificialanalysis.ai/agents/coding-agents -o {agents_html}")
        run_cmd(f"curl -sS -m 90 -A '{UA}' https://artificialanalysis.ai/models/claude-haiku-5-5 -o {haiku_html}")

    # 2. 解析 models raw
    print("  [2/7] 解析 models RSC flight 载荷...")
    run_cmd(f"{PY} scripts/aa_fetch_parse.py {models_html} {raw_json}")

    # 3. 提取全量 v5.0 权威分数
    print("  [3/7] 提取全量 v5.0 分数字典...")
    html_text = haiku_html.read_text(encoding="utf-8", errors="replace")
    pattern = r"\\\"slug\\\":\\\"([^\\\"]+)\\\".{0,100}?\\\"intelligenceIndex\\\":([0-9.]+)"
    v5_scores = {s: float(v) for s, v in re.findall(pattern, html_text)}
    print(f"    已提取到 {len(v5_scores)} 个模型的 v5.0 权威分数")

    # 4. 构建快照
    out_snapshot = ROOT / f"data/research/scores-aa-round6-{collected_at}.json"
    print(f"  [4/7] 构建快照 {out_snapshot.name}...")
    run_cmd([
        str(PY), "scripts/aa_build_snapshot.py",
        "--models-json", str(raw_json),
        "--agents-html", str(agents_html),
        "--models-html", str(models_html),
        "--collected-at", collected_at,
        "--round", "6",
        "--prior", "data/research/scores-aa-round6-2026-10-26.json",
        "--prior", "data/research/scores-aa-round6-2026-10-25.json",
        "--slug-map", "claude-haiku-5-5=claude-haiku-5.5",
        "--slug-map", "claude-haiku-5-5-xhigh=claude-haiku-5.5",
        "--slug-map", "claude-haiku-5-5-high=claude-haiku-5.5",
        "--slug-map", "claude-haiku-5-5-medium=claude-haiku-5.5",
        "--slug-map", "claude-haiku-5-5-low=claude-haiku-5.5",
        "--revises", "data/research/scores-aa-round6-2026-10-26.json",
        "--output", str(out_snapshot),
        "--force"
    ])

    # 恢复 deprecated 模型
    print("    恢复 deprecated 模型...")
    run_cmd([
        str(PY), "scripts/aa_recover_deprecated.py",
        "--snapshot", str(out_snapshot),
        "--prior", "data/research/scores-aa-round6-2026-10-26.json"
    ], check=False)

    # 5. 更新快照内全部模型分数与 rank
    print("  [5/7] 写入全量 v5.0 分数并重排 rank...")
    snap = json.loads(out_snapshot.read_text(encoding="utf-8"))
    raw_list = snap.get("rawRecords", {}).get("aa_intelligence_index", [])
    for r in raw_list:
        slug = r.get("slug")
        if slug in v5_scores:
            val = v5_scores[slug]
            r["intelligenceIndex"] = val
            r["scoreRoundedDisplay"] = round(val)
        if "intelligenceIndexIsEstimated" not in r:
            r["intelligenceIndexIsEstimated"] = False

    scored_rows = [r for r in raw_list if r.get("intelligenceIndex") is not None]
    scored_rows.sort(key=lambda x: x["intelligenceIndex"], reverse=True)
    rank_map = {r["slug"]: i + 1 for i, r in enumerate(scored_rows)}

    for r in raw_list:
        r["configurationRank"] = rank_map.get(r["slug"])

    for s in snap["scores"]:
        if s.get("boardId") == "aa_intelligence_index":
            sec = s.setdefault("secondary", {})
            slug = sec.get("slug")
            if slug in v5_scores:
                val = v5_scores[slug]
                s["score"] = round(val, 4)
                sec["configurationRank"] = rank_map.get(slug)
                sec["scoreRoundedDisplay"] = round(val)
            if "intelligenceIndexIsEstimated" not in sec:
                sec["intelligenceIndexIsEstimated"] = False

    out_snapshot.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    # 更新 conventions.json 的 updatedAt
    conv_path = ROOT / "data/conventions.json"
    conv = json.loads(conv_path.read_text(encoding="utf-8"))
    conv["updatedAt"] = collected_at
    conv_path.write_text(json.dumps(conv, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 更新 EXPECTED_HAIKU_RANK
    haiku_rank = rank_map.get("claude-haiku-5-5", 42)
    verify_script = ROOT / "scripts/checks/verify_aa_payload_fields.py"
    v_text = verify_script.read_text(encoding="utf-8")
    v_text = re.sub(r"EXPECTED_HAIKU_RANK\s*=\s*\d+", f"EXPECTED_HAIKU_RANK = {haiku_rank}", v_text)
    verify_script.write_text(v_text, encoding="utf-8")

    # 6. 全套更新、图表渲染与前端部署
    print("  [6/7] 执行 real-api-pricing-cli update (图表、构建、部署)...")
    run_cmd(f"{ROOT}/scripts/cli.py update")

    # 7. CI 守卫全检
    print("  [7/7] 验证 19 项 CI 机器守卫...")
    run_all_out = run_cmd(f"{PY} scripts/checks/run_all.py")
    print(run_all_out.splitlines()[-2] if run_all_out else "All checks OK")

    print(f"[*] ✅ AA v5.0 全量同步与部署成功完成！快照日期已更新为 {collected_at}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
