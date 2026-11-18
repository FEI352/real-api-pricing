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


def aa_pairs(snapshot: dict) -> list[tuple[str, str]]:
    """(project model id, AA slug) pairs across all currently active AA records (snapshot + supplements)."""
    seen: list[tuple[str, str]] = []
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
        slug = (row.get("secondary") or {}).get("slug")
        model = row.get("model")
        if slug and model and (model, slug) not in seen:
            seen.append((model, slug))
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


def aa_page_tokens(slug: str):
    cached = read_cache("aa", slug)
    if cached is not None:
        return cached
    try:
        html = get(AA_PAGE.format(slug=slug))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! AA {slug}: {exc}")
        return None
    obj = extract_object(html, "intelligenceIndexOutputTokensPerTask")
    if not isinstance(obj, dict) or not isinstance(obj.get("output"), (int, float)):
        print(f"  ! AA {slug}: no intelligenceIndexOutputTokensPerTask")
        return None
    record = {
        "tokens_per_task": round(float(obj["output"]), 1),
        "reasoning_tokens": round(float(obj.get("reasoning") or 0), 1),
        "answer_tokens": round(float(obj.get("answer") or 0), 1),
    }
    write_cache("aa", slug, record)
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


def parse_or_page(text: str):
    """Pick the provider row with the highest p50 throughput and take *its own* latency.

    Taking max throughput and min latency independently would invent a route nobody can
    buy — those two numbers usually come from different providers. The page advertises
    "P50, best across providers" for throughput, so the fastest provider is the headline,
    and its latency is what you actually get when you route to it.
    """
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
    return latency, throughput, provider


def or_stats(slug: str):
    cached = read_cache("or", slug)
    if cached is not None:
        return cached
    try:
        parsed = parse_or_page(get(OR_PAGE.format(slug=slug)))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! OR {slug}: {exc}")
        return None
    if parsed is None:
        print(f"  ! OR {slug}: no stats on page")
        return None
    latency_ms, tps, provider = parsed
    record = {"or_id": slug, "p50_latency_ms": latency_ms, "p50_throughput_tps": tps, "provider": provider}
    write_cache("or", slug, record)
    return record


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--no-latency", action="store_true")
    args = ap.parse_args()

    if args.show:
        latest = args.out or max(glob.glob(os.path.join(RESEARCH, "or-tasktime-*.json")), default="")
        if not latest:
            print("no map yet")
            return 1
        print(open(latest, encoding="utf-8").read()[:4000])
        return 0

    os.makedirs(CACHE_DIR, exist_ok=True)
    snapshot = latest_snapshot()
    pairs = aa_pairs(snapshot)
    if args.limit:
        pairs = pairs[: args.limit]
    print(f"AA snapshot: {snapshot.get('collectedAt')}  model→slug pairs: {len(pairs)}")

    # AA tokens per exact slug (the effort-specific slug is what the config points at)
    aa_tokens: dict[str, dict] = {}
    slugs = sorted({slug for _, slug in pairs})
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for slug, record in zip(slugs, pool.map(aa_page_tokens, slugs)):
            if record:
                aa_tokens[slug] = record
    print(f"AA tokens: {len(aa_tokens)}/{len(slugs)} slugs")

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
    for model, slug in pairs:
        if model in picked:
            continue
        hit = or_pick(model, slug)
        if hit is not None:
            picked[model] = hit
    print(f"OpenRouter: {len(picked)}/{len({m for m, _ in pairs})} models matched")

    or_stats_by_model: dict[str, dict] = {}
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for model, record in zip(picked, pool.map(lambda kv: or_stats(kv[1]["id"]), picked.items())):
            if record:
                or_stats_by_model[model] = record

    by_model_slug: dict[str, dict] = {}
    no_aa: list[str] = []
    derived = aa_derived_tokens()
    for model, slug in pairs:
        tokens = aa_tokens.get(slug)
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
            "p50_latency_ms": or_row["p50_latency_ms"],
            "p50_throughput_tps": tps,
            "latency_used_s": round(latency, 2),
            "generation_s": round(generation, 2),
            "task_time_s": round(latency + generation, 2),
        }

    by_model: dict[str, dict] = {}
    for key, value in by_model_slug.items():
        by_model.setdefault(key.split("::")[0], value)

    stamp = time.strftime("%Y-%m-%d")
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
    out = args.out or os.path.join(RESEARCH, f"or-tasktime-{stamp}.json")
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