"""Lossless benchmark records and explicit reference mappings; never infer quota effort."""
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "data" / "research"
_raw_cache = {}

def get_raw_record(archive_name, slug, name, model):
    target_archives = [archive_name]
    if archive_name != "scores-aa-round5-2026-09-30.json":
        target_archives.append("scores-aa-round5-2026-09-30.json")

    for arch in target_archives:
        if arch not in _raw_cache:
            path = RESEARCH / arch
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                raw = data.get("rawRecords", {}).get("aa_intelligence_index", [])
                _raw_cache[arch] = {
                    "by_slug": {r.get("slug"): r for r in raw if "slug" in r},
                    "by_name": {r.get("name"): r for r in raw if "name" in r},
                }
            else:
                _raw_cache[arch] = {"by_slug": {}, "by_name": {}}
        c = _raw_cache[arch]
        res = c["by_slug"].get(slug) or c["by_name"].get(name)
        if res:
            return res
        if slug:
            norm_slug = slug.replace("-preview", "").replace("-0420", "").replace("-0202", "")
            res = c["by_slug"].get(norm_slug)
            if res:
                return res
        if model:
            norm_m = model.replace("-preview", "").replace(".5", "-5").replace(".", "-")
            res = c["by_slug"].get(norm_m)
            if res:
                return res
    return None

TIME_FALLBACKS = {
    "gemini-3-8-flash-medium": 18.0,
    "gemini-3-8-flash-low": 12.0,
    "gpt-6-luna-medium": 12.6,
    "gpt-6-sol-medium": 18.2,
    "deepseek-v4-flash-0420-high": 12.55,
    "deepseek-v4-flash-0420": 12.55,
    "deepseek-v4-flash-0420-non-reasoning": 8.0,
    "longcat-2-0": 55.29,
}

# AA 自家 harness（agentHarness=Artificial Analysis）跑的 TB4 与 tbench.ai 官方榜分开计分：
# 同一套题、不同 agent 配置，两边分数不可互换（实测对拍中位差 ~2.6 分，Grok 4.7 差 11.8）。
AA_TB4_BOARD = "aa_terminal_bench_4"
AGENT_BOARDS = {"arena_code", "arena_agent_mode", "aa_coding_agent_index", "open_design_arena", "terminal_bench_4", AA_TB4_BOARD, "deepswe_1_1"}
OPEN_DESIGN_MODELS = {
    "GPT-6 Astra": "gpt-6-astra",
    "DeepSeek V4.1 Flash": "deepseek-v4.1-flash",
    "Claude Fable 5.1": "claude-fable-5.1",
    "GPT-5.6 Sol": "gpt-5.6-sol",
    "Hunyuan H4 Preview": "hy4-preview",
    "DeepSeek V4 Pro": "deepseek-v4-pro",
    "Grok 4.6": "grok-4.6",
    "Qwen 3.8-Max": "qwen3.8-max",
    "DeepSeek V4 Flash": "deepseek-v4-flash",
    "GLM-5.3 Flash": "glm-5.3-flash",
    "Gemini 3.8 Flash": "gemini-3.8-flash",
    "Muse Spark 1.3": "muse-spark-1.3",
    "Kimi K3": "kimi-k3",
}
# 渠道变体别名：OpenCode Go / GOAT 的 "Muse Spark *Contributor*" 行是同一模型的贡献者渠道命名
# （用户 2026-09-30 裁定：contributor 即贡献者渠道，模型相同），引用基础模型行的榜单分数。
# 用户 2026-09-30 拍板：全榜启用（con 就是本体，同分理直气壮；单榜限制等于帮小厂商藏分）。
SERVED_MODEL_ALIASES = {
    "muse-spark-1.3-contributor": "muse-spark-1.3",
    "muse-spark-1.2-contributor": "muse-spark-1.2",
}
ALIAS_BOARDS = {"aa_intelligence_index", "terminal_bench_4", "aa_terminal_bench_4", "arena_code", "arena_agent_mode", "aa_coding_agent_index", "open_design_arena", "deepswe_1_1"}


def alias_for(served_model, board):
    """该榜启用的渠道别名；未启用的榜按原名精确匹配。"""
    return SERVED_MODEL_ALIASES.get(served_model) if board in ALIAS_BOARDS else None


EFFORT = re.compile(r"(?<![a-z0-9])(xhigh|high|medium|low|max|none|thinking)(?![a-z0-9])", re.I)

# 我们自己的 RSC 抽取器（extract_all.py，只在 gitignore 的 _build/ 下，未入库）把一个
# Python dict repr 拼进了 round4 的三条 variantLabel；AA 站上的原文到 " (max)" 就结束。
# 归档证据只追加不改写，所以在解析层剥掉，并把 dict 里的参数取出来当结构化字段用——
# 裸剥会丢掉 effort（Opencode - GLM-5.3 的 max 只存在于这段 dict 里）。
PARAMS = re.compile(r"\s*\((\{.*\})\)\s*$")


def normalise_label(label: str) -> tuple[str, dict]:
    match = PARAMS.search(label)
    if not match:
        return label, {}
    try:
        params = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return label, {}
    return (label[:match.start()], params) if isinstance(params, dict) else (label, {})


