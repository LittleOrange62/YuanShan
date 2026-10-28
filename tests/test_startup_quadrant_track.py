# -*- coding: utf-8 -*-
"""「启动前 N 场四维象限轨迹」（`analytics.startup_quadrant_track`，sqt-1）的判据、契约与读侧不变量。

这一屏回答用户 2026-10-17 那一问：这几类启动母体在**启动日之前那 30 场**里，四维象限的
**分数、名次、象限位置**怎么挪的，以及「名次往上爬的是不是更容易走到 4 板」。
下面每一条都对应一个「错了不报错、只给出一句听着合理的读数」的地方。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_startup_quadrant_track.py -q`
真库那几条要连着 PostgreSQL 跑（本机是唯一库）；不连库时它们会红，**不许改成跳过**。
"""
from __future__ import annotations

import ast
import datetime as dt
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.compute import startup_quadrant_track as T    # noqa: E402
from duanxian import quadrant_track_view as V          # noqa: E402

DDL = ROOT / 'app' / 'db' / 'sql' / '43_startup_quadrant_track.sql'
CHAIN = ROOT / 'ingest' / 'jobs' / 'e1_fill_all.py'
COVERAGE = ROOT / 'duanxian' / 'coverage.py'
VR = ROOT / 'vr' / 'app.py'


def _flat(sql: str) -> str:
    return ' '.join(sql.split())


def _db_up() -> bool:
    try:
        return bool(T.engine.scalar('select 1'))
    except Exception:                                        # noqa: BLE001
        return False


# ============================================================ 一、窗口与符号（判据）

def test_窗口不含启动日当天_只取它之前那几场():
    """启动日当天已经涨停，分数被启动本身污染；把它算进「启动前」等于让启动事件自己解释自己。"""
    s = _flat(T.TRACK_SQL)
    assert 's.trade_date < e.launch' in s, '窗口必须是严格早于启动日'
    assert 's.trade_date >= e.launch - (%(back)s::int)' in s, '要有扫描下界，否则长停牌票整段回扫'
    assert "and not s.is_stale" in s, \
        '旧算法迁入批（列注释：算法已改，不能与新结果混着比）混进窗口 ⇒ rank_delta 就成了「旧算法减新算法」'
    assert T.LOOKBACK_DAYS >= T.WINDOW_DAYS * 7, '往前扫太少会把长期停牌的票判成不满窗'


def test_取前n场用_row_number_降序_象限翻转用_lag():
    """聚合下推 SQL 的两条主件：窗口那 N 场靠 `rn_desc<=N` 截，相邻比较靠 `lag(quadrant)`。"""
    s = _flat(T.TRACK_SQL)
    assert 'row_number() over (partition by e.code, e.launch, e.cohort, s.capability_key '\
           'order by s.trade_date desc) as rn_desc' in s
    assert 'where rn_desc <= %(win)s' in s
    assert 'lag(quadrant) over (partition by code, launch, cohort, capability_key '\
           'order by trade_date) as prev_quad' in s
    assert "and s.capability_key = any(%(keys)s::text[])" in s


def test_rank_delta_的正数是名次上升():
    """rank 越小越靠前 ⇒ `rank_first − rank_last`。写成 `last − first` 就把「往上爬」说成「往下掉」，
    而 buckets 正是按这一列切三分位的 —— 反了以后每一档的结论都是反的。"""
    assert T.rank_delta_of(3000, 200) == 2800, '3000 名 → 200 名是爬升 ⇒ 正数'
    assert T.rank_delta_of(200, 3000) == -2800
    assert T.rank_delta_of(500, 500) == 0
    src = pathlib.Path(T.__file__).read_text(encoding='utf-8')
    assert 'int(rank_first) - int(rank_last)' in src
    assert '正数＝名次上升' in src, '符号约定必须写在函数旁边，只写在 DDL 里会有人只读代码'


@pytest.mark.parametrize('first, last, want', [
    (10.0, 12.5, 2.5), (12.5, 10.0, -2.5), (None, 10.0, None), (10.0, None, None), (None, None, None)])
def test_value_delta_任何一边缺就是空而不是零(first, last, want):
    """0 是「真算出一样的分」，None 才是「没拿到」。混起来会把「判不了」读成「没变化」。"""
    assert T.delta_of(first, last) == want


def test_象限相邻变化的计数_null_参与不算一次翻转():
    """「NULL → Q1」是补上了一个读数，不是位置变了。两边都取到象限才比。"""
    assert T.switch_count(['Q1', 'Q1', 'Q2', 'Q2', 'Q3']) == 2
    assert T.switch_count(['Q1', 'Q1', 'Q1']) == 0
    assert T.switch_count(['Q1', None, 'Q2']) == 0, '中间那一场没有象限 ⇒ 两头不相邻，不计一次翻转'
    assert T.switch_count(['Q1', None, None, 'Q2']) == 0
    assert T.switch_count([]) == 0
    assert T.switch_count(['Q1', 'BOGUS', 'Q2']) == 0, '认不得的象限值不参与、也不当一次翻转'


