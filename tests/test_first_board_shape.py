# -*- coding: utf-8 -*-
"""「首板形状预示」（`analytics.first_board_shape`，fb-2）的判据、契约与读侧不变量。

这一屏回答两问：**启动前什么形状预示走到 4 板**、**预示次日开盘进场划不划算**。
母体是「每一个连板段的第一天」—— 当天收盘就能构造，所以段长 `seg_len` 在这里是**结果**不是筛子。
与 `startup_volume_profile`（sv-2，母体 `len>=4`，带未来函数）的分工就靠这条撑着，下面每一条都对应一个
"错了不报错、只给出一句听着合理的读数"的地方。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_first_board_shape.py -q`
真库那几条要连着 PostgreSQL 跑（本机是唯一库）；不连库时它们会红，不许改成跳过。
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

from app.compute import first_board_shape as F          # noqa: E402
from duanxian import first_board_view as V               # noqa: E402

DDL = ROOT / 'app' / 'db' / 'sql' / '42_first_board_shape.sql'
CHAIN = ROOT / 'ingest' / 'jobs' / 'e1_fill_all.py'
COVERAGE = ROOT / 'duanxian' / 'coverage.py'
VR = ROOT / 'vr' / 'app.py'
FE_LIB = ROOT / 'frontend' / 'src' / 'lib' / 'firstBoardShape.ts'
PAGE = ROOT / 'frontend' / 'src' / 'pages' / 'VolumeDouble.tsx'
COMPONENT = ROOT / 'frontend' / 'src' / 'components' / 'quant' / 'FirstBoardShapeBoard.tsx'


def _flat(sql: str) -> str:
    return ' '.join(sql.split())


# ============================================================ 一、母体与基准（判据）

@pytest.mark.parametrize('code, want', [
    ('688981.SH', '000680.SH'),        # 科创板：必须是 000680，不是 000688（那是上证科创 50 的另一个口子）
    ('600000.SH', '000002.SH'),
    ('300750.SZ', '399102.SZ'),
    ('000001.SZ', '399107.SZ'),
    ('920305.BJ', None),               # 北交所本机没有对应分类指数
    ('830799.BJ', None),
    ('430047.BJ', None),
])
def test_号段到分类指数那张表按前缀定档(code, want):
    assert F.bench_code(code) == want


def test_科创板前缀必须排在沪主板前面():
    """`startswith('6')` 会吃掉 688xxx —— 顺序一翻，科创板的超额基准就变成上证综指。"""
    assert [p for p, _ in F.BENCH_PREFIX][:2] == ['68', '6']
    assert F.bench_code('688001.SH') == '000680.SH'
    assert F.bench_code('600000.SH') == '000002.SH'


def test_超额的基准起点是进场当日指数开盘而不是收盘():
    """fb-1 拿**当日收盘**当基准 ⇒ ex1 的分子分母同日 ⇒ 基准恒为 0 ⇒ `ex1 == r1`，
    屏幕上那一列写着"超额"其实一个基准都没含。这条钉住 SQL 交 `open`、并且用它当起点。"""
    s = _flat(F.IDX_SQL)
    assert 'open, close' in s, '基准要有进场当日那个时刻，只取 close 就退化成 r1'
    src = pathlib.Path(F.__file__).read_text(encoding='utf-8')
    body = src[src.index('def collect('):src.index('def _num(')]
    assert ".get('o')" in body, '基准起点取的不是开盘'
    assert 'here = bp.get(' not in body, '终点必须走 {o,c} 字典（裸取日期会把整个 dict 当数字除）'
    assert ".get('c')" in body


def test_母体是每一个连板段的第一天_段长不当筛子():
    s = _flat(F.EVENTS_SQL)
    assert 'where is_zt group by' in s, 'runs 段要把非涨停日排除后才算段长'
    assert 'seg_len >= 2' not in s and 'seg_len >= 4' not in s and 'having count(*) >=' not in s, \
        '母体一旦按段长筛，就又把未来函数请回来了（那是 sv-2 那一屏的口径）'
    assert 'r.first_day between %(a)s::date and %(b)s::date' in s, '事件日要用区间两端筛，回看那段不算'


def test_连板岛号用未过滤序列的_row_number():
    """库里有一万二千行"没有涨停价"的日子。过滤之后再数行号，那些天在序列里是连着的，两段会被接成一段假连板。"""
    s = _flat(F.EVENTS_SQL)
    assert 'row_number() over (partition by d.security_code order by d.trade_date) as bar_no' in s
    assert 'bar_no - row_number() over (partition by security_code, is_zt order by trade_date) as g' in s
    head = s[:s.index('zt as')]
    assert 'limit_up is not null' not in head, 'bar_no 必须在还没筛涨停的 series 里算'


def test_段还在走的那些行用库尾之后有没有下一场判():
    s = _flat(F.EVENTS_SQL)
    assert 'exists (select 1 from market_data.stock_daily q where q.security_code = r.security_code '\
           'and q.trade_date > r.last_day) as seg_complete' in s, \
        'seg_complete 不是"段够长"，是"这段后面还有行情"——在走的段段长是下界'


def test_价格一律后复权_倍数只读_vr_那一列():
    s = _flat(F.PATH_SQL)
    assert 'coalesce(q.adjustment_factor,1)' in s, '不复权的区间涨幅在除权日必然算错'
    assert "q.spec_version = 'vr-1'" in s
    assert 'volume_shares' not in s and 'avg(' not in s, \
        '倍数只有一个来源（volume_ratio_daily）；这里再除一遍就会和那一屏漂移'


def test_财务按启动日往前推六十天取已披露那一期():
    s = _flat(F.FIN_SQL)
    assert "q.period_kind = 'cum'" in s
    assert 'q.report_date <= k.launch - %(lag)s' in s
    assert 'limit 1' in s
    assert F.DISCLOSE_LAG_DAYS == 60, '这个补偿天数是判据的一部分（同花顺只有报告期、没有披露日）'


@pytest.mark.parametrize('ratios, want', [
    ([1.0, None, 2.0, None, 0.5], {'bars': 3, 'dbl_days': 1, 'near_dbl_days': 0, 'calm_days': 1}),
    ([2.0, 1.9, 1.6, 1.59, 0.6, 0.59], {'bars': 6, 'dbl_days': 1, 'near_dbl_days': 2, 'calm_days': 1}),
    ([], {'bars': 0, 'dbl_days': 0, 'near_dbl_days': 0, 'calm_days': 0}),
])
def test_缺的格子不参与计数也不补零(ratios, want):
    """None 补成 0 会凭空造出一堆"缩量天数"；`1.59` 不算准倍量（等号归哪一头要写死）。"""
    got = F.shape_features(ratios)
    for k, v in want.items():
        assert got[k] == v, f'{k} 期望 {v} 实得 {got[k]}'
    assert got['r_avg'] is None if not ratios else got['r_avg'] is not None


def test_空窗口交回一整串空而不是零():
    got = F.shape_features([])
    assert got['bars'] == 0 and got['pre5'] is None and got['r_std'] is None
    for k in ('r_med', 'r_p80', 'r_p90', 'r_max', 'r_avg', 'base25_avg', 'last5_avg'):
        assert got[k] is None, f'{k} 缺数时必须是 None，0 会被读成"倍数全是 0"'


def test_前夜放量比在只有五根时给空而不是除以自己():
    got = F.shape_features([1.0] * 5)
    assert got['bars'] == 5 and got['base25_avg'] is None and got['pre5'] is None


def test_分位数与标准差和_pg_的同一条式子():
    """Python 侧的 `_interp`/`_std` 必须与 SQL 的 `percentile_cont`/`stddev_samp` 同一条规则，
    否则写侧存的 r_med 与读侧现算的分位对不上，"按哪一列分档"这句话就成了两套口径。"""
    s = [1.0, 2.0, 3.0, 4.0]
    assert F._interp(s, 0.5) == 2.5 and F._interp(s, 0.9) == 3.7 and F._interp([5.0], 0.8) == 5.0
    assert F._std(s) == pytest.approx(1.2910, abs=0.0001)
    assert F._std([1.0]) is None, '单根样本的标准差不存在（不是 0）'


def test_后复权路径凑不满的场次交空而不是零():
    """0 是"这 5 天平盘"，一条方向相反的结论；None 才是"没拿到"。"""
    path = [('2026-01-05', 10.0, 11.0, 11.5, 9.5), ('2026-01-06', 11.0, 10.45, 11.2, 10.0)]
    got = F.forward_metrics(10.0, path)
    assert got['r1'] == pytest.approx(10.0) and got['r3'] is None and got['r5'] is None
    assert got['maxup5'] == pytest.approx(15.0) and got['low5'] == pytest.approx(-5.0), \
        '最高/最低只数进场后前 5 场（15.0＝11.5/10−1、−5.0＝9.5/10−1）'
    assert got['entry_date'] == '2026-01-05' and got['entry_locked'] is False


def test_次日一字板标成买不进而不是当普通进场():
    """五年实测 1,864 条（约 3.1%）次日 high==low —— 挂单价在跌停式的一字板上根本成交不了。"""
    path = [('2026-01-05', 11.0, 11.0, 11.0, 11.0)]
    assert F.forward_metrics(11.0, path)['entry_locked'] is True
    got = F.forward_metrics(10.0, [])
    assert got['entry_locked'] is False and got['r1'] is None and got['entry_date'] is None


def test_超额在缺任一边时给空():
    assert F.excess(5.0, None) is None and F.excess(None, 5.0) is None and F.excess(None, None) is None
    assert F.excess(5.0, 1.25) == pytest.approx(3.75)


def test_分片一年一片_避开六十秒语句超时():
    """同一条 SQL 在 5 年窗口上实测 61 秒，而 reader 角色 60 秒就被服务端掐掉。"""
    chunks = F._chunks(dt.date(2021, 11, 1), dt.date(2026, 10, 16))
    assert len(chunks) == 5 and chunks[0][0] == dt.date(2021, 11, 1) and chunks[-1][1] == dt.date(2026, 10, 16)
    assert max((b - a).days for a, b in chunks) < F.CHUNK_DAYS
    assert F._chunks(dt.date(2026, 1, 1), dt.date(2026, 1, 1)) == [(dt.date(2026, 1, 1), dt.date(2026, 1, 1))]


# ============================================================ 二、写入契约（三处只有一份真值）

def test_列清单与库里的列一一对应():
    """`INSERT` 的列、占位符个数、`SET` 子句都从 COLS 生成；库里多一列少一列都会在这一发暴露。
    （fb-1 那次 50 列配 51 个占位符就是靠这条抓出来的。）"""
    if not F.engine.scalar('select 1'):
        pytest.fail('库不通：这张表的全部契约测试都要连真库跑，不许兜成跳过')
    rows = F.engine.query(
        "select column_name, ordinal_position from information_schema.columns "
        "where table_schema='analytics' and table_name='first_board_shape' order by ordinal_position",
        (), role='reader') or []
    db = [r['column_name'] for r in rows]
    assert set(db) - set(F.COLS) == {'calculated_at'}, \
        f'库里多出来的列（除了 calculated_at）：{set(db) - set(F.COLS) - {"calculated_at"}}'
    assert set(F.COLS) - set(db) == set(), f'COLS 里有库里没有的列：{set(F.COLS) - set(db)}'
    assert len(db) == 51 and len(F.COLS) == 50


def test_upsert_的占位符个数与冲突键就是那份列清单():
    assert F.UPSERT.count('%s') == len(F.COLS), '占位符与列数分叉：整批写入会 ProgrammingError'
    assert f"insert into {F.TABLE} ({', '.join(F.COLS)})" in F.UPSERT
    assert 'on conflict (security_code, launch_date, spec_version)' in F.UPSERT
    assert 'calculated_at' not in F.UPSERT.split('values')[0], 'calculated_at 有默认值，不进 INSERT 列表'
    assert 'calculated_at = now()' in F.UPSERT, '重算要刷新 calculated_at，否则读侧看不出这批是什么时候算的'
    for c in F.COLS:
        if c not in F._PK:
            assert f'{c} = excluded.{c}' in F.UPSERT, f'{c} 没进 SET：重算了但库里还是旧值'


def test_写库走_etl_角色而不是读角色():
    """etl 才有 30 分钟上限；用 reader 写会既没权限又撞 60 秒。"""
    src = pathlib.Path(F.__file__).read_text(encoding='utf-8')
    for seg in ('return engine.executemany(UPSERT, params, role=', 'engine.query(EVENTS_SQL'):
        assert "role='etl'" in src[src.index(seg):src.index(seg) + 260], seg
    assert 'DELETE FROM' not in src.upper(), '派生表的行要删得先问用户 —— 这一层只 upsert'


# ============================================================ 三、每日链与版本（两处必须同一个数）

def test_fbshape_接进每日加工段():
    sys.path.insert(0, str(ROOT))
    import ingest.jobs.e1_fill_all as e1
    names = [s[0] for s in e1.STAGES]
    assert 'fbshape' in names, '没接进每日链 ⇒ 判据表从此不再更新，页面上的"最近一场"会永远停在建表那天'
    assert names.index('fbshape') > names.index('volratio'), '启动前倍数读 volume_ratio_daily，排前面会读到昨天的窗口'
    row = [s for s in e1.STAGES if s[0] == 'fbshape'][0]
    assert row[2] is False, '分析产出缺一天不该把"追平"整条判失败'
    assert 'fbshape' in e1.STEP_TIMEOUTS and e1.step_timeout('fbshape') <= 900


def test_每日链用_recent_sessions_而不是_months():
    """每日只重算最近 40 场：`--months` 会把整段区间重新解析（一天 3 分钟的链跑不完）。"""
    src = CHAIN.read_text(encoding='utf-8')
    m = re.search(r"'fbshape':\s*\('app\.compute\.first_board_shape',\s*\[([^\]]+)\]", src, re.S)
    assert m, 'plan 里读不到 fbshape 的入参（跑到那一步会 KeyError）'
    assert "'--recent-sessions', '40'" in m.group(1), m.group(1)
    assert hasattr(F, 'main') and hasattr(F, 'sessions_back')


def test_写侧与读侧的版本号是同一个():
    assert V.SPEC_VERSION == F.SPEC_VERSION == 'fb-2', \
        f'写侧 {F.SPEC_VERSION} / 读侧 {V.SPEC_VERSION}：换版本只改一边，页面就静默读旧批'
    cov = COVERAGE.read_text(encoding='utf-8')
    assert f"and spec_version = '{F.SPEC_VERSION}'" in cov, \
        '覆盖体检那句还钉在旧批 ⇒ 这一张表会被天天报成"没跟上"（第三处版本号在 coverage.py 的字符串里，不是常量）'


def test_体检里那条基准退化的判据真的在():
    src = pathlib.Path(F.__file__).read_text(encoding='utf-8')
    body = src[src.index('def verify('):src.index('def sessions_back(')]
    assert 'ex1充数' in body and 'ex1 与 r1' in body, \
        'verify 不再数"ex1 等于 r1"的话，fb-1 那个假超额列就没人盯着了'


# ============================================================ 四、读侧：白名单、分档、诚实性

def test_白名单与控件清单是同一份():
    """界面上那份镜像迟早跟判据对不上（`fell_back` 那条 caveat 就是这么抓出来的）。"""
    opt = V.options()
    assert [b['value'] for b in opt['buckets']] == list(V.BUCKET_COLS)
    assert [m['value'] for m in opt['metrics']] == list(V.METRIC_LABELS)
    assert [m['value'] for m in opt['metrics']] == list(V.RESULT_COLS)
    assert opt['boards'][0]['value'] == '' and [b['value'] for b in opt['boards'][1:]] == list(V.BOARDS)


def test_分桶列只能来自白名单():
    """列名没法参数化 —— 白名单是唯一既拼不坏又不引注入的写法。这里给真注入串，要求它根本进不去。"""
    for bad in ('; drop table analytics.first_board_shape', '1=1', 'seg_len', 'name'):
        assert bad not in V.BUCKET_COLS
    assert 'seg_len' not in V.BUCKET_COLS, '段长是结果变量，拿它分桶就是自己筛自己'


def test_切点是左开右闭且一档不漏():
    """`col <= cut_i` 依次判，落到第一个满足的那一档 ⇒ (cut_{i-1}, cut_i]；最后一档不设上限。
    写成闭两端时切点值会重复计入第一档，屏幕上"每档合计"就大于全场。"""
    src = pathlib.Path(V.__file__).read_text(encoding='utf-8')
    assert 'when {bucket} <= %(cut{i})s then {i}' in src
    assert 'else {len(cuts)} end' in src, '最后一档必须有兜底，否则切点以上那批行整个消失'
    assert 'cuts = sorted({c for c in' in src, '四分位常落在同一个整数上，去重不做就会造出一档空档'


def test_聚合一次下推_python_不逐行分档():
    """5.8 万行拉到 python 里分档实测 7.3 秒，而这是一屏每改一个参数都要重算的界面。"""
    src = pathlib.Path(V.__file__).read_text(encoding='utf-8')
    assert 'grouping sets ((g), (g, y), (y), ())' in src
    assert 'grouping sets ((g), (g, y), ())' not in src, '少了 () 就没有全体基线，相对位置那句没得读'


def test_稳定判定用相对整族而不是与零同号():
    got = V._agree({'seg4_rate': 20.0, 'per_year': [{'year': '2023', 'n': 50, 'seg4_rate': 22.0},
                                                    {'year': '2024', 'n': 50, 'seg4_rate': 13.0}]},
                   {'seg4_rate': 15.0, 'per_year': [{'year': '2023', 'n': 900, 'seg4_rate': 18.0},
                                                    {'year': '2024', 'n': 900, 'seg4_rate': 14.0}]},
                   'seg4_rate')
    assert got == '1/2', '整族之上这一档 2023 年还在上（22>18）、2024 年掉到下（13<14）⇒ 一年稳一年不稳'
    assert V._agree({'seg4_rate': None, 'per_year': []}, {'seg4_rate': 1.0, 'per_year': []}, 'seg4_rate') is None
    thin = V._agree({'seg4_rate': 20.0, 'per_year': [{'year': '2023', 'n': 5, 'seg4_rate': 22.0}]},
                    {'seg4_rate': 15.0, 'per_year': [{'year': '2023', 'n': 900, 'seg4_rate': 18.0}]}, 'seg4_rate')
    assert thin is None, '两边样本都要够（≥8）才计入稳定判定，5 条的一年不配投票'


def test_出口清洗不留_nan_且零不是空():
    assert V._num(float('nan')) is None and V._num(float('inf')) is None
    assert V._num(0.0) == 0.0, '0 是有效读数（平盘），被吞成 None 就看不出"真的算出 0"'
    assert V._num(True) == 1.0 and V._num(None) is None
    assert V._num(dt.date(2026, 1, 5)) is None, '日期不是数字，不能带进 JSON'


def test_坏参数要出声地退回而不是悄悄退回():
    """屏幕上读的是退回之后的那个口径 —— 不说一句就是让用户拿错口径做决定。"""
    assert 'fell_back' in V._CAVEATS and '不在白名单里' in V._CAVEATS['fell_back']
    for key in ('seg_open', 'no_bench', 'thin', 'locked_out', 'one_bucket'):
        assert key in V._CAVEATS, f'少了 {key} 那条：这一屏又会把筛掉的东西说成"没有"'


def test_每一档的区间与切点一一对应_第一档不设下限():
    """`cuts = [1.0, 2.0]` 时屏幕上必须念成「≤1」「1～2」「>2」。
    10-17 首发实测交的是 Q1 `[2.0, 1.0]` —— `cuts[g-1]` 在第一档用负下标绕到了最后一个切点，
    页面老实照着拼句子，就成了「2～1（不含 2）」这种倒着的区间。不报错，只是这一档说的是另一段。"""
    d = V.board(months=12, bucket='dbl_days', n_buckets=4)
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
    assert all(len(r['samples']) <= 6 for r in rows)


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
    (V.__file__, {'CALIBER', 'DEFINITIONS', '_CAVEATS', 'BUCKET_COLS', 'METRIC_LABELS', 'board',
                  '_empty_by_bucket'}),
    (F.__file__, {'verify', 'main'}),
])
def test_交出的散文字段里没有裸的星号粗体标记(mod, consts):
    """页面把 `caliber`/`caveats`/`definitions` 当"后端原句"直接上屏，带 `**` 用户看到的就是星号。
    fb-1 那一版 CALIBER 里就有一个「段长是**结果**不是筛子」，靠这条抓出来。"""
    leaks = [s[:44] for s in _served_strings(pathlib.Path(mod), consts) if '**' in s]
    assert not leaks, f'交出的散文里带裸星号：{leaks}'
    ddl = DDL.read_text(encoding='utf-8')
    stars = [ln[:60] for ln in ddl.splitlines() if ln.strip().startswith('comment on') and '**' in ln]
    assert not stars, f'列注释带裸星号（会被别处当出处引用）：{stars}'


# ============================================================ 五、真库现状（回填完才有意义）

def test_库里真的回填过五年首板_且基准不再是退化那一版():
    if not F.engine.scalar('select 1'):
        pytest.fail('库不通：真库现状必须实测，不许兜成跳过')
    r = F.engine.query_one(
        f"select count(*) n, count(distinct security_code) codes, min(launch_date)::text a, "
        f"max(launch_date)::text z, "
        f"count(*) filter (where ex1 is not null and abs(ex1 - r1) < 0.0000005) ex1_eq, "
        f"count(*) filter (where bench_code is null) no_bench, "
        f"count(*) filter (where bench_code is null and ex1 is not null) fake_ex, "
        f"count(*) filter (where ex1 is not null) ex1_ok, "
        f"count(*) filter (where not seg_complete) open_seg, "
        f"count(*) filter (where dbl_days > bars) 越界 "
        f"from {F.TABLE} where spec_version = %s", (F.SPEC_VERSION,), role='reader')
    assert r['n'] > 40000, f'fb-2 只有 {r["n"]} 行 —— 五年回填没跑成（或写坏了）'
    assert r['a'] <= '2021-11-05' and r['z'] >= '2026-10-15', f'区间没铺满：{r["a"]} … {r["z"]}'
    assert r['ex1_eq'] == 0, f'{r["ex1_eq"]} 行 ex1 等于 r1 ⇒ 超额基准又退化成 0 了'
    assert r['ex1_ok'] > r['n'] * 0.9, f'ex1 只有 {r["ex1_ok"]}/{r["n"]} 有值 —— 指数那一族没取到？'
    assert r['no_bench'] > 0 and r['fake_ex'] == 0, \
        f'{r["fake_ex"]} 行没有基准却交出了超额 —— 那就是拿"没有基准"当"跑赢基准"（北交所 {r["no_bench"]} 行）'
    assert r['越界'] == 0, f'{r["越界"]} 行 dbl_days > bars：分类计数把同一天数进了两档'
    assert r['open_seg'] < r['n'] * 0.01, f'{r["open_seg"]} 行段还在走 —— 每日链的 --recent-sessions 没跟上？'


def test_首板日要覆盖区间内每一个交易日_断一天就是漏算():
    """每个交易日都有几十到三百条首板 ⇒ 中间断一场只可能是漏算（不是"那天没有首板"）。
    尾部那几场照旧有行，只是 r5/r10 为空，所以这里按"覆盖到的场数"判，不按行数判。"""
    mn = F.engine.scalar(f"select min(launch_date) from {F.TABLE} where spec_version = %s",
                         (F.SPEC_VERSION,), role='reader')
    mx = F.engine.scalar('select max(trade_date) from market_data.stock_daily')
    sess = F.engine.scalar('select count(distinct trade_date) from market_data.stock_daily '
                           'where trade_date between %s and %s', (mn, mx), role='reader')
    days = F.engine.scalar(f'select count(distinct launch_date) from {F.TABLE} where spec_version = %s '
                           'and launch_date between %s and %s', (F.SPEC_VERSION, mn, mx), role='reader')
    assert days >= int(sess) - 1, f'区间内 {sess} 个交易日只覆盖了 {days} 天首板 —— 中间断的那天要补跑'
    recent = F.engine.scalar(f'select count(*) from {F.TABLE} where spec_version = %s '
                             'and launch_date >= %s', (F.SPEC_VERSION, F.sessions_back(5)), role='reader')
    assert int(recent) > 100, f'最近 5 场只有 {recent} 条首板 —— 每日链那一步没跑（页面上"最近一场"会停在旧日子）'


def test_当日没有首板时那一跑按设计不写也不算失败():
    """rc 2 的语义要留在代码里：e1 那边把它折成"软失败"，两边各自解释一次就会分叉。"""
    src = pathlib.Path(F.__file__).read_text(encoding='utf-8')
    body = src[src.index('def main('):]
    assert "return 2" in body and '按设计不写，不算失败' in body
    assert 'fbshape' in CHAIN.read_text(encoding='utf-8')


# ============================================================ 六、端点与前端登记点

def test_端点存在_参数在白名单里现筛_并且登记进自检清单():
    src = VR.read_text(encoding='utf-8')
    assert '@app.get("/api/quant/first-board-shape")' in src
    seg = src[src.index('"/api/quant/first-board-shape"'):]
    for tok in ('first_board_view.board(', 'HTTPException(502'):
        assert tok in seg[:2500], tok
    assert 'def quant_first_board_shape(' in seg[:600], '端点函数名漂了（测试与文档都按它找）'
    cap = pathlib.Path(ROOT / 'frontend' / 'src' / 'pages' / 'System.tsx').read_text(encoding='utf-8')
    assert 'quant/first-board-shape' in cap, '接口在跑却没有一条自检盯着它'
    assert 'slow: true' in cap[cap.index('quant/first-board-shape') - 400:cap.index('quant/first-board-shape') + 400], \
        '这一发是重聚合（冷发 2 秒级），不进慢名单就会在自检里被当成卡死'


def test_前端把切点与稳定判定念出来而不是自己算():
    lib = FE_LIB.read_text(encoding='utf-8')
    for tok in ('rangeText', 'agreeText', 'coverageText', 'queryParams', 'columnsFor'):
        assert tok in lib, f'判读层少了 {tok}：组件又会自己重算一遍口径'
    assert '=== 0' not in lib or 'agreeText' in lib
    page = PAGE.read_text(encoding='utf-8')
    assert 'FirstBoardShapeBoard' in page, '这块没挂上 /volume-double，等于没交付'
    comp = COMPONENT.read_text(encoding='utf-8')
    assert 'useProbe' in comp and 'first-board-shape' in comp
    assert 'year_agree' in comp and 'cuts' not in comp.split('useMemo')[0][:400], \
        '组件不重算切点（切点只从后端读，页面上念区间）'
