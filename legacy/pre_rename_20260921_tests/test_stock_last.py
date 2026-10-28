# -*- coding: utf-8 -*-
"""
`/stock` 默认打开"上次看过的那只票"。

三件事必须锁住：

1. 不带 `?code=` 时**首屏不画默认票**，只给一句骨架 —— 先画茅台再换掉会闪一下
   一只跟你无关的票；
2. 带 `?code=` 的深链首屏就把正文渲染出来，不走恢复那一路（否则深链会被
   localStorage 里的旧票顶掉）；
3. options 和 value 由**同一个回调**写。Dash 3 里一个 Output 只允许一个回调写，
   而"打字搜"/"按分组选"/"恢复上次"三件事都要动这两个属性 —— 拆开的话
   应用直接起不来，这条用真的 `create_app()` 来验。
"""
from __future__ import annotations

import pytest

from app.web.pages import stock


def _walk(node):
    """
    递归遍历组件树。

    ★不要用 `to_plotly_json()` 再 `json.dumps` 来找东西：那个方法只序列化
    **当前这一层**，children 里的组件会被 `default=str` 变成 `repr` 字符串，
    于是 `'Candlestick' in dump` 这种断言永远是假的（2026-08-29 踩过）。
    """
    yield node
    ch = getattr(node, 'children', None)
    if isinstance(ch, (list, tuple)):
        for c in ch:
            yield from _walk(c)
    elif ch is not None:
        yield from _walk(ch)


def _texts(root) -> str:
    return '\n'.join(n for n in _walk(root) if isinstance(n, str))


def _by_id(root, id_):
    return [n for n in _walk(root) if getattr(n, 'id', None) == id_]


def _trace_types(root) -> list[str]:
    out = []
    for n in _walk(root):
        fig = getattr(n, 'figure', None)
        if fig is not None:
            out += [type(t).__name__ for t in fig.data]
    return out


def test_no_param_render_shows_skeleton_not_a_default_stock():
    page = stock.render()
    assert '正在打开上次看过的那只票' in _texts(page)
    # 骨架里不该已经画好某只票的 K 线
    assert _trace_types(page) == []
    assert _by_id(page, 'stock-last') and _by_id(page, 'stock-init')


def test_no_param_render_leaves_pick_empty():
    """value 留空，等浏览器把 localStorage 里的值报上来再填。"""
    picks = _by_id(stock.render(), 'stock-pick')
    assert len(picks) == 1
    assert picks[0].value is None
    assert picks[0].options == []


def test_deeplink_renders_the_body_at_first_paint():
    page = stock.render({'code': '600519.SH'})
    assert 'Candlestick' in _trace_types(page)
    assert '正在打开上次看过的那只票' not in _texts(page)
    assert _by_id(page, 'stock-pick')[0].value == '600519.SH'


def test_headline_name_and_code_link_to_eastmoney():
    """
    选中一只票之后，第一个指标块的**名称和代码都要能点到东方财富行情页** ——
    跟表格里代码/名称列的外链同一个口径（`quote_link.em_url`）。
    本页给的是评分归因，看盘口资金还得去行情页。
    """
    from dash import html

    page = stock.render({'code': '600519.SH'})
    links = [n for n in _walk(page) if isinstance(n, html.A)]
    em = [a for a in links if 'quote.eastmoney.com' in (a.href or '')]
    assert em, '页面上没有任何东方财富外链'
    texts = {str(a.children) for a in em}
    assert '贵州茅台' in texts
    assert '600519.SH' in texts
    for a in em:
        assert a.target == '_blank'          # 不顶掉当前复盘页
        # 链接色会盖掉指标块按涨跌染的颜色，所以要 inherit
        assert (a.style or {}).get('color') == 'inherit'


def test_code_option_label_carries_the_name():
    opt = stock._code_option('600519.SH')
    assert opt and opt[0]['value'] == '600519.SH'
    assert '贵州茅台' in opt[0]['label']
    # 拼音要进 search，否则 Dropdown 会在浏览器端把它过滤掉
    assert 'gzmt' in opt[0]['search']


def test_code_option_falls_back_for_unknown_code():
    opt = stock._code_option('999999.XX')
    assert opt == [{'label': '999999.XX', 'value': '999999.XX'}]


def _out_keys(out) -> list[str]:
    """`callback_map[…]['output']` 可能是 Output、Output 列表，也可能是
    `..a.b...c.d..` 这种字符串，三种都要认。"""
    if isinstance(out, str):
        return [p for p in out.strip('.').split('...') if p]
    if isinstance(out, (list, tuple)):
        return [k for o in out for k in _out_keys(o)]
    return [f'{out.component_id}.{out.component_property}']


def test_options_and_value_written_by_one_callback():
    """
    真的建一遍 app：Output 撞车 Dash 会在注册时抛异常，起都起不来。
    顺带核对 `stock-pick` 的 options 与 value 挂在同一个回调上。
    """
    from app.web.app import create_app
    app = create_app()
    owners: dict[str, list[str]] = {}
    for key, spec in app.callback_map.items():
        for out in _out_keys(spec['output']):
            owners.setdefault(out, []).append(key)
    for prop in ('stock-pick.options', 'stock-pick.value'):
        assert len(owners.get(prop, [])) == 1, f'{prop} 被多个回调写'
    assert owners['stock-pick.options'] == owners['stock-pick.value']
    assert len(owners.get('stock-last.data', [])) == 1


@pytest.mark.parametrize('text', ['600519', '贵州茅台', 'gzmt'])
def test_resolve_accepts_code_name_pinyin(text):
    assert stock._resolve(text) == '600519.SH'


def test_intro_says_it_is_not_watchlist_only():
    """
    页顶说明必须写明"不限自选"。

    页上并排放着「搜个股」和「从自选分组里挑」两个下拉，看起来像"只有自选的票
    才有这些图"（用户 2026-08-30 就是这么问的）。实际评分/因子是全市场逐票算的，
    这句话删了误解就会回来。
    """
    txt = _texts(stock.render())
    assert '不限自选' in txt
    assert '全市场' in txt


def test_intro_reports_real_coverage_numbers():
    """说明里的覆盖数字来自库，不是写死的文案。"""
    c = stock._coverage()
    assert c['scored'] > 5000 and c['stocks'] > 5000
    txt = _texts(stock.render())
    assert str(c['scored']) in txt
    assert str(c['stocks']) in txt