def test_最后一次翻转距t_1_几场_没翻转过是空不是零():
    """0 的意思是「刚刚才翻」，None 是「整个窗口一次都没翻」—— 两者读出来是相反的故事。"""
    assert T.last_switch_gap(['Q1', 'Q2']) == 0, '翻转就发生在最后一场（T-1）'
    assert T.last_switch_gap(['Q2', 'Q1', 'Q1']) == 1
    assert T.last_switch_gap(['Q1', 'Q2', 'Q1']) == 0, '取的是**最后一次**，不是第一次'
    assert T.last_switch_gap(['Q1', 'Q1', 'Q1']) is None
    assert T.last_switch_gap([]) is None


def test_象限天数相加不超过场数():
    got = T.quadrant_days(['Q1', 'Q2', 'Q2', None, 'BOGUS'])
    assert got == {'q1_days': 1, 'q2_days': 2, 'q3_days': 0, 'q4_days': 0}
    assert sum(got.values()) <= 5


def test_非oper_score的键_象限那一族必须全部是空():
    """库里 `quadrant` 只在 oper_score 行上有值（其余键恒 NULL 是设计）。
    写成 0 会被屏幕念成「这些票启动前从没换过象限」—— 而真意是它们根本没有象限这个读数。"""
    agg = {'quad_first_raw': None, 'quad_last_raw': None, 'quad_switches_raw': 0,
           'last_switch_rn': None, 'q1_raw': 0, 'q2_raw': 0, 'q3_raw': 0, 'q4_raw': 0}
    for key in T.TRACK_KEYS:
        if key == T.QUADRANT_KEY:
            continue
        got = T.quad_columns(key, agg, 30)
        assert set(got) == {'quad_first', 'quad_last', 'quad_switches', 'quad_last_switch_gap',
                            'q1_days', 'q2_days', 'q3_days', 'q4_days'}
        assert all(v is None for v in got.values()), f'{key} 的象限列必须是 None，实得 {got}'
        assert 0 not in got.values(), f'{key} 那一行不许写出 0（SQL 那一路数出来就是 0，靠这里挡掉）'


def test_oper_score行的象限列由agg拼出_gap是bars减最后一次翻转():
    got = T.quad_columns('oper_score', {'quad_first_raw': 'Q3', 'quad_last_raw': 'Q1',
                                         'quad_switches_raw': 4, 'last_switch_rn': 5,
                                         'q1_raw': 8, 'q2_raw': 3, 'q3_raw': 12, 'q4_raw': 2}, 30)
    assert got['quad_switches'] == 4 and got['quad_first'] == 'Q3' and got['quad_last'] == 'Q1'
    assert got['quad_last_switch_gap'] == 25, '最后一次翻转正序第 5 场、窗口 30 场 ⇒ 距 T-1 有 25 场'
    assert got['q1_days'] == 8 and got['q4_days'] == 2
    none = T.quad_columns('oper_score', {'quad_first_raw': 'Q3', 'quad_last_raw': 'Q1',
                                          'quad_switches_raw': 0, 'last_switch_rn': None,
                                          'q1_raw': 30, 'q2_raw': 0, 'q3_raw': 0, 'q4_raw': 0}, 30)
    assert none['quad_switches'] == 0 and none['quad_last_switch_gap'] is None, \
        '一次都没翻转：次数是 0（真读数），间隔是 NULL（无从谈起）'


def test_七个键的名单唯一且情绪族不进表():
    """`market_mood` 实测同日全市场同一个数（名次恒 1、分位恒 50.0090）⇒ 名次/分位没有横截面意义。
    这是**显式排除**（DDL 头部、这份名单、读侧 caveat 三处都点名），不许被读成漏采。"""
    assert len(set(T.TRACK_KEYS)) == 7
    assert 'market_mood' not in T.TRACK_KEYS and 'review_report' not in T.TRACK_KEYS
    assert list(T.TRACK_KEYS) == list(V.KEY_COLS), '写侧名单与读侧白名单分叉 ⇒ 有的键永远查不出来'
    assert set(T.TRACK_KEYS) == {k['value'] for k in V.options()['keys']} - {'all'}
    assert all(T.KEY_LABELS[k] for k in T.TRACK_KEYS)


def test_窗口只收三档且母体五档含anyfirst():
    assert T.WINDOWS == (10, 20, 30) and T.WINDOW_DAYS == 30
    assert T.COHORTS == ('anyfirst', 'board4', 'board4_45', 'board4_or_gain45', 'jump40')
    assert set(T.COHORTS) == set(V.COHORTS)
    # 10-18：anyfirst 必须排第一（读侧白名单顺序＝界面下拉顺序，答「预示」那一问的档要摆在最前面）
    assert list(V.COHORTS)[0] == 'anyfirst'
    assert T.ANYFIRST == 'anyfirst' and T.COHORTS_SV2 == ('board4', 'board4_45',
                                                          'board4_or_gain45', 'jump40')


