# -*- coding: utf-8 -*-
"""「首板分析」（`analytics.first_board_daily`，fbd-1）的判据、契约与一条真库不变量。

这一屏回答四问：**今天哪些票首板**、**它近期什么走势**、**为什么涨停**、**前一天怎么走的**。
母体只有 fb-2 一把尺（`analytics.first_board_shape` 的当日行），本表不另起判据 —— 下面每一条测试
都对着一个"错了不报错、只给出一句听着合理的读数"的地方。

跑法：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_first_board_daily.py`
真库那一条要连着 PostgreSQL 跑（本机是唯一库）；不连库时它会红，不许改成跳过。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.compute import first_board_daily as F        # noqa: E402

DDL = ROOT / 'app' / 'db' / 'sql' / '49_first_board_daily.sql'


def _base(**over) -> dict:
    """一条最小可用的母体行（当日 34 只里随便一只的样子）。"""
    row = {'security_code': '000518.SZ', 'launch_pct': 10.0, 'launch_turnover': 6.6364,
           'launch_amount_yuan': 242426176.0, 'launch_is_1word': False, 'pre5': 0.8563,
           'dbl_days': 1, 'calm_days': 3, 'seg_len': 1, 'launch_close': 3.63,
           'limit_up_price': 3.63, 'universe': 50}
    row.update(over)
    return row


def _prev(**over) -> dict:
    item = {'prev_date': dt.date(2026, 10, 20), 'prev_pct': 2.4845, 'prev_turnover': 1.6481,
            'prev_amount_yuan': 55600696.0, 'prev_vol_ratio': 0.8301,
            'prev_touched': False, 'prev_sealed': False, 'gap': None}
    item.update(over)
    return item


def _src(**over) -> dict:
    src = {'pool': {'000518': {'code': '000518', 'first_limit_up_time': '1792547172',
                               'last_limit_up_time': '1792565817', 'open_num': 2,
                               'order_amount': 2401971.0, 'order_volume': 661700.0,
                               'limit_up_type': '换手板', 'high_days': '首板',
                               'currency_value': 3737214600.0}},
           'reasons': {'000518': '长源并表+生物医药+中润收购'}, 'captured_at': None,
           'source': F.REASON_SOURCE_POOL, 'pool_gap': None, 'reason_gap': None, 'note': None}
    src.update(over)
    return src


def _hist(**over) -> dict:
    """一只票的四列历史位置读数（10-27 那四列）：默认"跑得通、什么都不缺"那个样子。"""
    item = {c: None for c in F.HIST_COLS}
    item.update({'lu_sealed_3m': 3, 'lu_touched_3m': 5, 'ld_sealed_3m': 0, 'ld_touched_3m': 1,
                 'max_consec_6m': 2, 'close_adj': 12.4, 'hi_2y': 24.8, 'lo_2y': 4.4,
                 'pos_2y': 40.0, 'bars_2y': 489,
                 'ma5': 11.9, 'ma10': 11.4, 'ma20': 10.8, 'ma60': 9.9,
                 'ma_stack': 'bull_strong', 'gap': {}})
    item.update(over)
    return item


def _one_day(base=None, prev=None, boards=None, chg=None, energy=None, src=None, hist=None):
    """ assemble 一把梭：只给必需的五路读数，返回那一行。"""
    code = (base or _base())['security_code']
    day = dt.date(2026, 10, 21)
    rows = F.assemble(day, [base or _base()],
                      {code: prev or _prev()},
                      boards if boards is not None else {code[:6]: {'days': 1, 'boards': 1,
                                                                    'label': '1天1板'}},
                      chg if chg is not None else {s: {code: {'chg_span': 4.91}}
                                                  for s in F.CHG_SPANS},
                      energy if energy is not None else {code: {'dbl_energy': 2.44,
                                                                'energy_fp': 'ev-x'}},
                      src if src is not None else _src(),
                      hist if hist is not None else {code: _hist()})
    assert len(rows) == 1
    return rows[0]


# ================================================== 一、前一日形状：七档与优先级

@pytest.mark.parametrize('pct, vol, touched, sealed, want', [
    # ① 触板炸开：只要昨天冲过板又没封住，涨幅是多少都不改这一档
    (None, None, True, False, 'broke_at_limit'),
    (-6.0, 0.20, True, False, 'broke_at_limit'),        # 比 deep_pullback 优先
    (9.9, 3.00, True, False, 'broke_at_limit'),         # 比 surge_unsealed 优先
    # ② 深水区回撤（阈值 −5.0，边界含）
    (-5.0, 0.20, False, False, 'deep_pullback'),
    (-5.01, 3.00, False, False, 'deep_pullback'),
    (-4.99, 0.20, False, False, 'pullback_shrink'),     # 差一分就不算深水区，但仍是回撤缩量
    # ③ 已大涨未封（阈值 +3.5，边界含；不依赖量比）
    (3.5, 9.00, False, False, 'surge_unsealed'),
    (3.5, None, False, False, 'surge_unsealed'),        # 缺量比也照样落这一档
    (3.49, 0.30, False, False, 'mild_up'),             # 差一分不算已大涨，超出平盘带 → 温和上行
    # ④ 平盘缩量：|涨幅| ≤ 2.0 **且** 量比 ≤ 0.77
    (2.0, 0.77, False, False, 'quiet_shrink'),
    (-2.0, 0.77, False, False, 'quiet_shrink'),
    (2.01, 0.77, False, False, 'mild_up'),              # 涨幅差一分就不算平盘
    (2.0, 0.78, False, False, 'mild_up'),               # 量比差一分就不算缩量
    # ⑤ 回撤缩量：跌幅超过平盘带、量比仍缩
    (-2.5, 0.70, False, False, 'pullback_shrink'),
    (-2.5, 0.78, False, False, 'other'),                # 没缩到 0.77 就不算洗盘
    # ⑥ 温和放量上行：涨、且量比 < 1.40（上界不含）
    (0.5, 1.39, False, False, 'mild_up'),
    (3.0, 1.00, False, False, 'mild_up'),
    (0.5, 1.40, False, False, 'other'),                 # 到 1.40 就是放量抢跑
    # ⑦ 其余
    (-3.0, 3.00, False, False, 'other'),
    (1.0, 5.00, False, False, 'other'),
    (0.0, 1.00, False, False, 'other'),                 # 平盘但没缩量：谁也不像
])
def test_前一日形状七档与每一条边界(pct, vol, touched, sealed, want):
    assert F.prev_shape_of(pct, vol, touched, sealed) == want


@pytest.mark.parametrize('pct, vol, touched, sealed', [
    (2.0, 0.5, None, False),          # 触板判不了 ⇒ 第一档排除不掉
    (2.0, 0.5, False, None),          # 封住判不了 ⇒ 同上
    (None, 0.5, False, False),        # 前一日涨幅缺 ⇒ 二档往后全落不下去
    (-1.0, None, False, False),       # 量比缺、且 1/2/3 档都不成立 ⇒ 不许往下猜
    (2.5, None, False, False),        # 同上（平盘带里但缺量比）
    (-4.9, None, False, False),       # 差一分不算深水区，又缺量比 ⇒ 判不了
])
def test_输入缺时交空而不是硬猜(pct, vol, touched, sealed):
    assert F.prev_shape_of(pct, vol, touched, sealed) is None
    assert F.prev_shape_blocker(pct, vol, touched, sealed) == F.GAP_SHAPE_INPUT


def test_前一日封着板的行不落成任何一档():
    """首板定义：前一天也封着就不是连板段的第一天。这种行是母体串了，不是"某一档"。"""
    assert F.prev_shape_of(1.0, 1.0, True, True) is None
    assert F.prev_shape_blocker(1.0, 1.0, True, True) == 'prev_sealed_true_not_first_board'


def test_缺量比时高优先档照样能落_低优先档整条不让():
    """深水区/已大涨不依赖量比 ⇒ 照落；平盘缩量那一档依赖量比 ⇒ 它后面的都不许落。"""
    assert F.prev_shape_of(-6.0, None, False, False) == 'deep_pullback'
    assert F.prev_shape_of(4.0, None, False, False) == 'surge_unsealed'
    assert F.prev_shape_of(-0.5, None, False, False) is None


def test_阈值只住一处_改数字边界就跟着动():
    """判据与阈值只有 `PREV_SHAPE_THRESHOLDS` 这一份：把某个阈值挪一格，边界必须跟着挪。

    如果哪一档把数字抄进了 if 里，这条测试就会红 —— 那正是"两处各写一遍、飘出第二个数"的入口。
    """
    keep = dict(F.PREV_SHAPE_THRESHOLDS)
    try:
        F.PREV_SHAPE_THRESHOLDS['deep_pullback_pct'] = -3.0
        assert F.prev_shape_of(-3.5, 0.5, False, False) == 'deep_pullback'
        F.PREV_SHAPE_THRESHOLDS['shrink_vol_ratio'] = 0.95
        assert F.prev_shape_of(0.5, 0.90, False, False) == 'quiet_shrink'
        F.PREV_SHAPE_THRESHOLDS['mild_up_vol_ratio'] = 2.00
        assert F.prev_shape_of(0.5, 1.60, False, False) == 'mild_up'
    finally:
        F.PREV_SHAPE_THRESHOLDS.clear()
        F.PREV_SHAPE_THRESHOLDS.update(keep)


def test_判档顺序就是判据本身():
    assert F.SHAPE_PRIORITY == ('broke_at_limit', 'deep_pullback', 'surge_unsealed',
                                'quiet_shrink', 'pullback_shrink', 'mild_up', 'other')
    assert set(F.SHAPE_LABELS) == set(F.SHAPE_PRIORITY)


@pytest.mark.parametrize('shape', list(F.SHAPE_PRIORITY))
def test_每一档都有一句人话且不夹带屏幕不该有的符号(shape):
    note = F.shape_note_of(shape)
    assert note and note == F.SHAPE_LABELS[shape]
    assert '"' not in note and '*' not in note, '屏幕上念的串要用「」，不嵌 ASCII 双引号、不带 markdown 星号'
    assert "'" not in note


def test_形状为空时那句说明也为空():
    """屏幕不能出现「没有档位却有一句解释」—— 那等于把判不了说成了一种形态。"""
    assert F.shape_note_of(None) is None
    row = _one_day(prev=_prev(prev_vol_ratio=None))
    assert row['prev_shape'] is None and row['prev_shape_note'] is None
    assert row['missing']['prev_shape'] == F.GAP_SHAPE_INPUT


# ================================================== 二、几天几板：源站文本只用来对账

@pytest.mark.parametrize('text, want', [
    ('2天2板', (2, 2)), ('5天3板', (5, 3)), (' 9 天 7 板 ', (9, 7)),
    ('首板', (1, 1)),                       # 实测源站对第一次封板写的是「首板」而不是「1天1板」
    ('1天1板', (1, 1)),
    (None, None), ('', None), ('   ', None), ('-', None), ('暂无', None),
    ('2板', None), ('3天', None), ('首板 2天2板', None), ('0天0板', (0, 0)),
])
def test_解析源站几天几板(text, want):
    assert F.parse_high_days(text) == want


@pytest.mark.parametrize('text, want', [('2天2板', 2), ('首板', 1), ('看不懂', None), (None, None)])
def test_板数只从文本里拆出来一个数(text, want):
    assert F.source_boards_of(text) == want


@pytest.mark.parametrize('local, text, want', [
    (1, '首板', True), (2, '2天2板', True), (1, '3天2板', False), (2, '首板', False),
    (None, '2天2板', None),       # 本地判不了 ⇒ 这格判不了，**不是 false**
    (2, None, None),              # 源站没这只 ⇒ 同上
    (2, '看不懂', None),
])
def test_板数一致判定缺任一边都交空(local, text, want):
    assert F.boards_agree(local, text) is want


def test_源站文本绝不覆盖本地boards():
    """源站写「首板」而本地数出 2 板（断了 1 天又封上）时：boards 留 2、agree 记 False、文本原样留着。"""
    row = _one_day(boards={'000518': {'days': 3, 'boards': 2, 'label': '3天2板'}})
    assert row['boards'] == 2 and row['board_label'] == '3天2板'
    assert row['source_high_days'] == '首板' and row['source_boards_agree'] is False


# ================================================== 三、源站缺数：NULL 而不是 0、也不是空串

def test_整场漏采时源站那一族逐列点名_而且不是空串():
    row = _one_day(src=_src(pool={}, reasons={}, captured_at=None, source=None,
                            pool_gap=F.GAP_SOURCE_NO_RECORDS,
                            reason_gap=F.GAP_SOURCE_NO_RECORDS))
    assert row['reason'] is None and row['first_seal_at'] is None and row['float_cap_yuan'] is None
    assert row['break_count'] is None, '漏采不能写成 0 次炸板'
    assert row['reason_source'] is None and row['reason_captured_at'] is None
    for col in F.SOURCE_COLS:
        assert row['missing'][col] == F.GAP_SOURCE_NO_RECORDS, col


def test_池子里没这只票时说清是源站不收():
    """北交所/ST 的那一批（实测 40 场 118 只）与漏采是两句话。"""
    other = {'code': '920099', 'first_limit_up_time': '1792547172'}
    row = _one_day(src=_src(pool={'920099': other}, reasons={}, captured_at=None, source=None,
                            reason_gap=F.GAP_SOURCE_ROW))
    assert row['reason'] is None and row['missing']['reason'] == F.GAP_SOURCE_ROW
    assert row['first_seal_at'] is None and row['missing']['first_seal_at'] == F.GAP_SOURCE_ROW
    assert row['missing']['float_cap_yuan'] == F.GAP_SOURCE_ROW


def test_池里有这只但某一格没给时只点那一格():
    raw = {'code': '000518', 'first_limit_up_time': '1792547172', 'open_num': None,
           'order_amount': 1.0, 'order_volume': 2.0, 'limit_up_type': '换手板',
           'high_days': '首板', 'currency_value': 3.0}
    row = _one_day(src=_src(pool={'000518': raw}))
    assert row['break_count'] is None and row['missing']['break_count'] == 'source_field_null'
    assert row['last_seal_at'] is None and row['missing']['last_seal_at'] == 'source_field_null'
    assert row['first_seal_at'] is not None and 'first_seal_at' not in row['missing']


def test_封板时间戳落在别的日子时不敢用_并点名():
    """日期只是请求参数、不是证据：源站给过别日的行，那一格就整格作废（NULL + 点名）。"""
    day = dt.date(2026, 10, 21)
    other = int(dt.datetime(2026, 10, 16, 1, 35, tzinfo=dt.timezone.utc).timestamp())
    miss: dict = {}
    assert F._seal_clock(str(other), day, miss, 'first_seal_at') is None
    assert miss['first_seal_at'] == F.GAP_SOURCE_DATE
    ok = F._seal_clock('1792547172', day, {}, 'first_seal_at')     # 2026-10-21 09:46:12 CST
    assert ok is not None and ok.hour == 9 and ok.minute == 46, '钟表时间按 Asia/Shanghai 折算'


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _pool_env(rows, total=None):
    return {'status_code': 0, 'data': {'page': {'total': total if total is not None else len(rows)},
                                       'info': rows}}


def _fake_get(monkeypatch, envs):
    def fake(url, **kw):
        return _Resp(envs[int(kw['params']['page']) - 1])
    monkeypatch.setattr(F.fetchers, '_direct_get', fake)


def test_池子里有一行是别的日子就整场判为不可用(monkeypatch):
    """"匹配不到就放行"是最需要拦的时候恰好不拦 —— 别场的题材串会塞进这一场。"""
    day = dt.date(2026, 10, 21)
    rows = [{'code': '000518', 'first_limit_up_time': '1792547172'},
            {'code': '600000', 'first_limit_up_time': '1792455228'}]      # 那是 10-20
    _fake_get(monkeypatch, [_pool_env(rows)])
    with pytest.raises(F.SourceUnavailable) as e:
        F.fetch_limit_up_pool(day)
    assert e.value.code == F.GAP_SOURCE_DATE


def test_池子整场空着说成没有记录而不是空名单(monkeypatch):
    _fake_get(monkeypatch, [_pool_env([])])
    with pytest.raises(F.SourceUnavailable) as e:
        F.fetch_limit_up_pool(dt.date(2026, 10, 21))
    assert e.value.code == F.GAP_SOURCE_NO_RECORDS


def test_翻页数对不上合计就不交半张名单(monkeypatch):
    """半张名单在界面上与"这些票源站没收"长得一模一样，会讲出一句假话。"""
    rows = [{'code': '000518', 'first_limit_up_time': '1792547172'}]
    _fake_get(monkeypatch, [_pool_env(rows, total=9), _pool_env([], total=9)])
    with pytest.raises(F.SourceUnavailable) as e:
        F.fetch_limit_up_pool(dt.date(2026, 10, 21))
    assert e.value.code == F.GAP_SOURCE_PAGING


def test_同一场里的重复代码是名单坏了而不是数据多(monkeypatch):
    rows = [{'code': '000518', 'first_limit_up_time': '1792547172'},
            {'code': '000518', 'first_limit_up_time': '1792547172'}]
    _fake_get(monkeypatch, [_pool_env(rows)])
    with pytest.raises(F.SourceUnavailable) as e:
        F.fetch_limit_up_pool(dt.date(2026, 10, 21))
    assert e.value.code == F.GAP_SOURCE_PAGING


def test_时间戳无效也是出声_不是当成没有():
    with pytest.raises(F.SourceUnavailable) as e:
        F.stamp_to_clock('not-a-stamp', dt.date(2026, 10, 21), dt.timezone.utc)
    assert e.value.code == F.GAP_SOURCE_DATE


def test_源站异常之外的问题不许被兜成空值(monkeypatch):
    """`SourceUnavailable` 才转 NULL；别的异常照旧往外抛 —— 否则代码坏了屏幕会说"源站过期"。"""
    def boom(*_a, **_k):
        raise KeyError('info')
    monkeypatch.setattr(F.fetchers, '_direct_get', boom)
    with pytest.raises(KeyError):
        F.fetch_limit_up_pool(dt.date(2026, 10, 21))


# ================================================== 四、母体逐字搬运与 NULL 纪律

def test_当日读数逐字搬母体_母体空着就不补零():
    row = _one_day(base=_base(launch_pct=None, pre5=None, dbl_days=None, calm_days=None,
                              launch_close=None, limit_up_price=None))
    assert row['launch_pct'] is None and row['vol_grab_ratio'] is None
    assert row['dbl_days'] is None and row['calm_days'] is None
    assert row['missing']['launch_pct'] == 'shape_row_null'
    assert row['missing']['vol_grab_ratio'] == 'shape_row_null'
    assert row['missing']['launch_close'] == F.GAP_NO_DAILY_ROW
    assert row['missing']['dbl_days'] == 'shape_row_null'


def test_量能抢跑比是母体的pre5_不是涨幅():
    row = _one_day(base=_base(pre5=1.8312))
    assert row['vol_grab_ratio'] == pytest.approx(1.8312)
    assert row['launch_pct'] == pytest.approx(10.0)


def test_分母随行交出一行():
    row = _one_day(base=_base(universe=68))
    assert row['universe'] == 68
    assert row['missing'] == {}, '一样都不缺时 missing 是空对象而不是 NULL'


def test_整批读回空要说成读不到_不是这批都没板():
    """`board_span` / `day_returns` 交回空 dict 与"这只票算不出"是两件事（它们的文档都这么要求）。"""
    row = _one_day(boards={}, chg={s: {} for s in F.CHG_SPANS})
    assert row['boards'] is None and row['missing']['boards'] == F.GAP_BOARD_SOURCE
    assert row['chg5'] is None and row['missing']['chg5'] == F.GAP_CHG_SOURCE
    assert row['source_boards_agree'] is None


def test_前一日没有日线行时说清是哪一列缺():
    row = _one_day(prev={'prev_date': dt.date(2026, 10, 20), 'gap': F.GAP_NO_PREV_ROW})
    assert row['prev_pct'] is None and row['prev_sealed'] is None
    assert row['missing']['prev_pct'] == F.GAP_NO_PREV_ROW
    assert row['missing']['prev_vol_ratio'] == F.GAP_NO_PREV_ROW
    assert row['missing']['prev_shape'] == F.GAP_SHAPE_INPUT


def test_倍量加权分与指纹同进同退():
    row = _one_day(energy={})
    assert row['dbl_energy'] is None and row['energy_fp'] is None
    assert row['missing']['dbl_energy'] == F.GAP_ENERGY_ROW
    assert row['missing']['energy_fp'] == F.GAP_ENERGY_ROW


def test_写库的每一列都在列清单里_占位符与列数同一处生成():
    assert len(F.COLS) == len(set(F.COLS)) == F.INSERT_SQL.count('%(')
    assert F.INSERT_SQL.count('::jsonb') == len(F._CASTS)
    assert 'missing' in F._CASTS, 'jsonb 不显式 cast 会被 psycopg 当 text 打回'


def test_列清单与DDL里的列一一对应():
    """两份都要动的地方最容易只动一份：DDL 与 COLS 必须同名同集（`calculated_at` 由默认值自己填）。

    ⚠️ 列有两个来源：建表体里的列，与后来用 `alter table … add column if not exists` 补的列
    （10-27 那四列历史位置走的就是后者）。只扫建表体会把"补了列没进 COLS"看成通过。
    """
    ddl = DDL.read_text(encoding='utf-8')
    body = ddl[ddl.index('create table'):ddl.index('create index')]
    defined = {m.group(1) for line in body.splitlines()
               if (m := re.match(r'\s*([a-z_][a-z0-9_]*)\s+'
                                 r'(text|date|boolean|numeric|smallint|int|bigint|jsonb|time'
                                 r'|timestamptz)\b', line))}
    defined |= {m.group(1) for m in re.finditer(
        r'alter table \S+ add column if not exists\s+([a-z_][a-z0-9_]*)', ddl)}
    assert set(F.COLS) | {'calculated_at'} == defined, sorted(
        set(F.COLS) ^ (defined - {'calculated_at'}))


def test_四列历史位置的列名在两处同名():
    """写侧 `HIST_COLS` 是加列时唯一要改的一处；漏了 COLS 会静默不落库（列存在但永远 NULL）。"""
    assert set(F.HIST_COLS) <= set(F.COLS)
    for col in F.HIST_COLS:
        assert re.search(rf'\b{col}\b', DDL.read_text(encoding='utf-8')), f'{col} 没进 DDL'


def test_重算一场先清这一场_空批也要清():
    src = (ROOT / 'app' / 'compute' / 'first_board_daily.py').read_text(encoding='utf-8')
    for seg in ('delete from {TABLE} where trade_date = %s and spec_version = %s',
                'def clear_day(day: dt.date) -> int:',
                'cleared = clear_day(day)'):
        assert seg in src, f'少了「{seg}」：只 upsert 不清场会留下上一次半截计算的残留行'


def test_写库走_etl_角色_读母体走_reader():
    src = (ROOT / 'app' / 'compute' / 'first_board_daily.py').read_text(encoding='utf-8')
    assert "role='etl'" in src and "role='reader'" in src
    assert "role='app'" not in src, '加工链统一 etl 角色，别混进业务写角色'


def test_母体版本号只写一处():
    src = (ROOT / 'app' / 'compute' / 'first_board_daily.py').read_text(encoding='utf-8')
    body = src.split('"""', 2)[2]              # 模块 docstring 里那句是口径说明，不算引用
    assert body.count("'fb-2'") == 1, 'fb-2 这个号漂在多处，升版时必漏一处'
    assert F.SHAPE_SPEC == 'fb-2' and F.SPEC_VERSION == 'fbd-2'
    # 本表自己的版本号也不许漂进第二处（读侧 `duanxian/first_board_daily.py` 是 import 它，不是抄）
    assert body.count("'fbd-2'") == 1, 'fbd-2 写了两处，升版时会漏'
    view = (ROOT / 'duanxian' / 'first_board_daily.py').read_text(encoding='utf-8')
    assert "'fbd-" not in view, '读侧抄了一份版本号 —— 写侧升号它不会跟着走'


