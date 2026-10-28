# -*- coding: utf-8 -*-
"""financetool 全库标识符改名 + 库内登记文本更新。
用法：python rename_db.py [--commit]
干跑：打印 DDL、写 rename_db.sql / mapping.json / text_changes.txt
"""
import json
import re
import sys

import psycopg

DSN = 'postgresql://postgres:Tata%23Pg2026%21Fin@127.0.0.1:5432/financetool'
BASE = '<旧仓库已删>/var/schema_analysis'
COMMIT = '--commit' in sys.argv

SCHEMA_MAP = {'md': 'market_data', 'fct': 'analytics', 'ref': 'reference_data', 'ops': 'operations'}
SCHEMAS_ALL = list(SCHEMA_MAP) + ['config']

TABLE_MAP = {
    'md.lhb_detail': 'dragon_tiger_detail',
    'md.lhb_inst': 'dragon_tiger_institution',
    'fct.vol_double': 'volume_double',
    'md.v_daily_source': 'view_daily_source',
    'md.v_etf_flow': 'view_etf_flow',
    'md.v_margin_trend': 'view_margin_trend',
    'fct.v_score_detail': 'view_score_detail',
    'md.stg_indval': 'staging_indicator_value',
    'md.stg_ignition': 'staging_ignition',
    'md.stg_c1_stock': 'staging_c1_stock',
    'md.stg_c16_minute': 'staging_c16_minute',
}
FUNC_QUAL = {
    'ref.board_limit_ratio': 'reference_data.board_limit_ratio',
    'md.trg_daily_sanity': 'market_data.trg_daily_sanity',
    'ops.trg_audit_stmt': 'operations.trg_audit_stmt',
    'ops.trg_config_history': 'operations.trg_config_history',
}

COLUMN_MAP = {
    'ts_code': 'security_code',
    'ind_key': 'indicator_key',
    'cap_key': 'capability_key',
    'obj_key': 'object_key',
    'obj_code': 'object_code',
    'obj_name': 'object_name',
    'ds_key': 'datasource_key',
    'part_key': 'component_key',
    'part_label': 'component_label',
    'part_value': 'component_value',
    'script_ver': 'script_version',
    'src_batch_id': 'source_batch_id',
    'src': 'source',
    'src_db': 'source_db',
    'src_table': 'source_table',
    'pct_chg': 'pct_change',
    'turnover_rate_pct': 'turnover_rate_percent',
    'roe_pct': 'roe_percent',
    'vol_shares': 'volume_shares',
    'vol_units': 'volume_units',
    'adj_factor': 'adjustment_factor',
    'total_mv_yuan': 'total_market_value_yuan',
    'float_mv_yuan': 'float_market_value_yuan',
    'win': 'window_days',
    'n_points': 'point_count',
    'n_terms': 'term_count',
    'n_parts': 'part_count',
    'pos': 'position_score',
    'xy_max': 'x_plus_y_max',
    'xy_max_date': 'x_plus_y_max_date',
    'quad_first': 'first_quadrant',
    'spark': 'sparkline',
    'up_streak': 'up_streak_days',
    'r': 'corr_coef',
    'lhb_amount': 'dragon_tiger_amount',
    'buy_inst_count': 'buy_institution_count',
    'sell_inst_count': 'sell_institution_count',
    'inst_buy': 'institution_buy',
    'inst_sell': 'institution_sell',
    'inst_net': 'institution_net',
    'xl_net': 'super_large_net',
    'xl_pct': 'super_large_net_percent',
    'lg_net': 'large_net',
    'lg_pct': 'large_net_percent',
    'md_net': 'medium_net',
    'md_pct': 'medium_net_percent',
    'sm_net': 'small_net',
    'sm_pct': 'small_net_percent',
    'main_pct': 'main_net_percent',
    'sh_close': 'shanghai_close',
    'sh_pct': 'shanghai_percent',
    'sz_close': 'shenzhen_close',
    'sz_pct': 'shenzhen_percent',
    'rz_balance': 'financing_balance',
    'rz_buy': 'financing_buy_amount',
    'rz_repay': 'financing_repay_amount',
    'rq_volume': 'securities_lending_volume',
    'rq_sell': 'securities_lending_sell_volume',
    'rq_repay': 'securities_lending_repay_volume',
    'rq_balance': 'securities_lending_balance',
    'rzrq_balance': 'margin_total_balance',
    'rz_d1': 'financing_balance_1d_ago',
    'rz_d5': 'financing_balance_5d_ago',
    'rz_d10': 'financing_balance_10d_ago',
    'rz_d20': 'financing_balance_20d_ago',
    'd5_date': 'ago5_date',
    'chg1_pct': 'change_1d_percent',
    'chg5_pct': 'change_5d_percent',
    'chg10_pct': 'change_10d_percent',
    'chg20_pct': 'change_20d_percent',
    'rz_of_float_pct': 'financing_to_float_mv_percent',
    'delta_units': 'delta_share_units',
    'cal_date': 'calendar_date',
    'prev_open': 'prev_open_date',
    'next_open': 'next_open_date',
    'member_scored': 'member_scored_count',
    'seq': 'display_order',
    'at': 'occurred_at',
    'auth_ref': 'auth_env_name',
    'pct_rank': 'percent_rank',
}
DB_TEXT_BARE_SKIP = {'at', 'r', 'seq'}   # 纯文本里误伤风险高的裸 token
CODE_RISKY = {'win', 'pos', 'src', 'r', 'seq', 'spark', 'at'}

