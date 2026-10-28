"""
就绪判定 —— 这套自动化的地基。

**完成判据只认数据里的日期，不认时间。** 这条规则是整个包里最重要的一条。

历史上的做法是"点了开始下载，然后 `sleep 600`，然后宣布完成"。
它错在三个地方：

- 下载快的时候白等 10 分钟；
- 下载慢或者卡住的时候，10 分钟后照样宣布成功，后面全链路拿旧数据算，
  算出来的分数看不出任何异常；
- 下载窗口消失也不等于数据到位 —— 实测出现过窗口关了而 `.day` 尾部
  日期还是上一个交易日的情况（通达信自己那一侧失败了但没报错）。

所以这里提供的判据都是"去读文件尾部那条记录的日期，跟目标交易日比"。
慢一点，但它不会撒谎。

另一件容易错的是**目标交易日算错**。周一早上跑，最近的已收盘交易日是上周五，
拿周一当目标会永远等不到。当天下午 3 点前跑，当天的盘后数据还没出，
也要退回上一个交易日。这两种情况都实测踩过。
"""

from __future__ import annotations

import datetime as dt
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

from app.config import VIPDOC_DIR
from app.ingest.tdx_binary import (
    DAY_RECORD,
    DAY_STRUCT,
    MIN_RECORD,
    MIN_STRUCT,
    _to_date,
    to_minute_dt,
)

# 盘后数据出来的时间。收盘 15:00，通达信服务器整理完通常在 15:30 之后。
# 早于这个点跑，当天的盘后数据不可能有，目标交易日必须退回上一天。
POST_CLOSE_CUTOFF = dt.time(15, 30)

MARKETS = ('sh', 'sz', 'bj')

# 抽样检查用的锚定代码。挑的都是**长期存在、不会停牌整年、每天必有成交**的票，
# 这样"文件尾部日期 == 目标交易日"才是可靠判据。
# 停牌股的 .day 尾部日期会停在停牌前，拿它判定会永远失败。
ANCHORS: dict[str, tuple[str, ...]] = {
    'sh': ('600519', '601398', '600036'),      # 茅台、工行、招行
    'sz': ('000001', '000002', '300750'),      # 平安银行、万科、宁德时代
    # 北交所：按 2026-08-25 成交额取前几名（920045 十亿级，其余四亿上下）。
    # **不要用 832000/833000 这类老三板号段** —— vipdoc\bj\lday 里根本没有
    # 这两个文件，拿它们当锚定会永远判成"未就绪"。实测 bj 目录只有 343 只，
    # 其中绝大多数是 920xxx。
    'bj': ('920045', '920438', '920087'),
}

# 指数文件。通达信登录后**自动**同步指数，不需要手动触发，
# 所以指数到位 ≠ 个股到位。这两条判据必须分开看：
# 指数新了而个股旧了，正是"该去手动点盘后数据下载"的信号。
INDEX_FILES = {'sh': '999999', 'sz': '399001'}

# 北交所整体流动性差，允许更多锚定代码落空。
MIN_FRESH_RATIO = 0.6


@dataclass
class Probe:
    """一个文件的探测结果。"""

    market: str
    symbol: str
    path: Path
    last_date: dt.date | None
    exists: bool
    error: str = ''

    def fresh(self, target: dt.date) -> bool:
        return self.last_date is not None and self.last_date >= target


@dataclass
class Verdict:
    """
    一次就绪判定的完整结果。

    `ok` 是结论，`probes` 是证据。**日志里必须把证据一起打出来** ——
    只打"未就绪"的话，排查时分不清是文件没更新、路径错了、还是目标日期算错了。
    """

    ok: bool
    target: dt.date
    kind: str
    probes: list[Probe] = field(default_factory=list)
    note: str = ''

    def report(self) -> str:
        lines = [f'[{self.kind}] 目标交易日 {self.target} → '
                 f'{"就绪" if self.ok else "未就绪"}']
        if self.note:
            lines.append(f'  {self.note}')
        for p in self.probes:
            mark = '√' if p.fresh(self.target) else '×'
            last = p.last_date or ('文件不存在' if not p.exists else '解析不出日期')
            lines.append(f'  {mark} {p.market}{p.symbol}: {last}'
                         + (f'  ({p.error})' if p.error else ''))
        return '\n'.join(lines)

    @property
    def stale_days(self) -> int | None:
        """落后目标交易日几个自然日。给页面显示用。"""
        dates = [p.last_date for p in self.probes if p.last_date]
        if not dates:
            return None
        return (self.target - max(dates)).days


# ---- 目标交易日 ---------------------------------------------------------

