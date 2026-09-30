#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
real-api-pricing-cli
管理数据更新、图表截图、周报生成与邮件推送的自动化命令行工具。
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

def update_data():
    """执行完整的数据更新与网站构建部署流程"""
    print("[1/4] 运行 build_adopted.py...")
    run_cmd("python3 scripts/build_adopted.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})

    print("[2/4] 运行 compute.py...")
    run_cmd("python3 scripts/compute.py", cwd=REPO_ROOT, env={"PYTHONPATH": "scripts"})

    print("[3/4] 运行 web 构建与数据打包 (npm run data && npm run build)...")
    run_cmd("npm run data && npm run build", cwd=WEB_DIR)

    print("[4/4] 部署到 Nginx 目录...")
    run_cmd(f"cp -r dist/. {DEPLOY_DIR}/", cwd=WEB_DIR)
    print("✅ 数据与网站更新部署成功！(https://real-plan.fja.su)")

def capture_chart(output_path=SCREENSHOT_PATH):
    """使用 headless chromium 截取最新图表"""
    print(f"正在截取 Cost per task 图表 -> {output_path}...")
    url = "https://real-plan.fja.su/?v=" + datetime.now().strftime("%Y%m%d%H%M%S") + "#lang=zh&config=summary&xmode=cost"
    cmd = (
        f"chromium --headless --no-sandbox --disable-gpu "
        f"--window-size=1600,1050 --virtual-time-budget=5000 "
        f"--screenshot={output_path} '{url}'"
    )
    run_cmd(cmd)
    
    print(f"正在通过 hermes-share 发布截图...")
    try:
        share_out = run_cmd(f"/root/.local/bin/hermes-share {output_path} --name cost-per-task-latest.png")
        print(share_out)
    except Exception as e:
        print(f"hermes-share 警告: {e}", file=sys.stderr)
    return output_path

def generate_report():
    """解析最新数据，计算前沿模型与对比报表"""
    site_json = json.loads((WEB_DIR / "public/data/site.json").read_text(encoding="utf-8"))
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

    return {
        "date": site_json.get("generatedAt", datetime.now().strftime("%Y-%m-%d")),
        "total_points": len(points),
        "cost_frontier": cost_frontier,
        "price_frontier": price_frontier,
    }

def format_email_content(report):
    """格式化周报的纯文本与 HTML 邮件内容"""
    date_str = report["date"]
    
    # 构造 Markdown / Text
    text = f"""=== Real API Pricing 模型性价比与任务成本周报 ({date_str}) ===

数据快照：{date_str}
已收录套餐点：{report['total_points']} 个
线上交互看板：https://real-plan.fja.su

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
</style>
</head>
<body>
<div class="container">
  <h1>📊 Real API Pricing 周报</h1>
  <p><b>数据快照：</b> {date_str} &nbsp;|&nbsp; <b>覆盖点数：</b> {report['total_points']} &nbsp;|&nbsp; <b>在线看板：</b> <a href="https://real-plan.fja.su" target="_blank">real-plan.fja.su</a></p>

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
    <p>此邮件由 <b>Real API Pricing 自动化监控管道</b> 每周定时发出。<br/>
    数据源自公开独立榜单及官方套餐规格测算。<br/><br/>
    All the best,<br/>
    <b>Hermes Agent</b></p>
  </div>
</div>
</body>
</html>"""

    return text, html

def send_report_email(to=DEFAULT_RECIPIENT):
    """生成并发送最新周报"""
    print(f"正在生成周报数据...")
    report = generate_report()
    text, html = format_email_content(report)
    
    # 标题遵循反垃圾规则：严禁包含收件人根域名 (如 ching.icu)
    subject = f"大模型真实性价比与任务成本周报 ({report['date']})"
    
    print(f"正在发送周报邮件至 {to}...")
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
    
    res = json.loads(proc.stdout)
    if res.get("ok"):
        print(f"✅ 邮件已成功送达 {to}！")
        return True
    else:
        print(f"❌ 发信错误: {res.get('error')}", file=sys.stderr)
        return False

def main():
    parser = argparse.ArgumentParser(description="Real API Pricing CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    subparsers.add_parser("update", help="更新数据、重跑计算并部署最新网站")
    subparsers.add_parser("chart", help="截图最新图表并上传分发")
    subparsers.add_parser("report", help="在终端输出最新前沿报表")
    
    send_parser = subparsers.add_parser("send-email", help="发送最新周报邮件")
    send_parser.add_argument("--to", default=DEFAULT_RECIPIENT, help="收件邮箱")

    cron_parser = subparsers.add_parser("cron-run", help="执行完整周巡检任务（更新、截图、生成报表并发送邮件）")
    cron_parser.add_argument("--to", default=DEFAULT_RECIPIENT, help="收件邮箱")

    args = parser.parse_args()

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
    elif args.command == "cron-run":
        print("=== 开始执行周度自动化更新与报告 ===")
        update_data()
        capture_chart()
        send_report_email(to=args.to)
        print("=== 任务完成 ===")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
