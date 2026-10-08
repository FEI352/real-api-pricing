# 新增一个模型：从 AA 新 slug 到线上可见（SOP）

适用：AA 榜单出现新 slug、或已上架模型首次进入本项目采用值表。
先读：`docs/DAILY_SYNC_RUNBOOK.md`（每日机械口径）、`docs/AA_ROUND_BUMP_CHECKLIST.md`（换榜当天的预期红清单）。

**两条铁律**：① 生成物只能由生成器产出（`data/adopted.csv` ← `scripts/build_adopted.py`，`derived/*` ← `scripts/compute.py`），手改会被覆盖；② 所有 Python 步骤用 `$R/.venv/bin/python`（`$R` = 仓库根）。

## 0. 全景（7 步）

| # | 步骤 | 产物 | 主要守卫 |
|---|---|---|---|
| 1 | 认领 slug（`--slug-map` / `--host-map`） | 新 AA 快照 | `verify_aa_payload_fields` / `verify_aa_snapshot` |
| 2 | 建标价档并登记 `LIST_PRICE_FILES` | `data/research/list-prices-*.json` | `verify_list_blended` |
| 3 | 采用值（`build_adopted.py` 加行） | `data/adopted.csv` | `verify_haiku55_tiers` / `verify_scenario_points` |
| 4 | i18n 四处同步点 | 前端 + 静态图英文 | `verify_i18n` + 手工 grep（见 §7 缺口） |
| 5 | 门禁与守卫链 | — | `check_gate1.py` + `run_all.py` |
| 6 | 重建 + 部署 + 重建基线 | `charts/`、线上站点 | `verify_publication` / `chart_fingerprint.py` |
| 7 | 收尾 | daily-log、frontier-state | runbook §7 |

## 1. 认领 slug

```bash
$R/.venv/bin/python scripts/aa_build_snapshot.py \
  --models-json /tmp/aa_models_raw.json --agents-html /tmp/aa_agents.html --models-html /tmp/aa_models.html \
  --collected-at <YYYY-MM-DD> --round <N> \
  --prior scores-aa-round<N-1>-<date>.json \
  --slug-map <aa-slug>=<project-model-id> [每个 effort 变体一条] \
  --host-map <host>=<project-model-id> \
  --output data/research/scores-aa-round<N>-<date>.json
```
- **验收判据**：`stderr` 里新 slug 不再出现在 `unmappedPublicRows`；`--diff-models` 能看到它进入模型集：
  ```bash
  $R/.venv/bin/python scripts/check_gate1.py --diff-models data/research/scores-aa-round<N-1>-<date>.json \
    --snapshot data/research/scores-aa-round<N>-<date>.json
  ```
- **做错会红**：**不会红 —— 这是当前最大的无守卫缺口**（2026-10-08 复核）。漏 effort 变体 → 静默少行（实测 303 vs 311，runbook 陷阱表 #15）。`scripts/checks/verify_aa_snapshot.py` 只遍历快照里**已有**的分数行做交叉核对（`current_score_records()`），漏掉的变体根本不在遍历集合里；`scripts/checks/verify_aa_payload_fields.py` 只查 `rawRecords` 行数 / 4 位小数 / rank，也不比对「每个 rawRecord 都有对应的 score 行」。→ 见 §7。
- **今天的事故**：`claude-haiku-5-5` 有 5 条 effort 变体，漏一条就少一行分数（runbook 陷阱表 #15）。

## 2. 建标价档 + `LIST_PRICE_FILES`

```bash
$R/.venv/bin/python -c "
import json;d=json.load(open('data/research/list-prices-<model>-round1-<date>.json',encoding='utf-8'))
m=d['models'][0];print('list_blended =', 0.97*m['cachedInput']+0.025*m['input']+0.005*m['output'])"
```
- **`model` 键必须是项目模型 id**（`claude-haiku-5.5`），不是官方 API id（`claude-haiku-5-5`）；写错会静默落进常数回退（`MEDIAN_AA_TASK_MTOK = 3.51759 × real`），多 effort 档被压到同一 x → **假前沿**。
- 新增文件要登记进 `scripts/compute.py` 的 `LIST_PRICE_FILES`（Lead 独占）。
- **验收判据**：`list_blended` 非空且等于 `0.97×cached + 0.025×input + 0.005×output`（标准负载混合价）。
- **做错会红**：`verify_list_blended.py`（订阅点 `list_blended` 为 `None` 且不在白名单 → FAIL）。
- **今天的事故**：GPT-6 Luna 假前沿（2026-10-08）——订阅点缺 `list_blended`，前端回退成常数，成本轴被压平；同日的跨模型分歧守卫（AA 配置价 ÷ 项目档价超 `[0.5, 2.0]` → 回退）摘掉了 `opencode_go::muse-spark-1.3-contributor` 的假前沿。

