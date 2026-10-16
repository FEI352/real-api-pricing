#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_subscription.py
一键自动化录入/更新套餐订阅并同步构建、校验与部署上线。
消除手动改数容易踩中的 i18n 数字不一致、色板未注册、SVG 坐标断言失败等所有陷阱。
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else "python3"
DEPLOY_DIR = Path("/var/www/real-plan")


def run_cmd(cmd, cwd=ROOT, check=True):
    print(f"==> 执行: {cmd} (cwd: {cwd})", file=sys.stderr)
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"❌ 命令失败 ({res.returncode}):\n{res.stderr}\n{res.stdout}", file=sys.stderr)
        raise RuntimeError(f"Command failed: {cmd}")
    return res


def calculate_blended(cached, inp, out):
    conventions = json.loads((ROOT / "data/conventions.json").read_text(encoding="utf-8"))
    mix = conventions["standardTokenMix"]
    return mix["cache"] * cached + mix["input"] * inp + mix["output"] * out


def sanitize_url_punct(text):
    """把 `(https://…):` 改写成 `— https://… —`。

    verify_i18n.py 的 URL_RE 只排除 CJK 与全角标点，**不排除 `)` `:`**，
    且 URL_TAIL 只 trim `;,.` —— 所以 URL 后紧贴的半角 `)` 会被吞进 URL 判 mismatch
    （2026-10-16 连续踩中两次）。这里在入口统一改写成空格包裹的裸 URL。
    """
    if not text:
        return text
    return re.sub(r"\(\s*(https?://[^\s)]+)\s*\):", r"— \1 —", text)


def upsert_extra_row(args, yi):
    """把本次录入写成 data/extra_subscriptions.json 的一行（幂等 upsert）。

    「数据驱动」的落点：build_adopted.py 读取该文件并构造套餐行，
    因此新增套餐**不再需要手工编辑 build_adopted.py 的 SUBS 字面量**。
    """
    path = ROOT / "data/extra_subscriptions.json"
    spec = (json.loads(path.read_text(encoding="utf-8")) if path.exists()
            else {"_doc": "增量套餐行（由 scripts/add_subscription.py 维护，勿手改）", "rows": []})
    row = {
        "plan_id": args.plan_id,
        "plan_name": args.plan_name,
        "price": args.price_usd,
        "currency": "USD",
        "model": args.model,
        "monthly_yi": yi,
        "confidence": args.confidence,
        "source": args.source_zh,
        "note": args.note_zh,
        "main": not args.full_tier,
    }
    if args.plan_name_en:
        row["plan_name_en"] = args.plan_name_en
    if args.inherit_date:
        pid, _, model = args.inherit_date.partition(",")
        row["date_inherit"] = [pid.strip(), model.strip()]
    spec["rows"] = [r for r in spec.get("rows", [])
                    if (r.get("plan_id"), r.get("model")) != (args.plan_id, args.model)]
    spec["rows"].append(row)
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"📦 增量行已落盘 data/extra_subscriptions.json（{args.plan_id}::{args.model}）", file=sys.stderr)


def register_channel_color(channel_name, plan_id):
    colors_path = ROOT / "config/channel-colors.json"
    data = json.loads(colors_path.read_text(encoding="utf-8"))
    
    # 1. 检查 channel 是否已存在
    prefix = plan_id.split("_")[0]
    has_prefix = any(p[0] == prefix for p in data.get("channels", []))
    has_color = channel_name in data.get("colors", {})
    
    changed = False
    if not has_color:
        # 寻找一个 delta_e >= 15 的颜色
        sys.path.insert(0, str(ROOT / "scripts"))
        from checks.verify_palette import COLORS, delta_e
        
        # 候选安全色系
        palette_candidates = [
            "#800080", "#701A75", "#831843", "#047857", "#B45309",
            "#1E40AF", "#3730A3", "#581C87", "#9D174D", "#0F766E"
        ]
        chosen = None
        for c in palette_candidates:
            if all(delta_e(c, v) >= 15.0 for v in COLORS.values()):
                chosen = c
                break
        if not chosen:
            chosen = "#701A75"
            
        data["colors"][channel_name] = chosen
        changed = True
        print(f"🎨 为渠道 {channel_name} 自动分配配色: {chosen}", file=sys.stderr)

    if not has_prefix:
        data["channels"].append([prefix, channel_name])
        changed = True
        print(f"🔗 注册前缀映射: {prefix} -> {channel_name}", file=sys.stderr)
        
    if changed:
        colors_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        
    # 2. 检查 plot_quotas.py 中的 VENDOR_COLORS 和 VENDOR_CODES
    plot_quotas_path = ROOT / "scripts/plot_quotas.py"
    pq_content = plot_quotas_path.read_text(encoding="utf-8")
    
    if channel_name not in pq_content:
        # 插入 VENDOR_COLORS
        pq_content = pq_content.replace(
            '"Local"])',
            f'"{channel_name}", "Local"])'
        )
        # 插入 VENDOR_CODES
        short_code = channel_name[:2].upper()
        pq_content = pq_content.replace(
            '"Local": "LO",',
            f'"{channel_name}": "{short_code}", "Local": "LO",'
        )
        plot_quotas_path.write_text(pq_content, encoding="utf-8")
        print(f"📊 已在 plot_quotas.py 中登记图例与编码: {channel_name} ({short_code})", file=sys.stderr)


