# -*- coding: utf-8 -*-
"""
趋势启动点。这一组测试盯的是三类"会静默出错、但看起来很正常"的问题：

1. **未来数据泄漏**。启动点的全部价值在于"当天就能看出来"，一旦要素里混进
   `shift(-k)`，回测会漂亮得离谱而实盘一条都用不上。所以有一条测试把面板
   在 T 日**截断**再算一遍，要求 T 日的五个要素分**一个字节都不变**。
   唯一允许看未来的是 `forward()`（那是评估，不是信号）。
2. **`squeeze` 方向被"顺手改回直觉"**。方向是回测选出来的（`active`），
   与方案里最初写的 `quiet` 相反。这里锁住默认值，谁改都会红。
3. **`ignite` 的前置条件退化成纯动量**。前置必须读**信号日之前**的蓄势状态；
   少一个 `shift(1)`，测试里那条"当天才蓄势"的用例就会被误判成 ignite。
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from app.compute import ignition as ig

DAYS = 200


def _panel(*, objs=('A', 'B'), n=DAYS, heat=None, up=None, amount=None):
    """
    造一个形状对的板块面板。默认 heat 走正弦 + 趋势，是为了让 120 日分位、
    20 日标准差、5/20 均线这些量都有真实的分布，而不是一条直线（直线上
    `rank(pct=True)` 全是 1.0，什么都测不出来）。
    """
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(n)]
    frames = []
    for k, obj in enumerate(objs):
        h = heat if heat is not None else [
            30.0 * math.sin(i / 7.0 + k) + i * 0.05 for i in range(n)]
        close = [100.0 + 0.3 * i + 5.0 * math.sin(i / 5.0 + k) for i in range(n)]
        amt = amount if amount is not None else [
            1e8 * (1.0 + 0.3 * math.sin(i / 11.0 + k)) for i in range(n)]
        upc = up if up is not None else [
            20 + 10 * math.sin(i / 6.0 + k) for i in range(n)]
        frames.append(pd.DataFrame({
            'obj_key': obj, 'trade_date': dates, 'heat': h,
            'high': [c * 1.02 for c in close], 'low': [c * 0.98 for c in close],
            'close': close,
            'pct_chg': [0.0] + [close[i] / close[i - 1] * 100 - 100
                                for i in range(1, n)],
            'amount_yuan': amt,
            'up_count': upc,
            'down_count': [50 - u for u in upc],
            'member_count': 50,
        }))
    df = pd.concat(frames, ignore_index=True)
    return df.set_index(['obj_key', 'trade_date']).sort_index()


def _terms_row(**over) -> pd.DataFrame:
    """给 stages() 用的极简要素表：3 天 × 1 个板块，逐天覆盖字段。"""
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(3)]
    idx = pd.MultiIndex.from_product([['A'], dates],
                                     names=['obj_key', 'trade_date'])
    base = {'squeeze': [0.0] * 3, 'pos': [0.0] * 3, 'ignition': [0.0] * 3,
            'volume': [0.0] * 3, 'breadth': [0.0] * 3, 'n_terms': [5] * 3,
            '_slope3': [0.0] * 3, '_q_heat': [0.5] * 3}
    base.update(over)
    return pd.DataFrame(base, index=idx)


# ------------------------------------------------------------ 参数
def test_squeeze_direction_defaults_to_active():
    """
    方向是被回测推翻后定下来的，不是直觉。原直觉 `quiet`（越沉寂越高分）
    样本内 ignite T+5 超额 −0.209 / 胜率 42.7%，比 none 对照组还差；
    `active` 同期 +0.148 / 51.1%，样本外 +0.455 / 57.5%（基准 +0.074）。
    """
    assert ig.DEFAULTS['squeeze_dir'] == 'active'


def test_squeeze_dir_is_a_string_knob_not_a_number():
    """
    `params()` 按默认值的类型转换配置。以前一律 `float(v)`，字符串旋钮会被
    吞成异常再被 except 吃掉 —— 于是配置写了没反应。
    """
    assert ig._cast('active', 'QUIET ') == 'quiet'
    assert ig._cast(1.0, '2.5') == pytest.approx(2.5)
    assert ig._cast(120, '60') == 60


def test_unknown_squeeze_dir_raises_instead_of_silently_passing():
    with pytest.raises(ValueError, match='squeeze_dir'):
        ig.params(squeeze_dir='both')


def test_two_directions_are_complementary():
    df = _panel()
    a = ig.terms(df, ig.params(squeeze_dir='active'))['squeeze']
    q = ig.terms(df, ig.params(squeeze_dir='quiet'))['squeeze']
    both = (a + q).dropna()
    assert not both.empty
    assert both.max() == pytest.approx(100.0, abs=1e-6)
    assert both.min() == pytest.approx(100.0, abs=1e-6)


# ------------------------------------------------------------ 不含未来数据
def test_terms_use_no_future_data():
    """
    把面板在 T 日截断再算，T 日的五个要素分必须完全一致。

    这是整组测试里最重要的一条：`_pctile` 用 `rolling(...).rank(pct=True)`、
    均线用 `rolling`、"由负转正"用 `shift(1)`，任何一处换成 `expanding` 之外
    的全样本统计（比如 `Series.rank`、全期 `mean`）都会在这里露出来。
    """
    df = _panel()
    p = ig.params()
    full = ig.terms(df, p)
    dates = df.index.get_level_values('trade_date')
    days = sorted(set(dates))
    for cut in (days[-1], days[-21], days[-60]):
        part = ig.terms(df[dates <= cut], p)
        for col in ig.TERMS + ('score', 'n_terms'):
            pd.testing.assert_series_equal(
                full[col].loc[[('A', cut)]], part[col].loc[[('A', cut)]],
                check_names=False)



def test_forward_is_the_only_future_looking_helper():
    """`forward()` 故意用未来数据 —— 这里顺手锁住它算的确实是 close 的比值。"""
    df = _panel(n=10)
    fwd = ig.forward(df, horizons=(3,))
    close = pd.to_numeric(df['close'], errors='coerce')
    first = ('A', df.index.get_level_values('trade_date')[0])
    day4 = ('A', df.index.get_level_values('trade_date')[3])
    want = (float(close[day4]) / float(close[first]) - 1.0) * 100
    assert float(fwd.loc[first, 'ret3']) == pytest.approx(want)
    # 末尾 3 天没有未来 → 必须是空，不能拿最后一天顶
    # （用 tail 而不是 groupby.last()：last() 会跳过 NaN 取到前面那天，永远绿）
    assert fwd['ret3'].groupby(level='obj_key').tail(3).isna().all()



# ------------------------------------------------------------ 分级
def test_ignite_wins_over_coil_and_fade_on_the_same_day():
    """
    同一天既"还在低位"又"今天点火"时要报 ignite —— 那才是要看的事。
    顺序错了的话，核心产出会被 coil（两万多行的观察池）盖掉。
    """
    p = ig.params()
    t = _terms_row(squeeze=[99.0] * 3, pos=[99.0] * 3,
                   ignition=[0.0, 0.0, 100.0], volume=[0.0, 0.0, 100.0],
                   breadth=[0.0, 0.0, 100.0], _q_heat=[0.5, 0.5, 0.95],
                   _slope3=[0.0, 0.0, -1.0])
    st = ig.stages(t, p)
    assert list(st) == ['coil', 'coil', 'ignite']


def test_pre_coil_must_come_from_before_the_signal_day():
    """
    只有当天蓄势、之前没有 → 不是 ignite。少一个 `shift(1)` 这条就会变红。
    """
    p = ig.params()
    t = _terms_row(squeeze=[0.0, 0.0, 99.0], pos=[0.0, 0.0, 99.0],
                   ignition=[0.0, 0.0, 100.0], volume=[0.0, 0.0, 100.0],
                   breadth=[0.0, 0.0, 100.0])
    assert list(ig.stages(t, p)) == ['none', 'none', 'coil']


def test_missing_breadth_never_reaches_ignite():
    """
    880001 上证这类指数型板块没有涨跌家数 → `breadth` 空 → `n_terms`=4 →
    永远到不了 ignite。这是有意的：上证指数不是"启动的板块"。
    """
    p = ig.params()
    t = _terms_row(squeeze=[99.0] * 3, pos=[99.0] * 3,
                   ignition=[0.0, 0.0, 100.0], volume=[0.0, 0.0, 100.0],
                   breadth=[None, None, None], n_terms=[4, 4, 4])
    st = ig.stages(t, p)
    assert 'ignite' not in set(st)

    df = _panel(objs=('IDX',))
    df['up_count'] = None
    df['down_count'] = None
    t2 = ig.terms(df, p)
    assert t2['breadth'].isna().all()
    assert int(t2['n_terms'].max()) == len(ig.TERMS) - 1
    assert 'ignite' not in set(ig.stages(t2, p))


def test_score_ignores_missing_terms_instead_of_scoring_them_zero():
    p = ig.params()
    df = _panel(objs=('IDX',))
    df['up_count'] = None
    df['down_count'] = None
    t = ig.terms(df, p)
    row = t.dropna(subset=['ignition', 'volume']).iloc[-1]
    w = {k: float(p[f'w_{k}']) for k in ('squeeze', 'pos', 'ignition', 'volume')}
    want = sum(float(row[k]) * v for k, v in w.items()) / sum(w.values())
    assert float(row['score']) == pytest.approx(want, abs=0.01)


def test_stage_values_stay_inside_the_check_constraint():
    """fct.ignition 上有 CHECK (stage IN ...)，算出别的值会在落库时才炸。"""
    df = _panel()
    st = ig.stages(ig.terms(df), ig.params())
    assert set(st) <= set(ig.STAGES)


def test_detail_json_is_serialisable_and_skips_nulls():
    df = _panel()
    t = ig.terms(df)
    d = json.loads(ig._detail(t.iloc[-1]))
    assert d['heat_pctile'] >= 0
    assert isinstance(d['turned_up'], bool)
    empty = json.loads(ig._detail(t.iloc[0]))   # 首日几乎全空
    assert 'slope3' not in empty


# ------------------------------------------------------------ 注册表与库
def test_seed_keeps_params_as_override_only():
    """
    `05_seed.sql` 里 `ignition.params` 必须是**空覆盖 + DO NOTHING**：
    抄一份完整默认值进库，之后改代码里的 DEFAULTS 就再也生效不了
    （库里那份一直赢，表现成"改了没反应"）；用 DO UPDATE 则会把人调过的
    参数在重跑 seed 时拍回去。
    """
    sql = Path('app/db/sql/05_seed.sql').read_text(encoding='utf-8')
    i = sql.index("('ignition.params'")
    seg = sql[i:i + 400]
    assert "'{}'" in seg.split('ON CONFLICT')[0]
    assert 'DO NOTHING' in seg


@pytest.fixture(scope='module')
def real():
    """真实面板（2024-06 起，给 120 日分位留足暖机），只读。"""
    df = ig.load_sector(dt.date(2024, 6, 1))
    if df.empty:
        pytest.skip('fct.sector_heat_daily 里还没有 src=sector 的行')
    p = ig.params()
    t = ig.terms(df, p)
    return df, t, ig.stages(t, p)


def test_ignite_beats_the_none_control_out_of_sample(real):
    """
    **自证**：ignite 的超额必须明显好于 `none` 对照组，否则这五个要素是白算的。

    样本外（2025-07 起）实测 ignite n=196 / 胜率 57.5 / T+5 超额 +0.455、
    T+10 +1.008，对照组 +0.074 / +0.147。这里放宽到"差值 > 0.2 且胜率 > 52"，
    留出重算热度、板块宇宙微调带来的抖动空间。
    """
    df, t, st = real
    dates = df.index.get_level_values('trade_date')
    oos = dates >= dt.date(2025, 7, 1)
    rows = {r['stage']: r for r in ig.backtest(df[oos], t[oos], st[oos])}
    assert 'ignite' in rows, '样本外一条 ignite 都没有 —— 阈值或数据出问题了'
    a, b = rows['ignite'], rows['none']
    assert a['n'] >= 100, f"只有 {a['n']} 条 ignite，统计说明不了问题"
    assert a['win5'] > 52.0, f"胜率 {a['win5']}"
    assert a['exc5'] - b['exc5'] > 0.2, f"ignite {a['exc5']} vs none {b['exc5']}"
    assert a['exc10'] - b['exc10'] > 0.3


def test_index_like_sectors_are_excluded_from_the_universe(real):
    """
    面板必须已经把 46 个坏板块挡在外面（次日涨幅四千多倍那批）。
    这里用 pct_chg 的极值当体检 —— 涨跌停之外的值就是数据错，不是行情。
    """
    df, _t, _st = real
    pct = pd.to_numeric(df['pct_chg'], errors='coerce').abs()
    assert float(pct.max()) <= 21.0, f'最大单日涨跌 {pct.max()}'
