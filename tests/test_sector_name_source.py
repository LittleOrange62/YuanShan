# -*- coding: utf-8 -*-
"""C7 的第二路名字来源（通达信名称表 `shs.tnf`）与"市场级统计指数"改判。

这块为什么值得单独钉住：`份额抬升端为什么全是六位代码` 的真答案不是"字典没名字"，
而是**那批根本不是板块** —— 880001~880099 是通达信的市场级统计指数（总市值 / 涨跌家数 /
主板总值…880099 通用回购），成交额逐日等于全 A，混在板块母体里必然霸榜。
2026-10-06 的两条实测事实同时写进测试，防止以后有人"顺手"把它们当板块补名：

1. 名字只能从 `shs.tnf` 拿到（`tdxzs*.cfg` 四份里这批码**一条都没有**），
   而那张表是定长记录 —— 结构判据（间距恒定 / 代码列后全 0 / GBK 可解）任何一条不过，
   整段放弃、返回空 + 原因，绝不"差不多地"解出一半名字。
2. 改判 `sector_type='统计'` 的 WHERE 里**必须带着名字**：类型只能由名字推出，
   代码段本身不算证据（通达信改了名就该跳过并报名，而不是照段改）。
"""
from __future__ import annotations

import pytest


CODE_FIELD = 12          # 与 c7.TNF_NAME_COL 对应用：名字列固定从第 31 字节起
NAME_COL = 31
STRIDE = 360


def rec(code: str, name: str, stride: int = STRIDE) -> bytes:
    """造一条 c7 认的那个形状的记录：代码 + 填充到 31 + GBK 名字 + 填充。"""
    body = code.encode("ascii")
    assert len(body) <= NAME_COL, code
    name_bytes = name.encode("gbk") + b"\x00"
    tail = stride - NAME_COL - len(name_bytes)
    assert tail >= 0, (code, name)
    return body + b"\x00" * (NAME_COL - len(body)) + name_bytes + b"\x00" * tail


def buf(records: list[bytes], head: bytes = b"\x00" * 14) -> bytes:
    return head + b"".join(records)


@pytest.fixture()
def c7():
    from ingest.jobs import c7_sector_name as m
    return m


def test_tnf_结构判据全过就给出名字(c7):
    # 判据里有"至少 100 条"，测试给一份够长的
    raw = buf([rec("88%04d" % (100 + i), "名%d" % i) for i in range(120)])
    names, why = c7.parse_tnf(raw)
    assert names["880100"] == "名0", why
    assert names["880219"] == "名119", why
    assert "间距恒" in why and "通过 120" in why, why


def test_tnf_间距不恒定就整段放弃(c7):
    """结构不是我认的那个样子时，宁可交空 —— 半解的名字比没名字更害人。"""
    good = b"".join(rec("88%04d" % (100 + i), "名%d" % i) for i in range(60))
    other = b"".join(rec("88%04d" % (200 + i), "别%d" % i) for i in range(60))
    names, why = c7.parse_tnf(buf([good + b"\x00" * 17 + other]))
    assert names == {}
    assert "间距" in why, why


def test_tnf_代码列后不是填充零就不算记录首(c7):
    """`880005` 作为别的数据里的数字出现时，不能被当成代码列。"""
    raw = buf([rec("88%04d" % (100 + i), "名%d" % i) for i in range(119)])
    bad = b"880999" + b"XX" + "串名".encode("gbk") + b"\x00" + b"\x00" * (STRIDE - 16)
    names, _ = c7.parse_tnf(raw + bad)
    assert "880999" not in names
    assert names["880100"] == "名0"


def test_tnf_名字解不出可读中文就丢弃(c7):
    raw = buf([rec("88%04d" % (100 + i), "名%d" % i) for i in range(119)])
    # 最后一条给一段非法 GBK 字节（0x81 单独出现是非法序列）
    raw += b"880999" + b"\x00" * (NAME_COL - 6) + b"\x81" + b"\x00" * (STRIDE - NAME_COL - 1)
    names, why = c7.parse_tnf(raw)
    assert "880999" not in names
    assert "拒绝 1" in why, why


def test_tnf_条数太少不给名字(c7):
    names, why = c7.parse_tnf(buf([rec("880001", "总市值"), rec("880002", "流通市值")]))
    assert names == {} and "少于" in why, why


def test_statistical_名单与名字一一对应(c7):
    """这批码是唯一可改判的集合：全在 880001~880099 段，且每条都有实测名字。"""
    assert len(c7.STATISTICAL) == 48
    for code, name in c7.STATISTICAL.items():
        assert code.startswith("8800") and len(code) == 6, code
        assert name and not name.isdigit(), code
    # 880595 在名称表里查不到（实测），所以它**不在**名单里、也就不会被改判
    assert "880595" not in c7.STATISTICAL


def test_statistical_名字一致才动手(c7):
    todo, drift = c7.statistical_updates(dict(c7.STATISTICAL))
    assert len(todo) == 48 and drift == []
    assert {"sector_code", "name"} <= set(todo[0].keys())
    # 交出去的每条都带着"要改成什么名字"，SQL 才能拿名字当条件
    assert all(t["sector_code"] == "TDX_" + code and t["name"] == c7.STATISTICAL[code]
               for t in todo for code in [t["sector_code"][-6:]])


def test_statistical_通达信改名后跳过并报名(c7):
    """名字对不上时不许按代码段盲改 —— 那与"按数量猜行业名"是同一类错。"""
    shifted = dict(c7.STATISTICAL)
    shifted["880001"] = "总市值(新)"
    del shifted["880005"]
    todo, drift = c7.statistical_updates(shifted)
    codes = {t["sector_code"] for t in todo}
    assert "TDX_880001" not in codes and "TDX_880005" not in codes
    assert len(todo) == 46
    assert any("880001" in d for d in drift) and any("880005" in d for d in drift), drift


def test_改判的sql必须带名字条件(c7):
    """类型只能由名字推出：UPDATE 的 WHERE 里 `name = %(name)s` 是与 sector_code 并列的条件。"""
    from pathlib import Path
    src = Path(c7.__file__).read_text(encoding="utf-8")
    at = src.index("SET sector_type")
    block = src[at:at + 240]
    assert "WHERE sector_code = %(sector_code)s AND name = %(name)s" in block, block
