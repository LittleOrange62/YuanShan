# -*- coding: utf-8 -*-
"""`/api/system/log` 那一层：能看什么、怎么读尾巴、拒绝什么。

起因是 2026-10-10 那条 tdx_watch 假绿：线程在 `host_loops` 里、页面写"正常·连败 0"、
台账冻 18 小时，真相只在 `.local/uvicorn.out.log` 里每两分钟一行的 `AttributeError` ——
而那一层当时**没有界面能看**，且重启一次就被 `-RedirectStandardOutput` 覆盖掉。

这里钉三件事：
1. 这台机器开着局域网访问 ⇒ 接口**只认白名单里的名字**，路径由后端拼；
   翻旧日志的 `run` 也必须命中"当场列出来的那几个"，两者都是查表不是拼串。
2. 只倒读尾巴（800 行上限），不许为看几十行把整份读进内存。
3. 读数要能自证：`truncated`/`skipped_bytes`/`mtime_age_seconds` 一起交，
   "日志停在 14:37"这类坑只有把年龄摆出来才看得见。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "vr"))   # 末尾追加：插最前会让 vr/ 抢走顶层 app/
import logtail  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把仓库根换成 tmp：LOGS 里存的是相对路径，换根就等于换文件，不碰真日志。"""
    (tmp_path / ".local").mkdir()
    monkeypatch.setattr(logtail, "ROOT", tmp_path)
    return tmp_path


def _write(name: str, text: str):
    p = logtail.ROOT / logtail.LOGS[name][0]
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------------ 白名单

def test_路径参数一律不认_只认白名单里的名字(home):
    for bad in ("../../.env", "uvicorn.out.log", "/etc/passwd", "", "ENV"):
        got = logtail.tail(bad)
        assert got["available"] is False and "白名单" in got["reason"], bad
        assert "lines" not in got or got.get("lines") in (None, []), "拒绝时不许顺手把内容带出去"


def test_归档号必须命中当场列出来的那份(home):
    _write("uvicorn.out", "第一行\n第二行\n")
    got = logtail.tail("uvicorn.out", 40, run="20260101-000000")
    assert got["available"] is False and "不在当场列出来的" in got["reason"]
    assert got["known_runs"] == [], "没归档过时要说清楚，而不是回一份当前日志"


def test_能读归档_而且读到的是那一天的内容(home):
    _write("uvicorn.out", "当前这份\n")
    (logtail.ROOT / ".local" / "uvicorn.out.20261009-213756.log").write_text(
        "上一次运行留下的现场\nAttributeError 在这一份里\n", encoding="utf-8")
    got = logtail.tail("uvicorn.out", 40, run="20261009-213756")
    assert got["available"] and got["is_archive"]
    assert "AttributeError" in "\n".join(got["lines"])
    assert got["run"] == "20261009-213756" and "20261009-213756" in got["runs"]


def test_归档里的_NUL_空洞要抹掉并留话(home):
    """2026-10-10 23:51 那份归档实测：55,776 字节的第 72~634 字节全是 0x00 ——
    PowerShell 的 `-RedirectStandardOutput` 留下的空洞，不是日志内容。
    原样送进界面就是屏幕上几百个看不见的字符，读起来像"日志坏了"（其实是读取器没处理）。
    空洞总在两条记录之间，上一条已经带了自己的换行，所以抹掉不会把两条拼成一条。"""
    (logtail.ROOT / ".local" / "uvicorn.out.20261010-235122.log").write_bytes(
        "第一条：React 构建产物已挂载\r\n".encode("utf-8")
        + b"\x00" * 300
        + "第二条：AttributeError 在这\n".encode("utf-8"))
    got = logtail.tail("uvicorn.out", 40, run="20261010-235122")
    assert got["available"] and got["nul_bytes_stripped"] == 300
    joined = "\n".join(got["lines"])
    assert "\x00" not in joined and "\ufffd" not in joined
    assert any("AttributeError" in ln for ln in got["lines"]), "抹 NUL 不许把真内容一起吞了"
    assert not any(ln.startswith("第一条") and "第二条" in ln for ln in got["lines"]), \
        "两条记录不许被并成一条"
    assert "NUL" in got["note"], got["note"]
    json.dumps(got, ensure_ascii=False, allow_nan=False)



# ------------------------------------------------------------------ 只读尾巴

