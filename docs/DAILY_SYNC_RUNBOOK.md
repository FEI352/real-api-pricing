# Real API Pricing · 每日同步 Runbook

本文件是每日自动化（Antigravity 计划任务 `real-plan-daily-sync`）的**唯一机械口径来源**。
Task Prompt 只写编排与门禁，所有"具体怎么做"都在这里；改口径只改本文件，不用动任务。

仓库：`/root/workspace/real-api-pricing`（下称 `$R`）。
**所有 Python 步骤必须用 `$R/.venv/bin/python`**——系统 python3 没有 matplotlib，`plot_quotas.py` / `verify_fee_bands.py` / `verify_chart_labels.py` 三步会直接挂。
**新增模型**：从「AA 出现新 slug」到「线上可见」的完整链（每步命令 + 验收判据 + 目前没有守卫兜底的缺口）见 [`ADD_A_MODEL.md`](ADD_A_MODEL.md)。

---

## 0. 阶段 0 · 预检（Lead 独占，不写任何文件）

固定中间路径（全文统一，不要换名字）：`/tmp/aa_models.html`（models 榜原始 HTML）、`/tmp/aa_agents.html`（coding-agents 原始 HTML）、`/tmp/aa_models_raw.json`（解析后的载荷 JSON）、`/tmp/upstream.json`（上游检查结果）。

1. `cd $R && git status --short` 记录脏文件；若工作树有**与本任务无关**的未提交改动，只读不覆盖。
2. 上游检查（确定性脚本，判定规则见 §2.3）：
   ```bash
   $R/.venv/bin/python scripts/upstream_check.py --json /tmp/upstream.json
   ```
   fork 拓扑：`origin` = `FEI352/real-api-pricing`（本项目自己的仓库），`upstream` = `FeiZhuLulu/real-api-pricing`（真正的上游）。`verdict=undetermined`（退出码 2）**不许当 noop 放过**。
3. 抓 AA 两个页面 + 载荷指纹（**同一个 UA**；抓取失败要重试，不要拿半截 HTML 往下走）：
   抓之前先跑数据源健康预检：`$R/.venv/bin/python scripts/source_health.py --json /tmp/source_health.json`——`exit 2`（有源无法判定或形态异常）时**不要进入抓取**，把失败写进 daily-log 并标 `[需人工介入]`。
   ```bash
   curl -sS -m 90 -A "Mozilla/5.0" https://artificialanalysis.ai/leaderboards/models -o /tmp/aa_models.html
   curl -sS -m 90 -A "Mozilla/5.0" https://artificialanalysis.ai/agents/coding-agents -o /tmp/aa_agents.html
   sha256sum /tmp/aa_models.html /tmp/aa_agents.html
   ```
   与上一版快照的 `extraction.payloadSha256` 比对（round6 起由 `scripts/aa_build_snapshot.py` 写入；更早的版本没有该字段，此时以第 4 条基线为准）。
4. 读上一版快照的 `scores` 条数、`collectedAt`、`rawRecords.aa_intelligence_index` 行数，作为"是否有变化"的基线（把文件名换成上一版）：
   ```bash
   $R/.venv/bin/python -c "import json;d=json.load(open('$R/data/research/scores-aa-round6-2026-10-08.json'));print(len(d['scores']), d['collectedAt'], len(d['rawRecords']['aa_intelligence_index']), d['extraction'].get('payloadSha256'))"
   ```

## 1. 阶段 1 · AA 快照刷新（Worker-AA）

目标：产出 `data/research/scores-aa-round<N>-<YYYY-MM-DD>.json`。
工具：`scripts/aa_fetch_parse.py`（解析）+ `scripts/aa_build_snapshot.py`（构建），**已纳管进版本控制**；`_build/aa_*.py` 是旧副本（写死 `/tmp` 输入与 2026-10-08 日期），已废弃、不要用。
**换榜（AA round bump）当天**：先读 [`AA_ROUND_BUMP_CHECKLIST.md`](AA_ROUND_BUMP_CHECKLIST.md)——操作顺序、预期会红的守卫（含写死常量与文件行号）、以及哪些红绝不能靠放宽断言解决。

