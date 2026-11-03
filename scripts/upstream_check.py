# -*- coding: utf-8 -*-
"""上游同步检查（只读）：把 runbook §2「上游同步」从口头要求变成确定性判定。

做四件事，全部只读 git（不 merge、不 checkout、不写工作区文件）：
  1. 确认 upstream 远端存在，`git fetch upstream --quiet`（可 --no-fetch 跳过）；
  2. 列 HEAD..<ref> 的新提交（hash / 短 hash / 日期 / subject）；
  3. 列上游改动文件（`git diff --name-only <merge-base> <ref>`，即三点语义），
     分类出数据目录命中、冲突风险前缀（scripts/ web/ docs/）、与本地脏文件重叠；
  4. 给一句判定：`noop`（无新提交）或 `review`（有提交，逐个评估、不做自动 merge）。

判定失败绝不静默当 noop：没有 upstream 远端 / ref 解析不到 / fetch 失败或超时，
一律输出 `"verdict": "undetermined"` 并带可读原因，退出码 2。

用法（Python 用仓库 .venv）：
  .venv/bin/python scripts/upstream_check.py                     # stdout 输出 JSON
  .venv/bin/python scripts/upstream_check.py --json /tmp/upstream.json
  .venv/bin/python scripts/upstream_check.py --no-fetch          # 不联网，只用已有远端 ref
  .venv/bin/python scripts/upstream_check.py --ref origin/main --timeout 60

退出码：0 = 已判定（noop / review）；2 = 无法判定（原因见 reason 字段）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "upstream"
DEFAULT_REF = "upstream/main"
RISK_PREFIXES = ("scripts/", "web/", "docs/")
# 上游几乎必动、且本地由管线重新生成的文件：改动它们不代表需要人工评估采用值
GENERATED_PREFIXES = ("data/research/", "derived/", "charts/", "web/public/data/")
GENERATED_FILES = ("data/adopted.csv",)


def run_git(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def undetermined(reason: str, message: str, **extra) -> dict:
    return {
        "schemaVersion": 1,
        "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "verdict": "undetermined",
        "reason": reason,
        "message": message,
        **extra,
    }


def local_dirty_paths(timeout: float) -> set[str]:
    r = run_git(["status", "--porcelain"], timeout)
    if r.returncode != 0:
        return set()
    out = set()
    for line in r.stdout.splitlines():
        path = line[3:].strip()
        if " -> " in path:  # rename: 取新路径
            path = path.split(" -> ", 1)[1]
        if path:
            out.add(path)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help=f"远端名（默认 {DEFAULT_REMOTE}）")
    parser.add_argument("--ref", default=DEFAULT_REF, help=f"对比 ref（默认 {DEFAULT_REF}）")
    parser.add_argument("--timeout", type=float, default=120.0, help="单条 git 命令超时秒数（默认 120）")
    parser.add_argument("--no-fetch", action="store_true", help="不执行 git fetch，只用本地已有远端 ref")
    parser.add_argument("--json", help="除 stdout 外，把同一份 JSON 写到该路径")
    args = parser.parse_args()

    base = {
        "schemaVersion": 1,
        "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": str(ROOT),
        "remote": args.remote,
        "ref": args.ref,
    }

    try:
        remote_url = run_git(["remote", "get-url", args.remote], args.timeout)
    except subprocess.TimeoutExpired:
        doc = undetermined("remote_check_timeout", f"git remote get-url {args.remote} 超时（{args.timeout}s）", **base)
        return emit(doc, args.json)
    if remote_url.returncode != 0:
        doc = undetermined(
            "remote_missing",
            f"没有远端 {args.remote!r}；上游同步无法判定（不是 noop）。"
            f"检查 `git remote -v`，或在仓库里 `git remote add {args.remote} <url>` 后重跑。",
            **base,
        )
        return emit(doc, args.json)
    base["remoteUrl"] = remote_url.stdout.strip()

    fetch = {"attempted": not args.no_fetch, "ok": None, "elapsedSeconds": None}
    if not args.no_fetch:
        started = time.monotonic()
        try:
            r = run_git(["fetch", args.remote, "--quiet"], args.timeout)
        except subprocess.TimeoutExpired:
            fetch.update(ok=False, elapsedSeconds=round(time.monotonic() - started, 2))
            doc = undetermined(
                "fetch_timeout",
                f"git fetch {args.remote} 超过 {args.timeout}s 未返回；网络或远端异常，无法判定（不是 noop）。",
                fetch=fetch, **base,
            )
            return emit(doc, args.json)
        fetch.update(ok=r.returncode == 0, elapsedSeconds=round(time.monotonic() - started, 2))
        if r.returncode != 0:
            doc = undetermined(
                "fetch_failed",
                f"git fetch {args.remote} 失败（exit {r.returncode}）：{r.stderr.strip()[:300] or '无 stderr'}；无法判定（不是 noop）。",
                fetch=fetch, **base,
            )
            return emit(doc, args.json)
    base["fetch"] = fetch

    try:
        head = run_git(["rev-parse", "HEAD"], args.timeout)
        ref_rev = run_git(["rev-parse", "--verify", f"{args.ref}^{{commit}}"], args.timeout)
    except subprocess.TimeoutExpired:
        doc = undetermined("rev_parse_timeout", "git rev-parse 超时；无法判定（不是 noop）。", **base)
        return emit(doc, args.json)
    if ref_rev.returncode != 0:
        doc = undetermined(
            "ref_missing",
            f"解析不到 {args.ref!r}；该远端 ref 可能还没抓下来（去掉 --no-fetch 重试），无法判定（不是 noop）。",
            **base,
        )
        return emit(doc, args.json)
    base["head"] = head.stdout.strip()
    base["upstreamHead"] = ref_rev.stdout.strip()

    try:
        log = run_git(
            ["log", "--format=%H%x1f%h%x1f%ad%x1f%s", "--date=short", f"HEAD..{args.ref}"],
            args.timeout,
        )
        merge_base = run_git(["merge-base", "HEAD", args.ref], args.timeout)
    except subprocess.TimeoutExpired:
        doc = undetermined("log_timeout", "git log/merge-base 超时；无法判定（不是 noop）。", **base)
        return emit(doc, args.json)

    commits = []
    for line in log.stdout.splitlines():
        if not line.strip():
            continue
        full, short, date, subject = (line.split("\x1f") + ["", "", "", ""])[:4]
        commits.append({"hash": full, "short": short, "date": date, "subject": subject})
    base["commitCount"] = len(commits)
    base["commits"] = commits

    if merge_base.returncode == 0 and merge_base.stdout.strip():
        diff_args = ["diff", "--name-only", merge_base.stdout.strip(), args.ref]
        base["mergeBase"] = merge_base.stdout.strip()
    else:
        diff_args = ["diff", "--name-only", "HEAD", args.ref]
        base["mergeBase"] = None
    try:
        diff = run_git(diff_args, args.timeout)
    except subprocess.TimeoutExpired:
        doc = undetermined("diff_timeout", "git diff 超时；无法判定（不是 noop）。", **base)
        return emit(doc, args.json)
    changed = sorted({p for p in diff.stdout.splitlines() if p.strip()})
    base["files"] = {"count": len(changed), "changed": changed}

    def _clean(path: str) -> str:
        # git 对非 ASCII 路径默认输出 C 风格转义并加引号，形如
        # "charts/zh/frontier/\345\211\215..."。不先还原，startswith("charts/") 永远为 False，
        # 生成物就会被误判成源码，进而伪造出「源码级双改 35 个」的假冲突（2026-11-04 实测）。
        p = path.strip()
        if p.startswith('"') and p.endswith('"'):
            try:
                p = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
            except (UnicodeDecodeError, UnicodeEncodeError):
                p = p.strip('"')
        return p

    def is_generated(path: str) -> bool:
        c = _clean(path)
        return c.startswith(GENERATED_PREFIXES) or c in GENERATED_FILES

    data_files = [p for p in changed if p.startswith("data/")]
    dirty = {_clean(p) for p in local_dirty_paths(args.timeout)}
    changed = sorted({_clean(p) for p in changed})
    base["files"] = {"count": len(changed), "changed": changed}
    # 2026-11-04 修正：本地脏文件里的「生成物」（charts/ · derived/ · data/research/ ·
    # web/public/data/ · adopted.csv）是重建产物，stash 后重新生成即可，不构成合并冲突。
    # 此前把它们一并计入 localOverlap，导致 2026-10-30 起连续 5 天判「高冲突风险不合并」，
    # 上游 29 个提交（含 customProviders / pricingPrompt 等前端功能）一天都没进来。
    # 真正的冲突判据只有一个：上游改了 **且** 本地也改了，且不是生成物。
    dirty_src = {p for p in dirty if not is_generated(p)}
    flags = {
        "touchesData": bool(data_files),
        "touchesDataSignificant": bool([p for p in data_files if not is_generated(p)]),
        "touchesGeneratedOutputs": bool([p for p in changed if is_generated(p)]),
        # 真冲突：上游与本地同时改到同一源码文件
        "conflictRiskPrefixes": sorted({p for p in changed if p.startswith(RISK_PREFIXES) and p in dirty_src}),
        # 上游单边改动的风险前缀（仅供参考，不阻断合并）
        "riskTouchedUpstream": sorted({p for p in changed if p.startswith(RISK_PREFIXES)}),
        "localOverlap": sorted(p for p in changed if p in dirty),
        # 与本地脏源码重叠（真正的阻断项）
        "localOverlapSource": sorted(p for p in changed if p in dirty_src),
        "localOverlapGenerated": sorted(p for p in changed if p in dirty and is_generated(p)),
    }
    base["flags"] = flags
    categories: dict[str, int] = {}
    for p in changed:
        top = p.split("/", 1)[0]
        categories[top] = categories.get(top, 0) + 1
    base["categories"] = dict(sorted(categories.items()))

    if not commits:
        base.update(
            verdict="noop",
            reason=f"{args.ref} 相对 HEAD 无新提交（merge-base {base['mergeBase'] or 'n/a'}）",
            suggestedAction="无需上游评估；照 runbook 后续阶段继续。",
        )
    else:
        bits = [f"{len(commits)} 个新提交"]
        if flags["touchesDataSignificant"]:
            bits.append("触及非生成物数据目录")
        if flags["riskTouchedUpstream"]:
            bits.append(f"上游改动风险前缀 {len(flags['riskTouchedUpstream'])} 个")
        if flags["localOverlapSource"]:
            bits.append(f"源码级双改 {len(flags['localOverlapSource'])} 个（真冲突）")
        if flags["localOverlapGenerated"]:
            bits.append(f"生成物重叠 {len(flags['localOverlapGenerated'])} 个（可 stash 丢弃重建）")

        if flags["localOverlapSource"]:
            base.update(
                verdict="review",
                reason="；".join(bits),
                suggestedAction=(
                    "上游与本地改到了同一批源码文件，需人工逐个合并（runbook §2.3）。"
                    "先 `git status --short` 看清本地改动，不要裸 merge。"
                ),
            )
        else:
            base.update(
                verdict="mergeable",
                reason="；".join(bits),
                suggestedAction=(
                    "无源码级双改，可直接合并："
                    "① `git stash push --include-untracked -- <生成物路径>` 或直接 `git checkout -- <生成物>`；"
                    "② `git merge --no-ff upstream/main`；"
                    "③ `real-api-pricing-cli update` 重建生成物；"
                    "④ 跑满 19 项守卫，全绿才算完成。"
                    "合并前先确认 HEAD 可回滚（记下 merge 前 sha）。"
                ),
            )
    return emit(base, args.json)


def emit(doc: dict, json_path: str | None) -> int:
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    print(text)
    if json_path:
        Path(json_path).write_text(text + "\n", encoding="utf-8")
    verdict = doc.get("verdict")
    if verdict == "undetermined":
        print(f"UNDETERMINED: {doc.get('reason')} — {doc.get('message')}", file=sys.stderr)
        return 2
    print(f"{verdict.upper()}: {doc.get('reason')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
