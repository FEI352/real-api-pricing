#!/usr/bin/env python3
"""Build the task-time map: tokens-per-task from AA, throughput from OpenRouter.

Why two sources
---------------
AA stopped publishing `medianEndToEndResponseTimeSeconds` / `medianOutputTokensPerSecond`
after the 2026-11-04 snapshot. Neither side can stand alone:

* **tokens per task — AA.** AA publishes it outright as
  `intelligenceIndexOutputTokensPerTask {reasoning, answer, output}` on every model page.
  That is the only honest measure of how long a benchmark answer is; deriving it from
  `tokens/s × time` gives 500-ish placeholders and is wrong by two orders of magnitude
  (gpt-6-sol is really 53,890 output tokens per task, not 500).
* **throughput — OpenRouter.** Live per provider `p50_throughput` (tok/s), refreshed on
  demand; AA's own column is gone.

    task_time = p50_latency_ms/1000 + tokens_per_task / p50_throughput_tps

The latency term is AA's time-to-first-token equivalent — AA's own end-to-end figure
includes it, so dropping it would flatter slow-first-token models (`--no-latency`).

Usage
-----
    .venv/bin/python scripts/or_tasktime.py              # full run
    .venv/bin/python scripts/or_tasktime.py --limit 5    # smoke test
    .venv/bin/python scripts/or_tasktime.py --show       # print the map
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import glob
import json
import os
import re
import shutil
import statistics as st
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(ROOT, "data", "research")
MODELS_URL = "https://openrouter.ai/api/v1/models"
OR_PAGE = "https://openrouter.ai/{slug}"
AA_PAGE = "https://artificialanalysis.ai/models/{slug}"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
TIMEOUT = 30
WORKERS = 6
CACHE_DIR = os.path.join(ROOT, "_build", "or-tasktime-cache")

LAT_RE = re.compile(r'\\?"p50_latency\\?":\s*([0-9]+(?:\.[0-9]+)?)')
TP_RE = re.compile(r'\\?"p50_throughput\\?":\s*([0-9]+(?:\.[0-9]+)?)')
PROV_RE = re.compile(r'\\"provider_display_name\\":\\"([^"\\]+)\\"')
STATS_RE = re.compile(
    r'\\"stats\\":\{\\"endpoint_id\\":\\"[^"\\]*\\",\\"latency_metric\\":\\"[^"\\]*\\",'
    r'\\"p50_latency\\":([0-9.]+),.*?\\"p50_throughput\\":([0-9.]+)'
)


def norm(name: str) -> str:
    """AA slugs and project ids disagree on dots vs dashes: gpt-6.1-sol ↔ gpt-6-1-sol."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def get(url: str, tries: int = 3) -> str:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def cache_path(prefix: str, slug: str) -> str:
    return os.path.join(CACHE_DIR, f"{prefix}-{slug.replace('/', '__')}.json")