1. 抓取（§0.3 已抓过就直接复用同一批文件；**同一个 UA**）：
   ```bash
   curl -sS -m 90 -A "Mozilla/5.0" https://artificialanalysis.ai/leaderboards/models -o /tmp/aa_models.html
   curl -sS -m 90 -A "Mozilla/5.0" https://artificialanalysis.ai/agents/coding-agents -o /tmp/aa_agents.html
   ```
2. 解析 models 页（两个位置参数：输入 HTML、输出载荷 JSON）：
   ```bash
   $R/.venv/bin/python scripts/aa_fetch_parse.py /tmp/aa_models.html /tmp/aa_models_raw.json
   ```
   stderr 预期有 `models rows: 696`（数字随榜单变化）与 `input sha256: ...`；退出码 1 = 读不到文件或页面结构变了。
3. 构建快照（**路径/日期/轮次/映射全部由参数给，脚本内无写死**）：
   ```bash
   $R/.venv/bin/python scripts/aa_build_snapshot.py \
     --models-json /tmp/aa_models_raw.json \
     --agents-html /tmp/aa_agents.html \
     --models-html /tmp/aa_models.html \
     --collected-at 2026-10-08 --round 6 \
     --prior scores-aa-round5-2026-10-01.json --prior scores-aa-round4-2026-09-22.json \
     --slug-map claude-haiku-5-5=claude-haiku-5.5 --slug-map claude-haiku-5-5-xhigh=claude-haiku-5.5 \
     --slug-map claude-haiku-5-5-high=claude-haiku-5.5 --slug-map claude-haiku-5-5-medium=claude-haiku-5.5 \
     --slug-map claude-haiku-5-5-low=claude-haiku-5.5 \
     --revises data/research/scores-aa-round5-2026-10-01.json \
     --output data/research/scores-aa-round6-2026-10-08.json
   ```
   - **`--agents-html` 必须是 coding-agents 页的原始 HTML**（不是解析后的 JSON）：喂错会静默少 30 行 coding 分数，无任何报错。
   - `--models-html` 决定 `extraction.payloadSha256`（§0.3 的早退判据）；不给会 WARN 并把该字段写 null。
   - 输出文件已存在时脚本**拒绝覆盖**（退出码 1）；确认要重写同一版才加 `--force`。
   - `--prior` 吃 `data/research/` 下的文件名或任意路径，可重复；`--slug-map` / `--host-map` 只做**精确同名**映射（近似名 `-next` / `-preview` / `-turbo` 一律不映射，落 `unmappedPublicRows`）。
4. 脚本已实现的口径（**改动前先读脚本 docstring**）：
   - `scores` 收录：智力榜全部 effort 变体 + TB4（`terminalBench40×100`）+ 编程 Agent（`indexScore×100`）；
   - **分数四舍五入到 4 位小数**（round5 起的既有约定，校验器容差 5.1e-5）；`costPerTaskUsd = round(intelligenceIndexCostPerTask, 4)`；
   - `configurationRank` = 按 `intelligenceIndex` 降序的全局名次（自校验：`claude-haiku-5-5` 应为 43）；
   - `effort` 从 `shortName` 正则取（载荷无 effort 字段，与 `benchmark_configs.EFFORT` 同规则）；
   - `variantLabel = shortName or name`（**不要自造标签**，校验器会比对载荷原字段）；
   - curated `secondary` 带 AA 载荷的价格字段（`price1mInputTokens` / `price1mOutputTokens` / `cacheHitPrice`，缺省 `null`）：`compute.py` 的 AA 兜底与 `benchmark_configs.configuration()` 靠它算每个配置自己的 `benchmark_list_price`；缺了会退回 `MEDIAN_AA_TASK_MTOK(3.51759)` 常数（GPT-6 Luna 假前沿的根因链，2026-10-08 修）。