def test_母体取_sv2_四档_键名单与窗口都在参数里():
    s = _flat(T.EVENTS_SQL)
    assert "p.spec_version = 'sv-2'" in s, 'sv-1 那批与 sv-2 不可并排（连板判定读的涨停档换过权威）'
    assert 'p.launch_date between %(a)s::date and %(b)s::date' in s
    assert '(%(cohort)s::text is null or p.cohort = %(cohort)s)' in s, '--cohort 能单跑一档，默认四档全要'
    assert 'p.trigger_gain' in s, 'outcome_gain4 的来源就在母体这一发里（fb-2 没有 trigger_gain 这一列）'


def test_anyfirst母体从fb2取全部首板_段长不当筛子():
    """10-18 的第二入口：sv-2 的 board4 一族按结果筛过（4 板率恒 100% 是同义反复），
    能答「预示」那一问的母体＝fb-2 全部首板。这一档若再拿 seg_len 当筛子就又把未来函数请回来了。"""
    s = _flat(T.ANYFIRST_EVENTS_SQL)
    assert "f.spec_version = 'fb-2'" in s
    assert "'anyfirst'::text as cohort" in s
    assert 'seg_len >=' not in s and 'seg_len =' not in s, 'anyfirst 的 seg_len 不筛 —— 筛了就又是 sv-2 那一屏'
    assert 'f.launch_date between %(a)s::date and %(b)s::date' in s
    body = pathlib.Path(T.__file__).read_text(encoding='utf-8')
    seg = body[body.index('def load_events('):body.index('def collect(')]
    assert 'engine.query(EVENTS_SQL' in seg and 'engine.query(ANYFIRST_EVENTS_SQL' in seg
    assert seg.count("role='etl'") >= 2, '两个母体入口都要走 etl（reader 60 秒就掐）'
    assert 'cohort is None or cohort in COHORTS_SV2' in seg and 'cohort is None or cohort == ANYFIRST' in seg, \
        'cohort=None 要两路都跑 —— 每日链不带 --cohort，anyfirst 不跟着刷当天就烂掉'


def test_结果两列分别取_fb2_与_sv2_缺就是空():
    """DDL 里说清了：`outcome_len` 是 fb-2 的 seg_len，`outcome_gain4` 是 sv-2 的 trigger_gain
    （**fb-2 没有 trigger_gain 这一列**，别再照着需求原文去那张表找）。"""
    assert _flat(T.FB_SQL).count("f.spec_version = 'fb-2'") == 1
    src = pathlib.Path(T.__file__).read_text(encoding='utf-8')
    body = src[src.index('def _row('):src.index('COLS = (')]
    assert "'outcome_len': outcome.get('seg_len')" in body
    assert "'outcome_gain4': _round(_num(event.get('trigger_gain')))" in body
    assert "outcome.get('trigger_gain')" not in body


def test_分片一年一片_避开六十秒语句超时():
    chunks = T._chunks(dt.date(2025, 10, 9), dt.date(2026, 10, 16))
    assert len(chunks) == 2 and chunks[0][0] == dt.date(2025, 10, 9) and chunks[-1][1] == dt.date(2026, 10, 16)
    assert max((b - a).days for a, b in chunks) < T.CHUNK_DAYS
    assert T._chunks(dt.date(2026, 1, 1), dt.date(2026, 1, 1)) == [(dt.date(2026, 1, 1), dt.date(2026, 1, 1))]


# ============================================================ 二、写入契约（三处只有一份真值）

def test_列清单与库里的列一一对应():
    if not _db_up():
        pytest.fail('库不通：这张表的全部契约测试都要连真库跑，不许兜成跳过')
    rows = T.engine.query(
        "select column_name, ordinal_position from information_schema.columns "
        "where table_schema='analytics' and table_name='startup_quadrant_track' "
        "order by ordinal_position", (), role='reader') or []
    db = [r['column_name'] for r in rows]
    assert set(db) - set(T.COLS) == {'calculated_at'}, \
        f'库里多出来的列（除了 calculated_at）：{set(db) - set(T.COLS) - {"calculated_at"}}'
    assert set(T.COLS) - set(db) == set(), f'COLS 里有库里没有的列：{set(T.COLS) - set(db)}'
    assert len(db) == len(T.COLS) + 1 == 33, f'库里 {len(db)} 列 / COLS {len(T.COLS)} 列（该是 33/32）'
    assert db[:5] == ['security_code', 'launch_date', 'cohort', 'capability_key', 'spec_version']


