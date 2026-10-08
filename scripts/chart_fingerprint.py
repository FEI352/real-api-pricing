#!/usr/bin/env python3
"""Fingerprint the published chart artifacts: turn "did the charts really change?" into one command.

Scans ``charts/`` and records sha256 + size + mtime for every published file (zh/en, overview /
pareto / frontier).  The JSON is meant to be committed as a baseline under ``data/research/``;
``--compare`` then classifies the current tree against that baseline as
added / removed / changed / unchanged and exits 0 (identical) or 1 (differences), so agents and CI
can judge "stale artifact vs real change" without eyeballing files.

Comparison ignores mtime on purpose: a re-render that lands byte-identical is *unchanged*.

PNG determinism (measured 2026-10-08 on this machine): re-rendering the same SVGs through
``node scripts/render_svg.cjs`` (sharp, density 144) produced byte-identical PNGs - 78/78 files
unchanged, including the 32 stems that were actually re-rendered.  PNGs are therefore fingerprinted
too; pass ``--ignore '*.png'`` to compare SVG/TXT only.

Usage (repository interpreter):
  .venv/bin/python scripts/chart_fingerprint.py
  .venv/bin/python scripts/chart_fingerprint.py --write data/research/chart-fingerprint-2026-10-08.json
  .venv/bin/python scripts/chart_fingerprint.py --compare data/research/chart-fingerprint-2026-10-08.json
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def data_snapshot(root: Path) -> str | None:
    """`conventions.json` updatedAt is the data snapshot the charts were built from."""
    try:
        return json.loads((root / "data" / "conventions.json").read_text(encoding="utf-8")).get("updatedAt")
    except (OSError, json.JSONDecodeError):
        return None


def is_ignored(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(Path(rel).name, p) for p in patterns)


def scan(root: Path, ignore: list[str]) -> dict:
    charts = root / "charts"
    if not charts.is_dir():
        raise SystemExit(f"charts/ not found under {root}")
    files: dict[str, dict] = {}
    by_dir: dict[str, int] = {}
    for path in sorted(charts.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_ignored(rel, ignore):
            continue
        stat = path.stat()
        files[rel] = {
            "sha256": sha256_of(path),
            "bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        }
        parent = Path(rel).parent.as_posix()
        by_dir[parent] = by_dir.get(parent, 0) + 1
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "dataSnapshot": data_snapshot(root),
        "root": root.name,
        "ignore": list(ignore),
        "files": files,
        "summary": {
            "total": len(files),
            "totalBytes": sum(entry["bytes"] for entry in files.values()),
            "byDir": dict(sorted(by_dir.items())),
        },
    }


def diff(current: dict, baseline: dict) -> dict:
    old = baseline.get("files", {})
    new = current["files"]
    old_keys, new_keys = set(old), set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = sorted(old_keys & new_keys)
    changed = [k for k in common if new[k]["sha256"] != old[k]["sha256"]]
    unchanged = [k for k in common if new[k]["sha256"] == old[k]["sha256"]]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(unchanged),
        },
        "baselineGeneratedAt": baseline.get("generatedAt"),
        "baselineDataSnapshot": baseline.get("dataSnapshot"),
        "currentDataSnapshot": current.get("dataSnapshot"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root to scan (default: this repo)")
    parser.add_argument("--compare", metavar="BASELINE", help="compare current charts/ against a saved fingerprint")
    parser.add_argument("--write", metavar="PATH", help="write the current fingerprint JSON to PATH")
    parser.add_argument("--ignore", action="append", default=[], metavar="GLOB", help="skip matching paths (repeatable)")
    parser.add_argument("--show-unchanged", action="store_true", help="list unchanged files too")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result on stdout")
    parser.add_argument("--quiet", action="store_true", help="suppress the human-readable report")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    current = scan(root, args.ignore)
    result: dict = {"fingerprint": current, "compare": None}

    if args.write:
        target = Path(args.write)
        if not target.is_absolute():
            target = root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"wrote {target} ({current['summary']['total']} files, "
                  f"{current['summary']['totalBytes']} bytes, dataSnapshot={current['dataSnapshot']})")

    if args.compare:
        baseline_path = Path(args.compare)
        if not baseline_path.is_absolute():
            baseline_path = root / baseline_path
        if not baseline_path.exists():
            print(f"baseline not found: {baseline_path}", file=sys.stderr)
            return 2
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        result["compare"] = diff(current, baseline)
        counts = result["compare"]["counts"]
        exit_code = 1 if (counts["added"] or counts["removed"] or counts["changed"]) else 0
        result["compare"]["exitCode"] = exit_code

        if not args.quiet:
            print(f"baseline {baseline_path} (generatedAt={result['compare']['baselineGeneratedAt']}, "
                  f"dataSnapshot={result['compare']['baselineDataSnapshot']})")
            print(f"current  dataSnapshot={result['compare']['currentDataSnapshot']} "
                  f"files={current['summary']['total']}")
            for label in ("added", "removed", "changed"):
                print(f"{label:<9}: {counts[label]}")
                for rel in result["compare"][label]:
                    if label == "changed":
                        print(f"    {rel}  {baseline['files'][rel]['sha256'][:12]} -> "
                              f"{current['files'][rel]['sha256'][:12]}")
                    else:
                        print(f"    {rel}")
            print(f"unchanged: {counts['unchanged']}")
            if args.show_unchanged:
                for rel in result["compare"]["unchanged"]:
                    print(f"    {rel}")
            verdict = "NO CHANGE (exit 0)" if exit_code == 0 else "CHANGED (exit 1)"
            print(f"verdict  : {verdict}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.compare:
        return result["compare"]["exitCode"]
    return 0


if __name__ == "__main__":
    sys.exit(main())
