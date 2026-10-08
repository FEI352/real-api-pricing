"""守卫「情景点」不得污染任何榜单前沿，且在设计上确实被支配。

背景：情景点 = 同一 served_model 的多档情景（用 plan_id 后缀表达，先例 GLM 忙/中/闲）。
它们与主行**同模型、同榜、同分**，只是单价档不同 —— 互不替代、不可相加，因此必须被
同模型更便宜的同分点支配；一旦进前沿，图上就会出现「一条自洽的假前沿」。
2026-10-08 新增的 Haiku 5.5 输入长度分档（claude_pro_over100k / claude_pro_mix5050 /
claude_max_20x_over100k / claude_max_20x_mix5050）曾由人工核验三次，本脚本把它机器化。

数据源与口径（照 scripts/cli.py generate_report()）：
* 点集 = derived/points.json，逐榜取 `<board>__score` / `<board>__mean_cost_usd_per_task`
  / `<board>__benchmark_list_price`；
* 每任务成本：`list_price = benchmark_list_price or list_blended_usd_per_mtok`，
  `list_price > 0` 时 `cost × real ÷ list_price`，否则回退 `3.51759 × real`
  （web/src/domain.ts MEDIAN_AA_TASK_MTOK 常数回退）；
* 排序 (cost 升序, score 降序)，前沿 = score 严格递增链（与 cli.py 同一算法）；
* `hyperqwen_local_*` 显式排除：其 real_usd_per_mtok 是本地硬件摊销价（不是订阅/按量
  可比价），且 mean_cost 为 0 会走常数回退造出假低成本点 —— 本地硬件不参与订阅成本
  前沿比较（当前数据下它们本来也不在链上，此排除是为防未来摊销价下探）。
* 全量口径与 main 口径各算一次：网页主图按 tier=main 过滤，全量图不滤，两个口径都不得
  出现情景点。

断言：
1. 前沿隔离：清单内情景点不得出现在 8 个榜的 cost 前沿上（全量 + main 两个口径）；
2. 支配存在：情景点在**每个有分的榜**上必须存在同 model、同榜、同分、real 更低的点；
   若不存在（例如价格更低而「应该」进前沿），报 FAIL 并要求人工确认是否登记错误；
3. 清单有效：清单里的 plan_id 必须在 data/adopted.csv 与 points.json 中都存在；
4. 登记完备性：扫描「情景命名 + data_date_kind=derived + 被同模型同分更便宜点支配」
   但不在清单里的行 —— 这是「漏登记情景点」的机器信号（实测当前 0 条）。未登记点若
   已污染前沿 → FAIL；仅被支配 → WARN 提示登记。

**新增情景点时必须登记**：把 plan_id 加进下面的 SCENARIO_PLAN_IDS（清单是权威判据，
不靠命名正则猜）。漏登记时断言 1/2 不覆盖该点，但第 4 项会以 WARN/FAIL 兜底提示。

失败输出点名「哪个情景点、在哪个榜、被谁支配/未支配」。
只读：不修改任何数据。失败时非 0 退出。`--points` 可指向 /tmp 副本做负向测试。
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POINTS = ROOT / "derived" / "points.json"
ADOPTED = ROOT / "data" / "adopted.csv"

FALLBACK_MTOK = 3.51759  # web/src/domain.ts MEDIAN_AA_TASK_MTOK，cli.py 常数回退同值
LOCAL_HARDWARE_PREFIX = "hyperqwen_local_"
SCORE_TOLERANCE = 1e-9  # 同 configuration 的分数应逐位相同；容差只防浮点复制噪声

# 8 个榜必须与 points.json 的 boards 集合一致（防榜单被静默删减）。
BOARDS = (
    "aa_intelligence_index",
    "terminal_bench_4",
    "aa_terminal_bench_4",
    "arena_code",
    "arena_agent_mode",
    "aa_coding_agent_index",
    "open_design_arena",
    "deepswe_1_1",
)

# 情景点清单（权威判据）：plan_id -> 说明（主行 / 档位）。
# 新增情景点时**必须**在此登记，并在 data/adopted.csv 的 decision_note 写明档位口径。
SCENARIO_PLAN_IDS = {
    "claude_pro_over100k": "Claude Pro · claude-haiku-5.5 >100k 档（主行 claude_pro）",
    "claude_pro_mix5050": "Claude Pro · claude-haiku-5.5 50/50 混合档（主行 claude_pro）",
    "claude_max_20x_over100k": "Claude Max 20x · claude-haiku-5.5 >100k 档（主行 claude_max_20x）",
    "claude_max_20x_mix5050": "Claude Max 20x · claude-haiku-5.5 50/50 混合档（主行 claude_max_20x）",
}

# 登记完备性用的「情景命名」形态（档位 / 时段），仅用于兜底侦测，不是权威判据。
SCENARIO_SHAPE = re.compile(r"档|忙时|闲时|中间值|50/50|>\s*100k|夜间")

failures = []
warnings = []


def check(ok, label, detail=""):
    if ok:
        return True
    failures.append(f"{label}: {detail}")
    print(f"FAIL {label}: {detail}")
    return False


def warn(label, detail=""):
    warnings.append(f"{label}: {detail}")
    print(f"WARN {label}: {detail}")


def plan_id_of(point):
    return point["id"].split("::", 1)[0]


def task_cost(point, board):
    """照 scripts/cli.py generate_report()：返回 None 表示该点不进该榜的成本榜。"""
    cost = point.get(f"{board}__mean_cost_usd_per_task")
    real = point.get("real_usd_per_mtok")
    list_price = point.get(f"{board}__benchmark_list_price") or point.get("list_blended_usd_per_mtok")
    if isinstance(cost, (int, float)) and isinstance(real, (int, float)) and list_price and list_price > 0:
        return cost * (real / list_price)
    if isinstance(real, (int, float)) and real > 0:
        return FALLBACK_MTOK * real
    return None


def cost_frontier(board, points, tier=None):
    """(cost 升序, score 降序) 扫一遍取 score 严格递增链；tier=None 为全量口径。"""
    rows = []
    for p in points:
        if p["id"].startswith(LOCAL_HARDWARE_PREFIX):
            continue
        if tier is not None and p.get("tier") != tier:
            continue
        score = p.get(f"{board}__score")
        if not isinstance(score, (int, float)):
            continue
        c = task_cost(p, board)
        if c is None:
            continue
        rows.append((c, score, p))
    rows.sort(key=lambda r: (r[0], -r[1]))
    out, best = [], None
    for _, score, p in rows:
        if best is None or score > best:
            out.append(p)
            best = score
    return out


def dominators(point, board, points):
    """同 model、同榜、同分、real 更低的点（即「应该」把 point 挡在前沿外的点）。"""
    score = point.get(f"{board}__score")
    real = point.get("real_usd_per_mtok")
    if not isinstance(score, (int, float)) or not isinstance(real, (int, float)):
        return []
    out = []
    for q in points:
        if q["id"] == point["id"] or q["model"] != point["model"]:
            continue
        qs = q.get(f"{board}__score")
        qr = q.get("real_usd_per_mtok")
        if not isinstance(qs, (int, float)) or not isinstance(qr, (int, float)):
            continue
        if abs(qs - score) > SCORE_TOLERANCE * max(1.0, abs(score)):
            continue
        if qr < real:
            out.append(q)
    return out


def fmt_real(value):
    return f"{value:.9g}" if isinstance(value, (int, float)) else repr(value)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--points", default=str(DEFAULT_POINTS),
                    help="points.json 路径（负向测试可指向 /tmp 副本，仓库数据只读）")
args = parser.parse_args()
points_path = Path(args.points)

if not points_path.exists():
    print(f"FAIL {points_path} missing - run scripts/compute.py first")
    sys.exit(1)
payload = json.loads(points_path.read_text(encoding="utf-8"))
points = payload["points"]
boards = list(payload.get("boards") or [])
check(set(boards) == set(BOARDS), "points.json boards == the 8 registered boards",
      f"actual {boards}")
check(len(SCENARIO_PLAN_IDS) > 0, "scenario plan list is not empty", "actual 0 entries")

with ADOPTED.open(encoding="utf-8-sig") as source:
    adopted = {r["plan_id"]: r for r in csv.DictReader(source)}

# ---- 前沿（全量 + main 两个口径）----
frontier_ids = {}
frontier_sizes = {}
for board in BOARDS:
    for tier in (None, "main"):
        front = cost_frontier(board, points, tier)
        frontier_ids[(board, tier)] = {p["id"] for p in front}
        frontier_sizes[(board, tier)] = len(front)
    check(frontier_sizes[(board, None)] > 0, f"{board} cost frontier is not empty",
          "0 points - upstream data suspect")

scenario_points = [p for p in points if plan_id_of(p) in SCENARIO_PLAN_IDS]

# ---- 断言 3：清单有效性（防清单腐烂 / 拼写错）----
for plan_id, note in SCENARIO_PLAN_IDS.items():
    check(plan_id in adopted, f"scenario plan {plan_id} exists in data/adopted.csv",
          f"missing row ({note})")
    check(any(plan_id_of(p) == plan_id for p in points),
          f"scenario plan {plan_id} exists in {points_path.name}", f"missing point ({note})")

# ---- 断言 1：情景点不得出现在任何榜的前沿上 ----
for board in BOARDS:
    for tier in (None, "main"):
        ids = frontier_ids[(board, tier)]
        scope = "all tiers" if tier is None else "main tier"
        for p in scenario_points:
            if p["id"] not in ids:
                continue
            dom = dominators(p, board, points)
            if dom:
                detail = (f"cost {task_cost(p, board):.6g}, score {p[f'{board}__score']:.4f}; "
                          f"同模型同分更便宜的 {dom[0]['id']}（real {fmt_real(dom[0]['real_usd_per_mtok'])} "
                          f"< {fmt_real(p['real_usd_per_mtok'])}）存在却未挡住它 → 前沿计算口径不一致")
            else:
                detail = (f"cost {task_cost(p, board):.6g}, score {p[f'{board}__score']:.4f}; "
                          f"同模型没有更便宜的同分点 → 单价档疑似登记反了（更低档反而更贵），"
                          f"请人工确认 {plan_id_of(p)} 的 real_usd_per_mtok")
            check(False, f"frontier isolation: {p['id']} is ON the {board} cost frontier ({scope})", detail)

# ---- 断言 2：每个情景点在有分的榜上必须被同模型同分更便宜点支配 ----
domination_evidence = {}
for p in scenario_points:
    pid = plan_id_of(p)
    for board in BOARDS:
        score = p.get(f"{board}__score")
        if not isinstance(score, (int, float)):
            continue  # 该榜无分：情景点不进该榜，支配关系不适用
        dom = dominators(p, board, points)
        if not dom:
            siblings = [
                f"{q['id']} real={fmt_real(q['real_usd_per_mtok'])}"
                for q in points
                if q["model"] == p["model"] and q["id"] != p["id"]
                and isinstance(q.get(f"{board}__score"), (int, float))
                and abs(q[f"{board}__score"] - score) <= SCORE_TOLERANCE * max(1.0, abs(score))
            ]
            check(False, f"domination: {p['id']} has no same-model same-score cheaper point on {board}",
                  f"score {score:.4f}, real {fmt_real(p['real_usd_per_mtok'])}; "
                  f"同模型同分点：{siblings or '无'} → 若它因价格更低而应进前沿，"
                  f"请确认是否登记错误")
        else:
            domination_evidence.setdefault(pid, []).append((board, dom[0]["id"]))

# ---- 第 4 项：登记完备性（WARN；未登记点污染前沿则 FAIL）----
unregistered = {}
for p in points:
    pid = plan_id_of(p)
    if pid in SCENARIO_PLAN_IDS or not SCENARIO_SHAPE.search(p.get("plan") or ""):
        continue
    if adopted.get(pid, {}).get("data_date_kind") != "derived":
        continue
    on_frontier = [b for b in BOARDS if p["id"] in frontier_ids[(b, None)]]
    doms = [(b, dominators(p, b, points)) for b in BOARDS]
    doms = [(b, d) for b, d in doms if d]
    if on_frontier or doms:
        unregistered[p["id"]] = (on_frontier, doms)

for point_id, (on_frontier, doms) in sorted(unregistered.items()):
    if on_frontier:
        check(False, f"unregistered scenario-shaped point {point_id} is ON the {on_frontier[0]} cost frontier",
              "该行 data_date_kind=derived 且命名形如情景档，却未登记进 SCENARIO_PLAN_IDS —— "
              "请登记或修正单价档")
    else:
        board, dom = doms[0]
        warn(f"unregistered scenario-shaped point {point_id}",
             f"命名形如情景档且被 {dom[0]['id']} 支配 @{board}；如属新增情景点请登记进 "
             f"SCENARIO_PLAN_IDS（否则断言 1/2 不覆盖它）")

# ---- 输出统计 ----
scored = [(pid, ev) for pid, ev in sorted(domination_evidence.items())]
print(f"scenario points: {len(SCENARIO_PLAN_IDS)} "
      f"({', '.join(sorted(SCENARIO_PLAN_IDS))}); points matched in {points_path.name}: {len(scenario_points)}")
print("frontier sizes (all/main): " + " | ".join(
    f"{b} {frontier_sizes[(b, None)]}/{frontier_sizes[(b, 'main')]}" for b in BOARDS))
for pid, ev in scored:
    boards_txt = ", ".join(f"{b} <- {dom}" for b, dom in ev)
    print(f"domination: {pid}: {boards_txt}")
if warnings:
    print(f"WARN: {len(warnings)} unregistered scenario-shaped row(s) - 见上方点名")

if failures:
    print(f"FAILED: {len(failures)} assertion(s) on scenario points")
    sys.exit(1)

print(f"PASS: {len(SCENARIO_PLAN_IDS)} scenario points stay off all {len(BOARDS)} board cost "
      f"frontiers (all + main tiers) and are dominated on every scored board by a same-model "
      f"same-score cheaper point; unregistered scenario-shaped rows: {len(unregistered)}")
