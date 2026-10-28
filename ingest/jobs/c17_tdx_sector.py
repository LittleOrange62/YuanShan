# -*- coding: utf-8 -*-
"""
C17 · 把通达信自己的板块指数全集（含分类）入库到 reference_data.sector_info。

**和 C7 的区别**：C7 只拿 tdxzs*.cfg 给库里已有的板块**补中文名**，
且只 UPDATE、不 INSERT —— 通达信配置里有、库里没有的板块进不来，
配置里的分类信息（行业/地区/概念/风格）也完全没落库。C17 做的是
**板块全集 + 通达信口径的分类标注**。

**数据源**：`T0002/hq_cache/tdxzs.cfg` 与 `tdxzs3.cfg`（GBK 明文，6 列竖线分隔）：

    板块名|880xxx|类型码|层级|标志|拼音

`tdxzsbase.cfg`/`tdxzsbase2.cfg` 不是板块定义（是逐代码的行情快照，列数 9/26），
这里按列数=6 过滤掉。

**类型码 → 板块分类**（按码段 + 真实板块名反推，非猜测）：
    2  → 行业   （880301~880993：煤炭/钢铁/水泥建材…）
    3  → 地区   （880201~880232：黑龙江/新疆板块…）
    4  → 概念   （880501~880978：含H股/稀缺资源/5G概念…）
    5  → 风格   （880081~880979：轮动趋势/高分红/低安全度…）
    12 → 行业   （881001~881479：通达信新一级行业指数）

**sector_code 口径**：统一 `TDX_` + 六位码，和 `market_data.sector_daily` 的主键一致
（实测 sector_daily 用 TDX_880xxx），这样入库后板块日线/热度能 join 上。

**只补缺、不覆盖**：库里已有的板块多半是旧系统人工确认过的名字，
C17 只在 name 是纯数字/为空时补名、只在 sector_type 为空时补分类，
绝不覆盖既有人工数据 —— 与 C7 同一套保守原则。

用法：
    python scripts\\ingest\\c17_tdx_sector.py --dry-run
    python scripts\\ingest\\c17_tdx_sector.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402

CFG_FILES = ('tdxzs.cfg', 'tdxzs3.cfg')          # 6 列板块定义；base 那两份是行情快照，不读
TYPE_BY_CODE = {'2': '行业', '3': '地区', '4': '概念', '5': '风格', '12': '行业'}


def load_sectors() -> dict[str, dict]:
    """六位码 → {name, sector_type, level}。tdxzs3.cfg 覆盖 tdxzs.cfg（后者是子集）。"""
    base = config.TDX_DIR / 'T0002' / 'hq_cache'
    out: dict[str, dict] = {}
    for fname in reversed(CFG_FILES):            # 先读子集，再用全集覆盖
        path = base / fname
        if not path.is_file():
            continue
        for line in path.read_bytes().decode('gbk', 'replace').splitlines():
            p = line.split('|')
            if len(p) < 6:
                continue
            name, code, tcode = p[0].strip(), p[1].strip(), p[2].strip()
            if not (code.isdigit() and len(code) == 6):
                continue
            out[code] = {
                'sector_code': 'TDX_' + code,
                'name': name,
                'sector_type': TYPE_BY_CODE.get(tcode),
                'level': int(p[3]) if p[3].strip().isdigit() else None,
            }
    return out


def build_rows(sectors: dict[str, dict]) -> list[tuple]:
    return [(s['sector_code'], s['name'], s['sector_type'], s['level'])
            for s in sorted(sectors.values(), key=lambda x: x['sector_code'])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    sectors = load_sectors()
    if not sectors:
        print('一个板块都没读到，检查 FT_TDX_DIR=%s' % config.TDX_DIR)
        return 1
    named = sum(1 for s in sectors.values() if s['name'] and not s['name'].isdigit())
    typed = sum(1 for s in sectors.values() if s['sector_type'])
    print('=== 通达信板块定义（%s）===' % ' + '.join(CFG_FILES))
    print('  板块码 %s 个，其中有中文名 %s，带分类 %s'
          % (len(sectors), named, typed))
    dist: dict[str, int] = {}
    for s in sectors.values():
        dist[s['sector_type'] or '未知'] = dist.get(s['sector_type'] or '未知', 0) + 1
    print('  分类分布：' + '  '.join('%s=%d' % kv for kv in sorted(dist.items())))

    existing = {r['sector_code'] for r in
                engine.query('SELECT sector_code FROM reference_data.sector_info')}
    new = [c for c in sectors if 'TDX_' + c not in existing]
    print('  库里缺的（本次新增）：%s 个' % len(new))

    if args.dry_run:
        print('\n--dry-run，未写库')
        return 0

    rows = build_rows(sectors)
    engine.executemany(
        """
        INSERT INTO reference_data.sector_info (sector_code, name, sector_type, level)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (sector_code) DO UPDATE SET
            name        = CASE WHEN reference_data.sector_info.name ~ '^[0-9]+$'
                                    OR reference_data.sector_info.name IS NULL
                               THEN EXCLUDED.name ELSE reference_data.sector_info.name END,
            sector_type = COALESCE(reference_data.sector_info.sector_type, EXCLUDED.sector_type),
            level       = COALESCE(reference_data.sector_info.level, EXCLUDED.level)
        """, rows, role='etl')
    print('=== 结果 ===')
    print('  提交 %s 行（新增 %s，其余为按保守规则补空）' % (len(rows), len(new)))
    print('  reference_data.sector_info 现有板块 %s 个'
          % engine.scalar('SELECT count(*) FROM reference_data.sector_info'))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
