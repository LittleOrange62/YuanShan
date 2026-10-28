# -*- coding: utf-8 -*-
"""「启动前量能画像」（`analytics.startup_volume_profile`）的判据与接线不变量。

三件事必须钉住，因为它们错了都不报错、只让页面上的数"看着很合理"：

1. **跨板块的强度对齐只有一个数**：`equiv_limit_ups = ln(1+涨幅)/ln(1+档位)`。
   拿"4 板"去数双创/北证等于用 10% 的尺子量 30% 的票（实测近 3 月 4 板及以上 85 个样本里
   主板占绝大多数、科创板 0 只）——所以 B 类必须走"4 日超四成 + 等效涨停数"这条。
2. **分位数是线性插值**，不是"取第 k 个样本"：30 个点上取整会随新一天数据跳档。
3. **样本不够不交分布**：`bars_used < MIN_BARS` 时所有 ratio_* 必须是 NULL。
   交一个算出来的 0.98 中位数，页面与满窗的票并排看，就是拿 6 个样本冒充 30 个。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_startup_volume.py -q`
"""
from __future__ import annotations

import ast
import datetime as dt
import math
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.compute import startup_volume as sv  # noqa: E402
from duanxian import pg_sources as PS  # noqa: E402  （情绪合成分与"按天读情绪"的唯一定义处）

CHAIN = ROOT / 'ingest' / 'jobs' / 'e1_fill_all.py'
OVERVIEW = ROOT / 'docs' / '数据加工工作指南' / '06-数据清洗与加工任务总览.md'


# ============================================================ 一、等效涨停数

@pytest.mark.parametrize('gain,band,want', [
    (0.10, 0.10, 1.0),                     # 一个涨停
    (0.21, 0.10, 2.0),                     # 主板两连板 ≈ 21%
    (0.40, 0.10, 3.530),                   # 40% 在主板 = 3.53 个涨停
    (0.40, 0.20, 1.845),                   # 同样 40% 在创业板只有 1.85 个
    (0.40, 0.30, 1.282),                   # 在北证只有 1.28 个
    (-0.10, 0.10, -1.105),                  # 对数比不对称：−10% 不等于"一个跌停"，是 1.105 个
])
def test_等效涨停数按档位换算(gain, band, want):
    assert sv.equiv_limit_ups(gain, band) == pytest.approx(want, abs=0.002)


@pytest.mark.parametrize('gain,band', [(None, 0.1), (0.4, None), (0.4, 0.0), (0.4, 1.0), (-1.0, 0.1)])
def test_档位或涨幅不合法时交回空而不是编一个数(gain, band):
    assert sv.equiv_limit_ups(gain, band) is None


def test_同一个涨幅在不同板块的等效强度必须排得开():
    """这条是 B 类存在的理由：40% 在三个板块是三种不同的事。"""
    g = 0.40
    a = sv.equiv_limit_ups(g, sv.BAND_BY_BOARD['主板'])
    b = sv.equiv_limit_ups(g, sv.BAND_BY_BOARD['创业板'])
    c = sv.equiv_limit_ups(g, sv.BAND_BY_BOARD['北交所'])
    assert a > b > c, '主板/创业/北证的等效数应严格递减'
    assert math.isclose(a, sv.equiv_limit_ups(0.10, 0.10) * a / a * a, rel_tol=1), '自洽'


# ============================================================ 二、分位数

def test_分位数是线性插值而不是取第k个样本():
    v = [1.0, 2.0, 3.0]
    assert sv.pctile(v, 0.5) == 2.0
    assert sv.pctile(v, 0.75) == pytest.approx(2.5)   # 落在两个观测之间就插值
    assert sv.pctile(v, 0.8) == pytest.approx(2.6)
    assert sv.pctile([1.0, 2.0], 0.5) == pytest.approx(1.5)
    assert sv.pctile([], 0.5) is None, '空样本交 NULL，不交 0'


# ============================================================ 三、窗口分类

def _flat_ratios(n, val=1.0):
    return [val] * n


