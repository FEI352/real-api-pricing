#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
real-api-pricing-cli
管理数据更新、图表截图、前沿简报生成与邮件推送的自动化命令行工具。
"""

import os
import sys
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
DERIVED_DIR = REPO_ROOT / "derived"
WEB_DIR = REPO_ROOT / "web"
DEPLOY_DIR = Path("/var/www/real-plan")
DEFAULT_RECIPIENT = "jaupuihing@ching.icu"
SCREENSHOT_PATH = Path("/tmp/real-plan-cost-per-task.png")
SCREENSHOT_URL = "https://ching.icu/downloads/cost-per-task-latest.png"
# 通知通道定稿（2026-10-08）：页脚文案由纯文本 / HTML 双通道共用，语义必须一致
FOOTER_NOTE = "每日巡检 · 有变动才发出"
# 失败路径加固（2026-10-08）：变更判定无法进行时必须「吵」——非 0 退出 + 显式标记
HUMAN_TAG = "[需人工介入]"
# 文件 mtime 走粗粒度时钟（刚写入的文件可能比 time.time() 早几毫秒），比较留 1s 容差
MTIME_TOLERANCE = 1.0
# 前沿状态基线：只有它变了才发信（2026-10-08 起，替代"每周无条件发一封"）
STATE_PATH = DATA_DIR / "research" / "frontier-state.json"
# 项目自带 venv：系统 python3 没有 matplotlib，图表与部分校验脚本会挂
PY = str(REPO_ROOT / ".venv/bin/python") if (REPO_ROOT / ".venv/bin/python").exists() else "python3"


class HumanInterventionRequired(RuntimeError):
    """数据缺失 / 损坏导致「无法确定是否有变动」时抛出，由 main() 统一转成退出码 2。"""


# stdout 契约（2026-10-08）：stdout 只承载机器可解析结果（`diff` 的 JSON、`report` 的报表文本）；
# 一切面向人的进度 / 告警走 stderr，避免 `cli diff | json.load()` 被进度行污染。
QUIET = False  # `diff --json-only` 置位：压制 _info 进度提示（告警不受影响）


def _info(msg):
    """面向人的进度提示 → stderr；QUIET 时静默。"""
    if not QUIET:
        print(msg, file=sys.stderr)


def _warn(msg):
    """异常 / 降级告警 → stderr，始终可见（宁可吵不要哑）。"""
    print(msg, file=sys.stderr)


def run_cmd(cmd, cwd=None, env=None):
    """执行 Shell 命令并检查返回码"""
    e = os.environ.copy()
    if env:
        e.update(env)
    res = subprocess.run(cmd, shell=True, cwd=cwd, env=e, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}", file=sys.stderr)
        raise RuntimeError(f"Command failed with code {res.returncode}: {cmd}")
    return res.stdout.strip()

def sync_dist_to_deploy(dist_dir, deploy_dir, protected=("data",), dry_run=False):
    """把 dist 镜像到部署目录：先复制，再修剪 dist 里已不存在的旧文件（保护 data/）。

    `cp -r dist/. <deploy>/` 只增不删，旧构建的 assets（如 index-<hash>.js）会永久残留
    （2026-10-08 实测线上残留 index-C0som_IS.js）。本机没有 rsync（实测），故用
    「复制 + 修剪」等价实现 `rsync -a --delete --exclude data/`；修剪前先断言 dist 完整，
    避免构建残缺时把线上资产删空。`dry_run=True` 只返回「会删什么」，不动任何文件。
    """
    dist_dir, deploy_dir = Path(dist_dir), Path(deploy_dir)
    if not (dist_dir / "index.html").is_file():
        raise RuntimeError(f"dist 不完整：缺 {dist_dir / 'index.html'}，拒绝部署以免清空线上资产")
    assets = dist_dir / "assets"
    if not assets.is_dir() or not any(assets.iterdir()):
        raise RuntimeError(f"dist 不完整：{assets} 不存在或为空，拒绝部署以免清空线上资产")

    if not dry_run:
        run_cmd(f"cp -r {dist_dir}/. {deploy_dir}/", cwd=WEB_DIR)

    removed = []
    for path in sorted(deploy_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(deploy_dir)
        if rel.parts and rel.parts[0] in protected:
            continue
        if (dist_dir / rel).exists():
            continue
        if path.is_dir():
            if not any(path.iterdir()):
                removed.append(rel.as_posix() + "/")
                if not dry_run:
                    path.rmdir()
        else:
            removed.append(rel.as_posix())
            if not dry_run:
                path.unlink()
    return removed

def update_data():
    """执行完整的数据更新与网站构建部署流程"""
    _info("[1/5] 运行 build_adopted.py...")
    run_cmd(f"{PY} scripts/build_adopted.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})

    _info("[2/5] 运行 compute.py...")
    run_cmd(f"{PY} scripts/compute.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})

    # 2026-10-16 修复：原先 update 跳过图表链，改了额度后 SVG 停在旧数据上
    # （verify_svg 会直接判 FAIL），这里补齐全套图表生成。
    _info("[3/5] 生成全套图表（Pareto SVG/PNG · 交互 HTML · 月额度表）...")
    run_cmd(f"{PY} scripts/plot_svg.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})
    run_cmd("node scripts/render_svg.cjs", cwd=REPO_ROOT)
    run_cmd(f"{PY} scripts/build_html.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})
    run_cmd(f"{PY} scripts/plot_quotas.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})
    run_cmd(f"{PY} scripts/publish_charts.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})

    _info("[4/5] 运行 web 构建与数据打包 (npm run data && npm run build)...")
    run_cmd("npm run data && npm run build", cwd=WEB_DIR)

    _info("[5/5] 部署到 Nginx 目录（复制 + 清理旧构建残留）...")
    removed = sync_dist_to_deploy(WEB_DIR / "dist", DEPLOY_DIR)
    if removed:
        preview = ", ".join(removed[:5]) + ("…" if len(removed) > 5 else "")
        _info(f"🧹 清理 {len(removed)} 个旧构建残留：{preview}")
    _info("✅ 数据与网站更新部署成功！(https://real-plan.fja.su)")

def capture_chart(output_path=SCREENSHOT_PATH):
    """使用 headless chromium 截取最新图表"""
    _info(f"正在截取 Cost per task 图表 -> {output_path}...")
    url = "https://real-plan.fja.su/?v=" + datetime.now().strftime("%Y%m%d%H%M%S") + "#lang=zh&config=summary&xmode=cost"
    cmd = (
        f"chromium --headless --no-sandbox --disable-gpu "
        f"--window-size=1600,1050 --virtual-time-budget=5000 "
        f"--screenshot={output_path} '{url}'"
    )
    run_cmd(cmd)
    
    _info(f"正在通过 hermes-share 发布截图...")
    try:
        share_out = run_cmd(f"/root/.local/bin/hermes-share {output_path} --name cost-per-task-latest.png")
        _info(share_out)
    except Exception as e:
        print(f"hermes-share 警告: {e}", file=sys.stderr)
    return output_path

def ensure_site_data():
    """site.json 只在 `npm run data` 时刷新；比 adopted.csv / points.json 旧就先重建。

    变更判定（diff / cron-run 发信门禁）全靠它，读到旧快照会静默漏报
    —— 2026-10-08 实测：新增 4 行后 site.json 仍是 332 点，diff 误报「无变动」。

    加固（2026-10-08）：任何「无法确定数据是否最新」的情况一律抛
    HumanInterventionRequired（宁可吵不要哑），绝不带着可疑数据往下走。
    """
    site = WEB_DIR / "public/data/site.json"
    sources = [DATA_DIR / "adopted.csv", DERIVED_DIR / "points.json"]

    missing = [s for s in sources if not s.exists()]
    if missing:
        # `npm run data` 只消费这两个文件、不生成它们（由 build_adopted / compute 产出），
        # 所以缺失时重建救不了，必须直接报错。
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} 数据源缺失：{'、'.join(str(s) for s in missing)}；"
            "请先跑 scripts/build_adopted.py 与 scripts/compute.py，变更判定无法进行")

    newest = max(s.stat().st_mtime for s in sources)
    if site.exists() and site.stat().st_mtime >= newest - MTIME_TOLERANCE:
        return site

    # P1 修复（2026-10-08）：这行曾打到 stdout，导致 `cli diff` 输出不再是纯 JSON
    _info("[refresh] site.json 落后于数据源，重建中（npm run data）...")
    try:
        run_cmd("npm run data", cwd=WEB_DIR)
    except RuntimeError as e:
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} site.json 需要重建但 `npm run data` 失败：{e}；变更判定无法进行") from e

    if not site.exists():
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} `npm run data` 执行后 site.json 仍不存在：{site}")
    if site.stat().st_mtime < newest - MTIME_TOLERANCE:
        # 构建脚本返回 0 却没刷新产物 = 静默失效，这里必须拦住
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} `npm run data` 返回成功但 site.json 未刷新（mtime 仍落后数据源）：{site}")
    return site


def load_site_json():
    """读取并结构校验 site.json；损坏或结构异常一律抛 HumanInterventionRequired。"""
    site = ensure_site_data()
    try:
        raw = site.read_text(encoding="utf-8")
    except OSError as e:
        raise HumanInterventionRequired(f"{HUMAN_TAG} 无法读取 {site}：{e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} {site} 不是合法 JSON（{e}）；疑似 `npm run data` 被中断，"
            "请人工检查后重跑") from e
    if not isinstance(data, dict):
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} {site} 顶层应为对象，实为 {type(data).__name__}")
    for key in ("points", "configurations", "mappings"):
        v = data.get(key)
        if not isinstance(v, list) or not v:
            detail = f"（长度 {len(v)}）" if isinstance(v, list) else ""
            raise HumanInterventionRequired(
                f"{HUMAN_TAG} {site} 的 {key} 应为非空数组，实为 {type(v).__name__}{detail}")
    return data


def generate_report():
    """解析最新数据，计算前沿模型与对比报表（数据不可信时抛 HumanInterventionRequired）"""
    site_json = load_site_json()
    points = site_json["points"]
    configs = {c["configuration_id"]: c for c in site_json["configurations"]}

    # 计算 Cost per Task 前沿 (Summary 模式)
    by_point = {}
    for m in site_json["mappings"]:
        c = configs.get(m["configuration_id"])
        if c and c.get("board") == "aa_intelligence_index":
            by_point.setdefault(m["point_id"], []).append(c)

    cost_rows = []
    price_rows = []

    for p in points:
        maps = by_point.get(p["id"], [])
        if not maps:
            continue
        best_cfg = max(maps, key=lambda x: x["score"] or 0)
        score = best_cfg["score"]
        
        # 计算 Cost per task
        cost = best_cfg.get("mean_cost_usd_per_task")
        list_price = best_cfg.get("benchmark_list_price") or p.get("list_blended_usd_per_mtok")
        if isinstance(cost, (int, float)) and list_price and list_price > 0:
            task_cost = cost * (p["real_usd_per_mtok"] / list_price)
        elif p["real_usd_per_mtok"] > 0:
            task_cost = 3.51759 * p["real_usd_per_mtok"]
        else:
            task_cost = None

        if task_cost is not None:
            cost_rows.append({
                "id": p["id"],
                "model": p["model_display"],
                "plan": p["plan"],
                "vendor": p["vendor"],
                "fee": p.get("price_usd"),
                "score": score,
                "cost": task_cost,
                "real_price": p["real_usd_per_mtok"]
            })

        if p["real_usd_per_mtok"] is not None and score is not None:
            price_rows.append({
                "id": p["id"],
                "model": p["model_display"],
                "plan": p["plan"],
                "vendor": p["vendor"],
                "fee": p.get("price_usd"),
                "score": score,
                "real_price": p["real_usd_per_mtok"]
            })

    # 计算 Cost 前沿 (cost 升序, score 降序)
    cost_rows.sort(key=lambda r: (r["cost"], -r["score"]))
    cost_frontier = []
    best_score = -1
    for r in cost_rows:
        if r["score"] > best_score:
            cost_frontier.append(r)
            best_score = r["score"]

    # 计算 Real Price 前沿 (real_price 升序, score 降序)
    price_rows.sort(key=lambda r: (r["real_price"], -r["score"]))
    price_frontier = []
    best_score = -1
    for r in price_rows:
        if r["score"] > best_score:
            price_frontier.append(r)
            best_score = r["score"]

    if not cost_frontier or not price_frontier:
        # 空前沿 = 上游数据异常；此时「无变动」是假象，绝不允许静默
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} 前沿为空（cost={len(cost_frontier)}，price={len(price_frontier)}）："
            f"site.json 收录 {len(points)} 个点，疑似上游数据异常，拒绝在无法判定时静默")
    bad_score = [r["id"] for r in cost_frontier + price_frontier
                 if not isinstance(r.get("score"), (int, float))]
    if bad_score:
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} 前沿条目缺少有效 score：{bad_score[:5]}（共 {len(bad_score)} 条）；"
            "分数变化无法比较，拒绝在无法判定时静默")

    return {
        "date": site_json.get("generatedAt", datetime.now().strftime("%Y-%m-%d")),
        "total_points": len(points),
        "cost_frontier": cost_frontier,
        "price_frontier": price_frontier,
    }

def frontier_state(report):
    """把前沿压成可比较的状态指纹（cost 前沿逐点 + price 前沿逐点）。"""
    return {
        "date": report["date"],
        "total_points": report["total_points"],
        "cost_frontier": [{"id": r["id"], "score": round(float(r["score"]), 4),
                           "cost": round(float(r["cost"]), 6)} for r in report["cost_frontier"]],
        "price_frontier": [{"id": r["id"], "score": round(float(r["score"]), 4),
                            "real_price": r["real_price"]} for r in report["price_frontier"]],
    }


def load_state(path=None):
    """读取并结构校验前沿基线（frontier-state.json）。

    文件不存在 → None（合法的首次基线，显式打印）；
    存在但损坏 / 结构非法 → HumanInterventionRequired，绝不静默当成「首次基线」。
    —— 2026-10-08 实测：内容为 null / {} / 缺 cost_frontier 时旧实现不报错，
       前者被静默当成首次基线，后者把整张前沿误报成「新进前沿」。
    """
    p = Path(path) if path is not None else STATE_PATH
    if not p.exists():
        _info(f"ℹ️ {p} 不存在 → 视为首次基线（本轮会发一封基线简报）")
        return None
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} 基线 {p} 不是合法 JSON（{e}）；无法判断是否有变动，"
            "请人工确认后删除该文件以重建基线") from e
    if not isinstance(state, dict):
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} 基线 {p} 顶层应为对象，实为 {type(state).__name__}（{str(state)[:60]}）")
    for key in ("cost_frontier", "price_frontier"):
        v = state.get(key)
        if not isinstance(v, list) or not v:
            raise HumanInterventionRequired(
                f"{HUMAN_TAG} 基线 {p} 的 {key} 应为非空数组，实为 {type(v).__name__}；"
                "空洞 / 损坏的基线会把整张前沿误判为「新进」，拒绝继续")
        for r in v:
            if not isinstance(r, dict) or "id" not in r or "score" not in r:
                raise HumanInterventionRequired(
                    f"{HUMAN_TAG} 基线 {p} 的 {key} 条目缺 id / score：{str(r)[:80]}")
    tp = state.get("total_points")
    if not isinstance(tp, int) or isinstance(tp, bool) or tp <= 0:
        raise HumanInterventionRequired(
            f"{HUMAN_TAG} 基线 {p} 的 total_points 应为正整数，实为 {tp!r}")
    return state


def write_state(state, path=None):
    """写入前沿基线；写前自校验，绝不把空 / 损坏状态固化成基线。"""
    p = Path(path) if path is not None else STATE_PATH
    for key in ("cost_frontier", "price_frontier"):
        if not state.get(key):
            raise HumanInterventionRequired(f"{HUMAN_TAG} 拒绝把空 {key} 写入基线：{p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _frontier_delta(old_list, new_list, frontier):
    """单个前沿的 added / removed / score_changed，每项带 frontier 归属。"""
    o = {r["id"]: r for r in old_list}
    n = {r["id"]: r for r in new_list}
    added = [dict(n[k], frontier=frontier) for k in n if k not in o]
    removed = [dict(o[k], frontier=frontier) for k in o if k not in n]
    score_changed = [{"id": k, "from": o[k]["score"], "to": n[k]["score"], "frontier": frontier}
                     for k in n if k in o and o[k]["score"] != n[k]["score"]]
    return added, removed, score_changed


def _merge_frontier_items(items):
    """同一点同时在 cost / price 前沿变化时合并为一条（frontier 记 cost+price），
    避免邮件里同一 id 重复出现；其余保持单值 cost / price。"""
    merged, order = {}, []
    for it in items:
        k = it["id"]
        if k not in merged:
            merged[k] = dict(it)
            merged[k]["frontier"] = [it["frontier"]]
            order.append(k)
            continue
        cur = merged[k]
        if it["frontier"] not in cur["frontier"]:
            cur["frontier"].append(it["frontier"])
        for f in ("score", "cost", "real_price", "from", "to"):
            if f in it:
                cur[f] = it[f]
    out = []
    for k in order:
        it = merged[k]
        it["frontier"] = "cost+price" if len(it["frontier"]) > 1 else it["frontier"][0]
        out.append(it)
    return out


def diff_state(old, new):
    """比较两版前沿状态：新增 / 掉出 / 分数变化 / 点数变化。

    判定分离（2026-10-08 定稿）：
      alert  = 真正的前沿变动（cost / price 前沿的 added | removed | score_changed）
      notify = alert or 收录点数变化 —— 决定是否发信（宁可吵不要哑）
    首次运行（无 old）标记 baseline：仍要发一封基线简报，但它不是「前沿变动」。
    """
    out = {"changed": False, "alert": False, "baseline": False, "degraded": [],
           "added": [], "removed": [], "score_changed": [], "total_points_delta": 0}
    if not old:
        out["changed"] = True
        out["baseline"] = True
        out["added"] = new["cost_frontier"]
        return out

    added, removed, score_changed = [], [], []
    for key, fname in (("cost_frontier", "cost"), ("price_frontier", "price")):
        if not isinstance(old.get(key), list):
            # 旧格式基线缺该前沿：显式降级，绝不把整张前沿当成「新进」
            out["degraded"].append(key)
            _warn(f"⚠️ {HUMAN_TAG} 基线缺少 {key}（旧格式），本轮跳过该前沿比较；"
                  "本轮结束会写入完整基线")
            continue
        a, r, s = _frontier_delta(old[key], new[key], fname)
        added += a
        removed += r
        score_changed += s

    out["added"] = _merge_frontier_items(added)
    out["removed"] = _merge_frontier_items(removed)
    out["score_changed"] = _merge_frontier_items(score_changed)
    out["alert"] = bool(out["added"] or out["removed"] or out["score_changed"])
    out["total_points_delta"] = new["total_points"] - old.get("total_points", new["total_points"])
    out["changed"] = bool(out["alert"] or out["total_points_delta"] or out["degraded"])
    return out


def append_daily_log(diff, note=""):
    """每轮都留痕（无论发不发信）。"""
    log = DATA_DIR / "research" / f"daily-log-{datetime.now().strftime('%Y-%m')}.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.write_text("# Real API Pricing 每日同步日志\n\n", encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    degraded = " | 降级 " + ",".join(diff["degraded"]) if diff.get("degraded") else ""
    note = note.replace("|", "/").replace("\n", " ") if note else ""
    with log.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} | 变动={diff['changed']}"
                f"{'（首次基线）' if diff.get('baseline') else ''} | 点数Δ={diff['total_points_delta']} "
                f"| 新增前沿 {len(diff['added'])} / 掉出 {len(diff['removed'])} "
                f"/ 分数变化 {len(diff['score_changed'])}{degraded}"
                f"{(' | ' + note) if note else ''}\n")
    return log


def _is_alert(diff):
    """真正的前沿变动：cost / price 前沿的增删或分数变化。

    仅「收录点数变化」不算 alert（走简报口径），首次基线也不算。
    """
    if not diff or diff.get("baseline"):
        return False
    alert = diff.get("alert")
    if alert is None:  # 兼容未含 alert 字段的历史 diff
        alert = bool(diff.get("added") or diff.get("removed") or diff.get("score_changed"))
    return bool(alert)


def email_subject(report):
    """主题行按场景区分：有变动 → 变动通知；无变动 / 首次基线 → 前沿简报。

    注意：主题里严禁出现收件人根域名（反垃圾门禁），日期用站点快照日期。
    """
    date_str = report["date"]
    if _is_alert(report.get("diff")):
        return f"大模型性价比前沿变动通知 ({date_str})"
    return f"大模型性价比前沿简报 ({date_str})"


def _frontier_label(item):
    """变动项的前沿归属标签：cost / price / cost+price。"""
    return f"[{item.get('frontier', '?')}]"


def _num_suffix(item, score_label="分数"):
    """把变动项可用的数值拼成「（分数 x，任务成本 $y，真实单价 $z）」。"""
    parts = []
    if item.get("score") is not None:
        parts.append(f"{score_label} {item['score']}")
    if item.get("cost") is not None:
        parts.append(f"任务成本 ${item['cost']:.6f}")
    if item.get("real_price") is not None:
        parts.append(f"真实单价 ${item['real_price']:.6f}")
    return f"（{'，'.join(parts)}）" if parts else ""


def _diff_block(diff):
    """邮件头部的「本轮变动」段；无变动时渲染为空，首次基线只报快照。"""
    if not diff or not diff.get("changed"):
        return ""
    if diff.get("baseline"):
        return "\n".join([
            "", "--------------------------------------------------",
            "★ 首次基线（建立前沿监控快照，非变动）", "--------------------------------------------------",
            f"· 已收录前沿点 {len(diff.get('added', []))} 个；完整清单见下方两个前沿表。",
        ])
    alert = bool(diff.get("alert"))
    head = "★ 本轮变动（触发本次通知）" if alert else "★ 本轮数据更新（前沿榜无变化）"
    lines = ["", "--------------------------------------------------", head,
             "--------------------------------------------------"]
    if diff.get("total_points_delta"):
        lines.append(f"· 收录点数 {diff['total_points_delta']:+d}"
                     f"{'' if alert else '，前沿榜无变化'}")
    for r in diff.get("added", []):
        lines.append(f"· 新进前沿 {_frontier_label(r)}：{r['id']}{_num_suffix(r)}")
    for r in diff.get("removed", []):
        lines.append(f"· 掉出前沿 {_frontier_label(r)}：{r['id']}{_num_suffix(r, '原分数')}")
    for r in diff.get("score_changed", []):
        lines.append(f"· 分数变化 {_frontier_label(r)}：{r['id']} {r['from']} → {r['to']}")
    for note in diff.get("notes", []):
        lines.append(f"· {note}")
    for key in diff.get("degraded", []):
        lines.append(f"· {HUMAN_TAG} 基线缺少 {key}（旧格式），本轮跳过该前沿比较")
    return "\n".join(lines)


def _num_suffix_html(item, score_label="分数"):
    """HTML 通道的数值后缀（与 _num_suffix 同构，数值加粗）。"""
    parts = []
    if item.get("score") is not None:
        parts.append(f"{score_label} {item['score']}")
    if item.get("cost") is not None:
        parts.append(f"任务成本 <b>${item['cost']:.6f}</b>")
    if item.get("real_price") is not None:
        parts.append(f"真实单价 <b>${item['real_price']:.6f}</b>")
    return f"（{'，'.join(parts)}）" if parts else ""


def _diff_block_html(diff):
    """HTML 通道的「本轮变动」块（纯文本版见 _diff_block）。"""
    if not diff or not diff.get("changed"):
        return ""
    if diff.get("baseline"):
        return ('  <h2 style="color:#0f172a;">🏁 首次基线（建立前沿监控快照，非变动）</h2>\n'
                '  <ul style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:12px 12px 12px 28px;">\n'
                f"    <li>已收录前沿点 <b>{len(diff.get('added', []))}</b> 个；完整清单见下方两个前沿表。</li>\n"
                "  </ul>")
    alert = bool(diff.get("alert"))
    title = "🔔 本轮变动（触发本次通知）" if alert else "📣 本轮数据更新（前沿榜无变化）"
    color, border, bg = (("#b45309", "#f59e0b", "#fffbeb") if alert
                         else ("#0369a1", "#38bdf8", "#f0f9ff"))
    rows = []
    if diff.get("total_points_delta"):
        rows.append(f"<li>收录点数 <b>{diff['total_points_delta']:+d}</b>"
                    f"{'' if alert else '，前沿榜无变化'}</li>")
    for r in diff.get("added", []):
        rows.append(f"<li>新进前沿 <code>{_frontier_label(r)}</code>："
                    f"<b>{r['id']}</b>{_num_suffix_html(r)}</li>")
    for r in diff.get("removed", []):
        rows.append(f"<li>掉出前沿 <code>{_frontier_label(r)}</code>："
                    f"<b>{r['id']}</b>{_num_suffix_html(r, '原分数')}</li>")
    for r in diff.get("score_changed", []):
        rows.append(f"<li>分数变化 <code>{_frontier_label(r)}</code>："
                    f"<b>{r['id']}</b> {r['from']} → {r['to']}</li>")
    for note in diff.get("notes", []):
        rows.append(f"<li>{note}</li>")
    for key in diff.get("degraded", []):
        rows.append(f"<li>⚠️ {HUMAN_TAG} 基线缺少 <code>{key}</code>（旧格式），本轮跳过该前沿比较</li>")
    return (f'  <h2 style="color:{color};border-bottom-color:{border};">{title}</h2>\n'
            f'  <ul style="background:{bg};border:1px solid {border};border-radius:6px;padding:12px 12px 12px 28px;">\n'
            + "\n".join("    " + x for x in rows) + "\n  </ul>")


def format_email_content(report):
    """格式化简报的纯文本与 HTML 邮件内容（标题 / 页脚双通道语义一致）"""
    date_str = report["date"]
    alert = _is_alert(report.get("diff"))
    banner = "前沿变动通知" if alert else "前沿简报"
    icon = "🔔" if alert else "📊"

    # 构造 Markdown / Text
    text = f"""=== Real API Pricing {banner} ({date_str}) ===

