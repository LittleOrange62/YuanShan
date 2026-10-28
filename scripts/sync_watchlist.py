"""自选分组同步运行入口（同花顺 ini → `config.watchlist_*`）。

    python scripts/sync_watchlist.py --check      # 只读：解析 + 差异报告，不动库
    python scripts/sync_watchlist.py --yes        # 快照 + 全量覆盖（唯一会写库的一条）

默认就是 `--check`：**没有 `--yes` 就不写**，因为这是全量覆盖，写坏了是用户手改的副本。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duanxian import watchlist_sync as ws     # noqa: E402


def _print(rep: dict) -> None:
    if not rep.get("ok"):
        print("✗ 未能完成：%s" % rep.get("reason"))
        return
    src = rep.get("source") or {}
    print("源：%s（mtime %s，用户 %s）" % (src.get("ini"), src.get("mtime") or src.get("ini_mtime"), src.get("user")))
    print("  组 %s / 成员行 %s / 唯一代码 %s；非个股丢弃 %s；多行组名续行 %s 行；重复组号 %s"
          % (src.get("groups"), src.get("member_rows"), src.get("unique_codes"),
             src.get("dropped_non_stock"), src.get("multiline_name_lines"),
             src.get("duplicate_group_codes")))
    print("守卫：FT 作业 enabled=%s、调度器活着=%s → %s"
          % (rep["guard"]["ft_job_enabled"], rep["guard"]["ft_scheduler_alive"],
             "已拒写" if rep["guard"]["ft_scheduler_alive"] else "本侧可接管"))
    db, plan = rep["db_before"], rep["plan"]
    print("库里现状：组 %s / 成员 %s" % (db["groups"], db["members"]))
    print("差异：新建组 %d、删除组 %d、成员 +%d / -%d（含随组连坐）、有变动的组 %d"
          % (len(plan["new_groups"]), len(plan["drop_groups"]),
             plan["add_members"], plan["drop_members"], len(plan["changed_groups"])))
    for g in plan["new_groups"]:
        print("    新组：%s（%d 只）" % (g["name"][:24], g["count"]))
    for g in plan["drop_groups"]:
        print("    要删的组：%s（连坐删 %d 只成员，ON DELETE CASCADE）" % (g["name"][:24], g["count"]))
    for c in plan["changed_groups"][:12]:
        print("    成员变动：%s  +%d/-%d" % (c["name"][:22], c["add"], c["drop"]))
    if len(plan["changed_groups"]) > 12:
        print("    （还有 %d 个组有变动）" % (len(plan["changed_groups"]) - 12))
    print("  is_focus 按名保留：%s；note 保留组数：%d" % (plan["focus_kept"] or "无（45 组全未勾）",
                                                        plan["notes_kept"]))
    if rep.get("dry_run"):
        print("→ dry-run：未动库。要真覆盖请加 --yes")
    else:
        ms = rep.get("member_snapshot") or {}
        print("→ 覆盖前已备份 %s 行进 ys.watchlist_backup（captured_at=%s）"
              % (rep.get("backup_rows"), rep.get("captured_at")))
        print("→ 成员每日快照：%s 行 → %s，归属交易日 %s%s%s"
              % (ms.get("rows"), ms.get("table") or ws.SNAPSHOT_TABLE,
                 ms.get("trade_date"),
                 "（%s）" % ms.get("skipped") if ms.get("skipped") else "",
                 "；新组取不到 group_id、本日跳过 %s" % ms.get("groups_skipped")
                 if ms.get("groups_skipped") else ""))
        if ms.get("error"):
            print("   🔴 快照这一段自己失败了：%s" % ms["error"])
        print("→ 覆盖结果：%s%s" % (rep.get("applied"), "；**已回滚（probe）**"
                                    if rep.get("rolled_back") else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真的写库（先快照再全量覆盖）")
    ap.add_argument("--check", action="store_true", help="只读比对（默认行为）")
    ap.add_argument("--probe", action="store_true",
                    help="通路验证：真跑备份/覆盖/成员快照后**整体回滚**，不留数据、"
                         "也不把 last_ok_at 记成成功；归属日临时用 2099-01-01 免撞真快照")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()
    rep = ws.run(write=(args.yes and not args.check) or args.probe,
                 force_open_date="2099-01-01" if args.probe else None,
                 probe=args.probe)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, default=str)[:4000])
    else:
        _print(rep)
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
