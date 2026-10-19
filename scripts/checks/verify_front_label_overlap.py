#!/usr/bin/env python3
"""守卫帕累托图的前沿标签不互相压字、不越出画布。

背景（2026-10-19）：AA 智力榜全量图右上角四个前沿标签叠在一起
（Muse Spark 1.3 Contributor 的 "$0.00091" 压住 Claude Sonnet 5.5 的标题，
DeepSeek V4.1 Flash 的 "$0.00081" 压住 Claude Haiku 5.5 的 "Max 20x"）。
唯一的既有守卫 `verify_label_overlap.py` 度量真实字形，本机缺 Microsoft YaHei
时只能降级为 KNOWN-NOISE——于是这类碰撞在本地**永远查不出来**，只能人工读
SVG 坐标试错，实测绕了三轮才摆平。

本守卫换一条不依赖字体安装的路：直接读**已渲染 SVG** 里 `<g class="front-label">`
的 text 坐标，用 `plot_svg.text_width()` 的同一套估算公式（全角 size、其余 0.58·size）
推出标签包围盒，做几何相交判定。宽度估算偏保守（×0.92 收窄），阈值留 8px/5px
余量吸收字形差异——宁可漏报轻微贴边，也不因字体不同误报，从而可以在任何机器上
保持硬 FAIL 语义。

只读：不修改任何产物。失败时打印碰撞对、坐标与越界项，非 0 退出。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from plot_svg import LEFT, RIGHT, text_width  # noqa: E402

CHARTS = ROOT / "charts"
# 宽度估算的保守系数：text_width 对英文按 0.58·size 计，实测偏大约 8~12%，
# 收窄后取并集包围盒，既不会漏掉真实压字，也不会因字体替换而假报。
WIDTH_SHRINK = 0.92
OVERLAP_X_MIN = 8.0   # px：横向重叠超过才算压字（贴边不算）
OVERLAP_Y_MIN = 5.0   # px
CANVAS_MARGIN = 30.0  # px：标签盒距画布左右边界的最小留白

TEXT_RE = re.compile(
    r'<text x="(?P<x>-?[\d.]+)" y="(?P<y>-?[\d.]+)" font-size="(?P<size>[\d.]+)"[^>]*'
    r'text-anchor="(?P<anchor>[a-z]+)"[^>]*>(?P<content>[^<]*)</text>')
LABEL_RE = re.compile(r'<g class="front-label" data-model="(?P<model>[^"]+)">(?P<body>.*?)</g>', re.S)

failures = []


def bbox(texts: list[dict]) -> tuple[float, float, float, float]:
    lefts, rights, tops, bottoms = [], [], [], []
    for t in texts:
        w = text_width(t["content"], t["size"]) * WIDTH_SHRINK
        anchor = t["anchor"]
        x = t["x"]
        if anchor == "end":
            lefts.append(x - w)
            rights.append(x)
        elif anchor == "middle":
            lefts.append(x - w / 2)
            rights.append(x + w / 2)
        else:
            lefts.append(x)
            rights.append(x + w)
        tops.append(t["y"] - t["size"] * 0.82)      # 基线以上为字形主体
        bottoms.append(t["y"] + t["size"] * 0.22)
    return min(lefts), min(tops), max(rights), max(bottoms)


def scan(svg: Path) -> int:
    body = svg.read_text(encoding="utf-8")
    labels = []
    for m in LABEL_RE.finditer(body):
        texts = []
        for t in TEXT_RE.finditer(m.group("body")):
            texts.append(dict(x=float(t.group("x")), y=float(t.group("y")),
                              size=float(t.group("size")), anchor=t.group("anchor"),
                              content=t.group("content")))
        if not texts:
            continue
        labels.append(dict(model=m.group("model"), texts=texts, box=bbox(texts)))
    if not labels:
        return 0
    rel = svg.relative_to(ROOT)
    for lab in labels:
        l, t, r, b = lab["box"]
        if l < CANVAS_MARGIN or r > 1440 - CANVAS_MARGIN:
            failures.append(f"{rel}: 标签 {lab['model']} 越出画布（x {l:.0f}..{r:.0f}，"
                            f"允许 {CANVAS_MARGIN:.0f}..{1440 - CANVAS_MARGIN:.0f}）")
        if t < 120:
            failures.append(f"{rel}: 标签 {lab['model']} 顶到标题带（y_top {t:.0f} < 120）")
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b_ = labels[i], labels[j]
            ox = min(a["box"][2], b_["box"][2]) - max(a["box"][0], b_["box"][0])
            oy = min(a["box"][3], b_["box"][3]) - max(a["box"][1], b_["box"][1])
            if ox > OVERLAP_X_MIN and oy > OVERLAP_Y_MIN:
                failures.append(
                    f"{rel}: 前沿标签压字 {a['model']} × {b_['model']}（重叠 {ox:.0f}×{oy:.0f}px，"
                    f"盒 {a['box'][0]:.0f},{a['box'][1]:.0f} 与 {b_['box'][0]:.0f},{b_['box'][1]:.0f}）"
                    " → 在 plot_svg.label_position() 对应分支调偏移，勿移动数据点")
    return len(labels)


def main() -> int:
    svgs = sorted(CHARTS.glob("*/pareto/*.svg"))
    if not svgs:
        print("FAIL: charts/*/pareto/*.svg 不存在——先跑 plot_svg.py + publish_charts.py")
        return 1
    total = sum(scan(p) for p in svgs)
    if failures:
        print(f"FAIL: {len(failures)} 项前沿标签问题（扫描 {len(svgs)} 张图、{total} 个标签）")
        for f in failures[:40]:
            print("  " + f)
        if len(failures) > 40:
            print(f"  ... 另有 {len(failures) - 40} 项")
        return 1
    print(f"PASS: {len(svgs)} 张帕累托图、{total} 个前沿标签无压字、无越界"
          f"（几何估算，宽度系数 {WIDTH_SHRINK}，阈值 {OVERLAP_X_MIN:.0f}×{OVERLAP_Y_MIN:.0f}px）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
