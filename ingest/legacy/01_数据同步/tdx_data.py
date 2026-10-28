# -*- coding: utf-8 -*-
"""
tdx_data.py — 通达信本地数据读取工具（以 TDX 本地文件为主）
=====================================================
用法示例:
    python tdx_data.py daily 600036           # 读取本地日线
    python tdx_data.py daily 600036 --csv     # 导出 CSV
    python tdx_data.py minute 600036          # 本地分钟线
    python tdx_data.py online-tick 600036     # 在线分笔(无需客户端)
    python tdx_data.py finance 600036         # 在线财务(无需客户端)
    python tdx_data.py check 600036           # 检查本地数据新鲜度

说明:
- 本地读取: mootdx Reader 读 D:/new_tdx64/vipdoc (需通达信已下载数据)
- 在线K线: 实测本机被公共主站拒绝(TdxFunctionCallError)，脚本自动降级;
           若你在别的网络环境可用, 会自动合并补最新。
- 在线分笔/财务/除权: 实测可用, 不依赖客户端。
- 数据存放: 所有导出 CSV 一律写入 D:/finance/data (公司数据目录),
  本 workspace 只放脚本不放数据。
- 架构备注(2026-08-23): `D:/finance/data/fin_data*.db` 旧名已废弃为空壳;
  本地正式库改为 `tq_*.db` 系列。此脚本默认只负责读取/导出 TDX 原始数据，
  不应再把 `fin_data*.db` 视为正式入库目标。
"""
import argparse
import os
import socket
import struct
import sqlite3
import sys
import glob
from datetime import date, datetime

import pandas as pd

