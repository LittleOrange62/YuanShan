# -*- coding: utf-8 -*-
"""
C18 · 通达信板块成分入库（reference_data.sector_member），走 TQ 接口。

**为什么走 TQ 不解析文件**：板块成分在通达信里是私有二进制（`csiblock.dat`
等无官方文档），逆向易错；而通达信自带插件自己也是用
`tq.get_stock_list_in_sector(block_code)` 取成分的。实测该接口直接返回
成分股代码列表（`['000552.SZ', ...]`），干净、口径与客户端一致。
（与 c12 同源：都靠 `app.tdx_client.tq_bridge` 指挥已登录客户端。）

**前置**：通达信已启动并登录（`tq_bridge.available()` 会检查）。

**板块口径**：`reference_data.sector_member.sector_code` 用 `TDX_` + 六位码，和
`market_data.sector_daily`、c17 写入的 `reference_data.sector_info` 一致，入库后能 join。
成分股 `security_code` 直接用接口返回的 `600519.SH` 形式。

**只补不删**：`ON CONFLICT DO NOTHING`。库里已有 26 万行同花顺/迁移来源的
成员关系，本脚本只往 TDX_ 板块上补通达信口径的成分，不动别的来源。

用法：
    python scripts\\ingest\\c18_tdx_membership.py --dry-run
    python scripts\\ingest\\c18_tdx_membership.py
    python scripts\\ingest\\c18_tdx_membership.py --limit 20   # 先试 20 个板块
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.tdx_client import tq_bridge  # noqa: E402

NDJSON = 'tdx_membership.ndjson'

# 在客户端进程里跑的循环：逐个板块取成分，边取边写 ndjson，最后 emit 汇总。
# 放到一个片段里跑是为了避免 587 次子进程往返（每次都要重新 initialize 连接）。
SNIPPET = '''
import json
codes = {codes!r}
out = []
n_empty = 0
with open(OUT_DIR + r"/{ndjson}", "w", encoding="utf-8") as f:
    for code in codes:
        try:
            members = tq.get_stock_list_in_sector(block_code=code)
        except Exception:
            members = None
        if not members:
            n_empty += 1
            continue
        f.write(json.dumps({{"block": code, "members": list(members)}},
                           ensure_ascii=False) + "\\n")
        out.append(len(members))
emit({{"sectors": len(out), "members": sum(out), "empty": n_empty}})
'''


def tdx_blocks(limit: int | None) -> list[str]:
    """库里所有 TDX_880/881 板块码，转成接口要的 `880301.SH` 形式。"""
    rows = engine.query(
        """
        SELECT sector_code FROM reference_data.sector_info
         WHERE sector_code ~ '^TDX_88[01][0-9]{3}$'
         ORDER BY sector_code
        """)
    blocks = ['%s.SH' % r['sector_code'][4:] for r in rows]
    return blocks[:limit] if limit else blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, help='只处理前 N 个板块（调试用）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    ok, why = tq_bridge.available()
    if not ok:
        print('TQ 不可用：%s' % why)
        print('先启动并登录通达信（python -m app.tdx_client.run_agent '
              '--only s1_launch --only s2_login）')
        return 2

    blocks = tdx_blocks(args.limit)
    print('=== 通达信板块成分（TQ）===')
    print('  待取板块 %s 个' % len(blocks))

    if args.dry_run:
        print('  样例板块：%s' % blocks[:5])
        print('\n--dry-run，未调用接口、未写库')
        return 0

    batch_id = ops_repo.begin_batch('tdx_tq_api', 'reference_data.sector_member',
                                    source_path='tq:get_stock_list_in_sector')
    try:
        r = tq_bridge.run_snippet(
            SNIPPET.format(codes=blocks, ndjson=NDJSON),
            timeout=1800, stream=True)
        if not r.get('ok'):
            raise RuntimeError('TQ 片段失败：%s' % r.get('error'))
        print('  接口回显：板块 %s 个有成分，共 %s 条(板块,成员)对，空板块 %s 个'
              % (r['sectors'], r['members'], r['empty']))

        nd = config.VAR_DIR / NDJSON
        pairs: list[tuple] = []
        with nd.open(encoding='utf-8') as f:
            for line in f:
                rec = json.loads(line)
                code6 = rec['block'][:6]
                sc = 'TDX_' + code6
                for ts in rec['members']:
                    pairs.append((sc, ts))
        print('  解析出 %s 条成员关系' % len(pairs))

        engine.executemany(
            """
            INSERT INTO reference_data.sector_member (sector_code, security_code)
            VALUES (%s, %s)
            ON CONFLICT (sector_code, security_code) DO NOTHING
            """, pairs, role='etl')
        ops_repo.end_batch(batch_id, status='ok', row_count=len(pairs))
    except Exception as exc:
        ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise

    print('=== 结果 ===')
    print('  reference_data.sector_member 现有 %s 行 / %s 个板块'
          % (engine.scalar('SELECT count(*) FROM reference_data.sector_member'),
             engine.scalar('SELECT count(DISTINCT sector_code) FROM reference_data.sector_member')))
    print('  其中 TDX_ 板块成员 %s 行 / %s 个板块'
          % (engine.scalar("SELECT count(*) FROM reference_data.sector_member WHERE sector_code LIKE 'TDX_%'"),
             engine.scalar("SELECT count(DISTINCT sector_code) FROM reference_data.sector_member WHERE sector_code LIKE 'TDX_%'")))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
