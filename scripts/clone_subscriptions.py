#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键从锚点模型克隆全套订阅套餐行并同步双语翻译，避免手动改 JSON 的低效操作。

用法：
    .venv/bin/python scripts/clone_subscriptions.py \
        --anchor-model gemini-4-argon \
        --target-model gemini-4-carbon \
        --target-name "Gemini 4 Carbon" \
        --price-file list-prices-gemini-4-carbon-round1-2026-10-29.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRA_SUBS = ROOT / "data" / "extra_subscriptions.json"
I18N_EN = ROOT / "data" / "i18n" / "adopted.en.json"
PY = ROOT / ".venv" / "bin" / "python"


def clone_subscriptions(anchor_model: str, target_model: str, target_name: str | None = None,
                        price_file: str | None = None, ratio: float = 1.0,
                        anchor_name: str | None = None) -> list[dict]:
    if not EXTRA_SUBS.exists():
        raise SystemExit(f"ERROR: {EXTRA_SUBS} 不存在")

    doc = json.loads(EXTRA_SUBS.read_text(encoding="utf-8"))
    rows = doc.get("rows", [])
    anchor_rows = [r for r in rows if r.get("model") == anchor_model]

    if not anchor_rows:
        raise SystemExit(f"ERROR: 未找到 anchor_model={anchor_model!r} 的套餐行")

    if not target_name:
        # 默认首字母大写转换，如 gemini-4-carbon -> Gemini 4 Carbon
        target_name = " ".join(part.capitalize() for part in target_model.replace(".", "-").split("-"))
    if not anchor_name:
        anchor_name = " ".join(part.capitalize() for part in anchor_model.replace(".", "-").split("-"))

    # 读取旧模型关联的定价文件名（若有）
    anchor_price_file = None
    for r in anchor_rows:
        m = re.search(r'(list-prices-[a-zA-Z0-9._-]+)', r.get("source", ""))
        if m:
            anchor_price_file = m.group(1)
            break

    cloned_rows = []
    # 词汇替换映射
    replace_pairs = [
        (anchor_name, target_name),
        (anchor_model, target_model),
    ]
    if anchor_price_file and price_file:
        replace_pairs.insert(0, (anchor_price_file, price_file))

    for r in anchor_rows:
        new_r = dict(r)
        new_r["model"] = target_model
        if ratio != 1.0:
            new_r["monthly_yi"] = round(r["monthly_yi"] * ratio, 3)

        source = r.get("source", "")
        note = r.get("note", "")
        for old, new in replace_pairs:
            source = source.replace(old, new)
            note = note.replace(old, new)
        new_r["source"] = source
        new_r["note"] = note

        # 如果原行没有 date_inherit，默认继承 anchor 行的锚点
        if "date_inherit" not in new_r:
            new_r["date_inherit"] = [r["plan_id"], anchor_model]

        cloned_rows.append(new_r)

    # 过滤掉已有的同 target_model 行，幂等追加
    doc["rows"] = [r for r in rows if r.get("model") != target_model] + cloned_rows
    EXTRA_SUBS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 成功克隆 {len(cloned_rows)} 个套餐行至 {target_model} (写入 {EXTRA_SUBS.name})")

    # 同步双语翻译
    sync_i18n(anchor_rows, cloned_rows, replace_pairs)

    # 执行构建与校验
    subprocess.run([str(PY), str(ROOT / "scripts" / "build_adopted.py")], check=True)
    subprocess.run([str(PY), str(ROOT / "scripts" / "checks" / "verify_i18n.py")], check=True)
    print(f"✓ 已完成 build_adopted.py 与 verify_i18n.py 自动化校验闭环")
    return cloned_rows


def sync_i18n(anchor_rows: list[dict], cloned_rows: list[dict], replace_pairs: list[tuple[str, str]]) -> None:
    if not I18N_EN.exists():
        return

    i18n_doc = json.loads(I18N_EN.read_text(encoding="utf-8"))
    entries = i18n_doc.get("entries", [])
    zh_to_en = {e["zh"]: e.get("en", "") for e in entries}

    new_entries_map = {}
    for ar, cr in zip(anchor_rows, cloned_rows):
        for field in ("source", "note"):
            a_zh = ar.get(field, "")
            c_zh = cr.get(field, "")
            if a_zh and c_zh and a_zh in zh_to_en:
                a_en = zh_to_en[a_zh]
                c_en = a_en
                for old, new in replace_pairs:
                    c_en = c_en.replace(old, new)
                new_entries_map[c_zh] = c_en

    updated_count = 0
    for e in entries:
        if e["zh"] in new_entries_map and not e.get("en"):
            e["en"] = new_entries_map[e["zh"]]
            updated_count += 1

    # 补充未收录的条目
    existing_zh = {e["zh"] for e in entries}
    for c_zh, c_en in new_entries_map.items():
        if c_zh not in existing_zh:
            entries.append({"zh": c_zh, "en": c_en})
            updated_count += 1

    i18n_doc["entries"] = entries
    I18N_EN.write_text(json.dumps(i18n_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 自动补全/克隆 {updated_count} 条 i18n 英文映射 (写入 {I18N_EN.name})")


def main():
    parser = argparse.ArgumentParser(description="从已有锚点模型克隆全套订阅套餐与翻译。")
    parser.add_argument("--anchor-model", required=True, help="锚点模型 id，如 gemini-4-argon")
    parser.add_argument("--target-model", required=True, help="目标新模型 id，如 gemini-4-carbon")
    parser.add_argument("--target-name", help="目标新模型展示名称，如 'Gemini 4 Carbon'")
    parser.add_argument("--anchor-name", help="锚点模型展示名称，如 'Gemini 4 Argon'")
    parser.add_argument("--price-file", help="新定价文件名，如 list-prices-gemini-4-carbon-round1-2026-10-29.json")
    parser.add_argument("--ratio", type=float, default=1.0, help="额度缩放倍率，默认为 1.0")

    args = parser.parse_args()
    clone_subscriptions(
        anchor_model=args.anchor_model,
        target_model=args.target_model,
        target_name=args.target_name,
        anchor_name=args.anchor_name,
        price_file=args.price_file,
        ratio=args.ratio,
    )


if __name__ == "__main__":
    main()
