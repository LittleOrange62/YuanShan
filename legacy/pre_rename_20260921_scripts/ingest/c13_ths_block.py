# -*- coding: utf-8 -*-
r"""
C13 · 同花顺本地板块成分股入库。

**要解决的问题**：`ref.sector_info` 里 231 个 `sector_type='行业'` 的板块
**一只成分股都没有**（而 231/231 都有板块日线行情），概念里 `GN_` 段 269 个、
风格 `FG_` 段 161 个同样是空的。板块广度、板块内涨跌家数、个股"所属板块"
这几处的分母因此是空的 —— 不报错，只是永远算不出来。

通达信本地补不上这个洞：`infoharbor_block.dat` / `spblock.dat` 是私有二进制，
`tdxzs.cfg` 只给板块名不给成员。同花顺恰好是**明文 ini** 且随客户端每天刷新。

写进去的东西（实测 2026-08-30）：
    概念 806 个（含 416 个细分概念）  ┐
    地域 33 / 特色指数 104 / 指标股 72 ├ BlockUpdate\block_*.ini
    行业 90 个 · 5,212 只成分股       ┘ industry.ini

**主键用 `THS_` 前缀，和通达信的 `TDX_` 井水不犯河水**：两套板块的分类口径
不一样（同花顺 90 个二级行业 vs 通达信 231 个行业指数），强行合并就是猜。

**行业对齐**是这一步最值钱的部分：通达信的行业板块**有行情没成员**，同花顺的
行业**有成员没行情**，按中文名精确相等把两边缝起来，行业维度才第一次同时有
名字 + 成员 + 行情。只做**精确相等**，不做模糊匹配 —— 猜错的后果是"煤炭"
板块里装着银行股，复盘会直接读错。对不上的原样列出来，人工往
`ref.sector_alias` 插一行（`alias_code='THS_881xxx'`, `sector_code='TDX_880xxx'`）
下次运行就认，这就是增量对照。

**整批替换而不是增量 diff**：同花顺板块 id 是十六进制、不保证跨版本稳定，
id 变了就是一个新板块。所以每次先把 `THS_` 段全删再全插，不留半张脸。

用法：
    python scripts\ingest\c13_ths_block.py --dry-run   # 只报数，不写库
    python scripts\ingest\c13_ths_block.py             # 整批替换 THS_ 段
    python scripts\ingest\c13_ths_block.py --no-align  # 不回填通达信行业板块
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.datasource import ths_local  # noqa: E402
from app.db import engine  # noqa: E402

PREFIX = 'THS_'


def known_codes() -> set[str]:
    """`ref.security` 里认识的 ts_code。不在里面的成员一律不写。"""
    return {r['ts_code'] for r in engine.query('SELECT ts_code FROM ref.security')}


def collect() -> tuple[list[dict], dict[str, int]]:
    blocks = ths_local.read_blocks()
    industry = ths_local.read_industry()
    dropped = dict(blocks['dropped'])
    for k, v in industry['dropped'].items():
        dropped[k] = dropped.get(k, 0) + v
    return blocks['sectors'] + industry['sectors'], dropped


def filter_members(sectors: list[dict], known: set[str]) -> tuple[int, int]:
    """
    成员对 `ref.security` 过一遍。返回 `(保留, 丢弃)`。

    为什么必须过这一道：写进去一个 `ref.security` 里没有的代码，页面上表现为
    "板块里有这只票，但没名字没行情"，比少一只票更难查。同花顺的板块里混着
    已退市的老代码和港股通标的，就是这种情况。
    """
    keep = drop = 0
    for s in sectors:
        good = [c for c in s['members'] if c in known]
        drop += len(s['members']) - len(good)
        keep += len(good)
        s['members'] = good
    return keep, drop


def align_industry(sectors: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """
    同花顺行业 → 通达信行业板块（`TDX_880xxx`）。

    两个来源：
      1. `ref.sector_alias` 里已有的人工对照（`alias_code` 以 `THS_` 开头）—— 优先
      2. 中文名**精确相等**

    返回 `({THS码: TDX码}, 没对上的行业列表)`。
    """
    manual = {r['alias_code']: r['sector_code'] for r in engine.query(
        "SELECT alias_code, sector_code FROM ref.sector_alias "
        " WHERE left(alias_code, 4) = %s", (PREFIX,))}
    tdx = {r['name']: r['sector_code'] for r in engine.query(
        "SELECT sector_code, name FROM ref.sector_info "
        " WHERE sector_type = %s AND left(sector_code, 4) = %s",
        ('行业', 'TDX_'))}
    hit: dict[str, str] = {}
    miss: list[dict] = []
    for s in sectors:
        if s['kind'] != '行业':
            continue
        target = manual.get(s['code']) or tdx.get(s['name'])
        if target:
            hit[s['code']] = target
        else:
            miss.append(s)
    return hit, miss


def write(sectors: list[dict], align: dict[str, str]) -> dict[str, int]:
    """整批替换 `THS_` 段，再把对齐上的行业成员回填到通达信板块。"""
    stat = {}
    # 顺序要紧：member 有 ON DELETE CASCADE 指向 info，先删 member 更直白
    stat['del_member'] = engine.execute(
        'DELETE FROM ref.sector_member WHERE left(sector_code, 4) = %s',
        (PREFIX,), role='app') or 0
    stat['del_info'] = engine.execute(
        'DELETE FROM ref.sector_info WHERE left(sector_code, 4) = %s',
        (PREFIX,), role='app') or 0
    engine.executemany(
        """
        INSERT INTO ref.sector_info (sector_code, name, sector_type, level, is_active)
        VALUES (%(code)s, %(name)s, %(kind)s, %(level)s, true)
        ON CONFLICT (sector_code) DO UPDATE SET
            name = EXCLUDED.name, sector_type = EXCLUDED.sector_type,
            level = EXCLUDED.level, is_active = true
        """, sectors, role='app')
    stat['info'] = len(sectors)
    pairs = [{'code': s['code'], 'ts': c} for s in sectors for c in s['members']]
    engine.executemany(
        'INSERT INTO ref.sector_member (sector_code, ts_code) VALUES (%(code)s, %(ts)s) '
        'ON CONFLICT (sector_code, ts_code) DO NOTHING', pairs, role='app')
    stat['member'] = len(pairs)

    # ---- 行业回填 ----
    if not align:
        stat['aligned'] = stat['aligned_member'] = 0
        return stat
    by_code = {s['code']: s for s in sectors}
    targets = sorted(set(align.values()))
    # 只清对齐到的那几个通达信板块：它们本来就是 0 个成员，
    # 但万一以后有别的来源写进来，也不该被这一步顺手抹掉别的板块。
    engine.execute(
        'DELETE FROM ref.sector_member WHERE sector_code = ANY(%s)',
        (targets,), role='app')
    rows = [{'code': tdx, 'ts': c}
            for ths, tdx in align.items()
            for c in by_code[ths]['members']]
    engine.executemany(
        'INSERT INTO ref.sector_member (sector_code, ts_code) VALUES (%(code)s, %(ts)s) '
        'ON CONFLICT (sector_code, ts_code) DO NOTHING', rows, role='app')
    # 对照关系落库，下次运行不必再靠名字碰运气
    engine.executemany(
        """
        INSERT INTO ref.sector_alias (alias_code, sector_code, src, note)
        VALUES (%(a)s, %(s)s, '行业', '同花顺行业按中文名对齐通达信行业板块')
        ON CONFLICT (alias_code) DO UPDATE SET sector_code = EXCLUDED.sector_code
        """, [{'a': a, 's': s} for a, s in align.items()], role='app')
    stat['aligned'] = len(align)
    stat['aligned_member'] = len(rows)
    return stat


def coverage() -> list[dict]:
    return engine.query(
        """
        SELECT i.sector_type, count(*) AS sectors,
               count(m.sector_code) AS with_member
          FROM ref.sector_info i
          LEFT JOIN (SELECT DISTINCT sector_code FROM ref.sector_member) m
                 ON m.sector_code = i.sector_code
         GROUP BY 1 ORDER BY 2 DESC
        """)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-align', action='store_true',
                    help='不把同花顺行业成员回填到通达信行业板块')
    args = ap.parse_args()

    print('=== 读同花顺本地板块 ===')
    print('  目录 %s' % ths_local.ths_dir())
    sectors, dropped = collect()
    if not sectors:
        print('  一个板块都没读到 —— 检查 FT_THS_DIR 是否指向同花顺安装目录')
        return 1
    kinds: dict[str, int] = {}
    for s in sectors:
        kinds[s['kind']] = kinds.get(s['kind'], 0) + 1
    print('  板块 %s 个：%s' % (
        len(sectors), '、'.join(f'{k} {v}' for k, v in sorted(kinds.items()))))
    raw = sum(len(s['members']) for s in sectors)
    keep, drop = filter_members(sectors, known_codes())
    sectors = [s for s in sectors if s['members']]
    print('  成员 %s 条：留 %s，%s 条不在 ref.security 里（已退市/港美股）'
          % (raw, keep, drop))
    if dropped:
        print('  解析阶段丢弃（按市场号，不猜后缀）：%s' % dropped)
    print('  去掉空板块后 %s 个' % len(sectors))

    print('=== 行业对齐（通达信有行情没成员，同花顺有成员没行情）===')
    align, miss = ({}, []) if args.no_align else align_industry(sectors)
    print('  对上 %s 个，没对上 %s 个' % (len(align), len(miss)))
    for s in miss[:12]:
        print('    没对上：%s %s（%s 只）' % (s['code'], s['name'], len(s['members'])))
    if len(miss) > 12:
        print('    ...共 %s 个。人工往 ref.sector_alias 插一行即可，下次自动认'
              % len(miss))

    if args.dry_run:
        print('\nDONE（未写库）')
        return 0

    stat = write(sectors, align)
    print('=== 写库 ===')
    print('  清空旧 THS_ 段：info %s 行 / member %s 行'
          % (stat['del_info'], stat['del_member']))
    print('  写入：板块 %s 个 / 成员 %s 条' % (stat['info'], stat['member']))
    print('  回填通达信行业板块 %s 个 / 成员 %s 条'
          % (stat['aligned'], stat['aligned_member']))
    print('=== 覆盖度（板块 / 其中有成员）===')
    for r in coverage():
        print('  %-6s %5s / %s' % (r['sector_type'], r['sectors'], r['with_member']))
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
