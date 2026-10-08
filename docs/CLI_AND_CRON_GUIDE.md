# Real API Pricing - CLI 与自动化周报指南

本指南记录了 `real-api-pricing` 项目的自动化数据管线、CLI 运维工具、图表自动截图分发，以及每日巡检「有变动才发信」的邮件推送配置。

---

## 1. 架构与工具链

### 1.1 CLI 命令行工具
CLI 工具位于 `scripts/cli.py`，并已软链接至系统路径 `/usr/local/bin/real-api-pricing-cli`，支持全局执行。

```bash
# 查看所有支持的命令
real-api-pricing-cli --help
```

### 1.2 核心子命令说明

| 命令 | 说明 | 对应执行流程 |
| :--- | :--- | :--- |
| `real-api-pricing-cli update` | 全量更新与部署 | 1. 运行 `scripts/build_adopted.py`<br/>2. 运行 `scripts/compute.py`<br/>3. 执行 `npm run data && npm run build`<br/>4. 将产物部署至 `/var/www/real-plan/` |
| `real-api-pricing-cli chart` | 图表自动截图与图床分发 | 1. 启动无头 Chromium 渲染 `https://real-plan.fja.su/#lang=zh&config=summary&xmode=cost`<br/>2. 生成高分辨率 PNG 截图至 `/tmp/real-plan-cost-per-task.png`<br/>3. 通过 `hermes-share` 同步至图床并生成直链 `https://ching.icu/downloads/cost-per-task-latest.png` |
| `real-api-pricing-cli report` | 终端生成最新前沿报表 | 解析 `site.json`，计算并打印：<br/>- Cost per Task 帕累托前沿<br/>- 真实单价前沿 |
| `real-api-pricing-cli send-email` | 发送简报邮件 | 将格式化好的 HTML+Markdown 双通道邮件通过 Hermes 邮件安全通道发送至指定邮箱（默认 `jaupuihing@ching.icu`） |
| `real-api-pricing-cli cron-run` | 一键执行自动化巡检 | `update` → 比对前沿 → **有变动才** `chart` + `send-email`（`--force` 无条件发信） |

---

## 2. 计算与筛选逻辑

### 2.1 任务成本 (Cost per Task) 换算
针对 Artificial Analysis 上的模型基准，若要折算在具体订阅套餐下的实际执行成本：
$$\text{Cost per Task}_{\text{sub}} = \text{Cost per Task}_{\text{AA}} \times \frac{\text{real\_usd\_per\_mtok}}{\text{benchmark\_list\_price}}$$
* **`benchmark_list_price`**：AA 评测时采用的官方 API 混合标价。
* **`real_usd_per_mtok`**：用户订阅该套餐后的实际每百万 Token 成本。
* **保底估算**：对于未单独跑任务成本的估算模型（如部分 Flash / 蒸馏小模型），使用全榜任务平均负载（~3.52 MTok/task）× 真实单价进行推算，确保所有上榜模型均有成本展示。

### 2.2 帕累托前沿 (Pareto Frontier)
* **X 轴（Cost per Task）**：从左到右单任务成本由低到高（对数坐标）。
* **Y 轴（Intelligence Index）**：AA 智力指数由低到高。
* **前沿点**：同等或更低成本下无法取得更高分数的模型集合。

---

## 3. 定时任务 (Cron Agent) 配置

### 3.1 主任务：Antigravity 计划任务 `real-plan-daily-sync`（2026-10-08 起）

* **任务名**：`Real API Pricing 每日同步（Agent Team）`（sidecar，Antigravity Web UI → Scheduled Tasks 可见）
* **执行时间**：每天 09:00 SGT（`0 9 * * *`）
* **Task Prompt**：`docs/daily-sync-task-prompt.md`（仓库内版本化；sidecar 里存的是它的副本，改完需重新 `agy-schedule create` 覆盖）
* **机械口径**：`docs/DAILY_SYNC_RUNBOOK.md`（唯一权威，含陷阱速查）
* **编排**：Lead + 5 个 Worker（AA 快照 / 标价与采用值 / 自搜 / 校验与部署 / 前沿比对），写作用域互斥，两道门禁在 Lead 手里
* **发信策略**：**有前沿变动才发**；无变动只写 `data/research/daily-log-YYYY-MM.md`
* **状态基线**：`data/research/frontier-state.json`

### 3.2 判定原语（CLI）

