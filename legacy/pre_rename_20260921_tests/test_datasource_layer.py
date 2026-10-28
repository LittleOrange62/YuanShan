# -*- coding: utf-8 -*-
"""
数据源的层级口径（2026-08-30 用户给的那张表）+ 管理表能不能写。

为什么要锁住：`layer` 是**口径**，不是探测出来的状态。一旦有人图省事把它跟
`health` 混用（"连不上就降级到辅助层"），"该先用谁"这条信息就没了 ——
而这正是这张表存在的唯一理由。所以这里按结果断言：
P0 只能是通达信本地，主力/基座/辅助各自是谁。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
import pytest

from app.datasource import registry as ds_reg
from app.db import engine
from app.web.pages import datasource as ds_page

# 用户 2026-08-30 给的口径。改这张表要先改需求，不是先改测试。
EXPECT_LAYER = {
    'tdx_vipdoc': 'p0',
    # 2026-08-30 追加：同花顺本地 ini 也是 P0（不出网、每天刷新）。
    # 它不跟 vipdoc 抢日线，抢的是板块成分股与自选分组。
    'ths_local_block': 'p0', 'ths_local_industry': 'p0',
    'ths_local_watchlist': 'p0',
    # 通达信 TQ 本地 JSON-RPC：kind 是 http，但打的是 127.0.0.1，同样不出网。
    'tq_http': 'p0',
    # 2026-09-01 追加：通达信插件落的收盘快照库（本机 SQLite）。也是 P0，
    # 但角色跟 vipdoc 不重叠 —— 它是市值与估值的唯一本地来源。
    'tdx_snapshot_db': 'p0',

    'ak_pkg': 'main', 'em_push2': 'main', 'miaoxiang': 'main',
    'baostock_pkg': 'base',
    'ts_pkg': 'assist', 'tushare_mcp': 'assist',
}


def _walk(node):
    yield node
    ch = getattr(node, 'children', None)
    if isinstance(ch, (list, tuple)):
        for c in ch:
            yield from _walk(c)
    elif ch is not None:
        yield from _walk(ch)


def test_p0_is_local_files_only():
    """
    P0 = **不出网就能拿到的数据**：通达信 vipdoc 文件 + 同花顺本地 ini +
    通达信 TQ 的本地 JSON-RPC（`http://127.0.0.1:17709/`）。

    判据不是 `kind='local'` 而是"**不出网**"：TQ 接口的 kind 是 http，但它打的是
    本机回环口，靠的是本机开着的通达信客户端，和联网源在"会不会因为断网/限频/
    接口改版而挂"这件事上不是一类东西。所以这条断言允许 http，但要求 endpoint
    必须是 127.0.0.1/localhost —— 谁把一个真·联网源塞进 P0，这条还是会红。
    """
    rows = engine.query(
        'SELECT ds_key, kind, endpoint FROM config.datasource '
        " WHERE layer = 'p0' ORDER BY ds_key")
    assert sorted(r['ds_key'] for r in rows) == sorted(
        k for k, v in EXPECT_LAYER.items() if v == 'p0')
    for r in rows:
        if r['kind'] == 'local':
            continue
        assert r['kind'] == 'http' and (
            '127.0.0.1' in (r['endpoint'] or '')
            or 'localhost' in (r['endpoint'] or '')), \
            f'P0 里混进了要出网的源：{r}'


def test_every_source_declares_a_known_layer_and_a_duty():
    bad = engine.query(
        'SELECT ds_key, layer, ds_role FROM config.datasource '
        ' WHERE layer <> ALL(%s) OR ds_role IS NULL', (list(ds_reg.LAYERS),))
    assert not bad, f'层级取值非法或没写职责：{bad}'


def test_user_given_layers_are_seeded():
    have = {r['ds_key']: r['layer'] for r in engine.query(
        'SELECT ds_key, layer FROM config.datasource WHERE ds_key = ANY(%s)',
        (list(EXPECT_LAYER),))}
    assert have == EXPECT_LAYER


def test_list_sources_orders_by_layer_then_seq():
    rows = ds_reg.list_sources(active_only=False)
    ranks = [ds_reg.LAYER_RANK[r['layer']] for r in rows]
    assert ranks == sorted(ranks), '清单没有按层级优先级排 —— 顺序就是取数顺序'
    assert rows[0]['layer'] == 'p0'


def test_layer_is_independent_of_health():
    """没权限 / 连不上都不改层级：状态坏了不等于角色变了。"""
    for key in ('ts_pkg', 'ts_moneyflow', 'em_push2'):
        r = ds_reg.get(key)
        assert r['layer'] == EXPECT_LAYER.get(key, r['layer'])
        assert r['ds_role'], f'{key} 没写职责'


# ------------------------------------------------------------------ 写回
def test_update_field_rejects_columns_outside_the_whitelist():
    # health 是探测的结论，endpoint/probe_spec/auth_ref 属于改代码
    for col in ('health', 'endpoint', 'probe_spec', 'auth_ref', 'ds_key'):
        with pytest.raises(ValueError):
            ds_reg.update_field('alphafeed', col, 'x')


def test_update_field_rejects_bad_layer_and_bad_int():
    with pytest.raises(ValueError):
        ds_reg.update_field('alphafeed', 'layer', '主力层')   # 要 key 不要中文
    with pytest.raises(ValueError):
        ds_reg.update_field('alphafeed', 'seq', '第一')


def test_update_field_writes_and_empty_means_null():
    old = ds_reg.get('alphafeed')
    try:
        ds_reg.update_field('alphafeed', 'tagline', '测试值')
        assert ds_reg.get('alphafeed')['tagline'] == '测试值'
        ds_reg.update_field('alphafeed', 'tagline', '')
        assert ds_reg.get('alphafeed')['tagline'] is None
        ds_reg.update_field('alphafeed', 'is_active', '否')
        assert ds_reg.get('alphafeed')['is_active'] is False
    finally:
        ds_reg.update_field('alphafeed', 'tagline', old['tagline'])
        ds_reg.update_field('alphafeed', 'is_active',
                            '是' if old['is_active'] else '否')


# ------------------------------------------------------------------ 页面
def test_page_has_both_tabs():
    # 只认 dbc.Tab 的 label —— dbc.Checkbox 也有 label，按属性名抓会把
    # 「连已知缺口一起重探」这个勾选框一起数进来。
    labels = [n.label for n in _walk(ds_page.render())
              if isinstance(n, dbc.Tab)]
    assert labels == ['① 监控与采集', '② 数据源管理']


def test_manage_table_is_editable_and_lists_disabled_sources_too():
    # 模式匹配 id 是 dict（不可哈希），所以只收字符串 id
    ids = {n.id for n in _walk(ds_page.render())
           if isinstance(getattr(n, 'id', None), str)}
    assert 'ds-mgr' in ids, '管理表不在页面上'
    # 停用的源也要列出来，否则停掉之后再也开不回来
    n_all = engine.scalar('SELECT count(*) FROM config.datasource')
    assert len(ds_page._mgr_rows()) == n_all
    # 表格里每个可编辑的中文列都要能映射到一个允许写的库列
    for col, column in ds_page.MGR_COLS.items():
        assert column in ds_reg.EDITABLE, f'{col} → {column} 不在写白名单里'