CHILD_RE = re.compile(r'_(20\d{2}|20\d{4})$')

def q(s):
    return '"' + s.replace('"', '""') + '"'

def new_table_name(sch, name):
    fq = sch + '.' + name
    if fq in TABLE_MAP:
        return TABLE_MAP[fq]
    m = re.match(r'^md\.stg_(.+)$', fq)
    if m:
        return 'staging_' + m.group(1)
    return name

def tok_replace(objname):
    out = objname
    for k, v in [('lhb_detail', 'dragon_tiger_detail'), ('lhb_inst', 'dragon_tiger_institution'),
                 ('vol_double', 'volume_double'), ('v_daily_source', 'view_daily_source'),
                 ('v_etf_flow', 'view_etf_flow'), ('v_margin_trend', 'view_margin_trend'),
                 ('v_score_detail', 'view_score_detail')]:
        out = re.sub(r'\b' + k + r'\b', v, out)
    out = re.sub(r'\bstg_([a-z0-9_]+)', lambda m: 'staging_' + {'indval': 'indicator_value'}.get(m.group(1), m.group(1)), out)
    name_skip = {'at', 'r', 'seq', 'win', 'pos', 'spark', 'src'}
    for old, new in sorted(COLUMN_MAP.items(), key=lambda kv: -len(kv[0])):
        if old in name_skip:
            continue
        out = re.sub(r'(?<![a-z0-9])' + re.escape(old) + r'(?![a-z0-9])', new, out)
    return out

