# -*- coding: utf-8 -*-
"""
C7 · 从通达信配置回填板块中文名。

**为什么要有这一步**：`ref.sector_info` 是从旧 SQLite 迁来的快照，里面有 128 个
`sector_type='行业'` 的板块，`name` 字段存的就是代码本身（'880005'）。
后果不是"少了个名字"这么轻——板块热度榜、板块轮动热力图的 y 轴全是六位数字，
图看不懂；`/sector` 页面上也无法判断某一行说的是哪个行业。

**名字从哪来**：通达信自己的索引配置。格式是 GBK 的竖线分隔文本：

    轮动趋势|880081|5|2|0|轮动趋势
    黑龙江|880201|3|1|0|1
             ↑ 第 2 列是六位板块码，第 1 列是中文名

覆盖情况实测（2026-08-27，D:\\new_tdx64）：
    tdxzs.cfg      604 行  →  128 个无名板块里能认出 79 个
    tdxzsbase.cfg           →  再认出 1 个
    合计 80 个，剩下 48 个是 880001~880099 段。

**剩下那 48 个为什么不猜**：880001~880099 是通达信的一级/二级行业指数，
`incon.dat#TDXNHY` 里确实有 145 条 `T01|能源` 这样的层级名，但那套是 T 码，
跟 880xxx 没有任何一列能对上。按"数量差不多、顺序应该一致"去映射就是猜，
猜错的行业名比六位数字更害人 —— 页面上"煤炭"其实是"银行"，复盘会直接读错。
所以这 48 个保持原样，由页面侧过滤掉（见 app/web/queries.py 的
`_named_sector` 条件），并记在说明书的已知缺口里。

用法：
    python scripts\\ingest\\c7_sector_name.py            # 只补 name 是纯数字的
    python scripts\\ingest\\c7_sector_name.py --overwrite # 通达信名覆盖所有同码板块
    python scripts\\ingest\\c7_sector_name.py --dry-run   # 只看会改哪些，不写库
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402

# 通达信索引配置。按顺序读，先出现的优先 —— tdxzs.cfg 是主表，
# base 那两份是老版本快照，只用来补漏。
CFG_FILES = ('tdxzs.cfg', 'tdxzsbase.cfg', 'tdxzsbase2.cfg', 'tdxzs3.cfg')


def load_names() -> dict[str, str]:
    """六位板块码 → 中文名。读 GBK，坏字节用替换字符而不是抛异常。"""
    out: dict[str, str] = {}
    base = config.TDX_DIR / 'T0002' / 'hq_cache'
    for fname in CFG_FILES:
        path = base / fname
        if not path.is_file():
            continue
        for line in path.read_bytes().decode('gbk', 'replace').splitlines():
            parts = line.split('|')
            if len(parts) < 3:
                continue
            name, code = parts[0].strip(), parts[1].strip()
            # 名字本身是数字的行没有价值，跳过；否则等于把代码又抄一遍
            if code.isdigit() and len(code) == 6 and name and not name.isdigit():
                out.setdefault(code, name)
    return out


def targets(names: dict[str, str], *, overwrite: bool) -> list[dict]:
    """
    要改名的板块。匹配用 sector_code 的后 6 位 —— 库里带 TDX_/GN_ 前缀。
    默认只动 name 是纯数字的行：正常板块的中文名是旧库里人工确认过的，
    通达信配置里的写法可能更长（"含H股"之类），不该无条件覆盖。
    """
    rows = engine.query(
        """
        SELECT sector_code, name, sector_type
          FROM ref.sector_info
         WHERE sector_code ~ '[0-9]{6}$'
         ORDER BY sector_code
        """)
    out = []
    for r in rows:
        code6 = r['sector_code'][-6:]
        new = names.get(code6)
        if not new or new == r['name']:
            continue
        if not overwrite and not r['name'].isdigit():
            continue
        out.append({'sector_code': r['sector_code'], 'old': r['name'],
                    'new': new, 'sector_type': r['sector_type']})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--overwrite', action='store_true',
                    help='连已有中文名的板块也用通达信名覆盖')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    names = load_names()
    print('=== 读取通达信索引配置 ===')
    print('  目录 %s' % (config.TDX_DIR / 'T0002' / 'hq_cache'))
    print('  可用板块名 %s 个' % len(names))
    if not names:
        print('一个都没读到，检查 FT_TDX_DIR 是否指向通达信安装目录')
        return 1

    todo = targets(names, overwrite=args.overwrite)
    unnamed = engine.scalar(
        "SELECT count(*) FROM ref.sector_info WHERE name ~ '^[0-9]+$'")
    print('=== 待改名 ===')
    print('  库里纯数字名板块 %s 个，本次能认出 %s 个' % (unnamed, len(todo)))
    for t in todo[:15]:
        print('  %s  %s -> %s' % (t['sector_code'], t['old'], t['new']))
    if len(todo) > 15:
        print('  ...共 %s 条' % len(todo))

    if args.dry_run or not todo:
        print('\nDONE（未写库）' if args.dry_run else '\nDONE（无需改动）')
        return 0

    n = engine.executemany(
        'UPDATE ref.sector_info SET name = %(new)s WHERE sector_code = %(sector_code)s',
        todo, role='etl')
    left = engine.scalar(
        "SELECT count(*) FROM ref.sector_info WHERE name ~ '^[0-9]+$'")
    print('=== 结果 ===')
    print('  已更新 %s 行，仍是纯数字名的还有 %s 个' % (n if n is not None else len(todo), left))
    print('  剩下的是 880001~880099 段的一级/二级行业指数，通达信配置里没有'
          '对应名字，页面侧按无名板块过滤')
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
