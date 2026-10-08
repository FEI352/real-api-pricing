#!/usr/bin/env python3
"""Doc-drift guard: every repo path / CLI subcommand / flag a doc references must really exist.

今天（2026-10-08）出过两次「文档与代码漂移」的真事故：
  ① `docs/CLI_AND_CRON_GUIDE.md` 把 `cron-run` 描述成「依次 update→chart→send-email」的周报，
     实际早已是「有变动才发信」；
  ② `docs/daily-sync-task-prompt.md` 的 DoD 判据写成 `python3 scripts/checks/run_all.py`，
     违反「管线必须用 `$R/.venv/bin/python`」的铁律。
人眼校对抓不完这类漂移，所以做成机器可判定的守卫。扫描范围：
`docs/*.md`、`README*.md`、`BUILD.md`、`CONVENTIONS.md`、`DECISIONS.md`。

判定四类：
  1) 仓库内路径引用（反引号、Markdown 链接、`python/node <path>` 命令）必须存在
     —— 相对链接按「仓库根」与「文档所在目录」双基解析；
  2) `real-api-pricing-cli <sub>` / `scripts/cli.py <sub>` 的子命令必须在 `scripts/cli.py`
     的 argparse 里真实存在（用 `ast` 解析，不靠 grep 猜）；
  3) 子命令行上的 `--flag` 必须属于该子命令的合法参数（同样来自 argparse）；
  4) 可直接粘贴的 `python3 scripts/...` 违反 .venv 铁律（`python3` 后跟脚本路径才算；
     「严禁用系统 python3 跑管线」这类禁令说明不会被误判）。

豁免必须显式登记并写明理由（构建产物 / gitignore 中间产物 / 占位符）。「建议新建/待补」
这类未来语境里的路径允许不存在（打印 WARN，不计失败）；引用一个应当存在的文件却写成
不存在的路径仍是 FAIL。
用法：`.venv/bin/python scripts/checks/verify_docs_refs.py [--root <repo>]`
输出：一行 `PASS: ...`；有问题逐条 `FAIL: <doc>:<line> ...` 并 exit 1。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DOC_GLOBS = ["BUILD.md", "CONVENTIONS.md", "DECISIONS.md", "README.md", "README.zh.md"]
DOC_DIR = "docs"

# 仓库内路径的顶层前缀（只校验这些；外部/绝对路径不碰）。
PREFIXES = ("scripts/", "docs/", "data/", "web/", "cli/", "config/", "charts/", "derived/", "_build/")

# 显式豁免：路径 → 理由（必须写明为什么允许不存在）。
EXPLICIT_EXEMPTIONS = {
    "cli/data/site.json": "cli 构建产物（npm --prefix cli run build 生成），未构建时不存在",
    "cli/dist/main.js": "cli 构建产物（npm --prefix cli run build 生成），未构建时不存在",
}
EXEMPT_PREFIXES = {
    "_build/": "gitignore 的中间产物目录，历史审计文件随时可被清理",
}
PLACEHOLDER_CHARS = "*<>{}…"
PLACEHOLDER_WORDS = ("YYYY", "MM", "NN")
# 「建议新建 / 待补」这类未来语境：路径尚不存在是正常表述，降级为 WARN（不 FAIL）。
# 反之，引用一个应当存在的文件却写成不存在的路径，仍然是 FAIL。
FUTURE_HINTS = ("建议新建", "建议补", "建议新增", "建议加", "待补", "将来", "计划新建", "拟建", "尚未创建")
# hint 必须紧邻在路径**之前**（间距 ≤ 该值）才算未来语境；否则同一行里其它写错的路径
# 会被连带降级（en-render-verify 2026-10-08 复核发现的漏洞：行级判定太粗）。
FUTURE_GAP = 12

TICK = re.compile(r"`([^`\s]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
CMD = re.compile(r"(?:^|\s)(?:python3?|node)\s+((?:scripts|cli|web)/[^\s`|;)]+)")
CLI_SUB = re.compile(r"(?:real-api-pricing-cli|scripts/cli\.py)\s+([a-z][a-z0-9-]*)")
FLAG = re.compile(r"--([a-z][a-z0-9-]+)")
SYSTEM_PY = re.compile(r"(?<![\w/.-])python3\s+scripts/")


def cli_surface(cli_path: Path) -> dict[str, set[str]]:
    """scripts/cli.py 的 argparse 面：{subcommand: {合法 flag}}（含 argparse 内置 --help）。"""
    tree = ast.parse(cli_path.read_text(encoding="utf-8"))
    parser_vars: dict[str, str] = {}
    surface: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_parser":
            name = node.args[0].value if node.args else None
            if isinstance(name, str):
                surface.setdefault(name, set())
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and getattr(node.value.func, "attr", "") == "add_parser" and node.value.args:
            name = node.value.args[0].value
            if isinstance(name, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        parser_vars[target.id] = name
                surface.setdefault(name, set())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument" \
                and isinstance(node.func.value, ast.Name):
            sub = parser_vars.get(node.func.value.id)
            if sub is None:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    surface[sub].add(arg.value)
    for flags in surface.values():
        flags.update({"-h", "--help"})
    return surface


def docs_to_scan(root: Path) -> list[Path]:
    found = [root / name for name in DOC_GLOBS if (root / name).is_file()]
    found += sorted((root / DOC_DIR).glob("*.md"))
    return found


def is_placeholder(cand: str) -> bool:
    return any(ch in cand for ch in PLACEHOLDER_CHARS) or any(w in cand for w in PLACEHOLDER_WORDS)


def normalise(raw: str) -> str:
    cand = raw.split("#", 1)[0].split("::", 1)[0].strip().strip("`").rstrip(".,;:)]}）】。，")
    return cand.removeprefix("./")


def is_future_reference(line: str, cand: str) -> bool:
    """路径是否为「建议新建/待补」这类未来语境：hint 必须紧邻在路径**之前**（间距 ≤ FUTURE_GAP）。

    只按整行判定会把同一行里其它写错的路径一起降级成 WARN（漏洞实例：
    `建议新建 `x_guard.py`，详见 `verify_nonexistent_guard.py`` —— 后者必须仍然 FAIL）。

    间距量的是 `len(line[:cand]) - len(hint)`：**hint 后空格（若有）+ 中间字符 + 路径前反引号
    三者之和** ≤ FUTURE_GAP。换算成内容长度：hint 后带空格时最多 10 个字符，不带空格时最多 11 个
    （实测四例：无空格 11 / 带空格 10 → 间距 12 → WARN；各多一个字符 → 间距 13 → FAIL）。
    """
    idx = line.find(cand)
    if idx < 0:
        return False
    head = line[:idx]
    for hint in FUTURE_HINTS:
        pos = head.rfind(hint)
        if pos >= 0 and len(head) - (pos + len(hint)) <= FUTURE_GAP:
            return True
    return False


def check_doc(doc: Path, root: Path, surface: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []
    rel = doc.relative_to(root).as_posix()
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        # 1) 路径引用
        for raw in TICK.findall(line) + LINK.findall(line) + CMD.findall(line):
            cand = normalise(raw)
            if not cand.startswith(PREFIXES):
                continue
            if is_placeholder(cand) or cand in EXPLICIT_EXEMPTIONS:
                continue
            if any(cand.startswith(prefix) for prefix in EXEMPT_PREFIXES):
                continue
            if not ((root / cand).exists() or (doc.parent / cand).exists()):
                message = f"{rel}:{lineno} 路径不存在: {cand}"
                if is_future_reference(line, cand):
                    warnings.append(f"{message}（未来语境：建议新建/待补，未存在属正常）")
                else:
                    problems.append(message)
        # 2) CLI 子命令 + 3) flag
        for match in CLI_SUB.finditer(line):
            sub = match.group(1)
            if sub not in surface:
                problems.append(
                    f"{rel}:{lineno} 子命令不存在: {sub}（scripts/cli.py 现有: "
                    f"{', '.join(sorted(surface))}）")
                continue
            for flag in FLAG.findall(line[match.end():]):
                if f"--{flag}" not in surface[sub]:
                    problems.append(
                        f"{rel}:{lineno} 子命令 {sub} 无此参数: --{flag}"
                        f"（合法: {', '.join(sorted(surface[sub]))}）")
        # 4) .venv 铁律
        if SYSTEM_PY.search(line):
            problems.append(
                f"{rel}:{lineno} 可直接粘贴的命令用了系统 python3: "
                f"{line.strip()[:90]} —— 改为 `.venv/bin/python scripts/...`")
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root (default: this repo)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    cli_path = root / "scripts" / "cli.py"
    if not cli_path.is_file():
        print(f"FAIL: scripts/cli.py not found under {root}", file=sys.stderr)
        return 2
    surface = cli_surface(cli_path)

    docs = docs_to_scan(root)
    problems: list[str] = []
    warnings: list[str] = []
    for doc in docs:
        doc_problems, doc_warnings = check_doc(doc, root, surface)
        problems += doc_problems
        warnings += doc_warnings

    if warnings:
        print(f"WARN: {len(warnings)} future-path reference(s)（建议新建/待补，未存在属正常，不计失败）:")
        for warning in warnings:
            print(f"  WARN: {warning}")

    if problems:
        print(f"FAIL: docs drift — {len(problems)} problem(s) in {len(docs)} documents")
        for problem in problems:
            print(f"  FAIL: {problem}")
        print("（豁免清单见 EXPLICIT_EXEMPTIONS / EXEMPT_PREFIXES；修文档或登记豁免都要写明理由）")
        return 1

    print(f"PASS: docs refs OK — {len(docs)} documents, {len(surface)} CLI subcommands, "
          f"{len(EXPLICIT_EXEMPTIONS)} explicit exemptions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
