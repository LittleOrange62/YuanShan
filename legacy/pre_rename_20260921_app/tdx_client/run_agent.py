"""
`ft-tdx-agent` 入口。

用法：

    ft-tdx-agent                      # 全链路（日线 + 分钟线 + 入库）
    ft-tdx-agent --day-only           # 只要日线，跳过分钟线那一遍
    ft-tdx-agent --check              # **只判定不动手**，看数据到哪一天了
    ft-tdx-agent --only s6_day        # 只重跑某一步（卡住之后接手用）
    ft-tdx-agent --skip s9_ingest     # 跳过某一步
    ft-tdx-agent --attempts 3         # 失败就清进程从 s1 重来，最多 3 轮
    ft-tdx-agent --probe              # 只 dump 控件树，什么都不点
    ft-tdx-agent --date 2026-08-25    # 指定目标交易日
    ft-tdx-agent --force-restart      # 强制重启通达信
    ft-tdx-agent --legacy             # 顺手同步一份到 D:\\finance\\data

出口码分三档，**给页面和智能体区分处境用**：

    0  全部完成
    2  需要人工/智能体接手（定位失败、"已在别处登录"这类）
    1  真失败

`--check` 是最常用的一个：跑一次就知道"到底要不要动通达信"，
而且完全只读，任何时候跑都安全。

**`--attempts` 的语义是"从头再来"**，不是"重试某一步"：每轮开头先
`reset.kill_all()` 把 `TdxW.exe` + `TPyth*` 全杀掉，再建一个全新的
`RunContext` 从 s1 跑。步骤内部的重试是另一层（`Step.retry`），
两层不重叠 —— 步骤级重试解决"点了没生效"，链路级重来解决"通达信崩了/
残留状态不对"。第 1 轮**不杀进程**：常态是通达信已经登录好摊在那，
杀掉重来要多花两三分钟登录时间。

"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from app.tdx_client import creds, locator, ps, readiness, reset, steps, watchdog, win32
from app.tdx_client.machine import Machine, RunContext
from app.tdx_client.steps import s8_finish

EXIT_OK, EXIT_FAIL, EXIT_HANDOFF = 0, 1, 2

# **先把 stdout/stderr 掰成 utf-8 且容错，再谈别的。**
# 实测 2026-08-27：SafeEdit 回读出来的是反键盘记录的混淆缓冲区，里面含
# '\u0368' 这类组合符号；日志把它打出来时 Windows 默认的 GBK 控制台编码抛
# `UnicodeEncodeError: 'gbk' codec can't encode character '\u0368'`，
# 于是 **s2 的 act() 是被一条日志语句弄失败的，登录动作本身其实已经做完**。
# 这种失败最难查：报错指向编码，跟通达信毫无关系，还会连累重试两遍。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y%m%d', '%Y/%m/%d'):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f'认不出日期：{s!r}（用 2026-08-25 或 20260825）')


def cmd_check(target: dt.date, r: readiness.Readiness) -> int:
    """
    只读判定。**不启动、不点击、不下载**，随时可跑。

    输出直接回答"要不要动通达信"这个问题，而不是罗列一堆状态。
    """
    print(f'目标交易日：{target}')
    running, who = locator.sync_running()
    main = locator.main_window()
    print(f'通达信主窗口：{main.text if main else "未运行"}')
    print(f'同步进程：{who}')
    print()
    v_idx = r.index_ready(target)
    v_day = r.day_ready(target)
    v_m1 = r.minute_ready(target, period=1)
    v_m5 = r.minute_ready(target, period=5)
    v_db = r.db_ready(target)
    for v in (v_idx, v_day, v_m1, v_m5, v_db):
        print(v.report())
        print()

    todo = []
    if not v_idx.ok:
        todo.append('登录通达信等自动同步（s1~s4）')
    if not v_day.ok:
        todo.append('手动触发盘后日线下载（s5~s6）')
    if not (v_m1.ok and v_m5.ok):
        todo.append('手动触发分钟线下载（s7）')
    if not v_db.ok:
        todo.append('解析入库（s9）')
    if todo:
        print('需要做的事：')
        for i, t in enumerate(todo, 1):
            print(f'  {i}. {t}')
        return EXIT_FAIL
    print('全部到位，无需操作。')
    return EXIT_OK


def cmd_probe() -> int:
    """
    只 dump 控件树。**一个点击都不发。**

    这是接手排查的第一条命令：先看清界面上到底有什么，
    再决定改哪个定位参数。dump 结果落到 `var/stuck/probe_<时间戳>/`。
    """
    from app.config import VAR_DIR

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    out = VAR_DIR / 'stuck' / f'probe_{stamp}'
    out.mkdir(parents=True, exist_ok=True)

    wins = win32.top_windows(visible_only=False)
    (out / 'windows.txt').write_text('\n'.join(str(w) for w in wins),
                                     encoding='utf-8')
    print(f'顶层窗口 {len(wins)} 个 → {out / "windows.txt"}')

    for w in wins:
        if not w.text or not w.visible:
            continue
        if not (locator.MAIN_TITLE in w.text or 'TQ' in w.text
                or '登录' in w.text or '下载' in w.text):
            continue
        safe = ''.join(c for c in w.text if c.isalnum() or c in '（）()_-')[:40]
        tree = win32.children(w.hwnd, max_depth=8)
        (out / f'tree_{safe}.txt').write_text(
            f'{w}\n' + '\n'.join(str(c) for c in tree), encoding='utf-8')
        n, err = ps.dump_to_file(w.text[:20], out / f'uia_{safe}.json')
        print(f'  {w.text!r}: Win32 {len(tree)} 个控件，UIA {n} 个节点'
              + (f'（UIA 失败：{err}）' if err else ''))

    win32.screenshot(out / 'screen.png')
    print(f'截图 → {out / "screen.png"}')
    print(f'\n现场包：{out}')
    return EXIT_OK


def _run_attempt(args, target: dt.date, r: readiness.Readiness,
                 attempt: int, attempts: int) -> tuple[int, list, str]:
    """
    跑一遍完整链路。返回 `(出口码, records, 汇总文本)`。

    **一次尝试 = 一个全新的 `RunContext` + 一个全新的 `Machine`。**
    复用会把上一轮的 `bag['pid']`、`dialog_hwnd` 带进来，那些句柄在进程被
    杀掉之后全是野值，后面每一步都会以"控件没反应"失败。
    """
    ctx = RunContext(target=target, readiness=r, dry_run=args.dry_run)
    ctx.bag['force_restart'] = args.force_restart
    ctx.bag['legacy'] = args.legacy
    ctx.bag['attempt'] = attempt
    head = f'第 {attempt}/{attempts} 轮 · ' if attempts > 1 else ''
    ctx.log(f'{head}目标交易日 {target}；'
            f'{"dry-run，" if args.dry_run else ""}'
            f'{"只跑 " + ",".join(args.only) if args.only else "全链路"}')

    machine = Machine(steps.build(args.only, day_only=args.day_only))

    wd = None
    if not args.no_watchdog and not args.dry_run:
        pids = win32.pids_by_name('TdxW.exe')
        wd = watchdog.PopupWatchdog(pid=pids[0] if pids else None).start()
        ctx.log(f'弹窗看门狗已启动（规则来源：{wd.rules_source}）')
    try:
        ok, records = machine.run(ctx, only=args.only, skip=args.skip)
    finally:
        if wd is not None:
            wd.stop()
            print()
            print(wd.report())
        # **收尾无条件跑一次。** 中途 handoff/失败时状态机会把 s8_finish 一起
        # 标成 aborted，通达信就被最大化摊在屏幕上没人管 —— 2026-08-28
        # s5 打不开菜单就是这样，最后要人手动去最小化。
        # dry-run 不动界面，所以不收；只跑某几步时也收，反正是幂等的。
        if not args.dry_run:
            ctx.log(f'收尾：{s8_finish.cleanup(ctx.bag.get("pid"))}')

    if ok:
        return EXIT_OK, records, machine.summary()
    handoff = any(x.state == 'handoff' for x in records)
    return (EXIT_HANDOFF if handoff else EXIT_FAIL), records, machine.summary()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog='ft-tdx-agent',
        description='通达信数据下载与入库的自动化代理')
    ap.add_argument('--date', help='目标交易日，默认自动推算')
    ap.add_argument('--check', action='store_true', help='只判定不动手（只读）')
    ap.add_argument('--probe', action='store_true', help='只 dump 控件树')
    ap.add_argument('--day-only', action='store_true', help='跳过分钟线那一遍')
    ap.add_argument('--only', action='append', help='只跑这些步骤（可重复）')
    ap.add_argument('--skip', action='append', help='跳过这些步骤（可重复）')
    ap.add_argument('--attempts', type=int, default=1,
                    help='失败时整条链路重来几轮（每轮开头先杀干净通达信相关'
                         '进程）。默认 1 = 不重来')
    ap.add_argument('--dry-run', action='store_true',
                    help='每步只跑 precheck 和 verify，不动界面')
    ap.add_argument('--force-restart', action='store_true', help='强制重启通达信')
    ap.add_argument('--legacy', action='store_true',
                    help='顺手同步一份到 D:\\finance\\data（口径有偏，只作副本）')
    ap.add_argument('--extra-samples', type=int, default=0,
                    help='就绪判定时每个市场多抽几只票（默认只用锚定代码）')
    ap.add_argument('--no-watchdog', action='store_true', help='不开弹窗看门狗')
    ap.add_argument('--list-steps', action='store_true')
    args = ap.parse_args(argv)

    if args.list_steps:
        for c in steps.ALL_STEPS:
            print(f'{c.key:<14} {c.title}')
        return EXIT_OK

    # **Session 0 自检放在最前面。** 服务/计划任务勾了"不管用户是否登录"
    # 时枚举不到任何窗口，后面每一步都会以"找不到控件"失败，
    # 而真正原因跟控件无关。宁可在这里直接拒绝。
    ok, why = win32.session_ok()
    print(f'会话自检：{"通过" if ok else "**不通过**"} —— {why}')
    if not ok and not args.check:
        return EXIT_HANDOFF

    # **输入桌面能力另判一次。** 这跟"能不能枚举窗口"是两件事：
    # 实测本机能读到通达信全部控件，但 GetCursorPos 是 ACCESS_DENIED、
    # GetForegroundWindow 返回 0 —— 鼠标点击和 SendKeys 全部静默失效。
    # 提前打出来，省得后面看到 `SetCursorPos(...) 失败` 一头雾水。
    in_ok, in_why = win32.input_desktop_ok()
    print(f'输入桌面：{"可用" if in_ok else "**不可用**"} —— {in_why}')
    if not in_ok:
        print('  → 全程只走消息路径（PostMessage / SetFocus / BM_CLICK），'
              '坐标点击与 SendKeys 两级自动放弃。')


    r = readiness.Readiness(extra_samples=args.extra_samples)
    target = parse_date(args.date) or readiness.expected_trade_date()

    if args.probe:
        return cmd_probe()
    if args.check:
        return cmd_check(target, r)

    if not creds.has_credentials():
        print('**缺少通达信凭据**：请在 .env 里设 FT_TDX_ACCOUNT 和 '
              'FT_TDX_PASSWORD（代码里不放明文）')
        return EXIT_HANDOFF

    attempts = max(1, args.attempts)
    code, records, summary = EXIT_FAIL, [], ''
    for attempt in range(1, attempts + 1):
        # **第 2 轮起先把进程清干净再重来。** 第 1 轮不清：这条链路的常态是
        # 通达信已经登录好摊在那，杀掉重来要多花两三分钟登录时间。
        # 要第一轮就清的话用 `--force-restart`（那个只重启主程序）
        # 或者手动 `python -m app.tdx_client.reset`。
        if attempt > 1:
            print()
            print(f'=== 第 {attempt}/{attempts} 轮：先清掉通达信相关进程再从 s1 重来 ===')
            ok_kill, why_kill = reset.kill_all(log=lambda m: print(m, flush=True))
            print(f'  {why_kill}')
            if not ok_kill:
                print('  进程没清干净，重来大概率还是同样的失败 —— 直接交出去。')
                break
        code, records, summary = _run_attempt(args, target, r, attempt, attempts)
        print()
        print(f'步骤汇总（第 {attempt}/{attempts} 轮）：' if attempts > 1
              else '步骤汇总：')
        print(summary)
        if code == EXIT_OK:
            break
        if attempt < attempts:
            print(f'\n本轮出口码 {code}，还有 {attempts - attempt} 轮可试。')

    if code == EXIT_OK:
        print('\n全部完成。')
        return EXIT_OK
    handoff = [r_ for r_ in records if r_.state == 'handoff']
    if handoff:
        print('\n**需要接手**：')
        for r_ in handoff:
            print(f'  {r_.title}：{r_.message}')
            if r_.detail.get('stuck_dir'):
                print(f'    现场包：{r_.detail["stuck_dir"]}')
        print('  先看现场包里的 screen.png 和 tree_*.txt，'
              '确认界面实际停在哪；改完定位参数用 --only 重跑那一步。')
        return EXIT_HANDOFF
    print('\n有步骤失败，见上面的现场包路径。')
    return EXIT_FAIL


if __name__ == '__main__':
    sys.exit(main())