def test_upsert_的占位符个数与冲突键就是那份列清单():
    assert T.UPSERT.count('%s') == len(T.COLS), '占位符与列数分叉：整批写入会 ProgrammingError'
    assert f"insert into {T.TABLE} ({', '.join(T.COLS)})" in T.UPSERT
    assert ('on conflict (security_code, launch_date, cohort, capability_key, window_days, spec_version)' in T.UPSERT)
    assert 'calculated_at' not in T.UPSERT.split('values')[0], 'calculated_at 有默认值，不进 INSERT 列表'
    assert 'calculated_at = now()' in T.UPSERT, '重算要刷新 calculated_at，否则读侧看不出这批是什么时候算的'
    for c in T.COLS:
        if c not in T._PK:
            assert f'{c} = excluded.{c}' in T.UPSERT, f'{c} 没进 SET：重算了但库里还是旧值'


def test_写库走_etl_角色而不是读角色_且不删行():
    src = pathlib.Path(T.__file__).read_text(encoding='utf-8')
    for seg in ("return engine.executemany(UPSERT, params, role=", 'engine.query(EVENTS_SQL',
                'engine.query(ANYFIRST_EVENTS_SQL', 'engine.query(TRACK_SQL', 'engine.query(FB_SQL'):
        assert "role='etl'" in src[src.index(seg):src.index(seg) + 300], seg
    assert 'delete from' not in src.lower(), '派生表的行要删得先问用户 —— 这一层只 upsert'


def test_ddl_主键含_spec_version_每列有注释_并授过权():
    sql = DDL.read_text(encoding='utf-8')
    assert 'primary key (security_code, launch_date, cohort, capability_key, window_days, spec_version)' in sql
    assert 'grant select, insert, update, delete on analytics.startup_quadrant_track to ft_etl, ft_app' in sql
    assert 'grant select                          on analytics.startup_quadrant_track to ft_reader' in sql
    assert 'create index if not exists startup_quadrant_track_date_idx' in sql
    assert '(spec_version, launch_date desc)' in sql and '(security_code, launch_date desc)' in sql
    assert sql.count('comment on column') >= len(T.COLS) - 5, '每列都得有注释：这一张表全靠注释说符号方向'
    assert 'rank_delta = rank_first - rank_last' in sql or 'rank_first - rank_last' in sql


# ============================================================ 三、每日链与版本（三处同一个数）

def test_quadtrack_接进每日加工段_且排在那两张表之后():
    import ingest.jobs.e1_fill_all as e1
    names = [s[0] for s in e1.STAGES]
    assert 'quadtrack' in names, '没接进每日链 ⇒ 判据表从此不再更新，页面上的"最近一场"会永远停在建表那天'
    assert names.index('quadtrack') > names.index('startupvol'), '母体名单来自 startup_volume_profile'
    assert names.index('quadtrack') > names.index('fbshape'), 'outcome_len 来自 first_board_shape'
    assert names.index('quadtrack') == 30, f'quadtrack 该是加工段第 31 步（实得第 {names.index("quadtrack") + 1}）'
    row = [s for s in e1.STAGES if s[0] == 'quadtrack'][0]
    assert row[2] is False, '分析产出缺一天不该把"追平"整条判失败'
    assert 'quadtrack' in e1.STEP_TIMEOUTS and e1.step_timeout('quadtrack') <= 900


def test_链里那条命令就是_months_12_且文档步数与代码一致():
    src = CHAIN.read_text(encoding='utf-8')
    m = re.search(r"'quadtrack':\s*\('app\.compute\.startup_quadrant_track',\s*\[([^\]]+)\]", src, re.S)
    assert m, 'plan 里读不到 quadtrack 的入参（跑到那一步会 KeyError）'
    assert "'--months', '12'" in m.group(1), m.group(1)
    import ingest.jobs.e1_fill_all as e1
    assert len(e1.STAGES) == 42, ('又加了一步却没改文档：docs 里那些手抄数由 test_volume_ratio 钉住'
                                  '（10-21 加 `fbdaily` 首板分析 → 42，quadtrack 从第 30 顺延到第 31）')
    # 10-18 的 anyfirst 档必须由链默认带到：plan 里不带 --cohort ⇒ 写侧 cohort=None 两路母体都跑，
    # 这一档才跟着每日链刷新（否则 backfill 那天之后它就永远停在旧批）。
    assert "--cohort" not in m.group(1), 'quadtrack 若被钉死单档，anyfirst 就不再随每日链更新'


