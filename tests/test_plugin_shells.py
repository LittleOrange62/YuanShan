"""`app/tdx_client/shells.py`：收了通达信 AutoRun 留下的**空壳控制台**。

用户 2026-10-08 的原话是"这个是通达信客户端的定时任务自动起来的，
你发现数据全了之后，帮我关闭进程即可" —— 授权的是**收那一个空壳**，
所以这里钉的全是"什么时候绝不动手"：链在跑不动、数据没判全不动、
脚本还在跑（有 python 子进程）不动、主客户端 `TdxW.exe` 永远不动、
按镜像名批量杀是红线。以及最要紧的一条：**发了 taskkill 不等于关掉了**，
收完必须重探，还在就如实写"需要人工"。
"""
from __future__ import annotations

import pytest

from app.tdx_client import shells
from duanxian import jobs as jstore


def _row(pid, kids=0, cmd='C:\\Windows\\system32\\cmd.exe /k ""python.exe" -u '
                          '"D:\\new_tdx64\\PYPlugins\\user\\Get_DayEnd.py" --run_tdx 1"'):
    return {"pid": pid, "python_kids": kids, "cmd": cmd}


class _FakePwsh:
    """把 powershell 的输出换成假数据：只验解析与判据，绝不碰真进程。"""

    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        text = "\n".join(self.lines)

        class R:
            stdout = text
        return R()


def test_解析只吃带_PYPlugins_user_的行(monkeypatch):
    fake = _FakePwsh([
        "12948|0|cmd.exe /k \"\"python.exe\" -u \"D:\\new_tdx64\\PYPlugins\\user\\Get_DayEnd.py\" --run_tdx 1\"",
        "777|1|cmd.exe /k \"python D:\\elsewhere\\thing.py\"",        # 不是这一类
        "垃圾行",
    ])
    monkeypatch.setattr(shells.subprocess, "run", fake)
    rows = shells.find_plugin_shells()
    assert [r["pid"] for r in rows] == [12948], "按命令行里的 PYPlugins\\user\\ 认，不按标题子串"
    assert rows[0]["python_kids"] == 0


def test_探测失败要抛出去由调用方决定不收(monkeypatch):
    def boom(*a, **k):
        raise OSError("powershell 起不来")

    monkeypatch.setattr(shells.subprocess, "run", boom)
    with pytest.raises(OSError):
        shells.find_plugin_shells()


def test_链在跑时一窗不动():
    out = shells.reap(complete=True, chain_busy="fill_all", probe=lambda: [_row(1)],
                      kill=lambda p: "不该被调用", after=lambda: [])
    assert out["acted"] is False and "收盘链 fill_all 正在跑" in out["reason"]


def test_数据没判全就不收(monkeypatch):
    calls = []
    out = shells.reap(complete=False, chain_busy=None, probe=lambda: [_row(1)],
                      kill=lambda p: calls.append(p) or "killed", after=lambda: [])
    assert out["acted"] is False and calls == []
    assert "还没判全" in out["reason"]


def test_脚本还在跑的那个数全了也不收():
    """`/k` 的窗口留着 ≠ 脚本跑完了。有活着的 python 子进程就是正在写文件。"""
    seen = []
    out = shells.reap(complete=True, chain_busy=None,
                      probe=lambda: [_row(1, kids=1), _row(2, kids=0)],
                      kill=lambda p: seen.append(p) or "ok", after=lambda: [])
    assert seen == [2], "只收没有 python 子进程的那一个"
    assert [r["pid"] for r in out["still_running_scripts"]] == [1]


