"""自选分组同步：同花顺 ini → `config.watchlist_group/stock`（全量覆盖，带守卫与快照）。

## 为什么要在 YuanShan 里重写一遍

FinanceTool 的 `c14_ths_watchlist.py` 从来没被调度器拉起过（`scheduler_enabled=false`、
计划任务 `FinanceTool-DailyCloseSync` `LastTaskResult=267011` 从未执行、心跳 `fired:0`）。
后果不是"报错"，是**库里的自选悄悄停在旧状态**：实测 `stockblock.ini` 的 mtime 是
09-24 10:42，而库里写入时间是 09-22 22:45 —— 用户新加的 7 只没进来、已删的 3 只还留着。
**最坑的陈旧就是零报错的陈旧**，所以这个模块把"看得见"当成一等需求。

## 四条不许打折的规矩

1. **覆盖前先快照**到 `ys.watchlist_backup`（带 `captured_at`），且快照与覆盖**同一事务**。
   同花顺那个 ini 会被客户端自己覆盖，`config.watchlist_*` 是用户手改内容的唯一副本。
2. **一张表不许两个写入者**。开工前查 `config.job.sync_ths_watchlist`：
   登记为 enabled **且** FT 调度器确实活着（心跳 `fired>0` 且新鲜）→ 直接拒写并报错，
   绝不默默双写。只 enabled 但调度器从没跑 → 允许接管，但把"请把它置 false"写进 `reason`。
3. **源里没有的列不许被覆盖抹掉**：同花顺没有"重点关注"概念 → `is_focus` 按组名**接续**，
   绝不因为"ini 里没这个字段"就统一刷成 false（那会把用户勾过的东西清零）；
   组的 `note` 同理按名保留。新组的 note 只打一个来源标记 `同花顺导入`。
4. **`watchlist_stock.group_id` 是 `ON DELETE CASCADE`** → 删组等于连坐删成员。
   所以只在"组确实不在 ini 里"时删组，同名组一律保留原 `group_id` 只改成员。

`last_ok_at = null`（**从未跑过**）与"跑了但零变化"必须是两种显示 —— 状态文件里分开存，
`doctor` 与 `/api/jobs` 都按这个语义读。
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from .paths import data_path
from .util import atomic_write_json

from ingest import ths_local        # 解析层保持不依赖数据库（见 ingest/__init__.py）

logger = logging.getLogger(__name__)

JOB_KEY = "sync_ths_watchlist"
SOURCE_TABLES = "config.watchlist_group + config.watchlist_stock"
LOCK_NAME = "watchlist_sync.lock"
STATE_FILE = "ingest_state.json"
LOCK_STALE_SECONDS = 30 * 60          # 超过这个时长且 pid 已不在 → 视为崩溃残留，可接管


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


# ---------------------------------------------------------------- 凭据与连接

def etl_ready() -> bool:
    from . import pg as _pg
    _pg._load_env_once()          # 全项目只有 pg.py 那一个 .env 读取口，不再各处 open
    return bool(os.environ.get("PG_ETL_USER") and os.environ.get("PG_ETL_PASSWORD"))


def _connect_etl():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("PG_PORT", "5432")),
        dbname=os.environ.get("PG_DB", "financetool"),
        user=os.environ["PG_ETL_USER"], password=os.environ["PG_ETL_PASSWORD"],
        connect_timeout=5, application_name="ys-watchlist-sync")


# ---------------------------------------------------------------- 锁

def _lock_path() -> Path:
    d = Path(data_path("locks"))
    d.mkdir(parents=True, exist_ok=True)
    return d / LOCK_NAME


def acquire_lock() -> tuple[Optional[Path], str]:
    """`O_EXCL` 抢锁。抢到返回 (路径, "")；抢不到返回 (None, 原因)。

    只靠进程内锁不够：uvicorn 有 reload/多 worker，两个进程同时做"全量覆盖"
    会把对方的成员删掉。锁文件里写 pid + 时间戳，崩溃留下的陈旧锁可以被接管，
    否则一次 Ctrl-C 就把同步永久锁死（那又是一种"看不出来的坏"）。
    """
    path = _lock_path()
    payload = "%d %s\n" % (os.getpid(), _now().isoformat(timespec="seconds"))
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
            pid, stamp = (raw.split() + ["", ""])[:2]
            age = time.time() - path.stat().st_mtime
            alive = _pid_alive(int(pid)) if pid.isdigit() else False
            if alive or age < LOCK_STALE_SECONDS:
                return None, "另一个同步在跑（pid=%s，%d 秒前）" % (pid or "?", int(age))
            logger.warning("接管陈旧锁 %s（pid=%s 已不在，%.0f 秒前）", path, pid, age)
        except OSError as exc:
            return None, "锁文件状态读不出：%s" % exc
        try:
            path.unlink()
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as exc:
            return None, "陈旧锁接管失败：%s" % exc
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return path, ""


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes
        # 0 句柄 + SYNCHRONIZE 权限：进程存在就能开到手柄；Windows 下唯一不弹框的探法
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:      # noqa: BLE001  非 Windows 或 API 不可用 → 保守当作活着
        return True


def release_lock(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- 读现状

def read_db(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""select g.group_id, g.name, g.owner, g.is_focus, g.display_order, g.note,
                              s.security_code, s.cost, s.added_at, s.note as stock_note
                       from config.watchlist_group g
                       left join config.watchlist_stock s using (group_id)
                       order by g.display_order, g.name, s.security_code""")
        rows = cur.fetchall()
    groups: dict[str, dict] = {}
    for gid, name, owner, is_focus, order, note, code, cost, added, snote in rows:
        g = groups.setdefault(name, {"group_id": gid, "owner": owner, "is_focus": bool(is_focus),
                                     "display_order": order, "note": note, "members": {}})
        if code:
            g["members"][code] = {"cost": float(cost) if cost is not None else None,
                                  "added": added, "note": snote}
    return groups