5. **rawRecords 必须原样存全量载荷行**：`rawRecords.aa_intelligence_index` 存全部行（2026-10-08 实测 696 行），其中带 `medianEndToEndResponseTimeSeconds` / `medianOutputTokensPerSecond` 的行数 **≥300 行**（同日实测 351 行）。裁剪这两个字段会让"任务用时"轴静默退化（round5 只有 12 个配置有值），构建后必须数一遍：
   ```bash
   $R/.venv/bin/python -c "import json;d=json.load(open('$R/data/research/scores-aa-round6-2026-10-08.json'));r=d['rawRecords']['aa_intelligence_index'];print(len(r), sum(1 for x in r if x.get('medianEndToEndResponseTimeSeconds') is not None))"
   ```
6. 注册：把新文件名插进 `scripts/compute.py` 的 `SCORE_FILES`（该文件由 Lead 独占），**位置紧跟被替换的那一版之后**（例如 round6 插在 `scores-aa-round5-2026-10-01.json` 之后、`scores-terminal-bench4-round3-2026-10-01.json` 之前）。
   - 原因：`current_score_records()` 对无 `baseSnapshot` 的 supplement 用 `position[board] < index` 判定存废。放到列表**末尾**会误杀 `scores-gemini-4-argon-*`、`scores-hyperqwen-local-*` 两个 supplement。
   - 新快照的 `revises` + `revisionNote` 由 `--revises` / `--revision-note` 写入，说明替换了哪一版、新增了什么。
   - 模型集差分（新增/消失，供报告用）：`$R/.venv/bin/python scripts/check_gate1.py --diff-models <上一版快照文件名>`。
   - 门禁 1（点数 ≥ 上一版 / 新模型在 points.json / 新模型订阅点 `list_blended` 非空）由 `scripts/check_gate1.py` 判定，用法见该脚本 docstring 与每日 Prompt 阶段 2。

**每日必改常量清单**（漏改哪一条，后果就在右列）：

| 常量 | 传参 / 位置 | 2026-10-08 的值 | 漏改的后果 |
|---|---|---|---|
| 采集日 | `--collected-at` | `2026-10-08` | 快照文件名、`collectedAt`、每行 `checkedAt` 全错 |
| 轮次号 | `--round` | `6` | 文件名轮次错、默认 revises 文本错 |
| 输出路径 | `--output` | `data/research/scores-aa-round6-2026-10-08.json` | 默认名 `round<N>-<date>` 与实际不符 |
| 上一版快照 | `--prior`（可重复） | `scores-aa-round5-2026-10-01.json` + `scores-aa-round4-2026-09-22.json` | 继承不到 slug/host 映射 → 大量行落 `unmappedPublicRows` |
| 新增精确映射 | `--slug-map` / `--host-map` | `claude-haiku-5-5=claude-haiku-5.5`（5 条 effort 变体） | 新模型不进 `scores`，前端缺分 |
| 被替换版本 | `--revises` | `data/research/scores-aa-round5-2026-10-01.json` | 溯源链断，`verify_aa_snapshot.py` 交叉核对不上 |
| `SCORE_FILES` 插入位置 | `scripts/compute.py`（Lead） | round6 紧跟 round5-2026-10-01 | 插到末尾会误杀两个 supplement |
| 标价档 + `LIST_PRICE_FILES` | `data/research/list-prices-*.json` + `scripts/compute.py`（Lead） | `list-prices-claude-haiku55-round1-2026-10-08.json` | `list_blended` 为 `None` → 每任务成本走常数回退，前沿画歪（见 §2.1） |
| 新增分档情景行 | 登记进 `scripts/checks/verify_scenario_points.py` 的 `SCENARIO_PLAN_IDS`（照现有 4 条格式：`plan_id` → 说明；**清单是权威判据，不靠命名正则猜**） | 本轮 4 条：`claude_pro_over100k` / `claude_pro_mix5050` / `claude_max_20x_over100k` / `claude_max_20x_mix5050` | 守卫断言 1/2 不覆盖该点；第 4 项只 **WARN**（**只有该点污染了某榜前沿才 FAIL**）。别指望 `decision_note` 关键词兜底——实测 4 行里只有 2 行写了「情景点」字样 |

