#!/usr/bin/env python3
"""统一校验入口 / Single entry point for the check chain.

按 BUILD.md 管线代码块里的既定顺序，依次执行其中所有 `scripts/checks/*` 校验，
逐条打印 OK / KNOWN-NOISE / FAIL，最后给汇总与退出码：

    .venv/bin/python scripts/checks/run_all.py

判定口径：
    OK          —— 退出码 0，且没有触发跳过。
    KNOWN-NOISE —— **仅** `verify_label_overlap.py` 在本机缺少 Microsoft YaHei
                   （度量字体与图内 font-family 首选不一致）或脚本自行 SKIP 时；
                   输出里写明原因，且该结果在 Windows/CI 上复验前不可判定。
    FAIL        —— 其他任何非零退出码，一律真失败，不降级、不静默。

退出码：有 FAIL → 1；只有 OK / KNOWN-NOISE → 0。

与逐条跑的区别：逐条跑要靠经验分辨「哪条红是真问题」（本地无 YaHei 时
label_overlap 必红）；本入口把这一条显式归类为 KNOWN-NOISE 并说明原因，
其余一律 FAIL。诊断单条 check 时仍以逐条跑为准。

清单来源：默认解析 BUILD.md 的 ```sh 代码块；解析失败时退回内置 FALLBACK_CHECKS。
解析成功后会做一次**漂移自检**——比对解析结果与 FALLBACK_CHECKS，集合或顺序不一致就打印
WARN（只提示，绝不改写 BUILD.md）：两边一旦漂移，解析失败那天就会静默漏跑守卫。
`--list-only` 只打印清单与自检结果、不执行任何 check；`--build-md <path>` 可指向副本（测试用）。
"""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_MD = ROOT / "BUILD.md"
LOG_DIR = ROOT / "_build" / "checks"
TIMEOUT_SECONDS = 900

# 唯一允许的已知噪声（缺 Microsoft YaHei 时的字形度量不可比）。
NOISE_ALLOWED = {"scripts/checks/verify_label_overlap.py"}

# BUILD.md 解析失败时的兜底顺序（与 BUILD.md 代码块逐行一致，不重排）。
FALLBACK_CHECKS = [
    "scripts/checks/verify_i18n.py",
    "scripts/checks/verify_benchmark_configs.py",
    "scripts/checks/verify_aa_snapshot.py",
    "scripts/checks/verify_deepswe.py",
    "scripts/checks/verify_aa_payload_fields.py",
    "scripts/checks/verify_haiku55_tiers.py",
    "scripts/checks/verify_list_blended.py",
    "scripts/checks/verify_scenario_points.py",
    "scripts/checks/verify_price_basis.py",
    "scripts/checks/verify_configuration_html.cjs",
    "scripts/checks/verify_fee_bands.py",
    "scripts/checks/verify_chart_labels.py",
    "scripts/checks/verify_svg.py",
    "scripts/checks/verify_label_overlap.py",
    "scripts/checks/verify_four_boards.py",
    "scripts/checks/verify_publication.py",
    "scripts/checks/verify_palette.py",
    "scripts/checks/verify_docs_refs.py",
]