def job_state(conn) -> tuple[bool, bool, str]:
    """(FT 作业是否 enabled, FT 调度器是否真的活着, 说明)。"""
    with conn.cursor() as cur:
        cur.execute("select enabled from config.job where job_key = %s", (JOB_KEY,))
        row = cur.fetchone()
        enabled = bool(row and row[0])
        # 心跳表的列名以库为准，先探再取（认不出就别猜，宁可按"活着"处理）
        cur.execute("""select column_name from information_schema.columns
                       where table_schema='operations' and table_name='scheduler_heartbeat'""")
        cols = [r[0] for r in cur.fetchall()]
        fired_col = next((c for c in cols if "fire" in c and c != "last_fire_at"), None)
        at_col = next((c for c in cols if "at" in c or "time" in c), None)
        if not (enabled and fired_col and at_col):
            return enabled, False, "FT 侧未登记该作业或读不到心跳列（%s）" % ",".join(cols[:6])
        cur.execute("select coalesce(sum(%s),0), max(%s) from operations.scheduler_heartbeat"
                    % (fired_col, at_col))
        fired, last = cur.fetchone()
        alive = bool(fired) and last is not None and (
            _now() - last.astimezone()).total_seconds() < 36 * 3600
        return enabled, alive, "心跳 fired=%s last=%s" % (fired, last)


# ---------------------------------------------------------------- 计划

