# -*- coding: utf-8 -*-
"""历史回算的可续批驱动器。

一天一趟的判据表（埋伏票 ~65 秒/场、突破信号 ~2 秒/场）回算近一年要跑几十分钟到几小时。
人不该守着它，机器也不该把它挤进 15:59 的收盘链 —— 这两件事都在这里落地：

    python scripts/backfill_history.py --module app.compute.ambush_setup \
        --extra --since --extra 2025-10-13 --chunk-minutes 100 --hold 15:40-18:10

三条规矩：
① **只认退出码**：0 算完 / 4 预算用完（还有剩）/ 3 有场次失败 / 1、2 停下来要人看。
   4 和 3 都自动再来一趟，靠被调模块自己的 `--skip-done` 跳过已经写好的场次，不重算；
② **收盘链那段时间不抢机器**：`--hold` 内的时段不起新趟；起在窗口之前的趟，
   预算自动收到「距窗口起点还剩几分钟」，到点自己收手（不是跑一半压进链里）；
③ 每一趟的原始输出落进 `.local/backfill_<模块名>_<日期>.log`，每趟结束打一行
   时刻 + rc + 日志末行，出事时有账可查。

退出码：0 全部算完 / 5 达到最大趟数还没算完 / 1、2 被调模块说没法跑。
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / '.local'


def _clock(s: str) -> dt.time:
    return dt.time.fromisoformat(s.strip())


def _window_bounds(hold: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """`15:40-18:10` → 今天那两个时刻；跨午夜写法（`22:00-06:00`）把终点挪到明天。"""
    lo_s, hi_s = hold.split('-', 1)
    lo = dt.datetime.combine(now.date(), _clock(lo_s))
    hi = dt.datetime.combine(now.date(), _clock(hi_s))
    if hi <= lo:
        hi += dt.timedelta(days=1)
    return lo, hi


def _sleep_until(target: dt.datetime, why: str) -> None:
    secs = max(1, int((target - dt.datetime.now()).total_seconds()))
    print(f'⏸ {why}：睡 {secs // 60} 分钟到 {target:%m-%d %H:%M} 再继续', flush=True)
    time.sleep(secs)


def _tail(log: Path, n: int = 2) -> str:
    try:
        lines = [x.rstrip() for x in log.read_text(encoding='utf-8', errors='replace').splitlines() if x.strip()]
    except OSError:
        return '(读不到日志)'
    return ' / '.join(lines[-n:])


def main() -> int:
    ap = argparse.ArgumentParser(description='可续批的历史回算驱动器')
    ap.add_argument('--module', required=True, help='被调模块，如 app.compute.ambush_setup')
    ap.add_argument('--extra', action='append', default=[],
                    help='传给模块的参数，可重复（如 --extra --since --extra 2025-10-13）')
    ap.add_argument('--chunk-minutes', type=int, default=100,
                    help='每趟最多跑多少分钟（交给被调模块的 --max-minutes 自限）')
    ap.add_argument('--hold', default='15:40-18:10', help='收盘链占用时段，这期间不起新趟')
    ap.add_argument('--max-rounds', type=int, default=40, help='最多起多少趟，防呆')
    ap.add_argument('--no-skip-done', dest='skip_done', action='store_false',
                    help='默认带 --skip-done（续跑不重算已经写好的场次）')
    a = ap.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    name = a.module.rsplit('.', 1)[-1]
    log = LOG_DIR / f'backfill_{name}_{dt.date.today():%m%d}.log'
    rounds = 0
    while rounds < a.max_rounds:
        now = dt.datetime.now()
        lo, hi = _window_bounds(a.hold, now)
        if lo <= now < hi:
            _sleep_until(hi, f'收盘链窗口 {a.hold} 内')
            continue
        # 窗口还没到：这一趟的预算不能跨过起点，否则就跑进链的时间里了
        budget = a.chunk_minutes
        if now < lo:
            mins_to_hold = int((lo - now).total_seconds() // 60)
            if mins_to_hold < 1:
                _sleep_until(hi, '距收盘链窗口不足一分钟')
                continue
            budget = min(budget, mins_to_hold)

        rounds += 1
        cmd = [sys.executable, '-X', 'utf8', '-m', a.module,
               '--max-minutes', str(budget)]
        if a.skip_done and '--skip-done' not in a.extra:
            cmd.append('--skip-done')
        cmd += list(a.extra)
        stamp = dt.datetime.now().strftime('%m-%d %H:%M:%S')
        with log.open('a', encoding='utf-8') as fh:
            fh.write(f'\n===== 第 {rounds} 趟 {stamp}（预算 {budget} 分钟）：'
                     f'{subprocess.list2cmdline(cmd)} =====\n')
            fh.flush()
            rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(ROOT))
        print(f'[{stamp}] 第 {rounds} 趟 rc={rc} 预算={budget}分钟｜{_tail(log)}', flush=True)
        if rc == 0:
            print(f'✅ {name} 这一批已经算完（共 {rounds} 趟）。日志：{log}', flush=True)
            return 0
        if rc in (3, 4):
            continue                    # 有失败场次 / 预算用完：再来一趟，只补没成的
        print(f'⛔ rc={rc}：被调模块自己拒绝继续（没有可算的交易日 / 一趟都没写进去），'
              f'需要人看一眼。日志：{log}', flush=True)
        return rc
    print(f'⚠️ 起了 {rounds} 趟还没算完，停在这里（日志：{log}）', flush=True)
    return 5


if __name__ == '__main__':
    raise SystemExit(main())
