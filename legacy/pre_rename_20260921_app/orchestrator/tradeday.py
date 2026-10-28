# -*- coding: utf-8 -*-
"""
"现在应该追到哪一天" —— 目标交易日的判定。

单独成模块是因为**有两个地方要用同一套判据**：`/jobs` 页面要在按钮旁边先把
目标日显示出来（点之前就看得见），`scripts/ops/e1_fill_all.py` 真跑的时候要
按它决定补哪几天。这种判据抄两份的后果是页面上写着 08-28、脚本却按 08-27 跑，
两边都"看起来对"。

判定链（顺序不能换）：

  1. **收盘线 15:30**。早于它，当天的盘后数据还没出全，上限只能退到昨天。
     14:00 点"追平"，最新已收盘交易日是上一个交易日，不是今天。
  2. **`ref.trade_calendar` 里 `is_open` 且 ≤ 上限的最大那天**。日历含节假日，
     是权威 —— 靠"星期几"猜会把国庆算成交易日。
  3. 日历比上限还旧时（新年份没同步上）退回 `readiness.expected_trade_date()`
     的星期几估算，并且**说明自己退化了**。日历是从历史行情反推出来的，
     它不知道明天开不开市；不做这一步的话目标永远等于"日历最后那天"，
     判定永远是"已经最新"，追平再也不会去拿新数据（2026-08-27 实测踩过）。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app import config
from app.db import engine
from app.tdx_client import readiness

# 15:30 而不是 15:00：留出清算/盘后文件落地的时间，和 c10_catchup 的 CLOSE_HHMM 一致
CLOSE_HHMM = (15, 30)


def now_bj() -> dt.datetime:
    """
    **北京时间**的"现在"（naive，本地墙钟语义）。

    不用 `dt.datetime.now()` 裸取：交易日是按上海时间算的，机器时区被改过
    （或者以后跑在别的机器/容器里）就会把收盘线判错一整天，而这种错误在页面上
    表现成"最新交易日少了一天"，没人会想到是时区。
    """
    return dt.datetime.now(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)


def closed_cutoff(now: dt.datetime | None = None) -> dt.date:
    """"现在"这个时刻，最多能指望哪一天的盘后数据。"""
    now = now or now_bj()
    return (now.date() if (now.hour, now.minute) >= CLOSE_HHMM
            else now.date() - dt.timedelta(days=1))


def target_date(now: dt.datetime | None = None) -> tuple[dt.date | None, str]:
    """
    返回 `(目标交易日, 判据说明)`。**只读日历，不联网同步** ——
    页面渲染时也会调它，联网会让整页卡住。真跑之前由脚本先同步一次日历。
    """
    now = now or now_bj()
    cutoff = closed_cutoff(now)
    d = engine.scalar(
        'SELECT max(cal_date) FROM ref.trade_calendar '
        ' WHERE is_open AND cal_date <= %s', (cutoff,))
    if d is not None:
        return d, f'日历判定（收盘上限 {cutoff}）'
    return readiness.expected_trade_date(now), '日历不够新，按星期几估算（偏激进）'


def missing_days(target: dt.date) -> list[dt.date]:
    """
    `md.stock_daily` 里缺的交易日（≤ 目标日）。

    **只看"库里最新那天往后"的缺口**，不数远古空洞：这是"追平"，不是"补史"。
    没有这个下界，历史上任何一个缺失日（迁移期就有）都会把窗口起点拉回几年前，
    接着因子/评分的 `--recent` 变成几百天、一跑几小时 —— 点一下按钮半天不结束，
    看起来就是卡死。
    """
    rows = engine.query(
        """
        SELECT c.cal_date FROM ref.trade_calendar c
         WHERE c.is_open AND c.cal_date <= %s
           AND c.cal_date > (SELECT max(trade_date) FROM md.stock_daily)
         ORDER BY c.cal_date
        """, (target,))
    return [r['cal_date'] for r in rows]


def back_window(target: dt.date, back: int) -> dt.date:
    """从目标日往前数 `back` 个交易日，作为辅助源的回补窗口起点。"""
    rows = engine.query(
        'SELECT cal_date FROM ref.trade_calendar '
        ' WHERE is_open AND cal_date <= %s ORDER BY cal_date DESC LIMIT %s',
        (target, max(int(back), 1)))
    return rows[-1]['cal_date'] if rows else target


def trade_days_between(start: dt.date, end: dt.date) -> int:
    return int(engine.scalar(
        'SELECT count(*) FROM ref.trade_calendar '
        ' WHERE is_open AND cal_date BETWEEN %s AND %s', (start, end)) or 1)
