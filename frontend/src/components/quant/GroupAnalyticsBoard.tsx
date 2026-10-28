import { useMemo, useState } from "react";
import { LineChart } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaveatText, Contract, Definitions, ExplainBox, Measured, Panel, Provenance, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { dash, hasData, num, type DataState } from "@/lib/datastate";
import { rankText } from "@/lib/ranking";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";

/**
 * 分组整体分析（原独立屏「自选组分析」`/watchlist-analytics`，2026-10-14 用户裁定搬进
 * 「我的股票 · 分组自选」的**最上方**：先把组这一层看清楚，再看组里的票）。
 *
 * 三条实现纪律，少一条就会变成两笔债：
 *  · **一份请求**：这一块的取数由调用方交进来（`got`），本组件自己不发请求。
 *    `/grouped` 上原来还有一个「看各组趋势与打分」按钮在读同一个接口 —— 两处各读一份迟早对不上，
 *    所以按钮已经删掉，组卡片上那六格与这里吃的是**同一个 `DataState`**。
 *  · **前端一列都不重算**：行与列全部来自接口交出的字段（`rows[]`/`series[]`/`calibers`/`unit`）。
 *  · **库里没有组级结果表**：读的是组内个股的 `analytics.score` + `analytics.indicator_value`
 *    + `market_data.stock_daily` + `market_data.dragon_tiger_detail`，在查询里现场聚合
 *    （实测 3.3~4.7 秒，所以 `slowHint` 与 `elapsed_ms` 那两格必须留在屏上）。
 *
 * 2026-10-26 加的那一列「近 N 场排名」（用户：「每天的自选组结果搞一个排名，然后展示中加一列，
 * 近5日排名趋势」）没有新表、没有新指标：名次是把这块**已经交出的** 20 场加权分逐场排一遍
 * （`duanxian/watchlist.py::_daily_group_ranks` 调 `duanxian/ranking.py::rank_in_memory`），
 * 窗口天数由接口交（`rank.days`），列头那个 N 不许页面自己抄一个 5。
 * ⚠️ 这一列与行序是**两把尺**：行序默认按预测操作分（整个窗口的 20 场均值再外推一步），
 * 名次按**那一天**的加权分 —— 所以第一名不一定在第一行，这句话由后端 `calibers.rank` 原句上屏。
 *
 * 2026-09-27 那次裁定撤掉的三列（涨/跌/未取到的只数、平均成本、备注「同花顺导入」）不许"顺手补回来"：
 * 「未取到」没有消失，它现在是「上涨占比」的分母。
 */

export interface GroupStat {
  group_id?: number | null; name?: string | null; is_focus?: boolean | null; display_order?: number | null;
  member_count?: number | null; scored_count?: number | null; score_date?: string | null;
  w_score?: number | null; eq_score?: number | null; pred_score?: number | null;
  trend_score?: number | null; trend_days?: number | null;
  /** 最近那一场（＝这一行「加权操作分」那一场）的组间名次；那天排不出就是 null，不是最后一名 */
  rank_now?: number | null;
  /** 逐场名次路径（早→晚，用 → 连），中间那个 `·` 是那天整组算不出加权分 */
  rank_path?: string | null;
  /** 这一串里排得出名次的场次数（凑不满窗口时界面要说清，不许补位） */
  rank_days?: number | null;
  /** 净变化 = 最早那场名次 − 最近那场名次；正数＝名次数字变小（往前走）。null＝两端没都有名次 */
  rank_gain?: number | null;
  avg_pct?: number | null; quoted_count?: number | null; up_count?: number | null; up_ratio?: number | null;
  chg10_bp?: number | null; chg20_bp?: number | null;
  amp10_avg?: number | null; amp20_avg?: number | null; amp_pred?: number | null;
  dt_times?: number | null;
}
export interface SeriesPoint { group_id?: number | null; name?: string | null; trade_date?: string | null; rn?: number | null; w_score?: number | null }
/** 名次那把尺的自述（全部由后端交出，页面不写判据、不抄规则原文） */
export interface GroupRankMeta {
  days?: number | null; sessions?: number | null; from?: string | null; to?: string | null;
  basis?: string | null;
  rule?: string | null; rule_short?: string | null; direction?: string | null;
  universe_note?: string | null;
  universe?: { min?: number | null; max?: number | null } | null;
  per_session?: { rn?: number | null; trade_date?: string | null; ranked?: number | null;
    rows?: number | null; rounded_ties?: number | null }[] | null;
  groups_on_board?: number | null;
  partial?: number | null; no_rank?: number | null; rounded_ties?: number | null;
}
export interface Analytics {
  available?: boolean | null; reason?: string | null; source?: string | null; date?: string | null;
  generated_at?: string | null; elapsed_ms?: number | null;
  rows?: GroupStat[] | null; group_count?: number | null;
  trend_ready?: number | null; trend_awaiting?: number | null;
  series?: SeriesPoint[] | null; series_dates?: string[] | null;
  top_trend_groups?: string[] | null;
  rank?: GroupRankMeta | null;
  window?: { sessions?: number | null; min_days?: number | null; from?: string | null; to?: string | null; note?: string | null } | null;
  market_level?: { lo?: number | null; hi?: number | null; days?: number | null; span?: number | null } | null;
  unit?: Record<string, string> | null; calibers?: Record<string, string> | null;
  caveats?: string[] | null;
}

