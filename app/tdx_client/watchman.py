# -*- coding: utf-8 -*-
"""
通达信状态探测与自愈（跑在 web 进程里的后台线程）。

回答的问题只有一个：通达信还开着吗？没开的话能不能自动补开。

    probe()  只读：进程 / 主窗口 / 登录框 / 是否最小化
    heal()   启动或复用 → 自动登录 → 清广告弹窗 → 最小化主窗口
             完整复用 s1~s3 那套已验证过的状态机，不另写一份界面自动化

**两道闸是分开的**（2026-10-06 按用户「tdx 进程要有监控」改）：

| 键 | 默认 | 管什么 | 为什么是这个默认 |
|---|---|---|---|
| `tdx_watch.probe` | **开** | 只读探测（枚举窗口/进程） | 不看界面就不影响任何人；"有没有监控"必须能被回答成"有，而且在跑" |
| `tdx_watch.enabled` | **关** | 自愈（`heal()`：启动客户端、发真实键鼠登录、抢前台） | 会动用户桌面，必须由人点头 |

以前这两个是同一个 `enabled`，所以"探测"跟着自愈一起默认关 —— 于是代码在、没人跑，
正是用户问"有监控吗"时最坏的答案。库里已存的 `tdx_watch.enabled=false` **不改动**：
它现在只表示"自愈关着"，探测靠新键默认开。（`config.setting` 我也写不动 ——
`operations.config_history` 那条触发器权限还没授出来。）

探测结果**两处都要落**，缺一处就等于没人能看见：
① `var/tdx_watch.json`（现场明细，含最近 100 次探测与自愈记录）；
② `duanxian/jobs.py::record('tdx_watch', …)`（`/api/jobs`、任务调度大全、
`manage.py doctor` 读的那份 `state.json`）。
只写 ① 的话页面看不见；只写 ② 的话丢明细。

状态明细落 `var/tdx_watch.json` 而**不是 config.setting**：那张表挂着
operations.trg_config_history 行级触发器，每 10 分钟写一条会把改动历史
淹成噪音（同 app/db/repo/schedule.py 里调度心跳不写库的理由）。

注意生命周期：线程在 web 进程里，**网站停了探测就停**（启动点见 `start()`，
由 `server.py` 的 lifespan 调；判"活没活"只看 `/api/jobs` 的 `scheduler.host_loops`）。
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time

from app import config
from app.tdx_client import locator, reset, win32
from app.tdx_client.machine import Machine, RunContext
from app.tdx_client.readiness import Readiness, expected_trade_date
from app.tdx_client.steps import s1_launch, s8_finish
from app.tdx_client.steps.s1_launch import LaunchStep
from app.tdx_client.steps.s2_login import LoginStep
from app.tdx_client.steps.s3_popup import PopupStep

STATE_PATH = config.VAR_DIR / 'tdx_watch.json'

# 全部开关的默认值。**两道闸分开的理由见模块头**：
# `probe` 是只读枚举窗口，默认开；`enabled` 管自愈（抢前台、发真实键鼠），默认关 ——
# 让网站进程自动拉起每一次都可能碰用户的桌面，这个决定必须由人来做。
_DEFAULTS: dict = {'probe': True, 'enabled': False, 'interval_min': 10,
                   'auto_start': True, 'minimize': True, 'restart_at': ''}
_KEY_PREFIX = 'tdx_watch.'

_heal_lock = threading.Lock()
_thread: threading.Thread | None = None
_start_guard = threading.Lock()


# ---------------------------------------------------------------- 配置

def load_conf() -> dict:
    """读 `config.setting` 里的开关。库连不上时按**未启用**返回。"""
    from app.db.repo import registry as reg_repo
    out = dict(_DEFAULTS)
    try:
        for k in _DEFAULTS:
            v = reg_repo.setting(_KEY_PREFIX + k)
            if v is not None:
                out[k] = v
    except Exception:  # noqa: BLE001
        out['enabled'] = False
        out['db_error'] = True
    try:
        out['interval_min'] = min(1440, max(1, int(out['interval_min'])))
    except (TypeError, ValueError):
        out['interval_min'] = _DEFAULTS['interval_min']
    return out


def save_conf(conf: dict) -> None:
    from app.db.repo import registry as reg_repo
    for k, v in conf.items():
        reg_repo.set_setting(_KEY_PREFIX + k, v,
                             label=f'通达信探测 · {k}', category='tdx_watch')


# ---------------------------------------------------------------- 只读探测

def probe() -> dict:
    """当前通达信状态。只枚举窗口，不动界面、不写库。"""
    pids = win32.pids_by_name(s1_launch.EXE_NAME)
    main = locator.main_window()
    login = s1_launch._login_box(pids[0]) if pids else None
    _syncing, who = locator.sync_running()
    return {'at': _now(),
            'running': bool(pids),
            'pid': (pids or [None])[0],
            'window': main.text if main else None,
            'minimized': s8_finish._minimized(main) if main else None,
            'login_box': login is not None,
            'sync': who}


# ---------------------------------------------------------------- 自愈

def heal(*, force_restart: bool = False, reason: str = '',
         minimize: bool | None = None) -> dict:
    """
    把通达信恢复到"已登录、弹窗已清、窗口已最小化"。

    `force_restart=True` 先 `kill_all` 再起（定时重启用）。
    同一时刻只允许一条自愈在跑：两条链抢同一个登录框会互相踩。
    """
    at = _now()
    if minimize is None:
        minimize = bool(load_conf()['minimize'])
    if not _heal_lock.acquire(blocking=False):
        return {'ok': False, 'busy': True, 'at': at, 'reason': reason,
                'message': '已有一条自愈在跑，本次跳过'}
    try:
        ok, why = win32.session_ok()
        if not ok:
            res = {'ok': False, 'at': at, 'reason': reason,
                   'message': f'当前会话无法操作界面：{why}'}
            _record('heal', res)
            return res
        if force_restart:
            reset.kill_all()

        try:
            target = expected_trade_date()
        except Exception:  # noqa: BLE001  库挂了也不该挡住"把客户端开回来"
            target = dt.date.today()
        ctx = RunContext(target=target, readiness=Readiness(), dry_run=False)
        ctx.log(f'watchman 自愈开始（{reason or "手动"}'
                + ('，先重启客户端' if force_restart else '') + '）')
        machine = Machine([LaunchStep(), LoginStep(), PopupStep()])
        done, records = machine.run(ctx)
        if minimize:
            ctx.log('收尾：' + s8_finish.cleanup(ctx.bag.get('pid'), target=target))
        msgs = [f'{r.title}：{r.state} —— {r.message}' for r in records]
        first_bad = next((r for r in records if r.state in ('failed', 'handoff')),
                         None)
        res = {'ok': done, 'at': at, 'reason': reason, 'records': msgs,
               'message': ('全部完成：已启动/登录、清弹窗'
                           + ('、已最小化' if minimize else ''))
               if done else f'停在「{first_bad.title}」：{first_bad.message}'
                          + ('（现场包见 var/stuck）' if first_bad.detail.get('stuck_dir') else '')}
        _record('heal', res)
        return res
    except Exception as exc:  # noqa: BLE001
        res = {'ok': False, 'at': at, 'reason': reason,
               'message': f'自愈异常：{type(exc).__name__}: {exc}'}
        _record('heal', res)
        return res
    finally:
        _heal_lock.release()


def heal_async(**kw) -> bool:
    """给页面按钮用：起一条后台自愈，立刻返回。True = 已触发。"""
    if _heal_lock.locked():
        return False
    threading.Thread(target=heal, kwargs=kw, name='tdx-heal', daemon=True).start()
    return True


# ---------------------------------------------------------------- 状态文件

def _now() -> str:
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(st: dict) -> None:
    try:
        config.VAR_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(st, ensure_ascii=False, indent=1, default=str),
            encoding='utf-8')
    except Exception:  # noqa: BLE001  状态文件写不坏主流程
        pass


def _record(kind: str, payload: dict, keep: int = 20) -> None:
    st = _load_state()
    lst = st.get(kind) or []
    lst.append(payload)
    st[kind] = lst[-keep:]
    _save_state(st)


# ---------------------------------------------------------------- 探测线程

def start() -> bool:
    """拉起**本模块自带的**探测线程（幂等）。给独立跑 `run_agent`/离线补数那条路用。

    ⚠️ **与宿主的循环二选一**：常驻 uvicorn 里已经有一条
    （`server.py::_tdx_watch_scheduler`，它才是 `jobs/state.json` 里 `tdx_watch` 的写者）。
    网页进程里不要再调本函数，否则同一个信号两条循环各探各的。
    这条线程只做"探测 + 按开关自愈"，明细落 `var/tdx_watch.json`。"""
    global _thread
    with _start_guard:
        if _thread and _thread.is_alive():
            return True
        _thread = threading.Thread(target=_loop, name='tdx-watchman',
                                   daemon=True)
        _thread.start()
        return True


def monitor_alive() -> bool:
    return bool(_thread and _thread.is_alive())


def _loop() -> None:
    # 固定 10s 醒一次，间隔本身按配置算 —— 这样在页面上改间隔不用重启线程。
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            _record('heal', {'ok': False, 'at': _now(), 'reason': 'watchdog',
                             'message': f'探测循环异常：{exc}'}, keep=10)
        time.sleep(10)


def _tick() -> None:
    """醒一次：**探测是无条件的**（除非明确把 `probe` 关掉），自愈要 `enabled` 且在跑。

    顺序是刻意的：先探、先把"探到了什么"交出去，再决定要不要动手。
    反过来（先判 enabled 再 return）就是 2026-10-06 之前那个形状 —— 自愈默认关，
    于是探测跟着一起不跑，"有没有监控"只能答"没有"。
    """
    conf = load_conf()
    if not conf['probe']:
        return                       # 明确把探测本身关了（唯一"真没监控"的形状）
    st = _load_state()
    if time.time() - (st.get('last_probe_ts') or 0) < conf['interval_min'] * 60:
        return

    p = probe()                      # 只读：枚举进程与窗口，不动界面
    st['last_probe_ts'] = time.time()
    st['last_probe'] = p
    probes = st.get('probes') or []
    probes.append(p)
    st['probes'] = probes[-100:]

    # **不往 jobs/state.json 写**：那个键 `tdx_watch` 的归属是宿主
    # （`server.py::_tdx_watch_scheduler` → `_tdx_probe_round`），一个信号两个记账者，
    # 两边的 `ok` 语义还不一样（那边判"能不能取数"，这边判"探测活着"），
    # 谁后写谁覆盖 —— 与本次自查的第 6 条同一个形状。这里只留现场明细文件。
    #
    # ⚠️ 落盘必须在 `enabled` 那道 return **之前**：只探测、不动手是默认形态，
    # 把保存放在后面就等于"没人开自愈时，探测根本没留下任何记录"
    # ——第一版就是错的，被 `test_tick_先探测后决定动手且不开自愈` 抓到。
    _save_state(st)

    if not conf['enabled']:
        return                       # 探测照做并留痕，动手要人点头
    running_logged = p['running'] and not p['login_box']
    if conf['auto_start'] and not running_logged:
        heal(reason='定时探测：' + ('未运行' if not p['running'] else '卡在登录框'))
    elif running_logged and conf['restart_at'] and _restart_due(
            conf['restart_at'], st.get('last_restart_date')):
        heal(force_restart=True, reason=f'定时重启 {conf["restart_at"]}')
        st['last_restart_date'] = dt.date.today().isoformat()
    _save_state(st)


def _restart_due(hhmm: str, last_date: str | None) -> bool:
    """到点没有：今天还没执行过，且现在落在 HH:MM 起的 2 小时窗口内。"""
    try:
        h, m = (int(x) for x in hhmm.split(':'))
    except ValueError:
        return False
    now = dt.datetime.now()
    if last_date == now.date().isoformat():
        return False
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return target <= now <= target + dt.timedelta(hours=2)


# ---------------------------------------------------------------- 页面视图

def view() -> dict:
    """给 /admin 卡片用的一揽子状态。"""
    st = _load_state()
    return {'probe': probe(), 'conf': load_conf(), 'alive': monitor_alive(),
            'dir': str(config.TDX_DIR),
            'heals': list(reversed((st.get('heal') or [])[-8:])),
            'last_probe_at': st.get('last_probe')}
