"""`/api/datasource` 与 `/api/system/doctor` 的数据底。

契约：docs/页面展示工作指南/04-新增页面落地进度.md（字段名按 `config.datasource` 实际列）。
两条硬规矩：
1. **没有调度就没有"新鲜"**：探测结果会一直陈旧，所以每个源都交 `last_probe_at` +
   `stale_seconds` + `probe_stale`，全局再给一个 `counts`。只报"上次探测于"而不报
   "多久没说话"，整屏就是假新鲜。
2. **`no_permission` 单独一档**，不跟 `down` 混灰：前者是"去授权就能解"，
   后者是"源死了"，用户的动作完全不同。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import threading

from duanxian.pg import enabled as pg_enabled
from duanxian.pg import fetch_df
from duanxian.util import china_now

# 超过这个时长没再探测就算陈旧。FT 的 probe.py 有 STICKY 逻辑（状态一旦定住就不再探），
# 所以"很老的 ok"是真实存在的状态，必须显出来而不是当现值用。
PROBE_STALE_SECONDS = 7 * 24 * 3600

def _int_or_none(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


HEALTH_ORDER = ("ok", "no_permission", "down", "broken", "unknown")


def _records(df) -> list[dict]:
    """DataFrame → dict 列表，**NaN/NaT 一律换成 None**。pandas 把 SQL NULL 读成 NaN，
    后面任何 `int(...)` 或 `json.dumps` 都会炸（`/api/jobs` 那次 500 就是这个成因）。"""
    if df is None or not len(df):
        return []
    import pandas as pd

    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value)
    return None if text in ("nan", "NaT", "<NA>", "None", "") else text


def _age_seconds(value, now: dt.datetime) -> int | None:
    """距现在多少秒。算不出给 None，**不折成 0**（0 = "刚刚探过"，是完全相反的事实）。"""
    stamp = _text(value)
    if not stamp:
        return None
    try:
        parsed = dt.datetime.fromisoformat(stamp.replace(" ", "T")[:25])
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return max(0, int((now.astimezone(dt.timezone.utc) - parsed).total_seconds()))


def _probe_latest() -> dict[str, str]:
    df = fetch_df("select datasource_key, max(checked_at) as at, count(*) as n "
                  "from operations.datasource_probe group by datasource_key")
    out: dict[str, str] = {}
    for r in _records(df):
            key, at = _text(r.get("datasource_key")), _text(r.get("at"))
            if key and at:
                out[key] = at
    return out


def datasource_payload() -> dict:
    now = china_now()
    if not pg_enabled():
        return {"generated_at": _text(now.isoformat(timespec="seconds")), "available": False,
                "reason": "本地库不可读（VIBE_PG 关闭或连不上），源登记与探测历史都拿不到",
                "counts": None, "sources": [], "probe": None}
    rows = fetch_df(
        "select datasource_key, name, kind, provider, layer, ds_role, cost, health, "
        "is_active, last_check, last_ok_at, last_err, latency_ms, row_count, "
        "auth_env_name, probe_spec, note from config.datasource "
        "order by health, display_order, datasource_key")
    if rows is None or not len(rows):
        return {"generated_at": _text(now.isoformat(timespec="seconds")), "available": False,
                "reason": "config.datasource 读不到行（表空或权限不足），不是"
                          "「没有数据源」", "counts": None, "sources": [], "probe": None}
    latest = _probe_latest()
    sources, counts = [], {k: 0 for k in HEALTH_ORDER}
    counts["total"] = 0
    for r in _records(rows):
        key = _text(r.get("datasource_key"))
        probed_at = latest.get(key) or _text(r.get("last_check"))
        age = _age_seconds(probed_at, now)
        health = _text(r.get("health")) or "unknown"
        counts[health] = counts.get(health, 0) + 1
        counts["total"] += 1
        sources.append({
            "datasource_key": key, "name": _text(r.get("name")) or key,
            "kind": _text(r.get("kind")), "provider": _text(r.get("provider")),
            "layer": _text(r.get("layer")), "ds_role": _text(r.get("ds_role")),
            "cost": _text(r.get("cost")), "health": health,
            "is_active": bool(r.get("is_active")) if r.get("is_active") is not None else None,
            "last_probe_at": probed_at, "stale_seconds": age,
            "probe_stale": age is None or age > PROBE_STALE_SECONDS,
            "last_ok_at": _text(r.get("last_ok_at")), "last_err": _text(r.get("last_err")),
            "latency_ms": _int_or_none(r.get("latency_ms")),
            "row_count": _int_or_none(r.get("row_count")),
            # 只给变量名，永远不给值 —— no_permission 那批的成因就是环境变量没设，
            # 页面要能说出"缺哪个"，但不该因此泄露口令/token。
            "auth_env_name": _text(r.get("auth_env_name")),
            "note": _text(r.get("note")),
        })
    global_ages = [s["stale_seconds"] for s in sources if s["stale_seconds"] is not None]
    return {
        "generated_at": _text(now.isoformat(timespec="seconds")), "available": True,
        "reason": None,
        "counts": counts,
        "health_order": list(HEALTH_ORDER),
        "probe": {
            "sources_total": counts["total"],
            "never_probed": sum(1 for s in sources if s["last_probe_at"] is None),
            "stale_now": sum(1 for s in sources if s["probe_stale"]),
            "newest_probe_age_seconds": min(global_ages) if global_ages else None,
            "stale_threshold_seconds": PROBE_STALE_SECONDS,
            "note": "探测无调度：`probe_stale=true` 的源其 health 是历史值，"
                    "不能当现值用（FinanceTool 的 STICKY 逻辑还会让某些源永不再探）",
        },
        "sources": sources,
        "basis": "config.datasource（30 行）+ operations.datasource_probe 分组取最近一次",
    }


# ---------------------------------------------------------------- doctor 上界面

_doctor_lock = threading.Lock()
_doctor_cache: dict | None = None
_doctor_refreshing = False
DOCTOR_TTL_SECONDS = 600


def _run_doctor() -> dict:
    """直接调 `scripts/manage.py` 的 doctor —— 判定逻辑只允许有一份，
    所以这里**不复制一份体检**，只把它的结果读出来。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("ys_manage", root / "scripts" / "manage.py")
    manage = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(manage)
    checks = manage.doctor(root)
    return {
        "generated_at": _text(china_now().isoformat(timespec="seconds")),
        "available": True, "reason": None,
        "source": "scripts/manage.py doctor",
        "blocking_failures": sum(1 for c in checks if not c["ok"] and not c.get("advisory")),
        "advisory_failures": sum(1 for c in checks if not c["ok"] and c.get("advisory")),
        "items": [{"key": c["name"], "label": c["name"], "ok": bool(c["ok"]),
                   "detail": c["action"], "advisory": bool(c.get("advisory"))}
                  for c in checks],
    }


