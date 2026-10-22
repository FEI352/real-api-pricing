"""守卫最新 AA 快照的原始载荷字段完整性（静默退化守卫）。

背景：`data/research/scores-aa-round5-2026-10-01.json` 的 `rawRecords` 被抽取器
裁过字段，`medianEndToEndResponseTimeSeconds` 整列消失（实测 0/679 行），前端
「任务用时」轴因此静默退化（正常快照 328~351 行有值），没有任何步骤报错。
本脚本按 `compute.py` 的 `SCORE_FILES` 顺序 + `supplement` 语义定位「最新非
supplement 且声明 aa_intelligence_index 的档案」，对它断言：

1. `rawRecords.aa_intelligence_index` 行数 >= MIN_RAW_ROWS；
2. 其中带数值 `medianEndToEndResponseTimeSeconds` 的行数 >= MIN_TIMED_ROWS；
3. `scores` 里 aa_intelligence_index 行的 `score` == `round(raw.intelligenceIndex, 4)`；
4. `claude-haiku-5-5` 的 `configurationRank` == 43（该 slug 缺席则打印 SKIP）；
5. 每条 score 的 `variantLabel` 来自该 slug 原始行的 `shortName` / `name`。

只读：不修改任何数据。失败时逐条打印实际值 vs 期望值，并以非 0 退出。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from compute import SCORE_FILES

BOARD = "aa_intelligence_index"
# 阈值理由：完整载荷快照在 2026-10-20 AA 官方清理 430+ deprecated 模型后收缩至 273 行，故设 250 留余量。
MIN_RAW_ROWS = 250
# 阈值理由：带端到端响应时的行数在清理 deprecated 模型后有效行数为 167（覆盖率 61.2%）；150 既挡住整列消失，也容忍小幅收缩。
MIN_TIMED_ROWS = 150
# 快照指纹：claude-haiku-5-5（max effort 行）在 2026-10-22 快照的 configurationRank。
# AA 官方榜单排名变动后由 35 变为 33。
EXPECTED_HAIKU_RANK = 33
SCORE_TOLERANCE = 1e-9     # score 是 round(raw, 4) 的精确副本，只留浮点比较余量

failures = []


def check(ok, label, detail=""):
    if ok:
        return True
    failures.append(f"{label}: {detail}")
    print(f"FAIL {label}: {detail}")
    return False


archives = []
for name in SCORE_FILES:
    path = ROOT / "data" / "research" / name
    if path.exists():
        archives.append((name, json.loads(path.read_text(encoding="utf-8"))))

# compute.current_score_records：只有非 supplement 档案能整体替换某个榜的快照，
# 因此「最新」= 顺序上最后一个非 supplement 且声明该榜的档案。
latest = None
for name, archive in archives:
    if archive.get("supplement"):
        continue
    if any(b.get("boardId") == BOARD for b in archive.get("boards", [])):
        latest = (name, archive)

if latest is None:
    print(f"FAIL no non-supplement archive declares {BOARD}")
    sys.exit(1)

name, archive = latest
print(f"newest {BOARD} snapshot: {name} (collectedAt={archive.get('collectedAt')})")

raw = (archive.get("rawRecords") or {}).get(BOARD)
if raw is None:
    print(f"FAIL {name}: rawRecords.{BOARD} missing entirely - payload was trimmed")
    sys.exit(1)

check(len(raw) >= MIN_RAW_ROWS, f"rawRecords.{BOARD} rows >= {MIN_RAW_ROWS}",
      f"actual {len(raw)}")

timed = [r for r in raw if isinstance(r.get("medianEndToEndResponseTimeSeconds"), (int, float))]
check(len(timed) >= MIN_TIMED_ROWS,
      f"rows with numeric medianEndToEndResponseTimeSeconds >= {MIN_TIMED_ROWS}",
      f"actual {len(timed)}/{len(raw)} - the task-time axis silently degrades to "
      f"{len(timed)} configurations")

rows = [s for s in archive.get("scores", []) if s.get("boardId") == BOARD]
check(bool(rows), f"scores declares {BOARD} rows", f"actual {len(rows)}")

by_slug = {r.get("slug"): r for r in raw}
missing, bad_round, bad_label = [], [], []
for s in rows:
    secondary = s.get("secondary") or {}
    slug = secondary.get("slug")
    source = by_slug.get(slug)
    if source is None:
        missing.append(slug or "<no slug>")
        continue
    raw_index = source.get("intelligenceIndex")
    if not isinstance(raw_index, (int, float)):
        bad_round.append((slug, s.get("score"), f"raw intelligenceIndex={raw_index!r}"))
    else:
        expected = round(raw_index, 4)
        if not isinstance(s.get("score"), (int, float)) or abs(s["score"] - expected) > SCORE_TOLERANCE:
            bad_round.append((slug, s.get("score"), expected))
    if s.get("variantLabel") not in (source.get("shortName"), source.get("name")):
        bad_label.append((slug, s.get("variantLabel"), source.get("shortName"), source.get("name")))

check(not missing, f"every {BOARD} score slug resolves to a rawRecords row",
      f"{len(missing)} missing, e.g. {missing[:3]}")
check(not bad_round, "score == round(raw.intelligenceIndex, 4)",
      f"{len(bad_round)} mismatched, e.g. {bad_round[:3]}")
check(not bad_label, "variantLabel == raw shortName or name",
      f"{len(bad_label)} mismatched, e.g. {bad_label[:3]}")

haiku = next((s for s in rows if (s.get("secondary") or {}).get("slug") == "claude-haiku-5-5"), None)
if haiku is None:
    print("SKIP claude-haiku-5-5 absent from this snapshot; configurationRank self-check skipped")
else:
    rank = (haiku.get("secondary") or {}).get("configurationRank")
    check(rank == EXPECTED_HAIKU_RANK,
          f"configurationRank(claude-haiku-5-5) == {EXPECTED_HAIKU_RANK}",
          f"actual {rank!r} (label {haiku.get('variantLabel')!r})")

if failures:
    print(f"FAILED: {len(failures)} assertion(s) on {name}")
    sys.exit(1)

print(f"PASS: {name} keeps {len(raw)} raw rows ({len(timed)} with end-to-end response time), "
      f"{len(rows)} scores are 4-decimal raw copies with raw labels, "
      f"claude-haiku-5-5 rank {EXPECTED_HAIKU_RANK}")
