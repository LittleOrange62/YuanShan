# -*- coding: utf-8 -*-
"""
筛选执行引擎：把 `config.screen_rule` 的条件模板拼成一条参数化 SQL 跑出来。

**注册表存的是 WHERE 片段，不是整条 SQL**，而且片段里的占位符分两类：

- 值类（数字、日期、天数）→ 一律换成 `%(名字)s` 走 psycopg 绑定，不进 SQL 文本
- 字面类（列名 `close`、比较符 `>`）→ 列名和运算符**不能**当绑定参数用，
  所以这类参数在 `param_schema` 里必须带 `"literal": true` 且给出 `enum`，
  引擎只在枚举白名单命中时才做文本替换，没命中直接报错。

这条分界线是这个模块存在的全部理由。没有它，"条件类型可配置"就等于
"数据库里能写任意 SQL"—— `config.screen_rule` 是 `/blocks` 页面上能改的表。

**候选集默认是自选池**（旧系统 `/watchlist_filter` 的语义），可以切成全市场。

**窗口起始日由引擎算**：模板里写 `:start`，引擎按参数 `days` 从
`reference_data.trade_calendar` 倒数第 N 个交易日取。用日历而不是 `:d - days`：
自然日往前推 5 天遇到长假只剩两个交易日，"近 5 天涨停过"就漏了。
"""
from __future__ import annotations

import datetime as dt
import json
import re

from app.db import engine
from app.compute import ma_turn

# 占位符：`:name`。冒号后必须是标识符，避免把 `::text` 这种类型转换也吃掉
_PH = re.compile(r':([a-z_][a-z0-9_]*)')

# 条件参数里出现这些名字要先过引擎自己的类型/枚举/区间校验；再在这里登记**跨参数**的关系校验。
# 判据本体住在各规则自己的模块里（`ma_turn` → `app/compute/ma_turn.py`），引擎只负责在拼 SQL 前调用。
_RULE_PARAM_CHECKS = {ma_turn.RULE_KEY: ma_turn.check_params}

# 引擎注入的占位符，条件参数里不许重名
RESERVED = ('d', 'start')

# 结果里固定带的列。筛完要能直接看，所以带上当日行情和操作分
BASE_COLS = """
    d.security_code AS 代码, s.name AS 名称, d.close AS 收盘, d.pct_change AS 涨跌幅,
    d.turnover_rate_percent AS 换手率, round(o.value, 1) AS 操作分, o.quadrant AS 象限
"""

MAX_LIMIT = 2000


class ScreenError(ValueError):
    """条件不合法。照实抛给页面显示 —— "筛选失败"这种提示没有任何用。"""


def rules(*, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT rule_key, name, param_schema, sql_template, note '
        '  FROM config.screen_rule WHERE enabled ORDER BY rule_key', role=role)


def rules_map(*, role: str = 'reader') -> dict[str, dict]:
    return {r['rule_key']: r for r in rules(role=role)}


def _schema_props(rule: dict) -> dict:
    schema = rule.get('param_schema') or {}
    if isinstance(schema, str):
        schema = json.loads(schema)
    return schema.get('properties') or {}


def schema_props(rule: dict) -> dict:
    """这条规则的参数定义（`{参数名: spec}`）。页面按它生成输入控件。"""
    return _schema_props(rule)



def param_names(rule: dict) -> list[str]:
    """这条规则用到哪些参数。页面上用它提示"这一条要填什么"。"""
    return list(_schema_props(rule))


def _coerce(name: str, spec: dict, value):
    """按 schema 把页面传来的字符串收成正确类型，顺带做白名单校验。

    校验三道，全部**点名参数**、不许静默当作没筛（2026-10-21 随 `ma_turn` 补齐）：
      · `literal` 类（列名/比较符是文本替换进 SQL 的）只能取 `enum` 白名单 —— 原有那道门；
      · 非 literal 但登记了 `enum` 的（绑定值也要落在枚举里，如 `long_dir`）同样点名退回；
      · 登记了 `minimum`/`maximum` 的数值参数收进类型后再夹区间；字符串收不成数字也点名退回。
    """
    if spec.get('literal'):
        allowed = spec.get('enum') or []
        if value not in allowed:
            raise ScreenError(f'参数 {name} 只能是 {allowed} 之一，收到 {value!r}')
        return value                      # 文本替换，调用方负责放进 SQL
    t = spec.get('type')
    if t == 'integer':
        try:
            val = int(value)
        except (TypeError, ValueError):
            raise ScreenError(f'参数 {name} 要的是整数，收到 {value!r}') from None
    elif t == 'number':
        try:
            val = float(value)
        except (TypeError, ValueError):
            raise ScreenError(f'参数 {name} 要的是数字，收到 {value!r}') from None
    else:
        val = value
        if spec.get('format') == 'date':
            val = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    if 'enum' in spec and val not in spec['enum']:
        raise ScreenError(f'参数 {name} 只能是 {spec["enum"]} 之一，收到 {value!r}')
    lo, hi = spec.get('minimum'), spec.get('maximum')
    if t in ('integer', 'number') and val is not None:
        if lo is not None and val < lo:
            raise ScreenError(f'参数 {name} 不能小于 {lo}，收到 {val}')
        if hi is not None and val > hi:
            raise ScreenError(f'参数 {name} 不能大于 {hi}，收到 {val}')
    return val