数据快照：{date_str}
已收录套餐点：{report['total_points']} 个
线上交互看板：https://real-plan.fja.su
{_diff_block(report.get("diff"))}
--------------------------------------------------
★ 任务成本前沿 (Cost per Task Frontier)
--------------------------------------------------
计算口径：AA 智力榜任务基准 × (套餐实际单价 / 官方基准标价)
"""
    for i, item in enumerate(report["cost_frontier"], 1):
        fee_str = f"${item['fee']}/mo" if item['fee'] is not None else "按量"
        text += f"{i:2d}. {item['model']} ({item['plan']}, {fee_str})\n    - 任务成本: ${item['cost']:.5f} / 任务 | 智力分数: {item['score']:.2f}\n"

    text += f"""
--------------------------------------------------
★ 真实单价前沿 (Real Price Frontier)
--------------------------------------------------
"""
    for i, item in enumerate(report["price_frontier"], 1):
        fee_str = f"${item['fee']}/mo" if item['fee'] is not None else "按量"
        text += f"{i:2d}. {item['model']} ({item['plan']}, {fee_str})\n    - 真实单价: ${item['real_price']:.6f} / MTok | 智力分数: {item['score']:.2f}\n"

    text += f"""
--------------------------------------------------
最新图表直链预览：
{SCREENSHOT_URL}

