# -*- coding: utf-8 -*-
"""「这只票的量化档案」的读数层 —— 把量化选股那一组各自算好的读数按单票拼到一起。

用户 2026-10-13 的原话：选中自选明细里的个股之后，「下面把这个个股的四维象限、趋势评分、
倍量形态、板块热度等等全部展现出来，就是量化选股内的信息，全部按照这个个股的角度」。

四条不是风格偏好的规矩：

1. **这里一块判据都不重算**。每一块读的都是那张表里已经落好的行；八分量明细直接叫
   `quant.scores()`（与 `/scores` 那一屏同一个函数），概念归属直接叫
   `pg_sources.concepts_by_code()`（与四维象限/倍量榜同一对函数），类型标签用各计算模块
   自己的 `TYPE_LABELS`。页面也就一个字段都不自己算。
2. **缺席要说清是哪种缺席**。三种读起来完全不同：
   ①查询失败（库不通／SQL 崩）→ `status=read_failed` 并把异常原文带出来；
   ②这一场那张表根本没算（榜没跑到这天）→ `status=not_computed`，措辞是「还没算到」；
   ③榜算了、这只票没进 → `status=not_on_board`，措辞是「它没进这一榜」，
   绝不能写成「它没有该形态」（那是把榜单读成判据的反面）。
   分辨靠一条恒有一行的聚合（`_presence`）。
3. **每块独立成／独立败**。一块读不到只让那一块不可用，整份档案照交 ——
   否则埋伏那一链在回算时，整个档案会跟着变空白。
4. **取数走 `app.db.engine` 的连接池**。一份档案要点开 20 多次查询，而 `duanxian/pg.py`
   每条都新连一次 psycopg2（实测 25 条 ≈ 7 秒）；池化后同一份 <0.5 秒。
   代价是失败会**抛出来**而不是兜成空表 —— 这正是这里要的：空表会被下游写成
   「这只票没进榜」那种听起来合理的假读数。
"""
from __future__ import annotations

from app.compute import ambush_setup as amb
from app.compute import breakout_signal as bs
from app.compute import confluence as CF
from app.compute import startup_volume as sv
from app.db import engine
from . import pg_sources
from . import ranking
from .quant import (QUADRANT_MEANING, _cache_caveat, _caveats, _coverage, _dash, _fail,
                    _full_code, _int, _labels, _latest, _num, _plain)

# 每一块读哪张表、判据版本是什么，只在这里定义一次；屏幕上的「这一屏覆盖哪几块」读的就是这份。
BLOCKS = (
    ('quadrant', '四维象限', '/quadrant', 'analytics.score × score_trend'),
    ('scores', '趋势评分八分量', '/grouped', 'analytics.score_detail'),
    ('volume_double', '倍量形态', '/volume-double', 'analytics.volume_double'),
    ('startup', '启动前量能画像', '/volume-double', 'analytics.startup_volume_profile'),
    ('sectors', '板块热度（所属板块）', '/sector-heat', 'sector_member × sector_heat_daily'),
    ('ambush', '埋伏票', '/volume-breakout', 'analytics.ambush_setup'),
    ('breakout', '突破信号', '/volume-breakout', 'analytics.volume_breakout_signal'),
    ('confluence', '选股汇总（交叉命中）', '/confluence', 'analytics.confluence_daily'),
)

# 这一组里**没有**按单票口径的东西，屏幕上要能念出来 —— 不说清就会被读成「这几块没做」。
NOT_COVERED = (
    ('/allocator', '分配者视角是板块级的过路费/温度/象限，单票没有这一格'),
    ('/watchlist-analytics', '自选组分析是组级聚合，不是单票'),
    ('复盘那一组（涨停池/首板/连板/情绪）', '那是「复盘」模块的口径，本页不混着算'),
)

HIST_SESSIONS = 10   # 每块带几行历史：够看出「连续在场」还是「今天偶然冒出来」


def _q(sql: str, params=()) -> list[dict]:
    """池化只读查询，值一律过 `quant._plain`（Decimal/日期/NaN → 可进 JSON 的形状）。

    失败直接抛：`pg.fetch_df` 把连接故障与「库里真没这行」都兜成 None，
    在这里会被读成「这只票没进榜」—— 那是假读数。
    """
    return [{k: _plain(v) for k, v in r.items()}
            for r in engine.query(sql, params or None, role='reader')]


def _q1(sql: str, params=()) -> dict | None:
    r = _q(sql, params)
    return r[0] if r else None


def _as_list(v) -> list:
    """text[] 经 `_plain` 是 list，`array_to_string`/库里本就是串 —— 两种都要认。

    不能拿一个「按逗号 split 串」的函数硬套 list：`str(['a','b'])` 会 split 出 "['a'"
    这种脏值，屏幕上就出现「命中 ['a' 榜」。
    """
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x is not None]
    if isinstance(v, str):
        return [x for x in v.split(',') if x]
    return []


