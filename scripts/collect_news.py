"""快讯采集运行入口（落 `data_path("news")/news-YYYY-MM-DD.jsonl`）。

    python scripts/collect_news.py --dry-run     # 只打印，不落盘
    python scripts/collect_news.py               # 单轮，落盘 + 推进游标
    python scripts/collect_news.py --loop        # 常驻，30 秒一轮
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duanxian import newswire as nw     # noqa: E402


def _one(cursors: dict, backfill: int) -> dict:
    res = nw.collect_once(cursors, backfill_minutes=backfill)
    print("抓到 %d 条（丢弃 %d 条）| 游标 %s | available=%s%s"
          % (len(res["rows"]), res["dropped"], res["cursors"] or "未推进",
             res["available"], "" if res["available"] else "（" + str(res["reason"]) + "）"))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写入的行，不连库")
    ap.add_argument("--loop", action="store_true", help="常驻 30 秒一轮")
    ap.add_argument("--backfill-minutes", type=int, default=nw.BACKFILL_MINUTES)
    args = ap.parse_args()

    cursors = {} if args.dry_run else nw.read_cursors()

    while True:
        res = _one(cursors, args.backfill_minutes)
        if args.dry_run:
            for r in res["rows"][:5]:
                print("   ", json.dumps({k: str(v) for k, v in r.items()
                                         if k in ("item_id", "published_at", "source", "level",
                                                  "sentiment", "title", "news_type")},
                                        ensure_ascii=False)[:240])
            if res["rows"]:
                print("    （只列前 5 条；实际待写 %d 条）" % len(res["rows"]))
            return 0 if res["available"] else 1
        wr = nw.persist(res)
        print("   落盘：%s" % wr)
        if wr.get("ok"):
            cursors = res["cursors"]         # 只有写成功才在内存里跟着推进
        if not args.loop:
            return 0 if wr.get("ok") else 1
        time.sleep(nw.POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
