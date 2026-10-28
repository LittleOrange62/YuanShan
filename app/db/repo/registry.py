# -*- coding: utf-8 -*-
"""
十三张 config 注册表的统一读写 —— "积木城堡"的后端。

四个通用能力在这里实现基础：
  read/upsert    页面表单的增删改
  references_to  反查依赖，避免删出空引用
  validate       保存前校验（引用完整性、依赖成环、权重和）
历史与回滚由库层触发器 + operations.config_history 承担（见 repo/operations.py）。
"""
from __future__ import annotations

import json
from typing import Any

from app.db import engine

# 表名 → 主键列。页面通用编辑器据此定位行。
TABLES: dict[str, str] = {
    'data_source': 'source_key',
    'field_spec': 'id',
    'dataset': 'dataset_key',
    'quality_rule': 'rule_key',
    'indicator': 'indicator_key',
    'capability': 'capability_key',
    'script': 'script_id',
    'binding': 'binding_id',
    'job': 'job_key',
    'screen_rule': 'rule_key',
    'screen_preset': 'preset_id',
    'chart': 'chart_key',
    'notify_channel': 'channel_key',
    'notify_rule': 'rule_key',
    'page_layout': 'layout_id',
    'menu': 'menu_key',
    'setting': 'key',
    # 界面说明。放进注册表是为了三件事：导出备份带上它、/blocks 能看原始行、
    # 删行时走同一套引用检查。正文的编辑走「说明维护」那个 tab（字段分开填）。
    'help_note': 'note_key',
}

# 受保护的表：改动影响数据可信度或有 SQL 注入面，需 admin 角色 + 二次确认
PROTECTED = {'field_spec', 'quality_rule', 'screen_rule', 'data_source'}


def _check_table(table: str) -> str:
    if table not in TABLES:
        raise ValueError(f'未知注册表: {table}')
    return table


def list_rows(table: str, *, where: str | None = None, params: Any = None,
              role: str = 'reader') -> list[dict]:
    _check_table(table)
    sql = f'SELECT * FROM config.{table}'
    if where:
        sql += f' WHERE {where}'
    sql += f' ORDER BY {TABLES[table]}'
    return engine.query(sql, params, role=role)


def get_row(table: str, key: Any, *, role: str = 'reader') -> dict | None:
    _check_table(table)
    return engine.query_one(
        f'SELECT * FROM config.{table} WHERE {TABLES[table]} = %s', (key,), role=role)


def upsert_row(table: str, values: dict, *, role: str = 'app') -> Any:
    """通用 upsert。JSON 类型的值由调用方给 dict，这里负责序列化。"""
    _check_table(table)
    pk = TABLES[table]
    cols = [c for c in values if c != 'updated_at']
    payload = []
    for c in cols:
        v = values[c]
        payload.append(json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v)

    collist = ', '.join(f'"{c}"' for c in cols)
    holders = ', '.join(['%s'] * len(cols))
    updates = ', '.join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != pk)
    has_updated_at = bool(engine.scalar(
        'SELECT 1 FROM information_schema.columns '
        "WHERE table_schema='config' AND table_name=%s AND column_name='updated_at'",
        (table,)))
    if has_updated_at:
        updates += ', updated_at = now()'

    sql = (f'INSERT INTO config.{table} ({collist}) VALUES ({holders}) '
           f'ON CONFLICT ("{pk}") DO UPDATE SET {updates} RETURNING "{pk}"')
    return engine.scalar(sql, payload, role=role)


def delete_row(table: str, key: Any, *, role: str = 'app') -> int:
    _check_table(table)
    refs = references_to(table, key)
    if refs:
        raise ValueError(f'仍被引用，不能删除：{refs}')
    return engine.execute(
        f'DELETE FROM config.{table} WHERE {TABLES[table]} = %s', (key,), role=role)


def set_enabled(table: str, key: Any, enabled: bool, *, role: str = 'app') -> int:
    _check_table(table)
    return engine.execute(
        f'UPDATE config.{table} SET enabled = %s WHERE {TABLES[table]} = %s',
        (enabled, key), role=role)


# ============================================================ 反查依赖