def expected_trade_date(now: dt.datetime | None = None) -> dt.date:
    """
    算出"现在应该已经有盘后数据的那个交易日"。

    三步：

    1. 当天盘后数据还没出（早于 `POST_CLOSE_CUTOFF`）就把上限退到昨天；
    2. 查 `ref.trade_calendar` —— 它含节假日，是权威；
    3. **但日历只覆盖到已有数据的最后一天。** `ref.trade_calendar` 是从
       历史行情反推出来的，它不知道明天开不开市。如果上限已经越过日历的
       最后一个交易日，就必须退回按星期几估算，否则永远算出"日历里最后
       那一天"，于是判定永远是"已经最新"，整套自动化再也不会去下载新数据
       —— 这正是实测遇到的：08-27 早上跑，日历最新到 08-25，
       目标就算成 08-25，而真正该拿的是 08-26。

    估算认不出节假日，所以只在越界时用，并且返回值会偏"激进"
    （把节假日当交易日）。宁可多等一轮、报"未就绪"，也不要漏掉一个交易日。
    """
    now = now or dt.datetime.now()
    today = now.date()
    # 当天盘后数据还没出的话，目标只能是上一个交易日。
    ceiling = today if now.time() >= POST_CLOSE_CUTOFF else today - dt.timedelta(days=1)

    guess = ceiling
    while guess.weekday() >= 5:      # 周六 5、周日 6
        guess -= dt.timedelta(days=1)

    try:
        from app.db import engine

        latest = engine.scalar(
            'SELECT max(cal_date) FROM ref.trade_calendar WHERE is_open',
            role='reader')
        if latest is None:
            return guess
        if guess > latest:
            # 日历没覆盖到，用估算。
            return guess
        cal = engine.scalar(
            'SELECT max(cal_date) FROM ref.trade_calendar '
            'WHERE is_open AND cal_date <= %s', (ceiling,), role='reader')
        return cal or guess
    except Exception:
        return guess


# ---- 文件尾部日期 -------------------------------------------------------

def last_day_date(path: Path) -> tuple[dt.date | None, str]:
    """
    读 `.day` 最后一条记录的日期。

    **只 seek 到尾部读 32 字节**，不解析整个文件 —— 一只票十年历史两千多条，
    全市场五千多只，每轮判定都全量解析的话一次就是几十秒，
    而这个函数在等待循环里每 15 秒调一次。
    """
    try:
        size = path.stat().st_size
    except OSError as e:
        return None, str(e)
    if size < DAY_RECORD:
        return None, f'文件只有 {size} 字节，不足一条记录'
    # 通达信盘中写文件时尾部可能是半条记录，所以从对齐边界往回取。
    usable = size - size % DAY_RECORD
    try:
        with path.open('rb') as f:
            f.seek(usable - DAY_RECORD)
            buf = f.read(DAY_RECORD)
    except OSError as e:
        return None, str(e)
    if len(buf) < DAY_RECORD:
        return None, '尾部读取不足'
    packed = DAY_STRUCT.unpack(buf)[0]
    d = _to_date(packed)
    if d is None:
        return None, f'尾部日期字段解不出来（raw={packed}）'
    return d, ''


def last_minute_dt(path: Path) -> tuple[dt.datetime | None, str]:
    """读 `.lc1`/`.lc5` 最后一条记录的时间戳。"""
    try:
        size = path.stat().st_size
    except OSError as e:
        return None, str(e)
    if size < MIN_RECORD:
        return None, f'文件只有 {size} 字节，不足一条记录'
    usable = size - size % MIN_RECORD
    try:
        with path.open('rb') as f:
            f.seek(usable - MIN_RECORD)
            buf = f.read(MIN_RECORD)
    except OSError as e:
        return None, str(e)
    try:
        didx, minute = MIN_STRUCT.unpack(buf)[:2]
    except struct.error as e:
        return None, str(e)
    stamp = to_minute_dt(didx, minute)
    if stamp is None:
        return None, f'尾部时间字段解不出来（didx={didx} minute={minute}）'
    return stamp, ''


# ---- 抽样探测 -----------------------------------------------------------

def _day_path(market: str, symbol: str, vipdoc: Path) -> Path:
    return vipdoc / market / 'lday' / f'{market}{symbol}.day'


def _minute_path(market: str, symbol: str, vipdoc: Path, period: int) -> Path:
    sub = 'minline' if period == 1 else 'fzline'
    ext = 'lc1' if period == 1 else 'lc5'
    return vipdoc / market / sub / f'{market}{symbol}.{ext}'


