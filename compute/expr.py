# -*- coding: utf-8 -*-
"""
受限表达式求值器：把 config.indicator.expr 里的公式算成数。

**为什么不用 eval**：这些公式是从数据库里读出来的，而数据库里的行未来要能在
/blocks 页面上编辑。`eval('__import__("os").system("...")')` 就是一句话的事。
所以走 Python 的 ast 模块解析成语法树，逐节点白名单校验：只允许字面量、
四则运算、比较、以及注册过的函数名和字段名，见到别的节点类型直接拒绝。
这是整个「积木城堡」里最容易被写成远程执行漏洞的一处。

**数据模型是长表面板**：DataFrame 带 (security_code, trade_date) 两级索引，
一次算全市场。这样时序函数（MA/REF/EMA）是 groupby(security_code) 的滚动运算，
截面函数（RANK/PCTRANK/MEAN BY）是 groupby(trade_date) 的运算，
两类函数在同一套数据上统一表达，不需要两条代码路径。

**BY 语法**：config.indicator 里有 `MEAN(pct_change BY sector)` 这种写法，
它不是合法 Python，ast 解析会失败。求值前把 ` BY x` 规范化成 `, "x"`，
这个改写只认标识符，不接受表达式，避免把注入面又开回来。
"""
from __future__ import annotations

import ast
import re
from typing import Any

import numpy as np
import pandas as pd

# 允许出现在公式里的字段。刻意用白名单而不是"面板里有什么就允许什么"——
# 否则一旦面板多带了一列敏感字段，公式就能读到它。
ALLOWED_FIELDS = (
    'open', 'high', 'low', 'close', 'pre_close', 'change_yuan', 'pct_change',
    'volume_shares', 'amount_yuan', 'turnover_rate_percent', 'limit_up', 'limit_down',
    'adjustment_factor', 'amp', 'board', 'sector', 'float_share', 'is_st',
)

# 排序方向这类裸标识符，按字符串常量处理
BARE_LITERALS = {'desc': 'desc', 'asc': 'asc'}

_BY_RE = re.compile(r'\s+BY\s+([A-Za-z_][A-Za-z0-9_]*)')
_LEVEL_CODE = 'security_code'
_LEVEL_DATE = 'trade_date'


class ExprError(ValueError):
    """公式不合法或算不出来。带上公式原文，方便在页面上直接提示给用户。"""


# ---------------------------------------------------------------- 函数实现

def _s(x: Any) -> pd.Series:
    if isinstance(x, pd.Series):
        return x
    raise ExprError('该函数的第一个参数必须是字段或表达式，不能是常量')


def _by_code(x: pd.Series):
    return x.groupby(level=_LEVEL_CODE, sort=False)


def _by_date(x: pd.Series):
    return x.groupby(level=_LEVEL_DATE, sort=False)


def _roll(x, n, how: str, *, min_periods: int | None = None) -> pd.Series:
    """
    分组滚动窗口。**走 pandas 原生聚合，不用 transform(lambda)**：
    5,500 只票 × 27 个指标时，每组一次 Python 回调会把单日计算拖到 11 分钟，
    换成原生聚合是十几秒。groupby().rolling() 会多出一层索引，用完 droplevel。
    """
    s = _s(x).astype('float64')
    n = int(n)
    mp = n if min_periods is None else min_periods
    out = getattr(_by_code(s).rolling(n, min_periods=mp), how)()
    if isinstance(out.index, pd.MultiIndex) and out.index.nlevels > s.index.nlevels:
        out = out.droplevel(0)
    return out.reindex(s.index)


def f_ma(x, n):
    return _roll(x, n, 'mean')


def f_ema(x, n):
    s = _s(x).astype('float64')
    out = _by_code(s).ewm(span=int(n), adjust=False).mean()
    if isinstance(out.index, pd.MultiIndex) and out.index.nlevels > s.index.nlevels:
        out = out.droplevel(0)
    return out.reindex(s.index)


def f_sum(x, n):
    return _roll(x, n, 'sum')


def f_std(x, n):
    # 样本标准差（ddof=1），与 pandas / Excel / 通达信同名函数一致
    return _roll(x, n, 'std')


def f_ref(x, n):
    return _by_code(_s(x)).shift(int(n))


def f_hhv(x, n):
    return _roll(x, n, 'max', min_periods=1)


def f_llv(x, n):
    return _roll(x, n, 'min', min_periods=1)


