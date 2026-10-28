# -*- coding: utf-8 -*-
"""
界面说明（`config.help_note`）的读取与渲染。

**为什么说明要进库**：原来说明有两处出身 —— 代码里的 `ui.CARD_HELP` 和布局里的
`config.page_layout.options.note`。前者改一个字要改代码 + 重启；后者是一整段自由
文本，"数据源"和"得分口径"糊在同一个字符串里，既查不了（"哪些卡用了
`fct.score`？"）也统一不了格式。现在正文按字段拆开存在 `config.help_note`，
这里负责把字段**组合**成一段 markdown。

拼装顺序固定，全站一个样式，缺哪段就少哪段：

    purpose          为什么有这个窗口（回答什么问题，不是"显示了什么"）
    content          看什么、怎么读
    取数              source_query / source_tables / script 三个字段拼出来
    derive           二次加工链：上游哪些数据、谁算的
    formula          参数与得分口径
    includes         公共片段（scope='snippet'），可嵌套，带环检测
    caveat           坑与已知缺口，自动加 ⚠️

查找优先级（`ui` 那边按这个用）：

    config.help_note（库）> config.page_layout.options.note > ui.CARD_HELP（代码兜底）
    > config.chart.options.note

代码兜底留着是为了**不倒退**：34 条存量说明还在 `CARD_HELP` 里，迁库是渐进的，
库里没有的照旧显示代码里的那份，而不是显示空白。
"""
from __future__ import annotations

import re
from typing import Any

from app.db import engine

# `{名字}` 占位符。只认字母数字下划线和点，避免把 markdown 里的花括号吃掉。
_PLACEHOLDER = re.compile(r'\{([A-Za-z_][A-Za-z0-9_.]*)\}')

# 进程内缓存。说明是"改了立刻要看到"的东西，但也不该每张卡查一次库 ——
# 一页 16 张卡就是 16 次查询。维护页保存后调 `reload()`。
_CACHE: dict[str, dict] | None = None


def reload() -> int:
    """丢掉缓存并重新装载，返回条数。维护页保存后调它。"""
    global _CACHE
    _CACHE = None
    return len(notes())


