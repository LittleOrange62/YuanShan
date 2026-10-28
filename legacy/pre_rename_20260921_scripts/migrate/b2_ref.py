# -*- coding: utf-8 -*-
"""
B2 · 迁移 ref 域：证券、交易日历、板块。

数据来源与体检结论：
  tq_core.security_info   9,468 行 —— 只有 ts_code/symbol/market/asset_type 有值，
                          name / list_date / total_shares / float_shares / industry
                          **全部为 NULL**。所以名称必须另找来源。
  T0002\\hq_cache\\*.tnf    52,208 条代码+名称 —— 名称与 ST 标记的唯一可用来源。
  tq_core.sector_info     1,417 个板块
  tq_core.sector_member   128,590 条成员关系（含 164 个 effective_date 版本）
  tq_daily_*.stock_daily  交易日历的事实来源（1,138 个交易日，20210802 起）

板块（board）不从旧库读，按代码前缀重新判定。理由：board 决定涨跌停幅度
（主板 10% / 创业板 20% / 科创板 20% / 北交所 30% / ST 主板 5%），旧库
一刀切 pre_close*1.1，连指数都算涨跌停，不能沿用。

股本仍然缺失，B2 不编造。缺股本 → 换手率算不出来，这一点写进 ops.quality_result
留痕，不静默放过。

用法：
    python scripts\\migrate\\b2_ref.py            # 全量
    python scripts\\migrate\\b2_ref.py --dry-run  # 只看统计不写库
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402
from app.ingest import instrument as ins  # noqa: E402
from app.ingest import tdx_tnf  # noqa: E402

CORE_DB = config.LEGACY_DATA / 'tq_core.db'
DAILY_YEARS = (2021, 2022, 2023, 2024, 2025, 2026)

# 旧库的 sector_type 是英文，统一成中文，与 config.field_spec 的口径一致
SECTOR_TYPE_MAP = {
    'index': '行业',
    'concept': '概念',
    'custom': '自定义',
    'style': '风格',
    'area': '地区',
}

# 旧库 asset_type 取值 stock/bond/sector/fund/index，
# 本系统只认 stock|index|etf|sector|other
ASSET_TYPE_MAP = {
    'stock': 'stock',
    'index': 'index',
    'sector': 'sector',
    'fund': 'etf',
    'bond': 'other',
}

# app.ingest.instrument 的归类结果 → 本系统的 asset_type
KIND_TO_ASSET_TYPE = {
    'stock': 'stock',
    'index': 'index',
    'sector': 'sector',
    'fund': 'etf',
    'bond': 'other',
    'bshare': 'other',      # 以美元/港币计价，口径与 A 股不兼容
    'pseudo': 'other',      # 999997/999998/999999 通达信内部伪代码
}


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=10)
    c.execute('PRAGMA busy_timeout=10000')
    return c


def to_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = str(s).strip().replace('-', '')
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def pinyin_of(name: str) -> tuple[str | None, str | None]:
    """全拼与首字母。检索用，失败不致命。"""
    if not name:
        return None, None
    try:
        from pypinyin import Style, lazy_pinyin
        full = ''.join(lazy_pinyin(name, style=Style.NORMAL))
        abbr = ''.join(lazy_pinyin(name, style=Style.FIRST_LETTER))
        return full or None, abbr or None
    except Exception:  # noqa: BLE001
        return None, None


# ------------------------------------------------------------------ 证券

def build_securities(names: dict[str, tdx_tnf.SecurityName]) -> tuple[list[tuple], dict]:
    c = ro(CORE_DB)
    try:
        base = c.execute(
            'SELECT ts_code, symbol, market, asset_type, list_date, delist_date '
            'FROM security_info').fetchall()
        # stock_info 里若有股本就用上（实测为空，但代码不写死这个前提）
        shares = {
            r[0]: (r[1], r[2]) for r in c.execute(
                'SELECT ts_code, total_shares, float_shares FROM stock_info '
                'WHERE total_shares IS NOT NULL OR float_shares IS NOT NULL')}
        idx_names = {r[0]: r[1] for r in c.execute(
            'SELECT ts_code, name FROM index_info WHERE name IS NOT NULL')}
    finally:
        c.close()

    rows: list[tuple] = []
    stat = {'name_from_tnf': 0, 'name_from_index_info': 0, 'no_name': 0,
            'st': 0, 'no_board': 0, 'has_share': 0}

    for ts_code, symbol, market, old_type, list_date, delist_date in base:
        market = (market or '').upper()
        symbol = symbol or ts_code.split('.')[0]

        tnf = names.get(ts_code)
        if tnf and tnf.name:
            name, is_st = tnf.name, tnf.is_st
            stat['name_from_tnf'] += 1
        elif ts_code in idx_names:
            name, is_st = idx_names[ts_code], False
            stat['name_from_index_info'] += 1
        else:
            name, is_st = None, False
            stat['no_name'] += 1
        if is_st:
            stat['st'] += 1

        # **代码前缀优先，旧库的 asset_type 只当兜底。** 第一版反了：
        # `ASSET_TYPE_MAP.get(old_type, guess(...))` 先信旧库，结果旧库把
        # 91 个 B 股（900xxx/200xxx）、国债逆回购（204xxx）和通达信伪代码
        # （999997/999998/999999）统统标成 stock，它们就带着 asset_type='stock'
        # 落进了 ref.security —— 而 load_panel 正是按这个字段筛个股面板的。
        # 交易所代码段是硬事实，旧库那个字段是别人的推断。
        kind = ins.classify(ts_code)
        asset_type = KIND_TO_ASSET_TYPE.get(kind)
        if asset_type is None:      # unknown，才轮到旧库的说法
            asset_type = ASSET_TYPE_MAP.get(
                old_type, sec_repo.guess_asset_type(symbol, market))
        board = sec_repo.guess_board(symbol, market) if asset_type == 'stock' else None
        if asset_type == 'stock' and not board:
            stat['no_board'] += 1
        stat.setdefault('kind', {})
        stat['kind'][kind] = stat['kind'].get(kind, 0) + 1

        full, abbr = pinyin_of(name) if name else (None, None)
        total, float_ = shares.get(ts_code, (None, None))
        if total or float_:
            stat['has_share'] += 1

        rows.append((
            ts_code, symbol, market, name, full, abbr, board, asset_type, is_st,
            to_date(list_date),
            int(total) if total else None,
            int(float_) if float_ else None,
            to_date(delist_date) is None,   # 没有退市日就算在市
        ))
    return rows, stat


# ------------------------------------------------------------------ 交易日历

def collect_trade_dates() -> list[dt.date]:
    """交易日以日线实际有数据的日期为准，不用 weekday() 猜。"""
    dates: set[dt.date] = set()
    for y in DAILY_YEARS:
        p = config.LEGACY_DATA / f'tq_daily_{y}.db'
        if not p.exists():
            continue
        c = ro(p)
        try:
            for (s,) in c.execute('SELECT DISTINCT trade_date FROM stock_daily'):
                d = to_date(s)
                if d:
                    dates.add(d)
        except sqlite3.Error as exc:
            print('  [warn] %s 读取交易日失败：%s' % (p.name, exc))
        finally:
            c.close()
    return sorted(dates)


# ------------------------------------------------------------------ 板块

def build_sectors() -> tuple[list[tuple], list[tuple], dict]:
    c = ro(CORE_DB)
    try:
        info = c.execute(
            'SELECT sector_code, sector_name, sector_type, parent_sector_code, '
            'is_active FROM sector_info').fetchall()
        # 一个 (sector_code, ts_code) 可能有多个 effective_date，取最新那条的
        # effective_date 当 in_date；out_date 有值的算已剔除
        member = c.execute("""
            SELECT sector_code, member_ts_code,
                   MAX(effective_date) AS eff,
                   MAX(COALESCE(out_date, '')) AS out_d
              FROM sector_member
             GROUP BY sector_code, member_ts_code
        """).fetchall()
    finally:
        c.close()

    info_rows = [
        (code, name or code, SECTOR_TYPE_MAP.get(stype, stype), None,
         parent or None)
        for code, name, stype, parent, _active in info
    ]
    valid = {r[0] for r in info_rows}
    member_rows = [
        (code, ts, to_date(eff))
        for code, ts, eff, _out in member if code in valid
    ]
    stat = {
        'sector': len(info_rows),
        'member': len(member_rows),
        'member_dropped_orphan': len(member) - len(member_rows),
        'inactive': sum(1 for r in info if not r[4]),
    }
    return info_rows, member_rows, stat


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not CORE_DB.exists():
        print('缺少 %s' % CORE_DB)
        return 1

    print('=== 读 .tnf 名称表 ===')
    names = tdx_tnf.name_map()
    print('  %d 条代码+名称，其中 ST %d 只'
          % (len(names), sum(1 for v in names.values() if v.is_st)))

    print('\n=== 组装 ref.security ===')
    sec_rows, sec_stat = build_securities(names)
    print('  待写 %d 行' % len(sec_rows))
    for k, v in sec_stat.items():
        print('    %-22s %s' % (k, v))

    print('\n=== 收集交易日历 ===')
    dates = collect_trade_dates()
    print('  %d 个交易日  %s .. %s'
          % (len(dates), dates[0] if dates else '-', dates[-1] if dates else '-'))

    print('\n=== 组装板块 ===')
    info_rows, member_rows, sec2_stat = build_sectors()
    for k, v in sec2_stat.items():
        print('    %-22s %s' % (k, v))

    if args.dry_run:
        print('\n--dry-run，未写库')
        return 0

    batch_id = ops_repo.begin_batch(
        'legacy_sqlite', 'ref', source_path=str(CORE_DB))
    try:
        print('\n=== 写库（batch %d）===' % batch_id)
        n = sec_repo.upsert_securities(sec_rows)
        print('  ref.security        %s' % n)
        n = sec_repo.upsert_calendar(dates)
        print('  ref.trade_calendar  %s 个交易日（含补全的非交易日）' % n)
        n = sec_repo.upsert_sector_info(info_rows)
        print('  ref.sector_info     %s' % n)
        n = sec_repo.upsert_sector_members(member_rows)
        print('  ref.sector_member   %s' % n)
        ops_repo.end_batch(
            batch_id, status='ok',
            row_count=len(sec_rows) + len(info_rows) + len(member_rows))
    except Exception as exc:
        ops_repo.end_batch(batch_id, status='failed', message=str(exc))
        raise

    print('\n=== 验收 ===')
    checks = [
        ('ref.security 总数', 'SELECT count(*) FROM ref.security'),
        ('  其中个股', "SELECT count(*) FROM ref.security WHERE asset_type='stock'"),
        ('  有名称', 'SELECT count(*) FROM ref.security WHERE name IS NOT NULL'),
        ('  ST', 'SELECT count(*) FROM ref.security WHERE is_st'),
        ('  个股缺 board', "SELECT count(*) FROM ref.security "
                           "WHERE asset_type='stock' AND board IS NULL"),
        ('  缺股本', "SELECT count(*) FROM ref.security "
                     "WHERE asset_type='stock' AND total_share IS NULL"),
        ('board 分布', "SELECT string_agg(board||':'||n, ' ') FROM "
                       "(SELECT board, count(*) n FROM ref.security "
                       "WHERE asset_type='stock' GROUP BY board ORDER BY board) t"),
        ('交易日数', 'SELECT count(*) FROM ref.trade_calendar WHERE is_open'),
        ('日历总天数', 'SELECT count(*) FROM ref.trade_calendar'),
        ('最新交易日', 'SELECT max(cal_date) FROM ref.trade_calendar WHERE is_open'),
        ('prev/next 已填', 'SELECT count(*) FROM ref.trade_calendar '
                           'WHERE is_open AND prev_open IS NOT NULL'),
        ('ref.sector_info', 'SELECT count(*) FROM ref.sector_info'),
        ('ref.sector_member', 'SELECT count(*) FROM ref.sector_member'),
        ('板块类型分布', "SELECT string_agg(sector_type||':'||n, ' ') FROM "
                         "(SELECT sector_type, count(*) n FROM ref.sector_info "
                         "GROUP BY sector_type ORDER BY n DESC) t"),
    ]
    for label, sql in checks:
        print('  %-20s %s' % (label, engine.scalar(sql)))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
