# -*- coding: utf-8 -*-
"""「当日前三甲 · 它们前五场名次 · 与它们形状最像的板块」的**只读**层。

用户 2026-10-25 点名要这三件事，同日裁定把计算搬到链上：
判据与落库只住 `app/compute/qd_sector_follow.py` 一份（接在加工链 `qd_sector` 那一步里），
本模块**只读 `qd.sector_follow` 的落库行**并按接口契约组装 payload。
先前这里是"读侧现算 + 进程内缓存一小时"：一句 SQL 回母体重排约两秒，
每个访客重付一遍，而算出来的东西每天只需要算一次 —— 那是把死数据当实时算。

因此本模块不再有任何 SQL 排名语句、也没有名次/距离/相关的计算：
读到什么就交什么。要改判据请改写侧并升 `spec_version`（旧批次留着能对照）。

三态要说清（都写成 `available:false` + 完整原因，不带 `leaders` 键）：
库读不到 / 表还没建 / 这一场没有落库行 / 这一档 lag 没落。
"没有这一档的行"与"这一场没有领涨板块"不合并成一句"暂无数据"。
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Dict, List, Optional

from app.compute import qd_sector_follow as W
from . import pg

#: 界面上那一档（用户按自己写的日期选的口径）
DEFAULT_LAG = W.DEFAULT_LAG
#: 落库的四档，与写侧同一份（`vr/app.py` 的 Query(ge=0, le=3) 与它对齐）
LAG_CHOICES = W.LAGS

_TABLE = 'qd.sector_follow'

_COLUMNS = """trade_date::text as trade_date, lag, sector_code, sector_name, sector_type,
                     day_rank, pct_change, universe_size, top_n, trajectory_points,
                     shape_points, k_similar, sessions, trajectory, similar_block,
                     deviation, source, spec_version, calculated_at"""

#: 落库行里那批偏差码 → 屏幕上念的那句。没登记的码照旧念码本身，不编一个好听的名字。
_DEV_LABEL = {
    'L-22': '跨日名次不可比',
    'L-23': '两个分母',
    'L-24': '模板缺场次',
}

CALIBERS = {
    'leaders': ('前三甲＝当日按板块涨幅排出的前三名（领涨那一名 + 后面两名）。'
                '母体是当天参与排名的通达信概念/行业板块，无名板块与|涨幅|>30% 的脏行同写侧'
                '一并剔除；并列涨幅按名称、代码定序，所以第 3 名和第 4 名换了顺序是真实变化，'
                '不是随机。'),
    'trajectory': ('前五场那一串数字是「该板块在当天全母体里的名次」（不含当日，从最近往远排）。'
                   '名次是横截面位置不是排名榜位次：同一场里 914 个板块一起排，'
                   '第 1 名和第 800 名之间没有"及格线"。空着（—）＝那天它没有行情，'
                   '不是第 0 名、也不是没涨没跌。'),
    'lag': ('相似匹配按「错开两场」对齐（用户 2026-10-25 按自己写的日期选的口径）：'
            '模板取领涨板块 t-2、t-3、t-4、t-5 这四个名次，候选取它自己 t、t-1、t-2、t-3 这四个名次。'
            '所以这一块回答的是「谁现在走的位置，像领涨板块两天前走的位置」——'
            '找潜在跟随者，不是找同涨同跌的同类（那要错开 0 场）。'),
    'similar': ('两个数一起看：平均名次差＝四个对齐点上名次绝对差的平均（单位就是名次，'
                '下面那列换成占母体的百分比好读）；秩相关＝两条名次序列的同形程度'
                '（本机没有 scipy，写侧用"先取秩再做 pearson"，与全仓 Spearman 的写法同一条）。'
                '排序以距离为主，距离并列时才看相关。秩相关算不出来的交空（—），不折成 0。'),
    'coverage': ('两个分母要分开读：母体＝当日参与排名的板块数；候选池＝这一窗里在母体出现过行的'
                 '板块并集减掉领涨板块自己（某板块可以这几天有行、当天没有行，所以池子会比当日母体大一个把）。'
                 '缺行的板块不参与比对、只进"因缺行未参与"的计数 —— 缺行不等于不像，是没法比；'
                 '把它当"不像"混进榜，榜就变成"在有数据的那批里挑最像"，那是另一件事。'),
    'landing': ('这一屏读的是加工链上算好并落库的那一批（步名 `qd_sector`，表 `qd.sector_follow`）：'
                '屏幕上的名次、距离、相关都不在请求时现算，所以同一场同一档谁打开都是同一份数。'
                '下方那句「本批算于 …」读的是入库时刻，不是你看页面的时刻。'),
}


def calibers() -> dict:
    return dict(CALIBERS)


def _plain(v):
    """Decimal/日期/numpy 标量 → JSON 可序列化；NaN → None（**绝不折成 0**，0 会被读成一个真名次）。

    ⚠️ `numpy` 那一支不是防御性代码：`pg.fetch_df` 走 pandas，`smallint`/`integer` 列到手是
    `numpy.int64`，它**不是** `int` 实例 —— 不折掉就会 `str()` 成 "2" 这种字符串名次，
    界面排序按字典序走（"10" 排在 "2" 前），而且前端类型声明会说谎。
    """
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if hasattr(v, "item") and not isinstance(v, (list, tuple, dict)):
        try:
            return _plain(v.item())          # numpy 标量 → 原生 int/float（NaN 交给下一支折成 None）
        except (TypeError, ValueError):
            pass
    if isinstance(v, float):
        return None if v != v else v
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    try:                                    # Decimal 不 import decimal 也能折（读侧只用一次）
        if v.__class__.__name__ == "Decimal":
            f = float(v)
            return None if f != f else f
    except Exception:                        # noqa: BLE001 —— 认不出来就交字符串，别炸整屏
        pass
    return str(v)


def _rows(sql: str, params=None) -> Optional[List[dict]]:
    df = pg.fetch_df(sql, params) if params is not None else pg.fetch_df(sql)
    if df is None:
        return None
    return [{k: _plain(v) for k, v in row.items()} for row in df.to_dict("records")]


def _json_col(v, default):
    """jsonb 经 psycopg2 + pandas 到手是字符串；写侧落库前已过 `allow_nan=False`，
    这里只负责解开，解不开就交默认值并把那一格留空 —— 不给一个看起来合理的假结构。"""
    if v is None or isinstance(v, (int, float)):
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _norm(date: str) -> str:
    base = str(date or '').strip().replace('-', '')
    return f'{base[:4]}-{base[4:6]}-{base[6:8]}' if len(base) == 8 else str(date or '').strip()


def _latest_landed(lag: int) -> Optional[str]:
    got = _rows(f"select max(trade_date)::text as d from {_TABLE} "
                f"where lag = %s and spec_version = %s", (lag, W.spec_version()))
    if not got:
        return None
    return got[0].get('d') or None


def _unavailable(base: str, lag: int, reason: str) -> dict:
    """不可用只交原因，**不带 leaders 键**（前端按 available 分支，多给一个空榜会被读成"今天没有相似板块"）。"""
    return {'available': False, 'date': base or None, 'lag': lag, 'reason': reason}


def _caveats(rows: List[dict], matched: int, skipped: int) -> List[dict]:
    """由落库事实组装：计数是写侧算好的，偏差是写侧记下的，这里只决定屏幕上念哪句。"""
    out = [{'code': 'lag_shape_match', 'label': '错开两场比对', 'note': CALIBERS['lag']},
           {'code': 'missing_day_row', 'label': '缺行不参与',
            'note': f'三张模板共比对 {matched} 个板块·人次，'
                    f'因这几天缺行未参与 {skipped} 个·人次'}]
    if any(c.get('rank') is None for r in rows
           for c in _json_col(r.get('trajectory'), [])):
        out.append({'code': 'partial_trajectory', 'label': '轨迹有缺口',
                    'note': '至少一个前三甲在某一天没有板块行情，那一格空着；'
                            '名次缺失的天不参与形状匹配（模板不齐的领涨板块直接说明"模板不完整"）'})
    seen: Dict[str, str] = {}
    for r in rows:
        for dev in _json_col(r.get('deviation'), []):
            code = (dev or {}).get('code')
            if code and code not in seen:
                seen[code] = dev.get('note') or ''
    for code in sorted(seen):
        out.append({'code': code, 'label': _DEV_LABEL.get(code, code), 'note': seen[code]})
    return out


def _build(rows: List[dict], base: str, lag: int) -> dict:
    rows = sorted(rows, key=lambda r: r['day_rank'])
    first = rows[0]
    leaders = []
    matched = skipped = 0
    for r in rows:
        block = _json_col(r.get('similar_block'), {})
        matched += int(block.get('matched') or 0)
        skipped += int(block.get('skipped_incomplete') or 0)
        leaders.append({
            'day_rank': r['day_rank'], 'sector_code': r['sector_code'],
            'sector_name': r['sector_name'], 'sector_type': r['sector_type'],
            'pct_change': r['pct_change'], 'universe_size': r['universe_size'],
            'trajectory': _json_col(r.get('trajectory'), []),
            'similar': block})
    ages = [r.get('calculated_at') for r in rows if r.get('calculated_at')]
    batch = max(ages) if ages else None
    age = None
    if batch:
        try:
            delta = dt.datetime.now().astimezone() - dt.datetime.fromisoformat(str(batch))
            age = max(int(delta.total_seconds()), 0)
        except (TypeError, ValueError):
            age = None
    return {
        'available': True,
        'date': base,
        'lag': lag,
        'top_n': first['top_n'],
        'trajectory_points': first['trajectory_points'],
        'shape_points': first['shape_points'],
        'k_similar': first['k_similar'],
        'universe_size': first['universe_size'],
        # 三个榜的比对数与跳过数由写侧逐行落好，这里只加总；前端只做渲染
        'match_total': matched,
        'skip_total': skipped,
        'sessions': _json_col(first.get('sessions'), []),
        'leaders': leaders,
        'calibers': calibers(),
        'caveats': _caveats(rows, matched, skipped),
        'source': first['source'],
        'spec_version': first['spec_version'],
        'cached': True,
        'cache_age_seconds': age,
        'generated_at': batch,
    }


def read(date: str = "", lag: int = DEFAULT_LAG) -> dict:
    """读 `qd.sector_follow` 的落库行，交出与前端约定的那一叠字段。

    `date` 缺省时用**这一档最新落了库的那一场**（不是板块日线的最新一场）：
    链还没跑今天时，屏幕说的是"这一批是哪天的、算于何时"，而不是假装今天有数。
    """
    lag = int(lag if lag in LAG_CHOICES else DEFAULT_LAG)
    base = _norm(date)
    if not base:
        base = _latest_landed(lag) or ''
    if not base:
        return _unavailable('', lag, '本地库读不到板块日线或 `qd.sector_follow` 里还没有任何批次 —— '
                                     '前三甲与相似板块是加工链 `qd_sector` 那一步算好落库的，'
                                     '先确认那一步跑过')
    probe = _rows(f"select to_regclass('{_TABLE}')::text as t")
    if probe is None:
        return _unavailable(base, lag, '本地库读不到 `qd.sector_follow`（库不通、超时或这一屏的取数失败），'
                                      '前三甲与相似板块不猜内容')
    if not probe or not probe[0].get('t'):
        return _unavailable(base, lag, f'`{_TABLE}` 还没建 —— 建表脚本 '
                                      'app/db/sql/52_qd_sector_follow.sql 没跑过，'
                                      '链上那一步写不进去，界面不拿现算冒充落库')
    rows = _rows(f"select {_COLUMNS} from {_TABLE} "
                 "where trade_date = %s::date and lag = %s and spec_version = %s",
                 (base, lag, W.spec_version()))
    if rows is None:
        return _unavailable(base, lag, '本地库读 `qd.sector_follow` 失败（超时或权限），'
                                      '这一屏不拿上一场顶上')
    if not rows:
        mine = _rows("select max(trade_date)::text as d from qd.sector_follow "
                     "where lag = %s and spec_version = %s", (lag, W.spec_version()))
        latest = (mine or [{}])[0].get('d')
        return _unavailable(base, lag,
                            f'`{_TABLE}` 里 {base} 这一场没有落库行'
                            + (f'（这一档最新一批是 {latest}）' if latest else '')
                            + ' —— 可能是那天的加工链没跑到 `qd_sector` 这一步，'
                              '也可能当天没有任何干净的板块行；两种都不是"今天没有相似板块"')
    return _build(rows, base, lag)