def _gap(table: str, own: dict, day: str, date_col: str = 'trade_date') -> dict:
    """把「这块为什么没有这只票的行」定成两种之一，措辞由界面直接念。

    字段名统一叫 `reason`（不是 `text`）：界面只读一处，两种名字迟早有一种漏渲染。
    """
    board_latest = own.get('board_latest')
    if board_latest is None:
        return {'status': 'not_computed',
                'reason': f'`{table}` 里 {date_col} ≤ {day} 一场都没有（这一版判据还没算过），'
                          '这一块的读数还没生成 —— 不是这只票不符合形态'}
    return {'status': 'not_on_board',
            'reason': f'这张表在 {day} 之前最近的一行是 {date_col}={board_latest}，'
                      '里面有算过的日子、没有这只票的行 —— '
                      '意思是「它没进这一榜」，不是「它没有这个形态」查不回来了'}


def _presence(table: str, code: str, day: str, spec: str = '',
              date_col: str = 'trade_date') -> dict:
    """恒有一行的聚合：这只票在这张表里有几行、最近哪天、这张表最近算到哪天。

    `date_col` 必须给：`startup_volume_profile` 的主日期列是 `launch_date`，
    拿 `trade_date` 去问会直接 UndefinedColumn（本项目实测），而那条错差一点就被兜成「这块没数」。
    """
    own, p_own = [f'security_code = %s', f'{date_col} <= %s'], [code, day]
    board, p_board = [f'{date_col} <= %s'], [day]
    if spec:
        own.append('spec_version = %s')
        p_own.append(spec)
        board.append('spec_version = %s')
        p_board.append(spec)
    # 参数按占位符**出现次数**给：own 那段在 count 与 max 里各写了一次，少给一份
    # psycopg 就抛 IndexError（`list index out of range`）。
    return _q1(f"""select (select count(*) from {table} where {' and '.join(own)})::int own_rows,
                      (select max({date_col})::text from {table} where {' and '.join(own)}) own_latest,
                      (select max({date_col})::text from {table} where {' and '.join(board)}) board_latest""",
               p_own + p_own + p_board) or {}


def _err(name: str, e: Exception) -> dict:
    """异常原样带出来。兜成空值会让「没读到」听起来像「这只票没有」，那是假读数。"""
    return {'available': False, 'status': 'read_failed', 'as_of': None,
            'reason': f'{name}这一块的读数查询失败：{type(e).__name__}: {e}'}


# ------------------------------------------------------------------ 各块