# ================================================== 四点五、四列历史位置（10-27 新增，纯判据）

DAY0 = dt.date(2026, 10, 21)


def _bars(n, close=10.0, high=None, low=None, lu=None, ld=None, factor=1.0, start=DAY0):
    """造 n 根升序日线（最后一根＝当日）。默认给一条"什么都摸不到板"的平线。"""
    out = []
    for i in range(n):
        c = close if isinstance(close, (int, float)) else close(i)
        out.append({'trade_date': start + dt.timedelta(days=i), 'close': c,
                    'high': c * 1.01 if high is None else high,
                    'low': c * 0.99 if low is None else low,
                    'limit_up': c * 1.1 if lu is None else lu,
                    'limit_down': c * 0.9 if ld is None else ld,
                    'adjustment_factor': factor})
    return out


def test_涨跌停次数封住与触及两把尺分开数():
    """只留一个数就会把"冲板没封住"那批藏掉（本机实测同一场 触板 103 对 封住 84）。"""
    bars = _bars(3)
    bars[0]['high'] = bars[0]['limit_up']            # 摸到涨停、收盘没封住
    bars[1]['close'] = bars[1]['high'] = bars[1]['limit_up']   # 封住涨停（封住必然也触及）
    bars[2]['low'] = bars[2]['limit_down']           # 摸到跌停、收盘打开
    cols, gap = F.limit_counts(bars, bars[0]['trade_date'])
    assert gap == {}
    assert (cols['lu_sealed_3m'], cols['lu_touched_3m']) == (1, 2)
    assert (cols['ld_sealed_3m'], cols['ld_touched_3m']) == (0, 1)


