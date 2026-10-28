# -*- coding: utf-8 -*-
"""C25 · 个股财务多期（同花顺财务摘要，累计档 + 单季档）→ `reference_data.stock_financial_period`。

**为什么要有这一步**（用户 2026-10-16 点名：「财务关键指标，这里把近 5 年的都分析一下，
季报和年报，营业额和利润」）：页面上那块「财务关键指标」本来就出网读同花顺
（`vr/astock.py:299 financials()`），但**只取了末行**。同一次请求里其实带着 103 期
（实测 600519：1998-12-31 … 2026-06-30），另有「按单季度」一档 98 期。
所以近 5 年的营收／利润**不需要新数据源**，需要的是把多期存下来 + 一个不会说谎的解析器。

三个实测事实，写在代码旁边免得下一个人重新踩：
  · **两档都直接来自源**，不是我们自己差的：可加性已核（茅台 2026 Q1 547.03 亿 + Q2 375.75 亿
    = 累计 H1 922.78 亿）。所以库里 `period_kind` 两行都是源口径。
  · **源交回的是带单位的字符串**：`'547.03亿'`、`'11.56%'`、`'205.67'`，缺数是 `False` 或 `''`。
    解析只住本文件 `_to_num()` 一处；解析不出来写 **NULL 并留 `raw`**，绝不写 0
    （0 会被下游读成"这一季营收为零"，而真意是"没拿到"）。
  · 代价实测：12 只连发 12/12 成、0.57 秒/只（一档）⇒ 一只票两档约 1.2 秒，
    全市场 5,200 只 ≈ 104 分钟。所以默认只刷**自选 + 持仓**，全市场要显式 `--all` 且带 `--max-minutes`。

只此一个源。东财版 `stock_financial_abstract`（70 指标、float 元、同样 103 期）**不写进这张表** ——
同一件事有两个数，迟早对不上（本机在涨停价口径上就犯过一次）。

用法：
    python -m ingest.jobs.c25_financials_ths                      # 自选 + 持仓（默认）
    python -m ingest.jobs.c25_financials_ths --code 600519 --code 000001.SZ
    python -m ingest.jobs.c25_financials_ths --all --max-minutes 25 --only-stale 3
    python -m ingest.jobs.c25_financials_ths --refresh-gap 60 --max-minutes 6   # 每日链用的那一档
    python -m ingest.jobs.c25_financials_ths --dry-run             # 只解析不写库
    python -m ingest.jobs.c25_financials_ths --verify              # 回查库里覆盖与新度

退出码：0 写了且没失败、库里也没缺口 / 1 一只都没写成（接口或库不通）/ 2 按设计不写（没票可刷）/
3 部分票失败**或覆盖率还有缺口**（清单在输出里点名，也落进 `operations.ingest_batch`）/
4 自限预算用完还剩票。
⚠️ 3 是故意把"还缺几只"也算进去的：失败单步重试阶梯认这个档，于是缺口是**会被自动吃掉的状态**。

**为什么每日链那一步要 `--refresh-gap`（10-16 补的闭环，别再退回"只刷本机名单"）**：
这一步原先在机器上三处没账 —— 不写 `operations.ingest_batch`（该表里这张目标的行数实测为 0）、
`verify()` 的百分比拿**表自己的只数**当分母（缺 11 只也显示 100%）、逐只失败只打进 stdout。
结果是那天 11 只票因一个溢出值整只写不进去，除了人去 grep 日志没有任何环节会发现。
现在：批账落一行（成/败/写多少行/失败代码/还缺几只）、覆盖率分母换成主档、预算分两档
（缺口与名单外先拿一份，名单最旧的拿剩下的），到点自收、明天接着 ⇒ 缺口自己收敛。
两档的比例与 `gap_days` 是按服务速率倒推的，见 `refresh_plan` 的 docstring ——
"排成一条队"的两种写法都被实测否掉了（名单在前 ⇒ 缺口永远轮不到；缺口在前 ⇒ 名单几天刷不到）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.db import engine                                        # noqa: E402

TABLE = 'reference_data.stock_financial_period'
SOURCE = 'ths'
#: 两档都取。名字就是源 `indicator=` 的取值，`period_kind` 由它映射。
INDICATORS: Dict[str, str] = {'cum': '按报告期', 'single': '按单季度'}

#: 源列名 → 我们的列名。列名带单位后缀，读的人不用猜。
FIELDS: Dict[str, str] = {
    '净利润': 'net_profit_yuan', '净利润同比增长率': 'net_profit_yoy_percent',
    '扣非净利润': 'deduct_profit_yuan', '扣非净利润同比增长率': 'deduct_profit_yoy_percent',
    '营业总收入': 'revenue_yuan', '营业总收入同比增长率': 'revenue_yoy_percent',
    '基本每股收益': 'eps', '每股净资产': 'bvps',
    '每股资本公积金': 'cap_reserve_ps', '每股未分配利润': 'undistributed_ps',
    '每股经营现金流': 'op_cf_ps',
    '销售净利率': 'net_margin_percent', '销售毛利率': 'gross_margin_percent',
    '净资产收益率': 'roe_percent', '净资产收益率-摊薄': 'roe_diluted_percent',
    '营业周期': 'operating_cycle_days', '存货周转率': 'inventory_turnover',
    '存货周转天数': 'inventory_days', '应收账款周转天数': 'receivable_days',
    '流动比率': 'current_ratio', '速动比率': 'quick_ratio',
    '保守速动比率': 'conservative_quick_ratio', '产权比率': 'equity_ratio',
    '资产负债率': 'debt_ratio_percent',
}
#: 这些列是金额（源写「亿」「万」），其余是比率／每股／天数（源多半已写成小数或百分号串）。
MONEY = frozenset({'net_profit_yuan', 'deduct_profit_yuan', 'revenue_yuan'})
_NUM_RE = re.compile(r'^(-?\d+(?:\.\d+)?)\s*(亿|万|千|万亿元|亿元|万元)?$')
_SCALE = {'万': 1e4, '千': 1e3, '亿': 1e8, '万元': 1e4, '亿元': 1e8, '万亿元': 1e12}


def _to_num(v: Any, money: bool = False) -> Optional[float]:
    """源那一格 → float 或 None。**只有源真给了 0 才返回 0。**

    形状实测：`'547.03亿'` / `'11.56%'` / `'205.67'` / `False` / `''` / `None` / `nan` /
    偶发带千分位。解析不出来一律 None + 原文留 `raw`：这一列的 NULL 在界面上写"没拿到"，
    写成 0 就是"这一季营收为零"那种假读数（本机在涨停价、eps/bps 上都栽过同一类）。
    """
    if v is None or v is False or v is True:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return None if not math.isfinite(x) else x
    s = str(v).strip().replace(',', '')
    if not s or s in {'--', '-', 'False', 'false', 'None', '未披露', '不适用'}:
        return None
    pct = s.endswith('%')
    s = s.rstrip('%').strip()
    m = _NUM_RE.match(s)
    if not m:
        return None
    try:
        x = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit:
        x *= _SCALE.get(unit, 1.0)
    elif money:
        # 金额列没带单位：源在这里给过裸数（元）。不猜数量级 —— 猜错是百倍误差。
        return None
    if pct:
        return x
    return x


def _fetch_raw(code: str, indicator: str) -> List[Dict[str, Any]]:
    """**唯一的出网出口**（两档各一发）。pytest 里硬挡真发：测试要的是解析与写库，不是打到同花顺。"""
    if os.environ.get('PYTEST_CURRENT_TEST') and not os.environ.get('FT_ALLOW_TEST_NET'):
        raise RuntimeError('pytest 进程里不许真出网：请 monkeypatch `_fetch_raw`'
                           '（要真连发请设 FT_ALLOW_TEST_NET=1）')
    import akshare as ak
    # 库里存带后缀全码（join 别的表要用），但同花顺这个接口只认 6 位裸码：
    # 实测传 '600519.SH' 会让 akshare 在 None 上取 .string 抛 AttributeError（不是"这只票没数据"）。
    df = ak.stock_financial_abstract_ths(symbol=str(code).split('.')[0][:6], indicator=indicator)
    if df is None:
        return []
    return list(df.to_dict('records'))


def parse_rows(code: str, kind: str, raw_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """源那 100 多行 → 我们的行。`report_date` 解析不出来的那一行整行丢（并计数），
    因为主键没有它就没法幂等写；其余列解析不出来是 NULL。"""
    out: List[Dict[str, Any]] = []
    for r in raw_rows:
        d = _as_date(r.get('报告期'))
        if d is None:
            continue
        row: Dict[str, Any] = {'security_code': code, 'report_date': d, 'period_kind': kind,
                               'source': SOURCE,
                               'raw': json.dumps(_json_safe(r), ensure_ascii=False, default=str)}
        for src, col in FIELDS.items():
            row[col] = _to_num(r.get(src), money=col in MONEY)
        out.append(row)
    return out


def _as_date(v: Any) -> Optional[dt.date]:
    if isinstance(v, dt.date):
        return v
    s = str(v or '').strip()
    for fmt in ('%Y-%m-%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S'):
        try:
            return dt.datetime.strptime(s[:len('2026-06-30')] if fmt != '%Y-%m-%d %H:%M:%S' else s, fmt).date()
        except ValueError:
            continue
    return None


_COLUMNS = ('security_code', 'report_date', 'period_kind') + tuple(FIELDS.values())
UPSERT = (f'insert into {TABLE} ({", ".join(_COLUMNS)}, raw) values '
          f'({", ".join(["%s"] * (len(_COLUMNS) + 1))}) '
          'on conflict (security_code, report_date, period_kind) do update set '
          + ', '.join(f'{c} = excluded.{c}' for c in _COLUMNS[3:]) + ', raw = excluded.raw, fetched_at = now()')


def _json_safe(v: Any) -> Any:
    """NaN/Inf → None，其余原样。

    ⚠️ 10-16 实测：源里"营业周期""周转率"那一类偶发 NaN，`json.dumps` 默认会写成裸 `NaN`，
    Python 读得回去，PG 的 jsonb 读不回（invalid input syntax for type json，Token "NaN" is invalid）
    ⇒ 那一整只票的全部行都写不进去（本次回填 313 只就是这么折的）。
    留原文只为了给"解析不出来的字段"作证据，不值得为它把整只票丢掉。
    """
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


def write_rows(rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    payload = [tuple(r.get(c) for c in _COLUMNS) + (json.dumps(r['raw']) if not isinstance(r['raw'], str) else r['raw'],)
               for r in rows]
    with engine.cursor(role='etl') as cur:
        cur.executemany(UPSERT, payload)
    return len(payload)


def _full(code: str) -> str:
    """六位码 → 带后缀全码。**认不出来的号段直接丢**，不猜市场（猜错＝往另一只票上写数）。"""
    c = str(code).strip().upper()
    if '.' in c:
        return c if re.match(r'^\d{6}\.(SH|SZ|BJ)$', c) else ''
    if not re.match(r'^\d{6}$', c):
        return ''
    if c.startswith(('60', '68', '88', '99')):
        return f'{c}.SH'
    if c.startswith(('43', '83', '87', '92')):
        return f'{c}.BJ'
    if c.startswith(('00', '30')):
        return f'{c}.SZ'
    return ''


def watchlist_codes() -> List[str]:
    """默认母体：自选 + 持仓（本机名单的最新一场快照）。读不到就交空表，调用方按"没票可刷"停。

    ⚠️ 这张表的列是 `captured_at` + `trade_date`，**没有 `snapshot_at`** —— 10-16 这条真炸过一次：
    写 SQL 的人按别处的命名猜了一列，于是每日链那一步（母体＝本机名单）会以 rc 1 崩在
    `UndefinedColumn` 上。命名不一致是这台机器上反复出现的坑（`sector_member` 那边同类）。
    """
    rows = engine.query(
        "select distinct security_code from reference_data.watchlist_member_snapshot "
        "where trade_date = (select max(trade_date) from reference_data.watchlist_member_snapshot)",
        (), role='reader')
    if rows is None:
        raise RuntimeError('本机名单读不到（表不在或查不通）—— 不交空表，空表会被念成"名单是空的"')
    return sorted({r['security_code'] for r in rows if r.get('security_code')})


def all_stock_codes() -> List[str]:
    rows = engine.query("select security_code from reference_data.security "
                        "where asset_type = 'stock' order by 1", (), role='reader')
    if rows is None:
        raise RuntimeError('主档读不到（reference_data.security）—— 不交空表，空表会被念成"市场没票"')
    return [r['security_code'] for r in rows]


def coverage_gap() -> Dict[str, Any]:
    """覆盖率闸：**分母是主档，不是这张表自己的只数**。

    10-16 的教训就在这句上：那天补跑缺了 11 只（一个 `numeric(12,4)` 装不下的同比把整只票的
    upsert 打回滚），而旧 `verify()` 打印的是「5566 只（占 5566 只的 100%）」—— 分母是它自己，
    缺多少只显示满多少。现在把缺口当**一等读数**：几只、具体哪几只、还差多少只在表里没有一行。

    库不通或主档读不到一律**抛错**，不交空 dict：空缺口会被下游念成"全齐了"。
    """
    master = engine.query_one(
        "select count(*) n from reference_data.security where asset_type = 'stock'", (), role='reader')
    if not master or master.get('n') is None:
        raise RuntimeError('主档只数读不到 —— 报不出缺口，不等于没有缺口')
    miss = engine.query(
        f"select s.security_code c from reference_data.security s where s.asset_type = 'stock' "
        f"and not exists (select 1 from {TABLE} t where t.security_code = s.security_code) order by 1",
        (), role='reader')
    if miss is None:
        raise RuntimeError(f'缺口清单读不到（{TABLE} 或主档不可查）—— 不交空表')
    codes = [r['c'] for r in miss if r.get('c')]
    return {'master': int(master['n']), 'have': int(master['n']) - len(codes), 'missing': codes}


def stale_codes(max_age_days: float, limit: int = 0) -> List[str]:
    """该刷的票：采过但已超过 N 天没更新的，**加上一次都没写成的**。

    ⚠️ 后半截是 10-16 补的：那天有 313 只因 `raw` 里的 NaN 整只写失败（一条都没落库），
    而旧写法只查"表里有行且过期"的那些 ⇒ 这些票永远进不了补跑名单，静默地缺。
    "从没采过"比"采得旧"更该刷。
    """
    sql = f"""select security_code, max(fetched_at) fresh from {TABLE}
               group by 1 having max(fetched_at) < now() - (%s || ' days')::interval
               union
              select s.security_code, null from reference_data.security s
                where s.asset_type = 'stock'
                  and not exists (select 1 from {TABLE} t where t.security_code = s.security_code)
               order by fresh nulls first"""
    rows = engine.query(sql, (str(max_age_days),), role='reader')
    if rows is None:
        raise RuntimeError('该刷名单读不到（库不通或表不在）—— 不交空表，空表会被念成"全都刷新过了"')
    codes = [r['security_code'] for r in rows]
    return codes[:limit] if limit else codes


def order_refresh(primary: Sequence[str], tail: Sequence[str]) -> List[str]:
    """母体顺序：`primary`（本机名单）在前保当天新鲜，`tail`（缺口）接在后面并按各自给定顺序推进。

    纯函数、不碰库 ⇒ 能被单测钉住。为什么名单在前而不是缺口在前：缺口尾巴天天先补会让本机名单里
    当天新披露的那一期一直排不到；反过来说，尾巴里排最前的本来就是"表里一行都没有"的那批
    （`stale_codes` 的 `order by fresh nulls first`），新股与写失败的票因此不会被饿死。
    """
    seen, out = set(), []
    for c in list(primary) + list(tail):
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


GAP_SHARE = 0.35        # 预算里划给"缺口与名单外回补"的那一份（其余给本机名单）


def refresh_plan(budget_min: Optional[float], gap_share: float = GAP_SHARE,
                 gap_days: float = 60.0) -> List[tuple]:
    """两阶段的预算分配，返回 `[(阶段名, 代码, 该阶段预算分钟)]`。

    为什么必须分阶段、而不是排成一条队：**实测本机名单有 1,122 只**（1.3 秒/只 ⇒ 约 24 分钟），
    而每日预算只有 6 分钟。一条队的话，"名单在前"就永远轮不到缺口（10-16 我第一版就是这么写的，
    跑起来才发现尾巴一行都没执行），"缺口在前"又会让名单几天刷不到新披露。
    所以是：先按 `gap_share` 的预算补**缺口 + 名单外最旧的**，剩下的预算刷**名单里最旧的**。

    `gap_days=60` 是按服务速率倒推的，不是随手取的：6 分钟 × `GAP_SHARE` ≈ 2.1 分钟 ≈ 96 只/天，
    而名单外有 5,577 − 1,122 ≈ 4,455 只 ⇒ 要 60 天才轮得一遍（需求 74 只/天 < 供给 96 只/天，
    留三成余量吃新股与写失败的）。把 `gap_days` 调到 45 就会**入不敷出**：每天新越过门槛的
    有 99 只 > 能补掉的 96 只，队列只会越积越长 —— 那种"看起来在跑、其实永远追不上"的配置，
    要靠这两行算术挡住，而不是靠每天盯。
    """
    watch = set(watchlist_codes())
    missing = coverage_gap()['missing']                       # 一行都没有的（新股、整只写失败的）
    tail = order_refresh(missing, [c for c in stale_codes(gap_days) if c not in watch])
    names = [c for c in stale_codes(1.0) if c in watch and c not in set(missing)]
    budget = float(budget_min or 0.0)
    if not budget:
        return [('缺口/名单外', tail), ('本机名单', names)]
    return [('缺口/名单外', tail, round(budget * gap_share, 2)),
            ('本机名单', names, round(budget * (1.0 - gap_share), 2))]



def run_plan(phases: Sequence[tuple], *, write: bool = True, kinds=('cum', 'single'),
             max_periods: int = 0) -> Dict[str, Any]:
    """按阶段跑并合成一个汇总（形状与 `run()` 一致，`main()` 与批账都按那一份读）。"""
    total: Dict[str, Any] = {'ok': 0, 'fail': 0, 'written': 0, 'notes': [], 'failed_codes': [],
                             'left': 0, 'budget_out': False, 'seconds': 0.0, 'phases': []}
    for ph in phases:
        label, codes = ph[0], ph[1]
        minutes = ph[2] if len(ph) > 2 else None
        one = run(codes, write=write, kinds=kinds, max_periods=max_periods, budget_min=minutes)
        total['ok'] += one['ok']
        total['fail'] += one['fail']
        total['written'] += one['written']
        total['notes'].extend(one['notes'])
        total['failed_codes'].extend(one['failed_codes'])
        total['left'] += one['left']
        total['budget_out'] = total['budget_out'] or one['budget_out']
        total['seconds'] = round(total['seconds'] + one['seconds'], 1)
        total['phases'].append({'label': label, 'asked': len(codes), 'ok': one['ok'], 'fail': one['fail'],
                                'left': one['left'], 'seconds': one['seconds'],
                                'budget_min': minutes})
        print(f'  · 阶段「{label}」：要刷 {len(codes)} 只，成 {one["ok"]} 败 {one["fail"]}，'
              f'预算 {minutes if minutes is not None else "不限"} 分钟，'
              + (f'还剩 {one["left"]} 只没轮到' if one['budget_out'] else '这一档跑完了'), flush=True)
    return total


def collect(code: str, kinds: Iterable[str] = ('cum', 'single'),

            max_periods: int = 0) -> tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    errs: List[str] = []
    for kind in kinds:
        try:
            raw = _fetch_raw(code, INDICATORS[kind])
        except Exception as exc:                                # noqa: BLE001
            errs.append(f'{kind}:{type(exc).__name__} {str(exc)[:80]}')
            continue
        got = parse_rows(code, kind, raw)
        if max_periods:
            got = sorted(got, key=lambda r: r['report_date'], reverse=True)[:max_periods]
        if not got:
            errs.append(f'{kind}:空表（源没给行或报告期全解析不出，不是"这只票没数据"）')
        rows.extend(got)
    return rows, errs


def run(codes: Sequence[str], *, write: bool = True, kinds=('cum', 'single'),
        max_periods: int = 0, budget_min: Optional[float] = None) -> Dict[str, Any]:
    t0 = time.time()
    ok = fail = 0
    written = 0
    notes: List[str] = []
    failed: List[str] = []        # 逐只点名，落进批账：只打进 stdout 的失败等于没有失败
    for i, code in enumerate(codes, 1):
        if budget_min and time.time() - t0 > budget_min * 60.0:
            # `seconds` 必须带上：`main()` 最后那句汇总读它，缺了就是 KeyError ——
            # "预算用完按设计收工"会被读成"跑到一半崩了"（10-16 实测 rc=1 + traceback）。
            return {'ok': ok, 'fail': fail, 'written': written, 'notes': notes,
                    'failed_codes': failed, 'left': len(codes) - i + 1, 'budget_out': True,
                    'seconds': round(time.time() - t0, 1)}
        rows, errs = collect(code, kinds, max_periods)
        if write and rows:
            try:
                written += write_rows(rows)
            except Exception as exc:                            # noqa: BLE001
                fail += 1
                failed.append(code)
                notes.append(f'{code} 写库失败：{type(exc).__name__} {str(exc)[:120]}')
                continue
        # 一只票只有两种下场：一行都没采到＝失败（要点名）；采到了＝成功，
        # 某一档空另记一条 note（"两档只拿到一档"不是成功，但也不是这只票没数据）。
        if not rows:
            fail += 1
            failed.append(code)
            notes.append(f'{code} 一行都没采到：' + ('；'.join(errs) or '源给了空表')[:180])
        else:
            ok += 1
            if errs:
                notes.append(f'{code} 部分档没拿到：' + '；'.join(errs)[:180])
        if not write:
            latest = max((r['report_date'] for r in rows if r['period_kind'] == 'cum'), default=None)
            print(f'  [{i}/{len(codes)}] {code} 解析出 {len(rows)} 行（各档合计），'
                  f'最新报告期 {latest}，营收非空 {sum(1 for r in rows if r["revenue_yuan"] is not None)} 行'
                  + (f' ⚠️ {"; ".join(errs)[:120]}' if errs else ''), flush=True)
        elif i % 25 == 0 or i == len(codes):
            print(f'  [{i}/{len(codes)}] 已写 {written} 行，成 {ok} 败 {fail}，用时 {time.time() - t0:.0f}s', flush=True)
    return {'ok': ok, 'fail': fail, 'written': written, 'notes': notes,
            'failed_codes': failed, 'left': 0, 'budget_out': False,
            'seconds': round(time.time() - t0, 1)}



def verify() -> List[str]:
    """回查库里覆盖：别信 rc=0 —— 采集器一只都没写也算"跑完了"。"""
    bad: List[str] = []
    r = engine.query_one(
        f'select count(distinct security_code) codes, count(distinct report_date) periods, '
        f'min(report_date)::text a, max(report_date)::text z, count(*) n, '
        f'count(*) filter (where period_kind = %s) cum, '
        f'count(*) filter (where period_kind = %s) single, '
        f'count(*) filter (where revenue_yuan is null) no_rev, '
        f'max(fetched_at)::text fresh from {TABLE}', ('cum', 'single'), role='reader')
    if not r or not r.get('n'):
        return [f'{TABLE} 是空的 —— 采集器一次都没写成过']
    if not r['single']:
        bad.append('单季档一行都没有：两档只写了一档 ⇒ 季节性/环比全判不了')
    if r['no_rev'] and r['no_rev'] > r['n'] * 0.5:
        bad.append(f'{r["no_rev"]}/{r["n"]} 行营收是 NULL：多半是单位解析没吃下源的新形状')
    near5 = engine.query_one(
        f"select count(distinct security_code) c from {TABLE} "
        f"where period_kind = 'single' and report_date >= (select max(report_date) from {TABLE}) - interval '5 years'",
        (), role='reader')
    gap = coverage_gap()
    # 分母一律是**主档只数**。旧那句「5566 只（占 5566 只的 100%）」的分母是表自己，
    # 缺多少只显示满多少 —— 这就是那 11 只能静默缺一天的原因。
    master = gap['master'] or 1
    print(f"  库里：{r['codes']} 只 / {r['periods']} 期 / {r['n']} 行"
          f"（累计 {r['cum']} + 单季 {r['single']}），报告期 {r['a']} … {r['z']}，"
          f"最后采于 {r['fresh']}")
    print(f"  覆盖（分母＝主档 stock {gap['master']} 只）：表里有 {gap['have']} 只（"
          f"{gap['have'] / master * 100:.2f}%），**缺 {len(gap['missing'])} 只**"
          + (f'：{("、".join(gap["missing"][:12]))}{"…（还有 " + str(len(gap["missing"]) - 12) + " 只)" if len(gap["missing"]) > 12 else ""}'
             if gap['missing'] else ''))
    print(f"  近 5 年单季有数的票：{near5['c'] if near5 else 0} 只"
          f"（占已覆盖 {gap['have']} 只的 {(near5['c'] or 0) / max(1, gap['have']) * 100:.2f}%、"
          f"占主档 {gap['master']} 只的 {(near5['c'] or 0) / master * 100:.2f}%）")
    if gap['missing']:
        bad.append(f'{len(gap["missing"])} 只票主档里有、这张表里一行都没有 ⇒ 是"没写成"不是"市场没这只票"。'
                   '每日链那条缺口尾巴会自动往前补；要立刻补就显式跑 '
                   '`python -m ingest.jobs.c25_financials_ths --refresh-gap 60 --max-minutes 20`')
    if near5 and (near5['c'] or 0) < gap['have'] * 0.6:
        bad.append('六成以上已覆盖的票凑不满近 5 年单季 —— 预测那一块对多数票会写"判不了"，'
                   '先确认是不是只回填了新股或只跑了自选')
    return bad



def _write_batch(batch_id: Optional[int], res: Dict[str, Any], gap: Dict[str, Any],
                 kinds: Sequence[str], budget_min: Optional[float]) -> None:
    """跑完落一行 `operations.ingest_batch`：这一步在机器上第一次有账（10-16 之前是 0 行）。

    账里必须带三件事：成/败只数、**失败的代码**（截一段）、还缺几只。
    只写"跑完了"的账等于没账 —— 那 11 只当初就是 stdout 里一句 `numeric field overflow`。
    """
    if batch_id is None:
        return
    from app.db.repo import ops
    status = 'ok' if (res['fail'] == 0 and res['ok'] > 0) else ('failed' if res['ok'] == 0 else 'partial')
    phases = res.get('phases') or []
    msg = (f'成 {res["ok"]} 只 / 败 {res["fail"]} 只，写 {res["written"]} 行，'
           f'档={",".join(kinds)}，用时 {res["seconds"]}s'
           + ('；分档：' + '、'.join(f'{p["label"]} 要刷 {p["asked"]} 只/成 {p["ok"]} 败 {p["fail"]}'
                                     f'/预算 {p["budget_min"]} 分钟/剩 {p["left"]}' for p in phases)
              if phases else '')
           + (f'，预算 {budget_min} 分钟用完还剩 {res["left"]} 只' if res.get('budget_out') else '')
           + (f'；缺 {len(gap["missing"])} 只没落成：' + '，'.join(gap['missing'][:50]) if gap['missing'] else '')
           + (f'；本次失败：' + '，'.join(res['failed_codes'][:50]) if res['failed_codes'] else ''))
    try:
        ops.end_batch(batch_id, row_count=res['written'], status=status, message=msg[:1800], role='etl')
    except Exception as exc:                                    # noqa: BLE001
        # 记账失败不许把采集本身判成失败（数据已经落库了），但必须出声：
        # 无声的账本坏了，下一步就没人知道这一步有没有跑。
        print(f'  ⚠️ 批账没写成（{type(exc).__name__} {str(exc)[:120]}）—— 数据已落库，但这一步在 '
              'operations.ingest_batch 里查不到，请人工补一次或修 ops.end_batch', flush=True)


def _reap_open_batches() -> int:
    """开账之前先把这一张表上挂着的 `running` 收成 `aborted`。

    进程被杀（重启 8910、Ctrl+C、任务被停）时 `end_batch` 永远不会跑，账上就留一行
    "正在跑"——它既不红也不绿，看账的人会一直等一个已经死了的批次。
    10-16 实测：我停掉那一跑之后 `batch_id 641` 就停在 `running`、`row_count` 是空的。
    所以收口放在**下一次开账之前**（自愈），不是靠人记得去 UPDATE。
    """
    try:
        return engine.execute(
            f"update operations.ingest_batch set status = 'aborted', finished_at = now(), "
            f"message = coalesce(nullif(message,''), '') || '｜开新账时收口：上一跑没写结束（进程被杀或崩了）' "
            f"where target = '{TABLE}' and source = %s and status = 'running'",
            (f'{SOURCE}:stock_financial_abstract',), role='etl') or 0
    except Exception as exc:                                    # noqa: BLE001
        print(f'  ⚠️ 收不掉上一跑的 open 账（{type(exc).__name__} {str(exc)[:100]}）', flush=True)
        return 0


def _why_nothing_to_refresh(plan: Sequence[tuple], args) -> str:
    """rc 2 那句「没有要刷的票」必须点名到底是哪一环空的。

    10-16 实测：当天 18:39 全市场刚刷完 ⇒ 缺口 0 只、名单外 60 天门槛内 0 只、名单内 1 天门槛内也 0 只
    ⇒ 这一步 rc 2（按设计不写，是对的），而屏幕上那句「本机名单为空」是**假话**
    （`reference_data.watchlist_member_snapshot` 最新一场 1,121 只）。
    假话的代价不是难看：读的人会去查自选同步那一步，而真答案是「库里都还新，今天没东西要补」。
    """
    gap_asked = len(plan[0][1]) if plan else 0
    name_asked = len(plan[1][1]) if len(plan) > 1 else 0
    try:
        n_watch = len(watchlist_codes())
        n_missing = len(coverage_gap()['missing'])
    except Exception as exc:                                    # noqa: BLE001
        return (f'这一跑没有要刷的票（两档合计 {gap_asked + name_asked} 只），但体检读不到：'
                f'{type(exc).__name__} {str(exc)[:110]} —— 读不到不等于没有缺口')
    if not plan:
        return (f'这一跑没有要刷的票，按设计不写、不算失败。这次用的母体不是缺口模式'
                f'（--code / --all / --only-stale / 默认本机名单），而本机名单是 {n_watch} 只'
                + ('；名单确实是空的 ⇒ 要去自选同步那一步看，不是这里的缺口' if not n_watch else ''))
    return (f'这一跑没有要刷的票，按设计不写、不算失败。三档分别：'
            f'①表里一行都没有的 {n_missing} 只；'
            f'②名单外且 {int(args.refresh_gap or 0)} 天没采的 {gap_asked} 只；'
            f'③本机名单 {n_watch} 只里超过 1 天没采的 {name_asked} 只。'
            + ('②③都是 0 ⇒ 库里这些票都还在门槛内（刚刚刷过），越过门槛那几晚自动补。'
               if not gap_asked and not name_asked else '')
            + ('⚠️ 本机名单是 0 只（那张表最新一场没有行）⇒ 要去自选同步那一步看。'
               if not n_watch else ''))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='个股财务多期（同花顺，累计档 + 单季档）')
    ap.add_argument('--code', action='append', default=[], help='可重复或逗号分隔')
    ap.add_argument('--all', action='store_true', help='全市场（谨慎：约 100 分钟）')
    ap.add_argument('--only-stale', type=float, metavar='DAYS', help='只刷超过 N 天没采的票')
    ap.add_argument('--refresh-gap', type=float, nargs='?', const=60.0, default=None, metavar='DAYS',
                    help='每日链用的那份母体：预算分两档——先补"表里一行都没有的 + 名单外最旧的"，'
                         '剩下的刷"名单里最旧的" ⇒ 缺口当天吃掉、名单与全市场各自按轮次推进。'
                         'DAYS＝名单外多久算旧，默认 60（按服务速率倒推，见 refresh_plan）')
    ap.add_argument('--gap-share', type=float, default=GAP_SHARE,
                    help=f'预算里划给缺口/名单外那一份的比例（默认 {GAP_SHARE}，其余给本机名单）')
    ap.add_argument('--gap-limit', type=int, default=0, help='缺口那一档最多补几只（0＝不设，只受预算管）')
    ap.add_argument('--limit', type=int, default=0, help='最多刷几只')
    ap.add_argument('--max-periods', type=int, default=0, help='每档只留最近 N 期（0＝全留）')
    ap.add_argument('--kinds', default='cum,single', help='cum / single，逗号分隔')
    ap.add_argument('--max-minutes', type=float, help='自限预算，到点停并交还剩几只')
    ap.add_argument('--dry-run', action='store_true', help='只解析不写库')
    ap.add_argument('--verify', action='store_true', help='回查库里覆盖')
    ap.add_argument('--no-batch', action='store_true', help='不落 operations.ingest_batch（调试/回放用）')
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        bad = verify()
        print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 覆盖与两档齐')
        return 1 if bad else 0

    codes: List[str] = []
    plan: List[tuple] = []
    for item in args.code:
        codes += [_full(x) for x in str(item).replace(';', ',').split(',') if x.strip()]
    if args.refresh_gap and not codes:
        plan = refresh_plan(args.max_minutes, gap_share=args.gap_share, gap_days=args.refresh_gap)
        if args.gap_limit and plan and len(plan[0][1]) > args.gap_limit:
            plan[0] = tuple([plan[0][0], plan[0][1][:args.gap_limit]] + list(plan[0][2:]))
        codes = [c for _, *rest in plan for c in (rest[0] if rest else [])]
    if args.only_stale and not codes:
        codes = stale_codes(args.only_stale, args.limit)
    if not codes and args.all:
        codes = all_stock_codes()
    if not codes and not args.only_stale and not args.refresh_gap:
        codes = watchlist_codes()
    codes = [c for c in codes if c]
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print(_why_nothing_to_refresh(plan, args), flush=True)
        return 2

    kinds = tuple(k.strip() for k in args.kinds.split(',') if k.strip() in INDICATORS) or ('cum', 'single')
    print(f'财务多期 spec：源={SOURCE} 档={",".join(kinds)}，{len(codes)} 只'
          + (f'（两档：' + '、'.join(f'{p[0]} {len(p[1])} 只/预算 {p[2]} 分钟' for p in plan)
             if plan else '')
          + ('（--dry-run 不写库）' if args.dry_run else ''), flush=True)

    batch_id: Optional[int] = None
    if not args.dry_run and not args.no_batch:
        # 先收上一跑的 open 账，再开新账：进程被杀时 end_batch 永远不执行，账上留一行
        # 「正在跑」既不红也不绿，看账的人会等一个已经死了的批次（10-16 实测 batch_id 641）。
        reaped = _reap_open_batches()
        if reaped:
            print(f'  ↺ 收掉上一跑没写结束的批账 {reaped} 行 → aborted（不是失败，是没跑完）', flush=True)
        try:
            from app.db.repo import ops
            batch_id = ops.begin_batch(
                'ths:stock_financial_abstract', TABLE,
                source_path=f'indicator={",".join(INDICATORS[k] for k in kinds)}&母体='
                            f'{"gap" if args.refresh_gap else ("stale" if args.only_stale else "all" if args.all else "watchlist")}',
                role='etl')
        except Exception as exc:                                # noqa: BLE001
            print(f'  ⚠️ 批账开不开（{type(exc).__name__} {str(exc)[:120]}）：这一跑的结果只进 stdout，'
                  'operations.ingest_batch 里会查不到这一步 —— 见本文件 _write_batch 上面那段注释', flush=True)
    res = run_plan(plan, write=not args.dry_run, kinds=kinds, max_periods=args.max_periods) if plan \
        else run(codes, write=not args.dry_run, kinds=kinds,
                 max_periods=args.max_periods, budget_min=args.max_minutes)
    for n in res['notes']:
        print(f'  ⚠️ {n}')
    if res['budget_out']:
        print(f'  ⏱ 预算 {args.max_minutes} 分钟用完：还有 {res["left"]} 只没轮到（明天这一档接着补，'
              '缺口与最旧的永远排在各档最前面）')

    print(f'  写入 {res["written"]} 行，成 {res["ok"]} 只 / 败 {res["fail"]} 只，用时 {res["seconds"]}s')
    if args.dry_run:
        return 0
    bad = verify()
    try:
        gap = coverage_gap()
    except Exception as exc:                                    # noqa: BLE001
        print(f'  ⚠️ 覆盖率闸读不到（{type(exc).__name__} {str(exc)[:120]}）—— 报不出缺口，'
              '不等于没有缺口', flush=True)
        gap = {'master': 0, 'have': 0, 'missing': []}
    _write_batch(batch_id, res, gap, kinds, args.max_minutes)
    for b in bad:
        print(f'  ✗ {b}')
    if res['fail'] and not res['ok']:
        return 1
    # 缺口折进 rc 3：这一档会被失败单步重试阶梯按点重跑，于是"缺 N 只"是**会被自动吃掉的状态**，
    # 不是等人去 grep 日志的状态。全市场都补齐后 rc 自己回到 0。
    if res['fail'] or gap['missing']:
        return 3
    if res['budget_out']:
        return 4
    return 0



if __name__ == '__main__':
    raise SystemExit(main())
