"""测试全局约束：**禁止真实出站网络**（本机回环除外）。

本目录只测纯逻辑。一旦某条测试忘了 patch 数据源（比如 `latest_session()` 内部
会去打腾讯行情判断今天是否开市），它就会变成"依赖网络 + 依赖今天是不是交易日"
的脆弱测试——平时绿、周末或断网时红，而且失败原因看不出来。
这里直接把 connect 拦掉：谁漏了 patch 就当场以明确信息失败。
回环地址放行：Responses 转换层与它的假网关都只能工作在本机上。
"""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import pytest

# 会话库在线默认走 PostgreSQL（用户裁定"PG 是唯一数据库"，本机 .env 里有 ft_etl 就生效）。
# 但测试必须各自关在 tmp_path 里：否则一百多条会话测试会往真库互相写、断言跟着漂。
# PG 后端不是没测 —— 由 tests/test_session_pg.py 显式打开并自己清场。
os.environ.setdefault("YS_SESSION_BACKEND", "sqlite")

# `duanxian.*` 里二十多个模块在**导入期**就把 `data_path(...)` 绑成模块级常量
# （`review_store.DIR`、`backtest.RESULT_DIR`、`breadth._CACHE_DIR`…），所以测试里再
# monkeypatch 只盖得住其中一个模块。2026-09-25 实测：单独跑
# `TestFailedReviewNeverClobbersGood` 那 7 条测试，就往 `~/.duanxian-agents/reviews/_rejected/`
# 里真写出 2 份假拒稿 —— 那批文件此后被当成"本机复盘全拒稿"的证据链引用过。
# 唯一可靠的收口点是在任何 duanxian 模块被导入之前，把整个数据根目录换掉。
_DATA_HOME = Path(tempfile.mkdtemp(prefix="yuanShan-tests-")).resolve()
os.environ["ASTOCK_DATA_HOME"] = str(_DATA_HOME)

REAL_CONNECT = socket.socket.connect


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and (host.startswith("127.") or host in ("::1", "localhost")):
            return REAL_CONNECT(self, address, *args, **kwargs)
        raise AssertionError(
            "测试期间不允许出站网络连接 —— 说明有数据源没被 patch，请补上 monkeypatch"
        )

    monkeypatch.setattr(socket.socket, "connect", blocked, raising=False)


@pytest.fixture(autouse=True)
def _pg_stubs_never_leak():
    """直接赋值打桩 `pg.fetch_df` 的测试用完不还，会把后面的模块一起打红。

    2026-09-27 实测：全跑时 `tests/test_sector_members.py` 四条红、单跑全绿，报错
    是 `未预期的 SQL: select sector_code ...` —— 那句话来自 `test_coverage.py` 里
    的假 `fetch_df`（`cov.pg.fetch_df = _fake(...)`，不是 monkeypatch，按模块对象
    换的、没有 teardown）。这类"单跑绿"的绿灯判不了非回归。
    这里兜住全局：每条测试结束都按测试开始时的样子还原 `duanxian.pg` 的命名空间。
    """
    try:
        from duanxian import pg
    except Exception:                       # 起不来的话没什么可还原的，别把全套拖死
        yield
        return
    original = dict(vars(pg))
    try:
        yield
    finally:
        if vars(pg) is not original:
            vars(pg).clear()
            vars(pg).update(original)