## 2. 阶段 2 · 标价与采用值（Worker-Pricing）

1. **新模型必须建标价档**：`data/research/list-prices-<model>-round1-<date>.json`（照 `list-prices-claude-sonnet55-round1-2026-09-30.json` 的 schema），并加进 `scripts/compute.py` 的 `LIST_PRICE_FILES`。
   - **`models[].model` 必须是项目模型 id（`claude-haiku-5.5`），不是官方 API id（`claude-haiku-5-5`）**。写错**不报错**：`load_list_prices()` 按 `scores[].model` 查表，查不到就是 `None` → 直接落进下面那条常数回退。
   - 必填键：`model` / `input` / `cachedInput`（可 `null`，按 `input×0.1` 兜底）/ `output` / `currency`；缺键会 `KeyError`（响亮失败，好排查）。
   - `LIST_PRICE_FILES` 里**后出现的文件覆盖同名 `model` 键**：新模型档插在哪都行；但改价/改档必须新建 `round2+` 文件并放在旧文件**之后**，否则旧价胜出。
   - 缺这一步的后果：该模型所有订阅点的 `list_blended_usd_per_mtok` 为 `None` → 前端"每任务成本"轴退化为 `MEDIAN_AA_TASK_MTOK(3.51759) × 真实单价` 常数回退 → **多个 effort 档被压到同一 x**，且前沿会被画歪（2026-10-08 的 GPT-6 Luna 假前沿就是此因）。
   - **两层同构的价格分歧守卫**（阈值同用 `[0.5, 2.0]`，2026-10-08 起；判定结果落在 `derived/list-price-divergence.json`，**该文件是灰区人工复核入口**——例如"点的档价本身是促销价"这类情形只能人工判）：
     · **配置层**（`compute.apply_price_divergence`）：`配置 AA 价 ÷ 项目 curated 档价` 越界 → 该配置 `benchmark_list_price` 置 `None`（回退到点的 `list_blended`）。实测 `kept 139 / reverted 9 / no_curated_baseline 8`（`reverted_models` 列出被回退的模型）。
     · **点层（跨模型）**（`compute.py` points 循环内）：`点 model ≠ 选中配置 model` **且** `配置 AA 价 ÷ 点自己的 list_blended` 越界 → 该 (点, 榜) 的 `benchmark_list_price` 置 `None`。实测 4 个 cell 全部 **36.4×** 回退，摘掉了 `opencode_go::muse-spark-1.3-contributor` 的**假前沿**（每任务成本 0.0073 → 0.2695，AA 智力榜前沿 **−1 +2**）；明细在 `cross_model_cells`。
     · 界内不触发（保护合法同价别名）；点无 `list_blended`（`no_curated_baseline`）不触发，保留 AA 价。
2. 订阅派生行一律写在 `scripts/build_adopted.py`（**它是 `data/adopted.csv` 的生成器，手改 CSV 必被覆盖**）：
   - 新模型按"基准模型采用值 × 标价混合比"派生，比值用 `blended(cache_read, input, output)` 同口径计算（例：`RATIO_HAIKU55 = blended(0.2,4,20)/blended(0.01,0.10,0.50) = 26.802721`）；
   - 新增 `DATA_DATES` 键（官方发布日 / 样本日 / derived 继承锚点）；
   - `scripts/compute.py` 的 `DISPLAY` 加显示名，否则 UI 显示原始 model id。
