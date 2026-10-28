"""AI 提示词的**槽位注册表**：页面上一键运行的固定话术，从库里读、可在系统配置页改。

为什么要有这个模块（用户 2026-09-27 提的）：「盘面数据 → 连板股 → 深入分析」这类按钮的话术
原先**写死在前端组件里**，想改一个字就得改代码重新构建 —— 而要看的关键数据其实早就在
那一行里，缺的只是话术的所有权。所以：

- **数据由前端带入**（那一行本来就有什么，就交什么，缺的字段一律交 `未提供`，不给 0 也不给空）；
- **话术存在 `config.setting`**，键名 `ai_prompt.<槽位 id>`，读不到就用本模块里的内置默认，
  并把"用的是哪一份"如实交回页面（带 `updated_at`）；
- **保存时校验占位符**：只认白名单里的字段。写错字段名当场拒绝 —— 否则渲染时会静默留下
  一个没换掉的 `{pice}`，用户看到的是一份"看起来没带数据"的回答，比报错难查得多。

一条不许越界的地方：模板只改**话术**。这个通道走的是产品红线（`task_kind='page'`），
服务端 `PRODUCT_POLICY` + `check_report_text` 照旧拦交易动作与点位。此外 `save()` 还有一条
**按意思拦**的最低要求（`_TRADE_ADVICE`）：自定义话术里一句"不给交易动作/点位、不构成投资建议"
之类的约束都没有就拒存 —— 换成自己的措辞可以，整段删掉不行，否则 README 那句对外承诺就成了
配置页上随手可关的开关。要放开价位是另一条通道（按需研究档 `analysis`），
见 `review_agent/product_policy.py`。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

SETTING_PREFIX = "ai_prompt."
#: 用户自建的模板另起一段前缀，`reset`（恢复内置默认）那条路径删不到它们 ——
#: 混在一个前缀里，"恢复默认"就会顺手删掉用户手打的资产。
CUSTOM_PREFIX = "ai_prompt.custom."
_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,40}")

#: 这些句子是 README 对外的承诺（"个股一律只作客观陈述，方向与情绪判断做到板块层面为止"），
#: 不是修辞。`tests/test_core_logic.py::TestPerStockPromptsStayAtSectorLevel` 守内置默认里
#: 它们在，`save()` 守**自定义话术**不许把它们整段删掉 —— 否则话术一旦可编辑，
#: 这条承诺就变成用户自己在配置页上随手可以关掉的东西。
COMPLIANCE_CLAUSES = (
    "个股层面只陈述已经发生的客观数据与事实",
    "方向与强弱判断做到题材板块层面为止",
    "这个题材板块整体",
    "不要由此推断这只个股接下来会怎样",
    "不预测个股涨跌",
    "不给个股参与倾向",
    "不推荐任何标的",
    "不构成投资建议",
)

#: 只讲"别给交易动作/点位"这一条硬红线。措辞可以换，但不能整段没有。
_TRADE_ADVICE = re.compile(r"不给交易动作|不写交易动作|不给.{0,6}点位|不构成投资建议")

# 占位符：`{小写字母/下划线}`。刻意不匹配 `{"answer":"正文"}` 这类花括号里带引号/冒号的内容，
# 那些是给模型看的输出约定，不是要替换的字段。
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def _slot(title: str, scope: str, default: str, notes: str = "") -> dict[str, Any]:
    """内置槽位 = 「类型（能拿到哪些字段）+ 一份默认话术」。

    字段表刻意搬去 `SCOPES`：用户自己新建的模板必须复用同一份字段表，
    否则"内建能用的占位符"和"自定义能用的占位符"会长成两套，校验就废了。
    """
    return {"title": title, "scope": scope, "default": default, "notes": notes}


# 类型表：一个类型 = 页面上的一个取数点 + 那一行能给模型的字段白名单。
# 配置页把这张表渲染成「类型」下拉与「可插入的数据」清单。
SCOPES: dict[str, dict[str, Any]] = {
    "lianban": {
        "label": "盘面数据 · 连板股那一行",
        "page": "首页 → 盘面数据 → 短线情绪 → 连板股",
        "fields": {
            "date": "研究交易日（库里实际有数的那一场）",
            "name": "个股名称", "code": "个股代码（含市场后缀）",
            "em_line": "东财行情页那一整行（没有链接时是空串）",
            "close": "该日收盘价（已带单位）", "pct": "该日涨幅",
            "boards": "连板天数", "seal_time": "首次封板时间 HH:MM，缺=空",
            "quote_line": "实时行情那一整句（没取到报价时是空串）",
            "amount": "成交额（亿/万已格式化）", "float_cap": "流通市值（已格式化）",
            "industry": "所属概念/行业，缺=未知", "reason": "涨停原因题材，缺=尚未核实",
        },
    },
    "firstboard": {
        "label": "首板分析 · 首板股那一行",
        "page": "复盘 → 首板分析",
        "fields": {
            "date": "研究交易日", "name": "个股名称", "code": "个股代码",
            "price": "现价（元）", "pct": "涨停涨幅",
            "seal_time": "首次封板时间，缺=未知", "break_count": "炸板次数",
            "amount": "成交额（已格式化）", "float_cap": "流通市值（已格式化）",
            "industry": "所属行业，缺=未知", "reason": "涨停原因题材，缺=（暂缺，需要自查）",
        },
    },
    "stock_brief": {
        "label": "个股深度复盘 · 400 字公司说明",
        "page": "个股 → 个股深度复盘 → 个股信息（点「让 AI 读下面「个股研究」那一整块」那个按钮）",
        # ⚠️ 这一档**没有 material 占位符**，是刻意的：材料由 `duanxian/stock_brief.py::compose` 组好，
        # 走这条通道的 `context`（硬顶 8,000 字），话术走 `messages` 的最后一条（硬顶 4,000 字）。
        # 把 {material} 塞进话术里会让两个上限互相挤，超了谁被截没人说得清。
        "fields": {
            "date": "生成这一刻的本地日期", "name": "个股名称", "code": "个股代码（含市场后缀）",
            "word_limit": "字数契约（后端一处常量，页面不另写一个数）",
            "segment_count": "本轮实际组出几段材料（没读到的段不计入，界面单独列着）",
        },
    },
}

# 占位符白名单在 `SCOPES[...]["fields"]`，形状是 `{字段: 中文说明}`。
SLOTS: dict[str, dict[str, Any]] = {
    "lianban_dive": _slot(
        "盘面数据 · 连板股「深入分析」", "lianban",
        "{date}（已收盘）A 股连板股「{name}（{code}）」的客观数据：\n"
        "{em_line}"
        "该日收盘 {close} 元、涨停 +{pct}%，已连续涨停 {boards} 天（{boards} 连板），"
        "{quote_line}"
        "成交额 {amount}，流通市值 {float_cap}，所属概念/行业 {industry}，"
        "涨停原因题材：{reason}。\n\n"
        "仅根据材料分析这个交易日的连板表现。若本次允许工具，可查询该日及之前的新闻研报；"
        "否则直接说明新闻研报尚未核实，不要求取新资料。"
        "按「已知事实」「尚未核实」「后续核验条件」三段，每段最多三条，全文约300字。"
        "题材串是来源归因，不是公司确认事实。只有单股与单日数据时，不能判断板块强度、扩散、"
        "阶段，也不能判断放量缩量、承接或持续性。"
        "只列需要补查的公开资料及可观察变化，不给交易动作、点位、仓位或涨跌预测。"
        "不在答案中复述工具权限与这些约束。"
        "个股层面只陈述已经发生的客观数据与事实，方向与强弱判断做到题材板块层面为止，"
        "且必须有这个题材板块整体的证据；不要由此推断这只个股接下来会怎样。"
        "不预测个股涨跌，不给个股参与倾向，不推荐任何标的，不构成投资建议。"
        "输出纯 Markdown，不使用 HTML 标签。",
        "「三段各三条、约 300 字」是本槽位的产出契约，改之前想清楚：复盘页把回答按段落渲染，"
        "写成散文不会报错，只会变难读。",
    ),
    "firstboard_dive": _slot(
        "首板分析 · 「深入分析」", "firstboard",
        "交易日（{date}）A 股首板涨停股「{name}（{code}）」的客观数据：\n"
        "现价 {price} 元，涨停 +{pct}%，首次封板时间 {seal_time}，"
        "炸板 {break_count} 次，成交额 {amount}，流通市值 {float_cap}，"
        "所属行业 {industry}，涨停原因题材：{reason}。\n\n"
        "请深入分析这只股票在该交易日涨停的原因：\n"
        "1. 若本次允许工具，查询该交易日及之前的新闻研报；未开启工具则仅依据给定资料并明确"
        "新闻研报尚未核实。结合题材串说明可能驱动，源站归因不是公司确认事实，"
        "不得用之后的新闻倒推当天原因；\n"
        "2. 材料只有单股数据，不能据此判定整个题材板块强度、阶段、扩散或持续性。"
        "缺少同日板块广度、梯队和历史对照时写「板块证据不足」，不要猜测情绪性一日游或产业趋势；\n"
        "3. 按「已知事实」「尚未核实」「后续核验条件」三段，每段最多三条，全文约300字。"
        "首次封板09:25属于开盘集合竞价，不能称连续竞价中的早盘承接；炸板0次仅说明数据源未记录开板，"
        "不证明买盘强弱或持续性。流通市值只报原值，缺少横截面比较不能自定大中小盘。\n"
        "后续核验条件只写需要补查的公开资料和可观察变化，不写交易动作、个股参与倾向、点位、仓位或"
        "价格预测，也不要复述被禁止的词语。"
        "不要展示工具开关、参数名、allow_tools、JSON等实现细节；没有查到新闻研报就直接说"
        "「新闻研报尚未核实」。"
        "个股层面只陈述已经发生的客观数据与事实；方向与强弱判断做到题材板块层面为止，"
        "且必须有这个题材板块整体的证据；不要由此推断这只个股接下来会怎样。"
        "不预测个股涨跌，不给个股参与倾向，不推荐任何标的，不构成投资建议。这是约束，不必在答案中复述。"
        "输出用纯 Markdown（不要在表格或正文里使用 <br> 等 HTML 标签）。",
        "这一档的话术里有一条本机数据现实必须留着：「首次封板时间」来自东财涨停池，"
        "不是分钟线；分钟线目前只有 2026-09-22 起三天，别在话术里要求按分钟复核封板。",
    ),
    "stock_brief": _slot(
        "个股深度复盘 · 400 字公司说明", "stock_brief",
        "{date} 的「{name}（{code}）」，请只根据随本问句一起交给你那段材料（本轮实组 {segment_count} 段"
        "公开源读数与本机档案），用不超过 {word_limit} 字的一段话回答四件事：\n"
        "① 这家公司主要做什么——说得出名目的具体产品与服务，不要只写「消费品」「制造业」这种大类；\n"
        "② 它的核心竞争力是什么，依据要落在材料里看得见的东西（毛利率与净利率、机构覆盖与评级分布、"
        "板块与概念归属、估值处自身历史的分位、公司在互动易的公开答复等）；材料里看不出来的写「材料未给出」，"
        "不要拿常识或模型记忆补；\n"
        "③ 想象空间在哪——只谈材料里有的业务与行业线索（新品与渠道、产能、出海、改革动作、题材归属），"
        "不许由这些推断股价会怎样；\n"
        "④ 当前基本面情况——按材料里的最新一期营收与归母净利及其同比、毛利率净利率、ROE、"
        "估值与历史分位、机构预期来陈述，并说清是哪一期、是累计口径还是单季口径。\n"
        "本轮没读到的那几段（解禁、龙虎榜、资金流、互动问答这些源可能空返回）直接说缺，"
        "不要编，也不要用「综合来看」糊过去。\n"
        "个股层面只陈述已经发生的客观数据与事实；不给交易动作、点位、仓位与买卖时机，"
        "不预测个股涨跌，不推荐任何标的，不构成投资建议。这是约束，不必在答案里复述。"
        "输出纯文本，不用 Markdown 标题、表格与 HTML 标签。",
        "这一档与另外两档的分工要说清：它回答「这是一家公司什么」，不回答「今天该怎么操作」。"
        "材料边界（哪几段、每段取几条）只住 `duanxian/stock_brief.py` 的 `SEGMENTS`，"
        "改这里的话术不会改到喂进去的材料；改材料口径要同时换 `SPEC_VERSION`，否则新旧结论在同一行上互相覆盖。",
    ),
}


def fields_of(scope: str) -> dict[str, str]:
    if scope not in SCOPES:
        raise KeyError(scope)
    return SCOPES[scope]["fields"]


def unknown_placeholders(template: str, slot: str) -> list[str]:
    """模板里出现了该槽位类型没有的占位符 → 列出来（保存时拒绝，渲染时不静默）。"""
    allowed = set(fields_of(SLOTS[slot]["scope"])) if slot in SLOTS else set()
    return sorted({p for p in PLACEHOLDER.findall(template or "") if p not in allowed})


def _check_text(text: str, allowed_fields: dict[str, str]) -> str:
    """两条存不进去的判据：空话术 / 未知占位符 / 整段没有不给点位的约束。

    不给点位那条是**按意思拦、不按措辞拦**：话术归用户写，但"这一档不给交易动作与点位"
    是 README 对外的承诺，不能因为话术可编辑就成了配置页上随手可关的开关。
    其余句子（三段体、约300字、板块层面限定…）随便改，不做校验。
    """
    out = (text or "").strip()
    if not out:
        raise ValueError("话术不能为空")
    bad = sorted({p for p in PLACEHOLDER.findall(out) if p not in allowed_fields})
    if bad:
        raise ValueError("这些占位符这个类型没有对应数据：" + "、".join("{" + b + "}" for b in bad)
                         + "；可用的是 " + "、".join("{" + f + "}" for f in allowed_fields))
    if not _TRADE_ADVICE.search(out):
        raise ValueError("这份话术里完全没有约束模型给点位的句子。"
                         "措辞随你写，但至少要有「不给交易动作/点位」或「不构成投资建议」这类一句；"
                         "要能自由给价位的通道在右侧「AI分析」（按需研究档），不是这里。")
    return out


def _stored(slot: str) -> tuple[Optional[str], Optional[str]]:
    """从 `config.setting` 读这份话术；没有这个键就交回 (None, None)。"""
    from app.db import engine     # 只在真要读库时拉起连接池，别让 import 期就连库
    row = engine.query_one(
        "select value ->> 'template' as template, updated_at from config.setting where key = %s",
        (SETTING_PREFIX + slot,), role='reader')
    if not row:
        return None, None
    return (row.get("template") or None), (str(row["updated_at"]) if row.get("updated_at") else None)


def effective(slot: str) -> dict[str, Any]:
    """这一槽位**现在实际生效**的是哪一份模板、从哪来。读不到库不报错，退回内置默认。

    `source` 必须一起交回前端：用户在配置页改了话术但库里没落进去（权限/键名错了）时，
    只有这一句能让他看出来"我改的和跑的不是同一份"。
    """
    if slot not in SLOTS:
        raise KeyError(slot)
    meta = SLOTS[slot]
    scope = SCOPES[meta["scope"]]
    template, updated_at = _stored(slot)
    return {
        "id": slot, "kind": "builtin", "title": meta["title"],
        "scope": meta["scope"], "scope_label": scope["label"], "page": scope["page"],
        "notes": meta["notes"], "fields": scope["fields"],
        "template": template or meta["default"],
        "default_template": meta["default"],
        "source": "config.setting（页面配置的那份）" if template else "内置默认（还没存过）",
        "edited": bool(template), "updated_at": updated_at,
    }


def custom_rows() -> list[dict[str, Any]]:
    """用户自己新建的模板。一条一个键（`ai_prompt.custom.<id>`），**不是一张表**：
    存一份整数组会在两个入口同时改时后写覆盖前写，而这份是用户手打的资产。"""
    from app.db import engine
    rows = engine.query(
        "select key, value, updated_at from config.setting where key like %s order by key",
        (CUSTOM_PREFIX + "%",), role='reader') or []
    out = []
    for r in rows:
        v = r.get("value") if isinstance(r.get("value"), dict) else {}
        tid = str(r["key"])[len(CUSTOM_PREFIX):]
        scope = v.get("scope")
        if scope not in SCOPES:          # 类型被代码删过：如实交回，让配置页能删掉它
            out.append({"id": tid, "kind": "custom", "title": v.get("title") or tid,
                        "scope": scope, "scope_label": f"（类型 {scope} 已不存在）",
                        "page": "", "fields": {}, "template": v.get("template") or "",
                        "notes": "这条模板的类型在本机代码里已经没有了，运行时会取不到字段，建议删掉重建。",
                        "edited": True, "updated_at": str(r["updated_at"]) if r.get("updated_at") else None})
            continue
        out.append({"id": tid, "kind": "custom", "title": v.get("title") or tid,
                    "scope": scope, "scope_label": SCOPES[scope]["label"],
                    "page": SCOPES[scope]["page"], "fields": SCOPES[scope]["fields"],
                    "notes": "", "template": v.get("template") or "",
                    "edited": True, "updated_at": str(r["updated_at"]) if r.get("updated_at") else None})
    return out


def list_slots() -> list[dict[str, Any]]:
    """配置页与页面下拉共用的一份清单：内置槽位 + 用户模板。"""
    return [effective(slot) for slot in SLOTS] + custom_rows()


def save(slot: str, template: str) -> dict[str, Any]:
    """覆盖某个内置槽位的话术。空话术、未知占位符、缺不给点位约束都拒绝并点名。"""
    if slot not in SLOTS:
        raise KeyError(slot)
    text = _check_text(template, fields_of(SLOTS[slot]["scope"]))
    from app.db import engine
    engine.execute(
        """
        INSERT INTO config.setting (key, value, label, category)
        VALUES (%s, %s::jsonb, %s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (SETTING_PREFIX + slot, json.dumps({"template": text}, ensure_ascii=False),
         f"AI 提示词 · {SLOTS[slot]['title']}", "ai_prompt"), role='app')
    return effective(slot)


