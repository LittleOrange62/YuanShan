# -*- coding: utf-8 -*-
"""`--date D` 的诚实性闸门（2026-09-30 自动执行机制自查后立）。

**为什么要有这个文件**：实测（`.local/audit_missing_target.py`，目标日 2026-10-01，
这场在上游根本不存在）—— 我负责的 10 个作业**全部返回 rc=0 却没交付目标日**：

| 作业 | 拿日期的方式 | 缺目标日时它干了什么 |
|---|---|---|
| d1/d2 | 交易日历（`recent_trade_dates`） | 照常算那场 → 写 0 行 → rc=0 |
| d4/d5/d6/d7/qd1/qd2/qd3 | **有数的最近 N 场**（`heat_days`/`scored_days`/`trade_days`） | **退回去算上一场已有的日子**，打印出来的还是那一场，rc=0 |
| d3 | 同 d4 | 同上 |

第二种最坏：页面上"评分/点火/情绪"全都新鲜、日期也写了、数字也对，**只是晚一场**。
`e1_fill_all` 的 `stopped` 门只跳 `indicators/scores/sectortrend` 三个名字，
下游七步照跑，所以链子看起来是绿的。

规矩（一句话）：**明确要 D，就必须交付 D；交不出就 rc=1 并说清缺在哪。**
不指定 `--date`（`--recent N` / `--start..--end` / `--full`）时**不拦** —— 那些形态本来就是
"把有数的日子重算一遍"，重算代价为零、也不会交付假日期。

`min_rows` 给的是"这场有多少输入才算存在"：默认 1 行；个股日线用 1000（实测 5,561 行/场，
零星几行不叫存在，那正是 09-29 那次「日期齐但派生列空」的同构坑）。
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from app.db import engine

#: 个股日线一场约 5,561 只；低于这个量就是"半截的一天"，不能当有数
STOCK_ROWS_FLOOR = 1000


def missing_target_reason(raw_date: Optional[str], table: str, date_col: str,
                          *, where: str = '', min_rows: int = 1,
                          upstream: str = '') -> Optional[str]:
    """返回一句"为什么这一步交不出目标日"；`raw_date` 为空或输入齐备时返回 `None`。

    只读查询，不写任何东西 —— 闸门自己绝不能成为第二个失败源：
    查询异常时**放行**（让作业本身去撞真实计算），并在原因里说明没判成。
    """
    if not raw_date:
        return None
    try:
        day = raw_date if isinstance(raw_date, dt.date) else dt.date.fromisoformat(str(raw_date))
    except ValueError:
        return None                      # 日期本身不合法，argparse 之外再说
    sql = ('select count(*) n from %s where %s = %%s %s' % (table, date_col, where))
    try:
        n = int(engine.scalar(sql, (day.isoformat(),), role='reader') or 0)
    except Exception as exc:             # noqa: BLE001
        print('  （输入完备性没判成：%s —— 交给作业自己判）' % str(exc)[:90])
        return None
    if n >= min_rows:
        return None
    return ('**目标交易日 %s 在 `%s` 里没有数**（%s 行 < %s）：%s还没入库或那天本就空。'
            '本步**不算成功**，也不会退回去算别的日子 —— 那种"rc=0 但交付上一场"'
            '在页面上看不出来。'
            % (day, table, f'{n:,}', min_rows,
               ('上游 `%s` ' % upstream) if upstream else ''))