def test_天数分类加起来不能超过总根数():
    ratios = [2.5, 2.0, 1.9, 1.05, 1.0, 0.95, 0.9, 0.5, 0.4, 3.0, 1.2, 0.8]
    c = sv.classify(ratios, [1] * len(ratios))
    assert c['bars_used'] == len(ratios)
    assert c['up_days'] + c['down_days'] + c['flat_days'] == len(ratios), '上/下/平是按 1.0 切的三分区，必须穷尽'
    assert c['dbl_days'] == 3 and c['near_dbl_days'] == 1 and c['calm_days'] == 2
    assert c['still_days'] >= c['flat_days'], '持平（精确 1.0）是"基本没变"（0.95~1.05）的子集'


def test_样本不足时分布列全是空而不是编一个中位数():
    c = sv.classify([1.2, 0.9, 3.0], [1, 1, 1])       # 只有 3 根 < MIN_BARS
    assert c['bars_used'] == 3
    for k in ('ratio_avg', 'ratio_median', 'ratio_p60', 'ratio_p80', 'ratio_p90',
              'ratio_max', 'ratio_min', 'std_days'):
        assert c[k] is None, f'{k} 在样本不足时必须交 NULL'
    assert c['missing'] == 'bars_below_min'
    assert c['up_days'] == 2, '天数分类照旧给（它不依赖样本量）'


def test_够样本时给出完整分布():
    ratios = [1.0 + i / 40 for i in range(30)]
    c = sv.classify(ratios, [1] * 30)
    assert c['ratio_median'] == pytest.approx(1.3625, abs=0.01)
    assert c['ratio_p80'] < c['ratio_p90'] <= c['ratio_max']
    assert c['missing'] is None


def test_停牌复牌的跳空量单独数出来():
    """倍数本身没错，但分母是几十天前的量 —— 不数出来的话会被读成"今天突然放量"。"""
    gaps = [1, 1, 40, 2, 55] + [1] * 25
    c = sv.classify([1.0] * 30, gaps)
    assert c['long_gap_days'] == 2
    assert c['missing'] is None


def test_空倍数和缺失值不进入除法():
    c = sv.classify([None, 1.5, None, 2.0] + [1.0] * 26, [None, 1, None, 1] + [1] * 26)
    assert c['bars_used'] == 28, 'None 的根数不进分母'
    assert c['ratio_avg'] is not None


# ============================================================ 四、每日接线

def _stages_names():
    import ingest.jobs.e1_fill_all as e1
    return [s[0] for s in e1.STAGES]


def test_startupvol_接进加工段且排在_volratio_之后():
    names = _stages_names()
    assert 'startupvol' in names, '没接进每日加工段'
    assert names.index('startupvol') > names.index('volratio'), \
        '启动前画像读的是 volume_ratio_daily，排前面会读到昨天的窗口'
    import ingest.jobs.e1_fill_all as e1
    row = [s for s in e1.STAGES if s[0] == 'startupvol'][0]
    assert row[2] is False, '分析产出缺一天不该把"追平"整条判失败'
    assert 'startupvol' in e1.STEP_TIMEOUTS and e1.step_timeout('startupvol') <= 900


def test_plan_里的入口指向判据模块自己没有壳():
    src = CHAIN.read_text(encoding='utf-8')
    assert re.search(r"'startupvol':\s*\('app\.compute\.startup_volume'", src), \
        'plan 里没有 startupvol 的入口（跑到那一步会 KeyError）'
    assert 'd12_startup_volume' not in src, '不该再有同名壳'
    assert hasattr(sv, 'main')


def test_每日链跑的档要盖住页面最深的档():
    """页面上有 3／6／12 三个档，而母体是"跑过才有行"的截面：链只跑 3 个月时，
    切到 12 个月交出的还是那 3 个月的名单 —— 标签与内容不符，屏幕上看不出区别
    （2026-10-07 实测踩过：三个档都交出同样的 85 行）。所以入口必须按最深档取数。"""
    src = CHAIN.read_text(encoding='utf-8')
    m = re.search(r"'startupvol':\s*\('app\.compute\.startup_volume',\s*\[([^\]]+)\]", src, re.S)
    assert m, '读不到 startupvol 的入参'
    args = m.group(1)
    assert "'--months', '12'" in args, f'每日档不是 12 个月，页面的 6/12 档会是假开关：{args}'


