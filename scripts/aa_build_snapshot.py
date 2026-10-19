# -*- coding: utf-8 -*-
"""AA 现场载荷 → scores-aa-round<N>-<YYYY-MM-DD>.json（每日 AA 快照构建器）。

输入三件（路径全部由参数给，脚本内无写死路径）：
  --models-json   `scripts/aa_fetch_parse.py` 解析出的 models 榜载荷 JSON
  --agents-html   coding-agents 页的**原始 HTML**（不是解析后的 JSON）
  --models-html   models 页原始 HTML（可选；给了才写 extraction.payloadSha256）

口径（与 round5 起既有约定一致，改动前先读这里）：
  · scores 收录：智力榜全部 effort 变体 + TB4（terminalBench40×100）+ 编程 Agent（indexScore×100）
  · 分数四舍五入到 4 位小数；costPerTaskUsd = round(intelligenceIndexCostPerTask, 4)
  · configurationRank = 按 intelligenceIndex 降序的全局名次
  · effort 从 shortName 正则取（载荷无 effort 字段，与 benchmark_configs.EFFORT 同规则）
  · variantLabel = shortName or name（不自造标签）
  · rawRecords.aa_intelligence_index 原样存全部载荷行（含 medianEndToEndResponseTimeSeconds
    与 medianOutputTokensPerSecond；裁剪这两个字段会让"任务用时"轴静默退化）

用法（Python 用仓库 .venv）：
  .venv/bin/python scripts/aa_build_snapshot.py
      --models-json /tmp/aa_models_raw.json
      --agents-html /tmp/aa_agents.html
      --models-html /tmp/aa_models.html
      --collected-at 2026-10-08 --round 6
      --prior scores-aa-round5-2026-10-01.json --prior scores-aa-round4-2026-09-22.json
      --slug-map claude-haiku-5-5=claude-haiku-5.5
      --revises data/research/scores-aa-round5-2026-10-01.json

退出码：0 = 已写快照；1 = 输入缺失/参数非法/输出已存在且未给 --force。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aa_fetch_parse import DEC, decode_rsc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
MODELS_URL = "https://artificialanalysis.ai/leaderboards/models"
AGENTS_URL = "https://artificialanalysis.ai/agents/coding-agents"
EFFORT = re.compile(r"(?<![a-z0-9])(xhigh|high|medium|low|max|none|thinking)(?![a-z0-9])", re.I)


def effort_of(label: str) -> str | None:
    """与 scripts/benchmark_configs.EFFORT 同一规则（AA 载荷不带 effort 字段）。"""
    m = EFFORT.search(label)
    return m.group(1).lower() if m else None


def resolve_prior(name: str) -> Path:
    p = Path(name)
    if p.is_file():
        return p
    cand = RESEARCH / name
    if cand.is_file():
        return cand
    raise FileNotFoundError(f"--prior {name} 既不是存在的路径，也不在 {RESEARCH}/ 下")


def parse_mapping(items: list[str], flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"{flag} 需要 KEY=VALUE 形式，收到 {item!r}")
        key, value = item.split("=", 1)
        if not key or not value:
            raise ValueError(f"{flag} 的 KEY/VALUE 不能为空：{item!r}")
        out[key.strip()] = value.strip()
    return out


def prior_maps(prior_paths: list[Path]) -> tuple[dict, dict, list]:
    slug_map: dict[str, str] = {}
    host_map: dict[str, str] = {}
    added: list[dict] = []
    for path in prior_paths:
        d = json.loads(path.read_text(encoding="utf-8"))
        for s in d["scores"]:
            sec = s.get("secondary", {})
            if sec.get("slug"):
                slug_map.setdefault(sec["slug"], s["model"])
            if sec.get("hostModelSlug"):
                host_map.setdefault(sec["hostModelSlug"], s["model"])
            if sec.get("hostConfigId"):
                host_map.setdefault(sec["hostConfigId"], s["model"])
        added.append({"file": path.name, "inheritedSlugs": len(slug_map), "inheritedHosts": len(host_map)})
    return slug_map, host_map, added


def coding_rows(agents_html: Path) -> list[dict]:
    d = decode_rsc(agents_html.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, dict] = {}
    for m in re.finditer(r'\{"id":"', d):
        try:
            obj, _ = DEC.raw_decode(d[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and {"displayLabel", "indexScore"} <= set(obj):
            out[obj["id"]] = obj
    return [out[k] for k in out]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models-json", required=True, help="aa_fetch_parse.py 产出的载荷 JSON")
    parser.add_argument("--agents-html", required=True, help="coding-agents 页原始 HTML")
    parser.add_argument("--models-html", help="models 页原始 HTML（给了才写 extraction.payloadSha256）")
    parser.add_argument("--collected-at", required=True, help="采集日 YYYY-MM-DD")
    parser.add_argument("--round", type=int, required=True, help="快照轮次号 N（写进文件名与 revises 文本）")
    parser.add_argument("--prior", action="append", default=[], required=True,
                        help="上一版快照（可重复；名字在 data/research/ 下或给路径）")
    parser.add_argument("--slug-map", action="append", default=[],
                        help="新增智力榜 slug→项目模型 id 的精确映射（可重复）")
    parser.add_argument("--host-map", action="append", default=[],
                        help="新增 coding-agents hostModelSlug→项目模型 id 的精确映射（可重复）")
    parser.add_argument("--revises", help="被替换的上一版路径（默认 data/research/<第一个 --prior>）")
    parser.add_argument("--revision-note", help="覆盖 revisionNote 文本")
    parser.add_argument("--output", help="输出路径（默认 data/research/scores-aa-round<N>-<collected-at>.json）")
    parser.add_argument("--force", action="store_true", help="输出文件已存在时允许覆盖")
    parser.add_argument("--temp-dir", help="写进 extraction.tempDir 的工作目录（默认 _build/aa-update-round<N>/）")
    parser.add_argument("--models-url", default=MODELS_URL)
    parser.add_argument("--agents-url", default=AGENTS_URL)
    args = parser.parse_args()

    try:
        date.fromisoformat(args.collected_at)
    except ValueError:
        print(f"ERROR: --collected-at 需要 YYYY-MM-DD，收到 {args.collected_at!r}", file=sys.stderr)
        return 1

    try:
        prior_paths = [resolve_prior(p) for p in args.prior]
        new_slugs = parse_mapping(args.slug_map, "--slug-map")
        new_hosts = parse_mapping(args.host_map, "--host-map")
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    models_json = Path(args.models_json)
    agents_html = Path(args.agents_html)
    for p in (models_json, agents_html):
        if not p.is_file():
            print(f"ERROR: 读不到 {p}（models-json 由 aa_fetch_parse.py 产出；agents-html 是原始 HTML）。",
                  file=sys.stderr)
            return 1

    out = Path(args.output) if args.output else RESEARCH / f"scores-aa-round{args.round}-{args.collected_at}.json"
    if not out.is_absolute():
        out = ROOT / out
    if out.exists() and not args.force:
        print(f"ERROR: {out} 已存在；换 --output 或显式 --force（默认拒绝静默覆盖上一版快照）。", file=sys.stderr)
        return 1

    payload = json.loads(models_json.read_text(encoding="utf-8"))
    slug_map, host_map, inherited = prior_maps(prior_paths)
    slug_map.update(new_slugs)
    host_map.update(new_hosts)

    ranked = sorted((r for r in payload if r.get("intelligenceIndex") is not None),
                    key=lambda r: -r["intelligenceIndex"])
    rank = {r["slug"]: i + 1 for i, r in enumerate(ranked)}
    print(f"payload {len(payload)} rows, scored {len(ranked)}", file=sys.stderr)

    scores: list[dict] = []
    for r in payload:
        model = slug_map.get(r["slug"])
        if not model or r.get("intelligenceIndex") is None:
            continue
        sec = {
            "configurationRank": rank[r["slug"]],
            "costPerTaskUsd": (round(r["intelligenceIndexCostPerTask"], 4)
                               if r.get("intelligenceIndexCostPerTask") is not None else None),
            "scoreRoundedDisplay": round(r["intelligenceIndex"]),
            "intelligenceIndexIsEstimated": r.get("intelligenceIndexIsEstimated"),
            "slug": r["slug"],
            "shortName": r.get("shortName") or r["name"],
            "isReasoning": r.get("isReasoning"),
            "deprecated": r.get("deprecated"),
            "modelCreatorName": r.get("modelCreatorName"),
            "effort": effort_of(r.get("shortName") or r["name"]),
            "terminalBench40": r.get("terminalBench40"),
            "scicode": r.get("scicode"),
            "hle": r.get("hle"),
            # AA 载荷自带的三段价，纯附加字段：benchmark_configs.configuration() 用它算
            # 每个配置自己的 benchmark_list_price，compute.load_list_prices() 的 AA 兜底
            # 也读它；不参与 score / variantLabel / rank / 舍入口径。
            "price1mInputTokens": r.get("price1mInputTokens"),
            "price1mOutputTokens": r.get("price1mOutputTokens"),
            "cacheHitPrice": r.get("cacheHitPrice"),
        }
        scores.append({
            "model": model, "boardId": "aa_intelligence_index",
            "variantLabel": r.get("shortName") or r["name"],
            "score": round(r["intelligenceIndex"], 4), "secondary": sec,
            "source": args.models_url, "checkedAt": args.collected_at,
            **({"note": r["note"]} if r.get("note") else {}),
        })
    print(f"curated intelligence rows: {len(scores)}", file=sys.stderr)

    tb = 0
    for r in payload:
        model = slug_map.get(r["slug"])
        if not model or r.get("terminalBench40") is None:
            continue
        scores.append({
            "model": model, "boardId": "aa_terminal_bench_4",
            "variantLabel": "Artificial Analysis - " + (r.get("shortName") or r["name"]),
            "score": round(r["terminalBench40"] * 100, 4),
            "secondary": {
                "configurationRank": rank.get(r["slug"]),
                "agentHarness": "Artificial Analysis",
                "reasoningEffort": effort_of(r.get("shortName") or r["name"]),
                "slug": r["slug"], "shortName": r.get("shortName") or r["name"],
                "terminalBench40Fraction": r["terminalBench40"],
            },
            "source": args.models_url, "checkedAt": args.collected_at,
            "note": "AA 自家 harness 独立实测的 Terminal-Bench 4.0（models 榜载荷 terminalBench40 字段，fraction×100）；与 tbench.ai 官方榜分开计分。",
        })
        tb += 1
    print(f"curated tb4 rows: {tb}", file=sys.stderr)

    agents = coding_rows(agents_html)
    ca = 0
    for a in agents:
        host_key = a.get("hostModelSlug") or a.get("id")
        model = host_map.get(host_key)
        if not model:
            continue
        ev = a.get("mean", {})
        scores.append({
            "model": model, "boardId": "aa_coding_agent_index",
            "variantLabel": a["displayLabel"], "score": round(a["indexScore"] * 100, 4),
            "secondary": {
                "configurationRank": None, "agentHarness": a["agentName"], "provider": a.get("provider"),
                "hostModelSlug": a.get("hostModelSlug") or a.get("id"), "displayModel": (a.get("display") or {}).get("model"),
                "isUnavailable": a.get("isUnavailable"), "variantOf": a.get("variantOf"),
                "deepSWE": (a.get("evals") or [{}])[0].get("score") if a.get("evals") else None,
                "meanCostUsdPerTask": ev.get("costUsd"), "agentWallTimeSec": ev.get("agentWallTimeSec"),
                "meanSteps": ev.get("steps"), "meanTotalTokens": ev.get("totalTokens"),
                "cacheHitRate": ev.get("cacheHitRate"), "hostConfigId": a["id"],
                "versions": a.get("versions"),
            },
            "source": args.agents_url, "checkedAt": args.collected_at,
        })
        ca += 1
    print(f"curated coding rows: {ca} (of {len(agents)} configs)", file=sys.stderr)

    unmatched = sorted({(a.get("hostModelSlug") or a.get("id"), a["agentName"]) for a in agents if (a.get("hostModelSlug") or a.get("id")) not in host_map})
    print(f"unmapped coding hosts: {len(unmatched)}", file=sys.stderr)
    for u in unmatched[:15]:
        print("   ", u, file=sys.stderr)

    if args.models_html:
        models_html = Path(args.models_html)
        if not models_html.is_file():
            print(f"ERROR: --models-html {models_html} 读不到", file=sys.stderr)
            return 1
        payload_sha = sha256_of(models_html)
    else:
        payload_sha = None
        print("WARN: 未给 --models-html，extraction.payloadSha256 将写 null（§0.3 的早退判据会失效）",
              file=sys.stderr)
    agents_sha = sha256_of(agents_html)

    revises = args.revises or f"data/research/{prior_paths[0].name}"
    revision_note = args.revision_note or (
        f"全量刷新 {args.collected_at} AA models 榜与 coding-agents 页，替换 {prior_paths[0].name} 的 "
        f"aa_intelligence_index / aa_terminal_bench_4 / aa_coding_agent_index 三个榜。"
        + (f"新增模型映射 {len(new_slugs)} 条。" if new_slugs else "")
    )
    doc = {
        "collectedAt": args.collected_at,
        "revises": revises,
        "revisionNote": revision_note,
        "extraction": {
            "method": (f"{args.collected_at} curl 下载公开 HTML；RSC flight push 载荷解码后取 models 数组"
                       f"（去重按 slug）与 coding-agents 配置数组（按 id 去重）。"),
            "tempDir": args.temp_dir or f"_build/aa-update-round{args.round}/",
            "pages": {"modelsLeaderboard": args.models_url, "codingAgents": args.agents_url},
            "payloadSha256": payload_sha,
            "agentsPayloadSha256": agents_sha,
            "counts": {
                "intelligencePayloadModels": len(payload),
                "intelligenceScored": len(ranked),
                "codingConfigurations": len(agents),
                "adoptedServedModels": len({s["model"] for s in scores}),
            },
            "mappingRule": "沿用 prior 快照的 slug 与 hostModelSlug 映射；新增项见 mappingAdditions，仅精确同名匹配，不做近似。",
            "mappingAdditions": {"intelligenceSlug": new_slugs, "codingHost": new_hosts, "inheritedFiles": inherited},
        },
        "boards": [
            {"boardId": "aa_intelligence_index", "name": "Artificial Analysis Intelligence Index v4.3",
             "url": args.models_url, "metric": "Intelligence Index", "snapshotDate": args.collected_at,
             "note": f"Fresh download {args.collected_at}; scores from public HTML RSC embedded model payload; page UI rounds to integer."},
            {"boardId": "aa_coding_agent_index", "name": "Artificial Analysis Coding Agent Index v1.5",
             "url": args.agents_url, "metric": "Coding Agent Index", "snapshotDate": args.collected_at,
             "note": f"Fresh download {args.collected_at}; {len(agents)} configurations."},
            {"boardId": "aa_terminal_bench_4", "name": "Terminal-Bench 4.0 (AA)", "url": args.models_url,
             "metric": "Resolution Rate %", "snapshotDate": args.collected_at,
             "note": "AA 自家 harness 独立实测的 Terminal-Bench 4.0：取 models 榜载荷 terminalBench40 字段（fraction×100）；与 tbench.ai 官方榜分开计分。"},
        ],
        "scores": scores,
        "unmappedPublicRows": {"aa_coding_agent_index": unmatched},
        "notFound": [],
        "rawRecords": {
            "aa_coding_agent_index": agents,
            "aa_intelligence_index": payload,
            "note": "AA 公开载荷原样留存，供 verify_aa_snapshot.py 交叉核对。",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {out.relative_to(ROOT) if str(out).startswith(str(ROOT)) else out}")

    hk = [s for s in scores if s["model"] in set(new_slugs.values())]
    for s in hk:
        print("  new-model row:", s["boardId"], s["variantLabel"], round(s["score"], 4),
              "rank", s["secondary"].get("configurationRank"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
