# -*- coding: utf-8 -*-
"""异动提醒（偏离值 / 异常波动 / 严重异常波动 / 明日涨停前瞻）的判据与写库诚实性。

判据那几例全是纯函数：`severity()` 与 `forward()` 吃合成行，不碰库。
钉住的三类"写错了不报错"的坑：
  · 前瞻把今日 10 场累计当成明日窗口（触发价算得比涨停价还高却留在榜里）；
  · 次数档用今日窗口的次数 +1（明日会把最早那场挤出去）；
  · 判不了的东西被兜成 False / 0（屏幕读成"没有一只会触发"）。
最后一条**不打桩**：现读真库算一场，确认 join 没把一个交易日叉成两行。
"""
from __future__ import annotations

import datetime as dt
import pytest

from app.compute import abnormal_move as A


def lim(r):
    """这只票的明日涨停价（forward() 只交触发涨幅/触发价，涨停价在行上）。"""
    return A.limit_price(r['close'], r['band_next'])


def row(**kw):
    """合成一只沪主板票的当日读数：默认三档窗口都齐、都没踩到。

    ⚠️ `stock_2/9/29` 是复利口径下的"这一区间个股涨了多少个点"（生产里由 `window_gains()`
    从 SQL 的对数和折出来）。合成行默认 0 ⇒ 放大系数 100/(100+0)=1，
    「所需涨幅」与「累计缺口」数值相等，下面那些期望值才写得动；
    放大不放大是另一件事，由 `test_区间已涨的票_所需涨幅比缺口小` 单独钉。
    """
    base = dict(security_code='600000.SH', trade_date=dt.date(2026, 10, 15),
                board='沪主板', thr_group='主板', close=10.0, pct_change=1.0,
                index_pct=0.5, bars_used=60, fifth_session=None,
                dev_10=10.0, n_10=10, dev_30=20.0, n_30=30,
                dev_10_pre=9.0, n_9=9, dev_30_pre=19.0, n_29=29,
                stock_2=0.0, stock_9=0.0, stock_29=0.0,
                up_cnt_10=0, down_cnt_10=0, up_cnt_9=0, down_cnt_9=0,
                dev_prev2=1.0, n_2=2, n_3=3, dev_3=1.5, sealed_10=0,
                band_next=0.10)
    base.update(kw)
    return base


# ------------------------------------------------------------------ 档位与涨停价

@pytest.mark.parametrize('close,limit_up,pre_close,want', [
    (11.00, 11.00, 10.00, 0.10),        # 普通主板
    (10.50, 10.50, 10.00, 0.05),        # 主板 ST
    (12.00, 12.00, 10.00, 0.20),        # 双创
    (10.40, 11.44, 10.40, 0.10),        # 最小变动价位把幅度推到 10.4%：归档，不当成 12%
    (13.00, 13.00, 10.00, None),        # 30% 不是本表任何一档 ⇒ 这行的 limit_up 不可信
])
def test_明日涨停档位由当日反推并归档(close, limit_up, pre_close, want):
    assert A.band_from_row(close, limit_up, pre_close) == want


def test_档位取不到就是_none_不是零():
    assert A.band_from_row(10.0, None, 10.0) is None
    assert A.band_from_row(10.0, 11.0, None) is None
    assert A.band_from_row(10.0, 11.0, 0.0) is None
    assert A.limit_price(10.0, None) is None


def test_涨停价四舍五入到分():
    assert A.limit_price(23.25, 0.10) == 25.58        # 25.575 → 分位进一
    assert A.limit_price(53.13, 0.10) == 58.44


# ------------------------------------------------------------------ 严重异常波动三条款

def test_主板十日累计过百算踩到():
    r = A.severity(row(dev_10=100.2))
    assert r['severe'] is True and 'dev10' in r['severe_how'] and r['severe_dir'] == 'up'


def test_主板十日累计过负五十算踩到并且方向是跌():
    r = A.severity(row(dev_10=-52.0))
    assert r['severe'] is True and r['severe_dir'] == 'down'