def notes() -> dict[str, dict]:
    """`{note_key: row}`。一次全量装载，之后走缓存。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = {r['note_key']: r for r in engine.query(
            'SELECT * FROM config.help_note WHERE enabled ORDER BY seq, note_key')}
    return _CACHE


def _index() -> dict[tuple[str, str], dict]:
    """`(scope, target) → row`。同一个 target 有多条时取 seq 最小的那条。"""
    out: dict[tuple[str, str], dict] = {}
    for r in notes().values():
        if r['scope'] == 'snippet' or not r['target']:
            continue
        out.setdefault((r['scope'], r['target']), r)
    return out


# ---------------------------------------------------------------- 渲染

def _fill(text: str | None, params: dict[str, Any]) -> str | None:
    """
    替换 `{名字}` 占位符。

    值可以是字面量，也可以是 `{"setting": "sector_trend.windows"}` ——
    后者去 `config.setting` 现取。窗口天数、权重这类会变的数写死在正文里，
    改完配置说明就开始骗人。

    没有对应参数的占位符**原样留着**：宁可让人看到 `{win}` 没配，
    也不要悄悄删掉一个数字，让句子读起来像"最近  个交易日"。
    """
    if not text:
        return text

    def one(m: re.Match) -> str:
        name = m.group(1)
        if name not in params:
            return m.group(0)
        v = params[name]
        if isinstance(v, dict) and 'setting' in v:
            v = engine.scalar('SELECT value FROM config.setting WHERE key = %s',
                              (v['setting'],))
        if isinstance(v, (list, tuple)):
            return ' / '.join(str(x) for x in v)
        return '' if v is None else str(v)

    return _PLACEHOLDER.sub(one, text)


def _fetch_line(row: dict) -> str | None:
    """把 source_query / source_tables / script 三个字段拼成「取数」那一段。"""
    q = (row.get('source_query') or '').strip()
    tables = [t for t in (row.get('source_tables') or []) if t]
    script = (row.get('script') or '').strip()
    if not (q or tables or script):
        return None
    bits = ['**取数**']
    if q:
        bits.append(f'`{q}`')
    if tables:
        bits.append(('→ ' if q else '') + ' ⋈ '.join(f'`{t}`' for t in tables))
    if script:
        bits.append(f'（脚本 `{script}`）')
    return ' '.join(bits)


def render(note_key: str, *, _seen: tuple[str, ...] = ()) -> str:
    """
    把一条说明渲染成 markdown。**引用成环直接抛 ValueError**，不静默截断 ——
    静默的话页面上只是少一段话，没人会发现配置写错了。
    """
    if note_key in _seen:
        chain = ' → '.join([*_seen, note_key])
        raise ValueError(f'说明片段循环引用：{chain}')
    row = notes().get(note_key)
    if row is None:
        raise KeyError(f'config.help_note 里没有 {note_key}')
    params = row.get('params') or {}
    parts: list[str] = []
    title = (row.get('title') or '').strip()
    # 卡片标题就在说明徽标左边，重复一遍纯占地方；页级说明没有那个标题，要写。
    if title and row['scope'] == 'page':
        parts.append(f'**{title}**')
    for key in ('purpose', 'content'):
        v = (row.get(key) or '').strip()
        if v:
            parts.append(v)
    fetch = _fetch_line(row)
    if fetch:
        parts.append(fetch)
    for key, prefix in (('derive', '**二次加工**：'), ('formula', '**口径**：')):
        v = (row.get(key) or '').strip()
        if v:
            parts.append(prefix + v)
    for inc in (row.get('includes') or []):
        if inc:
            parts.append(render(inc, _seen=(*_seen, note_key)))
    caveat = (row.get('caveat') or '').strip()
    if caveat:
        parts.append(caveat if caveat.startswith('⚠️') else f'⚠️ {caveat}')
    return _fill('\n\n'.join(p for p in parts if p), params) or ''


def text(note_key: str | None) -> str | None:
    """渲染一条说明；没有这条 key、或者配置写错了就返回 None（页面照旧显示兜底）。"""
    if not note_key:
        return None
    try:
        return render(note_key) or None
    except (KeyError, ValueError):
        return None


def _by_target(scope: str, target: str | None) -> str | None:
    if not target:
        return None
    row = _index().get((scope, target))
    return text(row['note_key']) if row else None


def for_page(route: str | None) -> str | None:
    """整页说明。target 就是路由（`/sector`）。"""
    return _by_target('page', route)


def for_card(target: str | None) -> str | None:
    """卡片说明。target 是 `card_ref`，手写卡用自己的稳定别名。"""
    return _by_target('card', target)


def for_stat(target: str | None) -> str | None:
    """指标块说明。target 是指标 key（`k_mood`）。"""
    return _by_target('stat', target)


# ---------------------------------------------------------------- 写

FIELDS = ('scope', 'target', 'title', 'purpose', 'content', 'source_query',
          'source_tables', 'script', 'derive', 'formula', 'caveat',
          'includes', 'seq')


def save(note_key: str, values: dict) -> str:
    """
    存一条说明（不存在就插）。返回 'ins' 或 'upd'。

    只认 `FIELDS` 里的列 —— 列名要拼进 SQL 文本，从写死的白名单取。
    存完**必须 `reload()`**，否则页面还在用缓存里的旧文字，看起来像"存了没生效"。
    """
    key = (note_key or '').strip()
    if not key:
        raise ValueError('note_key 不能为空')
    row = {k: values.get(k) for k in FIELDS if k in values}
    row.setdefault('scope', 'card')
    for arr in ('source_tables', 'includes'):
        if arr in row and isinstance(row[arr], str):
            row[arr] = [x.strip() for x in row[arr].replace('，', ',').split(',')
                        if x.strip()]
    if row.get('scope') != 'snippet' and not (row.get('target') or '').strip():
        raise ValueError('除了 snippet，target 必填（否则这条说明永远显示不出来）')
    exists = engine.scalar(
        'SELECT 1 FROM config.help_note WHERE note_key = %s', (key,))
    cols = list(row)
    if exists:
        sets = ', '.join(f'{c} = %s' for c in cols)
        engine.execute(
            f'UPDATE config.help_note SET {sets}, updated_at = now() '
            ' WHERE note_key = %s', [*[row[c] for c in cols], key], role='app')
    else:
        engine.execute(
            f'INSERT INTO config.help_note (note_key, {", ".join(cols)}) '
            f'VALUES (%s, {", ".join(["%s"] * len(cols))})',
            [key, *[row[c] for c in cols]], role='app')
    reload()
    return 'upd' if exists else 'ins'


# ---------------------------------------------------------------- 缺口报表

def coverage() -> list[dict]:
    """
    每个窗口有没有说明。`/blocks` 的说明维护区用它列缺口，测试也用它。

    只看**库里**有没有 —— 代码兜底（`CARD_HELP`）不算覆盖：兜底是过渡期的
    临时状态，把它算成"已覆盖"就永远迁不完。
    """
    idx = _index()
    out: list[dict] = []
    for r in engine.query(
            'SELECT page_key, slot, card_kind, card_ref, title '
            '  FROM config.page_layout WHERE visible ORDER BY page_key, slot'):
        target = r['card_ref'] or f"{r['page_key']}.{r['slot']}"
        out.append({'kind': 'card', 'where': f"{r['page_key']}#{r['slot']}",
                    'target': target, 'title': r['title'],
                    'has_note': ('card', target) in idx})
    for r in engine.query(
            'SELECT route, label FROM config.menu WHERE visible ORDER BY seq'):
        out.append({'kind': 'page', 'where': r['route'], 'target': r['route'],
                    'title': r['label'],
                    'has_note': ('page', r['route']) in idx})
    return out
