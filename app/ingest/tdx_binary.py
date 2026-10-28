# -*- coding: utf-8 -*-
"""
通达信二进制行情文件解析：`.day`（日线）与 `.lc1`/`.lc5`（1/5 分钟线）。

**格式已用实测校验，不是照抄网上的字段表。**
`sh600519.day` 尾部三条与 market_data.stock_daily 里已迁好的行逐字段相同：
    20260825 o=1311.89 h=1317.00 l=1301.11 c=1304.00 vol=2111118 amount=2757527040
这同时反证了旧库 vol 是「手」需要 ×100 的结论 —— vipdoc 的 vol 本来就是「股」。

日线 `.day`，每条 32 字节，小端：
    uint32  date        20260825 这样的十进制年月日，不是时间戳
    uint32  open        单位「分」，÷100 得元
    uint32  high
    uint32  low
    uint32  close
    float32 amount      元
    uint32  vol         股（基金是「份」）
    uint32  reserved    实测非恒 0（见过 65536），不要拿它做校验

分钟线 `.lc1`/`.lc5`，每条 32 字节，小端：
    uint16  date_idx    (年-2004)*2048 + 月*100 + 日
    uint16  minute      当日第几分钟（自 0:00 起算）
    float32 open/high/low/close
    float32 amount      元
    uint32  vol         股
    uint32  reserved

**分钟线的年份基准是 2004 不是 1900。** 实测 sh600519.lc1 尾部
didx=45881 → 22*2048+825，月日解出 08-25 完全正确，minute=900 即 15:00
（A 股收盘），而那一天正是 2026-08-25；同一 didx 下恰好 240 条
（4 小时 × 60 分钟，一个完整交易日）。所以 22 对应 2026，基准是 2004。
按 1900 解会把每一根分钟线写成 1922 年。


文件名即代码：`sh600519.day` → 600519.SH，`bj920000.day` → 920000.BJ。
"""
from __future__ import annotations

import datetime as dt
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DAY_RECORD = 32
DAY_STRUCT = struct.Struct('<IIIIIfII')

MIN_RECORD = 32
MIN_STRUCT = struct.Struct('<HHfffffII')

MARKET_BY_DIR = {'sh': 'SH', 'sz': 'SZ', 'bj': 'BJ'}

# 价格上限（元）。A 股最贵的茅台千余元；越过这个量级基本是解析错位而不是真价格。
PRICE_SANITY_MAX = 100_000.0


@dataclass(frozen=True)
class DayBar:
    security_code: str
    trade_date: dt.date
    open: float
    high: float
    low: float
    close: float
    vol: int          # 个股是股，基金是份
    amount: float     # 元


@dataclass(frozen=True)
class MinuteBar:
    security_code: str
    dt: dt.datetime
    open: float
    high: float
    low: float
    close: float
    vol: int
    amount: float


def ts_code_of(path: Path) -> str | None:
    """`sh600519.day` → `600519.SH`；认不出来返回 None，交给调用方决定是跳过还是报错。"""
    stem = path.stem.lower()
    market = MARKET_BY_DIR.get(stem[:2])
    symbol = stem[2:]
    if not market or len(symbol) != 6 or not symbol.isdigit():
        return None
    return f'{symbol}.{market}'