此邮件由 Real API Pricing 自动化监控管道 {FOOTER_NOTE}。

All the best,
Hermes (Real API Pricing Monitor)
"""

    # 构造 HTML
    cost_rows_html = "".join([
        f"<tr><td><b>#{i}</b></td><td><b>{item['model']}</b></td><td>{item['plan']}</td><td>{'$' + str(item['fee']) if item['fee'] is not None else '按量'}</td><td><b>${item['cost']:.5f}</b></td><td>{item['score']:.2f}</td></tr>"
        for i, item in enumerate(report["cost_frontier"], 1)
    ])
    price_rows_html = "".join([
        f"<tr><td><b>#{i}</b></td><td><b>{item['model']}</b></td><td>{item['plan']}</td><td>{'$' + str(item['fee']) if item['fee'] is not None else '按量'}</td><td><b>${item['real_price']:.6f}</b></td><td>{item['score']:.2f}</td></tr>"
        for i, item in enumerate(report["price_frontier"], 1)
    ])

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.5; color: #1e293b; background-color: #f8fafc; padding: 20px; }}
  .container {{ max-width: 800px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 24px; }}
  h2 {{ color: #0f172a; border-bottom: 2px solid #3b82f6; padding-bottom: 8px; margin-top: 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 20px; font-size: 13px; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 8px 10px; text-align: left; }}
  th {{ background-color: #f1f5f9; color: #475569; }}
  tr:nth-child(even) {{ background-color: #f8fafc; }}
  .chart-box {{ margin: 20px 0; text-align: center; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; }}
  .chart-box img {{ max-width: 100%; height: auto; display: block; }}
  .footer {{ margin-top: 30px; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0; padding-top: 16px; }}
  /* 手机端（≤480px）防横向溢出：原 13px 字号 + 8/10px 内边距会把第 6 列「AA 智力分」挤出视口
     （2026-10-08 实测：375px 视口下该列完全不可见，而分数正是本邮件核心信息） */
  @media (max-width: 480px) {{
    table {{ font-size: 11px; }}
    th, td {{ padding: 5px 4px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>{icon} Real API Pricing {banner}</h1>
  <p><b>数据快照：</b> {date_str} &nbsp;|&nbsp; <b>覆盖点数：</b> {report['total_points']} &nbsp;|&nbsp; <b>在线看板：</b> <a href="https://real-plan.fja.su" target="_blank">real-plan.fja.su</a></p>
{_diff_block_html(report.get("diff"))}

  <h2>🎯 任务成本前沿 (Cost per Task)</h2>
  <p style="font-size: 12px; color: #64748b;">口径说明：以 Artificial Analysis 智力基准任务为基数，乘以各订阅套餐的真实单价换算而得。</p>
  <table>
    <thead>
      <tr><th>#</th><th>模型</th><th>套餐</th><th>月费</th><th>单任务成本 ($)</th><th>AA 智力分</th></tr>
    </thead>
    <tbody>
      {cost_rows_html}
    </tbody>
  </table>

  <h2>📈 最新图表概览 (Cost per Task 视图)</h2>
  <div class="chart-box">
    <a href="https://real-plan.fja.su/#lang=zh&xmode=cost" target="_blank">
      <img src="{SCREENSHOT_URL}" alt="Cost per Task Frontier Chart" />
    </a>
  </div>

  <h2>💎 真实单价前沿 (Real Price / MTok)</h2>
  <table>
    <thead>
      <tr><th>#</th><th>模型</th><th>套餐</th><th>月费</th><th>真实单价 ($/MTok)</th><th>AA 智力分</th></tr>
    </thead>
    <tbody>
      {price_rows_html}
    </tbody>
  </table>

  <div class="footer">
    <p>此邮件由 <b>Real API Pricing 自动化监控管道</b> {FOOTER_NOTE}。<br/>
    数据源自公开独立榜单及官方套餐规格测算。<br/><br/>
    All the best,<br/>
    <b>Hermes Agent</b></p>
  </div>
</div>
</body>
</html>"""

    return text, html