def _coerce_all(rule: dict, given: dict) -> dict:
    """按登记的 schema 把一条条件的参数**全部**收一遍（没给的补默认值）。

    `_render` 只会碰到模板里出现过的占位符，而参数之间的关系校验（如 `ma_turn` 的
    long_days > recent_days）要拿到完整的一份才能判 —— 这一份就是校验用的那份。
    """
    props = _schema_props(rule)
    out: dict = {}
    for name, spec in props.items():
        raw = given.get(name, (spec or {}).get('default'))
        if raw in (None, ''):
            continue          # 没登记默认值的必填项由读层 `_given_all` 点名报缺
        out[name] = _coerce(name, spec or {}, raw)
    return out


def _start_date(target: dt.date, days: int, *, role: str = 'reader') -> dt.date:
    """从目标日往前数 days 个交易日（含目标日）的第一天。"""
    rows = engine.query(
        'SELECT calendar_date FROM reference_data.trade_calendar '
        ' WHERE is_open AND calendar_date <= %s ORDER BY calendar_date DESC LIMIT %s',
        (target, max(int(days), 1)), role=role)
    return rows[-1]['calendar_date'] if rows else target


def _render(i: int, rule: dict, given: dict, target: dt.date,
            params: dict) -> str:
    """
    把一条规则的模板渲染成 SQL 片段，同时把绑定参数塞进 `params`。

    参数名前缀 `c{i}_`：同一次筛选里两条"N 日内涨停过"用的是不同的 days，
    不加前缀第二条会把第一条的绑定值覆盖掉。

    替换用一次 `re.sub` 走完，**不能**按名字逐个 `str.replace`：模板里同时出现
    `:d` 和 `:days` 时，替换 `:d` 会把 `:days` 咬掉半截，拼出一条语法错的 SQL。
    """
    props = _schema_props(rule)
    tpl = rule['sql_template']
    used = {m.group(1) for m in _PH.finditer(tpl)}

    # 先算 :start —— 它依赖参数 days
    if 'start' in used:
        if 'days' not in props:
            raise ScreenError(f"{rule['rule_key']} 用了 :start 但没有 days 参数")
        days = int(_coerce('days', props['days'], given.get(
            'days', (props['days'] or {}).get('default', 5))))
        params[f'c{i}_start'] = _start_date(target, days)

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name == 'd':
            return '%(d)s'
        if name == 'start':
            return f'%(c{i}_start)s'
        spec = props.get(name)
        if spec is None:
            raise ScreenError(f"{rule['rule_key']} 的模板用了未声明的参数 :{name}")
        raw = given.get(name, spec.get('default'))
        if raw in (None, ''):
            raise ScreenError(f"{rule['name']}：参数 {name} 不能为空")
        val = _coerce(name, spec, raw)
        if spec.get('literal'):
            return str(val)
        params[f'c{i}_{name}'] = val
        return f'%(c{i}_{name})s'

    return f'({_PH.sub(sub, tpl)})'


def _price_cols(conds: list[dict], rmap: dict, params: dict) -> str:
    """
    `price_compare` 条件额外带两列：该日该价格、以及它到最新收盘的涨跌幅。

    只有这一种条件有"一个明确的价格"可展示（涨停/振幅/连阳条件对应的是一段
    窗口，没有单一价格），所以不做成通用能力 —— 通用化的代价是给另外四种
    条件编出一个似是而非的数。
    """
    parts = []
    for i, c in enumerate(conds, 1):
        if c.get('rule_key') != 'price_compare':
            continue
        rule = rmap.get('price_compare')
        props = _schema_props(rule)
        field = _coerce('field', props['field'], c['params'].get('field', 'close'))
        on_date = _coerce('on_date', props['on_date'], c['params']['on_date'])
        params[f'p{i}_date'] = on_date
        px = (f'(SELECT x.{field} FROM market_data.stock_daily x '
              f' WHERE x.security_code = d.security_code AND x.trade_date = %(p{i}_date)s)')
        parts.append(f'round({px}, 2) AS "条件{i}价"')
        # 列名不带 `%`：psycopg 会把 SQL 文本里的 `%` 当占位符起手，
        # `"…涨跌%"` 直接报 only '%s','%b','%t' are allowed as placeholders。
        # 转义成 `%%` 也能跑，但注册表里的模板将来也得跟着记住这条规矩 —— 不留这个坑。
        parts.append(f'round((d.close - {px}) / nullif({px}, 0) * 100, 2) '
                     f'AS "条件{i}到今涨跌幅"')
    return (', ' + ', '.join(parts)) if parts else ''


