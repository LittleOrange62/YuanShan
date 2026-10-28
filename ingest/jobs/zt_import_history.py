# -*- coding: utf-8 -*-
"""
把外部播报系统的历史日志 `zt_events.jsonl` 结构化进库（一次性搬迁，可重复跑）。

为什么要搬：那份日志是这七套判据**唯一的历史留痕**（每行一条 JSON，356 行、2026-10-19~10-26）。
外部脚本只往飞书群发文本，库里从来没有过这些数；不搬的话一迁完，历史就只剩一个文件，
而文件在被删掉的工作区里（用户裁定：非本项目目录随时会清）。

跑法：
    python -m ingest.jobs.zt_import_history                 # 用默认外部路径，只读它
    python -m ingest.jobs.zt_import_history --file <路径> --dry-run

三条规矩：
· **只读外部文件**，一个字都不写回去（那个目录随时会被删，改它没有意义且越界）。
· 幂等：撞键就跳过，重跑不会插出第二行；报"新插入 / 已存在"两个数，别看个总数就当成功。
· 旧日志的字段是按**旧默认值**填的（`lbc` 缺→1、`zbc` 缺→0），历史行**原样保留**并在
  `source_detail` 里标 `legacy_default_filled=true` —— 我不事后猜哪些是填的（猜错就是把
  一只 3 板票的历史改成首板）。新针从这里开始才走 NULL 口径，两边靠 `spec_version` 分开。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.intraday import store                       # noqa: E402
from app.intraday.pool_events import SPEC_VERSION, KIND_LABELS   # noqa: E402

DEFAULT_LOG = Path(r'C:\Users\Administrator\.qwenpaw\workspaces\yuanshan008'
                   r'\scripts\intraday_zt\var\zt_events.jsonl')

#: 外部中文标签 → 我们的机器名（旧日志里存的就是中文）
LABEL_KIND = {v: k for k, v in KIND_LABELS.items()}
#: 外部旧写法里 lbc/zbc 用 0/1 填过缺数，两边机器名不同名时的补集
LABEL_KIND.update({'跌停开板': 'dt_open', '新跌停': 'dt_seal_new'})

TASK_BY_WHAT = {'digest': 'watcher', 'sent': 'watcher', 'poll_fail': 'watcher',
                'auction': 'auction', 'auction_poll_fail': 'auction',
                'auction_stale': 'auction', 'auction_source_fallback': 'auction',
                'heat': 'heat', 'noon': 'noon', 'noon_em_ulist_fail': 'noon',
                'sysentry': 'sysentry', 'sysentry_crash': 'sysentry',
                'sysentry_quote_fail': 'sysentry', 'sysentry_quote_stale': 'sysentry',
                'sysentry_tdxw': 'sysentry', 'sysentry_tq_fail': 'sysentry',
                'pg_fail': 'watcher', 'wx_sent': 'watcher', 'wx_warn': 'watcher'}


def _at(obj: dict) -> dt.datetime:
    """旧日志的 `at` 是 '%Y-%m-%d %H:%M:%S' 本机墙钟字符串。解不出来就交 None 让调用方出声。"""
    raw = str(obj.get('at') or '')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _event_row(kind: str, e: dict) -> dict:
    """旧日志里一行事件 → normalize() 的形状（字段名不同，语义一一对上）。"""
    return {
        'code': str(e.get('code', '')),
        'name': e.get('name'),
        'pct': e.get('pct'),
        'price': e.get('price'),
        'board_count': e.get('lbc'),          # 旧日志已按 `or 1` 填过，原样留（见模块说明）
        'open_count': e.get('zbc'),
        'first_seal_time': e.get('fbt') or None,
        'seal_fund_yuan': e.get('fund'),
        'industry': e.get('hybk') or None,
        'missing': {},
        'kind': kind,
    }


def import_file(path: Path, *, dry_run: bool) -> int:
    if not path.is_file():
        print(f'找不到日志文件：{path}')
        return 1
    lines = [l for l in path.read_text(encoding='utf-8', errors='replace').splitlines() if l.strip()]
    n_events = n_bcast = n_bad = 0
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            n_bad += 1
            continue
        at = _at(obj)
        if at is None:
            n_bad += 1
            continue
        what = obj.get('what', '')
        task = TASK_BY_WHAT.get(what)
        if task is None:
            n_bad += 1
            continue
        evs = [_event_row(LABEL_KIND.get(e.get('kind'), e.get('kind', '')), e)
               for e in (obj.get('events') or [])]
        evs = [e for e in evs if e['kind'] in KIND_LABELS]
        if evs and not dry_run:
            # "旧默认值填过 lbc/zbc"这件事记在**播报行的 source_detail**里，不改事件表：
            # 事后猜哪几行是填的，等于把一只 3 板票的历史改成首板。
            n_events += store.write_events(at, evs,
                                           pool_n={'zt': obj.get('pool_n'), 'zb': None,
                                                   'dt': obj.get('dt_n')})
        counts = {k: v for k, v in obj.items() if k not in ('what', 'at', 'text', 'events')}
        if not dry_run:
            n_bcast += store.write_broadcast(
                at, task=task, what=what, body=obj.get('text', ''),
                ok=obj.get('ok'), delivered=obj.get('ok'), err=obj.get('err'),
                counts=counts,
                source_detail={'from_legacy_jsonl': str(path), 'spec_version': SPEC_VERSION,
                               'legacy_default_filled': what == 'digest'})
    print(f'读到 {len(lines)} 行 · 事件新插入 {n_events}（撞键跳过）· '
          f'播报行写入/覆盖 {n_bcast}（这条本来就要幂等更新发送状态）· '
          f'解不出/未知类型 {n_bad}' + ('（dry-run 未写库）' if dry_run else ''))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='导入外部盘中播报的历史 jsonl')
    ap.add_argument('--file', default=str(DEFAULT_LOG))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    return import_file(Path(args.file), dry_run=args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
