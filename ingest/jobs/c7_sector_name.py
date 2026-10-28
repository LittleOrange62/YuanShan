# -*- coding: utf-8 -*-
"""
C7 · 从通达信配置回填板块中文名。

**为什么要有这一步**：`reference_data.sector_info` 是从旧 SQLite 迁来的快照，里面有 128 个
`sector_type='行业'` 的板块，`name` 字段存的就是代码本身（'880005'）。
后果不是"少了个名字"这么轻——板块热度榜、板块轮动热力图的 y 轴全是六位数字，
图看不懂；`/sector` 页面上也无法判断某一行说的是哪个行业。

**名字从哪来**：通达信自己的索引配置。格式是 GBK 的竖线分隔文本：

    轮动趋势|880081|5|2|0|轮动趋势
    黑龙江|880201|3|1|0|1
             ↑ 第 2 列是六位板块码，第 1 列是中文名

覆盖情况实测（2026-08-27，D:\\new_tdx64）：
    tdxzs.cfg      604 行  →  128 个无名板块里能认出 79 个
    tdxzsbase.cfg           →  再认出 1 个
    合计 80 个，剩下 48 个是 880001~880099 段。

**剩下那 48 个为什么不猜**：那批码在 `tdxzs*.cfg` 四份配置里**一条都不存在**（2026-10-06 实测
`grep 880005 → 零命中`）。`incon.dat#TDXNHY` 里确实有 145 条 `T01|能源` 这样的层级名，但那套是 T 码，
跟 880xxx 没有任何一列能对上。按"数量差不多、顺序应该一致"去映射就是猜，
猜错的行业名比六位数字更害人 —— 页面上"煤炭"其实是"银行"，复盘会直接读错。

**2026-10-06 的两个更正**（都是量出来的，不是想通的）：

1. 名字**是有权威来源的**：通达信自己的证券名称表 `T0002\\hq_cache\\shs.tnf`。
   GBK 定长记录，代码列从记录首字节起、名字列固定从第 31 字节起，
   那一段 1,120 条记录相邻偏移差恒为 360（恒量对不上就整段放弃，不"差不多"地解）。
   拿它跟 `tdxzs.cfg` 交叉对过：两边都有的 1,072 个码里 604 个完全一致，
   剩下 468 个是 cfg 那一列写的就是 '1'（那种行本来就没名字），没有一条是真的互相矛盾。
2. ⚠️ **这批不是行业板块，是通达信做的"市场级统计指数"** —— 早前 docstring 说它们是
   "一级/二级行业指数"是错的。名字自己就说清了：
   880001 总市值 / 880002 流通市值 / 880003 平均股价 / 880005 涨跌家数 / 880006 停板家数 /
   880008 全Ａ等权 / 880011~880019 主板… / 880031~880039 创业… / 880041~880049 科创… /
   880071~880079 北证… / 880096 ETF等权 / 880097 REITs / 880098 可转债 / 880099 通用回购。
   它们在册里挂的是 `sector_type='行业'`，于是每一张板块榜的母体里都混进了"整个市场"：
   实测 `market_data.sector_daily` 里这 48 个的 `amount_yuan` 逐日等于全 A 当日成交额
   （09-30：全 A 段 14,501 亿、主板段 8,552、创业 3,690、科创 2,136、北证 123），
   份额口径下它们**必然**排第一 —— 这才是「份额抬升端为什么全是代码」的真答案：
   看到的既不是板块、也不是缺名字那么简单，而是榜的母体被市场级指数占了头名。
   所以这里除了回填名字，还把这一批重新分类成 `sector_type='统计'`：
   凡是按 `('行业','概念')` 正列表取母体的读取方（板块热度、份额轮动、qd 轨板块周期、
   分配者、加工侧的 sector_flow_trend 参数行）从此自动把它们排除在外。
   分类**只在名字与上面那份实测记录一致时才做**（`check_statistical`），
   通达信改了名就跳过那一条并报出来，绝不按代码段盲改。

用法：
    python scripts\\ingest\\c7_sector_name.py            # 只补 name 是纯数字的
    python scripts\\ingest\\c7_sector_name.py --overwrite # 通达信名覆盖所有同码板块
    python scripts\\ingest\\c7_sector_name.py --dry-run   # 只看会改哪些，不写库
    python scripts\\ingest\\c7_sector_name.py --no-stats  # 只补名字，不重新分类那 48 个
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402

# 通达信索引配置。按顺序读，先出现的优先 —— tdxzs.cfg 是主表，
# base 那两份是老版本快照，只用来补漏。
CFG_FILES = ('tdxzs.cfg', 'tdxzsbase.cfg', 'tdxzsbase2.cfg', 'tdxzs3.cfg')


def load_names() -> dict[str, str]:
    """六位板块码 → 中文名。读 GBK，坏字节用替换字符而不是抛异常。"""
    out: dict[str, str] = {}
    base = config.TDX_DIR / 'T0002' / 'hq_cache'
    for fname in CFG_FILES:
        path = base / fname
        if not path.is_file():
            continue
        for line in path.read_bytes().decode('gbk', 'replace').splitlines():
            parts = line.split('|')
            if len(parts) < 3:
                continue
            name, code = parts[0].strip(), parts[1].strip()
            # 名字本身是数字的行没有价值，跳过；否则等于把代码又抄一遍
            if code.isdigit() and len(code) == 6 and name and not name.isdigit():
                out.setdefault(code, name)
    return out


#: 通达信证券名称表：GBK 定长记录。代码列从记录首字节起（12 字节 + 填充到 31），
#: 名字列固定从第 31 字节起、到下一个 0x00 结束。
TNF_FILE = 'shs.tnf'
TNF_NAME_COL = 31
#: 那一段 88xxxx 记录的相邻偏移差（实测恒为 360，出现第二种间距就说明结构不是我认的那个）
TNF_RECORD_LEN = 360
TNF_MIN_RECORDS = 100

_CODE_RE = re.compile(rb'(?<![0-9])88[0-9]{4}(?![0-9])')

#: 880001~880099 段：通达信的**市场级统计指数**（不是行业板块），2026-10-06 从
#: `shs.tnf` 逐条实测出来的名字。分类动作以这份记录为准 —— 名字对不上就跳过并报名，
#: 目的是"没人能从代码段推出类型"，只能从通达信自己的名字推出。
STATISTICAL: dict[str, str] = {
    '880001': '总市值', '880002': '流通市值', '880003': '平均股价', '880004': '成交均价',
    '880005': '涨跌家数', '880006': '停板家数', '880007': '活筹市值', '880008': '全Ａ等权',
    '880009': '全Ａ中位',
    '880011': '主板总值', '880012': '主板流通', '880013': '主板平均', '880014': '主板均价',
    '880015': '主板涨跌', '880016': '主板停板', '880017': '主板活筹', '880018': '主板等权',
    '880019': '主板中位',
    '880031': '创业总值', '880032': '创业流通', '880033': '创业平均', '880034': '创业均价',
    '880035': '创业涨跌', '880036': '创业停板', '880037': '创业活筹', '880038': '创业等权',
    '880039': '创业中位',
    '880041': '科创总值', '880042': '科创流通', '880043': '科创平均', '880044': '科创均价',
    '880045': '科创涨跌', '880046': '科创停板', '880047': '科创活筹', '880048': '科创等权',
    '880049': '科创中位',
    '880071': '北证总值', '880073': '北证平均', '880074': '北证均价', '880075': '北证涨跌',
    '880076': '北证停板', '880077': '北证活筹', '880078': '北证等权', '880079': '北证中位',
    '880096': 'ETF等权', '880097': 'REITs', '880098': '可转债', '880099': '通用回购',
}
#: 重新分类后这一批在库里的类型值。凡是按 `('行业','概念')` 正列表取母体的读取方
#  （板块热度 / 份额轮动 / qd 轨板块周期 / 分配者 / 加工侧 sector_flow_trend 参数行）
#  就自动把它们挡在母体外 —— 用"正向列表"而不是"排除这批码"，是为了让下一个
#   混进来的市场级指数也进不了榜，而不是只修这一次看到的 48 个。
STAT_TYPE = '统计'


def parse_tnf(raw: bytes) -> tuple[dict[str, str], str]:
    """从通达信名称表的字节里解出 `{88xxxx 码: 中文名}`。

    返回 `(names, why)`：解不出来时 names 为空、why 说清是哪一条结构判据没过。
    **宁可交空也不猜** —— 这个函数只在"记录间距恒等 + 代码列后面全 0 + 名字能按 GBK
    解出可读中文"三条都过的时候才给名字。
    """
    hits = [m.start() for m in _CODE_RE.finditer(raw)]
    if len(hits) < TNF_MIN_RECORDS:
        return {}, "88xxxx 记录少于 %s 条（%s），这一路没有可用的名字" % (TNF_MIN_RECORDS, len(hits))
    strides = {hits[i + 1] - hits[i] for i in range(len(hits) - 1)}
    if len(strides) != 1:
        return {}, "88xxxx 记录间距不恒定（出现 %s 种），文件结构不是我认的那个样子" % len(strides)
    out: dict[str, str] = {}
    rejected = 0
    for p in hits:
        rec = raw[p:p + TNF_RECORD_LEN]
        if len(rec) < TNF_RECORD_LEN:
            rejected += 1
            continue
        code = rec[:6].decode('ascii', 'replace')
        # 代码列剩下的 25 字节必须是填充 0；不是 0 就说明这个命中落在别的数据里，不是记录首
        if rec[6:TNF_NAME_COL] != b'\x00' * (TNF_NAME_COL - 6):
            rejected += 1
            continue
        end = rec.find(b'\x00', TNF_NAME_COL)
        if end < 0:
            rejected += 1
            continue
        raw_name = rec[TNF_NAME_COL:end]
        try:
            name = raw_name.decode('gbk')          # 不许 replace：解不出可读中文就当作没有
        except UnicodeDecodeError:
            rejected += 1
            continue
        if not (2 <= len(name) <= 20) or name.isdigit() or '\x01' in name:
            rejected += 1
            continue
        out.setdefault(code, name)
    if len(out) < TNF_MIN_RECORDS:
        return {}, "结构判据全过后只剩 %s 个名字（拒绝 %s 条），不足以拿来补字典" % (len(out), rejected)
    return out, "记录 %s 条、间距恒 %s、通过 %s 个名字（拒绝 %s 条）" % (
        len(hits), TNF_RECORD_LEN, len(out), rejected)


def load_tnf_names(base: Path) -> tuple[dict[str, str], str]:
    """读 `<通达信>\\T0002\\hq_cache\\shs.tnf`。文件不在 → 空 + 原因。"""
    path = base / TNF_FILE
    if not path.is_file():
        return {}, "没有 %s（检查 FT_TDX_DIR）" % path
    return parse_tnf(path.read_bytes())


def statistical_updates(names: dict[str, str]) -> tuple[list[dict], list[str]]:
    """该重新分类成 `sector_type='统计'` 的行 + 名字对不上的码。

    只对**名字与实测记录一致**的那些动手：`STATISTICAL` 是 2026-10-06 从通达信自己的名称表
    抄下来的，如果哪天它改了名（比如 '全Ａ等权' → '全A等权'），这里会把那个码报出来而不是
    顺手按代码段改掉 —— 按代码段猜类型与按数量猜行业名是同一类错。
    """
    todo, drift = [], []
    for code, expected in sorted(STATISTICAL.items()):
        got = names.get(code)
        if got != expected:
            drift.append('%s（名称表给 %r，实测记录是 %r）' % (code, got, expected))
            continue
        todo.append({'sector_code': 'TDX_%s' % code, 'name': expected})
    return todo, drift


def targets(names: dict[str, str], *, overwrite: bool) -> list[dict]:
    """
    要改名的板块。匹配用 sector_code 的后 6 位 —— 库里带 TDX_/GN_ 前缀。
    默认只动 name 是纯数字的行：正常板块的中文名是旧库里人工确认过的，
    通达信配置里的写法可能更长（"含H股"之类），不该无条件覆盖。
    """
    rows = engine.query(
        """
        SELECT sector_code, name, sector_type
          FROM reference_data.sector_info
         WHERE sector_code ~ '[0-9]{6}$'
         ORDER BY sector_code
        """)
    out = []
    for r in rows:
        code6 = r['sector_code'][-6:]
        new = names.get(code6)
        if not new or new == r['name']:
            continue
        if not overwrite and not r['name'].isdigit():
            continue
        out.append({'sector_code': r['sector_code'], 'old': r['name'],
                    'new': new, 'sector_type': r['sector_type']})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--overwrite', action='store_true',
                    help='连已有中文名的板块也用通达信名覆盖')
    ap.add_argument('--no-stats', action='store_true',
                    help='只补名字，不把市场级统计指数重新分类成 sector_type=%r' % STAT_TYPE)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    base = config.TDX_DIR / 'T0002' / 'hq_cache'
    names = load_names()
    print('=== 读取通达信索引配置 ===')
    print('  目录 %s' % base)
    print('  可用板块名 %s 个' % len(names))

    # 第二路名字来源：通达信自己的证券名称表。tdxzs*.cfg 里根本没有 8800xx 这批码，
    # 只有它有 —— cfg 先读、名称表只补 cfg 没覆盖的（setdefault），所以老口径的名字不会被换掉。
    tnf, why = load_tnf_names(base)
    print('=== 读取通达信名称表（%s）===' % TNF_FILE)
    print('  %s' % why)
    added = 0
    for code, name in tnf.items():
        if code not in names:
            names[code] = name
            added += 1
    print('  名称表新增可用名字 %s 个' % added)
    if not names:
        print('一个都没读到，检查 FT_TDX_DIR 是否指向通达信安装目录')
        return 1

    todo = targets(names, overwrite=args.overwrite)
    unnamed = engine.scalar(
        "SELECT count(*) FROM reference_data.sector_info WHERE name ~ '^[0-9]+$'")
    print('=== 待改名 ===')
    print('  库里纯数字名板块 %s 个，本次能认出 %s 个' % (unnamed, len(todo)))
    for t in todo[:15]:
        print('  %s  %s -> %s' % (t['sector_code'], t['old'], t['new']))
    if len(todo) > 15:
        print('  ...共 %s 条' % len(todo))

    stats, drift = statistical_updates(tnf)
    already = engine.scalar(
        "SELECT count(*) FROM reference_data.sector_info WHERE sector_type = %s", (STAT_TYPE,))
    print('=== 市场级统计指数重新分类（sector_type=%s）===' % STAT_TYPE)
    print('  名字与实测记录一致、可以分类的 %s 个；库里已经是该类型 %s 行' % (len(stats), already))
    if drift:
        print('  ⚠️ 名字对不上、这一轮不动手的 %s 个：%s' % (len(drift), '、'.join(drift[:8])))
        print('     （通达信名称表与 2026-10-06 的实测记录不一致时宁可不改 —— 按代码段猜类型，'
              '和按数量猜行业名是同一类错）')

    if args.dry_run:
        print('\nDONE（未写库）')
        return 0

    if todo:
        n = engine.executemany(
            'UPDATE reference_data.sector_info SET name = %(new)s WHERE sector_code = %(sector_code)s',
            todo, role='etl')
        print('=== 改名结果 ===')
        print('  已更新 %s 行' % (n if n is not None else len(todo)))
    else:
        print('=== 改名结果 ===\n  没有要改的')

    if stats and not args.no_stats:
        # 名字条件一起写进 WHERE：这一批的类型**只能由名字推出**，代码段本身不算证据。
        n = engine.executemany(
            "UPDATE reference_data.sector_info SET sector_type = %(type)s"
            "  WHERE sector_code = %(sector_code)s AND name = %(name)s",
            [{**s, 'type': STAT_TYPE} for s in stats], role='etl')
        print('=== 分类结果 ===')
        print('  已按名字改判 %s 行（发出 %s 条）' % (n if n is not None else len(stats), len(stats)))
        print('  从此按 (行业, 概念) 正列表取母体的每一张板块榜都不再包含它们；'
              '库里**历史那些场次已算好的名次/分母仍含这批**，要一致得重跑加工侧的 '
              '`sectortrend` / `flowtrend` / `signal` / `allocator`（读侧已按类型过滤，页面上先看不见）')

    left = engine.scalar(
        "SELECT count(*) FROM reference_data.sector_info WHERE name ~ '^[0-9]+$'")
    print('=== 收尾 ===')
    print('  仍是纯数字名的还有 %s 个' % left)
    if left:
        print("  剩下的是名称表里也查不到的码（例如 880595），页面侧按「（无名 代码）」显示，不猜")
    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