def build_plan(ini: dict, db: dict, mode: str = "full") -> dict:
    """对比 ini 与库现状。**不产生任何 SQL**，纯计算，便于打测试。

    `mode`（用户 2026-10-07 要的一档，默认值只住 `duanxian/jobs.py::WATCHLIST_DEFAULTS`）：
      · `full` —— 以同花顺 ini 为准：ini 里没了的组/成员进 `drop_*`，写侧真的删；
      · `append` —— **只加不删**：那些 ini 里已经没有的东西进 `kept_absent_*`，
        计数照交、一条都不进 `drop_*`。这不是"更安全"，是另一种口径 ——
        库里会慢慢积累"同花顺已经删掉但这里还在"的票，所以这两条必须都上报告与页面，
        不能只印一个"同步成功"。
    """
    ini_groups = {g["name"]: g for g in ini["groups"]}
    append = str(mode or "full").strip().lower() == "append"
    plan = {"new_groups": [], "drop_groups": [], "keep_groups": [],
            "add_members": 0, "drop_members": 0, "changed_groups": [],
            "focus_kept": [], "notes_kept": 0, "orphan_note": [],
            "kept_absent_groups": [], "kept_absent_members": 0}
    for name, g in ini_groups.items():
        if name not in db:
            plan["new_groups"].append({"name": name, "count": len(g["members"])})
            # 新组/删掉的组的成员也要计进总数：不然报告说"+8"而实际写下去是 +8+新组条数，
            # 差的那点正好是"事后对不上账"的来源
            plan["add_members"] += len(g["members"])
            continue
        cur = db[name]
        plan["keep_groups"].append(name)
        if cur["is_focus"]:
            plan["focus_kept"].append(name)
        if cur["note"]:
            plan["notes_kept"] += 1
        codes = set(g["members"])
        have = set(cur["members"])
        if codes != have:
            plan["changed_groups"].append({"name": name, "add": len(codes - have),
                                           "drop": 0 if append else len(have - codes)})
        plan["add_members"] += len(codes - have)
        if append:
            plan["kept_absent_members"] += len(have - codes)
        else:
            plan["drop_members"] += len(have - codes)
    for name in db:
        if name not in ini_groups:
            if append:
                plan["kept_absent_groups"].append({"name": name,
                                                   "count": len(db[name]["members"])})
                plan["kept_absent_members"] += len(db[name]["members"])
                continue
            plan["drop_groups"].append({"name": name, "count": len(db[name]["members"])})
            plan["drop_members"] += len(db[name]["members"])     # 随组 CASCADE 一起没
    plan["members_total"] = sum(len(g["members"]) for g in ini["groups"])
    plan["mode"] = "append" if append else "full"
    return plan


# ---------------------------------------------------------------- 写入

def backup_current(conn, captured_at) -> int:
    """把现状整份灌进 `ys.watchlist_backup`。与后续覆盖**同一个事务**。

    名字从 `snapshot` 改过来是因为现在有两件"快照"：这个是**覆盖前的库内容备份**
    （用户手改的唯一副本，丢了不可恢复），另一个是 `write_snapshot`（成员历史，给趋势用）。
    两者混叫早晚会写反。
    """
    with conn.cursor() as cur:
        cur.execute("""insert into ys.watchlist_backup
            (captured_at, source_table, group_id, security_code, name,
             is_focus, display_order, note, added_at, cost, payload)
            select %s, 'config.watchlist_group', g.group_id, null, g.name,
                   g.is_focus, g.display_order, g.note, null, null,
                   jsonb_build_object('owner', g.owner)
            from config.watchlist_group g""", (captured_at,))
        n_grp = cur.rowcount
        cur.execute("""insert into ys.watchlist_backup
            (captured_at, source_table, group_id, security_code, name,
             is_focus, display_order, note, added_at, cost, payload)
            select %s, 'config.watchlist_stock', s.group_id, s.security_code, g.name,
                   g.is_focus, g.display_order, s.note, s.added_at, s.cost,
                   jsonb_build_object('owner', g.owner)
            from config.watchlist_stock s join config.watchlist_group g using (group_id)""",
                    (captured_at,))
        return n_grp + cur.rowcount


