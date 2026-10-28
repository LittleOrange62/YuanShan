"""「选一只票」那一格的检索视图：把 `reference_data.security` 的四种匹配（代码 / 名称 / 全拼 / 首字母）
包装成界面能老实念出来的形状。

为什么要有这一层（而不是让页面直接打 repo）：搜索框最容易骗人的三件事都发生在这里 ——
① **读不到被念成没有**（库断了要说"读不到"，不能说"字典里没这只票"）；
② **通配符**（用户输入里的 `%`/`_` 当通配符用会"匹配到一堆"，其实一只都没搜；转义在 repo 那一层做）；
③ **命中方式**（打 `PA` 出来「派林生物」，不告诉人是按首字母命中的，看着像乱出结果）。
"""
from __future__ import annotations

from app.db import engine
from app.db.repo import security as sec_repo

SOURCE = 'reference_data.security（pinyin/pinyin_abbr 由采集侧从通达信 .tnf 落进来，见 app/db/repo/security.py:49）'
KIND_LABELS = {'stock': '股票', 'etf': 'ETF', 'index': '指数', 'sector': '板块', 'other': '其他'}
DEFAULT_LIMIT = 20


def _matched_by(row: dict, q: str) -> str:
    """这一条是**凭什么**命中的。判据顺序与 repo 里那条 SQL 的 ORDER BY 一致，界面只转述不重算。"""
    s = q.strip().lower()
    if not s:
        return 'none'
    if str(row.get('symbol') or '').lower() == s or str(row.get('security_code') or '').lower() == s:
        return 'code'
    if s in str(row.get('name') or '').lower():
        return 'name'
    abbr = str(row.get('pinyin_abbr') or '').lower()
    full = str(row.get('pinyin') or '').lower()
    if abbr and abbr.startswith(s):
        return 'abbr'
    if full and full.startswith(s):
        return 'pinyin'
    # 名称里含全角字母/生僻写法时上面几条会都不中；子串匹配那条在 SQL 里，交不回具体列，
    # 老实说"按别的字段命中"，不硬编成"按名称"。
    return 'other'


def _py_gap() -> dict:
    """字典里有多少条**没有拼音**：这决定了"用首字母搜不到"是市场没这只票，还是参考数据缺登记。
    只在 0 命中时算（一次全表 9,551 行的条件计数），正常输入不背这个开销。
    """
    try:
        r = engine.query_one(
            """select count(*) total,
                      count(*) filter (where asset_type='stock'
                                         and (pinyin_abbr is null or pinyin_abbr='')) stock_missing
                 from reference_data.security""", role='reader') or {}
        return {'total': r.get('total'), 'stock_missing_abbr': r.get('stock_missing')}
    except Exception as e:  # noqa: BLE001  这块是解释性读数，坏了不能把搜索结果一起弄没
        return {'total': None, 'stock_missing_abbr': None, 'error': str(e)[:180]}


def search(q: str, limit: int = DEFAULT_LIMIT, kinds: str = '') -> dict:
    kw = (q or '').strip()
    asset_types = [k.strip() for k in (kinds or '').split(',') if k.strip()]
    out: dict = {'available': True, 'query': kw, 'source': SOURCE,
                 'limit': limit, 'kinds': asset_types, 'kind_labels': KIND_LABELS,
                 'matched_by_meaning': {
                     'code': '代码完全对上', 'name': '名称里含这一串', 'abbr': '拼音首字母以此开头',
                     'pinyin': '全拼以此开头', 'other': '按其它列的子串对上（界面不替你猜是哪一列）',
                     'none': '没输入'}}
    if not kw:
        # 空输入不是"库里没票"：根本还没去查
        out.update({'available': False, 'reason': '还没输入代码、名称或首字母，所以这一次没去查字典'
                                                  '（这不是"一只都没有"）', 'rows': [], 'returned': 0})
        return out

    try:
        rows = sec_repo.search(kw, limit=limit, asset_types=asset_types)
    except Exception as e:  # noqa: BLE001  读不到 ≠ 没有：这一句必须与"字典里没这只票"分开
        out.update({'available': False, 'reason': f'证券字典读不到（查询失败，不是这一串没匹配）：{str(e)[:200]}',
                    'rows': [], 'returned': 0})
        return out

    for r in rows:
        r['matched_by'] = _matched_by(r, kw)
        r['asset_label'] = KIND_LABELS.get(str(r.get('asset_type')), str(r.get('asset_type')))
    out['rows'] = rows
    out['returned'] = len(rows)
    out['truncated'] = len(rows) >= limit
    if not rows:
        gap = _py_gap()
        out['reason'] = (f'字典里 {gap.get("total")} 条里没有代码/名称/全拼/首字母对上「{kw}」。'
                         + (f'注意有 {gap["stock_missing_abbr"]} 只股票**没登记拼音**，'
                            '用首字母搜它们一定搜不到（是参考数据缺，不是市场没这只票）。'
                            if gap.get('stock_missing_abbr') else '')
                         + (' 这一档只在这些类别里搜：' + '、'.join(asset_types) + '。' if asset_types else ''))
        out['py_gap'] = gap
    return out
