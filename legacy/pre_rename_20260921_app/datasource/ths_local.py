# -*- coding: utf-8 -*-
"""
同花顺本地文件解析（只读，不联网）。

**为什么要有这个模块**：通达信本地是行情的权威源（9,491 只 × 5 年日线），但它
的**板块成分股**解不出来 —— `infoharbor_block.dat` / `spblock.dat` 是私有二进制，
`tdxzs.cfg` 只给板块名不给成员。结果是 `ref.sector_info` 里 231 个行业板块
**有日线行情、没有一只成分股**，板块广度/热度的分母是空的。

同花顺恰好把这些东西**明文**放在本地，而且每天随客户端刷新（实测
`ConfigVer=20260830.095501`）：

    BlockUpdate\\block_*.ini   概念 390 + 细分概念 416 + 地域 36 + 特色指数 106
    industry.ini              90 个同花顺行业（有中文名）+ 5,212 只成分股
    <用户名>\\stockblock.ini    43 个自选分组 + 2,392 条成员  ← 通达信的 zxg.blk 没有分组
    <用户名>\\SelfStockInfo.json 自选股的加入价与加入日期

**同花顺的 K 线一律不要**：`history\\` 里只有 9 只 A 股的 `.day`，那是"打开过的
票"的缓存，不是本地库。9 : 9,491，没有讨论空间。

---- 三条硬规矩 ----

1. **认不出的代码丢掉，绝不猜。** 市场前缀（`17`=SH / `33`=SZ / `-105`=BJ …）
   只映射已经实测对上的那几个，其余连同板块指数（`48`/`49`）一起计入
   `dropped`，由调用方如实报数。猜一个市场后缀的代价是库里多一条永远
   join 不上 `ref.security` 的孤行，页面上表现为"有这只票但没名字没行情"。
2. **只读文件，不写库、不联网。** 入库逻辑在 `scripts/ingest/c13_*`、`c14_*`，
   这里保持纯函数，测试才能不连库地跑。
3. **GBK 用 replace 解码。** 同花顺的 ini 里混着用户自己打的字，遇到坏字节
   宁可显示成替换字符，也不能整个文件读不出来。
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path

from app import config

# ---------------------------------------------------------------- 市场前缀
# 同花顺在成分股清单里用 `<市场>:<代码>` 表示一只票。这张表**只放实测对上的**：
# 17→.SH 与 33→.SZ 各抽 200 个全部命中 ref.security，-105/151/-111 是北交所
# （920xxx / 83xxxx），22 是上交所的另一类（600053 之类，仍是 .SH）。
MARKET = {
    '17': 'SH',
    '22': 'SH',
    '33': 'SZ',
    '-105': 'BJ',
    '-111': 'BJ',
    '151': 'BJ',
}
# 明确知道"不是个股"的市场号：48/49 是同花顺自己的板块指数（886xxx/884xxx），
# 16/32 是沪深指数，20/36 是基金，-71/-87/-95 是外盘。这些出现在自选和板块里
# 是正常的，只是不该写进个股成员表 —— 单独计数，不当成解析失败。
NOT_STOCK = {'16', '32', '20', '36', '48', '49', '88', '104', '112', '120',
             '128', '144', '168', '176', '184', '200', '216',
             '-71', '-87', '-95', '169', '185', '145'}

_ITEM = re.compile(r'^(-?\d+):(\w+)$')

# 只要这几类板块。`block_*.ini` 里还有港股/美股概念/英股行业/基金/"其他树根
# 节点"，都不是 A 股板块，不入库。判据是文件根节点的中文名，不是文件名 ——
# 文件名里的十六进制 id 每次升级都可能变，根节点名不变。
WANTED_ROOTS = ('概念', '地域', '特色指数', '指标股')

# 同花顺的板块分类 → 库里 `ref.sector_info.sector_type` 的既有词表
# （行业 / 概念 / 地区 / 风格 / 指数）。**不新造词**：
# `q_score_rank` 挑"最热板块"时只认 `sector_type IN ('行业','概念')`，
# 新造一个"特色指数"会让那些指数悄悄挤进个股的板块归属里。
SECTOR_TYPE = {
    '概念': '概念',
    '地域': '地区',
    '特色指数': '指数',
    '指标股': '风格',
    '行业': '行业',
}


def ths_dir() -> Path:
    """
    同花顺安装目录。**`config.setting['ths_dir']` 优先**（`/admin` 上能改，
    不用改代码也不用重启改环境变量），没配就用 `FT_THS_DIR` / 代码默认值。

    `import` 放在函数里是有意的：这个模块本身要能在**不连库**的情况下 import
    并跑测试（测试都显式传 `base`），不能因为一个可选配置把 DB 变成硬依赖。
    """
    try:
        from app.db.repo import registry as reg_repo
        value = reg_repo.setting('ths_dir')
    except Exception:  # noqa: BLE001  库没起来也要能读文件
        value = None
    return Path(value) if value else config.THS_DIR


def _read_ini(path: Path) -> OrderedDict[str, list[tuple[str, str]]]:
    """
    同花顺的 ini 解析。**不用 configparser**，原因有三个：

    - 组名里有 `%`、`:`、中文冒号，configparser 的插值会炸
    - 用户自己起的组名可能是多行的（"隔夜持股法：\\n1. 下午2点40…"），
      续行没有 `=`，configparser 直接抛 MissingSectionHeaderError 之类
    - 同一个 section 里 key 可能重复，dict 会吃掉前一个

    返回 `{section: [(key, value), ...]}`，**保留原始顺序**（组的排序要用它）。
    多行值只取第一行 —— 组名显示在表格里，第二行往后是备注性质的东西。
    """
    out: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    cur: str | None = None
    for raw in path.read_bytes().decode('gbk', 'replace').splitlines():
        line = raw.strip()
        if line.startswith('[') and line.endswith(']'):
            cur = line[1:-1]
            out.setdefault(cur, [])
        elif '=' in line and cur is not None:
            k, v = line.split('=', 1)
            out[cur].append((k.strip(), v.strip()))
    return out


def to_ts_code(market: str, code: str) -> str | None:
    """
    `('17', '600519')` → `'600519.SH'`。认不出返回 None，**不猜**。

    两道判据都必须过：市场号在 `MARKET` 里、代码段属于该市场的 A 股代码段
    （`config.A_SHARE_PREFIX`）。只判"6 位数字"是不够的 —— 同花顺的板块清单里
    混着 `17:821000` 这种通配写法，它是 6 位数字、市场号也合法，但不是任何一只
    A 股。放过去就会往 `ref.sector_member` 里塞不存在的代码，而那张表没有外键
    能拦住它，只能靠入库脚本兜。判据留在这里，谁调用都拦得住。
    """
    suffix = MARKET.get(market)
    if not suffix or not (code.isdigit() and len(code) == 6):
        return None
    if not code.startswith(config.A_SHARE_PREFIX[suffix]):
        return None
    return f'{code}.{suffix}'


def _parse_members(text: str) -> tuple[list[str], dict[str, int]]:
    """
    `'17:600519,33:000001,'` → `(['600519.SH', '000001.SZ'], 丢弃计数)`。

    丢弃计数按市场号分桶，让调用方能说清"丢的是板块指数还是没认出来的东西"。
    """
    codes: list[str] = []
    dropped: dict[str, int] = {}
    for item in text.split(','):
        item = item.strip()
        if not item:
            continue
        m = _ITEM.match(item)
        if not m:
            dropped['格式不认识'] = dropped.get('格式不认识', 0) + 1
            continue
        market, code = m.group(1), m.group(2)
        ts = to_ts_code(market, code)
        if ts:
            codes.append(ts)
        elif market in NOT_STOCK:
            key = market                       # 已知的非个股市场（板块指数等）
        elif market in MARKET:
            key = f'非A股代码段 {code[:3]}'      # 市场认得，代码段不是个股
        else:
            key = f'未知市场 {market}'
        if not ts:
            dropped[key] = dropped.get(key, 0) + 1
    # 同一个板块里同一只票可能出现两次（细分概念合并进父概念时），去重但保序
    return list(dict.fromkeys(codes)), dropped


def _merge_dropped(into: dict[str, int], more: dict[str, int]) -> None:
    for k, v in more.items():
        into[k] = into.get(k, 0) + v


# ---------------------------------------------------------------- 板块
def read_blocks(base: Path | None = None) -> dict:
    """
    概念 / 地域 / 特色指数 / 指标股 + 成分股。

    返回 `{'sectors': [{code,name,kind,level,members}], 'dropped': {...},
            'config_ver': '20260830.095501', 'files': n}`。

    `code` 形如 `THS_BF4D` —— 十六进制那段是同花顺自己的板块 id。
    ★它**不保证跨版本稳定**，所以入库是"整批替换"而不是增量：id 变了就是
    一个新板块，旧的连同成员一起删掉，不留半张脸。
    """
    base = base or ths_dir()
    d = base / 'BlockUpdate'
    out: list[dict] = []
    dropped: dict[str, int] = {}
    ver = None
    files = 0
    if not d.is_dir():
        return {'sectors': [], 'dropped': {}, 'config_ver': None, 'files': 0}
    for path in sorted(d.glob('block_*.ini')):
        block_id = path.stem.split('_', 1)[1]
        ini = _read_ini(path)
        names = dict(ini.get('BLOCK_NAME_MAP_TABLE', []))
        root = names.get(block_id)
        if not root or not root.startswith(WANTED_ROOTS):
            continue
        files += 1
        for k, v in ini.get('ConfigInfo', []):
            if k == 'ConfigVer' and ver is None:
                ver = v.split('|', 1)[0]
        sub_names = dict(ini.get('SUBDIVISION_BLOCK_NAME_MAP_TABLE', []))
        for section, level in (('BLOCK_STOCK_CONTEXT', 1),
                               ('SUBDIVISION_BLOCK_STOCK_CONTEXT', 2)):
            table = names if level == 1 else sub_names
            for code, text in ini.get(section, []):
                name = table.get(code) or names.get(code) or sub_names.get(code)
                if not name or code == block_id:
                    continue
                members, dr = _parse_members(text)
                _merge_dropped(dropped, dr)
                if not members:
                    continue
                out.append({'code': f'THS_{code}', 'name': name.strip(),
                            'kind': SECTOR_TYPE[_root_kind(root)],
                            'src': root, 'level': level, 'members': members})
    return {'sectors': out, 'dropped': dropped, 'config_ver': ver, 'files': files}


def _root_kind(root: str) -> str:
    """根节点名可能带后缀（"同花顺概念"之类），取能匹配上的那个前缀词。"""
    for key in SECTOR_TYPE:
        if root.startswith(key):
            return key
    return '概念'


def read_industry(base: Path | None = None) -> dict:
    """
    同花顺行业（`industry.ini`）。90 个二级行业，每个带中文名与成分股。

    `[industry]` 段里除了 `881xxx` 还有一堆 `VIxx`（期货/外盘品种分类），
    按 key 是不是 `881` 开头过滤 —— `[name]` 段里也只有这 90 个有名字。
    """
    base = base or ths_dir()
    path = base / 'industry.ini'
    if not path.is_file():
        return {'sectors': [], 'dropped': {}}
    ini = _read_ini(path)
    names = dict(ini.get('name', []))
    out: list[dict] = []
    dropped: dict[str, int] = {}
    for code, text in ini.get('industry', []):
        if not code.startswith('881'):
            continue
        # 这一段的成分股是**裸 6 位代码**（没有市场前缀），市场靠代码段判断：
        # 6/9 开头是沪、0/1/2/3 开头是深、8/4 开头是北交所。
        codes: list[str] = []
        for raw in text.split(','):
            c = raw.strip()
            if not (c.isdigit() and len(c) == 6):
                if c:
                    dropped['格式不认识'] = dropped.get('格式不认识', 0) + 1
                continue
            ts = _bare_to_ts(c)
            if ts:
                codes.append(ts)
            else:
                dropped[f'未知代码段 {c[:2]}'] = dropped.get(f'未知代码段 {c[:2]}', 0) + 1
        if not codes:
            continue
        out.append({'code': f'THS_{code}', 'name': (names.get(code) or code).rstrip(';'),
                    'kind': '行业', 'level': 2, 'members': list(dict.fromkeys(codes))})
    return {'sectors': out, 'dropped': dropped}


def _bare_to_ts(code: str) -> str | None:
    """裸 6 位代码 → ts_code。只认 A 股三个市场的代码段，其余返回 None。"""
    for suffix, prefixes in config.A_SHARE_PREFIX.items():
        if code.startswith(prefixes):
            return f'{code}.{suffix}'
    return None


# ---------------------------------------------------------------- 自选分组
def user_dirs(base: Path | None = None) -> list[Path]:
    """
    同花顺把每个登录用户的配置放在**以用户名命名的目录**里（本机是
    `炒股大王哈哈`）。判据是目录里有没有 `stockblock.ini`，不写死用户名。
    """
    base = base or ths_dir()
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir()
                  if p.is_dir() and (p / 'stockblock.ini').is_file())


def read_watchlist_groups(base: Path | None = None) -> dict:
    """
    自选分组：组名 + 成员 + 加入价 + 加入日期。

    **这是 `.sel` 文件里没有的东西**（2026-08-30 更正：上一轮结论"分组只存在于
    客户端内存里"是错的 —— 它就在 `stockblock.ini` 的
    `[BLOCK_NAME_MAP_TABLE]` 里，43 个中文组名）。

    返回 `{'user': 目录名, 'groups': [{name, seq, members, ...}],
            'dropped': {...}, 'quote': {ts_code: {cost, added}}}`。
    组的顺序按文件里出现的顺序 —— 那就是客户端左边栏的顺序。
    """
    dirs = user_dirs(base)
    if not dirs:
        return {'user': None, 'groups': [], 'dropped': {}, 'quote': {}}
    udir = dirs[0]
    ini = _read_ini(udir / 'stockblock.ini')
    names = OrderedDict(ini.get('BLOCK_NAME_MAP_TABLE', []))
    ctx = dict(ini.get('BLOCK_STOCK_CONTEXT', []))
    dropped: dict[str, int] = {}
    groups: list[dict] = []
    for seq, (code, name) in enumerate(names.items(), start=1):
        # 空组也保留：用户刚建了个还没往里放票的组，全量覆盖时也该建出来，
        # 否则"同花顺上有 43 个组、导进来只剩 41 个"没人说得清少了哪两个。
        members, dr = _parse_members(ctx.get(code, ''))
        _merge_dropped(dropped, dr)
        groups.append({'code': code, 'name': name.strip(), 'seq': seq,
                       'members': members})
    return {'user': udir.name, 'groups': groups, 'dropped': dropped,
            'quote': read_self_stock_info(udir)}


def read_self_stock_info(udir: Path) -> dict[str, dict]:
    """
    `SelfStockInfo.json` → `{ts_code: {'cost': float|None, 'added': 'YYYYMMDD'}}`。

    `P` 是加入自选时的价格、`T` 是加入日期 —— 正好当成本价与关注起始日。
    两者都可能是空串（刚加还没成交价），空就给 None，不填 0：
    成本 0 会让盈亏显示成 +∞%。
    """
    path = udir / 'SelfStockInfo.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_bytes().decode('utf-8', 'replace'))
    except (ValueError, OSError):
        return {}
    out: dict[str, dict] = {}
    for item in data if isinstance(data, list) else []:
        ts = to_ts_code(str(item.get('M', '')), str(item.get('C', '')))
        if not ts:
            continue
        price = str(item.get('P') or '').strip()
        try:
            cost = float(price) if price else None
        except ValueError:
            cost = None
        added = str(item.get('T') or '').strip() or None
        out[ts] = {'cost': cost, 'added': added}
    return out


def summary(base: Path | None = None) -> dict:
    """一次读完三块，给探测与 `--dry-run` 用。"""
    blocks = read_blocks(base)
    industry = read_industry(base)
    wl = read_watchlist_groups(base)
    return {
        'dir': str(base or ths_dir()),
        'config_ver': blocks['config_ver'],
        'block_files': blocks['files'],
        'blocks': len(blocks['sectors']),
        'block_members': sum(len(s['members']) for s in blocks['sectors']),
        'industries': len(industry['sectors']),
        'industry_members': sum(len(s['members']) for s in industry['sectors']),
        'user': wl['user'],
        'groups': len(wl['groups']),
        'group_members': sum(len(g['members']) for g in wl['groups']),
    }
