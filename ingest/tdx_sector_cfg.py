# -*- coding: utf-8 -*-
"""通达信板块分类文件解析（`T0002/hq_cache/tdxzs*.cfg`）。

吸收自 FinanceTool `scripts/ingest/c17_tdx_sector.py` 的解析两半
（`load_sectors` / `build_rows`），口径逐字保留，只改两处：

1. **不读 `app.config`**：通达信安装目录从参数传，默认取环境变量 `TDX_DIR`。
   这一层因此完全可测试、可离线跑，不需要 FinanceTool 的配置对象。
2. **只解析、不写库**：`INSERT ... ON CONFLICT` 那半留在原处（P2 未切，且
   `reference_data.sector_info` 是 FinanceTool 在写的表，同名双写会互相覆盖）。

`tdxzsbase.cfg` / `tdxzsbase2.cfg` **不是板块定义**（是逐代码行情快照，列数 9/26），
所以不读 —— 这条是原实现踩过的坑，别"顺手多读两个文件"。
"""
from __future__ import annotations

import os
from pathlib import Path

CFG_FILES = ('tdxzs.cfg', 'tdxzs3.cfg')
# tcode → 板块类型。'12' 也归行业是通达信自己的编码，不是笔误。
# 表里没有的 tcode 得到 None，宁缺不猜（下游 sector_type 是可空列）。
TYPE_BY_CODE = {'2': '行业', '3': '地区', '4': '概念', '5': '风格', '12': '行业'}


def default_tdx_dir() -> Path:
    return Path(os.environ.get('TDX_DIR', r'D:\new_tdx64'))


def load_sectors(tdx_dir: Path | None = None) -> dict[str, dict]:
    """六位码 → {sector_code, name, sector_type, level}。

    `tdxzs3.cfg` 覆盖 `tdxzs.cfg`（后者是前者的子集），所以先读子集再读全集。
    顺序反过来就会把全集被子集盖掉 —— 这是这份解析唯一的顺序敏感点。
    """
    base = (tdx_dir or default_tdx_dir()) / 'T0002' / 'hq_cache'
    out: dict[str, dict] = {}
    for fname in reversed(CFG_FILES):
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
    """落库顺序固定按 sector_code：对账时要的是可复现的行序，不是文件序。"""
    return [(s['sector_code'], s['name'], s['sector_type'], s['level'])
            for s in sorted(sectors.values(), key=lambda x: x['sector_code'])]