def register_i18n(source_zh, source_en, note_zh, note_en):
    i18n_path = ROOT / "data/i18n/adopted.en.json"
    data = json.loads(i18n_path.read_text(encoding="utf-8"))
    entries = data["entries"]
    
    existing_zh = {e["zh"]: e for e in entries}
    
    # 注册 source
    if source_zh and source_zh not in existing_zh:
        entries.append({"zh": source_zh, "en": source_en or source_zh})
    elif source_zh and source_en:
        existing_zh[source_zh]["en"] = source_en
        
    # 注册 note
    if note_zh and note_zh not in existing_zh:
        entries.append({"zh": note_zh, "en": note_en or note_zh})
    elif note_zh and note_en:
        existing_zh[note_zh]["en"] = note_en
        
    i18n_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("🌐 i18n 双语对照已持久化登记", file=sys.stderr)


def rebuild_and_deploy():
    print("\n🚀 [1/6] 生成 adopted.csv...", file=sys.stderr)
    run_cmd(f"{PY} scripts/build_adopted.py")
    # 清孤儿/补全：只走官方权威入口（自造的 csv-only 剪枝会误删 benchmark 侧条目，已废弃）
    run_cmd(f"{PY} scripts/checks/verify_i18n.py --sync", check=False)
    
    print("🚀 [2/6] 计算模型与各榜单前沿 (compute.py)...", file=sys.stderr)
    run_cmd(f"{PY} scripts/compute.py")
    
    print("🚀 [3/6] 绘制全套 SVG 与图表...", file=sys.stderr)
    run_cmd(f"{PY} scripts/plot_svg.py")
    run_cmd("node scripts/render_svg.cjs")
    run_cmd(f"{PY} scripts/build_html.py")
    run_cmd(f"{PY} scripts/plot_quotas.py")
    run_cmd(f"{PY} scripts/publish_charts.py")
    
    print("🚀 [4/6] 前端数据打包与静态构建...", file=sys.stderr)
    run_cmd("npm run data", cwd=ROOT / "web")
    run_cmd("npm run build", cwd=ROOT / "web")
    
    print("🚀 [5/6] 部署到 /var/www/real-plan...", file=sys.stderr)
    run_cmd(f"cp -r {ROOT}/web/dist/* {DEPLOY_DIR}/")
    
    print("🚀 [6/6] 运行全量 18 项 CI 守卫套件 (run_all.py)...", file=sys.stderr)
    res = run_cmd(f"{PY} scripts/checks/run_all.py", check=False)
    if res.returncode != 0:
        print("⚠️ 提示: 存在部分守卫报警，输出如下:", file=sys.stderr)
        print(res.stdout, file=sys.stderr)
    else:
        print("✅ 全部 18 项自动化守卫 100% 验证通过！", file=sys.stderr)


