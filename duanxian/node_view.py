# -*- coding: utf-8 -*-
"""「节点分析」的读侧共用块：格子里那句 `1` / `2` / `1, 2` / `—`，以及"判不了"要怎么出声。

判据只住 `app/compute/board_node.py` 一处。这一层只做三件事：按代码把落好的行取回来、
把"这一场读不读得动"算清楚、给调用方（首板页名单 / 首页连板榜）**同一规格**的字段。
两个调用点必须调这同一份实现 —— 各写一遍就会出现"同一格两种说法"，那是本项目吃过教训的那类重复。

三条不许破的读法：
  · `hits` 只有 `1` / `2` / `1, 2` / None 四种。None 在界面是「—」，**不等于"没命中"**：
    这一场库里没行、上证那天没数据、或这只票的连板窗口被截断，都是"判不了"。
    是哪一种由 `attach` 交出的 meta 点名，界面不许含糊成一句「—」。
  · 三板及以上那一格读的是**本轮首板日与二板日**的并集，不是当天的两条 —— 悬停必须说这句。
  · 剔除 ST／新股／没有名字的票，只作用于"前一交易日最高板梯队"那个样本，
    不作用于被检查的这只票本身。
"""
from __future__ import annotations

from . import pg
from .quant import _int, _num, _one, _rows

SPEC_VERSION = 'node-1'          # 与 app/compute/board_node.py 同一份；改判据两边一起升
TABLE = 'analytics.board_node_daily'

RULE_NOTE = (
    '节点 1：这一天这只票是**首板**，且上证指数当日跌幅 ≥ 1 个点，且当日成交量大于前一交易日。'
    '节点 2：这一天是**首板或二板**，且前一交易日全场最高连板 ≥ 6，且站在那个最高高度的那批票'
    '今天全部没有涨停（**断板与停牌都算没涨停**）。两条各自判断，同时命中写「1, 2」。'
    '三板及以上不看当天，看本轮连板的首板日与二板日，两天命中的编号取并集。'
    '连板全部由本地日线数出来（收盘 ≥ 涨停价 − 0.005 记封住；除权形态那根直接截断），'
    '不读东方财富涨停池。梯队样本先剔当日 ST、新股（名字 N/C 开头或日线不足 6 根）、'
    '以及名称表里没名字的票。')


def _code6_list(codes) -> list:
    out = [str(c or '').strip()[:6] for c in (codes or [])]
    return sorted({c for c in out if len(c) == 6})


def hits_by_code(codes, date: str) -> dict:
    """`{6 位代码: {hits, hit_reason, boards, prev_max_boards, prev_top_count, ...}}`。

    读不到库与"那场真的一条都没有"是两件事：这里只在库不通时交回空 dict，
    由 `attach` 的 meta 决定怎么说"没读到"。
    """
    pool = _code6_list(codes)
    if not pool or not pg.enabled():
        return {}
    rows = _rows(f"""
        select left(b.security_code, 6) as code, b.security_code, b.boards, b.node1, b.node2,
               b.hits, b.hit_reason, b.run_first_date::text run_first, b.run_second_date::text run_second,
               b.sh_pct, b.vol_vs_prev, b.prev_max_boards, b.prev_top_count, b.prev_top_sealed,
               b.prev_top_halted, b.prev_top_broke, b.sh_row_missing
          from {TABLE} b
         where b.trade_date = %s::date and b.spec_version = %s
           and left(b.security_code, 6) = any(%s::text[])""",
        (date, SPEC_VERSION, pool)) or []
    out = {}
    for r in rows:
        for k in ('sh_pct', 'vol_vs_prev'):
            r[k] = _num(r.get(k), 2)
        for k in ('boards', 'prev_max_boards', 'prev_top_count', 'prev_top_sealed',
                  'prev_top_halted', 'prev_top_broke'):
            r[k] = _int(r.get(k))
        r['sh_row_missing'] = bool(r.get('sh_row_missing'))
        out[r['code']] = r
    return out


