"""Freeze public review snapshots and expose a small, deterministic tool surface.

Numeric facts are rendered by the host. Model prose is qualitative inference;
checking references cannot establish that an inference is semantically correct.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import stat
import unicodedata
from .product_policy import has_trade_recommendation
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from duanxian.verification import METRICS

# Definitions follow the deterministic producers in emotion_metrics and
# market_facts. The envelope kind describes storage, not sample coverage.
METRIC_SCOPE = {
    "limit_up_count": "当日数据源涨停池中的家数，覆盖范围以该池为准。",
    "highest_board": "当日涨停池样本中的最高连板高度。",
    "promotion_1to2": "前一交易日首板样本在当日晋级的比例，非全市场上涨比例。",
    "money_effect_median": "昨日涨停样本中已取得当日表现的涨跌幅中位数，非全市场中位数，也非个人收益。",
    "broken_rate": "当日炸板未回封样本占涨停与炸板尝试样本之和的比例。",
    "deep_loss_count": "昨日涨停样本中已取得当日表现且跌幅达到或超过百分之五的家数，非全市场深跌家数。",
    "theme_concentration": "数据源行业分组中头部分组的涨停家数占当日涨停池的比例；行业分组不等同于概念题材。",
    "market_limit_down": "当日数据源全市场跌停池家数；定稿样本路径无法提供时保持未获取。",
}

# quantdash 口径轨读数（STRUCTURED_DATA_RULES 第 6 条：AI 与人必须读同一份结构化结果，
# 所以这里只许调 duanxian/qd.py 那个服务层，不许为 AI 另开取数口）。
# key 前缀 qd_ 是刻意的：这些指标与本轨同名指标**不是同一个算法**，注册进
# duanxian.verification.METRICS 之外，compare_metric 因此不会跨口径相减。
QD_METRICS = {
    "qd_limit_up_count": ("涨停家数（quantdash 口径）", "家"),
    "qd_limit_down_count": ("跌停家数（quantdash 口径）", "家"),
    "qd_touched_count": ("触板家数（quantdash 口径，涨停加炸板未回封）", "家"),
    "qd_broken_touched_count": ("炸板未回封家数（quantdash 口径）", "家"),
    "qd_board_height": ("最高连板高度（quantdash 口径）", "板"),
    "qd_high_board_count": ("高板家数（quantdash 口径）", "家"),
    "qd_first_board_count": ("首板家数（quantdash 口径）", "家"),
    "qd_first_board_ratio_pct": ("首板占比（quantdash 口径）", "%"),
    "qd_rise_count": ("上涨家数（quantdash 口径）", "家"),
    "qd_fall_count": ("下跌家数（quantdash 口径）", "家"),
    "qd_amount_yi": ("成交额（quantdash 口径）", "亿"),
}
QD_SCOPE = ("以上是 quantdash 口径的另一套算法读数，与本轨同名指标不等价：两者样本范围、"
            "阈值和分母都可能不同，不得互相顶替或相减。缺数就是缺数，不得当成零继续推理，"
            "也不得因为另一轨有数就断定这里也同样是那个值。")

# 选股口径的"四维象限"与复盘归因的"归因象限"都叫象限、语义正交，标签必须分开，
# 否则模型会把两者揉成一句结论 —— 那比缺数据更坑。名字与页面一致（/quadrant 就叫四维象限）。
QUANT_CELL_PREFIX = "【四维象限·选股口径】"
QUANT_CELL_SCOPE = ("按个股质量 X 与环境共振 Y 的正负分档，回答\"这只票当日处在哪一格\"，标签取自 config.setting.quadrant_labels。"
                    "它与复盘归因的\"判对错×盈亏\"四格不是同一件事，不得互相翻译或合并成一句。"
                    "只统计当日有象限的票：象限为空的票不计入任何一格，四格相加不等于全市场。")


def qd_evidence(anchor: str) -> tuple[list[dict], list[str]]:
    """把 qd 轨当日读数并进同一份证据包；读不到只成缺口，绝不让复盘失败。"""
    from duanxian import qd
    try:
        result = qd.sentiment_cycle(anchor)
    except Exception:  # noqa: BLE001  本地库未就绪等任何原因都不能连坐整场复盘
        return [], [f"quantdash 口径轨当日读数不可读取，本轮只有本地口径"]
    if not isinstance(result, dict) or result.get("available") is not True or result.get("date") != anchor:
        reason = result.get("reason") if isinstance(result, dict) else "读数结构不可用"
        return [], [f"quantdash 口径轨 {anchor} 无读数：{reason or '未回填'}"]
    metrics = ((result.get("qd") or {}).get("metrics")) or {}
    provenance = {key: result.get(key) for key in ("spec_version", "source", "computed_by") if result.get(key)}
    # 快照记录钉在文件哈希上；qd 读数钉在"本轮实际读到的那份结构"上，
    # 任何一个数变了，证据 id 与包版本都跟着变。
    pin = digest({"metrics": metrics, **provenance})
    records = []
    for column, (label, unit) in QD_METRICS.items():
        value = metrics.get(column.removeprefix("qd_"))
        available = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        record = {"date": anchor, "track": "qd", "source": "quantdash 口径轨只读服务层 duanxian/qd.py",
                  "source_sha256": pin,
                  "kind": "metric", "metric": column, "label": label,
                  "value": float(value) if available else None, "unit": unit, "available": available,
                  "display": display_number(value) + " " + unit if available else "未获取",
                  "note": QD_SCOPE, **provenance}
        record["id"] = "ev-" + digest(record)[:16]
        records.append(record)
    return records, []


def _quant_records(anchor: str, dist: dict, coverage: dict) -> list[dict]:
    """四维象限当日四格分布。个股明细（一天五千行）不进模型边界。"""
    span = "、".join(f"{name} {v.get('from')}→{v.get('to')}" for name, v in (coverage or {}).items() if v.get("from"))
    pin = digest({"dist": dist, "coverage": coverage})
    records = []
    for cell in ("Q1", "Q2", "Q3", "Q4"):
        row = dist.get(cell) or {}
        value = row.get("count")
        countable = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        label = f"{QUANT_CELL_PREFIX}{cell} {row.get('label', '')}".strip()
        record = {"date": anchor, "track": "own", "source": "量化选股只读服务层 duanxian/quant.py",
                  "source_sha256": pin, "kind": "metric", "metric": f"quant_quadrant_{cell.lower()}_count",
                  "label": label, "value": float(value) if countable else None, "unit": "只",
                  "available": countable,
                  "display": display_number(value) + " 只" if countable else "未获取",
                  "note": QUANT_CELL_SCOPE + (f"覆盖区间：{span}。" if span else "")}
        record["id"] = "ev-" + digest(record)[:16]
        records.append(record)
    return records


def quant_evidence(anchor: str) -> tuple[list[dict], list[str]]:
    """选股口径读数并进同一份冻结包；与 /api/quant/* 走同一个服务层函数。"""
    from duanxian import quant
    try:
        result = quant.quadrant(anchor)
    except Exception:  # noqa: BLE001  本地库未就绪不能连坐整场复盘
        return [], [f"量化选股口径当日读数不可读取，本轮只有本地复盘与 quantdash 口径"]
    if not isinstance(result, dict) or result.get("available") is not True or result.get("date") != anchor:
        reason = result.get("reason") if isinstance(result, dict) else "读数结构不可用"
        return [], [f"量化选股口径 {anchor} 无读数：{reason or '未回填'}"]
    if str(result.get("labels_source", "")).startswith("兜底"):
        # 标签读不到就不引用称谓：写死一套中文名会把"库里的口径"换成"我们猜的口径"。
        return [], ["量化选股口径的象限标签未能从 config.setting 读到，本轮不引用象限称谓，避免张冠李戴"]
    return _quant_records(anchor, result.get("distribution") or {}, result.get("coverage") or {}), []


HEAT_PREFIX = "【板块周期·热度口径】"
HEAT_STAGES = (("ignite", "点火"), ("follow", "延续"), ("fade", "退潮"), ("coil", "蓄势"), ("none", "无信号"))
# 一句话版口径，随每条读数交出：热度与点火是两套判据，阶段计数是点火那套的产物。
HEAT_SCOPE = ("当日在场板块按点火判据分档的板块数，分母是同一份服务层结果里的 `universe`（当日进榜板块数），"
              "不是全市场票数。`heat`（涨幅/广度/动力/成交四分项）与 `score`（五要素点火）是两套判据、两个榜，"
              "排序会分叉，不许互相翻译，也不许由其中一套去断另一套的对错。"
              "**行业板块的点火档长期为 0 是算不出**（行业广度大面积为空 ⇒ 点火判据永远不达标），"
              "读成\"那天没有行业轮动\"就是错的；同一天的概念与风格是有档位分布的。")


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def heat_evidence(anchor: str) -> tuple[list[dict], list[str]]:
    """板块热度当日读数并进同一份冻结包：与 `/api/quant/sector-heat` 同一个服务层函数。

    只带市场级聚合（阶段分布 + 分母），不带 688 行板块明细 —— 个股与板块明细不进模型边界，
    这条与选股象限那次同样的取舍保持一致。
    """
    from duanxian import quant
    try:
        result = quant.sector_heat(anchor)
    except Exception:  # noqa: BLE001  本地库未就绪不能连坐整场复盘
        return [], ["板块热度当日读数不可读取，本轮只有本地复盘、quantdash 口径与量化选股口径"]
    if not isinstance(result, dict) or result.get("available") is not True or result.get("date") != anchor:
        reason = result.get("reason") if isinstance(result, dict) else "读数结构不可用"
        return [], [f"板块热度 {anchor} 无读数：{reason or '未回填'}"]
    if str((result.get("heat_source_caliber") or {}).get("used", "")).startswith("member_agg"):
        # 已废弃口径（摊下去用唯一主板块、聚回来用全部归属）的两头不一致没法翻译，
        # 引用它的分档就是把一个已知有错的口径当成当日事实。
        return [], ["板块热度当日只读到已废弃的 member_agg 口径，本轮不引用热度分档"]
    universe = result.get("universe") or {}
    stages = {row.get("stage"): row.get("n") for row in (result.get("stage_distribution_today") or [])
              if isinstance(row, dict)}
    pin = digest({"stages": stages, "universe": universe})
    records = []
    for field, label, unit in (("rows_today", "当日进榜板块数（分母）", "个板块"),
                              ("unnamed_today", "其中名字等于代码的板块行数", "行")):
        value = universe.get(field)
        if field == "unnamed_today" and not _finite(value):
            continue   # 这一项只是分母的说明，缺了不影响阶段读数，不硬凑一个 0
        available = _finite(value)
        record = {"date": anchor, "track": "own", "source": "板块热度只读服务层 duanxian/quant.py:sector_heat",
                  "source_sha256": pin, "kind": "metric", "metric": f"heat_universe_{field}",
                  "label": f"{HEAT_PREFIX}{label}", "value": float(value) if available else None,
                  "unit": unit, "available": available,
                  "display": display_number(value) + " " + unit if available else "未获取",
                  "note": HEAT_SCOPE}
        record["id"] = "ev-" + digest(record)[:16]
        records.append(record)
    counted = _finite(universe.get("rows_today"))
    for stage, cn in HEAT_STAGES:
        value = stages.get(stage)
        # 分档缺席只有在"当日母体确实数得出来"时才等于 0 个；母体本身没有就是没读到，不折成 0。
        available = _finite(value) or counted
        record = {"date": anchor, "track": "own", "source": "板块热度只读服务层 duanxian/quant.py:sector_heat",
                  "source_sha256": pin, "kind": "metric", "metric": f"heat_stage_{stage}_count",
                  "label": f"{HEAT_PREFIX}{cn}阶段板块数", "value": float(value or 0) if available else None,
                  "unit": "个板块", "available": available,
                  "display": display_number(value or 0) + " 个板块" if available else "未获取",
                  "note": HEAT_SCOPE + ("该档在当日分布里没有出现，按母体在场计为零个 —— 是算出来的零。"
                                        if stage not in stages and available else "")}
        record["id"] = "ev-" + digest(record)[:16]
        records.append(record)
    return records, []


class EvidenceError(ValueError):
    pass


def valid_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise EvidenceError("交易日格式不正确")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceError("交易日不存在") from exc
    return value


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def clean_text(text: str, limit: int = 5000) -> str:
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cf"})
    return text[:limit]


def display_number(value: float | Decimal) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def mask_qualitative_numbers(text: str) -> str:
    """Mask existing fixed expressions without moving remaining character offsets."""
    for metric in METRICS:
        text = text.replace(metric.label, " " * len(metric.label))
    return re.sub(r"一手|单一|唯一|同一|统一|清一色|一段(?:时间|交易日)|一部分|一侧|一些|一直|一样|一系列|一体化|混为一谈|混为一体|其[一二三四五六七八九十](?=[是为，,、：:；;。]|$)|一致|一般|一定|进一步|一方面|另一方面|另一|一旦|逐一|一并|(?<![提滞落错靠推挪延])[前后上下]一(?:个)?(?:交易)?日|这一(?:观察|变化|判断|结论)|两点|[两二三]者|[两二三](?:项|个)(?:可比)?(?:指标|变化|观察)|两个(?:交易日|日期|时点)", lambda m: " " * len(m.group()), text)


def has_generated_number(text: str) -> bool:
    text = mask_qualitative_numbers(unicodedata.normalize("NFKC", text))
    return bool(re.search(r"\d|[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億]", text))

def _read_snapshot(path: Path) -> tuple[dict, str]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
            raise EvidenceError("复盘文件不是普通文件或超过读取上限")
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise EvidenceError("复盘文件超过读取上限")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise EvidenceError("复盘文件格式损坏")
    return value, hashlib.sha256(raw).hexdigest()


def build_bundle(root: Path, anchor: str) -> dict:
    """Read at most twenty dated snapshots, never `latest.json` or future days."""
    valid_date(anchor)
    candidates = sorted((p for p in root.glob("*.json")
                         if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem) and p.stem <= anchor), reverse=True)
    if not any(p.stem == anchor for p in candidates):
        raise EvidenceError("所选交易日没有已保存的复盘，请先生成该日复盘")
    records, dates, gaps = [], [], []
    targets = []
    for path in candidates[:20]:
        try:
            valid_date(path.stem)
            payload, sha = _read_snapshot(path)
            if (payload.get("target_date") or payload.get("trade_date")) != path.stem:
                raise EvidenceError("复盘内部日期与文件名不一致")
        except (EvidenceError, OSError, ValueError):
            if path.stem == anchor:
                raise EvidenceError("所选复盘损坏或不可安全读取，请重新生成") from None
            gaps.append(f"{path.stem} 的历史快照不可读取")
            continue
        dates.append(path.stem)
        base = {"date": path.stem, "track": "own", "source": "本地公开市场复盘快照",
                "source_file": path.name, "source_sha256": sha}
        # Project only registered market metrics. No generic recursive walk of a
        # review payload: unknown fields must never cross the model boundary.
        metrics = payload.get("emotion_metrics")
        facts = payload.get("market_facts")
        if path.stem == anchor and isinstance(facts, dict):
            structure = facts.get("theme_structure")
            if isinstance(structure, dict) and structure.get("available") is True and isinstance(structure.get("themes"), list):
                for row in structure["themes"][:30]:
                    if not isinstance(row, dict) or not isinstance(row.get("sector"), str):
                        continue
                    sector = clean_text(row["sector"], 60)
                    stocks = [{"code": s["code"], "name": clean_text(s["name"], 40)} for s in row.get("names", [])[:30]
                              if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("code"), str)
                              and re.fullmatch(r"\d{6}", s["code"])] if isinstance(row.get("names"), list) else []
                    targets.append({"sector": sector, "stocks": stocks})
                    for key, label, unit in [("limit_up", "涨停家数", "家"), ("highest", "最高连板", "板")]:
                        value = row.get(key)
                        available = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
                        record = {**base, "kind": "sector", "metric": "sector_" + key, "sector": sector,
                                  "label": sector + " · " + label, "value": value if available else None,
                                  "unit": unit, "available": available,
                                  "display": display_number(value) + " " + unit if available else "未获取",
                                  "note": "复盘来源的行业分组与涨停样本；不代表完整板块成分，也不等同于概念题材。"}
                        record["id"] = "ev-" + digest(record)[:16]
                        records.append(record)
        for metric in METRICS:
            try:
                value = metric.getter(metrics if isinstance(metrics, dict) else {},
                                      facts if isinstance(facts, dict) else {})
            except (TypeError, ValueError, AttributeError, KeyError):
                value = None
            available = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            unit = metric.unit or "%"
            if available and not metric.unit:
                value = float(Decimal(str(value)) * 100)
            if not available:
                value = None
            record = {**base, "kind": "metric", "metric": metric.key, "label": metric.label,
                      "value": value, "unit": unit, "available": available,
                      "display": display_number(value) + " " + unit if available else "未获取",
                      "note": "历史快照中的程序计算值；原始行情明细未在本轮重新核验。" + METRIC_SCOPE[metric.key]}
            record["id"] = "ev-" + digest(record)[:16]
            records.append(record)
        narrative = payload.get("focus_md")
        if isinstance(narrative, str) and narrative.strip():
            record = {**base, "kind": "narrative", "label": "既有 AI 复盘叙述（线索）",
                      "text": clean_text(narrative), "available": True,
                      "note": "这是此前 AI 的叙述，不是一手事实；不能据此引用新的市场数字。"}
            record["id"] = "ev-" + digest(record)[:16]
            records.append(record)
    if not dates:
        raise EvidenceError("没有可读取的公开市场快照")
    qd_records, qd_gaps = qd_evidence(anchor)
    quant_records, quant_gaps = quant_evidence(anchor)
    heat_records, heat_gaps = heat_evidence(anchor)
    records.extend(qd_records)
    records.extend(quant_records)
    records.extend(heat_records)
    gaps.extend(qd_gaps)
    gaps.extend(quant_gaps)
    gaps.extend(heat_gaps)
    bundle = {"anchor": anchor, "dates": dates, "evidence": records, "gaps": gaps, "targets": targets,
              "scope": "所选日及之前最多二十份已存公开复盘，外加 quantdash 口径轨、量化选股口径与板块热度的当日读数"
                       "（各自同一份服务层结果）；网络权限以 context.allow_network 为准，默认关闭。"}
    return {**bundle, "revision": digest(bundle)}


class ToolSession:
    """One turn, one allowlisted tool ledger. No path or expression arguments."""
    def __init__(self, bundle: dict, log: Path):
        self.bundle, self.log = bundle, log
        self.records = {e["id"]: e for e in bundle["evidence"]}
        self.used: set[str] = set()
        self.calls = 0
        self.answer = None
        self.submissions = 0
        self.network_gaps = []
        self.network_calls = 0
        self.replaying = False

    def _record(self, name: str, args: dict, ids: list[str]) -> None:
        if self.answer is not None:
            raise EvidenceError("回答已确认，本轮不能继续读取或修改")
        self.calls += 1
        if self.calls > 24:
            raise EvidenceError("本轮工具调用达到上限，请缩小问题范围")
        self.used.update(ids)
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(canonical({"tool": name, "args": args, "ids": ids}) + "\n")

    def list_evidence(self) -> dict:
        self._record("list_evidence", {}, [])
        return {"anchor": self.bundle["anchor"], "dates": self.bundle["dates"],
                "scope": self.bundle["scope"], "context": self.bundle.get("context", {}), "gaps": self.bundle["gaps"],
                "targets": self.bundle.get("targets", []),
                "items": [{k: e[k] for k in ("id", "date", "label", "kind", "available", "metric", "sector", "symbol", "track") if k in e}
                          for e in self.records.values()]}

    def read_evidence(self, ids: list[str]) -> list[dict]:
        if not isinstance(ids, list) or not 1 <= len(ids) <= 16 or any(
                not isinstance(eid, str) or eid not in self.records for eid in ids):
            raise EvidenceError("只能读取目录里已有的证据，每次最多十六条")
        self._record("read_evidence", {"ids": ids}, ids)
        return [self.records[eid] for eid in ids]

    def compare_metric(self, metric: str, first_date: str, last_date: str) -> dict:
        valid_date(first_date)
        valid_date(last_date)
        if first_date >= last_date:
            raise EvidenceError("比较必须从较早日期到较晚日期")
        if metric not in {m.key for m in METRICS}:
            raise EvidenceError("不支持这个指标")
        inputs = [next((e for e in self.bundle["evidence"] if e.get("metric") == metric
                        and e["date"] == d and e["available"]), None) for d in (first_date, last_date)]
        if any(e is None for e in inputs):
            raise EvidenceError("缺少该日期的有效指标；缺失不能作为零参与计算")
        first, last = inputs
        value = Decimal(str(last["value"])) - Decimal(str(first["value"]))
        unit = "个百分点" if first["unit"] == "%" else first["unit"]
        result = {"kind": "calculation", "metric": metric, "label": first["label"] + "变化", "track": first.get("track", "own"),
                  "date": last_date, "first_date": first_date, "value": float(value), "unit": unit,
                  "display": display_number(value) + " " + unit,
                  "inputs": [first["id"], last["id"]], "available": True,
                  "source": "确定性计算：较晚值减较早值", "note": "两点比较不代表连续趋势。" + METRIC_SCOPE[metric]
                  + "跨日样本成员及覆盖可能不同。"}
        result["id"] = "calc-" + digest(result)[:16]
        self.records[result["id"]] = result
        self._record("compare_metric", {"metric": metric, "first_date": first_date, "last_date": last_date},
                     result["inputs"] + [result["id"]])
        return result

    def submit_answer(self, status: str, findings: list[dict], gaps: list[str]) -> dict:
        """Validation feedback stays inside the current engine turn, not a new run."""
        if self.answer is not None:
            raise EvidenceError("回答已确认，请结束本轮")
        self.submissions += 1
        payload = {"status": status, "findings": findings, "gaps": gaps}
        # Record rejected submissions too: replay must enforce the same limit.
        self._record("submit_answer", payload, [])
        if self.submissions > 4:
            raise EvidenceError("回答校正次数已用完，请结束本轮")
        try:
            answer = validate_answer(payload, self)
        except EvidenceError as exc:
            return {"accepted": False, "error": str(exc), "attempts_left": 4 - self.submissions}
        self.answer = {**answer, "format_corrections": self.submissions - 1}
        return {"accepted": True, "instruction": "回答已保存。请结束本轮，不再调用工具。"}

    def fetch_stock_prices(self, symbol: str, first_date: str, last_date: str) -> dict:
        from .public_data import query_args, retrieve, normalize
        context = self.bundle.get("context", {})
        if self.answer is not None:
            raise EvidenceError("回答已确认")
        if not context.get("allow_network"):
            raise EvidenceError("本会话尚未启用公开行情补充")
        allowed = {s["code"] for t in self.bundle.get("targets", []) for s in t["stocks"]}
        allowed.add(context.get("symbol", ""))
        if symbol not in allowed:
            raise EvidenceError("代码不在本会话的复盘样本或手动选择范围内")
        args = query_args(symbol, first_date, last_date, self.bundle["anchor"])
        if self.network_calls >= 4:
            raise EvidenceError("本轮公开行情查询达到上限")
        self.network_calls += 1
        path = self.log.parent / ("public-" + digest(args)[:16] + ".json")
        if self.replaying or path.exists():
            try:
                receipt, _ = _read_snapshot(path)
            except (OSError, ValueError) as exc:
                raise EvidenceError("公开行情缓存损坏或无法读取，请新建会话重新获取；本次不采用损坏证据") from exc
        else:
            receipt = retrieve(args)
            from duanxian.util import atomic_write_json
            if len(canonical(receipt).encode("utf-8")) > 2_000_000:
                raise EvidenceError("公开行情缓存超过读取上限，本次未采用")
            if not atomic_write_json(str(path), receipt):
                raise EvidenceError("公开行情证据写入失败，本次未采用")
        records, gaps = normalize(receipt, args)
        for record in records:
            record.setdefault("track", "own")  # 公开历史行情只有本轨一条来源，不存在 qd 口径的收盘价
        self.network_gaps.extend(gaps)
        self.records.update({r["id"]: r for r in records})
        self._record("fetch_stock_prices", args, [r["id"] for r in records])
        return {"status": "partial" if records else "unavailable", "evidence": records, "gaps": gaps}

    def compare_stock_prices(self, first_id: str, last_id: str) -> dict:
        first, last = self.records.get(first_id), self.records.get(last_id)
        if not first or not last or any(r.get("kind") != "stock_price" for r in (first, last)) or first["symbol"] != last["symbol"] or first["date"] >= last["date"]:
            raise EvidenceError("请选择同一代码的前后两条历史收盘证据")
        value = Decimal(str(last["value"])) - Decimal(str(first["value"]))
        result = {"kind": "price_change", "metric": "stock_close", "symbol": first["symbol"], "track": first.get("track", "own"),
                  "date": last["date"], "first_date": first["date"], "value": float(value), "unit": "元",
                  "display": display_number(value) + " 元", "label": first["symbol"] + " 收盘变化",
                  "inputs": [first_id, last_id], "available": True, "source": "确定性计算：较晚值减较早值",
                  "note": "不复权收盘差；跨除权日不能直接解释为投资收益。"}
        result["id"] = "calc-" + digest(result)[:16]
        self.records[result["id"]] = result
        self._record("compare_stock_prices", {"first_id": first_id, "last_id": last_id}, result["inputs"] + [result["id"]])
        return result

    def replay(self) -> None:
        """Recompute citations from successful tool calls, never trust model ids."""
        if not self.log.exists():
            return
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.replaying = True
        if len(lines) > 24:
            raise EvidenceError("工具调用记录超限")
        # Replay in a separate in-memory sink, so the audit ledger stays unchanged.
        def replay_record(name, args, ids):
            if self.answer is not None:
                raise EvidenceError("确认回答后出现额外工具调用")
            self.used.update(ids)
        self._record = replay_record
        for line in lines:
            event = json.loads(line)
            method = {"list_evidence": self.list_evidence, "read_evidence": self.read_evidence,
                      "compare_metric": self.compare_metric, "submit_answer": self.submit_answer,
                      "fetch_stock_prices": self.fetch_stock_prices, "compare_stock_prices": self.compare_stock_prices}.get(event.get("tool"))
            if method is None:
                raise EvidenceError("工具记录包含未允许的调用")
            method(**event["args"])


def validate_answer(payload: object, session: ToolSession) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"status", "findings", "gaps"}:
        raise EvidenceError("Agent 返回格式不完整")
    if payload["status"] not in ("complete", "incomplete"):
        raise EvidenceError("Agent 返回状态无效")
    findings, gaps = payload["findings"], payload["gaps"]
    if not isinstance(findings, list) or not 1 <= len(findings) <= 8 or not isinstance(gaps, list) or len(gaps) > 10:
        raise EvidenceError("Agent 返回的分析条数无效")
    used, out = [], []
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"text", "citations"}:
            raise EvidenceError("分析条目格式无效")
        text, refs = finding["text"], finding["citations"]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
            raise EvidenceError("分析文字长度无效")
        text = clean_text(text, 1000).strip()
        if not text:
            raise EvidenceError("分析文字为空")
        # Numbers live exclusively in host-rendered facts/cards. This prevents
        # mixing a true id with a made-up Arabic number in generated prose.
        if has_generated_number(text):
            raise EvidenceError("解释中含自由生成的数字（含汉字零等），请以证据卡显示数值；缺口请写未获取，不要写零")
        if not isinstance(refs, list) or len(refs) > 12 or any(not isinstance(r, str) or r not in session.used for r in refs):
            raise EvidenceError("回答引用了本轮未读取的证据")
        if not refs and payload["status"] == "complete":
            raise EvidenceError("完整分析必须引用本轮证据")
        if re.search(r"复盘叙述|情绪标签|定性为", text) and not any(session.records[r]["kind"] == "narrative" for r in refs):
            raise EvidenceError("提及旧复盘叙述时，请在本轮读取并引用相应叙述证据")
        if has_trade_recommendation(text):
            raise EvidenceError("回答越过复盘分析边界")
        used.extend(refs)
        out.append({"text": clean_text(text, 1000), "citations": refs})
    if any(not isinstance(g, str) or not g.strip() or len(g) > 500 for g in gaps):
        raise EvidenceError("数据缺口格式无效")
    gaps = [clean_text(g, 500).strip() for g in gaps]
    if any(not g for g in gaps):
        raise EvidenceError("数据缺口为空")
    for gap in gaps:
        if has_generated_number(gap):
            raise EvidenceError("数据缺口中含自由生成的数字（含汉字零等）；请写未获取，不要写零")
        if has_trade_recommendation(gap):
            raise EvidenceError("回答越过复盘分析边界")
    if payload["status"] == "incomplete" and not gaps:
        raise EvidenceError("未完成分析必须说明数据缺口")
    records = [session.records[eid] for eid in dict.fromkeys(used)]
    for record in list(records):
        for eid in record.get("inputs", []):
            if eid not in {e["id"] for e in records}:
                records.append(session.records[eid])
    missing = [f"{e['date']} {e['label']}未获取" for e in records if not e["available"]]
    all_gaps = list(dict.fromkeys([*gaps, *session.bundle["gaps"], *missing, *session.network_gaps]))
    return {"status": "incomplete" if all_gaps else payload["status"], "findings": out,
            "gaps": [clean_text(g, 500) for g in all_gaps], "evidence": records}
