# -*- coding: utf-8 -*-
"""C24 · 板块名 → 东方财富板块页（BK 码）对表 → `reference_data.em_board_link`。

**为什么必须有这一步**（用户 2026-10-15 点名：「热度榜与五要素 里面的板块也要有超链接可以点开，
比如『油气开采』对应 `https://quote.eastmoney.com/bk/90.BK1276.html`，你看下，多测试几个，
把这个功能完善一下」）：本机的板块名有三套体系，彼此**没有码级通道** —— 热度榜母体是通达信
（行业 502 / 概念 268 / 风格 128 / 市场级统计 30），「覆盖以来累计净流入」那张表是同花顺行业榜
（90 个），而用户给的例子是**东财自己的板块码**。要发外链就得先回答
「这一个名字在东财对应哪一个板块页」，而这个问题只有东财的清单能答。所以先落一张对表，
页面按它决定「这个名字是链接还是纯文本」。

源实测（2026-10-15 三条路只有第三条通，前两条写在 `36_em_board_link.sql` 头部）：
  · `push2` / `push2delay` 的 `clist/get` —— 本机连不上（ConnectionError）；
  · `searchapi…/suggest/get` —— HTTP 200 却对任何输入回同一段 2023 年的缓存（三次逐字节相同），
    拿它做对表会静默造出一张全 miss 的表，**这才是这条路真正不能用的理由**；
  · ✅ `data.eastmoney.com/dataapi/bkzj/getbkzj?code=m:90+t:2`（行业 496）与 `m:90+t:3`（概念 506），
    一发一张清单，字段 `f12`=BK 码、`f14`=名。两张清单内部名字各自唯一（496/496、506/506 实测），
    所以「精确同名」不存在多义。

对表三条规矩（一条都不许放宽，判据函数 `resolve()` 只此一份）：
  1. **同类才对表**：我方行业只查东财行业榜、我方概念只查东财概念榜。跨类同名不链 ——
     东财概念里有个『白酒』，而我方那是行业档，名字撞上不等于说的同一件事。
  2. **只认精确同名，再加罗马后缀**：先逐字比；比不上才试「名+Ⅱ」「名+Ⅲ」（东财把二级/三级
     行业写成『油气开采Ⅱ』，用户举的例子正是这一族）。Ⅱ 优先于 Ⅲ。没有第四种规则。
  3. **对不上留痕**：`miss` 行照写。风格 / 市场级统计两档**根本不进这张表**（东财没有对应的族，
     硬对只会产出一屏假链接）。

用法：
    python -m ingest.jobs.c24_em_board_link                # 抓两张清单、对表、整表刷新
    python -m ingest.jobs.c24_em_board_link --dry-run      # 只打印会写什么，不落库
    python -m ingest.jobs.c24_em_board_link --no-write     # 同上（调试用别名）

退出码：0 写了 / 没变化；2 按设计不写（清单半截 = 宁可不刷，也不要让今天的一次抖动
把昨天对好的码盖成 miss）；1 真失败（接口异常、库不通、我方名字一个都没读到）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.db import engine  # noqa: E402

SOURCE = 'https://data.eastmoney.com/dataapi/bkzj/getbkzj'
TARGET_TABLE = 'reference_data.em_board_link'
#: 我方口径 → 东财那一族。只有这两档：风格与市场级统计在东财没有对应的清单，刻意不对表。
LISTS = {'行业': 'm:90+t:2', '概念': 'm:90+t:3'}
#: 少于这个行数判为「半截清单」直接不刷。实测正常 496 / 506。
MIN_ROWS = 300
REFERER = 'https://data.eastmoney.com/bkzj/hy.html'

UPSERT = """
insert into reference_data.em_board_link
 (sector_kind, sector_name, em_board_code, em_board_name, match_kind, source, tried_at)
values (%(sector_kind)s,%(sector_name)s,%(em_board_code)s,%(em_board_name)s,
        %(match_kind)s,%(source)s,now())
on conflict (sector_kind, sector_name) do update set
  em_board_code = excluded.em_board_code,
  em_board_name = excluded.em_board_name,
  match_kind    = excluded.match_kind,
  source        = excluded.source,
  tried_at      = now()
"""


# ------------------------------------------------------------------ 判据（纯函数，不碰网不碰库）

def resolve(name: str, kind: str, tables: dict[str, dict[str, str]]) -> tuple:
    """这一个名字在这一档里对应东财哪个板块页。返回 (码, 东财名, 方式)，对不上返回 (None, None, 'miss')。

    顺序就是判据，写死三条：精确同名 → 名+Ⅱ → 名+Ⅲ → miss。
    表里没有这一族（风格/统计）同样返回 miss，调用方不该问，但问了也不猜。
    """
    table = tables.get(kind) or {}
    if name in table:
        return table[name], name, 'exact'
    for suffix, way in (('Ⅱ', 'roman_ii'), ('Ⅲ', 'roman_iii')):
        cand = name + suffix
        if cand in table:
            return table[cand], cand, way
    return None, None, 'miss'


# ------------------------------------------------------------------ 取数

def fetch_lists() -> dict[str, dict[str, str]]:
    """两张东财板块清单：{我方口径: {东财名: BK 码}}。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'vr'))
    import astock

    headers = {'User-Agent': astock.UA, 'Referer': REFERER}
    out = {}
    for kind, code in LISTS.items():
        r = astock.em_get(SOURCE, params={'key': 'f62', 'code': code},
                          headers=headers, timeout=20)
        data = (r.json().get('data') or {}).get('diff') or []
        out[kind] = {str(x.get('f14') or '').strip(): str(x.get('f12') or '').strip()
                     for x in data if x.get('f14') and x.get('f12')}
    return out