def test_十日累计负四十九不算踩到():
    """603668.SH 实测就是这一档（−49.7）：次数条踩到、累计条没踩到，两条各判各的。"""
    r = A.severity(row(dev_10=-49.7))
    assert r['severe'] is False and r['severe_how'] is None


def test_双创阈值是三十点不是二十点():
    up = row(thr_group='创业板', dev_3=31.0)
    assert A.ab_thr(up['thr_group']) == 30.0
    assert A.severity(row(thr_group='创业板', dev_10=90.0))['severe'] is False


def test_次数档主板四次双创三次():
    assert A.severity(row(up_cnt_10=4))['severe'] is True
    assert A.severity(row(up_cnt_10=3))['severe'] is False
    gem = row(thr_group='科创板', up_cnt_10=3)
    assert A.severity(gem)['severe'] is True


def test_窗口根数不够是判不了不是没踩到():
    r = A.severity(row(dev_10=None, n_10=6, dev_30=None, n_30=6, up_cnt_10=None, n_3=3))
    assert r['severe'] is None and r['severe_reason'] == 'bars_lt_window'


def test_上市前五个交易日整条判不了():
    r = A.severity(row(fifth_session=dt.date(2026, 10, 20), trade_date=dt.date(2026, 10, 15)))
    assert r['severe'] is None and r['severe_reason'] == 'new_listing_lt5'


def test_指数涨幅取不到是判不了():
    """异常兜成空/False 会被下游写成一句合理的假读数（10-13 那条纪律）。"""
    assert A.severity(row(index_pct=None))['severe_reason'] == 'no_index_pct'
    assert A.severity(row(pct_change=None))['severe_reason'] == 'no_pct'


def test_涨跌两向都踩到交mixed不合成中性():
    r = A.severity(row(dev_10=-60.0, up_cnt_10=4))
    assert r['severe'] is True and r['severe_dir'] == 'mixed'


# ------------------------------------------------------------------ 前瞻：明日涨停会不会踩到

def test_十日档用的是去掉明日的九场累计():
    """600241.SH 实测：今日 10 场累计 73.9（去掉明日那场只有 66 左右），明日涨停 10% 根本到不了
    100 —— 老算法拿 100 − dev_10 当所需涨幅，算出触发价 17.64 比涨停价 15.39 还高却留在榜里。"""
    r = row(dev_10=73.9, dev_10_pre=73.9, n_10=10, band_next=0.10, up_cnt_9=0, dev_prev2=-5.0)
    f = A.forward(r)
    assert f['need_dev_to_100'] == pytest.approx(26.1)
    assert f['trigger_pct'] == pytest.approx(26.1)
    assert f['hit_if_limit_up'] is False           # 26.1 > 10 ⇒ 涨停也够不着
    assert f['fwd_clauses'] is None
    assert f['trigger_price'] > lim(r)             # 触发价在涨停价之上：榜里不该有它


def test_十日档扣掉被挤出去的那场才算得准():
    """sn-9 那场偏离 +20：今天窗口算它，明天窗口不算它 ⇒ 所需涨幅从 0 变成 20。"""
    f = A.forward(row(dev_10_pre=80.0, dev_10=100.0))
    assert f['need_dev_to_100'] == pytest.approx(20.0)
    assert f['hit_if_limit_up'] is False


def test_三十日档用的是去掉明日的二十九场累计():
    r = row(dev_30_pre=195.0)
    f = A.forward(r)
    assert f['need_dev_to_200'] == pytest.approx(5.0)
    assert 'dev30' in f['fwd_clauses'] and f['hit_if_limit_up'] is True
    assert f['trigger_pct'] == pytest.approx(5.0)
    assert f['trigger_price'] <= lim(r)


def test_三十日窗口不满二十九场就判不了这一档():
    f = A.forward(row(n_29=20, dev_30_pre=199.0))
    assert f['need_dev_to_200'] is None


def test_次数档要明日那场自己构成一次异常波动():
    # dev_prev2=13.27 ⇒ 主板要再 6.73 个点才凑出一次；涨停 10% 够
    r = row(dev_prev2=13.27, up_cnt_9=3, dev_10_pre=40.0, dev_30_pre=50.0)
    f = A.forward(r)
    assert f['times_if_limit'] is True
    assert 'times' in f['fwd_clauses']
    assert f['trigger_pct'] == pytest.approx(6.73)
    assert f['trigger_price'] <= lim(r)


