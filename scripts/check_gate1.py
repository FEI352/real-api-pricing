# -*- coding: utf-8 -*-
"""门禁 1 检查器：把每日 Prompt 的三条硬断言变成可执行判定（只读，除 --save-baseline/--json 外不写文件）。

三条断言（基于 derived/points.json）：
  1. 点数 ≥ 基线（基线必须在 compute 覆盖 derived/ 之前落盘，见 --save-baseline）；
  2. 本轮新模型（= 本轮 --slug-map/--host-map 新增映射到的**项目模型 id**）出现在 points.json；
  3. 新模型的订阅点（billing=subscription 且非 unmetered）的 list_blended_usd_per_mtok 非空；
     若某模型确实没有公开三段价（如 ChatGPT 独占款），显式列入 --allow-no-price 并在报告里写明理由。

用法（Python 用仓库 .venv）：
  # 阶段 2 之前：落基线（此刻 derived/ 还是上一版）
  .venv/bin/python scripts/check_gate1.py --save-baseline /tmp/gate1-baseline.json

  # 阶段 2 之后：跑断言
  .venv/bin/python scripts/check_gate1.py
      --baseline /tmp/gate1-baseline.json
      --new-models claude-haiku-5.5 --allow-no-price gpt-6-astra
      --json /tmp/gate1-result.json

  # Worker-AA 报告用：与上一版快照做模型集差分（新增/消失）
  .venv/bin/python scripts/check_gate1.py --diff-models scores-aa-round5-2026-10-01.json

基线文件 schema：
  {"schemaVersion": 1, "generatedAt": "<ISO8601>", "points": <int>, "models": ["<model id>", ...],
   "subscriptionListBlended": {"rows": <int>, "nonnull": <int>}}

退出码：0 = 全部通过；1 = 有断言失败；2 = 参数/文件问题（无法判定，不许当通过）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POINTS = ROOT / "derived" / "points.json"
RESEARCH = ROOT / "data" / "research"
SCHEMA_VERSION = 1


def load_points(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    points = doc["points"] if isinstance(doc, dict) and "points" in doc else doc
    if not isinstance(points, list):
        raise ValueError(f"{path} 里没有 points 数组")
    return points


def is_subscription(p: dict) -> bool:
    return p.get("billing") == "subscription" and not p.get("unmetered")


def snapshot_of(points: list[dict]) -> dict:
    sub = [p for p in points if is_subscription(p)]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "points": len(points),
        "models": sorted({p.get("model") for p in points if p.get("model")}),
        "subscriptionListBlended": {
            "rows": len(sub),
            "nonnull": sum(1 for p in sub if p.get("list_blended_usd_per_mtok") is not None),
        },
    }


def newest_snapshot() -> Path | None:
    cands = sorted(RESEARCH.glob("scores-aa-round*.json"), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def models_of_snapshot(path: Path) -> set[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {s["model"] for s in doc["scores"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--points", default=str(DEFAULT_POINTS), help="当前 points.json（默认 derived/points.json）")
    parser.add_argument("--save-baseline", metavar="PATH", help="把当前 points.json 落成基线文件")
    parser.add_argument("--baseline", metavar="PATH", help="与基线比对并跑三条断言")
    parser.add_argument("--new-models", help="本轮新模型（逗号分隔）；缺省则用基线做模型集自动差分")
    parser.add_argument("--allow-no-price", action="append", default=[],
                        help="允许 list_blended 为空的模型（无公开三段价；需在报告里写明理由），可重复")
    parser.add_argument("--diff-models", metavar="PRIOR_SNAPSHOT",
                        help="模型集差分：与上一版 AA 快照比较（Worker-AA 报告用）")
    parser.add_argument("--snapshot", help="配合 --diff-models 的当前快照（默认取 data/research 里最新的 scores-aa-round*.json）")
    parser.add_argument("--json", metavar="PATH", help="把结果摘要写成 JSON（便于自动化归档）")
    args = parser.parse_args()

    points_path = Path(args.points)
    if not points_path.is_absolute():
        points_path = ROOT / points_path

    # 模式 A：落基线
    if args.save_baseline:
        if not points_path.is_file():
            print(f"ERROR: 读不到 {points_path}；无法落基线。", file=sys.stderr)
            return 2
        snap = snapshot_of(load_points(points_path))
        out = Path(args.save_baseline)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"baseline saved: {out} | points={snap['points']} models={len(snap['models'])} "
              f"subscription={snap['subscriptionListBlended']['rows']} "
              f"list_blended_nonnull={snap['subscriptionListBlended']['nonnull']}")
        return 0

    # 模式 B：模型集差分（Worker-AA 报告用）
    if args.diff_models:
        prior = Path(args.diff_models)
        if not prior.is_file():
            prior = RESEARCH / args.diff_models
        cur = Path(args.snapshot) if args.snapshot else newest_snapshot()
        if not prior.is_file() or cur is None or not cur.is_file():
            print(f"ERROR: 快照缺失（prior={prior} current={cur}）；无法判定。", file=sys.stderr)
            return 2
        old, new = models_of_snapshot(prior), models_of_snapshot(cur)
        added, removed = sorted(new - old), sorted(old - new)
        print(f"prior={prior.name} current={cur.name}")
        print(f"added({len(added)}): {added}")
        print(f"removed({len(removed)}): {removed}")
        if args.json:
            Path(args.json).write_text(json.dumps(
                {"prior": prior.name, "current": cur.name, "added": added, "removed": removed},
                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0

    # 模式 C：断言
    if not args.baseline:
        print("ERROR: 需要 --save-baseline / --baseline / --diff-models 三者之一。", file=sys.stderr)
        return 2
    base_path = Path(args.baseline)
    if not base_path.is_file() or not points_path.is_file():
        print(f"ERROR: 读不到基线或 points.json（baseline={base_path} points={points_path}）；无法判定。",
              file=sys.stderr)
        return 2
    baseline = json.loads(base_path.read_text(encoding="utf-8"))
    if baseline.get("schemaVersion") != SCHEMA_VERSION:
        print(f"ERROR: 基线 schemaVersion={baseline.get('schemaVersion')} 与预期 {SCHEMA_VERSION} 不符；"
              f"重新 --save-baseline。", file=sys.stderr)
        return 2

    points = load_points(points_path)
    now = snapshot_of(points)
    new_models = ([m.strip() for m in args.new_models.split(",") if m.strip()]
                  if args.new_models else sorted(set(now["models"]) - set(baseline.get("models", []))))
    allow = set(args.allow_no_price)

    fails: list[str] = []
    results: list[dict] = []

    ok1 = now["points"] >= baseline["points"]
    print(f"[{'PASS' if ok1 else 'FAIL'}] 断言1 点数≥上一版: 实际 {now['points']} / 期望 ≥{baseline['points']}")
    results.append({"assertion": "points_not_decreased", "ok": ok1, "actual": now["points"], "expected": baseline["points"]})
    if not ok1:
        fails.append("点数下降")

    missing = [m for m in new_models if m not in set(now["models"])]
    ok2 = not missing
    print(f"[{'PASS' if ok2 else 'FAIL'}] 断言2 新模型出现在 points.json: 本轮新模型 {new_models or '（无）'}"
          f"{'，缺失 ' + str(missing) if missing else ''}")
    results.append({"assertion": "new_models_present", "ok": ok2, "newModels": new_models, "missing": missing})
    if not ok2:
        fails.append("新模型缺失")

    bad_rows: dict[str, dict] = {}
    for m in new_models:
        rows = [p for p in points if p.get("model") == m and is_subscription(p)]
        nonnull = [p for p in rows if p.get("list_blended_usd_per_mtok") is not None]
        if m in allow:
            print(f"      · {m}: 订阅点 {len(nonnull)}/{len(rows)} 有 list_blended（已列入 --allow-no-price，跳过）")
            continue
        if rows and len(nonnull) != len(rows):
            bad_rows[m] = {"rows": len(rows), "nonnull": len(nonnull)}
    ok3 = not bad_rows
    print(f"[{'PASS' if ok3 else 'FAIL'}] 断言3 新模型订阅点 list_blended 非空: "
          f"{'全部非空' if ok3 else '空值 ' + json.dumps(bad_rows, ensure_ascii=False)}"
          f"（缺 list_blended → 每任务成本走 3.51759 常数回退，前沿会画歪）")
    results.append({"assertion": "new_models_list_blended", "ok": ok3, "bad": bad_rows})
    if not ok3:
        fails.append("list_blended 为空")

    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "checkedAt": now["generatedAt"],
        "baseline": {"file": str(base_path), "points": baseline["points"]},
        "current": {"file": str(points_path), "points": now["points"],
                    "subscriptionListBlended": now["subscriptionListBlended"]},
        "newModels": new_models,
        "allowNoPrice": sorted(allow),
        "results": results,
        "passed": not fails,
        "failures": fails,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if fails:
        print(f"GATE1 FAIL: {'；'.join(fails)}")
        return 1
    print(f"GATE1 PASS: 点数 {now['points']}（基线 {baseline['points']}），新模型 {new_models or '（无）'}，"
          f"订阅点 list_blended 非空 {now['subscriptionListBlended']['nonnull']}/{now['subscriptionListBlended']['rows']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
