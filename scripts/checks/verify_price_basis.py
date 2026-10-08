"""价格基准守卫：前端每任务成本的分母必须等于 AA 计算 cost 时用的那个价。

背景（2026-10-08 线上事故）：前端公式 `x = cost × real ÷ list`（web/src/domain.ts:212-217），
其中 `cost ÷ list` 是「该 AA 任务消耗的 token 数」，`list` 必须是 AA 价
（`benchmark_list_price` = AA 行三段价按项目标准负载算出的 blended）。task-20 的裁决曾把
分母换成项目档价 → MiMo V2.5 Pro 成本被低估 12.5× 并挤进成本前沿；task-28 的点级回退则被
configuration 兜住、线上零效果。此前没有任何守卫检查「分母是不是 AA 价」。

断言：
1. **价格基准一致性**：`derived/benchmark-configurations.json` 里每个 `benchmark_list_price`
   非空的配置，必须等于由该配置 AA 行（`raw_record.secondary` 的
   `price1mInputTokens` / `price1mOutputTokens` / `cacheHitPrice`）按标准负载重算的 blended
   （相对容差 1e-6）；AA 行匹配不到则计 SKIP 并打印，绝不静默通过。
2. **分母链一致性**：对 `web/public/data/site.json` 复现前端三级链
   `mapping.benchmark_list_price ?? configuration.benchmark_list_price ?? point.list_blended`，
   断言每个 (点, 榜) 解析出的分母等于该配置的 AA 价（用断言 1 的重算值作期望）。
   这样既能抓「mapping 被点级值覆盖」，也能抓「configuration 的 AA 价被置空后掉到项目档价」。
3. **可疑档价报告（INFO，不失败）**：AA 价 ÷ 项目档价 超出 [0.5, 2.0] 的配置列出并指向
   `derived/list-price-divergence.json`——那说明**项目档价可疑**，走人工核实，不是数据错误。

只读：不修改任何数据。失败时打印实际值 vs 期望值，并以非 0 退出。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from compute import CONVENTIONS, load_list_prices  # noqa: E402

MIX = CONVENTIONS["standardTokenMix"]
MIX_CACHE, MIX_INPUT, MIX_OUTPUT = MIX["cache"], MIX["input"], MIX["output"]
EXPECTED_MIX = (0.97, 0.025, 0.005)
RELATIVE_TOLERANCE = 1e-6
DIVERGENCE_MIN, DIVERGENCE_MAX = 0.5, 2.0

failures = []


def check(ok, label, detail=""):
    if ok:
        return True
    failures.append(f"{label}: {detail}")
    print(f"FAIL {label}: {detail}")
    return False


def blended(cached, input_, output):
    return round(MIX_CACHE * cached + MIX_INPUT * input_ + MIX_OUTPUT * output, 6)


def aa_tier(secondary: dict):
    """AA 行三段价 → (cached, input, output)，缺输入/输出则返回 None。"""
    input_ = secondary.get("price1mInputTokens")
    output = secondary.get("price1mOutputTokens")
    if input_ is None or output is None:
        return None
    cached = secondary.get("cacheHitPrice")
    return (input_ * 0.1 if cached is None else cached), input_, output


configs = json.loads((ROOT / "derived/benchmark-configurations.json").read_text(encoding="utf-8"))
check((MIX_CACHE, MIX_INPUT, MIX_OUTPUT) == EXPECTED_MIX,
      "conventions.standardTokenMix == (0.97, 0.025, 0.005)",
      f"actual {(MIX_CACHE, MIX_INPUT, MIX_OUTPUT)}")

# --- 断言 1：benchmark_list_price == 由 AA 三段价重算的 blended -------------------------
priced = [c for c in configs if c.get("benchmark_list_price") is not None]
skipped, mismatched = [], []
expected_by_config = {}
for c in priced:
    tiers = aa_tier((c.get("raw_record") or {}).get("secondary") or {})
    if tiers is None:
        skipped.append((c["configuration_id"], c.get("model"), c.get("variant")))
        continue
    expected = blended(*tiers)
    expected_by_config[c["configuration_id"]] = expected
    actual = c["benchmark_list_price"]
    if abs(actual - expected) > RELATIVE_TOLERANCE * abs(expected):
        mismatched.append((c.get("model"), c.get("variant"), actual, expected,
                           (c.get("raw_record") or {}).get("secondary", {}).get("slug")))

check(not mismatched,
      f"benchmark_list_price == blended(AA tiers) for all {len(priced)} priced configurations",
      f"{len(mismatched)} mismatched, e.g. {mismatched[:3]}")
if skipped:
    print(f"SKIP {len(skipped)} priced configurations without AA tiers in raw_record "
          f"(cannot recompute), e.g. {skipped[:3]}")

# --- 断言 2：前端三级链解析出的分母 == 该配置的 AA 价 -----------------------------------
site_path = ROOT / "web/public/data/site.json"
if not site_path.exists():
    print(f"SKIP {site_path.relative_to(ROOT)} missing; run the site data build before this check")
else:
    site = json.loads(site_path.read_text(encoding="utf-8"))
    cfg_by_id = {c["configuration_id"]: c for c in site["configurations"]}
    point_by_id = {p["id"]: p for p in site["points"]}
    bad_resolved, no_expectation, covered = [], 0, 0
    for mapping in site["mappings"]:
        cfg = cfg_by_id.get(mapping.get("configuration_id"))
        point = point_by_id.get(mapping.get("point_id"))
        if cfg is None or point is None:
            continue
        expected = expected_by_config.get(cfg["configuration_id"])
        if expected is None:
            # 没有 AA 行可重算的配置无法独立验证（拿它自己的值自比等于不检查），计入 SKIP。
            no_expectation += 1
            continue
        covered += 1
        mapping_price = mapping.get("benchmark_list_price")
        cfg_price = cfg.get("benchmark_list_price")
        list_blended = point.get("list_blended_usd_per_mtok")
        resolved = mapping_price if mapping_price is not None else (
            cfg_price if cfg_price is not None else list_blended)
        if not isinstance(resolved, (int, float)) or abs(resolved - expected) > RELATIVE_TOLERANCE * abs(expected):
            bad_resolved.append((mapping.get("point_id"), mapping.get("board"),
                                 resolved, expected, mapping_price, cfg_price, list_blended))
    check(not bad_resolved,
          f"front-end ?? chain resolves to the AA price on all {covered} mappings",
          f"{len(bad_resolved)} wrong denominators, e.g. {bad_resolved[:3]}")
    if no_expectation:
        print(f"SKIP {no_expectation} mappings without any AA price to compare against")

# --- 3：可疑档价报告（INFO，口径与 compute.audit_price_divergence 一致：比 curated 档） ----
curated = load_list_prices()
suspect = {}
for c in priced:
    base = (curated.get(c.get("model")) or {}).get("blended_usd")
    if not base:
        continue
    ratio = c["benchmark_list_price"] / base
    if not (DIVERGENCE_MIN <= ratio <= DIVERGENCE_MAX):
        suspect.setdefault(c.get("model"), set()).add(round(ratio, 4))

if failures:
    print(f"FAILED: {len(failures)} assertion(s)")
    sys.exit(1)

if suspect:
    print(f"INFO {len(suspect)} model(s) have an AA price more than "
          f"[{DIVERGENCE_MIN}, {DIVERGENCE_MAX}]x their curated tier - curated tier is the "
          f"suspect one, verify by hand: see derived/list-price-divergence.json")
    for model, ratios in sorted(suspect.items()):
        print(f"     {model}: ratio(s) {sorted(ratios)}")
print(f"PASS: {len(priced)} priced configurations recompute from their AA tiers, "
      f"{covered if site_path.exists() else 0} front-end denominators resolve to the AA price, "
      f"{len(suspect)} curated-tier suspects reported")