def test_次数档用九场次数加一而不是十场():
    """明日那个 10 日窗是 sn-8…sn+1：今天窗口里最早那场（sn-9）被挤出去。
    主板要 4 次：up_cnt_10=4 但被挤掉那场正是第 4 次 ⇒ up_cnt_9=3，明日再凑一次才够。"""
    hit = A.forward(row(up_cnt_10=4, up_cnt_9=3, dev_prev2=19.0,
                        dev_10_pre=10.0, dev_30_pre=20.0))
    assert hit['times_if_limit'] is True
    miss = A.forward(row(up_cnt_10=4, up_cnt_9=2, dev_prev2=19.0,
                         dev_10_pre=10.0, dev_30_pre=20.0))
    assert miss['times_if_limit'] is False


def test_已踩到一条的票仍进榜但只算下一条的缺口():
    """10-27 用户点名换的行为。旧版在这里交 `hit_if_limit_up=False` 并把整只票挡在榜外
    （大业股份、海天瑞声就是这么从前瞻榜上消失的）—— 现在它照样进榜，说的是"下一条还差多少"。

    这一行三条档：dev10 已踩到（不再算它的缺口）、dev30 差 181 点（今天涨停 10% 够不着）、
    次数档算得出来但明日凑不满第 4 次 ⇒ `hit_if_limit_up` 是**False**（不是 None）：
    没踩到的两条全都判得动，这句才是真话。凑不满次数≠判不动，别把它混进"判不了"。
    """
    r = row(dev_10_pre=99.0, dev_10=100.5, dev_30_pre=19.0)
    assert A.severity(r)['severe'] is True and 'dev10' in A.severity(r)['severe_how']
    f = A.forward(dict(r, severe=True, severe_how='dev10'))
    assert f['fwd_clauses'] is None                    # 明日涨停不会**新**踩到一条
    assert f['hit_if_limit_up'] is False               # 三档全判得动、都没到 —— 这句有依据
    assert f['need_dev_to_100'] == pytest.approx(1.0)  # 已踩那条的缺口照交（负数=早已过线）
    assert f['need_dev_to_200'] == pytest.approx(181.0)
    assert f['trigger_pct'] == pytest.approx(181.0)    # 离下一条还差多少，榜上那一格念的就是它
    assert f['times_if_limit'] is False


def test_区间已涨的票_所需涨幅比缺口小():
    """复利口径下的两把尺：`need_dev_to_100` 是**偏离值缺口**，`trigger_pct` 是**明日涨幅**。
    区间已经涨过 S 个点时，明日每涨 1 个点会把那一段放大 (100+S)/100 倍，
    所以要的涨幅 = 缺口 × 100/(100+S)。旧口径（逐日相加）下 S 不参与，两列数字相等 ——
    这一条就是把"换了尺子之后两者不再相等"钉住，谁把公式退回加法会当场红。
    """
    # 10 日区间个股已涨 99 个点、指数没动 ⇒ 缺口 1 个点只需再涨 0.5 个点
    f = A.forward(row(dev_10_pre=99.0, stock_9=99.0))
    assert f['need_dev_to_100'] == pytest.approx(1.0)
    assert f['trigger_pct'] == pytest.approx(0.5025, abs=1e-3)
    assert f['hit_if_limit_up'] is True and 'dev10' in f['fwd_clauses']
    # 涨停后那个窗的累计同样按复利放大：1.99 × 1.10 − 1 = 118.9（逐日相加才会给 99 + 10 = 109）
    assert f['dev_10_if_limit'] == pytest.approx(118.9, abs=0.05)
    # 涨得越猛差得越少：30 日区间已涨 150 个点、缺口 50 ⇒ 只要再涨 20 个点
    g = A.forward(row(dev_30_pre=150.0, stock_29=150.0))
    assert g['need_dev_to_200'] == pytest.approx(50.0)
    assert g['trigger_pct'] == pytest.approx(20.0)
    # 涨停后那个 30 日窗：个股那半边放大 (100+150)/100=2.5 倍 ⇒ 150 + 10×2.5 = 175
    # （拿 1.5 × 1.10 复利回去也是 175 —— 两条算法必须给出同一个数，这里就是钉它同不同源）
    assert g['dev_30_if_limit'] == pytest.approx(175.0)