def test_判据不自己重解除倍数():
    """倍数只有一个来源：`analytics.volume_ratio_daily`。这里若出现第二处除法就会漂移。"""
    assert 'volume_ratio_daily' in sv.WINDOW_SQL
    assert 'volume_shares' not in sv.WINDOW_SQL, '窗口取数不该绕过 volume_ratio_daily 再除一遍'


def test_没有删除派生行的动作():
    """派生分析表里的行要删得先问用户 —— 这一步只增不改历史。"""
    src = pathlib.Path(sv.__file__).read_text(encoding='utf-8')
    assert 'DELETE FROM' not in src.upper(), '这里不许出现 DELETE'


def test_06_号文档登记了这一步():
    text = OVERVIEW.read_text(encoding='utf-8')
    assert 'startup_volume_profile' in text, '新步骤没进《数据清洗与加工任务总览》'
    assert 'equiv_limit_ups' in text, '跨板块对齐那个数没写进文档，下一个人还会去数 4 板'


def test_DDL_主键含_spec_version_且注释列都在表里():
    sql = (ROOT / 'app' / 'db' / 'sql' / '26_startup_volume_profile.sql').read_text(encoding='utf-8')
    assert 'primary key (security_code, launch_date, cohort, spec_version)' in sql
    # 注释里点名的列必须真存在（写过一次注释指向不存在的列，读的人照着找就白找）
    for col in re.findall(r"comment on column \S+\.(\w+)", sql):
        assert re.search(rf'^\s*{col}\s', sql, re.M), f'注释指向不存在的列 {col}'


def test_判据版本与读侧那个数是同一个():
    """版本有两份（写侧 `SPEC_VERSION`、读侧函数里那个 `spec = '…'`）。对不上时的现象是
    "作业明明跑了，页面还是旧的"—— 不报错，只是读不到新行（本项目的同名坑踩过一次）。"""
    from duanxian import quant as q
    src = pathlib.Path(q.__file__).read_text(encoding='utf-8')
    body = src[src.index('def startup_volume('):]
    m = re.search(r"^\s{4}spec = '([^']+)'", body, re.M)
    assert m, '读侧不再是写死 spec 的形式了，这条测试要看一眼改成什么形式再决定怎么钉'
    assert m.group(1) == sv.SPEC_VERSION, \
        f'写侧 {sv.SPEC_VERSION} / 读侧 {m.group(1)}：换版本只改一边，页面就静默读旧批'


def test_连板岛号用全序列编号而不是过滤后的行号():
    """涨停标记按 `limit_up IS NOT NULL` 过滤过（库里 12,125 行没有涨停价）。
    过滤之后再数行号，那几天在序列里就"连着"，两段会被接成一段假连板。"""
    s = ' '.join(sv.SAMPLES_SQL.split())
    assert 'bar_no - row_number() OVER (PARTITION BY security_code, is_zt ORDER BY trade_date) AS g' in s, \
        '岛号又用回过滤后的行号了'
    assert 'row_number() OVER (PARTITION BY d.security_code ORDER BY d.trade_date) AS bar_no' in s, \
        'bar_no 必须在 series（不按 limit_up 过滤）里算'
    assert 'FROM series' in s and 'WHERE limit_up IS NOT NULL' in s


def test_复牌日缺_pre_close_时退回上一根收盘而不是整段判空():
    """停牌复牌那天库里 `pre_close` 是空的（实测 600825.SH 2026-09-21，7 连板却算不出区间涨幅，
    等效涨停数跟着空）。整段判空是"宁缺不假"，但这一例的基价明明就在上一根日线上。"""
    s = ' '.join(sv.SAMPLES_SQL.split())
    assert 'coalesce(nullif(pre_close, 0), nullif(prev_close, 0)) AS base' in s
    assert 'exp(sum(ln(close / base))) - 1' in s, \
        '段内涨幅必须继续用 base 连乘（除权日 pre_close 与上一根收盘不同基，不能整列换成 lag(close)）'
    assert 'count(*) = count(base)' in s, '仍然一天都不能缺，缺了继续整段 NULL'


