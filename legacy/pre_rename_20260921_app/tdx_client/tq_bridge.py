# -*- coding: utf-8 -*-
"""
TQ 本地接口桥 —— **不碰界面**地指挥已登录的通达信客户端。

**为什么要有这个模块**：s5 那条"点开顶部菜单 TQ策略 → TQ数据设置"的路走不通，
而且不是定位参数写错，是入口本身不存在：

- `GetMenu(主窗口)` 返回 **0** —— 菜单栏是自绘的，没有标准 HMENU
- 主窗口 434 个子控件**全部无文字**，Win32 层拿不到 `TQ策略`
- UIA 只有 31 个节点，里面也没有 `TQ策略`
- 无输入桌面时坐标点击/SendKeys 两级被禁用；有输入桌面时 `SendInput` 文本又被拒

所以换入口。通达信自带的 TQ 插件（`PYPlugins\\user\\tqcenter.py`）通过
`PYPlugins\\TPythClient.dll` 直接和客户端进程通信，提供了三个正好够用的接口：

- `tq.exec_to_tdx(url)`        官方「在客户端调用功能或URL」，配合点系列命令
                              （`.933 盘后数据下载` / `.934 数据维护工具`）
- `tq.refresh_kline(codes, period)`  刷 K 线缓存，看它会不会把 vipdoc 推新
- `tq.get_market_data(...)`    直接从客户端取日线（通达信口径）

**为什么要起子进程而不是直接 import**：`tqcenter` 在 import 时就
`ctypes.CDLL(PYPlugins/TPythClient.dll)`，并且按 `__file__` 的父目录定位 dll；
`tq.initialize(path)` 还把脚本路径当"策略名"注册进客户端，同名策略只能有一个实例。
把它拉进 web/ETL 进程里，一是污染主进程（atexit 注册、全局连接、numpy/pandas 版本），
二是连接断了没法干净重来。子进程跑完就退，连接自然释放。

**判据仍然只认数据日期**：这些接口的返回值只能证明"调用没报错"，
不能证明盘后数据真的下来了。所以调用之后一律回去读 vipdoc `.day` 尾部日期
（`readiness.last_day_date`），跟目标交易日比。

用法：
    python -m app.tdx_client.tq_bridge --probe                 # 三个接口逐个探
    python -m app.tdx_client.tq_bridge --exec .933             # 只弹盘后数据下载
    python -m app.tdx_client.tq_bridge --refresh 600519.SH     # 只刷 K 线缓存
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

from app import config

# 生成的脚本必须落在 PYPlugins\user 下：`from tqcenter import tq` 要能 import 到，
# 而 tqcenter 又按自己的 __file__ 往上一级找 TPythClient.dll。放别处两样都断。
PLUGIN_DIR = config.TDX_DIR / 'PYPlugins' / 'user'
TQCENTER = PLUGIN_DIR / 'tqcenter.py'

# 固定文件名而不是随机名：客户端按脚本路径认"策略"，同名策略只允许一个实例。
# 随机名会在客户端里堆出一串再也不会被复用的僵尸策略登记。
SCRIPT_NAME = '_ft_tq_bridge.py'

# 结果分隔符。tqcenter 自己会往 stdout 打一堆中文日志（"TQ数据接口初始化成功…"），
# 直接 json.loads(stdout) 必然失败，所以约定一个标记，只解析标记后面那一行。
MARK = '__FT_TQ_JSON__'

DEFAULT_TIMEOUT = 600

# 流式模式下结果走文件，不走 stdout。
# **为什么要拆**：全市场取数要跑几分钟，`capture_output=True` 会把进度全憋到
# 最后一起吐出来，从外面看就是卡死；而结构化结果又不能和进度混在一起解析。
# 所以进度直接继承父进程的 stdout 实时回显，结果落到这个文件里。
RESULT_FILE = config.VAR_DIR / 'tq_bridge_result.json'


# 点系列快捷命令（通达信官方帮助 tdxjysm 里的「.9xx」表）。
# 写成常量是为了让"为什么是 933"有出处，不然半年后没人知道这三个数字哪来的。
CMD_DAY_DOWNLOAD = '.933'      # 盘后数据下载
CMD_DATA_TOOL = '.934'         # 数据维护工具
CMD_SETTINGS = '.930'          # 系统设置


class TqUnavailable(RuntimeError):
    """TQ 插件不可用（没装、dll 缺失、客户端没登录）。"""


def available() -> tuple[bool, str]:
    """TQ 这条路现在能不能走。只做文件检查，不启动进程。"""
    if not TQCENTER.is_file():
        return False, f'没有 {TQCENTER}'
    dll = PLUGIN_DIR.parent / 'TPythClient.dll'
    if not dll.is_file():
        return False, f'没有 {dll}'
    from app.tdx_client import win32
    if not win32.pids_by_name('TdxW.exe'):
        return False, '通达信没在运行（TQ 接口要求客户端已登录）'
    return True, f'{TQCENTER.parent} 就绪'


def run_snippet(body: str, *, timeout: int = DEFAULT_TIMEOUT,
                stream: bool = False) -> dict:
    """
    在 PYPlugins\\user 下跑一段用 `tq` 的代码，取回它 `emit()` 出来的字典。

    `body` 里可以直接用 `tq`、`emit(dict)` 和 `OUT_DIR`（一个可写目录），
    不用自己 import、不用自己 initialize、也不用管 close —— 模板已经包好，
    异常也会被转成 `{'ok': False, 'error': ...}` 而不是让子进程带着栈退出。

    `stream=True` 时子进程的输出**实时回显**，结果从 `RESULT_FILE` 读。
    取全市场那种要跑几分钟的活必须用它，否则外面看着像卡死。
    """
    ok, why = available()
    if not ok:
        raise TqUnavailable(why)

    config.VAR_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.unlink(missing_ok=True)

    script = PLUGIN_DIR / SCRIPT_NAME
    src = (_TEMPLATE
           .replace('#RESULT_FILE#', str(RESULT_FILE))
           .replace('#OUT_DIR#', str(config.VAR_DIR))
           .replace('#BODY#', _indent(body)))
    script.write_text(src, encoding='utf-8')
    try:
        proc = subprocess.run(
            [sys.executable, str(script)], cwd=str(PLUGIN_DIR),
            capture_output=not stream, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'子进程超时 {timeout}s'}
    finally:
        # 不留垃圾：这个文件在通达信的安装目录里，留着会被当成用户策略列出来。
        script.unlink(missing_ok=True)

    if stream:
        if RESULT_FILE.is_file():
            return json.loads(RESULT_FILE.read_text(encoding='utf-8'))
        return {'ok': False,
                'error': f'子进程没写结果文件（退出码 {proc.returncode}）'}

    out = proc.stdout.decode('utf-8', 'replace')
    err = proc.stderr.decode('utf-8', 'replace')
    for line in out.splitlines():
        if line.startswith(MARK):
            data = json.loads(line[len(MARK):])
            data['stdout'] = out
            return data
    return {'ok': False,
            'error': f'子进程没有输出结果（退出码 {proc.returncode}）',
            'stdout': out, 'stderr': err}


def _indent(body: str) -> str:
    return '\n'.join('    ' + ln if ln.strip() else ln
                     for ln in body.strip().splitlines())


_TEMPLATE = '''# -*- coding: utf-8 -*-
# 由 app/tdx_client/tq_bridge.py 自动生成，跑完即删。别手工编辑。
import json
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_RESULT = {}
_RESULT_FILE = r"#RESULT_FILE#"
OUT_DIR = r"#OUT_DIR#"


def emit(d):
    _RESULT.update(d)


def _dump():
    txt = json.dumps(_RESULT, ensure_ascii=False, default=str)
    print('__FT_TQ_JSON__' + txt)
    try:
        with open(_RESULT_FILE, 'w', encoding='utf-8') as f:
            f.write(txt)
    except Exception as exc:
        print('写结果文件失败: %s' % exc)


try:
    from tqcenter import tq
    tq.initialize(__file__)
except Exception as exc:
    _RESULT.update({'ok': False, 'error': 'initialize 失败: %s' % exc})
    _dump()
    raise SystemExit(0)

try:
#BODY#
    _RESULT.setdefault('ok', True)
except Exception as exc:
    _RESULT.clear()
    _RESULT.update({'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)})
finally:
    try:
        tq.close()
    except Exception:
        pass

_dump()
'''



# ---------------------------------------------------------------- 三个动作

def exec_url(url: str = CMD_DAY_DOWNLOAD) -> dict:
    """让客户端执行一条点系列命令 / URL。`.933` = 盘后数据下载。"""
    return run_snippet(f'emit({{"ret": tq.exec_to_tdx(url={url!r})}})',
                       timeout=60)


def refresh_kline(codes: list[str], period: str = '1d') -> dict:
    """刷 K 线缓存。tqcenter 只认 1m/5m/1d 三种周期。"""
    return run_snippet(
        f'emit({{"ret": tq.refresh_kline(stock_list={codes!r}, '
        f'period={period!r})}})')


def market_data(codes: list[str], start: dt.date, end: dt.date,
                period: str = '1d') -> dict:
    """
    从客户端取日线。**除权口径显式传 none**，要的是原始未复权价 ——
    库里的 `md.stock_daily` 存的就是未复权价，复权由 `adj_factor` 另算。
    """
    return run_snippet(
        f'd = tq.get_market_data(stock_list={codes!r}, period={period!r}, '
        f'start_time={start.strftime("%Y%m%d")!r}, '
        f'end_time={end.strftime("%Y%m%d")!r}, '
        "dividend_type='none')\n"
        'emit({"data": d})')



# ---------------------------------------------------------------- 探测

def probe(target: dt.date, code: str = '600519.SH') -> int:
    """
    逐个试三个接口，每一步都回去读 vipdoc 尾部日期当判据。

    **打印的是证据，不是结论**：哪个接口把 `.day` 推到目标日了，
    下一步才好决定 s5 走哪条路。
    """
    from app.tdx_client import readiness

    market, symbol = ('sh', code[:6]) if code.endswith('.SH') else \
                     ('sz', code[:6]) if code.endswith('.SZ') else ('bj', code[:6])
    day_file = config.VIPDOC_DIR / market / 'lday' / f'{market}{symbol}.day'

    def last() -> str:
        d, err = readiness.last_day_date(day_file)
        return str(d) if d else f'读不出（{err}）'

    ok, why = available()
    print(f'TQ 可用性：{"可用" if ok else "**不可用**"} —— {why}')
    if not ok:
        return 2

    print(f'目标交易日 {target}；{day_file.name} 当前尾部 {last()}')

    print(f'\n=== 1. exec_to_tdx({CMD_DAY_DOWNLOAD!r})（盘后数据下载） ===')
    r = exec_url(CMD_DAY_DOWNLOAD)
    print(f'  返回：{_brief(r)}')
    from app.tdx_client import win32
    wins = [w.text for w in win32.top_windows()
            if w.text and ('下载' in w.text or '数据' in w.text)]
    print(f'  当前带"下载/数据"字样的顶层窗口：{wins or "无"}')
    print(f'  尾部日期：{last()}')

    print(f'\n=== 2. refresh_kline([{code}], 1d) ===')
    r = refresh_kline([code])
    print(f'  返回：{_brief(r)}')
    print(f'  尾部日期：{last()}')

    print(f'\n=== 3. get_market_data([{code}], 1d) ===')
    r = market_data([code], target - dt.timedelta(days=6), target)
    print(f'  返回：{_brief(r)}')

    print(f'\n最终 {day_file.name} 尾部日期：{last()}（目标 {target}）')
    d, _ = readiness.last_day_date(day_file)
    return 0 if (d and d >= target) else 1



def _brief(r: dict, n: int = 600) -> str:
    d = {k: v for k, v in r.items() if k not in ('stdout', 'stderr')}
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + '…'


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog='ft-tq-bridge')
    ap.add_argument('--probe', action='store_true', help='三个接口逐个探（只读判据）')
    ap.add_argument('--exec', dest='url', help='执行一条点系列命令，如 .933')
    ap.add_argument('--refresh', help='刷这个代码的 1d K 线缓存')
    ap.add_argument('--date', help='目标交易日，默认自动推算')
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    from app.tdx_client import readiness

    target = (dt.date.fromisoformat(args.date) if args.date
              else readiness.expected_trade_date())

    if args.url:
        print(_brief(exec_url(args.url), 2000))
        return 0
    if args.refresh:
        print(_brief(refresh_kline([args.refresh]), 2000))
        return 0
    return probe(target)


if __name__ == '__main__':
    raise SystemExit(main())