## 3. 采用值（只能经生成器）

在 `scripts/build_adopted.py` 加行（Lead/Worker-Pricing 独占），然后：
```bash
$R/.venv/bin/python scripts/build_adopted.py && $R/.venv/bin/python scripts/compute.py
```
- **多档情景**（同一套餐同一模型的负载情景，如 Haiku 5.5 的 >100k / 50/50）：照 GLM 忙/中/闲与 Haiku 先例用 **plan_id 后缀**表达，互不替代、不可相加，并**登记进 `scripts/checks/verify_scenario_points.py` 的 `SCENARIO_PLAN_IDS`**（清单是权威判据，不靠命名正则猜）。
- 数值精度：`monthly_yi` 用 `round(..., 3)`（项目统一 3 位；2026-10-08 曾误用 2 位，落库值 163.71 应为 163.711，靠人工复核才发现）。
- **验收判据**：`derived/points.json` 出现该 `plan_id::model`；订阅点 `list_blended_usd_per_mtok` 非空；情景点在 8 个榜都不进前沿。
- **做错会红**：`verify_haiku55_tiers.py`（分档比例与 `real == price/(yi×100)` 的 8 位有效数字一致性）、`verify_scenario_points.py`（情景点污染前沿 → FAIL；漏登记 → WARN）、`verify_list_blended.py`。
- **今天的事故**：精度 2 位 vs 3 位；情景点登记只写进 `decision_note`（4 行里只有 2 行写了「情景点」字样，靠关键词兜底会漏）。

## 4. i18n 四处同步点

`$R/.venv/bin/python scripts/checks/verify_i18n.py --sync` 后**手填英文**，并逐处检查（详见 `CONVENTIONS.md` §英文套餐名同步点）：

| # | 位置 | 说明 |
|---|---|---|
| ① | `web/src/domain.ts` 的 `displayPlan()` | en 分支整段替换（含全角括号） |
| ② | `scripts/plot_quotas.py` 的 `plan_name()` en 替换表 | 额度/单价总览图 |
| ③ | `scripts/plot_svg.py` 的 `plan_name(plan, language)` | 帕累托/前沿图（独立词表，最易漏） |
| ④ | `data/adopted.csv` 的 `plan_name_en` | 多数为空，靠 ①②③ 兜底 |

- **验收判据**：`grep -rl --include='*.txt' --include='*.svg' '[一-龥]' charts/en/` 无输出；中英表格行数一致；数值中英一致（中文按「亿」、英文按 B，数值等价）。
- **做错会红**：`verify_i18n.py`（数据侧）；**静态图与 SVG `<title>` 目前只有手工 grep，没有守卫**（见 §7）。
- **今天的事故**：英文 overview 表格/图里套餐名残留中文（`（Haiku >100k 档）`），28 处；`plot_svg.py` 的词表未收录，`<title>` 仍为中文。

## 5. 门禁与守卫链

```bash
$R/.venv/bin/python scripts/check_gate1.py --baseline /tmp/gate1-baseline.json --points derived/points.json
$R/.venv/bin/python scripts/checks/run_all.py
```
- **硬失败**：新模型订阅点 `list_blended` 为 `None`（GPT-6 Luna 假前沿的根因）；点数低于上一版；新模型未出现在 `points.json`。
- **验收判据**：`run_all.py` 除 `verify_label_overlap`（本机无 YaHei，KNOWN-NOISE）外全绿。

## 6. 重建 + 部署 + 重建基线

```bash
$R/.venv/bin/python scripts/plot_svg.py && node scripts/render_svg.cjs && $R/.venv/bin/python scripts/build_html.py
$R/.venv/bin/python scripts/plot_quotas.py && $R/.venv/bin/python scripts/publish_charts.py
$R/.venv/bin/python scripts/checks/verify_publication.py
$R/.venv/bin/python scripts/chart_fingerprint.py --compare data/research/chart-fingerprint-<上一版>.json   # 判定"陈旧还是真变"
/usr/local/bin/real-api-pricing-cli update
```
- **验收判据**：线上点数 ≥ 上一版；`conventions.updatedAt` = 本轮日期；图表指纹差异清单与预期一致。
- **收尾**：`scripts/visibility_matrix.py --write-docs`、重建 `data/research/chart-fingerprint-<date>.json` 基线、更新 `frontier-state.json` 与 `daily-log-<YYYY-MM>.md`（runbook §7）。

## 7. 目前**没有守卫兜底**的步骤（最该补的缺口）

