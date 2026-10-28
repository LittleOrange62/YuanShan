# -*- coding: utf-8 -*-
"""`minute_window` 的快速路径必须和全量解析给出一样的数据。

这条链每天要入 270 万根分钟线，而 `--date` 只取一天：走全量解析就是
O(整个 vipdoc)（实测 400 秒还没跑完）。反向定位/二分把这些省掉，但**省时间的
前提是不许少数据** —— 少一根都没人看得见，只会表现为"日内走势缺一段"。

所以这里的判据不是"跑通了"，而是**和 `parse_minute` 逐行相等**，
外加两条必须交回 `None`（= 交给调用方回退全量）的畸形文件。
"""
from __future__ import annotations

import datetime as dt

from app.ingest import tdx_binary as tb


def _rec(date_idx: int, minute: int, price: float = 10.0) -> bytes:
    return tb.MIN_STRUCT.pack(date_idx, minute, price, price, price, price,
                              price * 1000, 1000, 0)


def _idx(d: dt.date) -> int:
    return tb._min_key(d)


def _write(tmp_path, name: str, body: bytes):
    p = tmp_path / 'sh' / 'minline'
    p.mkdir(parents=True, exist_ok=True)
    f = p / name
    f.write_bytes(body)
    return f


def _days(d0: dt.date, n: int, per_day: int = 240):
    """造 n 个连续交易日的升序记录（每天 per_day 根）。"""
    recs = []
    cur = d0
    for _ in range(n):
        for m in range(per_day):
            recs.append(_rec(_idx(cur), 9 * 60 + 30 + m))
        cur += dt.timedelta(days=1)
    return recs, cur


def test_window_equals_full_parse_single_day(tmp_path):
    d0 = dt.date(2026, 9, 20)
    recs, _ = _days(d0, 6)
    path = _write(tmp_path, 'sh600000.lc1', b''.join(recs))
    code = '600000.SH'
    for day in (d0, dt.date(2026, 9, 22), dt.date(2026, 9, 25)):
        fast = tb.minute_window(path, day, day, security_code=code)
        slow = [b for b in tb.parse_minute(path, security_code=code) if b.dt.date() == day]
        assert fast is not None, '布局认识的正常文件不该回退'
        assert [b.dt for b in fast] == [b.dt for b in slow]
        assert [(b.open, b.high, b.low, b.close, b.vol, b.amount) for b in fast] == \
               [(b.open, b.high, b.low, b.close, b.vol, b.amount) for b in slow]
        assert len(fast) == 240


def test_open_right_bound_covers_rest(tmp_path):
    d0 = dt.date(2026, 9, 20)
    recs, _ = _days(d0, 6)
    path = _write(tmp_path, 'sh600001.lc1', b''.join(recs))
    fast = tb.minute_window(path, dt.date(2026, 9, 23), None, security_code='600001.SH')
    # until=None 是"没有上界"，不是"等于 since" —— 写错会静默把窗口截成一天
    assert len(fast) == 3 * 240


def test_file_entirely_before_window_skipped_wholesale(tmp_path):
    recs, _ = _days(dt.date(2026, 8, 1), 5)
    path = _write(tmp_path, 'sh600002.lc1', b''.join(recs))
    assert tb.minute_window(path, dt.date(2026, 9, 23), dt.date(2026, 9, 23)) == []


def test_file_entirely_after_window(tmp_path):
    recs, _ = _days(dt.date(2026, 9, 24), 3)
    path = _write(tmp_path, 'sh600003.lc1', b''.join(recs))
    assert tb.minute_window(path, dt.date(2026, 9, 20), dt.date(2026, 9, 23)) == []


def test_misaligned_length_falls_back(tmp_path):
    recs, _ = _days(dt.date(2026, 9, 20), 2)
    path = _write(tmp_path, 'sh600004.lc1', b''.join(recs) + b'\x00' * 7)
    assert tb.minute_window(path, dt.date(2026, 9, 20), security_code='600004.SH') is None


def test_out_of_order_at_seam_falls_back(tmp_path):
    """乱序补写过的文件不许走快速路径 —— 二分的前提就是升序。"""
    recs, _ = _days(dt.date(2026, 9, 20), 5)
    swapped = list(recs)
    swapped[240], swapped[239] = swapped[239], swapped[240]      # 跨天边界倒挂
    path = _write(tmp_path, 'sh600005.lc1', b''.join(swapped))
    assert tb.minute_window(path, dt.date(2026, 9, 21), dt.date(2026, 9, 22),
                            security_code='600005.SH') is None


def test_empty_file_is_not_a_failure(tmp_path):
    path = _write(tmp_path, 'sh600006.lc1', b'')
    assert tb.minute_window(path, dt.date(2026, 9, 20)) == []


def test_min_key_orders_like_calendar():
    """二分的正确性全靠这条：`date_idx` 的比较必须和日历同序，跨年也一样。"""
    seq = [dt.date(2025, 12, 30), dt.date(2026, 1, 2), dt.date(2026, 9, 23),
           dt.date(2027, 1, 1), dt.date(2044, 12, 31)]
    keys = [tb._min_key(d) for d in seq]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