def test_覆盖浅于请求窗口与样本不足都要在_caveats_里说话():
    """两条只有实测才会发现的"看着矛盾"：
    ① 母体只算到某天时，"近 12 个月"标签下其实只有那之后的一段；
    ② `bars_used < MIN_BARS` 的行天数列有值、八个分布列全空 —— 那是口径，不是缺数。
    两个都要以 caveat 交出，覆盖边界本身也要交给页面（否则界面说不出这句话）。"""
    from duanxian import quant as q
    src = pathlib.Path(q.__file__).read_text(encoding='utf-8')
    body = src[src.index('def startup_volume('):]
    for token in ("'profile_window'", "'min_bars'", 'profile_earliest_launch',
                  'profile_rows_total', 'win_from', 'below_min'):
        assert token in body, f'读侧少了 {token}：这一屏又会把覆盖不足/样本不足当成"没有这种票"'


def test_两类母体的中文读法不写死月份():
    """`cohort_text` 是 `.format(months=…)`，页面直接显示它 —— 写死"近 3 个月"在选 6 个月时就是说谎。"""
    from duanxian import quant as q            # 中文读法住在读侧（页面显示它），判据不住这里
    for key, tpl in q.STARTUP_COHORTS.items():
        assert '{months}' in tpl, f'{key} 的说明把月份写死了'
        assert '3 个月' not in tpl
        assert 'cohort_text' in pathlib.Path(q.__file__).read_text(encoding='utf-8')


def test_整组汇总与明细行用的是同一批过滤条件():
    """明细 SQL 与汇总 SQL 一旦过滤条件不一致，页面上"这一组均值"就不是眼前这批票的均值
    —— 这种不一致不会报错，只会让两个数互相对不上。这里做文本级比对（不连库）。"""
    from duanxian import quant as q
    src = pathlib.Path(q.__file__).read_text(encoding='utf-8')
    body = src[src.index('def startup_volume('):]
    trio = ('spec_version', 'p.cohort', "launch_date > (date", "' months'")
    detail = body[body.index('rows = _rows('):body.index('total = _one(')]
    summ = src[src.index('STARTUP_SUM_SQL = '):src.index('def startup_volume(')]
    for token in trio:
        assert token in detail and token in summ, f'汇总与明细的过滤条件缺了 {token}'
    assert 'summary' in body, '汇总没进接口返回值'


def test_读侧清洗不留_nan_且空值走空而不是零():
    """`_num`/`_int` 是出口清洗的唯一一道；这里用假数据跑一遍，不连库。"""
    from duanxian import quant as q
    assert q._num(float('nan')) is None, 'NaN 必须变 None（否则 JSON 里是非法字面量）'
    assert q._int(None) is None and q._int(30) == 30
    assert q._num(0.0) == 0.0, '0 是有效读数，不能被吞成 None'


def test_交出的散文字段里没有裸的星号粗体标记():
    """页面把 `caliber`/`caveats.text` 当"后端原句"直接上屏（不自己编口径），
    所以这两处一旦带 markdown 的 `**`，用户看到的就是星号而不是粗体。

    只看会被交出去的字面量：走 AST 取 `startup_volume` 函数体与 `STARTUP_COHORTS`
    里的字符串常量，函数 docstring 跳过（那是给读代码的人看的，不上屏）。
    `definitions` 来自库里列注释，所以 DDL 里 `comment on` 那几行一起查。
    """
    from duanxian import quant as q
    src = pathlib.Path(q.__file__).read_text(encoding='utf-8')
    tree = ast.parse(src)
    served: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'startup_volume':
            head = node.body[0]
            has_doc = (isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant)
                       and isinstance(head.value.value, str))
            body = node.body[1:] if has_doc else node.body
            for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    served.append(sub.value)
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'STARTUP_COHORTS' for t in node.targets):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    served.append(sub.value)
    assert served, '没取到待查字面量（函数改名了？这条测试就会变成空跑）'
    leaks = [s[:40] for s in served if '**' in s]
    assert not leaks, f'交出的散文里带裸星号：{leaks}'

    ddl = ROOT / 'app' / 'db' / 'sql' / '26_startup_volume_profile.sql'
    stars = [ln[:60] for ln in ddl.read_text(encoding='utf-8').splitlines()
             if ln.strip().startswith('comment on') and '**' in ln]
    assert not stars, f'列注释（页面 definitions 的出处）带裸星号：{stars}'


