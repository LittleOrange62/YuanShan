# -*- coding: utf-8 -*-
"""
取数限流闸门（`app/datasource/registry.py` 的 `_gate` / `limit_group` / `limit_of`）。

**为什么要有限流，而不是只有重试**：东财是有风控会封 IP 的（a-stock-data 记录的
社区实测阈值：> 5 请求/秒、单 IP 并发 ≥ 10、1 分钟 ≥ 200、5 分钟 ≥ 300 触发封禁）。
只有重试没有节流的后果是：撞上风控 → 重试 → 封禁时间被拉长 → 页面上表现成
"这个源今天一直 down"，而真正的原因是我们自己打太快。

这些测试**不联网**：只验闸门自己的行为（等多久、谁和谁共用配额、到顶了会不会
死等），所以可以放在常规 pytest 里跑。
"""
from __future__ import annotations

import time

import pytest

from app.datasource import registry as reg


@pytest.fixture(autouse=True)
def _clean_hits():
    """每条测试用干净的滑动窗口，否则前一条打的请求会算进后一条的配额。"""
    reg._HITS.clear()
    yield
    reg._HITS.clear()


# ------------------------------------------------------------ 分组
def test_all_eastmoney_keys_share_one_quota():
    """
    三个东财 ds_key 打的是**同一个 IP**，必须落在同一个限流组。

    各自限流是最容易犯的错：三个 key 每个都"没超"，加起来照样被封。
    """
    keys = ['em_push2', 'em_datacenter', 'ak_concept_em', 'ak_fundflow_em',
            'ak_lhb_detail']
    groups = {reg.limit_group(k) for k in keys}
    assert groups == {'eastmoney'}, groups


def test_local_and_tencent_are_not_throttled():
    """
    本地文件不出网、腾讯实测不封 IP —— 给它们加间隔只会让全市场批量取数白慢几分钟。
    """
    for key in ('tdx_vipdoc', 'ths_local_block', 'ths_local_watchlist',
                'baostock_pkg', 'mootdx_pkg', 'tencent_quote'):
        assert reg.limit_group(key) == 'free', key


def test_tushare_and_sina_have_their_own_group():
    # Tushare 的配额按**接口**算（实测每接口 1 次/分钟），所以每个 ds_key 一个组；
    # 阈值仍然查 `rate_limit.tushare` 这一份
    assert reg.limit_group('ts_margin') == 'tushare:ts_margin'
    assert reg.limit_group('ts_moneyflow') == 'tushare:ts_moneyflow'
    assert reg.limit_of('tushare:ts_margin').per_min == reg.limit_of('tushare').per_min
    assert reg.limit_group('sina_daily') == 'sina'

    # 同花顺**本地** ini 解析不算网络接口，不该被网络组的间隔拖慢
    assert reg.limit_group('ths_hot_list') == 'ths'
    assert reg.limit_group('ths_local_industry') == 'free'


# ------------------------------------------------------------ 阈值来源
def test_thresholds_come_from_the_database_with_code_as_fallback():
    """
    阈值在 `config.setting['rate_limit.<组>']` 里，代码里的 `LIMITS` 只是兜底。

    东财那一组必须**严于**实测封禁线：间隔 ≥ 0.2s（封禁线是 5 次/秒 = 0.2s），
    1 分钟 < 200、5 分钟 < 300。松于封禁线的配置等于没配。
    """
    lim = reg.limit_of('eastmoney')
    assert lim.min_interval >= 0.2, lim.min_interval
    assert lim.per_min and lim.per_min < 200
    assert lim.per_5min and lim.per_5min < 300
    assert reg.LIMITS['free'].min_interval == 0
    assert reg.LIMITS['free'].per_min is None


# ------------------------------------------------------------ 等待行为
def test_free_group_never_waits():
    t0 = time.perf_counter()
    waited = sum(reg._gate('tdx_vipdoc') for _ in range(20))
    assert waited == 0.0
    assert time.perf_counter() - t0 < 0.2, '不限流的组不该有任何 sleep'


def test_eastmoney_group_spaces_out_requests():
    """
    连打 8 次东财：闸门要把它们摊开到 ≥ 7 × min_interval。

    只断言"总等待 > 0"是不够的 —— 那样把间隔配成 1ms 也能过，而 1ms 挡不住风控。
    """
    lim = reg.limit_of('eastmoney')
    waited = sum(reg._gate('em_push2') for _ in range(8))
    assert waited >= 7 * lim.min_interval * 0.9, (waited, lim.min_interval)
    # 第一次不该等：闸门是"两次之间留间隔"，不是"每次先睡一下"
    reg._HITS.clear()
    assert reg._gate('em_push2') == 0.0


def test_gate_lets_through_instead_of_hanging_when_it_cannot_hold():
    """
    单次等待有上限（`MAX_WAIT`）：真被限住时宁可放行并把情况记进 message，
    也不能让跑批卡在这里等到超时 —— 卡死比慢危险。
    """
    assert reg.MAX_WAIT <= 60
    # 把 1 分钟配额压到 2 次，第三次需要等将近 60s > MAX_WAIT，应当直接放行
    tight = reg.RateLimit(min_interval=0.0, per_min=2)
    reg._setting_cache['__test_tight'] = (time.monotonic(), tight)
    try:
        reg._GROUP_BY_KEY['__test_key'] = '__test_tight'
        t0 = time.perf_counter()
        for _ in range(3):
            reg._gate('__test_key')
        assert time.perf_counter() - t0 < reg.MAX_WAIT + 1
    finally:
        reg._GROUP_BY_KEY.pop('__test_key', None)
        reg._setting_cache.pop('__test_tight', None)


def test_wait_is_reported_not_hidden():
    """
    限流等待要写进 `record()` 的 message，否则"源变慢了"和"我们自己在等闸门"
    在页面上长得一模一样，会让人去查错的东西。
    """
    from pathlib import Path
    src = Path('app/datasource/registry.py').read_text(encoding='utf-8')
    assert '限流等待' in src
    assert 'waited += _gate(ds_key)' in src


def test_the_datasource_page_shows_which_group_a_source_is_in():
    """页面上看不到限流组，就没人知道"这两个源共用配额"。"""
    from pathlib import Path
    src = Path('app/web/pages/datasource.py').read_text(encoding='utf-8')
    assert "'限流组'" in src
    assert 'ds_reg.limit_group(' in src