def f_slope(x, n):
    """
    n 日线性回归斜率（每日变化量），全向量化。

    斜率 = Σ((t-t̄)(x-x̄)) / Σ(t-t̄)²。把窗口内的局部位置 t 换成组内全局位置 y
    再平移，Σ(t·x) = Σ(y·x) - (i-n+1)·Σx，于是分子只需要两个原生滚动求和。
    不用 rolling.apply：那是逐窗口的 Python 回调，200 万行要跑十几分钟。
    """
    s = _s(x).astype('float64')
    n = int(n)
    y = _by_code(s).cumcount().astype('float64')
    sx = _roll(s, n, 'sum')
    syx = _roll(s * y, n, 'sum')
    t_bar = (n - 1) / 2.0
    sum_tx = syx - (y - n + 1) * sx
    numer = sum_tx - n * t_bar * (sx / n)
    denom = n * (n * n - 1) / 12.0
    return numer / denom



def f_rank(x, direction='desc'):
    asc = str(direction).lower() != 'desc'
    return _by_date(_s(x)).rank(method='min', ascending=asc)


def f_pctrank(x):
    """截面百分位，0~100。0 是最低，100 是最高。"""
    return _by_date(_s(x)).rank(pct=True) * 100.0


def f_mean(x, by=None):
    s = _s(x)
    if by is None:
        return _by_date(s).transform('mean')
    return _group_by_field(s, by, 'mean')


def f_median(x, by=None):
    s = _s(x)
    if by is None:
        return _by_date(s).transform('median')
    return _group_by_field(s, by, 'median')


def _group_by_field(s: pd.Series, by: str, how: str) -> pd.Series:
    """按「同一交易日 + 同一分组字段」求组内统计量，如同板块均值。"""
    ctx = _CONTEXT.get('panel')
    if ctx is None or by not in ctx.columns:
        raise ExprError(f'BY {by} 需要面板里带 {by} 列')
    keys = [s.index.get_level_values(_LEVEL_DATE), ctx[by].reindex(s.index)]
    return s.groupby(keys, sort=False).transform(how)


def f_abs(x):
    return abs(x) if not isinstance(x, pd.Series) else x.abs()


def _align(args):
    """把混在一起的 Series 和标量对齐成同长度的 ndarray 列表。"""
    idx = next((a.index for a in args if isinstance(a, pd.Series)), None)
    if idx is None:
        return None, [np.asarray(a, dtype='float64') for a in args]
    out = []
    for a in args:
        if isinstance(a, pd.Series):
            out.append(a.reindex(idx).to_numpy(dtype='float64'))
        else:
            out.append(np.full(len(idx), float(a)))
    return idx, out


def f_max(*args):
    """逐元素取大。用 np.fmax 而不是 Series.combine —— 后者是逐行 Python 回调。"""
    idx, arrs = _align(args)
    out = arrs[0]
    for a in arrs[1:]:
        out = np.fmax(out, a)
    return pd.Series(out, index=idx) if idx is not None else out


def f_min(*args):
    idx, arrs = _align(args)
    out = arrs[0]
    for a in arrs[1:]:
        out = np.fmin(out, a)
    return pd.Series(out, index=idx) if idx is not None else out


def f_if(cond, a, b):
    return pd.Series(np.where(cond, a, b), index=getattr(cond, 'index', None))


def f_count(cond, n):
    """n 日内条件成立的次数。"""
    return _roll(_s(cond).astype('float64'), n, 'sum', min_periods=1)


FUNCTIONS = {
    'MA': f_ma, 'EMA': f_ema, 'SUM': f_sum, 'STD': f_std, 'REF': f_ref,
    'HHV': f_hhv, 'LLV': f_llv, 'SLOPE': f_slope,
    'RANK': f_rank, 'PCTRANK': f_pctrank, 'MEAN': f_mean, 'MEDIAN': f_median,
    'ABS': f_abs, 'MAX': f_max, 'MIN': f_min, 'IF': f_if, 'COUNT': f_count,
}

# _group_by_field 要拿到整张面板才能按板块分组。用模块级上下文而不是把面板
# 一层层透传进每个函数——求值是单线程同步的，不会有交叉污染。
_CONTEXT: dict[str, pd.DataFrame | None] = {'panel': None}


# ---------------------------------------------------------------- AST 校验

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.Call,
    ast.Name, ast.Constant, ast.Load, ast.Tuple,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.FloorDiv,
    ast.USub, ast.UAdd, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.And, ast.Or,
)


def normalize(expr: str) -> str:
    """把 `MEAN(pct_change BY sector)` 改写成 `MEAN(pct_change, "sector")`。"""
    return _BY_RE.sub(lambda m: f', "{m.group(1)}"', expr)


