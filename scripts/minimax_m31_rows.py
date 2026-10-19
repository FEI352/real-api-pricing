"""MiniMax M3.1 Flash M Plan rows: equal-money conversion from the M3 tier allowance.

2026-10-19 口径变更（用户实测指示 + 官方按量价首次公布）：
  额度不再用 2026-10-01 的截图 raw 差分（Go 15.98 亿 / Explore 47.94 亿 / Build 191.76 亿），
  改为「M3 套餐等额的钱」：官方 M Plan 额度按其按量计费价格扣减
  （pricing/overview 的 MCP 段原文："usage deducts from the included M Plan quota according to
  its pay-as-you-go price"），因此额度本质是美元 worth 池——
    M3 各档绝对 token 额度 × M3 标准负载混合价 = 月 worth
    月 worth ÷ M3.1 混合价 = M3.1 的 token 额度
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

EVIDENCE = 'minimax-m31-flash-preview-cn-round1-2026-10-01.json'
MODEL = 'minimax-m3.1-flash-preview'
D = Decimal

# 2026-10-19 官方首发按量价（platform.minimax.io/docs/pricing/overview，LLM 表 Standard 档）
#   国内站同表 ¥1.00 / ¥4.00 / ¥0.10 per M tokens，按项目汇率折算与国际站整数价一致。
M31_LIST = {'cached': D('0.015'), 'input': D('0.15'), 'output': D('0.60')}
# 同页 MiniMax-M3 ≤512k Standard 档（既有 minimax-m3 档所用的同一来源）
M3_LIST = {'cached': D('0.06'), 'input': D('0.30'), 'output': D('1.20')}
# M3 在 M Plan 各档的官方绝对 token 额度（亿）：Go 6.0 / Explore 18.0 来自官网 Token Plan
# 表（49 / 119 CNY = 6.0 / 18.0 亿，迁移通知称「价格与文本用量不变」）；Build 取 M Plan FAQ
# 的 1x/3x/12x → 12 × Go = 72.0（旧 Ultra 迁移后 71.0 记为冲突，不采用）。
M3_PLAN_YI = {'Go': D('6.0'), 'Explore': D('18.0'), 'Build': D('72.0')}
# 缓存占比：M3.1 实测全口径 cache 95.18% 低于 96% 阈值 → 按 2026-10-19 指示手动取 96%；
# 非 cache 余量 4% 中输出沿用标准档 0.5%、其余归普通输入。实测回升到 ≥96% 时改回实测 mix。
M31_MIX = {'cache': D('0.96'), 'input': D('0.035'), 'output': D('0.005')}
STD_MIX = {'cache': D('0.97'), 'input': D('0.025'), 'output': D('0.005')}


def blended(rates: dict[str, D], mix: dict[str, D]) -> D:
    return (mix['cache'] * rates['cached'] + mix['input'] * rates['input']
            + mix['output'] * rates['output'])


M3_BLEND = blended(M3_LIST, STD_MIX)
M31_BLEND = blended(M31_LIST, M31_MIX)
RATIO = M3_BLEND / M31_BLEND


def subscription_specs(conventions: dict[str, Any]) -> list[tuple]:
    """Return existing build_adopted.sub_row positional arguments.

    Annual subscriptions use upfront annual CNY / 12 as their comparison
    monthly fee, without rounding it to cents before calculating unit prices.
    sub_row remains responsible for the repository's allowance precision.
    """
    root = Path(__file__).resolve().parent.parent
    evidence = json.loads((root / 'data/research' / EVIDENCE).read_text(encoding='utf-8'))
    source = (
        'https://platform.minimax.io/docs/pricing/overview；'
        'https://platform.minimax.cn/docs/pricing/overview；'
        'https://platform.minimax.cn/docs/m-plan/faq；'
        f'{EVIDENCE}'
    )
    common = (
        '额度口径（2026-10-19 变更）：改用「M3 套餐等额的钱」推算，不再用 2026-10-01 截图 raw 差分'
        '（旧值 Go 15.98 亿 / Explore 47.94 亿 / Build 191.76 亿，保留作交叉对照）。'
        '官方 M Plan 额度按其按量计费价格扣减（pricing/overview MCP 段），故额度是美元 worth 池：'
        f'M3 各档官方绝对 token 额度 × M3 标准负载混合价 ${M3_BLEND}/MTok ＝ 月 worth，'
        f'再 ÷ M3.1 混合价 ${M31_BLEND}/MTok（96% 缓存档）＝ 本行 token 额度，'
        f'换算系数 {RATIO:.6f}。'
        'M3.1 Flash 官方按量价 2026-10-19 首次公布：$0.15 输入 / $0.60 输出 / $0.015 缓存读取'
        '（缓存读为输入的 10%，M3 为 20%）；国内站 ¥1.00/¥4.00/¥0.10 同比例。'
        '缓存占比：M3.1 实测全口径 95.1823%/3.4854%/1.3323%，cache 低于 96% 阈值，'
        '按指示手动取 96%（输入 3.5% / 输出 0.5%）；若后续实测回升到 ≥96% 则改回实测 mix。'
        'M3 侧绝对额度 Go 6.0 亿 / Explore 18.0 亿取官网 Token Plan 表（49/119 CNY），'
        'Build 取 M Plan FAQ 的 12×Go＝72.0 亿（旧 Ultra 迁移后 71.0 亿记为冲突，不采用）。'
        f'交叉对照：等额钱口径 Go {M3_PLAN_YI["Go"] * RATIO:.2f} 亿落在截图差分的取整敏感性上沿'
        '13.6974~19.1764 亿/月区间内，两口径方向一致。'
    )
    rows = []
    for plan in evidence['calculations']['prices']:
        name = plan['plan']
        multiplier = plan['usageRelativeToGo']
        base_yi = M3_PLAN_YI[name]
        for annual in (False, True):
            monthly_price = D(plan['annualCny']) / D(12) if annual else D(plan['monthlyCny'])
            pid = f"minimax_m_plan_{name.lower()}_cn" + ('_annual' if annual else '')
            label = f"MiniMax M Plan {name} (CN{' Annual' if annual else ''})"
            note = common
            if name != 'Go':
                note += (f'本档额度＝M3 侧 {base_yi:g} 亿（Go 的 {multiplier}×）× 同系数 '
                         f'{RATIO:.6f}；倍率取自官方 FAQ，非本档独立实测。')
            if annual:
                note += (f"年付¥{plan['annualCny']}，月比较费=年费/12；需整年预付，"
                         '不是按月售价。假设同档年付与月付周池相同，不是一次发放全年token。')
            else:
                note += f"月付常规价¥{plan['monthlyCny']}，由年付实价÷10及官方FAQ还原，不含首月促销。"
            if name == 'Explore':
                note += '截图年付划线价¥1490与119×12=1428不符；保留冲突，不用划线价倒推月付。'
            confidence = 'medium' if name == 'Go' and not annual else 'low'
            rows.append((pid, label, float(monthly_price), 'CNY', MODEL,
                         float(base_yi * RATIO), confidence, source, note, 'full'))
    if len(rows) != 6 or len({r[0] for r in rows}) != 6:
        raise ValueError('Expected exactly six unique plan/billing-cycle rows.')
    return rows