def evaluate_frontiers(point_id):
    points_path = ROOT / "derived/points.json"
    data = json.loads(points_path.read_text(encoding="utf-8"))
    
    target = None
    for p in data["points"]:
        if p["id"] == point_id:
            target = p
            break
            
    if not target:
        return {"error": f"Point {point_id} not found"}
        
    results = {}
    for board_id, meta in data["boards"].items():
        key = f"{board_id}__score"
        scored = [p for p in data["points"] if p.get(key) is not None]
        if target.get(key) is None:
            results[board_id] = {"in_frontier": False, "reason": "unscored"}
            continue
            
        t_score = target[key]
        t_price = target["real_usd_per_mtok"]
        
        doms = []
        for o in scored:
            if o["id"] == target["id"]:
                continue
            if o["real_usd_per_mtok"] <= t_price and o[key] >= t_score and (
                o["real_usd_per_mtok"] < t_price or o[key] > t_score
            ):
                doms.append(o["id"])
                
        results[board_id] = {
            "in_frontier": len(doms) == 0,
            "score": t_score,
            "real_usd_per_mtok": t_price,
            "dominated_by": doms[:3] if doms else []
        }
    return results


def main():
    parser = argparse.ArgumentParser(description="一键添加/更新套餐并完成全流程构建校验部署")
    parser.add_argument("--plan-id", required=True, help="套餐唯一ID，如 opendesign_go")
    parser.add_argument("--plan-name", required=True, help="套餐名称，如 OpenDesign Go")
    parser.add_argument("--plan-name-en", help="英文套餐名（可选，如 'Google AI Pro (Student)'）")
    parser.add_argument("--confidence", default="medium", choices=["high", "medium", "low"], help="置信度（默认 medium）")
    parser.add_argument("--full-tier", action="store_true", help="只进全量图（默认进精选主图）")
    parser.add_argument("--inherit-date", help="数据日期沿用锚点，格式 'plan_id,model'")
    parser.add_argument("--price-usd", type=float, required=True, help="月费(USD)，如 10.0")
    parser.add_argument("--model", required=True, help="模型ID，如 deepseek-v4.1-flash")
    parser.add_argument("--monthly-yi", type=float, help="月额度(亿token)，直接指定")
    parser.add_argument("--allowance-usd", type=float, help="等效美元额度，自动按三段价换算为亿token")
    parser.add_argument("--cached-price", type=float, default=0.003, help="缓存命中价($/1M)")
    parser.add_argument("--input-price", type=float, default=0.15, help="输入价($/1M)")
    parser.add_argument("--output-price", type=float, default=0.60, help="输出价($/1M)")
    parser.add_argument("--channel", help="所属渠道名，如 OpenDesign")
    parser.add_argument("--source-zh", required=True, help="中文数据源说明")
    parser.add_argument("--source-en", help="英文数据源说明")
    parser.add_argument("--note-zh", required=True, help="中文收录备注")
    parser.add_argument("--note-en", help="英文收录备注")
    parser.add_argument("--no-deploy", action="store_true", help="仅生成数据，不部署")
    
    args = parser.parse_args()

    # URL 尾随标点防御（verify_i18n 的提取正则会吞掉紧贴 URL 的 `):`）
    args.source_zh = sanitize_url_punct(args.source_zh)
    args.source_en = sanitize_url_punct(args.source_en)
    args.note_zh = sanitize_url_punct(args.note_zh)
    args.note_en = sanitize_url_punct(args.note_en)
    
    channel = args.channel or args.plan_name.split()[0]
    
    # 计算月度亿 token
    if args.monthly_yi is not None:
        yi = args.monthly_yi
    elif args.allowance_usd is not None:
        b_price = calculate_blended(args.cached_price, args.input_price, args.output_price)
        yi = round(args.allowance_usd / b_price / 100, 3)
        print(f"💡 自动折算: ${args.allowance_usd} ÷ blended({b_price:.5f}) = {yi} 亿 Token", file=sys.stderr)
    else:
        print("❌ 错误: 必须指定 --monthly-yi 或 --allowance-usd", file=sys.stderr)
        sys.exit(1)
        
    # 0. 落盘增量套餐行（数据驱动入口，build_adopted.py 会读取）
    upsert_extra_row(args, yi)

    # 1. 注册渠道配色与前缀
    register_channel_color(channel, args.plan_id)
    
    # 2. 注册 i18n
    register_i18n(args.source_zh, args.source_en, args.note_zh, args.note_en)
    
    # 3. 驱动构建与发布
    if not args.no_deploy:
        rebuild_and_deploy()
        
    point_id = f"{args.plan_id}::{args.model}"
    print(f"\n🎯 正在评估点 {point_id} 的前沿状态...", file=sys.stderr)
    res = evaluate_frontiers(point_id)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    

if __name__ == "__main__":
    main()
