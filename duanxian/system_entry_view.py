# -*- coding: utf-8 -*-
"""交易体系榜的**读数层**。判据与阈值只住 `app/compute/system_entry.py` 那一份。

这里刻意做两件事：

① 榜单只读算好的行，一块判据都不重算；
② **历史分布与榜单同批回**，且必须与同期基线并排 —— 这套筛法在本机数据上是
   "右尾更肥、期望更差"，把任何半句单独端出来都是骗人。统计读的是 `system_entry`
   自己的历史（不是另起一套 SQL 重新判一遍形态），否则页面榜单和下面的统计表
   迟早会用成两套口径。

次日/后 N 日一律 `lead` + 复利现算（`lag` 是前一天，这一条我实测错过一次）。
"""
from __future__ import annotations

from app.compute import system_entry as SE
from . import pg
from .quant import (_cache_caveat, _cav, _caveats, _dash, _fail, _int, _latest, _num, _one, _rows)

TABLE = 'analytics.system_entry'


def board(date: str = '', only_entry: bool = True, min_gap_pct: float | None = None,
          limit: int = 300) -> dict:
    """某一场的榜单。默认只列三条全过的；`only_entry=False` 给观察池（缺口成立但没确认）。"""
    d = _dash(date) or _latest(TABLE, spec=SE.SPEC_VERSION) or ''
    if not d or not pg.session_has(TABLE, d):
        return _fail('交易体系榜', TABLE, d or None)
    lim = max(1, min(int(limit or 300), 1000))
    cond, params = '', []
    cond += ' and (s.entry_ok or not %s)'
    params.append(bool(only_entry))
    if min_gap_pct is not None:
        cond += ' and s.gap_pct >= %s'
        params.append(float(min_gap_pct))

    sql = """
        select s.security_code as code, coalesce(sec.name, s.security_code) as name, sec.board,
               s.c1_ok, s.limit_up_count20, s.last_limit_up_date::text as last_limit_up_date,
               s.limit_up_ago, s.c2_ok, s.gap_date::text as gap_date, s.gap_ago, s.gap_pct,
               s.gap_unfilled, s.gap_floor_hfq, s.gap_vol_shares,
               s.c3_ok, s.pullback_sessions, s.pullback_down_cnt, s.pullback_max_vol_ratio,
               s.pullback_min_low_hfq, s.confirm_yang, s.vol_vs_prev,
               s.entry_ok, s.watch_ok, s.stop_ref_hfq, s.stop_dist_pct,
               s.close, s.close_hfq, s.pct_change, s.volume_shares, s.bars_used, s.missing,
               s.spec_version, s.calculated_at
          from """ + TABLE + """ s
          left join reference_data.security sec using (security_code)
         where s.trade_date = %s and s.spec_version = %s""" + cond + """
         order by s.entry_ok desc, s.gap_pct desc nulls last, s.stop_dist_pct asc nulls last,
                  s.security_code
         limit %s"""
    rows = _rows(sql, [d, SE.SPEC_VERSION] + params + [lim])
    if rows is None:
        return _fail('交易体系榜', TABLE, d)
    for r in rows:
        for k in ('gap_pct', 'gap_floor_hfq', 'pullback_max_vol_ratio', 'pullback_min_low_hfq',
                  'gap_vol_shares', 'vol_vs_prev', 'stop_ref_hfq', 'stop_dist_pct', 'close',
                  'close_hfq', 'pct_change'):
            r[k] = _num(r.get(k), 3 if k.endswith('hfq') or k == 'gap_pct' else 2)
        for k in ('limit_up_count20', 'limit_up_ago', 'gap_ago', 'gap_unfilled',
                  'pullback_sessions', 'pullback_down_cnt', 'bars_used'):
            r[k] = _int(r.get(k))
        for k in ('c1_ok', 'c2_ok', 'c3_ok', 'confirm_yang', 'entry_ok', 'watch_ok'):
            r[k] = bool(r.get(k))
    tot = _one(f"""select count(*)::int n,
                          count(*) filter (where entry_ok)::int entries,
                          count(*) filter (where watch_ok)::int watching,
                          max(calculated_at) as_of
                     from {TABLE} where trade_date = %s and spec_version = %s""",
               (d, SE.SPEC_VERSION)) or {}
    return {'available': True, 'date': d, 'rows': rows,
            'total': _int(tot.get('n')), 'entries': _int(tot.get('entries')),
            'watching': _int(tot.get('watching')), 'as_of': tot.get('as_of'),
            'only_entry': bool(only_entry), 'limit': lim}