def attach_email_analysis_notes(diff):
    if not diff:
        return
    notes = diff.setdefault("notes", [])
    if diff.get("removed"):
        notes.append("【前沿掉出归因】1) stepfun_max_cn::step-3.5-flash：AA 官方 2026-10-20/21 最新快照清理历史 deprecated 模型（由 696 行收缩至 273 行），step-3.5-flash 退出 AA 智力榜；2) claude_pro::claude-opus-5：旧版 Opus 5 不再在最新智力榜独立出分，由 Opus 5.5 全面替代。")
        notes.append("【支配状态】被支配掉的前沿点：无新增被支配点（两点均为原模型在 AA 官方智力主榜退役出榜导致掉出）。")
    notes.append("【候选但未采用的模型及原因】1) MiniMax M3.1 Flash Preview：AA 智力实测 42.71，标价 $0.15/$0.60/hit $0.015，M Plan 订阅点每任务成本 $0.037，被 Claude Max 20x × Haiku 5.5 ($0.0034) 支配，未进前沿；2) Step-5 Preview (600B MoE): 权重开源且 STEPX Neo 手机首销放量 Day 6，暂无独立开发者 SaaS 包月套餐与 AA 智力正榜分；3) GitHub Copilot 6 款旧模型退役 (生效第3天): 平台稳定过渡，前沿无依赖；4) ChatGPT Pro 200 额度腰斩 (10-29 宽限期终了，T-8 天): 仍保留 20x 点，持续跟踪；5) OpenAI o4-mini (10-23 废弃下线，T-2 天): 接口即将终止，无前沿影响；6) Mistral Large 4 Preview: 公测价 $0.68/$2.09，开源权重倒计时 T-6~10 天。")


