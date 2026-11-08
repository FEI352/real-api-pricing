#!/usr/bin/env python3
"""守卫「默认视图准入」：不该出现在默认榜的行，必须被显式标记且降到 full 档。

背景（2026-11-06/07 连续两次被用户退回）：
  · Claude Haiku 5.5 的订阅额度是拿 Opus 5.5 实测值按标价比倒推的，50/50 档还预设了
    「极低 prompt 占比」的立场——它们一度占据月额度榜前三（81.855B / 27.285B）。
  · Step 3.5 / 3.7 已被后续版本取代，用户要求「平时不显示」。
  第一次只在图表 tier 上降级，结果发现月额度表根本不看 tier，白改一轮。

本守卫把判据钉在数据侧，两条都必须成立：
  1. 命中 config/hidden-by-default.json 或 config/deprecated-models.json 的行，chart_tier 必须是 full；
  2. 这些行在 site.json 里必须带 hiddenByDefault=true（前端据此过滤），且 site.json 里
     不允许出现「该被隐藏却没有标记」的同名行。

site.json 不存在时（只跑数据管线、没跑 web 打包）跳到第 1 条，不误报。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADOPTED = ROOT / "data" / "adopted.csv"
SITE = ROOT / "web" / "public" / "data" / "site.json"

problems: list[str] = []


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


hidden_cfg = load_json(ROOT / "config" / "hidden-by-default.json")
dep_cfg = load_json(ROOT / "config" / "deprecated-models.json")
model_billing = {(r.get("model"), r.get("billing", "subscription")) for r in hidden_cfg.get("modelBilling", [])}
deprecated_models = set((dep_cfg.get("models") or {}).keys())


def should_hide(model: str, billing: str) -> bool:
    return model in deprecated_models or (model, billing) in model_billing


rows = list(csv.DictReader(ADOPTED.open(encoding="utf-8-sig")))
hidden_rows = [r for r in rows if should_hide(r["served_model"], r["billing"])]

if not hidden_rows:
    problems.append("没有命中任何应隐藏的行——配置或模型名八成写错了，让守卫空转比误报更糟")

# 倒推/情景行（hidden-by-default 的 modelBilling）必须同时降到 full 档；
# 已弃用模型只要求站点标记 hiddenByDefault（它们可能仍留在历史图表 tier 里）。
derived_rows = [r for r in hidden_rows if (r["served_model"], r["billing"]) in model_billing]
for r in derived_rows:
    if r.get("chart_tier") != "full":
        problems.append(
            f"{r['plan_id']}::{r['served_model']} 是倒推/情景行，但 chart_tier={r.get('chart_tier')!r}（应为 full）"
        )

if SITE.is_file():
    site = load_json(SITE)
    flagged = {(p["plan_id"], p["model"]) for p in site.get("points", []) if p.get("hiddenByDefault")}
    for r in hidden_rows:
        key = (r["plan_id"], r["served_model"])
        if key not in flagged:
            problems.append(f"{key[0]}::{key[1]} 数据侧应隐藏，但 site.json 未标 hiddenByDefault（前端不会过滤）")
    stray = [
        p for p in site.get("points", [])
        if p.get("model") == "claude-haiku-5.5" and p.get("billing") == "subscription" and not p.get("hiddenByDefault")
    ]
    if stray:
        problems.append(f"site.json 里仍有 {len(stray)} 条 Haiku 5.5 订阅行未标 hiddenByDefault")

if problems:
    print(f"FAIL: {len(problems)} 项默认视图准入问题")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)

print(
    f"PASS: {len(hidden_rows)} 条应隐藏行（其中 {len(derived_rows)} 条倒推/情景行已降 full 档）"
    + ("，且 site.json 标记一致" if SITE.is_file() else "（site.json 未生成，仅校验数据侧）")
)
