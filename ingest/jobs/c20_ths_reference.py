# -*- coding: utf-8 -*-
r"""
C20 · 同花顺本地补充参考数据入库（港股席位 + 用户自定义指标档案）。

2026-09-22 把同花顺本地文件全量摸底了一遍，明文可用、c13/c14 又没覆盖的
只剩这两样，都在这一个脚本里（数据量都只有 KB 级，没有拆成两个作业的价值）：

    reference_data.ths_hk_broker  ← BrokerInfoUpdate\hkbrokerinfo
        席位代码→经纪商名。现在库里没有港股行情，这张表是提前备好的名字字典，
        以后做港股通席位分析时不至于再去翻客户端目录。
    config.ths_user_formula       ← <用户名>\syncConfig\function\*.hxf
        用户在客户端攒的指标公式。带 hf1.0 魔数的能抽原文；从云端指标库
        下载的那类加密存放，source 存 NULL —— **照实记"读不出"，不硬凑**。

**两张表都是"镜像整批 upsert"**：席位表文件里没有删除标记，客户端删了席位
只会从文件里消失，所以先 DELETE 全表再插（同 c13 的 THS_ 段口径）；
指标档案以文件名为键，删掉的指标库里留着也只是历史 —— 跟着消失才对，同样先删后插。

用法：
    python -m ingest.jobs.c20_ths_reference --dry-run
    python -m ingest.jobs.c20_ths_reference
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.datasource import ths_local  # noqa: E402
from app.db import engine  # noqa: E402


def write(brokers: list[dict], formulas: list[dict]) -> dict[str, int]:
    stat = {}
    engine.execute('DELETE FROM reference_data.ths_hk_broker', role='app')
    engine.executemany(
        'INSERT INTO reference_data.ths_hk_broker (broker_id, name) '
        'VALUES (%(broker_id)s, %(name)s) ON CONFLICT (broker_id) DO NOTHING',
        brokers, role='app')
    stat['hk_broker'] = len(brokers)
    engine.execute('DELETE FROM config.ths_user_formula', role='app')
    engine.executemany(
        'INSERT INTO config.ths_user_formula (formula_key, name, source) '
        'VALUES (%(key)s, %(name)s, %(source)s)', formulas, role='app')
    stat['formula'] = len(formulas)
    return stat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    base = ths_local.ths_dir()
    print('=== 读同花顺本地补充数据 ===')
    print('  目录 %s' % base)
    brokers = ths_local.read_hk_brokers(base)['brokers']
    formulas = ths_local.read_user_formulas(base)
    if not brokers and not formulas:
        print('  两样都没读到 —— 检查 FT_THS_DIR 是否指向同花顺安装目录')
        return 1
    firms = {b['name'] for b in brokers}
    print('  港股席位 %s 个（%s 家经纪商）' % (len(brokers), len(firms)))
    readable = [f for f in formulas if f['source']]
    print('  自定义指标 %s 个：抽到公式原文 %s，加密读不出 %s'
          % (len(formulas), len(readable), len(formulas) - len(readable)))
    for f in formulas:
        print('    %-9s %-8s %s' % (f['key'], f['name'],
                                    '%s…' % f['source'][:36].replace('\n', ' ')
                                    if f['source'] else '（加密，无原文）'))

    if args.dry_run:
        print('\nDONE（未写库）')
        return 0

    stat = write(brokers, formulas)
    print('=== 写库 ===')
    print('  reference_data.ths_hk_broker %s 行 / config.ths_user_formula %s 行'
          % (stat['hk_broker'], stat['formula']))
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