def apply_plan(conn, ini: dict, db: dict, owner: str, mode: str = "full") -> dict:
    plan = build_plan(ini, db, mode)
    append = plan["mode"] == "append"
    ini_groups = {g["name"]: g for g in ini["groups"]}
    quote = ini.get("quote") or {}
    out = {"groups_new": 0, "groups_dropped": 0, "members_added": 0, "members_dropped": 0,
           "sync_mode": plan["mode"], "groups_kept_absent": 0, "members_kept_absent": 0}
    with conn.cursor() as cur:
        for item in plan["drop_groups"]:
            cur.execute("delete from config.watchlist_group where name = %s and owner = %s",
                        (item["name"], owner))          # 成员靠 ON DELETE CASCADE 连坐
            out["groups_dropped"] += cur.rowcount
        for g in ini["groups"]:
            name = g["name"]
            existing = db.get(name)
            if existing is None:
                cur.execute("""insert into config.watchlist_group
                               (name, owner, display_order, is_focus, note)
                               values (%s, %s, %s, false, %s) returning group_id""",
                            (name, owner, g["display_order"], "同花顺导入"))
                gid = cur.fetchone()[0]
                out["groups_new"] += 1
            else:
                gid = existing["group_id"]
                # is_focus / note **原样不动**：ini 里没有这两个字段，
                # 跟着"全量覆盖"一起刷成 false/null 就是抹掉用户的手工标记
                cur.execute("update config.watchlist_group set display_order = %s where group_id = %s",
                            (g["display_order"], gid))
            have = set(existing["members"]) if existing else set()
            codes = list(dict.fromkeys(g["members"]))                  # 组内去重，保序
            for code in codes:
                if code in have:
                    continue
                q = quote.get(code) or {}
                # ⚠️ `added_at` **不许显式绑 NULL**：这一列是 `not null default now()`，
                # 传 NULL 会绕过默认值当场抛 NotNullViolation，而它在一个事务里 ——
                # 一票失败等于整份自选没同步（2026-09-29/30 实测：这条已连败 91 次、
                # 从未成功过，根因就是 `SelfStockInfo.json` 只覆盖 1,008 个代码，
                # 而 ini 里有 1,093 个 —— 那 85 只"没有成本记录的票"里随便一只都能掀掉整批）。
                # 交还给默认值 = "这次同步第一次看见它"，正是默认值本来想表达的意思。
                cur.execute("""insert into config.watchlist_stock
                               (group_id, security_code, cost, added_at)
                               values (%s, %s, %s, coalesce(%s, now()))
                               on conflict (group_id, security_code) do nothing""",
                            (gid, code, q.get("cost"), q.get("added")))
                out["members_added"] += cur.rowcount
            for code in have - set(codes):
                if append:
                    # `append` 这一档的全部意义就在这条 `continue`：ini 已经不要的票留着。
                    # 计数走 `members_kept_absent`，不冒充"删了"，也不静默。
                    out["members_kept_absent"] += 1
                    continue
                cur.execute("delete from config.watchlist_stock where group_id = %s and security_code = %s",
                            (gid, code))
                out["members_dropped"] += cur.rowcount
        cur.execute("""select count(*) from config.watchlist_group g
                       where not exists (select 1 from config.watchlist_stock s
                                         where s.group_id = g.group_id)""")
        out["empty_groups_kept"] = cur.fetchone()[0]      # 空组照留（用户刚建还没放票）
        out["groups_kept_absent"] = len(plan["kept_absent_groups"])
    return out


def current_owner(conn, fallback: str = "fin") -> str:
    with conn.cursor() as cur:
        cur.execute("select owner, count(*) from config.watchlist_group group by 1 order by 2 desc")
        rows = cur.fetchall()
    if not rows:
        return fallback
    if len(rows) > 1:
        raise RuntimeError("库里有多位自选所有者 %s，全量覆盖会互相误删，先人工确认"
                           % [r[0] for r in rows])
    return rows[0][0]


# ---------------------------------------------------------------- 成员每日快照

#: 落点。2026-09-26 与数据加工对齐后定在 `reference_data`：它和 `sector_member`
#: （板块成分真时点）同 schema 同一族，读侧只记一个位置；原先写在 `ys.` 影子表里，
#: 而 `ys` 已在待删名单上 —— 往待删表里写每日增量等于自断历史。
SNAPSHOT_TABLE = "reference_data.watchlist_member_snapshot"

SNAPSHOT_DELETE_DAY = "delete from %s where trade_date = %%s::date" % SNAPSHOT_TABLE
SNAPSHOT_INSERT = ("insert into %s (trade_date, group_id, group_name, security_code, captured_at)"
                   " values (%%s::date, %%s, %%s, %%s, %%s)" % SNAPSHOT_TABLE)


def snapshot_rows(ini: dict, db: dict) -> tuple[list, list[str]]:
    """把 ini 的名单摊成快照行 `(group_id, group_name, code)`。

    `group_id` 一律**按组名从库里查**，不给查不到的新组编 id：编出来的 id 第二天
    会和真 id 撞，那天之后的历史就永久错位。查不到的组进 `skipped`，由调用方如实报数。
    """
    rows: list = []
    skipped: list[str] = []
    for g in ini["groups"]:
        hit = db.get(g["name"])
        if hit is None:
            skipped.append(g["name"])
            continue
        for code in dict.fromkeys(g["members"]):          # 组内去重、保序
            rows.append((hit["group_id"], g["name"], code))
    return rows, skipped