def references_to(table: str, key: Any) -> list[str]:
    """删除前反查引用，避免留下一地空引用。"""
    out: list[str] = []
    if table == 'indicator':
        rows = engine.query(
            "SELECT indicator_key FROM config.indicator WHERE %s = ANY(depends_on)", (key,))
        out += [f'指标 {r["indicator_key"]} 依赖它' for r in rows]
        rows = engine.query(
            "SELECT chart_key FROM config.chart WHERE encoding::text LIKE %s", (f'%{key}%',))
        out += [f'图表 {r["chart_key"]} 使用它' for r in rows]
    elif table == 'capability':
        rows = engine.query('SELECT job_key FROM config.job WHERE %s = ANY(caps)', (key,))
        out += [f'作业 {r["job_key"]} 产出它' for r in rows]
        rows = engine.query(
            'SELECT capability_key FROM config.capability WHERE %s = ANY(depends_on)', (key,))
        out += [f'功能点 {r["capability_key"]} 依赖它' for r in rows]
        rows = engine.query('SELECT count(*) AS n FROM config.binding WHERE capability_key = %s', (key,))
        if rows and rows[0]['n']:
            out.append(f'{rows[0]["n"]} 条绑定指向它')
    elif table == 'script':
        rows = engine.query(
            'SELECT capability_key FROM config.binding WHERE script_id = %s', (key,))
        out += [f'功能点 {r["capability_key"]} 绑定它' for r in rows]
    elif table == 'data_source':
        rows = engine.query(
            'SELECT count(*) AS n FROM config.field_spec WHERE source_key = %s', (key,))
        if rows and rows[0]['n']:
            out.append(f'{rows[0]["n"]} 个字段口径引用它')
        rows = engine.query(
            'SELECT dataset_key FROM config.dataset WHERE %s = ANY(source_keys)', (key,))
        out += [f'数据集 {r["dataset_key"]} 使用它' for r in rows]
    elif table == 'chart':
        rows = engine.query('SELECT page_key FROM config.page_layout WHERE card_ref = %s', (key,))
        out += [f'页面 {r["page_key"]} 布局引用它' for r in rows]
    elif table == 'notify_channel':
        rows = engine.query(
            'SELECT rule_key FROM config.notify_rule WHERE channel_key = %s', (key,))
        out += [f'推送规则 {r["rule_key"]} 使用它' for r in rows]
    return out


# ============================================================ 校验

