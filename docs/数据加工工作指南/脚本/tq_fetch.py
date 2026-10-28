# -*- coding: utf-8 -*-
"""
tq_fetch.py · 通达信 TQ 接口「一键取数」小工具（复用本项目的 tq_bridge）

用途：不想每次手写 tqcenter snippet 时，命令行直接取某只票的常用数据。
前置：**通达信客户端已启动并登录**（TQ 是连到运行中客户端的本地 RPC）。

    python tq_fetch.py 600519.SH                 # 全都要（行情/档案/板块/除权/财务）
    python tq_fetch.py 600519.SH --what quote    # 只要快照
    python tq_fetch.py 600519.SH --what sector   # 只要所属板块
    python tq_fetch.py 600519.SH --what divid    # 只要除权除息
    python tq_fetch.py 600519.SH --what fin      # 只要财务
    python tq_fetch.py 000001.SH --what profile  # 个股档案(主营/涨跌停/市值...)

what 可选：quote | profile | sector | divid | fin | all（默认 all）

它不写库，只取来打印，方便随手查/核对通达信口径。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]   # 本文件在 docs/<指南>/scripts/ 下，上溯三级即项目根

# 复用本项目 app/tdx_client/tq_bridge（写临时脚本到 PYPlugins\user、起子进程、读结果）
sys.path.insert(0, str(ROOT))                # 用项目根，绝不指外部仓库
sys.stdout.reconfigure(encoding='utf-8')

from app.tdx_client import tq_bridge  # noqa: E402

SNIPPET = '''
res = {{"code": {code!r}}}
try:
    m = tq.get_more_info(stock_code={code!r})
    res["profile"] = m
except Exception as e:
    res["profile_err"] = str(e)
if "{what}" in ("all", "quote"):
    try:
        d = tq.get_market_data(stock_list=[{code!r}], period="1d",
                               count=5, dividend_type="none")
        res["quote"] = d
    except Exception as e:
        res["quote_err"] = str(e)
if "{what}" in ("all", "sector"):
    try:
        res["sector"] = tq.get_relation(stock_code={code!r})
    except Exception as e:
        res["sector_err"] = str(e)
if "{what}" in ("all", "divid"):
    try:
        df = tq.get_divid_factors(stock_code={code!r})
        res["divid"] = df.reset_index().tail(8).to_dict("records")
    except Exception as e:
        res["divid_err"] = str(e)
if "{what}" in ("all", "fin"):
    try:
        df = tq.get_financial_data(stock_list=[{code!r}])
        res["fin_type"] = str(type(df))
        res["fin"] = (df.reset_index().tail(4).to_dict("records")
                      if hasattr(df, "reset_index") else str(df)[:500])
    except Exception as e:
        res["fin_err"] = str(e)
emit(res)
'''


def _trim(name, val):
    s = json.dumps(val, ensure_ascii=False, default=str)
    return f'{name}: {s if len(s) <= 800 else s[:800] + "…"}'


def main() -> int:
    ap = argparse.ArgumentParser(description='通达信 TQ 一键取数')
    ap.add_argument('code', help='证券代码，如 600519.SH / 000001.SZ')
    ap.add_argument('--what', default='all',
                    choices=['all', 'quote', 'profile', 'sector', 'divid', 'fin'])
    args = ap.parse_args()

    ok, why = tq_bridge.available()
    if not ok:
        print('TQ 不可用：%s' % why)
        print('先启动并登录通达信：python -m app.tdx_client.run_agent '
              '--only s1_launch --only s2_login')
        return 2

    r = tq_bridge.run_snippet(
        SNIPPET.format(code=args.code, what=args.what), timeout=180)
    if not r.get('ok') and 'error' in r:
        print('取数失败：', r['error'])
        return 1

    print('=== %s · what=%s ===' % (args.code, args.what))
    for k in ('quote', 'profile', 'sector', 'divid', 'fin', 'fin_type'):
        if k in r:
            print(_trim(k, r[k]))
    for k in [x for x in r if x.endswith('_err')]:
        print('%s: 出错 %s' % (k[:-4], r[k]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
