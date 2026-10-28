"""已弃用模型的唯一读取点（Python 侧）。

`config/deprecated-models.json` 是唯一来源：
前端由 `scripts/lib/build-site-data.mjs` 读取并打 `deprecated` 标记，
图表链（plot_svg / plot_quotas / build_html）由此模块读取并剔除，
两侧行为必须一致 —— 改清单只改 JSON，不动代码。

语义：弃用模型的点保留在 `derived/points.json` 与站点数据里（可在模型面板手动勾回），
但默认不进图表、不参与前沿计算。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "deprecated-models.json"


def load_deprecated_models() -> dict[str, str]:
    """Return {model_id: reason}. Missing or malformed config means "nothing deprecated"."""
    if not CONFIG.exists():
        return {}
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in (data.get("models") or {}).items()}


def is_deprecated(model: str | None) -> bool:
    return bool(model) and model in load_deprecated_models()


def drop_deprecated_points(points):
    """Filter `points.json` entries (key: `model`)."""
    deprecated = load_deprecated_models()
    return [p for p in points if p.get("model") not in deprecated]


def drop_deprecated_rows(rows):
    """Filter adopted.csv rows (key: `served_model`)."""
    deprecated = load_deprecated_models()
    return [r for r in rows if r.get("served_model") not in deprecated]
