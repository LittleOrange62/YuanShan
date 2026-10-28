# -*- coding: utf-8 -*-
"""
备份与恢复验证。

## 为什么不是"拷 TataPG 目录"就够了

拷数据目录属于物理备份，只在三个条件同时成立时可用：服务已停、目标机 PG
大版本相同（17.x）、同平台同架构。而且它是整簇的，恢复不了单张表。最要紧的
一条：**坏页会被原样拷走**。旧系统 37 GB 的 tq_minline_2026.db 报
"database disk image is malformed" 就是这个下场 —— 物理备份复制不了健康，
只复制现状。所以物理拷贝只当"整机搬家"的手段，不当日常备份。

## 分层策略

关键判断：**md 域是可再生的**。日线、分钟线的权威源是 vipdoc，坏了重解析即可。
真正不可再生的是 config.*（十三张注册表，即你在页面上点出来的全部配置）、
config.watchlist_*（自选股）、fct.*（评分结果）、ops.*（审计与血缘）。
这几个 schema 加起来很小，所以能做到天天备份。

  daily   pg_dump -Fc  只导 config + fct + ops + ref     留 30 天
  weekly  pg_dump -Fc  全库（含 md）                  留 8 周
  monthly pg_basebackup 整簇 + pg_dumpall --globals-only  留 6 份

pg_dump 单库**不含角色与口令**，所以 globals 必须单独导，否则换机恢复后
ft_reader/ft_app/ft_etl 全都不存在。这是最容易漏的一步。

## 没验证过的备份不算备份

verify() 会把 dump 恢复到临时库 financetool_verify，比对关键表行数，然后删库。
结果写进 operations.quality_result，页面上能看到"最近一次恢复验证是什么时候、过没过"。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
from pathlib import Path

from app import config
from app.db import engine

# daily 备份覆盖的 schema：全部不可再生的数据
CORE_SCHEMAS = ('config', 'analytics', 'operations', 'reference_data')

# 恢复验证时比对的表，行数为 0 说明恢复失败
VERIFY_TABLES = (
    'config.indicator', 'config.capability', 'config.binding', 'config.job',
    'config.field_spec', 'config.quality_rule', 'config.menu', 'config.page_layout',
    'reference_data.security', 'reference_data.trade_calendar', 'reference_data.sector_info',
)

#: 恢复验证的临时库**前缀**。名字必须再带一次运行的印记，见 `_verify_db()`。
VERIFY_DB = 'financetool_verify'

KEEP = {'daily': 30, 'weekly': 56, 'monthly': 180}

STAMP_RE = re.compile(r'_(\d{8}_\d{6})\.')


def _quiet_unlink(p: Path) -> None:
    """删一个可能不存在/正被占用的文件，删不掉也不炸（清理失败不该盖掉真正的病因）。"""
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _dump_bytes(p: Path) -> int:
    """一份档的字节数；目录型（monthly 的 basebackup）累加内容。"""
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
    except OSError:
        return 0


def _brief(msg: str, edge: int = 1200) -> str:
    """报错要**头尾都留**。原来只留 `msg[-2000:]`，而 pg_dump 的真错在第一行、
    后面跟着几百张表的进度/LOCK 清单 —— 只留尾巴等于把病因剪掉。
    2026-09-26 那次全库档失败就是这么被埋住的。"""
    msg = (msg or '').strip()
    if len(msg) <= edge * 2:
        return msg
    return msg[:edge] + f'\n…（中间省略 {len(msg) - edge * 2} 字）…\n' + msg[-edge:]


def _bin(name: str) -> str:
    p = config.PG_BIN / f'{name}.exe'
    if not p.exists():
        raise FileNotFoundError(f'找不到 {p}，检查 .env 的 FT_PG_BIN')
    return str(p)


def _env(password: str) -> dict:
    e = dict(os.environ)
    e['PGPASSWORD'] = password
    e['PGCLIENTENCODING'] = 'UTF8'
    return e


def _run(argv: list[str], password: str, *, timeout: int = 7200) -> tuple[bool, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=_env(password), timeout=timeout)
    msg = ((proc.stdout or '') + (proc.stderr or '')).strip()
    return proc.returncode == 0, msg


def _stamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime('%Y%m%d_%H%M%S')


def _outdir(kind: str) -> Path:
    d = config.BACKUP_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ 导出

def dump(kind: str = 'daily', *, now: dt.datetime | None = None) -> Path:
    """
    kind='daily' 只导核心 schema；kind='weekly' 导全库。
    -Fc 自定义压缩格式，配 pg_restore 用，支持只恢复单表。
    """
    admin = config.dsn_parts('admin')
    out = _outdir(kind) / f'{kind}_{_stamp(now)}.dump'
    argv = [_bin('pg_dump'), '-h', admin['host'], '-p', str(admin['port']),
            '-U', admin['user'], '-d', admin['dbname'],
            '-Fc', '-Z6', '--no-password', '-f', str(out)]
    if kind == 'daily':
        for s in CORE_SCHEMAS:
            argv += ['-n', s]
    ok, msg = _run(argv, admin['password'])
    if not ok or not out.exists() or out.stat().st_size == 0:
        # 失败的 0 字节文件必须消失。留着它有三层坏：`sorted()` 会把"最新一份"指到这个
        # 死文件上（doctor 就是这么报"读不开"而不是"没备份"）；`prune()` 按时间轮转，
        # 于是**新的死文件把旧的真备份挤掉**，某天真出事才发现整条备份线是空的。
        # 2026-09-26 实测：`weekly/` 里连留 4 个 0 字节档（18:25/18:26/18:28/19:31），
        # 病因是 ft_admin 对 ft_etl 建的 `ys.*` 表无 SELECT（现已授权修好）。
        _quiet_unlink(out)
        raise RuntimeError(f'pg_dump 失败：{_brief(msg)}')
    return out


def dump_globals(*, now: dt.datetime | None = None) -> Path:
    """角色与口令。pg_dump 单库不含这些，换机恢复必须先跑它。"""
    if not config.PG_SUPERPASS:
        raise RuntimeError('缺少 FT_PG_SUPERPASS，无法导出角色')
    out = _outdir('monthly') / f'globals_{_stamp(now)}.sql'
    ok, msg = _run(
        [_bin('pg_dumpall'), '-h', config.PG_HOST, '-p', str(config.PG_PORT),
         '-U', config.PG_SUPERUSER, '--globals-only', '--no-password',
         '-f', str(out)],
        config.PG_SUPERPASS)
    if not ok:
        raise RuntimeError(f'pg_dumpall 失败：{_brief(msg)}')
    return out


def basebackup(*, now: dt.datetime | None = None) -> Path:
    """
    在线整簇物理备份，不用停服务。产出 base.tar.gz + pg_wal.tar.gz。
    这是"整机搬家 / 灾难恢复"的兜底手段，恢复时 PG 大版本必须一致。
    """
    if not config.PG_SUPERPASS:
        raise RuntimeError('缺少 FT_PG_SUPERPASS，无法做 basebackup')
    target = _outdir('monthly') / f'base_{_stamp(now)}'
    target.mkdir(parents=True, exist_ok=True)
    ok, msg = _run(
        [_bin('pg_basebackup'), '-h', config.PG_HOST, '-p', str(config.PG_PORT),
         '-U', config.PG_SUPERUSER, '-D', str(target),
         '-Ft', '-z', '-Xstream', '-P', '--no-password'],
        config.PG_SUPERPASS)
    if not ok:
        raise RuntimeError(f'pg_basebackup 失败：{_brief(msg)}')
    return target


# ------------------------------------------------------------------ 恢复验证

def _verify_db(tag: str = '') -> str:
    """这次验证专用的临时库名：前缀 + 进程号 + 时分秒。

    固定名 `financetool_verify` 栽过一次（2026-10-14 实测）：上一轮验证留下的库还有连接挂着时
    `DROP DATABASE IF EXISTS` 会失败，而那一步的返回原先是**丢掉的** ——
    于是紧接着的 `CREATE DATABASE` 报 `database "financetool_verify" already exists`，
    屏幕上只留下一句"恢复验证未通过"。之后每一档备份都长成同一个红法，
    查不出是"这一档坏了"还是"上一轮的临时库还占着"。
    名字唯一化让两者不可能撞上；顺手把删除前的占用会话收掉、并把 DROP 失败说出来。
    """
    return f'{VERIFY_DB}_{os.getpid()}_{dt.datetime.now().strftime("%H%M%S")}{tag}'


def _db_name_or_raise(db: str) -> str:
    """库名只允许 `[A-Za-z0-9_]`：这几句 SQL 是字符串拼的（`psql -tAc` 不能绑参），
    名字来自本模块自己，不给它任何拼进 SQL 的机会。"""
    if not re.fullmatch(r'[A-Za-z0-9_]+', db):
        raise ValueError(f'临时库名不合形状，不拼进 SQL：{db!r}')
    return db


def verify(dump_path: Path) -> dict:
    """
    把 dump 恢复到临时库并比对关键表行数，然后删库。
    返回 {'ok': bool, 'tables': {...}, 'error': str|None}。
    """
    if not config.PG_SUPERPASS:
        return {'ok': False, 'error': '缺少 FT_PG_SUPERPASS，跳过恢复验证',
                'tables': {}}
    admin = config.dsn_parts('admin')
    su, supass = config.PG_SUPERUSER, config.PG_SUPERPASS
    psql = _bin('psql')
    db = _db_name_or_raise(_verify_db())

    def su_sql(sql: str) -> tuple[bool, str]:
        return _run([psql, '-h', config.PG_HOST, '-p', str(config.PG_PORT),
                     '-U', su, '-d', 'postgres', '-tAc', sql, '--no-psqlrc'],
                    supass, timeout=600)

    def _clear(name: str) -> tuple[bool, str]:
        """先收掉还连着这个名字的会话，再删 —— 顺序不能反：连着的时候 DROP 必失败。"""
        su_sql(f"select pg_terminate_backend(pid) from pg_stat_activity "
               f"where datname = '{name}' and pid <> pg_backend_pid()")
        return su_sql(f'DROP DATABASE IF EXISTS {name}')

    ok, msg = _clear(db)
    if not ok:
        return {'ok': False, 'error': f'清掉旧临时库 {db} 没成，先不建新的：{msg[-300:]}', 'tables': {}}
    # 名字唯一化之后，固定名那一轮留下的库不会再被谁清理 —— 它占着磁盘，
    # 而且那 43 个 `pg_restore -j4` 的残留客户端进程会一直挂着它（2026-10-14 实测）。
    # 这里顺手清一次；清不掉就如实带出来，不静默。
    # 存在性用目录查，不去解析错误消息里的子串：这台机器的服务端消息是**中文**的
    # （实测 `错误:  数据库 "…" 不存在`），按 `'does not exist' in msg` 判会永远判错。
    found, _ = su_sql(f"select 1 from pg_database where datname = '{VERIFY_DB}'")
    stale_exists = found and _ is not None and str(_).strip() == '1'
    result_stale = None
    if stale_exists:
        stale_ok, stale_msg = _clear(VERIFY_DB)
        if not stale_ok:
            result_stale = stale_msg[-200:]
    ok, msg = su_sql(f'CREATE DATABASE {db} OWNER {admin["user"]}')
    if not ok:
        return {'ok': False, 'error': f'建临时库失败：{msg[-500:]}', 'tables': {}}

    result: dict = {'ok': False, 'tables': {}, 'error': None, 'verify_db': db}
    if result_stale:
        result['stale_verify_db'] = result_stale
    try:
        # pg_restore 对缺失的 owner/权限会报 warning，不影响数据，所以不看返回码，
        # 只看最终行数对不对 —— 判据放在结果上，不放在过程上。
        _run([_bin('pg_restore'), '-h', admin['host'], '-p', str(admin['port']),
              '-U', admin['user'], '-d', db, '-j', '4',
              '--no-owner', '--no-privileges', '--no-password', str(dump_path)],
             admin['password'])

        empty = []
        for table in VERIFY_TABLES:
            ok2, out = _run(
                [psql, '-h', admin['host'], '-p', str(admin['port']),
                 '-U', admin['user'], '-d', db, '--no-psqlrc',
                 '-tAc', f'SELECT count(*) FROM {table}'],
                admin['password'], timeout=600)
            n = int(out) if ok2 and out.strip().isdigit() else -1
            result['tables'][table] = n
            if n <= 0:
                empty.append(table)
        result['ok'] = not empty
        if empty:
            result['error'] = '恢复后为空或查不到的表：' + ', '.join(empty)
    finally:
        dropped, dmsg = _clear(db)
        if not dropped:
            # 删不掉不改变这一档的成败（数据结论已经出了），但必须留字：
            # 孤儿临时库会一直占着磁盘，下一轮的 DROP 也会开始失败。
            result['cleanup_error'] = f'临时库 {db} 没删掉：{dmsg[-200:]}'
    return result


# ------------------------------------------------------------------ 轮转

def rotate(kind: str, root: Path | None = None) -> list[Path]:
    """按保留天数删旧备份。文件名里的时间戳是判据，不看 mtime（拷贝会改 mtime）。

    `root` 给跨盘副本用（同一个保留口径，两棵树都要有人清）；不传就是本机那棵树。
    """
    keep_days = KEEP.get(kind, 30)
    cutoff = dt.datetime.now() - dt.timedelta(days=keep_days)
    removed = []
    d = (root if root is not None else config.BACKUP_DIR) / kind
    if not d.exists():
        return removed
    for p in sorted(d.iterdir()):
        m = STAMP_RE.search(p.name) or re.search(r'_(\d{8}_\d{6})$', p.name)
        if not m:
            continue
        try:
            stamp = dt.datetime.strptime(m.group(1), '%Y%m%d_%H%M%S')
        except ValueError:
            continue
        if stamp < cutoff:
            if p.is_dir():
                for sub in sorted(p.rglob('*'), reverse=True):
                    sub.unlink() if sub.is_file() else sub.rmdir()
                p.rmdir()
            else:
                p.unlink()
            removed.append(p)
    return removed


# ------------------------------------------------------------------ 跨盘副本


def mirror(src: Path, *, dest_root: Path | None = None) -> dict:
    """把这一份产物复制到**第二块盘**，并核对字节数。

    为什么现在要有这一层（2026-10-18 实测）：`C:\\finance_backup\\TataPG\\daily` 里还是
    09-25 那两份，之后没有任何代码在写它 —— 那句话是当时手工复制的一次性动作，
    而"备份有一份在另一块盘上"这个结论已经悄悄过期三个星期，体检只看 D 盘那棵树，根本看不见。
    单盘＝D 盘坏就等于全丢，这一档必须自己跑，不能等人记得复制。

    三条边界：
      · `FT_BACKUP_MIRROR_DIR` 置空＝明确不做这件事，台账里写"没配"，不算失败也不算做成；
      · **只复制文件**（dump 与 globals）；目录型的 basebackup 是整簇体量，跨盘复制它
        会把夜间窗口吃掉，要做该单独排一档，这里不假装做了 —— 交回 `skipped` 并写明原因；
      · pytest 进程里绝不往真盘上写（与 `shells.reap` 的 `kill=None`、`c23.spawn` 同形的一道洞）。
    """
    root = config.BACKUP_MIRROR_DIR if dest_root is None else dest_root
    if root is None or str(root) in ('', '.'):
        return {'ok': None, 'skipped': '没配第二块盘（FT_BACKUP_MIRROR_DIR 置空＝这一档不做跨盘副本）'}
    if dest_root is None and os.environ.get('PYTEST_CURRENT_TEST'):
        return {'ok': None, 'skipped': '测试进程：不往真盘上复制'}
    if not src.is_file():
        return {'ok': None, 'skipped': f'{src.name} 不是单文件档（目录型 basebackup 不做跨盘复制）'}
    kind = src.parent.name
    dst_dir = root / kind
    dst = dst_dir / src.name
    src_bytes = _dump_bytes(src)
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        got = _dump_bytes(dst)
    except OSError as exc:
        return {'ok': False, 'to': str(dst),
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
    if got != src_bytes:
        return {'ok': False, 'to': str(dst),
                'error': f'跨盘副本字节数对不上：源 {src_bytes}、副本 {got}（半截文件，恢复时用不了）'}
    removed = [p.name for p in rotate(kind, root=root)]
    return {'ok': True, 'to': str(dst), 'mb': round(got / 1024 / 1024, 2),
            'rotated': removed}


# ------------------------------------------------------------------ 留痕

def _record(rule_key: str, severity: str, passed: bool, detail: dict) -> None:
    """写进 operations.quality_result，页面的数据质量看板直接能看到。"""
    try:
        from app.db.repo import ops as ops_repo
        ops_repo.save_quality_result(
            dt.date.today(), rule_key, 'backup', severity, passed,
            actual=detail)
    except Exception as exc:  # noqa: BLE001
        print('  [warn] 备份结果未能写入 operations.quality_result：%s' % exc)


# ------------------------------------------------------------------ 编排

def _as_date(day: dt.date | str | None) -> dt.date:
    """把"哪一天"归成 `date`。

    ⚠️ 这一层是被一次真事故逼出来的（2026-10-14 20:13 日志）：常驻备份循环把
    `util.china_today()` 交回的**字符串**（`"2026-10-14"`）原样递进来，而判档写的是
    `day.day` —— 字符串没有 `.day`，于是每一轮都在同一行炸掉，`AttributeError` 被循环的
    `except` 吞成一行黄字，**自这条循环接进常驻进程起就没背成过一次**。
    单测当时全绿：`tests/test_backup_round.py` 从头到尾只喂 `dt.date(...)`，
    没喂过生产真给的那个类型（同 10-10 那回"桩把 china_today 打成 date"的假绿）。
    所以现在**两种都收**，并在 `tests/test_backup_round.py` 里补一条"喂 `china_today()` 的原样返回值"的用例。
    """
    if day is None:
        return dt.date.today()
    if isinstance(day, dt.datetime):
        return day.date()
    if isinstance(day, dt.date):
        return day
    text = str(day).strip()[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"日期要形如 2026-10-14，收到「{day}」") from exc


def kind_for(day: dt.date | str | None) -> str:
    """这一天该背哪一档 —— 判据只住这一处，常驻循环与 `main()` 都从这里取。

    - 每月 1 号：`monthly`（全库 dump + globals + basebackup，最慢但最兜底）；
    - 周六、周日：`weekly`（全库 dump）—— 周末盘后链不跑，机器空，跑得动全库；
    - 其余：`daily`（只核心 schema，快）。

    为什么这么分：全库那两档都比 daily 慢一个量级，天天跑会把夜间窗口吃掉；
    而"最近一次全库"如果只靠人记得跑，实测就会变成 148 小时前那一份（2026-10-12）。

    `day` 收 `date` 也收 ISO 串 —— 见 `_as_date()` 那段（调用方两条链给的不是同一个类型）。
    """
    day = _as_date(day)
    if day.day == 1:
        return 'monthly'
    return 'weekly' if day.weekday() >= 5 else 'daily'


def run(kind: str = 'daily', *, do_verify: bool | None = None) -> dict:
    """
    daily   核心 schema dump（+ 恢复验证）
    weekly  全库 dump
    monthly 全库 dump + globals + basebackup + 恢复验证
    """
    if kind not in ('daily', 'weekly', 'monthly'):
        raise ValueError(f'未知备份类型：{kind}')
    now = dt.datetime.now()
    out: dict = {'kind': kind, 'started_at': now.isoformat(), 'artifacts': []}

    dump_kind = 'daily' if kind == 'daily' else 'weekly'
    path = dump(dump_kind, now=now)
    size_mb = round(path.stat().st_size / 1024 / 1024, 2)
    out['artifacts'].append({'path': str(path), 'mb': size_mb})
    print('  dump        %s  %.2f MB' % (path.name, size_mb))

    if kind == 'monthly':
        g = dump_globals(now=now)
        out['artifacts'].append({'path': str(g), 'mb': round(
            g.stat().st_size / 1024, 2)})
        print('  globals     %s' % g.name)
        b = basebackup(now=now)
        tot = sum(f.stat().st_size for f in b.rglob('*') if f.is_file())
        out['artifacts'].append({'path': str(b), 'mb': round(tot / 1024 / 1024, 2)})
        print('  basebackup  %s  %.1f MB' % (b.name, tot / 1024 / 1024))

    # **0 字节的产物不算背到**。这不是假想：`monthly/globals_20260924_231413.sql` 至今是 0 字节，
    # 而 `_失败_weekly_20260926/` 里躺着四份 0 字节的 weekly —— 命令返回码 0、文件却空的先例有过。
    # 只看 returncode 的话，那一档会被记成"成功"，等到真要恢复那天才发现手里是一份空档。
    # 判据用 `_dump_bytes`（目录型档累加内容），空档直接把这一轮判失败，**并且不做轮转**：
    # 一轮没背成不该顺手把旧的、还能用的那份删掉。
    empties = [Path(a['path']) for a in out['artifacts'] if not _dump_bytes(Path(a['path']))]
    if empties:
        out['verify'] = {'ok': False,
                         'error': '产物是 0 字节：%s（命令没报错，但那是空档，恢复时用不了）'
                                  % '、'.join(p.name for p in empties)}
        _record('backup_%s' % kind, 'error', False, out)
        return out

    if do_verify is None:
        do_verify = kind in ('daily', 'monthly')
    if do_verify:
        v = verify(path)
        out['verify'] = v
        print('  恢复验证    %s' % ('通过' if v['ok'] else '未通过：' + str(v['error'])))
    else:
        out['verify'] = {'ok': None, 'error': '本次未做恢复验证'}

    # 验过了才往第二块盘上拷 —— 半截或坏档复制到那边，只会把"还有一份能救"变成两份都不能救。
    mir = mirror(path)
    out['mirror'] = mir
    if mir.get('ok'):
        print('  跨盘副本    %s（%.2f MB）' % (mir['to'], mir['mb']))
    elif mir.get('skipped'):
        print('  跨盘副本    跳过：%s' % mir['skipped'])
    else:
        print('  ⚠️ 跨盘副本  **没落地**：%s → 这一轮只有一份，在 %s' % (mir.get('error'), path))

    for k in ('daily', 'weekly', 'monthly'):
        rm = rotate(k)
        if rm:
            print('  轮转 %-8s 删除 %d 份过期备份' % (k, len(rm)))
            out.setdefault('rotated', {})[k] = [p.name for p in rm]

    v = out.get('verify') or {}
    ok = v.get('ok')
    # 跨盘副本没落地＝这一轮只有一份档，按"没做成"记（不是"做成了但有件事没做"）：
    # 单盘的风险正是这句话，记成 info 就会一直没人看见。措辞点名是哪一半坏的。
    mirror_bad = mir.get('ok') is False
    if mirror_bad:
        out['mirror_failed'] = True
        print('  ⚠️ 这一轮记为失败：备份只落在了一块盘上（跨盘副本没做成）')
    _record('backup_%s' % kind, 'info' if (ok is not False and not mirror_bad) else 'error',
            ok is not False and not mirror_bad, out)
    return out


def run_once(*, day: dt.date | str | None = None, runner=None, journal=None) -> dict:
    """跑一轮"今天该背的那一档"，并把结果记进作业台账 `jobs.record('backup')`。

    为什么要单独有一层：备份的**动作**（`run`）早就齐了，缺的是"谁去判该背哪一档、
    背完算不算成、成没成都要有一格账"。这三件事原先散在宿主里，就会出现
    2026-10-12 那种形状 —— 模块能跑、doctor 一直红、没人（也没程序）去补。

    `day` 同样两种都收（`date` 或 ISO 串）——常驻循环给的正是 `util.china_today()` 那个串，
    见 `_as_date()` 的来由。

    `runner` / `journal` 是给单测留的洞（与 `app.tdx_client.shells.reap` 的 `probe/kill` 同形）：
    真值分别是 `run()` 与 `duanxian.jobs.record`。单测里两个都换掉，
    判据能测而绝不落到真 pg_dump、也绝不写真的 state.json。
    """
    day = _as_date(day)
    kind = kind_for(day)
    out: dict = {'kind': kind, 'ok': False, 'artifact': None, 'mb': None,
                 'verify_ok': None, 'rotated': {}, 'reason': ''}
    if runner is None and os.environ.get('PYTEST_CURRENT_TEST'):
        # **测试进程里不许起真备份**（与 `duanxian.jobs._spawn_stage`、`app.tdx_client.shells.reap` 同形）。
        # 这条循环挂在常驻进程里，而 pytest 的 TestClient 会跑 lifespan ⇒ 线程照起；
        # 只要那一轮恰好落进 `run_at` 的补窗，一次全库 dump 就是 500 MB + 十几分钟 + 真占 pg_dump 句柄。
        # 单测要测判据就把 `runner`/`journal` 两个洞换掉 —— 少一层洞，就少一处会漏翻译的地方。
        # 这里**不抛也不记账**：抛会让宿主的 except 把这一轮写成一条失败账，
        # 而那条账落在真 `state.json` 里（测试未必改过 `ASTOCK_DATA_HOME`）⇒ 测试把自己的台账写脏了。
        out['reason'] = ('测试进程里不许起真实备份（会落一份几百 MB 的 dump 并占住 pg_dump）：'
                         '判据请换 runner/journal 两个洞来测')
        return out
    do = runner or (lambda k: run(k))
    try:
        res = do(kind)
    except Exception as exc:  # noqa: BLE001  背不成要有名字有原因，不能只让线程消失
        out['reason'] = '%s: %s' % (type(exc).__name__, str(exc)[:160])
        _journal(out, journal)
        return out
    art = (res.get('artifacts') or [{}])[0]
    v = res.get('verify') or {}
    out.update({'artifact': art.get('path'), 'mb': art.get('mb'),
                'verify_ok': v.get('ok'),
                'rotated': {k: len(x or []) for k, x in (res.get('rotated') or {}).items()}})
    # 恢复验证**明确不通过**才算失败（那份备份不可信）；weekly 按设计不做恢复验证，
    # `verify.ok` 是 None —— 把"没验"当成"验不过"会让每周那一档永远记成红。
    # 跨盘副本同理分三种：`None`＝没配或这一档不做（不判红），`False`＝配了却没落地（判红，
    # 因为"只有一份"正是这一档存在的唯一理由），`True`＝两份都在。
    mir = res.get('mirror') or {}
    out['mirror_ok'] = mir.get('ok')
    out['ok'] = (bool(art.get('path')) and v.get('ok') is not False
                 and mir.get('ok') is not False)
    if not out['ok'] and mir.get('ok') is False and v.get('ok') is not False:
        out['reason'] = ('跨盘副本没落地：%s → 这一轮只有一份档，在 %s'
                         % (str(mir.get('error'))[:120], art.get('path')))
    else:
        out['reason'] = ('' if out['ok']
                         else '恢复验证未通过：%s' % str(v.get('error') or art.get('path') or '没有产物')[:160])
    _journal(out, journal)
    return out


def _note(detail: dict, *, ok: bool, journal=None) -> None:
    """把宿主那一轮的结果写进 `backup` 这一格台账。记账本身坏了不许带崩备份。"""
    if journal is None:
        try:
            from duanxian import jobs as _jobs
            journal = _jobs.record
        except Exception as exc:  # noqa: BLE001
            print('  [warn] 备份台账写不进（jobs 模块不可用）：%s' % exc)
            return
    try:
        journal('backup', ok=ok, detail=detail)
    except Exception as exc:  # noqa: BLE001
        print('  [warn] 备份结果没记进作业台账：%s: %s' % (type(exc).__name__, str(exc)[:120]))


def journal_skipped(reason: str, *, journal=None) -> None:
    """把"这一轮按设计不动手"（加工段正在跑）记进**同一格**台账。

    用 `detail['skipped']` 那一档：它既不刷新 `last_ok_at`、也不涨连败 —— 观望不是失败
    （`duanxian/jobs.py::record` 的第三种结果）。写这一格是为了让 doctor 读得出"循环活着、
    只是这一轮让路"，而不是让人去猜它有没有起。
    """
    _note({'skipped': True, 'reason': reason}, ok=False, journal=journal)


def journal_failure(reason: str, *, journal=None) -> None:
    """宿主那一轮的**异常**（读时钟失败、模块 import 失败……）也要有一格账。

    漏掉它就是"线程还在、什么都不做、界面上看不出来" —— 正是《自动执行机制自查表》判据①。
    """
    _note({'reason': reason}, ok=False, journal=journal)


def _journal(out: dict, journal=None) -> None:
    """记台账。记账本身坏了不许带崩备份（与 `server.py` 各循环同形）。"""
    if journal is None:
        try:
            from duanxian import jobs as _jobs
            journal = _jobs.record
        except Exception as exc:  # noqa: BLE001
            print('  [warn] 备份台账写不进（jobs 模块不可用）：%s' % exc)
            return
    try:
        journal('backup', ok=bool(out.get('ok')), detail={
            'kind': out.get('kind'), 'artifact': out.get('artifact'), 'mb': out.get('mb'),
            'verify_ok': out.get('verify_ok'), 'rotated': out.get('rotated'),
            # 跨盘那一份到底有没有落地：`None`＝这一档不做（没配/目录型），`False`＝该做而没做成。
            # 不交这一格，页面上"备份成功"就看不出它其实只有一份。
            'mirror_ok': out.get('mirror_ok'),
            'reason': out.get('reason') or None})
    except Exception as exc:  # noqa: BLE001
        print('  [warn] 备份结果没记进作业台账：%s: %s' % (type(exc).__name__, str(exc)[:120]))


def latest(kind: str = 'daily') -> Path | None:
    d = config.BACKUP_DIR / kind
    if not d.exists():
        return None
    files = sorted((p for p in d.iterdir() if p.suffix == '.dump'),
                   key=lambda p: p.name)
    return files[-1] if files else None


def status() -> list[dict]:
    """给页面用：每种备份的最新一份、大小、距今多久。

    ⚠️ **0 字节的档不算备份**。按名字排序取最后一份的写法会把一次失败的 pg_dump
    说成"今天刚备份过"—— 2026-09-26 实测 `weekly/` 里连着 4 个 0 字节文件就是这么
    被 doctor 读成"最新一份读不开"，而不是"根本没有可用备份"。
    空档另外单独报在 `empty` 里，让"失败了 N 次"这件事看得见。
    """
    out = []
    now = dt.datetime.now()
    for kind in ('daily', 'weekly', 'monthly'):
        d = config.BACKUP_DIR / kind
        all_items = sorted(d.iterdir(), key=lambda p: p.name) if d.exists() else []
        empty = [p.name for p in all_items if _dump_bytes(p) == 0]
        items = [p for p in all_items if _dump_bytes(p) > 0]
        if not items:
            out.append({'kind': kind, 'latest': None, 'age_hours': None,
                        'count': 0, 'mb': 0, 'empty': empty})
            continue
        last = items[-1]
        m = STAMP_RE.search(last.name) or re.search(r'_(\d{8}_\d{6})$', last.name)
        age = None
        if m:
            try:
                age = round((now - dt.datetime.strptime(
                    m.group(1), '%Y%m%d_%H%M%S')).total_seconds() / 3600, 1)
            except ValueError:
                age = None
        out.append({'kind': kind, 'latest': last.name, 'age_hours': age,
                    'count': len(items), 'mb': round(_dump_bytes(last) / 1024 / 1024, 2),
                    'empty': empty})
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description='数据库备份（每日/每周/全局）')
    ap.add_argument('kind', nargs='?', default='daily',
                    choices=['daily', 'weekly', 'monthly', 'status'])
    ap.add_argument('--no-verify', action='store_true')
    args = ap.parse_args()

    config.ensure_dirs()
    if args.kind == 'status':
        for s in status():
            print('  %-8s %-32s %s 份  %.1f MB  距今 %s 小时'
                  % (s['kind'], s['latest'] or '-', s['count'], s['mb'],
                     s['age_hours'] if s['age_hours'] is not None else '?'))
        return 0

    print('=== 备份 %s ===' % args.kind)
    try:
        res = run(args.kind, do_verify=False if args.no_verify else None)
    except Exception as exc:  # noqa: BLE001
        print('备份失败：%s' % exc)
        _record('backup_%s' % args.kind, 'error', False, {'error': str(exc)})
        return 1
    finally:
        engine.close_all()
    return 0 if (res.get('verify', {}).get('ok') is not False) else 2


if __name__ == '__main__':
    raise SystemExit(main())