const COLUMNS: { key: keyof GroupStat & string; label: string; hint: string }[] = [
  { key: "pred_score", label: "预测操作分", hint: "默认序 · 20 场加权分的均值再外推一步 = 水位＋方向；不是明天涨幅" },
  { key: "w_score", label: "加权操作分", hint: "Σ(操作分×当日成交额) ÷ Σ成交额，分母只统计有分的行" },
  { key: "eq_score", label: "平均操作分", hint: "组内等权平均，不看成交额。与加权分差得多＝分被大成交额的一两只拉出来的" },
  { key: "trend_score", label: "趋势分数", hint: "20 场加权分的最小二乘斜率 ×10，读作每 10 场变化多少分；正负号是主信息" },
  { key: "avg_pct", label: "当日均涨幅", hint: "分母是当日有行情行的成员" },
  { key: "chg10_bp", label: "组内10日均涨跌", hint: "组内个股 10 日日均涨跌幅的均值 ×100，单位 bp（1bp=0.01%），不是 %" },
  { key: "chg20_bp", label: "组内20日均涨跌", hint: "同上，20 日窗口" },
  { key: "up_ratio", label: "上涨占比", hint: "上涨只数 ÷ 有当日行情只数；分母不含停牌缺价的那几只" },
  { key: "amp_pred", label: "组内明日振幅", hint: "组内个股 amp_pred 的均值，口径是预测值不是已发生" },
  { key: "amp10_avg", label: "组内10日振幅", hint: "日均振幅，不是区间振幅" },
  { key: "amp20_avg", label: "组内20日振幅", hint: "日均振幅，不是区间振幅" },
  { key: "dt_times", label: "上榜次数", hint: "组内个股在这 20 场的龙虎榜条数合计（本地龙虎榜自 2026-06-01 起）" },
  { key: "trend_days", label: "趋势天数", hint: "这条趋势实际用了几个交易日；不足 10 场整行为空，不补 0" },
  { key: "member_count", label: "成员数", hint: "用于判断前面的数可不可信：只有 5 只的组波动天然大" },
  { key: "scored_count", label: "有分只数", hint: "当日有 oper_score 的成员数；小于成员数说明加权分是部分样本算的" },
];

