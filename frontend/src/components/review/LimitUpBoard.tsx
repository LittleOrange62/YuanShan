import { Fragment, useEffect, useMemo, useState } from "react";
import { Loader2, Flame, Sparkles, X, Copy, Check } from "lucide-react";
import { api, type Quote, type ShortTermEmotion, type LianbanStock } from "@/lib/api";
import { GlassCard } from "@/components/ui/GlassCard";
import { NodeCell, NodeWarn } from "@/components/ui/NodeCell";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { TemplatePicker } from "@/components/ui/TemplatePicker";
import { TierPicker, tiersParam } from "@/components/ui/TierPicker";
import { NoticeBar } from "@/components/ui/NoticeBar";
import { useDeepDive, DeepDivePanel, RunAllButton, type DiveItem } from "@/components/ui/DeepDive";
import { renderPrompt, useTemplatePicker, type PromptValues } from "@/lib/prompts";
import { PAGE_SIZES, canStep, clampPage, pageCount, pageSentence, slicePage } from "@/lib/paging";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { useTableSort } from "@/lib/useSort";
import { emUrl } from "@/lib/quoteLink";
import { useLinkTemplates } from "@/lib/quoteLinkTemplates";
import { pctColor } from "@/lib/colors";
import { finite, plain } from "@/lib/agent";
import { cn } from "@/lib/utils";

/**
 * 「涨停分析」＝原首页「短线情绪」那张连板股表 与 本页「首板样本」并成的一张表
 * （用户 2026-10-28 裁定：改名涨停分析、两块并一张、格式照首板样本、列全部并排）。
 *
 * 母体只认一把尺：**本地库** `analytics.limit_up_pool_daily`（lup-1，加工段每日落库）。
 * 原「首板样本」那一版每次请求现抓东财池，与库里这批行不是同一批票（本地含北交所与 ST），
 * 并成一张表之后按 2026-10-26 已裁的「以库为准」走 —— 池子那条路在本页只剩下
 * 「涨停原因／所属行业 重复出现的类别」两张榜（行业列本机只有东财池给得出）。
 *
 * 并表之后新增的那三列（首板样本带来的）：样本日收盘、样本日涨幅、开板次数。
 * 「现价（实时）」与「样本日收盘」是两天两个价，列名就把它说死，不并成一格。
 */
const TIER_KEY = "yuanshan-first-board-tier-up";
const DEFAULT_TIERS = ["2", "3", "4p"];
const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const yi = (v: number | null | undefined) => (v == null ? "—" : `${fmt(v / 1e8)} 亿`);
const pctText = (v: number | null | undefined) =>
  typeof v === "number" ? `${v > 0 ? "+" : ""}${fmt(v)}%` : "—";
const breadthTone = (v: number | null | undefined) =>
  v == null ? "text-muted-foreground/50" : v >= 1 ? "text-danger" : v <= -1 ? "text-info" : "text-foreground";
const dash = (v: number | null | undefined) =>
  v == null ? <span className="text-muted-foreground/50">—</span> : String(v);