def our_names() -> list[dict]:
    """我方两块屏幕会念出的板块名（去重后），带口径。

    行业这一档并了两处：`reference_data.sector_info`（通达信体系，热度榜的母体）与
    `market_data.sector_fund_flow_daily`（同花顺行业榜 —— 那张「覆盖以来累计净流入」用的就是它，
    它的名字**不在 sector_info 里**，漏掉这一批就等于净流入那一屏永远没有链接）。
    """
    rows = engine.query(
        """select '行业' as sector_kind, name as sector_name
             from reference_data.sector_info
            where is_active and sector_type = '行业' and name is not null and name <> ''
            union
           select '概念', name
             from reference_data.sector_info
            where is_active and sector_type = '概念' and name is not null and name <> ''
            union
           select '行业', sector_name
             from market_data.sector_fund_flow_daily
            where sector_name is not null and sector_name <> ''
            order by 1, 2""")
    return [r for r in rows if r.get('sector_name') not in (None, '')]


# ------------------------------------------------------------------ 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='板块名 → 东财板块页（BK 码）对表刷新')
    ap.add_argument('--dry-run', action='store_true', help='只打印会写什么，不落库')
    args = ap.parse_args(argv)

    names = our_names()
    if not names:
        print('✗ 我方板块名一条都没读到（reference_data.sector_info / sector_fund_flow_daily 都空？）'
              '—— 这不是「今天没有板块」，是底表读不到，宁可不刷')
        return 1
    by_kind = {}
    for r in names:
        by_kind.setdefault(r['sector_kind'], []).append(r['sector_name'])
    print('=== 要对的名单：%s ===' % ' / '.join('%s %d 个' % (k, len(v)) for k, v in sorted(by_kind.items())))

    try:
        tables = fetch_lists()
    except Exception as exc:  # noqa: BLE001  接口坏要说清坏在哪，别把旧对表盖成空
        print('✗ 东财板块清单抓取异常：%s: %s' % (type(exc).__name__, str(exc)[:160]))
        return 1
    short = {k: len(v) for k, v in tables.items() if len(v) < MIN_ROWS}
    if short:
        print('✗ 清单行数 %s 低于 %d —— 判为半截清单，本次不刷。'
              '半截比缺数更坏：今天对不上的名字会把昨天对好的码盖成 miss，屏幕会当场少一片链接。'
              % (short, MIN_ROWS))
        return 2
    print('  东财清单：行业 %d 个 / 概念 %d 个' % (len(tables['行业']), len(tables['概念'])))

    rows, stats = [], {}
    for kind, items in sorted(by_kind.items()):
        for name in items:
            code, em_name, way = resolve(name, kind, tables)
            stats[(kind, way)] = stats.get((kind, way), 0) + 1
            rows.append({'sector_kind': kind, 'sector_name': name, 'em_board_code': code,
                         'em_board_name': em_name, 'match_kind': way, 'source': SOURCE})

    print('=== 对表结果 ===')
    for kind in sorted(by_kind):
        got = sum(v for (k, w), v in stats.items() if k == kind and w != 'miss')
        tot = sum(v for (k, w), v in stats.items() if k == kind)
        detail = ' / '.join('%s %d' % (w, v) for (k, w), v in sorted(stats.items()) if k == kind and w != 'miss')
        print('  %s：%d / %d 有东财板块页（%s）；对不上 %d'
              % (kind, got, tot, detail or '无', tot - got))
    print('  例子：油气开采 → %s' % (next((r['em_board_code'] for r in rows
                                          if r['sector_name'] == '油气开采'), '没对上')))

    if args.dry_run:
        print('DONE（--dry-run 未写库）；会写 %d 行' % len(rows))
        return 0

    batch = engine.query_one(
        """insert into operations.ingest_batch
             (source, source_path, target, row_count, actor, status, started_at, finished_at, message)
           values ('em_board_link', %s, %s, %s, 'ft_etl', 'running', now(), now(),
                   '板块名对东财板块页（BK 码）。同类才对表 + 精确同名或 Ⅱ/Ⅲ 后缀，对不上记 miss')
           returning batch_id""", (SOURCE, TARGET_TABLE, len(rows)), role='etl')
    bid = (batch or {}).get('batch_id')
    n = engine.executemany(UPSERT, rows, role='etl')
    if bid is not None:
        engine.execute("update operations.ingest_batch set status='ok', row_count=%s, finished_at=now() "
                       "where batch_id=%s", (n or len(rows), bid), role='etl')
    print('=== 结果 ===\n  写入 %s 行（含 miss 行），batch_id=%s' % (n or len(rows), bid))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
