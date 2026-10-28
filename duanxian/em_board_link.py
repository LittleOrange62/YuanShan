# -*- coding: utf-8 -*-
"""板块 → 东财板块页（BK 码）的**读侧唯一一份**。

写侧是 `ingest/jobs/c24_em_board_link.py`（每天加工段 `boardlink` 那一步整表刷新，
对表规矩 `同类才对表 + 精确同名或 Ⅱ/Ⅲ 后缀 + 对不上记 miss` 只住那里的 `resolve()`）；
这张表落在 `reference_data.em_board_link`。

这里只管读：**两块屏幕（板块热度的「热度榜与五要素」与「覆盖以来累计净流入」）共用这一个函数**，
不在各自的 SQL 里各 join 一遍 —— 同一份对表两种读法迟早会分叉（谁忘了带 `sector_kind`
就会把「行业」的码发给一个概念板块）。

三条读法上的硬约束：
1. **读不到就是空，不编一个地址出去**。库不通 / 表空 / 那个名字没登记 ⇒ 交 `None`，
   界面退回纯文本。少一个链接可以接受，点开一个死页不行。
2. **新鲜度要跟着交出去**（`tried_at`）。对表是每天刷的，屏幕不能说成"实时对应"。
3. **东财侧真名必须一起交**（`em_board_name`）。走 Ⅱ/Ⅲ 后缀那条规则换来的链接，
   点开的页眉写的是「油气开采Ⅱ」而屏上是「油气开采」—— 这个差要看得见，不能藏。
"""
from __future__ import annotations

import pandas as pd

from duanxian import pg

TABLE = 'reference_data.em_board_link'
#: 我方口径 → 只在这些口径里发链接。风格 / 市场级统计两档东财没有对应的板块族，刻意不给。
LINKABLE_KINDS = ('行业', '概念')


def load(kind: str) -> dict[str, dict]:
    """这一档口径下所有**对上了**的板块名：`{屏上名: {code, em_name, match_kind, tried_at}}`。

    读不到（库不通 / 表不存在 / 这一档一条没有）返回空 dict —— 调用方据此把整列退回纯文本，
    并且要把"为什么没有"说出来（用 `status()`），不许默默少一片链接。
    """
    if kind not in LINKABLE_KINDS:
        return {}
    df = pg.fetch_df(
        """select sector_name, em_board_code, em_board_name, match_kind,
                 tried_at::date::text as tried_at
            from reference_data.em_board_link
           where sector_kind = %s and em_board_code is not null""", (kind,))
    if df is None:
        return {}
    out = {}
    for r in df.to_dict('records'):
        code = r.get('em_board_code')
        if code is None or (isinstance(code, float) and pd.isna(code)):
            continue
        out[str(r['sector_name'])] = {
            'code': str(code),
            'em_name': None if r.get('em_board_name') is None or pd.isna(r.get('em_board_name'))
                       else str(r['em_board_name']),
            'match_kind': str(r.get('match_kind') or ''),
            'tried_at': None if r.get('tried_at') is None or pd.isna(r.get('tried_at'))
                        else str(r['tried_at']),
        }
    return out


def decorate(rows: list[dict], *, kind_of=lambda row: '行业', name_of=lambda row: row['sector_name'],
             maps: dict[str, dict] | None = None) -> dict:
    """给每一行挂上 `em_board_code / em_board_name / em_match_kind`（对不上就是 None）。

    返回这次用到的对表（`{口径: {名: …}}`），调用方可以留着给 `status_line` 说覆盖率。
    两份屏幕一次页面加载里各自调一次，不要在页面上再拼一遍地址。
    """
    cache = maps if maps is not None else {}
    for row in rows:
        kind = kind_of(row)
        if kind not in cache:
            cache[kind] = load(kind)
        hit = cache[kind].get(str(name_of(row) or ''))
        row['em_board_code'] = (hit or {}).get('code')
        row['em_board_name'] = (hit or {}).get('em_name')
        row['em_match_kind'] = (hit or {}).get('match_kind')
    return cache


def status() -> dict:
    """这张对表现在长什么样：最近一次刷新是哪天、每一档对上多少个。

    屏幕要说「90 个行业里 73 个能点开东财板块页」就必须读这里，不要在界面里数行数
    —— 界面拿到的是**这一页**，数出来的是分页后的假分母。
    """
    df = pg.fetch_df(
        """select sector_kind,
                  count(*) filter (where em_board_code is not null) as linked,
                  count(*) as total,
                  max(tried_at)::timestamptz at time zone 'Asia/Shanghai' as tried_at
             from reference_data.em_board_link
            group by 1""")
    if df is None:
        return {'available': False, 'reason': '对表读不到（`reference_data.em_board_link` 不可访问或库不通）',
                'kinds': {}, 'tried_at': None}
    kinds = {}
    latest = None
    for r in df.to_dict('records'):
        kinds[str(r['sector_kind'])] = {'linked': int(r['linked'] or 0), 'total': int(r['total'] or 0)}
        if r.get('tried_at') and (latest is None or str(r['tried_at']) > latest):
            latest = str(r['tried_at'])[:10]
    if not kinds:
        return {'available': False, 'reason': '对表是空的 —— 加工段 `boardlink` 那一步还没跑过'
                                              '（手工：`python -m ingest.jobs.c24_em_board_link`）',
                'kinds': {}, 'tried_at': None}
    return {'available': True, 'kinds': kinds, 'tried_at': latest, 'reason': None}