def day_meta(date: str) -> dict:
    """这一场的覆盖与诊断：库里有几行、上证那天有没有那根日线、ST 退回快照几只、最新算到哪天。"""
    if not pg.enabled():
        return {'ok': False, 'reason': f'节点分析要读本地库 {TABLE}；未配置 .env 的 PG_USER/PG_PASSWORD 时没有这一项'}
    m = _one(f"""select count(*)::int rows,
                        bool_or(sh_row_missing) sh_missing,
                        max(st_fallback_count)::int st_fallback,
                        max(prev_max_boards)::int max_boards,
                        count(*) filter (where hits is not null)::int hit_rows,
                        count(*) filter (where hit_reason = 'run_window_broken')::int broken_rows
                   from {TABLE} where trade_date = %s::date and spec_version = %s""",
              (date, SPEC_VERSION)) or {}
    latest = _one(f'select max(trade_date)::text d from {TABLE} where spec_version = %s',
                  (SPEC_VERSION,)) or {}
    if not m.get('rows'):
        return {'ok': False, 'rows': 0, 'latest': latest.get('d'),
                'reason': (f'{TABLE} 里 {date} 这一场没有行'
                           f'（库里这一版算到 {latest.get("d") or "没有"}），'
                           '是**没算到**，不是这批票都没命中')}
    return {'ok': True, 'rows': _int(m.get('rows')), 'hit_rows': _int(m.get('hit_rows')),
            'sh_row_missing': bool(m.get('sh_missing')), 'st_fallback_count': _int(m.get('st_fallback')),
            'max_boards': _int(m.get('max_boards')), 'broken_rows': _int(m.get('broken_rows')),
            'latest': latest.get('d'), 'spec_version': SPEC_VERSION}


def attach(rows: list, date: str, key: str = 'code') -> dict:
    """把节点那一格挂到调用方已经有的行上，**原地**加三个字段并交出这一场的诊断。

    加的三个字段：
      · `node_hits`：`1` / `2` / `1, 2` / `''`（空串就是界面那个「—」）；
      · `node_reason`：不成立时差在哪一条（后端短码，页面按短码翻成人话，别自己编）；
      · `node_detail`：这一格的依据（板数、前一日最高板与那批的三种状态、上证涨跌幅、量比），
        给悬停与「判不了要说得出是哪一种」用。

    返回的 meta 里 `warn` 是一句可以直接上屏的话 —— 调用方只要把它摆出来就行，
    不许把它换成"这一场没有命中"。
    """
    got = hits_by_code([r.get(key) for r in rows], date)
    meta = day_meta(date)
    missing_codes = [str(r.get(key))[:6] for r in rows if str(r.get(key) or '')[:6] not in got]
    for r in rows:
        c = str(r.get(key) or '')[:6]
        g = got.get(c)
        if not g:
            r['node_hits'] = ''
            r['node_reason'] = 'no_db_row' if meta.get('ok') else (
                'db_unavailable' if not meta.get('rows') else 'not_computed')
            r['node_detail'] = None
            continue
        r['node_hits'] = g.get('hits') or ''
        r['node_reason'] = g.get('hit_reason') or ''
        r['node_detail'] = {
            'boards': g.get('boards'), 'run_first': g.get('run_first'), 'run_second': g.get('run_second'),
            'sh_pct': g.get('sh_pct'), 'vol_vs_prev': g.get('vol_vs_prev'),
            'prev_max_boards': g.get('prev_max_boards'), 'prev_top_count': g.get('prev_top_count'),
            'prev_top_sealed': g.get('prev_top_sealed'), 'prev_top_halted': g.get('prev_top_halted'),
            'prev_top_broke': g.get('prev_top_broke')}

    warn = ''
    if not meta.get('ok'):
        warn = meta.get('reason') or '节点分析这一场读不到（库不通或没这一场）—— 整列一律显示「—」，' \
                                     '但这不是"这批票都没命中"。'
    elif meta.get('sh_row_missing'):
        warn = ('这一场上证没有当天的那根日线 —— 节点 1 与"停牌"判定都不可信，'
                '整列的「—」要读成"判不了"，不是"没命中"。')
    elif missing_codes:
        warn = (f'有 {len(missing_codes)} 只票在 {TABLE} 这一场没有行'
                f'（{("、".join(missing_codes[:5]))}{(" 等" if len(missing_codes) > 5 else "")}）—— '
                '本机的连板判定只收当天封住涨停的票；池子说它涨停而库里没这一行时，'
                '先看日线与涨停价那两列当天有没有落，别把它读成"没命中"。')
    elif meta.get('broken_rows'):
        warn = (f'这一场有 {meta["broken_rows"]} 行的本轮连板起始日落在计算窗口之外 —— '
                '那一格按口径留「—」（不猜），要看得让判据侧把更早的那段补算出来。')
    meta = dict(meta)
    meta['warn'] = warn
    meta['missing_codes'] = missing_codes
    meta['rule'] = RULE_NOTE
    meta['attached'] = len(rows) - len(missing_codes)
    return meta
