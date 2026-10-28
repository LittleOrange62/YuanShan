# -*- coding: utf-8 -*-
"""auction_report.py — 交易日 09:25:30 竞价榜 → 飞书盯盘群

口径（2026-10-19 牛哥定义）：
  集合竞价涨幅 > 5%（9:25:00 撮合价=当日开盘价，对昨收的涨幅）按从高到低发送；
  汇总这批个股背后的【概念】出现次数，按从多到少排序；
  板块对照 = PG financetool `reference_data.sector_member` + `sector_info`（牛哥指定）。

数据源：
  行情  东财 push2 clist 全A按涨幅降序取头部（一次请求，非轮询，避开 push2 风控窗口）
  板块  PG 只读（FT_DSN_READER，仅 SELECT；牛哥 2026-10-19 明示"根据pg本地数据库对照"，
        定时脚本走只读直连，交互查询仍归 007）
  日历  PG reference_data.trade_calendar.is_open —— 非交易日整轮静默跳过

泛标签排除：成员数 > max_members(默认1500) 的概念不参与计数（2026-10-19 定标：
  融资融券3872/深股通1887/沪股通1645/国企改革1473 —— 都是通道标签不是题材；
  机器人概念1247/AI1094 等真热点保留）。

纪律：宁缺毋假 —— 行情不新鲜就重取，始终不新鲜就发"未就绪"，绝不拿旧价冒充竞价价；
  不打分不预测不给买卖建议。所有模板在 templates.json，参数在 auction_config.json。

用法：
  python auction_report.py --dry-run            # 立跑一轮（盘中跑的是实时涨幅，会标注）
  python auction_report.py --wake-at 09:25:30   # 计划任务用：等到点再跑
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from zt_watcher import load_channel, send_feishu, broadcast, log, VAR_DIR, fetch_pool, normalize, money  # 共用发送/日志/池子

CFG_FILE = HERE / "auction_config.json"
TPL_FILE = HERE / "templates.json"
ENV_FILE = Path(r"D:\finance\YuanShan\.env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

DEFAULTS = {
    "min_pct": 5.0,
    "top_n": 60,             # 个股榜最多列这么多，多余只报数
    "sector_top": 10,        # 概念热度取前 N
    "max_members": 1500,     # 泛标签阈值（定标见文件头）
    "channel": "feishu-main",
    "page_size": 100,
    "max_pages": 3,
    "fresh_min_movers": 30,  # 市场级新鲜判据：|涨幅|>=0.05% 的家数下限
    "retry": 3,
    "retry_wait_s": 15,
}


def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    if CFG_FILE.is_file():
        cfg.update(json.loads(CFG_FILE.read_text(encoding="utf-8")))
    return cfg


def load_tpl() -> dict:
    return json.loads(TPL_FILE.read_text(encoding="utf-8"))


def dsn_reader() -> str:
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^FT_DSN_READER=(.*)$", line.strip())
        if m:
            return m.group(1).strip().strip('"')
    raise RuntimeError(".env 里没有 FT_DSN_READER")


# ---------------------------------------------------------------- 交易日历
def is_trade_day(cur) -> bool:
    cur.execute("select is_open from reference_data.trade_calendar where calendar_date = current_date")
    row = cur.fetchone()
    if row is None:                       # 库里没有今天的日历：宁缺毋假，保守按工作日判断
        return date.today().weekday() < 5
    return bool(row[0])


# ---------------------------------------------------------------- 行情
def fetch_board_em(cfg: dict, asc: bool = False) -> list[dict]:
    """东财 push2 全A按涨幅排序取头部（主源）。asc=True 取跌幅榜（最负在前）。"""
    out: list[dict] = []
    for pn in range(1, cfg["max_pages"] + 1):
        q = urllib.parse.urlencode({
            "pn": pn, "pz": cfg["page_size"], "po": 0 if asc else 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f3,f6,f12,f14",
        })
        req = urllib.request.Request(
            "https://push2.eastmoney.com/api/qt/clist/get?" + q,
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        diff = ((data.get("data") or {}).get("diff")) or []
        for row in diff:
            pct = row.get("f3")
            if not isinstance(pct, (int, float)):
                continue                    # 停牌/无竞价 '-'
            out.append({"code": str(row.get("f12", "")), "name": row.get("f14", ""),
                        "pct": float(pct), "amount": row.get("f6") if isinstance(row.get("f6"), (int, float)) else None})
        tail = diff[-1].get("f3") if diff else None
        if len(diff) < cfg["page_size"] or (isinstance(tail, (int, float)) and
                                            (tail > -cfg["min_pct"] if asc else tail < cfg["min_pct"])):
            break                            # 到尾或已探过 min_pct 阈值
        time.sleep(2)                        # push2 风控：串行+喘息
    return out


def fetch_board_sina(cfg: dict, asc: bool = False) -> list[dict]:
    """新浪全A涨幅榜（备源）。asc=True 取跌幅榜。"""
    out: list[dict] = []
    for page in range(1, cfg["max_pages"] + 1):
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData?page=%d&num=%d&sort=changepercent&asc=%d&node=hs_a"
               % (page, cfg["page_size"], 1 if asc else 0))
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://vip.stock.finance.sina.com.cn/"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("gbk", "replace").strip()
        if not raw or raw in ("null", "[]"):
            break
        data = json.loads(re.sub(r'([{,])(\w+):', r'\1"\2":', raw))   # 新浪的 key 不带引号
        for row in data:
            try:
                pct = float(row.get("changepercent", ""))
            except (TypeError, ValueError):
                continue
            code = str(row.get("symbol", ""))[2:]
            out.append({"code": code, "name": row.get("name", ""), "pct": pct, "amount": None})
        last = data[-1].get("changepercent") if data else None
        if len(data) < cfg["page_size"] or (last is not None and (
                float(last) > -cfg["min_pct"] if asc else float(last) < cfg["min_pct"])):
            break
        time.sleep(1)
    return out


def fetch_gainers(cfg: dict, asc: bool = False) -> list[dict]:
    """主源东财；失败自动切新浪备源。都挂就抛错，由 run_once 的重试/未就绪逻辑兜底。"""
    errors = []
    for src, fn in (("东财push2", fetch_board_em), ("新浪榜", fetch_board_sina)):
        try:
            rows = fn(cfg, asc=asc)
            if rows:
                rows.sort(key=lambda x: x["pct"] if asc else -x["pct"])
                if src != "东财push2":
                    log({"what": "auction_source_fallback", "used": src, "asc": asc})
                return rows
            errors.append(f"{src}:空")
        except Exception as exc:
            errors.append(f"{src}:{type(exc).__name__}")
    raise RuntimeError("；".join(errors))


# ---------------------------------------------------------------- 板块
def sector_counts(codes: list[str], cfg: dict) -> tuple[list[tuple], int]:
    """返回 (概念计数[(name,n)], 板块表里查无此股的家数)。概念：成员数<=max_members，按 name 合并。"""
    import psycopg
    with psycopg.connect(dsn_reader(), connect_timeout=8) as conn, conn.cursor() as cur:  # ft_reader 本身只读角色
        cur.execute("""
            with big as (select sector_code from reference_data.sector_member
                          group by 1 having count(distinct security_code) > %s)
            select i.name, count(distinct left(m.security_code,6)) n
              from reference_data.sector_member m
              join reference_data.sector_info i using (sector_code)
             where left(m.security_code,6) = any(%s::text[])
               and i.sector_type = '概念'
               and coalesce(i.is_active, true)
               and m.sector_code not in (select sector_code from big)
             group by 1
             order by n desc, i.name
             limit %s""", (cfg["max_members"], codes, cfg["sector_top"]))
        rows = cur.fetchall()
        cur.execute("""select distinct left(security_code,6) from reference_data.sector_member
                        where left(security_code,6) = any(%s::text[])""", (codes,))
        mapped = {r[0] for r in cur.fetchall()}
    unmapped = len(set(codes)) - len(mapped & set(codes))
    return rows, unmapped


# ---------------------------------------------------------------- 一字/板块涨幅
def fetch_yizi(cfg: dict) -> tuple[list[dict] | None, str]:
    """竞价一字：涨停池里首封=09:25（集合竞价即封死）的票，带连板数 lbc 和封单额 fund。
    返回 (按连板数/封单额降序的行, 错误原因)；None=池子没就绪（宁缺毋假，不硬编）。"""
    ymd = datetime.now().strftime("%Y%m%d")
    wait = float(cfg.get("yizi_pool_wait_s", 5))
    last_err = ""
    for _ in range(int(cfg.get("yizi_pool_retries", 3))):
        try:
            rows = [normalize(r) for r in fetch_pool("getTopicZTPool", ymd)]
        except Exception as exc:
            last_err = type(exc).__name__
            time.sleep(wait)
            continue
        hits = [r for r in rows if r["fbt"] == "09:25"]
        if hits or rows:      # 池已开始写入：有命中或真没一字，都是事实，直接返回
            return sorted(hits, key=lambda r: (-r["lbc"], -(r["fund"] or 0))), ""
        time.sleep(wait)      # 空池：可能东财还没同步竞价封板，重试
    return None, last_err or "池空未写入（东财竞价封板未同步）"


def sector_pct(names: list[str]) -> dict[str, tuple[float, str]]:
    """概念名 → (sector_daily 最新交易日涨跌幅%, 'MM-DD')。一名多码按 TDX_ > THS_ > GN_ 取。"""
    import psycopg
    best: dict[str, tuple] = {}
    with psycopg.connect(dsn_reader(), connect_timeout=8) as conn, conn.cursor() as cur:
        cur.execute("""
            with latest as (select max(trade_date) d from market_data.sector_daily)
            select i.name, d.sector_code, d.pct_change::float8, d.trade_date
              from market_data.sector_daily d
              join reference_data.sector_info i using (sector_code)
              join latest on d.trade_date = latest.d
             where i.name = any(%s) and d.pct_change is not null
             order by i.name""", (names,))
        pref = {"TDX_": 0, "THS_": 1, "GN_": 2}
        for name, code, pct, d in cur.fetchall():
            rank = pref.get(code[:4], 9)
            if name not in best or rank < best[name][2]:
                best[name] = (pct, d, rank)
    return {n: (v[0], v[1].strftime("%m-%d")) for n, v in best.items()}


# ---------------------------------------------------------------- 主流程
def run_once(cfg: dict, tpl: dict, dry: bool) -> int:
    now = datetime.now()
    t = now.strftime("%H:%M:%S")
    gainers = None
    stale_reason = ""
    for attempt in range(1, cfg["retry"] + 1):
        try:
            gainers = fetch_gainers(cfg)
        except Exception as exc:
            stale_reason = f"行情拉取失败 {type(exc).__name__}"
            log({"what": "auction_poll_fail", "attempt": attempt, "err": str(exc)[:200]})
            gainers = None
        if gainers is not None:
            movers = sum(1 for g in gainers if abs(g["pct"]) >= 0.05)
            if movers >= cfg["fresh_min_movers"]:
                break
            stale_reason = f"市场级异动仅{movers}家(<{cfg['fresh_min_movers']})，疑似竞价价未刷新"
            gainers = None
        if attempt < cfg["retry"]:
            time.sleep(cfg["retry_wait_s"])

    if gainers is None:
        text = tpl["stale_notice"].format(t=t, why=stale_reason)
        log({"what": "auction_stale", "why": stale_reason})
        if dry:
            print("[dry-run]", text)
            return 1
        broadcast(cfg, text)
        return 1

    def build_section(rows_g: list[dict]) -> list[str]:
        """个股行 + 概念计数行 +（如有）未映射提示。涨跌两组共用。"""
        out_lines: list[str] = []
        shown = rows_g[:cfg["top_n"]]
        stocks = "、".join(tpl["auction_stock_row"].format(name=g["name"], pct=f"{g['pct']:+.2f}%")
                           for g in shown)
        if len(rows_g) > len(shown):
            stocks += f" …其余{len(rows_g)-len(shown)}只未列"
        out_lines.append(tpl["auction_stocks"].format(rows=stocks or "无"))
        unmapped, sec_fmt = len(rows_g), "无板块数据（PG未连）"
        if rows_g:
            try:
                rows_, unmapped = sector_counts([g["code"] for g in rows_g], cfg)
                pmap = sector_pct([n_ for n_, _ in rows_]) if rows_ else {}
                sec_fmt = "、".join(
                    tpl["auction_sector_pct_row"].format(
                        name=n_, n=n, pc=f"({pmap[n_][0]:+.1f}%)" if n_ in pmap else "")
                    for n_, n in rows_) or "无"
                if pmap:
                    sec_fmt += f"（板块涨幅截至{next(iter(pmap.values()))[1]}收盘）"
            except Exception as exc:
                log({"what": "pg_fail", "err": str(exc)[:200]})
                sec_fmt = f"PG查询失败:{type(exc).__name__}"
        out_lines.append(tpl["auction_sectors"].format(rows=sec_fmt))
        if unmapped:
            out_lines.append(f"（其中{unmapped}只板块表无概念行）")
        return out_lines

    picked = [g for g in gainers if g["pct"] > cfg["min_pct"]]
    losers = None
    try:                                    # 跌幅侧单独抓；挂了不影响涨侧出榜
        losers = [g for g in fetch_gainers(cfg, asc=True) if g["pct"] < -cfg["min_pct"]]
    except Exception as exc:
        log({"what": "losers_fetch_fail", "err": str(exc)[:200]})

    auction_mode = now.hour == 9                      # 9 点跑出来的才是竞价口径
    lines = [tpl["auction_header"].format(t=t, n=len(picked), min_pct=f"{cfg['min_pct']:g}")]
    yizi, yizi_err = fetch_yizi(cfg)                  # 竞价一字块：池子里首封09:25的
    if yizi is None:
        lines.append(tpl["yizi_fail"].format(why=yizi_err))
    elif not yizi:
        lines.append(tpl["yizi_none"])
    else:
        lines.append(tpl["yizi_header"].format(n=len(yizi)))
        lines.append("、".join(tpl["yizi_row"].format(
            name=r["name"], code=r["code"], lbc=r["lbc"],
            pct=f"{r['pct']:+.2f}%" if r["pct"] is not None else "涨幅缺数",
            fund=money(r["fund"])) for r in yizi))
    if picked:
        lines += build_section(picked)
    else:
        lines.append(tpl["auction_stocks"].format(rows="0只"))
    if losers is None:
        lines.append(f"跌幅>{cfg['min_pct']:g}%榜：未取到（两源皆挂，宁缺毋假）")
    elif not losers:
        lines.append(tpl["auction_down_none"].format(min_pct=f"{cfg['min_pct']:g}"))
    else:
        lines.append(tpl["auction_down_header"].format(n=len(losers), min_pct=f"{cfg['min_pct']:g}"))
        lines += build_section(losers)
    lines.append(tpl["auction_footer"].format(t=t))
    if not auction_mode:
        lines.append("※样机演示：当前非9点，涨幅为盘中实时价，非竞价价")
    text = "\n".join(lines)

    log({"what": "auction", "n": len(picked), "text": text})
    if dry:
        print("[dry-run] 将发送：\n" + text)
        return 0
    try:
        broadcast(cfg, text)
        print(f"[{t}] 竞价榜已推送（{len(picked)}只）")
        return 0
    except Exception as exc:
        log({"what": "send_fail", "err": str(exc)[:200]})
        print(f"[{t}] 发送失败：{exc}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wake-at", default="", help="如 09:25:30，未到点先睡到点")
    ap.add_argument("--force", action="store_true", help="跳过交易日检查")
    args = ap.parse_args()

    if args.wake_at:
        h, m, s = (int(x) for x in args.wake_at.split(":"))
        target = datetime.now().replace(hour=h, minute=m, second=s, microsecond=0)
        if datetime.now() < target:
            time.sleep((target - datetime.now()).total_seconds())

    cfg, tpl = load_cfg(), load_tpl()
    log_file = VAR_DIR / "auction_log.jsonl"
    if not args.force:
        try:
            import psycopg
            with psycopg.connect(dsn_reader(), connect_timeout=8) as conn, conn.cursor() as cur:
                if not is_trade_day(cur):
                    return 0            # 非交易日：静默
        except Exception as exc:
            if "psycopg" in type(exc).__name__.lower() or "connection" in str(exc).lower():
                if date.today().weekday() >= 5:
                    return 0            # 日历查不到 + 周末 → 不打扰
            # 其余情况继续跑，由行情新鲜度兜底
    try:
        return run_once(cfg, tpl, args.dry_run)
    except Exception as exc:            # 意外挂掉也要吭一声，静默死掉的播报最坑人
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        log({"what": "crash", "err": f"{type(exc).__name__}: {exc}"[:300]})
        try:
            send_feishu(*load_channel(cfg["channel"]),
                        f"【竞价速览 {datetime.now():%H:%M:%S}】任务异常退出：{type(exc).__name__}（详情见日志）")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    sys.exit(main())
