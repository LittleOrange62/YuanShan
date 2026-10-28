"""`config.setting` 的读取 shim（对齐 FT `app/db/repo/registry.py:setting`）。

d 系列里有三处按 `config.setting` 取参数（点火阈值、趋势窗口列表、倍量过滤覆盖），
读的是**同一份库里的同一张表**，所以行为必须一致：读不到就返回调用方给的 default，
不自己编一份默认值 —— 默认值写两份，迟早和库里的分叉。
"""

from __future__ import annotations

from . import engine


def setting(key: str, default=None, *, role: str = 'reader'):
    v = engine.scalar('SELECT value FROM config.setting WHERE key = %s', (key,), role=role)
    return default if v is None else v