def read_cache(prefix: str, slug: str):
    path = cache_path(prefix, slug)
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def write_cache(prefix: str, slug: str, payload) -> None:
    try:
        json.dump(payload, open(cache_path(prefix, slug), "w", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- AA

def aa_derived_tokens() -> dict[str, float]:
    """Fallback token counts from AA's own two columns, for slugs whose model page has no
    `intelligenceIndexOutputTokensPerTask` block. Still AA data — just a coarser column."""
    buckets: dict[str, list[float]] = {}
    for path in sorted(glob.glob(os.path.join(RESEARCH, "scores-aa-*.json"))):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for rows in (data.get("rawRecords") or {}).values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or not row.get("slug"):
                    continue
                rt = row.get("medianEndToEndResponseTimeSeconds")
                tps = row.get("medianOutputTokensPerSecond") or row.get("outputSpeed")
                if isinstance(rt, (int, float)) and isinstance(tps, (int, float)) and rt > 0 and tps > 0:
                    buckets.setdefault(row["slug"], []).append(rt * tps)
    return {slug: round(st.median(v), 1) for slug, v in buckets.items() if v}


def latest_snapshot() -> dict:
    """Newest dated round6 snapshot. The supplement files sort after the dated ones
    lexicographically, so pick by the date in the name, not by string order."""
    best, best_path = "", ""
    for path in glob.glob(os.path.join(RESEARCH, "scores-aa-round6-*.json")):
        found = re.search(r"scores-aa-round6-(\d{4}-\d{2}-\d{2})\.json$", os.path.basename(path))
        if found and found.group(1) > best:
            best, best_path = found.group(1), path
    if not best_path:
        return {}
    try:
        return json.load(open(best_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def aa_pairs(snapshot: dict) -> list[tuple[str, str, float | None]]:
    """(project model id, AA slug, costPerTaskUsd) across all currently active AA records (snapshot + supplements)."""
    seen: list[tuple[str, str, float | None]] = []
    seen_keys: set[tuple[str, str]] = set()
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import compute  # noqa: PLC0415

        archives = [
            (fn, json.loads(open(os.path.join(RESEARCH, fn), encoding="utf-8").read()))
            for fn in compute.SCORE_FILES
            if os.path.exists(os.path.join(RESEARCH, fn))
        ]
        records = [
            row
            for _, row in compute.current_score_records(archives)
            if row.get("boardId") == "aa_intelligence_index"
        ]
    except Exception:  # noqa: BLE001
        records = snapshot.get("scores", [])

    for row in records:
        sec = row.get("secondary") or {}
        slug = sec.get("slug")
        model = row.get("model")
        cpt = sec.get("costPerTaskUsd")
        cpt_val = float(cpt) if isinstance(cpt, (int, float)) else None
        if slug and model and (model, slug) not in seen_keys:
            seen_keys.add((model, slug))
            seen.append((model, slug, cpt_val))
    return seen


def extract_object(html: str, key: str):
    """Pull a flat JSON object that follows `key` out of an escaped RSC payload."""
    i = html.find(key)
    if i < 0:
        return None
    start = html.find("{", i)
    if start < 0:
        return None
    depth = 0
    for k in range(start, len(html)):
        char = html[k]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blob = html[start:k + 1].replace("\\\\", "\\").replace('\\"', '"')
                try:
                    return json.loads(blob)
                except Exception:  # noqa: BLE001
                    return None
    return None


def aa_page_tokens(slug: str, cpt_usd: float | None = None, model_id: str | None = None):
    cache_key = f"{slug}@{cpt_usd:.4f}" if cpt_usd is not None else slug
    cached = read_cache("aa", cache_key)
    if cached is not None:
        return cached
    base_slug = re.sub(r"-(?:xhigh|high|medium|low|non-reasoning|minimal|max)$", "", slug)
    cands = [slug]
    if base_slug != slug:
        cands.append(base_slug)
    if re.fullmatch(r"[0-9a-f-]{36}", slug) and model_id:
        m_slug = norm(model_id)
        cands.extend([m_slug, f"tencent-{m_slug}"])
    html = None
    last_exc = None
    for cand_slug in dict.fromkeys(cands):
        try:
            html = get(AA_PAGE.format(slug=cand_slug))
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if html is None:
        print(f"  ! AA {slug}: {last_exc}")
        return None
    # Decode escaped quotes so multi-effort comparison blocks match cleanly
    dec = html.replace('\\"', '"').replace("\\\\", "\\")
    blocks: list[tuple[float | None, dict]] = []
    for match in re.finditer(r'"intelligenceIndexOutputTokensPerTask"\s*:\s*(\{[^}]+\})', dec):
        try:
            obj = json.loads(match.group(1))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("output"), (int, float)):
            continue
        ctx = dec[max(0, match.start() - 1200):match.end() + 400]
        cpt_m = re.findall(r'"cost"\s*:\s*\{\s*"total"\s*:\s*([0-9.]+)', ctx)
        cpt_val = float(cpt_m[-1]) if cpt_m else None
        blocks.append((cpt_val, obj))
    if not blocks:
        print(f"  ! AA {slug}: no intelligenceIndexOutputTokensPerTask")
        return None
    chosen = blocks[0][1]
    if cpt_usd is not None:
        hit = next((b[1] for b in blocks if b[0] is not None and abs(b[0] - cpt_usd) < 0.002), None)
        if hit is not None:
            chosen = hit
    record = {
        "tokens_per_task": round(float(chosen["output"]), 1),
        "reasoning_tokens": round(float(chosen.get("reasoning") or 0), 1),
        "answer_tokens": round(float(chosen.get("answer") or 0), 1),
    }
    write_cache("aa", cache_key, record)
    return record


# -------------------------------------------------------------------- OpenRouter

def project_models() -> list[str]:
    out: list[str] = []
    with open(os.path.join(ROOT, "data", "adopted.csv"), encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            model = (row.get("served_model") or "").strip()
            if model and model not in out:
                out.append(model)
    return out


OR_ALIASES = {
    "mistral-large-4": "mistralai/mistral-large-4-0",
}


def build_or_index(models: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for model in models:
        mid = (model.get("id") or "").strip().lower()
        if ":" in mid:
            continue
        for key in (mid, (model.get("canonical_slug") or "").strip().lower()):
            if not key:
                continue
            index[key] = model
            tail = key.split("/")[-1]
            index.setdefault(tail, model)
            # AA slugs are dash-normalised; without these keys a versioned endpoint
            # (deepseek-v4.1-flash-1017) is never found and the wrong deployment wins.
            index.setdefault(norm(key), model)
            index.setdefault(norm(tail), model)
    return index


def parse_or_page_endpoints(text: str):
    """Extract all OpenRouter provider endpoint stats and pick the highest-throughput one.

    Taking max throughput and min latency independently would invent a route nobody can
    buy — those two numbers usually come from different providers. The page advertises
    "P50, best across providers" for throughput, so the fastest provider is the headline,
    and its latency is what you actually get when you route to it.
    """
    dec = text.replace('\\"', '"').replace("\\\\", "\\")
    prov_spans = [(m.group(1), m.start()) for m in re.finditer(r'"provider_display_name":"([^"]+)"', dec)]
    ep_names = dict(re.findall(r'"([0-9a-f-]{36})":"([^"]+)"', dec))
    endpoints = []
    for match in re.finditer(
        r'"stats":\{("endpoint_id":"([0-9a-f-]{36})","latency_metric":"[^"]*"[^}]*"p50_throughput":([0-9.]+)[^}]*)\}',
        dec,
    ):
        body, eid, tps = match.group(1), match.group(2), float(match.group(3))
        lat_m = re.search(r'"p50_latency":([0-9.]+)', body)
        if not lat_m:
            continue
        latency = float(lat_m.group(1))
        p75_m = re.search(r'"p75_throughput":([0-9.]+)', body)
        p90_m = re.search(r'"p90_throughput":([0-9.]+)', body)
        req_m = re.search(r'"throughput_request_count":([0-9]+)', body) or re.search(r'"request_count":([0-9]+)', body)
        owner = None
        for name, position in prov_spans:
            if position < match.start():
                owner = name
            else:
                break
        provider_name = owner or ep_names.get(eid) or "Unknown"
        endpoints.append({
            "provider": provider_name,
            "slug": ep_names.get(eid),
            "p50_tps": round(tps, 1),
            "p75_tps": round(float(p75_m.group(1)), 1) if p75_m else None,
            "p90_tps": round(float(p90_m.group(1)), 1) if p90_m else None,
            "p50_latency_ms": round(latency, 1),
            "requests": int(req_m.group(1)) if req_m else None,
        })
    if not endpoints:
        # Fallback to legacy regexes if OpenRouter alters endpoint JSON shape
        names = [(m.group(1), m.start()) for m in PROV_RE.finditer(text)]
        rows = []
        for match in STATS_RE.finditer(text):
            owner = None
            for name, position in names:
                if position < match.start() and (owner is None or position > owner[1]):
                    owner = (name, position)
            rows.append((float(match.group(2)), float(match.group(1)), owner[0] if owner else None))
        if not rows:
            return None
        throughput, latency, provider = max(rows)
        return latency, throughput, provider, []
    endpoints.sort(key=lambda e: (-e["p50_tps"], e["p50_latency_ms"]))
    best = endpoints[0]
    return best["p50_latency_ms"], best["p50_tps"], best["provider"], endpoints


def parse_or_page(text: str):
    res = parse_or_page_endpoints(text)
    if res is None:
        return None
    return res[0], res[1], res[2]


def or_stats(slug: str):
    cached = read_cache("or", slug)
    if cached is not None and "endpoints" in cached:
        return cached
    try:
        parsed = parse_or_page_endpoints(get(OR_PAGE.format(slug=slug)))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! OR {slug}: {exc}")
        return None
    if parsed is None:
        print(f"  ! OR {slug}: no stats on page")
        return None
    latency_ms, tps, provider, endpoints = parsed
    record = {
        "or_id": slug,
        "p50_latency_ms": latency_ms,
        "p50_throughput_tps": tps,
        "provider": provider,
        "endpoints": endpoints,
    }
    write_cache("or", slug, record)
    return record


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--no-latency", action="store_true")
    ap.add_argument("--no-cache", action="store_true", help="Clear _build/or-tasktime-cache before fetching")
    ap.add_argument("--model", default="", help="Only refresh matching project model id(s)")
    args = ap.parse_args()

    if args.show:
        latest = args.out or max(glob.glob(os.path.join(RESEARCH, "or-tasktime-*.json")), default="")
        if not latest:
            print("no map yet")
            return 1
        print(open(latest, encoding="utf-8").read()[:4000])
        return 0

    if args.no_cache and os.path.exists(CACHE_DIR):
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    snapshot = latest_snapshot()
    pairs = aa_pairs(snapshot)
    if args.model:
        kw = args.model.lower()
        pairs = [p for p in pairs if kw in p[0].lower()]
    if args.limit:
        pairs = pairs[: args.limit]
    print(f"AA snapshot: {snapshot.get('collectedAt')}  model→slug pairs: {len(pairs)}")

    # AA tokens per exact slug (matched by costPerTaskUsd on multi-effort pages)
    aa_tokens: dict[tuple[str, str, float | None], dict] = {}
    slug_cpts = sorted({(model, slug, cpt) for model, slug, cpt in pairs}, key=lambda sc: (sc[0], sc[1], -1.0 if sc[2] is None else sc[2]))
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for (model, slug, cpt), record in zip(slug_cpts, pool.map(lambda sc: aa_page_tokens(sc[1], sc[2], sc[0]), slug_cpts)):
            if record:
                aa_tokens[(model, slug, cpt)] = record
    print(f"AA tokens: {len(aa_tokens)}/{len(slug_cpts)} slug/effort pairs")

    # OpenRouter throughput per project model.
    # The endpoint must be the one AA actually measured. AA's slug carries the version
    # (deepseek-v4-1-flash-1017), and OpenRouter sells that as its own endpoint — matching
    # on the project model id alone silently grades the wrong deployment.
    or_index = build_or_index(json.loads(get(MODELS_URL))["data"])

    def or_pick(model: str, slug: str):
        alias = OR_ALIASES.get(model) or OR_ALIASES.get(slug)
        if alias and alias in or_index:
            return or_index[alias]
        for key in (norm(slug), slug.lower(), norm(model), model.lower(), f"{norm(model)}-0", f"{model.lower()}-0"):
            hit = or_index.get(key)
            if hit is not None:
                return hit
        return None

    picked: dict[str, dict] = {}
    for model, slug, _ in pairs:
        if model in picked:
            continue
        hit = or_pick(model, slug)
        if hit is not None:
            picked[model] = hit
    print(f"OpenRouter: {len(picked)}/{len({m for m, _, _ in pairs})} models matched")

    or_stats_by_model: dict[str, dict] = {}
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for model, record in zip(picked, pool.map(lambda kv: or_stats(kv[1]["id"]), picked.items())):
            if record:
                or_stats_by_model[model] = record

    by_model_slug: dict[str, dict] = {}
    no_aa: list[str] = []
    derived = aa_derived_tokens()
    for model, slug, cpt in pairs:
        tokens = aa_tokens.get((model, slug, cpt))
        source = "aa-page"
        if not tokens and slug in derived:
            tokens = {"tokens_per_task": derived[slug]}
            source = "aa-columns"
        or_row = or_stats_by_model.get(model)
        if not tokens or not or_row:
            if not tokens:
                no_aa.append(f"{model} ({slug})")
            continue
        tps = or_row["p50_throughput_tps"]
        generation = tokens["tokens_per_task"] / tps
        latency = 0.0 if args.no_latency else or_row["p50_latency_ms"] / 1000.0
        by_model_slug[f"{model}::{slug}"] = {
            "aa_slug": slug,
            "tokens_per_task": tokens["tokens_per_task"],
            "tokens_source": source,
            "reasoning_tokens": tokens.get("reasoning_tokens"),
            "answer_tokens": tokens.get("answer_tokens"),
            "or_id": or_row["or_id"],
            "or_url": f"https://openrouter.ai/{or_row['or_id']}",
            "or_provider": or_row.get("provider"),
            "p50_latency_ms": or_row["p50_latency_ms"],
            "p50_throughput_tps": tps,
            "latency_used_s": round(latency, 2),
            "generation_s": round(generation, 2),
            "task_time_s": round(latency + generation, 2),
            "or_endpoints": or_row.get("endpoints") or [],
        }

    by_model: dict[str, dict] = {}
    for key, value in by_model_slug.items():
        by_model.setdefault(key.split("::")[0], value)

    stamp = time.strftime("%Y-%m-%d")
    out = args.out or os.path.join(RESEARCH, f"or-tasktime-{stamp}.json")
    for row in by_model_slug.values():
        row["collected_at"] = stamp
    if args.model:
        # Merge into existing latest or-tasktime file so single-model refresh doesn't drop other models
        base_file = out if os.path.exists(out) else max(glob.glob(os.path.join(RESEARCH, "or-tasktime-*.json")), default="")
        if base_file and os.path.exists(base_file):
            prev = json.load(open(base_file, encoding="utf-8"))
            merged_slug = {k: v for k, v in (prev.get("byModelSlug") or {}).items() if args.model.lower() not in k.split("::")[0].lower()}
            merged_slug.update(by_model_slug)
            by_model_slug = dict(sorted(merged_slug.items()))
            merged_model = {k: v for k, v in (prev.get("byModel") or {}).items() if args.model.lower() not in k.lower()}
            merged_model.update(by_model)
            by_model = dict(sorted(merged_model.items()))
    payload = {
        "collectedAt": stamp,
        "aaSnapshot": snapshot.get("collectedAt"),
        "formula": "task_time_s = p50_latency_ms/1000 + tokens_per_task / p50_throughput_tps"
                   + ("   [latency term disabled]" if args.no_latency else ""),
        "tokensSource": "AA model pages — intelligenceIndexOutputTokensPerTask.output",
        "throughputSource": "OpenRouter model pages — p50_throughput (30-minute window)",
        "byModelSlug": dict(sorted(by_model_slug.items())),
        "byModel": dict(sorted(by_model.items())),
        "noAaTokens": sorted(no_aa),
    }
    json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote {out}  ({len(by_model_slug)} model/effort pairs, {len(by_model)} models)")
    if no_aa:
        print(f"  no AA tokens for {len(no_aa)} pairs, e.g. {', '.join(no_aa[:6])}")
    for key, row in list(by_model_slug.items())[:8]:
        print(f"  {key:44s} {row['tokens_per_task']:>9.0f} tok / {row['p50_throughput_tps']:>5.1f} tok/s "
              f"+ {row['latency_used_s']:>5.2f}s -> {row['task_time_s']:>8.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())