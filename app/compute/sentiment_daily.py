# -*- coding: utf-8 -*-
"""市场情绪两档（大盘宽度 / 题材投机）的**唯一判据实现** + 逐日落库。

背景（用户 2026-10-06 点名）：首页那两块原先是每次打开页面现抓 akshare
（`vr/market.py:_sentiment()`，5 分钟内存缓存），**历史一条都没留**，
所以"给我画近 20 场的折线"这件事当时做不出来。这张表把它变成有历史：

    analytics.market_sentiment_daily   （DDL：app/db/sql/24_sentiment_and_sector_flow.sql）

两条轴的 0 位是用户指定的，不是我自己选的：
    大盘宽度：中性 = 0 轴          冰点-2 偏弱-1 中性 0 偏强+1 普涨+2
    题材投机：普通与活跃的正中间 = 0 轴   冰点-1.5 普通-0.5 活跃+0.5 亢奋+1.5

**阈值只住这一份**：`vr/market.py`（页面现算那一格）改成 import 这里的
`breadth_level/theme_level`，不许两边各写一套 if —— 一改就漂移，
页面上"今天偏强"而折线上同一天是"中性"，那种不一致比没图更糟。

两个来源（`source` 列）必须分开看：
  · `legu` = 收盘后现抓乐咕的读数（权威口径，涨停用的是「真实涨停」）；
  · `local` = 由 `analytics.market_breadth` 回算 —— 它的 `limit_up_count` 是**单边判据**
    （09-22 给 70，双边实算 65），与乐咕会差几只。宽度那一档两边几乎一致（涨跌家数是同一批数），
    题材那一档回算值可能偏乐观一档 —— 所以折线上每个点都带着 `source`。

用法：
    python -m app.compute.sentiment_daily --backfill        # 把 market_breadth 里全部场次回算落库
    python -m app.compute.sentiment_daily                   # 只算最近一个已收盘交易日（回算口径）
    python -m app.compute.sentiment_daily --live            # 抓乐咕当天读数入库（收盘后跑）
    python -m app.compute.sentiment_daily --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from app.db import engine

SPEC_VERSION = 'msd-1'

#: 判据阈值。左列 = 名字，右列 = 编码（0 轴的位置见模块文档）。
BREADTH_BANDS = (('冰点', -2.0), ('偏弱', -1.0), ('中性', 0.0), ('偏强', 1.0), ('普涨', 2.0))
THEME_BANDS = (('冰点', -1.5), ('普通', -0.5), ('活跃', 0.5), ('亢奋', 1.5))

UP_MIN = 600          # 上涨家数低于这个数直接判冰点（结构性普跌，比值再高也不算普涨）
RATIO_WEAK = 0.7
RATIO_NEUTRAL = 1.2
RATIO_STRONG = 2.5
ZT_NORMAL = 30
ZT_ACTIVE = 60
ZT_HOT = 100


def breadth_level(up: int | None, down: int | None) -> tuple[str | None, float | None]:
    """大盘宽度：(档位, 编码)。涨跌家数缺任一 → 算不出，交回 `(None, None)`，不猜。"""
    if up is None or down is None:
        return None, None
    up, down = int(up), int(down)
    r = up / max(down, 1)
    if up < UP_MIN:
        name = '冰点'
    elif r < RATIO_WEAK:
        name = '偏弱'
    elif r < RATIO_NEUTRAL:
        name = '中性'
    elif r < RATIO_STRONG:
        name = '偏强'
    else:
        name = '普涨'
    return name, dict(BREADTH_BANDS)[name]


def theme_level(zt_real: int | None) -> tuple[str | None, float | None]:
    """题材投机：真实涨停家数分四档，**0 轴在「普通」与「活跃」中间**。"""
    if zt_real is None:
        return None, None
    z = int(zt_real)
    name = '亢奋' if z >= ZT_HOT else '活跃' if z >= ZT_ACTIVE else '普通' if z >= ZT_NORMAL else '冰点'
    return name, dict(THEME_BANDS)[name]


def row_from_counts(day: dt.date, *, up: Any, down: Any, flat: Any,
                    zt_real: Any, source: str) -> dict:
    b_name, b_score = breadth_level(up, down)
    t_name, t_score = theme_level(zt_real)
    return {'trade_date': day, 'spec_version': SPEC_VERSION,
            'up_count': _i(up), 'down_count': _i(down), 'flat_count': _i(flat),
            'ratio': (None if not _i(up) or not _i(down)
                      else round(_i(up) / max(_i(down), 1), 4)),
            'limit_up_real': _i(zt_real),
            'breadth_level': b_name, 'breadth_score': b_score,
            'theme_level': t_name, 'theme_score': t_score, 'source': source}


def _i(v: Any) -> int | None:
    try:
        return None if v is None else int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 两个来源

def from_breadth(days: list[dt.date] | None = None) -> list[dict]:
    """由 `analytics.market_breadth` 回算（能建历史，但涨停是单边判据，见模块文档）。"""
    sql = """select trade_date, up_count, down_count, limit_up_count
               from analytics.market_breadth"""
    params: tuple = ()
    if days:
        sql += " where trade_date = any(%s)"
        params = (list(days),)
    rows = engine.query(sql + " order by trade_date", params)
    return [row_from_counts(r['trade_date'], up=r['up_count'], down=r['down_count'],
                            flat=None, zt_real=r['limit_up_count'], source='local')
            for r in rows]


def from_legu() -> dict | None:
    """抓乐咕当天读数。**只在收盘后有意义**：它是即时截面，盘中抓到的是半天的数。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(engine.__file__).resolve().parent.parent.parent / 'vr'))
    try:
        import astock
        df = astock._akshare().stock_market_activity_legu()
    except Exception as exc:  # noqa: BLE001  接口坏就是拿不到，交回 None 让调用方保留回算那一档
        print('  乐咕接口没抓到：%s: %s' % (type(exc).__name__, str(exc)[:120]))
        return None
    d = {str(row['item']): row['value'] for _, row in df.iterrows()}
    day = d.get('统计日期')
    if hasattr(day, 'date'):
        day = day.date()
    else:
        try:
            day = dt.date.fromisoformat(str(day)[:10])
        except ValueError:
            from app.tdx_client.readiness import expected_trade_date
            day = expected_trade_date()
    return row_from_counts(day, up=d.get('上涨'), down=d.get('下跌'), flat=d.get('平盘'),
                           zt_real=d.get('真实涨停'), source='legu')


