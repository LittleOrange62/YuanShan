# -*- coding: utf-8 -*-
"""
网站与常驻后台进程的一键启停。

**为什么用 Python 而不是 .ps1**：PowerShell 5.1 读不带 BOM 的 .ps1 时按 ANSI 解码，
脚本里的中文会变乱码甚至语法报错；.cmd 里的中文同样受活动代码页影响。
这里把逻辑放在 Python（UTF-8 确定），根目录两个 .cmd 只做转发，内容全 ASCII。

**为什么停 Web 不顺手把 PostgreSQL 也停掉**：数据库是共享资源，别的脚本
（摄取、计算、通达信同步）可能正在写。默认只停 Web，要一起停必须显式 --with-db。

管三个进程：`web`（网站）、`scheduler`（调度器，决定该不该跑）、
`worker`（消费 ops.task 队列，真的跑）。`start` / `stop` 默认三个一起管，
因为后两个在 `scheduler_enabled=false`（出厂默认）时是**惰性的**：调度器只发心跳
不入队，worker 只在队列有任务时动手。要只动网站用 `--web-only`。

用法（也可以直接双击根目录的 启动网站.cmd / 停止网站.cmd）：
    python scripts\\web_ctl.py start
    python scripts\\web_ctl.py stop
    python scripts\\web_ctl.py restart
    python scripts\\web_ctl.py status
    python scripts\\web_ctl.py start --debug --no-browser
    python scripts\\web_ctl.py start --web-only
    python scripts\\web_ctl.py start-bg            # 只起调度器与 worker
    python scripts\\web_ctl.py stop --with-db
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')

# 配置读不出来也要能停服务，所以给兜底默认值而不是让脚本直接崩
try:
    from app import config

    WEB_PORT = config.WEB_PORT
    PG_PORT = config.PG_PORT
    PG_HOST = config.PG_HOST
    LOG_DIR = config.LOG_DIR
    VAR_DIR = config.VAR_DIR
except Exception as exc:  # noqa: BLE001
    print('[警告] 读 app.config 失败（%s），改用默认值' % exc)
    WEB_PORT, PG_PORT, PG_HOST = 18420, 5432, '127.0.0.1'
    LOG_DIR, VAR_DIR = ROOT / 'logs', ROOT / 'var'

PID_FILE = VAR_DIR / 'web.pid'
WEB_LOG = LOG_DIR / 'web.log'
PG_LOG = LOG_DIR / 'pg.log'

PG_CTL = Path(r'D:\PostgreSQL\17\bin\pg_ctl.exe')
PG_DATA = Path(r'D:\finance\TataPG')

# 只认这个特征串的进程才敢杀，避免 pid 复用后误杀别的程序
WEB_MARK = 'app.web.run'
DETACHED = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

# 三个常驻进程。`mark` 是判"这个 pid 到底是不是我们的"所用的命令行特征串 ——
# 只有 web 靠端口就能认（18420 基本不会被别的程序占），调度器和 worker 没有端口，
# 必须靠命令行，认不准就不能杀，否则会误伤同名 python 进程。
SERVICES = {
    'web': {'module': 'app.web.run', 'mark': WEB_MARK,
            'log': WEB_LOG, 'pid': PID_FILE, 'port': WEB_PORT},
    'scheduler': {'module': 'app.orchestrator.run_scheduler', 'mark': 'run_scheduler',
                  'log': LOG_DIR / 'scheduler.log', 'pid': VAR_DIR / 'scheduler.pid',
                  'port': None},
    'worker': {'module': 'app.orchestrator.run_worker', 'mark': 'run_worker',
               'log': LOG_DIR / 'worker.log', 'pid': VAR_DIR / 'worker.pid',
               'port': None},
}
BG = ('scheduler', 'worker')


# ---------------------------------------------------------------- 探测

def port_open(port: int, host: str = '127.0.0.1', timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def http_ok(port: int, timeout: float = 3.0) -> int | None:
    try:
        with urlopen(f'http://127.0.0.1:{port}/', timeout=timeout) as r:
            return r.status
    except URLError:
        return None
    except Exception:  # noqa: BLE001
        return None


def _menu_hint() -> str:
    """启动后打印的页面清单。从 config.menu 读，读不到就不吹牛，只说去首页看。"""
    try:
        from app.db.repo import registry as reg
        routes = [m['route'] for m in reg.menu()]
        return '  '.join(routes) if routes else '（config.menu 是空的）'
    except Exception:  # noqa: BLE001
        return '（读 config.menu 失败，打开首页看左侧导航）'



def listeners(port: int) -> list[int]:
    """占用某端口的 pid。用 netstat 解析，不引第三方库。"""
    try:
        out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True,
                             text=True, encoding='utf-8', errors='replace',
                             timeout=20).stdout
    except Exception:  # noqa: BLE001
        return []
    pids = []
    pat = re.compile(rf'^\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$')
    for line in out.splitlines():
        m = pat.match(line)
        if m:
            pids.append(int(m.group(1)))
    return sorted(set(pids))


def cmdline(pid: int) -> str:
    """取进程命令行。取不到就返回空串，调用方按"不确定"处理。"""
    ps = ('$p = Get-CimInstance Win32_Process -Filter "ProcessId=%d" '
          '-ErrorAction SilentlyContinue; if ($p) { $p.CommandLine }' % pid)
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=25)
        return (r.stdout or '').strip()
    except Exception:  # noqa: BLE001
        return ''


def alive(pid: int) -> bool:
    try:
        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=20)
        return str(pid) in (r.stdout or '')
    except Exception:  # noqa: BLE001
        return False


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text(encoding='utf-8').strip())
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- PostgreSQL

def pg_up() -> bool:
    return port_open(PG_PORT, PG_HOST)


def pg_start() -> bool:
    if pg_up():
        return True
    if not PG_CTL.is_file() or not (PG_DATA / 'PG_VERSION').is_file():
        print('  数据库没起，且找不到 pg_ctl（%s）或数据目录（%s），无法自动拉起'
              % (PG_CTL, PG_DATA))
        return False
    print('  数据库没起，用 pg_ctl 拉起 …')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(PG_CTL), '-D', str(PG_DATA), '-l', str(PG_LOG), 'start'],
                   capture_output=True, text=True, encoding='utf-8',
                   errors='replace', timeout=90)
    for _ in range(30):
        if pg_up():
            print('  数据库已就绪（%s:%s）' % (PG_HOST, PG_PORT))
            return True
        time.sleep(1)
    print('  数据库 30 秒内没起来，看日志：%s' % PG_LOG)
    return False


def pg_stop() -> None:
    if not pg_up():
        print('  数据库本来就没在跑')
        return
    if not PG_CTL.is_file():
        print('  找不到 pg_ctl，不动数据库')
        return
    # -m fast：等当前事务回滚，不等客户端自己断开；不用 immediate，那个会留下需要恢复的状态
    r = subprocess.run([str(PG_CTL), '-D', str(PG_DATA), '-m', 'fast', 'stop'],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=120)
    print('  数据库已停' if r.returncode == 0
          else '  停数据库失败：%s' % (r.stderr or r.stdout or '').strip()[:200])


# ---------------------------------------------------------------- 后台常驻进程

def _pids_matching(mark: str) -> list[int]:
    """
    命令行里带 `mark` 的 python 进程 pid。

    调度器和 worker 没有端口，`listeners()` 那套认不出它们；只能按命令行特征查。
    限定 `Name LIKE 'python%'` 是为了不把 notepad 之类恰好含该串的进程算进来。
    """
    # 用 f-string 而不是 % 格式化：PowerShell 那句里的 `'python%'` 会撞上
    # Python 的格式说明符（F509），写成 %% 又难读。
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
          f"Where-Object {{ $_.CommandLine -match '{re.escape(mark)}' }} | "
          f"ForEach-Object {{ $_.ProcessId }}")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=30)
    except Exception:  # noqa: BLE001
        return []
    return sorted({int(x) for x in r.stdout.split() if x.strip().isdigit()})


def svc_pids(name: str) -> list[int]:
    """
    后台进程按命令行特征认，**不认 pid 文件里的数**。

    pid 会被复用，而这两个进程没有端口可以旁证；网站那边敢认端口占用者，
    是因为 18420 基本不会被别的程序占。认错一次的代价是 taskkill 杀掉别人的
    python 进程 —— 这里宁可不杀。pid 文件只留给日志里说"上次是谁起的"。
    """
    return _pids_matching(SERVICES[name]['mark'])


def svc_start(name: str) -> int:
    svc = SERVICES[name]
    running = svc_pids(name)
    if running:
        print('  %s 已经在跑：pid %s' % (name, running))
        return 0
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, '-m', svc['module']]
    with svc['log'].open('a', encoding='utf-8') as log:
        log.write('\n===== %s %s 启动 =====\n'
                  % (time.strftime('%Y-%m-%d %H:%M:%S'), name))
        log.flush()
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=DETACHED, env=env)
    svc['pid'].write_text(str(p.pid), encoding='utf-8')
    # 这两个进程没有 HTTP 端口可以探，判"起没起来"只能看进程还活着没有。
    # 等 2 秒：连不上库、拿不到单实例锁这类启动即退的毛病都在这个窗口里发生。
    time.sleep(2)
    if not alive(p.pid):
        print('  %s 启动后立即退出，末尾日志：' % name)
        _tail(svc['log'], 20)
        svc['pid'].unlink(missing_ok=True)
        return 1
    print('  %s 已启动（pid %s，日志 %s）' % (name, p.pid, svc['log']))
    return 0


def svc_stop(name: str) -> int:
    svc = SERVICES[name]
    pids = svc_pids(name)
    if not pids:
        print('  %s 没在跑' % name)
        svc['pid'].unlink(missing_ok=True)
        return 0
    for pid in pids:
        r = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=30)
        print('  停 %s pid %s：%s' % (name, pid, '已结束' if r.returncode == 0
                                     else (r.stderr or r.stdout or '').strip()[:120]))
    svc['pid'].unlink(missing_ok=True)
    return 0


# ---------------------------------------------------------------- Web

def web_pids() -> list[int]:
    """认定为「我们的 Web 进程」的 pid：端口占用者 + pid 文件，且命令行带特征串。"""
    found = []
    for pid in listeners(WEB_PORT):
        found.append(pid)
    saved = read_pid()
    if saved and saved not in found and alive(saved):
        found.append(saved)

    out = []
    for pid in found:
        cl = cmdline(pid)
        if not cl:
            # 命令行读不到：端口占用者仍然收（自定义端口 18420 基本不会是别的程序），
            # 但 pid 文件里的孤儿 pid 不收，避免 pid 复用误杀
            if pid in listeners(WEB_PORT):
                out.append(pid)
            continue
        if WEB_MARK in cl or 'ft-web' in cl:
            out.append(pid)
        else:
            print('  跳过 pid %s：命令行不像本站（%s）' % (pid, cl[:80]))
    return sorted(set(out))


def do_start(args) -> int:
    print('=== 启动 FinanceTool 网站 ===')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    VAR_DIR.mkdir(parents=True, exist_ok=True)

    running = web_pids()
    if running:
        code = http_ok(WEB_PORT)
        print('  已经在跑了：pid %s，HTTP %s' % (running, code or '无响应'))
        print('  地址 http://127.0.0.1:%s/' % WEB_PORT)
        if not args.no_browser:
            webbrowser.open(f'http://127.0.0.1:{WEB_PORT}/')
        return 0
    if port_open(WEB_PORT):
        print('  端口 %s 被别的程序占了，先腾出来再启动' % WEB_PORT)
        return 1

    if not pg_start():
        print('  数据库没就绪，页面会打不开数据。仍继续启动 Web，方便你看报错')

    # 不给 --host 就**不传**，让 app.web.run 去读 config.setting.web_bind_host。
    # 以前这里写死 '--host 127.0.0.1'，于是 /admin 上那个监听地址选择框改了也没用 ——
    # 命令行参数优先级最高，页面上的开关等于摆设。
    cmd = [sys.executable, '-m', 'app.web.run', '--port', str(WEB_PORT)]
    if args.host:
        cmd[3:3] = ['--host', args.host]

    if args.debug:
        cmd.append('--debug')
    print('  %s' % ' '.join(cmd))
    with WEB_LOG.open('a', encoding='utf-8') as log:
        log.write('\n===== %s 启动 =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
        log.flush()
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=DETACHED, env=env)
    PID_FILE.write_text(str(p.pid), encoding='utf-8')

    # 判据是「HTTP 真的答话」，不是「睡够了几秒」
    for i in range(40):
        code = http_ok(WEB_PORT, timeout=2)
        if code:
            print('  启动成功（%.1fs，HTTP %s，pid %s）' % (i * 0.5, code, p.pid))
            print('  地址 http://127.0.0.1:%s/' % WEB_PORT)
            # 页面清单从 config.menu 读，不写死 —— 写死过一次，加了页面这里
            # 还在报旧的 5 个，看着像"其它页没做"。
            print('  页面 %s' % _menu_hint())
            print('  日志 %s' % WEB_LOG)
            if not args.no_browser:
                webbrowser.open(f'http://127.0.0.1:{WEB_PORT}/')
            if not args.web_only:
                for name in BG:
                    svc_start(name)
            else:
                print('  --web-only：没起调度器与 worker（要单独起：'
                      'python scripts\\web_ctl.py start-bg）')
            return 0
        if not alive(p.pid):
            print('  进程已退出，末尾日志：')
            _tail(WEB_LOG, 25)
            return 1
        time.sleep(0.5)

    print('  20 秒内没等到 HTTP 响应，进程还在（pid %s）。末尾日志：' % p.pid)
    _tail(WEB_LOG, 25)
    return 1


def do_stop(args) -> int:
    print('=== 停止 FinanceTool ===')
    targets = ['web'] if args.web_only else ['web', *BG]
    if 'web' in targets:
        pids = web_pids()
        if not pids:
            print('  网站没在跑')
        for pid in pids:
            r = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=30)
            print('  停 pid %s：%s' % (pid, '已结束' if r.returncode == 0
                                      else (r.stderr or r.stdout or '').strip()[:120]))
        for _ in range(10):
            if not port_open(WEB_PORT):
                break
            time.sleep(0.5)
        if port_open(WEB_PORT):
            print('  端口 %s 还在监听，可能有残留进程' % WEB_PORT)
        PID_FILE.unlink(missing_ok=True)
    for name in BG:
        if name in targets:
            svc_stop(name)

    if args.with_db:
        print('  --with-db：一并停数据库')
        pg_stop()
    else:
        print('  数据库保持运行（其它脚本可能正在写）。要一起停加 --with-db')
    return 0


def do_start_bg(_args) -> int:
    """只起后台进程，不动网站。库没起的话先拉库 —— 调度器连不上库会直接退。"""
    print('=== 启动调度器与 worker ===')
    if not pg_up() and not pg_start():
        print('  数据库没就绪，调度器与 worker 起来也是立刻退出')
        return 1
    rc = 0
    for name in BG:
        rc |= svc_start(name)
    return rc


def do_stop_bg(_args) -> int:
    print('=== 停止调度器与 worker（网站不动）===')
    for name in reversed(BG):
        svc_stop(name)
    return 0


def do_status(_args) -> int:
    print('=== FinanceTool 状态 ===')
    print('  数据库    %s:%-6s %s' % (PG_HOST, PG_PORT, '在跑' if pg_up() else '没跑'))
    pids = web_pids()
    code = http_ok(WEB_PORT) if pids or port_open(WEB_PORT) else None
    print('  网站      127.0.0.1:%-6s %s' % (
        WEB_PORT,
        ('在跑 pid %s，HTTP %s' % (pids, code or '无响应')) if pids else '没跑'))
    for name in BG:
        bg = svc_pids(name)
        print('  %-9s %s' % (name, ('在跑 pid %s' % bg) if bg else '没跑'))
    print('  日志      %s / %s / %s'
          % (WEB_LOG, SERVICES['scheduler']['log'], SERVICES['worker']['log']))
    _sched_switch_hint()
    return 0


def _sched_switch_hint() -> None:
    """
    进程活着 ≠ 会在自动跑。这一句必须打出来。

    `scheduler_enabled` 默认是 false：这时调度进程照常发心跳、但一条都不入队。
    status 里只报"在跑"会给人完全错误的印象 —— docs/09 §7.1 说的那种
    "看起来已经在自动跑了"，换个形式又回来了。
    """
    try:
        from app.db.repo import schedule as sch_repo
    except Exception:  # noqa: BLE001
        return
    try:
        s = sch_repo.settings()
    except Exception as exc:  # noqa: BLE001
        print('  调度开关    读不出来（%s）' % str(exc)[:80])
        return
    live = sch_repo.live_roles()
    for role, label in (('scheduler', '调度器'), ('worker', 'worker')):
        info = live.get(role)
        age = info.get('age_sec') if info else None
        state = '心跳 %.0fs 前' % age if age is not None else '没有过心跳'
        print('  %-10s %s（%s）' % (label + '心跳', '活' if info and info['alive']
                                   else '死', state))
    print('  调度开关    enabled=%s dry_run=%s web走队列=%s'
          % (s['scheduler_enabled'], s['scheduler_dry_run'],
             s['scheduler_web_via_queue']))
    if not s['scheduler_enabled']:
        print('              ↑ 总开关是关的：调度进程即便在跑也只发心跳、不入队，'
              '数据仍需手动追平。开关在 /schedule 页上。')


def _tail(path: Path, n: int) -> None:
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return
    for line in lines[-n:]:
        print('    | %s' % line)


def main() -> int:
    ap = argparse.ArgumentParser(description='FinanceTool 网站与后台进程一键启停')
    ap.add_argument('action', choices=['start', 'stop', 'restart', 'status',
                                       'start-bg', 'stop-bg'])
    ap.add_argument('--host', default=None,
                    help='临时覆盖监听地址；默认读 config.setting.web_bind_host'
                         '（/admin → 访问白名单 可改，默认只绑本机）')
    ap.add_argument('--debug', action='store_true', help='用 Dash 自带服务器')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--web-only', action='store_true',
                    help='start/stop 只动网站，不碰调度器与 worker')
    ap.add_argument('--with-db', action='store_true', help='stop 时一并停 PostgreSQL')
    args = ap.parse_args()

    if args.action == 'start':
        return do_start(args)
    if args.action == 'stop':
        return do_stop(args)
    if args.action == 'status':
        return do_status(args)
    if args.action == 'start-bg':
        return do_start_bg(args)
    if args.action == 'stop-bg':
        return do_stop_bg(args)
    do_stop(args)
    time.sleep(1)
    return do_start(args)


if __name__ == '__main__':
    raise SystemExit(main())
