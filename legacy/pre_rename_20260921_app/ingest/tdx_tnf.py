# -*- coding: utf-8 -*-
"""
通达信 .tnf 文件解析 —— 证券代码与名称表。

文件位置：D:\\new_tdx64\\T0002\\hq_cache\\{shs,szs,bjs}.tnf

结构（实测）：
    50 字节文件头，之后是定长 360 字节的记录
    记录内 offset 0  起 6 字节  ASCII 代码，如 b'600367'
    记录内 offset 31 起若干字节 GBK 名称，以 \\x00 截断

为什么必须解析它：`tq_core.db` 里 9,468 只证券的 name 字段**全是 NULL**，
股本、行业、上市日期也全空。名称只能从这里来。名称还决定 is_st ——
ST 股主板涨跌停是 5% 而不是 10%，名称错了涨跌停就算错。

解析器不信任常量：`records()` 会校验代码是否为 6 位数字，命中率过低直接报错，
避免偏移量假设错了却静默产出垃圾。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app import config

HEADER_SIZE = 50
RECORD_SIZE = 360
CODE_OFFSET = 0
CODE_SIZE = 6
NAME_OFFSET = 31
NAME_MAX = 24

CODE_RE = re.compile(r'^\d{6}$')

MARKET_BY_FILE = {'shs.tnf': 'SH', 'szs.tnf': 'SZ', 'bjs.tnf': 'BJ'}

# 名称里带这些标记的是风险警示股，主板涨跌停 5%
ST_MARKERS = ('ST', '*ST', 'S*ST', 'SST')


@dataclass(frozen=True)
class SecurityName:
    symbol: str
    market: str
    name: str

    @property
    def ts_code(self) -> str:
        return f'{self.symbol}.{self.market}'

    @property
    def is_st(self) -> bool:
        upper = self.name.upper().replace(' ', '')
        return any(m in upper for m in ST_MARKERS)


def _decode_name(raw: bytes) -> str:
    raw = raw.split(b'\x00', 1)[0].strip()
    if not raw:
        return ''
    for enc in ('gbk', 'gb18030'):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode('gbk', errors='replace').strip()


def parse_file(path: Path, market: str | None = None,
               *, min_hit_rate: float = 0.8) -> list[SecurityName]:
    """
    解析单个 .tnf。min_hit_rate 是代码字段的合法率下限，
    低于它说明偏移量假设错了，直接抛错而不是返回半截数据。
    """
    market = market or MARKET_BY_FILE.get(path.name.lower())
    if not market:
        raise ValueError(f'无法从文件名判断市场：{path.name}')

    blob = path.read_bytes()
    if len(blob) <= HEADER_SIZE:
        raise ValueError(f'{path.name} 只有 {len(blob)} 字节，不足文件头长度')

    body = blob[HEADER_SIZE:]
    total = len(body) // RECORD_SIZE
    if total == 0:
        raise ValueError(f'{path.name} 去掉文件头后不足一条记录')

    out: list[SecurityName] = []
    bad = 0
    for i in range(total):
        rec = body[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]
        code = rec[CODE_OFFSET:CODE_OFFSET + CODE_SIZE].decode('ascii', 'replace')
        if not CODE_RE.match(code):
            bad += 1
            continue
        name = _decode_name(rec[NAME_OFFSET:NAME_OFFSET + NAME_MAX])
        out.append(SecurityName(symbol=code, market=market, name=name))

    hit = len(out) / total
    if hit < min_hit_rate:
        raise ValueError(
            f'{path.name} 代码字段合法率只有 {hit:.1%}（{len(out)}/{total}），'
            f'说明 HEADER_SIZE={HEADER_SIZE} / RECORD_SIZE={RECORD_SIZE} / '
            f'CODE_OFFSET={CODE_OFFSET} 的假设与实际文件不符，拒绝返回可疑数据')
    return out


def parse_all(cache_dir: Path | None = None) -> list[SecurityName]:
    """解析 shs/szs/bjs 三个文件，返回合并结果。"""
    cache_dir = cache_dir or (config.TDX_DIR / 'T0002' / 'hq_cache')
    out: list[SecurityName] = []
    for fname, market in MARKET_BY_FILE.items():
        p = cache_dir / fname
        if not p.exists():
            continue
        out.extend(parse_file(p, market))
    return out


def name_map(cache_dir: Path | None = None) -> dict[str, SecurityName]:
    """ts_code -> SecurityName。同代码后出现的覆盖先出现的。"""
    return {s.ts_code: s for s in parse_all(cache_dir)}