def validate() -> list[dict]:
    """
    全量配置体检。/blocks 页面"校验"按钮调它，保存前也调。
    返回问题列表，空列表表示全部通过。
    """
    issues: list[dict] = []

    def add(level: str, where: str, msg: str) -> None:
        issues.append({'level': level, 'where': where, 'message': msg})

    # 权重归一化
    for r in engine.query(
            'SELECT capability_key, sum(eff_weight) AS s FROM config.view_effective_binding '
            'GROUP BY capability_key HAVING abs(sum(eff_weight) - 1) > 0.0001'):
        add('error', f'binding/{r["capability_key"]}', f'归一化权重和为 {r["s"]}，应为 1')

    # 功能点无可用脚本
    for r in engine.query(
            """
            SELECT c.capability_key FROM config.capability c
             WHERE c.enabled AND NOT EXISTS (
                SELECT 1 FROM config.view_effective_binding b WHERE b.capability_key = c.capability_key)
            """):
        add('error', f'capability/{r["capability_key"]}', '已启用但没有任何生效的脚本绑定')

    # 依赖的功能点不存在
    for r in engine.query(
            """
            SELECT c.capability_key, d AS missing FROM config.capability c, unnest(c.depends_on) d
             WHERE NOT EXISTS (SELECT 1 FROM config.capability x WHERE x.capability_key = d)
            """):
        add('error', f'capability/{r["capability_key"]}', f'依赖的功能点 {r["missing"]} 不存在')

    # 依赖成环
    cycles = engine.query(
        """
        WITH RECURSIVE e(a, b) AS (
            SELECT capability_key, d FROM config.capability, unnest(depends_on) d),
        walk(root, node, path, cyc) AS (
            SELECT a, b, ARRAY[a, b], a = b FROM e
            UNION ALL
            SELECT w.root, e.b, w.path || e.b, e.b = ANY(w.path)
              FROM walk w JOIN e ON e.a = w.node WHERE NOT w.cyc
              AND array_length(w.path, 1) < 20)
        SELECT DISTINCT root, path FROM walk WHERE cyc
        """)
    for r in cycles:
        add('error', f'capability/{r["root"]}', f'依赖成环: {" -> ".join(r["path"])}')

    # 作业引用了不存在的功能点 / 前置作业
    for r in engine.query(
            """
            SELECT j.job_key, c AS missing FROM config.job j, unnest(j.caps) c
             WHERE NOT EXISTS (SELECT 1 FROM config.capability x WHERE x.capability_key = c)
            """):
        add('error', f'job/{r["job_key"]}', f'引用的功能点 {r["missing"]} 不存在')
    for r in engine.query(
            """
            SELECT job_key, after_job FROM config.job
             WHERE trigger_kind = 'chain' AND (after_job IS NULL OR NOT EXISTS
                   (SELECT 1 FROM config.job x WHERE x.job_key = config.job.after_job))
            """):
        add('error', f'job/{r["job_key"]}', f'chain 触发但前置作业 {r["after_job"]!r} 无效')
    for r in engine.query(
            "SELECT job_key FROM config.job WHERE trigger_kind = 'cron' "
            "AND (cron_expr IS NULL OR cron_expr = '')"):
        add('error', f'job/{r["job_key"]}', 'cron 触发但未填 cron_expr')
    for r in engine.query(
            "SELECT job_key FROM config.job WHERE trigger_kind = 'on_ready' "
            "AND (ready_on IS NULL OR cardinality(ready_on) = 0)"):
        add('error', f'job/{r["job_key"]}', 'on_ready 触发但未指定 ready_on')

    # 字段口径与实际列双向比对
    for r in engine.query(
            """
            SELECT f.table_name, f.column_name FROM config.field_spec f
             WHERE NOT EXISTS (
                SELECT 1 FROM information_schema.columns c
                 WHERE c.table_schema = split_part(f.table_name, '.', 1)
                   AND c.table_name  = split_part(f.table_name, '.', 2)
                   AND c.column_name = f.column_name)
            """):
        add('error', f'field_spec/{r["table_name"]}.{r["column_name"]}', '登记了不存在的列')
    for r in engine.query(
            """
            SELECT c.table_schema || '.' || c.table_name AS t, c.column_name
              FROM information_schema.columns c
             WHERE c.table_schema IN ('market_data', 'reference_data', 'analytics')
               AND c.table_name NOT LIKE '%\\_2%' AND c.table_name NOT LIKE '%default'
               AND c.column_name NOT IN ('updated_at', 'created_at')
               AND NOT EXISTS (
                  SELECT 1 FROM config.field_spec f
                   WHERE f.table_name = c.table_schema || '.' || c.table_name
                     AND f.column_name = c.column_name)
            """):
        add('warning', f'field_spec/{r["t"]}.{r["column_name"]}', '实际列未登记口径')

    # 图表引用的指标/查询：等 web 层的命名查询表落地后再补，这里不留空跑的占位查询

    for r in engine.query(
            """
            SELECT page_key, slot, card_ref FROM config.page_layout
             WHERE card_kind = 'chart' AND (card_ref IS NULL OR NOT EXISTS
                   (SELECT 1 FROM config.chart c WHERE c.chart_key = config.page_layout.card_ref))
            """):
        add('error', f'page_layout/{r["page_key"]}#{r["slot"]}',
            f'卡片引用的图表 {r["card_ref"]!r} 不存在')

    # 推送渠道明文口令
    for r in engine.query(
            "SELECT channel_key FROM config.notify_channel "
            "WHERE endpoint ~* '(token|secret|key)=' "):
        add('warning', f'notify_channel/{r["channel_key"]}',
            'endpoint 里似乎含明文密钥，应改用 secret_ref')

    return issues


# ============================================================ 便捷读取

def effective_bindings(capability_key: str, *, role: str = 'reader') -> list[dict]:
    """生效绑定（含归一化后的权重）。策略执行与评分拆解都用它。"""
    return engine.query(
        'SELECT * FROM config.view_effective_binding WHERE capability_key = %s ORDER BY display_order',
        (capability_key,), role=role)


def capability(capability_key: str, *, role: str = 'reader') -> dict | None:
    return get_row('capability', capability_key, role=role)


