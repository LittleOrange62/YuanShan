# -*- coding: utf-8 -*-
"""
同花顺本地文件解析。

大部分断言用 `tmp_path` 里造出来的假 ini（GBK 编码，跟真文件一致），
这样测试不依赖本机装没装同花顺；只有几条"实机基线"用真目录，缺了就 skip。

要锁住的是三件事：
- **认不出的代码必须丢弃，不许猜**。同花顺的板块清单里混着板块指数
  （市场号 16/32/88…）、期货品种、`821*` 这种通配写法，猜一个市场后缀
  就会往 `ref.sector_member` 里塞不存在的票，而那张表没有外键能拦住它。
- **`THS_` 与 `TDX_` 两套主键不许互相污染**。板块主键分裂过一次
  （`GN_880501` vs `TDX_880501`），交集为 0、板块热度四个分项全是空值，
  藏了很久没人发现。
- **分组名确实存在于文件里**。上一轮结论"同花顺分组只在客户端内存里"是错的。
"""
from __future__ import annotations

import json

import pytest

from app.datasource import ths_local as t

# ---- 假的 block_*.ini：一个"概念"根 + 一个"香港概念"根（必须整个跳过）----
BLOCK_OK = """[ConfigInfo]
ConfigVer=20260830.095501|abcdef
[BLOCK_NAME_MAP_TABLE]
A001=概念
C024=BC电池
[SUBDIVISION_BLOCK_NAME_MAP_TABLE]
C099=固态电池细分
[BLOCK_STOCK_CONTEXT]
A001=17:600519,
C024=17:600519,33:000001,-105:430047,
[SUBDIVISION_BLOCK_STOCK_CONTEXT]
C099=17:600519,16:880501,88:880502,17:821000,17:ABCDEF,
"""

BLOCK_HK = """[ConfigInfo]
ConfigVer=20260830.095501|abcdef
[BLOCK_NAME_MAP_TABLE]
B001=香港概念
B002=港股高息股
[BLOCK_STOCK_CONTEXT]
B002=177:00700,
"""

INDUSTRY = """[industry]
881101=600519,000001,430047,999999,12345,
VI01=IF2609,
[name]
881101=白酒;
"""

STOCKBLOCK = """[BLOCK_NAME_MAP_TABLE]
0x01=重点关注
0x02=打板池
0x03=空组
[BLOCK_STOCK_CONTEXT]
0x01=17:600519,33:000001,
0x02=17:600519,16:880501,
"""

SELF_INFO = [
    {'M': '17', 'C': '600519', 'P': '1428.50', 'T': '20260817'},
    {'M': '33', 'C': '000001', 'P': '', 'T': ''},
    {'M': '16', 'C': '880501', 'P': '1.0', 'T': '20260817'},
]


@pytest.fixture
def fake(tmp_path):
    """造一份最小可用的同花顺目录。写 GBK —— 真文件就是 GBK。"""
    d = tmp_path / 'BlockUpdate'
    d.mkdir()
    (d / 'block_A001.ini').write_bytes(BLOCK_OK.encode('gbk'))
    (d / 'block_B001.ini').write_bytes(BLOCK_HK.encode('gbk'))
    (tmp_path / 'industry.ini').write_bytes(INDUSTRY.encode('gbk'))
    u = tmp_path / '某个用户'
    u.mkdir()
    (u / 'stockblock.ini').write_bytes(STOCKBLOCK.encode('gbk'))
    (u / 'SelfStockInfo.json').write_bytes(
        json.dumps(SELF_INFO, ensure_ascii=False).encode('utf-8'))
    return tmp_path


# ------------------------------------------------------------ 市场前缀
def test_market_prefix_maps_to_ts_code():
    assert t.to_ts_code('17', '600519') == '600519.SH'
    assert t.to_ts_code('22', '688981') == '688981.SH'
    assert t.to_ts_code('33', '000001') == '000001.SZ'
    assert t.to_ts_code('-105', '430047') == '430047.BJ'


def test_unknown_code_is_dropped_not_guessed():
    """认不出就返回 None。**不许**兜一个默认后缀。"""
    assert t.to_ts_code('16', '880501') is None      # 板块指数，不是个股
    assert t.to_ts_code('99', '600519') is None      # 没见过的市场号
    assert t.to_ts_code('17', '821000') is None      # 通配写法，不是 6 位
    assert t.to_ts_code('17', 'ABCDEF') is None      # 外盘字母代码
    assert t._bare_to_ts('999999') is None           # 不属于 A 股任何代码段