def test_窗口里一根行都没有时四列都判不了并且各自点名():
    cols, gap = F.limit_counts(_bars(2), DAY0 + dt.timedelta(days=30))
    assert all(v is None for v in cols.values())
    assert set(gap) == {'lu_sealed_3m', 'lu_touched_3m', 'ld_sealed_3m', 'ld_touched_3m'}
    assert set(gap.values()) == {F.GAP_HIST_ROWS}


def test_缺档位价那一天宁可不报也不给一个偏小的下界():
    bars = _bars(3)
    bars[1]['limit_up'] = None
    cols, gap = F.limit_counts(bars, bars[0]['trade_date'])
    assert cols['lu_sealed_3m'] is None and cols['lu_touched_3m'] is None
    assert gap['lu_sealed_3m'] == F.GAP_HIST_LIMIT_UP
    assert cols['ld_sealed_3m'] == 0                  # 跌停那一族不受它影响，照报


def test_两年位置是区间里的位置而不是涨幅():
    bars = _bars(300, close=10.0)
    bars[-1]['close'] = 15.0
    bars[-1]['high'] = 15.0
    bars[150]['low'] = 5.0
    bars[200]['high'] = 25.0
    cols, gap = F.position_2y(bars, bars[0]['trade_date'])
    assert gap == {} and cols['bars_2y'] == 300
    assert (cols['lo_2y'], cols['hi_2y']) == (5.0, 25.0)
    assert cols['pos_2y'] == pytest.approx(50.0)      # (15−5)/(25−5)


