# -*- coding: utf-8 -*-
"""C21 · 个股主力资金流入库（新浪通道）→ `market_data.fund_flow_rank`。

## 为什么换源

旧通道 `c11_fundflow.py` 走东财 `push2`，本机实测**约每 5~6 个请求就 `RemoteDisconnected`**，
全市场约 56 页根本拉不完 —— 这就是 `fund_flow_rank` 至今 **0 行** 的真根因（不是入库脚本漏跑）。
新浪 `MoneyFlow.ssl_bkzj_ssggzj` 实测：**61 页 / 0 失败 / 35 秒 / 5080 个唯一代码**，
字段够用（`r0_net` 主力净额、`r0_ratio` 主力净占比、`netamount` 总净流入、`changeratio`、`amount`）。

按用户 2026-09-25 裁定：**不稳定源不做重复获取，取到就入库**，所以本脚本单源、串行、一次跑完。

## 三条要命的口径（都写在返回值里，不藏在注释里）

1. **响应没有日期字段** —— 只有即时截面。归属交易日一律由 `reference_data.trade_calendar`
   算（`is_open` 且 ≤ 今天的最大日）。**非交易日跑 = 拒绝写**：那天拿到的大概率是上一场的数，
   而报文里没有任何东西能证明是哪一天 —— 写进去就是把"未知日期"伪装成"某天的数据"。
2. **榜内混着非股票** —— 实测 5080 个代码里有约 620 只是债券 ETF/货基/B 股，
   **主力净流入第一名是"公司债ETF平安" 107.8 亿元**。一律以主档 `asset_type='stock'` 为准过滤，
   主档没有的代码不写（不猜品种），数量交在 `dropped_not_in_master`。
3. **覆盖不足就是不足，不补 0** —— 榜内股票数 < 主档个股数时，缺的那些**不写行**，
   `coverage_pct` 如实交出（NULL ≠ 0 是本项目铁律；补 0 等于宣称"当天没有主力净流入"）。

## 失败处理

任一页失败即视为**本轮不完整**：默认不写库（半份榜单看着像全市场，比空表更坏），
只在 `--allow-partial` 时写已成功的部分并在 state 里标 `partial=true`。

用法：
    python -m ingest.jobs.c21_fundflow_sina --dry-run          # 只取数+过滤+报数，不写
    python -m ingest.jobs.c21_fundflow_sina                    # 交易日收盘后正式入库
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PAGE_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "MoneyFlow.ssl_bkzj_ssggzj?page=%d&num=%d&sort=netamount&asc=0&bankuai=&shichang=")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SCOPE = "stock"
WINDOW_DAYS = 1
STATE_FILE = "fundflow_state.json"

UPSERT = """
insert into market_data.fund_flow_rank
  (trade_date, scope, object_code, object_name, window_days,
   main_net, main_net_percent, pct_change, source_batch_id, updated_at)
values (%(trade_date)s, %(scope)s, %(object_code)s, %(object_name)s, %(window_days)s,
        %(main_net)s, %(main_net_percent)s, %(pct_change)s, %(batch_id)s, now())
on conflict (trade_date, scope, object_code, window_days) do update set
  main_net = excluded.main_net, main_net_percent = excluded.main_net_percent,
  pct_change = excluded.pct_change, object_name = excluded.object_name,
  source_batch_id = excluded.source_batch_id, updated_at = now()
