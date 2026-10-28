# -*- coding: utf-8 -*-
"""
同花顺本地 → 库：自选分组的全量覆盖。

**为什么是全量覆盖而不是增量合并**（用户 2026-08-30 明确要求）：库里那 24 组是
从同花顺客户端**手抄**过来的子集，名字一一对应（PCB、存储 看HBM、Cpo 光纤 ocs…）。
增量合并只会两头都留着旧的、看不出哪个是真的；同花顺客户端才是这批名单的
唯一编辑入口，库里应当是它的镜像。

**但覆盖之前一定先备份。** `config.watchlist_stock` 是 `ON DELETE CASCADE`，
删组连着把成本、备注一起删掉，而这些是手工攒的、别处没有第二份。备份落
`var/backup/watchlist_*.json`，恢复只要照着 JSON 重新插。

**★（`is_focus`）按组名接续**：同花顺没有"重点关注"这个概念，覆盖后同名组
重新打上 ★；同花顺里已经没有的组，★ 就丢了 —— 报告里如实写出来，不假装
无损。

这个模块被两个入口共用：`python -m ingest.jobs.c14_ths_watchlist`（命令行）与
`/watchlist` 页面上的「从同花顺导入」按钮。逻辑只有一份，两边不会走岔。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from app import config
from app.datasource import ths_local
from app.db import engine
from app.db.repo import watchlist as wl_repo

BACKUP_DIR = config.VAR_DIR / 'backup'


def current() -> list[dict]:
    """库里现在的自选组与成员，备份和报告都用它。"""
    rows = engine.query(
        """
        SELECT g.group_id, g.name, g.owner, g.is_focus, g.display_order, g.note,
               s.security_code, s.cost, s.note AS stock_note, s.added_at
          FROM config.watchlist_group g
          LEFT JOIN config.watchlist_stock s USING (group_id)
         ORDER BY g.display_order, g.name, s.security_code
        """)
    groups: dict[int, dict] = {}
    for r in rows:
        g = groups.setdefault(r['group_id'], {
            'group_id': r['group_id'], 'name': r['name'], 'owner': r['owner'],
            'is_focus': r['is_focus'], 'display_order': r['display_order'], 'note': r['note'],
            'stocks': []})
        if r['security_code']:
            g['stocks'].append({
                'security_code': r['security_code'],
                'cost': float(r['cost']) if r['cost'] is not None else None,
                'note': r['stock_note'],
                'added_at': str(r['added_at']) if r['added_at'] else None})
    return list(groups.values())


def backup(now: dt.datetime | None = None) -> Path:
    """把当前自选整份写成 JSON。**覆盖前必须先调它**，返回文件路径。"""
    now = now or dt.datetime.now()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"watchlist_{now.strftime('%Y%m%d_%H%M%S')}.json"
    data = {'exported_at': now.isoformat(timespec='seconds'),
            'source': 'config.watchlist_group + config.watchlist_stock',
            'groups': current()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding='utf-8')
    return path


def _known_codes() -> set[str]:
    return {r['security_code'] for r in engine.query('SELECT security_code FROM reference_data.security')}


def preview() -> dict:
    """
    要导入什么。不写库 —— 页面上的确认弹窗靠它把"将删几组、将建几组"说清楚。

    成员要过 `reference_data.security`：同花顺自选里混着板块指数（886xxx/884xxx）、ETF、
    港美股，它们不是个股，写进自选只会在表里显示成"有票没名字没行情"。
    解析阶段丢掉的（按市场号）与这一步丢掉的（不在 reference_data.security 里）分开报，
    两者的原因不一样。
    """
    parsed = ths_local.read_watchlist_groups()
    known = _known_codes()
    groups = []
    unknown: list[str] = []
    for g in parsed['groups']:
        good = [c for c in g['members'] if c in known]
        unknown.extend(c for c in g['members'] if c not in known)
        groups.append({**g, 'members': good,
                       'raw_count': len(g['members'])})
    old = current()
    return {
        'dir': str(ths_local.ths_dir()),
        'user': parsed['user'],
        'groups': groups,
        'group_count': len(groups),
        'stock_count': sum(len(g['members']) for g in groups),
        'not_stock': parsed['dropped'],
        'unknown': sorted(set(unknown)),
        'quote': parsed['quote'],
        'old_groups': len(old),
        'old_stocks': sum(len(g['stocks']) for g in old),
        'old_focus': sorted(g['name'] for g in old if g['is_focus']),
    }


def import_watchlist(*, dry_run: bool = False) -> dict:
    """
    全量覆盖。返回报告 dict（页面直接把它渲染成一段话）。

    顺序：先备份 → 删旧组（级联删成员）→ 建新组 → 插成员 → 接续 ★。
    中间任何一步抛异常都不会走到"删完还没建"的中间态吗？—— **会**：
    `engine.execute` 是自动提交的，删和插不在一个事务里。所以备份是硬要求，
    不是可选项；报告里始终带着备份路径，出问题第一句话就是"照这个文件恢复"。
    """
    rep = preview()
    rep['dry_run'] = dry_run
    if not rep['groups']:
        rep['error'] = (f'同花顺自选分组读不到 —— 检查 {rep["dir"]} 下有没有 '
                        '<用户名>\\stockblock.ini')
        return rep
    if dry_run:
        rep['backup'] = None
        return rep

    rep['backup'] = str(backup())
    owner = wl_repo.DEFAULT_OWNER
    focus_names = set(rep['old_focus'])
    rep['deleted_groups'] = engine.execute(
        'DELETE FROM config.watchlist_group WHERE owner = %s', (owner,),
        role='app') or 0

    quote = rep['quote']
    made = 0
    stocks = 0
    for g in rep['groups']:
        gid = engine.scalar(
            'INSERT INTO config.watchlist_group (name, owner, display_order, is_focus, note) '
            'VALUES (%s, %s, %s, %s, %s) RETURNING group_id',
            (g['name'], owner, g['seq'], g['name'] in focus_names,
             '同花顺导入'), role='app')
        made += 1
        rows = []
        for code in g['members']:
            q = quote.get(code) or {}
            rows.append({'g': gid, 'ts': code, 'cost': q.get('cost'),
                         'added': _to_date(q.get('added'))})
        if rows:
            engine.executemany(
                """
                INSERT INTO config.watchlist_stock (group_id, security_code, cost, added_at)
                VALUES (%(g)s, %(ts)s, %(cost)s, coalesce(%(added)s::timestamptz, now()))
                ON CONFLICT (group_id, security_code) DO NOTHING
                """, rows, role='app')
            stocks += len(rows)
    rep['groups_written'] = made
    rep['stocks_written'] = stocks
    rep['focus_kept'] = sorted(
        n for n in focus_names if any(g['name'] == n for g in rep['groups']))
    rep['focus_lost'] = sorted(set(focus_names) - set(rep['focus_kept']))
    return rep


def _to_date(compact: str | None) -> str | None:
    """`'20260817'` → `'2026-08-17'`。认不出返回 None，由 SQL 那边落到 now()。"""
    if not compact or len(compact) != 8 or not compact.isdigit():
        return None
    return f'{compact[:4]}-{compact[4:6]}-{compact[6:]}'