def test_两年位置用复权价除权日不造假顶():
    """未复权价在 10 送 10 那天腰斩，按原值算会把历史顶算歪、位置虚高。

    这里给一条"未复权 20 元、因子 0.5"的平线 ⇒ 复权后是 10 元的平线，区间上下沿重合，
    位置**没有定义**：宁缺不滥，既不报 0 也不报 100。
    """
    bars = _bars(250, close=20.0, high=20.0, low=20.0, factor=0.5)
    cols, gap = F.position_2y(bars, bars[0]['trade_date'])
    assert cols['close_adj'] == pytest.approx(10.0)
    assert cols['hi_2y'] == pytest.approx(10.0) and cols['lo_2y'] == pytest.approx(10.0)
    assert cols['pos_2y'] is None and gap['pos_2y'] == 'hist_range_flat'
    # 同一批线只要真的张开一个区间，位置就按复权价算（现价 11 元、顶 15、底 5 ⇒ 60%）
    bars2 = _bars(250, close=20.0, high=20.0, low=20.0, factor=0.5)
    bars2[10]['high'] = 30.0
    bars2[20]['low'] = 10.0
    bars2[-1]['close'] = 22.0
    cols2, gap2 = F.position_2y(bars2, bars2[0]['trade_date'])
    assert gap2 == {} and cols2['pos_2y'] == pytest.approx(60.0)   # (11−5)/(15−5)