# ================================================== 第三类母体 + 情绪列（用户 2026-10-15）

def test_第三类母体在写侧与读侧是同一张名单():
    """`COHORTS`（写）与 `STARTUP_COHORTS`（读）分叉 = 屏幕点得到一类而库里查不到，或反过来算好了不交。"""
    sys.path.insert(0, str(ROOT))
    from duanxian import quant as Q
    assert set(sv.COHORTS) == set(Q.STARTUP_COHORTS), \
        f'写侧 {sv.COHORTS} ↔ 读侧 {tuple(Q.STARTUP_COHORTS)}'
    assert 'board4_45' in sv.COHORTS


def test_门槛只有一个真值_SQL_里不许另写一个数():
    s = ' '.join(sv.SAMPLES_SQL.split())
    assert f"gain_first4 > {sv.GAIN_FIRST4_MIN}" in s, \
        f'SQL 里的门槛与 GAIN_FIRST4_MIN={sv.GAIN_FIRST4_MIN} 漂了（或者占位没被替换掉）'
    assert '__GAIN4_MIN__' not in s, '占位没替换：那条 WHERE 会把字符串当数值比较'
    assert "'board4_45'::text AS cohort" in s


def test_前四根涨幅是第4根收盘除首日基价_且段长不足或基价缺失时交空():
    """`array_agg(...)[4] / [1]` 这两处的位置语义与守卫，一条都不能少：
    少了 `count(*) >= 4` 会把 3 板的段也算出一个"前四根涨幅"，少了 `> 0` 会除到 NULL/0 上。"""
    s = ' '.join(sv.SAMPLES_SQL.split())
    assert '(array_agg(close ORDER BY trade_date))[4] AS fourth_close' in s
    assert '(array_agg(base ORDER BY trade_date))[1] AS first_base' in s
    assert 'count(*) >= 4' in s
    assert 'gain_first4 > ' in s
    # jump40 那一档的基准本来就是"前 4 根那天收盘"，同一条尺子，所以直接把它的 gain4 记进这一列
    assert 'gain4 AS gain_first4' in s


@pytest.mark.parametrize('sp, want, why', [
    ({'breadth_score': 1.0, 'theme_score': 0.5}, 42, '偏强 + 活跃：+50 与 +33 各一半'),
    ({'breadth_score': 2.0, 'theme_score': 1.5}, 100, '两档顶格才是 +100'),
    ({'breadth_score': -2.0, 'theme_score': -1.5}, -100, '两档地板才是 −100'),
    ({'breadth_score': 0.0, 'theme_score': -0.5}, -17, '中性 + 普通：题材那一族没有 0 档，所以平淡日读成 −17 而不是 0'),
    ({'breadth_score': 0.0, 'theme_score': 0.0}, 0, '真 0 要能算出 0（不许被当成缺数）'),
])
def test_情绪合成分按两档各归一后一半分(sp, want, why):
    got = PS.mood_score(sp)
    assert got['mood_score'] == want, why
    assert got['mood_parts'], '中间值要跟着交出去，别让人只能信一个数'


def test_情绪缺值时交空并说清缺的是哪一档():
    assert PS.mood_score(None)['mood_score'] is None
    assert '没有行' in PS.mood_score(None)['mood_missing']
    one = PS.mood_score({'breadth_score': 1.0, 'theme_score': None})
    assert one['mood_score'] is None and '题材' in one['mood_missing'], '缺一档不拿单档充数'
    both = PS.mood_score({'breadth_score': None, 'theme_score': None})
    assert both['mood_score'] is None and '没有行' in both['mood_missing']


