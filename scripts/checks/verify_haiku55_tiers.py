"""守卫 data/adopted.csv 里 Claude Haiku 5.5 分档行的内部一致性。

背景：新模型若不建 list-prices 档，其订阅点 `list_blended_usd_per_mtok` 为
None，前端每任务成本会退化成常数回退、多 effort 档被压到同一 x（GPT-6 Luna
假前沿事故）。Haiku 5.5 的 4 个情景点是同一事故族的另一半：它们的额度与单价
全部由 ≤100k / >100k 两档混合价按比例套算，一旦比例或换算漂移，图上会出现
一条自洽的假前沿。

断言（数据源：list-prices-claude-haiku55-*.json 的三段价 + conventions.json
的 standardTokenMix，保证与 compute.py 同一口径）：
1. 4 个情景 plan_id 存在且 served_model == claude-haiku-5.5；
2. ≤100k 档 == (cache 0.01, input 0.10, output 0.50)、>100k 档 == (0.05, 0.50, 2.50)；
   under=blended(0.01,0.10,0.50)、over=blended(0.05,0.50,2.50)、mix=(under+over)/2；
3. 主行(≤100k) : over : mix 的 monthly_yi 比例 == 1 : under/over : under/mix；
4. real_usd_per_mtok == price_usd / (monthly_yi * 100)，8 位有效数字半单位容差；
5. 4 个情景行的 workload == anthropic、chart_tier == main。

容差说明（当前口径，2026-10-08 数据）：情景行 monthly_yi 由 build_adopted.sub_row()
统一 round(...,3) 存储（163.711 / 272.852 / 1690.608 / 2817.681），半存储位为 5e-4。
第 3 条用「0.005 绝对 + 1e-6 相对」的混合容差：0.005 是半存储位的 10 倍余量，有意
保留以容忍存储精度约定的变化；1e-6 相对则保证比例误差不会被放大掩盖。第 4 条用
8 位有效数字最后一位的半单位（half-ULP），实测最紧的一行用满 98%
（claude_max_20x_over100k）。
只读：不修改任何数据。失败时打印实际值 vs 期望值，并以非 0 退出。
"""
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from compute import CONVENTIONS, LIST_PRICE_FILES

MODEL = "claude-haiku-5.5"
# 情景 plan_id -> (主行 plan_id, 比例种类)
SCENARIOS = {
    "claude_pro_over100k": ("claude_pro", "over"),
    "claude_pro_mix5050": ("claude_pro", "mix"),
    "claude_max_20x_over100k": ("claude_max_20x", "over"),
    "claude_max_20x_mix5050": ("claude_max_20x", "mix"),
}
EXPECTED_BASE_TIER = (0.01, 0.10, 0.50)  # cachedInput, input, output（≤100k 档）
EXPECTED_OVER_TIER = (0.05, 0.50, 2.50)  # >100k 档 = 5x
# 情景行 monthly_yi 以 3 位小数存储（半存储位 5e-4）；0.005 是它的 10 倍余量，理由见文件头。
YI_TOLERANCE = 0.005
RELATIVE_TOLERANCE = 1e-6
REAL_SIGNIFICANT_DIGITS = 8

failures = []


def check(ok, label, detail=""):
    if ok:
        return True
    failures.append(f"{label}: {detail}")
    print(f"FAIL {label}: {detail}")
    return False


def half_ulp(value, significant=REAL_SIGNIFICANT_DIGITS):
    """8 位有效数字下最后一位的半个单位。"""
    if not value:
        return 0.0
    return 0.5 * 10 ** (math.floor(math.log10(abs(value))) - significant + 1)


mix = CONVENTIONS["standardTokenMix"]


def blended(cached, input_, output):
    return mix["cache"] * cached + mix["input"] * input_ + mix["output"] * output

price_name = next((n for n in LIST_PRICE_FILES if "haiku55" in n), None)
if price_name is None:
    print(f"FAIL no {MODEL} file registered in compute.LIST_PRICE_FILES")
    sys.exit(1)
