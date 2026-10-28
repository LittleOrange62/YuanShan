# -*- coding: utf-8 -*-
"""客户端自己留下的**空壳控制台**：判它真的空了，再把那一个 pid 收掉。

现场（2026-10-08 实测）：通达信登录后，它自己的 PYPlugins 执行器（`py_strategy.cfg` 里
`AutoRun=1`）会起这么一行 ——

    cmd.exe /k ""python.exe" -u "D:\\new_tdx64\\PYPlugins\\user\\Get_DayEnd.py" --run_tdx 1"

窗口标题「管理员: TPyth - 可以获取各类字段」。`/k` 的语义就是**脚本跑完窗口也留着**，
所以它不会自己消失；而 c15 收盘快照读的正是这个脚本落的文件。用户 10-08 的指示：
"这个是通达信客户端的定时任务自动起来的，你发现数据全了之后，帮我关闭进程即可。"

四道闸，缺一个都不动手（顺序也是这个顺序）：
1. 命令行里必须出现 `PYPlugins\\user\\` —— **只认这一类**。不按窗口标题的子串判
   （09-30 那次"取消下载"事故就是子串匹配闯的祸），也不按镜像名批量杀。
2. 它**没有活着的 python 子进程**：脚本还在跑 = 正在写文件，收了等于掐断数据。
3. **没有收盘链在跑**：这个脚本就是链把客户端拉起来之后才被 AutoRun 带出来的。
4. 数据判全（调用方给的 `complete` 必须是明确的 True；**判不了 = 不收**）。

收的方式是 `duanxian.proc.kill_tree(pid)`，只点那一个 pid；**绝不动 `TdxW.exe`**
（TQ 那条腿要主客户端登录着）。收完**重新探一次**确认真的没了 ——
"我发了 taskkill"不等于"窗口关了"，这条形状在同花顺那个 `close_pids` 上刚栽过一次。
"""
from __future__ import annotations

import os
import subprocess

MARK = "PYPlugins\\user\\"

_PS = (
    "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
    "Where-Object { $_.CommandLine -like '*PYPlugins\\user\\*' } | "
    "ForEach-Object { "
    "$kids = @(Get-CimInstance Win32_Process -Filter \"ParentProcessId=$($_.ProcessId)\" | "
    "Where-Object { $_.Name -match '^python' }); "
    "\"{0}|{1}|{2}\" -f $_.ProcessId, $kids.Count, $_.CommandLine }"
)


def find_plugin_shells() -> list[dict]:
    """返回 `[{pid, python_kids, cmd}]`。探测本身坏了**抛给调用方**（宁可什么都不收）。"""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", _PS],
                         capture_output=True, text=True, timeout=25,
                         encoding="utf-8", errors="replace").stdout or ""
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3 or not parts[0].strip().isdigit():
            continue
        cmd = parts[2]
        if MARK.lower() not in cmd.lower():      # 自己再核一遍，不吃 PowerShell 的匹配
            continue
        rows.append({"pid": int(parts[0]), "python_kids": int(parts[1] or 0), "cmd": cmd[:240]})
    return rows


