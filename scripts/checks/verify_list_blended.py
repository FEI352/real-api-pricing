"""守卫 derived/points.json：订阅行的 list_blended_usd_per_mtok 不得静默缺失。

背景（2026-10-08 GPT-6 Luna 假前沿）：`compute.load_list_prices()` 查不到模型三段价时，
`list_blended_usd_per_mtok` 落成 `None`；前端对 `None` 走
`MEDIAN_AA_TASK_MTOK(3.51759) × real_usd_per_mtok` 的常数回退
（`web/src/domain.ts`、`scripts/cli.py`），于是多个 effort 档被压到同一 x、前沿被画歪。
根因是「新模型没建 list-prices 档」（`docs/DAILY_SYNC_RUNBOOK.md` §2.1）。

断言：
1. `derived/points.json` 的每个订阅行（`billing == "subscription"` 且 `unmetered` 为假）的
   `list_blended_usd_per_mtok` 要么是正数，要么其 model 在本文件的显式白名单里；
2. 失败时按模型聚合打印缺失行（model + plan id），直接可定位；
3. 白名单模型若已拿到正数 `list_blended` → 打印 STALE-WHITELIST 提示（该条目应删除）；
4. 白名单规模 > 10 个模型 → 打印 WARN（说明「新模型漏建档」正在成为常态）。

白名单口径：这些模型没有独立的按量 API 三段价（订阅内计量 / 免费 / 预览），前端走
3.51759 常数回退是设计使然。**每条必须写明依据**；依据不足的模型不进白名单——
那正是本守卫要报的缺口。`pending` 类（AA 快照 rawRecords 里已有三段价、项目尚未建档）
会在通过时额外打 WARN，补档后应从白名单删除。

只读：不修改任何数据。失败时打印实际值 vs 期望值，并以非 0 退出。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POINTS = ROOT / "derived" / "points.json"
FALLBACK_MTOK = 3.51759  # web/src/domain.ts MEDIAN_AA_TASK_MTOK

# model -> (状态, 依据)。ruled = 项目已裁定不建按量档；pending = 待补档（AA raw 有价）。
# 2026-10-08 收紧：原 8 条中 6 条已拿到正数 list_blended（claude-fable-5.1 / gpt-6-sol /
# gpt-6.1-sol / gemini-3.6-flash / mistral-medium-3.5 由
# list-prices-subscription-gaps-round1-2026-10-08.json 补档，gpt-6-astra 由
# compute.load_list_prices() 的 AA 兜底补齐），swe-2 当前无订阅行——均已删除。
# 现在只剩真正无价可建的一条；将来再出现缺失，本守卫会直接 FAIL 点名。
WHITELIST = {
    "minimax-m3.1-flash-preview": ("ruled", "preview 档：AA 快照 rawRecords 无该模型价格行，官方也未公布按量价（gaps 档记 notFound）"),
}
WHITELIST_WARN_SIZE = 10

failures = []


def check(ok, label, detail=""):
    if ok:
        return True
    failures.append(f"{label}: {detail}")
    print(f"FAIL {label}: {detail}")
    return False


if not POINTS.exists():
    print(f"FAIL {POINTS.relative_to(ROOT)} missing - run scripts/compute.py first")
    sys.exit(1)

points = json.loads(POINTS.read_text(encoding="utf-8"))["points"]
subs = [p for p in points if p.get("billing") == "subscription" and not p.get("unmetered")]
check(bool(subs), "points.json declares subscription rows", f"actual {len(subs)} of {len(points)} points")

missing = [p for p in subs
           if not (isinstance(p.get("list_blended_usd_per_mtok"), (int, float))
                   and p["list_blended_usd_per_mtok"] > 0)]
by_model = {}
for p in missing:
    by_model.setdefault(p.get("model"), []).append(p.get("id") or p.get("plan"))

offenders = {m: ids for m, ids in by_model.items() if m not in WHITELIST}
detail = "; ".join(f"{m} ({len(ids)} rows: {', '.join(str(i) for i in ids[:3])})"
                   for m, ids in sorted(offenders.items()))
check(not offenders,
      "every subscription row carries a positive list_blended or a whitelisted model",
      f"{len(missing)} of {len(subs)} rows fall back to {FALLBACK_MTOK} x real_usd_per_mtok "
      f"and are NOT whitelisted: {detail}")

stale = sorted(m for m in WHITELIST if m in {p.get("model") for p in subs}
               and m not in by_model)
unused = sorted(m for m in WHITELIST if m not in {p.get("model") for p in subs})
pending = sorted(m for m in by_model if m in WHITELIST and WHITELIST[m][0] == "pending")
exempt_rows = sum(len(ids) for m, ids in by_model.items() if m in WHITELIST)

if failures:
    print(f"FAILED: {len(failures)} assertion(s) on {POINTS.relative_to(ROOT)}")
    sys.exit(1)

for model in stale:
    print(f"STALE-WHITELIST {model}: already has a positive list_blended; remove it from WHITELIST")
for model in unused:
    print(f"NOTE {model}: whitelisted but absent from the current subscription rows; "
          f"drop the entry once the ruling no longer applies - {WHITELIST[model][1]}")
for model in pending:
    print(f"WARN {model}: whitelisted as pending - {WHITELIST[model][1]}")
if len(WHITELIST) > WHITELIST_WARN_SIZE:
    print(f"WARN whitelist holds {len(WHITELIST)} models (>{WHITELIST_WARN_SIZE}): "
          f"missing list-prices tiers is becoming the norm, tighten it")

print(f"PASS: {len(subs) - exempt_rows}/{len(subs)} subscription rows carry a positive "
      f"list_blended_usd_per_mtok; {exempt_rows} rows on {len(by_model)} whitelisted models "
      f"fall back to MEDIAN_AA_TASK_MTOK {FALLBACK_MTOK} by design "
      f"({len(pending)} of them pending a list-prices tier)")
