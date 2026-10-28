# -*- coding: utf-8 -*-
"""heat_report.py — 交易日 10:05 / 10:55 / 14:18 涨跌停概念热度 → 盯盘群

口径（2026-10-19 牛哥定义）：
  取当前涨停池+跌停池个股 → PG 概念字典对照 → 概念出现次数从高到低，
  **只播出现次数 >= 3 的概念**；涨停、跌停两组分开计数分开播。

数据源：东财 push2ex 涨停/跌停池（复用 zt_watcher）＋ PG 概念字典（复用 auction_report）。
交易日历 PG 判日，节假日静默；池子取不到发"未就绪"（宁缺毋假），不猜。
模板 templates.json（heat_* 键），参数 auction_config.json（heat_min_n / heat_top）。

用法：python heat_report.py --dry-run   （计划任务由 Guanlan-HeatReport 三触发器唤起）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import auction_report as ar          # sector_counts / load_cfg / dsn_reader
import zt_watcher as zw              # fetch_pool / load_channel / send_feishu / log


def sector_hits(rows: list[dict], cfg: dict, tpl: dict) -> str:
    """池子原始行 → 概念计数行文本（过滤 n>=heat_min_n）。"""
    min_n = int(cfg.get("heat_min_n", 3))
    codes = [str(r.get("c", "")) for r in rows if r.get("c")]
    if not codes:
        return "无"
    hits, _unmapped = ar.sector_counts(codes, {**cfg, "sector_top": int(cfg.get("heat_top", 25))})
    keep = [(n_, c) for n_, c in hits if c >= min_n]
    if not keep:
        return ""
    return "、".join(tpl["auction_sector_row"].format(name=n_, n=c) for n_, c in keep)


def run_once(cfg: dict, tpl: dict, dry: bool) -> int:
    now = datetime.now()
    t = now.strftime("%H:%M:%S")
    ymd = now.strftime("%Y%m%d")
    min_n = int(cfg.get("heat_min_n", 3))
    try:
        zt = zw.fetch_pool("getTopicZTPool", ymd)
        dt = zw.fetch_pool("getTopicDTPool", ymd, sort="fund:asc")
    except Exception as exc:
        text = tpl["heat_fail_notice"].format(t=t, why=f"池子取不到 {type(exc).__name__}")
        zw.log({"what": "heat_stale", "err": str(exc)[:200]})
        if dry:
            print("[dry-run]", text)
            return 1
        zw.broadcast(cfg, text)
        return 1

    try:
        up_s, dn_s = sector_hits(zt, cfg, tpl), sector_hits(dt, cfg, tpl)
    except Exception as exc:
        up_s = dn_s = ""
        zw.log({"what": "heat_pg_fail", "err": str(exc)[:200]})
        pg_err = f"\nPG概念查询失败：{type(exc).__name__}（宁缺毋假，本条无计数）"
    else:
        pg_err = ""

    lines = [tpl["heat_header"].format(t=t, zt_n=len(zt), dt_n=len(dt))]
    lines.append(tpl["heat_up"].format(rows=up_s) if up_s
                 else tpl["heat_up_none"].format(min_n=min_n))
    lines.append(tpl["heat_down"].format(rows=dn_s) if dn_s
                 else tpl["heat_down_none"].format(min_n=min_n, dt_n=len(dt)))
    lines.append(tpl["heat_footer"].format(t=t))
    if pg_err:
        lines.append(pg_err.strip())
    text = "\n".join(lines)

    zw.log({"what": "heat", "zt_n": len(zt), "dt_n": len(dt), "text": text})
    if dry:
        print("[dry-run] 将发送：\n" + text)
        return 0
    zw.broadcast(cfg, text)
    print(f"[{t}] 概念热度已推送（涨停{len(zt)}/跌停{len(dt)}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="跳过交易日检查")
    args = ap.parse_args()

    cfg, tpl = ar.load_cfg(), ar.load_tpl()
    if not args.force:
        try:
            import psycopg
            with psycopg.connect(ar.dsn_reader(), connect_timeout=8) as conn, conn.cursor() as cur:
                if not ar.is_trade_day(cur):
                    return 0                    # 非交易日：静默
        except Exception as exc:
            if "connection" in str(exc).lower() and date.today().weekday() >= 5:
                return 0                        # 日历查不到 + 周末 → 不打扰
    try:
        return run_once(cfg, tpl, args.dry_run)
    except Exception as exc:                    # 意外挂掉也要吭一声
        zw.log({"what": "heat_crash", "err": f"{type(exc).__name__}: {exc}"[:300]})
        try:
            zw.send_feishu(*zw.load_channel(cfg["channel"]),
                           f"【涨跌停概念热度 {datetime.now():%H:%M:%S}】任务异常退出：{type(exc).__name__}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    sys.exit(main())
