# -*- coding: utf-8 -*-
"""
每日复盘表：复盘首页「个股明细」那张卡的数据源（原 /review 页，2026-08-29 并入首页）。

output_kind 是 `table`，产出一张宽表而不是一列分数，所以评分驱动
compute_date 会跳过它，由编排层在 oper_score 之后单独调度。

**优先用 params['_prior'] 里的内存结果，其次回落到读 fct.score。**
同一次运行里刚算出来的分数还在内存里，再查一遍库只是浪费；
但页面上点"重新生成复盘"时并没有 prior，那时就得从库里读。
两条路径产出的列完全一致，页面不需要知道数据是哪来的。

象限只在 oper_score 上有值（fct.score.quadrant），所以象限一列
只能从库里取或由 oper_score 的分数重算 —— 这里选择从库里取，
因为库里那份是最终落库版本，页面显示的必须和它一致。
"""
from __future__ import annotations

import pandas as pd

from app.db import engine

# 复盘表要带上的功能点。顺序就是页面上的列顺序
CAPS = ('oper_score', 'stock_quality', 'env_resonance', 'sector_heat',
        'market_mood', 'temp_score')

# 「当前有效权重」那张表上这一行的说明。**不是评分脚本**（`output_kind = table`），
# 所以 terms 写"产出什么"，combine 明写不参与加权。
EXPLAIN = {
    'basis': '**不是评分脚本**：把当日行情和六个功能点的分数横过来拼成一张宽表，'
             '给复盘用的逐票明细。',
    'terms': ({'name': '当日行情', 'expr': 'md.stock_daily ⋈ ref.security',
               'norm': '原值', 'note': '收盘 / 涨跌 / 成交额 / 换手 / 涨跌停价'},
              {'name': '六个功能点的分', 'expr': 'fct.score WHERE cap_key = ANY(CAPS)',
               'norm': '原值',
               'note': '操作分 / 个股质量 / 环境共振 / 板块热度 / 大盘情绪 / 温度'}),
    'combine': '不参与加权（`output_kind = table`）。由编排层在 oper_score '
               '之后单独调度，权重 1.0 只是占位。',
    'caveat': '象限一列只从库里取（`fct.score.quadrant` 只在 oper_score 上有值）——'
              '页面显示的必须和最终落库那份一致。',
}


BASE_SQL = """
SELECT d.ts_code, s.name, s.board, s.is_st,
       d.close, d.pct_chg, d.amount_yuan, d.turnover_rate_pct,
       d.limit_up, d.limit_down
  FROM md.stock_daily d
  JOIN ref.security s USING (ts_code)
 WHERE d.trade_date = %(d)s AND s.asset_type = 'stock'
"""

SCORE_SQL = """
SELECT ts_code, cap_key, value, rank_in_market, pct_rank, quadrant
  FROM fct.score
 WHERE trade_date = %(d)s AND cap_key = ANY(%(caps)s)
"""


def run(ctx, params: dict) -> pd.DataFrame:
    d = ctx.trade_date
    top_n = int(params.get('top_n') or 100)

    base = pd.DataFrame(engine.query(BASE_SQL, {'d': d}, role='reader'))
    if base.empty:
        return pd.DataFrame()
    for c in ('close', 'pct_chg', 'amount_yuan', 'turnover_rate_pct',
              'limit_up', 'limit_down'):
        base[c] = pd.to_numeric(base[c], errors='coerce')
    base = base.set_index('ts_code')

    # 涨停/跌停标记：用回算出的涨跌停价判定，不用 pct_chg 近似 ——
    # 创业板 20%、北交所 30% 的票用 9.8% 判会全部漏掉
    base['is_limit_up'] = (base['limit_up'] > 0) & (
        base['close'] >= base['limit_up'] - 0.01)
    base['is_limit_down'] = (base['limit_down'] > 0) & (
        base['close'] <= base['limit_down'] + 0.01)
    base['sector'] = ctx.today.get('sector') if 'sector' in ctx.today else None

    rows = engine.query(SCORE_SQL, {'d': d, 'caps': list(CAPS)}, role='reader')
    scored = pd.DataFrame(rows)
    prior = params.get('_prior') or {}

    for cap in CAPS:
        col = None
        if not scored.empty:
            cut = scored[scored['cap_key'] == cap]
            if not cut.empty:
                col = pd.to_numeric(cut.set_index('ts_code')['value'],
                                    errors='coerce')
                if cap == 'oper_score':
                    base['quadrant'] = cut.set_index('ts_code')['quadrant']
                    base['rank'] = pd.to_numeric(
                        cut.set_index('ts_code')['rank_in_market'],
                        errors='coerce').astype('Int64')
        if col is None and cap in prior:
            col = pd.to_numeric(prior[cap], errors='coerce')
        base[cap] = col.reindex(base.index) if col is not None else pd.NA

    if 'quadrant' not in base.columns:
        base['quadrant'] = None
    if 'rank' not in base.columns:
        base['rank'] = pd.NA

    out = base.sort_values('oper_score', ascending=False, na_position='last')
    return out.head(top_n).reset_index() if top_n > 0 else out.reset_index()
