# -*- coding: utf-8 -*-
"""「个股深度复盘」的全指标总账 —— 一屏有哪几块、每块的数出自哪张表、每个字段说什么话。

用户 2026-10-16 第 ④ 条：「把所有的指标再汇总一遍，个股深度研究中要体现全，
并且把逻辑和各种指标的含义都说清楚。」

这一层的纪律（它存在的唯一理由是"汇总"，不是"再写一遍"）：
1. **字段义一律现场取各模块的那一份 `DEFINITIONS`**，不在这个文件里重抄。
   重抄＝第二份真相，改一处忘另一处就永远对不上（本项目在涨停价、步数、口径句上都犯过）。
   所以这里的 `fields` 是 `import` 进来的活对象，不是字面量。
2. **没有独立 definitions 的块就只登记出处**，并明写"字段义在该接口自己的 payload 里"，
   不硬凑一张看起来很全的表。凑出来的总数比缺口更坏。
3. 每块都交 `source`（哪张表 / 哪个模块）与 `endpoint`，回答的是"这个数怎么做出来的"；
   三态（有数 / 判不了 / 库里没这一列）由调用方现读那块接口，不在这张表里假装知道。
"""
from __future__ import annotations

from typing import Any, Dict, List

#: 页面上的块序（与 `StockReview.tsx` 的挂载顺序一致；测试钉住两边不许分叉）
BLOCKS: List[Dict[str, Any]] = [
    {'key': 'facts', 'title': '个股信息', 'endpoint': '/api/stock/facts',
     'owner': 'duanxian/stock_facts.py',
     'source': 'reference_data.security（主档）× market_data.stock_daily（最近一场行情）'
               '× market_data.stock_snapshot（即时估值）× reference_data.stock_profile（个股档案）'
               '× sector_member × watchlist_member_snapshot',
     'logic': '全部本机库、不出网；每个数各带自己的日期，行情与档案不同日就分开念。'
              '主档没有的两列（上市日/总股本）与快照里全 0 的三列（eps/bps/roe）在这里不出现。',
     'defs_from': 'duanxian/stock_facts.py::DEFINITIONS'},
    {'key': 'dossier', 'title': '这只票的量化档案', 'endpoint': '/api/quant/stock-dossier',
     'owner': 'duanxian/quant.py',
     'source': 'analytics.score / indicator_value / volume_double / startup_volume_profile / '
               'sector_heat_daily / ambush_setup / volume_breakout_signal / confluence_daily',
     'logic': '八分量与象限读的是当日算好落库的那一行；块里每一格自带"出自哪一天"。',
     'defs_from': None},
    {'key': 'trend', 'title': '该支票的趋势量化计算', 'endpoint': '/api/quant/scores',
     'owner': 'duanxian/quant.py + duanxian/trend_view.py',
     'source': 'analytics.score（八分量）与 analytics.indicator_value（均线/斜率族）',
     'logic': '合成公式与哪几项进合分只在那两处；窗口斜率按有评分的那几场一场一排。',
     'defs_from': None},
    {'key': 'financials', 'title': '财务多期与外推', 'endpoint': '/api/stock/fundamentals',
     'owner': 'app/compute/financial_forecast.py（判据）+ duanxian/stock_fundamentals.py（读侧）',
     'source': 'reference_data.stock_financial_period（源＝同花顺，采集器 ingest/jobs/c25_financials_ths.py）',
     'logic': '逐期表只是搬运；三格外推是半衰加权同比 + 季节环比 + 年报 CAGR 加权，'
              '参数（半衰期 2 季 / 增速夹 −60%~+150% / 分歧 25% / 情景带近 5 份）写死在判据文件一处。',
     'defs_from': 'duanxian/stock_fundamentals.py::DEFINITIONS'},
    {'key': 'pv', 'title': '量价走势（日 / 周 / 月三块）', 'endpoint': '/api/stock/price-volume-trend',
     'owner': 'duanxian/price_volume_trend.py',
     'source': 'market_data.stock_daily（唯一一张日线表，周线与月线由它聚合；不另找源、不建表）',
     'logic': '开=本期首根开、高=本期最高、低=本期最低、收=末根收，量/额逐根相加；价格一律乘复权因子'
              '（最新一场为 1 ⇒ 前复权形状），区间涨跌与上一期复权收比；均线是复权收的简单移动平均，'
              '整段算完再截断。最后一根标 partial（本期未走完），不足 6 根的那一块不画线。',
     'defs_from': 'duanxian/price_volume_trend.py::DEFINITIONS'},
    {'key': 'similarity', 'title': '近 30 根量价形状与历史最相近样本',
     'endpoint': '/api/stock/volume-similarity',
     'owner': 'duanxian/volume_similarity.py',
     'source': 'analytics.volume_ratio_daily（vr-1 逐日倍数）× analytics.startup_volume_profile（sv-2 样本库）'
               '× market_data.stock_daily（窗口涨跌幅与启动后走势）× analytics.market_sentiment_daily（情绪）',
     'logic': '六个分量按权重合成相近分（形状 35% / 强度 20% / 节奏 15% / 量价配合 15% / 情绪 10% / 可比性 5%），'
              '缺的那一维从权重里剔掉再归一；硬过滤是根数≥20、无长停牌跳空、同板块档、排除自身重叠窗口。',
     'defs_from': 'duanxian/volume_similarity.py::DEFINITIONS'},
    {'key': 'research', 'title': '个股研究（公开资料）', 'endpoint': '/api/stock-data 那一组只读接口',
     'owner': 'duanxian/tools.py + akshare 各源',
     'source': '公开源即时抓取（行情/估值/研报/新闻/财务/公告/资金面/龙虎榜/解禁/互动易）',
     'logic': '这一块要出网，字段义在各子接口自己的 definitions 里，本表只登记"它是公开源、可能整块不可用"，'
              '不与本机库的读数混成一份总账。',
     'defs_from': None},
    {'key': 'debate', 'title': '多空辩论 / 深度研究', 'endpoint': '/api/agent/deepdive 等',
     'owner': 'review_agent + duanxian/tools.py',
     'source': 'AI 来源现场生成（要出网、要计费），不产结构化指标',
     'logic': '这里没有"指标"可汇，只有一次生成的正文与引用核对结果；所以本表给它的位置只有一句出处说明。',
     'defs_from': None},
]


