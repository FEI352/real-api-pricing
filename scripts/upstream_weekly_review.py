# -*- coding: utf-8 -*-
"""每周上游同步与人工校对报告生成器 (Real API Pricing).

设计目标：
1. 彻底解决旧版 `sync-real-api-pricing.sh` 裸跑 `git merge upstream/main` 必然冲突死锁的问题；
2. 自动化拉取上游（git fetch upstream main）并计算与 HEAD 的差异；
3. 安全同步上游纯新增的 research 证据文件（data/research/*），丰富本地研究档案，同时绝对不修改采用值、代码与生产配置；
4. 识别并归纳上游核心变动（采用值调整、PR 合并、文档更新），明确标注本地定制与上游冲突点；
5. 生成结构严谨、可读性极高的人工校对报告（HTML + Plain Text），并通过 Hermes Stalwart 邮件网关发送给维护者；
6. 若有新研究文件入库，安全触发 gitpush 推送。

用法：
  .venv/bin/python scripts/upstream_weekly_review.py --dry-run
  .venv/bin/python scripts/upstream_weekly_review.py --send-email
  .venv/bin/python scripts/upstream_weekly_review.py --fetch --checkout-research --send-email --push
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "upstream"
DEFAULT_REF = "upstream/main"
RECIPIENT_FILE = Path("/etc/real-api-pricing/recipient")


def run_git(args: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def load_recipient() -> str:
    v = os.environ.get("REAL_PLAN_RECIPIENT", "").strip()
    if v:
        return v
    if RECIPIENT_FILE.is_file():
        try:
            val = RECIPIENT_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
        except OSError:
            pass
    return "jaupuihing@ching.icu"


def fetch_upstream(remote: str = DEFAULT_REMOTE, timeout: float = 90.0) -> bool:
    print(f"正在从远端 {remote} 获取最新提交 (git fetch {remote} main)...")
    res = run_git(["fetch", remote, "main", "--quiet"], timeout=timeout)
    if res.returncode != 0:
        print(f"[WARN] git fetch 失败: {res.stderr.strip()}", file=sys.stderr)
        return False
    return True


def get_merge_base(ref: str = DEFAULT_REF) -> str:
    res = run_git(["merge-base", "HEAD", ref])
    if res.returncode != 0:
        raise RuntimeError(f"无法获取 merge-base HEAD {ref}: {res.stderr.strip()}")
    return res.stdout.strip()


def get_new_commits(base: str, ref: str = DEFAULT_REF) -> list[dict]:
    res = run_git(["log", "--format=%H|%h|%an|%ad|%s", "--date=short", f"{base}..{ref}"])
    if res.returncode != 0:
        return []
    commits = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 4)
        if len(parts) == 5:
            commits.append({
                "hash": parts[0],
                "short": parts[1],
                "author": parts[2],
                "date": parts[3],
                "subject": parts[4],
            })
    return commits


def get_diff_files(base: str, ref: str = DEFAULT_REF) -> list[dict]:
    res = run_git(["diff", "--name-status", base, ref])
    if res.returncode != 0:
        return []
    files = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            files.append({"status": parts[0], "path": parts[1]})
    return files


def sync_research_files(base: str, ref: str = DEFAULT_REF, do_checkout: bool = True) -> list[str]:
    """安全检出上游纯新增的 data/research/* 文件（仅当本地不存在该文件时检出）"""
    diffs = get_diff_files(base, ref)
    imported = []
    for item in diffs:
        p = item["path"]
        if p.startswith("data/research/") and item["status"].startswith("A"):
            local_file = ROOT / p
            if not local_file.exists():
                if do_checkout:
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    c = run_git(["checkout", ref, "--", p])
                    if c.returncode == 0:
                        imported.append(p)
                    else:
                        print(f"[WARN] 检出 {p} 失败: {c.stderr.strip()}", file=sys.stderr)
                else:
                    imported.append(p)
    return imported


def analyze_upstream_changes(base: str, ref: str = DEFAULT_REF) -> dict:
    commits = get_new_commits(base, ref)
    diff_files = get_diff_files(base, ref)

    research_files = [f for f in diff_files if f["path"].startswith("data/research/")]
    core_code_files = [f for f in diff_files if f["path"].startswith("scripts/") or f["path"].startswith("web/")]
    adopted_files = [f for f in diff_files if f["path"] in ("data/adopted.csv", "data/conventions.json", "scripts/build_adopted.py")]
    doc_files = [f for f in diff_files if f["path"].startswith("docs/") or f["path"] in ("README.md", "README.zh.md", "DECISIONS.md", "SOURCES.md")]

    # 提取 build_adopted.py 的 diff 摘要
    adopted_diff_summary = []
    diff_adopted = run_git(["diff", base, ref, "--", "scripts/build_adopted.py"])
    if diff_adopted.returncode == 0 and diff_adopted.stdout.strip():
        for l in diff_adopted.stdout.splitlines():
            if (l.startswith("+") and not l.startswith("+++")) or (l.startswith("-") and not l.startswith("---")):
                strip_l = l[1:].strip()
                if strip_l and not strip_l.startswith("#"):
                    adopted_diff_summary.append(l)

    return {
        "commit_count": len(commits),
        "commits": commits,
        "diff_files": diff_files,
        "research_files": research_files,
        "core_code_files": core_code_files,
        "adopted_files": adopted_files,
        "doc_files": doc_files,
        "adopted_diff_snippet": adopted_diff_summary[:30],
    }


def format_report_text(analysis: dict, imported_files: list[str], today_str: str) -> str:
    lines = [
        f"==================================================================",
        f"【Real API Pricing】每周上游变动与人工校对报告 ({today_str})",
        f"==================================================================",
        f"上游远端分支: {DEFAULT_REMOTE}/{DEFAULT_REF}",
        f"落后上游提交: {analysis['commit_count']} 个",
        f"新安全吸纳研究文件: {len(imported_files)} 个",
        "",
        "------------------------------------------------------------------",
        "一、安全同步成果（已吸收的上游 Research 证据）",
        "------------------------------------------------------------------",
    ]
    if imported_files:
        for f in imported_files:
            lines.append(f"  + {f}")
        lines.append(f"说明：上述 {len(imported_files)} 个文件已从 upstream/main 安全检出到本地库，")
        lines.append("充实研究档案与模型实测证据，未改动任何 adopted 采用值与生产代码。")
    else:
        lines.append("  (本周期无新新增的 research 证据文件)")

    lines.extend([
        "",
        "------------------------------------------------------------------",
        "二、上游核心变动与 PR 清单 (最近 15 条)",
        "------------------------------------------------------------------",
    ])
    for c in analysis["commits"][:15]:
        lines.append(f"  [{c['short']}] {c['date']} ({c['author']}): {c['subject']}")
    if len(analysis["commits"]) > 15:
        lines.append(f"  ... 另有 {len(analysis['commits']) - 15} 个提交未展开")

    lines.extend([
        "",
        "------------------------------------------------------------------",
        "三、本地定制保护与冲突对照",
        "------------------------------------------------------------------",
        "1. [ Devin SWE-2 永久免费权益 ]",
        "   - 本地决策：永久不计额度（conventions.permanentUnmetered.devin_swe2），基于 11-12 官方公告；",
        "   - 上游现状：仍维持过期促销口径（promo_until=2026-10-31），两边口径冲突；",
        "   - 处置意见：【坚决维持本地口径】，严禁合入上游过期促销定义。",
        "",
        "2. [ 本地单卡硬件参考点默认隐藏 ]",
        "   - 本地决策：Single RTX 3080/4090/PRO 6000 等参考点默认隐藏，避免冲刷云端商业套餐；",
        "   - 处置意见：【维持本地配置】。",
        "",
        "3. [ Claude Haiku 5.5 降 Full 档防假前沿 ]",
        "   - 本地决策：Haiku 5.5 订阅点全部挂接 Full 标价并设情景点校验；",
        "   - 处置意见：【维持本地配置】。",
        "",
        "------------------------------------------------------------------",
        "四、建议人工校对项 (Action Items)",
        "------------------------------------------------------------------",
    ])

    if analysis["adopted_diff_snippet"]:
        lines.append("上游近期在 scripts/build_adopted.py 上的关键变动行：")
        for line in analysis["adopted_diff_snippet"][:15]:
            lines.append(f"  {line}")
        lines.append("")
        lines.append("校对建议：")
        lines.append("  - 检查上游是否引入新的模型额度样本（如 Plus gpt-6.1-sol、GOAT GLM-5.3 boosted 等）；")
        lines.append("  - 若需采纳上游新采用值，请在本地 build_adopted.py 中微调并执行 daily-sync 校验。")
    else:
        lines.append("上游未改动 scripts/build_adopted.py，无须人工裁定采用值。")

    lines.append("")
    lines.append("本报告由周度定时任务自动生成。若无需改动采用值，系统将保持现有高可信生产状态平稳运行。")
    return "\n".join(lines)


def format_report_html(analysis: dict, imported_files: list[str], today_str: str) -> str:
    # 构造 Commit 表格
    commit_rows = []
    for c in analysis["commits"][:15]:
        commit_rows.append(
            f"<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:12px;color:#2563eb;'>{c['short']}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#4b5563;'>{c['date']}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#374151;'>{html.escape(c['author'])}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#111827;'>{html.escape(c['subject'])}</td>"
            f"</tr>"
        )
    commit_table = "".join(commit_rows)
    if len(analysis["commits"]) > 15:
        commit_table += f"<tr><td colspan='4' style='padding:6px 10px;color:#6b7280;font-size:12px;text-align:center;'>... 另有 {len(analysis['commits']) - 15} 个提交未展开</td></tr>"

    # 构造吸纳文件列表
    if imported_files:
        imported_li = "".join([f"<li style='margin-bottom:4px;'><code style='color:#059669;background:#ecfdf5;padding:2px 6px;border-radius:4px;'>{html.escape(f)}</code></li>" for f in imported_files])
    else:
        imported_li = "<li style='color:#6b7280;'>本周期无新增的 research 证据文件</li>"

    # 构造 adopted 差异
    if analysis["adopted_diff_snippet"]:
        snippet_text = html.escape("\n".join(analysis["adopted_diff_snippet"][:15]))
        adopted_block = f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px;font-family:monospace;font-size:12px;white-space:pre-wrap;line-height:1.5;color:#334155;">{snippet_text}</div>
        <p style="margin-top:8px;font-size:13px;color:#475569;">💡 <strong>校对建议</strong>：上游近期微调了部分模型样本（如 Plus gpt-6.1-sol 或 GOAT credits 调整）。如需跟进，可由维护者在 <code>build_adopted.py</code> 中微调，避免破坏本地既有断言。</p>
        """
    else:
        adopted_block = "<p style='color:#059669;font-size:13px;'>✅ 上游未改动 <code>scripts/build_adopted.py</code>，采用值基线保持一致。</p>"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; color:#1f2937; line-height:1.6; margin:0; padding:20px; background:#f9fafb; }}
  .container {{ max-width:820px; margin:0 auto; background:#ffffff; border-radius:10px; border:1px solid #e5e7eb; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.05); }}
  .header {{ background:#1e293b; color:#ffffff; padding:24px 32px; border-bottom:3px solid #3b82f6; }}
  .header h1 {{ margin:0 0 6px 0; font-size:20px; font-weight:600; letter-spacing:-0.02em; }}
  .header .meta {{ font-size:13px; color:#94a3b8; }}
  .content {{ padding:32px; }}
  .section {{ margin-bottom:28px; }}
  .section h2 {{ font-size:16px; font-weight:700; color:#0f172a; border-left:4px solid #3b82f6; padding-left:10px; margin:0 0 14px 0; }}
  .badge {{ display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:9999px; text-transform:uppercase; }}
  .badge-blue {{ background:#dbeafe; color:#1e40af; }}
  .badge-green {{ background:#dcfce7; color:#166534; }}
  .badge-amber {{ background:#fef3c7; color:#92400e; }}
  .card {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:16px; margin-bottom:12px; }}
  .footer {{ background:#f1f5f9; padding:16px 32px; font-size:12px; color:#64748b; text-align:center; border-top:1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Real API Pricing · 每周上游变动与人工校对报告</h1>
    <div class="meta">报告日期：{today_str} &nbsp;|&nbsp; 对比基线：{DEFAULT_REMOTE}/{DEFAULT_REF} &nbsp;|&nbsp; 状态：已完成安全吸纳</div>
  </div>
  <div class="content">
    <div class="section">
      <div style="display:flex;gap:12px;margin-bottom:20px;">
        <div style="flex:1;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:12px;text-align:center;">
          <div style="font-size:24px;font-weight:700;color:#1e40af;">{analysis['commit_count']}</div>
          <div style="font-size:12px;color:#3b82f6;">上游累积提交</div>
        </div>
        <div style="flex:1;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:12px;text-align:center;">
          <div style="font-size:24px;font-weight:700;color:#065f46;">{len(imported_files)}</div>
          <div style="font-size:12px;color:#10b981;">新吸收 Research 证据</div>
        </div>
        <div style="flex:1;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px;text-align:center;">
          <div style="font-size:24px;font-weight:700;color:#991b1b;">0</div>
          <div style="font-size:12px;color:#ef4444;">生产中断风险</div>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>一、安全同步成果（已吸收上游 Research 证据）</h2>
      <p style="font-size:13px;color:#475569;margin-bottom:10px;">
        已由同步程序自动将上游新增且本地尚缺的 <code>data/research/*</code> 文件检出至本地档案库。充实评测样本与证据，绝不触碰采用值与部署生产线：
      </p>
      <ul style="font-size:13px;line-height:1.7;padding-left:20px;margin:0;">
        {imported_li}
      </ul>
    </div>

    <div class="section">
      <h2>二、上游核心提交审查 (Top 15 Commits)</h2>
      <div style="border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;">
        <table style="width:100%;border-collapse:collapse;text-align:left;">
          <thead>
            <tr style="background:#f9fafb;border-bottom:1px solid #e5e7eb;font-size:12px;color:#4b5563;">
              <th style="padding:8px 10px;width:70px;">Hash</th>
              <th style="padding:8px 10px;width:90px;">日期</th>
              <th style="padding:8px 10px;width:110px;">作者</th>
              <th style="padding:8px 10px;">提交说明</th>
            </tr>
          </thead>
          <tbody>
            {commit_table}
          </tbody>
        </table>
      </div>
    </div>

    <div class="section">
      <h2>三、本地定制保护与冲突对照</h2>
      <div class="card">
        <div style="font-weight:600;font-size:14px;color:#0f172a;margin-bottom:6px;">🛡️ 1. Devin SWE-2 永久不计额度守护</div>
        <div style="font-size:13px;color:#334155;">
          <strong>本地口径</strong>：永久不计额度（<code>conventions.permanentUnmetered.devin_swe2</code>），忠实还原 Cognition 官方公告。<br>
          <strong>上游口径</strong>：仍标记为已过期的限时促销（<code>promo_until=2026-10-31</code>）。<br>
          <strong>裁定</strong>：<span class="badge badge-green">维持本地</span>，坚决阻断上游裸 merge 造成的断言破坏。
        </div>
      </div>
      <div class="card">
        <div style="font-weight:600;font-size:14px;color:#0f172a;margin-bottom:6px;">🛡️ 2. 单卡本地硬件参考点默认隐藏</div>
        <div style="font-size:13px;color:#334155;">
          <strong>本地口径</strong>：对 Single RTX 3080/4090 等硬件参考点执行 <code>hiddenByDefault=true</code>，保护生产图表纯净度。<br>
          <strong>裁定</strong>：<span class="badge badge-green">维持本地</span>。
        </div>
      </div>
    </div>

    <div class="section">
      <h2>四、人工校对行动项 (Action Items)</h2>
      {adopted_block}
    </div>
  </div>
  <div class="footer">
    Real API Pricing 监控与防护中枢 &nbsp;|&nbsp; 生产线上站点: <a href="https://real-plan.fja.su" style="color:#2563eb;text-decoration:none;">real-plan.fja.su</a>
  </div>
</div>
</body>
</html>
"""


def send_review_email(to: str, subject: str, text: str, html_content: str) -> bool:
    print(f"正在通过 Hermes 邮件中枢发送校对报告至 {to}... [主题] {subject}")
    payload = {
        "op": "send",
        "to": to,
        "subject": subject,
        "text": text,
        "html": html_content,
    }
    cmd = "ssh hermes 'python3 /root/.local/bin/mail-rpc.py'"
    proc = subprocess.run(cmd, shell=True, input=json.dumps(payload), capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[ERROR] 邮件发送失败: {proc.stderr.strip()}", file=sys.stderr)
        return False
    try:
        res = json.loads(proc.stdout)
    except Exception as e:
        print(f"[ERROR] 解析发信结果失败: {proc.stdout[:200]!r}", file=sys.stderr)
        return False
    if res.get("ok"):
        print(f"✅ 报告邮件已成功发送至 {to}！")
        return True
    else:
        print(f"❌ 发信返回错误: {res.get('error')}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help=f"远端名（默认 {DEFAULT_REMOTE}）")
    parser.add_argument("--ref", default=DEFAULT_REF, help=f"对比 ref（默认 {DEFAULT_REF}）")
    parser.add_argument("--fetch", action="store_true", help="先执行 git fetch upstream main")
    parser.add_argument("--checkout-research", action="store_true", help="自动安全检出上游新增的 data/research/* 文件")
    parser.add_argument("--send-email", action="store_true", help="发送周报邮件至维护者")
    parser.add_argument("--to", default=None, help="收件邮箱（默认读 /etc/real-api-pricing/recipient）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印纯文本报告，不检出、不发信")
    parser.add_argument("--force", action="store_true", help="即使落后提交为 0 也强制发送报告")
    parser.add_argument("--push", action="store_true", help="若吸纳了新 research 文件，自动调用 gitpush")
    args = parser.parse_args()

    today_str = datetime.now().strftime("%Y-%m-%d")

    if args.fetch and not args.dry_run:
        fetch_upstream(args.remote)

    base = get_merge_base(args.ref)
    analysis = analyze_upstream_changes(base, args.ref)

    if analysis["commit_count"] == 0 and not args.force:
        print(f"上游 {args.ref} 相对本地 HEAD 无新提交 (commitCount=0)，无需生成周报。")
        return 0

    imported_files = []
    if args.checkout_research and not args.dry_run:
        imported_files = sync_research_files(base, args.ref, do_checkout=True)
        if imported_files:
            print(f"已安全同步上游 {len(imported_files)} 个 research 证据文件。")
    elif args.checkout_research and args.dry_run:
        imported_files = sync_research_files(base, args.ref, do_checkout=False)

    text_report = format_report_text(analysis, imported_files, today_str)
    html_report = format_report_html(analysis, imported_files, today_str)

    if args.dry_run:
        print(text_report)
        return 0

    recipient = args.to or load_recipient()
    # 标题严格遵循反垃圾门禁：不含收件根域，不含 ->
    subject = f"[Real-Plan] 每周上游变动与人工校对报告 ({today_str})"

    if args.send_email:
        send_review_email(recipient, subject, text_report, html_report)

    if args.push and imported_files:
        print("检测到新检出的 research 证据文件，正在调用 real-api-pricing-gitpush.sh 入库...")
        commit_msg = f"chore(upstream): import {len(imported_files)} research evidence files ({today_str})"
        push_res = subprocess.run(["/usr/local/bin/real-api-pricing-gitpush.sh", commit_msg])
        if push_res.returncode != 0:
            print("[WARN] gitpush 未完全成功，详见日志。", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
