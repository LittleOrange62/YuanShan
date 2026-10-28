import { Link } from "react-router-dom";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { ConceptBoard, type ConceptRow } from "@/components/quant/ConceptBoard";
import { RankStabilityBoard, type RankStabilityData } from "@/components/quant/RankStabilityBoard";
import { SteadyRankBoard } from "@/components/quant/SteadyRankBoard";
import { MomentumFollowBoard } from "@/components/quant/MomentumFollowBoard";
import type { FollowData } from "@/lib/momentumFollow";
import type { SteadyData, SteadyMetric } from "@/lib/steadyRank";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { num } from "@/lib/qd";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { plain } from "@/lib/agent";

/**
 * 四维象限（规划 §2 维度二）。导航栏、面包屑、页头三处名字必须一致。
 *
 * 两块刻意不合并 —— 它们回答两个不同的问题：
 *  · <b>选股象限</b>（X=个股质量、Y=环境共振）判<b>该不该买</b>；
 *  · <b>复盘归因象限</b>（判对错 × 盈亏）判<b>我看对了没</b>。
 * 合成一张表会逼其中一套的列留空，看起来像数据缺失（同双轨契约 02 §5 的理由）。
 *
 * ⚠️ 当日象限与"窗口入口象限"的分布本来就不一样，接口给了 caveat 原文，这里逐字透出。
 */

interface Dist { count?: number | null; label?: string | null; meaning?: string | null }
/** 反向概念榜的行形状住在共用那块里（`ConceptBoard`），这里只引用、不重定义一份。 */
interface QRow {
  code?: string | null; name?: string | null; quadrant?: string | null; window_entry_quadrant?: string | null;
  quadrant_trend?: string | null;
  /** 后端交出的榜内名次（按 oper_value 降序的原序）：分页按它切页，页内点列头只换先后 */
  rank_in_pool?: number | null;
  /** 概念归属：只列前 3 个（窄题材在前），口径读接口 concept_rule，页面不自己解释 */
  concepts?: string | null; concept_count?: number | null;
  oper_value?: number | null; rank_in_market?: number | null; percent_rank?: number | null; is_stale?: boolean | null;
  x_quality?: number | null; y_env?: number | null; slope?: number | null; up_streak_days?: number | null;
  window_days?: number | null; point_count?: number | null;
}
interface Quadrant {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  distribution?: Record<string, Dist> | null; rows?: QRow[] | null;
  axes?: Record<string, unknown> | null; quadrant_rule?: Record<string, unknown> | null;
  quadrant_labels?: Record<string, string> | null; labels_source?: string | null;
  filter?: Record<string, unknown> | null; definitions?: Record<string, string> | null;
  /** 把这批票反向按概念数一遍（一行＝一个概念名，同名的多条板块码只算一次） */
  concepts?: ConceptRow[] | null;
  concept_pool?: { size?: number | null; covered?: number | null;
                   concepts_total?: number | null; single_stock?: number | null } | null;
  /** 概念那一列与反向榜的口径文字（后端原句，页面不改写） */
  concept_rule?: string | null;
  null_coverage?: NullCoverage | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}
interface NullCoverage {
  score_oper_rows_all_sessions?: number | null;
  score_quadrant_null_all_sessions?: number | null;
  score_quadrant_null_pct_all_sessions?: number | null;
  score_selected_session?: { date?: string | null; quadrant_null?: number | null } | null;
  score_trend_rows?: number | null;
  score_trend_first_quadrant_null?: number | null;
  score_trend_first_quadrant_null_pct?: number | null;
  why?: string | null;
}
interface AttributionCell { days?: number | null; pnl?: number | null }
interface Attribution {
  available?: boolean | null; reason?: string | null; days_counted?: number | null; enough_samples?: boolean | null;
  quadrant_labels?: Record<string, string> | null; quadrants?: Record<string, AttributionCell> | null;
  skipped_no_amount?: number | null; skipped_no_read?: number | null; note?: string | null;
}

