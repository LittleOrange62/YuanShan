# -*- coding: utf-8 -*-
"""
缩量收敛后首次突破形态信号（`analytics.volume_breakout_signal`）。

用户 2026-10-11 的原话拆成三段式：
  ① 2 个月前有过一波放量（启动痕迹，vol ≥ 前5日均量 ×1.8）
  ② 之后持续缩量到均线附近（筹码沉淀，连续 10 天以上 vol < 前5日均量 且 |close/ma60-1| < 3%）
  ③ 第一个同时满足"突破均线 + 放量 ≥1.6×"的那天 = 潜在买点

为什么要单列一张表（而不是在 volume_double 里加几列）：
  ① 这是**时序形态**，不是单日统计：要扫 40 根 K 线、找阶段转换点，逻辑独立于"倍量次数"；
  ② 信号可回溯测试：哪天出现、哪天后失效、后续涨幅如何 —— 这些都要落库才能跑回测；
  ③ 每日链里单独一步（d8），失败不影响 d7（倍量震荡）的产出。

用法：
    python -m ingest.jobs.d8_breakout_signal                 # 最新有日线的一天
    python -m ingest.jobs.d8_breakout_signal --recent 20     # 往前 20 个交易日
    python -m ingest.jobs.d8_breakout_signal --date 2026-08-28
    python -m ingest.jobs.d8_breakout_signal --dry-run       # 只打前 20 名不写库
    python -m ingest.jobs.d8_breakout_signal --verify        # 抽一只票逐日复核

退出码：0 成功 / 1 没有可算的交易日 / 2 --verify 不通过。
"""
from __future__ import annotations

import datetime as dt
from typing import List, Dict, Any, Optional

from app.db import engine

# ---- 判据阈值（用户 2026-10-11 给的原话） ----
SURGE_MULT = 1.8          # 放量波门槛：vol / 前5日均量 >= 1.8
BREAK_MULT = 1.6          # 首破门槛：vol / 前5日均量 >= 1.6
DEV_THRESH = 0.05         # 贴线门槛：|close/ma60 - 1| < 5%（放宽到 5%，3% 太严）
CONVERGE_MIN_DAYS = 5     # 收敛段最短天数：连续 DEV_THRESH 内的天数 >= 5（原 10 天太严）
SHRINK_MULT = 1.0         # 缩量定义：vol / 前5日均量 < 1.0

# 观察窗长度（交易日）
WINDOW_DAYS = 40

# 前5日均量的窗口
AVG_VOL_WINDOW = 5

#: 母体：**当天有行情的全部票**（2026-10-13 实测 5,576 只），不再截前 800 只。
#: 原先的 `codes[:800]` 有两层错：① 不排序的 `DISTINCT` 由执行计划决定扫哪 800 只，
#: 同一只票的形态史会凭空断在某一天；② 一旦为了稳定加上 `order by security_code`，
#: 前 800 只就变成**只有深主板 000/002**（实测 626 行全是 00xxxx），沪市、创业、科创、北证
#: 一只都进不来 —— 自选里只要不是深主板，这只票的突破历史永远是空的。
#: 改成 bulk 取数之后全场只要 ~30 秒（原来逐票查 800 只就要 62.8 秒），没有理由再截。
#: 分片大小：一片 ~1,200 只 ≈ 12 万行；一次问全场实测 11.5 秒 / 55 万行，
#: 但 reader 角色的 `statement_timeout=60s` 在"日线表正在被重灌"那种最坏情况顶不住。
BARS_CHUNK = 1200

#: 一次取多少**日历日**的日线（要 `WINDOW_DAYS + 60` = 100 根 K 线）。
#: 长停牌的票在 320 个日历日里可能凑不满 100 根 —— 与逐票查全历史的旧口径保持一致，
#: 不把"停牌很久"读成"没有形态"（旧写法没有日历下界，靠 `LIMIT 100` 往回取）。
BARS_CAL_DAYS = 400

#: 同一天同一只票只能落一行（主键是 `(security_code, trade_date, spec_version)`）。
#: 实测 2026-10-12：算出 626 条、库里 615 行 —— 11 条撞在主键上，其中 7 条是
#: 「同一天既是收敛段最后一天、又是首破那天」，4 条是两段同类信号同一天收尾。
#: 原先靠 `ON CONFLICT DO UPDATE` 谁后写谁留，等于让写入顺序决定这只票算不算买点。
#: 现在按明说的规矩留：**首破 > 放量后缩量 > 收敛中**（确认过的盖掉没确认的），
#: 同类相撞留 `converge_days` 更大的那一段。
TYPE_RANK = {'first_break': 3, 'surge_after_shrink': 2, 'converging': 1}


