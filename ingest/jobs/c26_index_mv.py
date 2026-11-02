# -*- coding: utf-8 -*-
"""C26 · 把指数级总市值/流通市值落进 `market_data.index_market_value`。

**为什么要有这一步**（2026-10-26 用户点名「头部沪深300基金市值占沪深300流通市值比例的变化，
这个可以用来判断国家队的持仓；获取到的话也要入库」）：那一屏的分子（ETF 份额/规模）库里早就有
（`market_data.etf_share`，两所官网每日文件，实测可回补到 2019-10），**分母没有历史**。
分母的三条路当场都量过，都不通：

    ① 东财指数盘口只给**当日**：push2 stock/get secid=1.000300&fields=f116,f117
       实测 2026-10-27 总市值 51,000,638,979,882 元、流通市值 48,415,042,212,523 元。
    ② 中证官网历史行情接口（ak.stock_zh_index_hist_csindex）实测 2023-01 起 923 行，
       列里有 收盘/成交量/成交金额/样本数量/滚动市盈率 —— **没有市值**。
    ③ 本地自算（Σ成分流通股本×收盘）当场对账失败：`sector_member.ZS_沪深300` 的 300 条 in_date
       全是采集日 2026-08-23（无历史成分），`share_capital` 28,271 行里 25,251 行 float_share 是 NULL
       （c8/c9 只回算总股本），300 只成分里当日之前有流通股本行的只有 61 只
       ⇒ 自算流通市值 6.23 万亿 vs 东财 48.42 万亿，差 **−87%**。
    ④ 本机通达信插件库（c15 那个 SQLite）有每票 Ltsz 流通市值，但文件按日落盘，最早只到 2026-07-21。

⇒ 所以这张表的作用是**从今天起每天存一行真数**。存不到的日子在库里就是 null，
   界面显示 `—` 并现查 `min(trade_date)` 说明首日；**不插值、不问模型、不拿指数涨跌反推**。

**用法**
    python scripts\\ingest\\c26_index_mv.py                      # 库里最近一场指数日线
    python scripts\\ingest\\c26_index_mv.py --date 20261027
    python scripts\\ingest\\c26_index_mv.py --dry-run

**接口口径没验证过的东西不入库**：f116/f117 是"指数概况"里的两个字段，实测数量级与
中证月报一致（万亿级、流通< total），但**它是不是发布方口径的流通市值**没有第二条源交叉验证过
⇒ 屏上那句话必须带着 source 说"东财口径"，别写成"指数流通市值（权威）"。

**非交易日**：两所休市时接口仍返回上一场的值 ⇒ 本脚本**以库里 `index_daily` 的最近交易日为准**，
只在那一天写一行，不用接口自己的"当前"。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

import requests  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402

TARGET = 'market_data.index_market_value'
SPEC = 'imv-1'
SOURCE = 'eastmoney_push2'

# 要存哪几条指数：000300 是这一屏的分母，另两条是顺手把兄弟指数也攒着（以后做中证500/1000 的比例不用再等）
INDICES = [('1.000300', '000300.SH', '沪深300'), ('1.000905', '000905.SH', '中证500'),
           ('1.000852', '000852.SH', '中证1000')]

URL = ('https://push2.eastmoney.com/api/qt/stock/get'
       '?secid={secid}&fields=f43,f57,f58,f116,f117&fltt=2&invt=2')
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}

#: 归属日自证的容差：返回体 f43 与库里那场指数收盘的相对差。
#: 为什么要有这一道（与 c21/c22「报文里没有日期」同一条纪律）：这个接口只给"现在"那一个截面，
#: 追平跑到前天时它给的是**今天**的市值，直接盖上 `day` 就是拿今天的截面冒充前天。
#: 对账用的 `index_daily.close` 是本地日线入库的那一场，两者认不上就说明"接口现在说的"
#: 与"库里这一场"不是同一天 —— 那就一行都不写，而不是写三行贴个错日期。
CLOSE_TOL = 0.005


def _close_of(day: dt.date) -> float | None:
    v = engine.scalar('SELECT close FROM market_data.index_daily '
                      'WHERE security_code=%s AND trade_date=%s', ('000300.SH', day))
    return None if v is None else float(v)


def check_date(got_close, anchor: float | None, day: dt.date) -> str | None:
    """000300 那一发的收盘价与库里那一场对得上吗？对不上返回一句出声的话（对上了返回 None）。"""
    if anchor is None:
        return None
    if got_close is None:
        return '接口没给 f43（收盘价）—— 无法确认这一发就是库里那一场，不写'
    rel = abs(float(got_close) / anchor - 1.0)
    if rel > CLOSE_TOL:
        return (f'接口给的收盘 {float(got_close):.2f} 与库里 {day} 那场的 {anchor:.2f} 差 '
                f'{rel * 100:.2f}%（>{CLOSE_TOL * 100:.1f}%）⇒ 这一发不是那天的截面'
                '（这个接口只给「现在」），一行都不写')
    return None


def fetch_one(secid: str, timeout: float = 8.0) -> dict:
    """取一条指数的当日市值。列名对不上或值为空就**抛出去**，不兜成 0、不兜成空表。"""
    r = requests.get(URL.format(secid=secid), headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    data = (r.json() or {}).get('data') or {}
    out = {'close': data.get('f43'), 'code': data.get('f57'), 'name': data.get('f58'),
           'total_mv_yuan': data.get('f116'), 'float_mv_yuan': data.get('f117')}
    if out['total_mv_yuan'] is None or out['float_mv_yuan'] is None:
        raise RuntimeError(f'接口没给市值字段（secid={secid}，返回键={sorted(data)}）—— '
                          '字段改名要当场看见，不许静默产出 null 行')
    return out


def latest_session() -> dt.date:
    d = engine.scalar('SELECT max(trade_date) FROM market_data.index_daily WHERE security_code=%s',
                      ('000300.SH',))
    if d is None:
        raise SystemExit('market_data.index_daily 里没有 000300.SH，无法确定目标交易日')
    return d if isinstance(d, dt.date) else dt.datetime.strptime(str(d), '%Y-%m-%d').date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYYMMDD，默认取库里最近的指数交易日')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    day = (dt.datetime.strptime(args.date, '%Y%m%d').date() if args.date else latest_session())
    anchor = _close_of(day)
    print(f'=== 目标交易日 {day}（接口只给当日值，历史补不了）'
          f'归属日靠 f43 与库里那场收盘（{anchor:.2f}）对账 ===' if anchor else
          f'=== 目标交易日 {day}（库里那一场没有收盘价可比，归属日无法自证）===')

    rows, batch_id = [], 0
    for secid, code, name in INDICES:
        t0 = time.time()
        try:
            got = fetch_one(secid)
        except Exception as exc:
            print(f'  {name} 拉取失败：{type(exc).__name__} {str(exc)[:140]}')
            continue
        if code == '000300.SH':
            why = check_date(got.get('close'), anchor, day)
            if why:
                print(f'  归属日对账不过：{why}')
                return 2
        rows.append((day, code, SPEC, name,
                     float(got['total_mv_yuan']), float(got['float_mv_yuan']),
                     SOURCE, batch_id))
        print(f'  {name} 总市值 {float(got["total_mv_yuan"]) / 1e12:.2f} 万亿 '
              f'· 流通市值 {float(got["float_mv_yuan"]) / 1e12:.2f} 万亿 '
              f'· 流通/总 = {float(got["float_mv_yuan"]) / float(got["total_mv_yuan"]) * 100:.1f}%'
              f'  {time.time() - t0:.1f}s')

    if not rows:
        print('一条都没取到 —— 不写空批次（空批次会让人以为今天采过了）')
        return 1

    if args.dry_run:
        print(f'--dry-run，未写库（共 {len(rows)} 行）')
        return 0

    batch_id = ops_repo.begin_batch('eastmoney_index_mv', TARGET, source_path='push2 stock/get f116,f117')
    # 重算一天先清这一天（三行一起换），否则改了指数清单后旧行会挂着当"还有"
    engine.execute(f'DELETE FROM {TARGET} WHERE trade_date = %s AND spec_version = %s',
                   (day, SPEC), role='etl')
    engine.copy_rows(TARGET, ['trade_date', 'index_code', 'spec_version', 'index_name',
                              'total_mv_yuan', 'float_mv_yuan', 'source', 'source_batch_id'],
                     iter(rows), role='etl')
    engine.execute(f'UPDATE {TARGET} SET source_batch_id = %s WHERE trade_date = %s AND spec_version = %s',
                   (batch_id, day, SPEC), role='etl')
    ops_repo.end_batch(batch_id, row_count=len(rows))
    back = engine.query(f'SELECT index_code, float_mv_yuan FROM {TARGET} '
                        'WHERE trade_date = %s AND spec_version = %s ORDER BY 1', (day, SPEC))
    print(f'批次 {batch_id} 写 {len(rows)} 行；回读 {len(back)} 行：'
          + '，'.join(f"{b['index_code']} {float(b['float_mv_yuan']) / 1e12:.2f} 万亿" for b in back))
    return 0


if __name__ == '__main__':
    sys.exit(main())