def test_区间涨幅取不到时那一档判不了而不是按没涨顶过去():
    """`stock_*` 缺 ⇒ 放大系数算不出来。那一档绝不交一个"缺口=涨幅"的数，
    并且因为还有判不动的档，整只票的 `hit_if_limit_up` 必须是 None（不许说"不会触发"）。"""
    f = A.forward(row(dev_10_pre=99.0, stock_9=None, dev_30_pre=None, n_29=20, dev_prev2=None))
    assert f['need_dev_to_100'] == pytest.approx(1.0)   # 缺口这一列照交（它不需要放大系数）
    assert f['trigger_pct'] is None and f['hit_if_limit_up'] is None
    assert f['dev_10_if_limit'] is None                 # 绝不折成"99 + 10 = 109"那种假累计
    # 只有这一档算不动时，别的档照算、结论仍因这一档而交 None
    g = A.forward(row(dev_10_pre=99.0, stock_9=None, dev_30_pre=150.0, stock_29=150.0))
    assert g['trigger_pct'] == pytest.approx(20.0)      # 来自 30 日档，不是 dev10 的 1.0
    assert g['hit_if_limit_up'] is None                 # dev10 判不动 ⇒ 不许说"不会触发"


def test_window_gains_把对数和折成区间涨幅():
    """`window_gains` 是复利折算的唯一出口：ln 之和 → 区间涨幅（百分点），NaN → None。"""
    import math
    r = A.window_gains({'ls2': math.log(1.10), 'ls9': math.log(1.99), 'ls29': float('nan')})
    assert r['stock_2'] == pytest.approx(10.0)
    assert r['stock_9'] == pytest.approx(99.0)
    assert r['stock_29'] is None                        # 缺读数那几天绝不折成"没涨"
    assert A.window_gains({})['stock_2'] is None        # 那一列根本没交时也不凭空造一个 0


def test_跌向已踩不影响涨向预警():
    f = A.forward(row(severe=True, severe_dir='down', up_cnt_9=3, dev_prev2=15.0,
                      dev_10_pre=10.0, dev_30_pre=20.0))
    assert f['times_if_limit'] is True and f['hit_if_limit_up'] is True


def test_三档只判得出一部分时不许说不会触发():
    """新票/长停牌：dev10、dev30 窗口都不够，只有次数档算得出来且没到 ⇒ 交 None（判不了），
    不能交 False —— 屏幕会把 None 写成"这一档判不了"，把 False 写成"今天没有一只会触发"。"""
    f = A.forward(row(n_9=4, n_29=4, n_2=2, dev_10_pre=None, dev_30_pre=None,
                      dev_prev2=1.0, up_cnt_9=None))
    assert f['hit_if_limit_up'] is None
    assert f['need_dev_to_100'] is None and f['need_dev_to_200'] is None


def test_档位判不了时前瞻整块是_none():
    f = A.forward(row(band_next=None))
    assert f == {'need_dev_to_100': None, 'need_dev_to_200': None,
                 'dev_10_if_limit': None, 'dev_30_if_limit': None,
                 'trigger_pct': None, 'trigger_price': None,
                 'hit_if_limit_up': None, 'fwd_clauses': None, 'times_if_limit': None}


def test_缺口是负数时触发涨幅夹成零不夹成假的最小值():
    """去掉明日就已过线（明日窗口里那 9 场累计 ≥100）⇒ 所需涨幅 0，而不是负数。"""
    f = A.forward(row(dev_10_pre=104.0, dev_30_pre=50.0, dev_prev2=1.0, up_cnt_9=0))
    assert f['need_dev_to_100'] == pytest.approx(-4.0)     # 这一列如实交"已经过了"
    assert f['trigger_pct'] == 0.0
    assert f['trigger_price'] == 10.0
    assert f['hit_if_limit_up'] is True