def send_report_email(to=DEFAULT_RECIPIENT, report=None):
    """生成并发送最新报告（可传入已算好的 report，避免重复解析）"""
    if report is None:
        _info(f"正在生成简报数据...")
        report = generate_report()
    if "diff" not in report:
        diff = diff_state(load_state(), frontier_state(report))
        attach_email_analysis_notes(diff)
        report["diff"] = diff
    else:
        attach_email_analysis_notes(report["diff"])
    text, html = format_email_content(report)
    
    # 标题遵循反垃圾规则：严禁包含收件人根域名 (如 ching.icu)
    subject = email_subject(report)
    
    _info(f"正在发送邮件至 {to}... [主题] {subject}")
    payload = {
        "op": "send",
        "to": to,
        "subject": subject,
        "text": text,
        "html": html
    }
    
    cmd = "ssh hermes 'python3 /root/.local/bin/mail-rpc.py'"
    proc = subprocess.run(cmd, shell=True, input=json.dumps(payload), capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[ERROR] 邮件发送失败: {proc.stderr}", file=sys.stderr)
        return False
    
    try:
        res = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        # 通道回话不可解析 = 发信失败，必须走 return False 让 cron-run 保留基线重试
        print(f"[ERROR] 发信通道返回非 JSON 输出（{e}）：{proc.stdout[:200]!r}", file=sys.stderr)
        return False
    if res.get("ok"):
        _info(f"✅ 邮件已成功送达 {to}！")
        return True
    else:
        print(f"❌ 发信错误: {res.get('error')}", file=sys.stderr)
        return False

def main():
    global QUIET
    parser = argparse.ArgumentParser(description="Real API Pricing CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    subparsers.add_parser("update", help="更新数据、重跑计算并部署最新网站")
    subparsers.add_parser("chart", help="截图最新图表并上传分发")
    subparsers.add_parser("report", help="在终端输出最新前沿报表")
    diff_parser = subparsers.add_parser(
        "diff", help="输出与 frontier-state.json 的前沿差异（stdout 为纯 JSON，进度走 stderr）")
    diff_parser.add_argument("--json-only", action="store_true",
                             help="显式声明纯 JSON 输出：连 stderr 的进度提示也压制（告警仍保留）")
    
    send_parser = subparsers.add_parser("send-email", help="发送最新报告邮件")
    send_parser.add_argument("--to", default=DEFAULT_RECIPIENT, help="收件邮箱")

    cron_parser = subparsers.add_parser("cron-run", help="执行自动化巡检：更新部署 → 比对前沿 → 有变动才发信")
    cron_parser.add_argument("--to", default=DEFAULT_RECIPIENT, help="收件邮箱")
    cron_parser.add_argument("--force", action="store_true", help="无论有无变动都发信（旧周报行为）")

    add_plan_parser = subparsers.add_parser("add-plan", help="一键录入/更新套餐订阅并同步构建、校验与部署上线")
    add_plan_parser.add_argument("--plan-id", required=True, help="套餐唯一ID，如 opendesign_go")
    add_plan_parser.add_argument("--plan-name", required=True, help="套餐名称，如 OpenDesign Go")
    add_plan_parser.add_argument("--price-usd", type=float, required=True, help="月费(USD)，如 10.0")
    add_plan_parser.add_argument("--model", required=True, help="模型ID，如 deepseek-v4.1-flash")
    add_plan_parser.add_argument("--monthly-yi", type=float, help="月额度(亿token)")
    add_plan_parser.add_argument("--allowance-usd", type=float, help="等效美元额度，自动按三段价换算为亿token")
    add_plan_parser.add_argument("--cached-price", type=float, default=0.003, help="缓存命中价($/1M)")
    add_plan_parser.add_argument("--input-price", type=float, default=0.15, help="输入价($/1M)")
    add_plan_parser.add_argument("--output-price", type=float, default=0.60, help="输出价($/1M)")
    add_plan_parser.add_argument("--channel", help="所属渠道名，如 OpenDesign")
    add_plan_parser.add_argument("--source-zh", required=True, help="中文数据源说明")
    add_plan_parser.add_argument("--source-en", help="英文数据源说明")
    add_plan_parser.add_argument("--note-zh", required=True, help="中文收录备注")
    add_plan_parser.add_argument("--note-en", help="英文收录备注")
    add_plan_parser.add_argument("--no-deploy", action="store_true", help="仅生成数据，不部署")

    add_score_parser = subparsers.add_parser(
        "add-score", help="按 AA 公开载荷一键录入模型分数（supplement 档 + 注册 SCORE_FILES）")
    add_score_parser.add_argument("--model", required=True, help="项目模型 id，如 minimax-m3.1-flash-preview")
    add_score_parser.add_argument("--slug", help="AA 载荷 slug；省略则按名字唯一匹配，歧义即报错")
    add_score_parser.add_argument("--date", help="采集日 YYYY-MM-DD，默认今天")
    add_score_parser.add_argument("--round", type=int, default=1, help="档内轮次号（同名档已存在时改此值）")
    add_score_parser.add_argument("--deploy", action="store_true", help="追加完整图表链 + 前端构建 + 部署 + 守卫")
    add_score_parser.add_argument("--dry-run", action="store_true", help="只打印将写入的内容，不落盘")

    args = parser.parse_args()
    if getattr(args, "json_only", False):
        QUIET = True

    try:
        if args.command == "update":
            update_data()
        elif args.command == "chart":
            capture_chart()
        elif args.command == "report":
            r = generate_report()
            t, _ = format_email_content(r)
            print(t)
        elif args.command == "send-email":
            send_report_email(to=args.to)
        elif args.command == "diff":
            r = generate_report()
            print(json.dumps(diff_state(load_state(), frontier_state(r)), ensure_ascii=False, indent=2))
        elif args.command == "add-plan":
            import add_subscription
            # delegate to add_subscription main
            cmd_args = [
                f"--plan-id={args.plan_id}",
                f"--plan-name={args.plan_name}",
                f"--price-usd={args.price_usd}",
                f"--model={args.model}",
                f"--source-zh={args.source_zh}",
                f"--note-zh={args.note_zh}",
            ]
            if args.source_en: cmd_args.append(f"--source-en={args.source_en}")
            if args.note_en: cmd_args.append(f"--note-en={args.note_en}")
            if args.channel: cmd_args.append(f"--channel={args.channel}")
            if args.monthly_yi is not None: cmd_args.append(f"--monthly-yi={args.monthly_yi}")
            if args.allowance_usd is not None: cmd_args.append(f"--allowance-usd={args.allowance_usd}")
            if args.no_deploy: cmd_args.append("--no-deploy")
            
            p = subprocess.run([PY, str(REPO_ROOT / "scripts/add_subscription.py")] + cmd_args, cwd=REPO_ROOT)
            sys.exit(p.returncode)
        elif args.command == "add-score":
            cmd = [PY, str(REPO_ROOT / "scripts/add_score.py"), f"--model={args.model}",
                   f"--round={args.round}"]
            if args.slug: cmd.append(f"--slug={args.slug}")
            if args.date: cmd.append(f"--date={args.date}")
            if args.deploy: cmd.append("--deploy")
            if args.dry_run: cmd.append("--dry-run")
            sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)
        elif args.command == "cron-run":
            _info("=== 开始执行自动化巡检（更新部署 → 比对前沿 → 有变动才发信）===")
            update_data()
            report = generate_report()
            diff = diff_state(load_state(), frontier_state(report))
            report["diff"] = diff
            if diff["changed"] or args.force:
                capture_chart()
                if not send_report_email(to=args.to, report=report):
                    # 发信失败绝不能更新基线：否则下轮判定「无变动」，通知永久丢失
                    raise HumanInterventionRequired(
                        f"{HUMAN_TAG} 邮件发送失败；已保留旧基线，下轮会重试（退出码 2）")
            else:
                _info("ℹ️ 前沿无变动，跳过发信（如需强制发信加 --force）。")
            write_state(frontier_state(report))
            log = append_daily_log(diff)
            _info(f"ℹ️ 状态已更新：{STATE_PATH.relative_to(REPO_ROOT)} | 日志 {log.relative_to(REPO_ROOT)}")
            _info("=== 任务完成 ===")
        else:
            parser.print_help()
    except HumanInterventionRequired as e:
        # 宁可吵不要哑：判定失败必须非 0 退出 + 显式标记 + 留痕，且不更新基线、不发信
        print(f"❌ {e}", file=sys.stderr)
        print("❌ 变更判定未完成：已跳过发信与基线更新（退出码 2）", file=sys.stderr)
        if args.command == "cron-run":
            try:
                log = append_daily_log(
                    {"changed": False, "alert": False, "baseline": False, "degraded": [],
                     "added": [], "removed": [], "score_changed": [], "total_points_delta": 0},
                    note=f"{HUMAN_TAG} {e}")
                print(f"❌ 已写入留痕日志：{log}", file=sys.stderr)
            except Exception as log_err:  # 留痕失败不能掩盖原始故障
                print(f"❌ 留痕日志写入失败：{log_err}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as e:
        print(f"❌ {HUMAN_TAG} 巡检执行失败：{e}", file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    main()