# ------------------------------------------------------------ 板块
def test_blocks_read_only_wanted_roots(fake):
    r = t.read_blocks(fake)
    codes = {s['code'] for s in r['sectors']}
    # 香港概念那个文件整个跳过：里面一个板块都不许进来
    assert codes == {'THS_C024', 'THS_C099'}
    assert r['files'] == 1
    assert r['config_ver'] == '20260830.095501'
    # 根节点自己（A001=概念）不是板块，不入库
    assert 'THS_A001' not in codes


def test_block_members_drop_non_stocks_and_keep_level(fake):
    by = {s['code']: s for s in t.read_blocks(fake)['sectors']}
    assert by['THS_C024']['members'] == ['600519.SH', '000001.SZ', '430047.BJ']
    assert by['THS_C024']['level'] == 1
    assert by['THS_C024']['kind'] == '概念'
    # 细分里那 4 个非个股条目全被丢掉，只剩茅台
    assert by['THS_C099']['members'] == ['600519.SH']
    assert by['THS_C099']['level'] == 2


def test_block_kind_reuses_the_existing_sector_type_vocabulary():
    """
    不新造词。`q_score_rank` 挑最热板块时只认 `sector_type IN ('行业','概念')`，
    多一个"特色指数"会让指数悄悄挤进个股的板块归属。
    """
    assert set(t.SECTOR_TYPE.values()) <= {'行业', '概念', '地区', '风格', '指数'}
    assert t.SECTOR_TYPE['地域'] == '地区'
    assert t.SECTOR_TYPE['特色指数'] == '指数'


# ------------------------------------------------------------ 行业
def test_industry_keeps_881_only_and_names_it(fake):
    r = t.read_industry(fake)
    assert [s['code'] for s in r['sectors']] == ['THS_881101']
    s = r['sectors'][0]
    assert s['name'] == '白酒'          # 末尾的分号要去掉
    assert s['kind'] == '行业'
    # 999999 / 12345 认不出，丢弃；IF2609 不是 881* 段，整条跳过
    assert s['members'] == ['600519.SH', '000001.SZ', '430047.BJ']


@pytest.mark.parametrize('what,expect', [('block', 1015), ('industry', 90)])
def test_real_files_baseline(what, expect):
    """实机基线（2026-08-30）：数量掉一大截说明解析或客户端出了问题。"""
    base = t.ths_dir()
    if not base.is_dir():
        pytest.skip(f'本机没有同花顺目录：{base}')
    n = len((t.read_blocks() if what == 'block' else t.read_industry())['sectors'])
    assert n >= expect * 0.8, f'{what} 只解出 {n} 个，基线是 {expect}'


# ------------------------------------------------------------ 自选分组
def test_watchlist_group_names_live_in_the_file(fake):
    """★ 上一轮"分组只存在于客户端内存里"是错的，组名就在 ini 里。"""
    r = t.read_watchlist_groups(fake)
    assert r['user'] == '某个用户'
    assert [g['name'] for g in r['groups']] == ['重点关注', '打板池', '空组']
    assert [g['seq'] for g in r['groups']] == [1, 2, 3]


def test_watchlist_keeps_empty_group_and_drops_non_stocks(fake):
    by = {g['name']: g for g in t.read_watchlist_groups(fake)['groups']}
    assert by['重点关注']['members'] == ['600519.SH', '000001.SZ']
    assert by['打板池']['members'] == ['600519.SH']   # 880501 是板块指数
    # 空组也要保留：否则"同花顺上 43 个组、导进来 41 个"没人说得清少了哪两个
    assert by['空组']['members'] == []


def test_self_stock_info_gives_cost_and_date_but_never_zero(fake):
    q = t.read_watchlist_groups(fake)['quote']
    assert q['600519.SH'] == {'cost': 1428.5, 'added': '20260817'}
    # 空价格给 None 不给 0 —— 成本 0 会让盈亏显示成 +∞%
    assert q['000001.SZ'] == {'cost': None, 'added': None}
    assert '880501.SH' not in q and '880501.SZ' not in q


# ------------------------------------------------------------ 主键不串
def test_ths_and_tdx_prefixes_never_collide(fake):
    sectors = t.read_blocks(fake)['sectors'] + t.read_industry(fake)['sectors']
    assert all(s['code'].startswith('THS_') for s in sectors)
    # 同花顺行业是 881101，通达信也有 881101 —— 靠前缀区分，不许裸码入库
    assert all(not s['code'].startswith('TDX_') for s in sectors)
    assert all(s['code'][4:] and not s['code'][4:].startswith('THS_')
               for s in sectors)
