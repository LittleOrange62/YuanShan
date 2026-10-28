"""快讯采集（财联社的替代源）—— 把盘中资讯落到 `qd.news_item`。

## 为什么不是财联社

派活契约（`数据加工工作指南/09` §4.4）写的端点 `https://www.cls.cn/nodeapi/telegraphList`
**2026-09-24 实测 404**（`updateTelegraphList`、`nodeapi/depthList` 同样 404；旧版
`/v1/roll/get_roll_list` 还活着但要求另一套签名，返回 `errno 10012 签名错误`）。
域名没被墙，是接口改版。所以这一路换成两个**免鉴权、字段稳定、能增量**的 7×24 快讯源：

| | 主源 | 备源 |
|---|---|---|
| 端点 | `np-weblist.eastmoney.com/comm/web/getFastNewsList` | `zhibo.sina.com.cn/api/zhibo/feed`（`zhibo_id=152` 财经直播） |
| 去重键 | `code`（20 位，日内稳定） | `id`（自增整数） |
| 发布时刻 | `showTime` | `create_time` |
| 游标 | `realSort`（= 响应的 `data.sortEnd`） | `id` 单调 |
| 白送的东西 | `stockList` —— 官方已把快讯关联到个股代码 | `tag[].name` 分类（焦点/国际…） |

**口径照搬 quantdash 的四条裁定**：30 秒一轮（它 5 秒太密）、游标落库不落文本、
不截断条数、`sentiment` 一律 NULL（它根本没做情感，不许前端硬编码分数冒充）。

## 三条不许打折的纪律

1. **`published_at` 与 `captured_at` 分列，且前者只能来自报文。** 拿 `now()` 冒充发布时刻
   是 quantdash 的 D-25，会让"几点出的消息"这件事彻底失去意义。报文字段缺失或解析失败
   → 整条丢弃并计数，**不许回填采集时刻**。
2. **`level` 两源都没有对应字段，一律 NULL。** 财联社的 A/B 级是"重要电报"标记，
   东财的 `titleColor`、新浪的 `is_focus` 看着像但不是同一件事，**拿它们推 level 就是编数**。
   前端按"NULL → 不显示情感/级别标签"处理。
3. **落盘失败不许推游标。** 落进去多少条，游标才走到第几条；否则一次网络抖动就把那段
   资讯永久跳过（快讯不可回补，这是它和日线数据的根本区别）。

## 落点（2026-09-24 用户裁定：YuanShan 是唯一落地点）

数据落 **YuanShan 自己的业务数据目录** `data_path("news")`，见下文「落盘」一节的三条理由。
**不再等 PG 的 `qd.news_item`，也不再需要 `ft_etl` 凭据** —— 原先那两处阻塞（游标表没建、
只读账号写不了）随这条裁定一起消失。`qd.news_item` 退为可选对照，由总协调那侧决定是否镜像。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

from .paths import data_path
from .util import atomic_write_json, china_now

logger = logging.getLogger(__name__)

SPEC_VERSION = "qd-1"
LOGIC_SOURCE = "quantdash-logic"

POLL_SECONDS = 30
BACKFILL_MINUTES = 240          # 照搬 quantdash 的启动回溯窗口
PAGE_SIZE = 50

EM_URL = ("https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
          "?client=web&biz=web_724&fastColumn={column}&sortEnd={sort_end}"
          "&pageSize={size}&req_trace={trace}")
EM_COLUMNS = {"102": "全部要闻", "103": "公司公告", "104": "机构解读"}
SINA_URL = ("https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size={size}"
            "&zhibo_id=152&tag_id=0&dire=f&dpc=1")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json",
}

# 东财/新浪都给 "2026-09-24 21:57:24" 这种**无时区的北京时间**字符串。
# 列是 timestamptz，必须显式挂上 +08:00 —— 交给 PG 按会话时区解释的话，
# 换台机器或改时区就会整体平移 8 小时，而界面上完全看不出异样。
_CN_TZ = datetime.timezone(datetime.timedelta(hours=8))
_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _now_iso() -> str:
    return china_now().isoformat(timespec="seconds")


def _parse_published(raw: Any) -> Optional[datetime.datetime]:
    """报文的发布时刻 → 带时区 datetime。解析不了就是 None，**绝不回填采集时刻**。"""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        naive = datetime.datetime.strptime(s[:19], _TIME_FMT)
    except ValueError:
        return None
    return naive.replace(tzinfo=_CN_TZ)


def _http_json(url: str, timeout: int = 12) -> Any:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise IOError("HTTP %s" % resp.status)
        return json.loads(resp.read().decode("utf-8", "replace"))


def _monotonic(old: Any, new: Any) -> str:
    """游标只许往前走。

    实测东财那一页的 `realSort` **不是严格最新在前**（22:36 那轮拿到 `...601963`，
    16 秒后同一请求的最大值反而是 `...595620`）。直接取本页最大值会把游标回带，
    于是每轮都重取一段老消息 —— 靠 `item_id` 去重不会写出重复行，但会把"最近采到哪"
    这个观测值变成假的，调度页面上就是"明明在跑却在原地打转"。
    """
    o, n = str(old or ""), str(new or "")
    if not o:
        return n
    if not n:
        return o
    try:
        return o if int(o) >= int(n) else n
    except ValueError:
        return n          # 非数字游标（换源/格式变更）→ 保守取新值，让去重兜底


def _fallback_id(prefix: str, item: dict) -> str:
    """没有稳定 id 时的去重键 —— 与 quantdash 同一套（ctime + 标题 md5 前 8 位）。"""
    stem = "%s-%s" % (item.get("showTime") or item.get("create_time") or "",
                      str(item.get("title") or item.get("rich_text") or ""))
    return "%s-%s-%s" % (prefix, item.get("ctime") or "noid",
                         hashlib.md5(stem.encode("utf-8")).hexdigest()[:8])


def _related_codes(item: dict) -> list:
    """东财 `stockList` → 6 位代码列表（**不补市场后缀，那是猜**）。

    实测形如 `["999.673646", "1.688498"]`，前缀是东财内部市场编号且未见文档，
    猜错的代价是"新闻挂到另一只同号票上"。所以只留 6 位数字，下游要按股过滤就用
    `split_part(security_code, '.', 1)` 去 join 主档 —— 那是确定的，不靠推断。
    这是白送的官方关联，比 quantdash 拿标题做子串匹配强，**别退回关键词匹配**。
    """
    out = []
    for token in (item.get("stockList") or []):
        code = str(token).split(".")[-1].strip()
        if len(code) == 6 and code.isdigit():
            out.append(code)
    return out


def _row(*, item_id, published_at, source, title, content, url, news_type,
         captured_at, deviation, related_codes=()) -> Optional[dict]:
    """组装一行。标题为空或发布时刻解析失败 → 返回 None（调用方计数，不写库）。"""
    title = str(title or "").strip()
    if not title or published_at is None:
        return None
    return {
        "item_id": item_id,
        "published_at": published_at,
        "captured_at": captured_at,
        "source": source,
        "level": None,                      # 见模块头纪律 2：两源都无此字段
        "title": title,
        "content": str(content or "").strip() or None,
        "url": url or None,
        "sentiment": None,                  # 契约：照搬 NULL，不许填"中性"
        "news_type": news_type,
        "related_codes": list(related_codes),   # 东财官方关联（6 位码，不猜后缀）
        "logic_source": LOGIC_SOURCE,
        "spec_version": SPEC_VERSION,
        "deviation": deviation,
    }


def normalize_east(item: dict, captured_at: datetime.datetime,
                   column_label: str = "全部要闻") -> Optional[dict]:
    """东财 7×24 一条 → `qd.news_item` 一行。"""
    code = str(item.get("code") or "").strip()
    published = _parse_published(item.get("showTime"))
    return _row(
        item_id="em-%s" % code if code else _fallback_id("em", item),
        published_at=published,
        source="eastmoney-7x24",
        title=item.get("title"),
        content=item.get("summary"),
        url=("https://finance.eastmoney.com/a/%s.html" % code) if code else None,
        news_type="em-column-%s" % column_label,
        related_codes=_related_codes(item),
        captured_at=captured_at,
        deviation=[{"code": "SRC-1",
                    "note": "契约端点（财联社 telegraphList）2026-09-24 实测 404，本行取自东财 7×24 快讯"},
                   {"code": "SRC-2", "note": "财联社 level(A/B) 无对应字段，本行 level 置 NULL"}],
    )


def normalize_sina(item: dict, captured_at: datetime.datetime) -> Optional[dict]:
    """新浪 7×24 一条 → 一行。`rich_text` 里可能带 HTML，去标签后当正文。"""
    iid = str(item.get("id") or "").strip()
    published = _parse_published(item.get("create_time"))
    tags = [str(t.get("name")).strip() for t in (item.get("tag") or []) if t.get("name")]
    body = re.sub(r"<[^>]+>", "", str(item.get("rich_text") or "")).strip()
    return _row(
        item_id="sina-%s" % iid if iid else _fallback_id("sina", item),
        published_at=published,
        source="sina-7x24",
        title=(body[:60] + "…") if len(body) > 60 else body,
        content=body or None,
        url=item.get("docurl") or None,
        news_type="sina-tag-%s" % (tags[0] if tags else "未分类"),
        captured_at=captured_at,
        # 新浪没有独立标题字段：标题取正文前 60 字截断，这是**展示性截断**不是原文标题
        deviation=[{"code": "SRC-1",
                    "note": "契约端点（财联社 telegraphList）2026-09-24 实测 404，本行取自新浪 7×24 直播"},
                   {"code": "SRC-2", "note": "财联社 level(A/B) 无对应字段，本行 level 置 NULL"},
                   {"code": "SINA-T", "note": "该源无标题字段，title 为正文前 60 字截断"}],
    )


# ---------------------------------------------------------------- 采集

def fetch_east(sort_end: str = "", column: str = "102") -> tuple[list[dict], str]:
    """返回 (原始条目, 新游标 realSort)。游标空 = 从头（最新）拉。"""
    j = _http_json(EM_URL.format(column=column, sort_end=sort_end, size=PAGE_SIZE,
                                 trace=int(china_now().timestamp() * 1000)))
    data = (j or {}).get("data") or {}
    items = data.get("fastNewsList") or []
    rows = [it for it in items if isinstance(it, dict)]
    nxt = str(data.get("sortEnd") or "")
    if rows:
        # realSort 才是可比较的单调游标；sortEnd 是回给你的"下一页"值，首轮可能为空
        mx = max(str(r.get("realSort") or "") for r in rows)
        nxt = mx or nxt
    return rows, nxt


def fetch_sina() -> list[dict]:
    j = _http_json(SINA_URL.format(size=PAGE_SIZE))
    feed = ((j or {}).get("result") or {}).get("data") or {}
    return [it for it in ((feed.get("feed") or {}).get("list") or []) if isinstance(it, dict)]


def collect_once(cursors: dict, backfill_minutes: int = BACKFILL_MINUTES) -> dict:
    """跑一轮。`cursors` 形如 {'em': '1790258244022828', 'sina': '5112220'}，缺项=首次。

    返回 {'available', 'reason', 'rows', 'cursors', 'dropped'}：
      · rows  = 已过时间窗、可直接插入的行
      · cursors 只在**该源抓取成功**时前进；抓取失败保持原值（见模块头纪律 3）
    """
    cutoff = china_now() - datetime.timedelta(minutes=backfill_minutes)
    rows, nxt, dropped = [], dict(cursors), 0
    live: list[str] = []            # 这一轮真正抓成功的源 —— 判定 available 只认它
    failed: dict[str, str] = {}     # 源名 → 最后一次错误（页面上要能看见为什么没数）

    try:
        items, em_cur = fetch_east(str(cursors.get("em") or ""))
        kept = 0
        label = EM_COLUMNS["102"]
        for it in items:
            r = normalize_east(it, china_now(), label)
            if r is None:
                dropped += 1
                continue
            if r["published_at"] >= cutoff:
                rows.append(r)
                kept += 1
        if em_cur:
            nxt["em"] = _monotonic(cursors.get("em"), em_cur)
        logger.info("东财快讯 %d 条（窗内 %d 条）", len(items), kept)
        live.append("eastmoney-7x24")
    except Exception as exc:      # noqa: BLE001  单源挂掉不影响另一源，游标也不许动
        logger.warning("东财快讯抓取失败： %s", exc)
        failed["eastmoney-7x24"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])

    try:
        sitems = fetch_sina()
        hi = str(cursors.get("sina") or "")
        for it in sitems:
            r = normalize_sina(it, china_now())
            if r is None:
                dropped += 1
                continue
            iid = str(it.get("id") or "")
            if iid and (not hi or int(iid) > int(hi)):
                hi = iid
            if r["published_at"] >= cutoff:
                rows.append(r)
        if hi:
            nxt["sina"] = hi
        logger.info("新浪快讯 %d 条", len(sitems))
        live.append("sina-7x24")
    except Exception as exc:      # noqa: BLE001
        logger.warning("新浪快讯抓取失败： %s", exc)
        failed["sina-7x24"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])

    seen: set[str] = set()
    uniq = [r for r in rows if not (r["item_id"] in seen or seen.add(r["item_id"]))]
    sources = [{"source": s_, "ok": True, "last_err": None} for s_ in live]
    for s_ in ("eastmoney-7x24", "sina-7x24"):
        if s_ not in live:
            sources.append({"source": s_, "ok": False, "last_err": failed.get(s_)})
    if not live:
        return {"available": False, "reason": "两个快讯源均抓取失败（详见日志），游标未推进",
                "rows": [], "cursors": cursors, "dropped": dropped, "sources": sources}
    return {"available": True, "reason": None, "rows": uniq, "cursors": nxt,
            "dropped": dropped, "sources": sources}


# ---------------------------------------------------------------- 落盘（业务数据目录）
#
# 落点是 `data_path("news")`（`ASTOCK_DATA_HOME`，默认 `~\.duanxian-agents\news`），
# **不是仓库 `.local/`** —— README 把 `.local/` 定义为"开发日志与运行诊断、默认不进版本控制"，
# 而快讯是要被复盘和 AI 反复读的业务资产，放错地方等于给自己埋"数据去哪了"。
# 与 `intraday` 同一条规矩（`data_path("intraday")`）。
#
# 格式选 **JSONL 追加**，不选 quantdash 那种"整文件重写 + 截断 200 条"：
# 整文件重写意味着一次写入中途崩溃就把已攒的历史全丢掉，200 条上限则是它 JSON 方案的限制、
# 不是业务口径（契约明写"不搬"）。
#
# 按 `published_at` 分日，**不按采集时刻**：00:05 那轮采到的 23:58 消息属于昨天，
# 归错日会让"当天发生了什么"整条时间轴前移，而且看不出来。

CURSOR_FILE = "cursors.json"


def news_dir() -> str:
    d = data_path("news")
    os.makedirs(d, exist_ok=True)
    return d


def _day_file(day: str) -> str:
    return os.path.join(news_dir(), "news-%s.jsonl" % day)


def _existing_ids(path: str) -> set:
    """已落盘的 item_id —— 让重跑/补跑幂等，而不是把同一条消息存两遍。"""
    ids = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ids.add(str(json.loads(line)["item_id"]))
                except Exception:      # noqa: BLE001  半行（上次写崩）跳过，不连累整个文件
                    continue
    except FileNotFoundError:
        pass
    return ids


def read_cursors() -> dict:
    try:
        with open(os.path.join(news_dir(), CURSOR_FILE), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:      # noqa: BLE001  没游标 = 从头回溯 240 分钟，是安全默认而非错误
        return {}


def record_state(result: dict, out: dict, sources: dict) -> None:
    """每轮写 `data_path("ingest")/newswire_state.json`。

    为什么必须落盘：常驻线程的 print 只进 `.local/startup.log`，没人盯日志就等于
    "从来没坏过"。`/api/jobs`（系统配置在读）要的是 `last_ok_at` 与逐源存活，
    这里一次写齐，别让它去猜。**`last_ok_at=null`（从未跑过）与"跑过但零新增"是两种显示。**
    """
    now = _now_iso()
    prev = read_state()
    ok = bool(out.get("ok"))
    st = dict(prev)
    st.update({"last_run_at": now, "ever_ran": True,
               "last_ok_at": now if ok else prev.get("last_ok_at"),
               "consecutive_failures": 0 if ok else int(prev.get("consecutive_failures") or 0) + 1,
               "reason": out.get("reason"),
               "rows_in_round": len(result.get("rows") or []),
               "written": out.get("written", 0), "already_had": out.get("already_had", 0),
               "dropped_bad_payload": result.get("dropped", 0),
               "cursors": {str(k): str(v) for k, v in (result.get("cursors") or {}).items()},
               "sources": sources, "sink": news_dir()})
    try:
        atomic_write_json(str(_state_path()), st)
    except OSError:
        pass                      # 状态写不进别把采集本身带崩，但下一轮还会试


def read_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _state_path():
    from pathlib import Path
    d = Path(data_path("ingest"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "newswire_state.json"


def persist(result: dict) -> dict:
    """先落数据、后推游标。**顺序不许反**：先写游标的话，中途崩溃就是永久跳过那一段
    资讯（快讯没有历史可回补，这与日线完全不同）。"""
    written = skipped = 0
    by_day: dict[str, list] = {}
    for r in result["rows"]:
        day = r["published_at"].astimezone(_CN_TZ).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(r)
    try:
        for day, rows in sorted(by_day.items()):
            path = _day_file(day)
            have = _existing_ids(path)
            fresh = [r for r in rows if r["item_id"] not in have]
            if not fresh:
                skipped += len(rows)
                continue
            skipped += len(rows) - len(fresh)     # 逐日如实计数：调度页面要显示"重复多少条"
            # 上次写崩可能留下**没有换行的半行**（`{"item_id":"tru`）。直接追加会把下一条
            # 完整记录粘进那一行一起变成垃圾 —— 丢的就不再是半行，是一条好数据。
            needs_nl = False
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "rb") as rb:       # 必须用二进制探最后一个字节：文本 a+ 不允许 end 相对 seek
                    rb.seek(-1, os.SEEK_END)
                    needs_nl = rb.read(1) != b"\n"
            with open(path, "a", encoding="utf-8") as fh:
                if needs_nl:
                    fh.write("\n")
                for r in fresh:
                    line = {k: (v.isoformat() if isinstance(v, datetime.datetime) else v)
                            for k, v in r.items()}
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())       # 不刷盘就可能出现"文件半行、游标却已推进"
            written += len(fresh)
        atomic_write_json(os.path.join(news_dir(), CURSOR_FILE),
                          {str(k): str(v) for k, v in result["cursors"].items()})
    except Exception as exc:      # noqa: BLE001  游标不推进 → 下轮重取，已写条目靠 item_id 去重
        logger.error("快讯落盘失败（游标未推进，下轮重试）： %s: %s", type(exc).__name__, str(exc)[:200])
        out = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        record_state(result, out, {s_["source"]: s_ for s_ in result.get("sources") or []})
        return out
    out = {"ok": True, "written": written, "already_had": skipped,
           "days": sorted(by_day), "dir": news_dir()}
    record_state(result, out, {s_["source"]: s_ for s_ in result.get("sources") or []})
    return out
