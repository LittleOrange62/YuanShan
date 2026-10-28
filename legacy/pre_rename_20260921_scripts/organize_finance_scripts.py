# -*- coding: utf-8 -*-
"""
Organize loose python scripts under D:\\finance into the classified D:\\finance\\脚本 tree.

Sources handled
---------------
1. D:\\finance\\*.py                 -> 12 one-off probe / check scripts left at the root
2. D:\\finance\\脚本\\*.py           -> 11 unclassified scripts sitting at the 脚本 root
3. D:\\finance\\data\\scripts\\*.py  -> stray script inside the data directory

Every move is recorded to a manifest so the operation can be reverted.
Run with --apply to actually move files; default is a dry run.
"""
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

FINANCE = r'D:\finance'
SCRIPTS = os.path.join(FINANCE, '脚本')
DATA_SCRIPTS = os.path.join(FINANCE, 'data', 'scripts')

DIR_SYNC = '01_数据同步'
DIR_TDX = '02_通达信操作'
DIR_VALIDATE = '03_数据校验'
DIR_CLEAN = '04_数据清洗'
DIR_TOOLS = '05_辅助工具'
DIR_TEMP = '06_临时脚本'
DIR_FACTOR = '07_因子跑分'

# ---------------------------------------------------------------- routing table
# source dir -> {filename: target subdir}
ROUTES = {
    FINANCE: {
        # 重复行 / 表结构 / 数据存在性检查
        '_check_dup.py': DIR_VALIDATE,
        '_check_dup2.py': DIR_VALIDATE,
        '_check_dup3.py': DIR_VALIDATE,
        '_check_schema.py': DIR_VALIDATE,
        'check_600367.py': DIR_VALIDATE,
        # 通达信二进制 / 缓存文件格式探查
        'probe_blockmap.py': DIR_TOOLS,
        'probe_day.py': DIR_TOOLS,
        'probe_hq_cache.py': DIR_TOOLS,
        'probe_schema.py': DIR_TOOLS,
        # 临时查询
        'query_hx.py': DIR_TOOLS,
        'search_hx.py': DIR_TOOLS,
        # 分钟线按年拆分，属于 ETL
        'split_minline_by_year.py': DIR_SYNC,
    },
    SCRIPTS: {
        # 因子结果 / 本地数据 / 自选股 校验
        '_verify_factor.py': DIR_VALIDATE,
        '_verify_factor2.py': DIR_VALIDATE,
        '_verify_local.py': DIR_VALIDATE,
        'verify_watchlist.py': DIR_VALIDATE,
        # 查询与导出
        'query_plan1.py': DIR_TOOLS,
        'generate_csvs.py': DIR_TOOLS,
        # 因子跑分执行与回溯
        'run_all_factors_local.py': DIR_FACTOR,
        'run_factor_local.py': DIR_FACTOR,
        'run_plan1.py': DIR_FACTOR,
        'run_plan1_backfill.py': DIR_FACTOR,
        'run_plan_backfill.py': DIR_FACTOR,
    },
    DATA_SCRIPTS: {
        'query_hx.py': DIR_TOOLS,
    },
}

ALL_DIRS = [DIR_SYNC, DIR_TDX, DIR_VALIDATE, DIR_CLEAN,
            DIR_TOOLS, DIR_TEMP, DIR_FACTOR]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def unique_target(target_dir, name, src_path):
    """Resolve name collisions. Identical content -> signal 'duplicate'."""
    dest = os.path.join(target_dir, name)
    if not os.path.exists(dest):
        return dest, 'move'
    if sha256(dest) == sha256(src_path):
        return dest, 'duplicate'
    stem, ext = os.path.splitext(name)
    for i in range(2, 100):
        cand = os.path.join(target_dir, '%s_v%d%s' % (stem, i, ext))
        if not os.path.exists(cand):
            return cand, 'move_renamed'
        if sha256(cand) == sha256(src_path):
            return cand, 'duplicate'
    raise RuntimeError('cannot resolve name for %s' % name)


def main():
    apply = '--apply' in sys.argv
    print('MODE = %s' % ('APPLY' if apply else 'DRY-RUN'))
    print()

    if apply:
        for d in ALL_DIRS:
            os.makedirs(os.path.join(SCRIPTS, d), exist_ok=True)

    plan = []
    unrouted = []

    for src_dir, table in ROUTES.items():
        if not os.path.isdir(src_dir):
            print('SKIP missing source dir: %s' % src_dir)
            continue
        for name in sorted(os.listdir(src_dir)):
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src) or not name.lower().endswith('.py'):
                continue
            sub = table.get(name)
            if sub is None:
                unrouted.append(src)
                continue
            target_dir = os.path.join(SCRIPTS, sub)
            if not os.path.isdir(target_dir) and not apply:
                dest, action = os.path.join(target_dir, name), 'move'
            else:
                dest, action = unique_target(target_dir, name, src)
            plan.append({
                'src': src,
                'dest': dest,
                'action': action,
                'size': os.path.getsize(src),
            })

    by_dir = {}
    for item in plan:
        by_dir.setdefault(os.path.dirname(item['dest']), []).append(item)

    for d in sorted(by_dir):
        print('=> %s' % d)
        for item in sorted(by_dir[d], key=lambda x: x['dest']):
            tag = {'move': '', 'move_renamed': '  [RENAMED]',
                   'duplicate': '  [DUPLICATE -> will delete source]'}[item['action']]
            print('     %-34s <- %s%s'
                  % (os.path.basename(item['dest']), item['src'], tag))
        print()

    if unrouted:
        print('!! UNROUTED (left in place, add to ROUTES):')
        for p in unrouted:
            print('   %s' % p)
        print()

    print('TOTAL %d files' % len(plan))

    if not apply:
        print('\nDry run only. Re-run with --apply to execute.')
        return 0

    manifest = {
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'description': 'D:\\finance 散落 py 脚本归类记录，可用 revert_scripts.py 回滚',
        'moves': [],
    }
    moved = deleted = 0
    for item in plan:
        src = item['src']
        # Re-resolve the destination at execution time. The plan is computed up
        # front, so two sources with the same basename would otherwise both be
        # assigned the same destination and the second move would silently
        # overwrite the first.
        dest, action = unique_target(os.path.dirname(item['dest']),
                                     os.path.basename(src), src)
        if action == 'duplicate':
            os.remove(src)
            manifest['moves'].append({'src': src, 'dest': dest,
                                      'action': 'deleted_duplicate'})
            deleted += 1
            print('DUP-DEL %s (identical to %s)' % (src, dest))
            continue
        if os.path.exists(dest):
            raise RuntimeError('refusing to overwrite existing file: %s' % dest)
        shutil.move(src, dest)
        manifest['moves'].append({'src': src, 'dest': dest, 'action': 'moved'})
        moved += 1
        print('MOVED   %s -> %s%s'
              % (src, dest, '  [RENAMED]' if action == 'move_renamed' else ''))

    # remove D:\finance\data\scripts if it became empty
    if os.path.isdir(DATA_SCRIPTS) and not os.listdir(DATA_SCRIPTS):
        os.rmdir(DATA_SCRIPTS)
        manifest['removed_dirs'] = [DATA_SCRIPTS]
        print('RMDIR   %s (empty)' % DATA_SCRIPTS)

    mpath = os.path.join(SCRIPTS, '_整理清单.json')
    with open(mpath, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print('\nmoved=%d  duplicate_deleted=%d' % (moved, deleted))
    print('manifest -> %s' % mpath)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