def test_写侧读侧与覆盖体检三处的版本号是同一个():
    assert V.SPEC_VERSION == T.SPEC_VERSION == 'sqt-1', \
        f'写侧 {T.SPEC_VERSION} / 读侧 {V.SPEC_VERSION}：换版本只改一边，页面就静默读旧批'
    cov = COVERAGE.read_text(encoding='utf-8')
    assert f"and spec_version = '{T.SPEC_VERSION}'" in cov, \
        '覆盖体检那句还钉在旧批 ⇒ 这张表会被天天报成"没跟上"（第三处版本号在 coverage.py 的字符串里）'
    assert 'startup_quadrant_track' in cov and 'launch_date' in cov, '新表没登记进覆盖体检'


def test_当日没有母体事件时那一跑按设计不写也不算失败():
    """rc 2 的语义要留在代码里：e1 那边把它折成"软失败"，两边各自解释一次就会分叉。"""
    body = pathlib.Path(T.__file__).read_text(encoding='utf-8')
    m = body[body.index('def main('):]
    assert 'return 2' in m and '按设计不写，不算失败' in m
    assert "name == 'quadtrack' and rc == 2" in CHAIN.read_text(encoding='utf-8'), \
        '链那边没接住 rc 2 ⇒ 每天把"按设计不写"报成一步失败'


def test_verify_真的在数那几条静默缺陷():
    body = pathlib.Path(T.__file__).read_text(encoding='utf-8')
    v = body[body.index('def verify('):body.index('def sessions_back(')]
    for tok in ('脏象限', '天数越界', '名次不自洽', '窗口含启动日', '短窗'):
        assert tok in v, f'verify 不再数「{tok}」了 —— 那一类缺陷不会报错，只会给出一句合理的假读数'


# ============================================================ 四、读侧：白名单、分档、诚实性

def test_白名单与控件清单是同一份():
    opt = V.options()
    assert [c['value'] for c in opt['cohorts']] == list(V.COHORTS)
    assert [k['value'] for k in opt['keys']][0] == 'all'
    assert [k['value'] for k in opt['keys']][1:] == list(V.KEY_COLS)
    assert [w['value'] for w in opt['windows']] == list(V.WINDOWS)
    assert [m['value'] for m in opt['months']] == list(V.MONTHS)


def test_坏参数要出声地退回而不是悄悄退回():
    """屏幕上读的是退回之后的那个口径 —— 不说一句就是让用户拿错口径做决定。"""
    assert 'fell_back' in V._CAVEATS and '不在白名单里' in V._CAVEATS['fell_back']
    for key in ('no_batch', 'short_window', 'no_events', 'no_bench', 'no_outcome', 'no_rank',
                'thin', 'quad_null', 'stage_unit', 'mood_out', 'no_power', 'thin_result'):
        assert key in V._CAVEATS, f'少了 {key} 那条：这一屏又会把筛掉的东西说成"没有"'
    d = V.track(cohort='dropme', months=12, window=15, key='bogus', limit=5)
    assert any('母体档' in c and 'board4' in c for c in d['caveats']), d['caveats']
    assert any('判据键' in c for c in d['caveats'])
    assert any('窗口' in c for c in d['caveats'])
    assert d['params']['cohort'] == 'board4' and d['params']['window'] == 30 and d['params']['key'] == 'all'


def test_三档窗口各存各的行_换深度不许盖掉另一档():
    """10-18：主键补 `window_days` 之前，跑一次 --window 20 就把 30 场那批整行盖掉，
    界面切回 30 档只剩"这一档还没算"。现在三档必须同时有行；而"没算过的深度要说没算过"
    那句仍然留在读侧（防将来只回填一部分深度时把空表说成"这些票没轨迹"）。"""
    rows = V.engine.query(f'select window_days, count(*) n from {V.TABLE} '
                          f"where spec_version = %s group by 1", (V.SPEC_VERSION,), role='reader') or []
    got = {int(r['window_days']): int(r['n']) for r in rows}
    assert {10, 20, 30} <= set(got), f'三档窗口没有各自存行：{got}'
    assert all(got[w] > 0 for w in (10, 20, 30)), got
    assert any('还没算' in str(v) for v in V._CAVEATS.values()), '不可用那一句被删了：空表会被读成"没有轨迹"'
    for w in (10, 20, 30):
        d = V.track(window=w)
        assert d['available'] is True and 'rows' in d, f'window={w} 不交数：{d.get("reason")}'