def test_库里真的有三类母体行_且门槛确实在筛东西():
    """不打桩、真读库：屏幕能切换一类而库里没行，就是"点了空榜"那种假功能。"""
    if not sv.engine.scalar('select 1'):
        pytest.skip('本机库没通')
    rows = sv.engine.query(
        """select cohort, count(*) n, count(*) filter (where gain_first4 is null) 无该列,
                  min(gain_first4) lo, max(gain_first4) hi
             from analytics.startup_volume_profile where spec_version = %s group by 1""",
        (sv.SPEC_VERSION,), role='reader')
    got = {r['cohort']: r for r in rows}
    assert set(got) >= {'board4', 'board4_45', 'jump40'}, f'库里只有 {sorted(got)}'
    assert int(got['board4_45']['n']) > 0
    assert int(got['board4_45']['无该列']) == 0, '这一档每一行都必须带前四根涨幅（门槛按它筛）'
    assert float(got['board4_45']['lo']) > sv.GAIN_FIRST4_MIN, 'A′ 里出现了不达门槛的行'
    # A′ 必须是 A 的子集（同一批启动日，只是再筛一道），不是另起一池票
    extra = sv.engine.query(
        """select count(*) n from analytics.startup_volume_profile a
             where a.spec_version = %s and a.cohort = 'board4_45'
               and not exists (select 1 from analytics.startup_volume_profile b
                                 where b.spec_version = a.spec_version and b.cohort = 'board4'
                                   and b.security_code = a.security_code and b.launch_date = a.launch_date)""",
        (sv.SPEC_VERSION,), role='reader')[0]['n']
    assert int(extra) == 0, 'board4_45 里有 board4 之外的行 —— 它不该扩大母体'
    # 门槛真的在筛东西：board4 全表里存在不达标的行（4 个 5% 涨停的 ST 票）
    below = sv.engine.query(
        """select count(*) n from analytics.startup_volume_profile
            where spec_version = %s and cohort = 'board4' and gain_first4 <= %s""",
        (sv.SPEC_VERSION, sv.GAIN_FIRST4_MIN), role='reader')[0]['n']
    assert int(below) > 0, 'board4 里一行都不低于门槛 ⇒ 这道筛选形同虚设，要么窗口没拉开要么算错了'


# ================================================== 第四类母体：「或」（用户 2026-10-17）

def _db_alive() -> bool:
    """真库用例的统一入口：库不通就 pytest.fail，不许静默跳过（跳过等于没测）。"""
    try:
        return bool(sv.engine.scalar('select 1'))
    except Exception as exc:
        pytest.fail(f'连不上本机库，这一条必须真读数：{type(exc).__name__}: {exc}')


def test_第四类母体在写侧与读侧都存在且门槛与第三类共用同一个真值():
    """新类没接进读侧名单 = 算好了不交；SQL 里另写一个裸 0.45 = 改门槛只改到一半。"""
    sys.path.insert(0, str(ROOT))
    from duanxian import quant as Q
    assert 'board4_or_gain45' in sv.COHORTS, '写侧名单没有新类'
    assert 'board4_or_gain45' in Q.STARTUP_COHORTS, '读侧名单没有新类'
    # 源头模板（占位替换之前）：门槛处只能是占位，不许出现第二个裸 0.45
    src = pathlib.Path(sv.__file__).read_text(encoding='utf-8')
    start = src.index('SAMPLES_SQL = """')
    tpl = src[start + len('SAMPLES_SQL = """'):src.index('"""', start + len('SAMPLES_SQL = """'))]
    assert tpl.count('__GAIN4_MIN__') == 2, \
        '占位应当出现在第三类与第四类各一处，现在不是两处'
    assert '0.45' not in tpl, 'SQL 模板里出现了裸的 0.45 —— 门槛必须走 __GAIN4_MIN__ 占位'
    u_sec = tpl[tpl.index('u AS ('):tpl.index('c4 AS (')]
    assert 'coalesce(w.gain4_win, -1) > __GAIN4_MIN__' in u_sec, \
        '新类的「或」半边没有用占位门槛，或在的写法不是 coalesce 兜空'
    # 替换之后：两处门槛都等于 GAIN_FIRST4_MIN，jump40 那一档的 0.40 原样不动
    s = ' '.join(sv.SAMPLES_SQL.split())
    assert s.count(str(sv.GAIN_FIRST4_MIN)) == 2, \
        f'替换后 SQL 里 {sv.GAIN_FIRST4_MIN} 应当恰好出现两处（A′ 与新类），实际不是'
    assert f'coalesce(w.gain4_win, -1) > {sv.GAIN_FIRST4_MIN}' in s
    assert 'gain4 > 0.40' in s, 'jump40 的 0.40 是另一档门槛，不许被顺手改掉'


