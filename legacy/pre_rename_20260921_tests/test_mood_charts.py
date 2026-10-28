# -*- coding: utf-8 -*-
"""
复盘首页那两张「大盘温度」趋势图。

短窗口的分数图有一个必须锁住的点：**y 轴钉在 -100~100**。纵轴自适应的话，
同一段 20 分的波动在 7 日图里画成满屏震荡、在 20 日图里画成一条平线，
两张并排看得出的强弱印象是反的 —— 而这两张图就是为了并排比才做的。
"""
from __future__ import annotations

import pytest

from app.db import engine
from app.web import queries, ui

KEYS = ('mood_7d', 'mood_20d')


def _chart(key: str) -> dict:
    row = engine.query_one('SELECT * FROM config.chart WHERE chart_key = %s',
                           (key,))
    assert row, f'config.chart 里没有 {key}'
    return row


@pytest.mark.parametrize('key', KEYS)
def test_chart_is_registered_and_enabled(key):
    c = _chart(key)
    assert c['enabled']
    assert c['data_query'] == 'q_market_mood'
    assert queries.get(c['data_query']) is not None, '命名查询没注册'


@pytest.mark.parametrize(('key', 'n'), (('mood_7d', 7), ('mood_20d', 20)))
def test_window_is_configured_not_hardcoded(key, n):
    """窗口写在 config.chart.params 里 —— 改天数不用改代码。"""
    c = _chart(key)
    assert c['params'].get('lookback') == n
    rows = queries.run(c['data_query'], **c['params'])
    assert len(rows) == n, f'{key} 应该正好 {n} 个交易日'


@pytest.mark.parametrize('key', KEYS)
def test_y_axis_is_pinned_and_zero_line_drawn(key):
    c = _chart(key)
    rows = queries.run(c['data_query'], **c['params'])
    fig = ui.figure(c, rows)
    assert tuple(fig.layout.yaxis.range) == (-100.0, 100.0)
    assert len(fig.layout.shapes) == 1, '缺 0 基准线'


def test_seven_day_is_bars_and_twenty_day_has_the_moving_average():
    """7 日看逐日强弱（柱），20 日看趋势（线 + 5 日均线）。"""
    f7 = ui.figure(_chart('mood_7d'),
                   queries.run('q_market_mood', lookback=7))
    assert [t.type for t in f7.data] == ['bar']
    f20 = ui.figure(_chart('mood_20d'),
                    queries.run('q_market_mood', lookback=20))
    assert [t.type for t in f20.data] == ['scatter', 'scatter']
    assert len(f20.data[1].y) == 20


def test_both_charts_sit_side_by_side_on_the_home_page():
    rows = engine.query(
        """
        SELECT slot, width, card_kind, card_ref FROM config.page_layout
         WHERE page_key = 'home' AND card_ref = ANY(%s) ORDER BY slot
        """, (list(KEYS),))
    assert len(rows) == 2, '首页没放这两张图'
    assert [r['card_ref'] for r in rows] == ['mood_7d', 'mood_20d']
    # 12 栏栅格里各占 6 栏＝一行两张，左右并排
    assert all(r['width'] == 6 and r['card_kind'] == 'chart' for r in rows)
    assert rows[1]['slot'] == rows[0]['slot'] + 1, '中间不能夹别的卡'


def _walk(n):
    yield n
    ch = getattr(n, 'children', None)
    if isinstance(ch, (list, tuple)):
        for c in ch:
            yield from _walk(c)
    elif ch is not None:
        yield from _walk(ch)


def _text(comp):
    return '\n'.join(x for x in _walk(comp) if isinstance(x, str))


def test_card_falls_back_to_the_note_written_on_the_chart():
    """
    口径说明属于**图**：同一张图摆在两个页面上，说明不该抄两遍。
    原来只认 page_layout.options.note 和代码里的 CARD_HELP。

    这里刻意用一个 **CARD_HELP 里没有的 chart_key**（把 mood_7d 的定义换个键塞进
    charts）—— 否则这条测试会随着 CARD_HELP 补条目而失效：2026-08-30 全站补说明时
    mood_7d 有了自己的 CARD_HELP 条目，按优先级它就该盖住图上的 note，
    原来的断言因此变成在测"谁盖住谁"，不是在测兜底。
    """
    chart = engine.query_one(
        "SELECT * FROM config.chart WHERE chart_key = 'mood_7d'")
    assert '涨跌家数比' in ((chart['options'] or {}).get('note') or ''), \
        '这条测试要求图上写了 note'
    key = '_note_fallback_probe'
    assert key not in ui.CARD_HELP
    row = engine.query_one(
        "SELECT * FROM config.page_layout "
        "WHERE page_key = 'home' AND card_ref = 'mood_7d'")
    assert not (row['options'] or {}).get('note'), '这条测试要求布局层没写 note'
    comp = ui.render_card({**row, 'card_ref': key}, {key: chart}, params={})
    assert '涨跌家数比' in _text(comp), '图上的 note 没有渲染出来'


def test_card_help_wins_over_the_note_on_the_chart():
    """优先级：布局层 > CARD_HELP > 图。mood_7d 两边都有，必须是 CARD_HELP 赢。"""
    charts = {c['chart_key']: c for c in engine.query('SELECT * FROM config.chart')}
    row = engine.query_one(
        "SELECT * FROM config.page_layout "
        "WHERE page_key = 'home' AND card_ref = 'mood_7d'")
    txt = _text(ui.render_card(row, charts, params={}))
    assert ui.CARD_HELP['mood_7d'][:20] in txt

