# -*- coding: utf-8 -*-
"""按 mapping.json 同步改名 app/ scripts/ tests/ docs/ 与各 .md/.sql/.py/.toml。
用法：python rename_code.py [--apply]
"""
import json
import re
import sys
from pathlib import Path

APPLY = '--apply' in sys.argv
MAP = json.load(open('D:/FinanceTool/var/schema_analysis/mapping.json', encoding='utf-8'))
SCHEMA_MAP = MAP['schema']
TABLES = MAP['tables']          # 'md.stock_daily' -> 'market_data.stock_daily'
QCOLS = MAP['columns']          # 'md.stock_daily.ts_code' -> ...
BARE = MAP['bare_columns']
RISKY = set(MAP['risky'])       # win pos src r seq spark at —— 本脚本不动，后置人工
RISKY |= {'migration_baseline_src_db_src_table_key'}  # noop guard
EXTRA_FUNCS = {
    'ref.board_limit_ratio': 'reference_data.board_limit_ratio',
    'md.trg_daily_sanity': 'market_data.trg_daily_sanity',
    'ops.trg_audit_stmt': 'operations.trg_audit_stmt',
    'ops.trg_config_history': 'operations.trg_config_history',
    'config.v_effective_binding': 'config.view_effective_binding',
    'md.v_effective_binding': 'config.view_effective_binding',
}
TABLES = {**TABLES, **EXTRA_FUNCS}

# 1) 全限定列：最长优先
qual_col_re = re.compile(r'\b(?:' + '|'.join(map(re.escape, sorted(QCOLS, key=len, reverse=True))) + r')\b')
# 2) 限定表（含分区/默认子表与后缀）：schema.name 通用捕获
qual_tab_re = re.compile(r'\b(md|fct|ref|ops|config)\.([a-z][a-z0-9_]*)\b')
# 3) 引号形式 "md"."table"
quoted_tab_re = re.compile(r'"(md|fct|ref|ops|config)"\."([a-z][a-z0-9_]*)"')
# 4) 裸 schema 引用（SQL 文件）
bare_schema_ctx_re = re.compile(r'\b(SCHEMA|schema)\s+(IF NOT EXISTS\s+)?(md|fct|ref|ops)\b')
in_schema_list_re = re.compile(r'IN SCHEMA ([a-z_,\s]+)')
# 5) 引号内裸 schema 名（python 字符串）
quoted_schema_re = re.compile(r'([\'"])(md|fct|ref|ops)\1')
# 6) 裸列名：py 保守版（排除全部 risky）；sql/md 积极版（只排除 'r'）
_safe = lambda keys: re.compile(r'(?<![a-z0-9_])(' + '|'.join(sorted(keys, key=len, reverse=True)) + r')(?![a-z0-9_])')
bare_col_re = _safe(k for k in BARE if k not in RISKY)
bare_col_re_wide = _safe(k for k in BARE if k != 'r')

def map_table(sch, name):
    m = re.match(r'^(.*?)_(20\d{2}|20\d{4}|default)$', name)
    base, suffix = (m.group(1), '_' + m.group(2)) if m else (name, '')
    fq = f'{sch}.{base}'
    if fq in TABLES:
        return TABLES[fq] + suffix
    if base != name and sch in SCHEMA_MAP and f'{sch}.{name}' not in TABLES:
        return f'{SCHEMA_MAP[sch]}.{name}'
    if sch in SCHEMA_MAP:
        return f'{SCHEMA_MAP[sch]}.{name}'
    return f'{sch}.{name}'

def sub_qualified_table(m):
    return map_table(m.group(1), m.group(2))

def sub_quoted(m):
    return '"' + map_table(m.group(1), m.group(2)).replace('.', '"."') + '"'

def sub_bare_ctx(m):
    return f'{m.group(1)} {m.group(2) or ""}{SCHEMA_MAP.get(m.group(3), m.group(3))}'

def sub_list(m):
    items = [x.strip() for x in m.group(1).split(',') if x.strip()]
    return 'IN SCHEMA ' + ', '.join(SCHEMA_MAP.get(x, x) for x in items)

def sub_bare_col(m):
    return BARE[m.group(1)]

def process(text, is_sql, is_md=False):
    n0 = 0
    def cnt(f):
        def _s(m):
            nonlocal n0
            r = f(m)
            if r != m.group(0):
                n0 += 1
            return r
        return _s
    text = qual_col_re.sub(cnt(lambda m: QCOLS[m.group(0)]), text)
    text = quoted_tab_re.sub(cnt(sub_quoted), text)
    text = qual_tab_re.sub(cnt(sub_qualified_table), text)
    if is_sql:
        text = bare_schema_ctx_re.sub(cnt(sub_bare_ctx), text)
        text = in_schema_list_re.sub(cnt(sub_list), text)
    text = quoted_schema_re.sub(cnt(lambda m: m.group(1) + SCHEMA_MAP.get(m.group(2), m.group(2)) + m.group(1)), text)
    (bare_col_re_wide if (is_sql or is_md) else bare_col_re).sub
    text = (bare_col_re_wide if (is_sql or is_md) else bare_col_re).sub(cnt(sub_bare_col), text)
    return text, n0

def main():
    root = Path('D:/FinanceTool')
    targets = []
    for d in ('app', 'scripts', 'tests', 'docs'):
        targets += [p for p in (root / d).rglob('*') if p.suffix in ('.py', '.sql', '.md', '.toml', '.json') and '__pycache__' not in str(p)]
    targets += [root / 'README.md', root / '程序说明书.md', root / 'pyproject.toml']
    report = []
    for p in targets:
        if not p or not p.exists():
            continue
        old = p.read_text(encoding='utf-8')
        new, n = process(old, p.suffix == '.sql', p.suffix == '.md')
        if n:
            report.append((str(p.relative_to(root)), n))
            if APPLY:
                p.write_text(new, encoding='utf-8', newline='')
    for f, n in sorted(report, key=lambda x: -x[1]):
        print(f'{n:5d}  {f}')
    print(f'files={len(report)} total_hits={sum(n for _, n in report)} apply={APPLY}')

if __name__ == '__main__':
    main()
