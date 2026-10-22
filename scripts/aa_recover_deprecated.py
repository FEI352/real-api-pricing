#!/usr/bin/env python3
"""aa_recover_deprecated.py

AA 的 /leaderboards/models 只收录「有公开 API 的模型」，会把 deprecated 模型剔除；
但模型详情页 /models/<slug> 仍然保留 Intelligence 分数。

后果（2026-10-22 血证）：muse-spark-1.3 被判「下线」→ 退出帕累托前沿 → 释放包络
→ 两个模型被误报为「新进前沿」→ 发出错误的变更邮件。

本脚本在快照构建之后跑：对比 prior 与新版快照的 slug 集合，对「上一版有、这一版没有」
的模型逐个探测详情页；200 则从详情页 RSC 载荷取回 Intelligence 分数，作为 supplement
写进新快照的 scores，并在 rawRecords 里补一条最小记录。

用法：
    python scripts/aa_recover_deprecated.py \
        --snapshot data/research/scores-aa-round6-2026-10-22.json \
        --prior data/research/scores-aa-round6-2026-10-21.json \
        [--dry-run] [--quiet]

退出码：0 正常（无论补回几条）/ 1 快照不可读 / 2 网络全失败（需人工介入）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_URL = "https://artificialanalysis.ai/models/{slug}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
RSC_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
INDEX_RE = re.compile(r'"intelligenceIndex"\s*:\s*([0-9.]+)')


def fetch(url: str, timeout: int = 45) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        print(f"  ! fetch failed {url}: {e}", file=sys.stderr)
        return 0, ""


def rsc_join(html: str) -> str:
    chunks = RSC_RE.findall(html)
    out = []
    for c in chunks:
        try:
            out.append(c.encode().decode("unicode_escape"))
        except Exception:  # noqa: BLE001
            out.append(c)
    return "".join(out)


RELEASE_RE = re.compile(r'"releaseName":"([^"]+)"')
VARIANT_RE = re.compile(r'"selected":\{"label":"([^"]+)"')


def extract_meta(html: str) -> tuple[float | None, str | None, str | None]:
    """返回 (分数, releaseName, 当前 variant label)。"""
    joined = rsc_join(html)
    hits = INDEX_RE.findall(joined)
    score = float(hits[0]) if hits else None
    rel = RELEASE_RE.search(joined)
    var = VARIANT_RE.search(joined)
    return score, (rel.group(1) if rel else None), (var.group(1) if var else None)


def model_slugs(archive: dict) -> set[str]:
    raw = (archive.get("rawRecords") or {}).get("aa_intelligence_index") or []
    out = set()
    for r in raw:
        s = r.get("slug")
        if s:
            out.add(s)
    for sc in archive.get("scores") or []:
        s = (sc.get("secondary") or {}).get("slug")
        if s:
            out.add(s)
    return out


def slug_to_model_id(slug: str) -> str:
    """muse-spark-1-3 → muse-spark-1.3（项目 id 用点号）。

    🔴 只替换**最后一组** `-<数字>-<数字>`（版本号段），不能全局替换：
    全局 `re.sub(r"-(?=\\d)", ".")` 会把 muse-spark-1-3 变成 muse-spark.1.3，
    匹配不上任何套餐点，补回的分数等于白补（2026-10-22 实测踩中）。
    """
    m = re.match(r"^(.*?)-(\d+)-(\d+)(.*)$", slug)
    if m:
        return f"{m.group(1)}-{m.group(2)}.{m.group(3)}{m.group(4)}"
    return slug


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True, help="刚构建的新快照路径")
    ap.add_argument("--prior", required=True, help="被替换的上一版快照路径")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    snap_p = Path(args.snapshot)
    prior_p = Path(args.prior)
    if not snap_p.is_file():
        print(f"ERROR: 读不到新快照 {snap_p}", file=sys.stderr)
        return 1
    if not prior_p.is_file():
        print(f"WARN: 读不到 prior {prior_p}，跳过恢复", file=sys.stderr)
        return 0

    snap = json.loads(snap_p.read_text(encoding="utf-8"))
    prior = json.loads(prior_p.read_text(encoding="utf-8"))

    now_slugs = model_slugs(snap)
    old_slugs = model_slugs(prior)
    vanished = sorted(old_slugs - now_slugs)

    if not vanished:
        if not args.quiet:
            print("recover: prior 与新版 slug 集合一致，无需恢复", file=sys.stderr)
        return 0

    print(f"recover: 上一版有、本版消失的 slug {len(vanished)} 个：", file=sys.stderr)
    for s in vanished:
        print(f"  - {s}", file=sys.stderr)

    recovered, dead, failed = [], [], []
    for slug in vanished:
        status, html = fetch(MODEL_URL.format(slug=slug))
        if status == 0:
            failed.append(slug)
            continue
        if status != 200:
            dead.append((slug, status))
            print(f"  × {slug}: HTTP {status} → 判定真下线", file=sys.stderr)
            continue
        score, release, variant = extract_meta(html)
        if score is None:
            dead.append((slug, "200-but-no-index"))
            print(f"  ? {slug}: 详情页 200 但解不出 intelligenceIndex", file=sys.stderr)
            continue
        recovered.append((slug, round(score, 4), release, variant))
        label = f"{release} ({variant})" if release and variant else slug
        print(f"  ✓ {slug}: HTTP 200, Intelligence = {score}  [{label}]", file=sys.stderr)

    if failed and not recovered:
        print("ERROR: 全部探测失败，网络或 AA 结构可能变化，需人工介入", file=sys.stderr)
        return 2

    if not recovered:
        print("recover: 消失的模型均确认下线或无分，无需补录", file=sys.stderr)
        return 0

    if args.dry_run:
        print(f"[dry-run] 将补回 {len(recovered)} 条：{[r[0] for r in recovered]}",
              file=sys.stderr)
        return 0

    # 写入新快照：scores + rawRecords
    snap.setdefault("scores", [])
    raw = (snap.setdefault("rawRecords", {})
               .setdefault("aa_intelligence_index", []))
    # 去重必须按 (board, model)：同一模型可能在别的榜已有记录
    # （muse-spark-1.3 在 aa_coding_agent_index 有分，但 aa_intelligence_index 没有）。
    # 只看 model 会把要补的那条误判为「已存在」而跳过。
    existing_keys = {(s.get("boardId"), s.get("model")) for s in snap["scores"]}

    added = 0
    for slug, score, release, variant in recovered:
        model_id = slug_to_model_id(slug)
        if ("aa_intelligence_index", model_id) in existing_keys:
            continue
        label = (f"{release} ({variant})" if release and variant
                 else slug.replace("-", " ").title())
        snap["scores"].append({
            "boardId": "aa_intelligence_index",
            "model": model_id,
            "variantLabel": label,
            "score": score,
            "source": MODEL_URL.format(slug=slug),
            "checkedAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
            "secondary": {
                "slug": slug,
                "intelligenceIndexIsEstimated": False,
                "recoveredFrom": MODEL_URL.format(slug=slug),
                "recoveryNote": (
                    "AA /leaderboards/models 不再收录（多为 deprecated），"
                    "但详情页仍保留 Intelligence 分数；由 aa_recover_deprecated.py 补回。"
                ),
            },
        })
        raw.append({
            "slug": slug,
            "name": label,
            "shortName": label,
            "intelligenceIndex": score,
            "intelligenceIndexIsEstimated": False,
            "recovered": True,
        })
        added += 1

    if added:
        meta = snap.setdefault("extraction", {})
        counts = meta.setdefault("counts", {})
        counts["recoveredDeprecated"] = added
        meta["recoveryNote"] = (
            f"aa_recover_deprecated.py 补回 {added} 个 deprecated 模型"
            f"（AA API 榜已剔除但详情页仍有分）：{[r[0] for r in recovered]}"
        )
        snap_p.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"recover: 已补回 {added} 条到 {snap_p.name}", file=sys.stderr)
    if dead:
        print(f"recover: 确认下线 {len(dead)} 个：{dead}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