def _to_date(packed: int) -> dt.date | None:
    y, md = divmod(packed, 10000)
    m, d = divmod(md, 100)
    if not (1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def parse_day(path: Path, *, security_code: str | None = None) -> Iterator[DayBar]:
    """
    流式读一个 `.day`。坏记录跳过而不是抛错 —— 通达信在盘中写文件时
    尾部可能是半条记录，为一个残缺尾巴放弃整只票的历史不值得。
    """
    code = security_code or ts_code_of(path)
    if not code:
        raise ValueError(f'无法从文件名判断代码：{path}')
    with path.open('rb') as f:
        while True:
            buf = f.read(DAY_RECORD * 4096)
            if not buf:
                return
            usable = len(buf) - len(buf) % DAY_RECORD
            for off in range(0, usable, DAY_RECORD):
                packed, o, h, low, c, amount, vol, _res = DAY_STRUCT.unpack_from(buf, off)
                d = _to_date(packed)
                if d is None:
                    continue
                o, h, low, c = o / 100.0, h / 100.0, low / 100.0, c / 100.0
                if c <= 0 or c > PRICE_SANITY_MAX:
                    continue
                yield DayBar(code, d, o, h, low, c, int(vol), float(amount))


# 分钟线 date_idx 的年份基准。见模块 docstring 里的实测推导。
MIN_YEAR_BASE = 2004


def to_minute_dt(date_idx: int, minute: int) -> dt.datetime | None:
    """
    分钟线的 `(date_idx, minute)` → datetime。解不出来返回 None。

    抽出来是因为**判定数据新鲜度时只需要读尾部一条记录**，
    不该为了拿一个日期把整个文件流式解析一遍（见 tdx_client/readiness.py）。
    两处共用同一份解码逻辑，避免其中一处改了另一处没改。
    """
    y, rem = divmod(date_idx, 2048)
    m, d = divmod(rem, 100)
    if not (1 <= m <= 12 and 1 <= d <= 31) or not (0 <= minute < 1440):
        return None
    try:
        return dt.datetime(MIN_YEAR_BASE + y, m, d) + dt.timedelta(minutes=minute)
    except ValueError:
        return None


def _minute_bar(code: str, didx: int, minute: int, o: float, h: float, low: float,
                c: float, amount: float, vol: int) -> MinuteBar | None:
    """一根分钟线的解码 + 合法性判据。**正解析和反向定位共用这一份**，
    否则两边规则一漂移，就会出现在不同路径上算出不同结果的东西。"""
    stamp = to_minute_dt(didx, minute)
    if stamp is None:
        return None
    if c <= 0 or c > PRICE_SANITY_MAX:
        return None
    return MinuteBar(code, stamp, o, h, low, c, int(vol), float(amount))


def parse_minute(path: Path, *, security_code: str | None = None) -> Iterator[MinuteBar]:
    """流式读一个 `.lc1`/`.lc5`。"""
    code = security_code or ts_code_of(path)
    if not code:
        raise ValueError(f'无法从文件名判断代码：{path}')
    with path.open('rb') as f:
        while True:
            buf = f.read(MIN_RECORD * 4096)
            if not buf:
                return
            usable = len(buf) - len(buf) % MIN_RECORD
            for off in range(0, usable, MIN_RECORD):
                (didx, minute, o, h, low, c,
                 amount, vol, _res) = MIN_STRUCT.unpack_from(buf, off)
                bar = _minute_bar(code, didx, minute, o, h, low, c, amount, vol)
                if bar is not None:
                    yield bar


def _min_key(d: dt.date) -> int:
    """日期 → `date_idx` 的比较键。

    编码是 `date_idx = (年-2004)*2048 + 月*100 + 日`（见 `to_minute_dt`），
    而 `月*100+日 ≤ 1231 < 2048`，所以**按 date_idx 排序 = 按日历排序**，
    可以直接拿它做二分的界，不必先把记录解成 datetime。
    """
    return (d.year - MIN_YEAR_BASE) * 2048 + d.month * 100 + d.day


def minute_window(path: Path, since: dt.date, until: dt.date | None = None,
                  *, security_code: str | None = None,
                  block_records: int = 256):
    """
    只取 `[since, until]` 这一段分钟线，返回按时间升序的列表。

    为什么要有它：`parse_minute` + 事后 `if d < since: continue` 是
    **O(整个文件)** —— 一个 `.lc1` 存着几个月、每票每天 240 根，`--date` 一天
    照样把几个月全解一遍。实测 `c16_minute --date 2026-09-23`（单市场单周期）
    跑不完就是栽在这里；而这条链下完分钟线正指着它入库。

    两道探针 + 一次二分，都是**只读几条记录**的开销：
      1. 尾条 `date_idx < since` → 整个文件都在窗口之前，**整文件跳过**
         （停牌/退市/很久没更新的那批，占文件数的一大半，一个字节都不多读）；
      2. 头条已越过 `until` → 文件比窗口还新，同样空手返回；
      3. 二分到第一条 `>= since` 的记录，从那里顺序读到窗口右端 ——
         目标日离文件尾隔了 3 个交易日时，不再需要先把中间那 3 天解一遍。

    返回 `None` 表示**这个文件不能走快速路径**（长度不整除、读不满一块、
    或窗口边界前后不升序 = 文件被乱序补写过），调用方必须回退 `parse_minute`。
    宁可慢，不能悄悄少数据。
    """
    code = security_code or ts_code_of(path)
    if not code:
        return None
    # `until=None` 是**没有上界**（`--since` 回填就是这种），不是"等于 since"。
    # 写成 `until = until or since` 会把回填窗口静默截成一天 —— 实测
    # `sh881071.lc5` 该给 480 根只给 48 根。
    try:
        size = path.stat().st_size
    except OSError:
        return None
    n_rec, rem = divmod(size, MIN_RECORD)
    if rem:
        return None                      # 长度不整除：布局不认识，交给全量解析
    if n_rec == 0:
        return []
    k_since = _min_key(since)
    k_until = _min_key(until) if until is not None else None
    out: list[MinuteBar] = []
    try:
        with path.open('rb') as f:
            def key_at(i: int) -> int | None:
                f.seek(i * MIN_RECORD)
                raw = f.read(MIN_RECORD)
                if len(raw) < MIN_RECORD:
                    return None
                return MIN_STRUCT.unpack_from(raw, 0)[0]

            last = key_at(n_rec - 1)
            if last is None:
                return None
            if last < k_since:
                return []                        # 整文件都在窗口之前
            if k_until is not None:
                first = key_at(0)
                if first is None:
                    return None
                if first > k_until:
                    return []
            # 二分：找第一条 `date_idx >= since`
            lo, hi = 0, n_rec - 1
            while lo < hi:
                mid = (lo + hi) // 2
                k = key_at(mid)
                if k is None:
                    return None
                if k < k_since:
                    lo = mid + 1
                else:
                    hi = mid
            start = lo
            if start:
                prev = key_at(start - 1)
                if prev is None:
                    return None
                seam = key_at(start)
                if prev > seam:
                    # 边界是乱序的：反向定位的前提（升序）不成立，交回全量解析
                    return None
            pos = start
            stop = False
            while pos < n_rec and not stop:
                take = min(block_records, n_rec - pos)
                f.seek(pos * MIN_RECORD)
                buf = f.read(take * MIN_RECORD)
                if len(buf) < take * MIN_RECORD:
                    return None
                seq = []
                for off in range(0, len(buf), MIN_RECORD):
                    (didx, minute, o, h, low, c, amount, vol, _r) = MIN_STRUCT.unpack_from(buf, off)
                    seq.append(didx)
                    if didx < k_since:
                        continue
                    if k_until is not None and didx > k_until:
                        stop = True              # 已经读到窗口右端之外
                        break
                    bar = _minute_bar(code, didx, minute, o, h, low, c, amount, vol)
                    if bar is not None:          # 解不出/价格离谱的记录丢掉，与 parse_minute 同规矩
                        out.append(bar)
                pos += take
                # 读到的这段必须升序 —— 与旧实现同一条校验，只是范围换成窗口内
                if any(seq[i] < seq[i - 1] for i in range(1, len(seq))):
                    return None
            out.sort(key=lambda b: b.dt)
            return out
    except OSError:
        return None


def iter_day_files(vipdoc: Path, markets: tuple[str, ...] = ('sh', 'sz', 'bj')) -> Iterator[Path]:
    for m in markets:
        d = vipdoc / m / 'lday'
        if d.is_dir():
            yield from sorted(d.glob('*.day'))


#: 分钟线周期 → (vipdoc 子目录, 文件后缀)。1 分钟在 minline/*.lc1，
#: 5 分钟在 fzline/*.lc5（"分时线"）。见 tdx_client/readiness.py 的 _minute_path。
MINUTE_SUBDIR = {1: ('minline', 'lc1'), 5: ('fzline', 'lc5')}


def iter_minute_files(vipdoc: Path, period: int,
                      markets: tuple[str, ...] = ('sh', 'sz', 'bj')) -> Iterator[Path]:
    try:
        sub, ext = MINUTE_SUBDIR[period]
    except KeyError:
        raise ValueError(f'不支持的分钟周期：{period}（只认 1 或 5）') from None
    for m in markets:
        d = vipdoc / m / sub
        if d.is_dir():
            yield from sorted(d.glob(f'*.{ext}'))
