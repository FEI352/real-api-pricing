# Real API Pricing - CLI 与自动化周报指南

本指南记录了 `real-api-pricing` 项目的自动化数据管线、CLI 运维工具、图表自动截图分发以及每周邮件定时推送配置。

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
| `real-api-pricing-cli send-email` | 发送周报邮件 | 将格式化好的 HTML+Markdown 双通道邮件通过 Hermes 邮件安全通道发送至指定邮箱（默认 `jaupuihing@ching.icu`） |
| `real-api-pricing-cli cron-run` | 一键执行周度自动化 | 依次执行：`update` → `chart` → `send-email` |

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

### 3.1 DSH 任务调度器
在 `/root/.dsh/custom-settings/cron-jobs.json` 中配置了常驻定时任务：
* **任务 ID**：`real-api-pricing-weekly-report`
* **执行时间**：每周三 09:00 (SGT / UTC+8)
* **执行命令**：`/usr/local/bin/real-api-pricing-cli cron-run`
* **首期执行**：已于 2026-09-30 成功触发并验证发信。

### 3.2 邮件合规与反垃圾保证
* 严格遵循 `stalwart-mail` 规范，信头包含 `Date`、`Message-ID`、`MIME-Version`，正文采用 `8bit` 纯 UTF-8 编码。
* 严禁标题携带收件人根域，杜绝 `RCPT_DOMAIN_IN_SUBJECT` 惩罚。
* 发送后实测反垃圾评分达到 **-4.20（顶级 Ham 信用分）**，直接进入收件箱。