def build(conds: list[dict], *, logic: str = 'and', trade_date=None,
          universe: str = 'watchlist', limit: int = 500,
          role: str = 'reader') -> tuple[str, dict]:
    """
    组装 SQL。`conds` 是 `[{'rule_key':..., 'params':{...}}, ...]`。

    条件为空就是"全部通过"（沿用旧系统语义），这时结果就是候选集本身 ——
    比报错有用：先看候选集有多少只，再逐条加条件。
    """
    if logic not in ('and', 'or'):
        raise ScreenError(f'logic 只能是 and / or，收到 {logic!r}')
    if universe not in ('watchlist', 'market'):
        raise ScreenError(f'universe 只能是 watchlist / market，收到 {universe!r}')
    target = trade_date or engine.scalar(
        'SELECT max(trade_date) FROM market_data.stock_daily', role=role)
    if target is None:
        raise ScreenError('market_data.stock_daily 是空的，先同步日线')
    if isinstance(target, str):
        target = dt.date.fromisoformat(target)

    rmap = rules_map(role=role)
    params: dict = {'d': target, 'lim': min(int(limit), MAX_LIMIT)}
    frags = []
    for i, c in enumerate(conds, 1):
        key = c.get('rule_key')
        if key not in rmap:
            raise ScreenError(f'没有这种筛选条件：{key}')
        # 参数组合校验（单值的 enum/区间在 _coerce 里点名退回，跨参数的关系在这里）：
        # 不合法就**出声退回并点名**，不许静默退化成"这条没筛出东西"
        check = _RULE_PARAM_CHECKS.get(key)
        if check is not None:
            try:
                check(_coerce_all(rmap[key], c.get('params') or {}))
            except ScreenError:
                raise                      # _coerce 已经在消息里点名的，原话端出去，不套两层
            except ValueError as exc:
                raise ScreenError(f"「{rmap[key]['name']}」参数不合法：{exc}") from exc
        frags.append(_render(i, rmap[key], c.get('params') or {}, target, params))

    where = f' {logic.upper()} '.join(frags) if frags else 'true'
    pool = ('d.security_code IN (SELECT DISTINCT security_code FROM config.watchlist_stock)'
            if universe == 'watchlist' else "s.asset_type = 'stock'")
    sql = f"""
        SELECT {BASE_COLS.strip()}{_price_cols(conds, rmap, params)}
          FROM market_data.stock_daily d
          JOIN reference_data.security s ON s.security_code = d.security_code
          LEFT JOIN analytics.score o ON o.security_code = d.security_code
               AND o.trade_date = %(d)s AND o.capability_key = 'oper_score'
         WHERE d.trade_date = %(d)s AND {pool}
           AND ({where})
         ORDER BY o.value DESC NULLS LAST, d.security_code
         LIMIT %(lim)s
    """
    return sql, params


def run(conds: list[dict], **kw) -> list[dict]:
    role = kw.pop('role', 'reader')
    sql, params = build(conds, role=role, **kw)
    return engine.query(sql, params, role=role)


# ---------------------------------------------------------------- 方案

def presets(*, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT preset_id, name, owner, conditions, created_at '
        '  FROM config.screen_preset ORDER BY preset_id DESC', role=role)


def save_preset(name: str, conds: list[dict], *, logic: str = 'and',
                universe: str = 'watchlist') -> int:
    """
    存一个方案。`conditions` 里连 logic 和候选集一起存 —— 只存条件的话，
    同一组条件用 and 还是 or 是两个完全不同的方案，下次调出来就成了掷骰子。
    """
    name = (name or '').strip()
    if not name:
        raise ScreenError('方案名不能为空')
    if not conds:
        raise ScreenError('没有条件可存')
    payload = {'logic': logic, 'universe': universe, 'conditions': conds}
    return engine.scalar(
        """
        INSERT INTO config.screen_preset (name, conditions)
        VALUES (%s, %s)
        ON CONFLICT (owner, name) DO UPDATE SET conditions = EXCLUDED.conditions
        RETURNING preset_id
        """, (name, json.dumps(payload, ensure_ascii=False, default=str)),
        role='app')


def delete_preset(preset_id: int) -> int:
    return engine.execute('DELETE FROM config.screen_preset WHERE preset_id = %s',
                          (int(preset_id),), role='app')