def test_收完重探_还在就写需要人工():
    """10-08 同花顺那个 `close_pids` 就是把"发了 taskkill"当成"已关闭"，界面上说收了、
    桌上还开着。这一条钉的是同一件事在收壳上不许重犯。"""
    out = shells.reap(complete=True, chain_busy=None, probe=lambda: [_row(9)],
                      kill=lambda p: "发了关闭请求", after=lambda: [_row(9)])
    assert out["killed"] == [] and out["left"] == [9]
    assert "还在" in out["reason"] and "人工" in out["reason"]
    ok = shells.reap(complete=True, chain_busy=None, probe=lambda: [_row(9)],
                     kill=lambda p: "发了关闭请求", after=lambda: [])
    assert [k["pid"] for k in ok["killed"]] == [9] and ok["left"] == []


def test_没有壳就说没有():
    out = shells.reap(complete=True, chain_busy=None, probe=lambda: [],
                      kill=lambda p: "不该调用", after=lambda: [])
    assert out["acted"] is False and "没有 PYPlugins 留下的壳" in out["reason"]


def test_测试进程里不换缝就绝不真动():
    """`kill=None` 时 pytest 进程直接拒绝 —— 桌面上的窗口不是单测该碰的东西。"""
    out = shells.reap(complete=True, chain_busy=None, probe=lambda: [_row(1)])
    assert out["acted"] is False and "测试进程" in out["reason"]


def test_红线_绝不按镜像名批量杀():
    """只 `kill_tree(那一个 pid)`。按 `cmd.exe` / `/IM` 收会把用户自己开着的终端一起带走。"""
    import pathlib

    src = pathlib.Path(shells.__file__).read_text(encoding="utf-8")
    spawn_lines = [ln for ln in src.splitlines()
                   if ("subprocess.run(" in ln or "Popen(" in ln)]
    assert all("/IM" not in ln for ln in spawn_lines), "按镜像名杀是红线"
    assert "kill_tree" in src
    assert "/IM" not in src.split("def reap(")[1]


def test_看护那条循环真的接了这个动作():
    """后端交出了判据、界面与循环没调用 = "看着有其实没接"。钉调用点与在册开关。"""
    import pathlib

    srv = pathlib.Path(__file__).resolve().parents[1] / "server.py"
    body = srv.read_text(encoding="utf-8")
    assert "_reap_plugin_shells()" in body.split("def _tdx_watch_scheduler")[1].split("def _start_tdx_watch")[0]
    merged, _warns = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)
    assert merged["reap_script_shells"] is True, "用户 10-08 明确授权了这件事，默认就该做"
    assert "reap_script_shells" in jstore.TDX_WATCH_DEFAULTS


def test_宿主那一半的两个谎已修_复核用现探且空轮要平账():
    """2026-10-18 实测到的两条，都是"错了不报错、只会说一句听着合理的假话"：

    1. `probe=lambda: found` 传进去之后 `after` 默认等于 `probe`（`shells.py:74`），
       于是"收完重探"返回的是**杀之前**那份进程表 ⇒ 被杀的 pid 必然出现在 `left` 里，
       每晚记一条「这几个发了关闭请求但还在（pid ×××）：需要人工看一眼」，而那个 pid 早就不在了
       （`Get-CimInstance Win32_Process -Filter "ProcessId=3896"` 空，账还挂着）。
    2. "这一轮没有壳"直接 `return None` 不写台账 ⇒ 上一条 fail/连败没有任何东西能覆盖它，
       桌面干净了三周，体检还在说要人工看一眼。
    """
    import pathlib

    srv = (pathlib.Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    body = srv.split("def _reap_plugin_shells")[1].split("\ndef ")[0]
    assert "after=shells.find_plugin_shells" in body, \
        "复核必须现探：拿杀之前的快照复核＝必然说还在，且这条假话天天挂账"
    assert "supersedes=True" in body, "空轮要把上一条待人工那条抹掉，不然人会去补一件已经做完的事"
    assert 'jstore.record("tdx_shell_reap"' in body and "last_status" in body, \
        "平账要先看有没有旧账，两分钟一轮的空白轮不该把台账刷成「没有壳」"
    assert body.count('jstore.record("tdx_shell_reap"') == 2, \
        "只有两处记账：空轮平账那一笔 + 真收过的那一轮，别在第三处各写一份"