3. 上游同步：`$R/.venv/bin/python scripts/upstream_check.py --json /tmp/upstream.json`（只读 git：`fetch` + `log` + `diff --name-only`，**不 merge / 不 checkout / 不改任何文件**）。判定规则：
   - `verdict=noop` → 上游无新提交，本轮无上游动作；
   - `verdict=review` → 逐个评估 `commits[]`；先看 `flags.conflictRiskPrefixes`（命中 `scripts/` `web/` `docs/` 的上游改动）与 `flags.localOverlap`（与本地脏文件重叠，最可能冲突），再看 `flags.touchesDataSignificant`（`data/` 下非生成物）。**不做自动 merge、不解决冲突**，冲突就停下来报告；
   - `verdict=undetermined`（退出码 2，reason ∈ `remote_missing` / `fetch_timeout` / `fetch_failed` / `ref_missing`）→ 明确报「无法判定」，**不许静默当 noop**；
   - 示例输出（2026-10-08 实跑，上游确无新提交，`--json /tmp/upstream.json` 同时落盘）：
     ```json
     {"verdict": "noop", "commitCount": 0, "commits": [], "head": "3895ceb897623b54ef6d01ca1a19fab3d13190d3",
      "upstreamHead": "dce850df080582f2926a3376f97a2edf6020a508",
      "reason": "upstream/main 相对 HEAD 无新提交（merge-base dce850df080582f2926a3376f97a2edf6020a508）"}
     ```
4. 自搜（Worker-Search）：用 `web_search` 查新模型发布 / 新套餐 / 降价 / 榜单版本变化，产出候选清单写入 `data/research/probe-<date>.json`（只写探针文件，**不直接改采用值**）。

## 3. 阶段 3 · i18n（Worker-Verify 的第一步）

任何 `source` / `decision_note` / `note` 字符串变了，`npm --prefix web run data` 都会硬失败（`No English translation for: ...`）：

1. `$R/.venv/bin/python scripts/checks/verify_i18n.py --sync` 补空条目（同时会删孤儿条目）；
2. 手工填英文：**不得含 CJK / 全角**；URL、文件名、数字多重集必须与中文严格一致；
   - `亿` → `B`（值 ÷10），sub-billion 的 `亿` → `M`（值 ×100），`万` → `K`（值 ×10），无单位数字原样。

## 4. 阶段 4 · 构建与校验（Worker-Verify）

顺序照 `BUILD.md`，全部用 `.venv/bin/python`：

```
build_adopted.py → compute.py → checks/verify_i18n.py → readme_stats.py
→ checks/verify_benchmark_configs.py → checks/verify_aa_snapshot.py → checks/verify_deepswe.py
→ plot_svg.py → node scripts/render_svg.cjs → build_html.py → node scripts/checks/verify_configuration_html.cjs
→ plot_quotas.py → checks/verify_fee_bands.py → checks/verify_chart_labels.py → publish_charts.py
→ checks/verify_svg.py → checks/verify_label_overlap.py → checks/verify_four_boards.py
→ checks/verify_publication.py → checks/verify_palette.py → readme_stats.py --check
```

**已知噪声（不是回归，不要为它改数据）**：
- `verify_label_overlap.py` 在本机必红（约 130~160 项）：本机没有 Microsoft YaHei，字形度量与图表不一致。判定方法：违规数与"未改动基线"相当即放过。
- `verify_benchmark_configs.py` / `verify_aa_snapshot.py` 若报断言，先确认是不是**期望值过期**（历史上出现过 12 位小数 vs 4 位舍入的冲突、以及本地机型行被误当 AA 镜像核对）。

**统一入口（首选）**：`$R/.venv/bin/python scripts/checks/run_all.py` —— 按 `BUILD.md` 顺序跑全部 check，逐条打印 `OK / KNOWN-NOISE / FAIL`，有 FAIL 即非零退出（唯一允许的 KNOWN-NOISE 是 `verify_label_overlap`）。上面那份手抄清单会随 `BUILD.md` 增删漂移（守卫已有 `verify_aa_payload_fields` / `verify_haiku55_tiers` / `verify_list_blended` / `verify_scenario_points` 等），**清单以 `BUILD.md` 与 `run_all.py` 为准**。

## 5. 阶段 5 · 部署（Worker-Verify）

```bash
/usr/local/bin/real-api-pricing-cli update     # build_adopted → compute → web 构建 → cp 到 /var/www/real-plan
```

