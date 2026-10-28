import { RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaliberFold, Contract, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { plain } from "@/lib/agent";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { SortHeader } from "@/components/ui/SortHeader";
import { cn } from "@/lib/utils";
import { ageText, dash, hasData, num } from "@/lib/datastate";
import { Caliber } from "@/components/ui/Caliber";
import { SectorFlowCumulative } from "@/components/SectorFlowCumulative";
import { SectorLink, SECTOR_LINK_CALIBER } from "@/components/ui/SectorLink";
import { heatBg, pctColor } from "@/lib/colors";
import { useState } from "react";

/**
 * 板块热度与点火（映射表 #2：FT 的 `/sector`）。
 *
 * 两块屏答两个不同的问题，不合并：
 *  1. **钱今天实际去了哪些板块** —— `/api/market/overview`（涨幅 + 主力净流入）；
 *  2. **哪个板块的热度在积累、五要素里哪一项在起作用** —— `/api/quant/sector-heat`。
 * 混在一起会把"钱往哪走"和"哪个算点火"读成同一件事；接口自己的 caveat 也写了
 * `heat` 与 `score` 是**两套判据**，排序会分叉 —— 所以两列并排给，不相加、不折中。
 *
 * ⚠️ 算不出的要素显示「缺」不是 0：0 分会参与排名，"缺"不参与。这是这套判据最容易被读错的地方。
 */

interface SectorRow { name?: string | null; pct?: number | null; net?: number | null; inflow?: number | null; outflow?: number | null; firms?: number | null }
interface Overview { available?: boolean | null; reason?: string | null; sectors?: SectorRow[] | null; updated?: string | null }

interface Element { key?: string | null; column?: string | null; cn?: string | null; weight?: number | null; means?: string | null }
interface HeatRow {
  sector_code?: string | null; name?: string | null; sector_type?: string | null; unnamed?: boolean | null;
  statistical?: boolean | null;
  heat?: number | null; part_count?: number | null; heat_rank?: number | null; universe_count?: number | null;
  /** 板块自己那条 880 指数今天的涨跌幅（`(今收 − 昨收) ÷ 昨收`，不是成分平均）与它的名次、广度 */
  pct?: number | null; pct_rank?: number | null;
  up_count?: number | null; down_count?: number | null; member_count?: number | null;
  stage?: string | null; stage_cn?: string | null; score?: number | null;
  squeeze?: number | null; position_score?: number | null; ignition?: number | null; volume?: number | null;
  breadth?: number | null; breadth_available?: boolean | null; term_count?: number | null;
  heat_pctile?: number | null; slope_21?: number | null; points_21?: number | null;
  /** 趋势原料：后端逐列交出的现成值（斜率来自 `analytics.sector_trend`，标记来自 `ignition.detail`） */
  slope_3?: number | null; points_3?: number | null; slope_5?: number | null; points_5?: number | null;
  slope_11?: number | null; points_11?: number | null;
  slope3?: number | null; turned_up?: boolean | null; ma_cross?: boolean | null; accelerating?: boolean | null;
  vol_ratio?: number | null; vol_pctile?: number | null; up_ratio?: number | null; up_ratio_ma?: number | null;
  share_ratio?: number | null;
  /** 东财板块页那一档的码与真名（对表只住 `reference_data.em_board_link`，读法只住 `duanxian/em_board_link.py`）。
   *  没有码 = 没对上名（或这一档东财根本没有对应族），`components/ui/SectorLink.tsx` 据此退回纯文本 */
  em_board_code?: string | null; em_board_name?: string | null; em_match_kind?: string | null;
}
interface TrendRow {
  sector_code?: string | null; name?: string | null; sector_type?: string | null;
  regime?: string | null; dip_score?: number | null; missing_terms?: number | null; universe?: number | null;
  terms?: Record<string, number | null> | null;
  heat?: number | null; heat_pctile?: number | null;
  slope_short?: number | null; slope_mid?: number | null; slope_long?: number | null;
  points_short?: number | null; points_mid?: number | null; points_long?: number | null;
  reasons?: { why?: string | null; turned_days_ago?: number | null; heat_rank?: number | null;
              dip_score_ceiling?: number | null; position?: number | null; up_ratio?: number | null } | null;
}
interface Trend {
  available?: boolean | null; reason?: string | null; date?: string | null; scope?: string | null;
  window_set?: string | null; spec_version?: string | null;
  spec_versions_in_db?: { spec_version?: string | null; rows?: number | null }[] | null;
  universe?: { sectors?: number | null; rows_returned?: number | null; limit?: number | null;
                 active_total?: number | null; note?: string | null;
                 excluded_types?: { sector_type?: string | null; active?: number | null; rows_today?: number | null }[] | null };
  sort?: string | null; spec_is_effective?: boolean | null; effective_spec_version?: string | null;
  last_seed_session?: string | null; last_seed_count?: number | null;
  regime_distribution_today?: { regime?: string | null; n?: number | null }[] | null;
  regime_meaning?: Record<string, string> | null; rows?: TrendRow[] | null;
  groups?: { snapshot_days?: number | null; sessions_needed?: number | null; ready?: boolean | null;
               live_trustworthy?: boolean | null; provenance_note?: string | null;
               by_date?: { trade_date?: string | null; members?: number | null }[] | null } | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  generated_at?: string | null; source?: string | null;
}

/** 档位的人话名。后端交 `regime_meaning` 原文，这里只给短标签 —— 含义不自己编，hover 看后端那句 */
const REGIME_CN: Record<string, string> = {
  insufficient: '数据不足', no_shape: '没形状', downtrend: '退潮',
  turn_up_seed: '刚转', forming: '形成中', established: '已确立', overheated: '过热',
};
const TERM_KEYS = ['dir_agree', 'persist', 'breadth', 'volume_ok', 'position', 'rank_momentum'] as const;

function regimePill(r?: string | null): string {
  const base = 'rounded px-1.5 py-0.5 text-[10px]';
  if (r === 'turn_up_seed') return `${base} bg-primary/15 text-primary font-semibold`;
  if (r === 'forming') return `${base} bg-primary/12 text-primary`;
  if (r === 'established') return `${base} bg-muted text-foreground`;
  if (r === 'overheated') return `${base} bg-destructive/15 text-destructive`;
  if (r === 'downtrend') return `${base} bg-bg/60 text-muted-foreground`;
  return `${base} bg-muted text-muted-foreground`;
}

interface SectorHeat {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  rows?: HeatRow[] | null; elements?: Element[] | null;
  universe?: { rows_today?: number | null; sectors?: number | null; heat_null?: number | null; unnamed_today?: number | null; statistical_today?: number | null; pct_null_today?: number | null; caliber?: string | null; note?: string | null } | null;
  filter?: { sector_type?: string | null; stage?: string | null; limit?: number | null; rows_returned?: number | null; note?: string | null;
             /** 榜的主序档由后端定并交回：heat=热度合成分，pct=板块自己指数当日涨幅 */
             sort?: string | null; sort_label?: string | null; sort_note?: string | null } | null;
  stage_distribution_today?: { stage?: string | null; n?: number | null }[] | null;
  stage_priority?: string[] | null; stage_meaning?: Record<string, string> | null;
  score_formula?: string | null;
  board_link?: { available?: boolean | null; kinds?: Record<string, { linked?: number | null; total?: number | null }> | null;
                 tried_at?: string | null; reason?: string | null; linked_today?: number | null;
                 basis?: string | null; url?: string | null } | null;
  heat_source_caliber?: { used?: string | null; note?: string | null } | null;
  breadth_reality?: unknown; breadth_reality_note?: string | null;
  trend_windows_available?: { window_days?: number | null; rows_n?: number | null }[] | null;
  trend_windows_returned?: number[] | null;
  freshness?: { stats_mode?: string | null; always_live?: string | null; cached_or_absent?: Record<string, unknown> | null; cache_ttl_seconds?: number | null; note?: string | null } | null;
  definitions?: Record<string, unknown> | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

/** `definitions` 是"表 → 列 → 释义"两层；展平成一行一条，文字一律用后端原文 */
function flattenDefs(map?: Record<string, unknown> | null): Record<string, string> | null {
  const out: Record<string, string> = {};
  for (const [table, cols] of Object.entries(map ?? {})) {
    if (cols && typeof cols === "object" && !Array.isArray(cols)) {
      for (const [col, meaning] of Object.entries(cols as Record<string, unknown>)) {
        if (typeof meaning === "string") out[`${table}.${col}`] = meaning;
      }
    } else if (typeof cols === "string") {
      out[table] = cols;
    }
  }
  return Object.keys(out).length ? out : null;
}

export function SectorHeat() {
  const [nonce, refresh] = useNonce();
  const [stage, setStage] = useState<string | null>(null);
  const [type, setType] = useState<string | null>(null);
  // 市场级统计指数（880001 总市值 / 880005 涨跌家数 / 880011 主板总值…）默认藏起来：
  // 它们的成交额逐日等于全 A，热度排得很前，但它们不是板块。后端只打标不删行
  // （删了母体数就对不上），藏不藏是界面决定 —— 这里默认藏，且随时能点回来。
  const [showStats, setShowStats] = useState(false);
  const [fullStats, setFullStats] = useState(false);
  // 榜的主序有两把尺子（用户 2026-10-15 点名要后者）：热度合成分 / 板块自己那条 880 指数的当日涨幅。
  // 切档是**发一次带 sort 的请求**（后端排好序、名次也算好），不是前端把截断后的 200 行再排一遍。
  const [rank, setRank] = useState<"heat" | "pct">("heat");
  const flow = useProbe<Overview>("/market/overview", nonce);
  // 筛选走后端参数：母体 688 个、接口只交 limit 行，在前端截出来的行里再筛会看着像"没有"
  const [trendRegime, setTrendRegime] = useState("");
  const trend = useProbe<Trend>("/quant/sector-trend", nonce, { limit: 60, ...(trendRegime ? { regime: trendRegime } : {}) });
  const [seedDate, setSeedDate] = useState("");
  const seed = useProbe<Trend>("/quant/sector-trend", nonce,
    { limit: 30, sort: "seed", regime: "turn_up_seed", ...(seedDate ? { date: seedDate } : {}) });
  // 排除档的数来自档位表那一次取数，不另发请求（同一份 payload，避免两次读到不同场次）
  const excludedFromTrend = hasData(trend) ? (trend.data.universe?.excluded_types ?? null) : null;
  const heat = useProbe<SectorHeat>("/quant/sector-heat", nonce, {
    ...(stage ? { stage } : {}), ...(type ? { type } : {}),
    ...(fullStats ? { stats: "full" } : {}), ...(rank === "pct" ? { sort: "pct" } : {}), limit: 200,
  });
  // 点列头排序只重排"已经取回的这一批"，榜的截断仍由后端 limit 管（排序在 slice 之前）
  const flowSort = useTableSort();
  const heatSort = useTableSort();
  const trendSort = useTableSort();
  const seedSort = useTableSort();

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="板块热度与点火" level={1}
        subtitle="两个问题分开答：钱今天去了哪些板块；哪个板块的热度在积累、五要素里哪一项在起作用。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <Panel title="当日板块涨幅与资金净流入（现抓截面）" state={flow}
          note="底色只表示当日涨幅（A 股红涨绿跌），不表示资金流向 —— 净流入要读数字，别读颜色。">
          {d => (
            <>
              <Row label="更新于" value={dash(d.updated)} />
              {/* 这一列不写"主力净流入"：源那边没说明是大单主力还是全部成交资金，
                  首页同一份数据也只写"净流入" —— 两处一个口径。 */}
              <Caliber text={
                "取自同花顺行业资金流的盘中/盘后即时值，单位亿元，净流入 = 流入 − 流出。\n" +
                "⚠️ 那边没说明这是主力资金还是全部成交资金，所以不能当作主力净流入来读。\n" +
                "这是「截面」：此刻现抓、不落库（每天由加工链的 sectorflow 步在收盘后采一次才算历史）。"
              } />
              {(d.sectors ?? []).length === 0
                ? <p className="mt-1 text-[12px] text-muted-foreground">后端交出的板块列表为空（0 个）。这不是读不到。</p>
                : <table className="mt-2 w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="板块" sortKey="name" dir={flowSort.dirOf("name")} onSort={flowSort.toggle} />
                    <SortHeader label="涨幅" sortKey="pct" dir={flowSort.dirOf("pct")} onSort={flowSort.toggle} />
                    <SortHeader label="净流入(亿)" sortKey="net" dir={flowSort.dirOf("net")} onSort={flowSort.toggle}
                      title="同花顺行业榜的「净额」，单位亿；没说明是主力还是全部成交资金，别读成主力净流入" />
                    <SortHeader label="流入 / 流出" sortKey="inflow" dir={flowSort.dirOf("inflow")} onSort={flowSort.toggle} />
                    <SortHeader label="家数" sortKey="firms" dir={flowSort.dirOf("firms")} onSort={flowSort.toggle} />
                  </tr></thead>
                  <tbody>
                    {flowSort.apply(d.sectors ?? []).slice(0, 30).map((s, i) => (
                      <tr key={`${s.name}-${i}`} className="border-t border-border/40">
                        <td className="py-0.5">{dash(s.name)}</td>
                        <td><span className={cn("inline-block min-w-14 rounded px-1 text-center font-mono tabular-nums", heatBg(s.pct), (s.pct == null || (s.pct >= 0 && s.pct < 2)) ? "text-foreground" : "text-white/95")}>{num(s.pct, 2)}%</span></td>
                        <td className={cn("font-mono tabular-nums", pctColor(s.net ?? null))}>{num(s.net, 2)}</td>
                        <td className="font-mono tabular-nums text-muted-foreground">{num(s.inflow, 2)} / {num(s.outflow, 2)}</td>
                        <td className="font-mono tabular-nums">{dash(s.firms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
              {(d.sectors ?? []).length > 30 && (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  {flowSort.sort ? `已按所选列${flowSort.sort.dir === "asc" ? "升序" : "降序"}排，列出前 30 行`
                    : "仅列出前 30 行（后端榜序）"}，共 {d.sectors?.length} 个板块；没点列头时就是后端给的榜序。
                </p>
              )}
              <p className="mt-2 text-[11px] text-muted-foreground">
                单位随源、前端不换算。"领涨换了几手、谁连庄"是另一套判据，在
                <Link className="mx-1 text-primary underline" to="/qd-compare">quantdash 口径对照</Link>，本页不复制一份。
              </p>
            </>
          )}
        </Panel>

        {/* 用户 2026-10-06 点名的那一格：2024 年以来 / 近 3 个月的累计净流入，前 50 + 翻页 */}
        <SectorFlowCumulative />

        <Panel title="热度榜与五要素" state={heat} slowHint="五要素与阶段分布要对上千个板块逐日聚合，实测最慢 14 秒（只查当天这一档最快）。默认不查全库广度，那一档要点按钮才发。"
          note="热度由涨幅/广度/动力/成交四分项合成；点火分由五要素加权。两套判据并排看，不相加。">
          {d => {
            const allRows = d.rows ?? [];
            // 默认藏掉打了 `statistical` 标的行（市值/涨跌家数这类市场级统计指数，不是板块）
            const rows = showStats ? allRows : allRows.filter(r => r.statistical !== true);
            const hiddenStats = allRows.length - rows.length;
            const elements = d.elements ?? [];
            const dist = d.stage_distribution_today ?? [];
            const meaning = d.stage_meaning ?? {};
            const priority = d.stage_priority ?? [];
            return (
              <>
                <Row label="评分日" value={dash(d.date)} />
                <Row label="当日母体" value={`${dash(d.universe?.rows_today)} 个板块（无名 ${dash(d.universe?.unnamed_today)} 个、heat 为空 ${dash(d.universe?.heat_null)} 个、市场级统计指数 ${dash(d.universe?.statistical_today)} 个）`} />
                <Row label="本次交出" value={`${dash(d.filter?.rows_returned)} 行（limit ${dash(d.filter?.limit)}）—— 榜是截断的，母体数看上面那格`} />
                {/* 板块名点不点得开要说得出数：分母是全场（不是这一页），来自后端那格 board_link */}
                <Row label="东财板块页" value={d.board_link?.available
                  ? <>这一榜 {dash(d.board_link?.linked_today)}/{dash(d.filter?.rows_returned)} 个名字能点开；
                      全场 {Object.entries(d.board_link?.kinds ?? {}).map(([k, v]) => `${k} ${dash(v?.linked)}/${dash(v?.total)}`).join("、") || "—"}
                      （对表 {dash(d.board_link?.tried_at)}，差一个级别后缀的那些点开后页眉是东财那个名字）</>
                  : <span className="text-warning">对表读不到：{dash(d.board_link?.reason)} —— 板块名一律退回纯文本，不猜地址</span>} />

                <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px]">
                  <button type="button" onClick={() => setStage(null)} aria-pressed={stage === null}
                    className={cn("rounded px-2 py-0.5", stage === null ? "bg-primary/15 text-primary" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                    全部阶段
                  </button>
                  {priority.map(s => (
                    <button key={s} type="button" onClick={() => setStage(stage === s ? null : s)} aria-pressed={stage === s}
                      className={cn("rounded px-2 py-0.5", stage === s ? "bg-primary/15 text-primary" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                      {meaning[s] ?? s} {dist.find(x => x.stage === s)?.n ?? 0}
                    </button>
                  ))}
                  <span className="mx-1 text-muted-foreground">|</span>
                  {["行业", "概念", "风格"].map(t => (
                    <button key={t} type="button" onClick={() => setType(type === t ? null : t)} aria-pressed={type === t}
                      className={cn("rounded px-2 py-0.5", type === t ? "bg-primary/15 text-primary" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                      {t}
                    </button>
                  ))}
                  <span className="mx-1 text-muted-foreground">|</span>
                  {/* 默认藏 = 这批本来就不该出现在板块榜里；能点回来是为了让"藏掉了什么"可核对 */}
                  <button type="button" onClick={() => setShowStats(v => !v)} aria-pressed={showStats}
                    title="总市值 / 涨跌家数 / 主板总值… 这类是通达信的市场级统计指数（成交额=全市场），不是板块；热度、点火分对它们没有意义"
                    className={cn("rounded px-2 py-0.5", showStats ? "bg-warning/15 text-warning" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                    {showStats ? "显示着市场级统计指数"
                      : `藏掉市场级统计指数（本场 ${dash(d.universe?.statistical_today)} 个，这一屏藏了 ${hiddenStats} 行）`}
                  </button>
                  <span className="mx-1 text-muted-foreground">|</span>
                  <button type="button" onClick={() => setFullStats(v => !v)} aria-pressed={fullStats}
                    className={cn("rounded px-2 py-0.5", fullStats ? "bg-warning/15 text-warning" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                    {fullStats ? "已查全库广度覆盖" : "查全库广度覆盖（慢，2~3 秒）"}
                  </button>
                </div>
                <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                  阶段按后端给的优先级排（{priority.join(" → ")}），排前面的档会截走后面的档。
                  {d.filter?.note ? ` ${d.filter.note}` : ""}{d.universe?.caliber ? ` 母体口径：${d.universe.caliber}` : ""}
                </p>
                {/* 用户 2026-10-21：「描述性内容全部收缩起来，避免过多占屏」—— 算式与五要素权重
                    和「板块名能不能点开」那条对表规矩一起收进同一个框（点开可见，一处都收在同一屏）*/}
                <ExplainBox title="板块名能不能点开：对表规矩 · 差一个后缀 · 为什么有的点不开 · 算式与五要素权重">
                  <p className="text-[11px] leading-relaxed text-muted-foreground">{SECTOR_LINK_CALIBER}</p>
                  {d.score_formula && <p className="mt-1 font-mono text-[11px] leading-relaxed text-muted-foreground">{d.score_formula}</p>}
                  {elements.length > 0 && (
                    <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                      五要素与权重（点开这一框是为了少占屏，权重本身仍是后端交的那份）：
                      {elements.map(e => `${dash(e.cn)} ${num(e.weight, 2)}`).join(" · ")}
                    </p>
                  )}
                </ExplainBox>

                {/* 榜的主序两把尺子（用户 2026-10-15 点名要「按当日涨幅」那一档）。
                    切档是**换一次后端请求**（`sort=pct`），不是把截断后的 200 行再排一遍 ——
                    先 limit 再在前端排，读到的就是"前 200 名里的涨幅榜"，不是全场涨幅榜。 */}
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span className="text-muted-foreground">榜按哪把尺子排：</span>
                  {([["heat", "热度合成分"], ["pct", "板块自己指数当日涨幅"]] as const).map(([k, label]) => (
                    <button key={k} type="button" aria-pressed={rank === k}
                      onClick={() => setRank(k)}
                      className={cn("rounded px-2 py-0.5",
                        rank === k ? "bg-primary/15 text-primary" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                      {label}
                    </button>
                  ))}
                  <span className="text-muted-foreground">
                    {dash(d.filter?.sort_label)}　母体里 {dash(d.universe?.pct_null_today)} 个板块当天没有自己那条 880 日线
                  </span>
                </div>
                {d.filter?.sort_note && (
                  <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{plain(d.filter.sort_note)}</p>
                )}

                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead><tr className="text-left text-muted-foreground">
                      <SortHeader label="板块" sortKey="name" dir={heatSort.dirOf("name")} onSort={heatSort.toggle}
                        title={'这一档的板块来自「通达信体系」（板块码形如 880310 / 881333，行业 643 个 + 概念 268 个 + 风格 154 个，'
                          + "外加 48 个市场级统计指数默认藏掉），不是东财或同花顺的\"二级行业\"。\n"
                          + "下面那张「覆盖以来累计净流入」用的是「同花顺行业」（90 个一级行业），两套体系不互换、"
                          + "也不按名字硬对齐 —— 同一句\"电力\"在两边指的是不同的成分集合。"} />
                      <SortHeader label="热度" sortKey="heat" dir={heatSort.dirOf("heat")} onSort={heatSort.toggle}
                        title={'热度 = 涨幅、广度、动力、成交四个分项合成的一把分（-100~100，当日母体实测均值约 -20）。'
                          + "\n它回答\"今天这个板块热不热\"；点火分回答\"它是不是刚从冷里头被点起来\"。"
                          + "\n⚠️ 两套判据不相加、不互相换算，所以点这两列排序会分叉，这是设计不是 bug。"} />
                      <SortHeader label="涨幅" sortKey="pct" dir={heatSort.dirOf("pct")} onSort={heatSort.toggle}
                        title={'这一列是「板块自己那条 880 指数今天的涨跌幅」=（今收 − 昨收）÷ 昨收，'
                          + "**不是把成分股的涨幅平均出来** —— 两种算法在宽成分的概念榜上能差出一个档。\n"
                          + `左边那个名次（pct_rank）就是这一档的名次，切到「按当日涨幅」榜时它才是主序；`
                          + "点列头只重排已经取回的这一批，换榜要靠上面那个档按钮（截断在前端排不回来）。\n"
                          + "空值 = 这一天这条 880 指数没有日线行（新板块或本机没入库），不是涨 0%。"} />
                      <SortHeader label="涨/跌家数" sortKey="up_count" dir={heatSort.dirOf("up_count")} onSort={heatSort.toggle}
                        title="板块当日上涨家数 / 下跌家数（`sector_daily.up_count / down_count`，来自板块自己那条日线的成分统计）。成分总数见「项数」左边那一档的母体说明。" />
                      <SortHeader label="点火分" sortKey="score" dir={heatSort.dirOf("score")} onSort={heatSort.toggle}
                        title={'点火分 = 右边那五列各打 0~100 分，再按固定权重加权求和：'
                          + "\n  波动状态 15% · 位置 20% · 点火 30% · 量能 20% · 广度 15%"
                          + "\n（权重住在后端 `duanxian/quant.py::ELEMENTS`，与 analytics.ignition 的列注释同源；两边不一致时以列为准）"
                          + "\n\n算不出来的那一项不计权重（分母自动缩），所以分数只在\"同一行有几项\"的前提下可比 —— "
                          + "两项撑到 80 分不等于五项齐的 80 分，请看右边的「项数」。"
                          + "\n\n每一列各行其是：波动状态=热度波动已放大；位置=还在自身 120 日的低位（专治追高）；"
                          + "点火=3 日斜率由负转正且热度 MA5 上穿 MA20；量能=板块成交额对自身 20 日均额 + 占全市场份额的抬升；"
                          + "广度=板块内上涨家数占比及其抬升。"} />
                      <SortHeader label="阶段" sortKey="stage" dir={heatSort.dirOf("stage")} onSort={heatSort.toggle}
                        title="点火 / 延续 / 退潮 / 蓄势 / 无信号：按优先级顺序短路判定，排前面的档会截走后面的档" />
                      {elements.map(el => <SortHeader key={`h-${el.key}`} label={dash(el.cn)} sortKey={String(el.column ?? el.key)}
                        dir={heatSort.dirOf(String(el.column ?? el.key))} onSort={heatSort.toggle}
                        title={`${el.means ?? ""}\n这一列就是上面那一项的 0~100 分，占点火分 ${num((el.weight ?? 0) * 100, 0)}%；空＝这一项今天算不出来（不计权重），不是 0 分。`} />)}
                      <SortHeader label="项数" sortKey="term_count" dir={heatSort.dirOf("term_count")} onSort={heatSort.toggle}
                        className="font-normal whitespace-nowrap"
                        title={'这一行右边那五项里，今天真算得出来几项（满 5 才叫"五项齐全"）。'
                          + "\n\n为什么有人是 4：行业那一档的板块没有涨跌家数可用（实测行业 0/230 有广度，概念与风格基本都有），"
                          + "所以行业的项数最多 4 —— 而「点火」这一档的硬前置是五项齐全，"
                          + "行业因此规则上永远不会被标成点火。那是算不出，不是那天没有轮动。"
                          + "\n同理 880001~880005 这类指数型板块也进不了点火档（它们本来就不是板块，是市场级统计指数）。"} />
                      <SortHeader label="21日斜率" sortKey="slope_21" dir={heatSort.dirOf("slope_21")} onSort={heatSort.toggle}
                        className="font-normal whitespace-nowrap" title="热度 21 日窗口斜率（分/日）" />
                    </tr></thead>
                    <tbody>
                      {heatSort.apply(rows).map((r, i) => (
                        <tr key={`${r.sector_code}-${i}`} className="border-t border-border/40">
                          <td className="py-0.5 whitespace-nowrap">
                            {/* 名次跟着上面选的那一把尺子走：按涨幅榜时挂热度名次，读者会以为榜排错了 */}
                            <span className="mr-1 font-mono text-[9px] text-muted-foreground"
                              title={rank === "pct" ? "这一档按板块自己指数的当日涨幅排名（pct_rank）"
                                : "这一档按热度合成分排名（heat_rank）"}>
                              #{dash(rank === "pct" ? r.pct_rank : r.heat_rank)}
                            </span>
                            {r.unnamed === true
                              ? <span className="text-muted-foreground">（无名 {dash(r.sector_code)}）</span>
                              : <SectorLink name={r.name} code={r.em_board_code} emName={r.em_board_name}
                                  sectorType={r.sector_type}
                                  detail={`板块码 ${dash(r.sector_code)}；当日 heat 分位 ${num(r.heat_pctile, 1)}`} />}
                            <span className="ml-1 text-[9px] text-muted-foreground">{dash(r.sector_type)}</span>
                          </td>
                          <td className="font-mono tabular-nums">{num(r.heat, 1)}</td>
                          <td className={cn("font-mono tabular-nums", (r.pct ?? 0) > 0 ? "text-primary" : (r.pct ?? 0) < 0 ? "text-success" : "")}>
                            {r.pct == null ? <span className="text-muted-foreground/60"
                              title="这一天这条 880 指数在 sector_daily 里没有行（新板块或本机没入库）—— 不是涨 0%">缺</span>
                              : `${r.pct > 0 ? "+" : ""}${num(r.pct, 2)}%`}
                          </td>
                          <td className="font-mono tabular-nums">{dash(r.up_count)}/{dash(r.down_count)}</td>
                          <td className="font-mono tabular-nums">{num(r.score, 1)}</td>
                          <td className="whitespace-nowrap">
                            <span className={cn("rounded px-1 text-[10px]",
                              r.stage === "ignite" ? "bg-primary/15 text-primary" : r.stage === "none" ? "bg-muted text-muted-foreground" : "bg-warning/12 text-warning")}>
                              {dash(r.stage_cn ?? r.stage)}
                            </span>
                          </td>
                          {elements.map(el => {
                            const v = r[(el.column ?? el.key ?? "") as keyof HeatRow] as number | null | undefined;
                            const missing = el.column === "breadth" ? r.breadth_available === false : v == null;
                            return <td key={`${r.sector_code}-${el.key}`} className={cn("font-mono tabular-nums", missing && "text-muted-foreground/60")}>
                              {missing ? "缺" : num(v, 1)}
                            </td>;
                          })}
                          <td className={cn("font-mono tabular-nums", r.term_count != null && r.term_count < elements.length && "text-warning")}>
                            {dash(r.term_count)}/{elements.length}
                          </td>
                          <td className={cn("font-mono tabular-nums", pctColor(r.slope_21 ?? null))}
                            title={`窗口点数 ${dash(r.points_21)}；不足窗口不给斜率，不补 0`}>{num(r.slope_21, 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {rows.length === 0 && (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      这个组合筛出 0 个板块 —— 是筛空的，不是当日没数据（当日母体 {dash(d.universe?.rows_today)} 个）。
                      换一个阶段或板块类型试试；行业板块的点火常年是 0，原因见下方 caveat。
                    </p>
                  )}
                </div>

                {/* 声明／字段含义／覆盖／出处 + 三句后端散文（heat 口径、新鲜度、广度实态）
                    收成一处默认收起（用户 2026-10-21）。上面那段"阶段优先级"是读数与筛选依据、留在外面；
                    下面「缓存键 + 哪些是缓存」那句带 text-warning 的也不折 —— 那是读数的新鲜度声明。 */}
                <CaliberFold caveats={d.caveats} definitions={flattenDefs(d.definitions)}
                  coverage={d.coverage}
                  provenance={[d.source,
                    d.heat_source_caliber?.used ? `heat 口径 ${d.heat_source_caliber.used}` : null,
                    d.freshness?.stats_mode ? `stats=${d.freshness.stats_mode}` : null]}
                  notes={[d.heat_source_caliber?.note, d.freshness?.note,
                    d.breadth_reality == null ? d.breadth_reality_note : null,
                    d.breadth_reality != null ? `全库广度覆盖实测：${JSON.stringify(d.breadth_reality)}` : null]} />
                {(d.freshness?.cache_ttl_seconds != null || d.freshness?.cached_or_absent) && (
                  <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                    缓存：每 {ageText(d.freshness?.cache_ttl_seconds)}才重算一次（<code>stats={dash(d.freshness?.stats_mode)}</code>）；
                    本轮命中的缓存键原样列出 <code>{JSON.stringify(d.freshness?.cached_or_absent ?? {})}</code>
                    {" "}<span className="text-warning">—— 哪些是缓存、哪些是现算，以上面那句后端原文为准，页面不替它解释</span>。
                  </p>
                )}
                {/* 广度实态那两句（没查全库时的说明 / 查了之后的 JSON 实数）已经跟着
                    `<CaliberFold notes=…>` 收上去了，这里不再摊第二份 —— 摊两份就是两处会各说一套。 */}
                {(d.trend_windows_available ?? []).length > 0 && (
                  <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                    趋势窗口：可算 {(d.trend_windows_available ?? []).map(w => w.window_days).join("/")} 日，
                    本次随榜交出 {(d.trend_windows_returned ?? []).join("/")} 日 —— 没交出的斜率不显示，不拿别的窗口顶。
                  </p>
                )}
              </>
            );
          }}
        </Panel>

        {/* ── 新增一块：趋势到底成没成型 · 左侧低吸看的是"刚转那一瞬"。判据先写清楚，数值一律来自上面的接口 ── */}
        <GlassCard>
          <h2 className="text-sm font-bold">趋势形成度与低吸：判据（逻辑就在屏上，不藏在代码里）</h2>
          {/* 用户 2026-10-14：判据的说明文字整摊摊开，把读数挤到第二屏。
              六分项这一叠说明折进通用说明框（默认收起）；紧接后面那段「档位按顺序互斥」
              没跟着折 —— 那一段里写着「点不足就不给判据、显示缺而不是 0」和「过热」的
              误读告警，藏起来等于让屏幕说假话。 */}
          <ExplainBox title="判据六分项：每一项判什么 · 为什么这样判">
          <p>
            两个问题分开答：<b className="text-foreground">① 这个板块的涨幅/热度形成趋势了吗；② 如果做左侧，它是不是刚转、位置低不低</b>。
            合成一个分数就看不出是哪一项在起作用，所以下面六项<b className="text-foreground">分项全列、最后一列才给档位</b>。
            完整推导与建表草案在 <code>docs/页面展示工作指南/05-板块趋势与低吸判据.md</code>。
          </p>
          <ul className="space-y-1">
            {[
              ["方向一致 dir_agree", "短/中/长三窗口（5/11/21 日）最小二乘斜率同号才算成形状。为什么不用「今日减 N 日前」：那只用首尾两点，一根插针就能把趋势判反。"],
              ["持续性 persist", "窗口内热度逐日抬升的天数占比。斜率说方向，持续性说这个方向保持了多久 —— 左侧要的是刚起步，不是第 20 天。"],
              ["广度 breadth", "up_count / member_count 及其 5 日均值，低于 0.55 判「普涨没起来」。板块涨幅能被 3 只权重股拉起来，广度不跟上趋势是虚的。"],
              ["量能 volume_ok", "5 日均量比落在 [1.0, 3.0] 给满分，>3.0 反而降分：温和放量是资金进场，三倍量通常是一次性情绪。"],
              ["位置 position", "收盘价在过去 21 日高低区间里的位置 + heat_pctile。低吸的全部含意都在这一项：同一套方向判据，位置 0.9 是追高、0.2 才是低吸。"],
              ["排名动量 rank_momentum", "热度排名 5 日内的前进量。板块之间是零和的，它不涨排名不代表资金进来了。"],
            ].map(([k, v]) => (
              <li key={k}><b className="text-foreground">{k}</b><span className="text-muted-foreground"> —— {v}</span></li>
            ))}
          </ul>
          </ExplainBox>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            <b className="text-foreground">档位按顺序互斥</b>：点不足 <code>insufficient</code>（任一窗口点数 &lt; 0.7×窗口 ⇒ 不给判据，显示「数据不足」而不是 0）
            → <code>downtrend</code> → <b className="text-foreground"><code>turn_up_seed</code>：短窗斜率在 3 日内由 ≤0 翻正、且位置 ≤0.35、且广度达标 —— 你要找的就是这一档</b>
            → <code>forming</code>（三窗口同向、位置 ≤0.6）→ <code>established</code>（同向、位置 &gt;0.6）→ <code>overheated</code>（位置 ≥0.85 <b className="text-foreground">且</b> 自身热度分位 ≥0.9；量比 &gt;3 另成一支）。
            ⚠️ <b className="text-foreground">「过热」说的是这个板块在自己 21 日区间的顶部，不是「今天全场最热」</b>：
            实测有 38 行过热板块的当日热度低于全场中位数（自身从 -90 反弹到 -50，自身分位 1.0、横截面第 479 名）。
            所以过热旁边一并念出 <code>reasons.heat_rank / universe</code>，别把它读成龙头榜。
            低吸分权重 = 刚转新鲜度 35 / 位置 25 / 广度 20 / 量能 10 / 排名动量 10 ——
            <b className="text-foreground">这是可商量的先验，不是市场规律</b>。
          </p>
          <ExplainBox title="下面那张表的来源 · 页面不做第二次判断 · 判据版本只增行不覆盖">
          <p>
            下面那张表读的是<b className="text-foreground">落库结果</b> <code>analytics.sector_trend_signal</code>
            （参数在 <code>config.setting['sector_trend_signal.params']</code>，判据版本随数据一起交出）。
            页面不做第二次判断 —— 判据只允许存在一处，两处各判一次就会分叉。
            <b className="text-foreground">改阈值/权重会生成新的 <code>spec_version</code> 行、不覆盖旧行</b>：
            历史上哪天被算成"刚转"，就永远是当时那一版说的话。
          </p>
          </ExplainBox>
        </GlassCard>

        <Panel title="趋势档位与低吸候选（读 `analytics.sector_trend_signal`）" state={trend}
          note="档位、六分项、低吸分全部来自落库结果，页面不做第二次判断；`reasons` 是当时那一行的原始输入，「凭什么」直接念它。"
          slowHint="这张表读的是落库结果，本身不重算；慢只会慢在库，不会慢在浏览器。"
          contract={<Contract path="GET /api/quant/sector-trend?date=&regime=&type=&scope=" source="analytics.sector_trend_signal（判据在 app/compute/sector_trend_signal.py，只有一处）" fields={[
            ["rows[]{regime,dip_score}", "档位（互斥、按序判）与低吸分；dip_score 只在 turn_up_seed/forming 下有值"],
            ["rows[]{dir_agree,persist,breadth,volume_ok,position,rank_momentum}", "六个分项，NULL=当天算不出（不是 0）"],
            ["rows[].reasons", "当时的原始输入：斜率、点数、量比、位置、排名、翻正天数、dip_score_ceiling"],
            ["spec_version / spec_versions_in_db", "本屏只交一个判据版本；库里存在哪些版本一起交出，混版就是把两套结论演成一份"],
            ["groups{snapshot_days,sessions_needed,ready}", "自选组实盘序列要等成员快照攒够窗口，缺几天说得出来"],
          ]} />}>
          {d => {
            const rows = d.rows ?? [];
            const dist = d.regime_distribution_today ?? [];
            return (
              <>
                <Row label="判定日 / 窗口集 / 判据版本"
                  value={`${dash(d.date)} · ${dash(d.window_set)} · ${dash(d.spec_version)}${d.spec_is_effective === false ? `（这一版不是当前生效版，现生效 ${dash(d.effective_spec_version)}）` : ""}`}
                  tone={d.spec_is_effective === false ? "warn" : undefined} />
                <Row label="本屏母体 / 返回行" value={`${dash(d.universe?.sectors)} 个板块 / ${dash(d.universe?.rows_returned)} 行（上限 ${dash(d.universe?.limit)}）`} />
                <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                  {d.universe?.note
                    ? <>{plain(d.universe.note)}<span className="font-mono">（本屏 sectors={dash(d.universe.sectors)} · active_total={dash(d.universe.active_total)}）</span></>
                    : <>后端未交母体口径，这一句只是人工实测：<b className="text-foreground">母体=当日有热度行的板块</b>（某场 561 = 行业 293 + 概念 268），active 的概念+行业有 1,907 个，两者不是一个分母。</>}
                </p>
                {(d.spec_versions_in_db ?? []).length > 1 && (
                  <p className="mt-0.5 text-[11px] text-warning">
                    库里并存多个判据版本：{(d.spec_versions_in_db ?? []).map(v => `${v.spec_version}（${dash(v.rows)} 行）`).join(" · ")}。
                    本屏只交 {dash(d.spec_version)} 那一版 —— <b className="text-foreground">不同版本的档位不是同一套判据，混在一屏就是拿两套结论演成一份</b>。
                  </p>
                )}
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {dist.map(r => (
                    <button type="button" key={`${r.regime}`}
                      onClick={() => setTrendRegime(trendRegime === r.regime ? "" : (r.regime ?? ""))}
                      aria-pressed={trendRegime === r.regime}
                      className={cn("rounded px-1.5 py-0.5 text-[11px]", regimePill(r.regime),
                        "cursor-pointer hover:ring-1 hover:ring-primary/40",
                        trendRegime && trendRegime !== r.regime && "opacity-50")}
                      title={`${d.regime_meaning?.[r.regime ?? ""] ?? "该档含义后端未给，不编"}（点一下只筛这一档；再点取消）`}>
                      {REGIME_CN[r.regime ?? ""] ?? r.regime} {dash(r.n)}
                    </button>
                  ))}
                  {trendRegime && (
                    <span className="text-[11px] text-muted-foreground">
                      正在只看「{REGIME_CN[trendRegime] ?? trendRegime}」这一档（母体计数是全场数，不是本档筛选后的行数）
                    </span>
                  )}
                </div>
                <table className="mt-2 w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="板块" sortKey="name" dir={trendSort.dirOf("name")} onSort={trendSort.toggle} />
                    <SortHeader label="档位" sortKey="regime" dir={trendSort.dirOf("regime")} onSort={trendSort.toggle} />
                    <SortHeader label="低吸分" sortKey="dip_score" dir={trendSort.dirOf("dip_score")} onSort={trendSort.toggle}
                      className="font-normal whitespace-nowrap" title="0~100，上限看括号里的 dip_score_ceiling（缺项会压低上限）" />
                    <th className="font-normal whitespace-nowrap" title="六项都是 0~100。注意 heat_pctile 是 0~1 的另一个量纲，本页一律乘 100 带 % 显示">六分项 0~100（方向/持续/广度/量能/位置/排名）</th>
                    <SortHeader label="斜率" sortKey="slope_long" dir={trendSort.dirOf("slope_long")} onSort={trendSort.toggle}
                      className="font-normal whitespace-nowrap" title="最小二乘斜率 分/天，短/中/长 = 5/11/21 日；点列头按最长那档（21 日，slope_long）排" />
                    <th className="font-normal">凭什么</th>
                  </tr></thead>
                  <tbody>
                    {trendSort.apply(rows).map((r, i) => (
                      <tr key={`${r.sector_code}-${i}`} className="border-t border-border/40 align-top">
                        <td className="py-1 pr-2">
                          <p className="font-medium text-foreground">{dash(r.name)}</p>
                          <p className="font-mono text-[10px] text-muted-foreground">{dash(r.sector_code)} · {dash(r.sector_type)}
                            {r.reasons?.heat_rank != null ? ` · 热度名次 ${r.reasons.heat_rank}/${dash(r.universe)}` : ""}</p>
                        </td>
                        <td className="pr-2 whitespace-nowrap">
                          <span className={cn("rounded px-1 py-0.5 text-[10px]", regimePill(r.regime))}
                            title={r.regime === 'overheated'
                              ? '过热 = 在自己 21 日区间的顶部，不是「今天全场最热」。当日名次见旁边那串'
                              : d.regime_meaning?.[r.regime ?? ''] ?? '该档含义后端未给，不编'}>
                            {REGIME_CN[r.regime ?? ""] ?? dash(r.regime)}
                          </span>
                          {r.regime === 'overheated' && r.reasons?.heat_rank != null && (
                            <span className="ml-1 text-[10px] text-muted-foreground"
                              title="横截面名次只作事实陈列，不参与档位">当日 {r.reasons.heat_rank}/{dash(r.universe)}</span>
                          )}
                          {(r.missing_terms ?? 0) > 0 && <span className="ml-1 text-[10px] text-warning">缺 {r.missing_terms} 项</span>}
                        </td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums">
                          {r.dip_score == null ? dash(null) : num(r.dip_score, 1)}
                          {r.dip_score != null && r.reasons?.dip_score_ceiling != null && r.reasons.dip_score_ceiling < 100 && (
                            <span className="text-[10px] text-muted-foreground"> /上限{num(r.reasons.dip_score_ceiling, 0)}</span>
                          )}
                        </td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums text-muted-foreground">
                          {TERM_KEYS.map((k, ki) => (
                            <span key={k}>{r.terms?.[k] == null ? "缺" : num(r.terms[k], 0)}{ki < TERM_KEYS.length - 1 ? " / " : ""}</span>
                          ))}
                        </td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums">
                          {num(r.slope_short, 1)} / {num(r.slope_mid, 1)} / {num(r.slope_long, 1)}
                          <span className="text-[10px] text-muted-foreground"> （点 {dash(r.points_short)}/{dash(r.points_mid)}/{dash(r.points_long)}）</span>
                        </td>
                        <td className="max-w-72 text-[10px] leading-relaxed text-muted-foreground">{plain(r.reasons?.why)}</td>
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr><td colSpan={6} className="py-2 text-[11px] text-muted-foreground">
                        这一场后端交出 0 行 —— 是<b className="text-foreground">这一版判据没有命中筛选条件</b>，不是"没有趋势"。
                      </td></tr>
                    )}
                  </tbody>
                </table>
                {/* 「排序 = …」那半句是口径解释，收进折叠；后半句是防误读的指针（没有"刚转"是读数不是缺数），
                    留在屏上 —— 少这一句就会有人以为接口坏了。 */}
                <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
                  <b className="text-foreground">最新一场没有"刚转"是读数，不是缺数</b>：想看上一次出现是哪一天，把上面档位条里的「刚转」点一下按档筛。
                </p>
                <CaliberFold caveats={d.caveats}
                  provenance={[d.source, d.generated_at ? `生成于 ${d.generated_at}` : null]}
                  notes={["排序 = 低吸分 → 方向一致 → 热度（后端 `order by`，页面不重排）。"]} />
              </>
            );
          }}
        </Panel>

        <Panel title="低吸候选榜（独立排序：position 升序，只看「刚转」）" state={seed}
          note="与上面那张榜排序方向相反：上面按低吸分降序（谁最像刚转），这里按位置升序（谁还在最低位）。两榜混排会被读成「热度榜的另一种排法」，所以分成两屏。"
          contract={<Contract path="GET /api/quant/sector-trend?sort=seed&regime=turn_up_seed" source="同一张 sector_trend_signal，排序与筛选都由后端做" fields={[
            ["sort=seed", "position 升序 → dip_score 降序：左侧交易先要「低」，再要「新翻正」，最后才是分数"],
            ["regime=turn_up_seed", "只列刚转那一档；退潮/过热/没形状都不进这张榜"],
            ["reasons.turned_days_ago", "翻正是第几天前 —— 0 = 今天刚翻，越大越不「新」"],
            ["reasons.dip_score_ceiling", "这一行的上限（缺项会压低上限），不许读成「分数低」"],
          ]} />}>
          {d => {
            const rows = d.rows ?? [];
            return (
              <>
                <Row label="判定日 / 判据版本"
                  value={`${dash(d.date)} · ${dash(d.spec_version)}${d.spec_is_effective === false ? `（非当前生效版，现生效 ${dash(d.effective_spec_version)}）` : ""}`}
                  tone={d.spec_is_effective === false ? "warn" : undefined} />
                <table className="mt-2 w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="板块" sortKey="name" dir={seedSort.dirOf("name")} onSort={seedSort.toggle} />
                    <SortHeader label="位置 0~100" sortKey="terms.position" dir={seedSort.dirOf("terms.position")} onSort={seedSort.toggle}
                      title="0~100，越大越贴着自身区间顶部；低吸先看低位" />
                    <SortHeader label="刚转" sortKey="reasons.turned_days_ago" dir={seedSort.dirOf("reasons.turned_days_ago")} onSort={seedSort.toggle}
                      title="短窗斜率由负翻正是第几天前；D-0 = 今天刚翻" />
                    <SortHeader label="低吸分" sortKey="dip_score" dir={seedSort.dirOf("dip_score")} onSort={seedSort.toggle}
                      title="缺项的行上限低于 100，见「凭什么」里的 dip_score_ceiling" />
                    <SortHeader label="广度 0~100" sortKey="terms.breadth" dir={seedSort.dirOf("terms.breadth")} onSort={seedSort.toggle} />
                    <SortHeader label="热度名次" sortKey="reasons.heat_rank" dir={seedSort.dirOf("reasons.heat_rank")} onSort={seedSort.toggle}
                      title="当日横截面名次，只作陈列、不参与档位判定" />
                    <th className="font-normal">凭什么</th>
                  </tr></thead>
                  <tbody>
                    {seedSort.apply(rows).map((r, i) => (
                      <tr key={`${r.sector_code}-${i}`} className="border-t border-border/40 align-top">
                        <td className="py-1 pr-2">
                          <p className="font-medium text-foreground">{dash(r.name)}</p>
                          <p className="font-mono text-[10px] text-muted-foreground">{dash(r.sector_code)} · {dash(r.sector_type)}</p>
                        </td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums">{num(r.terms?.position, 1)}</td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums">
                          {r.reasons?.turned_days_ago == null ? "—" : `D-${r.reasons.turned_days_ago}`}
                        </td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums">
                          {num(r.dip_score, 1)}
                          {r.reasons?.dip_score_ceiling != null && r.reasons.dip_score_ceiling < 100 && (
                            <span className="text-[10px] text-muted-foreground"> /上限{num(r.reasons.dip_score_ceiling, 0)}</span>
                          )}
                        </td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums">{num(r.terms?.breadth, 0)}</td>
                        <td className="whitespace-nowrap pr-2 font-mono tabular-nums text-muted-foreground">
                          {r.reasons?.heat_rank == null ? "—" : `${r.reasons.heat_rank}/${dash(r.universe)}`}
                        </td>
                        <td className="max-w-64 text-[10px] leading-relaxed text-muted-foreground">{plain(r.reasons?.why)}</td>
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr><td colSpan={7} className="py-2 text-[11px] text-muted-foreground">
                        这一场没有「刚转」的板块 —— <b className="text-foreground">是读数，不是缺数</b>。
                        低吸候选本来就是稀有事件（全量实测占 1.57%）。
                        {d.last_seed_session && (
                          <> 往前数最近一次有刚转的是 <b className="text-foreground">{d.last_seed_session}（{dash(d.last_seed_count)} 个）</b>{" "}
                            <button type="button" className="text-primary underline" onClick={() => setSeedDate(d.last_seed_session ?? "")}>看那一场</button>
                            {seedDate ? <span className="text-muted-foreground">（现在看的是 {seedDate}，点「重新取数」回最新场）</span> : null}
                          </>
                        )}
                      </td></tr>
                    )}
                  </tbody>
                </table>
                <ExplainBox title="这张榜不含什么：自选组 · 为什么不含 · 去哪看">
                  <p>
                    这一榜<b className="text-foreground">不含自选组</b>：组的实盘序列要等成员快照攒够窗口，
                    在那之前组这一档不给「刚转」候选（见下面那一屏）。
                  </p>
                </ExplainBox>
                <CaliberFold caveats={d.caveats} />
              </>
            );
          }}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">为什么榜上看不到「昨日涨停 / 连板 / 突涨」</h2>
          {/* 两段解释收成默认收起（用户 2026-10-21），中间那一排"逐档实际数"是后端交的读数，留在外面 */}
          <ExplainBox title="为什么这几类天然不在分母里 · 「今日有档位行 0」是什么意思">
            <p>
              做打板的人要看的正是这几类，所以不能静默丢掉。事实是：它们
              <b className="text-foreground">全部 <code>sector_type='指数'</code></b>，而母体参数只取 概念 + 行业
              —— 也就是<b className="text-foreground">天然不在"谁在领涨"的分母里</b>，不是被排除名单剔掉的。
              下面这几档同样不参与排名，逐档列出实际数（后端交出，不是本页估的）：
            </p>
            <p className="mt-2">
              「今日有档位行 0」的意思就是：这几类在库里<b className="text-foreground">没有热度行</b>，
              所以连算判据的原料都没有。要看它们自己的走势，那要走另一条原料线（板块日线里有它们的价格，热度里没有）——
              这句我留在文档里当欠账，不在界面上摆一个看着像有的数。
            </p>
          </ExplainBox>
          <p className="mt-1 flex flex-wrap gap-2 text-[11px]">
            {(() => {
              const list = excludedFromTrend ?? [];
              if (list.length === 0) return <span className="text-warning">后端未交 <code>universe.excluded_types</code>，这一句只能等接口</span>;
              return list.map(e => (
                <span key={`${e.sector_type}`} className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                  {dash(e.sector_type)}：active {dash(e.active)} · 今日有档位行 {dash(e.rows_today)}
                </span>
              ));
            })()}
          </p>
        </GlassCard>
        <Panel title="自选板块那一档：实盘序列要等成员快照攒够" state={trend}
          note="通达信板块能回算历史，是因为 `reference_data.sector_member` 带 `in_date/out_date`（成分有时刻）。自选分组只有当前快照，所以先落一张每日快照表。">
          {d => {
            const g = d.groups ?? {};
            const need = g.sessions_needed ?? null;
            const have = g.snapshot_days ?? 0;
            return (
              <>
                <Row label="成员快照已攒" value={`${dash(have)} 场 / 需要 ${dash(need)} 场（长窗口）`}
                  tone={g.ready === true ? undefined : "warn"} />
                {g.live_trustworthy === false && (
                  <p className="mt-1 rounded-lg bg-destructive/10 p-2 text-[11px] leading-relaxed text-destructive">
                    <b className="text-foreground">但这 {dash(have)} 天不能当实盘序列用。</b>{plain(g.provenance_note)}
                    逐日人数：{(g.by_date ?? []).map(x => `${x.trade_date} ${dash(x.members)}`).join(" · ")}。
                  </p>
                )}
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  {g.ready === true
                    ? <>已够窗口：<code>scope=group</code> 的行就是<b className="text-foreground">实盘序列</b>（当天的名单就是当天看到的名单）。</>
                    : <><b className="text-foreground">还不够</b>：拿今天的名单回算过去的组涨幅是未来函数（用未来知道的名单做过去的决定），那种序列可以画出来看形状，<u>不可用于回测与胜率统计</u>，所以本屏不摆它 —— 要看得懂口径的人自己点名 <code>scope=group_rebuilt</code>。</>}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  差的那 {need != null ? Math.max(0, need - have) : "—"} 场只能等交易日过去，没有捷径。
                  快照不是等人点按钮：<code>app/compute/sector_trend_signal</code> 每次真跑（非 dry-run）会先把当日名单幂等存一遍再算，
                  因为实盘序列要连续 21 场、漏一天就少一天，事后补出来的只是"今日名单"= 未来函数。
                  执行入口是收盘链 <code>ingest/jobs/daily_close_sync.py</code> 的链尾两步（先 <code>--snapshot-only</code> 再 <code>--date</code>），
                  非交易日整链直接 return 2；<b className="text-foreground">e1 那条 <code>signal</code> 步在这条链上永远不会触发</b>（链已重写、不再经过 e1；只引步名，序号随加工段加步漂）。
                  跑没跑在 <Link className="text-primary underline" to="/jobs">任务调度</Link> 上看得见才算数。
                </p>
                <div className="mt-2">
                  <Contract path="GET /api/quant/sector-trend?scope=group" source="同一张 sector_trend_signal，scope 换成 group" fields={[
                    ["rows[]{sector_code=group_id,regime,dip_score}", "组作为一个对象，走的是同一套判据 —— 不另立一套组专用的「趋势」定义"],
                    ["series_kind", "live（有当日成员快照）/ rebuilt（今日名单回算）—— 两者不许混在同一列里"],
                  ]} />
                </div>
              </>
            );
          }}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">算不出的项与它的后果</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            上表标 <b className="text-foreground">缺</b> 的格子是"这一项当天算不出来"，不是 0 分。
            后果就写在上面的接口 caveat 里（原文照贴、不改写）：行业板块的涨跌家数大面积为空 ⇒ 广度项算不出 ⇒
            <b className="text-foreground">行业按这套判据永远不会被标为点火</b>。
            <code>项数</code> 那一列就是这件事的分母。
            库里 <code>ignition.term_count</code> 的列注释里写着一个常数（"230 个行业板块"），
            接口实测的是另一组数、且随场次变 —— 所以本页只引用当下的数，不引用注释里的常数。
          </p>
          <ExplainBox title="同类读数在别的屏：个股档案 · 35 项技术指标 · 与 qd 轨的口径对照">
            <p>
              个股维度的读数（含八分量与窗口斜率）在
              <Link className="text-primary underline" to="/grouped">分组自选 · 量化档案</Link>；
              35 项技术指标在 <Link className="text-primary underline" to="/stock-review">个股深度复盘</Link>（那一屏的「个股研究」块里）。
              本轨生命周期阶段与 qd 轨轮动矩阵的对照在
              <Link className="text-primary underline" to="/qd-compare"> quantdash 口径对照</Link>；
              两者为何不合并见 <code>docs/页面展示工作指南/02</code> §5。
            </p>
          </ExplainBox>
          <div className="mt-2">
            <Contract path="GET /api/quant/sector-heat?scope=group" source="analytics.ignition 的 scope='group'（自选组五要素）" fields={[
              ["scope=group 的行", "同一套五要素判据作用在自己的自选组上 —— 现在只能看板块，看不到组的分数"],
              ["group 键 ↔ group_id", "要与 /api/watchlist/groups 对得上，否则页面只能显示一串键"],
            ]} />
          </div>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