def test_尾巴按行取_并且说清跳过了多少字节(home):
    body = "".join(f"第 {i} 行：中文也可能很长" + "×" * 60 + "\n" for i in range(2000))
    p = _write("uvicorn.out", body)
    got = logtail.tail("uvicorn.out", 30)
    assert got["returned"] <= 30 and got["returned"] > 0
    assert got["lines"][-1].startswith("第 1999 行"), got["lines"][-1][:20]
    assert got["skipped_bytes"] > 0, "跳过多少字节必须说出口，不能让读者以为这就是全文"
    assert p.stat().st_size == got["size"]
    assert "只读了尾巴" in got["note"]
    json.dumps(got, ensure_ascii=False, allow_nan=False)


def test_中文不会被劈成乱码也不能抛(home):
    """倒着按字节切，很容易落在一个汉字的第 2~3 字节上 —— 那一行本来就残缺，
    要么丢掉要么按 replace 解码，**不许抛**（一抛整个面板就红了，而日志其实是好的）。"""
    _write("uvicorn.out", "开头很长" * 4000 + "\n尾巴这一行要看得到\n")
    got = logtail.tail("uvicorn.out", 5)
    assert "尾巴这一行要看得到" in "\n".join(got["lines"])
    assert "\ufffd" not in got["lines"][-1]


def test_文件不存在与零字节是两句话(home):
    missing = logtail.tail("uvicorn.err")
    assert missing["available"] is False and "还不存在" in missing["reason"]
    _write("uvicorn.err", "")
    empty = logtail.tail("uvicorn.err")
    assert empty["available"] is True and empty["size"] == 0
    assert "0 字节" in empty["note"], "空文件要说清是「还没写过」，不是「没有这一层」"


def test_索引把每一份的位置大小和年龄都交出来(home):
    _write("uvicorn.out", "一行\n")
    idx = logtail.live_summary()
    names = {f["name"]: f for f in idx["files"]}
    assert set(names) == set(logtail.LOGS)
    assert names["uvicorn.out"]["exists"] and names["uvicorn.out"]["size"] > 0
    assert names["uvicorn.out"]["mtime"], "多久没写必须交出去（停在 14:37 那种坑靠它才看得见）"
    assert names["uvicorn.err"]["exists"] is False
    assert idx["limits"]["lines_max"] == logtail.LINES_MAX


def test_行数被夹在上下限之间(home):
    _write("uvicorn.out", "a\nb\n")
    assert logtail.tail("uvicorn.out", 99999)["returned"] <= logtail.LINES_MAX
    assert logtail.tail("uvicorn.out", 0)["asked"] == logtail.LINES_MIN
    assert logtail.tail("uvicorn.out", "非数字")["asked"] == 200, "参数烂掉要回默认值，不能抛"


def test_时间戳格式与前端约定一致(home):
    """接口上这些时刻是给人看的，前端直接摆出来；换成 ISO 带 T 也能读，但两处会不一致。"""
    _write("uvicorn.out", "x\n")
    st = logtail.tail("uvicorn.out", 20)
    assert dt.datetime.strptime(st["mtime"], "%Y-%m-%d %H:%M:%S")
    assert dt.datetime.strptime(st["generated_at"], "%Y-%m-%d %H:%M:%S")
    assert isinstance(st["mtime_age_seconds"], int)


# ------------------------------------------------------------------ 端点真的挂着

def test_端点已接线并且拒绝路径穿越():
    """"代码写了但没接"是这一层最可能的失效方式（`/api/jobs` 那次就是端点先写好、
    页面接不上），所以这里必须走一次真路由，而不是只测函数。"""
    from fastapi.testclient import TestClient
    import server

    c = TestClient(server.app, base_url="http://127.0.0.1:8910", client=("127.0.0.1", 1))
    ok = c.get("/api/system/log", params={"name": "uvicorn.err", "lines": 20})
    assert ok.status_code == 200
    d = ok.json()["data"]
    assert d["available"] is True and isinstance(d["index"]["files"], list) and d["index"]["files"]
    assert "path" in d and "mtime_age_seconds" in d

    bad = c.get("/api/system/log", params={"name": "../../.env"}).json()["data"]
    assert bad["available"] is False and "白名单" in bad["reason"]
    assert "lines" not in bad, "拒绝时不许顺手把内容带出去"
    assert c.get("/api/system/log", params={"lines": 999999}).status_code == 422, "行数要夹紧"
