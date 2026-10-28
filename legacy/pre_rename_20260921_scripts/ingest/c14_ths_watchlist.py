# -*- coding: utf-8 -*-
r"""
C14 · 同花顺自选分组导入（全量覆盖）。

**上一轮的结论是错的，这里更正**：`.sel` 导出文件里确实没有分组（1,109 条扁平
列表、全文件零中文字节），但分组并不是"只存在于客户端内存里" —— 它就在
安装目录下 `<用户名>\stockblock.ini` 的 `[BLOCK_NAME_MAP_TABLE]` 里，
43 个中文组名，成员在 `[BLOCK_STOCK_CONTEXT]`，带市场前缀。

通达信那边**没有**这个东西：`T0002\blocknew\zxg.blk` 是扁平自选，
`zxgmore.dat` 是代码+成本，都不带分组。所以自选分组这一项归同花顺。

覆盖是**全量**的（用户明确要求"不是增量覆盖，是全量覆盖整体代替掉"），
所以脚本第一件事是把现有自选整份备份到 `var\backup\watchlist_*.json`。

用法：
    python scripts\ingest\c14_ths_watchlist.py --dry-run  # 只报数，不动库
    python scripts\ingest\c14_ths_watchlist.py --yes      # 直接覆盖
    python scripts\ingest\c14_ths_watchlist.py            # 先打印，再要一次确认
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.datasource import ths_sync  # noqa: E402


def show(rep: dict) -> None:
    print('=== 同花顺自选 ===')
    print('  目录 %s（用户 %s）' % (rep['dir'], rep['user']))
    print('  分组 %s 个，个股 %s 只' % (rep['group_count'], rep['stock_count']))
    if rep['not_stock']:
        print('  解析时按市场号丢弃（板块指数/基金/外盘，不是个股）：%s'
              % rep['not_stock'])
    if rep['unknown']:
        print('  不在 ref.security 里、因此不写库的 %s 个：%s'
              % (len(rep['unknown']), '、'.join(rep['unknown'][:12])))
    print('  前 8 组：')
    for g in rep['groups'][:8]:
        print('    %2s. %-22s %3s 只（原始 %s）'
              % (g['seq'], g['name'][:22], len(g['members']), g['raw_count']))
    print('=== 库里现有（将被整体替换）===')
    print('  分组 %s 个，个股 %s 只；★ 重点关注：%s'
          % (rep['old_groups'], rep['old_stocks'],
             '、'.join(rep['old_focus']) or '无'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--yes', action='store_true', help='跳过交互确认')
    args = ap.parse_args()

    rep = ths_sync.preview()
    if not rep['groups']:
        print('同花顺自选分组读不到 —— 检查 %s 下有没有 <用户名>\\stockblock.ini'
              % rep['dir'])
        return 1
    show(rep)

    if args.dry_run:
        print('\nDONE（未写库）')
        return 0
    if not args.yes:
        # 删组是不可逆的（成员级联删除），所以默认要一次显式确认。
        # 非交互环境（跑批/网页）走 --yes 或直接调 ths_sync.import_watchlist()。
        try:
            ans = input('\n确认整体替换？输入 yes 继续：').strip().lower()
        except EOFError:
            ans = ''
        if ans != 'yes':
            print('已取消，库没动。')
            return 0

    out = ths_sync.import_watchlist()
    print('=== 结果 ===')
    print('  备份 %s' % out['backup'])
    print('  删掉旧组 %s 个（成员级联删除）' % out['deleted_groups'])
    print('  新建 %s 组 / 写入 %s 只' % (out['groups_written'], out['stocks_written']))
    print('  ★ 保留：%s' % ('、'.join(out['focus_kept']) or '无'))
    if out['focus_lost']:
        print('  ★ 丢失（同花顺里没有同名组）：%s' % '、'.join(out['focus_lost']))
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