def test_分档区间与切点一一对应_第一档不设下限():
    """`cuts = [a, b]` 时屏幕上必须念成「≤a」「a～b」「>b」。fb-2 首发实测交过 Q1 `[2.0, 1.0]`：
    `cuts[g-1]` 写成 `if g-1 < len(cuts)` 时第一档的 -1 绕到最后一个切点，区间就倒过来了。"""
    d = V.track(cohort='board4_or_gain45', months=12, window=30, limit=5)
    assert d['available'], d.get('reason')
    cuts, rows = d['cuts'], d['buckets']
    assert rows and [r['label'] for r in rows] == [f'Q{i + 1}' for i in range(len(rows))], '档号跳了'
    for i, r in enumerate(rows):
        want = [cuts[i - 1] if i >= 1 else None, cuts[i] if i < len(cuts) else None]
        assert r['range'] == want, f'{r["label"]} 区间应为 {want}，实得 {r["range"]}'
        if want[0] is not None:
            assert r['lo_obs'] > want[0], f'{r["label"]} 左开：实测最小 {r["lo_obs"]} 不该等于下限'
        if want[1] is not None:
            assert r['hi_obs'] <= want[1] + 1e-9, f'{r["label"]} 右闭：实测最大 {r["hi_obs"]} 越过了上限'
    assert sum(r['n'] for r in rows) == d['base']['n'], '各档合计不等于全场：有行漏档或被数了两遍'
    assert 'range' not in d['base'], '基线那一行没有区间，别给页面留一个 null 区间让它自己编句子'


def test_名次为空的那些行不进分档但要点名():
    """分档按 rank_delta 切 ⇒ 这一列为空的轨迹根本分不进三档。不点名就会被读成「这一档少了几条」。"""
    src = pathlib.Path(V.__file__).read_text(encoding='utf-8')
    assert "and t.rank_delta is not null" in src, '分档那一条要把空名次的行剔掉（否则 case 的兜底把它们塞进最后一档）'
    assert 'no_rank' in V._CAVEATS


def test_象限那一族在非oper_score的键上是null不是零也不是空对象():
    d = V.track(cohort='board4', months=12, window=30, key='all', limit=5)
    oper = [k for k in d['keys'] if k['key'] == 'oper_score']
    others = [k for k in d['keys'] if k['key'] != 'oper_score']
    assert oper and others
    assert all(k['quad_first'] is None and k['quad_last'] is None
               and k['quad_switches_med'] is None and k['quad_last_switch_gap_med'] is None
               for k in others), '非 oper_score 的键交出 0 或 {} 就是写坏了'
    assert oper[0]['quad_first'] and set(oper[0]['quad_first']) == {'Q1', 'Q2', 'Q3', 'Q4'}
    for r in d['rows']:
        if r['capability_key'] != 'oper_score':
            assert r['quad_first'] is None and r['quad_switches'] is None


def test_出口清洗不留_nan_且零不是空():
    assert V._num(float('nan')) is None and V._num(float('inf')) is None
    assert V._num(0.0) == 0.0, '0 是有效读数（名次没变、0 次翻转），被吞成 None 就看不出"真的算出 0"'
    assert V._num(None) is None
    assert V._num(dt.date(2026, 1, 5)) is None, '日期不是数字，不能带进 JSON'


def test_聚合下推_sql_python_不逐行分档():
    src = pathlib.Path(V.__file__).read_text(encoding='utf-8')
    assert 'group by grouping sets ((g), ())' in src, '每档汇总 + 全体基线一次拿回'
    assert 'percentile_cont' in src
    assert 'left join analytics.first_board_shape' in src, 'r5/ex5 在库里 join，不拉到 python 再拼'


def test_四板率与超额的分数母都只数得出结果的那些行():
    """`seg4_rate` 的分母是「fb-2 里有那一行」的事件、`ex5_med`/`win5` 的分母是「有读数/有基准」的那些。
    拿全档条数当分母会把「没结果」读成「没走到 4 板」；拿 0 顶空超额会把「没有基准」读成「没赚」。"""
    src = pathlib.Path(V.__file__).read_text(encoding='utf-8')
    assert 'count(outcome_len) as outcome_n' in src
    assert "_pct(_int(row.get('s4')), _int(row.get('outcome_n')))" in src, '4 板率的分母必须是 outcome_n'
    assert "count(*) filter (where r5 > 0) as win_pos" in src
    assert "_pct(_int(row.get('win_pos')), _int(row.get('r5_n')))" in src, '胜率的分母必须是有 r5 的那些行'
    assert "count(*) filter (where r5 is not null and ex5 is null) as no_bench" in src, \
        '没有基准的行要数出来并在 caveat 点名（北交所），不许当 0 参与中位'


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
    (V.__file__, {'CALIBER', 'DEFINITIONS', '_CAVEATS', 'KEY_COLS', 'COHORTS', 'track',
                  '_key_row', '_bucket'}),
    (T.__file__, {'verify', 'main'}),
])
def test_交出的散文字段里没有裸的星号粗体标记(mod, consts):
    """页面把 `caliber`/`caveats`/`definitions` 当"后端原句"直接上屏，带 `**` 用户看到的就是星号。"""
    leaks = [s[:44] for s in _served_strings(pathlib.Path(mod), consts) if '**' in s]
    assert not leaks, f'交出的散文里带裸星号：{leaks}'
    ddl = DDL.read_text(encoding='utf-8')
    stars = [ln[:60] for ln in ddl.splitlines() if ln.strip().startswith('comment on') and '**' in ln]
    assert not stars, f'列注释带裸星号（会被别处当出处引用）：{stars}'


