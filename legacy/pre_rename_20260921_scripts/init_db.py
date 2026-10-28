# -*- coding: utf-8 -*-
"""
建库 / 重建库。

按顺序跑 app/db/sql/*.sql（用 psql.exe，它能正确处理 DO 块和多语句脚本）。
所有脚本都是幂等的，可以反复执行。

  01_roles.sql       postgres @ postgres     角色 + 超时
  CREATE DATABASE    postgres @ postgres     financetool，owner=ft_admin
  02_schema.sql      ft_admin  @ financetool schema、表、分区
  03_registry.sql    ft_admin  @ financetool 13 张 config 注册表
  04_constraints.sql ft_admin  @ financetool CHECK、触发器、视图、授权
  05_seed.sql        ft_admin  @ financetool 种子数据

跑完把每个文件的 sha256 写进 ops.schema_migration。校验和变了说明
建库脚本改过但库没重跑 —— 这是替代 Alembic 的轻量版本化。

口令全部从 .env 读，本文件零明文。
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402

PSQL = str(config.PG_BIN / 'psql.exe')
SQL_DIR = config.BASE_DIR / 'app' / 'db' / 'sql'

HOST = config.PG_HOST
PORT = str(config.PG_PORT)
DB = config.PG_DB

ADMIN = config.dsn_parts('admin')
READER = config.dsn_parts('reader')

APPLY_ORDER = ('02_schema.sql', '03_registry.sql', '04_constraints.sql',
               '05_seed.sql', '06_comments.sql', '07_scheduler.sql')


def _run(user, password, db, args, label):
    env = dict(os.environ)
    env['PGPASSWORD'] = password
    env['PGCLIENTENCODING'] = 'UTF8'
    base = [PSQL, '-h', HOST, '-p', PORT, '-U', user, '-d', db, '--no-psqlrc']
    proc = subprocess.run(base + args, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=env)
    return proc


def run_psql(user, password, db, *, file=None, command=None, tolerate=()):
    args = ['-v', 'ON_ERROR_STOP=1']
    label = command or os.path.basename(file)
    args += ['-f', file] if file else ['-c', command]

    proc = _run(user, password, db, args, label)
    out = (proc.stdout or '').strip()
    err = (proc.stderr or '').strip()

    if proc.returncode != 0:
        if any(t in err for t in tolerate):
            print('  [skip] %s -> %s' % (label, err.splitlines()[0][:120]))
            return True
        print('  [FAIL] %s (exit %s)' % (label, proc.returncode))
        if out:
            print('  stdout: %s' % out[-3000:])
        if err:
            print('  stderr: %s' % err[-3000:])
        return False

    print('  [ok]   %s' % label)
    for line in err.splitlines():
        if 'NOTICE' in line and 'already exists' not in line:
            print('         %s' % line[:160])
    return True


def scalar(user, password, db, sql):
    proc = _run(user, password, db, ['-tAc', sql], sql[:40])
    return (proc.stdout or '').strip() or (proc.stderr or '').strip()[:80]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_checksums():
    """把已应用文件的 sha256 记入 ops.schema_migration。"""
    values = []
    for name in APPLY_ORDER:
        values.append("('%s','%s')" % (name, sha256(SQL_DIR / name)))
    sql = (
        'INSERT INTO ops.schema_migration (filename, checksum) VALUES %s '
        'ON CONFLICT (filename) DO UPDATE SET checksum = excluded.checksum, '
        'applied_at = now(), applied_by = current_user'
    ) % ', '.join(values)
    return run_psql(ADMIN['user'], ADMIN['password'], DB, command=sql)


def main():
    if not os.path.exists(PSQL):
        print('psql 不存在: %s' % PSQL)
        return 1
    if not config.PG_SUPERPASS:
        print('缺少 FT_PG_SUPERPASS，请在 D:\\FinanceTool\\.env 中配置')
        return 1

    su, supass = config.PG_SUPERUSER, config.PG_SUPERPASS

    print('=== 1. 角色 ===')
    if not run_psql(su, supass, 'postgres', file=str(SQL_DIR / '01_roles.sql')):
        return 1

    print('=== 2. 数据库 ===')
    run_psql(su, supass, 'postgres',
             command='CREATE DATABASE %s OWNER ft_admin ENCODING UTF8' % DB,
             tolerate=('already exists',))

    print('=== 3. schema / 注册表 / 约束 / 种子 ===')
    for name in APPLY_ORDER:
        if not run_psql(ADMIN['user'], ADMIN['password'], DB,
                        file=str(SQL_DIR / name)):
            return 1

    print('=== 4. 记录 schema 版本 ===')
    if not record_checksums():
        return 1

    print('=== 5. 验收 ===')
    checks = [
        ("表数量", "SELECT count(*) FROM information_schema.tables "
                   "WHERE table_schema IN ('md','ref','fct','config','ops')"),
        ("md.stock_daily 分区", "SELECT count(*) FROM pg_inherits "
                                "WHERE inhparent = 'md.stock_daily'::regclass"),
        ("CHECK 约束", "SELECT count(*) FROM pg_constraint "
                       "WHERE conname LIKE 'ck_daily%'"),
        ("config.indicator", "SELECT count(*) FROM config.indicator"),
        ("config.capability", "SELECT count(*) FROM config.capability"),
        ("config.binding", "SELECT count(*) FROM config.binding"),
        ("config.job", "SELECT count(*) FROM config.job"),
        ("config.field_spec", "SELECT count(*) FROM config.field_spec"),
        ("config.quality_rule", "SELECT count(*) FROM config.quality_rule"),
        ("config.menu", "SELECT count(*) FROM config.menu"),
        ("config.page_layout", "SELECT count(*) FROM config.page_layout"),
        ("schema_migration", "SELECT count(*) FROM ops.schema_migration"),
        ("权重归一化异常数", "SELECT count(*) FROM (SELECT cap_key, sum(eff_weight) s "
                             "FROM config.v_effective_binding GROUP BY cap_key) t "
                             "WHERE abs(s-1) > 0.0001"),
    ]
    for label, sql in checks:
        print('  %-22s %s' % (label, scalar(READER['user'], READER['password'], DB, sql)))

    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
