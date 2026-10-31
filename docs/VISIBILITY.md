# 模型 × 榜 可见性矩阵 / Model-by-board visibility

> 由 `python scripts/visibility_matrix.py --write-docs` 生成，请勿手改。
> 生成时间：2026-10-31 09:16 UTC+08:00；数据：`web/public/data/site.json`（generatedAt=2026-10-29）。
> 口径：mappings 与 configurations 的 join 与 `web/src/loadData.ts` 的 `unpackData()` 完全一致；可见性判据与 `web/src/domain.ts` 一致。

## 1. 榜覆盖统计

| 榜 | 名称 | 快照 | 配置数 | 映射数 | 有分模型 | 最高分 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `aa_intelligence_index` | Artificial Analysis Intelligence Index v4.3 | 2026-10-31 | 74 | 413 | 33 | 59.4257 · `gemini-4-carbon` |
| `terminal_bench_4` | Terminal-Bench 4.0 | 2026-09-21 | 30 | 170 | 18 | 66.4 · `claude-opus-5.5` |
| `aa_terminal_bench_4` | Terminal-Bench 4.0 (AA) | 2026-10-31 | 75 | 418 | 33 | 67.1717 · `kimi-k3.1-pro` |
| `arena_code` | Code Arena \| WebDev Overall | 2026-09-30 | 62 | 306 | 53 | 1818 · `claude-opus-5.5` |
| `arena_agent_mode` | Agent Arena · Overall (Ranking / Pareto) | 2026-09-30 | 40 | 218 | 38 | 14.55 · `claude-fable-5.1` |
| `aa_coding_agent_index` | Artificial Analysis Coding Agent Index v1.5 | 2026-10-31 | 20 | 132 | 16 | 61.6529 · `claude-opus-5.5` |
| `open_design_arena` | OpenDesign Arena · Quality ranking | 2026-10-01 | 16 | 79 | 16 | 90.1 · `gpt-6.1-sol` |
| `deepswe_1_1` | DeepSWE v1.1 | 2026-09-03 | 70 | 418 | 28 | 74.2 · `deepseek-v4.1-flash` |

## 2. 全模型 × 榜 可见性总表

`✓` = 该榜有分数（该榜 pareto / 前沿 / 表格可见）；`—` = 无分数 → 该榜图不显示。