@pytest.mark.parametrize('rel', [V.__file__, T.__file__])
def test_中文串里不嵌_ascii_双引号(rel):
    """项目纪律：中文里用「」—— 嵌 ASCII 双引号既难读，又会在"把这句话拼进别的字符串"时点断。"""
    tree = ast.parse(pathlib.Path(rel).read_text(encoding='utf-8'))
    bad = [n.value[:44] for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and re.search(r'[\u4e00-\u9fff]', n.value) and '"' in n.value]
    assert not bad, f'中文串里嵌了 ASCII 双引号：{bad}'
    ddl = DDL.read_text(encoding='utf-8')
    stars = [ln[:60] for ln in ddl.splitlines()
             if re.search(r'[\u4e00-\u9fff]', ln) and '"' in ln]
    assert not stars, f'DDL 注释行的中文里嵌了 ASCII 双引号：{stars}'


# ============================================================ 五、真库现状（回填完才有意义）

def test_库里真的回填过_且非oper_score的行没有一个象限():
    if not _db_up():
        pytest.fail('库不通：真库现状必须实测，不许兜成跳过')
    r = T.engine.query_one(
        f"select count(*) n, count(distinct security_code) codes, "
        f"       count(distinct (security_code, launch_date, cohort)) events, "
        f"       min(launch_date)::text a, max(launch_date)::text z, "
        f"       count(*) filter (where bars < window_days) 短窗, "
        f"       count(*) filter (where capability_key <> 'oper_score' and quad_first is not null) 脏象限, "
        f"       count(*) filter (where rank_delta is not null and rank_delta is distinct from "
        f"         rank_first - rank_last) 不自洽, "
        f"       count(*) filter (where capability_key = 'oper_score' and coalesce(q1_days,0) "
        f"         + coalesce(q2_days,0) + coalesce(q3_days,0) + coalesce(q4_days,0) > bars) 天数越界, "
        f"       count(*) filter (where win_to >= launch_date) 含启动日, "
        f"       count(*) filter (where capability_key = 'market_mood') 混情绪族, "
        f"       count(*) filter (where window_days not in (10,20,30)) 怪窗口 "
        f"  from {T.TABLE} where spec_version = %s", (T.SPEC_VERSION,), role='reader')
    assert r['n'] > 0, '一张表空着 —— 回填没跑还是写库坏了？'
    assert int(r['脏象限']) == 0, f'{r["脏象限"]} 行非 oper_score 却带着象限：quad_columns() 那一道闸没挡住'
    assert int(r['不自洽']) == 0, f'{r["不自洽"]} 行 rank_delta 与 rank_first-rank_last 对不上（符号漂了）'
    assert int(r['天数越界']) == 0, f'{r["天数越界"]} 行 q1..q4 相加超过 bars'
    assert int(r['含启动日']) == 0, f'{r["含启动日"]} 行的窗口含了启动日当天'
    assert int(r['混情绪族']) == 0, 'market_mood 一族进了表（同日全市场同一个数，名次没意义）'
    assert int(r['怪窗口']) == 0
    print(f'\n  真库现状：{r["n"]} 行 / {r["codes"]} 只 / {r["events"]} 个事件，启动日 {r["a"]}…{r["z"]}，'
          f'其中窗口不满 {r["短窗"]} 行')
    # ⚠️ 这一条以前钉的是死日期「2026-07-29」（10-17 那晚量出来的可用批次起点）。10-19 夜评分
    # 回算了 220 场之后它就漂了：oper_score 与 env_resonance 的非 stale 名次现在都从
    # 2025-08-28 起（268 场）。**底线不许背日期，每次从库里取** —— 早于源自己可用批次最早一场
    # 才是要防的事（那意味着该被 is_stale 筛掉的旧系统行混进了表），日期本身会变。
    floor = T.engine.scalar(
        "select min(trade_date)::text from analytics.score "
        "where capability_key = 'oper_score' and not is_stale and rank_in_market is not null",
        role='reader')
    assert floor, '源里一场可用名次都没有 —— 这张表不该有任何行，去查 is_stale 与 rank_in_market'
    assert r['a'] >= str(floor), f'起点 {r["a"]} 早于源可用批次最早一场 {floor} ⇒ is_stale 那道筛掉了？'