| 命令 | 用途 |
| :--- | :--- |
| `real-api-pricing-cli diff` | 输出与 `frontier-state.json` 的结构化差异（JSON），供自动化判断是否发信 |
| `real-api-pricing-cli cron-run` | 更新部署 → 比对前沿 → **有变动才发信**（`--force` 恢复无条件发信） |

**`diff` 输出字段语义（2026-10-08 定稿）**

| 字段 | 含义 |
| :--- | :--- |
| `alert` | **真正的前沿变动**：`cost_frontier` 或 `price_frontier` 出现新增 / 掉出 / 分数变化 |
| `changed` | **notify 口径**：`alert` 为真，或仅收录点数变化（`total_points_delta != 0`）——决定是否发信 |
| `baseline` | 首次基线（`frontier-state.json` 不存在）：仍发一封，但走简报口径，它不是「变动」 |
| `degraded` | 基线缺某个前沿（旧格式）时的降级清单；非空即发信并在邮件里带 `[需人工介入]` |
| `added` / `removed` / `score_changed` | 每条变动项带 `frontier` 字段：`cost` / `price` / `cost+price`（同点同时在两个前沿变化时合并为一条，避免重复列出） |

**流契约（2026-10-08 定稿）**

> **`stdout` = 纯 JSON（`diff`）/ 报表文本（`report`）；进度与告警一律走 `stderr`。**
> 即 `real-api-pricing-cli diff | python3 -c "import json,sys; json.load(sys.stdin)"` 必须始终可解析。
> 反例（已修 P1）：`[refresh] site.json 落后于数据源，重建中…` 曾打到 stdout，导致常规流程
> （阶段 2 跑完 compute 让 site.json 过期 → 阶段 3 跑 diff）必然破坏 JSON 管道。
> `diff --json-only` 为显式声明：在默认已纯 JSON 的基础上，进一步压制 `stderr` 的进度提示
> （`[需人工介入]` 告警仍保留），便于严格脚本使用。

* **主题口径**：`alert` 为真 → 「大模型性价比前沿变动通知 (日期)」；否则（含仅点数变化、首次基线）→ 「大模型性价比前沿简报 (日期)」。
* **仅点数变化**时，邮件变动块写明「收录点数 ±N，前沿榜无变化」——既不误报成「变动」，也不静默吞掉。
* **`price_frontier` 参与判定**：真实单价前沿与任务成本前沿同等对待，各自算 added / removed / score_changed 后合并去重。

**失败路径（宁可吵不要哑，2026-10-08 加固）**

数据缺失 / JSON 损坏 / 基线结构非法 / `npm run data` 失败 / 邮件发送失败时，判定**绝不静默跳过**：

* stderr 打印带 `[需人工介入]` 的明确原因；`cron-run` 同时写入 `data/research/daily-log-YYYY-MM.md` 留痕；
* **退出码非 0**（判定失败 `2`、其他执行失败 `3`），并**跳过发信与基线更新**——保留旧基线以便下轮重试，避免「发信失败 → 基线照更新 → 通知永久丢失」；
* `frontier-state.json` **不存在**视为合法首次基线（显式打印）；**存在但损坏**（坏 JSON / `null` / `{}` / 缺 `cost_frontier`·`price_frontier` / `total_points` 非正整数）一律报错，绝不静默当成首次基线，也绝不把整张前沿误报成「新进」。

### 3.3 历史任务：DSH 每周周报（`0 9 * * 3`）

* 任务名：`Real API Pricing 每周性价比周报`，prompt 为「执行 `real-api-pricing-cli cron-run`」。
* 自 2026-10-08 起 `cron-run` 已改为**有变动才发信**，因此该周任务退化为「每周一次兜底巡检」：正常情况不发信，不会与每日任务重复打扰。
* 它的 prompt 未含 AA/上游同步能力，已被每日任务取代；如需彻底停用，在 DSH Web UI → Settings → Cron Jobs 里关闭即可。

### 3.4 邮件合规与反垃圾保证
* 严格遵循 `stalwart-mail` 规范，信头包含 `Date`、`Message-ID`、`MIME-Version`，正文采用 `8bit` 纯 UTF-8 编码。
* 严禁标题携带收件人根域，杜绝 `RCPT_DOMAIN_IN_SUBJECT` 惩罚。
* 发送后实测反垃圾评分达到 **-4.20（顶级 Ham 信用分）**，直接进入收件箱。