def checks_from_build_md(path: Path = BUILD_MD) -> list[str]:
    """按 BUILD.md 第一个 ```sh 代码块的出现顺序提取校验脚本。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    block = re.search(r"```sh\n(.*?)```", text, re.S)
    if not block:
        return []
    order: list[str] = []
    for line in block.group(1).splitlines():
        match = re.match(r"^(?:python|node)\s+(scripts/checks/\S+)\s*$", line.strip())
        if match:
            order.append(match.group(1))
    return order


def drift_warnings(parsed: list[str], fallback: list[str] = FALLBACK_CHECKS) -> list[str]:
    """BUILD.md 解析结果与 FALLBACK_CHECKS 的一致性自检（只警告，不改写任何文件）。

    正常路径下清单来自 BUILD.md，FALLBACK 只在解析失败时兜底；两边一旦漂移，
    「解析失败的那天」就会静默漏跑守卫（2026-10-08：verify_list_blended /
    verify_scenario_points 曾缺席 FALLBACK）。所以解析成功时也把差集报出来：
    ① BUILD.md 独有（= FALLBACK 缺少）；② FALLBACK 独有（= FALLBACK 多出）；
    ③ 集合相同但顺序不同。
    """
    warnings: list[str] = []
    parsed_set, fallback_set = set(parsed), set(fallback)
    if parsed_set != fallback_set:
        only_parsed = sorted(parsed_set - fallback_set)
        only_fallback = sorted(fallback_set - parsed_set)
        detail = []
        if only_parsed:
            detail.append("FALLBACK 缺少（BUILD.md 独有）: " + ", ".join(only_parsed))
        if only_fallback:
            detail.append("FALLBACK 多出（BUILD.md 无）: " + ", ".join(only_fallback))
        warnings.append("WARN: BUILD.md 与 FALLBACK_CHECKS 清单漂移 — " + "；".join(detail))
    elif parsed != fallback:
        warnings.append(
            "WARN: BUILD.md 与 FALLBACK_CHECKS 集合相同但顺序不同"
            "（FALLBACK 注释承诺与 BUILD.md 逐行一致，请同步）"
        )
    return warnings


def python_executable() -> str:
    """优先用仓库 .venv 的解释器（BUILD.md 口径），否则退回当前解释器。"""
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def metric_font(py: str) -> str | None:
    """verify_label_overlap 实际使用的度量字体路径（None = 探测失败）。"""
    code = (
        "import sys;sys.path.insert(0,'scripts/checks');"
        "import verify_label_overlap as v;print(v.font_path() or '')"
    )
    try:
        done = subprocess.run(
            [py, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=180
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
    return lines[-1].strip() if lines else None


def classify_label_overlap(rc: int, output: str, font: str | None) -> tuple[str, str]:
    """label_overlap 的 OK / KNOWN-NOISE / FAIL 判定，必须可解释。"""
    if rc == 0:
        if "SKIP: chart fonts unavailable" in output:
            return (
                "KNOWN-NOISE",
                "脚本自行跳过（SKIP: chart fonts unavailable）；本机无 CJK 字体，"
                "CI 有字体时为真检查",
            )
        return "OK", ""
    if "找不到中文字体" in output or "No CJK font found" in output:
        return (
            "KNOWN-NOISE",
            "本机无 Microsoft YaHei / Noto Sans CJK SC，脚本无法按真实字形量标签框；"
            "CI 有字体时为真检查",
        )
    if font and not re.search(r"msyh|yahei", font, re.I):
        return (
            "KNOWN-NOISE",
            f"本机无 Microsoft YaHei（实际度量字体：{font}），而图内 font-family 首选 "
            '"Microsoft YaHei"，字形宽度不可比；该结果在 Windows/CI 上复验前不可判定，'
            "不计真失败",
        )
    return "FAIL", ""


def run_check(path: str, py: str) -> tuple[int, str, float]:
    command = ["node", path] if path.endswith(".cjs") else [py, path]
    started = time.monotonic()
    try:
        done = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
        output = (done.stdout or "") + (done.stderr or "")
        return done.returncode, output, time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return 124, output + f"\nTIMEOUT after {TIMEOUT_SECONDS}s", time.monotonic() - started


def first_pass_line(output: str) -> str:
    for line in reversed(output.splitlines()):
        if line.startswith("PASS:"):
            return line.strip()
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1].strip() if lines else "(no output)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="打印每条 check 的完整输出")
    parser.add_argument("--no-log", action="store_true", help="不写 _build/checks 日志")
    parser.add_argument(
        "--build-md", default=str(BUILD_MD),
        help="读取清单的 BUILD.md 路径（默认仓库根 BUILD.md；测试可指向 /tmp 副本）",
    )
    parser.add_argument("--list-only", action="store_true", help="只打印清单与漂移自检，不执行 check")
    args = parser.parse_args()

    build_md = Path(args.build_md)
    parsed = checks_from_build_md(build_md)
    if parsed:
        checks, source = parsed, str(build_md)
    else:
        checks, source = FALLBACK_CHECKS, f"built-in fallback order ({build_md} 解析失败)"
    py = python_executable()

    print(f"check chain: {len(checks)} checks, order from {source}")
    if parsed:
        for warning in drift_warnings(parsed):
            print(warning)
    print(f"python: {py} | node: node | cwd: {ROOT}")
    print("-" * 100)

    if args.list_only:
        for index, path in enumerate(checks, 1):
            print(f"[{index:>2}/{len(checks)}] {path}")
        print("list-only: nothing executed (exit 0)")
        return

    results: list[tuple[str, str, str, float]] = []  # status, path, note, seconds
    log_chunks: list[str] = []
    font = metric_font(py) if "scripts/checks/verify_label_overlap.py" in checks else None

    for index, path in enumerate(checks, 1):
        rc, output, seconds = run_check(path, py)
        if path in NOISE_ALLOWED:
            status, note = classify_label_overlap(rc, output, font)
        else:
            status, note = ("OK", "") if rc == 0 else ("FAIL", f"exit code {rc}")
        if status == "OK":
            note = first_pass_line(output)
        results.append((status, path, note, seconds))
        log_chunks.append(
            f"\n===== [{index}/{len(checks)}] {status} {path} (exit {rc}, {seconds:.1f}s)\n{output}"
        )
        print(f"[{index:>2}/{len(checks)}] {status:<11} {path}  ({seconds:.1f}s)")
        if status == "FAIL":
            tail = [line for line in output.splitlines() if line.strip()][-20:]
            for line in tail:
                print(f"           | {line}")
        else:
            print(f"           | {note}")
        if args.verbose and status != "FAIL":
            for line in output.splitlines():
                print(f"           > {line}")

    failures = [r for r in results if r[0] == "FAIL"]
    noises = [r for r in results if r[0] == "KNOWN-NOISE"]
    oks = [r for r in results if r[0] == "OK"]

    print("-" * 100)
    print(f"summary: {len(oks)} OK, {len(noises)} KNOWN-NOISE, {len(failures)} FAIL")
    if noises:
        print("KNOWN-NOISE (allowed, does not fail the run):")
        for _, path, note, _ in noises:
            print(f"  - {path}: {note}")
    if failures:
        print("FAIL (real failures):")
        for _, path, note, _ in failures:
            print(f"  - {path}: {note}")
    print(f"exit code: {1 if failures else 0}")

    if not args.no_log:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = LOG_DIR / f"run_all-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        log.write_text(
            f"check chain: {len(checks)} checks, order from {source}\npython: {py}\n"
            + "".join(log_chunks)
            + f"\n\nsummary: {len(oks)} OK, {len(noises)} KNOWN-NOISE, {len(failures)} FAIL\n",
            encoding="utf-8",
        )
        print(f"log: {log.relative_to(ROOT)}")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