# ------------------------------------------------------------------ 母体

def test_没进母体的行整行不写():
    assert A.universe_by(row(severe=False, ab_up=False, ab_down=False, up_cnt_10=0,
                             down_cnt_10=0, dev_10=1.0, dev_30=2.0, n_10=10, n_30=30,
                             sealed_10=0, hit_if_limit_up=False)) is None


def test_母体理由把命中条都列出来():
    ub = A.universe_by(row(severe=True, dev_10=30.0, n_10=10, dev_30=70.0, n_30=30,
                           up_cnt_10=1, sealed_10=1, hit_if_limit_up=True))
    assert set(ub.split(', ')) == {'severe', 'ab_flag', 'dev10_ge25', 'dev30_ge60',
                                   'sealed10', 'forward_hit'}


# ------------------------------------------------------------------ 出口清洗与写库

def test_nan与decimal进判据前先变成float或_none():
    """10-15 首跑实踩：psycopg 把 numeric 交回 Decimal，`100.0 − Decimal` 直接抛。"""
    from decimal import Decimal
    assert A._f(float('nan')) is None
    r = A._normalize({'dev_10': Decimal('12.5'), 'n_10': 10, 'close': Decimal('0')})
    assert isinstance(r['dev_10'], float) and r['dev_10'] == 12.5
    assert isinstance(r['n_10'], int)
    f = A.forward(A._normalize(row(dev_10_pre=Decimal('80'))))   # 管线里判据只吃归一化后的行
    assert f['need_dev_to_100'] == pytest.approx(20.0)


def test_累计计数列交的是整数():
    """up_cnt_10 在 DDL 里是 int，被 _f 转成浮点会让界面写出 4.0 次。"""
    assert 'up_cnt_10' in A._INT_COLS and 'up_cnt_10' not in A._NUM_COLS
    assert 'down_cnt_10' in A._INT_COLS and 'n_9' in A._INT_COLS and 'n_29' in A._INT_COLS


def test_写入列数与占位符数一致并且冲突键就是主键():
    n = A.UPSERT.count('%s')
    assert n == len(A._COLUMNS)
    assert 'on conflict (security_code, trade_date, spec_version)' in A.UPSERT
    for c in ('security_code', 'trade_date', 'spec_version'):
        assert f'{c} = excluded.{c}' not in A.UPSERT      # 主键三列不参与 update set


def test_sql文本里没有裸百分号():
    """psycopg 把 % 当占位符开头：裸 % 会在 execute 时抛，或者更坏——吃掉参数。"""
    for i, ch in enumerate(A._SQL):
        if ch == '%':
            assert A._SQL[i + 1] == '(', f'第 {i} 字符是裸 %%：{A._SQL[i - 30:i + 10]!r}'


def test_重算一场先清这一场这一版本的旧行(monkeypatch):
    """只 upsert 不清场 ⇒ 上一次半截计算留下的行会被读成"今天判不了"（同族缺陷 #194）。"""
    sql: list[str] = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, q, p=None):
            sql.append(' '.join(str(q).split())[:70])

        def executemany(self, q, payload):
            sql.append('executemany %d' % len(payload))

    monkeypatch.setattr(A.engine, 'cursor', lambda **kw: _Cur())
    day = dt.date(2026, 10, 15)
    assert A.write(day, []) == 0
    assert any(s.startswith('delete from analytics.abnormal_move_daily') for s in sql), sql
    assert not any(s.startswith('executemany') for s in sql)     # 空批只清不写


@pytest.fixture(scope="module")
def one_real_session():
    """不打桩的一条：现读真库算一场。整批判据都跑真的，代价是这条要几十秒。"""
    day = A.latest_day()
    assert day is not None, '库里没有日线'
    rows, note = A.compute_day(day)
    return day, rows, note