def capabilities(*, enabled_only: bool = True, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT c.*, (SELECT count(*) FROM config.view_effective_binding b
                      WHERE b.capability_key = c.capability_key) AS script_count,
               (SELECT max(calculated_at) FROM analytics.score s
                 WHERE s.capability_key = c.capability_key) AS last_calculated_at
          FROM config.capability c
         WHERE (NOT %s OR c.enabled)
         ORDER BY c.category, c.capability_key
        """,
        (enabled_only,), role=role)


def jobs(*, enabled_only: bool = True, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT * FROM config.job WHERE (NOT %s OR enabled) ORDER BY job_key',
        (enabled_only,), role=role)


def job(job_key: str, *, role: str = 'reader') -> dict | None:
    return get_row('job', job_key, role=role)


def indicators(*, enabled_only: bool = True, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT * FROM config.indicator WHERE (NOT %s OR enabled) ORDER BY indicator_key',
        (enabled_only,), role=role)


def datasets(*, role: str = 'reader') -> list[dict]:
    return engine.query('SELECT * FROM config.dataset WHERE enabled ORDER BY dataset_key',
                        role=role)


def dataset(dataset_key: str, *, role: str = 'reader') -> dict | None:
    return get_row('dataset', dataset_key, role=role)


def data_sources(*, enabled_only: bool = True, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT * FROM config.data_source WHERE (NOT %s OR enabled) ORDER BY priority',
        (enabled_only,), role=role)


def quality_rules(scope: str | None = None, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT * FROM config.quality_rule
         WHERE enabled AND (%s::text IS NULL OR scope = %s)
         ORDER BY scope, severity, rule_key
        """,
        (scope, scope), role=role)


def charts(*, role: str = 'reader') -> list[dict]:
    return engine.query('SELECT * FROM config.chart WHERE enabled ORDER BY chart_key', role=role)


def chart(chart_key: str, *, role: str = 'reader') -> dict | None:
    return get_row('chart', chart_key, role=role)


def layout(page_key: str, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT * FROM config.page_layout WHERE page_key = %s AND visible ORDER BY slot',
        (page_key,), role=role)


