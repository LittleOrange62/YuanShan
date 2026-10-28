# -*- coding: utf-8 -*-
"""`duanxian/pg.py::fetch_df` 搬家到连接池之后的行为契约（2026-10-26）。

为什么要这一份：这一层是全站只读取数的**唯一入口**（66 个调用点），它改的是
"怎么连"而不是"连什么"，所以坏起来是**静默**的 —— 一律 fail-soft 交 `None`，
屏幕上只会写"没有数据"。四条必须钉住：

1. 走 `app/db/engine.py` 那张已有的 reader 池，不再每发新建连接
   （实测一发握手 76~80ms → 借池 0.7~2.7ms）；
2. **字面 `%`** 的语句在不带参数时不能被当成格式串（很多 SQL 里写 `LIKE 'TDX\\_%%'`）；
3. 日期列交 `str`、`timestamptz` 的文本仍带 `+08:00`（池上的连接带
   `-c timezone=Asia/Shanghai`，与原来那条 psycopg2 会话的默认一致 —— 这条不一致
   屏幕上"这批算于几点"就会整体平移 8 小时）；
4. 失败仍交 `None`，但日志要分得开"连接配置没配"与"这条 SQL 坏了"。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_pg_pool.py -q`
（连不上库时真库那几条自动 skip。）
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from duanxian import pg          # noqa: E402

SRC = (ROOT / "duanxian" / "pg.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def live():
    if pg.fetch_df("select 1 as one") is None:
        pytest.skip("库连不上（或 reader DSN 没配）：这一条要真库读数才能对账")


def test_走的是那张已有的池而不是每发新建一条连接():
    assert "engine.conn('reader')" in SRC, "只读取数要借 app/db/engine 的 reader 池"
    assert "import psycopg2" not in SRC, \
        "这一层不许再自己开连接（留着就没人知道走的是哪条）—— 注意查的是 import，不是文档里的字"
    # 空参数必须原样执行 —— 有参数的执行会让驱动把语句当格式串，字面 % 的语句当场炸
    assert "if params:" in SRC and "cur.execute(sql)" in SRC


def test_字面百分号在不带参数时也要能跑(live):
    got = pg.fetch_df("select 'TDX\\_880501' as s, ('TDX\\_880501' like '%8805%') as m")
    assert got is not None and got["s"].iloc[0] == "TDX\\_880501"
    assert bool(got["m"].iloc[0]) is True, "这句里的 % 是字面量，不是格式符"
    # 同一句改成带参数执行也要通（占位符那条路仍然在）
    n = pg.fetch_df("select 1 from (values (1)) v(x) where x = %s", (2,))
    assert n is not None and len(n) == 0


def test_日期列交字符串且带时区的时间戳仍是正八区(live):
    d = pg.fetch_df("select trade_date from market_data.stock_daily order by trade_date desc limit 1")
    assert d is not None and len(d) == 1
    assert isinstance(d["trade_date"].iloc[0], str), "读侧到处是 str(...)[:10]，日期列必须收成 str"
    assert d["trade_date"].iloc[0][:4].isdigit()
    t = pg.fetch_df("select now()::timestamptz as t, current_setting('TimeZone') as tz")
    assert t["tz"].iloc[0] == "Asia/Shanghai", f"会话时区不是上海：{t['tz'].iloc[0]}"
    assert str(t["t"].iloc[0]).endswith("+08:00"), \
        f"timestamptz 文本没有 +08:00，屏幕上的算时刻会整体平移：{t['t'].iloc[0]!r}"


def test_坏语句交none而不是抛出来(live):
    assert pg.fetch_df("select 这列不存在") is None


def test_失败日志分得开连接配置与坏语句():
    """整层静默降级成「读不到」是最难查的一种坏 —— 屏幕上那句话会写成"没有数据"。"""
    assert "连接配置" in SRC and "DSN" in SRC, "缺 FT_DSN_READER 时要按『连接配置』报，不能混成查询失败"
    assert 'kind = ' in SRC


def test_入口仍然只有一处():
    """调用点认的是 `pg.fetch_df`；谁另开一个只读入口，池与那两步清洗就都绕过了。"""
    assert SRC.count("def fetch_df(") == 1
    users = sum(1 for p in list((ROOT / "duanxian").rglob("*.py")) + list((ROOT / "vr").rglob("*.py"))
                if "fetch_df(" in p.read_text(encoding="utf-8"))
    assert users >= 10, f"只有 {users} 个文件在用 fetch_df，多半是有人另开了入口"