_UPSERT = """
insert into analytics.market_sentiment_daily
 (trade_date, spec_version, up_count, down_count, flat_count, ratio, limit_up_real,
  breadth_level, breadth_score, theme_level, theme_score, source, captured_at)
values (%(trade_date)s,%(spec_version)s,%(up_count)s,%(down_count)s,%(flat_count)s,%(ratio)s,
        %(limit_up_real)s,%(breadth_level)s,%(breadth_score)s,%(theme_level)s,%(theme_score)s,
        %(source)s,now())
on conflict (trade_date, spec_version) do update set
  up_count = coalesce(excluded.up_count, analytics.market_sentiment_daily.up_count),
  down_count = coalesce(excluded.down_count, analytics.market_sentiment_daily.down_count),
  flat_count = coalesce(excluded.flat_count, analytics.market_sentiment_daily.flat_count),
  ratio = coalesce(excluded.ratio, analytics.market_sentiment_daily.ratio),
  limit_up_real = coalesce(excluded.limit_up_real, analytics.market_sentiment_daily.limit_up_real),
  breadth_level = coalesce(excluded.breadth_level, analytics.market_sentiment_daily.breadth_level),
  breadth_score = coalesce(excluded.breadth_score, analytics.market_sentiment_daily.breadth_score),
  theme_level = coalesce(excluded.theme_level, analytics.market_sentiment_daily.theme_level),
  theme_score = coalesce(excluded.theme_score, analytics.market_sentiment_daily.theme_score),
  -- **legu 盖过 local，local 不许盖掉 legu**：回算是补空位的，不是重新判决。
  source = case when analytics.market_sentiment_daily.source = 'legu'
                  and excluded.source = 'local'
                then analytics.market_sentiment_daily.source else excluded.source end,
  captured_at = now()
"""


def write(rows: list[dict]) -> int:
    good = [r for r in rows if r['breadth_level'] or r['theme_level']]
    if good:
        engine.executemany(_UPSERT, good, role='etl')
    return len(good)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='市场情绪两档逐日落库（判据与 0 轴见模块文档）')
    ap.add_argument('--backfill', action='store_true', help='把 market_breadth 里所有场次回算入库')
    ap.add_argument('--live', action='store_true', help='抓乐咕当天读数入库（收盘后跑）')
    ap.add_argument('--date', help='只算这一场（回算口径）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    if args.live:
        r = from_legu()
        if r is None:
            print('乐咕没抓到 —— 这一场留回算值（source=local）或空着，不编数')
            return 1
        print('[%s] legu 读数：涨 %s / 跌 %s → %s；真实涨停 %s → %s'
              % (r['trade_date'], r['up_count'], r['down_count'], r['breadth_level'],
                 r['limit_up_real'], r['theme_level']))
        if args.dry_run:
            return 0
        return 0 if write([r]) else 1

    days = [dt.date.fromisoformat(args.date)] if args.date else None
    if not args.backfill and not args.date:
        latest = engine.scalar("select max(trade_date) from analytics.market_breadth")
        days = [latest] if latest else []
    rows = from_breadth(days)
    print('回算 %d 场（market_breadth 里有数的场次）' % len(rows))
    for r in rows[-3:]:
        print('  %s 宽度 %s(%s) / 题材 %s(%s) 涨停 %s'
              % (r['trade_date'], r['breadth_level'], r['breadth_score'],
                 r['theme_level'], r['theme_score'], r['limit_up_real']))
    if args.dry_run:
        print('DONE（--dry-run 未写库）')
        return 0
    print('写入 %d 行' % write(rows))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
