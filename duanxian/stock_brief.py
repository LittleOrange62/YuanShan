# -*- coding: utf-8 -*-
"""「个股信息」那一块里那份 400 字公司说明：材料怎么组、结论怎么存、怎么读回来。

用户 2026-10-22 的原话：「把个股深度复盘里面的『个股研究（行情 · 估值 · 研报 · 新闻 · 财务 · 公告 ·
资金面）』喂给 AI，告诉 AI 总结成 400 字以内说明这家公司是干什么的，核心竞争力是什么，想象空间在哪，
当前的基本面情况如何。然后在个股信息……把 AI 的结论放过来。」

三件事刻意放在后端这一处（页面只转述，不自己数）：

1. **材料边界**：哪几段进、每段进几条、每条截多长 —— 见 `SEGMENTS`。这条通道的硬顶是 8,000 字
   （`frontend/src/lib/llm.ts` 里 `context.length > 8000` 直接抛，不是静默截断），所以取舍必须**留回执**：
   每一段交出「源本轮给了几条 / 实际喂进去几条 / 因为上限被削掉没有」。没有这份回执，
   「研报 200 条只喂了 8 条」和「读完了全部研报」在屏幕上长得一样。
2. **字数与越界**：`check_brief_text` 是写入侧唯一的闸（正文由浏览器交回来，**不因为它已经过一次闸就免检**）：
   空正文、给出交易动作或点位、超过契约容忍上限（`WORD_LIMIT × OVER_TOLERANCE`）的一律拒收并说清原因。
   介于 400 与上限之间的**照实入库**，由界面报出实际字数 —— 契约是话术那条，不是模型自说自话。
3. **成功与失败不同字段**：重跑失败只写 `error`/`failed_at`，**不动 `brief`**（`record_failure`），
   否则一次网络抖动就把上一次已经生成的结论抹掉，屏幕上那块变空而没人知道为什么。

一条本机数据现实（2026-10-22 现查 600519，别再按"研报正文"设计）：东财研报那个源**只交标题、机构、评级、
预测 EPS/PE 与 pdf 链接，正文与摘要不在返回里**（200 条 × 每条 1,355 字符，字都在那些数字字段上）；
新闻源的「新闻内容」那一栏实测是别家代码的盘后数字串（「300403 汉宇集团 8.58 0.35 …」），
不是这只票的叙述性正文 ⇒ 喂的是**标题＋时间＋来源**，这两件事由 `SEGMENTS[*].note` 说给屏幕，不混进材料里。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.db import engine

from .quant import jsonable

#: 材料口径＋话术契约的版本。改 `SEGMENTS`（哪几段、几条、截多长）或改字数契约 ⇒ 换号，旧行留着可对照。
SPEC_VERSION = 'brief-1'
#: 这份结论的产出契约（屏幕上「400 字以内」比的是它，不是模型的自我声明）。
WORD_LIMIT = 400
#: 这条 AI 通道的材料硬顶（`lib/llm.ts` 同一数字）。超过就按段尾削，削过哪段必须留痕。
MATERIAL_CAP = 8000

CALIBER = (
    '这一块那份说明是 AI 生成的正文，不是本机判据：材料由本机在点按钮那一刻从公开源现抓'
    '（东财研报/公告/资金面、腾讯行情、同花顺财务、akshare 新闻），组装只住本模块一处（`SEGMENTS`），'
    '模型与接入来源由你在「AI 探索 · 接入设置」里配的那一份决定。'
    '存进 `analytics.stock_brief` 是为了让「双击开新窗」和「从导航进来」看到同一份结论，'
    '并带上它是哪一刻的材料、哪个模型、哪一份话术写的。'
)

DEFINITIONS: Dict[str, str] = {
    'brief': '模型交回的正文，原样存原样摆（渲染前只剥成对粗体标记，不改写、不再摘要）。',
    'generated_at': '这份结论是哪一刻写出来的。它和下面的「材料抓取时刻」是两个时刻。',
    'input_fetched_at': '材料是那一刻从公开源抓的。公开源今天的价格与新闻明天就变 ⇒ 这份说明说的是那一刻的这家公司。',
    'model': '哪一个模型写的这一份。没有它，「AI 说的」这句话就没有可追责对象。',
    'ai_provider': '走的哪家接入（订阅 / API 网关 / 服务商）。密钥不入库，只留这个名字。',
    'prompt_slot': '用的哪一份话术（内置槽位或你在系统配置页自建的那份），改话术在「系统配置 → AI 话术」。',
    'material_segments': '逐段回执：这一段源本轮给了几条、实际喂进模型几条、有没有因为 8,000 字上限被削。'
                         '「研报看了 8 条」与「研报全看完了」的区别就在这几行里。',
    'material_missing': '本轮没读到的段（查询失败 / 源空返回）。空表是真的不缺，null 是没记下，两件事不说成一件。',
    'brief_chars': '按字数契约实际数出来的字数（含标点与换行）。超 400 字后端直接拒收，所以这一格不会比契约大。',
    'error': '最近一次重新生成失败的原因（原样）。它不为空而正文还在 ⇒ 意思是「结论是上一次那份，这次没跑成」。',
}

#: 段序＝喂给模型的先后，也是被上限削时的先后（从表尾往回削）。
#: `cap` 是这段最多取几条，`clip` 是单条最多几字。两个数都是材料预算算出来的，不是拍的：
#: 全取满约 4,600 字（2026-10-22 按 600519 实测各段单条长度估），留一半给 8,000 字硬顶内的别的段落与余量。
SEGMENTS: List[Dict[str, Any]] = [
    {'key': 'profile', 'label': '本机个股档案（主营标签/员工数/板块）', 'cap': 1, 'clip': 300,
     'note': '这一段读本机库、不出网。主营只有一个标签、没有层级树，所以它只回答"大致做什么"，'
             '说不出产业链位置 —— 让模型知道这一句是必要的，否则它会拿一个标签编出一段业务结构。'},
    {'key': 'valuation', 'label': '估值与机构预期（东财，即时值）', 'cap': 1, 'clip': 400,
     'note': '这一段给的是现价/PE-TTM/PB/总市值，加上机构预测的 26E EPS、前向 PE、PEG 与覆盖机构家数。'},
    {'key': 'quote', 'label': '行情（腾讯，即时值）', 'cap': 1, 'clip': 300,
     'note': '与上面那段可能来自不同时刻的两个源，价差是时点不同，不是有一边错。'},
    {'key': 'percentile', 'label': '估值历史分位（本机自算，近 5 年）', 'cap': 2, 'clip': 300,
     'note': 'PE-TTM 与 PB 各自处在自身历史的分位，不是横截面名次。'},
    {'key': 'financials', 'label': '财务摘要（同花顺，最新一期）', 'cap': 1, 'clip': 400,
     'note': '累计口径（年初到报告期），同比也是这一档自己的；单季口径在「财务多期」那一块另算。'},
    {'key': 'reports', 'label': '研报标题（东财）', 'cap': 8, 'clip': 60,
     'note': '这个源只交标题、机构与评级，研报正文与摘要不在返回里 ⇒ 没有喂给模型，不是漏取。'
             '本轮源给了 200 条量级，按预算只取最近的若干条标题。'},
    {'key': 'news', 'label': '新闻（akshare，东财域内）', 'cap': 10, 'clip': 70,
     'note': '取标题＋时间＋来源。这个源的「内容」栏实测常是别家代码的盘后数字串，不是这只票的叙述，'
             '所以没喂。'},
    {'key': 'announcements', 'label': '公告标题（东财）', 'cap': 15, 'clip': 60,
     'note': '标题是公司公告的题名，正文不在这一屏的返回里；读得出的只有"发生了什么类别的事"。'},
    {'key': 'blocks', 'label': '所属板块（东财，当日涨跌幅与领涨股）', 'cap': 12, 'clip': 90,
     'note': '板块是它挂上的行业/题材，不代表主营占比。'},
    {'key': 'conceptTags', 'label': '概念标签（东财，在/不在）', 'cap': 40, 'clip': 40,
     'note': '概念归属是二值的（在/不在），没有权重也没有占比 ⇒ 只列名单。'
             '这一列里混着地域板与指数成分板（如「贵州板块」「HS300_」），不是同一类东西。'},
    {'key': 'hotConcepts', 'label': '热门概念命中（东财）', 'cap': 6, 'clip': 200,
     'note': 'hit 是这个概念板块在本机名单里被点到的次数，不是资金强度。'},
    {'key': 'margin', 'label': '融资融券余额（东财）', 'cap': 3, 'clip': 300,
     'note': '余额是当日存量；取最近几场看方向，不是逐日流入。'},
    {'key': 'blockTrade', 'label': '大宗交易（东财）', 'cap': 3, 'clip': 300,
     'note': '折溢价与买卖方营业部是公开逐笔，看不出动机。'},
    {'key': 'holders', 'label': '股东户数（东财）', 'cap': 2, 'clip': 200,
     'note': '按报告期披露，不是逐日。'},
    {'key': 'dividend', 'label': '分红方案（东财）', 'cap': 2, 'clip': 200,
     'note': '每 10 股派/转增与方案文本，是公告口径，不代表已到账。'},
    {'key': 'lockup', 'label': '限售解禁（东财）', 'cap': 3, 'clip': 300,
     'note': '解禁不等于减持，以公司公告为准。'},
    {'key': 'dragonTiger', 'label': '龙虎榜（东财）', 'cap': 3, 'clip': 400,
     'note': '有上榜记录才给；机构席位那一行是买卖金额差，不是净买入意图的证明。'},
    {'key': 'fundFlow', 'label': '每日资金流（东财）', 'cap': 3, 'clip': 300,
     'note': '大中小单分档是交易所口径的成交额切分，不叫"主力意图"。'},
    {'key': 'investorQa', 'label': '互动问答（东财互动易）', 'cap': 3, 'clip': 400,
     'note': '问答正文各截一段。这是公司自己的公开答复，喂给模型时算"公司口径"，比第三方转述近一层。'},
]

SEG_KEYS = [s['key'] for s in SEGMENTS]


# ───────────────────────── 材料组装（纯函数，不碰库、不出网） ─────────────────────────

def _clip(v: Any, n: int) -> str:
    s = str(v if v is not None else '').strip().replace('\n', ' ')
    return s if len(s) <= n else s[:n] + '…'


def _yi(v: Any, unit: str = '亿') -> str:
    try:
        return f'{float(v) / 1e8:.2f}{unit}'
    except (TypeError, ValueError):
        return '缺'


def _unwrap(v: Any) -> Any:
    """各段返回形状不一（有的直给数组、有的还裹着 `{"data": …}`）⇒ 先剥一层，认不出形状就说没读到。"""
    if isinstance(v, dict) and set(v.keys()) == {'data'}:
        return v['data']
    return v


def _rows(v: Any) -> List[dict]:
    v = _unwrap(v)
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def _pct(v: Any) -> str:
    try:
        return f'{float(v):.2f}%'
    except (TypeError, ValueError):
        return '缺'


def _build(key: str, raw: Any, spec: Dict[str, Any]) -> tuple[List[str], int]:
    """这一段能组出几行、源本轮一共给了几条。认不出/为空就交回空表（调用方按「没读到」处理，不编一行）。"""
    cap, clip = spec['cap'], spec['clip']

    if key == 'profile':
        d = _unwrap(raw)
        p = d.get('profile') if isinstance(d, dict) and isinstance(d.get('profile'), dict) else d
        if not isinstance(p, dict) or not any(p.get(k) for k in ('main_business', 'staff_num', 'board')):
            return [], 0
        bits = [f'名称 {p.get("name")}' if p.get('name') else None,
                f'主营标签 {p.get("main_business")}' if p.get('main_business') else None,
                f'员工数 {p.get("staff_num")}' if p.get('staff_num') is not None else None,
                f'上市板块 {p.get("board")}' if p.get('board') else None,
                'ST 标记 是' if p.get('is_st') else None,
                f'档案日 {p.get("hq_date")}' if p.get('hq_date') else None]
        return [_clip('｜'.join(x for x in bits if x), clip)], 1

    if key == 'valuation':
        r = _rows(raw)
        if not r:
            return [], 0
        v = r[0]
        line = (f'名称 {v.get("name")}｜代码 {v.get("code")}｜现价 {v.get("price")} 元'
                f'｜PE-TTM {v.get("pe_ttm")}｜PB {v.get("pb")}｜总市值 {v.get("mcap_yi")} 亿'
                f'｜机构覆盖 {v.get("analyst_count")} 家'
                f'｜26E EPS {v.get("eps_26e")}｜27E EPS {v.get("eps_27e")}'
                f'｜前向 PE {v.get("pe_26e")}｜PEG {v.get("peg")}｜预期增速 {v.get("cagr_pct")}%')
        return [_clip(line, clip)], 1

    if key == 'quote':
        d = _unwrap(raw)
        q: Optional[dict] = None
        if isinstance(d, dict):
            # 这一段的形状有三种：`{code: Quote}`（页面按代码取那一格）、直接一个 Quote、
            # 或者 FastAPI 的 `{"detail": […]}` 错误体 —— 最后那种必须判成"没读到"，不能当行情摆上去。
            q = next((v for v in d.values() if isinstance(v, dict) and 'price' in v), None) \
                or (d if 'price' in d else None)
        elif isinstance(d, list):
            q = d[0] if d and isinstance(d[0], dict) else None
        if not q:
            return [], 0
        line = (f'报价时刻 {q.get("quote_time")}｜价 {q.get("price")} 元｜涨跌 {q.get("change_pct")}%'
                f'｜成交额 {q.get("amount_wan")} 万｜换手 {q.get("turnover_pct")}%'
                f'｜涨停 {q.get("limit_up")}｜跌停 {q.get("limit_down")}')
        return [_clip(line, clip)], 1

    if key == 'percentile':
        d = _unwrap(raw) or {}
        out = []
        for mk, label in (('pe_ttm', 'PE-TTM'), ('pb', 'PB')):
            m = (d.get('metrics') or {}).get(mk) if isinstance(d, dict) else None
            if isinstance(m, dict) and m.get('percentile') is not None:
                out.append(_clip(f'{label} 处自身历史 {m.get("percentile")}% 分位'
                                 f'（当前 {m.get("current")}、区间 {m.get("min")}~{m.get("max")}'
                                 f'、样本 {m.get("n")} 场、窗口 {d.get("period")}）', clip))
        return out, len(out)

    if key == 'financials':
        f = _rows(raw)
        if not f:
            return [], 0
        x = f[0]
        line = (f'报告期 {x.get("period")}（累计口径）｜营收 {x.get("revenue")}（同比 {x.get("revenue_yoy")}）'
                f'｜归母净利 {x.get("net_profit")}（同比 {x.get("net_profit_yoy")}）'
                f'｜EPS {x.get("eps")}｜每股净资产 {x.get("bvps")}｜ROE {x.get("roe")}'
                f'｜毛利率 {x.get("gross_margin")}｜净利率 {x.get("net_margin")}'
                f'｜每股经营现金流 {x.get("op_cf_ps")}')
        return [_clip(line, clip)], 1

    if key == 'reports':
        rows = _rows(raw)
        lines = [_clip(f'{r.get("title")}（{r.get("orgSName") or r.get("orgName")} · '
                       f'{r.get("emRatingName") or "评级缺"} · {str(r.get("publishDate"))[:10]}）', clip)
                 for r in rows[:cap]]
        return lines, len(rows)

    if key == 'news':
        rows = _rows(raw)
        lines = [_clip(f'{r.get("新闻标题")}（{str(r.get("发布时间"))[:16]} · {r.get("文章来源")}）', clip)
                 for r in rows[:cap]]
        return lines, len(rows)

    if key == 'announcements':
        rows = _rows(raw)
        lines = [_clip(f'{str(r.get("date"))[:10]}｜{r.get("type")}｜{r.get("title")}', clip) for r in rows[:cap]]
        return lines, len(rows)

    if key == 'blocks':
        d = _unwrap(raw) or {}
        if not isinstance(d, dict):
            return [], 0
        boards = [b for b in (d.get('boards') or []) if isinstance(b, dict)]
        # 一条一行：`taken` 数的是行数，只有每行一项时它才等于"喂进去几条"
        return [_clip(f'{b.get("name")}｜当日 {b.get("change_pct")}%｜领涨 {b.get("lead_stock")}', clip)
                for b in boards[:cap]], len(boards)

    if key == 'conceptTags':
        d = _unwrap(raw) or {}
        tags = [str(t) for t in ((d.get('concept_tags') if isinstance(d, dict) else None) or []) if t]
        return [_clip(t, clip) for t in tags[:cap]], len(tags)

    if key == 'hotConcepts':
        rows = _rows(raw)
        return [_clip(f'{r.get("concept")} 命中 {r.get("hit")} 次（板块代码 {r.get("bk")}）', clip)
                for r in rows[:cap]], len(rows)

    if key == 'margin':
        rows = _rows(raw)
        return [_clip(f'{str(r.get("date"))[:10]}｜融资余额 {_yi(r.get("rzye"))}｜融券余额 {_yi(r.get("rqye"))}'
                      f'｜两融合计 {_yi(r.get("rzrqye"))}', clip) for r in rows[:cap]], len(rows)

    if key == 'blockTrade':
        rows = _rows(raw)
        return [_clip(f'{str(r.get("date"))[:10]}｜成交 {r.get("price")} 元（当日收 {r.get("close")}，'
                      f'折溢价 {r.get("premium_pct")}%）｜量 {r.get("vol")}｜额 {_yi(r.get("amount"))}'
                      f'｜买 {r.get("buyer")}｜卖 {r.get("seller")}', clip)
                for r in rows[:cap]], len(rows)

    if key == 'holders':
        rows = _rows(raw)
        return [_clip(f'{str(r.get("date"))[:10]}｜股东户数 {r.get("holder_num")}'
                      f'｜较上期 {_pct(r.get("change_ratio"))}', clip) for r in rows[:cap]], len(rows)

    if key == 'dividend':
        rows = _rows(raw)
        return [_clip(f'{str(r.get("date"))[:10]}｜每 10 股派 {r.get("bonus_rmb")} 元、'
                      f'转增 {r.get("transfer_ratio")} 股｜{r.get("plan")}', clip)
                for r in rows[:cap]], len(rows)

    if key == 'lockup':
        d = _unwrap(raw) or {}
        up = [x for x in (d.get('upcoming') or []) if isinstance(x, dict)] if isinstance(d, dict) else []
        return [_clip(f'{str(x.get("date"))[:10]}｜{x.get("type")}｜可流通股数 {x.get("able_shares")}'
                      f'｜占总市值 {x.get("ratio")}', clip) for x in up[:cap]], len(up)

    if key == 'dragonTiger':
        d = _unwrap(raw) or {}
        if not isinstance(d, dict):
            return [], 0
        recs = [x for x in (d.get('records') or []) if isinstance(x, dict)]
        inst = d.get('institution') or {}
        out = [_clip(f'{str(r.get("date"))[:10]}｜{r.get("reason")}｜净买 {_yi(r.get("net_buy"))}'
                     f'｜换手 {r.get("turnover")}', clip) for r in recs[:cap]]
        if isinstance(inst, dict) and any(inst.get(k) for k in ('buy_amt', 'sell_amt', 'net_amt')):
            out.append(_clip(f'机构席位｜买 {_yi(inst.get("buy_amt"))}｜卖 {_yi(inst.get("sell_amt"))}'
                             f'｜差 {_yi(inst.get("net_amt"))}', clip))
        return out, len(recs)

    if key == 'fundFlow':
        rows = _rows(raw)
        return [_clip(f'{str(r.get("date"))[:10]}｜主力净 {r.get("main_net")}｜超大单 {r.get("super_net")}'
                      f'｜大单 {r.get("large_net")}｜中单 {r.get("mid_net")}｜小单 {r.get("small_net")}', clip)
                for r in rows[:cap]], len(rows)

    if key == 'investorQa':
        rows = _rows(raw)
        return [_clip(f'问：{r.get("question")}｜答（{r.get("answerer")}，{str(r.get("ask_time"))[:10]}）：'
                      f'{r.get("answer")}', clip) for r in rows[:cap]], len(rows)

    return [], 0


def compose(code: str, segments: Dict[str, Any], fetched_at: Optional[str] = None) -> Dict[str, Any]:
    """把「个股研究」那一屏已经取到的各段原始返回组成一份材料，并交回**逐段取舍回执**。

    纯函数：不碰库、不出网、不猜。认不出的形状算「没读到」，写进 `missing`，
    **不写一行看起来像读到的话**（这是那条「检测器坏了不许兜成空值」的镜像：这里不许兜成填充句）。
    """
    sec = (code or '').strip().upper()
    segs: List[Dict[str, Any]] = []
    missing: List[str] = []
    received = (segments or {})
    for spec in SEGMENTS:
        raw = received.get(spec['key'], received.get(spec['key'].lower()))
        if raw is None and spec['key'] == 'conceptTags':
            raw = received.get('blocks')   # 概念标签与板块在同一份返回里，这一段不另起一次请求
        lines, total = _build(spec['key'], raw, spec)
        rec = {'key': spec['key'], 'label': spec['label'], 'taken': len(lines), 'total': total,
               'truncated': False, 'dropped': False, 'note': spec['note']}
        segs.append(rec)
        if not lines:
            missing.append(spec['label'])
        rec['lines'] = lines

    def _size() -> int:
        return len(_render(sec, segs, fetched_at))

    # 超上限就从**最后一段**往回削行（段序即优先级：越靠前的段越重要），削过必须留痕。
    guard = 0
    while _size() > MATERIAL_CAP and guard < 4000:
        guard += 1
        target = next((s for s in reversed(segs) if s['lines']), None)
        if target is None:
            break
        target['lines'].pop()
        target['truncated'] = True
        target['taken'] = len(target['lines'])
        if not target['lines'] and target['label'] not in missing:
            missing.append(target['label'])
            target['dropped'] = True

    material = _render(sec, segs, fetched_at)
    receipt = [{k: v for k, v in s.items() if k != 'lines'} for s in segs]
    return jsonable({
        'available': bool(any(s['lines'] for s in segs)),
        'code': sec, 'spec_version': SPEC_VERSION,
        'word_limit': WORD_LIMIT, 'material_cap': MATERIAL_CAP,
        'material': material, 'chars': len(material),
        'segments': receipt, 'missing': missing,
        'fetched_at': fetched_at,
        'material_sha': hashlib.sha1(material.encode('utf-8')).hexdigest()[:12],
        'caliber': CALIBER, 'definitions': DEFINITIONS,
        'reason': None if any(s['lines'] for s in segs) else
                  '这一块一份材料都没组出来（上面每一段都写着本轮没读到）⇒ 没有可喂的材料，不发起 AI 请求',
    })


def _render(sec: str, segs: List[Dict[str, Any]], fetched_at: Optional[str]) -> str:
    """材料的**唯一**一份文本形状（compose 的上限判断与最终文本都走这里，不各拼一遍）。"""
    head = [f'以下是本机于 {fetched_at or "（抓取时刻未交）"} 从公开源取到的「{sec}」的原始材料，'
            f'共 {sum(1 for s in segs if s["lines"])} 段。只对这份材料作答，里面没有的不要编。']
    body = []
    for s in segs:
        if not s['lines']:
            continue
        body.append(f'【{s["label"]}】\n' + '\n'.join(f'· {x}' for x in s['lines']))
    return '\n'.join(head + body)


# ───────────────────────── 写入侧唯一的闸 ─────────────────────────

#: 字数契约的容忍倍数。为什么不写"正好 400 字否则拒收"：模型交回 410~470 字是常态，
#: 严格拒收等于这一格大多数时候填不上；但也不能放任超字 —— 屏幕上那句「400 字以内」
#: 比的是实际存下来的 `brief_chars`，超契约仍在范围内时界面必须写「实际 N 字、比契约多 M 字」，
#: 不许把 470 字念成"400 字以内"。超过 1.2 倍判"这一版没按契约写"，退回重跑。
OVER_TOLERANCE = 1.2


def check_brief_text(text: str, word_limit: int = WORD_LIMIT) -> str:
    """正文能不能上这块屏。**浏览器交回来的文本在这里重新过一遍**，
    不因它已经过 AI 通道那一档的闸就免检（那一档按设计允许给价位，这一格不允许）。

    三条都拒并点名：空正文 / 出现交易动作或点位（复用全站那一处正则，不自写第二套）/
    超过契约的容忍上限。
    """
    from review_agent.product_policy import has_trade_recommendation

    body = (text or '').strip()
    if not body:
        raise ValueError('AI 交回空正文，未保存；请重试或换一份话术')
    if has_trade_recommendation(body):
        raise ValueError('这份正文里出现了交易动作或点位（买入/卖出/目标价/止损那类话）。'
                         '这一格只放"这家公司是怎样"的说明，越界的正文不入库 —— '
                         '要情景推演与参考价位请走右侧「AI分析」那一档，那里本来就允许')
    hard = int(word_limit * OVER_TOLERANCE)
    if len(body) > hard:
        raise ValueError(f'正文 {len(body)} 字，超过这一格契约的容忍上限（契约 {word_limit} 字、最多收到 {hard} 字）。'
                         '不截断入库 —— 截一半摆上屏会变成"公司说明就这么短"；'
                         '请在话术里把字数说得更硬，或换一份模型再跑')
    return body


# ───────────────────────── 落库与读回 ─────────────────────────

_COLUMNS = """security_code, brief, brief_chars, word_limit, material_chars,
              material_segments, material_missing, model, ai_provider,
              prompt_slot, prompt_source, input_fetched_at, generated_at,
              error, failed_at"""


def read_brief(code: str, spec_version: str = SPEC_VERSION) -> Optional[Dict[str, Any]]:
    """这一版口径下最新的一份结论（没有就 None，**不返回一个空壳让屏幕念成"生成过但为空"**）。"""
    row = engine.query_one(
        f'select {_COLUMNS} from analytics.stock_brief where security_code = %s and spec_version = %s',
        (code, spec_version), role='reader')
    if not row:
        return None
    out = dict(row)
    # 时间一律交 ISO：psycopg2 回来的 datetime 用 `str()` 会变成「2026-10-22 00:00:00+08:00」（空格不是 T），
    # 界面的缓存年龄条与"哪天生成的"都按 ISO 解析，别拿一个看起来像日期的串去赌。
    for k in ('generated_at', 'input_fetched_at', 'failed_at'):
        v = out.get(k)
        if v is not None:
            out[k] = v.isoformat() if hasattr(v, 'isoformat') else str(v)
    out['spec_version'] = spec_version
    return jsonable(out)


def save_brief(code: str, brief: str, *, word_limit: int = WORD_LIMIT,
               material_chars: Optional[int] = None,
               segments: Optional[List[dict]] = None,
               missing: Optional[List[str]] = None,
               model: Optional[str] = None, ai_provider: Optional[str] = None,
               prompt_slot: Optional[str] = None, prompt_source: Optional[str] = None,
               input_fetched_at: Optional[str] = None,
               spec_version: str = SPEC_VERSION) -> Dict[str, Any]:
    """成功那一路：覆盖同一 (票, 版本) 的正文，**并清掉上一次的失败记录**，
    然后回查读回来（不假定已写入）。写入方只有这一个入口。"""
    body = check_brief_text(brief, word_limit)
    engine.execute(
        """insert into analytics.stock_brief (security_code, spec_version, brief, brief_chars,
                   word_limit, material_chars, material_segments, material_missing,
                   model, ai_provider, prompt_slot, prompt_source, input_fetched_at,
                   generated_at, error, failed_at)
           values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::timestamptz,
                   now(), null, null)
           on conflict (security_code, spec_version) do update set
               brief = excluded.brief, brief_chars = excluded.brief_chars,
               word_limit = excluded.word_limit, material_chars = excluded.material_chars,
               material_segments = excluded.material_segments,
               material_missing = excluded.material_missing,
               model = excluded.model, ai_provider = excluded.ai_provider,
               prompt_slot = excluded.prompt_slot, prompt_source = excluded.prompt_source,
               input_fetched_at = excluded.input_fetched_at,
               generated_at = now(), error = null, failed_at = null""",
        (code, spec_version, body, len(body), word_limit, material_chars,
         json.dumps(segments, ensure_ascii=False) if segments is not None else None,
         json.dumps(missing, ensure_ascii=False) if missing is not None else None,
         model, ai_provider, prompt_slot, prompt_source, input_fetched_at), role='app')
    back = read_brief(code, spec_version)
    if not back or back.get('brief') != body:
        raise RuntimeError('写入后读不回来或正文不一致，不假定已生效')
    return back


def record_failure(code: str, error: str, *, material_chars: Optional[int] = None,
                   segments: Optional[List[dict]] = None, missing: Optional[List[str]] = None,
                   model: Optional[str] = None, ai_provider: Optional[str] = None,
                   prompt_slot: Optional[str] = None, prompt_source: Optional[str] = None,
                   input_fetched_at: Optional[str] = None,
                   spec_version: str = SPEC_VERSION) -> Dict[str, Any]:
    """失败那一路：**只写 error/failed_at 与这一轮的材料回执，绝不动 brief** ——
    一次网络抖动不该把上一次已经生成的结论抹掉。"""
    reason = (error or '失败（原因未交）').strip()[:900]
    engine.execute(
        """insert into analytics.stock_brief (security_code, spec_version, error, failed_at,
                   material_chars, material_segments, material_missing, model, ai_provider,
                   prompt_slot, prompt_source, input_fetched_at)
           values (%s, %s, %s, now(), %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::timestamptz)
           on conflict (security_code, spec_version) do update set
               error = excluded.error, failed_at = now(),
               material_chars = excluded.material_chars,
               material_segments = excluded.material_segments,
               material_missing = excluded.material_missing,
               model = excluded.model, ai_provider = excluded.ai_provider,
               prompt_slot = excluded.prompt_slot, prompt_source = excluded.prompt_source,
               input_fetched_at = excluded.input_fetched_at""",
        (code, spec_version, reason, material_chars,
         json.dumps(segments, ensure_ascii=False) if segments is not None else None,
         json.dumps(missing, ensure_ascii=False) if missing is not None else None,
         model, ai_provider, prompt_slot, prompt_source, input_fetched_at), role='app')
    back = read_brief(code, spec_version)
    if not back or not back.get('error'):
        raise RuntimeError('失败原因写入后读不回来，不假定已记账')
    return back
