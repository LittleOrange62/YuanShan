# -*- coding: utf-8 -*-
"""c22 · 用历史备份回填 `reference_data.watchlist_member_snapshot`。

**为什么要回填**：这张表唯一的用途是把"自选组趋势"从**以今日名单回算**（未来函数）里
区分出来。每日快照只能攒，补不出 21 场，所以能合法回填的每一天都要用。
可搬的副本在本项目 `var/backup/watchlist_*.json`（旧项目每次全量导出时留下的）。

**跑法**（默认只读，不动库）：
    python -m ingest.jobs.c22_watchlist_snapshot_backfill            # 看计划
    python -m ingest.jobs.c22_watchlist_snapshot_backfill --probe    # 真写再回滚，只验通路
    python -m ingest.jobs.c22_watchlist_snapshot_backfill --yes      # 落库

**三个坑（都是 2026-09-26 实测，与派活时给的描述有两处不一致，以实测为准）**：
1. **`group_id` 完全不可移植**。备份里的 id 与库里现在的 id **一个都对不上**，而且同一个组
   在备份之间自己就在漂（`犇犇熊`：249→544→588→632，库里现在是 705）—— 旧项目每次导出都在
   重新发号。**照搬 json 的 id 会把同一个组在 5 天里当成 5 个组，序列当场碎裂**；
   更坏的是那个小号段（2、3、5…）会撞上库里另一个不相干组的 id，把两个组的历史并成一条
   —— 界面完全看不出来。所以这里**只按组名认组**，id 取库里现在的；
   库里已经没有这个名字的组（当年存在、后来删了）**整组跳过并报名**，不猜、不复用旧号。
   ⚠️ 这与"必须按 group_id 键"那条裁定在回填这一段冲突，理由就是上面这段实测；
   每日增量（`duanxian/watchlist_sync.write_snapshot`）不受影响，它的 id 直接来自库。
2. **schema 有两种，而且错位的那一份不是最早那份**。0830~0916 用 `ts_code`，
   **0922_224527（122 字节）是 `groups: []` 的空导出**，同日 22:45 那份 185KB 才是全量。
   空导出**绝不许当成"那天大家都没票"写进去** —— 那会造出"41 组全变"的假漂移。
   这里 `groups=0` 一律判为**没有数据**，不参与回填，并在输出里单独报出来。
3. **不等间距、有洞**：能验口径与量级，凑不出连续 21 场。所以回填完还要说清
   "现在有几天、离 21 场还差几天"，别让读侧以为窗口快满了。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import engine                                    # noqa: E402
from app.config import BASE_DIR                              # noqa: E402

TABLE = "reference_data.watchlist_member_snapshot"
BACKUP_DIR = BASE_DIR / "var" / "backup"
PATTERN = "watchlist_*.json"
ROLE = "etl"          # ft_etl：实测对这张表 INSERT/UPDATE/DELETE 全 True、无触发器


def _date_of(path: Path) -> dt.date | None:
    """文件名 `watchlist_YYYYMMDD_HHMMSS.json` → 日期。对不上格式的宁可认不出来。"""
    parts = path.stem.split("_")
    if len(parts) < 2 or len(parts[1]) != 8 or not parts[1].isdigit():
        return None
    return dt.datetime.strptime(parts[1], "%Y%m%d").date()


def discover(backup_dir: Path = BACKUP_DIR) -> dict:
    """把备份按归属日归堆，同日多份时挑**有内容**的那份。

    返回 `{"by_date": {date: [path,...]}, "kept": {date: path}, "rejected": {date: [原因,...]}}`。
    """
    by_date: dict[dt.date, list[Path]] = {}
    for p in sorted(backup_dir.glob(PATTERN)):
        d = _date_of(p)
        if d is None:
            by_date.setdefault(None, []).append(p)       # type: ignore[arg-type]
            continue
        by_date.setdefault(d, []).append(p)

    kept: dict[dt.date, Path] = {}
    rejected: dict[dt.date, list[str]] = {}
    for d, paths in sorted(by_date.items(), key=lambda kv: (kv[0] is None, kv[0])):
        if d is None:
            rejected.setdefault(dt.date.min, []).append(
                "文件名对不上 watchlist_YYYYMMDD_HHMMSS：" + ", ".join(p.name for p in paths))
            continue
        good, empty = [], []
        for p in paths:
            try:
                n = len(json.loads(p.read_text(encoding="utf-8")).get("groups") or [])
            except Exception as exc:                    # noqa: BLE001  坏文件要出声，不能当天没数据
                empty.append("%s 解析失败 %s: %s" % (p.name, type(exc).__name__, str(exc)[:60]))
                continue
            (good if n else empty).append(p.name if n else p)
        good_paths = [p for p in paths if p.name in good]
        if not good_paths:
            rejected[d] = ["没有一份有成员（空导出/坏文件）：" + ", ".join(
                x if isinstance(x, str) else x.name for x in empty)]
            continue
        if len(good_paths) > 1:
            rejected[d] = ["同一天有多份非空导出，选哪份是猜测，停下来人工确认：" +
                           ", ".join(p.name for p in good_paths)]
            continue
        kept[d] = good_paths[0]
        if empty:
            rejected.setdefault(d, []).append("同日还有一对被弃掉：" + ", ".join(
                x if isinstance(x, str) else x.name for x in empty))
    return {"by_date": {str(k): [p.name for p in v] for k, v in by_date.items()},
            "kept": kept, "rejected": rejected}


def load_snapshot(path: Path) -> dict:
    """读一份导出。**代码键两种都认**（0830~0916 是 `ts_code`，0922 是 `security_code`）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    groups = raw.get("groups") or []
    out, no_code = [], 0
    for g in groups:
        codes = []
        for s in (g.get("stocks") or []):
            code = s.get("security_code") or s.get("ts_code")
            if code:
                codes.append(code)
            else:
                no_code += 1
        out.append({"group_id": g.get("group_id"), "name": g.get("name"), "codes": codes})
    return {"exported_at": raw.get("exported_at"), "groups": out,
            "stocks_without_code": no_code, "source": raw.get("source")}