def test_两年窗口根数不够时说判不了():
    cols, gap = F.position_2y(_bars(120), DAY0)
    assert cols['pos_2y'] is None and cols['bars_2y'] == 120
    assert gap['pos_2y'] == F.GAP_POS_BARS


def _ma_bars(m5, m10, m20, m60):
    """造 60 根收盘价，使 MA5/MA10/MA20/MA60 **精确**等于这四个目标值。

    分四段常数解出来：MA5 只看最后 5 根；MA10 把倒数 6~10 根配平；MA20 配 11~20 根；
    MA60 配最前 40 根。这样"档位怎么变"只由四条线的相对次序决定，不由取整误差决定。
    """
    a = m5
    b = 2 * m10 - a
    c = (20 * m20 - 5 * a - 5 * b) / 10
    d = (60 * m60 - 5 * a - 5 * b - 10 * c) / 40
    vals = [d] * 40 + [c] * 10 + [b] * 5 + [a] * 5
    return [{'trade_date': DAY0 + dt.timedelta(days=i), 'close': v, 'high': v, 'low': v,
             'limit_up': None, 'limit_down': None, 'adjustment_factor': None}
            for i, v in enumerate(vals)]


def test_均线排列五档与判档顺序():
    """判档顺序就是判据：先认两端（短在上＝多头／短在下＝空头），再看季线在哪一侧，最后才落到纠缠。

    ⚠️ 参数顺序是 (MA5, MA10, MA20, MA60)：多头排列＝**短的那条在最上面**（12>11>10>9），
    写成 9>10>11>12 那是空头。这一条把自己绕进去过一次，所以五个断言都点名写数值。
    """
    def stack(m5, m10, m20, m60):
        cols, gap = F.ma_stack(_ma_bars(m5, m10, m20, m60))
        assert gap == {} and (cols['ma5'], cols['ma10'], cols['ma20'], cols['ma60']) == (
            pytest.approx(m5), pytest.approx(m10), pytest.approx(m20), pytest.approx(m60))
        return cols['ma_stack']

    assert stack(12.0, 11.0, 10.0, 9.0) == 'bull_strong'        # 短在上、季线在最下：四线多头
    assert stack(9.0, 10.0, 11.0, 12.0) == 'bear_strong'        # 短在下、季线在最上：四线空头
    assert stack(12.0, 11.0, 10.0, 14.0) == 'bull_under_ma60'   # 短多、季线压在上方
    assert stack(9.0, 10.0, 11.0, 8.0) == 'pullback_ma60'       # 短空、季线在下方托着
    assert stack(11.0, 9.0, 12.0, 10.0) == 'tangled'            # 有交叉
    # 同一组短期线，只把季线从 9 抬到 14 ⇒ 档位必须变（判序写反了这两格会念成同一句话）
    assert stack(12.0, 11.0, 10.0, 9.0) != stack(12.0, 11.0, 10.0, 14.0)


