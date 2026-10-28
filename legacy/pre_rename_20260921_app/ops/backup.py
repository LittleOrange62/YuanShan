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
结果写进 ops.quality_result，页面上能看到"最近一次恢复验证是什么时候、过没过"。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
from pathlib import Path

from app import config
from app.db import engine

# daily 备份覆盖的 schema：全部不可再生的数据
CORE_SCHEMAS = ('config', 'fct', 'ops', 'ref')

# 恢复验证时比对的表，行数为 0 说明恢复失败
VERIFY_TABLES = (
    'config.indicator', 'config.capability', 'config.binding', 'config.job',
    'config.field_spec', 'config.quality_rule', 'config.menu', 'config.page_layout',
    'ref.security', 'ref.trade_calendar', 'ref.sector_info',
)

VERIFY_DB = 'financetool_verify'

KEEP = {'daily': 30, 'weekly': 56, 'monthly': 180}

STAMP_RE = re.compile(r'_(\d{8}_\d{6})\.')


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
    if not ok:
        raise RuntimeError(f'pg_dump 失败：{msg[-2000:]}')
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
        raise RuntimeError(f'pg_dumpall 失败：{msg[-2000:]}')
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
        raise RuntimeError(f'pg_basebackup 失败：{msg[-2000:]}')
    return target


# ------------------------------------------------------------------ 恢复验证

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

    def su_sql(sql: str) -> tuple[bool, str]:
        return _run([psql, '-h', config.PG_HOST, '-p', str(config.PG_PORT),
                     '-U', su, '-d', 'postgres', '-tAc', sql, '--no-psqlrc'],
                    supass, timeout=600)

    su_sql(f'DROP DATABASE IF EXISTS {VERIFY_DB}')
    ok, msg = su_sql(f'CREATE DATABASE {VERIFY_DB} OWNER {admin["user"]}')
    if not ok:
        return {'ok': False, 'error': f'建临时库失败：{msg[-500:]}', 'tables': {}}

    result: dict = {'ok': False, 'tables': {}, 'error': None}
    try:
        # pg_restore 对缺失的 owner/权限会报 warning，不影响数据，所以不看返回码，
        # 只看最终行数对不对 —— 判据放在结果上，不放在过程上。
        _run([_bin('pg_restore'), '-h', admin['host'], '-p', str(admin['port']),
              '-U', admin['user'], '-d', VERIFY_DB, '-j', '4',
              '--no-owner', '--no-privileges', '--no-password', str(dump_path)],
             admin['password'])

        empty = []
        for table in VERIFY_TABLES:
            ok2, out = _run(
                [psql, '-h', admin['host'], '-p', str(admin['port']),
                 '-U', admin['user'], '-d', VERIFY_DB, '--no-psqlrc',
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
        su_sql(f'DROP DATABASE IF EXISTS {VERIFY_DB}')
    return result


# ------------------------------------------------------------------ 轮转

def rotate(kind: str) -> list[Path]:
    """按保留天数删旧备份。文件名里的时间戳是判据，不看 mtime（拷贝会改 mtime）。"""
    keep_days = KEEP.get(kind, 30)
    cutoff = dt.datetime.now() - dt.timedelta(days=keep_days)
    removed = []
    d = config.BACKUP_DIR / kind
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


# ------------------------------------------------------------------ 留痕

def _record(rule_key: str, severity: str, passed: bool, detail: dict) -> None:
    """写进 ops.quality_result，页面的数据质量看板直接能看到。"""
    try:
        from app.db.repo import ops as ops_repo
        ops_repo.save_quality_result(
            dt.date.today(), rule_key, 'backup', severity, passed,
            actual=detail)
    except Exception as exc:  # noqa: BLE001
        print('  [warn] 备份结果未能写入 ops.quality_result：%s' % exc)


# ------------------------------------------------------------------ 编排

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

    if do_verify is None:
        do_verify = kind in ('daily', 'monthly')
    if do_verify:
        v = verify(path)
        out['verify'] = v
        print('  恢复验证    %s' % ('通过' if v['ok'] else '未通过：' + str(v['error'])))
    else:
        out['verify'] = {'ok': None, 'error': '本次未做恢复验证'}

    for k in ('daily', 'weekly', 'monthly'):
        rm = rotate(k)
        if rm:
            print('  轮转 %-8s 删除 %d 份过期备份' % (k, len(rm)))
            out.setdefault('rotated', {})[k] = [p.name for p in rm]

    v = out.get('verify') or {}
    ok = v.get('ok')
    _record('backup_%s' % kind, 'info' if ok is not False else 'error',
            ok is not False, out)
    return out


def latest(kind: str = 'daily') -> Path | None:
    d = config.BACKUP_DIR / kind
    if not d.exists():
        return None
    files = sorted((p for p in d.iterdir() if p.suffix == '.dump'),
                   key=lambda p: p.name)
    return files[-1] if files else None


def status() -> list[dict]:
    """给页面用：每种备份的最新一份、大小、距今多久。"""
    out = []
    now = dt.datetime.now()
    for kind in ('daily', 'weekly', 'monthly'):
        d = config.BACKUP_DIR / kind
        items = sorted(d.iterdir(), key=lambda p: p.name) if d.exists() else []
        if not items:
            out.append({'kind': kind, 'latest': None, 'age_hours': None,
                        'count': 0, 'mb': 0})
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
        size = (last.stat().st_size if last.is_file()
                else sum(f.stat().st_size for f in last.rglob('*') if f.is_file()))
        out.append({'kind': kind, 'latest': last.name, 'age_hours': age,
                    'count': len(items), 'mb': round(size / 1024 / 1024, 2)})
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description='FinanceTool 备份')
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

