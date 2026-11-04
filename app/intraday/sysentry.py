# -*- coding: utf-8 -*-
"""盘中「交易体系榜」的**取数与判据**（A 层，14:40 那一刀）—— 只算、只落库，一个字都不发。

与 `ingest/jobs/push/t07_sysentry_report.py` 的分工（用户 11-03 定：判断与取数属于数据加工环节，是取数落地的，
跟推送到飞书的那些行为分开，不要互相影响）：

    sysentry_fill（A，本模块）   14:39:30：历史日线 → ②预筛 → 实时快照拼今天这一根 → `judge()`
                                → 逐票落 `analytics.intraday_sysentry` + 一格摘要进留痕表，**不发**
    sysentry_report（B）          14:40:00：只读那张表 → 渲染 → 发渠道

为什么必须拆开（外部那份 `sysentry_intraday.py` 是边取边发的）：发失败之后想重发一次，
而重发时快照已经变了 —— 群里第二条与第一条不是同一场数，那种差别没人能察觉。
拆开后 B 任何时候重跑，拿到的数与 14:39:30 那一针一字不差。

判据**不在这里重写**：直接吃 `app/compute/system_entry.judge()`（spec `sys-1`），
把"今天之前 29 根日线 + 正在走的这一根盘中日线"拼成它要的那 30 根喂进去。
这样 14:40 播的这一榜，与今晚 15:59 收盘链落的 `analytics.system_entry` 是同一把尺子；
两边**允许不同**（尾盘跳水/尾盘涨停），所以这张表另带 `quote_source`/`quote_day` 说明出处。

筛法：先用 ② 在全市场挑候选（三条里最紧的一条，几十只量级），只对候选拉实时快照，
再交 `judge()` 精确判 ①②③。漏票的防线是 `sysentry_report --check-days`（与表逐票对账），不是运气。

宁缺毋假：快照的行情日期不是今天 → 重试 → 仍不对就判"源未刷新"，不拿昨收顶；
除权/基不符（快照昨收 vs 库昨收差 > 阈值）逐票剔除并计数；停牌/无报价计入"未判"并报数，不当 0。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.compute.system_entry import (GAP_AGO_MAX, GAP_AGO_MIN,  # noqa: E402
                                      GAP_MIN_PCT, GAP_UNFILLED_DAYS, LIMIT_LOOKBACK,
                                      SERIES_BARS, SPEC_VERSION, judge)
from app.compute.limit_prices import DATA_START                 # noqa: E402
from app.db import engine                                       # noqa: E402

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
_MKT = {'sh': 'SH', 'sz': 'SZ', 'bj': 'BJ'}
#: 快照源的顺序就是这里的顺序（榜尾那一格 `quote_source` 会写明真用了哪一个）
WEB_SOURCES = (('新浪', 'quotes_sina'), ('腾讯', 'quotes_tencent'), ('东财ulist', 'quotes_em'))

#: 作业侧读的短名（外部那份 `sysentry_config.json` 的形状）。真值只住一处：
#: `app/intraday/tasks.py` 的 `DEFAULTS`，键名带 `sysentry_` 前缀、与盘中播报其余几路同一个配置块。
#: 再写一份默认值就会有两处可改、一处生效。
CFG_KEYS = ('quote_source', 'tq_fallback_web', 'tq_timeout_s', 'tq_retry_wait_s', 'quote_batch',
            'quote_retry', 'quote_cool_s', 'preclose_tol_pct', 'min_fresh_ratio')


def cfg_from(raw: dict) -> dict:
    """`intraday_broadcast` 那一块 → 本模块用的短名表。缺键按 `DEFAULTS` 补，并**出声**。"""
    from app.intraday import tasks

    out: dict[str, Any] = {}
    for k in CFG_KEYS:
        name = f'sysentry_{k}'
        out[k] = raw[name] if raw.get(name) not in (None, '') else tasks.DEFAULTS.get(name)
    return out


def no_test_net(what: str) -> None:
    """pytest 进程里硬挡真出网 / 真起通达信子进程（与 `store._no_test_write` 同源的纪律）：
    跑一次测试就打一轮新浪/腾讯，CI 上红的是网络，本机红的是限频 —— 都不是判据。
    作业侧（涨停池对账那一发）也用这一个出口，别再开第二处。"""
    if os.environ.get('PYTEST_CURRENT_TEST'):
        raise RuntimeError(f'pytest 进程里不许真出网/真起子进程（{what}）—— 要打桩这一层')


_no_test_net = no_test_net          # 作业侧（涨停池对账）与本模块内部都用这一个出口


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _num(x: Any) -> float | None:
    v = _f(x)
    return v if v else None


def _opt_float(x: Any) -> float | None:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- 历史日线（PG 只读）

def last_daily_day(before: dt.date) -> dt.date | None:
    return engine.scalar(
        'select max(trade_date) from market_data.stock_daily where trade_date < %s',
        (before,), role='reader')


def history_bars(end: dt.date) -> dict[str, list]:
    """每票 `end` 之前（含 end）最近 `SERIES_BARS-1` 根日线；bar 形状同 `system_entry._series`：
    `[日期, 开, 高, 低, 收, 前收, 涨跌%, 涨停价, 成交量, 复权因子]`。"""
    start = end - dt.timedelta(days=SERIES_BARS * 3)
    rows = engine.query("""
        with s as (select security_code, trade_date, open, high, low, close, pre_close,
                          pct_change, limit_up, volume_shares, adjustment_factor,
                          row_number() over (partition by security_code order by trade_date desc) back
                     from market_data.stock_daily
                    where trade_date between %s and %s
                      and close > 0 and pre_close > 0 and adjustment_factor > 0)
        select security_code,
               jsonb_agg(jsonb_build_array(trade_date::text, open, high, low, close, pre_close,
                                           pct_change, limit_up, volume_shares, adjustment_factor)
                         order by trade_date) rows
          from s where back <= %s
         group by 1""", (start, end, SERIES_BARS - 1), role='reader') or []
    out: dict[str, list] = {}
    for r in rows:
        blob = r['rows']
        bars = json.loads(blob) if isinstance(blob, str) else blob
        out[r['security_code']] = [[b[0]] + [_f(x) for x in b[1:]] for b in bars]
    return out


def band_snapshot(codes: list[str], day: dt.date) -> dict[str, dict]:
    """候选票的档位资料：板块 / 当前 is_st / 当日 ST 档期 / 次新头 5 场（涨停价分层的输入）。"""
    if not codes:
        return {}
    rows = engine.query("""
        select s.security_code, s.board, coalesce(s.is_st, false) as is_st,
               (p.security_code is not null) as st_day,
               f.first_session, f.fifth_session
          from reference_data.security s
          left join reference_data.security_st_period p
            on p.security_code = s.security_code and p.is_st
               and %(day)s between p.valid_from and coalesce(p.valid_to, date '2038-01-01')
          left join reference_data.security_session_span f
            on f.security_code = s.security_code
         where s.asset_type = 'stock' and s.security_code = any(%(codes)s)""",
        {'day': day, 'codes': codes}, role='reader') or []
    return {r['security_code']: {'board': r['board'], 'is_st': r['is_st'],
                                 'st_day': r['st_day'], 'first': r['first_session'],
                                 'fifth': r['fifth_session']} for r in rows}


def limit_up_price(pre_close: float, today: dt.date, info: dict,
                   h: float, l: float) -> float | None:
    """今日涨停价：分层档位（顺序照 `app/compute/limit_prices._RESOLVE`），价格现算。

    它只影响"今天这一根自己算不算封住涨停"，算完与东财涨停池对账（不一致只计数、不拦）。
    """
    if not pre_close or pre_close <= 0:
        return None
    board = (info or {}).get('board') or ''
    board_band = {'北交所': 0.30, '创业板': 0.20, '科创板': 0.20}.get(board, 0.10)
    reach = max((h or pre_close) / pre_close - 1.0, 1.0 - (l or pre_close) / pre_close)
    if reach > board_band + 0.02:                       # unbounded：这天的限不受这套管
        return None
    first, fifth = (info or {}).get('first'), (info or {}).get('fifth')
    in_first5 = bool(first) and (fifth is None or today <= fifth)
    if in_first5 and first > DATA_START:                # 次新前 5 场不设停板
        return None
    if board == '主板' and (reach > 0.052
                            or round(pre_close * 1.05, 2) < (h or pre_close)
                            or round(pre_close * 0.95, 2) > (l or pre_close)):
        band = 0.10                                     # proved_non_st
    elif board == '主板' and (info or {}).get('st_day'):
        band = 0.05
    elif board == '主板' and (info or {}).get('is_st'):
        band = 0.05
    else:
        band = board_band
    return round(pre_close * (1 + band), 2)


# --------------------------------------------------------------------------- 候选预筛（② 的历史部分）

def gap_candidate(bars: list) -> bool:
    """bars = 今天之前升序的日线。缺口日距今 4~7 场 ⇒ 3 个确认场全落在历史里，可离线判定。"""
    n = len(bars)
    if n < LIMIT_LOOKBACK:
        return False
    for ago in range(GAP_AGO_MIN, GAP_AGO_MAX + 1):
        i = n - ago                                     # 拼上今天后 judge 用的正是这个下标
        if i < 1:
            continue
        low = _f(bars[i][3]) * _f(bars[i][9] or 1)
        floor = _f(bars[i - 1][2]) * _f(bars[i - 1][9] or 1)
        if not floor or low <= floor:
            continue
        if 100.0 * (low / floor - 1.0) < GAP_MIN_PCT:
            continue
        after = bars[i + 1:i + 1 + GAP_UNFILLED_DAYS]
        if len(after) < GAP_UNFILLED_DAYS:
            continue
        if min(_f(b[3]) * _f(b[9] or 1) for b in after) <= floor:
            continue
        return True
    return False


def candidates(hist: dict[str, list]) -> list[str]:
    return sorted(c for c, b in hist.items() if gap_candidate(b))


# --------------------------------------------------------------------------- 实时快照（通达信 TQ 主源）

def tq_snapshot(codes: list[str], timeout_s: int) -> dict:
    """一次子进程内逐只取实时快照 + 扩展信息（`get_market_snapshot`/`get_more_info` 都是单只接口）。"""
    _no_test_net('tq_snapshot')
    try:
        from app.tdx_client import tq_bridge as tb
    except Exception as exc:  # noqa: BLE001 装不上就是没这条路
        return {'ok': False, 'error': f'tq_bridge 不可导入：{type(exc).__name__}'}
    ok, why = tb.available()
    if not ok:
        return {'ok': False, 'error': f'TQ 不可用：{why}'}
    body = f"""