TDXDIR = os.environ.get("TDXDIR", "D:/new_tdx64")
# 公司数据存放目录(只放数据, 不放脚本); workspace 只放脚本
DATA_DIR = os.environ.get("DATA_DIR", "D:/finance/data")
# 本地正式日线库 (tq_* 系列; fin_data*.db 已废弃为空壳)
DB_DAILY = os.path.join(DATA_DIR, "tq_daily_2026.db")
TABLE = "stock_daily"
# 实测可达的主站(优先级从高到低)
SERVERS = [
    ("110.41.147.114", 7709),  # 深圳双线主站1
    ("124.70.199.56", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("123.60.73.44", 7709),
]
TIMEOUT = 8
socket.setdefaulttimeout(TIMEOUT)

# ---------- 本地读取 ----------

def read_local_daily(symbol: str) -> pd.DataFrame:
    """读本地日线(vipdoc/sh(或sz)/lday)。失败返回空 DataFrame。"""
    try:
        from mootdx.reader import Reader
        reader = Reader.factory(market="std", tdxdir=TDXDIR)
        df = reader.daily(symbol=symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        if "datetime" in df.columns:
            df = df.rename(columns={"datetime": "date"})
        return df
    except Exception as e:
        print(f"[本地读取失败] {symbol}: {type(e).__name__}: {e}")
        return pd.DataFrame()


def read_local_minute(symbol: str) -> pd.DataFrame:
    try:
        from mootdx.reader import Reader
        reader = Reader.factory(market="std", tdxdir=TDXDIR)
        df = reader.minute(symbol=symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        return df.reset_index()
    except Exception as e:
        print(f"[本地分钟读取失败] {symbol}: {type(e).__name__}: {e}")
        return pd.DataFrame()


# ---------- 在线读取 ----------

def _try_connect():
    """尝试连接主站, 返回 client 或 None。"""
    from mootdx.quotes import Quotes
    for ip, port in SERVERS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect((ip, port))
        except Exception:
            continue
        finally:
            s.close()
        try:
            client = Quotes.factory(market="std", server=(ip, port))
            return client
        except Exception:
            continue
    return None


def fetch_online_bars(symbol: str, offset: int = 800) -> pd.DataFrame:
    """在线抓日线。本机实测被拒(返回空 DataFrame)。"""
    client = _try_connect()
    if client is None:
        print("[在线] 所有主站连接失败")
        return pd.DataFrame()
    try:
        df = client.bars(symbol=symbol, frequency=9, offset=offset)
        if df is not None and not df.empty:
            df = df.reset_index()
            if "datetime" in df.columns:
                df = df.rename(columns={"datetime": "date"})
            return df
        print("[在线] K线请求被主站拒绝(本机网络环境常见), 返回空")
        return pd.DataFrame()
    except Exception as e:
        print(f"[在线] K线获取异常: {type(e).__name__}: {e}")
        return pd.DataFrame()


def fetch_online_tick(symbol: str):
    """在线分笔(实时成交明细) — 实测可用, 无需客户端。"""
    from pytdx.hq import TdxHq_API
    for ip, port in SERVERS:
        try:
            api = TdxHq_API(raise_exception=True)
            if not api.connect(ip, port, time_out=TIMEOUT):
                continue
            market = 1 if symbol.startswith(("6", "9", "5")) else 0
            data = api.get_minute_time_data(market, symbol)
            api.disconnect()
            if data:
                return pd.DataFrame(data)
        except Exception as e:
            print(f"[在线分笔] {ip}:{port} 失败: {type(e).__name__}: {e}")
    return pd.DataFrame()


def fetch_online_finance(symbol: str):
    """在线财务 — 实测可用, 无需客户端。"""
    from pytdx.hq import TdxHq_API
    for ip, port in SERVERS:
        try:
            api = TdxHq_API(raise_exception=True)
            if not api.connect(ip, port, time_out=TIMEOUT):
                continue
            market = 1 if symbol.startswith(("6", "9", "5")) else 0
            data = api.get_finance_info(market, symbol)
            api.disconnect()
            if data:
                return dict(data)
        except Exception as e:
            print(f"[在线财务] {ip}:{port} 失败: {type(e).__name__}: {e}")
    return {}


# ---------- 合并 ----------

def merge_latest(symbol: str) -> pd.DataFrame:
    """本地 + 在线合并日线: 以本地为底, 在线有更新的行则补上。"""
    local = read_local_daily(symbol)
    if local.empty:
        print(f"[警告] 本地无 {symbol} 数据, 请先运行通达信下载")
        return pd.DataFrame()

    # 数据新鲜度
    last_local = local["date"].max()
    if isinstance(last_local, str):
        last_local = last_local[:10]
    print(f"[本地] {symbol} 共 {len(local)} 条, 最新: {last_local}")

    online = fetch_online_bars(symbol)
    if online.empty:
        print(f"[合并] 在线不可用, 使用本地数据(共 {len(local)} 条)")
        return local

    # 统一 date 列为字符串(取前10位)
    def norm(df):
        d = df.copy()
        d["date"] = d["date"].astype(str).str[:10]
        return d

    local = norm(local)
    online = norm(online)

    last_local_str = str(last_local)
    newer = online[online["date"] > last_local_str]
    if newer.empty:
        print(f"[合并] 本地已是最新, 无新增 ({len(local)} 条)")
        return local

    print(f"[合并] 在线有 {len(newer)} 条新数据, 追加合并")
    merged = pd.concat([local, newer], ignore_index=True)
    merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    print(f"[合并] 完成: {len(merged)} 条 (原 {len(local)} + 新 {len(newer)})")
    return merged


# ---------- 增量同步到本地库 ----------

def read_day_file(filepath):
    """直接 struct 解析通达信 .day 文件
    返回: [(trade_date, open, high, low, close, amount, vol), ...]
    单位: 价格=元, vol=手(股/100), amount=元
    """
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // 32
    recs = []
    for i in range(n):
        rec = data[i*32:(i+1)*32]
        date_int, open_p, high_p, low_p, close_p, amount, vol, _ = struct.unpack('<IIIIIfII', rec)
        recs.append((str(date_int), open_p/100.0, high_p/100.0, low_p/100.0, close_p/100.0,
                     float(amount), vol/100.0))
    return recs


def sync_symbol(symbol: str) -> int:
    """增量同步单只股票: vipdoc .day → tq_daily_2026.db stock_daily
    增量判断: .day 中 trade_date > 数据库现有 max(trade_date) 的记录
    返回: 插入条数
    """
    # 判断市场: 6/9/5 开头为上海, 其余为深圳
    market = 'sh' if symbol.startswith(('6', '9', '5')) else 'sz'
    ts_code = f'{symbol}.{"SH" if market == "sh" else "SZ"}'
    day_file = os.path.join(TDXDIR, 'vipdoc', market, 'lday', f'{market}{symbol}.day')

    if not os.path.isfile(day_file):
        print(f'[sync] 日线文件不存在: {day_file}')
        return 0

    recs = read_day_file(day_file)
    if not recs:
        print(f'[sync] {ts_code} 日线文件为空')
        return 0
    print(f'[sync] {ts_code} 本地日线 {len(recs)} 条, 最新 {recs[-1][0]}')

    conn = sqlite3.connect(DB_DAILY)
    cur = conn.cursor()
    cur.execute(f"SELECT MAX(trade_date) FROM {TABLE} WHERE ts_code=?", (ts_code,))
    max_date = cur.fetchone()[0]
    print(f'[sync] 数据库现有最新: {max_date}')

    insert_recs = [r for r in recs if max_date is None or r[0] > max_date]
    if not insert_recs:
        print(f'[sync] 无需增量 (数据库已最新)')
        conn.close()
        return 0
    print(f'[sync] 待补 {len(insert_recs)} 条: {[r[0] for r in insert_recs]}')

    # 第一条增量的 pre_close = 数据库最新那天的收盘价(从 .day 里找)
    pre_close = None
    if max_date:
        for r in recs:
            if r[0] == max_date:
                pre_close = r[4]
                break

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    inserted = 0
    for r in insert_recs:
        td, o, h, l, c, amt, vol = r
        if pre_close is None:
            chg = pct = lu = ld = None
        else:
            chg = round(c - pre_close, 2)
            pct = round((c - pre_close) / pre_close * 100, 2)
            lu = round(pre_close * 1.1, 2)
            ld = round(pre_close * 0.9, 2)
        cur.execute(f"DELETE FROM {TABLE} WHERE ts_code=? AND trade_date=?", (ts_code, td))
        cur.execute(
            f"INSERT INTO {TABLE} (ts_code, trade_date, open, high, low, close, pre_close, "
            f"change, pct_change, vol, amount, adj_factor, limit_up, limit_down, source, updated_at) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts_code, td, o, h, l, c, pre_close, chg, pct, vol, amt, 1.0, lu, ld, 'vipdoc', now_str))
        inserted += 1
        pre_close = c
    conn.commit()
    conn.close()
    print(f'[sync] {ts_code} 写入 {inserted} 条 ✅')
    return inserted


def sync_all(limit=None) -> int:
    """增量同步全市场: 遍历 vipdoc sh/sz lday 所有 .day 文件
    增量判断: 每只股票 .day 最新交易日 > 数据库现有 max(trade_date)
    """
    files = []
    for mkt in ('sh', 'sz'):
        files += glob.glob(os.path.join(TDXDIR, 'vipdoc', mkt, 'lday', '*.day'))
    files.sort()
    if limit:
        files = files[:limit]
    print(f'[sync-all] 待处理文件: {len(files)}')

    conn = sqlite3.connect(DB_DAILY)
    cur = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_insert = 0
    ok_files = skip_files = err_files = 0

    for idx, fp in enumerate(files, 1):
        fname = os.path.basename(fp)
        try:
            market_char = fname[:2].lower()
            symbol = fname[2:8]
            ts_code = f'{symbol}.{"SH" if market_char == "sh" else "SZ"}'

            recs = read_day_file(fp)
            if not recs:
                skip_files += 1
                continue

            cur.execute(f"SELECT MAX(trade_date) FROM {TABLE} WHERE ts_code=?", (ts_code,))
            max_date = cur.fetchone()[0]
            insert_recs = [r for r in recs if max_date is None or r[0] > max_date]
            if not insert_recs:
                skip_files += 1
                continue

            pre_close = None
            if max_date:
                for r in recs:
                    if r[0] == max_date:
                        pre_close = r[4]
                        break

            for r in insert_recs:
                td, o, h, l, c, amt, vol = r
                if pre_close is None:
                    chg = pct = lu = ld = None
                else:
                    chg = round(c - pre_close, 2)
                    pct = round((c - pre_close) / pre_close * 100, 2)
                    lu = round(pre_close * 1.1, 2)
                    ld = round(pre_close * 0.9, 2)
                cur.execute(f"DELETE FROM {TABLE} WHERE ts_code=? AND trade_date=?", (ts_code, td))
                cur.execute(
                    f"INSERT INTO {TABLE} (ts_code, trade_date, open, high, low, close, pre_close, "
                    f"change, pct_change, vol, amount, adj_factor, limit_up, limit_down, source, updated_at) "
                    f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ts_code, td, o, h, l, c, pre_close, chg, pct, vol, amt, 1.0, lu, ld, 'vipdoc', now_str))
                total_insert += 1
                pre_close = c
            ok_files += 1
            if idx % 500 == 0:
                conn.commit()
                print(f'  进度 {idx}/{len(files)} 已插入 {total_insert} 条')
        except Exception as e:
            err_files += 1
            print(f'  [错误] {fname}: {e}')

    conn.commit()
    conn.close()
    print('=' * 60)
    print(f'[sync-all] 完成: {ok_files} 文件有增量, {skip_files} 跳过, {err_files} 失败')
    print(f'[sync-all] 共插入 {total_insert} 条记录 → {DB_DAILY}')
    return total_insert


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description="通达信本地+在线数据工具")
    ap.add_argument("cmd", choices=["daily", "minute", "online-tick", "finance", "check", "sync"],
                    help="操作类型 (sync=增量同步vipdoc到本地库)")
    ap.add_argument("symbol", nargs="?", default=None, help="股票代码, 如 600036 / 000001")
    ap.add_argument("--all", action="store_true", help="sync 全市场增量")
    ap.add_argument("--csv", action="store_true", help="同时导出 CSV")
    ap.add_argument("--limit", type=int, default=None, help="sync-all 限制处理文件数(测试用)")
    args = ap.parse_args()

    symbol = args.symbol

    if args.cmd == "sync":
        if args.all:
            sync_all(limit=args.limit)
        elif symbol:
            n = sync_symbol(symbol)
            if n == 0:
                print(f"[sync] {symbol} 无增量或文件缺失")
        else:
            print("[sync] 用法: tdx_data.py sync 600036 或 tdx_data.py sync --all")
        return

    if args.cmd == "daily":
        df = merge_latest(symbol)
        if not df.empty:
            print(df.tail(8).to_string(index=False))
            if args.csv:
                os.makedirs(DATA_DIR, exist_ok=True)
                out = os.path.join(DATA_DIR, f"{symbol}_daily_merged.csv")
                df.to_csv(out, index=False, encoding="utf-8-sig")
                print(f"已导出: {out}")

    elif args.cmd == "minute":
        df = read_local_minute(symbol)
        if not df.empty:
            print(f"[本地分钟] {symbol} 共 {len(df)} 条")
            print(df.tail(5).to_string(index=False))
            if args.csv:
                os.makedirs(DATA_DIR, exist_ok=True)
                out = os.path.join(DATA_DIR, f"{symbol}_minute.csv")
                df.to_csv(out, index=False, encoding="utf-8-sig")
                print(f"已导出: {out}")

    elif args.cmd == "online-tick":
        df = fetch_online_tick(symbol)
        if not df.empty:
            print(f"[在线分笔] {symbol} 共 {len(df)} 条(实时, 无需客户端)")
            # 价格合理性检查: A股单价一般 <100 元, 若明显异常则提示
            if "price" in df.columns and pd.to_numeric(df["price"], errors="coerce").max() > 100:
                print("[注意] 该主站返回的 price 字段可能失真(实测公共主站有此问题), "
                      "建议仅使用 vol 成交量, 价格以本地文件为准")
            print(df.tail(5).to_string(index=False))
            if args.csv:
                os.makedirs(DATA_DIR, exist_ok=True)
                out = os.path.join(DATA_DIR, f"{symbol}_tick.csv")
                df.to_csv(out, index=False, encoding="utf-8-sig")
                print(f"已导出: {out}")
        else:
            print("[在线分笔] 获取失败")

    elif args.cmd == "finance":
        info = fetch_online_finance(symbol)
        if info:
            print(f"[在线财务] {symbol}")
            for k, v in list(info.items())[:15]:
                print(f"  {k}: {v}")
        else:
            print("[在线财务] 获取失败")

    elif args.cmd == "check":
        df = read_local_daily(symbol)
        if df.empty:
            print(f"[检查] 本地无 {symbol} 数据")
            return
        last = df["date"].max()
        if isinstance(last, str):
            last = last[:10]
        days = (date.today() - datetime.strptime(str(last)[:10], "%Y-%m-%d").date()).days
        status = "最新 ✅" if days <= 2 else f"落后 {days} 天 ⚠️ (请打开通达信让它下载)"
        print(f"[检查] {symbol}: {len(df)} 条, 最新 {last}, {status}")


if __name__ == "__main__":
    main()
