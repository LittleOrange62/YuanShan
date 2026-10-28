# -*- coding: utf-8 -*-
"""换手强度口径（basis='turnover'，2026-10-12 加的第二把尺子）。

用户点名的问题原话：「这个流入流出也已经参照总市值去折算了吧」—— 答案是**没有**，
share 只剔"全场共同放量"，不剔"这个板块盘子本来就大"。这条口径存在的唯一理由就是把
盘子大小剔掉，所以测试要钉的不是"能不能跑"，而是**它有没有悄悄变回另一把尺子**：

① 分母必须是成分流通市值合计，且市值来源是**当日日线反推**（`stock_snapshot` 只有 36 场，
   用它这条口径就永远攒不满 10/20/30 场）；
② 分子必须与分母**同一母体**（停牌成员的钱不能只留在分子里 ⇒ 换手率虚高）；
③ 算不出必须交 NULL / 不写行，**不许交 0%**（0% 的意思是"今天没人交易它"）；
④ 母体必须与 share 同一集合（当天在 `sector_daily` 有行且有成交额）——
   实测按成员聚合会把 `THS_*`/`GN_*` 那些板块日线里没有行的板块带进来（1,856 vs 915），
   两条口径的名次分母不同，并排一比就是假话；
⑤ `universe_amount` 在 turnover 行必须留 NULL：那一列在 share 行里是"全场板块成交额合计"，
   同一天填两个不同含义的数，屏幕会说谎；换手自己的分母住 `mv_yuan`。
"""
import datetime as dt
import json

import pytest

from app.compute import sector_flow_trend as sft

P = {'windows': [10, 20, 30], 'sector_types': ['行业', '概念'], 'basis': ['share', 'turnover'],
     'min_days_ratio': 0.6, 'spec_version': 'sft-1', 'turnover_spec_version': 'stn-1',
     'turnover_chunk_days': 60}

D1, D2, D3 = dt.date(2026, 10, 8), dt.date(2026, 10, 9), dt.date(2026, 10, 12)


def _row(code, day, amt, mv, matched=10, members=10, sector_amount=1e9):
    return {'sector_code': code, 'trade_date': day, 'amt_matched': amt, 'mv': mv,
            'matched': matched, 'sector_amount': sector_amount, 'member_count': members}


@pytest.fixture
def fake(monkeypatch):
    """把 engine 按住：query 交回预设行，executemany 只记录不写库（测试绝不打库）。"""
    box = {'rows': [], 'batches': []}

    def _query(sql, params=None):
        box['sql'] = sql
        return [r for r in box['rows']]

    monkeypatch.setattr(sft.engine, "query", _query)
    monkeypatch.setattr(sft.engine, "executemany",
                        lambda sql, batch: box['batches'].append((sql, list(batch))))
    return box


# ---------------------------------------------------------------- ① 判据本身

def test_换手率是成员成交额除成员市值而不是除全场合计(fake):
    """value = 100 × Σ成员成交额 ÷ Σ成分流通市值，单位 %；与 share 的"除以全场"完全不同式。"""
    fake['rows'] = [_row('TDX_1', D1, 2.0e9, 1.0e11), _row('TDX_1', D2, 3.0e9, 1.0e11),
                    _row('TDX_1', D3, 4.0e9, 1.0e11)]
    sft.run_turnover([D3], P, False)
    r = fake['batches'][0][1][0]
    assert r['value'] == pytest.approx(4.0), "4e9/1e11 = 4%"
    assert r['value_prev'] == pytest.approx(3.0)
    # 差值 ×100 才是 bp：换手 1pp = 100bp
    assert r['delta'] == pytest.approx(100.0)
    assert r['mv'] == pytest.approx(1.0e11) and r['amt_matched'] == pytest.approx(4.0e9)


def test_市值反推的公式在注释里且不许换成快照表(fake):
    """反推口径是"100×成交额÷换手率"。哪天有人图省事去 join stock_snapshot，
    这条口径的历史立刻塌回 36 场 —— 所以把两句话都钉在源码里。"""
    assert '100.0 * p.a / p.t' in sft._TURNOVER_FETCH
    assert 'stock_snapshot' not in sft._TURNOVER_FETCH, "分母不许改用快照表（只有 36 场）"
    assert 'turnover_rate_percent' in sft._TURNOVER_FETCH
    assert '36' in sft.run_turnover.__doc__, "为什么不用快照的理由必须留在函数里，不许只活在聊天记录里"


def test_缺换手率或缺成交额的成员整条剔掉且钱也不进分子(fake):
    """分子分母同一母体：SQL 里 st 那一段要求 amount 与 turnover_rate 都非零，
    少任何一边都会做出一个虚高的换手率。"""
    sql = sft._TURNOVER_FETCH
    assert 'nullif(sd.amount_yuan, 0) is not null' in sql
    assert 'nullif(sd.turnover_rate_percent, 0) is not null' in sql


def test_全被剔分时不写行也不交零(fake):
    """mv 为 0/NULL 的板块这一行直接不出现 —— 交 0% 会被读成"今天没人交易它"。"""
    fake['rows'] = [_row('TDX_BAD', D1, 1.0e9, None), _row('TDX_BAD', D2, 1.0e9, 0),
                    _row('TDX_BAD', D3, None, 1.0e11),
                    _row('TDX_OK', D1, 1.0e9, 1.0e11), _row('TDX_OK', D2, 2.0e9, 1.0e11),
                    _row('TDX_OK', D3, 3.0e9, 1.0e11)]
    out = sft.run_turnover([D3], P, False)
    codes = [r['code'] for _, batch in fake['batches'] for r in batch]
    assert codes == ['TDX_OK'], "算不出的板块不许用 0 占位"
    assert out['rows'] == 1


