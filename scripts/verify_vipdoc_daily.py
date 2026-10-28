"""只读对账：YuanShan 自己解析 vipdoc `.day` vs 库里 `market_data.stock_daily`。

为什么先做这个而不是先写：要把 FinanceTool 那批入库脚本搬进 YuanShan，前提是
**本侧解析出的每一条都和库里对得上**。对不上就接管写库，等于拿用户唯一的真资产
（`financetool` 库，9.7GB，无调度备份）做实验。所以顺序是：先对账 → 差异归零 → 再写。

    python scripts/verify_vipdoc_daily.py --since 2026-09-01 [--markets sh sz bj]

只 SELECT，不写任何东西。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.tdx_binary import DAY_STRUCT, MARKET_BY_DIR, ts_code_of     # noqa: E402

VIPDOC = Path(os.environ.get("FT_VIPDOC", r"D:\new_tdx64\vipdoc"))
TAIL_RECORDS = 40          # 只看每个文件尾部 40 条：对账要的是近期，不是全历史重放


def tail_bars(path: Path, since: dt.date):
    size = path.stat().st_size
    n = size // DAY_STRUCT.size
    start = max(0, n - TAIL_RECORDS)
    with open(path, "rb") as fh:
        fh.seek(start * DAY_STRUCT.size)
        blob = fh.read()
    code = ts_code_of(path)
    if not code:
        return
    for rec in struct.iter_unpack(DAY_STRUCT.format, blob):
        packed = rec[0]
        year, rest = divmod(packed, 10000)
        month, day = divmod(rest, 100)
        try:
            d = dt.date(year, month, day)
        except ValueError:
            continue
        if d < since:
            continue
        # 通达信 .day 里价格是 0.01 元整数，成交量单位与库里一致（股/份）—— 与 FT 同口径
        yield {"security_code": code, "trade_date": d,
               "open": rec[1] / 100.0, "high": rec[2] / 100.0,
               "low": rec[3] / 100.0, "close": rec[4] / 100.0,
               "volume": rec[6], "amount": float(rec[5])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-01")
    ap.add_argument("--markets", nargs="*", default=["sh", "sz", "bj"])
    ap.add_argument("--limit", type=int, default=0, help="只核对前 N 个文件（冒烟用）")
    args = ap.parse_args()
    since = dt.date.fromisoformat(args.since)

    from duanxian import pg
    if not pg.enabled():
        print("✗ 库连不上（VIBE_PG / PG_USER / PG_PASSWORD）")
        return 1

    files = []
    for mk in args.markets:
        d = VIPDOC / mk / "lday"
        if not d.is_dir():
            print("✗ 目录不存在：%s" % d)
            return 1
        files.extend(sorted(d.glob("*.day")))
    if args.limit:
        files = files[:args.limit]
    print("待核对文件：%d 个（%s），起点 %s" % (len(files), ",".join(args.markets), since))

    parsed, last_date = [], None
    for p in files:
        for bar in tail_bars(p, since):
            parsed.append(bar)
            if last_date is None or bar["trade_date"] > last_date:
                last_date = bar["trade_date"]
    print("解析出 %d 条（本地文件最新数据日期 %s）" % (len(parsed), last_date))

    by_day = Counter(b["trade_date"].isoformat() for b in parsed)
    days = sorted(by_day)
    print("本地文件覆盖的交易日：", "、".join("%s(%d)" % (d, by_day[d]) for d in days[-8:]))

    # 与库比对：一次把窗口内所有行拉回来，内存里对，避免 9500 次查询
    df = pg.fetch_df(
        "select security_code, trade_date::text as d, open, high, low, close, "
        "volume_shares as volume, amount_yuan as amount "
        "from market_data.stock_daily where trade_date >= %s", (str(since),))
    if df is None:
        print("✗ 库里窗口查询失败")
        return 1
    db = {(r["security_code"], r["d"]): r for r in df.to_dict("records")}
    print("库里同期 %d 行" % len(db))

    mismatch, missing_in_db, missing_locally, ok = [], [], set(), 0
    fields = ("open", "high", "low", "close", "volume", "amount")
    for b in parsed:
        key = (b["security_code"], b["trade_date"].isoformat())
        r = db.get(key)
        if r is None:
            missing_in_db.append(key)
            continue
        bad = [f for f in fields
               if abs(float(r[f] or 0) - float(b[f] or 0)) > (0.005 if f != "volume" else 0)]
        if bad:
            mismatch.append((key, bad, r, b))
        else:
            ok += 1
    parsed_keys = {(b["security_code"], b["trade_date"].isoformat()) for b in parsed}
    for key in db:
        if key[1] <= (last_date.isoformat() if last_date else ""):
            missing_locally.add(key)

    print("\n=== 对账结果 ===")
    print("完全一致      : %d" % ok)
    print("字段不一致    : %d" % len(mismatch))
    print("本地有库里没有: %d %s" % (len(missing_in_db), sorted(missing_in_db)[:6]))
    print("库里有本地没有: %d（多为历史回补/别的质量源，不代表错）" % len(missing_locally))
    for key, bad, r, b in mismatch[:8]:
        print("  %s 差异字段 %s 库=%s 本地=%s"
              % (key, bad, {f: r[f] for f in bad}, {f: b[f] for f in bad}))
    worst = Counter()
    for _, bad, _, _ in mismatch:
        worst.update(bad)
    if worst:
        print("  差异字段分布：", dict(worst))
    verdict = (len(mismatch) == 0)
    print("\n判定：本地解析 %s 库内既有数据 —— %s"
          % ("与" if verdict else "与 **不一致**（差 %d 条）" % len(mismatch),
             "可以谈接管写库" if verdict else "**不许**接管写库，先归因"))
    return 0 if verdict else 2


if __name__ == "__main__":
    raise SystemExit(main())