def _live_definitions(key: str) -> Dict[str, str]:
    """现场取那一份 definitions（不复制、不改写）。取不到就交空表并说明去哪看。"""
    if key == 'facts':
        from . import stock_facts as M
    elif key == 'financials':
        from . import stock_fundamentals as M
    elif key == 'pv':
        from . import price_volume_trend as M
    elif key == 'similarity':
        from . import volume_similarity as M
    else:
        return {}
    return dict(getattr(M, 'DEFINITIONS', {}) or {})


def _live_caliber(key: str) -> str | None:
    if key == 'facts':
        from . import stock_facts as M
    elif key == 'financials':
        from . import stock_fundamentals as M
    elif key == 'pv':
        from . import price_volume_trend as M
    else:
        from . import volume_similarity as M
    return getattr(M, 'CALIBER', None)


def ledger(code: str = '') -> Dict[str, Any]:
    """每一块：出处、逻辑、字段清单（活的）、字段条数。`code` 只用来回一句"这一屏现在选的是哪一只"。"""
    blocks: List[Dict[str, Any]] = []
    total = 0
    for b in BLOCKS:
        defs = _live_definitions(b['key'])
        total += len(defs)
        row = dict(b)
        row['fields'] = [{'name': k, 'says': v} for k, v in defs.items()]
        row['field_count'] = len(defs)
        row['caliber'] = _live_caliber(b['key']) if defs else None
        if not defs:
            row['fields_note'] = ('这一块没有独立的 definitions 字典：字段义在它自己接口的 payload 里，'
                                  '本表不重抄一份（重抄＝第二份真相，迟早对不上）。')
        blocks.append(row)
    return {
        'available': True, 'spec': 'ledger-1', 'code': (code or '').strip().upper() or None,
        'page': '/stock-review', 'blocks': blocks, 'field_total': total,
        'how_to_read': (
            '这张表回答三件事：这一屏有哪几块、每块的数出自哪张表或哪个模块、每个字段在说什么话。'
            '"出处"列说的是写入方，不是页面 —— 判据都不在前端。'
            '字段清单是现场从各模块的定义字典里取的活对象，改那边这里就跟着变，不存在第二份措辞。'
            '某一格现在是"有数／判不了／库里没这一列"，要回到那一块本身上屏的话去看，'
            '这张总账不替它下结论。'),
        'caveats': [
            {'code': 'not_a_second_copy', 'text':
             '没有独立 definitions 字典的块（量化档案 / 趋势量化 / 个股研究 / 多空辩论）'
             '在这里只登记出处与逻辑，字段义留在各接口自己的 payload 里 —— 本表不复制第二份。'},
            {'code': 'three_state', 'text':
             '"这一格此刻是有数、判不了、还是库里根本没这一列"由那一块自己上屏；'
             '这张总账只说它出自哪里、怎么算，不替读数下结论。'},
        ],
    }
