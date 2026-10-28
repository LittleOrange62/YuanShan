# -*- coding: utf-8 -*-
"""盘中涨跌停池**事件差**判据（纯函数，不碰库、不碰网络、不发送）。

搬自外部播报系统 `intraday_zt/zt_watcher.py`（2026-10-19~10-23 实测定型），
判据一字未改，只改了三处**数据诚实性**，每处都写了为什么：

1. `board_count`（连板数）原来缺字段时 `or 1` —— 会把一只 3 板票在第一针上记成**首板**，
   而且事后完全看不出来（1 是合法读数，没法和"真的首板"区分）。现在缺就是 `None`，
   并进 `missing` 点名。代价：`digest()` 的首板数/最高板必须**排除 None**，
   旧实现是拿 1 参与的，所以那两个数在缺字段的票上本来就是虚的。
2. `open_count`（炸板次数）原来缺时 `or 0` —— 0 在这里是真实读数（"从头封到尾"），
   所以缺数不能写成 0。现在 `None`。
   ⚠️ 判据 `zbc 变大 → 开板又回封` 在两侧都缺数时**不触发**（旧实现会拿 0 比 0，行为一致）。
3. `price` 原来 `(_num(p) or 0)/1000` —— 缺价会算出 0.0 元。现在 `None`。

时刻与单位（现查 2026-10-26，写死在此一处）：
    p    池行价格 = 元 × 1000        → price 存元
    zdp  涨幅                      → 已是百分数，pct 直接存（10.0654 = +10.0654%）
    fbt  首封时刻 = hhmmss 整数      → 'HH:MM' 字符串；跌停池没有这一格
    fund 封单额 = 元
"""
from __future__ import annotations

from typing import Any, Optional

#: 判据版本。改判据（含上面三条诚实性改动）**必须换号**，旧行留着对账。
SPEC_VERSION = 'intraday-pool-event-1'

#: 机器名 → 界面中文。中文只在这一处定义，页面与播报都读它（不许在别处抄一份）。
KIND_LABELS = {
    'seal_new': '新封',
    'reseal': '回封',
    'reseal_after_open': '开板又回封',
    'unseal': '炸板',
    'dt_seal_new': '新跌停',
    'dt_reseal': '跌停回封',
    'dt_reseal_after_open': '跌停开板又回封',
    'dt_open': '跌停开板',
}
KIND_SIDE = {k: ('dt' if k.startswith('dt_') else 'zt') for k in KIND_LABELS}
#: 一条消息里的排列顺序（外部定版：先涨停轨四条，再跌停轨四条）
KIND_ORDER = ('seal_new', 'reseal_after_open', 'reseal', 'unseal',
              'dt_seal_new', 'dt_reseal_after_open', 'dt_reseal', 'dt_open')
#: 涨跌停池的字段语义不同：涨停轨掉出池子要"出现在炸板池"才算炸板；
#: 跌停池**没有炸板池对应物**，出池即判开板（外部 10-21 定）。
_LABEL_ZT = {'seal_new': '新封', 'reseal': '回封',
             'reseal_after_open': '开板又回封', 'unseal': '炸板'}


def _num(v: Any) -> Optional[float]:
    """东财数值格可能是 '-'（停牌/无数据）或干脆没有这一键 → None，**不当 0**。"""
    return v if isinstance(v, (int, float)) else None


def _hhmm(hhmmss: Any) -> Optional[str]:
    """`fbt` 是 hhmmss 整数。缺格返回 None（旧实现返回空串，会和"00:00"混在一起）。"""
    if not hhmmss:
        return None
    s = str(int(hhmmss)).zfill(6) if isinstance(hhmmss, (int, float)) else str(hhmmss).zfill(6)
    return f'{s[:2]}:{s[2:4]}'


def normalize(row: dict) -> dict:
    """池行 → 我们的字段。缺的键一律 None，并记进 `missing`（键名＝落库列名）。"""
    price_raw = _num(row.get('p'))
    lbc, zbc, fund = _num(row.get('lbc')), _num(row.get('zbc')), _num(row.get('fund'))
    missing = {}
    if lbc is None:
        missing['board_count'] = '池行没有 lbc 格'
    if zbc is None:
        missing['open_count'] = '池行没有 zbc 格'
    if fund is None:
        missing['seal_fund_yuan'] = '池行没有 fund 格'
    if price_raw is None:
        missing['price'] = '池行没有 p 格或为 -'
    return {
        'code': str(row.get('c', '')),
        'name': row.get('n') or None,                  # 实测有「生 意 宝」这种带空格的名，原样留
        'pct': _num(row.get('zdp')),
        'price': round(price_raw / 1000.0, 3) if price_raw is not None else None,
        'board_count': int(lbc) if lbc is not None else None,
        'open_count': int(zbc) if zbc is not None else None,
        'first_seal_time': _hhmm(row.get('fbt')),
        'seal_fund_yuan': fund,
        'industry': row.get('hybk') or None,
        'missing': missing,
    }