price_payload = json.loads((ROOT / "data" / "research" / price_name).read_text(encoding="utf-8"))
entry = next((m for m in price_payload["models"] if m["model"] == MODEL), None)
if entry is None:
    print(f"FAIL {price_name} has no model entry for {MODEL}")
    sys.exit(1)

cached = entry.get("cachedInput")
base_tier = (entry["input"] * 0.1 if cached is None else cached, entry["input"], entry["output"])
over_variant = (entry.get("variants") or {}).get("tierOver100k") or {}
over_tier = (over_variant.get("cachedInput"), over_variant.get("input"), over_variant.get("output"))

check(base_tier == EXPECTED_BASE_TIER, f"{price_name} <=100k tier == {EXPECTED_BASE_TIER}",
      f"actual {base_tier}")
check(over_tier == EXPECTED_OVER_TIER, f"{price_name} >100k tier == {EXPECTED_OVER_TIER}",
      f"actual {over_tier}")

under = blended(*base_tier)
over = blended(*over_tier)
mid = (under + over) / 2
if not check(over > under > 0, "tier prices are positive and >100k costs more than <=100k",
             f"under={under!r} over={over!r}"):
    print(f"FAILED: {len(failures)} assertion(s) before adopted.csv could be checked")
    sys.exit(1)

with (ROOT / "data" / "adopted.csv").open(encoding="utf-8-sig") as source:
    rows = list(csv.DictReader(source))
by_plan = {r["plan_id"]: r for r in rows}

for plan_id, (base_plan, kind) in SCENARIOS.items():
    row = by_plan.get(plan_id)
    base_row = by_plan.get(base_plan)
    if row is None:
        check(False, f"{plan_id} exists in adopted.csv", "missing row")
        continue
    if base_row is None:
        check(False, f"base row {base_plan} exists in adopted.csv", "missing row")
        continue
    check(row.get("served_model") == MODEL, f"{plan_id} served_model == {MODEL}",
          f"actual {row.get('served_model')!r}")
    check(base_row.get("served_model") == MODEL, f"{base_plan} base served_model == {MODEL}",
          f"actual {base_row.get('served_model')!r}")

    ratio = under / over if kind == "over" else under / mid
    expected_yi = float(base_row["monthly_yi"]) * ratio
    actual_yi = float(row["monthly_yi"])
    yi_tolerance = YI_TOLERANCE + RELATIVE_TOLERANCE * expected_yi
    check(abs(actual_yi - expected_yi) <= yi_tolerance,
          f"{plan_id} monthly_yi == {base_plan} x under/{kind} ({ratio:.9f})",
          f"actual {actual_yi!r} vs expected {expected_yi!r} "
          f"(delta {actual_yi - expected_yi:+.3e}, tolerance {yi_tolerance:.3e}; "
          f"a bare 1e-6 relative tolerance cannot hold at 2-decimal monthly_yi storage)")

    expected_real = float(row["price_usd"]) / (actual_yi * 100)
    actual_real = float(row["real_usd_per_mtok"])
    real_tolerance = half_ulp(expected_real)
    check(abs(actual_real - expected_real) <= real_tolerance,
          f"{plan_id} real_usd_per_mtok == price_usd / (monthly_yi * 100)",
          f"actual {actual_real!r} vs expected {expected_real!r} "
          f"(delta {actual_real - expected_real:+.3e}, {REAL_SIGNIFICANT_DIGITS}-digit half unit "
          f"{real_tolerance:.3e})")

    check(row.get("workload") == "anthropic", f"{plan_id} workload == anthropic",
          f"actual {row.get('workload')!r}")
    check(row.get("chart_tier") == "main", f"{plan_id} chart_tier == main",
          f"actual {row.get('chart_tier')!r}")

if failures:
    print(f"FAILED: {len(failures)} assertion(s) on {MODEL} tier rows")
    sys.exit(1)

print(f"PASS: 4 {MODEL} scenario rows scale from claude_pro/claude_max_20x by "
      f"under/over={under / over:.9f} and under/mix={under / mid:.9f}; "
      f"real_usd_per_mtok matches price_usd/(monthly_yi*100) to "
      f"{REAL_SIGNIFICANT_DIGITS} significant digits (source {price_name})")
