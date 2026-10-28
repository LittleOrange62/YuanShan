# -*- coding: utf-8 -*-
"""交易体系榜**下面那一叠**：最近 6 个月里三条全过的个股名单（用户 2026-10-22 点名）。

用户要的是三件事，一件都不许含糊：

1. 母体＝这段时间**真的走完三条**的那些票·日（`entry_ok`，不含观察池），按符合日从近到远排；
2. 每一行交它的**后 1/2/3/5/10/15 个场次**的涨幅；
3. 每一行交它**前 2 场 ~ 后 5 场**（连当日共 8 格）在交叉命中榜里的样子 ——
   「比如四维象限 q3-q3-q1-q3-q3-q2-q3-q3，具体分数也换行罗列，其他排名也这样罗列」。

三件都只读**别处已经算好落库**的行，这一块一块判据都不重算：
涨幅读 `market_data.stock_daily`（后复权），位次读 `analytics.confluence_daily`
（各源的名次/榜长/原值是写侧 `app/compute/confluence.py` 的 `SOURCES` 落下的），
象限档位与分数读 `analytics.score`（`capability_key='oper_score'`）。

几处口径是这一层自己定的，都写在 `calibers()` 里、屏幕上念：

· **后 N 日一律是「累计」**（相对符合日收盘，后复权），单日涨幅另交一列给悬停 ——
  六列同一把尺；一半单日一半累计会被读成「持有越久越赚」（10-22 上面那块历史分布刚为同一件事换过一次尺）。
· **场次按市场场次编号，不按这只票自己的 K 线根数**（与 `duanxian/day_returns.py` 同一条）。
  停牌那天它没有行 ⇒ 那一格交 `None`（界面出 `—`），不拿更早/更晚的一天冒充，
  不然「10 日涨幅」会被说成「13 日涨幅」，而格子里看不出区别。
· **榜长/分母按「一天一榜」交一份全局表**，不逐行重复：672 行各带 8 个榜长会把 payload 撑到 1 MB，
  而那个数本来就与哪只票无关（`sizes`／`universe`／`stale_days`／`conf_days` 四份都是这样）。
· 整批一次算完要 ~8 秒（四条取数 + 672 行对齐），所以进程内缓存一小时并**自己交出年龄**
  （`generated_at`／`cache_age_seconds` ＋ `cached_aggregates` 那条 caveat），
  接口带 `refresh=1` 就现算 —— 回填之后读者要能看出自己看的是几点十分的库。
"""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any, Dict, List, Optional

from app.compute import confluence as CF
from app.compute import system_entry as SE
from . import pg
from .quant import (_cache_caveat, _cav, _caveats, _fail, _int, _latest, _num, _rows)

TABLE = 'analytics.system_entry'
CONF = 'analytics.confluence_daily'

MONTHS = 6                    # 默认窗口（自然月，不是场次）—— 用户点名「最近 6 个月」
PAGE_SIZE = 20                # 用户点名的一页行数
SPANS = (1, 2, 3, 5, 10, 15)  # 后 N 个场次（1 就是「次日」）
SEQ_BACK, SEQ_FWD = 2, 5      # 前 2 场 ~ 后 5 场：连当日共 8 格
#: 往前多取几场：窗口左端那一场自己的「前 2 场」也得在取回来的范围里
PAD_BACK = SEQ_BACK + 1
CACHE_TTL = 3600.0            # 与 `_AGG` 同一条：一小时，且年龄必须随数据交出
_CACHE: Dict[str, tuple[float, dict]] = {}