| 模型 | `aa_intelligence_index` | `terminal_bench_4` | `aa_terminal_bench_4` | `arena_code` | `arena_agent_mode` | `aa_coding_agent_index` | `open_design_arena` | `deepswe_1_1` | 有分榜数 |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | ---: |
| `claude-fable-5` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `claude-fable-5.1` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | 7 |
| `claude-haiku-5.5` | ✓ | — | ✓ | — | — | ✓ | — | — | 3 |
| `claude-opus-4.8` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `claude-opus-5` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `claude-opus-5.5` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | 6 |
| `claude-sonnet-5` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `claude-sonnet-5.5` | ✓ | — | ✓ | ✓ | — | ✓ | ✓ | — | 5 |
| `composer-2.5` | — | — | — | — | — | — | — | — | 0 |
| `deepseek-v4-flash` | — | — | — | ✓ | — | — | — | ✓ | 2 |
| `deepseek-v4-flash-fast` | — | — | — | — | — | — | — | — | 0 |
| `deepseek-v4-flash-vision-exp` | — | — | ✓ | — | — | — | — | — | 1 |
| `deepseek-v4-pro` | ✓ | — | ✓ | ✓ | ✓ | — | — | ✓ | 5 |
| `deepseek-v4.1-flash` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 8 |
| `deepseek-v4.1-flash-fast` | — | — | — | — | — | — | — | — | 0 |
| `gemini-3.6-flash` | — | — | — | ✓ | ✓ | — | — | ✓ | 3 |
| `gemini-3.7-flash` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `gemini-3.8-flash` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 8 |
| `gemini-4-argon` | ✓ | — | ✓ | — | — | ✓ | — | — | 3 |
| `gemini-4-carbon` | ✓ | — | ✓ | — | — | — | — | — | 2 |
| `glm-5` | — | — | — | ✓ | — | — | — | — | 1 |
| `glm-5.1` | — | — | — | ✓ | — | — | — | — | 1 |
| `glm-5.2` | — | — | — | ✓ | ✓ | — | — | ✓ | 3 |
| `glm-5.2-fast` | — | — | — | — | — | — | — | — | 0 |
| `glm-5.3` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | 7 |
| `glm-5.3-flash` | ✓ | — | ✓ | ✓ | ✓ | — | — | ✓ | 5 |
| `glm-5.3-flashx` | — | — | — | — | — | — | ✓ | — | 1 |
| `gpt-5.5` | — | — | — | ✓ | ✓ | — | — | ✓ | 3 |
| `gpt-5.6-luna` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `gpt-5.6-sol` | — | ✓ | — | ✓ | ✓ | — | ✓ | ✓ | 5 |
| `gpt-5.6-terra` | ✓ | ✓ | — | ✓ | ✓ | — | — | ✓ | 5 |
| `gpt-6-astra` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 8 |
| `gpt-6-luna` | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ | — | 6 |
| `gpt-6-sol` | — | — | — | ✓ | ✓ | — | ✓ | — | 3 |
| `gpt-6.1-sol` | ✓ | — | ✓ | ✓ | — | ✓ | ✓ | — | 5 |
| `gpt-6.1-terra` | ✓ | — | ✓ | — | — | — | — | — | 2 |
| `grok-4.5` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `grok-4.6` | — | ✓ | — | ✓ | ✓ | — | — | ✓ | 4 |
| `grok-4.7` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 8 |
| `hy3` | — | — | ✓ | ✓ | ✓ | — | — | — | 3 |
| `hy4-preview` | — | — | — | ✓ | ✓ | — | ✓ | — | 3 |
| `inkling` | ✓ | — | ✓ | ✓ | ✓ | — | — | — | 4 |
| `inkling-small` | ✓ | — | ✓ | ✓ | ✓ | — | — | — | 4 |
| `kimi-k2.5` | — | — | — | ✓ | — | — | — | — | 1 |
| `kimi-k2.6` | — | — | — | ✓ | — | — | — | — | 1 |
| `kimi-k2.7-code` | — | — | ✓ | ✓ | — | — | — | ✓ | 3 |
| `kimi-k2.7-code-highspeed` | — | — | — | — | — | — | — | — | 0 |
| `kimi-k3` | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | 6 |
| `kimi-k3.1-pro` | ✓ | — | ✓ | — | — | — | — | — | 2 |
| `longcat-2.0` | — | — | ✓ | — | — | — | — | — | 1 |
| `mimo-v2.5` | — | — | — | ✓ | — | — | — | — | 1 |
| `mimo-v2.5-pro` | — | — | — | ✓ | ✓ | — | — | ✓ | 3 |
| `mimo-v2.6-flash` | ✓ | — | ✓ | — | — | — | ✓ | ✓ | 4 |
| `mimo-v2.6-pro` | ✓ | — | ✓ | ✓ | — | — | — | ✓ | 4 |
| `mimo-v2.6-pro-ultraspeed` | — | — | — | — | — | — | — | — | 0 |
| `minimax-m2.5` | — | — | — | ✓ | — | — | — | — | 1 |
| `minimax-m2.7` | — | — | — | ✓ | — | — | — | — | 1 |
| `minimax-m3` | ✓ | — | ✓ | ✓ | ✓ | — | — | — | 4 |
| `minimax-m3.1-flash-preview` | ✓ | — | ✓ | — | — | — | — | — | 2 |
| `mistral-medium-3.5` | — | — | ✓ | ✓ | ✓ | — | — | — | 3 |
| `muse-spark-1.2` | — | — | — | ✓ | ✓ | — | — | ✓ | 3 |
| `muse-spark-1.2-contributor` | — | — | — | — | — | — | — | — | 0 |
| `muse-spark-1.3` | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | — | 5 |
| `muse-spark-1.3-contributor` | — | — | — | — | — | — | — | — | 0 |
| `nemotron-3-ultra` | ✓ | — | ✓ | — | — | — | — | — | 2 |
| `qwen3.6-max-preview` | — | — | — | ✓ | — | — | — | — | 1 |
| `qwen3.6-plus` | — | — | — | ✓ | — | — | — | — | 1 |
| `qwen3.7-flash` | — | — | — | — | — | — | — | — | 0 |
| `qwen3.7-max` | — | — | — | ✓ | ✓ | — | — | — | 2 |
| `qwen3.7-plus` | — | — | ✓ | — | ✓ | — | — | — | 2 |
| `qwen3.8-27b` | ✓ | — | ✓ | ✓ | ✓ | — | — | — | 4 |
| `qwen3.8-27b-dflash-5090` | ✓ | — | — | — | — | — | — | — | 1 |
| `qwen3.8-27b-hyperqwen-3090` | ✓ | — | — | — | — | — | — | — | 1 |
| `qwen3.8-27b-rtx-pro-6000` | ✓ | — | — | — | — | — | — | — | 1 |
| `qwen3.8-flash` | — | — | — | — | — | — | — | — | 0 |
| `qwen3.8-max` | — | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | 5 |
| `qwen3.8-max-0902` | ✓ | — | ✓ | ✓ | — | — | — | — | 3 |
| `qwen3.8-omni-flash` | — | — | — | — | — | — | — | — | 0 |
| `step-3.5-flash` | — | — | — | — | — | — | — | — | 0 |
| `step-3.7-flash` | — | — | — | — | — | — | — | — | 0 |
| `step-5-preview` | ✓ | — | ✓ | ✓ | — | ✓ | — | — | 4 |
| `swe-2` | — | ✓ | — | — | — | — | — | — | 1 |