"""


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def fetch_ranking(page_size: int = 100, sleep: float = 0.4, max_pages: int = 80):
    """串行翻页 + 每页间歇。返回 (rows, pages_ok, pages_fail, seconds)。

    不并发：新浪这个口没测过并发上限，而本项目对 push2 的教训就是"并发/连打必被频率掐"。
    """
    t0 = time.time()
    rows, ok, fail = [], 0, 0
    for page in range(1, max_pages + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    PAGE_URL % (page, page_size), headers=UA), timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            if not isinstance(payload, list):
                fail += 1
                break
            rows.extend(payload)
            ok += 1
            if len(payload) < page_size:
                break                      # 短页 = 到底了
        except Exception as exc:           # noqa: BLE001  单页失败要计数，不许静默 continue
            fail += 1
            print("  第 %d 页失败：%s: %s" % (page, type(exc).__name__, str(exc)[:80]))
        time.sleep(sleep)
    return rows, ok, fail, round(time.time() - t0, 1)


def latest_open_date(conn) -> str | None:
    with conn.cursor() as cur:
        cur.execute("""select max(calendar_date)::text from reference_data.trade_calendar
                       where is_open and calendar_date <= current_date""")
        return cur.fetchone()[0]


def today_is_open(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("select is_open from reference_data.trade_calendar where calendar_date = current_date")
        r = cur.fetchone()
        return bool(r and r[0])


def master_stock_names(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("select security_code, name from reference_data.security where asset_type = 'stock'")
        return dict(cur.fetchall())


def to_rows(items, names):
    """报文 → 待写行。主档没有的代码不写；`name` 字段以主档为准（报文的 name 实测有脏值）。"""
    out, dropped = [], {"不在主档(不猜品种)": 0, "字段坏": 0}
    for it in items:
        sym = str(it.get("symbol") or "")
        if len(sym) != 8 or not sym[:2].isalpha() or not sym[2:].isdigit():
            dropped["字段坏"] += 1
            continue
        code = "%s.%s" % (sym[2:], sym[:2].upper())
        if code not in names:
            dropped["不在主档(不猜品种)"] += 1
            continue
        main_net = _num(it.get("r0_net"))
        if main_net is None:
            dropped["字段坏"] += 1
            continue
        ratio = _num(it.get("r0_ratio"))
        chg = _num(it.get("changeratio"))
        out.append({"object_code": code, "object_name": names[code],
                    "main_net": round(main_net / 1e8, 4),          # 亿元
                    "main_net_percent": round(ratio, 2) if ratio is not None else None,   # 百分数
                    "pct_change": round(chg * 100, 2) if chg is not None else None})      # 百分数
    return out, dropped


def write_batch(conn, rows, trade_date, count):
    with conn.cursor() as cur:
        cur.execute("""insert into operations.ingest_batch
                       (source, source_path, target, row_count, actor, status, started_at, finished_at, message)
                       values ('sina_moneyflow', 'vip.stock.finance.sina.com.cn/MoneyFlow.ssl_bkzj_ssggzj',
                               'market_data.fund_flow_rank', %s, 'ft_etl', 'running', now(), now(),
                               '主力资金流：归属交易日由日历判定（响应无日期字段）')
                       returning batch_id""", (count,))
        batch_id = cur.fetchone()[0]
        n = 0
        for r in rows:
            cur.execute(UPSERT, {**r, "trade_date": trade_date, "scope": SCOPE,
                                 "window_days": WINDOW_DAYS, "batch_id": batch_id})
            n += cur.rowcount
        cur.execute("update operations.ingest_batch set status='ok', row_count=%s, finished_at=now() "
                    "where batch_id=%s", (n, batch_id))
    return batch_id, n


def read_state() -> dict:
    """上一次真正落库的凭据。用来做"同一天不重采"的判定，见 `already_written()`。"""
    from duanxian.paths import data_path
    p = Path(data_path("ingest")) / STATE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def already_written(trade_date: str) -> dict | None:
    """这一天是否已经写过。**这张表是"当天不采就永久没有"的那一类**，
    而它现在有两个可能的触发点（收盘链 + 常驻线程 20:50）—— 两个入口都必须
    先看这里，否则同一天会被写两次：新浪这个口是**即时截面**，盘中/盘后两次的值
    不一样，第二次覆盖第一次，事后只看得到"今天有数"，看不出数是哪一刻的。
    """
    st = read_state()
    if st.get("trade_date") == trade_date and (st.get("written") or 0) > 0:
        return st
    return None


def save_state(state: dict) -> None:
    from duanxian.paths import data_path
    d = Path(data_path("ingest"))
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / STATE_FILE)


def check_date_arg(conn, want: str | None, default_day: str, open_today: bool) -> str:
    """`--date` 只允许两个值：今天（当天在盘中/盘后重试）或最近一个已收盘交易日（跨零点补跑）。

    为什么不给它任意历史日期：新浪这个口**报文里没有日期**，它永远只给"当下这一份截面"。
    传 `--date 2026-08-01` 不会取回 8 月 1 日的资金流，只会把**今天**的数字盖上 8 月 1 日的
    归属日 —— 那是造数据，比空表坏得多。所以这里宁可拒收，并说清楚为什么。
    """
    if want is None:
        return default_day
    if want in (default_day, str(dt.date.today())):
        return want
    raise SystemExit(
        "✗ --date 只接受 %s（最近一个已收盘交易日）或 %s（今天）；"
        "新浪这个口没有历史查询能力，给更早的日期只会把今天的截面冒充那天。\n"
        "  真要历史序列，走 `c11` 的大盘 daykline（那个报文里有日期），别用这张表。"
        % (default_day, dt.date.today()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只取数与报数，一行都不写")
    ap.add_argument("--allow-partial", action="store_true", help="有页失败时也写（state 里标 partial）")
    ap.add_argument("--force", action="store_true", help="跳过非交易日闸（仅调试，写出的日期归属不可信）")
    ap.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                    help="归属日；只接受『最近一个已收盘交易日』或今天（当天重试用）。"
                         "这个口无历史查询能力，更早的日期会被拒收")
    ap.add_argument("--rerun", action="store_true",
                    help="明知今天已写过还要重采（会覆盖当天那一次，state 里留下 rerun 痕迹）")
    ap.add_argument("--probe-write", action="store_true",
                    help="真跑一遍 upsert 与批次登记然后 **回滚**：验证 SQL/主键/权限通了，但不留数据")
    args = ap.parse_args()

    import psycopg2
    from duanxian import pg as _pg
    _pg._load_env_once()          # 全项目只有 pg.py 那一个 .env 读取口
    os.environ.setdefault("PG_HOST", "127.0.0.1")
    if not (os.environ.get("PG_ETL_USER") and os.environ.get("PG_ETL_PASSWORD")):
        print("✗ .env 里缺 PG_ETL_USER / PG_ETL_PASSWORD：写库要 ft_etl，只读账号写不了")
        return 1
    conn = psycopg2.connect(host=os.environ.get("PG_HOST", "127.0.0.1"),
                            dbname=os.environ.get("PG_DB", "financetool"),
                            user=os.environ["PG_ETL_USER"], password=os.environ["PG_ETL_PASSWORD"],
                            connect_timeout=5, application_name="ys-c21-fundflow")
    try:
        trade_date = latest_open_date(conn)
        open_today = today_is_open(conn)
        print("归属交易日（响应无日期字段，一律由日历判定）：today is_open=%s → %s"
              % (open_today, trade_date))
        if not open_today and not args.force:
            print("✗ 今天不是交易日：新浪这个口只给即时截面、报文里没有日期，"
                  "此刻抓到的极可能是上一场的数却无法证明。拒绝写。要调试加 --force。")
            return 3
        # --date 只做"归属日重试"，不做时间旅行（原因见 check_date_arg）
        trade_date = check_date_arg(conn, args.date, trade_date, open_today)
        if args.date:
            print("→ 归属日按 --date 指定：%s" % trade_date)
        # 两个触发点（收盘链 + 常驻 20:50 线程）共写一张表：谁先到谁算，后来者跳过。
        # 不能"每天都写一次"：这个口是即时截面，晚一发的值不一样，覆盖掉的就是
        # 一个没人记得的时刻 —— 与自选成员快照同一类坑，只是这里更隐蔽。
        if not (args.rerun or args.dry_run or args.probe_write):
            done = already_written(trade_date)
            if done:
                print("→ %s 已经采过（%s 行，ran_at=%s），本次跳过；确实要重采加 --rerun "
                      "（会用当下这份即时截面盖掉那一次）"
                      % (trade_date, done.get("written"), done.get("ran_at")))
                return 0

        items, ok, fail, secs = fetch_ranking()
        uniq = {str(i.get("symbol")) for i in items}
        names = master_stock_names(conn)
        rows, dropped = to_rows(items, names)
        print("取数：%d 页成功 / %d 页失败，%.1f 秒；原始 %d 条，唯一代码 %d 个"
              % (ok, fail, secs, len(items), len(uniq)))
        print("过滤：主档个股 %d 只 → 可写 %d 条；丢弃 %s" % (len(names), len(rows), dropped))
        coverage = round(100.0 * len(rows) / max(len(names), 1), 1)
        print("覆盖率 %s%%（主档个股 %d 只，榜内只覆盖 %d 只 —— 缺的不写，不补 0）"
              % (coverage, len(names), len(rows)))

        partial = fail > 0
        state = {"ran_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "trade_date": trade_date, "pages_ok": ok, "pages_fail": fail,
                 "seconds": secs, "raw_rows": len(items), "unique_codes": len(uniq),
                 "writable": len(rows), "master_stocks": len(names), "coverage_pct": coverage,
                 "dropped": dropped, "partial": partial, "dry_run": args.dry_run,
                 "written": 0, "batch_id": None,
                 "unit": "main_net 亿元；main_net_percent 与 pct_change 百分数"}

        if args.probe_write:
            batch_id, n = write_batch(conn, rows, trade_date, len(rows))
            conn.rollback()                    # 验完就撤：休市日写进去就是拿不确定日期冒充某天
            state.update({"probed": True, "would_write": n, "rolled_back": True, "batch_id": batch_id})
            save_state(state)
            print("✅ 写路径已验证：%d 行 upsert + 批次登记都过了，随后 **已回滚**（未留任何数据）。"
                  "首个正式入库窗口 = 下一个交易日收盘后。batch_id 探针用掉=%s" % (n, batch_id))
            return 0

        if args.dry_run:
            print("→ dry-run：未写库")
            save_state(state)
            return 0
        if partial and not args.allow_partial:
            print("✗ 有页失败，默认不写（半份榜单看着像全市场，比空表更坏）。"
                  "确认要写加 --allow-partial。")
            state["reason"] = "部分失败，未写"
            save_state(state)
            return 2

        batch_id, n = write_batch(conn, rows, trade_date, len(rows))
        conn.commit()
        state.update({"written": n, "batch_id": batch_id})
        save_state(state)
        print("✅ 写入 %d 行 → market_data.fund_flow_rank（trade_date=%s, scope=%s, window_days=%d, batch=%s）"
              % (n, trade_date, SCOPE, WINDOW_DAYS, batch_id))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
