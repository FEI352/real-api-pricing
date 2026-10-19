#!/usr/bin/env python3
"""按 AA 公开载荷一键录入模型分数（supplement 档），替代手工建档四步。

背景：2026-10-19 给 MiniMax-M3.1-Flash 补分时，实际走了
「curl 抓页 → aa_fetch_parse → 手写 supplement JSON → 改 compute.SCORE_FILES → compute」
五步，其中手写 JSON（rank / rawRecords / 两条 board）最费时且容易抄错。
本脚本把整条链收敛成一次调用：

    .venv/bin/python scripts/add_score.py --model minimax-m3.1-flash-preview
    .venv/bin/python scripts/add_score.py --model <id> --slug <aa-slug> --deploy

做的事：
  1. 抓 models 榜原始 HTML，用 aa_fetch_parse 的同一套解码取载荷；
  2. 按 --slug 精确匹配，缺省时按模型名/短名大小写无关匹配（歧义即报错，不猜）；
  3. 生成 supplement 档：aa_intelligence_index + aa_terminal_bench_4 两条，
     逐字段取自原始行（分数 4 位小数、variantLabel 用原始 shortName，绝不改写）；
  4. 幂等注册进 scripts/compute.py 的 SCORE_FILES 末尾（已在则不重复）；
  5. 跑 compute.py，打印分数 / TB4 / 每任务成本与前沿压制判定。

`--deploy` 追加完整图表链 + 前端构建 + 部署 + 18 项守卫（等同 `cli update`）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from aa_fetch_parse import biggest_models_array, decode_rsc  # noqa: E402

MODELS_URL = "https://artificialanalysis.ai/leaderboards/models"
UA = "Mozilla/5.0"
RESEARCH = ROOT / "data" / "research"
COMPUTE = ROOT / "scripts" / "compute.py"
PY = str(ROOT / ".venv" / "bin" / "python")

BOARD_TB4 = "aa_terminal_bench_4"
BOARD_IDX = "aa_intelligence_index"


def fetch_payload(url: str, timeout: float = 90) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="replace")
    payload = biggest_models_array(decode_rsc(html))
    if not payload:
        raise SystemExit("ERROR: 载荷解析为空（页面结构变了？先跑 scripts/source_health.py）")
    return payload


def pick_row(payload: list[dict], model: str, slug: str | None) -> dict:
    if slug:
        row = next((r for r in payload if r.get("slug") == slug), None)
        if row is None:
            near = [r["slug"] for r in payload if slug in str(r.get("slug"))][:5]
            raise SystemExit(f"ERROR: 载荷里没有 slug={slug!r}；近似项：{near}")
        return row
    key = model.lower().replace("-", "").replace(".", "")
    hits = [r for r in payload
            if key in str(r.get("name", "")).lower().replace("-", "").replace(".", "")
            or key in str(r.get("slug", "")).lower().replace("-", "").replace(".", "")]
    if len(hits) != 1:
        names = [f'{r["slug"]} ({r.get("name")})' for r in hits[:8]]
        raise SystemExit(f"ERROR: --model {model!r} 命中 {len(hits)} 行，需用 --slug 指定：{names}")
    return hits[0]


def build_snapshot(row: dict, payload: list[dict], collected_at: str, round_no: int) -> dict:
    ranked = sorted((r for r in payload if r.get("intelligenceIndex") is not None),
                    key=lambda r: -r["intelligenceIndex"])
    rank = next((i + 1 for i, r in enumerate(ranked) if r["slug"] == row["slug"]), None)
    label = row.get("shortName") or row["name"]
    idx = round(row["intelligenceIndex"], 4)
    scores = [{
        "boardId": BOARD_IDX,
        "model": None,  # 由调用方填项目 id
        "variantLabel": label,
        "score": idx,
        "secondary": {
            "configurationRank": rank,
            "costPerTaskUsd": (round(row["intelligenceIndexCostPerTask"], 4)
                               if row.get("intelligenceIndexCostPerTask") is not None else None),
            "scoreRoundedDisplay": round(row["intelligenceIndex"]),
            "intelligenceIndexIsEstimated": row.get("intelligenceIndexIsEstimated"),
            "slug": row["slug"],
            "shortName": label,
            "isReasoning": row.get("isReasoning"),
            "deprecated": row.get("deprecated"),
            "modelCreatorName": row.get("modelCreatorName"),
            "effort": None,  # 载荷无 effort 字段，AA 单档模型为 null
            "terminalBench40": row.get("terminalBench40"),
            "scicode": row.get("scicode"),
            "hle": row.get("hle"),
            "price1mInputTokens": row.get("price1mInputTokens"),
            "price1mOutputTokens": row.get("price1mOutputTokens"),
            "cacheHitPrice": row.get("cacheHitPrice"),
            "intelligenceIndex": row["intelligenceIndex"],
            "medianOutputTokensPerSecond": row.get("medianOutputTokensPerSecond"),
            "medianTimeToFirstTokenSeconds": row.get("medianTimeToFirstTokenSeconds"),
            "medianEndToEndResponseTimeSeconds": row.get("medianEndToEndResponseTimeSeconds"),
            "medianReasoningTimeSeconds": row.get("medianReasoningTimeSeconds"),
        },
        "source": MODELS_URL,
        "checkedAt": collected_at,
    }]
    if row.get("terminalBench40") is not None:
        scores.append({
            "boardId": BOARD_TB4,
            "model": None,
            "variantLabel": "Artificial Analysis - " + label,
            "score": round(row["terminalBench40"] * 100, 4),
            "secondary": {
                "configurationRank": rank,
                "agentHarness": "Artificial Analysis",
                "reasoningEffort": None,
                "slug": row["slug"],
                "shortName": label,
                "terminalBench40Fraction": row["terminalBench40"],
            },
            "source": MODELS_URL,
            "checkedAt": collected_at,
            "note": ("AA 自家 harness 独立实测的 Terminal-Bench 4.0（models 榜载荷 terminalBench40 字段，"
                     "fraction×100）；与 tbench.ai 官方榜分开计分。"),
        })
    return {
        "collectedAt": collected_at,
        "supplement": True,
        "supplementNote": (f"{label} 于 {collected_at} 从 AA models 榜载荷录入："
                           f"intelligenceIndex {idx}、Terminal-Bench 4.0 "
                           f"{(row.get('terminalBench40') or 0) * 100:.4f}%。"),
        "boards": [
            {"boardId": BOARD_IDX, "name": "Artificial Analysis Intelligence Index v4.3",
             "url": MODELS_URL, "metric": "Intelligence Index", "snapshotDate": collected_at,
             "note": f"{collected_at} 抓取 AA models 榜载荷中的该模型行。"},
            {"boardId": BOARD_TB4, "name": "Terminal-Bench 4.0 (AA)", "url": MODELS_URL,
             "metric": "Resolution Rate %", "snapshotDate": collected_at,
             "note": "AA 自家 harness 独立实测的 Terminal-Bench 4.0（models 榜载荷 terminalBench40 字段 × 100）。"},
        ],
        "scores": scores,
        "rawRecords": {"aa_intelligence_index": [row],
                       "note": "AA 公开载荷原样留存，供 verify_aa_snapshot.py 交叉核对。"},
    }


def register_score_file(name: str) -> bool:
    text = COMPUTE.read_text(encoding="utf-8")
    if f'"{name}"' in text:
        return False
    marker = "\n)\nLIST_PRICE_FILES"
    if marker not in text:
        raise SystemExit("ERROR: compute.py 的 SCORE_FILES 块结构变了，需人工注册")
    COMPUTE.write_text(text.replace(marker, f'\n    "{name}",{marker}', 1), encoding="utf-8")
    return True


def summarise(model: str) -> None:
    points = json.loads((ROOT / "derived" / "points.json").read_text(encoding="utf-8"))["points"]
    rows = [p for p in points if p["model"] == model]
    if not rows:
        print(f"⚠️  derived/points.json 里没有 {model} 的订阅行（分数已入库，但没有套餐点引用它）")
        return
    state = json.loads((ROOT / "data" / "research" / "frontier-state.json").read_text(encoding="utf-8"))
    frontier = [(p["score"], p["cost"]) for p in state["cost_frontier"]]
    print(f"\n{model}：{len(rows)} 个订阅点")
    for p in sorted(rows, key=lambda r: r["real_usd_per_mtok"]):
        score = p.get(f"{BOARD_IDX}__score")
        cost = p.get(f"{BOARD_IDX}__mean_cost_usd_per_task")
        bench = p.get(f"{BOARD_IDX}__benchmark_list_price")
        task_cost = (cost * p["real_usd_per_mtok"] / bench) if (cost and bench and p["real_usd_per_mtok"]) else None
        dom = next(((s, c) for s, c in frontier if s >= (score or 0) and c <= (task_cost or 9e9)), None)
        mark = f"被 {dom[0]:.2f}分/${dom[1]:.5f} 支配" if dom else "★ 进成本前沿"
        print(f"  {p['id']:52s} 分={score} 每任务成本={task_cost and round(task_cost, 5)} {mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="项目模型 id（如 minimax-m3.1-flash-preview）")
    ap.add_argument("--slug", help="AA 载荷 slug（省略则按名字唯一匹配，歧义即报错）")
    ap.add_argument("--date", default=date.today().isoformat(), help="采集日 YYYY-MM-DD，默认今天")
    ap.add_argument("--round", type=int, default=1, help="档内轮次号（同名档已存在时改此值）")
    ap.add_argument("--deploy", action="store_true", help="追加完整图表链 + 前端构建 + 部署 + 18 项守卫")
    ap.add_argument("--dry-run", action="store_true", help="只打印将写入的内容，不落盘")
    args = ap.parse_args()

    payload = fetch_payload(MODELS_URL)
    row = pick_row(payload, args.model, args.slug)
    doc = build_snapshot(row, payload, args.date, args.round)
    stem = re.sub(r"[^a-z0-9]+", "-", args.model.replace(".", "")).strip("-")
    out = RESEARCH / f"scores-{stem}-round{args.round}-{args.date}.json"
    for s in doc["scores"]:
        s["model"] = args.model

    if out.exists():
        raise SystemExit(f"ERROR: {out.name} 已存在；确认要覆盖加 --round N 或先删旧档")
    label = doc["scores"][0]["variantLabel"]
    print(f"slug     : {row['slug']}")
    print(f"model    : {args.model}")
    print(f"label    : {label}")
    print(f"index    : {doc['scores'][0]['score']}  (rank {doc['scores'][0]['secondary']['configurationRank']})")
    if len(doc["scores"]) > 1:
        print(f"TB4      : {doc['scores'][1]['score']}%")
    print(f"file     : {out.relative_to(ROOT)}")
    if args.dry_run:
        print("\n--dry-run：未写入")
        return 0

    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    added = register_score_file(out.name)
    print(f"SCORE_FILES: {'已注册' if added else '已存在，跳过'}")

    subprocess.run([PY, "scripts/compute.py"], cwd=ROOT, check=True)
    summarise(args.model)

    if args.deploy:
        print("\n[deploy] 完整图表链 + 前端 + 部署 + 守卫 ...")
        subprocess.run([PY, "scripts/cli.py", "update"], cwd=ROOT, check=True)
        subprocess.run([PY, "scripts/checks/run_all.py"], cwd=ROOT, check=True)
    else:
        print("\n下一步：--deploy 或 `real-api-pricing-cli update` 后跑 run_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
