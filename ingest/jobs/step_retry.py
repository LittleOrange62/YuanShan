# -*- coding: utf-8 -*-
"""C24 · 加工段**失败单步的重试阶梯**（用户 2026-10-08 点名）。

要解决的问题很具体：链 17:21 收工时 `fundflow` 因为上游连不上失败了，而在那之后
**没有任何机制会再试一次** —— 要么等明天 15:59 那条链顺手补，要么人记得去敲命令。
"失败了没人知道、知道了也没人补"正是这一轮要收掉的最后一段。

判据（三条都是用户原话里的约束，不许自己放宽）：

1. **只重试之前失败的**：待补的名单来自逐步台账 `status == 'fail'` 的那些步
   （`refused` = 按口径拒写、`ok` = 成功，都不进名单）。每一档跑之前**重新读一次台账**，
   已经补成的当场从名单里摘掉，绝不重复跑一份好数据。
2. **时点可增删可改**：`step_retry.times` 是一串 `HH:MM`，界面上逐条改、加一格、删一格；
   写法坏的格子（不是 HH:MM）跳过并出声，不让一个错别字废掉整条阶梯。
3. **用完就放弃**：最后一档之后还有失败的，记 `gave_up` 并写清"等下一次收盘链一起补"，
   不再半夜反复敲门 —— 反复重试一个真坏了的源只会把上游封得更死。

跨午夜是刻意的：21:00 / 22:00 / 23:00 落在链收工那一天，01:00 / 02:30 / 04:20 落在次日。
分界线取中午 12:00（链最早 15:59 收工，不存在下午的档），比"看日期"更少歧义。

与 15:59 那条链**抢同一把锁**（`fill_all`）：手动补跑、到点链、这一条阶梯是同一个写入方，
两边各判各的"没在跑"会同时起两个 `c11`/`c21`，源侧看到的是一分钟内两笔同样的请求，
然后一起被判成网络故障，谁都不知道为什么。

用法：
    python -m ingest.jobs.step_retry --check          # 只看今天有什么要补、下一档什么时候
    python -m ingest.jobs.step_retry                  # 到点该跑的那一档立刻跑一次（人工演练）
    python -m ingest.jobs.step_retry --lane 22:00     # 显式跑某一档

退出码：0 这一档跑完且没有失败 / 3 没有要补的（不算失败）/ 2 抢不到锁或档期已过 / 1 真失败。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from duanxian import jobs as jstore          # noqa: E402
from duanxian.paths import data_path          # noqa: E402

# 默认阶梯（用户 10-08 原话：21、22、23、隔日 1、后面 2 点多 4 点多随机）
TIMES_DEFAULT = "21:00,22:00,23:00,01:00,02:30,04:20"
#: 到点后最多还认多久的"迟到"。宿主重启、机器睡着、上一档正跑着都会让某一档晚起，
#: 但晚到太久就失去意义了（下一档快到了），所以给一个窗口而不是"当天任意时刻"。
CATCH_UP_MIN = 90
#: 中午之后算"链收工那一天"的档，之前的算次日 —— 见模块开头。
SAME_DAY_FROM_HOUR = 12


def plan_path() -> str:
    return data_path('jobs', 'step_retry_plan.json')


def read_plan() -> dict:
    try:
        with open(plan_path(), encoding='utf-8') as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def write_plan(plan: dict) -> None:
    from duanxian.util import atomic_write_json
    atomic_write_json(plan_path(), plan)


# ------------------------------------------------------------------ 档期

def parse_times(text: str) -> tuple[list[dt.time], list[str]]:
    """`HH:MM[,HH:MM]*` → (能用的档, 写坏了的格子)。坏格子只跳过，不让整条阶梯失效。"""
    out, bad = [], []
    for cell in (text or '').split(','):
        one = cell.strip()
        if not one:
            continue
        m = re.fullmatch(r'([01]?\d|2[0-3]):([0-5]\d)', one)
        if not m:
            bad.append(one)
            continue
        out.append(dt.time(int(m.group(1)), int(m.group(2))))
    return out, bad


def lane_at(day: dt.date, t: dt.time) -> dt.datetime:
    """某一档的绝对时刻。早于中午的算 `day` 的**次日**（跨午夜的 01:00/02:30/04:20）。"""
    base = day if t.hour >= SAME_DAY_FROM_HOUR else day + dt.timedelta(days=1)
    return dt.datetime.combine(base, t)


def lanes(day: dt.date, text: str) -> list[dict]:
    """从"链收工那一天"排出的全部档，按**绝对时刻**升序。

    ⚠️ 不许按钟表时分排：默认那串里 01:00/02:30/04:20 是**次日**，按时分排会把它们
    排到 21:00 前面（10-08 实测就这么错了，于是 21:00 那档永远轮不上、"最后一档"也判错）。
    """
    times, bad = parse_times(text)
    rows = [{'label': t.strftime('%H:%M'), 'at': lane_at(day, t)} for t in times]
    rows.sort(key=lambda r: r['at'])
    return rows + [{'label': b, 'at': None, 'broken': True} for b in bad]


# ------------------------------------------------------------------ 台账读法

def ledger_failures(ledger: dict) -> list[str]:
    """**只有真失败的**才进重试名单。

    `ok`（成功）与 `refused`（按口径拒写：归属日不可证明时写进去才是错）都不重试 ——
    后者每天会以同一个退出码回来一次，把它当故障就会把阶梯变成天天白跑的噪声。
    """
    return [s.get('name') for s in (ledger.get('steps') or [])
            if s.get('status') == 'fail' and s.get('name')]


# ------------------------------------------------------------------ 一轮

def external_run_busy() -> str | None:
    """**别的进程**里有没有正在跑的链或补跑（同一个终端手敲的 `e1_fill_all` 也算）。

    `jobs.claim('fill_all')` 是**进程内**的集合（`_claims`），挡得住"宿主到点的链 vs
    页面上刚点的补跑"，但挡不住人在终端里跑的那一遍 —— 而两个 `c11` 同时打同一个东财接口，
    源侧看到的是同一分钟内两笔同样的请求，然后一起被判成网络故障，谁都不知道为什么
    （这把锁存在的理由本来就是这个）。所以起子进程前先按命令行探一次。
    """
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return None
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | '
             'ForEach-Object { $_.CommandLine }'],
            capture_output=True, text=True, timeout=20,
            encoding='utf-8', errors='replace').stdout or ''
    except Exception:  # noqa: BLE001  探测坏了既不假装"没在跑"也不让阶梯停摆：交 None 让它照判
        return None
    for line in out.splitlines():
        if 'ingest.jobs.e1_fill_all' in line:
            return ('另有一次单步补跑正在跑（终端或另一个窗口）' if '--only' in line
                    else '链正在另一个进程里跑（终端里的 e1_fill_all）')
    return None


def _run_only(names: list[str], timeout: int) -> int:
    """起**那一个**补跑进程。测试进程里硬挡（同 `jobsboard._spawn_step` / `c23.spawn`）。"""
    if os.environ.get('PYTEST_CURRENT_TEST'):
        print('（pytest 进程：不真起补跑子进程，只走判据）')
        return 0
    cmd = [sys.executable, '-X', 'utf8', '-u', '-m', 'ingest.jobs.e1_fill_all',
           '--only', ','.join(names)]
    print('$ %s' % ' '.join(cmd[1:]), flush=True)
    try:
        return subprocess.run(cmd, cwd=str(ROOT), timeout=timeout).returncode
    except Exception as exc:  # noqa: BLE001  超时/起不来都算这一档没成，不许漏出异常
        print('  ⚠️ 补跑子进程异常：%s: %s' % (type(exc).__name__, str(exc)[:160]))
        return 1


def status(now: dt.datetime | None = None) -> dict:
    """今天（按台账那一场）有什么要补、下一档什么时候、阶梯用完了没有。界面与宿主共用这一份。"""
    now = now or dt.datetime.now()
    from ingest.jobs import e1_fill_all as e1
    led = e1.read_ledger() or {}
    merged, _warns = jstore.settings('step_retry', jstore.STEP_RETRY_DEFAULTS)
    text = merged.get('times') or TIMES_DEFAULT
    cfg = {'enabled': bool(merged.get('enabled', True)), 'times': text,
           'catch_up_min': CATCH_UP_MIN}
    if not led:
        return dict(cfg, available=False, reason='还没有逐步台账：加工段至少跑过一次才会有',
                    pending=[], next_at=None, lanes=[], gave_up=False)
    target = led.get('target')
    # 档期属于"哪一遍链"：**不能用 written_at** —— 每跑一档都会刷新它，于是同一档会被
    # 当成新一场、每 60 秒重跑一次。`cycle` 由台账带着：整遍链换新锚点，`--only` 继承旧的。
    written = led.get('written_at')
    cycle = led.get('cycle') or led.get('started_at') or written
    # 日锚点同一条纪律：**用链次那一天，不用"最后一次写台账那一天"**。
    # 10-08 实测被这点咬掉两档：01:00 那档把 written_at 推到 10-09，于是 02:30/04:20
    # 被算成 10-10 凌晨 —— 同一夜剩下的两档永远等不到，`lanes_done` 只有 4 档而
    # `gave_up` 还是 false（既没跑也没放弃，界面上就是一句"下一档还没到"）。
    anchor = str(cycle or written or '')[:10]
    try:
        day = dt.date.fromisoformat(anchor)
    except ValueError:      # 台账里的日期串坏了：按今天判，别把整条阶梯废掉
        day = now.date()
    all_lanes = [l for l in lanes(day, text)]
    plan = read_plan()
    same_run = plan.get('cycle') == cycle
    done = (plan.get('lanes_done') or {}) if same_run else {}
    gave_up = bool(plan.get('gave_up')) if same_run else False
    pend = ledger_failures(led)
    rows = [{'label': l['label'],
             'at': l['at'].isoformat(timespec='minutes') if l.get('at') else None,
             'broken': bool(l.get('broken')),
             'ran_at': (done.get(l['label']) or {}).get('ran_at'),
             'outcome': (done.get(l['label']) or {}).get('outcome')} for l in all_lanes]
    nxt = None
    if pend and cfg['enabled'] and not gave_up:
        for l in all_lanes:
            if not l.get('at') or l['label'] in done:
                continue
            if l['at'] > now:
                nxt = l
                break
    return dict(cfg, available=True, target=target, written_at=written, cycle=cycle,
                day=str(day),
                pending=pend, pending_soft=[s.get('name') for s in (led.get('steps') or [])
                                            if s.get('status') == 'refused'],
                lanes=rows, ran=len(done), gave_up=gave_up,
                next_at=nxt['at'].isoformat(timespec='minutes') if nxt else None,
                next_label=nxt['label'] if nxt else None,
                last_lane=plan.get('last_lane'), last_detail=plan.get('last_detail'))


def due_lane(now: dt.datetime) -> tuple[dict | None, str]:
    """现在该不该跑一档。返回 (档, 为什么不该跑)。不跑的情况要说得出是哪一种。"""
    st = status(now)
    if not st['available']:
        return None, st.get('reason') or '读不到台账'
    if not st['enabled']:
        return None, '阶梯在系统配置里关着（不会自己恢复，要有人再打开）'
    if not st['pending']:
        return None, '没有要重试的步骤（台账里没有非 ok 的步）'
    if st['gave_up']:
        return None, '这一场的档期已用完并放弃，等下一次收盘链一起补'
    written = st.get('cycle')
    plan = read_plan()
    done = (plan.get('lanes_done') or {}) if plan.get('cycle') == written else {}
    for row in _lane_rows(st['day'], st['times']):
        if row['label'] in done or row.get('at') is None:
            continue
        if row['at'] <= now <= row['at'] + dt.timedelta(minutes=CATCH_UP_MIN):
            return row, ''
        if row['at'] > now:
            return None, f'下一档 {row["label"]} 还没到（现在 {now.strftime("%H:%M")}）'
    return None, '所有档都用完了，等下一次收盘链一起补'


def _lane_rows(day: str, text: str) -> list[dict]:
    d = dt.date.fromisoformat(day)
    return [l for l in lanes(d, text) if l.get('at')]


def run_due(now: dt.datetime | None = None, *, force_lane: str = '') -> dict:
    """跑**该跑的那一档**：只跑名单里的步，跑完重读台账摘掉成功的。"""
    now = now or dt.datetime.now()
    st = status(now)
    if force_lane:
        rows = _lane_rows(st.get('day') or str(now.date()), st['times'])
        row = next((r for r in rows if r['label'] == force_lane), None)
        why = '' if row else '指定了这一档但档期里没有它：%s' % force_lane
    else:
        row, why = due_lane(now)
    if row is None:
        return {'ok': False, 'rc': 3, 'skipped': True, 'reason': why, 'ran': [], 'pending': st['pending']}
    if not st['pending']:
        return {'ok': False, 'rc': 3, 'skipped': True, 'reason': '没有要重试的步骤', 'ran': []}

    from ingest.jobs import e1_fill_all as e1
    names = st['pending']
    # 超时按"这一步自己的上限加起来"给，不给一个通用值：一次阶梯可能连着跑
    # c11(网络) + c21(网络) + d6(算)，各步上限差两个数量级。
    budget = sum(e1.step_timeout(n) for n in names if n in {s for s, _w, _c in e1.STAGES})
    busy = external_run_busy()
    if busy:
        # 同 `claim` 那条：不算失败、也不算用过这一档，下一档再试。
        return {'ok': False, 'rc': 2, 'skipped': True, 'busy': True, 'ran': [],
                'reason': f'{busy}，这一档顺延到下一档', 'lane': row['label']}
    if not jstore.claim('fill_all'):
        # 抢不到锁不是失败：那一头正跑着（15:59 的链、或页面上刚点了一次补跑）。
        # 这一档**不算用过**，下一档还会再试 —— 把它记成失败会天天刷一笔假故障。
        return {'ok': False, 'rc': 2, 'skipped': True, 'busy': True, 'ran': [],
                'reason': '加工段正在跑（15:59 那条链或一次手动补跑），这一档顺延到下一档',
                'lane': row['label']}

    try:
        rc = _run_only(names, budget or e1.STEP_TIMEOUT_SEC)
        after = status(dt.datetime.now())
        still = after['pending']
        ran_now = [n for n in names if n not in still]
        outcome = 'ok' if not still else ('partial' if ran_now else 'fail')
        plan = read_plan()
        same = plan.get('cycle') == st.get('cycle')
        done = dict(plan.get('lanes_done') or {}) if same else {}
        all_done = [r['label'] for r in _lane_rows(st['day'], st['times'])]
        done[row['label']] = {'ran_at': now.isoformat(timespec='seconds'), 'outcome': outcome,
                              'ran': names, 'rc': rc}
        last_one = row['label'] == all_done[-1] if all_done else True
        gave_up = bool(st['gave_up']) or (last_one and bool(still))
        # 锚点用**跑完之后**那一份台账的 cycle：`--only` 会继承旧 cycle，所以档期不会被
        # 自己写下的台账刷新打断（否则同一档会 60 秒一轮地重跑，见 `status()` 里那段）。
        write_plan({'cycle': (after.get('cycle') or st.get('cycle')),
                    'written_at': after.get('written_at') or st.get('written_at'),
                    'target': st.get('target'),
                    'lanes_done': done, 'gave_up': gave_up, 'last_lane': row['label'],
                    'last_detail': {'outcome': outcome, 'still_failing': still,
                                    'recovered': ran_now, 'rc': rc}})
        reason = ('档 %s：%s 补成功了' % (row['label'], '、'.join(ran_now)) if ran_now
                  else '档 %s：跑完还是没成（%s）' % (row['label'], '、'.join(still) or '无'))
        if gave_up and still:
            reason += '；档期用完，放弃这一场，等下一次收盘链一起补'
        # **阶梯这一格记的是"这一档有没有把该补的补动"，不是"整条链干净了没有"**。
        # 2026-10-12 实测：21:00 档把 quality 补成功了、只剩 fundflow（东财 push2 那个口被封，
        # 今晚补不动、明晚也补不动），而 `ok=not still` 把这个结果记成"连败 1、最近成功停在 10-10"
        # —— doctor 于是念出一句"这一场要么没跑要么没跑成"，可它明明跑了还补成了一步。
        # 一个外部源永久封死的日子里，这条会天天涨连败，把真故障（阶梯自己没起、崩了）淹掉，
        # 与 #85 那条 tdx_watch 夜间假红同一个形状。**还差哪几步**照样在 detail.still_failing 里，
        # 并由 doctor 的链台账那一项单独点名（那里才是"数据缺不缺"的口径），不在这一格重复判红。
        worked = bool(ran_now) or not still
        jstore.record('step_retry', ok=worked,
                      detail={'skipped': False, 'lane': row['label'], 'outcome': outcome,
                              'ran': names, 'recovered': ran_now, 'still_failing': still,
                              'rc': rc, 'gave_up': gave_up, 'reason': reason})
        return {'ok': not still, 'worked': worked, 'rc': 0 if not still else 1, 'lane': row['label'],
                'ran': names, 'recovered': ran_now, 'still_failing': still,
                'gave_up': gave_up, 'reason': reason}
    finally:
        jstore.release('fill_all')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='加工段失败单步的重试阶梯')
    ap.add_argument('--check', action='store_true', help='只看档期与待补名单，不跑')
    ap.add_argument('--lane', default='', help='显式跑某一档（如 22:00）')
    args = ap.parse_args(argv)

    st = status()
    print('=== 失败单步重试阶梯 ===')
    print('  开关 %s　时点 %s　到点后认 %d 分钟内迟到' %
          ('开' if st.get('enabled') else '关', st.get('times'), st.get('catch_up_min') or 0))
    if not st.get('available'):
        print('  %s' % st.get('reason'))
        return 3
    print('  台账：目标日 %s（写于 %s）' % (st.get('target'), st.get('written_at')))
    print('  要重试 %s 步：%s' % (len(st['pending']), '、'.join(st['pending']) or '无'))
    if st.get('pending_soft'):
        print('  按口径拒写、不进重试名单：%s' % '、'.join(st['pending_soft']))
    print('  档期：%s' % '、'.join('%s%s' % (r['label'], '（已用）' if r['ran_at'] else '')
                                   for r in st['lanes']))
    print('  下一档 %s　%s' % (st.get('next_at') or '没有（都用完了或已放弃）',
                              '已放弃：等下一次收盘链补' if st.get('gave_up') else ''))
    if args.check:
        return 0
    out = run_due(force_lane=args.lane)
    print('  %s' % out.get('reason'))
    return int(out.get('rc') or 0)


if __name__ == '__main__':
    raise SystemExit(main())