def reap(*, complete: bool, chain_busy: str | None, kill=None,
         probe=None, after=None) -> dict:
    """按四道闸收掉空壳。`complete` 必须是**明确的** True 才动手。

    `probe` / `kill` / `after` 是给单测留的缝：真值分别是"读进程表 / `proc.kill_tree` /
    收完重探一次"。单测里三个都换掉，判据就能测而绝不碰到桌面上的窗口；
    不换的话在 pytest 进程里直接拒动作（洞只留一个，与 `c23.spawn`、`jobsboard._spawn_step` 同形）。
    """
    if kill is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return {"acted": False, "killed": [], "outcome": "skipped",
                "reason": "测试进程：不真收窗口，只验判据"}
    if kill is None:
        from duanxian.proc import kill_tree as kill      # noqa: N803  真收口只有一处
    if probe is None:
        probe = find_plugin_shells
    if after is None:
        after = probe

    # `outcome` 是三档，**不是两档**（成功 / 观望 / 失败），由调用方直接记进台账：
    # 2026-10-12 实测这一条被记成"从未成功过、连败 46"，而那 46 轮每一轮都按设计做得对 ——
    # 收盘链正在跑，它就该不动窗口。观望混进失败里，等于"真故障来了也看不出来"。
    # （与 #85 那条 tdx_watch 夜间假红、`jobs.record` 的第三种结果同一个道理。）
    if chain_busy:
        return {"acted": False, "killed": [], "outcome": "skipped",
                "reason": f"收盘链 {chain_busy} 正在跑 → 这一轮不动任何窗口"}
    if not complete:
        return {"acted": False, "killed": [], "outcome": "skipped",
                "reason": "数据还没判全（或判不了）→ 不收，留着下一轮再看"}
    try:
        found = probe()
    except Exception as exc:  # noqa: BLE001
        # 探不到进程表是**真故障**（powershell/tasklist 坏了），不是观望：记成失败才有机会被发现
        return {"acted": False, "killed": [], "outcome": "fail",
                "reason": f"探不到进程表（{type(exc).__name__}: {str(exc)[:80]}）→ 不收"}
    if not found:
        # "这一轮没有该收的壳"就是它把活干完了，算成功 —— 否则一条长期干净的循环永远记不出"最近成功"
        return {"acted": False, "killed": [], "outcome": "ok",
                "reason": "没有 PYPlugins 留下的壳"}

    empty = [r for r in found if not r["python_kids"]]
    kept = [r for r in found if r["python_kids"]]
    killed, failed = [], []
    for r in empty:
        note = kill(r["pid"])
        killed.append({"pid": r["pid"], "how": str(note)[:80], "cmd": r["cmd"]})
    still = {x["pid"] for x in (after() if killed else [])}
    left = [k for k in killed if k["pid"] in still]
    done = [k for k in killed if k["pid"] not in still]
    if left:
        failed = [k["pid"] for k in left]
    reason = ("已收 %d 个空壳（%s）；%d 个脚本还在跑没动；主客户端 TdxW 未动" % (
        len(done), "、".join(str(k["pid"]) for k in done) or "无", len(kept)))
    if failed:
        reason += "；「这几个发了关闭请求但还在」（pid %s）：需要人工看一眼" % "、".join(
            str(p) for p in failed)
    return {"acted": bool(killed), "killed": done, "left": failed,
            "outcome": "fail" if failed else "ok",
            "still_running_scripts": kept, "reason": reason}


def main() -> int:
    """命令行看一眼桌面上有没有该收的壳（人自己判断用，不动任何窗口）。

    为什么要有这一条：待人工清单里那句「这一步没有登记手动做法」最要命的地方是**人不敢下手**——
    桌上杵着一个黑色 cmd 窗口，谁都怕那是还在写文件的那一个。这里把判据直接打到屏幕上：
    `python_kids` 为 0 的那几条才是已经跑完、只剩一个 conhost 的空壳（可以关），非空的不许关。
    """
    try:
        found = find_plugin_shells()
    except Exception as exc:  # noqa: BLE001  探不到就如实说探不到，别兜成"没有壳"
        print('探不到进程表：%s: %s' % (type(exc).__name__, str(exc)[:160]))
        return 2
    if not found:
        print('没有 PYPlugins 留下的壳（这一项没什么要人做的）')
        return 0
    for r in found:
        tag = '空壳，可以关' if not r['python_kids'] else '脚本还在跑，不许关'
        print('pid=%-8s python子进程=%-3d %s\n    %s' % (
            r['pid'], r['python_kids'], tag, str(r['cmd'])[:200]))
    print('\n要收就按 pid 单个收：taskkill /PID <那一个 pid>'
          '（**绝不按镜像名批量杀** —— 那会把你自己开着的终端一起带走；也绝不碰主客户端 TdxW.exe）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
