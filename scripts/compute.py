# -*- coding: utf-8 -*-
"""adopted.csv × 榜单分数 × 官方标价 → derived/points.csv + points.json。

每个点 = (套餐, 实际服务模型)。x = 真实单价 $/MTok；y = 该模型在各榜单的分数（同模型多个 effort 变体取最高分）。
d = 真实单价 / 标价混合单价（标价按项目统一标准负载折算），只作注释，不进图。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from benchmark_configs import configuration, candidates, routed_board, score_fields, alias_for
from build_adopted import OPENCODE_GO_MODELS, COMMAND_CODE_GOAT_MODELS

ROOT = Path(__file__).resolve().parent.parent
DATA, RESEARCH, OUT = ROOT / "data", ROOT / "data" / "research", ROOT / "derived"
CONVENTIONS = json.loads((DATA / "conventions.json").read_text(encoding="utf-8"))
STANDARD_MIX = CONVENTIONS["standardTokenMix"]
# terminal_bench_4 只收 tbench.ai 官方 harness 行（含厂商自报附录行）；
# AA 自家 harness 的 TB4 运行拆到 aa_terminal_bench_4（benchmark_configs.configuration 路由）。
BOARDS = ("aa_intelligence_index", "terminal_bench_4", "aa_terminal_bench_4", "arena_code", "arena_agent_mode", "aa_coding_agent_index", "open_design_arena", "deepswe_1_1")
SCORE_FILES = (
    "scores-2026-09.json",
    "scores-code-arena-round1-2026-09-06.json",
    "scores-aa-coding-agent-round1-2026-09-06.json",
    "scores-aa-round3-2026-09-09.json",
    "scores-aa-intelligence-2026-09-12.json",
    "scores-aa-round4-2026-09-22.json",
    "scores-open-design-round1-2026-09-09.json",
    "scores-terminal-bench4-round1-2026-09-10.json",
    "scores-terminal-bench4-round2-selfreport-2026-09-12.json",
    "scores-deepswe-1.1-2026-09-12.json",
    "scores-deepswe-selfreport-2026-09-12.json",
    "scores-stepfun-step5-round1-2026-09-21.json",
    "scores-grok47-round1-2026-09-22.json",
    "scores-mimo-v26-grok47-round1-2026-09-22.json",
    "scores-deepswe-mimo-v26-grok47-selfreport-2026-09-22.json",
    "scores-opus55-selfreport-2026-09-23.json",
    "scores-new-models-round1-2026-09-23.json",
    "scores-gpt6sol-round1-2026-09-24.json",
    "scores-gpt6luna-round1-2026-09-26.json",
    "scores-aa-round5-2026-10-01.json",
    "scores-aa-round6-2026-10-08.json",
    "scores-aa-round6-2026-10-09.json",
    "scores-aa-round6-2026-10-10.json",
    "scores-aa-round6-2026-10-11.json",
    "scores-aa-round6-2026-10-12.json",
    "scores-aa-round6-2026-10-13.json",
    "scores-aa-round6-2026-10-14.json",
    "scores-aa-round6-2026-10-15.json",
    "scores-aa-round6-2026-10-20.json",
    "scores-aa-round6-2026-10-21.json",
    "scores-aa-round6-2026-10-22.json",
    "scores-aa-round6-2026-10-23.json",
    "scores-aa-round6-2026-10-25.json",
    "scores-aa-round6-2026-10-26.json",
    "scores-terminal-bench4-round3-2026-10-01.json",
    "scores-terminal-bench4-selfreport-carry-2026-10-01.json",
    "scores-code-arena-round2-2026-10-01.json",
    "scores-agent-arena-round2-2026-10-01.json",
    "scores-open-design-round2-2026-10-01.json",
    "scores-gemini-4-argon-round1-2026-10-01.json",
    "scores-hyperqwen-local-2026-10-02.json",
    "scores-deepseek-v41-flash-round1-2026-10-19.json",
    "scores-minimax-m31-round1-2026-10-20.json",
)
LIST_PRICE_FILES = (
    "list-prices-2026-09.json",
    "list-prices-deepseek-v41-round1-2026-09-09.json",
    "list-prices-deepseek-v41-round2-2026-09-10.json",
    "list-prices-stepfun-round1-2026-09-10.json",
    "list-prices-stepfun-round2-2026-09-21.json",
    "list-prices-mimo-v26-grok47-round1-2026-09-22.json",
    "list-prices-claude-sonnet55-round1-2026-09-30.json",
    "list-prices-claude-sonnet55-round2-2026-10-09.json",
    "list-prices-claude-haiku55-round1-2026-10-08.json",
    "list-prices-gemini-4-argon-round1-2026-10-01.json",
    "list-prices-subscription-gaps-round1-2026-10-08.json",
    "list-prices-minimax-m31-round1-2026-10-19.json",
)


def score_archives():
    return [json.loads((RESEARCH / name).read_text(encoding="utf-8"))
            for name in SCORE_FILES if (RESEARCH / name).exists()]

DISPLAY = {
    "minimax-m3.1-flash-preview": "MiniMax M3.1 Flash Preview",
    "gpt-5.6-sol": "GPT 5.6 Sol", "gpt-5.6-terra": "GPT 5.6 Terra", "gpt-5.6-luna": "GPT 5.6 Luna", "gpt-5.5": "GPT 5.5", "gpt-6-astra": "GPT-6 Astra", "gpt-6-sol": "GPT-6 Sol", "gpt-6-luna": "GPT-6 Luna", "gpt-6.1-sol": "GPT-6.1 Sol",
    "claude-opus-5": "Claude Opus 5", "claude-opus-5.5": "Claude Opus 5.5", "claude-fable-5": "Claude Fable 5", "claude-fable-5.1": "Claude Fable 5.1", "claude-sonnet-5": "Claude Sonnet 5", "claude-opus-4.8": "Claude Opus 4.8", "claude-sonnet-5.5": "Claude Sonnet 5.5", "claude-haiku-5.5": "Claude Haiku 5.5",
    "grok-4.6": "Grok 4.6", "grok-4.7": "Grok 4.7", "grok-4.5": "Grok 4.5", "kimi-k3": "Kimi K3", "kimi-k2.7-code": "Kimi K2.7 Code", "kimi-k2.6": "Kimi K2.6", "kimi-k2.5": "Kimi K2.5",
    "glm-5.3": "GLM 5.3", "glm-5.3-flash": "GLM 5.3 Flash", "glm-5.3-flashx": "GLM 5.3 FlashX", "glm-5.2": "GLM 5.2", "glm-5.1": "GLM 5.1", "glm-5": "GLM 5",
    "minimax-m3": "MiniMax M3", "minimax-m2.7": "MiniMax M2.7", "minimax-m2.5": "MiniMax M2.5",
    "qwen3.8-max": "Qwen3.8 Max", "qwen3.8-flash": "Qwen3.8 Flash", "qwen3.8-omni-flash": "Qwen3.8 Omni Flash", "qwen3.7-max": "Qwen3.7 Max",
    "qwen3.7-plus": "Qwen3.7 Plus", "qwen3.7-flash": "Qwen3.7 Flash", "qwen3.6-plus": "Qwen3.6 Plus", "qwen3.6-max-preview": "Qwen3.6 Max Preview",
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash", "deepseek-v4.1-flash-fast": "DeepSeek V4.1 Flash Fast", "deepseek-v4-flash": "DeepSeek V4 Flash", "deepseek-v4-flash-fast": "DeepSeek V4 Flash Fast", "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash-vision-exp": "DeepSeek V4 Flash Vision Exp",
    "gemini-3.1-pro": "Gemini 3.1 Pro", "gemini-3.6-flash": "Gemini 3.6 Flash", "gemini-3.7-flash": "Gemini 3.7 Flash", "gemini-3.8-flash": "Gemini 3.8 Flash", "gemini-4-argon": "Gemini 4 Argon",
    "mimo-v2.5": "MiMo V2.5", "mimo-v2.5-pro": "MiMo V2.5 Pro", "mimo-v2.6-pro": "MiMo V2.6 Pro", "mimo-v2.6-flash": "MiMo V2.6 Flash",
    "mimo-v2.6-pro-ultraspeed": "MiMo V2.6 Pro UltraSpeed", "longcat-2.0": "LongCat 2.0",
    "muse-spark-1.3": "Muse Spark 1.3", "muse-spark-1.3-contributor": "Muse Spark 1.3 Contributor",
    "muse-spark-1.2": "Muse Spark 1.2", "muse-spark-1.2-contributor": "Muse Spark 1.2 Contributor",
    "glm-5.2-fast": "GLM 5.2 Fast", "inkling": "Inkling", "inkling-small": "Inkling Small", "mistral-medium-3.5": "Mistral Medium 3.5",
    "kimi-k2.7-code-highspeed": "Kimi K2.7 Code HighSpeed", "nemotron-3-ultra": "Nemotron 3 Ultra",
    "qwen3.8-27b": "Qwen3.8 27B", "qwen3.8-max-0902": "Qwen3.8 Max 0902",
    "step-3.5-flash": "Step 3.5 Flash", "step-3.7-flash": "Step 3.7 Flash", "step-5-preview": "Step 5 Preview", "jev": "Jev",
    "hy3": "Hy3", "hy4-preview": "Hy4 Preview", "omen-alpha": "Omen Alpha", "composer-2.5": "Composer 2.5",
    "swe-2": "SWE-2",
}
VENDOR = {
    "gpt": "OpenAI", "claude": "Anthropic", "grok": "SpaceXAI", "kimi": "Kimi", "glm": "Zhipu", "minimax": "MiniMax",
    "qwen": "Alibaba", "deepseek": "DeepSeek", "gemini": "Google", "mimo": "Xiaomi", "hy": "Tencent", "composer": "Cursor",
    "longcat": "Meituan", "muse": "Muse", "omen": "OpenCode", "step": "StepFun", "swe": "Cognition",
    "mistral": "Mistral",
}


def vendor_of(model: str) -> str:
    return next((v for k, v in VENDOR.items() if model.startswith(k)), "other")


def load_scores() -> list[dict]:
    """Keep all configurations in each board's selected snapshot, never mix versions.

    注意：不要在这里展开 *-contributor 别名。别名映射由
    benchmark_configs.alias_for() + candidates() 在「点 → 配置」查找时完成，
    在这里复制 record 会制造幽灵配置、顶翻 verify_benchmark_configs 的
    `len(configs) == len(expected)` 守卫（2026-10-22 实测多出 10 条）。
    """
    archives = [(name, json.loads((RESEARCH / name).read_text(encoding="utf-8")))
                for name in SCORE_FILES]
    return [configuration(record, name) for name, record in current_score_records(archives)]


def current_score_records(archives):
    # Files are explicitly ordered oldest to newest. A complete new board snapshot
    # replaces that board as a whole, including models removed from its coverage.
    # Archives flagged "supplement" only append rows (e.g. vendor self-reports) to the
    # current snapshot and never replace it. A supplement with baseSnapshot applies only
    # while that snapshot is current; one without applies to whichever snapshot was current
    # at its position, so a later full snapshot of the (routed) board supersedes it.
    latest, position = {}, {}
    for index, (name, archive) in enumerate(archives):
        if not archive.get("supplement"):
            for b in archive["boards"]:
                if b["boardId"] in BOARDS:
                    latest[b["boardId"]], position[b["boardId"]] = name, index

    def current(index, name, archive, record):
        if not archive.get("supplement"):
            return latest.get(record["boardId"]) == name
        if "baseSnapshot" in archive:
            return archive["baseSnapshot"] == latest.get(record["boardId"])
        return position.get(routed_board(record), -1) < index

    return [(name, record) for index, (name, archive) in enumerate(archives)
            for record in archive["scores"] if current(index, name, archive, record)]


def load_list_prices(scores: list[dict] | None = None) -> dict[str, dict]:
    out = {}
    for name in LIST_PRICE_FILES:
        for m in json.loads((RESEARCH / name).read_text(encoding="utf-8"))["models"]:
            cached = m["cachedInput"] if m["cachedInput"] is not None else m["input"] * 0.1
            rate = CONVENTIONS["usdPerCny"] if m["currency"] == "CNY" else 1
            # 档内 tokenMix 覆盖：仅按量价已建档但实测/指定负载偏离标准档的模型使用
            # （如 minimax-m3.1-flash-preview 按指示固定 96% 缓存）；缺省走 STANDARD_MIX。
            mix = m.get("tokenMix") or STANDARD_MIX
            out[m["model"]] = dict(
                cached=cached, input=m["input"], output=m["output"], currency=m["currency"],
                blended_usd=(mix["cache"] * cached + mix["input"] * m["input"] + mix["output"] * m["output"]) / rate,
            )
    if scores:
        for s in scores:
            # curated AA 行的键是 boardId（个别历史 supplement 行写 board）；两者兼容。
            # 兜底只在模型尚无 list-prices 档时生效（m not in out）：建档优先，兜底补缺。
            if (s.get("boardId") or s.get("board")) == "aa_intelligence_index":
                m = s["model"]
                raw = s.get("raw_record", {})
                sec = raw.get("secondary", {})
                inp = sec.get("price1mInputTokens") or raw.get("price1mInputTokens")
                out_p = sec.get("price1mOutputTokens") or raw.get("price1mOutputTokens")
                cache = sec.get("cacheHitPrice") or raw.get("cacheHitPrice")
                if inp is not None and out_p is not None and m not in out:
                    cached = cache if cache is not None else inp * 0.1
                    blended = STANDARD_MIX["cache"] * cached + STANDARD_MIX["input"] * inp + STANDARD_MIX["output"] * out_p
                    out[m] = dict(cached=cached, input=inp, output=out_p, currency="USD", blended_usd=blended)

    for m, usage, c_read, inp, out_p, *rest in OPENCODE_GO_MODELS:
        if m not in out:
            blended = STANDARD_MIX["cache"] * c_read + STANDARD_MIX["input"] * inp + STANDARD_MIX["output"] * out_p
            out[m] = dict(cached=c_read, input=inp, output=out_p, currency="USD", blended_usd=blended)

    for m, allowance, c_read, inp, out_p, *rest in COMMAND_CODE_GOAT_MODELS:
        if m not in out:
            blended = STANDARD_MIX["cache"] * c_read + STANDARD_MIX["input"] * inp + STANDARD_MIX["output"] * out_p
            out[m] = dict(cached=c_read, input=inp, output=out_p, currency="USD", blended_usd=blended)

    for alias, target in [("muse-spark-1.3-contributor", "muse-spark-1.3"), ("muse-spark-1.2-contributor", "muse-spark-1.2")]:
        if target in out and alias not in out:
            out[alias] = out[target]

    return out


# 配置级 AA 标价 vs 项目档价的**分歧报告**阈值（2026-10-08 修正为 informational）：
# 比值在 [0.5, 2.0] 内视为一致（consistent）；超出只标 data_quality_suspect，提示人工
# 核实项目档价，**绝不改分母**——换分母会篡改 token 数（见 audit_price_divergence）。
# 实测分歧：muse-spark-1.3/1.2-contributor 36.7x、hy3 3.4x、mimo-v2.5-pro 0.08x、
# gemini-3.6-flash 1.66x（后者落在区间内）。
PRICE_DIVERGENCE_MIN = 0.5
PRICE_DIVERGENCE_MAX = 2.0


def audit_price_divergence(scores: list[dict], curated: dict[str, dict]) -> list[dict]:
    """配置级 AA 标价 vs 项目档价的**分歧报告**（informational，不改任何数据）。

    2026-10-08 裁定修正（推翻 task-20 的「超阈值回退」）：前端的每任务成本是
    `cost × real ÷ list`（web/src/domain.ts:212-217），其中 `cost ÷ list` 是「该 AA
    任务消耗的 token 数」——AA 的 cost 是用它自己那行的三段价算出来的，所以 `list`
    必须是 `benchmark_list_price`（AA 价）。把分母换成项目档价等于篡改 token 数：
    MiMo V2.5 Pro 实测被低估 12.5×（0.0361 → 0.00289），并因此挤进成本前沿。

    因此本函数只做报告：比值超 [PRICE_DIVERGENCE_MIN, PRICE_DIVERGENCE_MAX] 的配置标
    `data_quality_suspect`——那说明**项目档价可能是错的**（来源/版本/时效问题，例如
    mimo-v2.5-pro 的 0.234 反推约 $5.4/$10.9 每 M，像是别的变体或陈旧价），走人工核实，
    绝不改分母。没有项目档价的标 `no_curated_baseline`（保留 AA 价）。
    """
    audit = []
    for s in scores:
        price = s.get("benchmark_list_price")
        if price is None:
            continue
        model = s.get("model")
        baseline = curated.get(model)
        base = baseline["blended_usd"] if baseline else None
        entry = dict(
            configuration_id=s["configuration_id"], board=s["board"], model=model,
            variant=s["variant"], benchmark_list_price=price,
            curated_list_blended=round(base, 6) if base else None,
            ratio=None, status="consistent",
        )
        if not base or base <= 0:
            entry["status"] = "no_curated_baseline"
        else:
            ratio = price / base
            entry["ratio"] = round(ratio, 4)
            if not (PRICE_DIVERGENCE_MIN <= ratio <= PRICE_DIVERGENCE_MAX):
                entry["status"] = "data_quality_suspect"
        audit.append(entry)
    return audit


def main() -> None:
    scores = load_scores()
    list_prices = load_list_prices(scores)
    # 项目维护的档价（不含 AA 兜底），只用于配置级 AA 价的分歧判定。
    curated_prices = load_list_prices()
    price_audit = audit_price_divergence(scores, curated_prices)
    boards_meta = {b["boardId"]: b for archive in score_archives() if not archive.get("supplement") for b in archive["boards"]}
    # supplement 档案可以声明新榜（如 aa_terminal_bench_4）的元数据；已存在榜仍以快照档为准。
    for archive in score_archives():
        if archive.get("supplement"):
            for b in archive["boards"]:
                boards_meta.setdefault(b["boardId"], b)

    points, configuration_points = [], []
    cross_model: list[dict] = []
    with (DATA / "adopted.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            model = r["served_model"]
            real = float(r["real_usd_per_mtok"])
            lp = list_prices.get(model)
            lb = lp["blended_usd"] if lp else None
            plan_en = r.get("plan_name_en") or None
            gen = r.get("plan_gen") or ""
            gen_tag = f" ({gen})" if gen else ""
            p = dict(
                id=f"{r['plan_id']}::{model}", plan=r["plan_name"], plan_en=plan_en, billing=r["billing"], model=model,
                model_display=DISPLAY.get(model, model) + gen_tag, plan_gen=gen, vendor=vendor_of(model),
                local_price=f"¥{r['price']}" if plan_en and r["currency"] == "CNY" else None,
                label=r["plan_name"] if r["billing"] == "metered" else f"{DISPLAY.get(model, model)}{gen_tag} · {r['plan_name']}",
                workload=r.get("workload") or "",
                list_price={k: lp[k] for k in ("cached", "input", "output", "currency")} if lp else None,
                price_usd=float(r["price_usd"]) if r["price_usd"] else None,
                monthly_yi=float(r["monthly_yi"]) if r["monthly_yi"] else None,
                real_usd_per_mtok=real, list_blended_usd_per_mtok=round(lb, 4) if lb else None,
                d=round(real / lb, 4) if lb else None, confidence=r["confidence"], tier=r["chart_tier"], source=r["source"], note=r["decision_note"],
                unmetered=r.get("unmetered") == "true", promo_until=r.get("promo_until") or None,
                data_date=r.get("data_date") or None, data_date_kind=r.get("data_date_kind") or None,
                data_date_from=r.get("data_date_from") or None,
            )
            for b in BOARDS:
                # 渠道别名按榜启用（见 benchmark_configs.ALIAS_BOARDS）；未启用榜 alias 为 None。
                alias = alias_for(model, b)
                options = candidates(r, scores, b)
                # Explicit optional summary projection; full configuration rows are also published.
                selected = max(options, key=lambda s: s["score"], default=None)
                p[f"{b}__selection"] = "highest_archived_reference" if selected else None
                p[f"{b}__configuration_count"] = len(options)
                for field, value in score_fields(selected, model).items():
                    p[f"{b}__{field}"] = value
                # 跨模型价差报告（informational）：点的 model 与选中配置的 model 不同时，
                # 配置的 AA 价来自另一个模型的价目（如 muse-spark-1.3 的 $1.25/$4.25 vs
                # *-contributor 的 $0.10/$0.20）。**不改分母**——前端的 `cost ÷ list` 是
                # AA 任务的 token 数，换分母即篡改（见 audit_price_divergence 的修正说明）。
                # 比值超界只标 data_quality_suspect，提示人工核实两侧价目。
                if selected is not None and selected.get("model") != model:
                    bench = p.get(f"{b}__benchmark_list_price")
                    if isinstance(bench, (int, float)) and bench > 0 and lb and lb > 0:
                        ratio = bench / lb
                        cross_model.append(dict(
                            point=p["id"], board=b, point_model=model,
                            configuration_model=selected.get("model"),
                            benchmark_list_price=bench, point_list_blended=round(lb, 6),
                            ratio=round(ratio, 4),
                            status=("data_quality_suspect"
                                    if not (PRICE_DIVERGENCE_MIN <= ratio <= PRICE_DIVERGENCE_MAX)
                                    else "consistent"),
                        ))
                for option in options:
                    configuration_points.append(dict(point_id=p["id"], board=b, **score_fields(option, model)))
            points.append(p)

    OUT.mkdir(exist_ok=True)
    (OUT / "benchmark-configurations.json").write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "benchmark-points.json").write_text(json.dumps(configuration_points, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, records in (("benchmark-configurations", scores), ("benchmark-points", configuration_points)):
        with (OUT / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as f:
            fields = [k for k in records[0] if k != "raw_record"]
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
    with (OUT / "points.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(points[0].keys()))
        w.writeheader()
        w.writerows(points)
    (OUT / "points.json").write_text(json.dumps(dict(
        generatedAt=CONVENTIONS["updatedAt"], mix={k: round(v, 4) for k, v in STANDARD_MIX.items() if isinstance(v, (int, float))},
        boards={b: dict(name=boards_meta[b]["name"].replace("🏆 ", ""), metric=boards_meta[b]["metric"], url=boards_meta[b]["url"], snapshot=boards_meta[b]["snapshotDate"]) for b in BOARDS},
        points=points,
    ), ensure_ascii=False, indent=1), encoding="utf-8")

    reverted_ids = {a["configuration_id"] for a in price_audit if a["status"] == "reverted"}
    affected: dict[str, list[str]] = {}
    for p in points:
        for b in BOARDS:
            cid = p.get(f"{b}__configuration_id")
            if cid in reverted_ids:
                affected.setdefault(cid, []).append(f"{p['id']} [{b}]")
    for entry in price_audit:
        entry["affected_point_board_cells"] = len(affected.get(entry["configuration_id"], []))
    divergence_counts = {status: sum(1 for a in price_audit if a["status"] == status)
                         for status in ("consistent", "data_quality_suspect", "no_curated_baseline")}
    divergence_counts["cross_model_suspect"] = sum(
        1 for c in cross_model if c["status"] == "data_quality_suspect")
    (OUT / "list-price-divergence.json").write_text(json.dumps(dict(
        generatedAt=CONVENTIONS["updatedAt"],
        rule=dict(
            min=PRICE_DIVERGENCE_MIN, max=PRICE_DIVERGENCE_MAX,
            description=("informational（2026-10-08 修正）：前端每任务成本 = cost × real ÷ list，"
                         "cost ÷ list 是 AA 任务的 token 数，list 必须是 AA 价（benchmark_list_price），"
                         "换分母即篡改 token 数。比值在 [min, max] 内标 consistent；超界标 "
                         "data_quality_suspect（提示**项目档价可能错**，人工核实，不改数据）；"
                         "无项目档价标 no_curated_baseline（保留 AA 价）。"),
        ),
        counts=divergence_counts,
        suspect_models=sorted({a["model"] for a in price_audit if a["status"] == "data_quality_suspect"}),
        cross_model_cells=cross_model,
        configurations=price_audit,
    ), ensure_ascii=False, indent=1), encoding="utf-8")

    unscored = {b: sorted({p["label"] for p in points if p[f"{b}__score"] is None}) for b in BOARDS}
    print(f"{len(points)} points -> {OUT}")
    print(f"  price divergence (informational): {divergence_counts}, suspect models: "
          f"{sorted({a['model'] for a in price_audit if a['status'] == 'data_quality_suspect'})}")
    print(f"  cross-model cells: {len(cross_model)} checked, "
          f"{divergence_counts['cross_model_suspect']} flagged data_quality_suspect (report only)")
    for b in BOARDS:
        print(f"  {b}: {sum(p[f'{b}__score'] is not None for p in points)} scored, unscored: {unscored[b]}")


if __name__ == "__main__":
    main()