export function LimitUpBoard({ day }: { day: string | null }) {
  const [data, setData] = useState<ShortTermEmotion | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tiers, setTiers] = useState<string[]>(() => {
    try {
      const s = sessionStorage.getItem(TIER_KEY);
      return s ? s.split(",").filter(Boolean) : DEFAULT_TIERS;
    } catch { return DEFAULT_TIERS; }
  });
  const pickTier = (k: string) => setTiers(prev => {
    const next = prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k];
    try { sessionStorage.setItem(TIER_KEY, next.join(",")); } catch { /* 存不住只影响下次记忆的勾选 */ }
    return next;
  });

  // 一档没勾就不发请求：空 `tiers` 在接口那边是"用默认那批"，屏幕上会读出另一批票
  const tierKeys = data?.tier_keys ?? [];
  const query = useMemo(() => tiersParam(data?.tier_keys ?? tiers, tiers), [data?.tier_keys, tiers]);
  useEffect(() => {
    if (!tiers.length) { setData(d => d); setLoaded(true); return; }
    setLoaded(false);
    api.limitMove(day ?? undefined, query).then(d => { setData(d); setError(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "涨停分析读取失败，请刷新重试"))
      .finally(() => setLoaded(true));
  }, [day, query, tiers.length]);

  const rows = data?.lianban_stocks ?? [];
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  useEffect(() => {
    const codes = rows.map(r => r.code);
    if (!codes.length) { setQuotes({}); return; }
    api.quote(codes.join(",")).then(setQuotes).catch(() => {});
  }, [data?.date, rows.length]);                        // eslint-disable-line react-hooks/exhaustive-deps

  const sort = useTableSort({ key: "boards", dir: "desc" });
  const [page, setPage] = useState(1);
  const sorted = sort.apply(rows);
  const size = PAGE_SIZES[0];
  const pages = pageCount(sorted.length, size);
  const cur = clampPage(page, pages);
  const paged = slicePage(sorted, cur, size);
  const sortBy = (k: string) => { setPage(1); fb.clearAll(); sort.toggle(k); };
  const fb = useStockRowInteraction("涨停分析");

  // 话术两套并存：这一张表里既有首板（勾了首板档才会有）也有连板，两种票的问法本来不同，
  // 系统配置里也是两个类型。所以按行的连板数各取各的模板，不是一套话术糊两批票。
  const dd = useDeepDive("lianban", data?.date || "");
  const lianbanDive = useTemplatePicker("lianban");
  const firstDive = useTemplatePicker("firstboard");
  const linkTpl = useLinkTemplates();
  const [copied, setCopied] = useState("");
  const scopeOf = (s: LianbanStock) => (s.boards >= 2 ? lianbanDive : firstDive);
  const values = (s: LianbanStock): PromptValues => {
    const q = quotes[s.code];
    return {
      date: data?.date || "", name: s.name, code: s.code,
      em_line: emUrl(s.code, linkTpl) ? `东财行情页（公开榜单，可核对分笔与封单）：${emUrl(s.code, linkTpl)}\n` : "",
      close: s.price, pct: s.pct, boards: s.boards,
      quote_line: finite(q?.change_pct) !== null
        ? `另附行情：${q.price} 元（${q.change_pct > 0 ? "+" : ""}${q.change_pct}%），报价时点 ${q.quote_time || "未提供"}；按报价自身日期解释，不冒充研究日收盘或今日盘中值。`
        : "",
      amount: yi(s.amount), float_cap: yi(s.float_cap),
      industry: s.industry || "未知", reason: s.reason || "尚未核实",
    };
  };
  const promptOf = (s: LianbanStock) => {
    const t = scopeOf(s).current;
    return t ? renderPrompt(t.template, values(s)) : null;
  };
  const item = (s: LianbanStock): DiveItem => ({
    key: s.code, prompt: promptOf(s) ?? "",
    context: `连板股 ${s.name}(${s.code}) ${s.boards}连板 深入分析`,
  });
  const copyPrompt = async (s: LianbanStock) => {
    const text = promptOf(s);
    if (!text) return;
    try { await navigator.clipboard.writeText(text); setCopied(s.code); setTimeout(() => setCopied(""), 1600); }
    catch { setCopied(`fail:${s.code}`); setTimeout(() => setCopied(""), 2400); }
  };
  const noTemplate = lianbanDive.error || firstDive.error;

  return (
    <GlassCard docTitle={`涨停分析 · ${data?.date ?? "场次待确认"}`}>
      {/* 说明按钮在标题这一行的最右边（用户 10-31）：原先这颗长在一半正文里（表下面那只折框），
          标题行另有一颗「口径」悬停芯片，两个出口都不像"这一块的说明"。现在并成一颗，
          母体、比率算法与「怎么点」全部进同一口抽屉；档位多选、话术入口、`caliber_note` 告警仍留在屏上。 */}
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <Flame className="h-4 w-4" /> 涨停分析 · {data?.date || "资料日待确认"}
        </h3>
        <span className="ml-auto flex flex-wrap items-center gap-1">
          <DocDrawer title={`涨停分析 · ${data?.date ?? "场次待确认"}`}>
            <p className="whitespace-pre-line leading-relaxed">{plain(
              "母体＝本地库 analytics.limit_up_pool_daily（加工段每日落库），不每次请求现抓东财池。\n"
              + (data?.caliber || "") + "\n"
              + "封板率 / 炸板率：分母是本场摸过涨停的家数（收盘封住 + 收盘没封住），按家数不按开板次数。\n"
              + (data?.seal_rule || ""))}</p>
            <DrawerSection heading="这一张表怎么点 · 并表之后哪几列是新来的">
              <p className="whitespace-pre-line">{`${STOCK_ROW_HINT}\n「样本日收盘／样本日涨幅／开板次数」这三列是原「首板样本」那张表并过来的；`
                + "「现价（实时）」「今日涨跌（实时）」是另一路实时行情，说的是今天，与样本日那两列不是同一天。`"
                + (data?.coverage ? `\n库里的覆盖：${data.coverage.from_day ?? "—"} 到 ${data.coverage.to_day ?? "—"}，共 ${data.coverage.sessions ?? "—"} 场。` : "")}</p>
            </DrawerSection>
          </DocDrawer>
        </span>
        <span className="text-[11px] text-muted-foreground/50">本地库判据（含北交所与 ST）· 客观公开榜单，非推荐 / 非预测</span>
        <span className="ml-auto inline-flex flex-wrap items-center gap-1">
          <TemplatePicker templates={lianbanDive.templates} currentId={lianbanDive.currentId}
            onPick={lianbanDive.choose} scopeLabel="连板股（二板及以上）" />
          <TemplatePicker templates={firstDive.templates} currentId={firstDive.currentId}
            onPick={firstDive.choose} scopeLabel="首板股" />
          <RunAllButton dd={dd} items={paged.map(item)} nameOf={(k) => rows.find(s => s.code === k)?.name || k} />
        </span>
      </div>

      {noTemplate && <p className="mb-1 text-[11px] text-danger">话术模板没取到：{noTemplate}（去系统配置 → AI 提示词看这一档的生效来源）</p>}
      {/* 日期口径（库里最近一场 ≠ 日历最近交易日时才有话）与非默认勾选的那句声明，一起走公用告警条 */}
      {data?.caliber_note && <NoticeBar kind="warning">{data.caliber_note}</NoticeBar>}

      {/* 上面那一排比率说的是**全场**；下面这张表按勾选筛过 —— 两句都要在屏上，否则读者会拿筛后的行数当涨停家数 */}
      {data?.available && (
        <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <span>本场封住 <b className="font-mono text-foreground">{data.zt_count}</b> 家</span>
          <span>炸板 <b className="font-mono text-foreground">{data.zb_count}</b> 家</span>
          <span>封板率 <b className="font-mono text-foreground">{data.seal_rate == null ? "—" : `${(data.seal_rate * 100).toFixed(1)}%`}</b></span>
          <span>最高 <b className="font-mono text-foreground">{data.max_boards}</b> 连板</span>
          <span>2 板及以上 <b className="font-mono text-foreground">{data.lianban_count}</b> 家</span>
          <span>晋级率（昨封今又封 ÷ 上一场封住）
            <b className="font-mono text-foreground">{data.promotion_rate == null ? "—" : `${(data.promotion_rate * 100).toFixed(1)}%`}</b>
            {data.prev_date ? `／上一场 ${data.prev_date} 封住 ${data.prev_sealed ?? "—"} 家` : ""}</span>
          <span>这张表 <b className="font-mono text-foreground">{data.lianban_shown ?? rows.length}</b> 行（勾选只筛这张表）</span>
        </div>
      )}

      {/* 档位的键序与中文名都来自后端那一份（`market.TIER_KEYS` / `TIER_LABELS`）：前端不排第二次序、
          不抄第二份中文（抄了就会长成"两屏档位不同序、不同名"）。单子没取到时不摆空按钮。 */}
      <TierPicker keys={tierKeys} labels={data?.tier_labels ?? {}} counts={data?.tier_counts}
        total={data?.tier_parent_total} selected={tiers} onToggle={pickTier} side="up" />
      {!tierKeys.length && loaded && <p className="mb-1.5 text-[11px] text-muted-foreground">档位单子还没取到（接口没回 `tier_keys`），所以这一排按钮先不摆 —— 不是「今天没有档位」。</p>}

      {!loaded ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 涨停分析读取中…
        </div>
      ) : error ? (
        <p role="alert" className="py-8 text-center text-sm text-danger">涨停分析读取失败：{error}。请刷新页面重试。</p>
      ) : !data ? (
        <p className="py-6 text-sm text-muted-foreground">还没取到数（没有勾档位时不发请求）。</p>
      ) : data.available === false ? (
        /* 库里没有这一场的行 ≠ 今天没有涨停。这句"该跑哪一步"必须念出来 */
        <NoticeBar kind="warning">{data.reason || "这一块读不到数"}</NoticeBar>
      ) : rows.length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">
          {tiers.length ? `勾的这几档本场没有票（本场共封住 ${data.zt_count} 家）—— 换一档看看，这不是"今天没有涨停"。`
            : "没有勾任何一档，这张表没有母体。"}
        </p>
      ) : (<>
        <p className="mb-1 text-[11px] text-muted-foreground">
          名单＝本场收盘仍封在涨停档的那批票（勾了首板档才会含首板）· 客观公开榜单，非推荐 / 非预测 ·
          默认序：连板由高到低，同连板按首封先后（早→晚），首封缺数排本档最后
        </p>
        <NodeWarn text={data.node_warn} />
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                <SortHeader label="名称" sortKey="name" dir={sort.dirOf("name")} onSort={sortBy} className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="连板" sortKey="boards" dir={sort.dirOf("boards")} onSort={sortBy} className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="几天几板" sortKey="board_ct" dir={sort.dirOf("board_ct")} onSort={sortBy}
                  title={`${data.days_boards_rule || "这一列的口径文字由后端交：回溯若干个交易日、从今天往前数这一轮、连续几天没封住才算断。"}\n与「连板」的区别：那一列只数紧挨着的场次，断一天再封就回到 1；这一列仍算在同一轮里，所以会出现「5天4板」而连板只有 1。缺数显示 —（数不出来，不是 0 板）。${data.days_boards_warn ? `\n注意：${data.days_boards_warn}` : ""}`}
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="首板日大盘情绪" sortKey="first_board_breadth_score" dir={sort.dirOf("first_board_breadth_score")} onSort={sortBy}
                  title={`${data.first_board_rule || "这一列的口径文字由后端交：首板日取这一轮最早的封板日，档位取 analytics.market_sentiment_daily。"}${data.first_board_warn ? `\n注意：${data.first_board_warn}` : ""}`}
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="节点" sortKey="node_hits" dir={sort.dirOf("node_hits")} onSort={sortBy}
                  title={`${data.node_rule || "这一列的判据原文后端没交（node_rule 为空），页面不自己复述规则。"}${data.node_warn ? `\n注意：${data.node_warn}` : ""}`}
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="首封" sortKey="seal_time" dir={sort.dirOf("seal_time")} onSort={sortBy}
                  title={`${data.seal_rule || "这一列的算法文字后端没交（seal_rule 为空），页面不自己复述口径。"}\n标了〈源〉的那一行：这一场本地分钟线没读到，取的是同花顺池的秒级首封时刻。`}
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="开板次数（样本日）" sortKey="open_times" dir={sort.dirOf("open_times")} onSort={sortBy}
                  title="两把尺一起给：格子左半边是本地分钟线自己数的开板次数（右边那一列强度分的输入之一），右括号里是同花顺池记的那一份。两边算法不同（分钟收盘 vs 秒级撮合），差一次很常见，不当成其中一个算错。都没有 ⇒ —（没读到，不是 0 次）"
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="封板强度" sortKey="seal_score" dir={sort.dirOf("seal_score")} onSort={sortBy}
                  title={data.seal_spec || "后端没交算法说明（这一列按强度排还是能点的；口径要看说明请刷新）"}
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="样本日收盘" sortKey="price" dir={sort.dirOf("price")} onSort={sortBy}
                  title="所选那一场的收盘价（本地日线 market_data.stock_daily）—— 不是今天的价，今天的价在它右边那两列"
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="样本日涨幅" sortKey="pct" dir={sort.dirOf("pct")} onSort={sortBy}
                  title="同一场那一天的涨跌幅。这一列都是正的（收盘封住涨停），要看今天怎么样看右边那两列实时"
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <th className="whitespace-nowrap px-2 py-2 font-medium" title="这两列的值来自另一路实时行情（不在榜行里），给排序就会拿样本日收盘价冒充实时值，所以不给点">现价（实时）</th>
                <th className="whitespace-nowrap px-2 py-2 font-medium">今日涨跌（实时）</th>
                <SortHeader label="样本日成交额" sortKey="amount" dir={sort.dirOf("amount")} onSort={sortBy} title="样本日（不是今天）的成交额，来自本地日线 market_data.stock_daily" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="流通市值" sortKey="float_cap" dir={sort.dirOf("float_cap")} onSort={sortBy} title="优先取同花顺池原值；池里没有的票（北交所、ST 居多）用当日日线反推（100 × 成交额 ÷ 换手率）。两把尺都在这张库里，悬停可看用的是哪一种" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="涨停原因" sortKey="reason" dir={sort.dirOf("reason")} onSort={sortBy} title="同花顺涨停揭秘池的编辑归因（非公司确认）。池只含沪深主板/创业板/科创板、基本不含 ST ⇒ 空着多半是「源站不收这只票」，不是「本机没采到」。源站保留窗约一年，过期就补不回来" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="概念" sortKey="industry" dir={sort.dirOf("industry")} onSort={sortBy} title="本地 reference_data.sector_member（板块类型=概念，按名字去重，成员数少的在前＝窄题材优先），列前 3 个并给总数；显示 — 是库里没有这一档，不等于它不属于任何题材（北交所基本无概念行）" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <th className="whitespace-nowrap px-2 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {paged.map((s, i) => (
                <Fragment key={s.code}>
                  <tr {...fb.rowProps(i, s.code, "border-b border-border/30")}>
                    <td {...fb.cellProps(i, "name", "px-2 py-2")}>
                      <StockLink code={s.code} name={s.name} className="font-medium hover:text-primary" />{" "}
                      <StockLink code={s.code} className="text-xs text-muted-foreground/50 underline decoration-dotted underline-offset-2 hover:text-primary">{s.code}</StockLink>
                      {s.board_kind && <span className="ml-1 text-[10px] text-muted-foreground/60">{s.board_kind}</span>}
                    </td>
                    <td {...fb.cellProps(i, "boards", "whitespace-nowrap px-2 py-2 font-mono font-bold text-primary")}>{s.boards} 板</td>
                    <td {...fb.cellProps(i, "board_ct", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}
                      title={s.days_boards
                        ? `这一轮的封板日：${(s.board_dates ?? []).map(x => String(x).slice(5)).join("、") || "—"}`
                          + `${s.src_high_days ? `　｜　源站那份写的是「${s.src_high_days}」（两把尺都留着，本地这份不改它）` : ""}`
                        : "本地日线没数出这一轮（多半是 stock_daily 缺当天那行）—— 数不出 ≠ 没有板"}>
                      {s.days_boards || <span className="text-muted-foreground/50">—</span>}
                    </td>
                    <td {...fb.cellProps(i, "first_board_breadth_score", "whitespace-nowrap px-2 py-2 text-xs")} title={s.first_board_note || ""}>
                      {s.first_board_breadth ? (
                        <span className="inline-flex items-baseline gap-1">
                          <span className="font-mono text-[10px] text-muted-foreground/50">{String(s.first_board_day ?? "").slice(5)}</span>
                          <span className={cn("font-medium", breadthTone(s.first_board_breadth_score))}>{s.first_board_breadth}</span>
                          {typeof s.first_board_breadth_score === "number" && (
                            <span className="font-mono text-[10px] text-muted-foreground/60">
                              {s.first_board_breadth_score > 0 ? "+" : ""}{s.first_board_breadth_score.toFixed(1)}
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-muted-foreground/50"
                          title={s.first_board_day
                            ? `首板日 ${s.first_board_day} 在 analytics.market_sentiment_daily 里没有行 —— 不是「那天没情绪」，是这条记录缺`
                            : "这一轮的封板日本地没数出来（原因看「几天几板」那一列），所以首板日无从定位"}>—</span>
                      )}
                    </td>
                    <td {...fb.cellProps(i, "node_hits", "whitespace-nowrap px-2 py-2 text-center")}><NodeCell row={s} rule={data.node_rule} /></td>
                    <td {...fb.cellProps(i, "seal_time", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}
                      title={s.seal_basis === "source"
                        ? `这一场本地分钟线没读到，这一格取的是同花顺池的秒级首封 ${s.src_seal_time ?? "—"}（列头悬停有两条尺的差别）`
                        : s.seal_basis === "none"
                          ? "分钟线里没有任何一根收盘封在档位上，源站那一份也没有 ⇒ 判不了，不是 00:00、也不是没封住"
                          : `本地 1 分钟线自算${s.src_seal_time ? `（源站写的是 ${s.src_seal_time}，差一分钟以内属分钟收盘与秒级撮合的固有差）` : ""}${typeof s.at_limit_minutes === "number" ? `｜封在档位上 ${s.at_limit_minutes} 分钟／共 ${s.minute_bars ?? 0} 根` : ""}`}>
                      {s.seal_time || "—"}{s.seal_basis === "source" && <span className="ml-0.5 text-[10px] text-warning/80">源</span>}
                    </td>
                    <td {...fb.cellProps(i, "open_times", "whitespace-nowrap px-2 py-2 font-mono")}
                      title={`本地分钟线自算 ${s.open_times ?? "—"} 次${typeof s.src_reopen === "number" ? `｜同花顺池记的 ${s.src_reopen} 次` : "｜同花顺池那份没给"}${s.at_limit_minutes != null ? `｜封在档位上 ${s.at_limit_minutes} 分钟` : ""}`}>
                      {s.open_times == null && s.src_reopen == null
                        ? <span className="text-muted-foreground/50">—</span>
                        : <>{s.open_times ?? "—"}{typeof s.src_reopen === "number" && <span className="text-[10px] text-muted-foreground/60">（源 {s.src_reopen}）</span>}</>}
                    </td>
                    <td {...fb.cellProps(i, "seal_score", "whitespace-nowrap px-2 py-2 font-mono")} title={s.seal_note || ""}>
                      {typeof s.seal_score === "number" ? (
                        <span className={cn("font-bold",
                          s.seal_score >= 70 ? "text-danger" : s.seal_score >= 40 ? "text-foreground" : "text-muted-foreground/60")}>
                          {s.seal_score}
                        </span>
                      ) : <span className="text-muted-foreground/50" title="封单、开板次数、首封时间三项都没给：算不出强度，不当成 0 分">—</span>}
                    </td>
                    <td {...fb.cellProps(i, "price", "whitespace-nowrap px-2 py-2 font-mono")}>{dash(s.price)}</td>
                    <td {...fb.cellProps(i, "pct", "whitespace-nowrap px-2 py-2 font-mono text-danger")}>{pctText(s.pct)}</td>
                    <td {...fb.cellProps(i, "price_rt", "whitespace-nowrap px-2 py-2 font-mono")}>
                      {quotes[s.code]?.price ?? <span className="text-muted-foreground/50" title="实时行情未取到，这一格充不上样本日收盘价（那是另一天的数）">—</span>}
                    </td>
                    <td {...fb.cellProps(i, "pct_rt", cn("whitespace-nowrap px-2 py-2 font-mono",
                        pctColor(finite(quotes[s.code]?.change_pct))))}>
                      {pctText(finite(quotes[s.code]?.change_pct) as number | null)}
                    </td>
                    <td {...fb.cellProps(i, "amount", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}>{yi(s.amount)}</td>
                    <td {...fb.cellProps(i, "float_cap", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}
                      title={s.float_cap_basis === "daily_replay"
                        ? "这只票不在同花顺涨停池里（北交所、ST 居多），流通市值是当日日线反推的（100 × 成交额 ÷ 换手率）"
                        : "同花顺涨停池原值 currency_value"}>{yi(s.float_cap)}</td>
                    <td {...fb.cellProps(i, "reason", "max-w-56 px-2 py-2 text-xs")}>
                      {s.reason ? <span className="text-foreground">{s.reason}</span> : <span className="text-muted-foreground/50">—</span>}
                    </td>
                    <td {...fb.cellProps(i, "industry", "whitespace-nowrap px-2 py-2 text-xs text-muted-foreground")}>
                      {s.industry || <span className="text-muted-foreground/50">—</span>}
                      {typeof s.concept_count === "number" && <span className="ml-1 text-[10px] text-muted-foreground/60">共 {s.concept_count} 个</span>}
                    </td>
                    <td {...fb.cellProps(i, "actions", "whitespace-nowrap px-2 py-2 text-right")}>
                      <div className="inline-flex items-center gap-1">
                        <button onClick={() => void copyPrompt(s)} disabled={!scopeOf(s).current}
                          title={copied === s.code ? "已复制到剪贴板"
                            : copied === `fail:${s.code}` ? "浏览器拒绝了剪贴板访问，请在页面里手动复制"
                              : `这一行按${s.boards >= 2 ? "连板股" : "首板股"}那一套话术渲染后复制到剪贴板`}
                          className="inline-flex items-center gap-1 rounded-lg border border-border/60 px-1.5 py-1 text-[11px] text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-40">
                          {copied === s.code ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                          {copied === s.code ? "已复制" : copied === `fail:${s.code}` ? "复制失败" : "复制提示词"}
                        </button>
                        <button onClick={() => dd.toggle(item(s))}
                          className="inline-flex items-center gap-1 rounded-lg border border-primary/50 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20">
                          {dd.running === s.code ? <Loader2 className="h-3 w-3 animate-spin" /> : dd.open === s.code ? <X className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                          {dd.open === s.code ? "收起" : dd.analysis[s.code] ? "展开" : "深入分析"}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {dd.open === s.code && (
                    <DeepDivePanel dd={dd} stockKey={s.code} colSpan={17}
                      noteTitle={`连板深析 · ${s.name} ${s.boards}板`} onRerun={() => dd.rerun(item(s))} />
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-muted-foreground">{pageSentence(sorted.length, size, cur)}</span>
          {canStep(cur, size, sorted.length, -1) && (
            <button type="button" onClick={() => setPage(cur - 1)} className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>)}
          {canStep(cur, size, sorted.length, 1) && (
            <button type="button" onClick={() => setPage(cur + 1)} className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>)}
        </div>
        {fb.popHint && <NoticeBar kind="warning">{fb.popHint}</NoticeBar>}
      </>)}
    </GlassCard>
  );
}