def test_sql_那一路与纯判据对得上_真库一条轨迹逐列复算():
    """聚合下推给 SQL 的代价是：`lag` 数出来的翻转次数与 python 那三个纯函数是**两份实现**。
    这一条拿真库一条满窗轨迹把场次序列取回来重算，逐列与库里那一行对 —— 分叉当场红，
    而不是让屏幕上那句「最后一次翻转距 T-1 有 N 场」悄悄变成另一个数。"""
    if not _db_up():
        pytest.fail('库不通：这条对照必须连真库跑')
    row = T.engine.query_one(
        f"select security_code c, launch_date::text d, bars, window_days, quad_first, quad_last, "
        f"       quad_switches, quad_last_switch_gap, q1_days, q2_days, q3_days, q4_days, "
        f"       rank_first, rank_last, rank_delta, value_first, value_last, value_delta "
        f"  from {T.TABLE} where spec_version = %s and capability_key = 'oper_score' "
        f"    and bars = window_days order by launch_date desc limit 1", (T.SPEC_VERSION,), role='reader')
    assert row, '库里还没有一条满窗轨迹'
    tail = T.engine.query(
        "select trade_date::text d, quadrant from analytics.score "
        "where security_code = %s and capability_key = 'oper_score' and trade_date < %s::date "
        "  and not is_stale order by trade_date desc limit %s",
        (row['c'], row['d'], row['bars']), role='reader') or []
    series = [x['quadrant'] for x in reversed(tail)]
    assert len(series) == row['bars']
    assert T.switch_count(series) == row['quad_switches']
    assert T.last_switch_gap(series) == row['quad_last_switch_gap']
    assert T.quadrant_days(series) == {k: row[k] for k in ('q1_days', 'q2_days', 'q3_days', 'q4_days')}
    assert series[0] == row['quad_first'] and series[-1] == row['quad_last']
    assert row['rank_delta'] == T.rank_delta_of(row['rank_first'], row['rank_last'])
    assert float(row['value_delta']) == pytest.approx(T.delta_of(float(row['value_first']),
                                                                 float(row['value_last'])))


def test_不满窗的那些行首末读数与场数自洽_不许补出来的场():
    """bars<window_days 的行是覆盖事实：窗口只拿到几场就写几场。反过来说，**该拿到的都拿到了** ——
    库里那一行标的场数必须等于「启动日之前、往前 LOOKBACK 天内该票该键的可用场次」（不满窗时才相等，
    满窗的行是截到 30 场）。少数一场就是 bars 与窗口两头对不上，屏幕上那句「只拿到 12 场」是假话。"""
    if not _db_up():
        pytest.fail('库不通：这条真库核对不许兜成跳过')
    bad = T.engine.scalar(
        f"select count(*) from (select x.bars bars, x.window_days wd, "
        f"       (select count(*) from analytics.score s "
        f"         where s.security_code = x.security_code and s.capability_key = x.capability_key "
        f"           and not s.is_stale and s.trade_date < x.launch_date "
        f"           and s.trade_date >= x.launch_date - {int(T.LOOKBACK_DAYS)}) avail "
        f"  from {T.TABLE} x where x.spec_version = %s) y "
        f"where y.bars < y.wd and y.avail <> y.bars",
        (T.SPEC_VERSION,), role='reader')
    assert int(bad or 0) == 0, f'{bad} 行标着不满窗，可启动日之前那一段里还有没算进去的场次'


def test_anyfirst母体接上fb2_四板率不再是同义反复():
    """10-18 的 anyfirst：母体＝fb-2 全部首板（不筛结果）。若回填接的还是按结果筛的那批、
    或第二入口 join 断了，三档 seg4_rate 会照旧清一色 100 —— 看着齐，其实是假数。"""
    if not _db_up():
        pytest.fail('库不通：这条真库端到端不许兜成跳过')
    d = V.track(cohort='anyfirst', months=12, window=30, key='oper_score', limit=3)
    assert d['available'], d.get('reason')
    assert d['params']['cohort_text'] == '全部首板（不筛结果，能回答预示那一问）'
    assert d['base']['n'] > 0, 'anyfirst 一行都没有 —— 回填没跑还是写侧第二入口断了？'
    assert d['base']['seg4_rate'] is not None and d['base']['seg4_rate'] < 99.0, \
        f"anyfirst 档 4 板率 {d['base']['seg4_rate']}% —— 还是清一色 100 就说明这一档没接上 fb-2 " \
        f'或者仍按结果筛了（母体若是筛过的，这一问就没有分辨力）'
    assert d['coverage']['events'] > 0
    assert any(b['seg4_rate'] is not None for b in d['buckets']), 'anyfirst 档 buckets 没有一档有 4 板率读数'


def test_端点存在_参数交给读侧现筛():
    src = VR.read_text(encoding='utf-8')
    assert '@app.get("/api/quant/startup-quadrant-track")' in src
    seg = src[src.index('"/api/quant/startup-quadrant-track"'):]
    for tok in ('quadrant_track_view.track(', 'HTTPException(502', 'def quant_startup_quadrant_track(',
                'ge=1, le=12', 'ge=1, le=600'):
        assert tok in seg[:1800], tok
    assert 'return {"data"' in seg[:1800], '信封与 first-board-shape 同一风格'