def test_不足六十根时均线整档判不了不拿三档冒充():
    bars = [{'trade_date': DAY0 + dt.timedelta(days=i), 'close': 10.0, 'high': 10.0,
             'low': 10.0, 'limit_up': None, 'limit_down': None, 'adjustment_factor': None}
            for i in range(30)]
    cols, gap = F.ma_stack(bars)
    assert cols['ma5'] == 10.0 and cols['ma20'] == 10.0 and cols['ma60'] is None
    assert cols['ma_stack'] is None and gap['ma_stack'] == F.GAP_MA_BARS


def test_这一路没跑时四列全空并单独点名():
    """与"跑了但这只票缺数"分开：前者是加工段没接上，后者是这只票的日线不够。"""
    row = _one_day(hist={})
    assert row['lu_sealed_3m'] is None and row['ma_stack'] is None
    assert {row['missing'][c] for c in F.HIST_COLS} == {'hist_readings_absent'}
    row2 = _one_day(hist={_base()['security_code']: _hist(gap={'pos_2y': F.GAP_POS_BARS})})
    assert row2['missing']['pos_2y'] == F.GAP_POS_BARS
    assert 'lu_sealed_3m' not in row2['missing']       # 别的列照实交，不连坐


# ================================================== 五、真库（不打桩、不跳过）