class Readiness:
    """
    就绪判定的入口。所有判据都在这里，**别在 step 里自己写判据** ——
    判据散落各处就必然出现某一处偷懒改成看时间的。
    """

    def __init__(self, vipdoc: Path | None = None,
                 *, extra_samples: int = 0, seed: int | None = None):
        self.vipdoc = vipdoc or VIPDOC_DIR
        self.extra_samples = extra_samples
        self._rng = random.Random(seed)

    # -- 指数（登录后自动同步，不需手动触发） --
    def index_ready(self, target: dt.date | None = None) -> Verdict:
        target = target or expected_trade_date()
        probes = []
        for market, symbol in INDEX_FILES.items():
            p = _day_path(market, symbol, self.vipdoc)
            d, err = last_day_date(p) if p.exists() else (None, '')
            probes.append(Probe(market, symbol, p, d, p.exists(), err))
        ok = all(p.fresh(target) for p in probes)
        return Verdict(ok, target, '指数自动同步', probes,
                       note='' if ok else '指数还没到位，说明登录后的自动同步没跑完')

    # -- 个股日线（必须手动触发盘后数据下载） --
    def day_ready(self, target: dt.date | None = None,
                  *, markets: tuple[str, ...] = MARKETS) -> Verdict:
        """
        **多市场多代码抽样，不能只看一只票。**

        只看 `sh600519` 的话，遇到"沪市下来了深市没下来"就会误判成功。
        实测通达信的盘后下载是分市场进行的，中途失败一个市场很常见。
        """
        target = target or expected_trade_date()
        probes: list[Probe] = []
        for market in markets:
            for symbol in self._symbols(market):
                p = _day_path(market, symbol, self.vipdoc)
                d, err = last_day_date(p) if p.exists() else (None, '')
                probes.append(Probe(market, symbol, p, d, p.exists(), err))
        return self._judge(target, '个股日线', probes, markets)

    # -- 分钟线（第二遍下载） --
    def minute_ready(self, target: dt.date | None = None, *, period: int = 1,
                     markets: tuple[str, ...] = ('sh', 'sz')) -> Verdict:
        """
        分钟线判据看**日期部分**，不看具体到哪一分钟。

        收盘后完整的 1 分钟线尾部应该是 15:00 前后，但通达信在不同版本里
        对集合竞价那几分钟的处理不一样（有的写到 15:00，有的 14:57）。
        拿"必须等于 15:00"当判据会随版本失效，看日期就够了。
        """
        target = target or expected_trade_date()
        probes: list[Probe] = []
        for market in markets:
            for symbol in self._symbols(market):
                p = _minute_path(market, symbol, self.vipdoc, period)
                if not p.exists():
                    probes.append(Probe(market, symbol, p, None, False))
                    continue
                stamp, err = last_minute_dt(p)
                probes.append(Probe(market, symbol, p,
                                    stamp.date() if stamp else None, True, err))
        ext = 'lc1' if period == 1 else 'lc5'
        return self._judge(target, f'分钟线 .{ext}', probes, markets)

    def _symbols(self, market: str) -> list[str]:
        base = list(ANCHORS.get(market, ()))
        if self.extra_samples <= 0:
            return base
        # 随机补几只，防止锚定代码正好全都停牌/退市造成系统性误判。
        d = self.vipdoc / market / 'lday'
        if not d.is_dir():
            return base
        pool = [f.stem[2:] for f in d.glob(f'{market}*.day')]
        pool = [s for s in pool if s not in base]
        if not pool:
            return base
        return base + self._rng.sample(pool, min(self.extra_samples, len(pool)))

    def _judge(self, target: dt.date, kind: str, probes: list[Probe],
               markets: tuple[str, ...]) -> Verdict:
        """
        **按市场分别判定，再取交集。** 某个市场整体不新就是没就绪，
        哪怕另外两个市场把总体比例拉过了线。

        单市场内部允许少数代码不新（停牌、退市、当天无成交），
        但比例必须过 `MIN_FRESH_RATIO`。
        """
        bad: list[str] = []
        for market in markets:
            group = [p for p in probes if p.market == market]
            if not group:
                continue
            fresh = sum(1 for p in group if p.fresh(target))
            if fresh == 0:
                bad.append(f'{market} 全部落后')
            elif fresh / len(group) < MIN_FRESH_RATIO:
                bad.append(f'{market} 只有 {fresh}/{len(group)} 到位')
        ok = not bad
        note = '' if ok else '；'.join(bad)
        if ok:
            note = f'{sum(1 for p in probes if p.fresh(target))}/{len(probes)} 个锚定代码到位'
        return Verdict(ok, target, kind, probes, note)

    # -- 数据库侧 --
    def db_ready(self, target: dt.date | None = None) -> Verdict:
        """
        PG 里有没有这一天。

        **权威口径是 PG，不是 `D:\\finance\\data`。** vipdoc 到位只说明
        通达信那边下完了，还得跑 `c1_day.py` 才算真的进了库。
        这条判据用来决定"要不要跑入库"，以及入库之后核对。
        """
        target = target or expected_trade_date()
        probe = Probe('pg', 'md.stock_daily',
                      Path('md.stock_daily'), None, True)
        try:
            from app.db import engine

            n = engine.scalar(
                'SELECT count(*) FROM md.stock_daily WHERE trade_date = %s',
                (target,), role='reader')
            probe = Probe('pg', 'md.stock_daily', Path('md.stock_daily'),
                          target if n else None, True,
                          f'{n or 0} 行')
            # 5000 只出头是全市场；只有几百行说明入库中断了。
            ok = bool(n and n > 4000)
            note = f'{n or 0} 行' if ok else f'只有 {n or 0} 行，全市场应在 5000 行以上'
        except Exception as e:
            ok = False
            note = f'查不通数据库：{e}'
        return Verdict(ok, target, 'PG 入库', [probe], note)
