# -*- coding: utf-8 -*-
"""「启动前爆量/急拉 × 量增/量缩（日线代理）」（`analytics.startup_spike_days`，ssd-1）的判据、契约与读侧不变量。

这一屏回答用户 10-18 追加的那一问：「前 30 个交易日里爆量/突然拉高，分别出现在**量增的天数还是
量减的天数**」。分钟线已取消 ⇒ 全部是日线代理读数。核心是**联合计数**：
`dbl_up_days + dbl_down_days = dbl_days` 这条自证判据两头都钉；`jump_days=0`（真没有）与
NULL（窗口不满）是两件事。下面每一条都对应一个「错了不报错、只给一句听着合理的读数」的地方。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_startup_spike_days.py -q`
真库那几条要连着 PostgreSQL 跑（本机是唯一库）；不连库时它们会红，**不许改成跳过**。
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.compute import startup_spike_days as S        # noqa: E402
from app.compute import startup_volume as SV           # noqa: E402
from duanxian import spike_days_view as V              # noqa: E402

DDL = ROOT / 'app' / 'db' / 'sql' / '44_startup_spike_days.sql'
CHAIN = ROOT / 'ingest' / 'jobs' / 'e1_fill_all.py'
COVERAGE = ROOT / 'duanxian' / 'coverage.py'
VR = ROOT / 'vr' / 'app.py'


def _flat(sql: str) -> str:
    return ' '.join(sql.split())


def _db_up() -> bool:
    try:
        return bool(S.engine.scalar('select 1'))
    except Exception:                                        # noqa: BLE001
        return False


# ============================================================ 一、纯判据（联合计数与 0/NULL 纪律）

def test_尺子常量引用_sv2_不抄数_classify_复用同一份():
    """vr 尺子（up/down/flat/still/dbl/near/calm）与阈值常量只许有一份实现 —— 两处除法迟早漂移，
    第三份尺子会把「倍量落在哪一边」数成另一个数。"""
    assert S.classify is SV.classify, 'vr 尺子必须复用 startup_volume.classify，不许再写第二份'
    assert S.DBL is SV.DBL and S.NEAR_DBL is SV.NEAR_DBL and S.CALM is SV.CALM
    from app.compute import limit_prices as LP
    assert S.LIMIT_EPS == LP.LIMIT_EPS, '涨停容差引用 limit_prices 的全项目唯一常量（半分），不许新写 0.01'
    src = pathlib.Path(S.__file__).read_text(encoding='utf-8')
    assert 'limit_up - 0.01' not in src, 'SQL 里又手写了一遍 0.01 容差'


def test_联合计数自洽_倍量日的涨跌相加等于倍量天数():
    bars = [(2.5, 0.09, True), (2.5, -0.03, None), (2.5, 0.0, None),
            (0.4, 0.02, True), (1.9, -0.01, None), (1.2, 0.05, None)]
    c = S.window_counts(bars, window_days=6)
    assert c['dbl_up_days'] + c['dbl_down_days'] == c['dbl_days'] == 3
    assert c['near_up_days'] + c['near_down_days'] == c['near_dbl_days'] == 1
    assert c['calm_up_days'] + c['calm_down_days'] == c['calm_days'] == 1
    # 涨＝收益 >0；跌＝<=0（平盘归跌那一头）——0.000 倍量日进了 down
    assert c['dbl_up_days'] == 1 and c['dbl_down_days'] == 2
    assert c['up_days'] == 5 and c['down_days'] == 1 and c['flat_days'] == 0
    assert c['limit_days'] == 2


def test_缺ret的那天不参与涨跌判定_缺vr的那天不进vr尺子():
    """ret 为 None 的倍量日既不算 up 也不算 down（此时联合相加会小于 dbl_days——真库里
    倍量日必有前一根，verify 数「例外」要求为 0）；vr 为 None 的日子不进 up/down/flat/still/dbl。"""
    bars = [(2.5, None, True), (2.5, 0.09, True)]
    c = S.window_counts(bars, window_days=2)
    assert c['dbl_days'] == 2 and c['dbl_up_days'] == 1 and c['dbl_down_days'] == 0
    bars = [(None, 0.09, False), (0.9, -0.02, False), (2.1, 0.03, False)]
    c = S.window_counts(bars, window_days=3)
    assert c['bars'] == 3, 'bars＝窗口拿到的场数（含缺 vr 的那场，收益判定还在用它）'
    assert c['up_days'] == 1 and c['down_days'] == 1 and c['flat_days'] == 0
    assert c['dbl_days'] == 1 and c['dbl_up_days'] == 1


def test_急拉阈值边界_等于阈值算拉高_下闭写死():
    """`ret >= JUMP_PCT` —— 正好 0.07 的那场**算**拉高。归哪一头必须写死并各有断言：
    两边各判一次（写侧 < 、读侧 >=）就会有日子两头都不算，屏幕上那句「一次都没拉高」是假话。"""
    assert S.window_counts([(1.5, S.JUMP_PCT, False)], window_days=1)['jump_days'] == 1
    assert S.window_counts([(1.5, round(S.JUMP_PCT - 0.0001, 6), False)], window_days=1)['jump_days'] == 0
    assert S.JUMP_PCT == 0.07 and V.JUMP_PCT == S.JUMP_PCT, '读写两侧的阈值必须同一个数'
    c = S.window_counts([(2.5, 0.08, True), (0.3, 0.09, False), (1.1, 0.20, False)],
                        window_days=3, jump_pct=0.05)
    assert c['jump_days'] == 3 and c['jump_dbl_days'] == 1 and c['jump_calm_days'] == 1
    assert c['jump_near_days'] == 0 and c['jump_limit_days'] == 1
    assert c['jump_dbl_days'] + c['jump_near_days'] + c['jump_calm_days'] <= c['jump_days'], \
        '差额是既非倍量也非缩量的普通量日子'


def test_满窗没有急拉是0_不满窗整个jump族是NULL不是0():
    """0 的意思是「凑满了 N 场、一次都没拉高」；不满窗没有资格说这句话 ⇒ jump 族整组 NULL。
    把不满窗的交 0，屏幕上「jump_days=0 的事件数」就成了「这些票 30 天没一次拉高」的假读数。"""
    full = S.window_counts([(1.2, 0.01, False)] * 5, window_days=5)
    assert full['jump_days'] == 0 and full['jump_up_days'] == 0 and full['jump_limit_days'] == 0
    assert full['first_jump_gap'] is None, '满窗但一次没拉高：天数是 0（真读数），间隔是 NULL（无从谈起）'
    short = S.window_counts([(1.2, 0.09, True)] * 3, window_days=5)
    assert short['bars'] == 3
    for k in ('jump_days', 'jump_up_days', 'jump_down_days', 'jump_dbl_days',
              'jump_near_days', 'jump_calm_days', 'jump_limit_days', 'first_jump_gap'):
        assert short[k] is None, f'{k} 不满窗必须是 None，实得 {short[k]}'
    # 正字数出来的事实不随不满窗变 NULL
    assert short['dbl_days'] == 0 and short['limit_days'] == 3


def test_first_gap_是距t_1几场_没有发生是NULL不是零():
    """`first_dbl_gap`：窗口内第一次倍量距 T-1 的场数（0＝就发生在启动日前一天）；一次都没有＝NULL。
    写成 0 会把「没有倍量」读成「倍量发生在启动日前一天」。"""
    bars = [(2.5, 0.01, False), (1.1, 0.01, False), (2.2, 0.01, False)]
    c = S.window_counts(bars, window_days=3)
    assert c['first_dbl_gap'] == 2, '第一次倍量是最早那场，距 T-1（最后一场）有 2 场'
    assert S.window_counts([(1.1, 0.01, False), (2.2, 0.01, False)], window_days=2)['first_dbl_gap'] == 0
    assert S.window_counts([(1.1, 0.01, False)], window_days=1)['first_dbl_gap'] is None
    assert S.window_counts([(1.1, 0.01, False)], window_days=1)['first_jump_gap'] is None


def test_空窗口不炸_all_zero_读数与缺数分得开():
    c = S.window_counts([], window_days=30)
    assert c['bars'] == 0 and c['dbl_days'] == 0 and c['up_days'] == 0
    assert c['jump_days'] is None, '空窗口连「没拉高」都没资格说'


# ============================================================ 二、母体与取数（判据在上游，这里只接线）

def test_anyfirst母体从fb2取全部首板_段长不当筛子():
    s = _flat(S.ANYFIRST_EVENTS_SQL)
    assert "f.spec_version = 'fb-2'" in s
    assert "'anyfirst'::text as cohort" in s
    assert 'seg_len >=' not in s and 'seg_len =' not in s, 'anyfirst 再把段长当筛子就把未来函数请回来了'
    assert 'f.launch_date between %(a)s::date and %(b)s::date' in s


def test_收益一律后复权重算_vr只读vr1不在这再除一遍():
    s = _flat(S.BARS_SQL)
    assert 'coalesce(q.adjustment_factor, 1)' in s, '不复权的当日涨跌在除权日必然算错（day_returns 那一课）'
    assert 'pct_change' not in s, '不许直接读 stock_daily.pct_change'
    assert "v.spec_version = 'vr-1'" in s
    assert 'volume_shares' not in s and '/' not in s.split('jsonb_agg')[0].replace('hfq / prev_hfq', ''), \
        '倍数只有一个来源（volume_ratio_daily）；这里再除一遍量就会和那一屏漂移'
    assert 'q.trade_date < k.launch' in s, '窗口不含启动日当天'
    assert 'where rn >= 2' in s, '多取的那一场只当首根收益的基价，不进窗口'


def test_窗口一场都凑不出的事件整行不写():
    src = pathlib.Path(S.__file__).read_text(encoding='utf-8')
    body = src[src.index('def collect('):src.index('COLS = (')]
    assert 'if not day_rows:' in body and 'continue' in body, \
        '一场都没有就整行不写 —— 写一排 0 会被读成「30 天全平盘」'
    assert "'window_days': window_days" in body


def test_结果四列从fb2同键join_缺了整组为空():
    assert _flat(S.OUTCOME_SQL).count("f.spec_version = 'fb-2'") == 1
    src = pathlib.Path(S.__file__).read_text(encoding='utf-8')
    body = src[src.index('def collect('):src.index('COLS = (')]
    assert "'outcome_len': oc.get('seg_len')" in body
    assert "'outcome_r5': _num(oc.get('r5'))" in body and "'outcome_ex5': _num(oc.get('ex5'))" in body


def test_分片一年一片_与CHUNK_DAYS同一条():
    chunks = S._chunks(dt.date(2025, 10, 9), dt.date(2026, 10, 16))
    assert len(chunks) == 2 and chunks[0][0] == dt.date(2025, 10, 9) and chunks[-1][1] == dt.date(2026, 10, 16)
    assert max((b - a).days for a, b in chunks) < S.CHUNK_DAYS
    assert S.CHUNK_DAYS == 366


# ============================================================ 三、写入契约（三处只有一份真值）

def test_列清单与库里的列一一对应():
    if not _db_up():
        pytest.fail('库不通：这张表的全部契约测试都要连真库跑，不许兜成跳过')
    rows = S.engine.query(
        "select column_name, ordinal_position from information_schema.columns "
        "where table_schema='analytics' and table_name='startup_spike_days' order by ordinal_position",
        (), role='reader') or []
    db = [r['column_name'] for r in rows]
    assert set(db) - set(S.COLS) == {'calculated_at'}, \
        f'库里多出来的列（除了 calculated_at）：{set(db) - set(S.COLS) - {"calculated_at"}}'
    assert set(S.COLS) - set(db) == set(), f'COLS 里有库里没有的列：{set(S.COLS) - set(db)}'
    assert len(db) == len(S.COLS) + 1 == 38, f'库里 {len(db)} 列 / COLS {len(S.COLS)} 列（该是 38/37）'
    assert db[:4] == ['security_code', 'launch_date', 'cohort', 'spec_version']


def test_upsert_的占位符个数与冲突键就是那份列清单():
    assert S.UPSERT.count('%s') == len(S.COLS), '占位符与列数分叉：整批写入会 ProgrammingError'
    assert f"insert into {S.TABLE} ({', '.join(S.COLS)})" in S.UPSERT
    assert 'on conflict (security_code, launch_date, cohort, window_days, spec_version)' in S.UPSERT, \
        '冲突键必须就是 DDL 主键（主键不带 window_days —— 换窗重灌是覆盖同一批行）'
    assert 'calculated_at' not in S.UPSERT.split('values')[0]
    assert 'calculated_at = now()' in S.UPSERT
    for c in S.COLS:
        if c not in S._PK:
            assert f'{c} = excluded.{c}' in S.UPSERT, f'{c} 没进 SET：重算了但库里还是旧值'


def test_ddl_主键含_spec_version_每列有注释_并授过权():
    sql = DDL.read_text(encoding='utf-8')
    assert 'primary key (security_code, launch_date, cohort, window_days, spec_version)' in sql
    assert 'grant select, insert, update, delete on analytics.startup_spike_days to ft_etl, ft_app' in sql
    assert 'grant select                          on analytics.startup_spike_days to ft_reader' in sql
    assert 'create index if not exists startup_spike_days_date_idx' in sql
    assert 'create index if not exists startup_spike_days_code_idx' in sql
    assert sql.count('comment on column') >= len(S.COLS), '每列都得有注释：联合计数的自证判据就写在注释里'
    assert 'dbl_up_days + dbl_down_days = dbl_days' in sql, '这条自证判据必须在列注释里写死'


def test_写库与取数走_etl_角色_且不删行():
    src = pathlib.Path(S.__file__).read_text(encoding='utf-8')
    for seg in ('return engine.executemany(UPSERT, params, role=', 'engine.query(EVENTS_SQL',
                'engine.query(ANYFIRST_EVENTS_SQL', 'engine.query(BARS_SQL', 'engine.query(OUTCOME_SQL'):
        assert "role='etl'" in src[src.index(seg):src.index(seg) + 300], seg
    assert 'delete from' not in src.lower(), '派生表的行要删得先问用户 —— 这一层只 upsert'


# ============================================================ 四、版本三处与每日链

def test_写侧读侧与覆盖体检三处的版本号是同一个():
    assert V.SPEC_VERSION == S.SPEC_VERSION == 'ssd-1', \
        f'写侧 {S.SPEC_VERSION} / 读侧 {V.SPEC_VERSION}：换版本只改一边，页面就静默读旧批'
    cov = COVERAGE.read_text(encoding='utf-8')
    assert f"and spec_version = '{S.SPEC_VERSION}'" in cov, \
        '覆盖体检那句还钉在旧批 ⇒ 这张表会被天天报成「没跟上」（第三处版本号在 coverage.py 的字符串里）'
    assert 'startup_spike_days' in cov and 'launch_date' in cov, '新表没登记进覆盖体检'
    entry = cov[cov.index('startup_spike_days'):cov.index('startup_spike_days') + 900]
    assert '日线代理' in entry and '两件事' in entry, \
        'coverage 备注必须写清「这是日线代理不是分时图」与「0 与 NULL 两件事」（那条纪律就住在这里）'


def test_spikedays_接进每日加工段_排在_quadtrack_与_fbshape_之后():
    import ingest.jobs.e1_fill_all as e1
    names = [s[0] for s in e1.STAGES]
    assert 'spikedays' in names, '没接进每日链 ⇒ 判据表从此不再更新，页面上的「最近一场」会永远停在建表那天'
    assert names.index('spikedays') == 31, f'spikedays 该是加工段第 32 步（实得第 {names.index("spikedays") + 1}）'
    assert names.index('spikedays') > names.index('quadtrack') > names.index('fbshape'), \
        '它读 fb-2 当天补完的母体与结果列，排前面拿到的是昨天那份'
    assert names.index('spikedays') > names.index('startupvol') and names.index('spikedays') > names.index('volratio'), \
        'vr 尺子读 volume_ratio_daily、母体读 sv-2'
    row = [s for s in e1.STAGES if s[0] == 'spikedays'][0]
    assert row[2] is False, '分析产出缺一天不该把「追平」整条判失败'
    assert 'spikedays' in e1.STEP_TIMEOUTS and e1.step_timeout('spikedays') == 900


def test_链里那条命令是_months_12_且_rc2_有软分支():
    src = CHAIN.read_text(encoding='utf-8')
    m = re.search(r"'spikedays':\s*\('app\.compute\.startup_spike_days',\s*\[([^\]]+)\]", src, re.S)
    assert m, 'plan 里读不到 spikedays 的入参（跑到那一步会 KeyError）'
    assert "'--months', '12'" in m.group(1), m.group(1)
    assert '--cohort' not in m.group(1), '不带 --cohort＝写侧两路母体全跑，anyfirst 才跟着每日链刷新'
    assert "name == 'spikedays' and rc == 2" in src, \
        '链那边没接住 rc 2 ⇒ 每天把「按设计不写」报成一步失败（照 fbshape 那段写法）'


def test_rc_语义留在代码里():
    body = pathlib.Path(S.__file__).read_text(encoding='utf-8')
    m = body[body.index('def main('):]
    assert 'return 2' in m and '按设计不写，不算失败' in m
    assert 'return 3' in m and 'return 1' in m


def test_verify_真的在数那几条自证判据():
    body = pathlib.Path(S.__file__).read_text(encoding='utf-8')
    v = body[body.index('def verify('):body.index('def sessions_back(')]
    for tok in ('联合不自洽', 'jump越界', 'vr超总根', '越界', '零场', '窗口含启动日', 'anyfirst缺结果'):
        assert tok in v, f'verify 不再数「{tok}」了 —— 那一类缺陷不会报错，只会给出一句听着合理的假读数'


# ============================================================ 五、读侧：白名单、分档、诚实性

def test_白名单与控件清单是同一份_anyfirst排第一():
    opt = V.options()
    assert [c['value'] for c in opt['cohorts']] == list(V.COHORTS)
    assert opt['cohorts'][0]['value'] == 'anyfirst', '答「预示」那一问的母体必须排第一（界面下拉顺序就是这份）'
    assert opt['cohorts'][0]['label'] == '全部首板（不筛结果，能回答预示那一问）'
    assert [w['value'] for w in opt['windows']] == list(V.WINDOWS)
    assert [m['value'] for m in opt['months']] == list(V.MONTHS)
    assert set(V.COHORTS) == set(S.COHORTS), '写侧名单与读侧白名单分叉 ⇒ 有的档永远查不出来'


def test_坏参数要出声地退回而不是悄悄退回():
    assert 'fell_back' in V._CAVEATS and '不在白名单里' in V._CAVEATS['fell_back']
    for key in ('proxy', 'short_window', 'jump_null', 'jump_pct', 'no_bench', 'no_outcome',
                'no_power', 'thin', 'one_bucket', 'no_batch', 'no_events', 'limit_cut'):
        assert key in V._CAVEATS, f'少了 {key} 那条：这一屏又会把筛掉的东西说成「没有」'
    d = V.board(cohort='dropme', months=12, window=15, jump_pct=0.9, limit=5)
    assert d['available'], d.get('reason')
    assert d['params']['cohort'] == 'anyfirst' and d['params']['window'] == 30
    assert d['params']['jump_pct'] == V.JUMP_PCT, '越界的急拉阈值退回模块常量'
    assert any('母体档' in c for c in d['caveats']), d['caveats']
    assert any('窗口' in c for c in d['caveats'])
    assert any('急拉阈值' in c for c in d['caveats'])


def test_库里那批jump按写侧阈值算_读侧改不动要出声():
    """传一个合法但不等于写侧常量的 jump_pct：不退回（在区间内），但必须点名「读侧改不动库里的数」，
    并给出要重跑的口径 —— 不然屏幕顶着一个 0.05 的标签，数的却是 0.07 的那批日子。"""
    d = V.board(cohort='anyfirst', months=12, window=30, jump_pct=0.05, limit=5)
    assert d['available'], d.get('reason')
    assert d['params']['jump_pct'] == 0.05
    assert any('写侧阈值' in c for c in d['caveats']), d['caveats']


def test_分档区间与切点对应_各档合计等于全场():
    """切点从返回里重建（区间逐档相接）：第一档不设下限、最后一档不设上限、左开右闭。
    fb-2 首发那种 `cuts[g-1]` 绕回最后一个切点的倒区间，这一条当场抓住。"""
    d = V.board(cohort='anyfirst', months=12, window=30, limit=5)
    assert d['available'], d.get('reason')
    bs = d['buckets']
    assert bs and [b['label'] for b in bs] == [f'Q{i + 1}' for i in range(len(bs))], '档号跳了'
    for i, b in enumerate(bs):
        if i >= 1:
            assert b['range'][0] == bs[i - 1]['range'][1], '相邻档的区间必须逐档相接（左开右闭）'
            assert b['range'][0] is not None
        else:
            assert b['range'][0] is None, '第一档不设下限'
        if i == len(bs) - 1:
            assert b['range'][1] is None, '最后一档不设上限（兜底档漏了就没有合计可对）'
    assert sum(b['n'] for b in bs) == d['base']['n'], '各档合计不等于全场：有行漏档或被数了两遍'
    assert d['base']['label'] == '全部母体事件' and 'range' not in d['base']


def test_返回形状逐字对得上前端约定():
    d = V.board(cohort='anyfirst', months=12, window=30, limit=3)
    assert d['available'], d.get('reason')
    assert d['spec_version'] == 'ssd-1'
    assert set(d['params']) == {'cohort', 'cohort_text', 'months', 'window', 'jump_pct', 'limit'}
    assert set(d['coverage']) == {'from', 'to', 'events', 'codes', 'vr_earliest',
                                  'window_full_events', 'window_short_events'}
    assert set(d['joint']) == {'dbl_total_days', 'dbl_up_share', 'dbl_down_share', 'near_up_share',
                               'near_down_share', 'calm_up_share', 'calm_down_share'}
    assert set(d['jumps']) == {'jump_days_med', 'jump_on_dbl_share', 'jump_on_near_share',
                               'jump_on_calm_share', 'jump_limit_share', 'first_jump_gap_med',
                               'jump_days_zero_events', 'jump_days_null_events'}
    bucket_keys = {'label', 'n', 'dbl_up_days_med', 'dbl_down_days_med', 'jump_days_med',
                   'jump_on_dbl_share', 'seg4_rate', 'r5_med', 'ex5_med', 'win5'}
    assert all(set(b) == bucket_keys | {'range'} for b in d['buckets'])
    assert set(d['base']) == bucket_keys
    row_keys = {'code', 'name', 'board', 'launch_date', 'cohort', 'bars', 'up_days', 'down_days',
                'flat_days', 'still_days', 'dbl_days', 'near_dbl_days', 'calm_days', 'dbl_up_days',
                'dbl_down_days', 'near_up_days', 'near_down_days', 'calm_up_days', 'calm_down_days',
                'jump_days', 'jump_dbl_days', 'jump_near_days', 'jump_calm_days', 'jump_limit_days',
                'limit_days', 'first_dbl_gap', 'first_jump_gap', 'outcome_len', 'outcome_r5',
                'outcome_ex5'}
    assert d['rows'] and all(set(r) == row_keys for r in d['rows']), 'rows 字段名差一个就接不上'
    assert set(d['options']) == {'cohorts', 'months', 'windows'}


def test_caveats至少把日线代理_0与NULL_不满窗_无基准四件事说清():
    d = V.board(cohort='anyfirst', months=12, window=30, limit=5)
    text = '‖'.join(d['caveats'])
    assert '日线代理' in text, '① 这是日线代理不是分时图，必须永远出声'
    assert '两件事' in text, '③ jump_days=0（真没有）与 NULL（窗口不满）是两件事'
    assert d['coverage']['window_full_events'] + d['coverage']['window_short_events'] \
        == d['coverage']['events'], '② 满窗+不满窗必须等于事件数（coverage 不许藏）'
    if d['coverage']['window_short_events']:
        assert '窗口不满' in text
    if d['base']['ex5_med'] is not None or d['coverage']['events']:
        # ④ 超额剔掉几条无基准 —— 有被剔的才点名（没有基准的行被剔出分母，不许拿 0 顶）
        no_bench = any('不计入中位' in c for c in d['caveats'])
        assert (not no_bench) or '北交所' in text


def test_出口清洗不留_nan_且零不是空():
    assert V._num(float('nan')) is None and V._num(float('inf')) is None
    assert V._num(0.0) == 0.0, '0 是有效读数（真的一次没拉高、真平盘），被吞成 None 就看不出区别'
    assert V._num(None) is None
    assert V._num(dt.date(2026, 1, 5)) is None, '日期不是数字，不能带进 JSON'


def test_非anyfirst档的4板率不许清一色100():
    """board4_or_gain45 的母体有「四天涨过四成半但段长 2、3」那半边 ⇒ 4 板率必须 <100。
    真库里若也给出 100%，说明结果列 join 错了或母体接错档 —— 抓到就报，不许交一个看着齐的假数。
    （board4/board4_45 两档恒 100% 是同义反复：那一问只能看 anyfirst，读侧必须自动出 no_power。）"""
    if not _db_up():
        pytest.fail('库不通：这条真库端到端不许兜成跳过')
    d = V.board(cohort='board4_or_gain45', months=12, window=30, limit=5)
    assert d['available'], d.get('reason')
    assert d['base']['n'] > 0, 'board4_or_gain45 一档在 ssd-1 里一行都没有 —— 回填只跑了 anyfirst？'
    assert d['base']['seg4_rate'] is not None and d['base']['seg4_rate'] < 99.0, \
        f"board4_or_gain45 的 4 板率 {d['base']['seg4_rate']}% 清一色 100 —— 母体接错还是结果列 join 错"
    b4 = V.board(cohort='board4', months=12, window=30, limit=5)
    if b4['base']['seg4_rate'] == 100.0:
        assert any('同义反复' in c or 'no_power' in c or '换 cohort=anyfirst' in c for c in b4['caveats']), \
            'board4 的 4 板率恒 100% 时必须点名「这一问在这里没有分辨力」'


def _served_strings(path: pathlib.Path, names: set[str]) -> list[str]:
    """取会被交出去的字面量：模块级常量 + 指定函数体里的字符串常量；docstring（裸表达式）跳过。"""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    out: list[str] = []

    def collect(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.append(sub.value)

    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets):
            collect(n.value)
        elif isinstance(n, ast.AnnAssign) and getattr(n.target, 'id', '') in names:
            collect(n.value)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name in names:
            body = list(n.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
            for sub in body:
                collect(sub)
    assert out, '没取到待查字面量（常量改名了？这条测试会变成空跑）'
    return out


@pytest.mark.parametrize('mod, consts', [
    (V.__file__, {'CALIBER', 'DEFINITIONS', '_CAVEATS', 'COHORTS', 'board', '_bucket'}),
    (S.__file__, {'verify', 'main'}),
])
def test_交出的散文字段里没有裸的星号粗体标记(mod, consts):
    """页面把 `caliber`/`caveats`/`definitions` 当「后端原句」直接上屏，带 `**` 用户看到的就是星号。"""
    leaks = [s[:44] for s in _served_strings(pathlib.Path(mod), consts) if '**' in s]
    assert not leaks, f'交出的散文里带裸星号：{leaks}'
    ddl = DDL.read_text(encoding='utf-8')
    stars = [ln[:60] for ln in ddl.splitlines() if ln.strip().startswith('comment on') and '**' in ln]
    assert not stars, f'列注释带裸星号（会被别处当出处引用）：{stars}'


@pytest.mark.parametrize('rel', [V.__file__, S.__file__])
def test_中文串里不嵌_ascii_双引号(rel):
    """项目纪律：中文里用「」—— 嵌 ASCII 双引号既难读，又会在「把这句话拼进别的字符串」时点断。"""
    tree = ast.parse(pathlib.Path(rel).read_text(encoding='utf-8'))
    bad = [n.value[:44] for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and re.search(r'[一-鿿]', n.value) and '"' in n.value]
    assert not bad, f'中文串里嵌了 ASCII 双引号：{bad}'
    ddl = DDL.read_text(encoding='utf-8')
    stars = [ln[:60] for ln in ddl.splitlines()
             if re.search(r'[一-鿿]', ln) and '"' in ln]
    assert not stars, f'DDL 注释行的中文里嵌了 ASCII 双引号：{stars}'


def test_端点存在_参数交给读侧现筛():
    src = VR.read_text(encoding='utf-8')
    assert '@app.get("/api/quant/startup-spike-days")' in src
    seg = src[src.index('"/api/quant/startup-spike-days"'):]
    for tok in ('spike_days_view.board(', 'HTTPException(502', 'def quant_startup_spike_days(',
                'ge=1, le=12', 'ge=1, le=600', 'ge=0.03, le=0.15'):
        assert tok in seg[:2500], tok
    assert 'return {"data"' in seg[:2500], '信封与 startup-quadrant-track 同一风格'


# ============================================================ 六、真库现状（回填完才有意义）

def test_库里真的回填过_五档母体都有行_自证判据例外为零():
    if not _db_up():
        pytest.fail('库不通：真库现状必须实测，不许兜成跳过')
    r = S.engine.query_one(
        f"select count(*) n, count(distinct security_code) codes, "
        f"       count(distinct cohort) cohorts, min(launch_date)::text a, max(launch_date)::text z, "
        f"       count(*) filter (where dbl_up_days + dbl_down_days is distinct from dbl_days) 联合例外, "
        f"       count(*) filter (where jump_days is not null and coalesce(jump_dbl_days,0) "
        f"         + coalesce(jump_near_days,0) + coalesce(jump_calm_days,0) > jump_days) jump越界, "
        f"       count(*) filter (where up_days + down_days + flat_days > bars) vr越界, "
        f"       count(*) filter (where dbl_days > bars or near_dbl_days > bars or calm_days > bars "
        f"         or still_days > bars or limit_days > bars "
        f"         or (jump_days is not null and jump_days > bars)) 某列越界, "
        f"       count(*) filter (where win_to >= launch_date) 含启动日, "
        f"       count(*) filter (where bars = 0) 零场, "
        f"       count(*) filter (where cohort = 'anyfirst' and outcome_len is null) anyfirst缺结果, "
        f"       count(*) filter (where jump_days = 0) jump零, count(*) filter (where jump_days is null) jump空 "
        f"  from {S.TABLE} where spec_version = %s", (S.SPEC_VERSION,), role='reader')
    assert int(r['n']) > 0, '一张表空着 —— 回填没跑还是写库坏了？'
    assert int(r['cohorts']) == 5, f"母体只有 {r['cohorts']} 档（该是 5：anyfirst ＋ sv-2 四类）—— 回填命令少了？"
    assert int(r['联合例外']) == 0, \
        f'{r["联合例外"]} 行 dbl_up+dbl_down <> dbl_days：这一屏的核心自证判据断了'
    assert int(r['jump越界']) == 0, f'{r["jump越界"]} 行 jump_dbl+jump_near+jump_calm > jump_days'
    assert int(r['vr越界']) == 0, f'{r["vr越界"]} 行 up+down+flat > bars'
    assert int(r['某列越界']) == 0, f'{r["某列越界"]} 行某一类天数大于 bars'
    assert int(r['含启动日']) == 0, '窗口含了启动日当天'
    assert int(r['零场']) == 0, 'bars=0 的行进了库（该整行不写）'
    assert int(r['anyfirst缺结果']) == 0, 'anyfirst 行缺 outcome_len —— 母体就在 fb-2，join 断了'
    assert int(r['jump空']) > 0, '不满窗的 jump NULL 一条都没有？不满窗是覆盖事实，应该数得出（别藏）'
    print(f'\n  真库现状：{r["n"]} 行 / {r["codes"]} 只 / {r["cohorts"]} 档母体，启动日 {r["a"]}…{r["z"]}；'
          f'jump=0 {r["jump零"]} 行 vs NULL {r["jump空"]} 行')


def test_库里逐行与纯判据对得上_抽一条真窗口逐列复算():
    """SQL 那一路把窗口取回后由 `window_counts` 计数写库 —— 抽一条真行把它的窗口按同一条 SQL
    再取一遍、用纯函数重算，逐列对：分叉当场红，而不是让屏幕上那句「倍量涨的天数」悄悄变另一个数。"""
    if not _db_up():
        pytest.fail('库不通：这条对照必须连真库跑')
    row = S.engine.query_one(
        f"select security_code c, launch_date::text d, cohort, window_days, bars, "
        f"       up_days, down_days, dbl_days, dbl_up_days, dbl_down_days, calm_days, calm_up_days, "
        f"       jump_days, jump_dbl_days, first_dbl_gap, limit_days "
        f"  from {S.TABLE} where spec_version = %s and bars = window_days "
        f" order by launch_date desc limit 1", (S.SPEC_VERSION,), role='reader')
    assert row, '库里还没有一条满窗行'
    pairs = json.dumps([[row['c'], row['d'], row['cohort']]])
    got = S.engine.query(S.BARS_SQL, {'pairs': pairs, 'fetch': row['window_days'] + 1,
                                      'eps': S.LIMIT_EPS}, role='reader') or []
    assert got and len(got[0]['day_rows']) == row['bars']
    bars = [(x.get('vr'), x.get('ret'), x.get('lim')) for x in got[0]['day_rows']]
    c = S.window_counts(bars, window_days=row['window_days'])
    for k in ('up_days', 'down_days', 'dbl_days', 'dbl_up_days', 'dbl_down_days', 'calm_days',
              'calm_up_days', 'jump_days', 'jump_dbl_days', 'first_dbl_gap', 'limit_days'):
        assert c[k] == row[k], f'{k}：纯判据 {c[k]} 与库里 {row[k]} 对不上（窗口重取复算不一致）'