def diff_events(prev: dict, cur: dict, *, zb_codes: Optional[set] = None,
                seen: Optional[set] = None, side: str = 'zt'
                ) -> list[dict]:
    """
    两针池子做差 → 逐条事件。`prev`/`cur` 是 `code → normalize() 结果`。

    判据（外部定版，未改）：
      · 在 cur 不在 prev → 首次进池：`seal_new`；若历史上进过池（`seen`）→ `reseal`（炸后回封）
      · 两针都在、`open_count` 变大 → `reseal_after_open`（开板又回封）
      · 在 prev 不在 cur → 涨停轨要"这针出现在炸板池里"才算 `unseal`；
        跌停轨没有炸板对应物，**出池即 `dt_open`**（`side='dt'`）
    """
    zb_codes = zb_codes or set()
    seen = seen or set()
    dt = side == 'dt'
    out: list[dict] = []
    for code, row in cur.items():
        if code not in prev:
            kind = 'dt_reseal' if (dt and code in seen) else ('dt_seal_new' if dt else
                   ('reseal' if code in seen else 'seal_new'))
            out.append({'kind': kind, **row})
            continue
        before, after = prev[code].get('open_count'), row.get('open_count')
        if before is not None and after is not None and after > before:
            out.append({'kind': 'dt_reseal_after_open' if dt else 'reseal_after_open', **row})
    for code, row in prev.items():
        if code in cur:
            continue
        if dt or code in zb_codes:
            out.append({'kind': 'dt_open' if dt else 'unseal', **row})
    return out


def money_text(v: Optional[float]) -> str:
    if v is None:
        return '封单缺数'
    return f'封单{v / 1e8:.2f}亿' if v >= 1e8 else f'封单{v / 1e4:.0f}万'


def fmt_rows(rows: list[dict], limit: int, board: str = '连板') -> str:
    """
    一行一只票。**缺的那一格直接不显示**（用户 10-26 点名：首封缺数就别念出来）。

    以前这里念"首封缺数 / 板数缺数 / 涨幅缺数"，一条消息里全是"缺数"，读起来像坏消息、
    其实只是源站这一格没有（跌停池**结构上就没有 fbt**，跌停轨永远"缺"）。
    库里仍然写 NULL + `missing` 点名（数据层不能省），**只是屏幕上不念**。
    当日首封要盘后才能从 `analytics.limit_up_pool_daily.first_seal_at`（本地分钟线自算）补上。
    """
    parts = []
    for r in rows[:limit]:
        seg = [f"{r['name']} {r['code']}"]
        if r.get('board_count') is not None:
            seg.append(f"{r['board_count']}{board}")
        if r.get('pct') is not None:
            seg.append(f"{r['pct']:+.2f}%")
        if r.get('first_seal_time'):
            seg.append(f"首封{r['first_seal_time']}")
        if r.get('seal_fund_yuan') is not None:
            seg.append(money_text(r['seal_fund_yuan']))
        if r.get('open_count'):
            seg.append(f"炸{r['open_count']}次")
        parts.append(' '.join(seg))
    return '、'.join(parts) + (f"…等{len(rows)}只" if len(rows) > limit else "")


def digest(pool: dict, dt_pool: Optional[dict] = None) -> str:
    """基线摘要一句话。**`board_count` 为 None 的票不参与首板数与最高板**，
    并在末尾点名"板数缺数 N 只"——旧实现拿 `or 1` 参与，那两个数是虚的。"""
    rows = list(pool.values())
    known = [r for r in rows if r.get('board_count') is not None]
    unknown = len(rows) - len(known)
    firsts = [r for r in known if r['board_count'] == 1]
    tops = sorted(known, key=lambda r: -r['board_count'])[:3]
    top_s = '、'.join(f"{r['name']} {r['board_count']}板" for r in tops)
    dt_s = f'；跌停 {len(dt_pool)} 家' if dt_pool is not None else ''
    gap_s = f'（板数缺数 {unknown} 只未计入）' if unknown else ''
    return (f'涨停池 {len(rows)} 家（首板 {len(firsts)}{gap_s}），'
            f'最高板：{top_s or "—"}{dt_s}')