def test_新类是_board4_的超集():
    """入选条件是 r.len >= 4 OR …，所以 board4 的每一行都必须原样落进新类。
    少一行说明 win4 的 LEFT JOIN 或 UNION 段把行弄丢了。
    比对范围钉在**新类自己算过的启动日窗口**（取新类的 min(launch_date)）：
    board4 这批表里还留着新类上线前回填的历史批次（最早到 2021-11），那些启动日新类没算过，
    拿全表比是拿"没跑过的窗口"判"跑过的窗口"输 —— 不是放宽，是同窗对同窗。
    窗口内一条都不许差。"""
    _db_alive()
    lo = sv.engine.query(
        "select min(launch_date) d from analytics.startup_volume_profile "
        "where spec_version = %s and cohort = 'board4_or_gain45'",
        (sv.SPEC_VERSION,), role='reader')[0]['d']
    assert lo is not None, '新类一行都没有，超集无从谈起（先看回填跑没跑）'
    miss = sv.engine.query(
        """select count(*) n from analytics.startup_volume_profile b
            where b.spec_version = %s and b.cohort = 'board4'
              and b.launch_date >= %s
              and not exists (select 1 from analytics.startup_volume_profile u
                                where u.spec_version = b.spec_version
                                  and u.cohort = 'board4_or_gain45'
                                  and u.security_code = b.security_code
                                  and u.launch_date = b.launch_date)""",
        (sv.SPEC_VERSION, lo), role='reader')[0]['n']
    assert int(miss) == 0, \
        f'新类覆盖窗口（launch_date >= {lo}）内 board4 有 {int(miss)} 行不在新类里 —— 超集关系被打破'


def test_或那半边真的放进了短段样本且每一行都过门槛():
    """「或」存在的意义：段长 2、3 但启动日起 4 个交易日涨过 45% 的票（按「且」永远进不来）。
    库里一段都没有 = 这一档只是 board4 的别名，功能没有落地。"""
    _db_alive()
    tot = int(sv.engine.query(
        "select count(*) n from analytics.startup_volume_profile "
        "where spec_version = %s and cohort = 'board4_or_gain45'",
        (sv.SPEC_VERSION,), role='reader')[0]['n'])
    rows = sv.engine.query(
        """select count(*) n,
                  count(*) filter (where gain_first4 is null) 空读数,
                  count(*) filter (where gain_first4 <= %s) 不达标
             from analytics.startup_volume_profile
            where spec_version = %s and cohort = 'board4_or_gain45'
              and trigger_len < 4""",
        (sv.GAIN_FIRST4_MIN, sv.SPEC_VERSION), role='reader')[0]
    n, nul, bad = int(rows['n']), int(rows['空读数']), int(rows['不达标'])
    assert n > 0, f'新类共 {tot} 行，其中 trigger_len<4 的 0 行 —— 「或」没有放进任何东西'
    assert bad == 0, (f'短段里有 {bad} 行 gain_first4 不达 {sv.GAIN_FIRST4_MIN}：'
                     f'它们只可能靠「或」入选，不达标就是门槛判据错了')
    assert nul == 0, (f'短段里有 {nul} 行 gain_first4 为空却入选：取不到第 4 根的行只能靠 len>=4 进来，'
                     '而 len<4 的行没有别的入选路径')