def save_custom(title: str, scope: str, template: str, tid: str = "") -> dict[str, Any]:
    """新建/修改一份用户模板。返回库里**实际生效的那一份**，不让调用方假定已写入。"""
    if scope not in SCOPES:
        raise ValueError(f"没有这个类型：{scope}；可用的是 " + "、".join(SCOPES))
    name = (title or "").strip()
    if not name:
        raise ValueError("模板要起个名字（页面上的下拉靠它认）")
    if len(name) > 60:
        raise ValueError(f"模板名太长了（{len(name)} 字），下拉里会被截断看不清，控制在 60 字内")
    text = _check_text(template, fields_of(scope))
    tid = (tid or "").strip()
    if tid and not _ID_RE.fullmatch(tid):
        raise ValueError("模板 id 只能是字母/数字/下划线/短横线")
    if not tid:
        tid = uuid.uuid4().hex[:12]
    from app.db import engine
    engine.execute(
        """
        INSERT INTO config.setting (key, value, label, category)
        VALUES (%s, %s::jsonb, %s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
            label = EXCLUDED.label, updated_at = now()
        """,
        (CUSTOM_PREFIX + tid,
         json.dumps({"title": name, "scope": scope, "template": text}, ensure_ascii=False),
         f"AI 提示词 · 自建模板「{name}」", "ai_prompt"), role='app')
    row = next((r for r in custom_rows() if r["id"] == tid), None)
    if row is None:
        raise RuntimeError("写入后读不回来，不假定已生效")
    return row