#: 六列涨幅各自上屏的名字与说明（只这一份，页面按它渲染表头与折叠说明）
SPAN_COLUMNS = (
    {'n': 1, 'label': '次日', 'cum': 'c1', 'day': 'day1',
     'note': '符合日收盘起第 1 个场次的累计涨幅 % —— 这一格累计与单日恒等（就是次日那一天）。'},
    {'n': 2, 'label': '第2日', 'cum': 'c2', 'day': 'day2',
     'note': '累计＝第 2 场收盘 ÷ 符合日收盘 − 1。悬停里另给第 2 场自己那一天的单日涨幅。'},
    {'n': 3, 'label': '第3日', 'cum': 'c3', 'day': 'day3',
     'note': '累计＝第 3 场收盘 ÷ 符合日收盘 − 1。悬停里另给第 3 场自己那一天的单日涨幅。'},
    {'n': 5, 'label': '5日', 'cum': 'c5', 'day': 'day5',
     'note': '累计＝第 5 场收盘 ÷ 符合日收盘 − 1。悬停里另给第 5 场自己那一天的单日涨幅。'},
    {'n': 10, 'label': '10日', 'cum': 'c10', 'day': 'day10',
     'note': '累计＝第 10 场收盘 ÷ 符合日收盘 − 1。悬停里另给第 10 场自己那一天的单日涨幅。'},
    {'n': 15, 'label': '15日', 'cum': 'c15', 'day': 'day15',
     'note': '累计＝第 15 场收盘 ÷ 符合日收盘 − 1。悬停里另给第 15 场自己那一天的单日涨幅。'},
)


def clear_cache() -> int:
    """丢掉本块的缓存（回填之后要看新数、以及测试要重新现算时用）。"""
    n = len(_CACHE)
    _CACHE.clear()
    return n


def _pct(last, base) -> Optional[float]:
    """涨幅（%）。缺任何一边都不返回 0 —— 0 会被读成「平盘」，而实情是那天它没有行。"""
    last, base = _num(last), _num(base)
    if last is None or base is None or not base:
        return None
    return round((last / base - 1) * 100.0, 2)


