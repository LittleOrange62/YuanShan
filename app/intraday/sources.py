# -*- coding: utf-8 -*-
"""盘中播报的取数与计数（竞价榜 / 概念计数 / 板块涨幅 / 一字）。

判据逐字搬自外部 `intraday_zt/auction_report.py`（2026-10-19~10-23 实测定型），
**只改了两处**，都在下面标了行：
 ① 板块涨幅单位：`market_data.sector_daily.pct_change` 实测存**小数**
    （10-26 现查 `TDX_880347 = -0.0619` ＝ −6.19%），外部直接按百分数渲染 ⇒ 错 100 倍。
    这里 ×100 后**只在这一处**，读侧与文案不再乘。
 ② 概念计数与板块涨幅的库连接走 `app/db`（role='reader'），不再自己正则解析 `.env` ——
    两处连接串读法迟早漂移，而 `.env` 是唯一真源这一条已经写在《环境事实》里。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

from app.db import engine

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
EM_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
# 沪深主板+创业板+科创板+北交所（外部定版的那串 fs，一个字符都别改：改了榜就少一块市场）
EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
SINA_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData")


# ---------------------------------------------------------------- 双榜

def board_em(cfg: dict, asc: bool = False) -> list[dict]:
    """东财 push2 全 A 按涨幅取头部（主源）。`asc=True` 取跌幅榜（最负在前）。

    ⚠️ push2 是**风控域**：串行翻页 + 每页间歇 2 秒；探到阈值另一侧就停（省请求）。
    """
    out: list[dict] = []
    for pn in range(1, cfg['max_pages'] + 1):
        q = urllib.parse.urlencode({
            'pn': pn, 'pz': cfg['page_size'], 'po': 0 if asc else 1, 'np': 1,
            'fltt': 2, 'invt': 2, 'fid': 'f3', 'fs': EM_FS, 'fields': 'f3,f6,f12,f14'})
        req = urllib.request.Request(EM_CLIST + '?' + q,
                                     headers={'User-Agent': UA,
                                              'Referer': 'https://quote.eastmoney.com/'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode('utf-8'))
        diff = ((data.get('data') or {}).get('diff')) or []
        for row in diff:
            pct = row.get('f3')
            if not isinstance(pct, (int, float)):
                continue                     # 停牌/无竞价是 '-'，不是 0
            out.append({'code': str(row.get('f12', '')), 'name': row.get('f14', ''),
                        'pct': float(pct),
                        'amount': row.get('f6') if isinstance(row.get('f6'), (int, float)) else None})
        tail = diff[-1].get('f3') if diff else None
        if len(diff) < cfg['page_size'] or (isinstance(tail, (int, float)) and
                                            (tail > -cfg['min_pct'] if asc else tail < cfg['min_pct'])):
            break
        time.sleep(2)
    return out


def board_sina(cfg: dict, asc: bool = False) -> list[dict]:
    """新浪全 A 榜（备源）。两个源形状都不干净：新浪**返回 GBK**，且 key 不带引号。"""
    out: list[dict] = []
    for page in range(1, cfg['max_pages'] + 1):
        url = SINA_URL + '?page=%d&num=%d&sort=changepercent&asc=%d&node=hs_a' % (
            page, cfg['page_size'], 1 if asc else 0)
        req = urllib.request.Request(url, headers={'User-Agent': UA,
                                                   'Referer': 'https://vip.stock.finance.sina.com.cn/'})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode('gbk', 'replace').strip()
        if not raw or raw in ('null', '[]'):
            break
        data = json.loads(re.sub(r'([{,])(\w+):', r'\1"\2":', raw))
        for row in data:
            try:
                pct = float(row.get('changepercent', ''))
            except (TypeError, ValueError):
                continue
            out.append({'code': str(row.get('symbol', ''))[2:], 'name': row.get('name', ''),
                        'pct': pct,
                        'amount': None})     # 新浪这一格根本没有，**不填 0**
        last = data[-1].get('changepercent') if data else None
        if len(data) < cfg['page_size'] or (last is not None and (
                float(last) > -cfg['min_pct'] if asc else float(last) < cfg['min_pct'])):
            break
        time.sleep(1)
    return out


def board(cfg: dict, *, asc: bool = False, note=None) -> tuple[list[dict], str]:
    """主源东财，挂了切新浪；两边都挂就抛。返回 `(行, 实际用的源)`。"""
    errors = []
    for src, fn in (('eastmoney_clist', board_em), ('sina_market_center', board_sina)):
        try:
            rows = fn(cfg, asc=asc)
            if rows:
                rows.sort(key=lambda x: x['pct'] if asc else -x['pct'])
                if src != 'eastmoney_clist' and note:
                    note('auction_source_fallback', {'used': src, 'asc': asc})
                return rows, src
            errors.append(f'{src}:空')
        except Exception as exc:  # noqa: BLE001
            errors.append(f'{src}:{type(exc).__name__}')
    raise RuntimeError('；'.join(errors))


def movers(rows: list[dict], *, threshold: float = 0.05) -> int:
    """市场级新鲜判据：|涨幅| ≥ threshold% 的家数。9:25 竞价价没刷新时这个数上不来。"""
    return sum(1 for r in rows if abs(r['pct']) >= threshold)


# ---------------------------------------------------------------- 概念计数

def sector_counts(codes: list[str], *, max_members: int, sector_top: int,
                  min_hits: int = 1) -> tuple[list[dict], int]:
    """
    一组个股命中哪些概念、各几只 → `([{name, n, sector_code|None}], 未映射家数)`。

    两条口径都是外部定标过的，别随手改：
    · 只 `sector_type='概念'`；
    · **成员数 > max_members(1500) 的泛标签剔除**（10-19 定标：融资融券 3872／深股通 1887／
      沪股通 1645 剔，机器人概念 1247／AI 1094 留。10-26 现查库里 1387 个概念中只有 3 个超线）。
      这一条不是洁癖：留着"融资融券"当热点，榜上永远它是第一，等于没榜。
    """
    if not codes:
        return [], 0
    rows = engine.query(
        """
        with big as (select sector_code from reference_data.sector_member
                      group by 1 having count(distinct security_code) > %s)
        select i.name, count(distinct left(m.security_code, 6)) n,
               case when count(distinct m.sector_code) = 1 then min(m.sector_code) end sector_code
          from reference_data.sector_member m
          join reference_data.sector_info i using (sector_code)
         where left(m.security_code, 6) = any(%s::text[])
           and i.sector_type = '概念'
           and coalesce(i.is_active, true)
           and m.sector_code not in (select sector_code from big)
         group by 1
        having count(distinct left(m.security_code, 6)) >= %s
         order by n desc, i.name
         limit %s
        """, (max_members, codes, min_hits, sector_top), role='reader')
    mapped = {r['code6'] for r in engine.query(
        "SELECT distinct left(security_code, 6) AS code6 FROM reference_data.sector_member"
        " WHERE left(security_code, 6) = any(%s::text[])", (codes,), role='reader')}
    unmapped = len(set(codes)) - len(mapped & set(codes))
    return [dict(r) for r in rows], unmapped


def sector_pcts(names: list[str]) -> dict[str, tuple[float, dt.date]]:
    """
    概念名 → (上一交易日收盘涨跌幅, 那个交易日)。**库里存的就是百分数，不乘 100。**

    一名多码按 `TDX_ > THS_ > GN_` 优先；该板块当日无日线就不给（宁缺毋假）。

    ⚠️ 这里翻过一次车，留字为证：10-26 我先看到 `TDX_880347 = -0.0619` 就判成"库里是小数"，
    加了 ×100；同一晚渲染出来"专精特新 −232.3%"才发现不对。复测同场分布
    （`avg(abs)=1.79`、`max=9.30`、与 `stock_daily.pct_change` 的 −29..+20 同量纲）
    ⇒ 单位是百分数，那句 −0.0619 只是一个 −0.0619% 的小幅波动。**别拿单行样本定单位。**
    同场另有 10 行 `|pct_change|>12`（min −99.5）——那是数据本身有异常值，另案登记，
    不在这里靠截断掩盖。
    """
    if not names:
        return {}
    rows = engine.query(
        """
        with latest as (select max(trade_date) d from market_data.sector_daily)
        select i.name, d.sector_code, d.pct_change::float8, d.trade_date
          from market_data.sector_daily d
          join reference_data.sector_info i using (sector_code)
          join latest on d.trade_date = latest.d
         where i.name = any(%s) and d.pct_change is not null
         order by i.name
        """, (names,), role='reader')
    pref = {'TDX_': 0, 'THS_': 1, 'GN_': 2}
    best: dict[str, tuple] = {}
    for r in rows:
        rank = pref.get(str(r['sector_code'])[:4], 9)
        cur = best.get(r['name'])
        if cur is None or rank < cur[2]:
            best[r['name']] = (round(float(r['pct_change']), 4), r['trade_date'], rank)
    return {k: (v[0], v[1]) for k, v in best.items()}


def concept_rows(counts: list[dict], pcts: dict[str, tuple], *, tpl_row: str,
                 tpl_pct_row: str) -> list[str]:
    """概念行渲染：**板块涨幅只有拿得到才拼**，拿不到就用不含涨幅的那一版模板。"""
    out = []
    for c in counts:
        got = pcts.get(c['name'])
        out.append((tpl_pct_row if got else tpl_row).format(
            name=c['name'], n=c['n'],
            pc=f"（截至{got[1]:%m-%d}收盘{got[0]:+.1f}%）" if got else ''))
    return out


def yizi_from_pool(rows: list[dict]) -> list[dict]:
    """竞价一字＝首封时刻**恰好** '09:25'（集合竞价即封死）。按连板数、封单额降序。

    ⚠️ 依赖本机墙钟：9 点后手工跑拿到的是当天累计池，不是竞价一字（外部用
    `auction_mode = now.hour == 9` 在文案上标"样机演示"，这里照做，见作业里那一段）。
    连板数/封单额缺的是 `None`，排序时按最小处理，**不当 0 也不当 1**。
    """
    hits = [r for r in rows if r.get('first_seal_time') == '09:25']
    return sorted(hits, key=lambda r: (-(r['board_count'] if r['board_count'] is not None
                                         else -1),
                                        -(r['seal_fund_yuan'] if r['seal_fund_yuan'] is not None
                                          else -1)))