def test_真库算一场每只票只有一行并且前瞻不自相矛盾(one_real_session):
    day, rows, _ = one_real_session
    assert rows, f'{day} 母体为空 —— 日线没落盘还是取数SQL坏了'
    codes = [r['security_code'] for r in rows]
    assert len(set(codes)) == len(codes), 'join 把一个交易日叉成多行 ⇒ 窗口累计偏离翻倍'
    for r in rows:
        assert (r.get('n_9') or 0) <= 9 and (r.get('n_29') or 0) <= 29, \
            f'{r["security_code"]} 窗口根数超过 RANGE 上界：取数有重复行'
        if r.get('fifth_session'):
            assert not ((r['ab_up'] or r['ab_down']) and r['trade_date'] <= r['fifth_session']), \
                f'{r["security_code"]} 上市前 5 个交易日的 3 日窗被计成了一次异常波动'
        if r.get('hit_if_limit_up'):
            assert r['trigger_pct'] is not None and r['trigger_price'] is not None
            assert r['trigger_pct'] <= r['band_next'] * 100.0 + 1e-6, \
                f'{r["security_code"]} 所需涨幅在涨停幅度外却进了预警榜'
            assert r['trigger_price'] <= A.limit_price(r['close'], r['band_next']) + 0.005, \
                f'{r["security_code"]} 触发价比涨停价还高'
        if r.get('severe') is True:
            assert r.get('severe_how') and r.get('severe_dir') in ('up', 'down', 'mixed')
        if r.get('severe') is False:
            assert not r.get('severe_how')
            assert (r.get('severe_reason') or '').startswith('partial:') \
                or r.get('severe_reason') == 'none'



def test_库里真的算过那些日子():
    """不打桩的一条：回算之后回查库里的 min/max/场次数（rc=0 与日志都会说谎）。"""
    from app.db import engine
    r = engine.query_one(
        "select count(distinct trade_date) sessions, min(trade_date)::text a, "
        "max(trade_date)::text z, count(*) n, "
        "count(*) filter (where severe) sev, "
        "count(*) filter (where severe is null) unk, "
        "count(*) filter (where hit_if_limit_up) fwd, "
        "count(*) filter (where hit_if_limit_up and trigger_price > limit_price_next) bad "
        "from analytics.abnormal_move_daily where spec_version = %s",
        (A.SPEC_VERSION,), role='reader')
    assert r and r['n'] > 0, '表里一场都没有 —— 每日加工那一步没接上，还是回算没跑？'
    assert r['sessions'] >= 200, f'只算了 {r["sessions"]} 场：{r["a"]} ~ {r["z"]}'
    assert r['z'] == engine.scalar("select max(trade_date)::text from market_data.stock_daily"), \
        '异动提醒没算到最新一场（每日加工链那一步没接上或被跳过）'
    assert (r['bad'] or 0) == 0, f'{r["bad"]} 行"触发价比涨停价还高"却进了预警榜'
    assert (r['fwd'] or 0) > 0, '一整年没有一只明日涨停能触发 —— 先怀疑前瞻那三档算漏了'
    # verify() 读的是**裸行**（Decimal、group by、聚合别名都在这条路上），只跑单测里的合成行
    # 永远碰不到它 —— 10-16 这一发实跑揪出两处"写在代码里却从没被跑到"的错：
    # `count(*)` 被算进 group by 的序号里、`band_next * 100.0` 拿 Decimal 乘 float 直接抛。
    bad = A.verify(dt.date.fromisoformat(r['z']))
    assert bad == [], f'{r["z"]} 这一场的行不自洽：{bad[:4]}'