def save_layout(page_key: str, cards: list[dict], *, role: str = 'app') -> int:
    """整页保存：拖拽排序后一次性写入。"""
    engine.execute('DELETE FROM config.page_layout WHERE page_key = %s', (page_key,), role=role)
    rows = [(page_key, i + 1, c.get('width', 6), c.get('height', 320),
             c['card_kind'], c.get('card_ref'), c.get('title'),
             json.dumps(c.get('options', {}), ensure_ascii=False), c.get('visible', True))
            for i, c in enumerate(cards)]
    return engine.executemany(
        'INSERT INTO config.page_layout (page_key, slot, width, height, card_kind, '
        'card_ref, title, options, visible) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        rows, role=role)


def menu(*, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT * FROM config.menu WHERE visible ORDER BY display_order', role=role)


def setting(key: str, default: Any = None, *, role: str = 'reader') -> Any:
    v = engine.scalar('SELECT value FROM config.setting WHERE key = %s', (key,), role=role)
    return default if v is None else v


def set_setting(key: str, value: Any, *, label: str | None = None,
                category: str | None = None, role: str = 'app') -> None:
    engine.execute(
        """
        INSERT INTO config.setting (key, value, label, category)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
            label = coalesce(EXCLUDED.label, config.setting.label),
            category = coalesce(EXCLUDED.category, config.setting.category),
            updated_at = now()
        """,
        (key, json.dumps(value, ensure_ascii=False), label, category), role=role)


def screen_rules(*, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT * FROM config.screen_rule WHERE enabled ORDER BY rule_key', role=role)


def field_dict(keyword: str | None = None, *, role: str = 'reader') -> list[dict]:
    like = f'%{keyword}%' if keyword else None
    return engine.query(
        """
        SELECT * FROM operations.view_field_dict
         WHERE (%s::text IS NULL OR column_name LIKE %s OR label_cn LIKE %s
                OR table_name LIKE %s)
        """,
        (like, like, like, like), role=role)


# ============================================================ 脚本登记

def register_script(script_key: str, name: str, path: str, provides: list[str],
                    *, version: str | None = None, checksum: str | None = None,
                    param_schema: dict | None = None, role: str = 'app') -> int:
    return engine.scalar(
        """
        INSERT INTO config.script (script_key, name, path, version, provides,
                                checksum, param_schema, registered_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (script_key) DO UPDATE SET
            name = EXCLUDED.name, path = EXCLUDED.path, version = EXCLUDED.version,
            provides = EXCLUDED.provides, checksum = EXCLUDED.checksum,
            param_schema = EXCLUDED.param_schema, registered_at = now(),
            last_error = NULL
        RETURNING script_id
        """,
        (script_key, name, path, provides, version, checksum,
         json.dumps(param_schema or {}, ensure_ascii=False)), role=role)


def script_by_key(script_key: str, *, role: str = 'reader') -> dict | None:
    return engine.query_one(
        'SELECT * FROM config.script WHERE script_key = %s', (script_key,), role=role)


def mark_script_ok(script_id: int, *, role: str = 'app') -> None:
    engine.execute(
        'UPDATE config.script SET last_ok_at = now(), last_error = NULL WHERE script_id = %s',
        (script_id,), role=role)


def mark_script_error(script_id: int, error: str, *, role: str = 'app') -> None:
    """加载失败要显式记录，不静默回退内置实现（旧系统 :457 会静默 fallback）。"""
    engine.execute(
        'UPDATE config.script SET last_error = %s WHERE script_id = %s',
        (error[:2000], script_id), role=role)


def bind(capability_key: str, script_id: int, *, weight: float = 1.0, display_order: int = 0,
         params: dict | None = None, enabled: bool = True, role: str = 'app') -> int:
    return engine.scalar(
        """
        INSERT INTO config.binding (capability_key, script_id, weight, display_order, params, enabled)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (capability_key, script_id) DO UPDATE SET
            weight = EXCLUDED.weight, display_order = EXCLUDED.display_order,
            params = EXCLUDED.params, enabled = EXCLUDED.enabled, updated_at = now()
        RETURNING binding_id
        """,
        (capability_key, script_id, weight, display_order,
         json.dumps(params or {}, ensure_ascii=False), enabled), role=role)


def export_config() -> dict:
    """整套配置导出，用于备份 / 换机复现 / 分享调好的因子配置。"""
    out: dict[str, Any] = {}
    for t in TABLES:
        out[t] = list_rows(t)
    return out


def revert_config(history_id: int, *, role: str = 'app') -> str:
    """
    把一条 `operations.config_history` 记录回滚到"改动前"的状态。

    三种情况分开处理：

    - `INSERT` 的改动前是空的 → 回滚就是把那行删掉
    - `DELETE` 的改动前是整行 → 回滚就是把它插回去
    - `UPDATE` → 用 `before` 整行覆盖回去

    `table_name` 在历史表里存的是带 schema 的全名（如 `config.setting`），
    这里要剥掉 `config.` 才能对上 `TABLES` 的键 —— 之前 `/blocks` 页调的
    `revert_config` 根本不存在（AttributeError），点"回滚"必然报错。

    只回滚 `config.*`：`md.*` 的历史不走这张表，`ops.*` 自身不该被回滚。
    """
    row = engine.query_one(
        'SELECT id, table_name, op, before, after FROM operations.config_history '
        'WHERE id = %s', (history_id,))
    if row is None:
        raise ValueError(f'没有 id={history_id} 这条配置历史')

    full = row['table_name'] or ''
    schema, _, table = full.partition('.')
    if not table:
        schema, table = 'config', full
    if schema != 'config':
        raise ValueError(f'只能回滚 config.* 的改动，这条是 {full}')
    _check_table(table)
    pk = TABLES[table]

    before, after, op = row['before'], row['after'], (row['op'] or '').upper()

    if op == 'INSERT' or before is None:
        key = (after or {}).get(pk)
        if key is None:
            raise ValueError('改动前是空的、改动后又取不到主键，无法回滚')
        engine.execute(f'DELETE FROM config.{table} WHERE "{pk}" = %s', (key,),
                       role=role)
        return f'已撤销新增：删除 config.{table} 的 {pk}={key}'

    # 生成列不能写（如自增主键之外的 GENERATED 列），先问一下实际可写列有哪些
    writable = {r['column_name'] for r in engine.query(
        'SELECT column_name FROM information_schema.columns '
        "WHERE table_schema='config' AND table_name=%s "
        "AND is_generated='NEVER' AND is_updatable='YES'", (table,))}
    values = {k: v for k, v in before.items() if not writable or k in writable}
    upsert_row(table, values, role=role)
    verb = '已恢复被删的行' if op == 'DELETE' else '已回滚到改动前'
    return f'{verb}：config.{table} 的 {pk}={values.get(pk)}'