# 历史分布**不落在这里**：三档读数要扫全市场票·日（250 场 ≈ 135 万行）并取每一根的未来 20 根，
# 实测 261 秒，而只读角色带 60 秒 `statement_timeout` —— 现算的第一后果不是慢，
# 是查询被超时咬死、界面把"没算出来"说成"这一场没有数据"（第一版就是这么写的，实测踩过）。
# 所以它由加工段算好落进 `analytics.system_entry_stats`（判据与那份 SQL 都在
# `app/compute/system_entry.py`），这里只读表。
#: 读侧要取的列 = SPEC 里那几个数 + 四列各自的分母 + 元数据。列头文字与说明都跟着 SPEC 走，
#: 这里不另写一遍中文列名（2026-10-22：原先中文列名手抄在组件里、说明写在 calibers 里，两处各说各话）。
_EXTRA_KEYS = tuple(SE._STATS_COUNTS)
_META_KEYS = ('bucket_key', 'bucket', 'window_days', 'window_from', 'window_to', 'missing',
              'bars_scanned', 'calculated_at', 'spec_version')


def history(window_days: int | None = None) -> dict:
    """三档（基线 / ①② / ①②③全过）在同一滚动窗口里的并排读数。

    随读数一起交出 `columns`（列头 + 逐列说明 + 哪一列与基线比色 + 分母是哪一列）：
    那张表的中文列名与"这列什么意思"只住 `app/compute/system_entry.py::STATS_COLUMNS_SPEC` 一份。
    """
    wd = int(window_days or SE.WINDOW_DAYS)
    head = _one(f"""select max(window_to) d from {SE.STATS_TABLE}
                     where spec_version = %s and window_days = %s""", (SE.SPEC_VERSION, wd))
    end = (head or {}).get('d')
    if not end:
        return {'available': False,
                'reason': (f'历史分布（{SE.SPEC_VERSION}／窗口 {wd} 场）还没算过 —— '
                           f'每日加工段 `sysentry` 那一步的末尾算它；手动补一发：'
                           f'python -m app.compute.system_entry --stats-only。'
                           f'这不是"市场里没有三条全过的票"，是这张聚合表里没行。')}
    cols = SE.stats_columns()
    measure_keys = [c['key'] for c in cols if c['key'] != 'bucket']
    select_keys = sorted(set(measure_keys) | set(_EXTRA_KEYS))
    sel = (['bucket_key', 'bucket', 'window_days',
            'window_from::text as window_from', 'window_to::text as window_to',
            'missing', 'bars_scanned', 'calculated_at', 'spec_version'] + list(select_keys))
    rows = _rows(f"""select {', '.join(sel)}
                       from {SE.STATS_TABLE}
                      where spec_version = %s and window_days = %s and window_to = %s
                      order by case bucket_key when 'baseline' then 0 when 'c12' then 1 else 2 end""",
                 (SE.SPEC_VERSION, wd, end)) or []
    if not rows:
        return {'available': False,
                'reason': f'历史分布读到了窗口 {end} 却没有行（加工段那一遍写坏了？重跑 --stats-only）'}
    for x in rows:
        for k in list(x):
            if k in _EXTRA_KEYS or k == 'n':
                # 分母与样本数是整数，过 `_num` 会变成 5,568.0 这种带小数的只数
                x[k] = _int(x.get(k))
            elif k not in _META_KEYS:
                x[k] = _num(x.get(k), 2)
        x['bars_scanned'] = _int(x.get('bars_scanned'))
    # 旧批次没算过的那几列：值与分母**同时**为空才算"这一列在这一窗没数"——
    # 只按值为空判会把"真的一根都没凑齐"说成"没跑过"（同一族纪律：缺数与没算不能混）。
    # 这里判的是 `is None`，不是"假值"：0.0 是合法的读数（平均刚好一个点没动），不算没数。
    uncomputed = [c['label'] for c in cols
                  if c.get('denom_key') and all(r.get(c['key']) is None
                                                and r.get(c['denom_key']) is None for r in rows)]
    return {'available': True, 'window': {'from': rows[0].get('window_from'),
                                          'to': rows[0].get('window_to')},
            'window_days': wd, 'spec_version': SE.SPEC_VERSION,
            'columns': cols, 'uncomputed_columns': uncomputed,
            'as_of': rows[0].get('calculated_at'), 'buckets': rows}