def doctor_payload(force: bool = False) -> dict:
    """stale-while-revalidate。`manage.py doctor` 单跑约 10s（大头是既有的 pip/依赖检查，
    不是我加的那两项），所以**有旧值就立刻给旧值 + 它的年龄**，超期只在后台重算。
    只有服务刚起来、一次都没算过时才同步等 —— 那次没有可显示的缓存，
    与其给半屏 null 不如等。绝不为了响应速度放宽判据。"""
    global _doctor_cache, _doctor_refreshing
    now = china_now()
    stamp = now.isoformat(timespec="seconds")
    with _doctor_lock:
        cache, refreshing = _doctor_cache, _doctor_refreshing
    if cache is None:
        snap = {**_run_doctor(), "computed_at": _text(stamp)}
        with _doctor_lock:
            _doctor_cache = snap
        return {**snap, "age_seconds": 0, "refreshing": False, "ttl_seconds": DOCTOR_TTL_SECONDS}
    age = _age_seconds(cache.get("computed_at"), now)
    if (force or age is None or age > DOCTOR_TTL_SECONDS) and not refreshing:
        with _doctor_lock:
            _doctor_refreshing = True
        threading.Thread(target=_refresh, name="doctor-refresh", daemon=True).start()
        refreshing = True
    return {**cache, "age_seconds": age, "refreshing": refreshing,
            "ttl_seconds": DOCTOR_TTL_SECONDS}


def _refresh() -> None:
    global _doctor_cache, _doctor_refreshing
    try:
        snap = _run_doctor()
        with _doctor_lock:
            _doctor_cache = {**snap, "computed_at": _text(china_now().isoformat(timespec="seconds"))}
    except Exception as exc:                                  # noqa: BLE001  后台线程不能抛
        with _doctor_lock:
            _doctor_cache = {"generated_at": _text(china_now().isoformat(timespec="seconds")),
                             "available": False, "items": [],
                             "reason": f"体检计算失败：{type(exc).__name__}: {exc}",
                             "computed_at": _text(china_now().isoformat(timespec="seconds"))}
    finally:
        with _doctor_lock:
            _doctor_refreshing = False