## 3. 无分也能看到的位置

- **真实单价轴**（默认 X）：`real_usd_per_mtok`，任何点都能画，不需要榜分数。
- **月额度视图**：需要 `monthly_yi`（订阅点），同样不需要榜分数。
- **完整数据表**：该榜无分的点仍会列出，只是分数列为空。
- 反面：**能力图 / 前沿图**（Y 轴是榜分数）与 **按榜排序** 只对「该榜有分」的模型有效；`任务用时` 轴需要映射配置的 `response_time_seconds`，`每任务成本` 轴需要 `median_cost_usd_per_task`（缺失时走 `MEDIAN_AA_TASK_MTOK × 真实单价` 常数回退）。

## 4. 常见误判（2026-10-08 真实案例）

### 案例 1：「榜单没看到 claude-haiku-5.5」

`claude-haiku-5.5` 只在 **3/8** 个榜有分：`aa_coding_agent_index`、`aa_intelligence_index`、`aa_terminal_bench_4`；其余 5 个榜没有它的配置映射，所以切到那些榜时图里看不到它——不是数据缺失，也不是过滤问题。

### 案例 2：「任务用时轴没有 Haiku」

`claude-haiku-5.5` 的 77 条映射配置全部没有 `response_time_seconds`（榜单未发布响应时间/速度档），因此「任务用时」轴上没有任何一行可画；切回「真实单价」轴即可看到（该轴不需要榜分数）。

## 5. 自查命令

```sh
.venv/bin/python scripts/visibility_matrix.py --model claude-haiku-5.5
.venv/bin/python scripts/visibility_matrix.py            # 全量矩阵
.venv/bin/python scripts/visibility_matrix.py --write-docs  # 重新生成本文件
```

不用脚本时，可直接查 join 后的配置：

```sh
python3 -c "import json;d=json.load(open('web/public/data/site.json'));c={x['configuration_id']:x for x in d['configurations']};print(sorted({c[m['configuration_id']]['board'] for m in d['mappings'] if c[m['configuration_id']]['model']=='claude-haiku-5.5' and c[m['configuration_id']]['score'] is not None}))"
```

（当前快照：82 个模型 / 8 个榜 / 2154 条映射；其中 13 个模型在所有榜均无分。）