线上站点由 nginx 从 **`/var/www/real-plan/`** 提供（不是 `web/dist`，不是 `web/public`）。只重建不部署 = 线上不变。
验证（必须做）：

```bash
curl -s https://real-plan.fja.su/data/site.json | $R/.venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print(len(d['points']), d['conventions']['updatedAt'])"
```

nginx 对 `/data/` 与 `/index.html` 发 `no-cache, must-revalidate`，部署后普通刷新即可见。

## 6. 阶段 6 · 变更侦测与发信（Lead 独占）

1. 算当前前沿（AA 智力榜，`config=all`，x = 每任务成本）：
   `costPerTask = mean_cost_usd_per_task × (real_usd_per_mtok ÷ (benchmark_list_price or list_blended))`；空则 `3.51759 × real`。Pareto：按成本升序扫描，分数高于此前所有点的才入前沿。
   - 现成实现：`/usr/local/bin/real-api-pricing-cli diff`（`scripts/cli.py`，发信判据的权威口径，读 `web/public/data/site.json` + `data/research/frontier-state.json`）。
   - `hyperqwen_local_*` 排除**不在 `scripts/cli.py` 里**：本地机型点的 site 数据 mappings `board=None`（天然进不了 AA 智力榜前沿），前端另有 `web/src/domain.ts::isLocalHardware` 把它们归到 Local 渠道。手写前沿脚本时要自己按同一口径排除，别去找 cli.py 里的开关。
2. 与 `data/research/frontier-state.json`（Lead 维护）比对：前沿点集合、各点分数、快照 `collectedAt`。
3. **有变动** → `/usr/local/bin/real-api-pricing-cli send-email`（或 `chart` + `send-email`），邮件必须写清：新增/掉出的前沿点、分数变化、新模型采用值、数据日期。
4. **无变动** → 不发信；把本轮结论追加到 `data/research/daily-log-<YYYY-MM>.md`。
5. 无论发不发信，都更新 `frontier-state.json`。

## 7. 阶段 7 · 收尾（防静默过期，Lead 独占）

收尾动作的共同点：**过期不会报错，只会让答案静默失真**。所以每条都必须显式跑、显式记录；脚本缺失或报错一律按「无法判定」处理，**不许静默跳过**。

| # | 动作 | 命令 | 强制性 | 失败/差异处置 |
|---|---|---|---|---|
| 1 | 可见性索引重生成 | `$R/.venv/bin/python scripts/visibility_matrix.py --write-docs` | **必须**（<1s、幂等；重跑只刷新数字与日期，骨架 336 points / 540 configurations / 2785 mappings / 8 榜不变） | 脚本缺失或报错 → 「无法判定」：daily-log 记 `[需人工介入] visibility_matrix 未运行：<原因>`，并写明「`docs/VISIBILITY.md` 本轮未更新、可能与数据不一致」 |
| 2 | 图表指纹比对 | `$R/.venv/bin/python scripts/chart_fingerprint.py --compare data/research/chart-fingerprint-<最新基线日期>.json`（当前基线 `chart-fingerprint-2026-10-08.json`；用 `ls -t data/research/chart-fingerprint-*.json \| head -1` 取最新） | **必须**（唯一可比的「图表是否真变」判据，替代人眼比对） | 退出码 `0` = 无变化（daily-log 写「本轮图表未变」）；`1` = 有变化（把 added/removed/changed 计数与代表文件写进 daily-log）；其他非 0 或基线缺失 → 「无法判定」，记 daily-log，**不要退回人眼比对** |
| 3 | 线上数据核验 | 见 §5（点数 + `conventions.updatedAt`） | **必须** | curl 失败 → 「无法判定」，不许当「线上已更新」 |
| 4 | 前沿状态与留痕 | 见 §6.4 / §6.5 | **必须** | — |
| 5 | 重写图表指纹基线 | `$R/.venv/bin/python scripts/chart_fingerprint.py --write data/research/chart-fingerprint-<本轮日期>.json` | 可选（仅当本轮图表确实重出） | 写失败 → 记 daily-log，沿用旧基线并在下一轮注明 |

