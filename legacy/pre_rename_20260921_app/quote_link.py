# -*- coding: utf-8 -*-
"""
行情外链：把库里的 `ts_code` 换成东方财富行情页的地址。

**公开可调用**，不依赖 dash、不碰数据库，任何脚本都能 `from app.quote_link
import em_url` 直接用：

    >>> em_url('600367.SH')
    'https://quote.eastmoney.com/sh600367.html'
    >>> em_url('sh600367') == em_url('600367') == em_url('600367.SH')
    True
    >>> em_url('880501.SH') is None          # 板块没有个股行情页
    True

东财的路径规则不是一条，是四条 —— 2026-08-29 逐个真实请求核过（看返回的
`<title>`，不是猜的）：

    沪深个股 / ETF   sh600367 / sz000001 / sz159915 / sh510300   200
    指数             zs000001（上证指数）/ zs399001（深证成指）   200
                     `sh000001` 也能打开同一页，但指数统一用 `zs` 前缀
    北交所           **bj/920000**（带斜杠）                      200
                     `bj920000`、`BJ920000` 都是 404 —— 这是最容易写错的一条
    板块 / 伪代码     没有对应页面，返回 None，调用方按"不加链接"处理

品种判定复用 `app/ingest/instrument.py` 的 `classify()`，不在这里另立一套代码段
规则 —— 那种"两处各判一遍"的写法迟早会互相走偏（`000001` 在 SH 是指数、在 SZ
是个股，只有一处判定才不会错）。
"""
from __future__ import annotations

import re

from app.ingest import instrument as ins

EM_TEMPLATE = 'https://quote.eastmoney.com/{path}.html'

# 只有这些品种有个股式行情页
LINKABLE_KINDS = ('stock', 'fund', 'bond', 'bshare', 'index')

_TS = re.compile(r'^(\d{6})\.(SH|SZ|BJ)$', re.I)          # 600367.SH
_PREFIXED = re.compile(r'^(SH|SZ|BJ)(\d{6})$', re.I)      # sh600367
_BARE = re.compile(r'^(\d{6})$')                          # 600367

# 裸 6 位码猜市场。只在没带后缀时用；库里的数据一律带后缀，所以这条只服务
# 手输入的场景。
_BARE_MARKET = ((('6', '9', '5'), 'SH'),
                (('0', '1', '2', '3'), 'SZ'),
                (('4', '8'), 'BJ'))


def normalize(code: str | None) -> tuple[str, str] | None:
    """`'sh600367'` / `'600367.SH'` / `'600367'` → `('600367', 'SH')`。"""
    s = str(code or '').strip()
    if not s:
        return None
    m = _TS.match(s)
    if m:
        return m.group(1), m.group(2).upper()
    m = _PREFIXED.match(s)
    if m:
        return m.group(2), m.group(1).upper()
    m = _BARE.match(s)
    if m:
        for heads, market in _BARE_MARKET:
            if s.startswith(heads):
                return s, market
    return None


def em_path(code: str | None, kind: str | None = None) -> str | None:
    """东财行情页的路径段（`sh600367` / `zs000001` / `bj/920000`）。"""
    parts = normalize(code)
    if parts is None:
        return None
    symbol, market = parts
    kind = kind or ins.classify(f'{symbol}.{market}')
    if kind not in LINKABLE_KINDS:
        return None
    if market == 'BJ':
        return f'bj/{symbol}'
    if kind == 'index':
        return f'zs{symbol}'
    return f'{market.lower()}{symbol}'


def em_url(code: str | None, kind: str | None = None) -> str | None:
    """行情页完整地址；没有对应页面（板块、伪代码、认不出的串）时返回 None。"""
    path = em_path(code, kind)
    return EM_TEMPLATE.format(path=path) if path else None


def em_markdown(code: str | None, text: str | None = None,
                kind: str | None = None) -> str:
    """
    Markdown 链接，给 `dash_table` 的 `presentation='markdown'` 用。

    没有行情页时**原样返回文字**，不返回空 —— 表格里少一个链接可以接受，
    少一个代码不行。
    """
    label = str(text if text not in (None, '') else (code or ''))
    url = em_url(code, kind)
    if not url:
        return label
    # 竖线会把 Markdown 表格切开，中括号会把链接语法搞乱，出现就退回纯文本
    if any(ch in label for ch in '|[]()'):
        return label
    return f'[{label}]({url})'


def detail_link(code: str | None, text: str = '看详情') -> str:
    """
    站内深链：`/stock?code=600519.SH`。给 `dash_table` 的 markdown 单元格用。

    和 `em_markdown` 的分工：那个是**去东财看行情**，这个是**留在本站看评分
    归因**。名单页两个都要 —— 手上有一只票时，"它现在什么分、为什么这个分"
    只有本站答得上来。

    放在这个模块而不是 `app/web/ui.py`：`queries.py` 也要造这个链接，而
    `ui.py` 反过来会 import `queries`，写在 ui 里就成了循环 import。
    """
    s = str(code or '').strip()
    return f'[{text}](/stock?code={s})' if s else ''