def delete_custom(tid: str) -> None:
    if not _ID_RE.fullmatch((tid or "").strip()):
        raise ValueError("模板 id 不合法")
    from app.db import engine
    engine.execute("DELETE FROM config.setting WHERE key = %s", (CUSTOM_PREFIX + tid.strip(),), role='app')


def reset(slot: str) -> dict[str, Any]:
    """恢复默认 = **删掉这个键**，不是把默认文本再存一份进去 ——
    存进去的那份以后就是"自定义"，改了内置默认也不会跟着走。"""
    if slot not in SLOTS:
        raise KeyError(slot)
    from app.db import engine
    engine.execute("DELETE FROM config.setting WHERE key = %s", (SETTING_PREFIX + slot,), role='app')
    return effective(slot)


def template_of(ident: str) -> Optional[dict[str, Any]]:
    """按 id 取一份模板（内置槽位或自建模板都认这个入口，页面下拉只管传 id）。"""
    for row in list_slots():
        if row["id"] == ident:
            return row
    return None


def render(slot: str, values: dict[str, Any]) -> str:
    """服务端渲染内置槽位当前生效的那一份。"""
    return render_text(effective(slot)["template"], values)


def render_text(template: str, values: dict[str, Any]) -> str:
    """与前端 `lib/prompts.ts` 同一条规则：已知字段换值，
    值为 None/空 一律换 `未提供`（**不换 0**），未知占位符原样留着。
    """

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            return m.group(0)          # 未知占位符：留着比编一个值好，一眼能看见
        val = values[key]
        if val is None:
            return "未提供"
        if val == "" and key not in _NULLABLE:
            return "未提供"
        return str(val)

    return PLACEHOLDER.sub(sub, template or "")


# 这两行是"整句开关"：没有实时报价/没有东财链接时**就该是空串**（整句不出现），
# 不是"未提供" —— 写"未提供"会让模型以为有这么一句但缺内容。
_NULLABLE = {"quote_line", "em_line"}
