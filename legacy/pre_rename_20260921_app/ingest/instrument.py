# -*- coding: utf-8 -*-
"""
品种归类：给定代码，判断它是个股、指数、板块、债、基金、B股还是伪代码。

这套判据原来写在 scripts/migrate/b3_daily.py 里，vipdoc 解析也要用同一套，
所以提出来放在这里。**不要在两处各留一份** —— 归类规则一旦不一致，
同一只 510010 会在旧库迁移里进基金表、在 vipdoc 摄取里进个股表。

判据顺序：**代码前缀优先，外部给的 asset_type 只当兜底。**
b3 第一版反了，结果 204001.SH（1 天期国债逆回购）在旧库里被标成 fund，
被当 ETF 放进个股表，单位触发器算出均价 9999.9998 远超价格区间
[191, 249.5]，整批回滚。交易所代码段是硬事实，别人库里的字段是别人的推断。
"""
from __future__ import annotations

from app import config

# 固收类。10xxxx 国债/企业债(SH)  11xxxx 可转债(SH)  12xxxx 可转债(SZ)
# 13xxxx 可交换债/逆回购(SZ)  19xxxx 债券(SZ)  20xxxx 国债逆回购(SH)
# 债券按「张」计量、报价是每百元面值；逆回购报的是年化利率不是价格。
BOND_PREFIX = ('10', '11', '12', '13', '19', '20')

# 基金类（ETF/LOF/封闭式）。计量单位是「份」不是「股」，各品种面值也不同。
# 5xxxxx(SH) ETF/LOF/基金   15/16/18xxxx(SZ) ETF/LOF/封闭式
FUND_PREFIX = ('50', '51', '52', '53', '55', '56', '58', '15', '16', '18')

# B 股以美元(SH)/港币(SZ)计价，amount 不是人民币元。
B_SHARE_PREFIX = ('900', '200')

# 通达信内部伪代码（999997 A股指数 等），不是可交易标的。
TDX_PSEUDO_PREFIX = ('999',)

# 个股代码段。北交所除历史的 43/83/87/88 外，新股用 920xxx。
# SZ 的 302 是创业板 2024 年新增段（302132.SZ 中航成飞），漏掉它会让
# 该票 board 判成 NULL 而拿不到 20% 的涨跌停档。
STOCK_PREFIX = {
    'SH': ('600', '601', '603', '605', '688', '689'),
    'SZ': ('000', '001', '002', '003', '300', '301', '302'),
    'BJ': ('430', '830', '831', '832', '833', '834', '835', '836', '837',
           '838', '839', '870', '871', '872', '873', '874', '875', '876',
           '877', '920'),
}

KINDS = ('stock', 'index', 'sector', 'bond', 'fund', 'bshare', 'pseudo', 'unknown')

# 口径不兼容、明确不进 md.stock_daily 的类别
EXCLUDED = ('bond', 'fund', 'bshare', 'pseudo')


def classify(ts_code: str, asset_types: dict[str, str] | None = None) -> str:
    """返回 stock | index | sector | bond | fund | bshare | pseudo | unknown。"""
    symbol, _, market = ts_code.partition('.')

    if symbol.startswith(config.SECTOR_PREFIX):     # 880 / 881 通达信板块
        return 'sector'
    if symbol.startswith(TDX_PSEUDO_PREFIX):        # 999xxx 内部伪代码
        return 'pseudo'
    if symbol.startswith(B_SHARE_PREFIX):           # B 股，外币计价
        return 'bshare'
    if symbol.startswith(BOND_PREFIX):              # 债券、逆回购
        return 'bond'
    if symbol.startswith(FUND_PREFIX):              # ETF / LOF / 封闭式基金
        return 'fund'
    if symbol.startswith(STOCK_PREFIX.get(market, ())):
        return 'stock'
    if symbol.startswith(config.INDEX_PREFIX.get(market, ())):
        # 000xxx 在 SZ 是个股、在 SH 是指数，所以必须放在个股判断之后按市场分
        return 'index'
    if market == 'BJ' and symbol.startswith(('899', '810')):
        return 'index'                              # 899050 北证50 等

    known = (asset_types or {}).get(ts_code)
    if known in ('stock', 'etf'):
        return 'stock'
    if known in ('index', 'sector'):
        return known
    if known == 'other':
        return 'bond'
    return 'unknown'
