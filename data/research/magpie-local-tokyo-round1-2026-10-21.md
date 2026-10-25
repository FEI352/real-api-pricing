# Local + Tokyo Magpie quota evidence / 双机套餐额度证据

Observation cutoff: **2026-10-21 23:50 UTC+8**. Account identities clarified by the contributor on 2026-10-22. [Machine-readable evidence](magpie-local-tokyo-round1-2026-10-21.json).

**Research-only:** seven medium-grade account-cycle measurements and one high-grade official-terms observation. These are conditional observations for maintainer review, not replacements for the adopted table. All private inputs stay outside Git.

## Qualified measurements / 通过筛选的测量

Monthly allowances below assume saturated use: four weeks, or five weekly pools for Kimi under the existing convention. Except the MiniMax raw exception, token components are converted to reference list-worth and then to the repository workload. Reference prices are pinned to commit `087fd09`; they are not a fresh market-price survey or actual cash spend. Fees come from the existing contributor account mapping, not invoices.

| Plan / 套餐 | Model | Observed interval (UTC) | Quota drop | Calls | Segments | Monthly tokens (亿) | Reference fee | Saturated reference fee / MTok | Grade |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Kimi Allegro | Kimi K3 | Oct 17 15:31 → Oct 21 15:44 | 84pp | 2,782 | 7 | 32.1921 | CNY 699 | CNY 0.21713 | medium |
| SuperGrok Heavy | Grok 4.7 | Oct 13 16:23 → Oct 16 14:13 | 10pp | 1,755 | 2 | 85.9552 | USD 300 | USD 0.03490 | medium |
| Devin Pro, cycle A | Claude Opus 5.5 | Oct 13 14:59 → Oct 15 12:13 | 82pp | 202 | 14 | 2.8971 | USD 20 | USD 0.06903 | medium |
| Devin Pro, cycle B | Claude Opus 5.5 | Oct 18 08:02 → Oct 20 12:32 | 85pp | 235 | 13 | 2.7659 | USD 20 | USD 0.07231 | medium |
| Devin Max (standard), cycle A | Claude Opus 5.5 | Oct 14 05:00 → Oct 15 10:13 | 22pp | 307 | 4 | 12.8416 | USD 200 | USD 0.15574 | medium |
| Devin Max (standard), cycle B | Claude Opus 5.5 | Oct 18 08:02 → Oct 21 15:46 | 87pp | 1,152 | 17 | 29.2189 | USD 200 | USD 0.06845 | medium |
| MiniMax M Plan Go (CN) | M3.1 Flash Preview | Oct 18 18:10 → Oct 19 13:04 | 20pp | 664 | 4 | 15.4881 raw | CNY 49 | CNY 0.03164 | medium |

The JSON holds exact timestamps, every sample in each selected cycle, all token components, selected/excluded sample flags, endpoint-rounding sensitivity, and recomputable confidence metrics. The table's intervals bound selected samples; excluded mixed-model intervals may occur inside them.

**Interpretation and adoption limits:**