def thresholds() -> dict:
    return {'limit_lookback': SE.LIMIT_LOOKBACK, 'gap_min_pct': SE.GAP_MIN_PCT,
            'gap_unfilled_days': SE.GAP_UNFILLED_DAYS,
            'gap_ago_min': SE.GAP_AGO_MIN, 'gap_ago_max': SE.GAP_AGO_MAX,
            'pullback_window': SE.PULLBACK_WINDOW, 'seal_tol_yuan': SE.SEAL_TOL}


def calibers() -> dict:
    """口径原文只这一份，页面读它、不自已写一遍句子。

    ⚠️ 这里的键是**机器键，只给接口内部引用与对账用**（`controls` 取 `mother`、列头悬停取 `gap`/`stop`）。
    屏幕上那份字典念的是中文标题 —— 见下面 `CALIBER_TITLES`／`caliber_items()`
    （10-22 那条「看不懂」的根因就是字典头念 `mother`／`no_fish_pond` 这种标识符，
    细则见《页面展示工作指南/01》§十三之三）。
    """
    return {
        'mother': ('三条同时成立才算：① 近 20 个场次内封住过涨停；② 向上整段跳空缺口'
                   '（缺口日最低价高于前一日最高价，幅度 >= 1 个点）且之后 3 个场次最低价都没回补、'
                   '今日仍未回补；③ 回调段（今天往前 3 场）至少一根阴线且最大单日量不超过缺口日量，'
                   '今日收阳并站上昨收。'),
        'gap': ('缺口下沿 = 缺口日前一日最高（后复权）。"三天不回补"与"今日仍未回补"都按最低价判：'
                '只要有一天最低价摸回这条线，这个缺口就不再是证据。价格一律后复权，'
                '拿未复权价判跳空会在除权日造出一堆假缺口。'),
        'stop': ('止损参考位 = 缺口下沿（与判据同源：跌破就是这套体系的证据被推翻），'
                 '表里那一列 stop_dist_pct 是它相对今日收盘的百分比 —— **这一列全是负数**'
                 '（库里 12,014 行实测 −65.8% ~ −0.097%，没有正值：止损位必须在下方，'
                 '出现正数就是判据坏了）。越负 = 这一笔要冒的结构风险越远；接近 0 = 止损就在脚下，'
                 '一碰就走，看着"风险小"其实是没有空间。'),
        'timing': ('缺口日到今日限 4~7 个场次：3 场用来确认不回补，剩下 1~3 场就是"歇口气"那一段。'
                   '所以这个榜天然是窄的（库里 2025-10-14~2026-10-13 的 242 场实测：观察池平均 49.6 只/场、'
                   '三条全过平均 5.7 只/场，有的场次是 0）—— 窄是"组合"的意思，不是坏了。'),
        'history': ('这三档读的是 `analytics.system_entry_stats`：每日加工段算好落库，'
                    '基线与两档母体在**同一个滚动窗口、同一句查询**里并排出来 —— '
                    '所以上面那一列列的"比基线高/低"是同窗比出来的，不是拿两个时间段比。'
                    '本机落库后的结论（数字就在上面那三行，别只记一半）：这套组合**右尾更肥**'
                    '（后 20 场内冲出两成的比例明显高于基线），但**胜率与规则期望更低** —— '
                    '它筛的是"有浪"，不是"会涨"。'),
        'path': ('"带好止损"那两列：从当日收盘起 20 个场次内，盘中先碰到 +20% 还是先碰到 −7%'
                 '（按触发价成交；两个都没碰到的按第 20 场收盘算，就是最后一列那条规则的期望）。'
                 '写判据前扫过 +15/−5、+20/−7、+30/−12、+50/−15 四档止盈/止损配对，方向一致：'
                 '先止盈的概率高于基线，但先止损高得更多，扣完的期望每一档都不如基线。'),
        'no_fish_pond': ('"有资金的鱼塘"那一维（所属板块在热）**量过了，没做进母体**：'
                         '2026-10-14 用只读探针（.local/fish_pond_probe.py）在 1,370 例三条全过上按'
                         '「所属板块热度分位头部 20%」与「板块资金近 20 场头部 20%」各切一刀 —— '
                         'A 热 390 例（次日上涨 44.7%、后 20 场冲出两成 27.2%）与 A 不热 618 例'
                         '（45.0% / 26.9%）分不开，B 那把尺子同样分不开，而"查不到板块热度"那 362 例'
                         '右尾反而最高（30.9%）。所以这一维在本机数据上不给判别力，只留在这里说明它被量过。'
                         '另有一条偏差必须写：成分表 reference_data.sector_member 的 out_date 全是空，'
                         '拿今天的名单回看历史 = 未来函数，本来就不够格当判据。'),
    }