codes = {codes!r}
kline, snap = {{}}, {{}}
for c in codes:
    s = tq.get_market_snapshot(stock_code=c) or {{}}
    kline[c] = {{k: s.get(k) for k in
                ('LastClose', 'Open', 'Max', 'Min', 'Now', 'Volume', 'Amount', 'ItemNum')}}
    m = tq.get_more_info(stock_code=c,
                         field_list=['ZTPrice', 'DTPrice', 'HqDate', 'TPFlag', 'ZAF']) or {{}}
    snap[c] = {{k: m.get(k) for k in ('ZTPrice', 'DTPrice', 'HqDate', 'TPFlag', 'ZAF')}}
emit({{'kline': kline, 'snap': snap}})
"""
    try:
        return tb.run_snippet(body, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}


def _tq_day(hq_date: Any) -> str:
    s = re.sub(r'\D', '', str(hq_date or ''))
    if len(s) == 8:
        return f'{s[0:4]}-{s[4:6]}-{s[6:8]}'
    return s[:10] if len(s) >= 10 else ''


def quotes_tq(codes: list[str], cfg: dict, today: dt.date) -> dict[str, dict]:
    """TQ 快照 → 与新浪同形状的 dict。`Volume` 是「总手」，×100 折成股（与 `stock_daily` 同尺）。"""
    res = tq_snapshot(codes, int(cfg['tq_timeout_s']))
    if not res.get('ok'):
        raise RuntimeError(res.get('error') or 'TQ 无结果')
    out: dict[str, dict] = {}
    for code, s in (res.get('kline') or {}).items():
        m = (res.get('snap') or {}).get(code, {})
        price = _f(s.get('Now'))
        if price <= 0:
            continue                                      # 无现价＝停牌/未开盘，计入未判
        try:
            out[code] = {
                'name': '', 'open': _f(s.get('Open')), 'pre_close': _f(s.get('LastClose')),
                'price': price, 'high': _f(s.get('Max')), 'low': _f(s.get('Min')),
                'vol_shares': _f(s.get('Volume')) * 100.0,
                'lu': _opt_float(m.get('ZTPrice')), 'day': _tq_day(m.get('HqDate')), 'time': '',
                'suspended': str(m.get('TPFlag') or '').strip() not in ('', '0')}
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- 实时快照（网络三源）

def _http(url: str, referer: str, enc: str = 'utf-8') -> str:
    _no_test_net('quotes_http')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': referer})
    with urllib.request.urlopen(req, timeout=12) as r:      # noqa: S310 固定 https 域名，无用户输入
        return r.read().decode(enc, 'replace')


def _sym(code: str) -> str:
    c, _, mkt = code.partition('.')
    return {'SH': 'sh', 'SZ': 'sz', 'BJ': 'bj'}.get(mkt, '') + c


def quotes_sina(codes: list[str], cfg: dict) -> dict[str, dict]:
    """新浪批量：0名称 1今开 2昨收 3现价 4高 5低 8成交量(股) … 30日期 31时间。"""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), int(cfg['quote_batch'])):
        syms = [_sym(c) for c in codes[i:i + int(cfg['quote_batch'])] if _sym(c)]
        if not syms:
            continue
        raw = _http('https://hq.sinajs.cn/list=' + ','.join(syms),
                    'https://finance.sina.com.cn', 'gbk')
        for line in raw.strip().splitlines():
            m = re.match(r'var hq_str_([a-z]{2})(\d+)="([^"]*)"', line.strip())
            if not m or not m.group(3) or m.group(1) not in _MKT:
                continue
            f = m.group(3).split(',')
            if len(f) < 32:
                continue
            day = next((x for x in f[-8:] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', x)), '')
            tm = next((x for x in f[30:] if re.fullmatch(r'\d{2}:\d{2}:\d{2}', x)), '')
            try:
                out[f'{m.group(2)}.{_MKT[m.group(1)]}'] = {
                    'name': f[0], 'open': float(f[1]), 'pre_close': float(f[2]),
                    'price': float(f[3]), 'high': float(f[4]), 'low': float(f[5]),
                    'vol_shares': float(f[8]), 'day': day, 'time': tm}
            except (TypeError, ValueError, IndexError):
                continue
        time.sleep(0.6)
    return out


def quotes_tencent(codes: list[str], cfg: dict) -> dict[str, dict]:
    """腾讯批量：3现价 4昨收 5今开 6成交量(手) 30时间戳 33高 34低。"""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), int(cfg['quote_batch'])):
        syms = [_sym(c) for c in codes[i:i + int(cfg['quote_batch'])] if _sym(c)]
        if not syms:
            continue
        raw = _http('https://qt.gtimg.cn/q=' + ','.join(syms), 'https://qt.gtimg.cn/', 'gbk')
        for line in raw.strip().splitlines():
            m = re.match(r'v_([a-z]{2})(\d+)="([^"]*)"', line.strip())
            if not m or not m.group(3) or m.group(1) not in _MKT:
                continue
            f = m.group(3).split('~')
            if len(f) < 35:
                continue
            stamp = f[30] or ''
            day = f'{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}' if re.fullmatch(r'\d{14}', stamp) else ''
            tm = f'{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}' if day else ''
            try:
                out[f'{m.group(2)}.{_MKT[m.group(1)]}'] = {
                    'name': f[1], 'open': float(f[5]), 'pre_close': float(f[4]),
                    'price': float(f[3]), 'high': float(f[33]), 'low': float(f[34]),
                    'vol_shares': float(f[6]) * 100.0, 'day': day, 'time': tm}
            except (TypeError, ValueError, IndexError):
                continue
        time.sleep(0.6)
    return out


def quotes_em(codes: list[str], cfg: dict) -> dict[str, dict]:
    """东财 ulist（末位）：f2现价 f5量(手) f13市场 f15高 f16低 f17今开 f18昨收 —— 无行情日期。"""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), int(cfg['quote_batch'])):
        batch = codes[i:i + int(cfg['quote_batch'])]
        secids = ','.join(('1.' if c.endswith('.SH') else '0.') + c.split('.')[0] for c in batch)
        q = urllib.parse.urlencode({'secids': secids, 'fltt': 2, 'invt': 2,
                                    'fields': 'f2,f5,f12,f13,f14,f15,f16,f17,f18'})
        data = json.loads(_http('https://push2.eastmoney.com/api/qt/ulist.np/get?' + q,
                                'https://quote.eastmoney.com/'))
        for row in ((data.get('data') or {}).get('diff') or []):
            code, price = str(row.get('f12', '')), row.get('f2')
            if not isinstance(price, (int, float)):
                continue                                 # '-' ＝停牌/无行情
            mkt = 'SH' if str(row.get('f13')) == '1' else ('BJ' if code[0] in '49' else 'SZ')
            try:
                out[f'{code}.{mkt}'] = {
                    'name': row.get('f14', ''), 'open': _f(row.get('f17')),
                    'pre_close': _f(row.get('f18')), 'price': float(price),
                    'high': _f(row.get('f15')), 'low': _f(row.get('f16')),
                    'vol_shares': _f(row.get('f5')) * 100.0, 'day': '', 'time': ''}
            except (TypeError, ValueError):
                continue
        time.sleep(float(cfg['quote_cool_s']))
    return out


def _fresh(got: dict[str, dict], today: dt.date) -> tuple[int, int]:
    dated = [v for v in got.values() if v.get('day')]
    return len(dated), sum(1 for v in dated if v['day'] == str(today))


def v_day(got: dict[str, dict]) -> str:
    return next((v['day'] for v in got.values() if v.get('day')), '?')


def fetch_quotes(codes: list[str], cfg: dict, today: dt.date, *, allow_undated: bool = False,
                 note: Callable[[dict], None] = lambda _d: None
                 ) -> tuple[dict[str, dict], str, str]:
    """三源依次试 → `(报价表, 源名, 时点出处)`。源的行情日期不是今天就当未刷新，换下一个源。

    `allow_undated`：只有盘中时窗内才准用"不给行情日期"的源（东财 ulist）。夜里/盘前它给的是
    上一场的收盘快照，拿它拼"今天这根"等于拿旧数冒充实时 —— 宁缺毋假，直接拒。
    """
    errs: list[str] = []
    for src, fname in WEB_SOURCES:
        fn = globals()[fname]
        for attempt in range(1, int(cfg['quote_retry']) + 1):
            try:
                got = fn(codes, cfg)
            except Exception as exc:  # noqa: BLE001 一个源挂了换下一个，原因留着拼进榜尾
                errs.append(f'{src}:{type(exc).__name__}')
                note({'what': 'sysentry_quote_fail', 'src': src, 'attempt': attempt,
                      'err': str(exc)[:180]})
                time.sleep(float(cfg['quote_cool_s']))
                continue
            if not got:
                errs.append(f'{src}:空')
                break
            dated, fresh = _fresh(got, today)
            if not dated:                                # 源不给日期：只在盘中时窗内采信
                if allow_undated:
                    return got, src, '源不给行情日期·盘中时窗内采信'
                errs.append(f'{src}:无行情日期，非盘中不采信')
                break
            if fresh / max(len(got), 1) >= float(cfg['min_fresh_ratio']):
                return got, src, f'{fresh}/{len(got)}只标{v_day(got)}'
            errs.append(f'{src}:行情日期非今日({fresh}/{len(got)})')
            note({'what': 'sysentry_quote_stale', 'src': src, 'fresh': fresh, 'n': len(got)})
            break
    raise RuntimeError('；'.join(errs) or '无源可用')


def gather_quotes(cands: list[str], cfg: dict, today: dt.date, now: dt.datetime,
                  note: Callable[[dict], None] = lambda _d: None
                  ) -> tuple[dict[str, dict], str, str]:
    """先本地通达信 TQ（用户指定主源），不行再按开关降级网络三源。都拿不到就抛。

    自拉通达信客户端这一件**没搬过来**：14:40 客户端在不在盘里由 `tdx_watch` 那一路管，
    这一路不碰桌面（同一条桌面自动化两处在做，谁先起的算谁、出错时两边都说不清）。
    """
    errs: list[str] = []
    if cfg['quote_source'] in ('tq', 'auto'):
        for attempt in (1, 2):
            try:
                got = quotes_tq(cands, cfg, today)
            except Exception as exc:  # noqa: BLE001
                errs.append(f'TQ:{type(exc).__name__}:{str(exc)[:90]}')
                note({'what': 'sysentry_tq_fail', 'attempt': attempt, 'err': str(exc)[:200]})
                break
            if not got:
                errs.append('TQ:空')
                break
            dated, fresh = _fresh(got, today)
            if dated and fresh / max(len(got), 1) < float(cfg['min_fresh_ratio']):
                errs.append(f'TQ:行情日期非今日({fresh}/{len(got)})')
                note({'what': 'sysentry_tq_stale', 'fresh': fresh, 'n': len(got)})
                if attempt == 1:                      # 客户端行情缓存可能刚没刷，等一会再要一轮
                    time.sleep(float(cfg['tq_retry_wait_s']))
                    continue
                break
            proven = f'HqDate {fresh}/{len(got)}只={today}' if dated else 'TQ未给行情日期'
            return got, '通达信TQ', proven
    if errs and not cfg['tq_fallback_web']:
        raise RuntimeError('；'.join(errs) + '（未允许降级网络源）')
    try:
        got, src, proven = fetch_quotes(cands, cfg, today, allow_undated=in_trading_window(now),
                                        note=note)
    except Exception as exc:                              # 网络源也不行 → 把 TQ 的原因一起报出去
        raise RuntimeError('；'.join(errs + [str(exc)]) if errs else str(exc)) from None
    prefix = '降级网络快照（' + ('；'.join(errs) if errs else '按配置') + '）：' if errs else ''
    return got, prefix + src, proven


# --------------------------------------------------------------------------- 判据（纯算，不碰网不碰库）

def offline_today_bars(day: dt.date) -> dict[str, dict]:
    """对账/复盘：把库里 `day` 那根日线读成与快照同形状（`limit_up` 一并取，让 ① 逐字对齐）。"""
    rows = engine.query("""
        select security_code, open, high, low, close, pre_close, pct_change, limit_up,
               volume_shares, adjustment_factor
          from market_data.stock_daily
         where trade_date = %s and close > 0 and pre_close > 0""", (day,), role='reader') or []
    return {r['security_code']: {'name': '', 'open': _f(r['open']), 'high': _f(r['high']),
                                 'low': _f(r['low']), 'price': _f(r['close']),
                                 'pre_close': _f(r['pre_close']), 'pct': _num(r['pct_change']),
                                 'lu': _num(r['limit_up']), 'vol_shares': _f(r['volume_shares']),
                                 'af': _num(r['adjustment_factor']), 'day': str(day),
                                 'time': ''} for r in rows}


def build_rows(hist: dict[str, list], quotes: dict[str, dict], *, today: dt.date, offline: bool,
               infos: dict[str, dict], zt: set[str], cfg: dict,
               note: Callable[[dict], None] = lambda _d: None) -> dict:
    """只对候选逐票判，返回 `{entry, watch, *_n}`；同一只在一场里只留一票（`judge` 决定进哪个池）。"""
    res: dict[str, Any] = {'hist_n': len(hist), 'cand_n': 0, 'no_quote': 0, 'exright': 0,
                           'mismatch': 0, 'judged': 0, 'watch': [], 'entry': []}
    cand = sorted(quotes.keys()) if offline else candidates(hist)
    res['cand_n'] = len(cand)
    for code in cand:
        bars, q = hist.get(code), quotes.get(code)
        if not bars or not q:
            res['no_quote'] += 1
            continue
        if not offline:
            last_close = _f(bars[-1][4])
            if q['pre_close'] > 0 and last_close > 0 and abs(
                    q['pre_close'] / last_close - 1.0) * 100 > float(cfg['preclose_tol_pct']):
                res['exright'] += 1                       # 除权/基不符：拼进去会造出假缺口
                continue
        af = _f(q.get('af') or 0) or _f(bars[-1][9] or 1)
        lu = q.get('lu') if offline else (
            _opt_float(q.get('lu')) or                          # TQ 给的涨停价（客户端自己算的）
            limit_up_price(q['pre_close'], today, infos.get(code, {}), q['high'], q['low']))
        if not offline and lu and abs(q['price'] - lu) <= 0.005 and zt and \
                code.split('.')[0] not in zt:
            res['mismatch'] += 1                          # 我算的涨停价 vs 东财涨停池 对不上
        pct = (100.0 * (q['price'] / q['pre_close'] - 1.0)) if q['pre_close'] else q.get('pct')
        series = (bars + [[str(today), q['open'], q['high'], q['low'], q['price'],
                           q['pre_close'], pct, lu, q['vol_shares'], af]])[-SERIES_BARS:]
        try:
            row = judge(code, series)
        except Exception as exc:  # noqa: BLE001 单票脏数据不带走整场
            note({'what': 'sysentry_judge_fail', 'code': code, 'err': str(exc)[:160]})
            continue
        if not row:
            continue
        res['judged'] += 1
        row['_name'] = q.get('name') or ''
        row['_af'] = af
        row['_board'] = (infos.get(code) or {}).get('board')
        (res['entry'] if row['entry_ok'] else res['watch']).append(row)
    key = lambda r: (-(r['gap_pct'] or 0),                                  # noqa: E731
                     r['pullback_max_vol_ratio'] if r['pullback_max_vol_ratio'] is not None else 9,
                     r['security_code'])
    res['entry'].sort(key=key)
    res['watch'].sort(key=lambda r: (-(r['gap_pct'] or 0), r['security_code']))
    for i, r in enumerate(res['entry'], 1):
        r['_rank'] = i
    for i, r in enumerate(res['watch'], 1):
        r['_rank'] = i
    return res


def table_rows(res: dict, captured_at: dt.datetime, src: str, quote_day: str,
               day: dt.date) -> list[tuple]:
    """判据结果 → `analytics.intraday_sysentry` 的行（列序与 `store.SYSENTRY_COLUMNS` 互校）。"""
    out: list[tuple] = []
    for kind in ('entry', 'watch'):
        for r in res[kind]:
            out.append((day, captured_at, r.get('spec_version') or SPEC_VERSION,
                        r['security_code'], kind, r.get('_name') or None, r.get('_rank'),
                        r.get('close'), r.get('pre_close'), r.get('pct_change'),
                        r.get('gap_date'), r.get('gap_pct'), r.get('gap_unfilled'),
                        r.get('pullback_max_vol_ratio'), r.get('pullback_down_cnt'),
                        r.get('stop_ref_hfq'), r.get('adjustment_factor'),
                        r.get('_board'), src, quote_day or None))
    return out


def in_trading_window(now: dt.datetime) -> bool:
    """盘中时窗（含竞价后 5 分钟到收盘后 5 分钟）：只有这里才准用不给日期的行情源。"""
    hm = now.hour * 100 + now.minute
    return 925 <= hm <= 1505


def collect(*, now: dt.datetime | None = None, day: dt.date | None = None, offline: bool = False,
            cfg: dict, zt_pool: Iterable[str] = (), note: Callable[[dict], None] = lambda _d: None
            ) -> dict:
    """A 层总入口：取数 + 判据 → `(rows, summary)`。**不落库、不发送**（那两件事归调用方）。"""
    now = now or dt.datetime.now()
    day = day or now.date()
    end = last_daily_day(day)
    hist = history_bars(end) if end else {}
    if offline:
        quotes = offline_today_bars(day)
        src, proven, qday = '库内日线（对账/复盘）', f'{day}收盘落库', str(day)
    else:
        cands = candidates(hist)
        if not cands:
            return {'rows': [], 'summary': {'hist_n': len(hist), 'cand_n': 0, 'no_quote': 0,
                                            'exright': 0, 'mismatch': 0, 'judged': 0,
                                            'entry_n': 0, 'watch_n': 0},
                    'src': '—', 'proven': '—无②候选', 'captured_at': now,
                    'last_day': '无', 'day': day}
        quotes, src, proven = gather_quotes(cands, cfg, day, now, note=note)
        qday = v_day(quotes)
    infos = band_snapshot(sorted(quotes.keys()), day)
    res = build_rows(hist, quotes, today=day, offline=offline, infos=infos,
                     zt=set(zt_pool) if not offline else set(), cfg=cfg, note=note)
    last_day = max((str(b[0])[:10] for v in hist.values() for b in v), default='无')
    summary = {k: res[k] for k in ('hist_n', 'cand_n', 'no_quote', 'exright', 'mismatch', 'judged')}
    summary['entry_n'] = len(res['entry'])
    summary['watch_n'] = len(res['watch'])
    return {'rows': table_rows(res, now, src, qday, day), 'summary': summary, 'src': src,
            'proven': proven, 'captured_at': now, 'last_day': last_day, 'day': day,
            'res': res}
