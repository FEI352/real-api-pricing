#!/usr/bin/env python3
"""模型 × 榜 可见性矩阵 / Model-by-board visibility matrix.

回答「某模型在哪能看到、在哪看不到、为什么」——不必再手工交叉查
mappings × configurations。

    .venv/bin/python scripts/visibility_matrix.py                 # 全量矩阵 + 榜统计
    .venv/bin/python scripts/visibility_matrix.py --model claude-haiku-5.5
    .venv/bin/python scripts/visibility_matrix.py --write-docs    # 生成 docs/VISIBILITY.md

口径：site.json 里的 mappings 是压缩引用（只有 point_id + configuration_id + 映射元数据），
分数/榜单/负载字段都在 configurations 里；本脚本按 web/src/loadData.ts 的 unpackData()
做同一份 join，所以结论与网页一致。

可见性判据（与 web/src/domain.ts 一致）：
  能力图（pareto / 榜单图）—— 需要该榜有 score；
  真实单价轴（默认 X）    —— 不需要分数，任何点都能画；
  月额度视图              —— 需要 monthly_yi（订阅点）；
  完整数据表              —— 任何点都会列出，该榜无分时分数列为空；
  任务用时轴              —— 需要映射配置的 response_time_seconds（缺则不在该轴出现）；
  每任务成本轴            —— 需要 median_cost_usd_per_task，或退化为
                             MEDIAN_AA_TASK_MTOK × 真实单价的常数回退。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "web" / "public" / "data" / "site.json"
DOC = ROOT / "docs" / "VISIBILITY.md"
SGT = timezone(timedelta(hours=8))

# 模型数超过这个值时，总表只列「至少有一个榜有分」的模型，其余折进摘要（防文档爆炸）。
MATRIX_ROW_LIMIT = 200

NO_SCORE_NOTE = "无分数 → 该榜图不显示"

# 控制台矩阵的短标签（避免 11 字符截断后 aa_intelligence / aa_terminal / aa_coding 难分辨）。
BOARD_SHORT = {
    "aa_intelligence_index": "aa-intel",
    "terminal_bench_4": "tb4",
    "aa_terminal_bench_4": "tb4-aa",
    "arena_code": "arena-code",
    "arena_agent_mode": "arena-agent",
    "aa_coding_agent_index": "aa-coding",
    "open_design_arena": "open-design",
    "deepswe_1_1": "deepswe",
}


def short(board: str) -> str:
    return BOARD_SHORT.get(board, board[:11])


def load() -> tuple[dict, list[dict]]:
    if not SITE.exists():
        sys.exit(f"missing {SITE.relative_to(ROOT)}; run `npm --prefix web run data` first")
    data = json.loads(SITE.read_text(encoding="utf-8"))
    configs = {c["configuration_id"]: c for c in data["configurations"]}
    joined: list[dict] = []
    for m in data["mappings"]:
        cfg = configs.get(m["configuration_id"])
        if cfg is None:
            sys.exit(f"orphan benchmark mapping: {m['configuration_id']}")
        merged = {k: v for k, v in cfg.items() if k != "model"}
        merged.update(m)
        merged["model"] = cfg["model"]
        joined.append(merged)
    return data, joined


def boards_of(data: dict) -> list[str]:
    return list(data["boards"])


def board_stats(data: dict, joined: list[dict]) -> list[dict]:
    stats = []
    for board in boards_of(data):
        rows = [m for m in joined if m["board"] == board]
        scored = [m for m in rows if m["score"] is not None]
        best = max(scored, key=lambda m: m["score"]) if scored else None
        stats.append(
            {
                "board": board,
                "name": data["boards"][board]["name"],
                "snapshot": data["boards"][board]["snapshot"],
                "configurations": len({m["configuration_id"] for m in rows}),
                "mappings": len(rows),
                "models": len({m["model"] for m in scored}),
                "best_model": best["model"] if best else None,
                "best_score": best["score"] if best else None,
                "best_variant": best["variant"] if best else None,
            }
        )
    return stats


def scored_boards(joined: list[dict]) -> dict[str, set[str]]:
    per: dict[str, set[str]] = defaultdict(set)
    for m in joined:
        if m["score"] is not None:
            per[m["model"]].add(m["board"])
    return per


def models_of(data: dict) -> list[str]:
    return sorted({p["model"] for p in data["points"]})


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def md_cell(text: object) -> str:
    """Markdown 表格单元格：转义竖线，避免榜名（如 Code Arena | WebDev Overall）拆列。"""
    return str(text).replace("|", "\\|")


def print_board_stats(data: dict, joined: list[dict]) -> None:
    print("== 榜覆盖统计 / board coverage ==")
    print(f"{'board':<24}{'configs':>8}{'mappings':>9}{'models':>7}  best")
    for s in board_stats(data, joined):
        best = (
            f"{fmt(s['best_score'])} · {s['best_model']} · {s['best_variant']}"
            if s["best_score"] is not None
            else "—"
        )
        print(
            f"{s['board']:<24}{s['configurations']:>8}{s['mappings']:>9}{s['models']:>7}  {best}"
        )


def print_matrix(data: dict, joined: list[dict]) -> None:
    boards = boards_of(data)
    per = scored_boards(joined)
    models = models_of(data)
    scored_models = [m for m in models if per.get(m)]
    truncated = len(models) > MATRIX_ROW_LIMIT
    shown = scored_models if truncated else models
    print()
    print("== 模型 × 榜 可见性 / visibility (✓ 有分, — 无分) ==")
    print(f"{'model':<34}" + "".join(f"{short(b):>13}" for b in boards) + f"{'#boards':>9}")
    for model in shown:
        cells = "".join(f"{('✓' if b in per.get(model, set()) else '—'):>13}" for b in boards)
        print(f"{model:<34}{cells}{len(per.get(model, set())):>9}")
    print("legend: " + " | ".join(f"{short(b)} = {b}" for b in boards))
    if truncated:
        print(
            f"(模型数 {len(models)} > {MATRIX_ROW_LIMIT}：仅列有分模型 {len(scored_models)} 个，"
            f"其余 {len(models) - len(scored_models)} 个在所有榜均无分)"
        )


def query_model(data: dict, joined: list[dict], model: str) -> None:
    boards = boards_of(data)
    points = [p for p in data["points"] if p["model"] == model]
    rows = [m for m in joined if m["model"] == model]
    if not points and not rows:
        sys.exit(f"unknown model: {model}（可用 --list-models 查看全部）")
    print(f"== {model} ==")
    print(f"订阅/API 点：{len(points)} 个" + (f" | 映射：{len(rows)} 条" if rows else ""))
    with_score: list[str] = []
    print()
    print(f"{'board':<24}{'score':>10}  configuration")
    for board in boards:
        bms = [m for m in rows if m["board"] == board]
        scored = [m for m in bms if m["score"] is not None]
        if not scored:
            reason = "无该榜映射" if not bms else "有映射但无分数"
            print(f"{board:<24}{'—':>10}  {reason} → {NO_SCORE_NOTE}")
            continue
        best = max(scored, key=lambda m: m["score"])
        with_score.append(board)
        print(f"{board:<24}{fmt(best['score']):>10}  {best['variant']}")
    print()
    print(f"有分榜：{len(with_score)}/{len(boards)}" + (f"（{', '.join(with_score)}）" if with_score else ""))
    print("无分也能看到：真实单价轴（默认 X）、完整数据表（分数列空）"
          + ("、月额度视图（订阅点）" if any(p["billing"] == "subscription" for p in points) else ""))
    # 为什么在某些轴上看不到
    times = [m["response_time_seconds"] for m in rows]
    if rows and not any(isinstance(t, (int, float)) for t in times):
        print("任务用时轴：看不到 —— 该模型所有映射配置都没有 response_time_seconds"
              "（榜单未发布响应时间/速度档）。")
    elif rows:
        print(f"任务用时轴：可见（response_time_seconds 覆盖 {sum(isinstance(t, (int, float)) for t in times)}/{len(times)} 条映射）。")
    blanks = [p["id"] for p in points if p.get("list_blended_usd_per_mtok") is None]
    if points and blanks:
        print(f"每任务成本轴：{len(blanks)}/{len(points)} 个点无标价档 → 走 "
              f"MEDIAN_AA_TASK_MTOK × 真实单价 的常数回退（多 effort 档压到同一 x）。")
    elif points:
        print("每任务成本轴：全部点都有标价档，无常数回退。")


def build_doc(data: dict, joined: list[dict]) -> str:
    now = datetime.now(SGT).strftime("%Y-%m-%d %H:%M %Z")
    boards = boards_of(data)
    per = scored_boards(joined)
    models = models_of(data)
    scored_models = [m for m in models if per.get(m)]
    stats = board_stats(data, joined)
    truncated = len(models) > MATRIX_ROW_LIMIT
    shown = scored_models if truncated else models

    out: list[str] = []
    out.append("# 模型 × 榜 可见性矩阵 / Model-by-board visibility")
    out.append("")
    out.append("> 由 `python scripts/visibility_matrix.py --write-docs` 生成，请勿手改。")
    out.append(f"> 生成时间：{now}；数据：`web/public/data/site.json`"
               f"（generatedAt={data.get('generatedAt')}）。")
    out.append("> 口径：mappings 与 configurations 的 join 与 `web/src/loadData.ts` 的 "
               "`unpackData()` 完全一致；可见性判据与 `web/src/domain.ts` 一致。")
    out.append("")
    out.append("## 1. 榜覆盖统计")
    out.append("")
    out.append("| 榜 | 名称 | 快照 | 配置数 | 映射数 | 有分模型 | 最高分 |")
    out.append("| --- | --- | --- | ---: | ---: | ---: | --- |")
    for s in stats:
        best = (
            f"{fmt(s['best_score'])} · `{s['best_model']}`"
            if s["best_score"] is not None
            else "—"
        )
        out.append(
            f"| `{s['board']}` | {md_cell(s['name'])} | {s['snapshot']} | {s['configurations']} | "
            f"{s['mappings']} | {s['models']} | {best} |"
        )
    out.append("")
    out.append("## 2. 全模型 × 榜 可见性总表")
    out.append("")
    out.append("`✓` = 该榜有分数（该榜 pareto / 前沿 / 表格可见）；"
               f"`—` = {NO_SCORE_NOTE}。")
    if truncated:
        out.append("")
        out.append(f"> 截断规则：模型数超过 {MATRIX_ROW_LIMIT} 时只列「至少一个榜有分」的模型"
                   f"（当前 {len(models)} 个模型，其中 {len(scored_models)} 个有分），"
                   f"其余 {len(models) - len(scored_models)} 个在所有榜均无分。")
    out.append("")
    out.append("| 模型 | " + " | ".join(f"`{b}`" for b in boards) + " | 有分榜数 |")
    out.append("| --- | " + " | ".join(":-:" for _ in boards) + " | ---: |")
    for model in shown:
        cells = " | ".join("✓" if b in per.get(model, set()) else "—" for b in boards)
        out.append(f"| `{model}` | {cells} | {len(per.get(model, set()))} |")
    out.append("")
    out.append("## 3. 无分也能看到的位置")
    out.append("")
    out.append("- **真实单价轴**（默认 X）：`real_usd_per_mtok`，任何点都能画，不需要榜分数。")
    out.append("- **月额度视图**：需要 `monthly_yi`（订阅点），同样不需要榜分数。")
    out.append("- **完整数据表**：该榜无分的点仍会列出，只是分数列为空。")
    out.append("- 反面：**能力图 / 前沿图**（Y 轴是榜分数）与 **按榜排序** 只对「该榜有分」的模型有效；"
               "`任务用时` 轴需要映射配置的 `response_time_seconds`，`每任务成本` 轴需要 "
               "`median_cost_usd_per_task`（缺失时走 `MEDIAN_AA_TASK_MTOK × 真实单价` 常数回退）。")
    out.append("")
    out.append("## 4. 常见误判（2026-10-08 真实案例）")
    out.append("")
    haiku = "claude-haiku-5.5"
    hb = sorted(per.get(haiku, set()))
    hrows = [m for m in joined if m["model"] == haiku]
    hpts = [p for p in data["points"] if p["model"] == haiku]
    out.append(f"### 案例 1：「榜单没看到 {haiku}」")
    out.append("")
    out.append(
        f"`{haiku}` 只在 **{len(hb)}/{len(boards)}** 个榜有分："
        + "、".join(f"`{b}`" for b in hb)
        + f"；其余 {len(boards) - len(hb)} 个榜没有它的配置映射，"
        "所以切到那些榜时图里看不到它——不是数据缺失，也不是过滤问题。"
    )
    out.append("")
    out.append("### 案例 2：「任务用时轴没有 Haiku」")
    out.append("")
    out.append(
        f"`{haiku}` 的 {len(hrows)} 条映射配置全部没有 `response_time_seconds`"
        "（榜单未发布响应时间/速度档），因此「任务用时」轴上没有任何一行可画；"
        "切回「真实单价」轴即可看到（该轴不需要榜分数）。"
    )
    out.append("")
    out.append("## 5. 自查命令")
    out.append("")
    out.append("```sh")
    out.append(".venv/bin/python scripts/visibility_matrix.py --model claude-haiku-5.5")
    out.append(".venv/bin/python scripts/visibility_matrix.py            # 全量矩阵")
    out.append(".venv/bin/python scripts/visibility_matrix.py --write-docs  # 重新生成本文件")
    out.append("```")
    out.append("")
    out.append("不用脚本时，可直接查 join 后的配置：")
    out.append("")
    out.append("```sh")
    out.append(
        "python3 -c \"import json;d=json.load(open('web/public/data/site.json'));"
        "c={x['configuration_id']:x for x in d['configurations']};"
        "print(sorted({c[m['configuration_id']]['board'] for m in d['mappings'] "
        "if c[m['configuration_id']]['model']=='claude-haiku-5.5' and "
        "c[m['configuration_id']]['score'] is not None}))\""
    )
    out.append("```")
    out.append("")
    out.append(f"（当前快照：{len(models)} 个模型 / {len(boards)} 个榜 / "
               f"{len(joined)} 条映射；其中 {len(models) - len(scored_models)} 个模型在所有榜均无分。）")
    out.append("")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="只查一个模型（项目模型 id，如 claude-haiku-5.5）")
    parser.add_argument("--list-models", action="store_true", help="列出全部模型 id 与有分榜数")
    parser.add_argument("--write-docs", action="store_true", help=f"生成 {DOC.relative_to(ROOT)}")
    args = parser.parse_args()

    data, joined = load()

    if args.list_models:
        per = scored_boards(joined)
        for model in models_of(data):
            print(f"{model:<34}{len(per.get(model, set()))} boards")
        return
    if args.model:
        query_model(data, joined, args.model)
        return
    print(f"site.json: {SITE.relative_to(ROOT)} | generatedAt={data.get('generatedAt')} | "
          f"{len(data['points'])} points / {len(data['configurations'])} configurations / "
          f"{len(joined)} mappings")
    print_board_stats(data, joined)
    print_matrix(data, joined)
    if args.write_docs:
        DOC.parent.mkdir(parents=True, exist_ok=True)
        DOC.write_text(build_doc(data, joined), encoding="utf-8")
        text = DOC.read_text(encoding="utf-8")
        print()
        print(f"wrote {DOC.relative_to(ROOT)}: {len(text.splitlines())} lines, "
              f"{len(text.encode('utf-8'))} bytes")


if __name__ == "__main__":
    main()