| 缺口 | 做错时的表现 | 建议的守卫形态 |
|---|---|---|
| **英文静态产物残留中文** | 英文图/表里出现中文套餐名（今天 28 处，靠人工 grep 发现） | **已有守卫**：`scripts/checks/verify_publication.py` 扫 `charts/en/**` 的 `.txt/.svg/.html` 断言无 CJK（2026-10-08 复核确认覆盖 pareto SVG 的 `<title>`）。剩余真缺口是「改了词表但忘记重跑出图脚本」——守卫只查产物内容、不查产物是否已按最新词表重建；可考虑给 `scripts/checks/run_all.py` 加一步「词表 mtime 晚于产物 mtime → 提示重建」 |
| **采用值精度位数** | `round(...,2)` 落库，值与 3 位口径差 0.01 亿，且**自洽性检查抓不到**（real 按错值算仍成立） | 断言 `monthly_yi` 与其 `price_usd/(real×100)` 的位数口径一致，或对分档行断言 ratio 等于 `RATIO_HAIKU55_*` 到 3 位 |
| **图表中英行数/条目一致** | 中英表行数不等、某档少一行 | 断言 zh/en overview 表格行数与 SVG 行标签数逐档相等（今天人工核验过） |
| **`chart_tier` / `workload` 取值** | 把 `main` 写成 `full`，总览图少一行且无人察觉 | 断言 `chart_tier ∈ {main, full}`、`workload ∈ {anthropic, standard, lowCache, measured}` |
| **`SCORE_FILES` 插入位置** | 插到列表末尾 → 误杀 supplement | 断言「插入后 supplement 贡献的行数不减少」（现有 `verify_benchmark_configs` 覆盖语义但未覆盖"位置"这一具体错误） |
| **情景点漏登记** | 守卫断言 1/2 不覆盖该点，只 WARN | 已有 WARN 兜底；可升级为：凡 `SCENARIO_SHAPE` 命中且未登记 → FAIL（现在是 WARN） |
| **`--slug-map` 漏 effort 变体** | 静默少行（303 vs 311），**无任何报错**（见 §1 的 2026-10-08 复核） | 断言「每个在榜模型的 configuration 数 ≥ 上一版」，或给 `scripts/check_gate1.py` 加 `--diff-configurations` |
| **手改 `data/adopted.csv`** | 下次 `scripts/build_adopted.py` 整表覆盖，白干且无人红 | 给生成器加 `--check`：重跑生成到临时文件再 diff（仿 `scripts/readme_stats.py --check` 的形态），避免 `scripts/checks/run_all.py` 每次全量重写 |
| **`DISPLAY` 未登记** | UI 显示原始 model id（如 `claude-haiku-5.5`），无守卫 | 断言 `derived/points.json` 的每个 model 都能在 `scripts/compute.py` 的 `DISPLAY` 找到键（或命中 fallback 规则） |
| **`DATA_DATES` 未登记** | `data_date` 缺失 | 断言已存在于 `web/src/domain.test.ts`（"Every published point carries a data date of a known kind"），但**不在 `scripts/checks/run_all.py` 链里**：本地 run_all 不会红，只有 CI 第二 job 会红。建议新建 `scripts/checks/verify_data_dates.py`（用 Python 重写该断言，读 `derived/points.json`）；**不要**把 `npm --prefix web test` 塞进 run_all —— 那会让纯 Python 链依赖 node 与 web 构建 |
| **部署后线上核验** | 只构建不部署 → 线上仍是旧数据，靠人记得 curl | 给 `scripts/checks/run_all.py` 加可选 `--online` 档，执行 §6 的 curl 断言（默认不跑，避免离线环境红） |
| **促销 / 免费点白名单过期** | 促销结束后仍走常数回退，`scripts/checks/verify_list_blended.py` 的白名单静默放行 | 白名单条目加促销截止日字段，过期自动提示复核 |

## 8. 今天（2026-10-08）的真实事故对照

| 事故 | 命中哪一步 | 当时靠什么发现 | 现在哪个守卫兜住 |
|---|---|---|---|
| 落库精度 2 位（163.71 应为 163.711） | §3 | 人工复核 | **无**（见 §7） |
| 英文静态图残留中文 28 处 | §4 | 人工核验（charts-verify）；2026-10-08 复核发现 `verify_publication.py` 本可自动抓到 | `verify_publication.py`（`charts/en/**` 的 txt/svg/html CJK 断言） |
| `list_blended` 缺失 → 假前沿 | §2 | 前端表现异常 | `verify_list_blended.py` |
| AA 兜底死路径 / 跨模型价差 36.4× | §2 | 人工查价 | 分歧守卫 + `derived/list-price-divergence.json` |
| `medianEndToEndResponseTimeSeconds` 整列被裁 | §1 | 前端任务用时轴退化 | `verify_aa_payload_fields.py`（≥300 行） |
| 情景点漏登记 | §3 | scenario-guard 实测 | `verify_scenario_points.py`（WARN 兜底） |