def configuration(record, archive):
    secondary = record.get("secondary", {})
    label, params = normalise_label(record["variantLabel"])
    board = (AA_TB4_BOARD if record["boardId"] == "terminal_bench_4"
             and secondary.get("agentHarness") == "Artificial Analysis" else record["boardId"])
    estimated = secondary.get("intelligenceIndexIsEstimated", record.get("scoreIsEstimated"))
    self_reported = bool(secondary.get("selfReported"))
    model = record.get("model") or (OPEN_DESIGN_MODELS.get(label) if board.startswith("open_design_arena") else None)
    identity = [board, model, label, record.get("checkedAt"), archive]
    cid = board + ":" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]
    effort = EFFORT.search(label)
    declared = params.get("reasoning_effort")
    harness = secondary.get("agentHarness")
    if harness is None and "codex-harness" in label.lower():
        harness = "Codex"
    if harness is None and record["boardId"] == "open_design_arena":
        harness = "OpenDesign"
    minus, plus = secondary.get("ciMinus"), secondary.get("ciPlus")
    inp = secondary.get("price1mInputTokens") if secondary.get("price1mInputTokens") is not None else record.get("price1mInputTokens")
    out_p = secondary.get("price1mOutputTokens") if secondary.get("price1mOutputTokens") is not None else record.get("price1mOutputTokens")
    cache = secondary.get("cacheHitPrice") if secondary.get("cacheHitPrice") is not None else record.get("cacheHitPrice")
    if inp is not None and out_p is not None:
        cached = cache if cache is not None else inp * 0.1
        benchmark_list_price = round(0.97 * cached + 0.025 * inp + 0.005 * out_p, 6)
    else:
        benchmark_list_price = None
    raw_item = get_raw_record(archive, secondary.get("slug"), record.get("variantLabel"), model)
    resp_time = None
    tps = None
    if raw_item:
        rt = raw_item.get("medianEndToEndResponseTimeSeconds")
        if isinstance(rt, (int, float)):
            resp_time = round(float(rt), 2)
        sp = raw_item.get("medianOutputTokensPerSecond")
        if isinstance(sp, (int, float)):
            tps = round(float(sp), 1)
    if resp_time is None and secondary.get("slug") in TIME_FALLBACKS:
        resp_time = TIME_FALLBACKS[secondary["slug"]]

    return dict(
        configuration_id=cid, board=board, model=model,
        variant=label + (" [AA estimate]" if estimated else "") + (" [vendor self-report]" if self_reported else ""),
        score_is_estimated=estimated, score_is_self_reported=self_reported,
        agent_harness=harness, reasoning_effort=str(declared).lower() if declared else (effort.group(1).lower() if effort else None),
        service_mode={"cursor cli - composer 2.5 fast": "fast", "cursor cli - composer 2.5": "standard"}.get(label.lower())
                     if record["model"] == "composer-2.5" else None,
        score=record["score"], score_low=record["score"] - minus if minus is not None else None,
        score_high=record["score"] + plus if plus is not None else None,
        # AA intelligence rows store the task cost as costPerTaskUsd; coding-agent rows use meanCostUsdPerTask.
        mean_cost_usd_per_task=secondary.get(
            "meanCostUsdPerTask", secondary.get("cost", secondary.get("costPerTaskUsd"))
        ),
        median_cost_usd_per_task=secondary.get("medianCostPerTaskUsd"),
        benchmark_list_price=benchmark_list_price,
        response_time_seconds=resp_time,
        output_speed_tps=tps,
        source=record.get("source"), checked_at=record.get("checkedAt"), archive=archive,
        raw_record=record,
    )


def candidates(row, configurations, board):
    served = alias_for(row["served_model"], board) or row["served_model"]
    records = [c for c in configurations if c["board"] == board and c["model"] == served]
    if row["served_model"] == "composer-2.5":
        mode = "fast" if row["plan_id"].endswith("_composer_fast") else "standard"
        records = [c for c in records if c["service_mode"] == mode]
    return records


def mapping(record, aliased_from=None):
    agent = record["board"] in AGENT_BOARDS
    note = ("Exact served-model reference only; product harness and quota-measurement effort are unverified. "
            "Not a benchmark measurement of this subscription or API channel." if agent else
            "Exact served-model reference; quota-measurement effort is unverified.")
    if aliased_from:
        note = (f"Served-model alias: {aliased_from} is the same model as {record['model']} "
                f"(user decision 2026-09-30); reference inherited. ") + note
    return dict(
        mapping_kind="agent_configuration_reference" if agent else "model_configuration_reference",
        mapping_confidence="low" if agent else "medium",
        mapping_note=note + (" Vendor self-reported score, not an official leaderboard run." if record.get("score_is_self_reported") else ""),
        quota_effort_matched=None,
    )


def score_fields(record, aliased_from=None):
    keys = ("configuration_id", "variant", "score", "score_is_estimated", "score_is_self_reported", "agent_harness", "reasoning_effort", "service_mode",
            "score_low", "score_high", "mean_cost_usd_per_task", "median_cost_usd_per_task", "benchmark_list_price",
            "response_time_seconds", "output_speed_tps", "source")
    fields = {k: record[k] if record else None for k in keys}
    fields.update(mapping(record, aliased_from) if record else {k: None for k in
                  ("mapping_kind", "mapping_confidence", "mapping_note", "quota_effort_matched")})
    return fields
