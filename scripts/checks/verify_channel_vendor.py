#!/usr/bin/env python3
"""守卫「渠道归属」：前缀可达 + 渠道/厂商一致。

背景（2026-11-04 两次事故，都是靠用户肉眼发现的）：

1. **空前缀 catch-all 遮蔽**：`config/channel-colors.json` 的 channels 里有一条
   `["", "Test"]` 排在 `["mistral", "Mistral"]` 之前。`palette.py::channel_of()`
   取首个 `startswith` 命中，空串匹配一切 → Mistral 被算成 Test，图例里看不到；
   同时把 `channel_of` 原本「无前缀就抛 ValueError」的守卫一起废掉。
2. **跨厂商串味**：把 Z.ai 的 `glm-5.3` 挂到 `mistral_vibe_pro` 这个套餐下，
   于是 Mistral 渠道下出现 Zhipu 的模型。数据本身能跑通、图表也能画，
   但把别人的模型记在了另一家名下 —— 这类错误只能靠人看图发现。

因此本守卫做三件事，全部是机器判定，不依赖人眼：
  A. channels 里不允许出现空白前缀（会吞掉其后所有渠道）；
  B. 每个渠道前缀必须能「回环命中」自己 —— 用 `prefix+"probe"` 反查，
     防止将来又被更早的条目遮住；
  C. 模型厂商渠道（Mistral / Zhipu / OpenAI …）下不得出现别家的模型；
     产品型渠道（Cursor / Devin / Factory / OpenCode / Command Code / Ollama /
     Local / OpenDesign…）本就跨厂商聚合，不参与 C 判定。
     需要长期豁免的条目写进 `config/channel-vendor-allow.json` 的 `exemptions`。

只读：不修改任何产物。失败打印具体点位与修法，非 0 退出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from palette import COLORS, channel_of  # noqa: E402

CONFIG = json.loads((ROOT / "config" / "channel-colors.json").read_text(encoding="utf-8"))
ALLOW_PATH = ROOT / "config" / "channel-vendor-allow.json"
POINTS = ROOT / "derived" / "points.json"

# 这些渠道名本身就是模型厂商：其下只应出现自家模型。
VENDOR_CHANNELS = {
    "OpenAI", "Anthropic", "Google", "SpaceXAI", "Kimi", "Zhipu",
    "MiniMax", "Alibaba", "Xiaomi", "StepFun", "DeepSeek", "Mistral",
}

failures: list[str] = []


def check_prefixes() -> None:
    entries = CONFIG["channels"]
    for prefix, channel in entries:
        if not str(prefix).strip():
            failures.append(
                f"空前缀渠道 {channel!r} 会吞掉其后所有前缀（Mistral 就是这样丢的）——"
                f"删掉它，或改成 'zz_test_' 这种不可能被真实 id 命中的前缀"
            )
    for prefix, channel in entries:
        if not str(prefix).strip():
            continue
        probe = f"{prefix}probe"
        try:
            got = channel_of(probe)
        except ValueError as exc:  # 前缀在表里却查不到，属实现级错误
            failures.append(f"前缀 {prefix!r} 回环失败：{exc}")
            continue
        if got != channel:
            failures.append(
                f"前缀 {prefix!r} 被更早的条目遮住：期望 {channel!r}，实际 {got!r}——"
                f"把更具体的渠道前缀排到前面，或删掉遮蔽者"
            )


def exemptions() -> set[str]:
    if not ALLOW_PATH.exists():
        return set()
    data = json.loads(ALLOW_PATH.read_text(encoding="utf-8"))
    return {str(x) for x in data.get("exemptions", [])}


def check_vendor_channels() -> None:
    if not POINTS.exists():
        failures.append("derived/points.json 不存在：先跑 scripts/compute.py")
        return
    points = json.loads(POINTS.read_text(encoding="utf-8"))["points"]
    allowed = exemptions()
    for p in points:
        pid = p["id"]
        plan_id = pid.split("::", 1)[0]
        vendor = p.get("vendor") or ""
        if not vendor or vendor == "other":
            continue
        try:
            channel = channel_of(plan_id)
        except ValueError as exc:
            failures.append(f"{pid} 无法解析渠道：{exc}")
            continue
        if channel not in VENDOR_CHANNELS:
            continue
        if vendor == channel or vendor not in VENDOR_CHANNELS:
            # 厂商渠道下出现非厂商型 vendor（如 other/厂牌代工）不在此守卫范围内
            continue
        if pid in allowed or plan_id in allowed:
            continue
        failures.append(
            f"{pid}: 渠道 {channel!r} 下挂了 {vendor} 的模型 —— 要么把 plan_id 前缀改成 "
            f"{vendor.lower()}，要么这条数据本来就不该挂在这个套餐下"
        )


def main() -> int:
    check_prefixes()
    check_vendor_channels()
    if failures:
        print(f"FAIL: {len(failures)} 项渠道归属问题")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        f"PASS: {len(CONFIG['channels'])} 个渠道前缀全部可达，"
        f"{len(VENDOR_CHANNELS)} 个厂商渠道下无跨厂商模型"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