def write_snapshot(ini: dict, trade_date: str, *, probe: bool = False) -> dict:
    """独立事务落当天的成员快照。**主写入成功或失败都要跑。**

    原来它接在 `apply_plan` 后面同事务跑（"快照必须等于这次写进库的那份"）。
    2026-09-26 实测那条设计的代价：`config.*` 被一个触发器权限卡住 → 整笔回滚 →
    当天名单一行没留下，而 `config` 那时已经落后源（ini 1288 成员行 vs 库 1283，
    ini mtime 停在 09-24 10:42）。成员序列**只能当天存、事后补不出来**，
    所以"写不进去"的那天恰恰最该记下"今天名单长什么样"。
    代价是快照与库可以不一致 —— 那是故意的：快照记源（同花顺 ini），库记镜像。

    ⚠️ 真写之前**再查一次日历**：`trade_date` 必须是开市日。`run()` 上面已有
    `open_state` 那道闸，但这里查的是**落库这一刻的参数** —— 手动跑、以后新增的调用方、
    或谁把 `force_open_date` 传成周六，都绕不过去。表里那行 `2026-09-26`（周六、
    `is_open=false`、1283 行）就是没有这道闸时写进去的，而 W=21 的窗口起点落在没有行情
    的日子上。`probe` 例外：它一定回滚、不留痕，用假归属日才有意义。
    """
    conn = _connect_etl()
    try:
        if not probe:
            with conn.cursor() as cur:
                cur.execute("select is_open from reference_data.trade_calendar "
                            "where calendar_date = %s::date", (trade_date,))
                row = cur.fetchone()
            if not row or not row[0]:
                raise RuntimeError(
                    "归属日 %s 不是交易日（日历 %s），快照不写 —— 非交易日的名单没有归属日"
                    % (trade_date, "查无此日" if not row else "is_open=false"))
        db_now = read_db(conn)     # 若新组刚被本次 apply 建出来，这里就取得到真 id
        rows, skipped = snapshot_rows(ini, db_now)
        captured = _now()
        with conn.cursor() as cur:
            cur.execute(SNAPSHOT_DELETE_DAY, (trade_date,))
            replaced = cur.rowcount
            cur.executemany(SNAPSHOT_INSERT,
                            [(trade_date, gid, name, code, captured) for gid, name, code in rows])
        if probe:
            conn.rollback()
        else:
            conn.commit()
        return {"trade_date": trade_date, "rows": len(rows), "replaced": replaced,
                "groups_skipped": skipped, "table": SNAPSHOT_TABLE, "rolled_back": probe}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def open_state(conn) -> tuple[bool, Optional[str]]:
    """(今天是否交易日, 今天日期)。日历读不到 → (False, None)，宁可不写。"""
    with conn.cursor() as cur:
        cur.execute("select is_open, calendar_date::text from reference_data.trade_calendar "
                    "where calendar_date = current_date")
        row = cur.fetchone()
    if not row:
        return False, None
    return bool(row[0]), row[1]


# ---------------------------------------------------------------- 运行与状态

def state_path() -> Path:
    d = Path(data_path("ingest"))
    d.mkdir(parents=True, exist_ok=True)
    return d / STATE_FILE


def read_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"last_run_at": None, "last_ok_at": None, "consecutive_failures": 0,
                "ever_ran": False}


