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
# 收件人不再硬编码：公开仓库里明文邮箱等于把用户主邮箱长期挂在公网。
# 解析顺序：环境变量 REAL_PLAN_RECIPIENT → /etc/real-api-pricing/recipient → 空。
# 解析为空时 send_report_email 会显式报错，绝不静默发往错误地址。
def _load_recipient():
    v = os.environ.get("REAL_PLAN_RECIPIENT", "").strip()
    if v:
        return v
    try:
        p = Path("/etc/real-api-pricing/recipient")
        if p.is_file():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
    except OSError:
        pass
    return ""


DEFAULT_RECIPIENT = _load_recipient()
SCREENSHOT_PATH = Path("/tmp/real-plan-cost-per-task.png")
SCREENSHOT_URL = "https://ching.icu/downloads/cost-per-task-latest.png"
TIME_SCREENSHOT_PATH = Path("/tmp/real-plan-task-time.png")
TIME_SCREENSHOT_URL = "https://ching.icu/downloads/task-time-latest.png"
PARETO_3D_SCREENSHOT_PATH = Path("/tmp/real-plan-3d.png")
PARETO_3D_SCREENSHOT_URL = "https://ching.icu/downloads/pareto-3d-latest.png"
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
        # 2026-11-07：改用「临时目录 + 原子换名」，替代 cp -r。
        # 原实现直接往线上目录覆盖写，构建与拷贝并发时 cp 会以退出码 1 中断，
        # 且半途失败会留下「新旧资产混在一起」的中间态（本会话已复现两次）。
        import shutil, tempfile, os
        deploy_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".deploy-", dir=str(deploy_dir.parent)))
        try:
            # 先复制既有内容（保住 data/ 等受保护目录），再用 dist 覆盖
            shutil.copytree(deploy_dir, staging, dirs_exist_ok=True)
            for item in dist_dir.iterdir():
                target = staging / item.name
                if item.name in protected and target.exists():
                    continue
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
            backup = deploy_dir.with_name(deploy_dir.name + ".prev")
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(deploy_dir, backup)
            os.replace(staging, deploy_dir)
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

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
    # 2026-11-13 修复：原先 data/ 被列为 protected，dist/data 永远不覆盖线上 data/，
    # 结果是「构建成功、assets 已换、data/site.json 还是旧的」——新增的 Gemini 4 Hydrogen
    # 与 Claude Fable 5.2 在线上完全看不到（线上 381 点 vs dist 383 点）。
    # 实测 dist/data 与线上 data 的文件清单完全一致（各 125 个），同步不会丢文件，
    # 故这里不再保护 data/，让 nginx 的 /data/ 与构建产物保持同一份事实。
    removed = sync_dist_to_deploy(WEB_DIR / "dist", DEPLOY_DIR, protected=())
    if removed:
        preview = ", ".join(removed[:5]) + ("…" if len(removed) > 5 else "")
        _info(f"🧹 清理 {len(removed)} 个旧构建残留：{preview}")
    _info("✅ 数据与网站更新部署成功！(https://real-plan.fja.su)")

def _crop_chart_png(path, y1, y2):
    """裁掉顶部重复导航与底部留白，突出高密图表与前沿卡片（2x HiDPI 坐标）"""
    try:
        from PIL import Image
        im = Image.open(path)
        w, h = im.size
        if w >= 2400 and h >= y2:
            crop = im.crop((100, y1, w - 100, min(h, y2)))
            crop.save(path, optimize=True)
    except Exception as e:
        _warn(f"图表裁剪跳过 ({path}): {e}")


def _get_aa_floor_param():
    """读取 site.json 中 Qwen3.8 27B 的最高 AA 智力分（默认 20.1），用于 Task Time 与 3D 下限过滤"""
    try:
        site_path = WEB_DIR / "public/data/site.json"
        if site_path.is_file():
            data = json.loads(site_path.read_text(encoding="utf-8"))
            cfgs = {c["configuration_id"]: c for c in data.get("configurations", [])}
            pt_mod = {p["id"]: p.get("model") for p in data.get("points", [])}
            best = None
            for m in data.get("mappings", []):
                if pt_mod.get(m.get("point_id")) != "qwen3.8-27b":
                    continue
                c = cfgs.get(m.get("configuration_id"))
                if not c or c.get("board") != "aa_intelligence_index":
                    continue
                sc = m.get("score") if m.get("score") is not None else c.get("score")
                if isinstance(sc, (int, float)) and (best is None or sc > best):
                    best = float(sc)
            if best is not None:
                import math as _math
                return f"{_math.floor(float(best) * 100.0) / 100.0:.2f}"
    except Exception:
        pass
    return "20.10"