export function GroupAnalyticsBoard({ got }: { got: DataState<Analytics> }) {
  const grpSort = useTableSort();
  const cur = hasData(got) ? got.data : null;

  return (
    <>
      <Panel title="分组整体分析（组热度表）" state={got}
        slowHint="这一块要把每个组 × 20 场的加权分现场聚合出来，2026-10-07 实测 3.3~3.8 秒、10-14 实测 4.19 秒；先别当成卡死。"
        note="行与列全部来自接口交出的字段，前端不重算任何分数。默认序是后端给的预测操作分从高到低。报「连不到后端」时先看服务在不在 —— 这一块本身没坏（同一接口实测 200），取数层会自己补试两次（隔 2 秒、6 秒）再下结论，重启那几十秒里打开页面不会再一眼红。"
        contract={<Contract path="GET /api/watchlist/group-analytics"
          source="config.watchlist_stock × analytics.score(oper_score) × market_data.stock_daily × analytics.indicator_value × market_data.dragon_tiger_detail"
          fields={[
            ["rows[]{pred_score,w_score,eq_score,trend_score,trend_days}", "分数四列＋这条趋势用了几个交易日"],
            ["rows[]{rank_now,rank_path,rank_days,rank_gain}", "最近那场的组间名次＋逐场名次路径＋排得出名次的场次数＋净变化（正数＝往前走）"],
            ["rank{days,from,to,rule,rule_short,direction,universe_note,per_session}", "名次那把尺自己的说明：窗口几天、母体是哪一批、规则原文"],
            ["rows[]{avg_pct,up_count,quoted_count,up_ratio,chg10_bp,chg20_bp}", "广度：当日均涨幅、上涨占比（带分母）、近 10/20 日日均涨跌（bp）"],
            ["rows[]{amp_pred,amp10_avg,amp20_avg,dt_times}", "组内个股同名日均指标取均值＋龙虎榜上榜次数"],
            ["series[]{group_id,trade_date,rn,w_score}", "折线用的 20 场加权分，与表共用同一段聚合"],
            ["calibers / unit / market_level", "每个数怎么算的、单位是什么、这段时间全市场水位摆了多少"],
          ]} />}>
        {d => {
          const list = d.rows ?? [];
          const ready = list.filter(r => r.trend_score != null).length;
          return (
            <>
              <Row label="组数" value={`${dash(d.group_count ?? list.length)} 个有成员的组 · ${ready} 个够 10 场拟合出趋势`} />
              <Row label="窗口" value={`${dash(d.window?.sessions)} 个有分的交易日（${dash(d.window?.from)} → ${dash(d.window?.to)}）· 最短 ${dash(d.window?.min_days)} 场`} />
              <Row label="全市场水位" value={
                d.market_level?.lo == null ? dash(null)
                  : `oper_score 等权均值在窗口内 ${num(d.market_level.lo, 1)} ~ ${num(d.market_level.hi, 1)}（摆幅 ${num(d.market_level.span, 1)} 分）—— 趋势分数里混着这一项`} />
              {/* 名次那一列的母体写在头里：这张表只有这些组，"全场"这个词在这儿用不得（筛选/母体纪律） */}
              <Row label={`近 ${dash(d.rank?.days)} 场名次`} value={
                d.rank
                  ? `${dash(d.rank.from)} → ${dash(d.rank.to)} · 每天 ${universeText(d.rank)} 个组参与排名`
                    + ` · 这张表一共 ${dash(d.rank.groups_on_board)} 个有成员的组（不是全市场）`
                    + ` · ${dash(d.rank.partial)} 个组这一串没凑满、${dash(d.rank.no_rank)} 个组一场名次都排不出`
                  : "接口没交 rank 那一份：窗口、母体与规则原文都没读到（不是 0 个组）"} />
              <Provenance bits={[d.source, d.generated_at ? `生成于 ${d.generated_at}` : null,
                d.elapsed_ms != null ? `聚合耗时 ${d.elapsed_ms} ms` : null]} />
              <div className="mt-2 overflow-x-auto">
                <table className="w-full min-w-[1220px] text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="组" sortKey="name" dir={grpSort.dirOf("name")} onSort={grpSort.toggle}
                      title="点组名可在下方趋势图里单看这一条线" />
                    <SortHeader label={`近 ${dash(d.rank?.days)} 场排名`} sortKey="rank_now"
                      dir={grpSort.dirOf("rank_now")} onSort={grpSort.toggle}
                      title={[d.rank?.rule_short, d.rank?.direction, d.calibers?.rank,
                        d.rank?.universe_note,
                        "点列头排的是「最近那场」的名次；那串路径与净变化不参与排序"].filter(Boolean).join("｜")} />
                    {COLUMNS.map(c => (
                      <SortHeader key={c.key} label={c.label} sortKey={c.key} title={c.hint}
                        dir={grpSort.dirOf(c.key)} onSort={grpSort.toggle} />
                    ))}
                  </tr></thead>
                  <tbody>
                    {grpSort.apply(list).map((r, i) => (
                      <tr key={`${r.group_id}-${i}`} className="border-t border-border/40 align-top">
                        <td className="py-1 pr-2 whitespace-nowrap">
                          <p className="font-medium text-foreground">{dash(r.name)}</p>
                          <p className="font-mono text-[10px] text-muted-foreground">#{dash(r.group_id)}</p>
                        </td>
                        <RankTd r={r} rk={d.rank ?? null} calibers={d.calibers ?? null} />
                        <Td v={r.pred_score} nd={2} tone="score" strong />
                        <Td v={r.w_score} nd={2} tone="score" />
                        <Td v={r.eq_score} nd={2} tone="score" />
                        <Td v={r.trend_score} nd={2} tone="score" />
                        <Td v={r.avg_pct} nd={2} tone="pct" suffix="%" />
                        <Td v={r.chg10_bp} nd={1} tone="pct" suffix="bp" />
                        <Td v={r.chg20_bp} nd={1} tone="pct" suffix="bp" />
                        <td className="pr-2 text-right font-mono tabular-nums">
                          {r.up_ratio == null ? dash(null) : `${num(r.up_ratio, 1)}%`}
                          <span className="ml-1 text-[10px] text-muted-foreground">
                            {dash(r.up_count)}/{dash(r.quoted_count)}
                          </span>
                        </td>
                        <Td v={r.amp_pred} nd={2} tone="flat" suffix="%" />
                        <Td v={r.amp10_avg} nd={2} tone="flat" suffix="%" />
                        <Td v={r.amp20_avg} nd={2} tone="flat" suffix="%" />
                        <Td v={r.dt_times} nd={0} tone="flat" />
                        <Td v={r.trend_days} nd={0} tone="flat" />
                        <Td v={r.member_count} nd={0} tone="flat" />
                        <Td v={r.scored_count} nd={0}
                          tone={r.scored_count != null && r.member_count != null && r.scored_count < r.member_count ? "warn" : "flat"} />
                      </tr>
                    ))}
                    {list.length === 0 && (
                      <tr><td colSpan={COLUMNS.length + 2} className="py-2 text-[11px] text-muted-foreground">
                        后端交出 0 个组。这是"库里没有带成员的自选组"，不是聚合坏了。
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
                最后四列（趋势天数 / 成员数 / 有分只数）是<b className="text-foreground">元信息</b>：用来判断前面那些数可不可信，
                不是主角。趋势三列为空 = 这一组的窗口不够 10 场，<b className="text-foreground">留空不补 0</b>
                （两三个点也拟合得出斜率，而 0 会被读成"横着走"）；名次那一列同理 ——
                那天整组算不出加权分就是 <b className="text-foreground">—／路径里的「·」</b>，不是最后一名。
                行序与名次是两把尺：行序按预测操作分（整个窗口的外推值），名次按<b className="text-foreground">那一天</b>的加权分，
                所以第一名不一定在第一行。
                逐票明细与按组筛票在本页下面；要看某一只票拼起来的量化档案，
                <b className="text-foreground">双击明细的那一行</b>另开一个窗口看「个股深度复盘」（10-15 那几叠个股功能点合到那一屏）。
              </p>
              {(d.caveats ?? []).map((c, i) => <CaveatText key={i} text={c} />)}
            </>
          );
        }}
      </Panel>

      <GlassCard>
        <h2 className="flex items-center gap-1.5 text-sm font-bold">
          <LineChart className="h-4 w-4 text-primary" /> 各组的分数趋势（这 20 场怎么走过来的）
        </h2>
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
          表给的是今天这一个点和一条斜率，图给的是这段路怎么走 —— 斜率一样的两个组，
          一个稳步爬升、一个暴涨后回落，只看数字分不出来。默认只画<b className="text-foreground">斜率最好的 3 组</b>
          （<GroupChips d={cur} />）：这张图问的是"谁在起来"，不是"谁最强"，
          所以这 3 条<b className="text-foreground">很可能分还是负的</b>。谁最强看上面那张表的第一行。
        </p>
        <TrendChart d={cur} />
        <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
          前 3 条默认常亮（按斜率选，不是按当日分），其余点组名展开、再点收回。
          前 3 条点不掉 —— 它们是默认读数本身。
        </p>
      </GlassCard>

      <GlassCard>
        <h2 className="text-sm font-bold">这一块的口径</h2>
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
          下面每一条都是接口原样交出的（<code>calibers</code>），不是前端另写一份 ——
          两处各写一份迟早对不上。
        </p>
        <div className="mt-2">
          {cur?.calibers ? <Definitions map={cur.calibers} />
            : <p className="text-[11px] text-muted-foreground">口径条文由接口随数据一起交出，这一块没取到数据时不预先描述算法。</p>}
        </div>
        <div className="mt-3 grid gap-1 text-[11px] leading-relaxed text-muted-foreground sm:grid-cols-2">
          <Measured>
            单位混排是刻意的：涨跌类给 <b className="text-foreground">%</b> 与 <b className="text-foreground">bp</b>，
            振幅给 <b className="text-foreground">%/日</b>，分数给 <b className="text-foreground">分</b>。
            <b className="text-foreground">10/20 日均涨跌用 bp 不用 %</b>：组内均值只剩 ±1% 以内，
            两位小数的百分数满屏是"−0.25"看不出组间差别；标成 % 又会把 −25bp 读成 −25%，量级差 100 倍。
          </Measured>
          <Measured>
            不合成「组热度单一指数」：加权分、等权分、趋势、振幅是四件不同的事，
            乘成一个数之后就看不出这一组是"整组在走强"还是"一只大成交额的票把均值顶起来"。
            所以两者<b className="text-foreground">并列给</b>，看差值本身就是读数。
          </Measured>
        </div>
      </GlassCard>

      <GlassCard>
        <h2 className="text-sm font-bold">这一块不做什么</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          按 2026-09-27 的裁定撤掉了三列：<b className="text-foreground">涨/跌/未取到的只数</b>（"未取到"没有消失，
          它现在是"上涨占比"的分母，仍然看得见）、<b className="text-foreground">平均成本</b>与
          <b className="text-foreground">备注（同花顺导入）</b>—— 成本与备注逐票记在本页下面的明细表里，组级均价没有决策价值。
        </p>
        <ExplainBox title="曲线的历史怎么连 · 成员回溯 · is_focus 与空组">
          <p>
            曲线的历史<b className="text-foreground">不拿当日一列去拼</b>：图里每个点都是那一场真的加权分。
            缺数的那一天<b className="text-foreground">断线不连过去</b>，也不补 0。
            成员用今天的分组回溯整条历史（<code>config.watchlist_stock</code> 没有时间维度），
            刚加进去的票会把它过去的分数算进本组趋势线 —— 这一条改不了，只能在读数时知道。
          </p>
          <p>
            <code>is_focus</code> 全库无一为真，那是"没勾过重点组"、不是同步坏了；
            本表只列有成员的组（{dash(cur?.group_count)} 个），空组不占行。
          </p>
        </ExplainBox>
      </GlassCard>
    </>
  );
}

function Td({ v, nd, tone, suffix, strong }: {
  v?: number | null; nd: number; tone: "score" | "pct" | "flat" | "warn"; suffix?: string; strong?: boolean;
}) {
  const cls = tone === "pct" ? pctColor(v)
    : tone === "score" ? (v == null ? "text-muted-foreground" : pctColor(v))
      : tone === "warn" ? "text-warning" : "text-foreground";
  return (
    <td className={cn("pr-2 text-right font-mono tabular-nums whitespace-nowrap", cls, strong && "font-semibold text-foreground")}>
      {v == null ? dash(null) : `${num(v, nd)}${suffix ?? ""}`}
    </td>
  );
}

/** 母体那一句：每天参与排名的组数。min==max 时只念一个数，"44~44" 那种念法读不出信息。 */
function universeText(rk: GroupRankMeta): string {
  const lo = rk.universe?.min, hi = rk.universe?.max;
  if (lo == null || hi == null) return "—（没读到，不是 0 个组）";
  return lo === hi ? String(lo) : `${lo}~${hi}`;
}

/**
 * 名次那一格：最近那场的名次 ＋ 净变化 ＋ 逐场路径。
 *
 * 三个不显然的点：
 *  · 名次数字、规则原文、方向读法、母体说明**全部来自接口**（`rank.*` 与 `calibers.*`），
 *    这里只排版；判据写第二遍迟早走偏（名次公用件那条纪律）。
 *    格子里取的是 `rule_short` 那句短版（列头同款）：`rank.rule` 全版是给推送与文档读的，
 *    它按"票/只"量词写，挂在组这一层的格子里读着别扭 —— 换短句是显示选择，不是另写一份规则。
 *  · 缺名次是 `—`（`rankText` 那一处出口），不是 0、也不是"最后一名"。
 *  · 净变化的颜色跟全站涨跌同一套（红＝往前走、绿＝往后退），语义由 `rank.direction` 原句在悬停里给。
 */
function RankTd({ r, rk, calibers }: {
  r: GroupStat; rk: GroupRankMeta | null; calibers: Record<string, string> | null;
}) {
  const gain = r.rank_gain;
  const gainText = gain == null ? null : gain > 0 ? `进 ${gain}` : gain < 0 ? `退 ${-gain}` : "持平";
  return (
    <td className="py-1 pr-2 whitespace-nowrap"
      title={[rk?.rule_short, rk?.direction, calibers?.rank_gain,
        `逐场名次（早→晚）：${r.rank_path ?? "—"}，那个「·」是那一天整组算不出加权分（不是最后一名）`,
        `这一串里排得出名次的场次：${dash(r.rank_days)} / ${dash(rk?.sessions)}`,
        rk?.universe_note].filter(Boolean).join("｜")}>
      <p className="font-mono text-[12px] font-semibold tabular-nums text-foreground">
        {rankText(r.rank_now)}
        {gainText && (
          <span className={cn("ml-1 text-[10px] font-normal", gain === 0 ? "text-muted-foreground" : pctColor(gain))}>
            {gainText}
          </span>
        )}
      </p>
      <p className="font-mono text-[10px] tabular-nums text-muted-foreground">{dash(r.rank_path)}</p>
    </td>
  );
}

function GroupChips({ d }: { d?: Analytics | null }) {
  const names = d?.top_trend_groups ?? [];
  if (!names.length) return <span>—</span>;
  return <>{names.map(n => <span key={n} className="ml-1 rounded bg-primary/10 px-1 text-[10px] text-primary">{n}</span>)}</>;
}

/**
 * 多组加权分折线。自己画 SVG（与涨停池那段 sparkline 同一套路）而不是引图表库：
 * 只有 41 条 × 20 个点，且"缺数断线"这件事在现成组件里反而要绕。
 */
function TrendChart({ d }: { d?: Analytics | null }) {
  const data = d ?? null;
  const [open, setOpen] = useState<string[]>([]);
  const lines = useMemo(() => {
    const by = new Map<string, { name: string; pts: Map<number, number> }>();
    for (const p of data?.series ?? []) {
      if (p.group_id == null || p.name == null || p.rn == null || p.w_score == null) continue;
      const k = String(p.group_id);
      if (!by.has(k)) by.set(k, { name: p.name, pts: new Map() });
      by.get(k)!.pts.set(p.rn, p.w_score);
    }
    const trendOf = new Map((data?.rows ?? []).map(r => [String(r.group_id), r.trend_score ?? null]));
    return [...by.entries()]
      .map(([id, v]) => ({ id, name: v.name, pts: v.pts, trend: trendOf.get(id) ?? null }))
      .sort((a, b) => (b.trend ?? -Infinity) - (a.trend ?? -Infinity));
  }, [data?.series, data?.rows]);

  const dates = data?.series_dates ?? [];
  const top3 = lines.slice(0, 3).map(l => l.id);
  const visible = lines.filter(l => top3.includes(l.id) || open.includes(l.id));
  if (!dates.length || !lines.length) {
    return <p className="mt-2 text-[11px] text-muted-foreground">序列还没有内容：这一块要先有 20 个有分的交易日。</p>;
  }
  const xs = dates.map((_, i) => i + 1);
  const vals = visible.flatMap(l => [...l.pts.values()]);
  const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
  const W = 560, H = 180, PAD = 6;
  const px = (i: number) => (PAD + (i - 1) / Math.max(1, xs.length - 1) * (W - PAD * 2)).toFixed(1);
  const py = (v: number) => (H - PAD - (v - min) / rng * (H - PAD * 2)).toFixed(1);

  return (
    <div className="mt-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-44 w-full" preserveAspectRatio="none" role="img"
        aria-label={`组加权分趋势，${visible.length} 条线`}>
        <line x1={PAD} y1={py(min)} x2={W - PAD} y2={py(min)} className="stroke-border" strokeWidth="0.5" />
        <line x1={PAD} y1={py(max)} x2={W - PAD} y2={py(max)} className="stroke-border" strokeWidth="0.5" />
        {max > 0 && min < 0 && (
          <line x1={PAD} y1={py(0)} x2={W - PAD} y2={py(0)} className="stroke-muted-foreground" strokeDasharray="3 3" strokeWidth="0.5" />
        )}
        {visible.map(l => {
          const segs: string[] = [];
          let cur: string[] = [];
          xs.forEach((i, idx) => {
            const v = l.pts.get(i);
            if (v == null) { if (cur.length > 1) segs.push(cur.join(" ")); cur = []; return; }
            cur.push(`${px(idx + 1)},${py(v)}`);
          });
          if (cur.length > 1) segs.push(cur.join(" "));
          return (
            <g key={l.id}>
              {segs.map((pts, si) => (
                <polyline key={si} points={pts} fill="none" strokeWidth="1.4"
                  className={l.trend == null ? "stroke-muted-foreground" : l.trend >= 0 ? "stroke-danger" : "stroke-success"}
                  vectorEffect="non-scaling-stroke" />
              ))}
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-[10px] text-muted-foreground">
        纵轴 {num(min, 1)} ~ {num(max, 1)} 分（只按<b className="text-foreground">当前显示的那几条</b>定标，勾组会变）·
        横轴 {dates.length} 场：{dates[0]} → {dates[dates.length - 1]} · 缺数断线不连 · 线色只表斜率正负
      </p>
      <div className="mt-2 flex flex-wrap gap-1">
        {lines.map(l => {
          const on = top3.includes(l.id) || open.includes(l.id);
          return (
            <button key={l.id} type="button"
              onClick={() => setOpen(o => (top3.includes(l.id)
                ? o : o.includes(l.id) ? o.filter(x => x !== l.id) : [...o, l.id]))}
              title={`趋势分数 ${dash(l.trend)}`}
              className={cn("rounded px-1.5 py-0.5 text-[10px]",
                on ? "bg-primary/15 text-primary" : "bg-muted/40 text-muted-foreground hover:bg-muted")}>
              {l.name}{l.trend != null ? ` ${l.trend > 0 ? "+" : ""}${num(l.trend, 1)}` : ""}
            </button>
          );
        })}
      </div>
    </div>
  );
}