def _write_state(prev: dict, ok: bool, reason: Optional[str], extra: dict,
                 check_only: bool = False) -> dict:
    """`check_only=True` = dry-run：**只记 `last_check_at`，绝不写 `last_ok_at`**。

    把"我看了一眼、有差异"记成"同步成功"，页面就会显示今天已同步而库里其实还是旧的 ——
    这正是本轮要根治的那类零报错陈旧。
    """
    st = dict(prev)
    stamp = _now().isoformat(timespec="seconds")
    if check_only:
        st["last_check_at"] = stamp
        st["last_check_ok"] = bool(ok)
        st["last_check_reason"] = None if ok else reason
        st.update(extra)
        atomic_write_json(str(state_path()), st)
        return st
    st["last_run_at"] = stamp
    st["ever_ran"] = True
    if ok:
        st["last_ok_at"] = stamp
        st["consecutive_failures"] = 0
        st["reason"] = None
    else:
        st["consecutive_failures"] = int(prev.get("consecutive_failures") or 0) + 1
        st["reason"] = reason
    st.update(extra)
    atomic_write_json(str(state_path()), st)
    return st


def run(write: bool = False, force_open_date: Optional[str] = None,
        probe: bool = False) -> dict:
    """跑一轮。`write=False` = 只出差异报告，不动库（也不写 last_ok_at 为成功）。

    `force_open_date='YYYY-MM-DD'` 只给通路验证用（配合外层回滚）：临时把"今天是否交易日"
    这道闸抬起来写成指定归属日。正常调度**绝不带它** —— 休市日写快照就是把不确定的名单
    塞进某个归属日，那正是这件事要防的东西。

    返回里 `dry_run` 与 `ok` 分开：dry-run 成功不等于同步成功。
    """
    prev = read_state()
    parsed = ths_local.read_watchlist()
    if not parsed["available"]:
        _write_state(prev, False, parsed["reason"], {"dry_run": write is False}, check_only=not write)
        return {"ok": False, "reason": parsed["reason"], "dry_run": not write}

    if not etl_ready():
        reason = "未配置 PG_ETL_USER / PG_ETL_PASSWORD（写 config.watchlist_* 需要 ft_etl）"
        _write_state(prev, False, reason, {"source_ini": parsed["ini"]}, check_only=not write)
        return {"ok": False, "reason": reason, "dry_run": not write, "source": parsed}

    lock, why = acquire_lock()
    if lock is None:
        _write_state(prev, False, why, {}, check_only=not write)
        return {"ok": False, "reason": why, "dry_run": not write}
    try:
        conn = _connect_etl()
        report = None          # 失败可能发生在它被赋值之前，下面的 except 要能安全读它
        try:
            enabled, alive, hb = job_state(conn)
            if enabled and alive:
                reason = ("FinanceTool 的 %s 仍在跑（%s）—— 一张表不许两个写入者，本次拒写。"
                          "请先把它置 enabled=false 再来。" % (JOB_KEY, hb))
                _write_state(prev, False, reason, {}, check_only=not write)
                return {"ok": False, "reason": reason, "dry_run": not write}
            db = read_db(conn)
            owner = current_owner(conn)
            # 同步模式的默认值**只住 `duanxian/jobs.py::WATCHLIST_DEFAULTS`**：宿主、页面、
            # 命令行都从这里读同一份，谁再写一个 "full" 字面量就会出现"页面改了、代码不读"。
            from . import jobs as _jobs
            merged, warns = _jobs.settings("watchlist", _jobs.WATCHLIST_DEFAULTS)
            mode = str(merged.get("sync_mode") or "full")
            plan = build_plan(parsed, db, mode)
            report = {"ok": True, "reason": None, "dry_run": not write,
                      "guard": {"ft_job_enabled": enabled, "ft_scheduler_alive": alive,
                                "heartbeat": hb},
                      "owner": owner,
                      "sync_mode": mode,
                      "sync_mode_warning": warns[0] if warns else None,
                      "sync_mode_meaning": (
                          "增量添加：只把同花顺里有的加进来，库里那些 ini 已经没有的组/成员留着不删"
                          "（所以库里会积累同花顺已经不要的票）" if mode == "append" else
                          "全量更新：以同花顺本机 stockblock.ini 为准，ini 里删掉的组/成员这里也删"),
                      "source": {"ini": parsed["ini"], "ini_mtime": parsed["ini_mtime"],
                                 "user": parsed["user"], "groups": parsed["group_count"],
                                 "member_rows": parsed["member_rows"],
                                 "unique_codes": parsed["unique_codes"],
                                 "dropped_non_stock": parsed["dropped"],
                                 "multiline_name_lines": parsed["multiline_name_lines"],
                                 "duplicate_group_codes": parsed["duplicate_codes"],
                                 "quote": parsed["quote_source"]},
                      "db_before": {"groups": len(db),
                                    "members": sum(len(g["members"]) for g in db.values())},
                      "plan": plan}
            if write:
                captured = _now()
                is_open, today = (True, force_open_date) if force_open_date else open_state(conn)
                apply_error = None
                try:
                    report["backup_rows"] = backup_current(conn, captured)   # 覆盖前先整份备份
                    report["applied"] = apply_plan(conn, parsed, db, owner, mode)
                    conn.commit() if not probe else conn.rollback()
                    report["captured_at"] = captured.isoformat(timespec="seconds")
                    if probe:
                        report["rolled_back"] = True
                except Exception as exc:                      # noqa: BLE001
                    conn.rollback()
                    apply_error = exc      # 先按住不抛：今天这份名单比这条报错更要紧
                # 快照走**独立事务**、且以 ini 为准（理由见 write_snapshot）。放在 except 之后
                # 是为了让"config 被权限卡住"的那天照样留下名单 —— 那天的历史补不回来。
                if is_open and today:
                    try:
                        report["member_snapshot"] = write_snapshot(parsed, today, probe=probe)
                    except Exception as exc:                  # noqa: BLE001
                        # 快照自己的失败不许盖掉主错误，否则排障时会去查一条没发生过的链
                        report["member_snapshot"] = {
                            "trade_date": today, "rows": 0, "table": SNAPSHOT_TABLE,
                            "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
                else:
                    report["member_snapshot"] = {"trade_date": None, "rows": 0,
                                                 "skipped": "今日非交易日：成员历史不写归属日"}
                if apply_error is not None:
                    raise apply_error
            extra = {"groups_new": plan["new_groups"] and len(plan["new_groups"]) or 0,
                     "groups_dropped": len(plan["drop_groups"]),
                     "members_added": plan["add_members"], "members_dropped": plan["drop_members"],
                     "changed_groups": len(plan["changed_groups"]),
                     "member_snapshot_date": (report.get("member_snapshot") or {}).get("trade_date"),
                     "member_snapshot_rows": (report.get("member_snapshot") or {}).get("rows"),
                     "source_ini": parsed["ini"], "source_mtime": parsed["ini_mtime"],
                     "dry_run": not write}
            # probe 只验通路、数据已回滚，所以**不许把 last_ok_at 写成成功** ——
            # 那会让页面以为今天已同步，而库里其实一点没变。
            _write_state(prev, not probe, None if not probe else "probe：已回滚，未落库",
                         extra, check_only=(not write) or probe)
            return report
        except Exception as exc:      # noqa: BLE001  失败要出声，且不提交半个覆盖
            conn.rollback()
            reason = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            logger.error("自选同步失败（未提交）：%s", reason)
            # 权限/缺表这类是**再重试一百次也不会自己变好**的硬错误。常驻线程 60 秒一轮，
            # 不给它终态就会刷一整天（实测一次通路验证把 consecutive_failures 顶到 91）。
            # 处理：标 fatal 让调度器今天不再重试，原因照旧留在状态里显示为红色 ——
            # 停止重试不等于停止出声。
            fatal = any(k in reason for k in ("InsufficientPrivilege", "permission denied",
                                              "UndefinedTable", "does not exist"))
            snap = (report or {}).get("member_snapshot") or {}
            # 覆盖失败但快照已落的那天，页面上要看得出来："名单历史已存 N 行，只是镜像没跟上"
            # —— 否则红色一条会误导人去以为今天整条链白跑。
            extra_fail = {"fatal": fatal, "member_snapshot_date": snap.get("trade_date"),
                          "member_snapshot_rows": snap.get("rows")}
            if snap.get("error"):
                extra_fail["member_snapshot_error"] = snap["error"]
            _write_state(prev, False, reason, extra_fail, check_only=not write)
            return {"ok": False, "reason": reason, "dry_run": not write,
                    "fatal": fatal, "member_snapshot": snap or None}
        finally:
            conn.close()
    finally:
        release_lock(lock)