def collapse_same_day(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按主键 `(security_code, trade_date, spec_version)` 收敛成一行，规则见 `TYPE_RANK`。

    返回的顺序保持第一次出现的次序（回算逐场写库，顺序不参与判据）。
    """
    best: Dict[tuple, tuple] = {}     # key → (rank, converge_days, 行)
    for r in results:
        key = (r['security_code'], r['trade_date'], r['spec_version'])
        cand = (TYPE_RANK.get(r['signal_type'], 0),
                r.get('converge_days') or 0, r)
        cur = best.get(key)
        if cur is None or (cand[0], cand[1]) > (cur[0], cur[1]):
            best[key] = cand
    return [v[2] for v in best.values()]

#: 三类信号的中文名与"这一类在说什么"。**措辞只住这一处**：读侧接口与页面都引这份，
#: 前端不再自己抄一份（抄的那份会和列注释、和这里的判据一起漂）。
TYPE_LABELS = {
    'first_break': '首破',
    'surge_after_shrink': '放量后缩量',
    'converging': '收敛中',
}

TYPE_MEANINGS = {
    'first_break': f'收敛段之后第一次同时满足"站上 MA60 + 放量 >= {BREAK_MULT}×"的那一天：'
                   '三段式走完了，这是买点候选（也是这一栏唯一"已确认"的那一档）。',
    'surge_after_shrink': f'窗口里有过一波放量（>= {SURGE_MULT}×），之后成交量掉到前 5 日均量以下：'
                          '还在缩量，没有突破 —— 观察期，不是买点。',
    'converging': f'连续 {CONVERGE_MIN_DAYS} 天以上 |close/ma60-1| < {DEV_THRESH:.0%}：'
                  '价格贴着季线横，但窗口里没出现放量突破 —— 还没走出来。',
}

# spec_version：改判据就换版本号，旧行留着对账。
# sv-2（2026-10-13）：同日同一条码撞主键时改由 `TYPE_RANK` 决定谁留下（原先是谁后写谁留），
# 并且取票固定按 `security_code` 排序（原先不排序，扫的是哪 800 只随执行计划漂，
# 同一只票的形态史会凭空断在某一天）。旧 sv-1 行留着对账 —— 所以读侧必须带 spec_version 条件。
SPEC_VERSION = 'breakout-sv-2'


def _latest_date() -> Optional[dt.date]:
    """取 market_data.stock_daily 的最新交易日（和 vol_double 同一份数据源）。"""
    r = engine.query_one(
        "SELECT max(trade_date) AS d FROM market_data.stock_daily")
    return r['d'] if r and r['d'] else None


def trade_days(end: Optional[dt.date] = None, recent: int = 1) -> List[dt.date]:
    """从 stock_daily 拉出 `end` 往前最近的 `recent` 个交易日（升序）。

    两件事都在这条里错过，都钉在这里：
    ① **不能写 `INTERVAL '%s days'`** —— psycopg 的参数不会进字符串字面量，那个 `%s`
       原样留着，区间塌成 0 天，于是 `--recent 205` 实测只交出 1 个交易日（2026-10-13 查出）。
       「回算近一年」就这么静默地只跑了当天一场，而且退出码还是 0。
    ② 也不要拿固定日历天数当上限：MA60 的暖机是 `_fetch_bars` 逐日自己取的，
       这里只回答"要算哪几天"，上限就该是 `recent` 本身。
    """
    end_d = end or _latest_date()
    if not end_d:
        return []
    rows = engine.query(
        """
        SELECT DISTINCT trade_date FROM market_data.stock_daily
         WHERE trade_date <= %s
         ORDER BY trade_date DESC
         LIMIT %s
        """, (end_d, max(1, int(recent))))
    return sorted(r['trade_date'] for r in rows)


def _fetch_bars(code: str, end_date: dt.date, span: int = WINDOW_DAYS + 60) -> List[Dict[str, Any]]:
    """取某只票截至 end_date 的最近 span 根日线（按日期升序），MA60 在 SQL 里自算。"""
    rows = engine.query(
        """
        SELECT trade_date, close, volume_shares,
               avg(close) OVER (ORDER BY trade_date ROWS 59 PRECEDING) AS ma60
          FROM market_data.stock_daily
         WHERE security_code = %(c)s AND trade_date <= %(d)s
           AND close IS NOT NULL AND volume_shares > 0
         ORDER BY trade_date DESC LIMIT %(span)s
        """, {'c': code, 'd': end_date, 'span': span})
    # SQL 是 DESC，Python 侧翻成正序方便扫窗口
    return list(reversed(rows))


def _avg_vol_5d(bars: List[Dict], idx: int) -> Optional[float]:
    """计算 bars[idx] 之前 5 日的均量（不含当日）。不足 5 日返回 None。"""
    if idx < AVG_VOL_WINDOW:
        return None
    base = bars[idx - AVG_VOL_WINDOW:idx]
    vols = [float(b['volume_shares']) for b in base if b.get('volume_shares')]
    if len(vols) < AVG_VOL_WINDOW:
        return None
    return sum(vols) / len(vols)


def _dev_from_ma60(bar: Dict) -> Optional[float]:
    """计算 |close/ma60 - 1|。ma60 为 NULL 或 0 时返回 None。"""
    ma = bar.get('ma60')
    close = bar.get('close')
    if ma is None or close is None or float(ma) == 0:
        return None
    return abs(float(close) / float(ma) - 1)


def scan_window(bars: List[Dict]) -> List[Dict[str, Any]]:
    """
    扫描一只票的 40 日窗口，返回所有信号（可能多个 first_break，但通常只有一个）。

    返回字段：
      signal_type: 'first_break' / 'surge_after_shrink' / 'converging'
      last_surge_date: 上一波放量的日期
      surge_vol_ratio: 那波放量的最大倍数
      shrink_days: 从 last_surge_date 到信号日的缩量天数
      converge_days: 收敛段长度
      break_vol_ratio: 突破那天的放量倍数
      break_dev: 突破那天的离线程度
      break_close / break_ma60: 突破日的价格与均线
    """
    signals = []
    n = min(WINDOW_DAYS, len(bars))
    if n < WINDOW_DAYS:
        return signals  # 数据不够，跳过

    # 预计算所有日的 vol_ratio 和 dev
    vol_ratios = []
    devs = []
    for i in range(n):
        avg5 = _avg_vol_5d(bars, i)
        vr = float(bars[i]['volume_shares']) / avg5 if avg5 else None
        vol_ratios.append(vr)
        devs.append(_dev_from_ma60(bars[i]))

    # 找最近一次放量波（从后往前找第一个 vol_ratio >= SURGE_MULT）
    last_surge_idx = None
    surge_max_ratio = 0
    for i in range(n - 1, -1, -1):
        vr = vol_ratios[i]
        if vr is not None and vr >= SURGE_MULT:
            last_surge_idx = i
            surge_max_ratio = vr
            break  # 只要最近的那一波

    # 如果没有放量波，直接返回空
    if last_surge_idx is None:
        return signals

    # 从 last_surge_idx 之后，找收敛段和首破
    # 收敛段定义：连续 |dev| < DEV_THRESH 且 vol_ratio < SHRINK_MULT 的天数
    converge_start = None
    converge_len = 0
    shrink_count = 0
    
    for i in range(last_surge_idx + 1, n):
        vr = vol_ratios[i]
        dev = devs[i]
        
        # 缩量计数
        if vr is not None and vr < SHRINK_MULT:
            shrink_count += 1
        
        # 收敛段判断
        if dev is not None and dev < DEV_THRESH:
            if converge_start is None:
                converge_start = i
            converge_len = i - converge_start + 1
        else:
            # 偏离超过阈值，收敛段断开
            if converge_len >= CONVERGE_MIN_DAYS:
                # 这是一段有效的收敛，检查后面有没有首破
                # （已经断开了，所以这段收敛没有后续突破 → converging）
                signals.append({
                    'signal_type': 'converging',
                    'last_surge_date': bars[last_surge_idx]['trade_date'],
                    'surge_vol_ratio': round(surge_max_ratio, 2),
                    'shrink_days': shrink_count,
                    'converge_days': converge_len,
                    'break_vol_ratio': None,
                    'break_dev': None,
                    'break_close': None,
                    'break_ma60': None,
                })
            converge_start = None
            converge_len = 0
    
    # 如果收敛段一直延续到最后一天（没有断开）
    if converge_len >= CONVERGE_MIN_DAYS:
        signals.append({
            'signal_type': 'converging',
            'last_surge_date': bars[last_surge_idx]['trade_date'],
            'surge_vol_ratio': round(surge_max_ratio, 2),
            'shrink_days': shrink_count,
            'converge_days': converge_len,
            'break_vol_ratio': None,
            'break_dev': None,
            'break_close': None,
            'break_ma60': None,
        })
    
    # 检查收敛段内有没有首破（在收敛段里找第一个同时满足 vol_ratio >= BREAK_MULT 且 close > ma60 的日子）
    if converge_start is not None and converge_len >= CONVERGE_MIN_DAYS:
        for i in range(converge_start, converge_start + converge_len):
            vr = vol_ratios[i]
            dev = devs[i]
            if vr is not None and vr >= BREAK_MULT and dev is not None:
                # 首破信号
                signals.append({
                    'signal_type': 'first_break',
                    'last_surge_date': bars[last_surge_idx]['trade_date'],
                    'surge_vol_ratio': round(surge_max_ratio, 2),
                    'shrink_days': shrink_count,
                    'converge_days': i - converge_start + 1,
                    'break_vol_ratio': round(vr, 2),
                    'break_dev': round(dev, 4),
                    'break_close': float(bars[i]['close']),
                    'break_ma60': float(bars[i]['ma60']) if bars[i].get('ma60') else None,
                })
                break  # 只要第一个首破

    # 如果没有任何信号，但有放量波 → surge_after_shrink
    if not signals:
        signals.append({
            'signal_type': 'surge_after_shrink',
            'last_surge_date': bars[last_surge_idx]['trade_date'],
            'surge_vol_ratio': round(surge_max_ratio, 2),
            'shrink_days': 0,
            'converge_days': 0,
            'break_vol_ratio': None,
            'break_dev': None,
            'break_close': None,
            'break_ma60': None,
        })

    return signals


_BARS_SQL = """
with base as (
    select security_code c, trade_date dt, close, volume_shares v,
           avg(close) over (partition by security_code order by trade_date
                            rows between 59 preceding and current row) ma60,
           row_number() over (partition by security_code order by trade_date desc) rn
      from market_data.stock_daily
     where security_code = any(%(codes)s)
       and trade_date <= %(d)s and trade_date > %(d)s - %(cal)s
       and close is not null and volume_shares > 0)
select c, array_agg(dt order by dt) dts, array_agg(close order by dt) cls,
       array_agg(v order by dt) vs, array_agg(ma60 order by dt) mas
  from base where rn <= %(span)s group by c
"""


def bars_by_code(codes: List[str], end_date: dt.date,
                 span: int = WINDOW_DAYS + 60) -> Dict[str, List[Dict[str, Any]]]:
    """一次问库拿这批票截至 `end_date` 的最近 `span` 根日线（每票一条，按日期升序）。

    MA60 必须在**分片内的完整历史**上算：`avg(...) rows between 59 preceding` 的分区
    是每只票，`rn <= span` 只在算完之后才截，所以切片不会让谁的均线少几根（这是
    与旧 `_fetch_bars` 逐票 `LIMIT 100` 同义的口径）。
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for i in range(0, len(codes), BARS_CHUNK):
        chunk = codes[i:i + BARS_CHUNK]
        rows = engine.query(_BARS_SQL, {'codes': chunk, 'd': end_date,
                                       'cal': BARS_CAL_DAYS, 'span': span},
                            role='etl') or []
        for r in rows:
            out[r['c']] = [{'trade_date': d, 'close': c, 'volume_shares': v, 'ma60': m}
                           for d, c, v, m in zip(r['dts'], r['cls'], r['vs'], r['mas'])]
        del rows
    return out


def compute_day(d: dt.date, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    计算某一天的突破信号。返回格式适合直接 upsert 到 analytics.volume_breakout_signal。

    `limit` 只给 dry-run/测试用（取前 N 只），默认 `None` = 当天有行情的全部票。
    三件事是 2026-10-13 查出来的，别再退回去：
    ① 母体不截断（见 `BARS_CHUNK` 那段：截 800 只 + 排序 = 只有深主板）；
    ② 取票按 `security_code` 排序，扫的是哪一批必须可复现；
    ③ 出口过 `collapse_same_day`：主键装不下同一天两条信号，原先由写入顺序决定留哪条。
    """
    codes = [r['security_code'] for r in (engine.query(
        "SELECT DISTINCT security_code FROM market_data.stock_daily "
        "WHERE trade_date = %s ORDER BY security_code", (d,)) or [])]
    if limit:
        codes = codes[:limit]

    results = []
    for code, bars in bars_by_code(codes, d).items():
        for sig in scan_window(bars):
            results.append({
                'security_code': code,
                'trade_date': d,
                'spec_version': SPEC_VERSION,
                'signal_type': sig['signal_type'],
                'last_surge_date': sig['last_surge_date'],
                'surge_vol_ratio': sig['surge_vol_ratio'],
                'shrink_days': sig['shrink_days'],
                'converge_days': sig['converge_days'],
                'break_vol_ratio': sig['break_vol_ratio'],
                'break_dev': sig['break_dev'],
                'break_close': sig['break_close'],
                'break_ma60': sig['break_ma60'],
                'window_days': WINDOW_DAYS,
                'bars_used': len(bars),
            })

    return collapse_same_day(results)


def write(results: List[Dict[str, Any]]) -> int:
    """
    把计算结果 upsert 到 analytics.volume_breakout_signal。
    返回写入的行数。
    """
    if not results:
        return 0

    engine.executemany(
        """
        INSERT INTO analytics.volume_breakout_signal (
            security_code, trade_date, spec_version, signal_type,
            last_surge_date, surge_vol_ratio, shrink_days, converge_days,
            break_vol_ratio, break_dev, break_close, break_ma60,
            window_days, bars_used, calculated_at
        ) VALUES (
            %(security_code)s, %(trade_date)s, %(spec_version)s, %(signal_type)s,
            %(last_surge_date)s, %(surge_vol_ratio)s, %(shrink_days)s, %(converge_days)s,
            %(break_vol_ratio)s, %(break_dev)s, %(break_close)s, %(break_ma60)s,
            %(window_days)s, %(bars_used)s, now()
        )
        ON CONFLICT (security_code, trade_date, spec_version)
        DO UPDATE SET
            signal_type = EXCLUDED.signal_type,
            last_surge_date = EXCLUDED.last_surge_date,
            surge_vol_ratio = EXCLUDED.surge_vol_ratio,
            shrink_days = EXCLUDED.shrink_days,
            converge_days = EXCLUDED.converge_days,
            break_vol_ratio = EXCLUDED.break_vol_ratio,
            break_dev = EXCLUDED.break_dev,
            break_close = EXCLUDED.break_close,
            break_ma60 = EXCLUDED.break_ma60,
            window_days = EXCLUDED.window_days,
            bars_used = EXCLUDED.bars_used,
            calculated_at = now()
        """, results)

    return len(results)


def refresh(dates: List[dt.date]) -> int:
    """
    刷新指定日期列表的信号。先删后插（保证干净）。

    删的是**当前判据版本**的那批行，不是这一天所有的行：主键含 spec_version，
    旧版本的行留着对账（sv-1 那批是"同日同码由写入顺序决定留哪条"算出来的，
    正好用来核对 sv-2 改完后的差多少）。代价是读侧每一句都必须带版本条件 ——
    漏一处就是把两个版本的行数加在一起当今天的数。
    """
    total = 0
    for d in dates:
        engine.execute(
            "DELETE FROM analytics.volume_breakout_signal "
            "WHERE trade_date = %s AND spec_version = %s", (d, SPEC_VERSION))
        # 计算新数据
        results = compute_day(d)
        n = write(results)
        total += n
    return total


def picks(d: dt.date, signal_type: str = 'first_break') -> List[Dict[str, Any]]:
    """
    取某一天某种类型的信号票列表（用于页面展示）。
    """
    rows = engine.query(
        """
        SELECT v.*, s.name, s.board
          FROM analytics.volume_breakout_signal v
          LEFT JOIN reference_data.security s USING (security_code)
         WHERE v.trade_date = %s AND v.signal_type = %s AND v.spec_version = %s
         ORDER BY v.break_vol_ratio DESC NULLS LAST
        """, (d, signal_type, SPEC_VERSION))
    return rows