def open_dates(dates: list[dt.date]) -> dict[dt.date, bool]:
    """查交易日历。日历里没有这一天 = **按非交易日处理**（宁可不写，不猜开市）。"""
    if not dates:
        return {}
    rows = engine.query(
        "select calendar_date::date d, is_open from reference_data.trade_calendar "
        "where calendar_date = any(%s::date[])", (dates,), role="reader")
    got = {r["d"]: bool(r["is_open"]) for r in rows}
    return {d: got.get(d, False) for d in dates}


def build_rows(day: dt.date, snap: dict, db_ids: dict[str, int]) -> tuple[list[tuple], dict]:
    """把一份导出摊成 `(trade_date, group_id, group_name, security_code, captured_at)`。

    `captured_at` 用备份自己的 `exported_at`（真实采集时刻），**不用 now()** ——
    否则事后看不出哪几天是回填的。
    """
    cap = snap.get("exported_at")
    try:
        captured = dt.datetime.fromisoformat(str(cap).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        captured = None
    rows: list[tuple] = []
    skipped_groups: list[str] = []
    for g in snap["groups"]:
        gid = db_ids.get(g["name"])
        if gid is None:
            if g["codes"]:
                skipped_groups.append("%s(%d 只)" % (g["name"], len(g["codes"])))
            continue                       # 库里已无此组：没有可对齐的 id，整组不写并报名
        for code in dict.fromkeys(g["codes"]):
            rows.append((day, gid, g["name"], code, captured))
    return rows, {"groups_total": len(snap["groups"]), "groups_skipped": skipped_groups,
                  "stocks_without_code": snap["stocks_without_code"],
                  "captured_at": str(cap), "captured_parsed": captured is not None,
                  "source": snap["source"]}


def existing_days() -> dict[dt.date, int]:
    rows = engine.query("select trade_date::date d, count(*) n from %s group by 1" % TABLE,
                        role="reader")
    return {r["d"]: int(r["n"]) for r in rows}


def current_group_ids() -> dict[str, int]:
    rows = engine.query("select name, group_id from config.watchlist_group", role="reader")
    return {r["name"]: r["group_id"] for r in rows}


def plan(backup_dir: Path = BACKUP_DIR) -> dict:
    disc = discover(backup_dir)
    ids = current_group_ids()
    days = open_dates(sorted(disc["kept"]))
    have = existing_days()
    per_day = []
    for d, path in sorted(disc["kept"].items()):
        snap = load_snapshot(path)
        rows, meta = build_rows(d, snap, ids)
        why_not = None
        if not days.get(d):
            why_not = "非交易日（日历 is_open=false 或查无此日）"
        elif d in have:
            why_not = "库里已有 %d 行，不覆盖（要覆盖加 --force）" % have[d]
        per_day.append({"date": d, "file": path.name, "rows": len(rows),
                        "skip_reason": why_not, "meta": meta, "_rows": rows})
    return {"per_day": per_day, "rejected": {str(k): v for k, v in disc["rejected"].items()},
            "existing": {str(k): v for k, v in have.items()},
            "table": TABLE}


def write_day(rows: list[tuple], *, probe: bool) -> int:
    """当日整批替换（删+插同一事务）。全部同一天，所以一次 delete + 一次 insert。"""
    day = rows[0][0]
    with engine.conn(ROLE) as c, c.cursor() as cur:
        cur.execute("delete from %s where trade_date = %%s" % TABLE, (day,))
        cur.executemany(
            "insert into %s (trade_date, group_id, group_name, security_code, captured_at)"
            " values (%%s, %%s, %%s, %%s, %%s)" % TABLE, rows)
        if probe:
            c.rollback()
    return len(rows)


def verify(pick_name: str = "犇犇熊") -> int:
    """可重跑的抽查：从备份 json 里取一个组，逐日与库里的成员比，要求一模一样。

    用法：`python -m ingest.jobs.c22_watchlist_snapshot_backfill --verify [组名]`
    """
    disc = discover()
    days = sorted(disc["kept"])
    src: dict[dt.date, set[str]] = {}
    for d in days:
        snap = load_snapshot(disc["kept"][d])
        hit = next((g for g in snap["groups"] if g["name"] == pick_name), None)
        if hit:
            src[d] = set(hit["codes"])
    db = engine.query(
        "select trade_date::text d, array_agg(security_code order by security_code) codes "
        "from %s where group_name = %%s group by 1" % TABLE, (pick_name,), role="reader")
    print("组「%s」：源备份有 %d 天，库里查到 %d 天" % (pick_name, len(src), len(db)))
    bad = 0
    for r in sorted(db, key=lambda x: x["d"]):
        d = dt.date.fromisoformat(r["d"])
        want = src.get(d)
        got = set(r["codes"])
        if want is None:
            print("  %s 库里有 %d 只，但备份里没有这一天 → 来源不明" % (r["d"], len(got)))
            bad += 1
            continue
        flag = "✓" if want == got else "✗"
        print("  %s %s 源 %d 只 / 库 %d 只%s" % (r["d"], flag, len(want), len(got),
              "" if want == got else "  差: 只在源 %s 只在库 %s"
              % (sorted(want - got)[:5], sorted(got - want)[:5])))
        bad += want != got
    print("→ " + ("逐日一致" if not bad else "%d 天不一致" % bad))
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true", help="真写库（默认只出计划）")
    ap.add_argument("--probe", action="store_true", help="真跑 SQL 再回滚，只验通路")
    ap.add_argument("--force", action="store_true", help="允许覆盖库里已有的一天")
    ap.add_argument("--verify", nargs="?", const="犇犇熊", metavar="组名",
                    help="抽查一组逐日成员与备份是否一致")
    a = ap.parse_args(argv)
    if a.verify:
        return verify(a.verify)

    pl = plan()
    print("目标表：%s" % pl["table"])
    print("库里已有的天：%s" % (pl["existing"] or "（无）"))
    to_write = []
    for item in pl["per_day"]:
        line = "  %s %s → %d 行" % (item["date"], item["file"], item["rows"])
        m = item["meta"]
        line += "（组 %d，跳过 %s，无代码的股票 %d，captured_at=%s%s）" % (
            m["groups_total"], m["groups_skipped"] or "无", m["stocks_without_code"],
            m["captured_at"], "" if m["captured_parsed"] else " ⚠️没能解析成时刻")
        print(line + ("" if not item["skip_reason"] else "  ⛔ " + item["skip_reason"]))
        if not item["skip_reason"]:
            to_write.append(item)
        if item["skip_reason"] and "已有" in (item["skip_reason"] or "") and a.force:
            to_write.append(item)
    for d, why in pl["rejected"].items():
        print("  %s ⛔ %s" % (d, "; ".join(why)))
    print("→ 本轮可回填 %d 天 / 共 %d 行" % (len(to_write), sum(i["rows"] for i in to_write)))
    if not (a.yes or a.probe):
        print("→ 只出计划，未动库。要真写加 --yes（先 --probe 验通路）")
        return 0
    n = 0
    for item in to_write:
        n += write_day(item["_rows"], probe=a.probe)
        print("   %s %s %d 行" % (item["date"], "回滚（probe）" if a.probe else "已写",
                                  item["rows"]))
    have = existing_days()
    print("→ %s %d 行。现在库里共 %d 天：%s"
          % ("probe 已回滚（不留痕）" if a.probe else "已回填", n, len(have),
             "、".join("%s(%d)" % (k, v) for k, v in sorted(have.items()))))
    print("  ⚠️ 21 场窗口还差 %d 场，且回填这几天不等间距（只能验口径与量级，"
          "不能当连续窗口用）" % max(0, 21 - len(have)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
