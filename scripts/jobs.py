"""采集作业开关与状态。契约见 docs/系统配置工作指南/03-环境变量与作业配置.md。

    python -X utf8 scripts/jobs.py status
    python -X utf8 scripts/jobs.py enable  fundflow
    python -X utf8 scripts/jobs.py disable  news
    python -X utf8 scripts/jobs.py set      fundflow run_at=15:10 backfill_minutes=
    python -X utf8 scripts/jobs.py run-now  news
    python -X utf8 scripts/jobs.py set      daily_close launch_lead_min=10
    python -X utf8 scripts/jobs.py set      completeness deviation=30

最后两条是 2026-10-07 用户要的两处可配置：**收盘前几分钟拉起通达信**、
**判"全不全"的偏离值（条数）**。系统配置页（`/settings` 的收盘链那一格）改的是
同一份 `jobs/config.json`，两个入口谁都不比另一个多一层权限。

默认值**以各作业模块自己的常量为唯一真源**（`newswire.POLL_SECONDS` 等）。
这里刻意不复制数字 —— 两处各写一个 30 或 240，迟早打架。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duanxian import jobs                                # noqa: E402


def _defaults(name: str) -> dict | None:
    """作业自己的默认值。返回 None = 该作业还没接入。"""
    if name == "news":
        from duanxian import newswire as nw
        return {"enabled": True, "interval_seconds": nw.POLL_SECONDS,
                "backfill_minutes": nw.BACKFILL_MINUTES}
    # `fundflow` / `watchlist`（数据获取接线的两条采集）不在这里写默认值：
    # 它们是"时点型"（run_at，一天一发），走下面 `CLOSE_DEFAULTS` 那条分支照本宣科地取。
    # 重复跑对这两条有害：新浪/东财的口都是即时截面、报文没有日期，
    # 晚一发的值不一样，会把早那一发盖掉且事后分不出是哪一刻的。
    if name in jobs.CLOSE_DEFAULTS:
        # 默认值住在 `duanxian/jobs.py::CLOSE_DEFAULTS`，这里照本文件的规矩**不复制**一份。
        # 在册的不只是收盘链两段：`fundflow/watchlist/coverage/tdx_watch` 都是常驻循环
        # 在跑的作业，页面看得见就要能在这里开关，且带 `_changed_by` 留痕。
        return dict(jobs.CLOSE_DEFAULTS[name])
    if name == "completeness":
        # 判"这一场的数全不全"的两个偏离值（用户 2026-10-07：默认 30 条、可在系统配置改）。
        # 它**不是一条作业**：没有开关、不会自己跑、也不往 state.json 记账，是一组参数。
        # 默认值只住 `jobs.COMPLETENESS_DEFAULTS`，判据（`duanxian/completeness.py`）与页面
        # （`/api/jobs/completeness`）都读它 —— 这里再写一份数字就必然打架。
        return dict(jobs.COMPLETENESS_DEFAULTS)
    if name == "quote_links":
        # 行情外链地址（用户 2026-10-08：按市场可配置，代码用 ****** 占位）。同样是一组参数。
        # 默认值只住 `app/quote_link.py::DEFAULT_TEMPLATES`（判据与真值表也在那），
        # 这里只登记"改得动的键"。
        return dict(jobs.QUOTE_LINK_DEFAULTS)
    return None


def _known() -> list[str]:
    # 在册 = CLI 改得动。`watchlist`/`fundflow` 是常驻线程跑的采集，页面上看得见却
    # 没有开关入口，就等于"只能等会话去改文件"；`coverage`/`tdx_watch` 同理
    # ——09-28 那次"为什么被关了三次"就是因为开关有三份读法、留痕只有一份。
    # 末尾的 `completeness` 是参数组（见 `_defaults` 里那句），不是作业：`set` 改得动，
    # `enable/disable/run-now` 会明确拒绝，不会写出一个没人读的键。
    # `intraday` / `watchtower` 是 10-07 补进来的两条：它们一直在跑、一直在记账，
    # 只是既不在册也没开关（`test_在册清单比CLI多不许有人改不动` 就是为这种洞写的）。
    # `notify_push` 是 10-15 接的第 12 条循环（消息推送：每 60 秒扫一遍定时计划）。
    # 它**没有 run_at**：几点发是"每条计划自己"的字段（`plan.run_at` + 频度），
    # 给循环再配一个时点就是页面上多一个改了也不生效的框。
    return ["news", "fundflow", "watchlist", "daily_close", "fill_all", "coverage",
            "tdx_watch", "intraday", "watchtower", "ths_sync", "step_retry", "backup",
            "notify_push", "completeness", "quote_links"]


def _typed(raw: str):
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def cmd_status(_: argparse.Namespace) -> int:
    print(f"data home = {jobs.data_home()}")
    print(f"config    = {jobs.config_path()}")
    print(f"state     = {jobs.state_path()}")
    print()
    registry = {n: d for n in _known() if (d := _defaults(n))}
    for row in jobs.summary(registry):
        # 参数组（如 `completeness`）没有开关也不会自己跑：给它印"[开] 最近成功 无运行记录"
        # 是纯粹的误导——那条线本来就不记账。分开说，别让人去找一个不存在的失败。
        if "enabled" not in (registry.get(row["name"]) or {}):
            print(f"[参数] {row['name']:<11} 这不是一条作业：没有开关、不自己跑，只被别的作业读")
            print(f"        {json.dumps(row['settings'], ensure_ascii=False)}")
            for warn in row["warnings"]:
                print(f"        ⚠ {warn}")
            continue
        flag = "开" if row["enabled"] else "关"
        # 三档分开说：09-28 的教训是"从未成功（连败 91）"和"还没有运行记录"都印成"从未成功"，
        # 而前者的处置是去查归因、后者可能只是今天还没到点 —— 混写会让人去查一个不存在的失败。
        if row["last_ok_at"]:
            last = row["last_ok_at"]
        elif row["last_run_at"]:
            last = f"从未成功（跑过 {row['last_run_at']}，连败 {row['consecutive_failures']}）"
        else:
            last = "无运行记录（在册但至今没记过一笔 —— 到点没跑、还是跑了不记账，要现场查 /api/jobs）"
        print(f"[{flag}] {row['name']:<11} 最近成功 {last}"
              + (f"｜上次失败 {row['last_run_at']}（连续 {row['consecutive_failures']} 次）"
                 if row["last_status"] == "fail" and row["last_ok_at"] else ""))
        print(f"        {json.dumps(row['settings'], ensure_ascii=False)}")
        for warn in row["warnings"]:
            print(f"        ⚠ {warn}")
    for name in _known():
        if not _defaults(name):
            print(f"[--] {name:<9} 未接入（代码里还没有这个作业的循环，配置可以先写，接上即生效）")
    return 0


def _require(name: str) -> None:
    if name not in _known():
        raise SystemExit(f"未知作业 {name!r}；可用：{', '.join(_known())}")


def _is_param_group(name: str) -> bool:
    """这"条"是不是一组参数（没有 `enabled`，也就没有开关与单轮入口）。"""
    defaults = _defaults(name)
    return bool(defaults) and "enabled" not in defaults


def cmd_toggle(args: argparse.Namespace) -> int:
    _require(args.name)
    if _is_param_group(args.name):
        # 拒绝，而不是写一个 `enabled` 进去：那个键没有任何代码会读，
        # 页面上却会多出"看起来能开关"的一格（"给一个没人读的键配开关"是老坑）。
        keys = "、".join(k for k in _defaults(args.name) if k != "note")
        raise SystemExit(f"{args.name} 是一组参数，不是作业：没有开关可关。"
                         f"要改取值：jobs.py set {args.name} <{keys}>=<值>")
    on = args.command == "enable"
    # `via` 会写进 config.json 的留痕字段：`enabled=false` 是会**一直留着**的状态
    # （默认值救不回来），没有"谁改的"就只能互相猜
    jobs.set_enabled(args.name, on, via=f"cli:jobs.py {args.command}")
    print(f"{args.name}: {'已开启' if on else '已关闭（线程空转，不删作业）'}"
          f" → {jobs.config_path()}")
    if not on:
        note = jobs.change_note(args.name)
        print(f"  ⚠ 关掉之后到点也不会跑，且**不会自己恢复**；要再开：jobs.py enable {args.name}"
              f"｜本次记录：{note.get('changed_at')} {note.get('changed_by')}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    _require(args.name)
    kv = {}
    for item in args.pairs:
        if "=" not in item:
            raise SystemExit(f"要写成 key=value，收到 {item!r}")
        key, value = item.split("=", 1)
        kv[key.strip()] = _typed(value.strip()) if value.strip() else None
    jobs.write_setting(args.name, via="cli:jobs.py set", **kv)
    print(f"{args.name} 已更新 {list(kv)}（值为空的键已交还默认）")
    known = _defaults(args.name)
    if not known:
        print("⚠ 该作业尚未接入，值没有校验；接上后会按同一规则校验并出声")
        return 0
    _, warns = jobs.settings(args.name, known)
    for warn in warns:
        print(f"⚠ {warn}")
    return 1 if warns else 0      # 写进去的值自己读回来是坏的，就不能算成功


def cmd_run_now(args: argparse.Namespace) -> int:
    _require(args.name)
    if _is_param_group(args.name):
        raise SystemExit(f"{args.name} 是一组参数，不是作业：它没有「跑一次」这个动作，"
                         f"是收盘链每判一场时读它。要看它此刻判出什么："
                         f"python -X utf8 -m duanxian.completeness --summary")
    if args.name in jobs.CLOSE_CHAIN:
        # 收盘链的两段都能单独跑（补历史日、周末手动追）。走的是与定时宿主、
        # `/api/jobs` 同一个执行体，所以退出码的读法不会分叉；抢不到锁就是有人在跑，
        # 这里如实返回非零，不"排队重试"—— 命令行里排队等于让人以为已经在跑了。
        out = jobs.run_close_stage(args.name)
        if not out["ran"]:
            print(f"⚠ {out['reason']}")
            return 4
        mark = {"run": "成", "stop": "按设计停下", "hold": "没成"}[out["verdict"]]
        print(f"{args.name}: {mark}｜rc={out['rc']}｜{out['reason']}")
        return 0 if out["ok"] else 1
    if args.name == "step_retry":
        # 阶梯有真的单轮入口（`python -m ingest.jobs.step_retry`）：跑"现在该跑的那一档"。
        # 没到点就说没到点（rc=3），不硬凑一次重跑 —— 它每次跑都会碰加工段的锁与上游源。
        from ingest.jobs import step_retry
        out = step_retry.run_due()
        mark = "成" if out.get("ok") else ("顺延" if out.get("busy")
                                       else ("没事干" if out.get("skipped") else "没成"))
        print(f"step_retry: {mark}｜{out.get('reason')}")
        return 0 if (out.get("ok") or out.get("skipped")) else 1
    if args.name == "backup":
        # 备份有真的单轮入口，而"该背哪一档、背完算不算成、结果记哪一格"全部住在
        # `app.ops.backup.run_once` 一处 —— 命令行与常驻循环走同一个执行体，
        # 就不会出现"人刚手动背完、体检还说最近备份 148 小时前"那种两份口径各说各话。
        from app.ops import backup
        out = backup.run_once()
        print("backup: %s（%s）%s %.2f MB｜恢复验证 %s%s"
              % ("成" if out["ok"] else "没成", out["kind"],
                 os.path.basename(str(out.get("artifact") or "-")),
                 float(out.get("mb") or 0),
                 {True: "通过", False: "未通过", None: "没做成（见原因）"}[out.get("verify_ok")],
                 "" if out["ok"] else "｜" + str(out.get("reason"))))
        return 0 if out["ok"] else 1
    if args.name != "news":
        # 这几条是**常驻循环按自己的节奏跑**的（`/api/jobs` 的 `scheduler.host_loops` 能看见在不在）。
        # 这里不假装"帮你立刻跑一次"：没有单轮入口就是没有，硬凑一个只会产出第二套跑法。
        raise SystemExit(f"{args.name} 没有单轮入口：它由本进程的常驻循环按自己的节奏跑，"
                         f"开关与时点在这里改得动（enable/disable/set），最近一次结果看 {jobs.state_path()}")
    from duanxian import newswire as nw
    defaults = _defaults("news")
    merged, _warns = jobs.settings("news", defaults)
    res = nw.collect_once(nw.read_cursors(), backfill_minutes=merged["backfill_minutes"])
    ok = bool(res.get("available"))
    jobs.record("news", ok=ok, detail={"rows": len(res.get("rows") or []),
                                       "dropped": res.get("dropped"),
                                       "reason": res.get("reason")})
    print(f"news: {'ok' if ok else 'fail'} rows={len(res.get('rows') or [])} "
          f"dropped={res.get('dropped')} reason={res.get('reason')}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="采集作业开关与状态（Python 标准库 + 项目内模块）")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    for verb in ("enable", "disable"):
        p = sub.add_parser(verb)
        p.add_argument("name")
        p.set_defaults(func=cmd_toggle)
    p = sub.add_parser("set")
    p.add_argument("name")
    p.add_argument("pairs", nargs="+", help="key=value，值留空＝交还默认")
    p.set_defaults(func=cmd_set)
    p = sub.add_parser("run-now")
    p.add_argument("name")
    p.set_defaults(func=cmd_run_now)
    args = ap.parse_args()
    try:
        return args.func(args)
    except (OSError, SystemExit) as exc:
        print(f"未执行：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