def test_真库_最新一场行数与母体一致_且全表没有一行前日封住():
    if not F.engine.scalar('select 1'):
        pytest.fail('库不通：这一条是真库不变量，不许兜成跳过')
    day = F.engine.scalar(
        f"select max(trade_date) from {F.TABLE} where spec_version = %s", (F.SPEC_VERSION,))
    assert day is not None, f'{F.TABLE} 一行都没有（回填没跑？）'
    mine = F.engine.scalar(
        f"select count(*) from {F.TABLE} where spec_version = %s and trade_date = %s",
        (F.SPEC_VERSION, day))
    mother = F.engine.scalar(
        "select count(*) from analytics.first_board_shape "
        "where spec_version = 'fb-2' and launch_date = %s", (day,))
    assert mine == mother, f'{day}：母体 {mother} 只首板 vs 本表 {mine} 行'
    assert mine > 0, f'{day} 一行都没有，母体却判定有首板'

    sealed = F.engine.scalar(
        f"select count(*) from {F.TABLE} where spec_version = %s and prev_sealed",
        (F.SPEC_VERSION,))
    assert sealed == 0, f'{sealed} 行 prev_sealed=true —— 前一天也封着就不是连板段的第一天，母体串了'

    cols = {r['column_name'] for r in F.engine.query(
        "select column_name from information_schema.columns "
        "where table_schema='analytics' and table_name='first_board_daily'", role='reader')}
    assert set(F.COLS) <= cols
    assert cols - {'calculated_at'} == set(F.COLS)

    # 库里存的形状必须还是那七档 + 判不了，且散文字段没有漂进屏幕不该有的符号
    odd = F.engine.query(
        f"select distinct prev_shape from {F.TABLE} where spec_version = %s", (F.SPEC_VERSION,),
        role='reader') or []
    assert {o['prev_shape'] for o in odd} - {None} <= set(F.SHAPE_PRIORITY)
    bad = F.engine.query(
        f"select prev_shape_note from {F.TABLE} where spec_version = %s "
        f"and prev_shape_note is not null and (prev_shape_note like '%%\"%%' "
        f"or prev_shape_note like '%%*%%')", (F.SPEC_VERSION,), role='reader') or []
    assert not bad, '库里已有带 ASCII 双引号/星号的说明串'

    # missing 是对象、键是列名
    sample = F.engine.query(
        f"select missing from {F.TABLE} where spec_version = %s and missing <> '{{}}'::jsonb "
        f"limit 20", (F.SPEC_VERSION,), role='reader') or []
    for s in sample:
        m = s['missing'] if isinstance(s['missing'], dict) else json.loads(s['missing'])
        assert set(m) <= set(F.COLS), f'missing 里有非列名的键：{sorted(set(m) - set(F.COLS))}'