def capture_chart(output_path=SCREENSHOT_PATH):
    """使用 headless chromium 截取 Cost / Task Time / 3D Pareto 三张最新图表并发布"""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    aa_floor = _get_aa_floor_param()
    specs = [
        ("Cost per task", f"https://real-plan.fja.su/?v={ts}#lang=zh&config=all&xmode=cost",
         Path(output_path), "cost-per-task-latest.png", 730, 2390),
        ("Task time", f"https://real-plan.fja.su/?v={ts}#lang=zh&config=all&xmode=time&range=~~{aa_floor}~~~",
         TIME_SCREENSHOT_PATH, "task-time-latest.png", 730, 2390),
        ("3D Pareto", f"https://real-plan.fja.su/?v={ts}#lang=zh&config=all&xmode=terrain3d&eng=three",
         PARETO_3D_SCREENSHOT_PATH, "pareto-3d-latest.png", 730, 2550),
    ]
    for label, url, dest, share_name, y1, y2 in specs:
        _info(f"正在截取 {label} 图表 -> {dest}...")
        cmd = (
            f"chromium --headless --no-sandbox --disable-gpu "
            f"--force-device-scale-factor=2 --enable-unsafe-swiftshader "
            f"--window-size=1440,1360 --virtual-time-budget=6500 "
            f"--screenshot={dest} '{url}'"
        )
        run_cmd(cmd)
        _crop_chart_png(dest, y1, y2)
        _info(f"正在通过 hermes-share 发布 {share_name}...")
        try:
            share_out = run_cmd(f"/root/.local/bin/hermes-share {dest} --name {share_name}")
            _info(share_out)
        except Exception as e:
            print(f"hermes-share 警告 ({share_name}): {e}", file=sys.stderr)
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
    import re as _re
    site_json = load_site_json()
    points = site_json["points"]
    configs = {c["configuration_id"]: c for c in site_json["configurations"]}

    by_point_merged = {}
    max_aa_by_model = {}
    point_model_map = {p["id"]: p.get("model", "") for p in points}
    for m in site_json["mappings"]:
        c = configs.get(m["configuration_id"])
        if not c or c.get("board") != "aa_intelligence_index":
            continue
        merged = dict(c)
        for k, v in m.items():
            if v is not None:
                merged[k] = v
        by_point_merged.setdefault(m["point_id"], []).append(merged)
        sc = merged.get("score")
        mod = point_model_map.get(m["point_id"])
        if isinstance(sc, (int, float)) and mod:
            if mod not in max_aa_by_model or sc > max_aa_by_model[mod]:
                max_aa_by_model[mod] = sc

    def _is_visible_default(p):
        if p.get("deprecated"):
            return False
        if isinstance(p.get("hiddenByDefault"), bool) and p["hiddenByDefault"]:
            return False
        if p.get("plan_gen") in ("v1", "v2"):
            return False
        if p.get("model") == "claude-haiku-5.5" and "over100k" not in p.get("id", ""):
            return False
        return True

    def _point_task_cost(p, cfg):
        if str(p.get("plan_id", "")).startswith("hyperqwen_local_"):
            return None
        cost_v = cfg.get("mean_cost_usd_per_task")
        lp = cfg.get("benchmark_list_price") or p.get("list_blended_usd_per_mtok")
        real_p = p.get("real_usd_per_mtok")
        if isinstance(cost_v, (int, float)) and lp and lp > 0 and real_p is not None:
            return cost_v * (real_p / lp)
        if real_p is not None and real_p > 0:
            return 3.51759 * real_p
        return None

    def _effort_tag(cfg):
        eff = cfg.get("reasoning_effort")
        if eff:
            return eff
        m = _re.search(r"\((non-reasoning|minimal|low|medium|high|xhigh|max|none|thinking)\)\s*$", cfg.get("variant") or "", _re.I)
        return m.group(1).lower() if m else None

    def _model_label(base_name, cfg, multi_cfg=True):
        tag = _effort_tag(cfg)
        if multi_cfg and tag and not _re.search(rf"\({_re.escape(tag)}\)\s*$", base_name, _re.I):
            return f"{base_name} ({tag})"
        return base_name

    # 计算 Cost per Task & Real Price 前沿 (config=all 模式，与站点默认视图一致)
    cost_rows = []
    price_rows = []

    for p in points:
        if not _is_visible_default(p):
            continue
        maps = by_point_merged.get(p["id"], [])
        if not maps:
            continue
        multi_cfg = len(maps) > 1
        for cfg in maps:
            score = cfg.get("score")
            if score is None:
                continue
            task_cost = _point_task_cost(p, cfg)
            disp_model = _model_label(p["model_display"], cfg, multi_cfg=multi_cfg)
            if task_cost is not None:
                cost_rows.append({
                    "id": p["id"],
                    "variant": cfg.get("variant") or "",
                    "model": disp_model,
                    "plan": p["plan"],
                    "vendor": p["vendor"],
                    "fee": p.get("price_usd"),
                    "score": score,
                    "cost": task_cost,
                    "real_price": p["real_usd_per_mtok"]
                })

            if p["real_usd_per_mtok"] is not None:
                price_rows.append({
                    "id": p["id"],
                    "variant": cfg.get("variant") or "",
                    "model": disp_model,
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

    points_by_id = {
        p["id"]: {
            "id": p["id"],
            "model": p.get("model_display") or p.get("model") or p["id"],
            "plan": p.get("plan") or "",
            "vendor": p.get("vendor") or "",
            "fee": p.get("price_usd"),
            "real_price": p.get("real_usd_per_mtok"),
        }
        for p in points
    }

    # --- Task Time 前沿与 3D 帕累托非支配前沿（config=all，与 web/src/domain.ts & Chart3D.tsx 同口径）---
    aa_floor_3d = max_aa_by_model.get("qwen3.8-27b", 20.1069)

    time_rows = []
    tier_counts = {}
    for p in points:
        if not _is_visible_default(p):
            continue
        mod_id = p.get("model")
        if mod_id in max_aa_by_model and max_aa_by_model[mod_id] < aa_floor_3d:
            continue
        maps = by_point_merged.get(p["id"], [])
        if not maps:
            continue
        multi_cfg = len(maps) > 1
        for cfg in maps:
            sc = cfg.get("score")
            t_sec = cfg.get("response_time_seconds")
            if not isinstance(sc, (int, float)) or sc < aa_floor_3d or not isinstance(t_sec, (int, float)) or t_sec <= 0:
                continue
            t_key = (round(float(t_sec), 2), round(float(sc), 4))
            tier_counts[t_key] = tier_counts.get(t_key, 0) + 1
            tc = _point_task_cost(p, cfg)
            raw_name = p.get("model_display") or p.get("model") or p["id"]
            time_rows.append({
                "id": p["id"],
                "variant": cfg.get("variant") or "",
                "model": _model_label(raw_name, cfg, multi_cfg=multi_cfg),
                "plan": p.get("plan") or "",
                "vendor": p.get("vendor") or "",
                "fee": p.get("price_usd"),
                "score": sc,
                "time_s": float(t_sec),
                "tps": cfg.get("output_speed_tps"),
                "cost": tc,
                "real_price": p.get("real_usd_per_mtok"),
                "_t_key": t_key,
            })

    time_rows.sort(key=lambda r: (
        r["time_s"],
        -r["score"],
        r["cost"] if r["cost"] is not None else 1e9,
        r["real_price"] if r["real_price"] is not None else 1e9,
    ))
    time_frontier = []
    best_score = -1
    for r in time_rows:
        if r["score"] > best_score:
            item = dict(r)
            item["same_speed_plans"] = tier_counts.get(item.pop("_t_key"), 1)
            time_frontier.append(item)
            best_score = r["score"]

    # 3D 帕累托非支配前沿 (与 Chart3D.tsx default3dPointIds + per-config score>=aaFloor + dedupeByModel 一致)
    family_noise = _re.compile(
        r"\s*\((?:v\d+|max|xhigh|high|medium|low)\)\s*$|\s+Contributor\s*$|\s+Eco\s*$",
        _re.I,
    )
    by_family_3d = {}
    for p in points:
        if not _is_visible_default(p):
            continue
        if str(p.get("plan_id", "")).startswith("hyperqwen_local_"):
            continue
        mod_id = p.get("model")
        if mod_id in max_aa_by_model and max_aa_by_model[mod_id] < aa_floor_3d:
            continue
        maps = by_point_merged.get(p["id"], [])
        if not maps:
            continue
        for cfg in maps:
            sc = cfg.get("score")
            t_sec = cfg.get("response_time_seconds")
            tc = _point_task_cost(p, cfg)
            if (
                not isinstance(sc, (int, float))
                or sc < aa_floor_3d
                or tc is None
                or tc <= 0
                or not isinstance(t_sec, (int, float))
                or t_sec <= 0
            ):
                continue
            raw_name = p.get("model_display") or p.get("model") or p["id"]
            fam = family_noise.sub("", raw_name).strip()
            cfg_id = cfg.get("configuration_id") or cfg.get("variant") or p.get("model")
            tag = _effort_tag(cfg)
            node_key = (fam, cfg_id)
            cand = {
                "id": p["id"],
                "variant": cfg.get("variant") or "",
                "_base_fam": fam,
                "_cfg_id": cfg_id,
                "_effort_tag": tag,
                "family": fam,
                "model": fam,
                "model_display": raw_name,
                "plan": p.get("plan") or "",
                "vendor": p.get("vendor") or "",
                "fee": p.get("price_usd"),
                "cost": float(tc),
                "time_s": float(t_sec),
                "tps": cfg.get("output_speed_tps"),
                "score": float(sc),
                "is_cost_frontier": False,
                "is_time_frontier": False,
                "is_dual_winner": False,
            }
            prev = by_family_3d.get(node_key)
            if prev is None or cand["cost"] < prev["cost"]:
                by_family_3d[node_key] = cand

    nodes_3d = list(by_family_3d.values())
    fam_cfg_sets = {}
    for n in nodes_3d:
        fam_cfg_sets.setdefault(n["_base_fam"], set()).add(n["_cfg_id"])
    for n in nodes_3d:
        if len(fam_cfg_sets.get(n["_base_fam"], ())) > 1 and n.get("_effort_tag"):
            lbl = f"{n['_base_fam']} ({n['_effort_tag']})"
            n["family"] = lbl
            n["model"] = lbl
        n.pop("_base_fam", None)
        n.pop("_cfg_id", None)
        n.pop("_effort_tag", None)
    # 标注 3D 节点在 2D 成本 / 2D 耗时投影上的角色
    best_c = -1e9
    for n in sorted(nodes_3d, key=lambda x: (x["cost"], -x["score"])):
        if n["score"] > best_c:
            n["is_cost_frontier"] = True
            best_c = n["score"]
    best_t = -1e9
    for n in sorted(nodes_3d, key=lambda x: (x["time_s"], -x["score"])):
        if n["score"] > best_t:
            n["is_time_frontier"] = True
            best_t = n["score"]
    for n in nodes_3d:
        n["is_dual_winner"] = n["is_cost_frontier"] and n["is_time_frontier"]

    frontier_3d = []
    for a in nodes_3d:
        dominated = any(
            b is not a
            and b["cost"] <= a["cost"]
            and b["time_s"] <= a["time_s"]
            and b["score"] >= a["score"]
            and (b["cost"] < a["cost"] or b["time_s"] < a["time_s"] or b["score"] > a["score"])
            for b in nodes_3d
        )
        if not dominated:
            frontier_3d.append(a)
    frontier_3d.sort(key=lambda x: x["cost"])

    return {
        "date": site_json.get("generatedAt", datetime.now().strftime("%Y-%m-%d")),
        "total_points": len(points),
        "aa_floor": aa_floor_3d,
        "cost_frontier": cost_frontier,
        "price_frontier": price_frontier,
        "time_frontier": time_frontier,
        "frontier_3d": frontier_3d,
        "points_by_id": points_by_id,
    }

def frontier_state(report):
    """把前沿压成可比较的状态指纹（cost 前沿逐配置 + price 前沿逐配置）。"""
    return {
        "date": report["date"],
        "total_points": report["total_points"],
        "cost_frontier": [
            {
                "id": r["id"],
                **({"variant": r["variant"]} if r.get("variant") else {}),
                "score": round(float(r["score"]), 4),
                "cost": round(float(r["cost"]), 6),
            }
            for r in report["cost_frontier"]
        ],
        "price_frontier": [
            {
                "id": r["id"],
                **({"variant": r["variant"]} if r.get("variant") else {}),
                "score": round(float(r["score"]), 4),
                "real_price": r["real_price"],
            }
            for r in report["price_frontier"]
        ],
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


def _state_item_key(r):
    return (r["id"], r.get("variant") or "")


def _frontier_delta(old_list, new_list, frontier):
    """单个前沿的 added / removed / score_changed，每项带 frontier 归属。"""
    o = {_state_item_key(r): r for r in old_list}
    n = {_state_item_key(r): r for r in new_list}
    added = [dict(n[k], frontier=frontier) for k in n if k not in o]
    removed = [dict(o[k], frontier=frontier) for k in o if k not in n]
    score_changed = [
        {"id": n[k]["id"], **({"variant": n[k]["variant"]} if n[k].get("variant") else {}),
         "from": o[k]["score"], "to": n[k]["score"], "frontier": frontier}
        for k in n if k in o and o[k]["score"] != n[k]["score"]
    ]
    return added, removed, score_changed


def _merge_frontier_items(items):
    """同一点同时在 cost / price 前沿变化时合并为一条（frontier 记 cost+price），
    避免邮件里同一 (id, variant) 重复出现；其余保持单值 cost / price。"""
    merged, order = {}, []
    for it in items:
        k = _state_item_key(it)
        if k not in merged:
            merged[k] = dict(it)
            merged[k]["frontier"] = [it["frontier"]]
            order.append(k)
            continue
        cur = merged[k]
        if it["frontier"] not in cur["frontier"]:
            cur["frontier"].append(it["frontier"])
        for f in ("score", "cost", "real_price", "from", "to", "variant"):
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


def _frontier_label_zh(frontier):
    mapping = {
        "cost": "任务成本前沿",
        "price": "真实单价前沿",
        "cost+price": "双前沿",
    }
    return mapping.get(frontier, str(frontier or "前沿"))


def _resolve_point_meta(point_id, points_by_id=None):
    meta = (points_by_id or {}).get(point_id)
    if meta:
        return meta
    if "::" in str(point_id):
        plan_part, model_part = str(point_id).split("::", 1)
        return {"id": point_id, "model": model_part, "plan": plan_part, "fee": None}
    return {"id": point_id, "model": str(point_id), "plan": "", "fee": None}


def _humanize_note_text(text, points_by_id=None):
    """Replace raw `plan_id::model` tokens in prose with readable `Model (Plan)` labels."""
    import re
    if not points_by_id:
        return text

    def _repl(m):
        pid = m.group(0)
        meta = points_by_id.get(pid)
        if not meta:
            return pid
        plan = meta.get("plan") or ""
        model = meta.get("model") or pid
        return f"{model}（{plan}）" if plan else model

    return re.sub(r"\b[a-z0-9_]+::[a-z0-9._-]+\b", _repl, text)


def _split_note_clauses(body, points_by_id=None):
    """Split newline or `1) ...; 2) ...` note paragraphs into clean bullet items."""
    import re
    cleaned = _humanize_note_text(body.strip(), points_by_id)
    if "\n" in cleaned:
        lines = [ln.strip().lstrip("•·- ").strip() for ln in cleaned.splitlines() if ln.strip()]
        if lines:
            return lines
    cleaned = re.sub(r"。\s*(触发原因[：:])", r"；\1", cleaned)
    cleaned = re.sub(r"([：:])\s*(?=1\)\s)", r"\1；", cleaned)
    parts = re.split(r"(?:^|[；;]\s*)(?=(?:\d+\)|触发原因[：:]))", cleaned)
    items = []
    for part in parts:
        p = re.sub(r"^\d+\)\s*", "", part).strip().rstrip("；;")
        if p:
            items.append(p)
    return items if len(items) > 1 else [cleaned]


def _parse_notes_sections(notes, points_by_id=None):
    """Parse `【Title】Body` notes into structured (kind, title, items) tuples."""
    import re
    summary_cards = []
    detail_cards = []
    for raw in notes or []:
        m = re.match(r"^【([^】]+)】\s*(.*)$", raw.strip(), re.S)
        if m:
            title, body = m.group(1).strip(), m.group(2).strip()
        else:
            title, body = "分析说明", raw.strip()
        items = _split_note_clauses(body, points_by_id)
        entry = {"title": title, "items": items}
        if any(k in title for k in ("归因", "变动", "解读", "选型", "速览", "掉出", "离开")):
            summary_cards.append(entry)
        else:
            detail_cards.append(entry)
    return summary_cards, detail_cards


def _render_note_item_html(text):
    import html as _html, re
    esc = _html.escape(text)
    # Highlight countdown tags such as T-2, T-5, T-10
    esc = re.sub(
        r"\((T-\d+)\)",
        r'<span style="display:inline-block;padding:1px 6px;margin:0 2px;border-radius:4px;background:#fff8e8;border:1px solid #eedcae;color:#7a5b12;font-size:11px;font-weight:700;vertical-align:baseline;">\1</span>',
        esc,
    )
    # Bold concise lead-in labels before colon (avoid URLs like https:)
    m = re.match(r"^([^：:\n]{2,28})([：:])\s*(.+)$", esc)
    if m and not m.group(1).endswith(("http", "https")):
        return f'<strong style="color:#16191d;font-weight:600;">{m.group(1)}{m.group(2)}</strong> {m.group(3)}'
    return esc


def _num_suffix(item, score_label="分数"):
    """把变动项可用的数值拼成「（分数 x，任务成本 $y，真实单价 $z）」。"""
    parts = []
    if item.get("score") is not None:
        parts.append(f"{score_label} {item['score']:.2f}")
    if item.get("cost") is not None:
        parts.append(f"任务成本 ${item['cost']:.5f}")
    if item.get("real_price") is not None:
        parts.append(f"真实单价 ${item['real_price']:.6f}")
    return f"（{'，'.join(parts)}）" if parts else ""


def _diff_block(diff, points_by_id=None):
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
        meta = _resolve_point_meta(r["id"], points_by_id)
        m_name = r.get("variant") or meta["model"]
        label = f"{m_name} · {meta['plan']}" if meta.get("plan") else r["id"]
        lines.append(f"· 新进前沿 [{_frontier_label_zh(r.get('frontier'))}]：{label}{_num_suffix(r)}")
    for r in diff.get("removed", []):
        meta = _resolve_point_meta(r["id"], points_by_id)
        m_name = r.get("variant") or meta["model"]
        label = f"{m_name} · {meta['plan']}" if meta.get("plan") else r["id"]
        lines.append(f"· 掉出前沿 [{_frontier_label_zh(r.get('frontier'))}]：{label}{_num_suffix(r, '原分数')}")
    for r in diff.get("score_changed", []):
        meta = _resolve_point_meta(r["id"], points_by_id)
        m_name = r.get("variant") or meta["model"]
        label = f"{m_name} · {meta['plan']}" if meta.get("plan") else r["id"]
        lines.append(f"· 分数变化 [{_frontier_label_zh(r.get('frontier'))}]：{label} {r['from']:.2f} → {r['to']:.2f}")
    for note in diff.get("notes", []):
        lines.append(f"· {_humanize_note_text(note, points_by_id)}")
    for key in diff.get("degraded", []):
        lines.append(f"· {HUMAN_TAG} 基线缺少 {key}（旧格式），本轮跳过该前沿比较")
    return "\n".join(lines)


def _diff_metrics_html(item, score_label="AA 智力"):
    parts = []
    if item.get("score") is not None:
        parts.append(f'{score_label} <strong style="color:#16191d;">{item["score"]:.2f}</strong>')
    if item.get("cost") is not None:
        parts.append(f'单任务 <strong style="color:#0b7a55;">${item["cost"]:.5f}</strong>')
    if item.get("real_price") is not None:
        parts.append(f'真实单价 <strong style="color:#0b7a55;">${item["real_price"]:.6f}</strong>/MTok')
    return " &nbsp;&middot;&nbsp; ".join(parts)


def _diff_block_html(diff, points_by_id=None, summary_cards=None):
    """HTML 通道的「本轮核心变动」区（warm-paper / emerald 排版，零 emoji 方块）。"""
    import html as _html
    if not diff or not diff.get("changed"):
        return ""
    if diff.get("baseline"):
        return (
            '<div style="margin:22px 0 26px;padding:16px 18px;background:#fafaf7;border:1px solid #e4e0d6;border-left:4px solid #0b7a55;border-radius:10px;">'
            '<div style="font-size:12px;font-weight:700;letter-spacing:0.06em;color:#0b7a55;text-transform:uppercase;margin-bottom:6px;">BASELINE SNAPSHOT</div>'
            '<div style="font-size:16px;font-weight:700;color:#16191d;margin-bottom:4px;">首次基线已建立</div>'
            f'<div style="font-size:13.5px;color:#3a4048;line-height:1.6;">已收录前沿点 <strong>{len(diff.get("added", []))}</strong> 个，完整清单见下方榜单。</div>'
            '</div>'
        )

    alert = bool(diff.get("alert"))
    accent = "#b45309" if alert else "#0b7a55"
    soft_bg = "#fffcf4" if alert else "#f4faf7"
    soft_border = "#eedcae" if alert else "#b6dccb"
    pill_bg = "#fff8e8" if alert else "#e7f4ee"
    pill_fg = "#7a5b12" if alert else "#0b7a55"
    pill_text = "前沿变动触发" if alert else "数据更新"
    section_title = "本轮核心变动" if alert else "本轮数据同步摘要"

    item_rows = []
    if diff.get("total_points_delta"):
        item_rows.append(
            f'<tr><td style="padding:10px 12px;border-bottom:1px solid #f0ece2;font-size:13px;color:#3a4048;" colspan="2">'
            f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;background:#f2f1ec;color:#3a4048;font-size:11.5px;font-weight:600;margin-right:8px;">点位规模</span>'
            f'总收录点数变动 <strong style="color:#16191d;">{diff["total_points_delta"]:+d}</strong>'
            f'{"（前沿结构未变）" if not alert else ""}</td></tr>'
        )

    for r in diff.get("added", []):
        meta = _resolve_point_meta(r["id"], points_by_id)
        m_name = r.get("variant") or meta["model"]
        fee_txt = f"${meta['fee']}/mo" if meta.get("fee") is not None else "按量"
        item_rows.append(
            '<tr>'
            '<td class="diff-cell-l" style="padding:11px 12px;border-bottom:1px solid #f0ece2;vertical-align:middle;">'
            '<span style="display:inline-block;padding:2px 8px;border-radius:999px;background:#e7f4ee;border:1px solid #b6dccb;color:#0b7a55;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;white-space:nowrap;">新进前沿</span>'
            f'<strong style="font-size:14px;color:#16191d;vertical-align:middle;">{_html.escape(m_name)}</strong>'
            f'<div style="font-size:12px;color:#5c646e;margin-top:3px;">{_html.escape(meta["plan"])} &middot; {fee_txt} &middot; {_html.escape(_frontier_label_zh(r.get("frontier")))}</div>'
            '</td>'
            f'<td class="diff-cell-r" style="padding:11px 12px;border-bottom:1px solid #f0ece2;text-align:right;vertical-align:middle;font-size:12.5px;color:#3a4048;font-variant-numeric:tabular-nums;white-space:nowrap;">{_diff_metrics_html(r)}</td>'
            '</tr>'
        )

    for r in diff.get("removed", []):
        meta = _resolve_point_meta(r["id"], points_by_id)
        m_name = r.get("variant") or meta["model"]
        item_rows.append(
            '<tr>'
            '<td class="diff-cell-l" style="padding:11px 12px;border-bottom:1px solid #f0ece2;vertical-align:middle;">'
            '<span style="display:inline-block;padding:2px 8px;border-radius:999px;background:#fef2f2;border:1px solid #fecaca;color:#991b1b;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;white-space:nowrap;">掉出前沿</span>'
            f'<strong style="font-size:14px;color:#16191d;vertical-align:middle;">{_html.escape(m_name)}</strong>'
            f'<div style="font-size:12px;color:#5c646e;margin-top:3px;">{_html.escape(meta["plan"])} &middot; {_html.escape(_frontier_label_zh(r.get("frontier")))}</div>'
            '</td>'
            f'<td class="diff-cell-r" style="padding:11px 12px;border-bottom:1px solid #f0ece2;text-align:right;vertical-align:middle;font-size:12.5px;color:#5c646e;font-variant-numeric:tabular-nums;white-space:nowrap;">{_diff_metrics_html(r, "原 AA 智力")}</td>'
            '</tr>'
        )

    for r in diff.get("score_changed", []):
        meta = _resolve_point_meta(r["id"], points_by_id)
        m_name = r.get("variant") or meta["model"]
        item_rows.append(
            '<tr>'
            '<td class="diff-cell-l" style="padding:11px 12px;border-bottom:1px solid #f0ece2;vertical-align:middle;">'
            '<span style="display:inline-block;padding:2px 8px;border-radius:999px;background:#fff8e8;border:1px solid #eedcae;color:#7a5b12;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;white-space:nowrap;">分数变动</span>'
            f'<strong style="font-size:14px;color:#16191d;vertical-align:middle;">{_html.escape(m_name)}</strong>'
            f'<div style="font-size:12px;color:#5c646e;margin-top:3px;">{_html.escape(meta["plan"])} &middot; {_html.escape(_frontier_label_zh(r.get("frontier")))}</div>'
            '</td>'
            f'<td class="diff-cell-r" style="padding:11px 12px;border-bottom:1px solid #f0ece2;text-align:right;vertical-align:middle;font-size:12.5px;color:#16191d;font-variant-numeric:tabular-nums;white-space:nowrap;"><strong>{r["from"]:.2f}</strong> &rarr; <strong style="color:#0b7a55;">{r["to"]:.2f}</strong></td>'
            '</tr>'
        )

    for key in diff.get("degraded", []):
        item_rows.append(
            f'<tr><td colspan="2" style="padding:10px 12px;border-bottom:1px solid #f0ece2;font-size:12.5px;color:#991b1b;">'
            f'<strong>{_html.escape(HUMAN_TAG)}</strong> 基线缺少 <code>{_html.escape(str(key))}</code>（旧格式），本轮跳过该前沿比较。</td></tr>'
        )

    items_table = ""
    if item_rows:
        items_table = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="width:100%;border-collapse:collapse;background:#ffffff;border:1px solid #e4e0d6;border-radius:8px;overflow:hidden;margin-top:12px;">'
            + "".join(item_rows) +
            '</table>'
        )

    notes_html = ""
    for card in summary_cards or []:
        li_html = "".join(
            f'<li style="margin:0 0 6px 0;color:#3a4048;font-size:13px;line-height:1.65;">{_render_note_item_html(it)}</li>'
            for it in card["items"]
        )
        notes_html += (
            '<div style="margin-top:12px;padding:12px 14px;background:#ffffff;border:1px solid #ebe6da;border-radius:8px;">'
            f'<div style="font-size:12.5px;font-weight:700;color:#16191d;margin-bottom:6px;">{_html.escape(card["title"])}</div>'
            f'<ul style="margin:0;padding-left:18px;">{li_html}</ul>'
            '</div>'
        )

    return (
        f'<div style="margin:22px 0 26px;padding:18px 20px;background:{soft_bg};border:1px solid {soft_border};border-left:4px solid {accent};border-radius:10px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;"><tr>'
        f'<td style="font-size:16px;font-weight:700;color:#16191d;">{section_title}</td>'
        f'<td style="text-align:right;"><span style="display:inline-block;padding:3px 10px;border-radius:999px;background:{pill_bg};border:1px solid {soft_border};color:{pill_fg};font-size:11.5px;font-weight:700;">{pill_text}</span></td>'
        '</tr></table>'
        f'{items_table}'
        f'{notes_html}'
        '</div>'
    )


def _fmt_duration_short(seconds):
    s = int(round(float(seconds)))
    if s < 60:
        return f"{s}s"
    m, rem = divmod(s, 60)
    return f"{m}m{rem:02d}s"


def _render_frontier_rows_html(rows, value_key, value_fmt, added_keys=None, max_score=70.0):
    import html as _html
    added_keys = added_keys or set()
    if rows:
        top_score = max(max_score, max(float(r.get("score") or 0) for r in rows))
    else:
        top_score = max_score
    out = []
    for i, item in enumerate(rows, 1):
        is_new = _state_item_key(item) in added_keys or (item["id"], "") in added_keys
        row_bg = "#f7fcf9" if is_new else ("#ffffff" if i % 2 == 1 else "#fafaf7")
        rank_bg = "#e7f4ee" if i == 1 else "#f2f1ec"
        rank_fg = "#0b7a55" if i == 1 else "#5c646e"
        fee_str = f"${item['fee']}/mo" if item.get("fee") is not None else "按量"
        val_str = value_fmt(item[value_key])
        score_val = float(item["score"])
        bar_pct = max(8, min(100, round(score_val / top_score * 100)))
        new_badge = (
            '<span style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;'
            'background:#e7f4ee;border:1px solid #b6dccb;color:#0b7a55;font-size:10.5px;font-weight:700;vertical-align:middle;">新进</span>'
            if is_new else ""
        )
        out.append(
            f'<tr style="background:{row_bg};">'
            f'<td class="col-rank" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:center;vertical-align:middle;width:32px;">'
            f'<span style="display:inline-block;min-width:20px;padding:2px 5px;border-radius:6px;background:{rank_bg};color:{rank_fg};font-size:11.5px;font-weight:700;font-variant-numeric:tabular-nums;">{i}</span>'
            f'</td>'
            f'<td class="col-model" style="padding:11px 8px;border-bottom:1px solid #ece8de;vertical-align:middle;">'
            f'<div style="font-size:13.5px;font-weight:700;color:#16191d;line-height:1.35;">{_html.escape(item["model"])}{new_badge}</div>'
            f'<div style="font-size:12px;color:#5c646e;margin-top:2px;line-height:1.35;">{_html.escape(item["plan"])}</div>'
            f'</td>'
            f'<td class="col-fee" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;font-size:12.5px;color:#5c646e;font-variant-numeric:tabular-nums;">{_html.escape(fee_str)}</td>'
            f'<td class="col-val" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;font-size:13.5px;font-weight:700;color:#0b7a55;font-variant-numeric:tabular-nums;">{val_str}</td>'
            f'<td class="col-score" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;width:76px;">'
            f'<div style="font-size:13px;font-weight:700;color:#16191d;font-variant-numeric:tabular-nums;">{score_val:.2f}</div>'
            f'<div class="score-bar" style="margin-top:4px;height:4px;width:58px;margin-left:auto;background:#ebedf0;border-radius:99px;overflow:hidden;">'
            f'<div style="height:4px;width:{bar_pct}%;background:#0b7a55;border-radius:99px;"></div>'
            f'</div>'
            f'</td>'
            f'</tr>'
        )
    return "".join(out)


def _render_time_rows_html(rows, max_score=70.0):
    import html as _html
    top_score = max(max_score, max((float(r.get("score") or 0) for r in rows), default=max_score))
    out = []
    for i, item in enumerate(rows, 1):
        row_bg = "#ffffff" if i % 2 == 1 else "#fafaf7"
        rank_bg = "#e7f4ee" if i == 1 else "#f2f1ec"
        rank_fg = "#0b7a55" if i == 1 else "#5c646e"
        fee_str = f"${item['fee']}/mo" if item.get("fee") is not None else "按量"
        cost_part = f" &middot; 单任务 ${item['cost']:.5f}" if item.get("cost") is not None else ""
        same_n = item.get("same_speed_plans") or 1
        same_part = f" &middot; {same_n}套餐同速" if same_n > 1 else ""
        tps_val = item.get("tps")
        tps_str = f"{round(float(tps_val))} tok/s" if isinstance(tps_val, (int, float)) else "-"
        t_sec = float(item["time_s"])
        dur_sub = _fmt_duration_short(t_sec) if t_sec >= 60 else "极速档"
        score_val = float(item["score"])
        bar_pct = max(8, min(100, round(score_val / top_score * 100)))
        out.append(
            f'<tr style="background:{row_bg};">'
            f'<td class="col-rank" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:center;vertical-align:middle;width:32px;">'
            f'<span style="display:inline-block;min-width:20px;padding:2px 5px;border-radius:6px;background:{rank_bg};color:{rank_fg};font-size:11.5px;font-weight:700;font-variant-numeric:tabular-nums;">{i}</span>'
            f'</td>'
            f'<td class="col-model" style="padding:11px 8px;border-bottom:1px solid #ece8de;vertical-align:middle;">'
            f'<div style="font-size:13.5px;font-weight:700;color:#16191d;line-height:1.35;">{_html.escape(item["model"])}</div>'
            f'<div style="font-size:12px;color:#5c646e;margin-top:2px;line-height:1.35;">{_html.escape(item["plan"])} &middot; {_html.escape(fee_str)}{cost_part}{same_part}</div>'
            f'</td>'
            f'<td class="col-fee" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;font-size:12.5px;color:#5c646e;font-variant-numeric:tabular-nums;">{_html.escape(tps_str)}</td>'
            f'<td class="col-val" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;">'
            f'<div style="font-size:13.5px;font-weight:700;color:#0b7a55;font-variant-numeric:tabular-nums;">{t_sec:.1f}s</div>'
            f'<div style="font-size:11px;color:#5c646e;margin-top:1px;font-variant-numeric:tabular-nums;">{dur_sub}</div>'
            f'</td>'
            f'<td class="col-score" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;width:76px;">'
            f'<div style="font-size:13px;font-weight:700;color:#16191d;font-variant-numeric:tabular-nums;">{score_val:.2f}</div>'
            f'<div class="score-bar" style="margin-top:4px;height:4px;width:58px;margin-left:auto;background:#ebedf0;border-radius:99px;overflow:hidden;">'
            f'<div style="height:4px;width:{bar_pct}%;background:#0b7a55;border-radius:99px;"></div>'
            f'</div>'
            f'</td>'
            f'</tr>'
        )
    return "".join(out)


def _render_3d_rows_html(rows, max_score=70.0):
    import html as _html
    top_score = max(max_score, max((float(r.get("score") or 0) for r in rows), default=max_score))
    out = []
    for i, item in enumerate(rows, 1):
        is_dual = bool(item.get("is_dual_winner"))
        row_bg = "#f7fcf9" if is_dual else ("#ffffff" if i % 2 == 1 else "#fafaf7")
        rank_bg = "#e7f4ee" if is_dual else "#f2f1ec"
        rank_fg = "#0b7a55" if is_dual else "#5c646e"
        fee_str = f"${item['fee']}/mo" if item.get("fee") is not None else "按量"
        if is_dual:
            role_badge = '<span style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;background:#e7f4ee;border:1px solid #b6dccb;color:#0b7a55;font-size:10.5px;font-weight:700;vertical-align:middle;">双前沿</span>'
        elif item.get("is_cost_frontier"):
            role_badge = '<span style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;background:#fff8e8;border:1px solid #eedcae;color:#7a5b12;font-size:10.5px;font-weight:700;vertical-align:middle;">成本</span>'
        elif item.get("is_time_frontier"):
            role_badge = '<span style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;background:#eef4ff;border:1px solid #c7d9fc;color:#1d4ed8;font-size:10.5px;font-weight:700;vertical-align:middle;">耗时</span>'
        else:
            role_badge = '<span style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;background:#f2f1ec;border:1px solid #e4e0d6;color:#5c646e;font-size:10.5px;font-weight:700;vertical-align:middle;">3D折中</span>'
        t_sec = float(item["time_s"])
        dur_sub = _fmt_duration_short(t_sec)
        score_val = float(item["score"])
        bar_pct = max(8, min(100, round(score_val / top_score * 100)))
        out.append(
            f'<tr style="background:{row_bg};">'
            f'<td class="col-rank" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:center;vertical-align:middle;width:32px;">'
            f'<span style="display:inline-block;min-width:20px;padding:2px 5px;border-radius:6px;background:{rank_bg};color:{rank_fg};font-size:11.5px;font-weight:700;font-variant-numeric:tabular-nums;">{i}</span>'
            f'</td>'
            f'<td class="col-model" style="padding:11px 8px;border-bottom:1px solid #ece8de;vertical-align:middle;">'
            f'<div style="font-size:13.5px;font-weight:700;color:#16191d;line-height:1.35;">{_html.escape(item["model"])}{role_badge}</div>'
            f'<div style="font-size:12px;color:#5c646e;margin-top:2px;line-height:1.35;">{_html.escape(item["plan"])} &middot; {_html.escape(fee_str)}</div>'
            f'</td>'
            f'<td class="col-fee" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;font-size:13px;font-weight:700;color:#0b7a55;font-variant-numeric:tabular-nums;">${item["cost"]:.5f}</td>'
            f'<td class="col-val" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;">'
            f'<div style="font-size:13px;font-weight:700;color:#16191d;font-variant-numeric:tabular-nums;">{t_sec:.1f}s</div>'
            f'<div style="font-size:11px;color:#5c646e;margin-top:1px;font-variant-numeric:tabular-nums;">{dur_sub}</div>'
            f'</td>'
            f'<td class="col-score" style="padding:11px 8px;border-bottom:1px solid #ece8de;text-align:right;vertical-align:middle;white-space:nowrap;width:76px;">'
            f'<div style="font-size:13px;font-weight:700;color:#16191d;font-variant-numeric:tabular-nums;">{score_val:.2f}</div>'
            f'<div class="score-bar" style="margin-top:4px;height:4px;width:58px;margin-left:auto;background:#ebedf0;border-radius:99px;overflow:hidden;">'
            f'<div style="height:4px;width:{bar_pct}%;background:#0b7a55;border-radius:99px;"></div>'
            f'</div>'
            f'</td>'
            f'</tr>'
        )
    return "".join(out)


def _render_chart_card_html(img_url, href, caption, alt):
    import html as _html
    return (
        '<div style="margin-top:12px;border:1px solid #e4e0d6;border-radius:10px;overflow:hidden;background:#fafaf7;">'
        f'<a href="{_html.escape(href)}" target="_blank" style="display:block;text-decoration:none;">'
        f'<img src="{_html.escape(img_url)}" alt="{_html.escape(alt)}" style="display:block;width:100%;max-width:100%;height:auto;border:0;" />'
        '</a>'
        '<div style="padding:8px 12px;border-top:1px solid #ece8de;background:#fafaf7;font-size:11.5px;color:#5c646e;">'
        f'{_html.escape(caption)} &middot; <a href="{_html.escape(href)}" target="_blank" style="color:#0b7a55;text-decoration:none;font-weight:600;">在交互看板中打开 &rarr;</a>'
        '</div>'
        '</div>'
    )


def format_email_content(report):
    """格式化简报的纯文本与 HTML 邮件内容（对齐 real-plan.fja.su 编辑部纸感排版）"""
    import html as _html
    date_str = report["date"]
    diff = report.get("diff") or {}
    points_by_id = report.get("points_by_id") or {}
    time_frontier = report.get("time_frontier") or []
    frontier_3d = report.get("frontier_3d") or []
    alert = _is_alert(diff)
    banner = "前沿变动通知" if alert else "前沿简报"

    # 构造纯文本回退版本
    text = f"""=== Real API Pricing {banner} ({date_str}) ===

数据快照：{date_str}
已收录套餐点：{report['total_points']} 个
线上交互看板：https://real-plan.fja.su
{_diff_block(diff, points_by_id)}
--------------------------------------------------
★ 01 3D 帕累托非支配前沿 (Cost × Time × Intelligence 折中视图)
--------------------------------------------------
"""
    for i, item in enumerate(frontier_3d, 1):
        fee_str = f"${item['fee']}/mo" if item['fee'] is not None else "按量"
        role = "双前沿" if item.get("is_dual_winner") else ("成本前沿" if item.get("is_cost_frontier") else ("耗时前沿" if item.get("is_time_frontier") else "3D折中"))
        text += f"{i:2d}. {item['model']} [{role}] ({item['plan']}, {fee_str})\n    - 单任务成本: ${item['cost']:.5f} | 任务用时: {item['time_s']:.2f}s ({_fmt_duration_short(item['time_s'])}) | 智力分数: {item['score']:.2f}\n"

    text += f"""
--------------------------------------------------
★ 02 任务成本前沿 (Cost per Task Frontier)
--------------------------------------------------
计算口径：AA 智力榜任务基准 × (套餐实际单价 / 官方基准标价)
"""
    for i, item in enumerate(report["cost_frontier"], 1):
        fee_str = f"${item['fee']}/mo" if item['fee'] is not None else "按量"
        text += f"{i:2d}. {item['model']} ({item['plan']}, {fee_str})\n    - 任务成本: ${item['cost']:.5f} / 任务 | 智力分数: {item['score']:.2f}\n"

    text += f"""
--------------------------------------------------
★ 03 任务用时前沿 (Task Time Frontier)
--------------------------------------------------
计算口径：OpenRouter P50 延迟 + AA 每任务 Output Token / P50 吞吐
"""
    for i, item in enumerate(time_frontier, 1):
        fee_str = f"${item['fee']}/mo" if item['fee'] is not None else "按量"
        tps_str = f"{round(float(item['tps']))} tok/s" if isinstance(item.get("tps"), (int, float)) else "-"
        cost_str = f"${item['cost']:.5f}" if item.get("cost") is not None else "-"
        text += f"{i:2d}. {item['model']} ({item['plan']}, {fee_str})\n    - 任务用时: {item['time_s']:.2f}s ({_fmt_duration_short(item['time_s'])}) | 吞吐: {tps_str} | 任务成本: {cost_str} | 智力分数: {item['score']:.2f}\n"

    text += f"""
--------------------------------------------------
★ 04 真实单价前沿 (Real Price Frontier · 附表对照)
--------------------------------------------------
"""
    for i, item in enumerate(report["price_frontier"], 1):
        fee_str = f"${item['fee']}/mo" if item['fee'] is not None else "按量"
        text += f"{i:2d}. {item['model']} ({item['plan']}, {fee_str})\n    - 真实单价: ${item['real_price']:.6f} / MTok | 智力分数: {item['score']:.2f}\n"

    text += f"""
--------------------------------------------------
最新图表直链预览：
- 3D Pareto: {PARETO_3D_SCREENSHOT_URL}
- Cost per Task: {SCREENSHOT_URL}
- Task Time: {TIME_SCREENSHOT_URL}

此邮件由 Real API Pricing 自动化监控管道 {FOOTER_NOTE}。

All the best,
Hermes (Real API Pricing Monitor)
"""

    added_cost_keys = {
        _state_item_key(r) for r in diff.get("added", [])
        if r.get("frontier") in ("cost", "cost+price")
    }
    added_price_keys = {
        _state_item_key(r) for r in diff.get("added", [])
        if r.get("frontier") in ("price", "cost+price")
    }
    summary_cards, detail_cards = _parse_notes_sections(diff.get("notes"), points_by_id)

    cost_rows_html = _render_frontier_rows_html(
        report["cost_frontier"], "cost", lambda v: f"${v:.5f}", added_keys=added_cost_keys
    )
    time_rows_html = _render_time_rows_html(time_frontier)
    rows_3d_html = _render_3d_rows_html(frontier_3d)
    price_rows_html = _render_frontier_rows_html(
        report["price_frontier"], "real_price", lambda v: f"${v:.6f}", added_keys=added_price_keys
    )

    aa_floor = report.get("aa_floor")
    if isinstance(aa_floor, (int, float)):
        import math as _math
        aa_floor_param = f"{_math.floor(float(aa_floor) * 100.0) / 100.0:.2f}"
    else:
        aa_floor_param = _get_aa_floor_param()

    cost_chart_html = _render_chart_card_html(
        SCREENSHOT_URL,
        "https://real-plan.fja.su/#lang=zh&config=all&xmode=cost",
        "Cost per Task 帕累托前沿全景图（点击看交互原图）",
        "Real API Pricing Cost per Task Frontier Chart",
    )
    time_chart_html = _render_chart_card_html(
        TIME_SCREENSHOT_URL,
        f"https://real-plan.fja.su/#lang=zh&config=all&xmode=time&range=~~{aa_floor_param}~~~",
        "Task Time 任务用时前沿图（已过滤低于 Qwen3.8 27B 智力门槛的模型，越靠右越快）",
        "Real API Pricing Task Time Frontier Chart",
    )
    pareto_3d_chart_html = _render_chart_card_html(
        PARETO_3D_SCREENSHOT_URL,
        "https://real-plan.fja.su/#lang=zh&config=all&xmode=terrain3d&eng=three",
        "3D 全息徽章舱：金徽章 = 三维非支配最优，橙线 = 成本前沿，蓝线 = 耗时前沿",
        "Real API Pricing 3D Pareto Terrain Chart",
    )

    diff_section_html = _diff_block_html(diff, points_by_id, summary_cards)

    detail_section_html = ""
    if detail_cards:
        cards_html = []
        for card in detail_cards:
            is_risk = any(k in card["title"] for k in ("风险", "预警", "候选"))
            border_color = "#eedcae" if is_risk else "#e4e0d6"
            bg_color = "#fffcf4" if is_risk else "#fafaf7"
            badge_bg = "#fff8e8" if is_risk else "#f2f1ec"
            badge_fg = "#7a5b12" if is_risk else "#5c646e"
            badge_txt = "观察与预警" if is_risk else "口径说明"
            lis = "".join(
                f'<li style="margin:0 0 8px 0;color:#3a4048;font-size:13px;line-height:1.68;">{_render_note_item_html(it)}</li>'
                for it in card["items"]
            )
            cards_html.append(
                f'<div style="margin-top:12px;padding:16px 18px;background:{bg_color};border:1px solid {border_color};border-radius:10px;">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-bottom:8px;"><tr>'
                f'<td style="font-size:14px;font-weight:700;color:#16191d;">{_html.escape(card["title"])}</td>'
                f'<td style="text-align:right;"><span style="display:inline-block;padding:2px 8px;border-radius:999px;background:{badge_bg};color:{badge_fg};font-size:11px;font-weight:600;">{badge_txt}</span></td>'
                '</tr></table>'
                f'<ul style="margin:0;padding-left:18px;">{lis}</ul>'
                '</div>'
            )
        detail_section_html = (
            '<div style="margin-top:28px;">'
            '<div style="margin-bottom:4px;">'
            '<span style="display:inline-block;padding:2px 7px;border-radius:5px;background:#f2f1ec;color:#5c646e;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;">05</span>'
            '<span style="font-size:17px;font-weight:700;color:#16191d;vertical-align:middle;">观察清单、口径备注与到期提醒</span>'
            '</div>'
            '<div style="font-size:12.5px;color:#5c646e;margin-bottom:6px;">暂未入库的候选方案、近期促销到期倒计时，以及影响实际可用性的平台规则动态。</div>'
            + "".join(cards_html) +
            '</div>'
        )

    delta_val = diff.get("total_points_delta") or 0
    delta_chip = (
        f' ({delta_val:+d})'
        if delta_val else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Real API Pricing {banner} ({_html.escape(str(date_str))})</title>
<style>
  @media (max-width: 560px) {{
    .outer-pad {{ padding: 10px 6px !important; }}
    .card-pad {{ padding: 16px 12px !important; }}
    .kpi-cell {{ display: block !important; width: 50% !important; float: left !important; box-sizing: border-box !important; border-right: none !important; border-bottom: 1px solid #e4e0d6 !important; }}
    .cta-cell {{ display: block !important; text-align: left !important; padding-top: 10px !important; }}
    .head-cell {{ display: block !important; padding-bottom: 4px !important; }}
    .diff-cell-l, .diff-cell-r {{ display: block !important; width: 100% !important; box-sizing: border-box !important; text-align: left !important; white-space: normal !important; }}
    .diff-cell-l {{ border-bottom: none !important; padding-bottom: 4px !important; }}
    .diff-cell-r {{ padding-top: 2px !important; padding-bottom: 10px !important; }}
    .frontier-table th, .frontier-table td {{ padding: 8px 4px !important; font-size: 11.5px !important; }}
    .frontier-table .col-rank {{ width: 22px !important; padding-left: 4px !important; padding-right: 2px !important; }}
    .frontier-table .col-score {{ width: 52px !important; padding-right: 6px !important; }}
    .frontier-table .score-bar {{ width: 42px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:#f5f3ed;color:#16191d;font-family:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;-webkit-font-smoothing:antialiased;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;background-color:#f5f3ed;border-collapse:collapse;">
  <tr>
    <td class="outer-pad" align="center" style="padding:28px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:760px;width:100%;background:#ffffff;border:1px solid #e4e0d6;border-radius:14px;border-collapse:separate;overflow:hidden;box-shadow:0 2px 10px rgba(22,25,29,0.04);">
        <tr>
          <td class="card-pad" style="padding:28px 30px;">
            <!-- Masthead -->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;padding-bottom:18px;border-bottom:1px solid #ece8de;">
              <tr>
                <td class="head-cell" style="vertical-align:top;padding-bottom:16px;">
                  <div style="font-size:11px;font-weight:700;letter-spacing:0.09em;color:#0b7a55;text-transform:uppercase;margin-bottom:6px;">REAL API PRICING &middot; FRONTIER MONITOR</div>
                  <h1 style="margin:0;font-size:23px;line-height:1.28;font-weight:700;color:#16191d;letter-spacing:-0.01em;">Real API Pricing {_html.escape(banner)}</h1>
                  <div style="margin-top:6px;font-size:13px;color:#5c646e;">从「单任务成本 &times; 任务用时 &times; 3D 折中」评估真实生产力门槛 &middot; 已收录 <strong>{report['total_points']}</strong> 个方案{delta_chip}</div>
                </td>
                <td class="cta-cell" style="vertical-align:middle;text-align:right;padding-bottom:16px;white-space:nowrap;">
                  <a href="https://real-plan.fja.su" target="_blank" style="display:inline-block;padding:9px 16px;background:#16191d;color:#ffffff;font-size:13px;font-weight:600;text-decoration:none;border-radius:8px;">打开交互看板 &rarr;</a>
                </td>
              </tr>
            </table>

            <!-- KPI Strip -->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;margin-top:18px;background:#fafaf7;border:1px solid #e4e0d6;border-radius:10px;border-collapse:collapse;">
              <tr>
                <td class="kpi-cell" width="25%" style="padding:12px 14px;border-right:1px solid #ece8de;">
                  <div style="font-size:11px;color:#5c646e;margin-bottom:3px;">数据快照</div>
                  <div style="font-size:15px;font-weight:700;color:#16191d;font-variant-numeric:tabular-nums;">{_html.escape(str(date_str))}</div>
                </td>
                <td class="kpi-cell" width="25%" style="padding:12px 14px;border-right:1px solid #ece8de;">
                  <div style="font-size:11px;color:#5c646e;margin-bottom:3px;">3D 非支配前沿</div>
                  <div style="font-size:15px;font-weight:700;color:#0b7a55;font-variant-numeric:tabular-nums;">{len(frontier_3d)} 个模型</div>
                </td>
                <td class="kpi-cell" width="25%" style="padding:12px 14px;border-right:1px solid #ece8de;">
                  <div style="font-size:11px;color:#5c646e;margin-bottom:3px;">任务成本前沿</div>
                  <div style="font-size:15px;font-weight:700;color:#0b7a55;font-variant-numeric:tabular-nums;">{len(report['cost_frontier'])} 个模型</div>
                </td>
                <td class="kpi-cell" width="25%" style="padding:12px 14px;">
                  <div style="font-size:11px;color:#5c646e;margin-bottom:3px;">任务用时前沿</div>
                  <div style="font-size:15px;font-weight:700;color:#0b7a55;font-variant-numeric:tabular-nums;">{len(time_frontier)} 个模型</div>
                </td>
              </tr>
            </table>

            <!-- Diff Summary -->
            {diff_section_html}

            <!-- Section 01: 3D Pareto Compromise Frontier + Chart -->
            <div style="margin-top:26px;">
              <div style="margin-bottom:4px;">
                <span style="display:inline-block;padding:2px 7px;border-radius:5px;background:#e7f4ee;color:#0b7a55;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;">01</span>
                <span style="font-size:17px;font-weight:700;color:#16191d;vertical-align:middle;">3D 帕累托非支配前沿 (Cost &times; Time &times; 智力折中)</span>
              </div>
              <div style="font-size:12.5px;color:#5c646e;margin-bottom:12px;">同时权衡「单任务成本」「任务用时」与「智力分数」的三维非支配解集（已排除低于 Qwen3.8 27B 门槛的超小模型）；绿色高亮为同时站稳成本与耗时双前沿的交汇方案。</div>
              <table class="frontier-table" role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #e4e0d6;border-radius:10px;overflow:hidden;">
                <thead>
                  <tr style="background:#fafaf7;">
                    <th class="col-rank" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:center;font-size:11.5px;font-weight:600;color:#5c646e;width:32px;">#</th>
                    <th class="col-model" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:left;font-size:11.5px;font-weight:600;color:#5c646e;">模型 / 最优套餐</th>
                    <th class="col-fee" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">单任务成本</th>
                    <th class="col-val" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">任务用时</th>
                    <th class="col-score" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;width:76px;">AA 智力分</th>
                  </tr>
                </thead>
                <tbody>
                  {rows_3d_html}
                </tbody>
              </table>
              {pareto_3d_chart_html}
            </div>

            <!-- Section 02: Cost per Task Frontier + Chart -->
            <div style="margin-top:30px;">
              <div style="margin-bottom:4px;">
                <span style="display:inline-block;padding:2px 7px;border-radius:5px;background:#e7f4ee;color:#0b7a55;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;">02</span>
                <span style="font-size:17px;font-weight:700;color:#16191d;vertical-align:middle;">任务成本前沿 (Cost per Task)</span>
              </div>
              <div style="font-size:12.5px;color:#5c646e;margin-bottom:12px;">按 Artificial Analysis 智力基准的单任务 Token 消耗折算真实费用，直接反映完成一次标准任务的实际开销（比单纯看 MTok 单价更贴近生产力负载）。</div>
              <table class="frontier-table" role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #e4e0d6;border-radius:10px;overflow:hidden;">
                <thead>
                  <tr style="background:#fafaf7;">
                    <th class="col-rank" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:center;font-size:11.5px;font-weight:600;color:#5c646e;width:32px;">#</th>
                    <th class="col-model" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:left;font-size:11.5px;font-weight:600;color:#5c646e;">模型 / 套餐</th>
                    <th class="col-fee" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">月费</th>
                    <th class="col-val" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">单任务成本</th>
                    <th class="col-score" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;width:76px;">AA 智力分</th>
                  </tr>
                </thead>
                <tbody>
                  {cost_rows_html}
                </tbody>
              </table>
              {cost_chart_html}
            </div>

            <!-- Section 03: Task Time Frontier + Chart -->
            <div style="margin-top:30px;">
              <div style="margin-bottom:4px;">
                <span style="display:inline-block;padding:2px 7px;border-radius:5px;background:#e7f4ee;color:#0b7a55;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;">03</span>
                <span style="font-size:17px;font-weight:700;color:#16191d;vertical-align:middle;">任务用时前沿 (Task Time)</span>
              </div>
              <div style="font-size:12.5px;color:#5c646e;margin-bottom:12px;">结合 OpenRouter 实时 P50 首字延迟、生成吞吐与每任务输出长度测算（已排除低于 Qwen3.8 27B 门槛的超小模型）；同速模型列示单任务成本最优方案。</div>
              <table class="frontier-table" role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #e4e0d6;border-radius:10px;overflow:hidden;">
                <thead>
                  <tr style="background:#fafaf7;">
                    <th class="col-rank" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:center;font-size:11.5px;font-weight:600;color:#5c646e;width:32px;">#</th>
                    <th class="col-model" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:left;font-size:11.5px;font-weight:600;color:#5c646e;">模型 / 最优套餐</th>
                    <th class="col-fee" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">吞吐</th>
                    <th class="col-val" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">任务用时</th>
                    <th class="col-score" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;width:76px;">AA 智力分</th>
                  </tr>
                </thead>
                <tbody>
                  {time_rows_html}
                </tbody>
              </table>
              {time_chart_html}
            </div>

            <!-- Section 04: Real Price Frontier (Reference) -->
            <div style="margin-top:30px;">
              <div style="margin-bottom:4px;">
                <span style="display:inline-block;padding:2px 7px;border-radius:5px;background:#f2f1ec;color:#5c646e;font-size:11px;font-weight:700;margin-right:8px;vertical-align:middle;">04</span>
                <span style="font-size:17px;font-weight:700;color:#16191d;vertical-align:middle;">真实单价前沿 (Real Price / MTok &middot; 附表对照)</span>
              </div>
              <div style="font-size:12.5px;color:#5c646e;margin-bottom:12px;">以订阅月费除以每月实测可用 Token 估算的单位吞吐下限，仅作纯 Token 价格对照参考（未计入模型单任务输出长度差异）。</div>
              <table class="frontier-table" role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #e4e0d6;border-radius:10px;overflow:hidden;">
                <thead>
                  <tr style="background:#fafaf7;">
                    <th class="col-rank" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:center;font-size:11.5px;font-weight:600;color:#5c646e;width:32px;">#</th>
                    <th class="col-model" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:left;font-size:11.5px;font-weight:600;color:#5c646e;">模型 / 套餐</th>
                    <th class="col-fee" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">月费</th>
                    <th class="col-val" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;">真实单价/MTok</th>
                    <th class="col-score" style="padding:9px 8px;border-bottom:1px solid #e4e0d6;text-align:right;font-size:11.5px;font-weight:600;color:#5c646e;width:76px;">AA 智力分</th>
                  </tr>
                </thead>
                <tbody>
                  {price_rows_html}
                </tbody>
              </table>
            </div>

            <!-- Section 05: Details & Risk Watch -->
            {detail_section_html}

            <!-- Footer -->
            <div style="margin-top:30px;padding-top:16px;border-top:1px solid #ece8de;font-size:12px;color:#6b737e;line-height:1.65;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
                <tr>
                  <td>
                    此邮件由 <strong style="color:#3a4048;">Real API Pricing 自动化监控管道</strong>（{_html.escape(FOOTER_NOTE)}）生成。<br/>
                    数据源自独立基准榜单及官方/实测套餐规格测算 &middot; 线上看板：<a href="https://real-plan.fja.su" style="color:#0b7a55;text-decoration:none;font-weight:600;">real-plan.fja.su</a>
                  </td>
                  <td style="text-align:right;vertical-align:bottom;white-space:nowrap;color:#5c646e;">
                    Hermes Agent
                  </td>
                </tr>
              </table>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""

    return text, html

def attach_email_analysis_notes(diff, report=None):
    """生成面向人的简报解读与外部观察笔记（避免内部变量名、文件名与 CI 术语堆砌）。"""
    if not diff:
        return
    notes = diff.setdefault("notes", [])
    if notes:
        return

    points_by_id = (report or {}).get("points_by_id") or {}
    added = diff.get("added") or []
    removed = diff.get("removed") or []
    cost_f = (report or {}).get("cost_frontier") or []
    f3d = (report or {}).get("frontier_3d") or []

    # 1. 本轮变动解读（自然语言，说明改了什么、为什么变、对主力选型的影响）
    change_lines = []
    if added:
        added_names = []
        for r in added:
            meta = _resolve_point_meta(r["id"], points_by_id)
            label = f"{meta['model']}（{meta['plan']}）" if meta.get("plan") else meta["model"]
            added_names.append(label)
        front_kinds = {r.get("frontier") for r in added}
        if front_kinds == {"price"}:
            change_lines.append(
                "变动详情：纯 Token 单价前沿新增 " + "、".join(added_names) + "。"
            )
            if any("claude-" in r.get("id", "") for r in added):
                change_lines.append(
                    "成因说明：Google AI Pro 学生版（Gemini 4 Neon）核定月额度由 12.91 亿下调至 7.74 亿 Token 后，"
                    "单位成本上升约 67%，不再能以低价全面压制 Anthropic 高阶席位，使上述方案重回单价前沿。"
                )
        else:
            change_lines.append("新进前沿：" + "、".join(added_names) + "。")
    if removed:
        rem_names = []
        for r in removed:
            meta = _resolve_point_meta(r["id"], points_by_id)
            rem_names.append(f"{meta['model']}（{meta['plan']}）" if meta.get("plan") else meta["model"])
        change_lines.append("退出前沿：" + "、".join(rem_names) + "。")
    if not added and not removed and diff.get("total_points_delta"):
        change_lines.append(
            f"收录规模：新增 {diff['total_points_delta']:+d} 个方案点位，各维度前沿结构维持不变。"
        )
    if cost_f or f3d:
        stability_parts = []
        if cost_f:
            stability_parts.append(f"任务成本前沿（{len(cost_f)} 席）")
        if f3d:
            stability_parts.append(f"3D 折中前沿（{len(f3d)} 席）")
        if added and all(r.get("frontier") == "price" for r in added) and not removed:
            change_lines.append(
                f"格局结论：实际生产主力关注的{'与'.join(stability_parts)}本轮完整留存、无任何退出，日常选型无需调整。"
            )
        elif not removed:
            change_lines.append(f"留存情况：{'、'.join(stability_parts)}无旧方案被淘汰出局。")
    if change_lines:
        notes.append("【本轮变动解读】" + "\n".join(change_lines))

    # 2. 3D 折中选型速览（基于 3D 非支配前沿自动归纳甜点）
    if f3d:
        pick_lines = []
        cheapest = min(f3d, key=lambda x: x["cost"])
        smartest = max(f3d, key=lambda x: x["score"])
        pick_lines.append(
            f"低成本入口：{cheapest['model']}（{cheapest['plan']}，单任务 ${cheapest['cost']:.5f}，用时 {_fmt_duration_short(cheapest['time_s'])}，{cheapest['score']:.1f} 分）。"
        )
        sweet_items = [
            x for x in sorted(f3d, key=lambda n: n["time_s"])
            if (x.get("is_time_frontier") or x.get("is_dual_winner"))
            and _state_item_key(x) not in (_state_item_key(cheapest), _state_item_key(smartest))
        ]
        sweet_parts = []
        for x in sweet_items:
            tag = "，成本与耗时双前沿" if x.get("is_dual_winner") else ""
            sweet_parts.append(
                f"{x['model']}（{x['plan']}，${x['cost']:.5f} · {_fmt_duration_short(x['time_s'])} · {x['score']:.1f} 分{tag}）"
            )
        if sweet_parts:
            pick_lines.append("速度与成本甜点：" + "；".join(sweet_parts) + "。")
        pick_lines.append(
            f"最高智力封顶：{smartest['model']}（{smartest['plan']}，{smartest['score']:.1f} 分，单任务 ${smartest['cost']:.5f}，用时 {_fmt_duration_short(smartest['time_s'])}）。"
        )
        notes.append("【3D 折中选型速览】" + "\n".join(pick_lines))

    # 3. 外部观察与到期倒计时（从 data/research/email-watchnotes.json 读取，自动算 T-N 并过滤已过期项）
    watch_path = DATA_DIR / "research" / "email-watchnotes.json"
    if watch_path.is_file():
        try:
            watch = json.loads(watch_path.read_text(encoding="utf-8"))
        except Exception as e:
            _warn(f"读取 email-watchnotes.json 失败: {e}")
            watch = {}
        meth = watch.get("methodology_notes") or []
        if meth:
            notes.append("【数据基准与价格校对】" + "\n".join(meth))
        cands = watch.get("candidates") or []
        if cands:
            notes.append("【暂未纳入的候选方案】" + "\n".join(cands))
        risk_lines = list(watch.get("risks") or [])
        ref_date = datetime.now().date()
        for cd in watch.get("countdowns") or []:
            dl_str = str(cd.get("deadline") or "").strip()
            txt = str(cd.get("text") or "").strip()
            if not dl_str or not txt:
                continue
            try:
                dl = datetime.strptime(dl_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            days_left = (dl - ref_date).days
            if days_left < 0:
                continue
            mmdd = dl.strftime("%m-%d")
            tag = f"(T-{days_left})" if days_left > 0 else "(今日到期)"
            risk_lines.append(f"优惠到期 {mmdd} {tag}：{txt}。")
        if risk_lines:
            notes.append("【风险与优惠到期提醒】" + "\n".join(risk_lines))


def send_report_email(to=None, report=None):
    """生成并发送最新报告（可传入已算好的 report，避免重复解析）"""
    to = to or DEFAULT_RECIPIENT
    if not to:
        raise SystemExit(
            "收件人未配置：请设置环境变量 REAL_PLAN_RECIPIENT，"
            "或写入 /etc/real-api-pricing/recipient。")
    if report is None:
        _info(f"正在生成简报数据...")
        report = generate_report()
    if "diff" not in report:
        diff = diff_state(load_state(), frontier_state(report))
        attach_email_analysis_notes(diff, report)
        report["diff"] = diff
    else:
        attach_email_analysis_notes(report["diff"], report)
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
    send_parser.add_argument("--to", default=None, help="收件邮箱（缺省读环境变量或配置文件）")

    cron_parser = subparsers.add_parser("cron-run", help="执行自动化巡检：更新部署 → 比对前沿 → 有变动才发信")
    cron_parser.add_argument("--to", default=None, help="收件邮箱（缺省读环境变量或配置文件）")
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

    sync_aa_parser = subparsers.add_parser(
        "sync-aa", help="一键全自动同步最新 AA 全量数据并完成部署上线")
    sync_aa_parser.add_argument("--date", help="采集日 YYYY-MM-DD，默认今天")
    sync_aa_parser.add_argument("--skip-fetch", action="store_true", help="复用本地 HTML 缓存")

    clone_sub_parser = subparsers.add_parser(
        "clone-subscriptions", help="一键从锚点模型克隆全套订阅乘区与双语翻译")
    clone_sub_parser.add_argument("--anchor-model", required=True, help="锚点模型 id，如 gemini-4-argon")
    clone_sub_parser.add_argument("--target-model", required=True, help="目标新模型 id，如 gemini-4-carbon")
    clone_sub_parser.add_argument("--target-name", help="目标新模型展示名称")
    clone_sub_parser.add_argument("--anchor-name", help="锚点模型展示名称")
    clone_sub_parser.add_argument("--price-file", help="新定价文件名")
    clone_sub_parser.add_argument("--ratio", type=float, default=1.0, help="额度缩放倍率")

    query_parser = subparsers.add_parser(
        "query", help="查询模型在 points.json 中的全量点位、单价、跑分与每任务成本")
    query_parser.add_argument("model", help="模型 id 或关键词")

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
        elif args.command == "sync-aa":
            cmd = [str(PY), "scripts/aa_sync_all.py"]
            if args.date: cmd.append(f"--date={args.date}")
            if args.skip_fetch: cmd.append("--skip-fetch")
            sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)
        elif args.command == "clone-subscriptions":
            cmd = [str(PY), "scripts/clone_subscriptions.py",
                   f"--anchor-model={args.anchor_model}",
                   f"--target-model={args.target_model}",
                   f"--ratio={args.ratio}"]
            if args.target_name: cmd.append(f"--target-name={args.target_name}")
            if args.anchor_name: cmd.append(f"--anchor-name={args.anchor_name}")
            if args.price_file: cmd.append(f"--price-file={args.price_file}")
            sys.exit(subprocess.run(cmd, cwd=REPO_ROOT).returncode)
        elif args.command == "query":
            points_file = REPO_ROOT / "derived" / "points.json"
            if not points_file.exists():
                sys.exit("ERROR: derived/points.json 不存在，先运行 update")
            pts = json.loads(points_file.read_text(encoding="utf-8")).get("points", [])
            kw = args.model.lower()
            matched = [p for p in pts if kw in p.get("model", "").lower() or kw in p.get("id", "").lower()]
            dep_site = Path("/var/www/real-plan/data/site.json")
            dep_map = {}
            if dep_site.exists():
                dep_map = {p["id"]: p for p in json.loads(dep_site.read_text(encoding="utf-8")).get("points", [])}
            print(f"找到 {len(matched)} 个匹配点位 ({args.model}):\n")
            for p in matched:
                dep_p = dep_map.get(p["id"])
                sync_tag = "IN_SYNC" if (dep_p and dep_p.get("real_usd_per_mtok") == p.get("real_usd_per_mtok") and dep_p.get("monthly_yi") == p.get("monthly_yi")) else "DRIFT/MISSING"
                print(f"[{p['id']}] {p.get('label', '')} (deploy={sync_tag})")
                print(f"  Billing: {p.get('billing')} | Monthly Yi: {p.get('monthly_yi')} | Real USD/MTok: ${p.get('real_usd_per_mtok')} | List Blended: ${p.get('list_blended_usd_per_mtok')}")
                for b in ["aa_intelligence_index", "aa_terminal_bench_4", "aa_coding_agent_index"]:
                    if p.get(f"{b}__score") is not None:
                        bl = p.get(f"{b}__benchmark_list_price") or p.get("list_blended_usd_per_mtok")
                        aa_cpt = p.get(f"{b}__mean_cost_usd_per_task")
                        sub_cpt = round(aa_cpt * p["real_usd_per_mtok"] / bl, 5) if (aa_cpt and bl and p.get("real_usd_per_mtok")) else None
                        tps = p.get(f"{b}__output_speed_tps")
                        tt = p.get(f"{b}__response_time_seconds")
                        print(f"  {b}: score={p.get(f'{b}__score')}, aa_cpt=${aa_cpt}, sub_cpt=${sub_cpt}, tps={tps}, task_time={tt}s")
                print()
            sys.exit(0)
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
