#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无头高保真截取 real-plan.fja.su 网页图表并通过 hermes-share 极速发布直链。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_PATH = Path("/tmp/real-plan-chart.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="price", choices=["price", "cost", "allowance"], help="图表维度")
    parser.add_argument("--name", help="输出图片发布名称")
    args = parser.parse_args()

    mode_map = {
        "price": "price",
        "cost": "cost",
        "allowance": "allowance",
    }
    xmode = mode_map.get(args.mode, "price")
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    url = f"https://real-plan.fja.su/?v={ts}#lang=zh&config=summary&xmode={xmode}"
    out_name = args.name or f"real-plan-{args.mode}-{datetime.now().strftime('%Y%m%d')}.png"

    cmd = [
        "chromium", "--headless", "--no-sandbox", "--disable-gpu",
        "--force-device-scale-factor=2",
        "--window-size=1600,1050",
        "--virtual-time-budget=5000",
        f"--screenshot={SCREENSHOT_PATH}",
        url
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ERROR: Chromium capture failed: {res.stderr or res.stdout}", file=sys.stderr)
        return 1

    share_cmd = ["/root/.local/bin/hermes-share", str(SCREENSHOT_PATH), "--name", out_name]
    share_res = subprocess.run(share_cmd, capture_output=True, text=True)

    result = {
        "ok": True,
        "mode": args.mode,
        "local_path": str(SCREENSHOT_PATH),
        "url": f"https://ching.icu/downloads/{out_name}",
        "detail": share_res.stdout.strip()
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
