# -*- coding: utf-8 -*-
"""**排名**这件事只准有一处实现（公用组件，2026-10-19 用户点名）。

用户定的规则（原话摘要）：得分相同 ⇒ **名次相同**，后一个分数的名次**按位置顺延** ——
最高分有 3 只并列第 1，那么下一个分数的名次是 **4**，不是 2。
这在统计口径里叫 standard competition ranking（SQL 的 `rank()`），
与 `dense_rank()`（并列后不跳号：1,1,1,2）不是一回事，用错一个函数，"第 4 名"就变"第 2 名"。

为什么要有这个模块（而不是各页各写一遍）：名次这种东西**看数字看不出对错**。
`volume_double` 的榜、名次稳定榜、启动前量能、四维象限的分量名次……
每处自己 `row_number()` 一次，就会有"并列时到底跳不跳号"的五六种答案，
而界面全都长得一样。所以：

- SQL 侧要排名 → 用 `rank_sql()` / `dense_rank_sql()`（默认 standard）；
- Python 侧要对一批数排名 → 用 `rank_in_memory()`；
- 界面上那句解释 → 读 `RULE_NOTE`（随接口交出，不各自抄写）。

⚠️ 两条容易做错的边界，都在 `tests/test_ranking_shared.py` 里钉着：

1. **NULL（缺数）不参与排名，也不占号**。把"没有得分"排成第 N 名，等于编一个名次；
   把缺数当成 0 分参与排名，更会把"没数据"念成"最差"（本项目反复被教育过的那条）。
   所以 SQL 侧一律 `order by <expr> desc nulls last` 再配 `where <expr> is not null` 的母体，
   Python 侧交回 `None`。
2. **分母要说清是哪一批**。同一个"第 40 名"，在"全场 5,514 只"与"倍量次≥6 的 124 只"里
   是两个完全不同的话；所以 `rank_in_memory` 连同 `universe`（参与排名的只数）一起交回。
"""
from __future__ import annotations

#: 名次规则的原文解释。界面/悬停/推送都读这一份，不许在组件里另写一句。
RULE_NOTE = (
    '名次＝同分同名、下一名按位置顺延：最高分有 3 只并列第 1，下一个分数的名次就是第 4'
    '（不是第 2）。没有得分的票不参与排名、也不占号 —— 格子显示 —，不是「最后一名」。')

#: 同一句话的短版（列头悬停那种放不下的地方用）。
RULE_NOTE_SHORT = '同分同名、下一名顺延（3 只并列第 1 ⇒ 下一个是第 4）；没得分的不占号。'

#: 「名次」的方向读法（1 = 最靠前，数字越小越好）。
#: 以前这句话只住在 `duanxian/quant.py` 的口径表里，组热度表要加"近 N 场名次"那一列时
#: 就成了第二份 —— 而方向这种东西界面上看不出对错，两处各写一遍迟早有一处反过来。
#: 所以搬到这里，`quant._CAL['rank_direction']` 与本文件的调用方都读这**一份**。
DIRECTION_NOTE = (
    '名次是「第几名」：1 = 全场第一，数字越小越靠前。所以「名次稳步前进」= '
    '名次数字一场比一场小，不是数字变大。这一句在任何名次列（逐场路径、净变化）上都同一个方向读，'
    '界面别把「增长」读成数字上涨')


def rank_sql(order: str, partition: str | None = None) -> str:
    """standard competition ranking 的 SQL 片段（并列同名、下一名顺延）。

    `order` 必须自带 `nulls last`：缺数排到末尾之后，外面那句
    `where <score> is not null` 才把号数留干净。
    """
    part = f'partition by {partition} ' if partition else ''
    return f'rank() over ({part}order by {order})::int'


def dense_rank_sql(order: str, partition: str | None = None) -> str:
    """并列后**不**跳号的那一种（1,1,1,2）。只给"档位"语义用（比如第几档），
    榜上的名次一律用 `rank_sql` —— 用户要的是顺延，不是稠密。"""
    part = f'partition by {partition} ' if partition else ''
    return f'dense_rank() over ({part}order by {order})::int'


def rank_in_memory(rows: list[dict], key: str = 'score', *, desc: bool = True,
                   rank_key: str = 'rank') -> list[dict]:
    """给一批已经取回的数排名（读侧聚合、内存里翻页时用，不再回库）。

    返回**新列表**（每项是原 dict 的浅拷贝，多了 `rank_key` 一项），规则与 `rank_sql` 逐位一致：

    * 同分同名；下一名 ＝ 已排进来的只数 + 1（顺延，不是 +1 递增）；
    * `key` 取不到数（None/NaN/非数字）的交回 `rank=None`，不占号，排在有数的那批之后；
    * `desc=True` 是"分高在前"，榜单一律它；`desc=False` 给"名次数字越小越好"那类（名次榜）。
    """

    def _num(v):
        try:
            f = None if v is None else float(v)
        except (TypeError, ValueError):
            return None
        return None if f != f else f

    out = [dict(r) for r in rows]
    scored = [(i, _num(r.get(key))) for i, r in enumerate(out)]
    have = [(i, v) for i, v in scored if v is not None]
    missing = [i for i, v in scored if v is None]
    have.sort(key=lambda t: t[1], reverse=bool(desc))
    prev_val, prev_rank = None, 0
    for pos, (i, v) in enumerate(have):
        if prev_val is not None and v == prev_val:
            rk = prev_rank                      # 同分：沿用上一个的名次
        else:
            rk = pos + 1                        # 不同分：按位置顺延（不是 prev_rank+1）
            prev_rank, prev_val = rk, v
        out[i][rank_key] = rk
    for i in missing:
        out[i][rank_key] = None
    return out


def tie_counts(rows: list[dict], rank_key: str = 'rank') -> dict[int, int]:
    """`{名次: 这一名并列几只}`。界面上"第 1（并列 3 只）"那一格读它，
    自己数一遍＝第二份判据（数错了没人发现）。"""
    out: dict[int, int] = {}
    for r in rows or []:
        rk = r.get(rank_key)
        if isinstance(rk, int):
            out[rk] = out.get(rk, 0) + 1
    return out