# ---------------------------------------------------------------- ④ 母体与 share 同一集合

def test_母体必须走板块日线的内连接(fake):
    """按成员聚合会把 `THS_*`/`GN_*` 这些"板块日线里当天没有行"的板块带进来（实测 1,856 vs 915），
    名次分母一变，两条口径并排就是假话 —— 所以这条门是内连接 + 成交额>0，不许改回 left join。"""
    sql = sft._TURNOVER_FETCH
    assert 'join market_data.sector_daily' in sql
    assert 'left join market_data.sector_daily' not in sql, "改成 left join 就等于放开母体门"
    assert 'sd.amount_yuan > 0' in sql
    # 成员归属按那一天对齐，不是"今天的成员表回看历史"
    assert 'm.in_date <= p.d' in sql and 'm.out_date > p.d' in sql


def test_universe_amount留null分母住mv_yuan(fake):
    """同一天两个含义的"分母"是屏幕说谎的经典形状：share 行那里是全场板块成交额合计，
    turnover 行的分母是本板块成分市值（住 mv_yuan），所以这一列必须空着。"""
    assert 'universe, universe_amount, amount_yuan' in sft._UPSERT_T
    assert '%(universe)s, null, %(amount)s' in sft._UPSERT_T
    for col in ('mv_yuan', 'amount_matched', 'members_matched', 'members_total'):
        assert col in sft._UPSERT_T


# ---------------------------------------------------------------- ② 统计量与 share 同语义

def test_连续场次与累计的语义跟share那条一模一样(fake):
    """相等即断链、cum = 末场−首场、days_N 是实际有数的场数 —— 两条口径不是一套算法两次实现。

    ⚠️ `streak_up_days` 数的是**这一段有几场**（含当日），不是"上了几跳"：
    份额序列 1%→2%→2%→5% 里最后一跳上行、前一次相等把链断了，所以这一段是 {2%,5%} 两场 ⇒ 期望 2 不是 1。
    （这一条 10-12 我先按"跳数"写断了期望，红了一次才核对到函数本身的语义 —— 记在这儿免得下次再猜。）
    """
    fake['rows'] = [_row('TDX_1', D1, 1.0e9, 1.0e11), _row('TDX_1', D2, 2.0e9, 1.0e11),
                    _row('TDX_1', D3, 2.0e9, 1.0e11)]
    # 再加一天让最后一天真的上行
    d4 = dt.date(2026, 10, 13)
    fake['rows'].append(_row('TDX_1', d4, 5.0e9, 1.0e11))
    sft.run_turnover([d4], P, False)
    r = fake['batches'][0][1][0]
    assert (r['up'], r['dn']) == (2, 0), "中间那场相等把链断了，剩下的这一段是两场（含当日）"
    assert r['cum10'] == pytest.approx(5.0 - 1.0), "窗口攒不满时那是「末场−首场」的差，单位 pp"
    assert r['d10'] == 4 and r['d30'] == 4


def test_名次在场次不够时不给(fake):
    """min_days_ratio=0.6 ⇒ 10 场窗口至少要 6 场有名次；30 场窗口只有 4 场 ⇒ 三档全 None。"""
    fake['rows'] = [_row('TDX_1', D1, 1.0e9, 1.0e11), _row('TDX_1', D2, 2.0e9, 1.0e11),
                    _row('TDX_1', D3, 3.0e9, 1.0e11)]
    sft.run_turnover([D3], P, False)
    r = fake['batches'][0][1][0]
    assert r['r10'] is None and r['r20'] is None and r['r30'] is None, "给数不给排名，排名不许硬算"
    assert r['d30'] == 3 and r['missing'] == 27


def test_版本号各走各的改换手口径不许带走share(fake):
    """share 是 sft-1、turnover 是 stn-1：主键里有 spec_version，共用一号会让两条口径互相覆盖。"""
    p = dict(P)
    p.pop('turnover_spec_version')
    fake['rows'] = [_row('TDX_1', D1, 1.0e9, 1.0e11), _row('TDX_1', D2, 2.0e9, 1.0e11)]
    out = sft.run_turnover([D2], p, False)
    assert out['spec_version'] == 'stn-1'
    assert out['spec_version'] != P['spec_version']
    assert fake['batches'][0][1][0]['spec'] == 'stn-1'


def test_一条都没算出来时退出码要响(monkeypatch, capsys):
    """加第二条口径最容易留的洞：那条天天空跑、退出码还是 0，界面上就是一张永远为空的榜。"""
    monkeypatch.setattr(sft, 'run_turnover',
                        lambda days, p, dry: {'basis': 'turnover', 'rows': 0,
                                             'no_data_days': ['2026-10-12'], 'dry_run': dry})
    monkeypatch.setattr(sft, 'run_shares',
                        lambda days, p, dry: {'basis': 'share', 'rows': 915, 'per_day': {}})
    monkeypatch.setattr(sft.engine, 'scalar', lambda *a, **k: D3)
    assert sft.main(['--date', '2026-10-12', '--basis', 'turnover']) == 1
    out = capsys.readouterr().out
    assert '换手强度这一条一条都没算出来' in out and '不去拿只有 36 场的 stock_snapshot 凑数' in out


def test_dry_run不算失败也不写库(monkeypatch, capsys):
    monkeypatch.setattr(sft, 'run_turnover',
                        lambda days, p, dry: {'basis': 'turnover', 'rows': 0,
                                             'no_data_days': ['2026-10-12'], 'dry_run': dry})
    monkeypatch.setattr(sft.engine, 'scalar', lambda *a, **k: D3)
    assert sft.main(['--date', '2026-10-12', '--basis', 'turnover', '--dry-run']) == 0
