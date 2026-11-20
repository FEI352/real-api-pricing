#!/usr/bin/env python3
"""Structured live probe & local/deployed parity check for one model.

Fetches live Artificial Analysis (leaderboard + per-effort model page tokens)
and live OpenRouter (all provider endpoints with pricing, p50 latency, p50 TPS),
then compares against derived/points.json, derived/benchmark-configurations.json,
and deployed /var/www/real-plan/data/site.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import aa_fetch_parse  # noqa: E402
import or_tasktime  # noqa: E402

DEPLOY_SITE = Path("/var/www/real-plan/data/site.json")


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def fetch_aa_live(model: str, aa_slug: str | None) -> list[dict]:
    html = or_tasktime.get("https://artificialanalysis.ai/leaderboards/models")
    rows = aa_fetch_parse.biggest_models_array(aa_fetch_parse.decode_rsc(html))
    key = norm_key(aa_slug or model)
    hits = []
    for r in rows:
        cand = norm_key(r.get("slug") or r.get("shortName") or r.get("name") or "")
        if key in cand:
            hits.append(r)
    # Grab per-effort output tokens from AA model page
    base_slug = re.sub(r"-(?:xhigh|high|medium|low|non-reasoning|minimal|max)$", "", aa_slug or model.replace(".", "-"))
    tok_blocks: list[tuple[float | None, dict]] = []
    for cand_slug in dict.fromkeys([base_slug, f"tencent-{base_slug}"]):
        try:
            m_html = or_tasktime.get(or_tasktime.AA_PAGE.format(slug=cand_slug))
            dec = m_html.replace('\\"', '"').replace("\\\\", "\\")
            for match in re.finditer(r'"intelligenceIndexOutputTokensPerTask"\s*:\s*(\{[^}]+\})', dec):
                obj = json.loads(match.group(1))
                ctx = dec[max(0, match.start() - 1200):match.end() + 400]
                cpt_m = re.findall(r'"cost"\s*:\s*\{\s*"total"\s*:\s*([0-9.]+)', ctx)
                cpt_val = float(cpt_m[-1]) if cpt_m else None
                tok_blocks.append((cpt_val, obj))
            if tok_blocks:
                break
        except Exception:
            continue

    out = []
    for r in hits:
        cpt = r.get("intelligenceIndexCostPerTask")
        tb4 = r.get("terminalBench40")
        if tb4 is None and isinstance(r.get("scores"), dict):
            tb4 = r["scores"].get("terminalbench-4-0")
        matched_tok = None
        if isinstance(cpt, (int, float)):
            matched_tok = next(
                (b[1] for b in tok_blocks if b[0] is not None and abs(b[0] - float(cpt)) < 0.002),
                None,
            )
        out.append({
            "name": r.get("shortName") or r.get("name"),
            "ii": round(r["intelligenceIndex"], 4) if isinstance(r.get("intelligenceIndex"), (int, float)) else None,
            "tb4_pct": round(tb4 * 100, 4) if isinstance(tb4, (int, float)) else None,
            "cpt_usd": round(float(cpt), 4) if isinstance(cpt, (int, float)) else None,
            "tokens_per_task": round(float(matched_tok["output"]), 1) if matched_tok else None,
            "reasoning_tokens": round(float(matched_tok.get("reasoning") or 0), 1) if matched_tok else None,
            "answer_tokens": round(float(matched_tok.get("answer") or 0), 1) if matched_tok else None,
            "price_in": r.get("price1mInputTokens"),
            "price_out": r.get("price1mOutputTokens"),
            "price_cache": r.get("cacheHitPrice"),
        })
    return sorted(out, key=lambda x: -(x["ii"] or -1))


def fetch_or_live(model: str, or_slug: str | None) -> dict:
    models = json.loads(or_tasktime.get(or_tasktime.MODELS_URL)).get("data", [])
    idx = or_tasktime.build_or_index(models)
    picked_id = or_slug
    if not picked_id:
        for k in (or_tasktime.OR_ALIASES.get(model), or_tasktime.norm(model), model.lower()):
            if k and k in idx:
                picked_id = idx[k]["id"]
                break
    if not picked_id:
        return {"or_id": None, "endpoints": [], "best": None}

    ep_meta = {}
    try:
        ep_json = json.loads(or_tasktime.get(f"https://openrouter.ai/api/v1/models/{picked_id}/endpoints"))
        for e in ep_json.get("data", {}).get("endpoints", []):
            ep_meta[e.get("tag")] = e
    except Exception:
        pass

    html = or_tasktime.get(or_tasktime.OR_PAGE.format(slug=picked_id))
    best = or_tasktime.parse_or_page(html)

    # Extract per-endpoint stats + name/tag from page payload
    ep_names = dict(re.findall(r'"([0-9a-f-]{36})":"([^"]+)"', html.replace('\\"', '"')))
    endpoints = []
    dec = html.replace('\\"', '"')
    for m in re.finditer(
        r'"endpoint_id":"([0-9a-f-]{36})","latency_metric":"[^"]*","p50_latency":([0-9.]+),.*?"p50_throughput":([0-9.]+)',
        dec,
    ):
        eid, lat_ms, tps = m.group(1), float(m.group(2)), float(m.group(3))
        endpoints.append({
            "endpoint_id": eid[:12],
            "label": ep_names.get(eid, "?"),
            "p50_latency_ms": lat_ms,
            "p50_tps": tps,
        })
    return {
        "or_id": picked_id,
        "best": {"p50_latency_ms": best[0], "p50_tps": best[1], "provider": best[2]} if best else None,
        "endpoints": sorted(endpoints, key=lambda x: -x["p50_tps"]),
        "pricing_tiers": [
            {
                "provider": v.get("provider_name"),
                "tag": k,
                "in_mtok": round(float(v["pricing"]["prompt"]) * 1e6, 4) if v.get("pricing", {}).get("prompt") else None,
                "out_mtok": round(float(v["pricing"]["completion"]) * 1e6, 4) if v.get("pricing", {}).get("completion") else None,
                "cache_mtok": round(float(v["pricing"]["input_cache_read"]) * 1e6, 4) if v.get("pricing", {}).get("input_cache_read") else None,
            }
            for k, v in ep_meta.items()
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, help="Project model id, e.g. gemini-4-neon")
    ap.add_argument("--openrouter-slug", help="Optional explicit OpenRouter slug, e.g. google/gemini-4-neon")
    ap.add_argument("--aa-slug", help="Optional explicit AA slug/name filter")
    args = ap.parse_args()

    print(f"=== Live Probe: {args.model} ===")
    aa_rows = fetch_aa_live(args.model, args.aa_slug)
    print(f"\n[1] Artificial Analysis (live {len(aa_rows)} variants):")
    for r in aa_rows:
        print(
            f"  {r['name']:28s} | II={r['ii']} | TB4={r['tb4_pct']}% | "
            f"AA_cpt=${r['cpt_usd']} | tok={r['tokens_per_task']} "
            f"(reason={r['reasoning_tokens']}, ans={r['answer_tokens']}) | "
            f"price=(cache={r['price_cache']}, in={r['price_in']}, out={r['price_out']})"
        )

    or_data = fetch_or_live(args.model, args.openrouter_slug)
    print(f"\n[2] OpenRouter live ({or_data['or_id']}):")
    if or_data["best"]:
        b = or_data["best"]
        print(f"  Best across providers: {b['provider']} -> {b['p50_tps']} tok/s, latency {b['p50_latency_ms']} ms")
    for e in or_data["endpoints"]:
        print(f"  endpoint {e['endpoint_id']} ({e['label']}): p50_tps={e['p50_tps']}, p50_latency_ms={e['p50_latency_ms']}")
    if or_data["pricing_tiers"]:
        print("  OpenRouter endpoint pricing tiers:")
        for t in or_data["pricing_tiers"]:
            print(f"    {t['tag']:32s} ({t['provider']}): cache=${t['cache_mtok']}, in=${t['in_mtok']}, out=${t['out_mtok']}")

    # Local vs deployed check
    pts = json.loads((ROOT / "derived" / "points.json").read_text(encoding="utf-8"))["points"]
    cfgs = json.loads((ROOT / "derived" / "benchmark-configurations.json").read_text(encoding="utf-8"))
    print(f"\n[3] Local derived/benchmark-configurations.json ({args.model}, aa_intelligence_index):")
    for c in cfgs:
        if c["model"] == args.model and c["board"] == "aa_intelligence_index":
            print(
                f"  {c['variant']:26s} | effort={str(c['reasoning_effort']):6s} | ii={c['score']} | "
                f"aa_cpt=${c['mean_cost_usd_per_task']} | bl=${c['benchmark_list_price']} | "
                f"tps={c['output_speed_tps']} | time={c['response_time_seconds']}s"
            )

    print(f"\n[4] Local derived/points.json vs Deployed site.json ({args.model}):")
    dep_map = {}
    if DEPLOY_SITE.exists():
        dep = json.loads(DEPLOY_SITE.read_text(encoding="utf-8"))
        dep_map = {p["id"]: p for p in dep.get("points", [])}
    for p in pts:
        if p["model"] != args.model:
            continue
        bl = p.get("aa_intelligence_index__benchmark_list_price") or p.get("list_blended_usd_per_mtok")
        cpt = p.get("aa_intelligence_index__mean_cost_usd_per_task")
        sub_cpt = round(cpt * p["real_usd_per_mtok"] / bl, 5) if (cpt and bl and p.get("real_usd_per_mtok")) else None
        dep_p = dep_map.get(p["id"])
        dep_sync = "IN_SYNC" if (dep_p and dep_p.get("real_usd_per_mtok") == p.get("real_usd_per_mtok") and dep_p.get("monthly_yi") == p.get("monthly_yi")) else "DRIFT/MISSING"
        print(
            f"  {p['id']:40s} | yi={str(p.get('monthly_yi')):7s} | real=${p['real_usd_per_mtok']:.6f} | "
            f"sub_cpt=${sub_cpt} | tps={p.get('aa_intelligence_index__output_speed_tps')} | "
            f"time={p.get('aa_intelligence_index__response_time_seconds')}s | deploy={dep_sync}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