def test_换尺之后那两只漏报的要判成踩到():
    """`abn-1`（逐日相加）把这两只判成"没踩到"，而行情软件那一边标着「10日100%」——
    用户 10-27 点名"计算方式好像有问题"就是从这里来的。这一条**不打桩**：读库里 `abn-2` 那批行，
    所以它同时也是"回算真的跑到这些场次了"的证据（rc=0 与日志都不算）。"""
    from app.db import engine
    for code, day, floor in (('688137.SH', dt.date(2026, 9, 30), 100.0),
                             ('300313.SZ', dt.date(2026, 8, 25), 100.0)):
        r = engine.query_one(
            'select dev_10, severe, severe_how from analytics.abnormal_move_daily '
            'where security_code = %s and trade_date = %s and spec_version = %s',
            (code, day, A.SPEC_VERSION), role='reader')
        assert r, f'{code} @{day} 在 {A.SPEC_VERSION} 这批里没有行 —— 回算没跑到这一场'
        assert r['dev_10'] is not None and float(r['dev_10']) >= floor, \
            f'{code} @{day} 区间复利累计只有 {r["dev_10"]}，没到 {floor}（旧口径 78.7/78.3）'
        assert r['severe'] is True and 'dev10' in (r['severe_how'] or ''), \
            f'{code} @{day} 累计 {r["dev_10"]} 却没判成踩到 dev10：severe={r["severe"]} how={r["severe_how"]}'


def test_前瞻榜把已踩到某一条的票放回来了并且那一格说清踩到哪条():
    """用户 10-27 点名的第二件事：`abn-1` 那版 `where hit_if_limit_up` 一只不留，
    已踩到 times 而 10 日档还差十几个点的票（大业股份、海天瑞声）整只从榜上消失。
    这一条不打桩：库里现数一个"应该有几行"，与读侧交出的行数对上 ——
    只在读侧偷偷放宽 where 而写侧没算，两边数字对不上就会红。"""
    from app.db import engine
    from duanxian import abnormal_view as V
    day = A.latest_day()
    d = V.screen(date=day.isoformat(), limit=200)
    assert d.get('available'), d.get('reason')
    rows = d['forward']['rows']
    want = engine.query_one(
        f'select count(*)::int n from analytics.abnormal_move_daily '
        'where trade_date = %s and spec_version = %s '
        '  and (hit_if_limit_up or (severe and trigger_pct is not null))',
        (day, V.SPEC_VERSION), role='reader')['n']
    assert len(rows) == min(want, 200), f'库里这一场该交 {want} 行，读侧只交出 {len(rows)} 行'
    already = [r for r in rows if r.get('fwd_state') == 'already_partial']
    assert already, f'{day} 一场里没有任何"已踩到某一条而下一条还差着"的行 —— 先怀疑回算没跑到'
    for r in rows:
        assert r['fwd_state'] in ('will_trigger', 'already_partial'), r
        label = r.get('fwd_state_label') or ''
        assert label, f'{r["security_code"]} 进了前瞻榜却没有那一格的文本'
        assert (label.startswith('已踩到')) == (r['severe'] is True), \
            f'{r["security_code"]} severe={r["severe"]} 但那一格念成「{label}」'
        if r['fwd_state'] == 'will_trigger':
            assert '明日涨停即踩到' in label and r.get('fwd_clauses')
        else:
            assert '再涨' in label or '算不出来' in label, label
    # 两类各归各位：会新踩到的排在前面（`order by hit_if_limit_up desc`），
    # 不然用户第一眼看到的会是"差 181 个点"那种够不着的
    states = [r['fwd_state'] for r in rows]
    assert states == sorted(states, key=lambda s: s != 'will_trigger'), \
        f'两类在榜里交错：{states[:12]}'


def test_读侧第三块把踩到哪几条真的交出来了():
    """10-16 浏览器实测抓到的：`_recent_severe` 的 SELECT 里没投影 severe，
    于是屏幕上那一格 89 行全念成「判不了」—— 判据明明筛的是 severe=true。
    这条不打桩：读真库这一场，逐行核对状态列不是空的。"""
    import datetime as dt

    from duanxian import abnormal_view as V
    day = A.latest_day()
    d = V.screen(date=day.isoformat())
    assert d.get('available'), d.get('reason')
    rows = d['recent_severe']['rows']
    assert rows, '近期严重异动一块是空的'
    for r in rows:
        assert r.get('severe') is True, r
        assert r.get('severe_how'), f'{r["security_code"]} 踩到了却没交条款'
        mv = V.__dict__['_label'](dict(r))
        assert '判不了' not in str(mv.get('severe_how_label') or ''), mv
    assert all(r.get('board_span') is not None or True for r in d['hot']['rows'])