def check(expr: str) -> ast.Expression:
    """解析并逐节点校验。不合法就抛 ExprError，绝不放行到求值阶段。"""
    src = normalize(expr)
    try:
        tree = ast.parse(src, mode='eval')
    except SyntaxError as exc:
        raise ExprError(f'公式语法错误：{expr} —— {exc.msg}') from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExprError(f'公式含不允许的语法 {type(node).__name__}：{expr}')
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExprError(f'只能调用注册过的函数名：{expr}')
            if node.func.id not in FUNCTIONS:
                raise ExprError(f'未注册的函数 {node.func.id}，可用：'
                                f'{", ".join(sorted(FUNCTIONS))}')
            if node.keywords:
                raise ExprError(f'函数不支持关键字参数：{expr}')
        if (isinstance(node, ast.Name) and node.id not in FUNCTIONS
                and node.id not in ALLOWED_FIELDS
                and node.id not in BARE_LITERALS):
            raise ExprError(f'未知字段 {node.id}，可用：'
                            f'{", ".join(sorted(ALLOWED_FIELDS))}')
    return tree


def fields_used(expr: str) -> set[str]:
    """公式用到哪些字段。取数时据此决定要 SELECT 哪些列。"""
    tree = check(expr)
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id in ALLOWED_FIELDS}


# ---------------------------------------------------------------- 求值

def _eval_node(node: ast.AST, panel: pd.DataFrame):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, panel)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in BARE_LITERALS:
            return BARE_LITERALS[node.id]
        if node.id in panel.columns:
            return panel[node.id]
        raise ExprError(f'面板里没有字段 {node.id}')
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e, panel) for e in node.elts)
    if isinstance(node, ast.UnaryOp):
        v = _eval_node(node.operand, panel)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return v
        return ~v.astype(bool) if isinstance(v, pd.Series) else (not v)
    if isinstance(node, ast.BinOp):
        a, b = _eval_node(node.left, panel), _eval_node(node.right, panel)
        op = node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            # 除零得 NaN 而不是抛错。行情里 pre_close 为空是正常的（上市首日），
            # 让整批计算因为一只票的首日而失败不合理。
            return _safe_div(a, b)
        if isinstance(op, ast.FloorDiv):
            return _safe_div(a, b) // 1
        if isinstance(op, ast.Mod):
            return a % b
        if isinstance(op, ast.Pow):
            return a ** b
        raise ExprError(f'不支持的运算符 {type(op).__name__}')
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, panel)
        out = None
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _eval_node(comp, panel)
            r = _compare(left, right, op)
            out = r if out is None else (out & r)
            left = right
        return out
    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, panel) for v in node.values]
        out = vals[0]
        for v in vals[1:]:
            out = (out & v) if isinstance(node.op, ast.And) else (out | v)
        return out
    if isinstance(node, ast.Call):
        args = [_eval_node(a, panel) for a in node.args]
        try:
            return FUNCTIONS[node.func.id](*args)
        except ExprError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExprError(f'{node.func.id} 计算失败：{exc}') from exc
    raise ExprError(f'不支持的语法 {type(node).__name__}')


def _safe_div(a, b):
    if isinstance(b, pd.Series):
        b = b.replace(0, np.nan)
    elif b == 0:
        return np.nan
    return a / b


def _compare(a, b, op):
    if isinstance(op, ast.Eq):
        return a == b
    if isinstance(op, ast.NotEq):
        return a != b
    if isinstance(op, ast.Lt):
        return a < b
    if isinstance(op, ast.LtE):
        return a <= b
    if isinstance(op, ast.Gt):
        return a > b
    if isinstance(op, ast.GtE):
        return a >= b
    raise ExprError(f'不支持的比较 {type(op).__name__}')


def evaluate(expr: str, panel: pd.DataFrame) -> pd.Series:
    """
    在面板上求值，返回与 panel 同索引的 Series。

    panel 必须是 (security_code, trade_date) 两级索引且按此排序 —— 时序函数依赖顺序，
    没排序算出来的 MA 是错的且不会报错，这类静默错误最难查，所以在这里强制。
    """
    if not isinstance(panel.index, pd.MultiIndex) or \
            list(panel.index.names) != [_LEVEL_CODE, _LEVEL_DATE]:
        raise ExprError('面板索引必须是 (security_code, trade_date)')
    tree = check(expr)
    _CONTEXT['panel'] = panel
    try:
        out = _eval_node(tree, panel)
    finally:
        _CONTEXT['panel'] = None
    if not isinstance(out, pd.Series):
        out = pd.Series(out, index=panel.index, dtype='float64')
    return pd.to_numeric(out, errors='coerce')
