import { useEffect, useState } from "react";
import { Loader2, TrendingDown } from "lucide-react";
import { api, type LimitDownData, type Quote } from "@/lib/api";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { GlassCard } from "@/components/ui/GlassCard";
import { NoticeBar } from "@/components/ui/NoticeBar";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { TierPicker, tiersParam } from "@/components/ui/TierPicker";
import { PAGE_SIZES, canStep, clampPage, pageCount, pageSentence, slicePage } from "@/lib/paging";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { pctColor } from "@/lib/colors";
import { finite, plain } from "@/lib/agent";
import { useTableSort } from "@/lib/useSort";
import { cn } from "@/lib/utils";

/**
 * 「跌停分析」（用户 2026-10-28 点名：格式跟涨停那一块一样，同属「短线情绪」这一大块）。
 *
 * 母体＝本地日线 `market_data.stock_daily`（判据只住 `duanxian/limit_down_board.py`），
 * **与涨停那张库表（lup-1）无关**：跌停侧本机没有封单额、首封时刻、涨停原因那三族读数
 * （同花顺揭秘池只交涨停、东财跌停池没采），所以那一族**整列不出现** ——
 * 不是摆 `—` 占位：占位会被读成"今天没有"，而事实是"这个读数本机不存在"。
 *
 * 这一屏没有一个 AI 入口：话术库里没有「跌停股」这一型，硬借首板／连板那两套话术，
 * 交出去的 prompt 会把跌停股当涨停股问。要加就先在系统配置里加一型（登记点在后端）。
 */
const TIER_KEY = "yuanshan-first-board-tier-down";
const DEFAULT_TIERS = ["2", "3", "4p"];
const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const yi = (v: number | null | undefined) => (v == null ? "—" : `${fmt(v / 1e8)} 亿`);
const pctText = (v: number | null | undefined) =>
  typeof v === "number" ? `${v > 0 ? "+" : ""}${fmt(v)}%` : "—";
const rate = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

