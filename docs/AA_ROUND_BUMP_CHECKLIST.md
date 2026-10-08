# AA 换榜 Checklist（round bump）

适用：Artificial Analysis 发布新 round、榜单版本号变化（v4.3 → v4.4）、或快照整体重排的那一天。
依据：2026-10-08 在 `/tmp` 沙盒里做的 round7 预演（5 个场景 × 6 个守卫），逐场景结果见 §4。

## 0. 先记住一句话

**换榜那天守卫红是常态，但红分两种：预期红（改常量即可）与真错红（必须重做快照/重跑管线）。**
把真错红当预期红放过、或为了让 CI 变绿去放宽断言，是这一类事故里唯一不可逆的错误。

## 1. 操作顺序

1. 抓取 → 解析 → 构建新快照（照 runbook §1；`--round` / `--collected-at` / `--output` 换新值，`--agents-html` 必须是原始 HTML）。
2. **更新 `scripts/compute.py` 的 `SCORE_FILES`**：新快照插在**被它替换的那一版之后**（不是列表末尾）——插错会误杀 supplement。
3. 跑门禁 1（`scripts/check_gate1.py`）：点数 ≥ 上一版；新模型出现在 `derived/points.json` 且订阅点 `list_blended_usd_per_mtok` 非空。
4. 跑全链：`$R/.venv/bin/python scripts/checks/run_all.py`。
5. **逐条核对 §2 的预期红**：预期红 → 更新写死常量；真错红（§3）→ 停下来修快照/管线，绝不放宽断言。
6. 改完常量重跑 run_all，直到"预期外的红 = 0"。
7. 重建图表 → 部署 → 重建图表指纹基线（`scripts/chart_fingerprint.py --write data/research/chart-fingerprint-<date>.json`），并用 `--compare` 判定图表是否真有变化。

## 2. 预期会红的清单（改常量即可）

| 守卫 | 常量 / 行号 | 报错文案 | 处置 |
|---|---|---|---|
| `verify_aa_payload_fields.py` | `EXPECTED_HAIKU_RANK = 43`（第 35 行），断言第 119–121 行 | `FAIL configurationRank(claude-haiku-5-5) == 43: actual 48 (label 'Claude Haiku 5.5 (max with fallback)')` | 读新快照里 `claude-haiku-5-5` 那条 score 的 `secondary.configurationRank`，改常量；同时确认 `variantLabel` 仍等于该 slug 原始行的 `shortName`/`name`（配置身份没错配） |
| 同上 | `MIN_RAW_ROWS = 600`（第 28 行） | `FAIL rawRecords.aa_intelligence_index rows >= 600: actual …` | 只有 AA 真把榜缩到 600 行以下才改；否则按真错处理（载荷被裁） |
| 同上 | `MIN_TIMED_ROWS = 300`（第 31 行） | `FAIL rows with numeric medianEndToEndResponseTimeSeconds >= 300: actual 0/696 - the task-time axis silently degrades to …` | **绝不改**：0 行 = 抽取器裁了字段，必须重做快照（§3.1） |

`claude-haiku-5-5` 若在新快照里缺席，守卫打印 `SKIP … configurationRank self-check skipped`（不红）；这时要确认是"真的下榜"而不是 slug 改名。

## 3. 真错红清单（绝不能靠放宽断言解决）

1. **`medianEndToEndResponseTimeSeconds` 整列消失** → `verify_aa_payload_fields` 的 timed 断言红。这是 2026-10-01 round5 的真实事故：字段被裁、无任何步骤报错，前端「任务用时」轴静默退化。处置：重抓 + 重解析，确认带该字段的行数 ≥300 再继续。
2. **score 引用的 slug 在 `rawRecords` 里不存在** → `FAIL every aa_intelligence_index score slug resolves to a rawRecords row: 1 missing, e.g. ['…']`（`verify_aa_payload_fields`），以及 `verify_aa_snapshot.py` 第 30 行抛 **StopIteration**（报错形式不友好，别以为脚本坏了）。含义：榜上删了模型但 scores 仍引用它。
3. **`checkedAt` 与 `collectedAt` 不一致** → `verify_aa_snapshot.py` 第 40 行 `assert row["checkedAt"] == raw_archives[name]["collectedAt"]`。正常由 `aa_build_snapshot.py` 统一写入（第 203/224/250 行覆盖 models 与 agents 两页）；**手工改快照时必须同步所有榜的 `checkedAt`**——预演里只改了一个榜，造成 159 行假阳性。
4. **derived 陈旧** → `verify_benchmark_configs.py` 第 52 行 `assert c["archive"] == file and c["raw_record"] == record`。含义：`derived/` 还是上一版快照算出来的，忘了跑 `compute.py`。处置：重跑管线，不要改守卫。
5. **`verify_deepswe` / `verify_list_blended` / `verify_scenario_points` 任何红**：预演里这三个守卫在全部 5 个场景下都是绿的 → 它们与 AA 换榜无关，红了就是真错（或情景点/清单没登记），按各自 FAIL 文案查。

## 4. 预演证据（2026-10-08，/tmp 沙盒）

沙盒 `/tmp/aa_bump_sandbox`：`scripts/`、`data/`、`derived/`、`config/`、`cli/` 为真副本（可改），`web/`、`charts/`、`node_modules/` 为只读符号链接。模拟 round7：`collectedAt=2026-10-15`、榜名 `… v4.4`、`configurationRank +5`、增 1 假 slug / 删 1 真 slug、裁 `medianEndToEndResponseTimeSeconds`。

| 场景 | compute | payload_fields | aa_snapshot | benchmark_configs | deepswe | list_blended | scenario_points |
|---|---|---|---|---|---|---|---|
| A 版本/时间/rank 漂移 | 0 | **1（rank＝预期）** | 0 | 0 | 0 | 0 | 0 |
| B 增 1 假 slug + 删 1 真 slug | 0 | **1（rank 预期 + missing slug 真错）** | **1（StopIteration 真错）** | 0 | 0 | 0 | 0 |
| C 裁 medianEndToEnd 列 | 0 | **1（rank 预期 + timed 真错）** | 0 | 0 | 0 | 0 | 0 |
| D A+B+C 组合 | 0 | **1（3 条）** | **1** | 0 | 0 | 0 | 0 |
| E D 但不重跑 compute（陈旧 derived） | 跳过 | **1** | **1** | **1（archive 不匹配，真错）** | 0 | 0 | 0 |

（0 = 绿，1 = 红；`compute` 列为 `scripts/compute.py` 的退出码。）

结论：**换榜本身只应让 `verify_aa_payload_fields` 红一条（rank）**，其余红按 §3 判定。预演全程只在沙盒内操作，主仓库零改动。

## 5. 已知缺口（建议后续补）

- `verify_aa_snapshot.py` 第 30 行的 `next(...)` 在 slug 缺失时抛 StopIteration；建议改成显式断言并打印缺失 slug（与 `verify_aa_payload_fields` 的文案对齐）。
- `EXPECTED_HAIKU_RANK` 这类单点指纹每轮都要人工改；可考虑换成"rank 与快照内该 slug 的排序自洽"这类结构性断言，把人工常量降级为 WARN。
- 换榜后 `charts/` 是否变化应由 `scripts/chart_fingerprint.py --compare` 判定；本次预演未覆盖"换榜 → 图表差异"这一段，可补一轮。