def _blk_quadrant(code: str, day: str) -> dict:
    """四维象限：X 个股质量、Y 环境共振、oper 合成，加窗口趋势与象限移动串。"""
    caps = _q("""select capability_key, value, rank_in_market, percent_rank, quadrant, is_stale,
                       trade_date::text as as_of
                  from analytics.score
                 where security_code = %s and trade_date <= %s
                   and trade_date = (select max(trade_date) from analytics.score
                                      where security_code = %s and trade_date <= %s)
                   and capability_key in ('oper_score','stock_quality','env_resonance')
                 order by capability_key""", (code, day, code, day))
    by = {r['capability_key']: r for r in caps}
    oper = by.get('oper_score') or {}
    labels, labels_source = _labels()
    trend = _q("""select window_days, point_count, slope, up_streak_days, first_quadrant,
                        corr_coef, trade_date::text as as_of
                   from analytics.score_trend
                  where security_code = %s and trade_date <= %s
                    and trade_date = (select max(trade_date) from analytics.score_trend
                                       where security_code = %s and trade_date <= %s)
                    order by window_days""", (code, day, code, day))
    wd = _int(trend[0].get('window_days')) if trend else None
    moving = None
    if oper.get('quadrant') is not None:
        # 象限移动串：与 /quadrant 那一屏同一个写法（把窗口内每日象限按时间串起来）。
        # 窗口天数换算成日历天数的那一句 `×7/5` 也照抄那里，两处算出不同的窗才叫骗人。
        got = _q1("""select string_agg(quadrant, '→' order by trade_date) as s, count(*)::int n
                       from analytics.score
                      where security_code = %s and capability_key = 'oper_score'
                        and quadrant is not null and trade_date <= %s
                        and trade_date > %s::date - make_interval(days => %s)""",
                  (code, day, day, max(7, (wd or 10) * 7 // 5))) or {}
        moving = {'path': got.get('s'), 'sessions': _int(got.get('n')), 'window_days': wd,
                  'meaning': '窗口内每个交易日的象限按先后串起来，相邻重复的不折叠'}
    out = {'available': bool(caps), 'as_of': oper.get('as_of'),
           'oper': {'value': _num(oper.get('value'), 2), 'quadrant': oper.get('quadrant'),
                    'quadrant_label': labels.get(oper.get('quadrant')),
                    'meaning': QUADRANT_MEANING.get(oper.get('quadrant')),
                    'rank_in_market': _int(oper.get('rank_in_market')),
                    'percent_rank': _num(oper.get('percent_rank'), 2),
                    'is_stale': oper.get('is_stale')},
           'x_quality': _cap(by.get('stock_quality')),
           'y_env': _cap(by.get('env_resonance')),
           'labels_source': labels_source,
           'formula': 'oper_score = 0.40×个股质量 + 0.60×环境共振（口径与 /quadrant 同源）',
           'trend': [{**t, 'slope': _num(t.get('slope'), 4),
                      'corr_coef': _num(t.get('corr_coef'), 4),
                      'first_quadrant_note': '窗口「第一天」的象限，不是另一套口径'}
                     for t in trend],
           'quadrant_path': moving,
           'caliber': 'X/Y/oper 都是 analytics.score 的原值与当日全市场名次，这一屏不加权、不重排。'}
    if not caps:
        out.update(_gap('analytics.score', _presence('analytics.score', code, day), day))
    return out


def _cap(row) -> dict:
    row = row or {}
    return {'value': _num(row.get('value'), 2), 'percent_rank': _num(row.get('percent_rank'), 2),
            'rank_in_market': _int(row.get('rank_in_market')), 'has_row': bool(row)}


def _blk_scores(code: str, day: str) -> dict:
    """趋势评分八分量：**直接叫 /scores 那一屏用的同一个函数**，只挑要展示的字段。

    注册表权重的交叉核对（`registry_weights`）留在那一屏，这里带走 `weight_sum_ok`/
    `weight_mismatch` 两个布尔 —— 档案要能回答「凭什么是这个分」，但不重复一遍绑定表。
    """
    from .quant import scores as _scores

    got = _scores(code, day)
    if not got.get('available'):
        return {'available': False, 'status': 'not_computed', 'as_of': None,
                'reason': got.get('reason') or '趋势评分没读到（这一场 score 表里没有这只票）'}
    caps = []
    # 中文名与"这一项的名次能不能念"都由 `quant.scores()` 现查/现数交出（真源是
    # `config.capability.name`、`config.view_effective_binding.script_name` 与那一条分布聚合）。
    # 档案这一层**不再自己查 config.capability** —— 两处各查一遍就是两份真相源，
    # 改名或加一项能力时总有一处会慢半拍（用户 2026-10-22：整屏机器键看不懂）。
    for c in got.get('capabilities') or []:
        caps.append({'capability_key': c.get('capability_key'),
                     'capability_label': c.get('capability_name'),
                     'capability_note': c.get('capability_note'),
                     'value': _num(c.get('value'), 2),
                     'rank_in_market': _int(c.get('rank_in_market')),
                     'percent_rank': _num(c.get('percent_rank'), 2),
                     'rank_kind': c.get('rank_kind'), 'rank_note': c.get('rank_note'),
                     'tie_at_value': _int(c.get('tie_at_value')),
                     'distinct_values': _int(c.get('distinct_values')),
                     'universe': _int(c.get('universe')),
                     'quadrant': c.get('quadrant'), 'is_stale': c.get('is_stale'),
                     'component_count': _int(c.get('component_count')),
                     'weight_sum': _num(c.get('weight_sum'), 4),
                     'weight_sum_ok': c.get('weight_sum_ok'),
                     'weight_mismatch': c.get('weight_mismatch'),
                     'components': [{'component_key': p.get('component_key'),
                                     'component_label': p.get('component_name') or p.get('component_label'),
                                     'component_value': _num(p.get('component_value'), 4),
                                     'weight': _num(p.get('weight'), 4),
                                     'contribution': _num(p.get('contribution'), 4)}
                                    for p in (c.get('components') or [])]})
    return {'available': True, 'as_of': got.get('date'), 'capabilities': caps,
            'n_components': _int(got.get('n_components')),
            'names_source': got.get('names_source'),
            'caveats': got.get('caveats') or [],
            'caliber': '八项能力的分量、权重、贡献逐项来自 analytics.score_detail'
                       '（这一场这一只票实际用的那一份），中文名与名次可否念由 /scores 同一个函数现判、同一份数'}


def _blk_volume_double(code: str, day: str) -> dict:
    """倍量形态：这张表**全市场每票一行都存**（`app/compute/vol_double.py` 的 refresh），
    所以这里的 rank 是当日全场名次，不是「150 强里的第几名」。

    2026-10-19 起这一块的**主尺换成加权分** `dbl_energy`（与倍量榜同一把，判据只住
    `app/compute/volume_energy.py`）：档案与倍量页若各用一把尺，同一个名字在两个屏上
    就不是一个数了。旧的硬计数 `dbl_count` 仍随行交出，只作对照、不排名。
    """
    pres = _presence('analytics.volume_double', code, day)
    hist = _q("""select trade_date::text as d, dbl_energy, dbl_count, dbl_prev_count,
                        cross_count, dev_med, dev_max, above_ratio, ma60_chg, bars, ma60_days,
                        close_last, ma60_last
                  from analytics.volume_double
                 where security_code = %s and trade_date <= %s
                 order by trade_date desc limit %s""", (code, day, HIST_SESSIONS))
    rank = _q1("""with r as (select security_code, dbl_energy,
                                    rank() over (partition by energy_fp
                                                 order by dbl_energy desc nulls last) rk,
                                    count(*) over (partition by energy_fp) uni,
                                    count(dbl_energy) over (partition by energy_fp) scored
                               from analytics.volume_double where trade_date = %s)
             select rk, uni, scored, dbl_energy from r where security_code = %s""",
               (hist[0]['d'], code)) if hist else None
    # 「没有得分不占号」这条规则只住 `duanxian/ranking.py`（公用组件）：这一块的 rank 与
    # 倍量榜的 rank_in_day 必须是同一条规则、同一个分母，否则同一个名字在两个屏上是两个数。
    scored = rank.get('dbl_energy') is not None if rank else False
    out = {'available': bool(hist), 'as_of': hist[0]['d'] if hist else None,
           'board_latest': pres.get('board_latest'), 'rows_total': _int(pres.get('own_rows')),
           'latest': None, 'history': [],
           'rank_in_market': _int(rank.get('rk')) if (rank and scored) else None,
           'universe': _int(rank.get('uni')) if (rank and scored) else None,
           'universe_scored': _int(rank.get('scored')) if (rank and scored) else None,
           'rank_rule': ranking.RULE_NOTE_SHORT,
           'rank_by': 'dbl_energy',
           'caliber': '加权分＝近 40 场里每一天按「当日成交量 ÷ 前 5 日均量（不含当日）」落在哪一档，'
                      '取那一档的分值（一天计几分）累加：得分 = Σ 每档（这一档在窗口里出现的次数 × '
                      '这一档的分值）。档表与分值在「系统配置 → 倍量加权分档」里可改，所以这不是次数、'
                      '也没有系数／占比那一层，改档前的历史批与今天这批不能直接比。'
                      'dev_med＝那几天收盘价对 MA60 偏离的中位数（越小说明贴着均线）；'
                      '本表全市场每票一行都存，所以 rank_in_market 是按加权分的当日全场名次'
                      '（同分同名、下一名顺延；没得分的不占号，名次交 —）。'
                      '分母：universe＝这一场这一批的行数，universe_scored＝其中有分的只数。'
                      '这不是页面那张 150 强榜的名次。'
                      '对照口径：dbl_count 是旧的硬计数（日量比 ≥2 才 +1），仍随行交出、不参与排名。'}
    for r in hist:
        row = {k: r.get(k) for k in ('d', 'dbl_count', 'dbl_prev_count', 'cross_count',
                                     'bars', 'ma60_days')}
        row['dbl_energy'] = _num(r.get('dbl_energy'), 4)
        row.update({k: _num(r.get(k), 2) for k in ('dev_med', 'dev_max', 'above_ratio',
                                                   'ma60_chg', 'close_last', 'ma60_last')})
        out['history'].append(row)
    out['latest'] = out['history'][0] if out['history'] else None
    if not hist:
        out.update(_gap('analytics.volume_double', pres, day))
    return out


def _blk_startup(code: str, day: str) -> dict:
    """启动前量能画像：这张表按**启动日**存（一票多行＝历次启动），所以取最近几次启动。

    两个判据版本（sv-1/sv-2）会同时留着对账，读侧一律带版本条件 ——
    不带就会把同一次启动读成两次（实测 000678 的 2026-09-24 那次就是两行）。
    """
    pres = _presence('analytics.startup_volume_profile', code, day,
                     spec=sv.SPEC_VERSION, date_col='launch_date')
    rows = _q("""select launch_date::text as launch_date, cohort, board, equiv_limit_ups,
                       trigger_len, trigger_gain, window_days, bars_used,
                       up_days, down_days, flat_days, dbl_days, near_dbl_days, calm_days, still_days,
                       ratio_avg, ratio_median, ratio_p90, ratio_max, ratio_min, std_days
                  from analytics.startup_volume_profile
                 where security_code = %s and spec_version = %s and launch_date <= %s
                 order by launch_date desc, cohort limit %s""",
               (code, sv.SPEC_VERSION, day, 4))
    out = {'available': bool(rows), 'as_of': rows[0]['launch_date'] if rows else None,
           'spec_version': sv.SPEC_VERSION, 'board_latest': pres.get('board_latest'),
           'rows': [{**r, 'equiv_limit_ups': _num(r.get('equiv_limit_ups'), 3),
                     'ratio_median': _num(r.get('ratio_median'), 3),
                     'ratio_avg': _num(r.get('ratio_avg'), 3),
                     'ratio_p90': _num(r.get('ratio_p90'), 3),
                     'ratio_max': _num(r.get('ratio_max'), 3),
                     'ratio_min': _num(r.get('ratio_min'), 3),
                     'trigger_gain': _num(r.get('trigger_gain'), 2)} for r in rows],
           'caliber': '一行＝一次启动。bars_used＝启动日之前该股自己的 30 根有效日线里凑够的根数；'
                      '不足 10 根时那八个分布读数按口径留空，不是数据坏了。'}
    if not rows:
        out.update(_gap('analytics.startup_volume_profile', pres, day, 'launch_date'))
    return out


def _blk_sectors(code: str, day: str) -> dict:
    """板块热度：这只票**所属**的行业/地区板块 + 前 3 个概念，各自当日热度与全场名次。

    概念的选法（同名多板块码取成员最少的那一条、窄题材排前面）不在这儿重写，
    叫 `pg_sources.concepts_by_code` —— 四维象限榜与倍量榜用的是同一个函数。
    """
    heat_cte = """with heat as (select sector_code, heat, part_count,
                                      rank() over (order by heat desc nulls last) rk,
                                      count(*) over () uni
                                 from analytics.sector_heat_daily
                                where trade_date = %s and source = 'sector')"""
    mem = _q(heat_cte + """
        select i.sector_code, i.name, i.sector_type, h.heat, h.rk as heat_rank, h.uni, h.part_count
          from reference_data.sector_member m
          join reference_data.sector_info i using (sector_code)
          left  join heat h using (sector_code)
         where m.security_code = %s and i.sector_type in ('行业','地区')
           and (m.out_date is null or m.out_date > current_date)
         order by i.sector_type, h.rk nulls last""", (day, code))
    cons = pg_sources.concepts_by_code([code[:6]]) or {}
    got = cons.get(code[:6]) or {}
    # `concepts_by_code` 交出的是**顿号串**（它内部 `string_agg(name,'、')`），不是 list；
    # 整串塞进 `unnest(%s::text[])` 会报 malformed array literal（本项目实测）。
    names = [x for x in str(got.get('concepts') or '').split('、') if x]
    crows = []
    if names:
        # 同一个概念名可能挂在多条板块码上（`concepts_by_code` 的口径注释里就写着：减肥药有两条），
        # 而**不是每条码当天都有热度行**。实测 000678：「军民融合」三条码
        # （THS_D4F4 245 只、TDX_880950 195 只有热度、GN_880950 0 只）——
        # 按成员最多挑会挑到没热度那条，屏幕上就是「—」；按最少挑会挑到空壳码。
        # 规矩：**先取当天热度表里有记录的那条**，同热度记录里再取成员多的（那条才是热度榜上挂名的码）。
        # 名单本身与"哪三个概念"仍只由 `concepts_by_code` 决定，这里只补它没有的那两格。
        crows = _q(heat_cte + """,
               cand as (select i.name, i.sector_code,
                              (select count(distinct mm.security_code)
                                 from reference_data.sector_member mm
                                where mm.sector_code = i.sector_code) members,
                              count(*) over (partition by i.name) as codes,
                              h.heat, h.rk as heat_rank, h.uni, h.part_count,
                              row_number() over (partition by i.name order by
                                  (case when h.sector_code is null then 0 else 1 end) desc,
                                  (select count(distinct mm.security_code)
                                     from reference_data.sector_member mm
                                    where mm.sector_code = i.sector_code) desc,
                                  i.sector_code) pick
                         from unnest(%s::text[]) n(name)
                         join reference_data.sector_info i using (name)
                         left join heat h on h.sector_code = i.sector_code
                        where i.sector_type = '概念')
        select name, sector_code, members, codes, heat, heat_rank, uni, part_count
          from cand where pick = 1 order by members asc""", (day, names))
    out = {'available': bool(mem or crows), 'as_of': day,
           # 只交名次与分母，不替页面发明「百分位」—— 那个数一旦取了名就会被当成另一套热度口径。
           'sectors': [{'sector_code': r.get('sector_code'), 'name': r.get('name'),
                        'sector_type': r.get('sector_type'), 'heat': _num(r.get('heat'), 4),
                        'heat_rank': _int(r.get('heat_rank')), 'universe': _int(r.get('uni')),
                        'part_count': _int(r.get('part_count'))} for r in mem],
           'concepts': [{'name': r.get('name'), 'sector_code': r.get('sector_code'),
                         'members': _int(r.get('members')), 'codes': _int(r.get('codes')),
                         'heat': _num(r.get('heat'), 4), 'part_count': _int(r.get('part_count')),
                         'heat_rank': _int(r.get('heat_rank')), 'universe': _int(r.get('uni'))}
                        for r in crows],
           'concept_count': got.get('concept_count'),
           'heat_source': 'analytics.sector_heat_daily（当日、source=sector）',
           'caliber': 'heat_rank 是该板块当日在「全部有热度读数的板块」里的名次（分母见 universe）；'
                      '热度只在当天进了热度表的那些板块上有数，查不到就交 null（屏幕上是一格 —，'
                      '意思是"这张表今天没有这个板块"，不是"这个板块热度为零"）。'
                      '概念归属是二值的（在册/不在册），库里没有权重，'
                      '所以这里不交「该股在某概念的权重」。一个概念名可能挂多条板块码（`codes` 是条数），'
                      '热度取其中「当天有热度记录」的那条（多条都有时取成员多的）—— '
                      '三条码都没有记录时才交 null。'}
    if not out['available']:
        # 这张表按 sector_code 存，没有 security_code 那一列，所以不叫 `_presence`。
        bl = _q1("select max(trade_date)::text d from analytics.sector_heat_daily "
                 "where trade_date <= %s and source = 'sector'", (day,)) or {}
        out.update(_gap('analytics.sector_heat_daily', bl, day))
        out['reason'] = ((out.get('reason') or '') +
                         ' 另外：这只票在 sector_member 里没有行业/地区在册行、也没有概念归属 —— '
                         '「没有归属」与「热度没算」是两件事，上面那条 status 说的是后者')
    return out


def _blk_ambush(code: str, day: str) -> dict:
    """埋伏票（左侧机会）：这张榜只落**入选的票**，所以没行时要分清榜没算与没入选。"""
    pres = _presence('analytics.ambush_setup', code, day, spec=amb.SPEC_VERSION)
    rows = _q("""select trade_date::text as d, ma_spread_pct, close_hfq,
                       weekly_streak, weekly_ups6, weekly_bars, week_partial,
                       range20_pct, box_pos_pct, dev_ma20_pct, dev_ma60_pct, trend60_pct,
                       shrink_ratio, obv_ratio, days_since_big, hold_vs_big_pct,
                       ambush_types, strict_pass, ambush_score, bars_60, missing,
                       rank() over (partition by trade_date order by ambush_score desc
                                    nulls last, security_code) as rank_in_board
                  from analytics.ambush_setup
                 where security_code = %s and spec_version = %s and trade_date <= %s
                 order by trade_date desc limit %s""",
              (code, amb.SPEC_VERSION, day, HIST_SESSIONS))
    out = {'available': bool(rows), 'spec_version': amb.SPEC_VERSION,
           'as_of': rows[0].get('d') if rows else None,
           'board_latest': pres.get('board_latest'), 'days_on_board': _int(pres.get('own_rows')),
           'history': [], 'latest': None, 'type_labels': amb.TYPE_LABELS,
           'type_meanings': amb.TYPE_MEANINGS,
           'caliber': '埋伏分与那几类判据只住 `app/compute/ambush_setup.py`，这里读的是'
                      '已经落好的行；rank_in_board 是「当日榜内」名次（这张表只有入选的票），'
                      '与倍量/象限那种全场名次不是同一把尺子。'}
    for r in rows:
        types = _as_list(r.get('ambush_types'))
        row = {k: r.get(k) for k in ('d', 'weekly_streak', 'weekly_ups6', 'weekly_bars',
                                     'week_partial', 'strict_pass', 'bars_60', 'missing',
                                     'rank_in_board', 'days_since_big')}
        row.update({k: _num(r.get(k), 2) for k in
                    ('ma_spread_pct', 'close_hfq', 'range20_pct', 'box_pos_pct', 'dev_ma20_pct',
                     'dev_ma60_pct', 'trend60_pct', 'shrink_ratio', 'obv_ratio',
                     'hold_vs_big_pct', 'ambush_score')})
        row['ambush_types'] = types
        row['ambush_type_labels'] = [amb.TYPE_LABELS.get(t, t) for t in types]
        out['history'].append(row)
    out['latest'] = out['history'][0] if out['history'] else None
    if not rows:
        out.update(_gap('analytics.ambush_setup', pres, day))
    return out


def _blk_breakout(code: str, day: str) -> dict:
    """突破信号（缩量收敛后的首次突破）：最近几次信号 + 历次按类型的次数。"""
    pres = _presence('analytics.volume_breakout_signal', code, day, spec=bs.SPEC_VERSION)
    rows = _q("""select trade_date::text as d, signal_type,
                       last_surge_date::text as last_surge_date,
                       surge_vol_ratio, shrink_days, converge_days, break_vol_ratio, break_dev,
                       break_close, break_ma60, window_days, bars_used
                  from analytics.volume_breakout_signal
                 where security_code = %s and spec_version = %s and trade_date <= %s
                 order by trade_date desc limit %s""",
              (code, bs.SPEC_VERSION, day, HIST_SESSIONS))
    by_type = _q("""select k as t, count(*)::int n, max(trade_date)::text last_day
                      from analytics.volume_breakout_signal,
                           lateral unnest(%s::text[]) k
                     where security_code = %s and spec_version = %s and trade_date <= %s
                       and signal_type = k
                     group by 1 order by 1""",
                  (list(bs.TYPE_LABELS), code, bs.SPEC_VERSION, day))
    out = {'available': bool(rows), 'spec_version': bs.SPEC_VERSION,
           'as_of': rows[0].get('d') if rows else None,
           'board_latest': pres.get('board_latest'), 'rows_total': _int(pres.get('own_rows')),
           'history': [], 'latest': None,
           'type_counts': [{'signal_type': r.get('t'),
                            'signal_type_label': bs.TYPE_LABELS.get(r.get('t'), r.get('t')),
                            'n': _int(r.get('n')), 'last_day': r.get('last_day')} for r in by_type],
           'type_labels': bs.TYPE_LABELS, 'type_meanings': bs.TYPE_MEANINGS,
           'caliber': '三段式判据（启动放量 → 缩量收敛 → 首破均线且放量）只住 '
                      '`app/compute/breakout_signal.py`；bars_used 不足时窗口读数按口径留空。'
                      '这张表按信号日存行，所以「没行」＝那天它没出信号，不是形态消失。'}
    for r in rows:
        row = {k: r.get(k) for k in ('d', 'signal_type', 'last_surge_date', 'shrink_days',
                                     'converge_days', 'window_days', 'bars_used')}
        row.update({k: _num(r.get(k), 3) for k in ('surge_vol_ratio', 'break_vol_ratio',
                                                   'break_dev', 'break_close', 'break_ma60')})
        row['signal_type_label'] = bs.TYPE_LABELS.get(r.get('signal_type'), r.get('signal_type'))
        out['history'].append(row)
    out['latest'] = out['history'][0] if out['history'] else None
    if not rows:
        out.update(_gap('analytics.volume_breakout_signal', pres, day))
    return out


def _blk_confluence(code: str, day: str) -> dict:
    """选股汇总：最近一场命中几个榜、各榜名次，以及历史上重复出现过的天数。

    逐日明细不在这儿铺开（`/api/quant/confluence-history` 与 /confluence 那一屏已经有了），
    这里只交「有几天 ≥2 榜、最近一次是哪天」，够判断它是常客还是一闪而过。
    """
    pres = _presence('analytics.confluence_daily', code, day, spec=CF.SPEC_VERSION)
    row = _q1("""select trade_date::text as d, hits,
                       array_to_string(hit_sources, ',') as hit_sources,
                       ranks::text as ranks, sizes::text as sizes,
                       best_source, best_rank, best_rank_pct, source_count, sources_absent
                  from analytics.confluence_daily
                 where security_code = %s and spec_version = %s and trade_date <= %s
                 order by trade_date desc limit 1""", (code, CF.SPEC_VERSION, day))
    agg = _q1("""select count(*)::int days,
                       count(*) filter (where hits >= 2)::int repeat_days,
                       max(trade_date) filter (where hits >= 2)::text last_repeat_day
                  from analytics.confluence_daily
                 where security_code = %s and spec_version = %s and trade_date <= %s""",
               (code, CF.SPEC_VERSION, day)) or {}
    from .confluence_view import _json_dict   # jsonb 读不出形状就交 None，不交空 dict 冒充「一个名次都没有」

    hits = _as_list((row or {}).get('hit_sources'))
    absent = _as_list((row or {}).get('sources_absent'))
    out = {'available': bool(row), 'spec_version': CF.SPEC_VERSION,
           'as_of': (row or {}).get('d'), 'board_latest': pres.get('board_latest'),
           'days_in_boards': _int(agg.get('days')),
           'repeat_days': _int(agg.get('repeat_days')),
           'last_repeat_day': agg.get('last_repeat_day'),
           'hits': _int((row or {}).get('hits')),
           'hit_sources': hits,
           'hit_labels': [CF.SOURCE_LABELS.get(k, k) for k in hits],
           'ranks': _json_dict((row or {}).get('ranks')),
           'sizes': _json_dict((row or {}).get('sizes')),
           'best_source_label': CF.SOURCE_LABELS.get((row or {}).get('best_source')),
           'best_rank': _int((row or {}).get('best_rank')),
           'best_rank_pct': _num((row or {}).get('best_rank_pct'), 2),
           'source_count': _int((row or {}).get('source_count')),
           'sources_absent': absent,
           'sources_absent_labels': [CF.SOURCE_LABELS.get(k, k) for k in absent],
           'caliber': '命中榜数（hits）优先、其次各榜最好名次；没有合成总分。'
                      'ranks/sizes 是各榜名次与该榜当日的榜长 —— 名次要除以榜长才可比。'}
    if not row:
        out.update(_gap('analytics.confluence_daily', pres, day))
    return out


# ------------------------------------------------------------------ 出口

BLOCK_FNS = {
    'quadrant': _blk_quadrant,
    'scores': _blk_scores,
    'volume_double': _blk_volume_double,
    'startup': _blk_startup,
    'sectors': _blk_sectors,
    'ambush': _blk_ambush,
    'breakout': _blk_breakout,
    'confluence': _blk_confluence,
}


def _gather(sec: str, day: str, labels: dict) -> dict:
    """八块**并行**取。串行实测 ~3 秒，而块与块之间没有依赖（各自读自己那张表）。

    线程里只做「发一条只读 SQL 并把形状理干净」：不写库、不 print
    （本项目有一条纪律：守护/工作线程里 print 会撞 pytest 的全局捕获，变成假红）。
    一块抛出来的异常只让那一块 `read_failed`，不带走整份档案。
    """
    from concurrent.futures import ThreadPoolExecutor

    out: dict = {}
    with ThreadPoolExecutor(max_workers=len(BLOCK_FNS)) as ex:
        futs = {ex.submit(fn, sec, day): key for key, fn in BLOCK_FNS.items()}
        for fut, key in futs.items():
            try:
                out[key] = fut.result()
            except Exception as e:        # noqa: BLE001  留原文，不兜成空块
                out[key] = _err(labels.get(key, key), e)
    return out


def dossier(code: str, date: str = '') -> dict:
    """一只票的量化档案。任何一块失败都只让那一块不可用，整份照交。"""
    sec = _full_code(code)
    if not sec:
        return {'available': False, 'reason': '缺少 code 参数（6 位代码或带市场后缀的全称都行）'}
    day = _dash(date) or _latest('analytics.score') or ''
    if not day:
        return _fail('个股量化档案', 'analytics.score', None)
    try:
        ident = _q1("""select security_code as code, name, board, is_st,
                             list_date::text as list_date
                        from reference_data.security where security_code = %s""", (sec,))
        quote = _q1("""select trade_date::text as d, close, pct_change, turnover_rate_percent,
                             amount_yuan
                        from market_data.stock_daily
                       where security_code = %s and trade_date <= %s
                       order by trade_date desc limit 1""", (sec, day)) or {}
    except Exception as e:                     # noqa: BLE001  身份都读不到就只能整份不可用
        return {'available': False, 'status': 'read_failed', 'code': sec, 'date': day,
                'reason': f'本地库读这只票的身份/行情失败了（不是「这只票没有数据」）：'
                          f'{type(e).__name__}: {e}'}
    if not ident:
        return {'available': False, 'status': 'unknown_code', 'code': sec, 'date': day,
                'reason': f'reference_data.security 里没有 {sec} —— 代码写错了，或库里没有这只票'}

    labels = {k: lb for k, lb, _b, _t in BLOCKS}
    blocks = _gather(sec, day, labels)

    missing = [k for k, v in blocks.items() if not v.get('available')]
    cov_keys = ('score', 'volume_double', 'ambush_setup', 'confluence_daily')
    caveats = _caveats('coverage') + _cache_caveat(tuple(f'coverage:{t}' for t in cov_keys))
    if missing:
        caveats.append({'code': 'dossier_blocks_missing',
                        'text': '这几块这一场没有可用读数：'
                                + '、'.join(f'{labels.get(k, k)}（{blocks[k].get("status", "?")}）'
                                            for k in missing)
                                + ' —— 每块自己在下面交代是「查询失败」「榜还没算到这天」'
                                  '还是「算了但没进榜」，这里不合并、也不拿别块的数顶上。'})

    return {'available': True, 'code': sec, 'name': ident.get('name'),
            'board': ident.get('board'), 'is_st': ident.get('is_st'),
            'list_date': ident.get('list_date'),
            'date': day, 'requested_date': _dash(date) or None,
            'quote': {**quote, 'close': _num(quote.get('close'), 3),
                      'pct_change': _num(quote.get('pct_change'), 2),
                      'turnover_rate_percent': _num(quote.get('turnover_rate_percent'), 2),
                      'amount_yuan': _num(quote.get('amount_yuan'), 0)},
            'blocks': blocks,
            'boards_covered': [{'key': k, 'label': lb, 'board': bd, 'tables': tb}
                               for k, lb, bd, tb in BLOCKS],
            'not_covered': [{'where': b, 'why': w} for b, w in NOT_COVERED],
            'caliber': '这一屏不重算任何判据：每块读的都是那张表里已经落好的行；'
                       '缺席分三种（查询失败／榜没算到这天／算了但没进榜）并各说各的。'
                       '「没进榜」不等于「没有这个形态」—— 榜单是筛子，不是判据。',
            'coverage': _coverage(cov_keys),
            'caveats': caveats, 'source': 'pg'}

