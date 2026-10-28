# -*- coding: utf-8 -*-
"""板块前三甲与相似板块（写侧 `app/compute/qd_sector_follow.py` + 读侧 `duanxian/qd_follow.py`）。

2026-10-25 这一族从"读侧现算 + 进程内缓存"改为"链上算好落 `qd.sector_follow`"，
所以钉的东西也跟着搬：

1. **判据与缺数出口**：名次距离/秩相关算不出必须交 `None`。折成 0 会被读成
   "完全重合"（距离）或"毫无相关"（相关），两个都是假读数，格子上还看不出来。
2. **相似匹配不许把缺行当成不像**：那几天没有行情的板块要计数后剔除，而不是补一个名次。
3. **母体与排名尺子只准有一处定义**（住在 `qd_sector`）。判法不是"两边写得像不像"，
   而是**读侧 SQL 里字面嵌着写侧那一段文本** —— 分叉的表现是一条真库用例：
   首页矩阵里的第 1 名和这块里的第 1 名不是同一个板块。
4. **读侧只读落库行**：源码里出现 `market_data.sector_daily`、`row_number`、
   `mean_rank_gap` 之类，就说明现算那套又长回来了（这次改掉的正是它）。
5. **跨日名次可比性的守卫两处共用一条**（`qd_common.universe_break`），
   并把断点如实落进 `deviation` → 界面念得出「跨日名次不可比」。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_qd_follow.py -q`
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

import pytest

from app.compute import qd_common as Q
from app.compute import qd_sector as ROT
from app.compute import qd_sector_follow as W
from duanxian import qd_follow as RD


DDL = pathlib.Path('app/db/sql/52_qd_sector_follow.sql').read_text(encoding='utf-8')
READ_SRC = pathlib.Path('duanxian/qd_follow.py').read_text(encoding='utf-8')


# ---------------------------------------------------------------- 纯判据（写侧）

def test_取几场随错开档变长():
    assert W.sessions_needed(0) == 6, '错开 0 场时是"前五场"那一条更深'
    assert W.sessions_needed(2) == 6, '模板要伸到 t-5 ⇒ 含当日共 6 场'
    assert W.sessions_needed(3) == 7, '错开 3 场时模板要伸到 t-6'
    assert W.sessions_needed(max(W.LAGS)) == 7


def test_平均名次差只认对齐的点():
    assert W.mean_rank_gap([10, 20, 30, 40], [12, 22, 28, 50]) == 4.0, '(2+2+2+10)/4'
    assert W.mean_rank_gap([1, 1, 1, 1], [1, 1, 1, 1]) == 0.0, '0 是"完全重合"，是真的 0'
    assert W.mean_rank_gap([1, 2, 3], [1, 2]) is None, '长度不等不比'
    assert W.mean_rank_gap([1, None, 3], [1, 2, 3]) is None, '任一边缺一格就整条不比对'
    assert W.mean_rank_gap([], []) is None, '空序列不是"距离 0"'


def test_秩相关三点起算算不出交空():
    assert W.rank_corr([1, 2, 3], [2, 4, 6]) == 1.0
    assert W.rank_corr([3, 2, 1], [1, 2, 3]) == -1.0
    assert W.rank_corr([5, 5, 5], [1, 2, 3]) is None, '全平没有相关可言，不是 0'
    assert W.rank_corr([1, 2], [1, 2]) is None, '两点不算相关'
    assert W.rank_corr([1, None, 3], [1, 2, 3]) is None


def test_相似榜剔除领涨板块自己也把缺行单独计数():
    dates = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5']
    series = {
        'LEAD': {d: r for d, r in zip(dates, [1, 50, 900, 5, 2, 3])},
        # 候选窗是 t..t-3（d0..d3），模板是 d2..d5 ⇒ A 的 d0..d3 与模板同形
        'A': {d: r for d, r in zip(dates, [880, 300, 6, 3, 200, 100])},
        # B 在 d1 没有行 ⇒ 不许补一个名次，只许进 skipped 计数
        'B': {d: r for d, r in zip(dates, [890, None, 8, 4, 1, 1])},
    }
    info = {c: {'sector_code': c, 'sector_name': c, 'sector_type': '概念'} for c in series}
    got = W.similar(series, info, dates, 'LEAD', lag=2, points=4, k=5, universe=914)
    codes = [i['sector_code'] for i in got['items']]
    assert 'LEAD' not in codes, '自己不能是自己的相似板块'
    assert codes == ['A'], codes
    assert got['skipped_incomplete'] == 1, '缺行的那只需要被点名，不能被算成"不像"'
    assert got['matched'] == 1 and got['incomplete_template'] is False


def test_模板不齐时明说不比对而不是给一张空榜():
    dates = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5']
    series = {'LEAD': {d: r for d, r in zip(dates, [1, 50, None, 5, 2, 3])},
              'A': {d: 10 for d in dates}}
    info = {c: {'sector_code': c, 'sector_name': c, 'sector_type': '行业'} for c in series}
    got = W.similar(series, info, dates, 'LEAD', lag=2, universe=914)
    assert got['incomplete_template'] is True and got['items'] == []
    assert got['matched'] == 0, '模板都不齐就没有"比对过 913 个"这回事'


def test_榜按距离升序距离并列时才看相关():
    """两条候选的平均名次差故意做成相等（各自 4 个差的和都是 80），此时要比同形程度。"""
    dates = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5']
    series = {
        'LEAD': {d: r for d, r in zip(dates, [1, 2, 10, 20, 30, 40])},
        # 模板 d2..d5 = [10,20,30,40]；差的绝对值 20,20,20,20 ⇒ 距离 20，顺序完全反着
        'SAME_DIST_REVERSED': {d: r for d, r in zip(dates, [30, 40, 10, 20, 1, 1])},
        # 同样距离 20，但候选窗 [30,40,50,60] 与模板同序
        'SAME_DIST_SAME_ORDER': {d: r for d, r in zip(dates, [30, 40, 50, 60, 1, 1])},
        'FAR': {d: r for d, r in zip(dates, [1, 2, 3, 4, 1, 1])},
    }
    info = {c: {'sector_code': c, 'sector_name': c, 'sector_type': '概念'} for c in series}
    got = W.similar(series, info, dates, 'LEAD', lag=2, k=3, universe=914)
    gaps = [i['gap_mean_rank'] for i in got['items']]
    assert gaps == sorted(gaps), '主排序必须是平均名次差从小到大'
    assert [i['sector_code'] for i in got['items']] == \
        ['SAME_DIST_SAME_ORDER', 'SAME_DIST_REVERSED', 'FAR'], got['items']
    same, rev = got['items'][0], got['items'][1]
    assert same['gap_mean_rank'] == rev['gap_mean_rank'] == 20.0
    assert same['rank_corr'] > rev['rank_corr'], '距离并列时同形的那条要排前面'


def test_轨迹前五场不含当日且缺行交None():
    dates = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5']
    series = {'A': {d: r for d, r in zip(dates, [1, 45, 847, 7, 1, 220])},
              'B': {d: r for d, r in zip(dates, [2, 333, 912, 205, None, 898])}}
    got = W.trajectory(series, 'A', dates)
    assert [c['trade_date'] for c in got] == dates[1:], '当日那一格不属于"前五场"'
    assert [c['rank'] for c in got] == [45, 847, 7, 1, 220]
    assert W.trajectory(series, 'B', dates)[3]['rank'] is None, '那天没行就是没行，不折成 0'


def test_基准日不在窗口里就一行不交():
    """不拿前一天冒充当天（读侧现算那版也是这条纪律，搬到写侧更要钉住）。"""
    rows = [{'trade_date': '2026-01-05', 'sector_code': 'TDX_A', 'sector_name': 'A',
             'sector_type': '概念', 'rnk': 1, 'universe_size': 10, 'pct_change': 3.0}]
    assert W.build_records(rows, ['2026-01-06'], '2026-01-06') == []


# ---------------------------------------------------------------- 单一来源

def test_母体与排名尺子只有一处定义():
    """尺子一分叉 = 首页两块自相矛盾。判法是 SQL 文本里字面嵌着写侧那一段，不是"看着像"。"""
    assert ROT.MOTHER_POOL in W.MATRIX_SQL, '母体那段必须是同一段文本，不是重抄一份'
    assert ROT.MOTHER_RANK_ORDER in W.MATRIX_SQL, '名称与代码两级 tie-break 必须同一条'
    assert "sector_type IN ('概念', '行业')" in ROT.MOTHER_POOL
    assert "LIKE 'TDX\\_%%'" in ROT.MOTHER_POOL, '只取通达信一族（L-15 同一条）'
    assert "split_part(d.sector_code, '_', 2)" in ROT.MOTHER_POOL, '无名板块的守卫也要在'
    # 脏数据上限只住在 qd_sector 一处，写侧那一族是**引用**它而不是重抄
    src = pathlib.Path('app/compute/qd_sector_follow.py').read_text(encoding='utf-8')
    assert 'MAX_SECTOR_PCT = ' not in src, '第二把 |涨幅| 上限 —— 两边一旦分叉就没人知道谁对'
    assert 'from app.compute import qd_sector as W' in src, '上限要从 qd_sector 引进来'
    assert "'cap': W.MAX_SECTOR_PCT" in src, '取数时用的必须是那一份'


def test_跨日可比性守卫两处共用一条():
    """板块持续性与板块轨迹判的是同一件事，不能各写一把 ±20%。"""
    assert Q.universe_break([448, 449, 448, 829]) is not None, '2026-09-23 那次扩容必须判得出'
    assert Q.universe_break([914, 915, 914, 914]) is None, '自然浮动（±2 只）不算断点'
    assert Q.universe_break([914]) is None, '一天没有"跨日"可言'
    assert Q.universe_break([None, None]) is None
    for name in ('app/compute/qd_sector.py', 'app/compute/qd_sector_follow.py'):
        assert 'Q.universe_break(' in pathlib.Path(name).read_text(encoding='utf-8'), name


def test_四档错开都落库_且与接口开的那四档对齐():
    assert W.LAGS == (0, 1, 2, 3)
    assert RD.LAG_CHOICES == W.LAGS and RD.DEFAULT_LAG == W.DEFAULT_LAG, '读侧不许自己另开一档'
    assert 'lag between 0 and 3' in DDL.lower(), 'DDL 的 CHECK 与常量要同一条（写超界会被库拒绝）'
    endpoint = pathlib.Path('vr/app.py').read_text(encoding='utf-8')
    assert 'Query(2, ge=0, le=3' in endpoint


def test_链上没有新增步数_判据接在qd_sector那一步里():
    rot_src = pathlib.Path('app/compute/qd_sector.py').read_text(encoding='utf-8')
    assert 'qd_sector_follow.refresh(' in rot_src, '落库必须接在同一步里'
    stages = pathlib.Path('ingest/jobs/e1_fill_all.py').read_text(encoding='utf-8')
    assert "('qd_sector',   'qd 轨板块轮动与持续性" in stages


# ---------------------------------------------------------------- 读侧：只读行

def test_读侧不再回母体也不再算判据():
    """这一条钉的是这次改动本身：现算那套一旦长回来，接口照样 200、页面照样有数，
    只是每个人重付两秒 —— 没有任何功能会红，所以只能靠源码断言拦住。"""
    for banned in ('market_data.sector_daily', 'row_number', 'mean_rank_gap(',
                   'rank_corr(', 'sessions_needed(', 'def similar', '_MATRIX_SQL'):
        assert banned not in READ_SRC, f'读侧不该出现「{banned}」—— 判据只住写侧'
    assert 'qd.sector_follow' in READ_SRC


def test_读侧取数串里的列都在DDL里():
    cols = [c.strip().split(' as ')[-1].strip()
            for c in RD._COLUMNS.replace('\n', ' ').split(',') if c.strip()]
    for c in cols:
        assert re.search(rf'\b{c}\b', DDL), f'{c} 不在建表脚本里'
    # `similar` 是 PostgreSQL 保留字：库里叫 similar_block，接口仍交 similar（前端契约不动）
    assert 'similar_block     jsonb' in DDL
    assert "'similar'" in READ_SRC or '\"similar\"' in READ_SRC


def test_口径句子六句且没有裸星号():
    need = {'leaders', 'trajectory', 'lag', 'similar', 'coverage', 'landing'}
    assert need <= set(RD.calibers()), set(RD.calibers()) ^ need
    for k, text in RD.calibers().items():
        assert len(text) > 30 and '**' not in text, k


def test_新端点只包一层读数层():
    src = pathlib.Path('vr/app.py').read_text(encoding='utf-8')
    assert '@app.get("/api/qd/sector-follow")' in src
    assert 'qd_follow.read(' in src, '端点必须只把读数层的返回包进 {data}，不在这里算'


def test_前端不重算名次与距离():
    src = pathlib.Path('frontend/src/components/qd/LeaderFollow.tsx').read_text(encoding='utf-8')
    for banned in ('Math.abs', 'row_number', 'sort((a, b)', 'reduce('):
        assert banned not in src, f'前端不该自己算（出现「{banned}」）—— 名次/距离/相关只住后端'
    assert 'data.calibers' in src, '口径句子要读后端那一份，不自写'
    assert 'cal.landing' in src, '落库批次这件事要在屏上说出来'
    assert '**' not in re.sub(r'/\*.*?\*/', '', src, flags=re.S), '代码里别留裸星号（星号门禁扫的就是这个形状）'


# ---------------------------------------------------------------- 真库（不打桩）

@pytest.fixture(scope='module')
def payload():
    d = RD.read()
    assert d.get('available'), d.get('reason')
    return d


def _j(v):
    """jsonb 到手的形状两边不一样：`engine.query`（psycopg3）直接给 list/dict，
    读侧那条 `pg.fetch_df`（psycopg2 + pandas）给字符串。比的是内容，不是包装。"""
    return json.loads(v) if isinstance(v, str) else v


def test_真库前三名与轮动矩阵的领涨板块是同一批(payload):
    """两张表各自落库、同一个母体 —— 打架就说明写侧的两条 SQL 分叉了。"""
    from app.db import engine

    rows = engine.query(
        "select day_rank, sector_code from qd.sector_rotation "
        "where trade_date = %s::date and day_rank <= 3 order by day_rank", (payload['date'],))
    assert len(rows) == 3, f'落库行不足三条：{rows}'
    assert [(r['day_rank'], r['sector_code']) for r in rows] == \
        [(l['day_rank'], l['sector_code']) for l in payload['leaders']], \
        '两张表的名次尺子分叉了（另见 test_母体与排名尺子只有一处定义）'


def test_真库每场每档都是前三甲各一行():
    """回算的完成判据是**库里的行**，不是作业说的 rc=0，也不是日志里的"完成"。"""
    from app.db import engine

    bad = engine.query(
        "select trade_date, lag, count(*) n from qd.sector_follow "
        "group by 1,2 having count(*) <> %s limit 5", (W.TOP_N,))
    assert not bad, f'有的场次每档不是 {W.TOP_N} 行（前三甲 × 四档）：{bad}'
    days = engine.scalar('select count(distinct trade_date) from qd.sector_follow')
    assert days and days > 1250, f'只回了 {days} 场，全史应覆盖 1,260+ 场'
    assert engine.scalar('select count(distinct lag) from qd.sector_follow') == len(W.LAGS)


def test_真库读侧交出的就是落库那一行(payload):
    """读侧只做组装与类型折算：把库里的 jsonb 原样读回来比一遍。"""
    from app.db import engine

    lead = payload['leaders'][0]
    row = engine.query_one(
        "select trajectory, similar_block, sessions, universe_size, spec_version, top_n "
        "from qd.sector_follow where trade_date = %s::date and lag = %s and sector_code = %s "
        "and spec_version = %s",
        (payload['date'], payload['lag'], lead['sector_code'], W.spec_version()))
    assert row is not None, '落库行读不到，payload 是从哪来的？'
    assert _j(row['trajectory']) == lead['trajectory']
    assert _j(row['similar_block']) == lead['similar']
    assert _j(row['sessions']) == payload['sessions']
    assert row['universe_size'] == lead['universe_size'] == payload['universe_size']
    assert row['top_n'] == payload['top_n'] == W.TOP_N
    assert payload['spec_version'] == W.spec_version()


def test_真库独立复算榜首相似板块的四场名次(payload):
    """不复用同一份内存里的数：另起一句 SQL、按显式日期数组自己走一遍。"""
    from app.db import engine

    lead = payload['leaders'][0]
    items = lead['similar']['items']
    assert items, f'{lead["sector_name"]} 一个相似板块都没匹配上：{lead["similar"]}'
    top = items[0]
    days = lead['similar']['candidate_days']
    assert len(days) == W.SHAPE_POINTS
    full = engine.query(
        """
        select sd.trade_date::text as d, sd.sector_code,
               row_number() over (partition by sd.trade_date
                                  order by sd.pct_change desc, si.name asc, sd.sector_code asc) as rnk
          from market_data.sector_daily sd
          join reference_data.sector_info si using (sector_code)
         where sd.trade_date = any(%s::date[])
           and si.sector_type in ('概念','行业')
           and sd.sector_code like 'TDX\\_%%'
           and sd.pct_change is not null
           and si.name <> split_part(sd.sector_code, '_', 2)
           and abs(sd.pct_change) <= %s
        """, (days, ROT.MAX_SECTOR_PCT))
    per_day = {r['d']: r['rnk'] for r in full if r['sector_code'] == top['sector_code']}
    assert [per_day.get(d) for d in days] == top['candidate_ranks'], \
        f'{top["sector_name"]} 的四个名次两边对不上：库 {per_day} 落库 {top["candidate_ranks"]}'


def test_真库的分母与距离换算自洽(payload):
    universe = payload['universe_size']
    assert universe and universe > 500, universe
    for lead in payload['leaders']:
        block = lead['similar']
        assert block['matched'] + block['skipped_incomplete'] == block['candidate_pool'], \
            f"{lead['sector_name']}：比对数 + 缺行数必须正好等于候选池，差一个就是有一边没算进来"
        assert block['candidate_pool'] >= universe - 1, \
            '候选池＝这一窗里在母体出现过行的板块数（并集），至少不该少于当日母体减自己；' \
            '少于就说明有一批板块被排名 SQL 悄悄丢了'
        assert len(block['items']) <= min(block['matched'], payload['k_similar'])
        assert block['template'] and block['template_days'], '模板与它用的那几天要一起交出'
        for s in block['items']:
            assert s['gap_mean_rank'] is not None and s['gap_mean_rank'] >= 0
            assert s['rank_corr'] is None or -1.0 <= s['rank_corr'] <= 1.0
            if s['gap_pct_of_universe'] is not None:
                assert abs(s['gap_pct_of_universe']
                           - round(s['gap_mean_rank'] / universe * 100.0, 2)) < 0.02
            assert s['sector_code'] != lead['sector_code'], '自己不能出现在自己的相似榜里'
            assert len(s['candidate_ranks']) == W.SHAPE_POINTS


def test_真库的批次年龄随数据交出(payload):
    """落库之后每次读都是同一批：年龄来自入库时刻，不是请求时刻。"""
    assert payload['cached'] is True
    assert isinstance(payload['cache_age_seconds'], int) and payload['cache_age_seconds'] >= 0
    stamp = payload['generated_at']
    assert stamp and dt.datetime.fromisoformat(str(stamp))
    # 再读一次，同一批 ⇒ 年龄只增不减（现算那版会造出一个"刚刚"）
    again = RD.read(date=payload['date'], lag=payload['lag'])
    assert again['generated_at'] == stamp
    assert again['cache_age_seconds'] >= payload['cache_age_seconds']


def test_跨母体扩容的窗口要落偏差码而不是静默():
    """合成一个有断点的窗口来钉这条守卫。**当前全史它一次都没发过**：实测 1,264 场里
    相邻两场母体偏离最大不到两成（库里 L-22 行数为 0）⇒ 不能拿"真库查不到 L-22"当它坏了，
    也不许在文档里写成它发过。"""
    uni = {'d0': 915, 'd1': 915, 'd2': 915, 'd3': 448, 'd4': 448, 'd5': 448}
    dev = json.loads(W._deviation({'incomplete_template': False}, ['d0', 'd1', 'd2', 'd3'], uni))
    codes = [x['code'] for x in dev]
    assert 'L-22' in codes and 'L-23' in codes and 'L-24' not in codes
    flat = json.loads(W._deviation({'incomplete_template': True}, ['d0', 'd1'], uni))
    assert 'L-24' in [x['code'] for x in flat], '模板不齐要单独记一条，不给一张看着正常的空榜'
    assert '**' not in json.dumps(dev + flat, ensure_ascii=False), '偏差原句要上屏，别留星号'


def test_落库的偏差在界面上有中文名():
    """屏幕上不念机器码（与全站 caveat 中文标签同一条纪律）；没登记的码照旧念码本身。"""
    got = RD._caveats([{'deviation': json.dumps([{'code': 'L-22', 'note': '母体 915→448'}],
                                                ensure_ascii=False),
                        'trajectory': [{'trade_date': 'd1', 'rank': None}]}], 5, 1)
    by = {c['code']: c for c in got}
    assert by['L-22']['label'] == '跨日名次不可比', by['L-22']
    assert 'partial_trajectory' in by, '轨迹里有空格就要点名'
    assert by['missing_day_row']['note'].startswith('三张模板共比对 5')


def test_真库早期场次模板凑不齐时说不比对而不是空榜():
    """库最早几场往前伸就是缺场 —— 这一档要落 L-24、置 incomplete_template，并在界面念出来。"""
    from app.db import engine

    n = engine.scalar("select count(*) from qd.sector_follow "
                      "where deviation @> '[{\"code\": \"L-24\"}]'::jsonb")
    assert n and n > 0, '全史里没有任何一条 L-24：早期那几场的模板缺场没被记下来'
    bad = engine.query("select trade_date, lag, sector_code from qd.sector_follow "
                       "where deviation @> '[{\"code\": \"L-24\"}]'::jsonb "
                       "and similar_block ->> 'incomplete_template' <> 'true' limit 3")
    assert not bad, f'记了 L-24 却没把 incomplete_template 置真：{bad}'
    d = RD.read(date='2021-08-09', lag=2)
    assert d.get('available'), d.get('reason')
    assert any(x['similar']['incomplete_template'] for x in d['leaders']), d['leaders']
    assert 'L-24' in {c['code'] for c in d['caveats']}, d['caveats']
    assert sum(len(x['similar']['items']) for x in d['leaders']) == 0, '模板不齐就不该有榜'


def test_没有落库行的那一场要把话说清():
    bad = RD.read(date='1999-01-04', lag=2)
    assert bad.get('available') is False
    assert 'leaders' not in bad, '不可用分支不许带榜键（带了前端就会渲染出一张空榜）'
    assert '没有落库行' in bad['reason'], bad['reason']


def test_真库整包能被JSON序列化(payload):
    """出现 NaN 字面量 = 出口清洗漏了字段，浏览器 JSON.parse 会炸整页空白。"""
    json.dumps(payload, allow_nan=False, ensure_ascii=False)
    for lead in payload['leaders']:
        for cell in lead['trajectory']:
            assert cell['rank'] is None or isinstance(cell['rank'], int)
        for s in lead['similar']['items']:
            assert s['rank_corr'] is None or isinstance(s['rank_corr'], float)
