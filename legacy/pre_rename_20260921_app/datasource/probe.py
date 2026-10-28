# -*- coding: utf-8 -*-
"""
数据源体检。

每个源在 config.datasource.probe_spec 里写清楚"怎么算通"，探测器按 kind 分派。
**判据是"拿到了几行"而不是"没抛异常"**：akshare 对未发布的日期会返回空表
而不报错，只看异常会把"今天还没发布"判成健康。

支持的 kind：
    version  包能 import，且能读到版本号
    df       调 akshare 的某个函数，返回 DataFrame 且行数 > 0
    tushare  调 Tushare pro_api 的某个接口。40203 单独识别成 no_permission
    http     GET 一个 URL，看状态码
    env      只检查某个环境变量在不在（用于只有密钥、没有免费探测端点的源）
    vipdoc   检查本地 vipdoc 目录与文件数
    ths      解析同花顺本地 ini（what=block/industry/watchlist），看条数与 ConfigVer 日期
    noop     不探测，保持原状（交互式鉴权的 MCP 之类）
"""
from __future__ import annotations

import datetime as dt
import importlib
import os
import time
import urllib.error
import urllib.request

from app import config  # noqa: F401  确保 .env 已灌进 os.environ
from app.datasource import registry as reg
from app.db import engine

TIMEOUT = 20
# 探测的重试次数。东财实测是**间歇性**断连，单次失败不足以判定"不可用"，
# 但连续失败要如实标出来。第 2 次才成功的记 degraded，不记 ok。
PROBE_RETRY = 2
PROBE_BACKOFF = 2.0
# 用常见浏览器 UA。东财的 push2 对 'FinanceTool/1.0' 这类 UA 直接断连
# （实测 RemoteDisconnected），换成浏览器 UA 才回 200。
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
# Tushare 的无权限错误码。它是**正常返回**里的 code，不是异常，
# 只看 try/except 会把它当成"成功但 0 行"。
TS_NO_PERMISSION = 40203


def latest_trade_date() -> str:
    d = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
    if d is None:
        d = dt.date.today() - dt.timedelta(days=1)
    return d.strftime('%Y%m%d')


def _fill(args: dict, date_str: str) -> dict:
    return {k: (date_str if v == '{date}' else v) for k, v in (args or {}).items()}


def _probe_version(spec: dict) -> tuple[str, int | None, str | None]:
    mod = importlib.import_module(spec['module'])
    ver = getattr(mod, '__version__', None)
    return reg.OK, None, f'版本 {ver}' if ver else '已安装（无 __version__）'


def _probe_df(spec: dict, date_str: str) -> tuple[str, int | None, str | None]:
    import akshare as ak
    fn = getattr(ak, spec['fn'], None)
    if fn is None:
        return reg.BROKEN, None, f'akshare 里没有 {spec["fn"]}（版本不匹配）'
    df = fn(**_fill(spec.get('args'), date_str))
    n = 0 if df is None else len(df)
    if n == 0:
        # 空表不等于坏：非交易日、或当日尚未发布。标 degraded 并说清楚
        return reg.DEGRADED, 0, '返回 0 行（非交易日或当日尚未发布）'
    return reg.OK, n, None


def _probe_tushare(spec: dict, date_str: str) -> tuple[str, int | None, str | None]:
    import tushare as ts
    token = os.environ.get('FT_TUSHARE_TOKEN', '')
    if not token:
        return reg.DOWN, None, '环境变量 FT_TUSHARE_TOKEN 未设置'
    api = ts.pro_api(token)
    try:
        df = api.query(spec['api'], **_fill(spec.get('args'), date_str))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if str(TS_NO_PERMISSION) in msg or '权限' in msg:
            return reg.NO_PERMISSION, 0, msg[:200]
        raise
    n = 0 if df is None else len(df)
    return (reg.OK if n else reg.DEGRADED), n, None if n else '返回 0 行'