def main():
    conn = psycopg.connect(DSN)
    cur = conn.cursor()
    stmts = []

    cur.execute("""
        select n.nspname, c.relname, c.relkind
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname = any(%s) and c.relkind in ('r','p','v','m') and not c.relispartition
        order by n.nspname, c.relname""", (SCHEMAS_ALL,))
    rels = cur.fetchall()
    kind_of = {(s, n): k for s, n, k in rels}
    cur.execute("""
        select n.nspname, c.relname, a.attname
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        join pg_attribute a on a.attrelid=c.oid and a.attnum>0 and not a.attisdropped
        where n.nspname = any(%s) and c.relkind in ('r','p','v','m') and not c.relispartition
        order by 1,2,3""", (SCHEMAS_ALL,))
    cols = cur.fetchall()

    # 1) 表/视图改名（跳过分区子表）
    for sch, name, kind in rels:
        if CHILD_RE.search(name):
            continue
        nn = new_table_name(sch, name)
        if nn != name:
            tgt = 'view' if kind in ('v', 'm') else 'table'
            stmts.append(f'ALTER {tgt} {q(sch)}.{q(name)} RENAME TO {q(nn)}')

    # 2) 列改名（父表/普通表；分区子表与视图列级联，不手工处理）
    seen = set()
    for sch, name, col in cols:
        if kind_of.get((sch, name)) in ('v', 'm'):
            continue
        nc = COLUMN_MAP.get(col)
        if nc and (sch, name, col) not in seen:
            stmts.append(f'ALTER TABLE {q(sch)}.{q(new_table_name(sch, name))} RENAME COLUMN {q(col)} TO {q(nc)}')
            seen.add((sch, name, col))

    # 3) 索引 / 序列 / 约束改名
    cur.execute("""select c.relname, n.nspname, c.relkind from pg_class c
        join pg_namespace n on n.oid=c.relnamespace
        where n.nspname = any(%s) and c.relkind in ('i','S','P')""", (SCHEMAS_ALL,))
    for name, sch, kind in cur.fetchall():
        nn = tok_replace(name)
        if nn != name:
            stmts.append(f"ALTER {'index' if kind == 'i' else 'sequence'} {q(sch)}.{q(name)} RENAME TO {q(nn)}")
    cur.execute("""select con.conname, n.nspname, c.relname from pg_constraint con
        join pg_class c on c.oid=con.conrelid join pg_namespace n on n.oid=c.relnamespace
        where n.nspname = any(%s) and con.conindid = 0""", (SCHEMAS_ALL,))
    for conname, sch, tbl in cur.fetchall():
        nn = tok_replace(conname)
        if nn != conname:
            stmts.append(f'ALTER TABLE {q(sch)}.{q(new_table_name(sch, tbl))} RENAME CONSTRAINT {q(conname)} TO {q(nn)}')

    # 4) schema 改名
    for old, new in SCHEMA_MAP.items():
        stmts.append(f'ALTER SCHEMA {q(old)} RENAME TO {q(new)}')

    # ---------- 生成共用替换表（DDL 后函数体与文本更新用） ----------
    qual_tables = {f'{sch}.{name}': f"{SCHEMA_MAP.get(sch, sch)}.{new_table_name(sch, name)}"
                   for sch, name, _ in rels}
    # 暂存表是临时对象（跑时建、完事删），不在当前目录也要进映射
    for fq, nn in TABLE_MAP.items():
        sch = fq.split('.')[0]
        qual_tables.setdefault(fq, f'{SCHEMA_MAP.get(sch, sch)}.{nn}')
    qual_cols = {}
    for sch, name, col in cols:
        nc = COLUMN_MAP.get(col)
        if nc:
            base = CHILD_RE.sub('', name)
            qual_cols[f'{sch}.{name}.{col}'] = f"{SCHEMA_MAP.get(sch, sch)}.{new_table_name(sch, base) if CHILD_RE.search(name) else new_table_name(sch, name)}.{nc}"
    json.dump({'schema': SCHEMA_MAP, 'tables': qual_tables, 'columns': qual_cols,
               'bare_columns': COLUMN_MAP, 'funcs': FUNC_QUAL, 'risky': sorted(CODE_RISKY)},
              open(f'{BASE}/mapping.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    def subst_text(s):
        out = s
        for pat, rep in sorted(list(qual_cols.items()) + list(qual_tables.items()) + list(FUNC_QUAL.items()),
                               key=lambda kv: -len(kv[0])):
            out = re.sub(r'\b' + re.escape(pat) + r'\b', rep, out)
        for old, new in sorted(COLUMN_MAP.items(), key=lambda kv: -len(kv[0])):
            if old in DB_TEXT_BARE_SKIP:
                continue
            out = re.sub(r'(?<![a-z0-9_])' + re.escape(old) + r'(?![a-z_0-9])', new, out)
        return out

    cur.execute("select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname = any(%s)", (list(SCHEMA_MAP),))
    func_oids = [r[0] for r in cur.fetchall()]

    if not COMMIT:
        print('\n'.join(stmts))
        print(f'-- dry run: {len(stmts)} ddl')
        conn.rollback()
        return

    for s in stmts:
        cur.execute(s)

    # ---------- 函数体 ----------
    for oid in func_oids:
        row = cur.execute("""select n.nspname, p.proname, p.proargtypes::regtype[]::text[],
                             p.prorettype::regtype::text, p.prosrc from pg_proc p
                             join pg_namespace n on n.oid=p.pronamespace where p.oid=%s""", (oid,)).fetchone()
        sch, name, argt, ret, src = row
        newsrc = subst_text(src)
        if newsrc != src:
            args = ', '.join(argt or [])
            cur.execute(f'CREATE OR REPLACE FUNCTION {q(sch)}.{q(name)}({args}) RETURNS {ret} '
                        f"AS $body$ {newsrc} $body$ LANGUAGE plpgsql")
            print(f'func body updated: {sch}.{name}')

    # ---------- config/ops 文本列更新 ----------
    cur.execute("""
        select n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        join pg_attribute a on a.attrelid=c.oid and a.attnum>0 and not a.attisdropped
        where n.nspname in ('config','ops') and c.relkind='r'
          and a.atttypid in ('text'::regtype,'text[]'::regtype,'jsonb'::regtype)
        order by 1,2,3""")
    text_cols = cur.fetchall()
    changes, ncell = [], 0
    for sch, tbl, col, typ in text_cols:
        newtbl = new_table_name(sch, tbl)
        pkrows = cur.execute("""select a.attname from pg_index i
            join pg_attribute a on a.attrelid=i.indrelid and a.attnum=any(i.indkey)
            where i.indrelid=(%s||'.'||%s)::regclass and i.indisprimary""", (sch, newtbl)).fetchall()
        pks = [r[0] for r in pkrows]
        if not pks:
            pks = [col]
        rows = cur.execute(f'SELECT {", ".join(q(p) for p in pks)}, {q(col)} FROM {q(sch)}.{q(newtbl)}').fetchall()
        for r in rows:
            val = r[-1]
            if val is None or (isinstance(val, str) and 'a' not in val and 'md' not in val and '.' not in val and not (set(val) & set(COLUMN_MAP))):
                pass
            if isinstance(val, str):
                nv = subst_text(val)
            elif isinstance(val, list):
                nv = [subst_text(v) if isinstance(v, str) else v for v in val]
            elif isinstance(val, dict):
                nv = {k: subst_text(v) if isinstance(v, str) else v for k, v in val.items()}
            else:
                nv = val
            if nv != val:
                w = ' AND '.join(f'{q(p)} = %s' for p in pks)
                cast = '::jsonb' if typ == 'jsonb' else ('::text[]' if typ.endswith('[]') else '')
                cur.execute(f'UPDATE {q(sch)}.{q(newtbl)} SET {q(col)}=%s{cast} WHERE {w}',
                            [json.dumps(nv) if typ == 'jsonb' else nv] + list(r[:-1]))
                ncell += 1
                changes.append((f'{sch}.{newtbl}.{col}', str(r[:-1])[:60], str(val)[:120], str(nv)[:120]))
    conn.commit()
    with open(f'{BASE}/text_changes.txt', 'w', encoding='utf-8') as f:
        for c in changes:
            f.write(' | '.join(c) + '\n')
    print(f'DONE ddl={len(stmts)} text_cells={ncell}')

if __name__ == '__main__':
    main()
