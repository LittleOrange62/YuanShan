# -*- coding: utf-8 -*-
"""`GET /api/system/log`：把**原始文本日志的尾部**端到界面上。

为什么非要有这一层（2026-10-10 的一次真实事故）：通达信看护循环"线程活着、页面上写着
正常·连败 0"，但台账 `jobs/state.json` 已经 18 小时没动 —— 真相只在
`.local/uvicorn.out.log` 里每两分钟一行的 `AttributeError`。那一层当时**没有任何界面能看**，
只能开终端 `type`，而重启一次就把上一份截断没了（`-RedirectStandardOutput` 是覆盖不是追加）。
所以这模块管两件事：看得见，以及配合 `scripts/restart_8910.ps1` 的归档别再看不到历史。

三条不是建议的规矩：
1. **白名单，且只读自己拼出来的路径**。这台机器按用户 09-28 的指示开了局域网访问，
   `?file=../../.env` 这种参数绝不能被接受 —— 名字必须命中 `LOGS` 的键，
   翻旧日志的 `run` 必须命中"当场列出来的那几个文件名"，两者都是**查表**不是拼字符串。
2. **只倒读尾巴**：从文件末尾按字节倒着读，绝不为看 200 行把整份文件读进内存
   （它按现在的打印速度一天能长几百 KB，一年就是几十 MB）。
3. **读数要能自证**：交出 `path/size/mtime/truncated/skipped_bytes`，
   让人一眼看出这是尾巴还是全部、文件多久没写过 —— "日志停在 14:37"这种坑
   只有把 mtime 摆出来才看得见。
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 允许看的东西：键是接口参数，值是（相对仓库根的路径, 这一份是什么）。
#: 加新条目 = 愿意让人在界面上读它，别往这里塞含口令或用户笔记的文件。
LOGS: dict[str, tuple[str, str]] = {
    "uvicorn.out": (".local/uvicorn.out.log",
                    "后端进程的标准输出：加工链/收盘链/常驻循环的 print 全在这（`-u` 已加，是实时的）"),
    "uvicorn.err": (".local/uvicorn.err.log",
                    "uvicorn 自己的启动与异常：`Application startup failed` 这类只有这里写"),
}

LINES_MIN, LINES_MAX = 20, 800
#: 一次最多倒读多少字节（800 行 × ~500B，够宽；再多就按行截）
READ_CAP = 512 * 1024


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _key(name: str) -> str:
    """`uvicorn.out` → `uvicorn.out`（归档叫 `uvicorn.out.20261010-132513.log`）。"""
    return name


def archives(name: str) -> list[dict]:
    """上一次/上几次运行留下的归档日志（重启脚本归档的），按时间倒序。

    这里**列出来的**才是接口允许读的 —— 读的时候拿名字回来查这张表，
    于是"能读什么"这件事由目录里真有什么决定，不由参数决定。
    """
    base, _desc = LOGS[name]
    stem = Path(base).stem                       # uvicorn.out
    # ⚠️ 必须从 ROOT 拼：`Path(".local").iterdir()` 是**相对当前工作目录**的，
    # 生产上恰好也是仓库根所以看不出错，测试与任何换目录的调用方会静默返回空列表
    # （于是"能翻旧日志"这件事悄悄变成"永远没有归档"）。
    d = ROOT / Path(base).parent
    pat = re.compile(r"^" + re.escape(stem) + r"\.(\d{8}-\d{6})\.log$")
    out = []
    try:
        for p in d.iterdir():
            m = pat.match(p.name)
            if m:
                st = p.stat()
                out.append({"run": m.group(1), "file": p.name, "size": st.st_size,
                            "mtime": _iso(st.st_mtime)})
    except OSError:
        return []
    return sorted(out, key=lambda x: x["run"], reverse=True)


def tail(name: str, lines: int = 200, run: str | None = None) -> dict:
    """读某一名的尾部若干行。`run` 给了就读那一份归档（必须在 `archives()` 里）。"""
    if name not in LOGS:
        return {"available": False,
                "reason": f"不认识的日志名 {name!r}：接口只读白名单里那几个（{ '、'.join(LOGS) }），"
                          "不接受路径参数"}
    base, desc = LOGS[name]
    path = ROOT / base
    try:
        # 0 / 负数 → 夹到下限（而不是"当成没给、偷偷用 200"：那会让调用方以为自己要的是 200）
        want_lines = 200 if lines is None else int(lines)
    except (TypeError, ValueError):
        want_lines = 200        # 参数根本不是数字才回默认值
    want_lines = max(LINES_MIN, min(want_lines, LINES_MAX))

    if run:
        hit = next((a for a in archives(name) if a["run"] == str(run)), None)
        if hit is None:
            return {"available": False,
                    "reason": f"归档 {run!r} 不在当场列出来的那几份里（可能是被轮转删掉了，"
                              f"也可能名字是手打的）—— 只能读列表里的，避免把接口变成任意文件读取",
                    "known_runs": [a["run"] for a in archives(name)][:12]}
        path = ROOT / base[:base.rindex("/") + 1] / hit["file"]

    if not path.is_file():
        return {"available": False, "path": str(path),
                "reason": "这份日志文件还不存在（服务从没起过，或从来没往这里打过）",
                "desc": desc}
    st = path.stat()
    size = st.st_size
    step = max(4096, want_lines * 512)
    raw, start = b"", 0
    while True:
        start = max(0, size - min(step, READ_CAP))
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read()
        if raw.count(b"\n") > want_lines or start == 0 or len(raw) >= READ_CAP:
            break
        step *= 4
        if step > READ_CAP:
            break
    # 从中间切开可能劈到一个汉字的第 2~3 个字节上 → errors="replace" 而不是 strict，
    # 并且只在确实倒读了非零起点时丢首行（那一行本来就是残缺的）
    text = raw.decode("utf-8", "replace")
    # ⚠️ 归档里会有**成片的 NUL**：PowerShell 的 `-RedirectStandardOutput` 不是顺序追加，
    # 进程退出时它留下的文件里第一条记录之后就是一段空洞（2026-10-10 23:51 那份实测：
    # 55,776 字节里第 72~634 字节全是 0x00）。这些字节**不是日志内容**，原样送进界面就是
    # 屏幕上几百个看不见的字符（读起来像"日志坏了"，其实是读取器没处理）。
    # 空洞总在两条记录之间，所以抹掉不会把两条拼成一条 —— 上一条已经带了自己的换行。
    nuls = text.count("\x00")
    if nuls:
        text = text.replace("\x00", "")
    body = text.splitlines()
    if start > 0 and body:
        body = body[1:]
    lines = body[-want_lines:]
    out = {"available": True, "name": name, "desc": desc, "path": str(path),
           "is_archive": bool(run), "run": run or None,
           "size": size, "mtime": _iso(st.st_mtime),
           "mtime_age_seconds": int(dt.datetime.now().timestamp() - st.st_mtime),
           "returned": len(lines), "asked": want_lines,
           # 一个数就够：窗口之前那段字节数 = 没读的部分（先前另给一个 truncated 是同一个数起两个名）
           "skipped_bytes": start,
           "lines": lines,
           "runs": [a["run"] for a in archives(name)][:12],
           "generated_at": _iso(dt.datetime.now().timestamp())}
    # 空文件 ≠ 没日志：也要能说清"它在写，只是这一份是 0 字节"
    if size == 0:
        out["note"] = "文件在，但是 0 字节：可能是这个进程还没打过东西，或输出被重定向到别处了"
    elif start:
        out["note"] = (f"只读了尾巴：为了 {want_lines} 行跳过前面 {start:,} 字节"
                       f"（整份 {size:,} 字节）。要全文就在机器上看 {path}")
    if nuls:
        # 抹过东西必须留一句话，否则"内容比文件短"又会变成一个说不清的差异
        out["note"] = ((out.get("note") or "") +
                       f"{'；' if out.get('note') else ''}抹掉了 {nuls:,} 个 NUL 字节"
                       "（归档时 PowerShell 重定向留下的空洞，不是日志内容）").lstrip("；")
        out["nul_bytes_stripped"] = nuls
    return out


def live_summary() -> dict:
    """给 `/api/system/log` 的默认视图：有哪些名、当前各自多大、多久没写。"""
    rows = []
    for name, (base, desc) in LOGS.items():
        p = ROOT / base
        exists = p.is_file()
        st = p.stat() if exists else None
        rows.append({"name": name, "desc": desc, "path": str(p),
                     "exists": exists, "size": st.st_size if st else None,
                     "mtime": _iso(st.st_mtime) if st else None,
                     "archives": len(archives(name))})
    return {"files": rows, "limits": {"lines_min": LINES_MIN, "lines_max": LINES_MAX}}


def touches(pid: int | None = None) -> bool:
    """这个 pid（默认自己）是不是正在往日志里写 —— 用于判断"文件停了"还是"进程停了"。"""
    return os.getpid() == (pid or os.getpid())