def _probe_http(spec: dict) -> tuple[str, int | None, str | None]:
    req = urllib.request.Request(spec['url'], headers={
        'User-Agent': UA,
        'Accept': '*/*',
        'Referer': spec.get('referer') or 'https://www.eastmoney.com/',
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            code = r.status
            body = r.read(4096)
    except urllib.error.HTTPError as exc:
        # 403 常见于 Cloudflare 挡爬虫，站点其实活着；标 degraded 而不是 down
        return (reg.DEGRADED if exc.code in (401, 403, 429) else reg.DOWN), None, \
            f'HTTP {exc.code}'
    return reg.OK, len(body), f'HTTP {code}'


def _probe_env(spec: dict) -> tuple[str, int | None, str | None]:
    var = spec['var']
    # 只报"在不在"，绝不打印值
    return (reg.OK, None, f'{var} 已配置') if os.environ.get(var) \
        else (reg.DOWN, None, f'{var} 未设置')


def _probe_vipdoc() -> tuple[str, int | None, str | None]:
    d = config.VIPDOC_DIR
    if not d.is_dir():
        return reg.DOWN, None, f'目录不存在：{d}'
    n = sum(1 for _ in d.glob('*/lday/*.day'))
    latest = engine.scalar('SELECT max(trade_date)::text FROM md.stock_daily')
    return (reg.OK if n else reg.DOWN), n, f'{n} 个 .day 文件，库内最新 {latest}'


#: 同花顺的 `ConfigVer` 超过这么多天没动，就算文件还在也标 degraded
THS_STALE_DAYS = 7


def _ths_ver_date(ver: str | None) -> dt.date | None:
    """`'20260830.095501'` → date(2026, 8, 30)。认不出返回 None。"""
    head = (ver or '').split('.')[0]
    try:
        return dt.datetime.strptime(head, '%Y%m%d').date()
    except ValueError:
        return None


def _probe_ths(spec: dict) -> tuple[str, int | None, str | None]:
    """
    同花顺本地文件。`spec['what']` = block / industry / watchlist。

    ★判据是**文件里写的 `ConfigVer` 日期**，不是文件的 mtime —— 学 vipdoc 的
    教训：客户端每次启动都会摸一遍这些文件，时间戳永远是"今天"，看时间戳会把
    "同花顺装着但没登录、板块清单一个月没更新"判成健康。`ConfigVer` 是同花顺
    自己写进 `block_*.ini` 的板块清单版本号，它不更新就是真的没更新。

    `industry.ini` / `stockblock.ini` 里没有 ConfigVer：
    行业清单跟着安装包走、自选是用户自己改的，都没有"该每天变"的预期，
    所以这两个只看条数。
    """
    from app.datasource import ths_local

    d = ths_local.ths_dir()
    if not d.is_dir():
        return reg.DOWN, None, f'目录不存在：{d}'
    what = spec.get('what', 'block')

    if what == 'block':
        r = ths_local.read_blocks()
        n = len(r['sectors'])
        members = sum(len(s['members']) for s in r['sectors'])
        if not n:
            return reg.DOWN, 0, f'{d / "BlockUpdate"} 里没解出板块'
        msg = f'{n} 个板块 / {members} 条成员，ConfigVer {r["config_ver"] or "无"}'
        vd = _ths_ver_date(r['config_ver'])
        if vd is None:
            return reg.DEGRADED, n, msg + '（版本号认不出）'
        age = (dt.date.today() - vd).days
        if age > THS_STALE_DAYS:
            return reg.DEGRADED, n, f'{msg}，已 {age} 天没更新（客户端没登录？）'
        return reg.OK, n, msg

    if what == 'industry':
        r = ths_local.read_industry()
        n = len(r['sectors'])
        members = sum(len(s['members']) for s in r['sectors'])
        return (reg.OK if n else reg.DOWN), n, \
            f'{n} 个行业 / {members} 条成员' if n else 'industry.ini 缺失或解不出'

    if what == 'watchlist':
        r = ths_local.read_watchlist_groups()
        n = len(r['groups'])
        members = sum(len(g['members']) for g in r['groups'])
        if not n:
            return reg.DOWN, 0, '没找到带 stockblock.ini 的用户目录'
        return reg.OK, n, f'用户 {r["user"]}：{n} 个分组 / {members} 只'

    return reg.BROKEN, None, f'probe_spec.what 不认识：{what}'


#: 收盘快照库最新一张日表超过这么多天没长出来，就算文件还在也标 degraded。
#: 3 天：插件只在通达信开着的时候跑，周末不跑，所以 1 天太敏感
SNAPDB_STALE_DAYS = 3


def _probe_snapdb() -> tuple[str, int | None, str | None]:
    """
    通达信收盘快照库（插件落的 SQLite）。

    ★判据是**库里最新那张 `day_end_*` 表的日期**，不是文件 mtime，也不是
    表的张数 —— 插件是 AutoRun，通达信一开就会摸这个文件，看 mtime 会把
    "插件报错了、一个月没落新表"判成健康。

    只报"库里有到哪天"，不判断那张表是不是收盘后采的 —— 盘中采集的表由
    `c15_snapshot.py` 自己按数据回推时跳掉，探活不重复那套判断。
    """
    import sqlite3

    p = config.TDX_DIR / 'daily_close_data.db'
    if not p.is_file():
        return reg.DOWN, None, f'文件不存在：{p}'
    try:
        con = sqlite3.connect(f'file:{p.as_posix()}?mode=ro', uri=True)
        try:
            names = [n for (n,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                " AND name LIKE 'day_end_%' ORDER BY name DESC")]
            if not names:
                return reg.DOWN, 0, '库在但没有 day_end_* 表，插件没落过数'
            n = con.execute(f'SELECT count(*) FROM [{names[0]}]').fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as exc:
        return reg.BROKEN, None, f'打不开：{exc}'

    try:
        latest = dt.datetime.strptime(names[0][-8:], '%Y%m%d').date()
    except ValueError:
        return reg.DEGRADED, n, f'最新表 {names[0]} 的日期认不出'
    age = (dt.date.today() - latest).days
    msg = f'{len(names)} 张日表，最新 {latest}（{n} 行）'
    if age > SNAPDB_STALE_DAYS:
        return reg.DEGRADED, n, f'{msg}，已 {age} 天没落新表（插件没跑？）'
    return reg.OK, n, msg


def _probe_tqhttp() -> tuple[str, int | None, str | None]:

    # 通达信没开的时候这条必然不通，而那不是"源坏了"是"客户端没开" ——
    # 所以文案要写清楚，否则会有人去查网络。
    from app.datasource import tq_http
    ok, msg = tq_http.available()
    return (reg.OK if ok else reg.DOWN), None, msg


NETWORK_KINDS = ('df', 'tushare', 'http')


def _dispatch(kind: str, spec: dict, date_str: str) -> tuple[str, int | None, str | None]:
    if kind == 'version':
        return _probe_version(spec)
    if kind == 'df':
        return _probe_df(spec, date_str)
    if kind == 'tushare':
        return _probe_tushare(spec, date_str)
    if kind == 'http':
        return _probe_http(spec)
    if kind == 'tqhttp':
        return _probe_tqhttp()
    if kind == 'env':
        return _probe_env(spec)
    if kind == 'vipdoc':
        return _probe_vipdoc()
    if kind == 'ths':
        return _probe_ths(spec)
    if kind == 'snapdb':
        return _probe_snapdb()


    raise LookupError(kind)


def probe_one(ds: dict) -> dict:
    """探一个源。返回 {'ds_key','health','latency_ms','row_count','message'}。"""
    spec = ds.get('probe_spec') or {}
    kind = spec.get('kind', 'noop')
    date_str = latest_trade_date()
    # 联网类的源重试一次：间歇性断连不该被判成"不可用"。
    # 但第 2 次才成功要如实降级成 degraded，否则页面上永远看不出这个源在抖。
    attempts = PROBE_RETRY if kind in NETWORK_KINDS else 1
    t0 = time.perf_counter()
    health = n = msg = None
    for i in range(attempts):
        try:
            health, n, msg = _dispatch(kind, spec, date_str)
        except LookupError:
            return {'ds_key': ds['ds_key'], 'health': ds.get('health') or 'unknown',
                    'latency_ms': None, 'row_count': None, 'message': '不探测'}
        except ModuleNotFoundError as exc:
            health, n, msg = reg.DOWN, None, f'包未安装：{exc.name}'
            break                       # 包没装，重试没有意义
        except Exception as exc:  # noqa: BLE001
            health, n, msg = reg.DOWN, None, f'{type(exc).__name__}: {exc}'
            if i < attempts - 1:
                time.sleep(PROBE_BACKOFF)
            continue
        if health == reg.OK and i > 0:
            health = reg.DEGRADED
            msg = f'第 {i + 1} 次尝试才成功（源不稳）'
        break

    ms = int((time.perf_counter() - t0) * 1000)
    reg.record(ds['ds_key'], health or reg.DOWN, latency_ms=ms, row_count=n,
               message=msg)
    # record() 会保护 no_permission/broken 这类既定事实，所以回读一次真实结果
    return {'ds_key': ds['ds_key'], 'health': reg.health_of(ds['ds_key']),
            'probe_health': health, 'latency_ms': ms, 'row_count': n, 'message': msg}


def probe_all(*, only: list[str] | None = None,
              skip_sticky: bool = True) -> list[dict]:
    """
    探所有启用的源。

    `skip_sticky=True` 时跳过 no_permission / broken —— 它们的结论不会变，
    每次都去撞一遍只是白等超时。要重新确认就显式传 False。
    """
    out = []
    for ds in reg.list_sources():
        if only and ds['ds_key'] not in only:
            continue
        if skip_sticky and ds['health'] in reg.STICKY:
            out.append({'ds_key': ds['ds_key'], 'health': ds['health'],
                        'latency_ms': None, 'row_count': None,
                        'message': '既定结论，跳过探测'})
            continue
        out.append(probe_one(ds))
    return out