def _json_dict(v) -> Optional[dict]:
    """jsonb 过 pandas 会变字符串。读不出形状交 `None`，**不交空 dict** ——
    空 dict 会被下游写成「这一场它一个榜都没进」，那是假话（`confluence_view` 同一条闸）。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, (bytes, bytearray)):
        v = v.decode('utf-8', 'replace')
    if isinstance(v, str):
        try:
            got = json.loads(v)
        except ValueError:
            return None
        return got if isinstance(got, dict) else None
    return None


def _start_day(end: dt.date, months: int) -> dt.date:
    """自然月往回推，月末收小：3 月 31 日往回 6 个月落在 9 月 30 日（不许漂成 3 月 3 日）。"""
    y, m = end.year, end.month - months
    while m <= 0:
        m += 12
        y -= 1
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    last = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(end.day, last))


def _sessions(lo: str, hi: str) -> Optional[List[str]]:
    """市场场次表（ISO 串，升序）。取不到交 `None` —— 空的场次表会让每一行都算不出涨幅，
    那必须说成「取数失败」，不能说成「这一窗没人涨」。"""
    rows = _rows("""select distinct trade_date::text d from market_data.stock_daily
                     where trade_date between %s::date and %s::date order by d""", (lo, hi))
    return None if rows is None else [r['d'] for r in rows]


def _back_sessions(before: str, n: int) -> List[str]:
    """`before` 那一天往前数 n 场（升序）。

    ⚠️ 别写成 `trade_date between '1900-01-01' and …` 去数左端：`stock_daily` 一百多万行那句要扫全表，
    实测把整个读数从 4 秒拖到 25 秒，而这里只要左端前面那几场。
    """
    rows = _rows("""select distinct trade_date::text d from market_data.stock_daily
                     where trade_date < %s::date order by d desc limit %s""", (before, n)) or []
    return sorted(r['d'] for r in rows)


def _cohort(start: str, end: str) -> Optional[List[dict]]:
    """窗口内三条全过的行。判据本身读 `analytics.system_entry`，这里一块都不重算。"""
    return _rows(f"""select s.security_code as code, coalesce(sec.name, s.security_code) as name,
                            sec.board, s.trade_date::text as date, s.gap_pct,
                            s.gap_date::text as gap_date, s.gap_ago, s.gap_unfilled,
                            s.stop_dist_pct, s.close, s.pct_change, s.limit_up_ago,
                            s.pullback_max_vol_ratio, s.calculated_at
                       from {TABLE} s
                       left join reference_data.security sec using (security_code)
                      where s.spec_version = %s and s.entry_ok
                        and s.trade_date between %s::date and %s::date
                      order by s.trade_date desc, s.gap_pct desc nulls last, s.security_code""",
                 (SE.SPEC_VERSION, start, end))


def _bars(codes: List[str], lo: str, hi: str) -> Optional[Dict[tuple, float]]:
    rows = _rows("""select security_code as c, trade_date::text as d,
                           (close * coalesce(adjustment_factor, 1))::float as hfq
                      from market_data.stock_daily
                     where security_code = any(%s::text[])
                       and trade_date between %s::date and %s::date
                       and close is not null and close > 0""", (codes, lo, hi))
    if rows is None:
        return None
    return {(r['c'], r['d']): _num(r.get('hfq')) for r in rows}


def _conf(codes: List[str], lo: str, hi: str) -> Optional[tuple]:
    """交叉命中榜那些行 + 一份「一天一榜」的榜长表（`sizes` 本来就与哪只票无关，逐行重复是浪费）。"""
    rows = _rows(f"""select security_code as c, trade_date::text as d, hits,
                            ranks::text as ranks, sizes::text as sizes, metrics::text as metrics
                       from {CONF}
                      where spec_version = %s and security_code = any(%s::text[])
                        and trade_date between %s::date and %s::date""",
                 (CF.SPEC_VERSION, codes, lo, hi))
    if rows is None:
        return None
    out: Dict[tuple, dict] = {}
    sizes: Dict[str, Dict[str, int]] = {}
    for r in rows:
        d = r['d']
        cell = {'hits': _int(r.get('hits')), 'ranks': _json_dict(r.get('ranks')),
                'sizes': _json_dict(r.get('sizes')), 'metrics': _json_dict(r.get('metrics'))}
        out[(r['c'], d)] = cell
        for k, v in (cell['sizes'] or {}).items():
            n = _int(v)
            if n is not None:
                day = sizes.setdefault(k, {})
                day[d] = max(n, day.get(d) or 0)      # 同一天同榜的榜长应当一致，取大的当稳妥值
    return out, sizes


def _conf_days(lo: str, hi: str) -> Optional[set]:
    """交叉榜**真正跑到了**的那些天。有了这份名单才分得开两件事：
    「那一场这只票没进任何榜」与「那一场整张交叉榜没算」—— 后者不能念成前者。"""
    rows = _rows("""select distinct trade_date::text d from analytics.confluence_daily
                     where spec_version = %s and trade_date between %s::date and %s::date""",
                 (CF.SPEC_VERSION, lo, hi))
    return None if rows is None else {r['d'] for r in rows}


def _scores(codes: List[str], lo: str, hi: str) -> Optional[tuple]:
    """四维象限那一行要的档位、分数与**全市场名次**，外加一份「哪几天是沿用旧场」的名单。
    `score` 里 `oper_score` 那一条全市场都有行，不需要它当天挤进象限榜前 200 才有档位。"""
    rows = _rows("""select security_code as c, trade_date::text as d, value, rank_in_market,
                           quadrant, is_stale
                      from analytics.score
                     where capability_key = 'oper_score' and security_code = any(%s::text[])
                       and trade_date between %s::date and %s::date""", (codes, lo, hi))
    if rows is None:
        return None
    out: Dict[tuple, dict] = {}
    stale: set = set()
    for r in rows:
        x = {'value': _num(r.get('value'), 2), 'rank': _int(r.get('rank_in_market')),
             'quadrant': r.get('quadrant') or None, 'stale': bool(r.get('is_stale'))}
        out[(r['c'], r['d'])] = x
        if x['stale']:
            stale.add(r['d'])
    return out, stale


def _score_universe(lo: str, hi: str) -> Dict[str, int]:
    """每一天有多少只票被打了 `oper_score` —— 象限那一行的名次分母（`{日期: 只数}`）。"""
    rows = _rows("""select trade_date::text d, count(*)::int n from analytics.score
                     where capability_key = 'oper_score' and trade_date between %s::date and %s::date
                     group by 1""", (lo, hi)) or []
    return {r['d']: _int(r.get('n')) for r in rows}


def _metric_of(cell) -> Optional[float]:
    """`metrics` 那一格是 `{metric: 原值, strict/reasons: 附带读数}` —— 取主值。"""
    return _num(cell.get('metric'), 3) if isinstance(cell, dict) else None


def _forward(bars: Dict[tuple, float], sessions: List[str], pos: int, code: str) -> dict:
    """后 1/2/3/5/10/15 场：累计（主值）＋ 那一场自己的单日（悬停）。

    超出场次表右端（那场还没到）或那天它停牌没有行 ⇒ 两把都交 `None`，界面出 `—`。
    `fwd_at` 逐档记下**那一场它到底有没有行情**（`[1,2,3]` 这样）：有了它才说得出
    「第 5 格空着是因为那天它停牌」与「第 5 格空着是因为第 5 场还没到」是两句话。
    """
    base = bars.get((code, sessions[pos]))
    out: Dict[str, Any] = {}
    at: List[int] = []
    for col in SPAN_COLUMNS:
        j = pos + col['n']
        cur = bars.get((code, sessions[j])) if j < len(sessions) else None
        prev = bars.get((code, sessions[j - 1])) if 0 <= j - 1 < len(sessions) else None
        out[col['cum']] = _pct(cur, base)
        out[col['day']] = _pct(cur, prev)
        if cur is not None:
            at.append(col['n'])
    last = min(pos + max(SPANS), len(sessions) - 1)
    out['fwd_at'] = at
    out['fwd_sessions'] = max(0, last - pos)
    out['fwd_bars'] = sum(1 for j in range(pos + 1, last + 1)
                          if bars.get((code, sessions[j])) is not None)
    out['has_base'] = base is not None
    return out


def _sequence(conf: Dict[tuple, dict], scores: Dict[tuple, dict], sessions: List[str],
              pos: int, code: str) -> dict:
    """前 2 场 ~ 后 5 场这 8 格里，每一格在交叉命中榜里的样子。

    交的是**位置**（`i` = 首格在 `sessions` 里的下标，`n` = 格数，`signal_at` = 符合日在那一段的第几格）
    而不是每行一串日期 —— 场次表整体在响应里只交一份。
    每个**这一窗里出现过**的源一组 `rank`/`value`（长度 = 格数，缺数留 `None`）；
    榜长不在这里重复（见 `read()` 的 `sizes`）。源一个都不硬编码：清单、名字、原值名、
    它在哪一屏，全读 `CF.SOURCES` 那一份，顺序也照它。

    四维象限那一组特殊：`tier`（Q1~Q4 档位）与 `rank`（**全市场名次**）、`value`（`oper_score`）
    都来自 `analytics.score` —— 象限榜那份名单只到前 200，拿"榜内第几"当"每天的排名"会大面积留空。
    """
    lo, hi = max(0, pos - SEQ_BACK), min(len(sessions) - 1, pos + SEQ_FWD)
    days = sessions[lo:hi + 1]
    cells = [conf.get((code, d)) for d in days]
    sc = [scores.get((code, d)) for d in days]
    series: List[dict] = []
    for s in CF.SOURCES:
        if s.key == 'quadrant':
            item = {'key': s.key, 'label': s.label, 'metric': s.metric, 'board': s.board,
                    'tier': [x['quadrant'] if x else None for x in sc],
                    'rank': [x['rank'] if x else None for x in sc],
                    'value': [x['value'] if x else None for x in sc]}
        else:
            item = {'key': s.key, 'label': s.label, 'metric': s.metric, 'board': s.board,
                    'rank': [_int(c['ranks'].get(s.key)) if c and c['ranks'] else None for c in cells],
                    'value': [_metric_of(c['metrics'].get(s.key)) if c and c['metrics'] else None
                              for c in cells]}
        if any(v is not None for v in item['rank'] + item['value'] + (item.get('tier') or [])):
            series.append(item)
    return {'i': lo, 'n': len(days), 'signal_at': pos - lo,
            'hits': [c['hits'] if c else None for c in cells],
            'in_conf': [c is not None for c in cells], 'sources': series}


def check_cohort(rows: List[dict]) -> List[str]:
    """落笔之前自己验一遍这一批的**形状**（同 `system_entry.check_stats` 那条路数）。

    这一层能错的不是数值大小而是**对齐**：涨幅的场次偏移、序列格数与各数组长度、
    把「没数」写成 0 —— 三种都在屏幕上看不出来。
    """
    bad: List[str] = []
    for r in rows:
        tag = f'{r["code"]}@{r["date"]}'
        seq = r.get('seq') or {}
        n = seq.get('n') or 0
        if not n:
            bad.append(f'{tag}：位次序列一格都没有（连符合日本身都没落进场次表？）')
            continue
        if n < SEQ_BACK + 1 or n > SEQ_BACK + SEQ_FWD + 1:
            bad.append(f'{tag}：序列格数 {n} 不在 {SEQ_BACK + 1}~{SEQ_BACK + SEQ_FWD + 1} 之间')
        if seq.get('signal_at') is None or not 0 <= seq['signal_at'] < n:
            bad.append(f'{tag}：信号日那一格落在序列外面（{seq.get("signal_at")}/{n}）')
        if len(seq.get('hits') or []) != n or len(seq.get('in_conf') or []) != n:
            bad.append(f'{tag}：hits／in_conf 长度与格数不齐')
        for s in seq.get('sources') or []:
            for k in ('rank', 'value'):
                if len(s.get(k) or []) != n:
                    bad.append(f'{tag}：{s["key"]} 的 {k} 长度 {len(s.get(k) or [])} 与格数 {n} 不齐')
            if s.get('tier') is not None and len(s['tier']) != n:
                bad.append(f'{tag}：{s["key"]} 的 tier 长度与格数不齐')
        if not r.get('has_base'):
            if any(r.get(c['cum']) is not None for c in SPAN_COLUMNS):
                bad.append(f'{tag}：符合日没有行情却交出了涨幅 —— 没有基期就没有「累计」')
            continue
        # 有行情却没数：只在「那一场确实到了且有行情」时才算坏了（右端没到／那天停牌都是正当的空）
        for col in SPAN_COLUMNS:
            if col['n'] in (r.get('fwd_at') or []) and r[col['cum']] is None:
                bad.append(f'{tag}：说第 {col["n"]} 场有行情，却交不出那一格的累计涨幅')
    return bad


#: 这一块那几句口径原文：**一份三样（机器键 / 上屏的中文标题 / 句子）**。
#: 机器键留着给接口内部引用用（`controls` 取 `cohort`、位次列的列头悬停取 `seq`），
#: 屏幕上的字典只念中文标题 —— 字典头念 `forward`／`seq` 那种机器码，是 10-22 已经点过一次的名不副实
#: （《页面展示工作指南/01》§十三之三：标识符要换成中文，不是收起来）。
#: 两句刻意的规矩：句子不写死日期与条数（那些数每天随窗口滚，写了就过期，数在
#: `window`/`total`/格子里）；也不出现裸星号（这些句子既进 `plain()` 的折叠框、
#: 也进原生 `title` 悬停，后者不过 `plain()`）。
CALIBERS = (
    {'key': 'cohort', 'title': '这一叠列的是谁',
     'text': ('这一叠列的是「最近 6 个自然月里走完三条的个股」：母体＝`entry_ok`，'
              '不含「缺口成立但没走完回调/确认」的观察池；按符合日从近到远排，'
              '一只票在窗口里走完两次就出现两行，所以行数多于只数。'
              '一页 20 行，翻的是这一整批（后端一次交全，前端只切屏）。')},
    {'key': 'forward', 'title': '后 N 日那几列（涨幅）',
     'text': ('几列涨幅同一把尺：从符合日收盘起第 N 个场次的「累计」涨幅'
              '（后复权价＝收盘 × adjustment_factor，跨除权日连续；不是本地日线里那列未复权的 pct_change）。'
              '「日」是场次、不是日历日，且按市场场次数、不按这只票自己的 K 线根数 —— '
              '停牌那天它没有行，那一格就空着（—），不拿更早或更晚的一天冒充。'
              '悬停里另给「那一场自己那一天」的单日涨幅：只有次日那一格两个数恒等，'
              '往后每一格都可能「累计为正、当日为负」。')},
    {'key': 'seq', 'title': '位次那一格（交叉命中榜）',
     'text': ('最后那一格是交叉命中榜的轨迹：符合日之前 2 场 ~ 之后 5 场（含当日共 8 格，'
              '最上面那排日期里的 ▸ 就是符合日）。每一行是一个交叉源（源清单、名字、原值名、'
              '它在哪一屏，只住 `app/compute/confluence.py` 的 `SOURCES` 一份）：'
              '上面一行是名次（象限那一源把档位与全市场名次并在一起念，如 `Q4(926)`），'
              '下面一行是该榜排序用的那个原值；名次旁边的「共 N」是这一场该榜的榜长 —— '
              '名次只有除以它才可跨榜比（象限榜第 30 名和首破榜第 30 名不是一回事）。'
              '空位（·）＝那一场它没进这个榜，不等于那天没有行情；那天有没有行情看上面的涨幅列。')},
    {'key': 'tier', 'title': '象限那行的档位与名次',
     'text': ('档位（Q1~Q4）来自 `analytics.score` 的 `quadrant`。括号里那个数是「全市场名次」'
              '（`rank_in_market`），不是「象限榜前 200 里的第几」—— 后者只在挤进那份名单时才数得出，'
              '当「每天的排名」念会大面积留空。下面一行的分数是 `oper_score`'
              '（0.40×个股质量 + 0.60×环境共振），分母「共 N」是那一天被打了分的只数。')},
    {'key': 'stale', 'title': '带 * 的那些格子',
     'text': ('那一场的趋势评分是沿用旧场的（`is_stale`）。'
              '沿用分与新算分不能横比，所以那一格只当位置参考，不进「谁更强」的比较。')},
    {'key': 'edge', 'title': '右端短一截的行',
     'text': ('靠近窗口右端那几行「后 5 场还没到齐」（或那几天还没算完）：序列与涨幅在那里短一截。'
              '短是数据没到，不是这只票后来没动静 —— 缺的那几格一律空着，不补 0、不拿最后一根顶上。')},
)


def calibers() -> dict:
    """`{机器键: 句子}` —— 给接口内部引用（列头悬停、controls 那一句）。屏幕上的字典读 `caliber_items()`。"""
    return {c['key']: c['text'] for c in CALIBERS}


def caliber_items() -> List[dict]:
    """上屏那一份字典（中文标题 + 句子；顺序就是 `CALIBERS` 的顺序，两处同源不会分叉）。"""
    return [dict(c) for c in CALIBERS]


def _build(months: int) -> dict:
    months = max(1, min(int(months or MONTHS), 24))
    end_s = _latest(TABLE, spec=SE.SPEC_VERSION)
    if not end_s or not pg.session_has(TABLE, end_s):
        return _fail('近 6 个月名单', TABLE, end_s)
    end = dt.date.fromisoformat(end_s)
    start_s = _start_day(end, months).isoformat()
    rows = _cohort(start_s, end_s)
    if rows is None:
        return _fail('近 6 个月名单', TABLE, end_s)

    base = {'available': True, 'date': end_s, 'spec_version': SE.SPEC_VERSION,
            'confluence_spec_version': CF.SPEC_VERSION, 'months': months,
            'window': {'from': start_s, 'to': end_s},
            'page_size': PAGE_SIZE, 'total': len(rows), 'codes': len({r['code'] for r in rows}),
            'span_columns': [dict(c) for c in SPAN_COLUMNS],
            'seq': {'back': SEQ_BACK, 'fwd': SEQ_FWD, 'cells': SEQ_BACK + SEQ_FWD + 1},
            'sources': [{'key': s.key, 'label': s.label, 'metric': s.metric, 'board': s.board,
                         'limit': s.limit, 'meaning': s.meaning} for s in CF.SOURCES],
            'calibers': calibers(), 'caliber_items': caliber_items(),
            'self_check': [], 'rows': rows}
    caveats = _caveats('coverage') + _cache_caveat(('coverage:system_entry',))
    if not rows:
        caveats.append(_cav('cohort_empty_window',
                            f'{start_s} ~ {end_s} 这一段里没有三条全过的票（判据没变，是这一窗确实'
                            '没人走完三条）。把窗口调长，或看上面那块榜单 —— 不是这一屏坏了。'))
        return {**base, 'caveats': caveats, 'window': {**base['window'], 'sessions': 0},
                'sizes': {}, 'universe': {}, 'stale_days': [], 'conf_days': [], 'sessions': []}

    codes = sorted({r['code'] for r in rows})
    before = _back_sessions(start_s, PAD_BACK)
    sessions = _sessions((before or [start_s])[-1], end_s)
    if not sessions:
        caveats.append(_cav('cohort_sessions_unread',
                            '市场场次表没读到（`stock_daily` 那一句查询失败了）—— 这不是「这一窗没有场次」，'
                            '所以涨幅与位次那一整块本轮不算：整列空着是坏了，不是没数。'))
        return {**base, 'caveats': caveats, 'window': {**base['window'], 'sessions': 0},
                'sizes': {}, 'universe': {}, 'stale_days': [], 'conf_days': [], 'sessions': []}

    pos = {d: i for i, d in enumerate(sessions)}
    conf_pack, conf_days_raw, score_pack = _conf(codes, sessions[0], end_s), \
        _conf_days(sessions[0], end_s), _scores(codes, sessions[0], end_s)
    bars = _bars(codes, sessions[0], end_s)
    inputs = {'行情': bars, '交叉命中榜': conf_pack, '交叉榜跑到的天': conf_days_raw,
              '趋势评分': score_pack}
    lost = [k for k, v in inputs.items() if v is None]
    if lost:
        # 一张输入表读不到 ⇒ 相关格子全空。必须点名是「读失败」，不能让整列 — 冒充「本来就是 0/没有」
        caveats.append(_cav('cohort_inputs_unread',
                            '这几张输入表本轮读不到（查询失败）：' + '、'.join(lost)
                            + ' —— 于是涨幅或位次那一块整列空着。这是读失败，不是「那些格子本来就是空」。'))
    conf, sizes = conf_pack or ({}, {})
    scores, stale_days = score_pack or ({}, set())
    conf_days = conf_days_raw or set()
    universe = _score_universe(sessions[0], end_s)

    stamps: List[str] = []
    for r in rows:
        for k in ('gap_pct', 'stop_dist_pct', 'close', 'pct_change', 'pullback_max_vol_ratio'):
            r[k] = _num(r.get(k), 2)
        for k in ('gap_ago', 'gap_unfilled', 'limit_up_ago'):
            r[k] = _int(r.get(k))
        if r.get('calculated_at'):
            stamps.append(str(r['calculated_at']))
        r.pop('calculated_at', None)      # 每行一个时间戳只是把 payload 撑大；出处交在 `as_of`
        p = pos.get(r['date'])
        if p is None:
            r.update({'has_base': False, 'fwd_at': [], 'fwd_sessions': 0, 'fwd_bars': 0,
                      'seq': {'i': 0, 'n': 0, 'signal_at': None, 'hits': [], 'in_conf': [],
                              'sources': []}})
            continue
        r.update(_forward(bars or {}, sessions, p, r['code']))
        r['seq'] = _sequence(conf, scores, sessions, p, r['code'])

    problems = check_cohort(rows) if not lost else []
    no_conf = [r for r in rows if not (r.get('seq') or {}).get('sources')]
    if no_conf:
        caveats.append(_cav('cohort_conf_unmatched',
                            f'{len(no_conf)} / {len(rows)} 行的位次那一格整格空着：那只票那一天'
                            '没进任何一份名单（`confluence_daily` 只记进过榜的票；样例 '
                            + '、'.join(f'{x["code"]}@{x["date"]}' for x in no_conf[:6]) + '…）。'
                            '空不等于那天它没有行情 —— 行情看上面的涨幅列。'))
    # 「这一格空」有两种完全不同的原因：那只票当天没进任何榜 vs 交叉榜那一天整张没算。
    # 后者只能靠「那一天在 `confluence_daily` 里一行都没有」判出来，所以先按天分好类再数格子。
    ran = {d: d in conf_days for d in sessions}
    not_ran = sum(1 for r in rows for k in range((r.get('seq') or {}).get('n') or 0)
                  if not ran[sessions[r['seq']['i'] + k]])
    if not_ran:
        gone = sorted({sessions[(r['seq']['i'] + k)] for r in rows if (r.get('seq') or {}).get('n')
                       for k in range(r['seq']['n']) if not ran[sessions[r['seq']['i'] + k]]})
        caveats.append(_cav('cohort_conf_day_missing',
                            f'位次轨迹里有 {not_ran} 个空格落在「交叉榜那几天没算」上（'
                            + '、'.join(gone[:6]) + ('…' if len(gone) > 6 else '') + '）：'
                            '那些天 `confluence_daily` 一行都没有，是加工链没跑到，'
                            '不是这些票没进榜 —— 两种在格子里都空，所以只能这样点名。'))
    if stale_days:
        caveats.append(_cav('cohort_stale_score',
                            f'位次轨迹里有些格子落在「象限档位沿用旧场」的日期上（带 * 的那些）：'
                            f'{len(stale_days)} 天（{sorted(stale_days)[0]} 起）。'
                            '沿用分与新算分不能横比，所以那些格子只当位置参考。'))
    short = sum(1 for r in rows if (r.get('seq') or {}).get('n')
                and (r['seq'].get('signal_at') or 0) + SEQ_FWD >= r['seq']['n'])
    if short:
        caveats.append(_cav('cohort_edge',
                            f'{short} 行的「后 5 场」还没到齐（符合日靠近窗口右端）：序列与涨幅在那几行短一截 —— '
                            '是数据还没到，不是那只票后来没动静。'))
    if problems:
        caveats.append(_cav('cohort_self_check',
                            '这一块自检没过 ' + str(len(problems)) + ' 项：' + '；'.join(problems[:6])
                            + ' —— 屏幕上那些格子先别照着下结论（对齐坏了在格子里看不出来）。'))
    return {**base, 'caveats': caveats, 'self_check': problems[:20], 'sessions': sessions,
            'sizes': sizes, 'universe': universe, 'stale_days': sorted(stale_days),
            'conf_days': sorted(conf_days), 'as_of': max(stamps) if stamps else None,
            'window': {**base['window'], 'sessions': len(sessions)}}


def read(months: int = MONTHS, refresh: bool = False) -> dict:
    """整批一次算完（~4 秒）→ 进程内缓存一小时，并**自己交出年龄**。

    缓存键带窗口右端（最新一场）与月数：链跑出新的一场 ⇒ 键变了 ⇒ 自然重算，
    不会把旧一场的数据当成今天的。
    """
    months = max(1, min(int(months or MONTHS), 24))
    key = f'{SE.SPEC_VERSION}|{CF.SPEC_VERSION}|{months}'
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and not refresh and now - hit[0] < CACHE_TTL:
        payload, age = dict(hit[1]), round(now - hit[0], 1)
        payload['cached'] = True
        payload['cache_age_seconds'] = age
        return payload
    payload = _build(months)
    if payload.get('available'):
        payload['cached'] = False
        payload['cache_age_seconds'] = 0
        payload['generated_at'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        _CACHE[key] = (time.monotonic(), payload)
    return payload