- 三条「无法判定」的记录格式统一：`[需人工介入] <动作> 未完成/未判定：<原因>`，追加进 `data/research/daily-log-<YYYY-MM>.md`；**没有记录 = 视为本轮未做**。
- 与陷阱表的对应：本节第 1/2 条的失败口径 ↔ #16；「已存在即拒绝覆盖」`--force` 守卫 ↔ #12 / #14；`--slug-map` 逐条给全 ↔ #15。同一件事只在这两处出现，改动时两处一起改。

---

## 陷阱速查（每条都是真实踩过的）

| # | 陷阱 | 后果 |
|---|---|---|
| 1 | 手改 `data/adopted.csv` | 下次 `build_adopted.py` 整表覆盖，白干 |
| 2 | 新快照插到 `SCORE_FILES` 末尾 | 误杀 gemini-4-argon / hyperqwen supplement |
| 3 | `rawRecords` 裁剪字段 | 任务用时轴静默退化（round5 掉到 12 个配置；门槛是带 `medianEndToEndResponseTimeSeconds` 的行数 ≥300） |
| 4 | 新模型不建 list-prices 档 | 每任务成本走常数回退，多 effort 档压到同一 x，前沿画歪 |
| 5 | 用系统 python3 跑管线 | matplotlib 相关三步直接失败 |
| 6 | 改了文案没跑 i18n --sync | `npm run data` 硬失败，网站构建不出来 |
| 7 | 只构建不部署 | 线上还是旧数据（用户会先发现） |
| 8 | 拿 Anthropic 含 cache-write 价当标准负载混合价 | 比值偏高约 6%（0.419 vs 0.394） |
| 9 | 用 `_build/aa_*.py` 旧副本（写死 `/tmp` 输入与 2026-10-08 日期） | 输入路径对不上；或静默覆盖上一版快照、日期全错。口径以 `scripts/aa_*.py` 为准 |
| 10 | `--agents-html` 喂成解析后的 JSON | 静默少 30 行 coding 分数，无报错 |
| 11 | list-prices 的 `models[].model` 写成官方 API id（`claude-haiku-5-5`） | 静默 `None` → 同 #4 的常数回退，前沿画歪 |
| 12 | `--collected-at` / `--round` / `--output` 沿用昨天的值 | 覆盖上一版快照、`checkedAt` 全错（脚本已用"已存在即拒绝覆盖"兜底） |
| 13 | 上游检查报 `undetermined`（无远端/fetch 超时）却当 noop 放过 | 漏掉上游改动，采用值悄悄过期 |
| 14 | 同日重跑 AA 抓取（隔十几分钟再抓一次） | `costPerTaskUsd` 会变：实测 22 分钟内 268/696 行 `medianEndToEndResponseTimeSeconds`、170 行 tok/s、5 行 `intelligenceIndexCostPerTask` 都变了 → 快照不可复现。固定时间跑；同日重写同一版必须显式 `--force`（脚本默认拒绝覆盖） |
| 15 | 漏传 `--slug-map`（只给主 slug、漏 effort 变体） | **静默少行**：实测漏 4 条 → 303 vs 311（少 8 行 = 4 智力榜 + 4 TB4）。照 §1「每日必改常量清单」逐条给全 |
| 16 | 收尾步骤（可见性索引 / 图表指纹）缺失或报错就跳过 | 答案静默失真：`docs/VISIBILITY.md` 与图表指纹停在旧数据且无记录。按 §7 记为「无法判定」并写 daily-log（`[需人工介入] …`） |
| 17 | **分母取错会翻转事故方向**：把「跨模型配置的 AA 价」当成点的档价（分母偏大 36.7×） | 每任务成本被**低估**同样倍数 → 假便宜 → 假前沿（2026-10-08 `muse-spark-1.3-contributor` 实测 36.4×，成本 0.0073 → 0.2695）。当时先误判成「假贵」；判定方向必须由实测数字决定，别靠直觉。两层分歧守卫（§2.1）就是为此 |
