# -*- coding: utf-8 -*-
"""
表格十字高亮（点一格亮整行整列）与「默认按操作分排序」。

这两条都是"看得见才算做到"的功能，而单元测试看不见颜色。所以这里测的是**能让它
失效的三件事**：

1. CSS 文件根本没被 Dash 当资源发出去（放错目录、Dash 的 assets_folder 不是这里）；
2. Dash 升级后单元格的 class 改名了（`cell--selected` / `column-N`），
   CSS 还在但一条都匹配不上 —— 这种失效最难发现，页面不报错、只是"点了没反应"；
3. 表格列数超过 CSS 里写死的列上限，右边几列点了不亮。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path('app/web/assets/ft-table.css')
MAX_COL = 60          # CSS 里写到 column-59


@pytest.fixture(scope='module')
def app():
    from app.web import app as wa
    return wa.get_app()


def test_css_lives_in_the_folder_dash_actually_scans(app):
    """
    `Dash(__name__)` 的 assets 目录是**该模块所在包的目录**（app/web/assets）。
    放到仓库根下的 assets/ 看起来一样合理，但 Dash 不会去扫，页面上没有任何提示。
    """
    folder = Path(app.config.assets_folder)
    assert folder.name == 'assets'
    assert folder.parent.name == 'web'
    assert (folder / CSS.name).is_file()


def test_css_is_served_over_http(app):
    """真发一次请求：文件在磁盘上 ≠ 浏览器拿得到（Dash 启动时才扫一次目录）。"""
    client = app.server.test_client()
    page = client.get('/').get_data(as_text=True)
    hrefs = re.findall(r'href="([^"]*ft-table[^"]*)"', page)
    assert hrefs, '首页 HTML 里没有 ft-table.css 的引用'
    res = client.get(hrefs[0])
    assert res.status_code == 200
    assert b'cell--selected' in res.get_data()


def test_class_names_still_match_what_dash_emits():
    """
    class 名是从 dash 的 bundle 里读出来的事实，不是约定 —— 升级 dash 后如果
    `cell--selected` 或 `column-` 变了名，这里先红，而不是等你在页面上点半天。
    """
    import dash
    js = (Path(dash.__file__).parent / 'dash_table' / 'async-table.js')
    src = js.read_text(encoding='utf-8', errors='replace')
    assert 'cell--selected' in src
    assert '" column-"' in src or ' column-' in src
    assert 'dash-header' in src


def test_css_covers_row_column_and_header():
    css = CSS.read_text(encoding='utf-8')
    # 整行
    assert 'tr:has(> td.cell--selected) > td.dash-cell' in css
    # 整列 + 表头，且列序号连续覆盖到上限
    for i in range(MAX_COL):
        assert f'td.dash-cell.column-{i}' in css, f'列 {i} 的规则缺了'
        assert f'th.dash-header.column-{i}' in css, f'表头列 {i} 的规则缺了'
    # 用 inset 阴影而不是背景色：背景色要 !important 才盖得住行内样式，
    # 那会把涨跌红绿一起盖掉。（注释里提到这个词不算，所以先把注释剥掉。）
    rules = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    assert 'background-color' not in rules
    assert 'box-shadow: inset' in rules



def test_css_column_limit_covers_the_widest_table():
    """
    最宽的表是自选组内成员（代码/名称/详情 + 5 个收盘日 + 6 个指标列 + 成本/盈亏/
    备注…）。上限太小的话，右边几列点了不亮，而且没人会想到是 CSS 少写了几行。
    """
    from app.web.pages import watchlist as wl
    widest = len(wl.PCT_COLS) + len(wl.AMP_COLS) + 12
    assert widest * 2 <= MAX_COL, f'列上限 {MAX_COL} 对最宽约 {widest} 列的表太紧'


def test_members_table_defaults_to_amp10_desc():
    """默认排序写在 SQL 的 ORDER BY 里（用户 2026-09-01 指定：10 日振幅从高到低）。"""
    src = Path('app/web/pages/watchlist.py').read_text(encoding='utf-8')
    i = src.index('def members_table')
    body = src[i:i + 4000]
    # 同值要有确定的兜底顺序，否则每次刷新同值的行会互换位置
    assert ('ORDER BY i.amp10_avg DESC NULLS LAST, o.value DESC NULLS LAST, '
            'm.ts_code' in body)


# ------------------------------------------------------------ 显示更多行
def test_table_height_shows_all_43_groups():
    """
    用户 2026-08-30：两个框"再拉长一点，直接显示更多的行"。

    2026-08-31 列紧凑后行高由 27px 收到 23px、又去掉了逐列筛选行（抬头 76 → 54），
    43 个组需要的高度从 1249 变成 `54 + 23×43 + 12 = 1055` —— 所以不再写死 1200，
    改成按 `ui` 的行高常量算出"装满 43 行要多少"，再要求 cap 没把它截掉。
    以后再调行高不用改这条断言，但把 cap 调到看不全 43 个组时仍然会红。
    """
    from app.web import ui
    from app.web.pages import watchlist as wl
    need = ui.HEAD_PX + ui.ROW_PX * 43 + ui.TAIL_PX
    assert need == 1055                   # 行高常量被人改动时先在这里暴露

    assert wl._fit_height(43) == need     # cap 1240 仍有余量，没被截
    # 少量行时仍然按行数收缩，不留一大片空白（★重点关注只有两三只票）
    assert wl._fit_height(2) < 200


def test_watchlist_pool_card_shows_a_hundred_rows_per_page():
    """
    自选池明细的「一页几行」和「多高」在 `config.page_layout` 里，不在代码里。
    这里同时卡库和 seed：只改库不改 seed，下次重建就退回 30 行。
    """
    from app.db import engine
    row = engine.query_one(
        'SELECT height, options FROM config.page_layout '
        ' WHERE page_key = %s AND card_ref = %s', ('watchlist', 'q_watchlist'))
    assert row, 'watchlist 页没有 q_watchlist 这张卡'
    assert int(row['options'].get('page_size', 0)) >= 100
    assert int(row['height']) >= 1000
    seed = Path('app/db/sql/05_seed.sql').read_text(encoding='utf-8')
    assert "'q_watchlist', '自选池明细', '{\"page_size\":100}'" in seed


def test_watchlist_pool_is_sorted_by_amp10_across_all_groups():
    """
    以前是先按组（g.seq, g.name）再排，"全池最该看的十只"要在 43 个组之间翻。
    现在全表按 10 日振幅降序（2026-09-01 用户指定，和右栏组内成员表同键）——
    这里直接查数据核，不只看 SQL 文本。
    """
    from app.web import queries
    rows = queries.q_watchlist()
    if not rows:
        pytest.skip('自选池是空的')
    vals = [r['amp10_avg'] for r in rows]
    seen_null = False
    prev = None
    for v in vals:
        if v is None:
            seen_null = True
            continue
        assert not seen_null, '有振幅的行排在了没振幅的行后面'
        if prev is not None:
            assert float(v) <= float(prev) + 1e-9, f'{v} 排在了 {prev} 后面'
        prev = v