export function Quadrant() {
  const [nonce, refresh] = useNonce();
  const q = useProbe<Quadrant>("/quant/quadrant", nonce);
  const qdSort = useTableSort();
  const attr = useProbe<Attribution>("/risk/attribution", nonce);
  const [quadrantFilter, setQuadrantFilter] = useState<string | null>(null);
  /** 分页：一页 100 行，第二页手动加载（用户 2026-10-12 点名）。接口默认交 200 行，正好两页。 */
  const [page, setPage] = useState(1);
  const nc = q.kind === "ok" ? (q.data.null_coverage ?? null) : null;

  /** 名次稳定榜（用户 2026-10-15 点名近 3/7/11 场名次之和、前 50、按和升序）。
   *  窗口是**状态**不是判据：判据全在接口那一份，页面只交"问哪一档"。
   *  `rsFresh` 单独一档：那一块有它自己的「清缓存重算」，不该把整屏的三个接口都重取一遍。 */
  const [rsDays, setRsDays] = useState(7);
  const [rsFresh, setRsFresh] = useState(0);
  const rs = useProbe<RankStabilityData>("/quant/rank-stability", nonce + rsFresh,
    { days: rsDays, limit: 50, refresh: rsFresh ? 1 : undefined });

  /** 名次稳步前进榜（用户 2026-10-18 点名「xy 名次近 6 个交易日稳步增长…可以切换成总分稳步增长」）。
   *  窗口固定 6 场、判据全在接口那一份；页面只交「问哪一档、取多深」。 */
  const [rmMetric, setRmMetric] = useState<SteadyMetric>("xy");
  const [rmLimit, setRmLimit] = useState(80);
  const [rmFresh, setRmFresh] = useState(0);
  const rm = useProbe<SteadyData>("/quant/rank-momentum", nonce + rmFresh,
    { metric: rmMetric, limit: rmLimit, refresh: rmFresh ? 1 : undefined });

  /** 上一场稳步前进榜 · 后续表现（用户 2026-10-20 凌晨点名「昨日前 80 名的进入股价表现」）。
   *  母体档位/前 N 与上面那张榜**共享同一个状态**（切上面那张，这块跟着换母体），
   *  基准日不在这儿选：接口默认取「库里最新有名次场次的前一场」，这句原话在 caveats 里。 */
  const mf = useProbe<FollowData>("/quant/momentum-follow", nonce + rmFresh,
    { metric: rmMetric, limit: rmLimit, refresh: rmFresh ? 1 : undefined });

  /** 选股象限那张榜的交互（单击交叉高亮 / 双击开深度复盘）—— 实现全站一份。 */
  const qx = useStockRowInteraction("四维象限");
  const sortQx = (k: string) => { qx.clearAll(); qdSort.toggle(k); };

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="四维象限" level={1}
        subtitle="X=个股质量、Y=环境共振，四档判「该不该买」；下面那块是另一件事：判对错 × 盈亏，判「我看对了没」。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <Panel title="选股象限（质量 × 环境）" state={q} slowHint="象限分布要过全市场打分表，实测约 7 秒。"
          note="点上面某一档可以只筛那一档的票；不点就是全量前若干名。分布是全场统计，不受下面的筛选影响。">
          {d => {
            const all = d.rows ?? [];
            // 顺序只有一份：接口按 oper_value 降序交出、行上带 rank_in_pool。
            // 先筛档 → 再按名次切页 → 页内点列头只换本页先后。
            // 反过来（先排序再切页）会让"第 2 页"变成"按某一列排完之后不知道是哪些票"。
            const PAGE = 100;
            const filtered = all.filter(r => quadrantFilter === null || r.quadrant === quadrantFilter);
            const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
            const cur = Math.min(page, pages);
            const pageRows = filtered.slice((cur - 1) * PAGE, cur * PAGE);
            const lo = pageRows[0]?.rank_in_pool ?? null;
            const hi = pageRows[pageRows.length - 1]?.rank_in_pool ?? null;
            const nextLo = filtered[cur * PAGE]?.rank_in_pool ?? null;
            const nextHi = filtered[Math.min(filtered.length, (cur + 1) * PAGE) - 1]?.rank_in_pool ?? null;
            const rule = d.concept_rule ?? "（这一列的口径文字后端没交，页面不自己编）";
            return (
              <>
                <Row label="评分日" value={dash(d.date)} />
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {Object.entries(d.distribution ?? {}).map(([k, v]) => (
                    <button key={k} type="button" onClick={() => { setQuadrantFilter(quadrantFilter === k ? null : k); setPage(1); qx.clearAll(); }}
                      aria-pressed={quadrantFilter === k}
                      className={cn("rounded-lg border p-2 text-left", quadrantFilter === k ? "border-primary/70 bg-primary/10" : "border-border hover:bg-muted/50")}>
                      <p className="text-[12px] font-semibold">{dash(v.label)}<span className="ml-1 font-mono text-[10px] text-muted-foreground">{k}</span></p>
                      <p className="text-[11px] text-muted-foreground">{dash(v.meaning)}</p>
                      <p className="mt-0.5 font-mono text-[12px] tabular-nums">{dash(v.count)} 只</p>
                    </button>
                  ))}
                </div>
                {/* 口径声明 / 字段含义 / 覆盖 / 出处收成一处默认收起的折叠块（用户 10-19：
                    「这些内容都默认收缩起来」）。summary 把每一类的条数摆在外面，所以收起≠藏掉；
                    告警类（弹窗被拦、筛出来的空、截断）仍然不折 —— 那条规矩没变。 */}
                <CaliberFold caveats={d.caveats} definitions={d.definitions}
                  coverage={d.coverage} provenance={[d.source, d.labels_source]} />
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead><tr className="text-left text-muted-foreground">
                      <SortHeader label="名次" sortKey="rank_in_pool" dir={qdSort.dirOf("rank_in_pool")} onSort={sortQx}
                        title="榜内名次，按综合分（oper_value）降序，由后端随行交出。分页就按这一列切：第 1 页 1~100、第 2 页 101~200。页内点别的列头只换本页先后，不会把别页的票换进来" />
                      <SortHeader label="代码" sortKey="code" dir={qdSort.dirOf("code")} onSort={sortQx} /><SortHeader label="名称" sortKey="name" dir={qdSort.dirOf("name")} onSort={sortQx} /><SortHeader label="档" sortKey="quadrant" dir={qdSort.dirOf("quadrant")} onSort={sortQx} />
                      <SortHeader label="概念板块" sortKey="concepts" dir={qdSort.dirOf("concepts")} onSort={sortQx}
                        title={`只列这只票身上最窄的前 3 个概念（后面那个数是它一共有几个概念）。${rule}`} />
                      <SortHeader label="入口档" sortKey="window_entry_quadrant" dir={qdSort.dirOf("window_entry_quadrant")} onSort={sortQx}
                        title="进窗口第一天的档，与「档」（当日）本来就可以不同" />
                      <SortHeader label="象限趋势" sortKey="quadrant_trend" dir={qdSort.dirOf("quadrant_trend")} onSort={sortQx}
                        title="窗口期内每天的象限变化轨迹（Q1→Q2→Q3），看稳定性与迁移方向" />
                      <SortHeader label="X 质量" title="质量轴原值（x_quality）" sortKey="x_quality" dir={qdSort.dirOf("x_quality")} onSort={sortQx} /><SortHeader label="Y 环境" title="环境轴原值（y_env）" sortKey="y_env" dir={qdSort.dirOf("y_env")} onSort={sortQx} />
                      <SortHeader label="oper" sortKey="oper_value" dir={qdSort.dirOf("oper_value")} onSort={sortQx} /><SortHeader label="全市场排名" sortKey="rank_in_market" dir={qdSort.dirOf("rank_in_market")} onSort={sortQx} /><SortHeader label="斜率" sortKey="slope" dir={qdSort.dirOf("slope")} onSort={sortQx} />
                      <SortHeader label="窗口" sortKey="point_count" dir={qdSort.dirOf("point_count")} onSort={sortQx}
                        title="窗口内点数；不足窗口的行不要当满窗读" />
                    </tr></thead>
                    <tbody>
                      {qdSort.apply(pageRows).map((r, i) => (
                        <tr key={`${r.code}-${r.rank_in_pool ?? i}`} {...qx.rowProps(i, r.code)}>
                          <td {...qx.cellProps(i, "rank_in_pool", "font-mono tabular-nums text-muted-foreground")}>{dash(r.rank_in_pool)}</td>
                          <td {...qx.cellProps(i, "code", "whitespace-nowrap font-mono")}><StockLink code={r.code} className="font-mono hover:text-primary">{dash(r.code)}</StockLink></td>
                          <td {...qx.cellProps(i, "name", "whitespace-nowrap")}><StockLink code={r.code} name={r.name} className="hover:text-primary" /></td>
                          <td {...qx.cellProps(i, "quadrant", "font-mono")}>{dash(r.quadrant)}</td>
                          {/* 概念板块：只列最窄的前 3 个，口径是后端那句原话（页面不复述一遍，两处各写一套早晚走偏） */}
                          <td {...qx.cellProps(i, "concepts", "max-w-[190px] whitespace-normal break-words text-[10px] leading-tight")}
                            title={`${r.concepts
                              ? `这只票在参考数据里有 ${dash(r.concept_count)} 个概念归属，这里按成员数由少到多只列前 3 个（窄题材在前）。`
                              : "库里没有它的概念归属行：显示 —，不是「0 个题材」，也不是页面截断了。"}${rule}`}>
                            {r.concepts ? dash(r.concepts) : dash(null)}
                            {typeof r.concept_count === "number" && (
                              <span className="ml-1 font-mono text-muted-foreground">共{r.concept_count}</span>
                            )}
                          </td>
                          <td {...qx.cellProps(i, "window_entry_quadrant", cn("font-mono",
                            r.window_entry_quadrant !== r.quadrant && "text-warning"))}>
                            {dash(r.window_entry_quadrant)}{r.window_entry_quadrant !== r.quadrant ? " ≠" : ""}
                          </td>
                          <td {...qx.cellProps(i, "quadrant_trend", "font-mono text-[10px] leading-tight")} title={r.quadrant_trend || '窗口内象限序列为空'}>
                            {r.quadrant_trend ? (
                              <span className="tracking-tight">{r.quadrant_trend}</span>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                          <td {...qx.cellProps(i, "x_quality", cn("font-mono tabular-nums", pctColor(r.x_quality ?? null)))}>{num(r.x_quality, 2)}</td>
                          <td {...qx.cellProps(i, "y_env", cn("font-mono tabular-nums", pctColor(r.y_env ?? null)))}>{num(r.y_env, 2)}</td>
                          <td {...qx.cellProps(i, "oper_value", "font-mono tabular-nums")}>{num(r.oper_value, 2)}</td>
                          <td {...qx.cellProps(i, "rank_in_market", "font-mono tabular-nums")}>
                            {dash(r.rank_in_market)}{typeof r.percent_rank === "number" ? `（${r.percent_rank.toFixed(1)}%）` : ""}
                            {r.is_stale === true && <span className="ml-1 text-warning">旧</span>}
                          </td>
                          <td {...qx.cellProps(i, "slope", "font-mono tabular-nums")}>{num(r.slope, 3)}</td>
                          <td {...qx.cellProps(i, "point_count", "whitespace-nowrap text-muted-foreground")}>{dash(r.window_days)}日/{dash(r.point_count)}点</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {/* 怎么点这一张表：一句通用话（实现全站一份），弹窗被拦的告警单独念、不折进说明框 */}
                  <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
                  {qx.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{qx.popHint}</p>}
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="text-muted-foreground">
                      接口交出 {all.length} 行 · 每页 100 行，现在是第 {cur}/{pages} 页
                      {lo != null && <>，本页名次 <b className="font-mono text-foreground">{lo}~{hi}</b></>}
                      {quadrantFilter && <>（已筛 {quadrantFilter} 档：本页名次跳号是筛选造成的，不是漏了行）</>}
                    </span>
                    {cur > 1 && (
                      <button type="button" onClick={() => { qx.clearAll(); setPage(cur - 1); }}
                        className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>
                    )}
                    {cur < pages && (
                      <button type="button" onClick={() => { qx.clearAll(); setPage(cur + 1); }}
                        className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">
                        加载第 {cur + 1} 页{nextLo != null && <>（名次 {nextLo}~{nextHi}）</>}
                      </button>
                    )}
                  </div>
                  {/* 两页是同一份数切开的，不是两次取数 —— 不写这句就会被读成"第二页是刚拉的实时数" */}
                  <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                    第二页不重新发请求：一次取数交 200 行，两页是同一段时间的两刀（基准同一场，不会翻页之间数变了）。
                    点上面四张档卡只筛这一屏的行、不改名次，翻页后仍是筛完再切页。
                  </p>
                  {pageRows.length === 0 && <p className="mt-1 text-[11px] text-muted-foreground">这一档在本次交出的行里没有票 —— 是筛出来的空，不是数据没了。</p>}
                </div>
              </>
            );
          }}
        </Panel>

        {/* 名次稳定榜：与上面那张榜同一张源表（`analytics.score` 的 `rank_in_market`），
            但回答的是另一个问题 ——「这几天谁一直靠前」，不是「今天谁分高」。
            表体实现在 `components/quant/RankStabilityBoard.tsx`（只此一份），判据全在接口那一份。 */}
        <RankStabilityBoard state={rs} days={rsDays}
          onDaysChange={setRsDays}
          onRefresh={() => setRsFresh(n => n + 1)} />

        {/* 名次稳步前进榜：与上面那张读同一列 `rank_in_market`，问的却是另一个问题 ——
            上一张答「谁一直靠前」，这一张答「谁一直在往前走」。判据只在接口那一份。 */}
        <SteadyRankBoard state={rm} metric={rmMetric} onMetricChange={setRmMetric}
          limit={rmLimit} onLimitChange={setRmLimit}
          onRefresh={() => setRmFresh(n => n + 1)} />

        {/* 后续表现榜（用户 10-20 凌晨点名，紧长在母体榜正下方）：母体＝上一场那张稳步前进榜的
            前 N 名（同档 metric），看它们的「今日涨跌 + 近 5 日涨跌」与母体场次当天的各路名次/在榜。
            10-27 用户点名改名：母体是前一场 ⇒ 基准后第一场就是今天，旧名「次日」让人以为要等明天；
            「至今累计」在母体=前一场时与今日是同一个数，撤下换成近 5 日（起点不同，两列不许相减）。
            判据、母体、复权尺子全在接口那一份（duanxian/momentum_follow.py）。 */}
        <MomentumFollowBoard state={mf} metric={rmMetric} onMetricChange={setRmMetric}
          limit={rmLimit} onLimitChange={setRmLimit}
          onRefresh={() => setRmFresh(n => n + 1)} />

        {/* 反向聚合：把上面「选股象限」那张榜的票按概念板块数一遍。
            分母**只有本榜交出的这些票**，不是全市场 —— 这句话必须摆在表头旁边，
            否则「融资融券 130 只」会被读成「全市场有 130 只融资融券进了象限榜」。
            表体与倍量形态那一屏共用一份实现（`components/quant/ConceptBoard.tsx`）。 */}
        <ConceptBoard state={q} pageSize={20}
          note="一行＝一个概念名：同一个概念名挂多条板块码时只算一次；不做同义名合并（源站是两条就这里是两条）。"
          poolLine={d => `上面那张榜交出的 ${dash(d.concept_pool?.size ?? (d.rows ?? []).length)} 只（不是全市场，也不是这一场全部有象限的票）`}
          pickCaveats={d => (d.caveats ?? []).filter(c => String(c.code ?? "").startsWith("concept") || c.code === "pool_truncated")} />

        <Panel title="复盘归因象限（判对错 × 盈亏）" state={attr}
          note="看对了却亏钱 = 执行问题；看错了还赚钱 = 运气。按入场日对齐：那天的操作依据是前一晚那份判断；盈亏恰好为 0 的日子不进任何一格。">
          {d => (
            <>
              <Row label="可归因交易日" value={`${dash(d.days_counted)} 天${d.enough_samples === false ? "（样本不足以形成结论）" : ""}`}
                tone={d.enough_samples === false ? "warn" : undefined} />
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {Object.entries(d.quadrants ?? {}).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-border p-2">
                    <p className="text-[12px] font-semibold">{d.quadrant_labels?.[k] ?? k}</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">{dash(v.days)} 天 · 合计 {num(v.pnl, 2)} 元</p>
                  </div>
                ))}
              </div>
              {(d.skipped_no_amount ?? 0) + (d.skipped_no_read ?? 0) > 0 && (
                <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                  没进任何一格的：缺成交明细 {dash(d.skipped_no_amount)} 笔、那天没有判断记录 {dash(d.skipped_no_read)} 笔 —— 分母不完整的象限不能读成结论。
                </p>
              )}
              {d.note && <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{plain(d.note)}</p>}
              <p className="mt-2 text-[11px] text-muted-foreground">
                逐笔依据在 <Link className="text-primary underline" to="/journal">交易日志</Link>。
              </p>
            </>
          )}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">两套象限为什么必须分开看，而且不得互相翻译</h2>
          {/* 说明与定义收进通用说明框（用户 2026-10-14）。**下面那段「四格相加不等于全市场票数」没跟着折** ——
              它交的是缺数行数与为空占比的实际读数（象限为空的票一格都不进），藏起来等于让屏幕说假话。 */}
          <ExplainBox title="两套象限的分母差在哪 · 为什么不得互相翻译">
            <p className="mt-1">
              上面那套的分母是<b className="text-foreground">全市场所有有评分的票</b>（当日），
              下面那套的分母是<b className="text-foreground">我自己填了成交明细的已平仓交易日</b>。
              两者数量差三四个数量级，混在一屏里读会得出"我操作性地回避了 70% 的票"这种荒谬结论。
            </p>
            <p className="mt-2">
              更要说清的是<b className="text-foreground">不存在"Q1 就是判对且赚钱"</b>：上面的 Q1~Q4 是
              X（个股质量）与 Y（环境共振）的正负组合，下面的四格是「判断对错 × 盈亏」。
              两把尺子量的是两件不同的事，这里不做任何换算，也不给对照表 —— 谁把另一套的格子翻成自己这套，
              就是在造一个库里没有的数。
            </p>
          </ExplainBox>
          {nc && (
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              <b className="text-foreground">四格相加不等于全市场票数</b>：象限为空的票一格都不进。
              {dash(nc.score_selected_session?.date)} 这一场为空 {dash(nc.score_selected_session?.quadrant_null)} 行；
              全部历史 {dash(nc.score_oper_rows_all_sessions)} 行综合分里有 {dash(nc.score_quadrant_null_all_sessions)} 行
              （{dash(nc.score_quadrant_null_pct_all_sessions)}%）没算出象限；
              入口象限（<code className="font-mono">window_entry_quadrant</code>）全区间 {dash(nc.score_trend_first_quadrant_null_pct)}%
              为空 —— 页面显示 <code className="font-mono">—</code>，不是 0。
              {nc.why && <span className="mt-1 block">{nc.why}</span>}
            </p>
          )}
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