- The contributor confirms the first Devin account is Pro despite the cached `Devin Max` label. The second is a **standard Max account**. Its two cycles differ by about 2.28×; preserve them separately. USD 200 is the old mapping's reference fee, not a newly confirmed payment.
- Kimi and SuperGrok tiers retain the prior contributor mapping; the cached quota responses do not independently verify their tier or fee. Grades describe the account-cycle measurements, not universal plan entitlements.
- The SuperGrok sample is historical, only 10pp and two blocks. The more recent mixed/unstable cycle failed qualification. It is not evidence that the account currently has this capacity.
- MiniMax is raw observed-workload capacity under the existing [2026-10-01 exception](../../CONVENTIONS.md#4-各渠道现行口径). No M3 price is borrowed. The newer `minimax-m3.1-flash` identifier is kept separate from Preview. Annual billing and other tiers are not extrapolated.
- Kimi 32.1921亿 is workload-normalized; the adopted 43.53亿 is raw and derived from another tier. They cannot be compared as a direct capacity decrease. Likewise, different-date Devin Max observations cannot silently replace the existing 66.9亿 standard-Max anchor.
- The statistical rubric is explicit and is not a confidence probability. High requires ≥30pp, ≥200 token-bearing calls, effective blocks ≥4, CV ≤25%, leave-one-out change ≤15%, rounding error/delta ≤10%. Medium requires ≥10pp, ≥30 calls, effective blocks ≥1.5, CV ≤50%, leave-one-out ≤35%, rounding ≤25%. Direct-audit completeness limits all inferred capacities here to medium.
- Gateway and direct-client telemetry do not prove all outside activity is visible. Candidate direct use is an attribution-risk scenario; it is not assigned to a subscription as proven billing.

## GOAT: high-grade observations, different credit scopes

Checked against the [official GOAT page](https://commandcode.ai/docs/plans/goat) and [Usage Limits](https://commandcode.ai/docs/resources/usage-limits) on 2026-10-22:

- The usage-limits tables show USD 10/month, 70 monthly usage credits, a 14-credit five-hour slice and a 35-credit weekly slice.
- The refreshed GOAT page headline says 80 credits; its explanatory section describes **up to 80 of per-model allowances**. The same page's Usage limits section still says 70/14/35. Both observations are recorded; this PR makes no shared-pool change.
- The frozen local cache shows **48.17 / 70.00** and 68.811565488% used at 2026-10-21 23:50:48 UTC+8. This directly supports that account's displayed monthly amount, not a universal cash balance.
- Credits, time-window slices and per-model usage allowances are different quantities and are not additive. No token capacity is fitted from GOAT token/percentage ratios. Old local 10/40 window constants are explicitly rejected as current vendor evidence.

## Coverage and rejected candidates / 覆盖与排除

41,357 local and 5,150 Tokyo gateway records were frozen. The merge removes 207 forwarding mirrors with the same upstream response identity and token vector, retaining 46,300 rows; 46,292 are at or before the cutoff. Matching histories and accounts are treated as one pool, not independent machines.

The local direct audit captured 23,582 pre-cutoff session records; all 48 page totals stayed at 23,619, with later records removed by timestamp. The API does not offer snapshot isolation, so this is an audit of discovered candidates, not proof of completeness. The Tokyo CLI export contains 5,177 gateway rows and no discovered direct-session rows; it was read after the frozen gateway files and is used only for the direct-source audit.

The inventory has **20 anonymous history/cache record handles, not 20 confirmed subscriptions**. Empty versus labelled cache keys can refer to the same login; removed historical accounts are retained for traceability. No subscription count or total monthly bill is inferred from these handles.

The JSON records 43 rejected model-cycle observations without publishing their extrapolated capacity as usable data:

- **Codex Pro 5x:** mixed weights, review model identity, quota refills and possible direct usage prevent a qualified exclusive-capacity result. In the latest Sol interval, candidate direct list-worth is about 2.21× gateway list-worth; that scenario exceeds the 20% downgrade threshold.
- **MiniMax old Explore mapping:** the cached tier is now Build; do not price the old sample as Explore or apply its old fee. Other cycles also fail stability/coverage checks.
- **OpenCode Go / Command Code GOAT:** percentage bars and vendor credit pools are not unknown token-linear weekly allowances. Catalog amounts stay separate from token measurements.
- **Cursor:** separate `Cursor Models` and `Other Models` windows cannot be treated as a weekly pool by this method. No capacity is manufactured from its history.
- **Google AI Pro on Tokyo:** a current plan response and a few requests exist, but no matching weekly history sufficient for this estimator.
- **Other historical Devin accounts, WorkBuddy and unclassified gateways:** no verified tier/fee and/or no eligible token-capacity window. Their presence in an inventory is not a pricing claim.

Source-file SHA-256 hashes remain in the public evidence; original emails, host addresses, credentials, request/session identifiers, prompts and private paths do not. Public aggregates reproduce the arithmetic, not independently authenticate the private account attribution.

## Status / 状态

The existing adopted table, derived prices and charts remain the maintainer's current decisions under [CONVENTIONS.md §3](../../CONVENTIONS.md#3-证据等级).
