# -*- coding: utf-8 -*-
"""D7 · 每日复盘（18:18 盘后那一栏）。

按用户 11-03 定的分层：「判断与取数属于数据加工环节，是取数落地的；跟推送到飞书的那些
行为分开」。所以 `collect()` 只读库里**当天已落地**的东西（`analytics.*`／`market_data.*`／
`reference_data.*`，全部 `role='reader'`），一条源站都不碰；要新取数就另写一炉入库。

正文回答复盘七问（框架＝市场发生了什么 → 为什么 → 明天准备怎么做）：
  一、量能与指数        两市成交额（≥1万亿活跃／7000亿—1万亿震荡／<7000亿冰点）+ 三大指数方向
  二、赚钱效应          涨跌停/炸板/高度/涨跌家数/中位涨幅/阿姆氏 + 昨日涨停的晋级与红盘
  三、情绪周期          冰点／发酵／高潮／分歧退潮／中性（阈值规则贴标签，非预测）
  四、主线与赚钱效应    涨停概念归组（与午间同一口径：概念型、剔成员>1500 泛标签）
                        + 板块涨幅榜 + 高位涨停题材归因（源站 reason）
  五、连板梯队          趋势还是连板、分歧还是一致（开板/炸板率/晋级率，阈值规则）
  六、明日观察点        条件句观察（若 A 则看 B），不预测不给建议；仓位倾向按框架档位

判读纪律（与午间复盘同一套）：**缺的那格说"缺数"，不写 0、不拿昨天顶**；
段落纯文本、不打分、不预测、不给买卖建议 —— 阈值贴的那句永远带"（阈值规则，非预测）"。

账户复盘（我赚在哪/亏在哪）不在这一路：库里没有交易日志表，这一栏留给你自己填。

跑法：
    python -m ingest.jobs.push.t09_daily_review --dry-run   # 只算只排版，不写账不发送
    python -m ingest.jobs.push.t09_daily_review             # 排版 + 落留痕（不发）
    python -m ingest.jobs.push.t09_daily_review --send      # 真发（渠道读系统配置那一格）
    python -m ingest.jobs.push.t09_daily_review --send --resend  # 补发今天已落库的正文（不重新取数）
    python -m ingest.jobs.push.t09_daily_review --force     # 跳过交易日判断（周末手工试）

调度：Windows 任务计划程序每天 18:18 起这一炉（时点在系统配置「盘中播报」那一屏改）。
18:18 在盘后，所以这里**不判交易时段**，只判交易日（非交易日静默退，不发"今天没复盘"）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # push/ → jobs/ → ingest/ → 仓库根（挪目录时这一格最容易数错）
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                           # noqa: E402
from app.intraday import sources, store, tasks, vol_snap             # noqa: E402
from ingest.jobs.push import t01_zt_watch as zt_watch                                    # noqa: E402  交易日判据共用一份

#: 涨停价/跌停价与收盘价的容差（元）：唯一来源 `app.compute.limit_prices.LIMIT_EPS`（半分），
#: 与 `app/compute/breadth.py` 同一把尺 —— 手抄 0.01 已经漂过一次（见那边注释）。
from app.compute.limit_prices import LIMIT_EPS                    # noqa: E402

TPL_FILE = ROOT / 'app' / 'intraday' / 'templates.json'
CFG_FILE = ROOT / 'app' / 'intraday' / 'broadcast_config.json'
TASK = 'daily_review'
DRY_RUN = False

#: 复盘的四个指数（用户框架：上证、创业板、科创50；北证50 一并带上）。
#: `pre_close` 那一列在 index_daily 里是空的，涨跌幅用**表内前一场收盘**自算（同表两行，不是取数）。
IDX_CODES = ('000001.SH', '399006.SZ', '000688.SH', '899050.BJ')
IDX_NAMES = {'000001.SH': '上证', '399006.SZ': '创业板', '000688.SH': '科创50',
             '899050.BJ': '北证50'}


def _log(now, what, **kw):
    if DRY_RUN:
        return 0
    try:
        store.write_broadcast(now, task=TASK, what=what, **kw)
    except Exception:  # noqa: BLE001  留痕写不进去不该把今天这一栏带走
        pass


def _tpl() -> dict:
    """读文案模板。读不出来就**抛** —— 用一套编出来的措辞去播，等于把口径改掉。"""
    return json.loads(TPL_FILE.read_text(encoding='utf-8'))


def _cfg() -> dict:
    return json.loads(CFG_FILE.read_text(encoding='utf-8'))


def _money(v) -> str:
    """元 → 可读（亿/万），None 不念。封单额那一格专用。"""
    if v is None:
        return ''
    v = float(v)
    if abs(v) >= 1e8:
        return f'{v / 1e8:.1f}亿'
    if abs(v) >= 1e4:
        return f'{v / 1e4:.0f}万'
    return f'{v:.0f}元'


def _amt(v) -> float | None:
    return None if v is None else float(v)


def _pct_chg(close, prev_close) -> float | None:
    """两格都在库里（同表前后两场），算差不是取数。任一缺就是缺数。"""
    if close is None or prev_close in (None, 0):
        return None
    return (float(close) / float(prev_close) - 1.0) * 100.0


def _band_label(p: float | None, band: float = 0.10) -> str:
    """±10% 带内算持平（与 `app/intraday/volume.py::label` 同一把尺）。"""
    if p is None:
        return '缺数'
    if p >= band:
        return '放量'
    if p <= -band:
        return '缩量'
    return '持平'


# ─────────────────────────────────────────────── 各节的取数（只读库里已落地的）

def _volume_lines(day: dt.date) -> list[str]:
    """一、量能与指数：两市成交额 + 三/四大指数方向。"""
    t = _tpl()
    sd_day = engine.scalar(
        'SELECT max(trade_date) FROM market_data.stock_daily WHERE trade_date <= %s',
        (day,), role='reader')
    if sd_day != day:
        return [t['daily_review_volume_missing'].format(d=day.isoformat())]
    # 前一场 ＝ `LAG` 取"库里上一场有日线的交易日"。⚠️ **窗口必须在外层过滤之前算**：
    # PG 的 WHERE 先于窗口函数求值，把 `WHERE trade_date = %s` 直接贴在同一层会把 LAG
    # 的可见行砍成一行 ⇒ prev_amt 永远是 NULL（2026-11-04 实测踩过，探查 6/7 对出）。
    row = engine.query_one(
        """
        WITH tot AS (
            SELECT trade_date, sum(amount_yuan) amt
              FROM market_data.stock_daily
             GROUP BY trade_date
        ),
        w AS (
            SELECT trade_date, amt, lag(amt) OVER (ORDER BY trade_date) prev_amt
              FROM tot
        )
        SELECT * FROM w WHERE trade_date = %s
        """, (day,), role='reader') or {}
    amt, prev = _amt(row.get('amt')), _amt(row.get('prev_amt'))
    if amt is None:
        return [t['daily_review_volume_missing'].format(d=day.isoformat())]
    cfg = _cfg()
    yi = amt / 1e8
    if yi >= float(cfg.get('daily_review_active_amt_yi', 10000)):
        tag = '活跃市（≥1万亿）'
    elif yi >= float(cfg.get('daily_review_range_amt_yi', 7000)):
        tag = '震荡市（7000亿—1万亿）'
    else:
        tag = '冰点市（<7000亿）'
    cmp = (f'{amt / prev - 1:+.1%} {_band_label(amt / prev - 1)}'
           if prev else '缺数')
    lines = [t['daily_review_volume'].format(
        amt=f'{yi:,.0f}亿', cmp=cmp, tag=tag)]

    idx_rows = engine.query(
        """
        SELECT a.security_code, a.close, p.close prev_close
          FROM market_data.index_daily a
          LEFT JOIN market_data.index_daily p
            ON p.security_code = a.security_code
           AND p.trade_date = (SELECT max(i.trade_date) FROM market_data.index_daily i
                               WHERE i.security_code = a.security_code
                                 AND i.trade_date < a.trade_date)
         WHERE a.security_code = ANY(%s::text[])
           AND a.trade_date = (SELECT max(trade_date) FROM market_data.index_daily
                               WHERE trade_date <= %s)
        """, (list(IDX_CODES), day), role='reader') or []
    latest = engine.scalar('SELECT max(trade_date) FROM market_data.index_daily WHERE trade_date <= %s',
                           (day,), role='reader')
    if latest != day:
        lines.append(t['daily_review_index_missing'].format(d=day.isoformat()))
        return lines
    parts = []
    for code in IDX_CODES:
        r = next((x for x in idx_rows if x['security_code'] == code), None)
        if not r:
            continue
        pct = _pct_chg(r['close'], r['prev_close'])
        if pct is None:
            parts.append(f"{IDX_NAMES[code]}缺数、")
        else:
            parts.append(t['daily_review_index_row'].format(name=IDX_NAMES[code], pct=pct))
    lines.append(t['daily_review_index'].format(rows=''.join(parts).rstrip('、') or '缺数'))
    return lines


def _breadth_row(day: dt.date, strict: bool = True) -> dict | None:
    """market_breadth 当日那行；strict=True 时场次不是今天就返回 None（缺数）。"""
    row = engine.query_one(
        'SELECT * FROM analytics.market_breadth WHERE trade_date = %s', (day,), role='reader')
    if row:
        return row
    if not strict:
        return None
    return None


def _prev_breadth(day: dt.date) -> tuple[dt.date | None, dict | None]:
    row = engine.query_one(
        'SELECT * FROM analytics.market_breadth'
        ' WHERE trade_date = (SELECT max(trade_date) FROM analytics.market_breadth'
        '                     WHERE trade_date < %s)', (day,), role='reader')
    return (row or {}).get('trade_date'), row


def _pools_lines(day: dt.date) -> list[str]:
    """二、赚钱效应：涨跌停读数 + 宽度 + 昨日涨停的晋级/红盘 + 昨日高位票的风险侧。"""
    t = _tpl()
    today = _breadth_row(day)
    if today is None:
        latest = engine.scalar('SELECT max(trade_date) FROM analytics.market_breadth'
                               ' WHERE trade_date <= %s', (day,), role='reader')
        return [t['daily_review_pools_missing'].format(d=str(latest or '缺场次'))]
    prev_day, prev = _prev_breadth(day)
    zt, dt_n = today['limit_up_count'], today['limit_down_count']
    broken = today['broken_count']
    zb_pct = float(today['broken_rate']) if today['broken_rate'] is not None else None
    seal_pct = (100.0 - zb_pct) if zb_pct is not None else None
    lines = [t['daily_review_pools'].format(
        zt=zt, dt=dt_n, zb=broken,
        zb_pct=f'{zb_pct:.0f}%' if zb_pct is not None else '缺数',
        seal_pct=f'{seal_pct:.0f}%' if seal_pct is not None else '缺数',
        hb=today['highest_board'] if today['highest_board'] is not None else '?')]
    if today['up_count'] is not None and today['down_count'] is not None:
        lines.append(t['daily_review_breadth'].format(
            up=today['up_count'], down=today['down_count'],
            ratio=(f"{today['up_ratio']:.0f}" if today['up_ratio'] is not None else '缺数'),
            median=(f"{float(today['median_pct']):+.2f}"
                     if today['median_pct'] is not None else '缺数'),
            arms=(f'{float(today["arms_ratio"]):.2f}'
                  if today['arms_ratio'] is not None else '缺数')))
    # 昨日涨停的今日脸色 —— **全部走本地涨停池口径**（与下面高位票那一行同一母体），
    # 不混用 market_breadth 内部那场回算的数（它按当时落库的判据算，母体可能不同）。
    if prev_day is not None:
        prom = engine.query_one(
            """
            WITH prev_pool AS (
                SELECT distinct left(l.security_code, 6) c6
                  FROM analytics.limit_up_pool_daily l
                 WHERE l.trade_date = %s AND l.sealed),
            today AS (
                SELECT left(security_code, 6) c6, pct_change,
                       (limit_up IS NOT NULL AND close >= limit_up - %s::numeric) is_zt
                  FROM market_data.stock_daily WHERE trade_date = %s)
            SELECT count(*) n,
                   count(*) filter (where t.is_zt) promoted,
                   count(*) filter (where t.pct_change > 0) red
              FROM prev_pool p LEFT JOIN today t ON t.c6 = p.c6
            """, (prev_day, LIMIT_EPS, day), role='reader') or {}
        if prom and prom.get('n'):
            lines.append(t['daily_review_prev'].format(
                d=prev_day, n=prom['n'], promoted=prom['promoted'] or 0,
                promo=f"{prom['promoted'] / prom['n'] * 100:.0f}",
                red=prom['red'] or 0, red_pct=f"{prom['red'] / prom['n'] * 100:.0f}"))
    # 昨日高位（≥2 连板）票今天的脸色 —— 亏钱效应有没有在高位票身上扩散
    hi = engine.query(
        """
        SELECT h.name, h.c6, t.pct_change,
               (t.limit_down IS NOT NULL AND t.close <= t.limit_down + %s::numeric) is_dt
          FROM (SELECT left(l.security_code, 6) c6, l.security_code, s.name
                 FROM analytics.limit_up_pool_daily l
                 LEFT JOIN reference_data.security s ON s.security_code = l.security_code
                WHERE l.trade_date = %s AND l.sealed AND l.consec >= 2) h
          LEFT JOIN market_data.stock_daily t
            ON t.security_code = h.security_code AND t.trade_date = %s
         ORDER BY is_dt DESC, h.c6
        """, (LIMIT_EPS, prev_day, day), role='reader') or []
    if not hi:
        lines.append(t['daily_review_risk_missing'].format(d=str(prev_day or '缺场次')))
        return lines
    else:
        dt_rows = [r for r in hi if r['is_dt']]
        dt_names = '、'.join(f"{r['name'] or r['c6']}" for r in dt_rows[:6]) or '无'
        if len(dt_rows) >= 3:
            lines.append(t['daily_review_risk'].format(
                n=len(hi), dt_n=len(dt_rows),
                names='、'.join(f"{r['name'] or r['c6']}" for r in dt_rows[:6])))
        else:
            lines.append(t['daily_review_risk_ok'].format(
                n=len(hi), dt_n=len(dt_rows), names=dt_names))
    return lines


def _stage_lines(day: dt.date) -> list[str]:
    """三、情绪周期：当日 vs 昨场 的阈值规则贴标签（非预测）。"""
    t = _tpl()
    today = _breadth_row(day)
    if today is None:
        return [t['daily_review_stage_missing']]
    prev_day, prev = _prev_breadth(day)
    if prev is None:
        return [t['daily_review_stage_missing']]
    zt, pzt = today['limit_up_count'], prev['limit_up_count']
    hb = today['highest_board'] or 0
    zb_rate = float(today['broken_rate']) if today['broken_rate'] is not None else None
    promo = float(prev['promotion_rate']) if prev['promotion_rate'] is not None else None
    cfg = _cfg()
    hi_dt_n = engine.scalar(
        """
        SELECT count(*) FROM analytics.limit_up_pool_daily h
          JOIN market_data.stock_daily t ON t.security_code = h.security_code
                                        AND t.trade_date = %s
         WHERE h.trade_date = %s AND h.sealed AND h.consec >= 2
           AND t.limit_down IS NOT NULL AND t.close <= t.limit_down + %s::numeric
        """, (day, prev_day, LIMIT_EPS), role='reader') or 0
    if zt < int(cfg.get('daily_review_frozen_zt_n', 30)) and hb <= 3:
        stage, ev = '冰点', f'涨停 {zt} 家（<{cfg.get("daily_review_frozen_zt_n", 30)}）且高度 ≤3 板'
    elif zt < pzt * float(cfg.get('daily_review_fade_zt_ratio', 0.7)) and (
            (zb_rate is not None and zb_rate >= float(cfg.get('daily_review_broken_hot_pct', 30)))
            or hi_dt_n >= 3):
        stage, ev = '分歧/退潮', (
            f'涨停锐减 {pzt}→{zt}'
            + (f'、炸板率 {zb_rate:.0f}% ≥30%' if zb_rate is not None
               else '') + (f'、昨日高位今日跌停 {hi_dt_n} 家' if hi_dt_n >= 3 else ''))
    elif promo is not None and promo >= float(cfg.get('daily_review_promote_hot_pct', 60)) \
            and zt > pzt:
        stage, ev = '高潮/发酵', f'晋级率 {promo:.0f}% ≥60% 且涨停增多 {pzt}→{zt}'
    elif zt >= pzt * float(cfg.get('daily_review_repair_zt_ratio', 1.3)):
        stage, ev = '启动/发酵（修复）', f'涨停 {pzt}→{zt}（≥前场 130%）'
    else:
        stage, ev = '中性/震荡', f'涨停 {pzt}→{zt}、高度 {prev["highest_board"]}→{hb}'
        if zb_rate is not None:
            ev += f'、炸板率 {zb_rate:.0f}%'
    return [t['daily_review_stage'].format(stage=stage, evidence=ev)]


def _dedup_groups(groups: list[dict]) -> list[dict]:
    """按**成员重合度**去重：同一个概念在 TDX_/THS_ 两套体系下有两个名字，
    且成员集**不完全相同**（实测 11-04「芯片」47 只 vs「芯片概念」47 只、各差 2 只），
    精确集合相等去不掉 ⇒ 用包含度 |A∩B| / min(|A|,|B|) ≥ 0.85 判重。
    输入已按命中家数降序，先到的名字留下（榜上不会两个名字念一遍同一群人）。"""
    kept: list[tuple[set, dict]] = []
    for g in groups:
        ms = set(g.get('members') or ())
        dup = False
        for k_ms, _k_g in kept:
            inter = len(ms & k_ms)
            small = min(len(ms), len(k_ms)) or 1
            if inter / small >= 0.85:
                dup = True
                break
        if not dup:
            kept.append((ms, g))
    return [g for _ms, g in kept]


def _mainline_lines(day: dt.date) -> tuple[list[str], list[dict] | None]:
    """四、主线与赚钱效应所在：涨停概念归组 + 板块涨幅榜 + 题材归因 + 消息面（缺）。

    返回 (正文行, 归组清单) —— 归组清单给第五节判"风格"用，不再查第二遍库。
    """
    t = _tpl()
    cfg = _cfg()
    day_row = engine.scalar(
        'SELECT max(trade_date) FROM analytics.limit_up_pool_daily WHERE trade_date <= %s',
        (day,), role='reader')
    out: list[str] = []
    groups: list[dict] | None = None
    if day_row != day:
        out.append('涨停概念归组缺数（limit_up_pool_daily 最新场次 '
                   f'{day_row} 不是今天）')
    else:
        sealed = engine.query(
            'SELECT security_code FROM analytics.limit_up_pool_daily'
            ' WHERE trade_date = %s AND sealed ORDER BY security_code',
            (day,), role='reader') or []
        codes6 = sorted({r['security_code'][:6] for r in sealed})
        want = int(cfg.get('daily_review_group_top', 5))
        # 多取一些再按成员集合去重：TDX_/THS_ 两套名字会各占一份名额（见 _dedup_groups）
        groups, _un = sources.sector_groups(
            codes6, max_members=int(cfg.get('max_members', 1500)),
            min_hits=2, limit=max(want * 4, 20))
        groups = _dedup_groups(groups)[:want]
        by_code = {}
        for r in engine.query(
                """
                SELECT left(l.security_code, 6) c6, s.name, l.consec, l.order_amount
                  FROM analytics.limit_up_pool_daily l
                  LEFT JOIN reference_data.security s ON s.security_code = l.security_code
                 WHERE l.trade_date = %s AND l.sealed
                """, (day,), role='reader') or []:
            by_code[r['c6']] = r
        n_total = len(codes6)
        if groups:
            for i, g in enumerate(groups, 1):
                hit = sorted(
                    (by_code[c] for c in g['members'] if c in by_code),
                    key=lambda r: (-(r.get('consec') or 0),
                                    -float(r.get('order_amount') or 0)))
                if len(hit) < 2:
                    continue
                share = len(hit) / n_total if n_total else 0.0
                tier = ('主线候选' if share >= 0.40 else
                        '辅线' if share >= 0.20 else '点状')
                reps = '、'.join(
                    f"{r['name'] or r['c6']}{int(r['consec'])}连板"
                    for r in hit[:3])
                out.append(t['daily_review_group'].format(
                    i=i, name=g['name'], n=len(hit), share=f'{share:.0%}',
                    tier=tier, top_lbc=int(hit[0].get('consec') or 0), reps=reps))
            if not out:
                out.append(t['daily_review_groups_none'])
        else:
            out.append(t['daily_review_groups_none'])
        # 高位涨停的题材归因（源站 reason：编辑归因、非公司确认）
        reasons = [r for r in engine.query(
                """
                SELECT s.name, l.consec, l.reason
                  FROM analytics.limit_up_pool_daily l
                  LEFT JOIN reference_data.security s ON s.security_code = l.security_code
                 WHERE l.trade_date = %s AND l.sealed AND l.consec >= 2 AND l.reason IS NOT NULL
                 ORDER BY l.consec DESC, l.order_amount DESC NULLS LAST
                 LIMIT 3
                """, (day,), role='reader') or []]
        if reasons:
            out.append(t['daily_review_reason'].format(
                rows='；'.join(f"{r['name'] or '?'}（{int(r['consec'])}连板）{r['reason']}"
                               for r in reasons)))
        else:
            out.append(t['daily_review_reason_missing'])
    out.append(t['daily_review_news'])
    # 板块涨幅榜（概念，今日）+ 两日连涨标记（趋势信号）
    sd_day = engine.scalar('SELECT max(trade_date) FROM market_data.sector_daily'
                           ' WHERE trade_date <= %s', (day,), role='reader')
    if sd_day != day:
        out.append(t['daily_review_sectors_missing'].format(d=str(sd_day or '缺场次')))
    else:
        sd_prev = engine.scalar('SELECT max(trade_date) FROM market_data.sector_daily'
                               ' WHERE trade_date < %s', (day,), role='reader')
        top = engine.query(
            """
            SELECT i.name, d.sector_code, d.pct_change,
                   (SELECT d2.pct_change FROM market_data.sector_daily d2
                     WHERE d2.sector_code = d.sector_code AND d2.trade_date = %s) prev_pct
              FROM market_data.sector_daily d
              JOIN reference_data.sector_info i USING (sector_code)
             WHERE d.trade_date = %s AND i.sector_type = '概念'
               AND coalesce(i.is_active, true) AND d.pct_change IS NOT NULL
             ORDER BY d.pct_change DESC
             LIMIT 60
            """, (sd_prev, day), role='reader') or []
        pref = {'TDX_': 0, 'THS_': 1, 'GN_': 2}
        best: dict[str, dict] = {}
        for r in top:
            rank = pref.get(str(r['sector_code'])[:3], 9)
            cur = best.get(r['name'])
            if cur is None or rank < cur[2]:
                best[r['name']] = (r, rank)
        ranked = [v[0] for v in sorted(
            best.values(), key=lambda x: (-float(x[0]['pct_change']), x[1]))
            [: int(cfg.get('daily_review_sectors_top', 5))]]
        parts = []
        for r in ranked:
            streak = ''
            if r['prev_pct'] is not None and float(r['prev_pct']) > 0:
                streak = '（两日连涨）'
            parts.append(t['daily_review_sector_row'].format(
                name=r['name'], pct=float(r['pct_change']), streak=streak))
        out.append(t['daily_review_sectors'].format(
            rows=''.join(parts).rstrip('、') or '无'))
    return out, groups


def _ladder_lines(day: dt.date, groups: list[dict] | None) -> list[str]:
    """五、连板梯队：最高标/分布/开板 + 分歧一致判读 + 趋势还是连板。"""
    t = _tpl()
    today = _breadth_row(day)
    if today is None:
        return ['连板梯队缺数（market_breadth 没有今天场次）']
    hi_rows = engine.query(
        """
        SELECT left(l.security_code, 6) c6, s.name, l.consec, l.order_amount,
               l.reopen_count, l.board_kind, l.first_seal_at
          FROM analytics.limit_up_pool_daily l
          LEFT JOIN reference_data.security s ON s.security_code = l.security_code
         WHERE l.trade_date = %s AND l.sealed AND l.consec >= 2
         ORDER BY l.consec DESC, l.order_amount DESC NULLS LAST
        """, (day,), role='reader') or []
    zt = today['limit_up_count']
    n2, n3 = today['board2_count'] or 0, today['board3_count'] or 0
    n4 = today['board4plus_count'] or 0
    n1 = max(zt - n2 - n3 - n4, 0)
    hb = today['highest_board'] or 0
    tops = [r for r in hi_rows if (r['consec'] or 0) == hb][:3]
    top_names = '、'.join(f"{r['name'] or r['c6']}（{_money(r.get('order_amount'))}）"
                          .replace('（）', '') for r in tops) or '缺数'
    lines = [t['daily_review_ladder'].format(
        hb=hb, top_names=top_names, n3=n3, n2=n2, n1=n1)]
    cfg = _cfg()
    n_hi = len(hi_rows)
    if n_hi:
        reopens = [r for r in hi_rows if (r['reopen_count'] or 0) > 0]
        total_re = sum(r['reopen_count'] or 0 for r in hi_rows)
        detail = ('，'.join(f"{r['name'] or r['c6']} 开 {r['reopen_count']} 次"
                            for r in reopens[:5]))
        lines.append(t['daily_review_ladder_reopen'].format(
            n=total_re,
            detail=f'（{detail}）' if detail else '（无开板）'))
        broken_pct = float(today['broken_rate']) if today['broken_rate'] is not None else None
        promo = None
        if today['promotion_rate'] is not None:
            promo = float(today['promotion_rate'])
        broken_hot = float(cfg.get('daily_review_broken_hot_pct', 30))
        if not reopens and (broken_pct is None or broken_pct < broken_hot):
            label, ev = '一致', f'高位 {n_hi} 只无开板' + (
                f'、炸板率 {broken_pct:.0f}% <{broken_hot:.0f}%' if broken_pct is not None else '')
        elif (reopens and len(reopens) * 2 >= n_hi) or (
                broken_pct is not None and broken_pct >= broken_hot):
            label, ev = '分歧', (
                f'高位 {len(reopens)}/{n_hi} 只有开板过' if reopens else '') + (
                f'、炸板率 {broken_pct:.0f}% ≥{broken_hot:.0f}%'
                if broken_pct is not None and broken_pct >= broken_hot else '')
        else:
            label, ev = '混合', (
                f'开板 {len(reopens)}/{n_hi} 只' if reopens else '') + (
                f'、炸板率 {broken_pct:.0f}%' if broken_pct is not None else '')
            if promo is not None and promo < 30:
                ev += f'、晋级率 {promo:.0f}% <30%（接力弱）'
        lines.append(t['daily_review_ladder_label'].format(label=label, evidence=ev))
    # 风格：趋势还是连板
    top_share, top_lbc = 0.0, 0
    if groups:
        by_code = {}
        for r in engine.query(
                'SELECT left(security_code, 6) c6, consec FROM analytics.limit_up_pool_daily'
                ' WHERE trade_date = %s AND sealed', (day,), role='reader') or []:
            by_code[r['c6']] = r
        g0 = groups[0]
        hit = [c for c in g0['members'] if c in by_code]
        top_share = len(hit) / max(zt, 1)
        top_lbc = max((by_code[c].get('consec') or 0) for c in hit) if hit else 0
    streak2 = 0
    rows3 = engine.query(
        """
        SELECT i.name, d.pct_change,
               (SELECT d2.pct_change FROM market_data.sector_daily d2
                 WHERE d2.sector_code = d.sector_code
                   AND d2.trade_date = (SELECT max(trade_date) FROM market_data.sector_daily
                                         WHERE trade_date < %s)) prev_pct
          FROM market_data.sector_daily d
          JOIN reference_data.sector_info i USING (sector_code)
         WHERE d.trade_date = (SELECT max(trade_date) FROM market_data.sector_daily
                                WHERE trade_date <= %s)
           AND i.sector_type = '概念' AND coalesce(i.is_active, true)
           AND d.pct_change IS NOT NULL
         ORDER BY d.pct_change DESC LIMIT 3
        """, (day, day), role='reader') or []
    streak2 = sum(1 for r in rows3
                  if r['prev_pct'] is not None and float(r['prev_pct']) > 0)
    first_ratio = (n1 / zt) if zt else 0.0
    promo = float(today['promotion_rate']) if today['promotion_rate'] is not None else None
    if streak2 >= 2:
        style, ev = '趋势主导', f'涨幅榜前 3 概念中 {streak2} 个两日连涨（板块趋势在）'
    elif top_share >= 0.40 and top_lbc >= 3:
        style, ev = '主线连板', f'第一概念占涨停 {top_share:.0%} 且带 {top_lbc} 板龙头'
    elif promo is not None and promo >= 50 and hb >= 4:
        style, ev = '连板主导', f'晋级率 {promo:.0f}% ≥50% 且高度 {hb} 板'
    elif first_ratio >= 0.8:
        style, ev = '首板扩散', f'首板占涨停 {first_ratio:.0%}（赚钱效应偏广度，不是高度）'
    else:
        style, ev = '轮动/无明显主线', f'首板占 {first_ratio:.0%}、最高概念占涨停 {top_share:.0%}'
    lines.append(t['daily_review_style'].format(style=style, evidence=ev))
    return lines


def _watch_lines(day: dt.date) -> list[str]:
    """六、明日观察点：条件句（若 A 则看 B），不预测；仓位倾向按用户框架的档位念。"""
    t = _tpl()
    out: list[str] = []
    today = _breadth_row(day)
    if today is not None:
        hb = today['highest_board'] or 0
        top = engine.query_one(
            """
            SELECT s.name, l.consec, l.order_amount, l.reopen_count
              FROM analytics.limit_up_pool_daily l
              LEFT JOIN reference_data.security s ON s.security_code = l.security_code
             WHERE l.trade_date = %s AND l.sealed AND l.consec = (
                     SELECT max(consec) FROM analytics.limit_up_pool_daily
                      WHERE trade_date = %s AND sealed)
             ORDER BY l.order_amount DESC NULLS LAST LIMIT 1
            """, (day, day), role='reader')
        if top:
            out.append(t['daily_review_watch'].format(
                text=f"最高标 {top['name'] or '缺数'}（{int(top['consec'])} 板、"
                     f"封单 {_money(top.get('order_amount'))}）：开盘竞价承接不弱则梯队续命，"
                     f"竞价大面/炸板则高低切换（观察口径，非预测）"))
        group = engine.query_one(
            """
            SELECT g.name, count(distinct left(m.security_code, 6)) n
              FROM (SELECT left(l.security_code, 6) c
                      FROM analytics.limit_up_pool_daily l
                     WHERE l.trade_date = %s AND l.sealed) g0
              JOIN reference_data.sector_member m
                ON left(m.security_code, 6) = g0.c
              JOIN reference_data.sector_info g USING (sector_code)
             WHERE g.sector_type = '概念' AND coalesce(g.is_active, true)
               AND m.sector_code NOT IN (SELECT sector_code
                                           FROM reference_data.sector_member
                                          GROUP BY 1 HAVING count(distinct security_code) > 1500)
             GROUP BY g.name ORDER BY n DESC LIMIT 1
            """, (day,), role='reader')
        if group:
            out.append(t['daily_review_watch'].format(
                text=f"第一概念 {group['name']}（今日涨停 {group['n']} 只）：前排竞价有承接则主线延续，"
                     f"大幅低开无承接则转看辅线与新题材（观察口径，非预测）"))
        if today['broken_rate'] is not None:
            out.append(t['daily_review_watch'].format(
                text=f"炸板率今日 {float(today['broken_rate']):.0f}%：明日 ≥30% 视为情绪转差基准"
                     f"（与午间判读同一把尺）"))
    stage = engine.query_one(
        'SELECT promotion_rate, limit_up_count, broken_rate, highest_board'
        ' FROM analytics.market_breadth WHERE trade_date = %s', (day,), role='reader')
    if stage:
        _prev_day, prev = _prev_breadth(day)
        pos = '中性/震荡 → 控制仓位，只做核心低吸'
        if prev is not None:
            zt, pzt = stage['limit_up_count'], prev['limit_up_count']
            promo = float(stage['promotion_rate']) if stage['promotion_rate'] is not None else None
            if zt < 30 and (stage['highest_board'] or 0) <= 3:
                pos = '冰点 → 空仓或极轻仓，管住手'
            elif zt < pzt * 0.7:
                pos = '分歧/退潮 → 去弱留强；退潮确认则空仓'
            elif promo is not None and promo >= 60 and zt > pzt:
                pos = '高潮/发酵 → 持有为主，准备撤退'
            elif zt >= pzt * 1.3:
                pos = '启动/发酵 → 可逐步试错，聚焦主线前排'
        out.append(t['daily_review_watch_pos'].format(text=pos))
    return out


# ───────────────────────────────────────────────────────────────── 你要改的就这一个函数

def collect(now: dt.datetime) -> dict:
    """算出"今天这一栏要说什么"，返回 `{'sections': [...]}`。

    只读库里已落地的东西；缺的那格说"缺数"，不写 0、不拿昨天顶。
    任何一节整体缺（比如 market_breadth 还没算到今天）→ 那一节只念缺数，
    全空才不发（render 对空 sections 返回 None ⇒ 今天不播）。
    """
    day = now.date()
    T = _tpl()
    sections: list[str] = []
    groups = None
    blocks = [
        (T['daily_review_s1'], _volume_lines(day)),
        (T['daily_review_s2'], _pools_lines(day)),
        (T['daily_review_s3'], _stage_lines(day)),
    ]
    for head, body in blocks:
        if body:
            sections.append(head + '\n' + '\n'.join(body))
    ml_body, groups = _mainline_lines(day)
    if ml_body:
        sections.append(T['daily_review_s4'] + '\n' + '\n'.join(ml_body))
    lad = _ladder_lines(day, groups)
    if lad:
        sections.append(T['daily_review_s5'] + '\n' + '\n'.join(lad))
    watch = _watch_lines(day)
    if watch:
        sections.append(T['daily_review_s6'] + '\n' + '\n'.join(watch))
    return {'sections': sections}


def render(data: dict, now: dt.datetime) -> str | None:
    """排版。`collect()` 没给内容就返回 None（调用方据此**不发**，不许发一条空复盘）。"""
    sections = [str(s).strip() for s in (data or {}).get('sections') or [] if str(s).strip()]
    if not sections:
        return None
    t = _tpl()
    stamp = f'{now:%H:%M:%S}'
    lines = [t['daily_review_header'].format(t=stamp, title=(data.get('title') or '每日复盘'))]
    lines.extend(sections)
    lines.append(t['daily_review_footer'].format(t=stamp))
    return '\n'.join(lines)


# ───────────────────────────────────────────────────────────────── 以下是不用你动的壳

def today_body(day: dt.date) -> str | None:
    """今天已经落库的那一条正文（`--resend` 用它：只读库，不重新取数）。"""
    row = engine.query_one(
        "SELECT body FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND task = %s AND what = 'daily_review' AND body IS NOT NULL"
        ' ORDER BY at_ts DESC LIMIT 1', (day, TASK), role='reader')
    return (row or {}).get('body')


def one_round(*, dry_run: bool, send: bool, force: bool, channel: str | None,
              resend: bool) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    now = dt.datetime.now()
    day = now.date()
    if not force and not resend:
        if not zt_watch.is_trading_day(now):
            print(f'[{now:%H:%M:%S}] 非交易日 —— 不发每日复盘')
            return 0
    if resend:
        body = today_body(day)
        if not body:
            print(f'[{now:%H:%M:%S}] 今天库里没有已落的每日复盘正文 —— 没有东西可补发（不重算冒充）')
            _log(now, 'daily_review_no_body', ok=False, err='analytics.intraday_broadcast 里没有今天的 daily_review 行')
            return 1
        print(f'[{now:%H:%M:%S}] 补发今天已落库的那一条（不重新取数）\n{body}')
        if not dry_run:
            _send(now, body, tasks.send_channels(channel), counts={'resend': True})
        return 0
    data = collect(now)
    text = render(data, now)
    if text is None:
        print(f'[{now:%H:%M:%S}] `collect()` 没给出内容 —— 一条都不发')
        _log(now, 'daily_review_empty', ok=False, err='collect() 返回空内容，未发送')
        return 1
    print(text)
    counts = {'sections': len([s for s in (data.get('sections') or []) if str(s).strip()])}
    if dry_run:
        print('（dry-run：未写账、未发送）')
        return 0
    _log(now, 'daily_review', body=text, counts=counts)
    if send:
        _send(now, text, tasks.send_channels(channel), counts=counts)
    _vol_snap_close(now)
    return 0


def _vol_snap_close(now: dt.datetime) -> None:
    """收盘量能快照入库（用户 11-05：量能每天两次入库，这一针是"晚上复盘"那一针）。

    放在正文落库与发送**之后**：量能快照失败只留痕，不拖累复盘正文（正文已在库里）。
    快照自己取数（本地 .day 优先、腾讯分钟 15:00 兜底），不走 `collect()` ——
    collect() 的纪律是"只读库里已落地的"，这一针的数还没落地，属于写侧。
    """
    try:
        vol_snap.write_snap('close', now)
    except Exception as exc:  # noqa: BLE001
        _log(now, 'vol_snap_close_fail', err=f'{type(exc).__name__}: {str(exc)[:180]}')


def _send(now, text, keys, *, counts):
    from app.intraday.cards import send_broadcast_with_card
    res = send_broadcast_with_card(keys, '每日复盘', text)
    ok = bool(res.get('ok'))
    store.write_broadcast(now, task=TASK, what='sent', body=text, ok=ok, delivered=ok,
                          channel_key=','.join(keys), counts=counts,
                          err=None if ok else str(res.get('detail'))[:300])
    verdict = '已确认' if ok else f'未确认：{str(res.get("detail"))[:160]}（判据是渠道业务码，不是 HTTP 200）'
    print(f'  发送 → {",".join(keys)}：{verdict}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='D7 · 每日复盘（18:18，正文由 collect() 提供）')
    ap.add_argument('--dry-run', action='store_true', help='只算只排版，不写账不发送')
    ap.add_argument('--send', action='store_true', help='发渠道（不给就只落留痕）')
    ap.add_argument('--channel', default=None,
                    help='逗号分隔的渠道串；不给就读系统配置「盘中播报」那一格')
    ap.add_argument('--force', action='store_true', help='跳过交易日判断（手工试）')
    ap.add_argument('--resend', action='store_true', help='补发今天已落库的那一条（不重新取数）')
    a = ap.parse_args(argv)
    return one_round(dry_run=a.dry_run, send=a.send, force=a.force,
                     channel=a.channel, resend=a.resend)


if __name__ == '__main__':
    raise SystemExit(main())