export function LimitDownBoard({ day }: { day: string | null }) {
  const [data, setData] = useState<LimitDownData | null>(null);
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
  const query = tiersParam(data?.tier_keys ?? tiers, tiers);
  useEffect(() => {
    if (!tiers.length) { setLoaded(true); return; }
    setLoaded(false);
    api.limitDown(day ?? undefined, query).then(d => { setData(d); setError(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "跌停分析读取失败，请刷新重试"))
      .finally(() => setLoaded(true));
  }, [day, query, tiers.length]);

  const rows = data?.rows ?? [];
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  useEffect(() => {
    const codes = rows.map(r => r.code);
    if (!codes.length) { setQuotes({}); return; }
    api.quote(codes.join(",")).then(setQuotes).catch(() => {});
  }, [data?.date, rows.length]);                        // eslint-disable-line react-hooks/exhaustive-deps

  const sort = useTableSort({ key: "streak", dir: "desc" });
  const [page, setPage] = useState(1);
  const fb = useStockRowInteraction("跌停分析");
  const sortBy = (k: string) => { setPage(1); fb.clearAll(); sort.toggle(k); };
  const sorted = sort.apply(rows);
  const size = PAGE_SIZES[0];
  const pages = pageCount(sorted.length, size);
  const cur = clampPage(page, pages);
  const paged = slicePage(sorted, cur, size);
  const tierKeys = data?.tier_keys ?? [];

  return (
    <GlassCard docTitle={`跌停分析 · ${data?.date ?? "场次待确认"}`}>
      {/* 说明按钮在标题这一行最右边（用户 10-31）：母体口径与「为什么比涨停那一块少三列」
          并进同一口抽屉；`caliber_note`、档位缺数那句与弹窗被拦的告警仍然留在屏上。 */}
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <TrendingDown className="h-4 w-4" /> 跌停分析 · {data?.date || "资料日待确认"}
        </h3>
        <span className="text-[11px] text-muted-foreground/50">本地日线判据（含北交所与 ST）· 客观公开榜单，非推荐 / 非预测</span>
        <span className="ml-auto"><DocDrawer title={`跌停分析 · ${data?.date ?? "场次待确认"}`}>
          <p className="whitespace-pre-line leading-relaxed">{plain(
            "母体＝本地日线 market_data.stock_daily（跌停档位取那一张表的 limit_down/limit_basis）。\n"
            + (data?.caliber || "") + "\n触及＝当日最低价踩到档位；打开＝触及而收盘没封住，与涨停那侧的「炸板」是同一形状。\n"
            + "这一屏没有封单额／首封时刻／原因那三列：本机跌停侧没有这三个读数，整列不出现（不是摆 — 占位）。")}</p>
          {data && <DrawerSection heading="这一张表怎么点 · 为什么比涨停那一块少三列">
            <p className="whitespace-pre-line">{`${STOCK_ROW_HINT}\n少的三列是封单额、首次封板时刻、涨停原因（编辑归因）：本机跌停侧没有这三个读数 —— `
              + "同花顺揭秘池只交涨停，东财跌停池没采。所以那三列整列不出现，不是摆 — 占位（占位会被读成「今天没有」）。\n"
              + (data.coverage ? `日线覆盖：${data.coverage.first_day ?? "—"} 到 ${data.coverage.last_day ?? "—"}，共 ${data.coverage.sessions ?? "—"} 场。` : "")}</p>
          </DrawerSection>}
        </DocDrawer></span>
      </div>

      {data?.caliber_note && <NoticeBar kind="warning">{data.caliber_note}</NoticeBar>}

      {/* 上面这一排说的是**全场**（不跟着勾选走）；下面那张表按档位筛过 */}
      {data?.available && (
        <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <span>本场封住跌停 <b className="font-mono text-foreground">{data.dt_count}</b> 只</span>
          <span>触及 <b className="font-mono text-foreground">{data.touched_count}</b> 只</span>
          <span>盘中打开 <b className="font-mono text-foreground">{data.opened_count}</b> 只（{rate(data.open_rate)}）</span>
          <span>封住率 <b className="font-mono text-foreground">{rate(data.seal_rate)}</b></span>
          <span>连续 2 场及以上 <b className="font-mono text-foreground">{data.streak_ge2_count}</b> 只</span>
          <span>续跌率（今连昨 ÷ 上一场封住）
            <b className="font-mono text-foreground">{rate(data.continuation_rate)}</b>
            {data.prev_date ? `／上一场 ${data.prev_date} 封住 ${data.prev_sealed ?? "—"} 只` : ""}</span>
          <span>这张表 <b className="font-mono text-foreground">{data.rows_shown ?? rows.length}</b> 行（勾选只筛这张表）</span>
        </div>
      )}

      <TierPicker keys={tierKeys} labels={data?.tier_labels ?? {}} counts={data?.tier_counts}
        total={data?.tier_parent_total} selected={tiers} onToggle={pickTier} side="down" />
      {!tierKeys.length && loaded && (
        <p className="mb-1.5 text-[11px] text-muted-foreground">档位单子还没取到（接口没回 `tier_keys`），所以这一排按钮先不摆 —— 不是「今天没有档位」。</p>
      )}

      {!loaded ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 跌停分析读取中…
        </div>
      ) : error ? (
        <p role="alert" className="py-8 text-center text-sm text-danger">跌停分析读取失败：{error}。请刷新页面重试。</p>
      ) : !data ? (
        <p className="py-6 text-sm text-muted-foreground">还没取到数（没有勾档位时不发请求）。</p>
      ) : data.available === false ? (
        /* 库里没有那一天的日线 ≠ 那天没有跌停。这句"该跑哪一步"必须念出来 */
        <NoticeBar kind="warning">{data.reason || "这一块读不到数"}</NoticeBar>
      ) : rows.length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">
          {tiers.length ? `勾的这几档本场没有票（本场共封住跌停 ${data.dt_count ?? "—"} 只）—— 换一档看看，这不是「今天没有跌停」。`
            : "没有勾任何一档，这张表没有母体。"}
        </p>
      ) : (<>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                <SortHeader label="名称" sortKey="name" dir={sort.dirOf("name")} onSort={sortBy} className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="连续跌停" sortKey="streak" dir={sort.dirOf("streak")} onSort={sortBy}
                  title={data.streak_rule || "这一列的口径文字后端没交，页面不自己复述规则"}
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="几天几跌停" sortKey="board_ct" dir={sort.dirOf("board_ct")} onSort={sortBy}
                  title={`${data.days_boards_rule || "这一列的口径文字由后端交。"}\n与「连续跌停」的区别：那一列只数紧挨着的场次，中间打开一场就回到 1；这一列允许中间空一场仍算同一轮。缺数显示 —（数不出来，不是 0）。${data.days_boards_warn ? `\n注意：${data.days_boards_warn}` : ""}`}
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="当日跌幅" sortKey="pct" dir={sort.dirOf("pct")} onSort={sortBy}
                  title="样本日那一天的涨跌幅（本地日线 pct_change）。这一列都是负的（收盘封住跌停），要看今天怎么样看右边那两列实时"
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="样本日收盘" sortKey="close" dir={sort.dirOf("close")} onSort={sortBy}
                  title="所选那一场的收盘价 —— 不是今天的价"
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="当日跌停价" sortKey="limit_down" dir={sort.dirOf("limit_down")} onSort={sortBy}
                  title="档位取 stock_daily 的 limit_down（由 limit_basis 那一列的前收盘价 × 板块幅度算好并收口，本屏不重算）。收盘价与它相差半分以内算封住"
                  className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="样本日成交额" sortKey="amount" dir={sort.dirOf("amount")} onSort={sortBy} title="样本日（不是今天）的成交额，来自本地日线 amount_yuan" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="换手率" sortKey="turnover" dir={sort.dirOf("turnover")} onSort={sortBy} title="本地日线 turnover_rate_percent（采集侧给的那一把，本屏不反推）" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="流通市值" sortKey="float_cap" dir={sort.dirOf("float_cap")} onSort={sortBy} title="读 market_data.stock_snapshot 的 float_market_value_yuan（另一条采集链）。那一场的快照里没有这只票 ⇒ 显示 —，不是 0 元" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <SortHeader label="概念" sortKey="industry" dir={sort.dirOf("industry")} onSort={sortBy} title="本地 reference_data.sector_member（板块类型=概念，窄题材在前）；显示 — 是库里没有这一档，不等于它不属于任何题材（北交所基本无概念行）" className="whitespace-nowrap px-2 py-2 text-left text-xs font-medium" />
                <th className="whitespace-nowrap px-2 py-2 font-medium" title="这两列的值来自另一路实时行情（不在榜行里），给排序就会拿样本日收盘价冒充实时值，所以不给点">现价（实时）</th>
                <th className="whitespace-nowrap px-2 py-2 font-medium">今日涨跌（实时）</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((s, i) => (
                <tr key={s.code} {...fb.rowProps(i, s.code, "border-b border-border/30")}>
                  <td {...fb.cellProps(i, "name", "px-2 py-2")}>
                    <StockLink code={s.code} name={s.name} className="font-medium hover:text-primary" />{" "}
                    <StockLink code={s.code} className="text-xs text-muted-foreground/50 underline decoration-dotted underline-offset-2 hover:text-primary">{s.code}</StockLink>
                    <span className="ml-1 text-[10px] text-muted-foreground/60" title="板别与 ST 读 reference_data.security（登记值，不是当天的实时状态）">
                      {s.board ?? "—"}{s.is_st ? " · ST" : ""}
                    </span>
                  </td>
                  <td {...fb.cellProps(i, "streak", "whitespace-nowrap px-2 py-2 font-mono font-bold text-info")}
                    title={`回溯窗口 ${data.window_sessions ?? "—"} 场里它有 ${s.streak_bars ?? "—"} 根日线；这一列按半分容差数紧挨着的场次${typeof s.streak_bars === "number" && s.streak_bars < (data.window_sessions ?? 0) ? "（这根窗口不满，别读成整窗）" : ""}`}>
                    {s.streak == null ? <span className="text-muted-foreground/50">—</span> : `${s.streak} 场`}
                  </td>
                  <td {...fb.cellProps(i, "board_ct", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}
                    title={s.days_boards
                      ? `这一轮的封住日：${(s.board_dates ?? []).map(x => String(x).slice(5)).join("、") || "—"}`
                      : "本地日线没数出这一轮（这一把容差更严）—— 数不出 ≠ 没跌过停"}>
                    {s.days_boards || <span className="text-muted-foreground/50">—</span>}
                  </td>
                  <td {...fb.cellProps(i, "pct", cn("whitespace-nowrap px-2 py-2 font-mono", pctColor(finite(s.pct))))}>{pctText(s.pct)}</td>
                  <td {...fb.cellProps(i, "close", "whitespace-nowrap px-2 py-2 font-mono")}>{s.close ?? "—"}</td>
                  <td {...fb.cellProps(i, "limit_down", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}
                    title={s.limit_basis ? `档位由前收盘 ${s.limit_basis} 算（收口层的结果，本屏不重算）` : undefined}>
                    {s.limit_down ?? "—"}
                  </td>
                  <td {...fb.cellProps(i, "amount", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}>{yi(s.amount)}</td>
                  <td {...fb.cellProps(i, "turnover", "whitespace-nowrap px-2 py-2 font-mono")}>{typeof s.turnover === "number" ? `${fmt(s.turnover)}%` : "—"}</td>
                  <td {...fb.cellProps(i, "float_cap", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}>{yi(s.float_cap)}</td>
                  <td {...fb.cellProps(i, "industry", "whitespace-nowrap px-2 py-2 text-xs text-muted-foreground")}>
                    {s.industry || <span className="text-muted-foreground/50">—</span>}
                    {typeof s.concept_count === "number" && <span className="ml-1 text-[10px] text-muted-foreground/60">共 {s.concept_count} 个</span>}
                  </td>
                  <td {...fb.cellProps(i, "price_rt", "whitespace-nowrap px-2 py-2 font-mono")}>
                    {quotes[s.code]?.price ?? <span className="text-muted-foreground/50" title="实时行情未取到，这一格不拿样本日收盘价顶上（那是另一天的数）">—</span>}
                  </td>
                  <td {...fb.cellProps(i, "pct_rt", cn("whitespace-nowrap px-2 py-2 font-mono", pctColor(finite(quotes[s.code]?.change_pct))))}>
                    {pctText(finite(quotes[s.code]?.change_pct))}
                  </td>
                </tr>
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