#: 字典上屏时念的中文标题（键仍是上面那份机器键，只是不再念给用户看）。
#: 少配一条就会把 `no_fish_pond` 这种标识符念到屏幕上，所以 `tests/test_system_entry.py`
#: 钉住「每一条都有标题、且标题不等于机器键」。
CALIBER_TITLES = {
    'mother': '三条同时成立才算',
    'gap': '缺口那一列（下沿与未回补）',
    'stop': '止损距那一列为什么全是负数',
    'timing': '这一屏本来就窄（4~7 场的择时窗）',
    'history': '这三档与每一列怎么读',
    'path': '先碰 +20% 还是先碰 −7%',
    'no_fish_pond': '「有资金的鱼塘」那一维：量过了，没做进母体',
}


def caliber_items() -> list:
    """上屏那一份字典：`{机器键, 中文标题, 句子}`（顺序＝`calibers()` 的顺序，两处同源）。"""
    return [{'key': k, 'title': CALIBER_TITLES.get(k) or k, 'text': v}
            for k, v in calibers().items()]


def calendar(days: int = 120) -> list:
    """这张表**有数的那些天**（日期选择器只列这些天 —— 让用户去选一个没算的日子没有意义）。"""
    rows = _rows(f"""select trade_date::text d, count(*)::int n,
                            count(*) filter (where entry_ok)::int entries
                       from {TABLE}
                      where spec_version = %s
                      group by 1 order by 1 desc limit %s""",
                 (SE.SPEC_VERSION, max(5, int(days)))) or []
    return [{'date': r['d'], 'rows': _int(r.get('n')) or 0,
             'entries': _int(r.get('entries')) or 0} for r in rows]


def read(date: str = '', only_entry: bool = True, min_gap_pct: float | None = None,
         limit: int = 300, days: int = 250) -> dict:
    b = board(date, only_entry=only_entry, min_gap_pct=min_gap_pct, limit=limit)
    if not b.get('available'):
        return b
    hs = history(window_days=days)
    caveats = _caveats('coverage') + _cache_caveat(('coverage:system_entry',))
    if hs.get('available') and hs.get('uncomputed_columns'):
        # 旧批次没算过的列要念出来：否则那几列整列 — 会被读成"这一档没有样本"，
        # 而真实原因是这张聚合表里那几列在这一窗还没落过数（重跑 --stats-only 就有）。
        caveats.append(_cav('stats_columns_missing',
                            f'历史分布里有 {len(hs["uncomputed_columns"])} 列在这一窗没数（'
                            + '、'.join(hs['uncomputed_columns'])
                            + '）：那是这几列落库前算的旧批次，补一发 '
                              'python -m app.compute.system_entry --stats-only 就有了 —— '
                              '不是"持有 N 天一个都没凑齐"。'))
    if not b['rows']:
        if min_gap_pct is not None:
            # 是"这一档把它们筛空了"，不是"这一场没有" —— 两者在屏幕上必须是两句话
            caveats.append({'code': 'system_entry_filtered_out',
                            'text': (f'交出 0 行是因为「缺口幅度不低于 {min_gap_pct:g} 个点」把它们筛空了：'
                                     f'这一场三条全过 {b["entries"]} 只、观察池 {b["watching"]} 只，'
                                     f'库里都有数。把下限调低就出来了 —— 这不是加工链没跑到这一天。')})
        else:
            caveats.append({'code': 'system_entry_empty',
                            'text': ('这一场没有满足全部三条的票 —— 这套体系本来就是窄的'
                                     '（缺一条就在观察池里）。切到"观察池"能看到缺口成立但还没确认的票；'
                                     '如果连观察池也是 0，那是加工链没跑到这一场，不是市场里没有跳空。')})
    return {**b, 'history': hs if hs.get('available') else {'available': False,
                                                            'reason': hs.get('reason')},
            'calendar': calendar(),
            'thresholds': thresholds(), 'calibers': calibers(),
            'caliber_items': caliber_items(), 'caveats': caveats,
            'spec_version': SE.SPEC_VERSION}
