# -*- coding: utf-8 -*-
"""Verify that every runtime dependency is importable and the DB is reachable."""
import importlib.util
import sys

sys.stdout.reconfigure(encoding='utf-8')

MODULES = [
    'psycopg', 'psycopg_pool', 'pandas', 'numpy', 'sqlalchemy', 'alembic',
    'dash', 'dash_bootstrap_components', 'plotly', 'waitress', 'apscheduler',
    'pydantic', 'sqlglot', 'pypinyin', 'argon2', 'pytest', 'ruff', 'dotenv',
]

missing = []
for m in MODULES:
    ok = importlib.util.find_spec(m) is not None
    print('%-30s %s' % (m, 'OK' if ok else 'MISSING'))
    if not ok:
        missing.append(m)

print()
if missing:
    print('MISSING: %s' % ', '.join(missing))
else:
    print('all dependencies present')

# 数据库连通性（走 app.db.engine，顺便验证 config/.env 解析）
print()
sys.path.insert(0, r'D:\FinanceTool')
try:
    from app.db import engine
    engine.set_app_name('dep_check')
    print('db healthcheck: %s' % engine.healthcheck())
    print('field_spec rows: %s' % engine.scalar('SELECT count(*) FROM config.field_spec'))
    print('effective weights:')
    for r in engine.query(
            'SELECT cap_key, round(sum(eff_weight), 4) AS total, count(*) AS n '
            'FROM config.v_effective_binding GROUP BY cap_key ORDER BY cap_key'):
        print('   %-16s total=%s parts=%s' % (r['cap_key'], r['total'], r['n']))
    engine.close_all()
except Exception as exc:  # noqa: BLE001
    print('db check FAILED: %r' % exc)
    raise SystemExit(1) from exc

raise SystemExit(1 if missing else 0)
