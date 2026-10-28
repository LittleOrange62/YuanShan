import { RefreshCw } from "lucide-react";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { CaliberFold, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { useState } from "react";
import { StockLink } from "@/components/ui/StockLink";
import { AmbushBoard } from "@/components/quant/AmbushBoard";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";

/**
 * 突破信号 = 左右两栏（用户 2026-10-13 的原话：「页面改为左侧和右侧；右侧就是原来的
 * 突破信号；左侧就是那种埋伏票」）。
 *
 *  · 右栏 `BreakoutBoard` —— **已确认**的右侧信号（放量波 → 缩量收敛 → 首破均线）；
 *  · 左栏 `<AmbushBoard />` —— 还没走出来的左侧机会（均线汇集 + 周线重心抬升 + 缩量）。
 *
 * 两栏各自取数、各自的筛条件与阈值都在后端一份，页面不重算判据；
 * 两栏各砍到 8 列，被砍的那些进整行悬停（口径原话在后端列注释里，不在这里重写）。
 */

interface RowX {
  code?: string | null; name?: string | null; board?: string | null;
  signal_type?: string | null; signal_type_label?: string | null;
  last_surge_date?: string | null; surge_vol_ratio?: number | null;
  shrink_days?: number | null; converge_days?: number | null;
  break_vol_ratio?: number | null; break_dev?: number | null;
  break_close?: number | null; break_ma60?: number | null;
  window_days?: number | null; bars_used?: number | null;
}
interface BreakoutData {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  window_days?: number | null; rows?: RowX[] | null; count?: number | null;
  filter?: { signal_type?: string | null; signal_type_rejected?: string[] | null;
             rows_returned?: number | null; limit?: number | null } | null;
  definitions?: Record<string, string> | null;
  type_options?: { value?: string | null; label?: string | null;
                   meaning?: string | null; count?: number | null }[] | null;
  thresholds?: Record<string, number> | null;
  day_total?: number | null;
  universe?: { codes_total?: number | null; codes_scanned?: number | null;
               computed_by?: string | null } | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

/** 母体那一行的老实话：这一榜的数只覆盖当天有行情的票里被扫到的那一批，不是全市场。 */
function universeText(d: BreakoutData) {
  const u = d.universe ?? {};
  const total = typeof u.codes_total === "number" ? u.codes_total : null;
  const scanned = typeof u.codes_scanned === "number" ? u.codes_scanned : null;
  if (total === null || scanned === null) {
    return "没读到当天有多少只有行情 —— 上面那些数先按「榜内的数」读，不当成全市场";
  }
  return scanned >= total
    ? `当天有行情的 ${total.toLocaleString()} 只全部扫过`
    : `当天有行情的 ${total.toLocaleString()} 只里扫了前 ${scanned.toLocaleString()} 只`
      + `（逐票扫 40 日窗口的那一段判据），所以上面「命中/各类」都是这 ${scanned.toLocaleString()} 只里的数，不是全市场`;
}

const universePartial = (d: BreakoutData) => {
  const u = d.universe ?? {};
  return typeof u.codes_total === "number" && typeof u.codes_scanned === "number"
    && u.codes_scanned < u.codes_total;
};

/** 砍掉的四列（板块 / 上次放量日 / 那波放量倍数 / 突破日 MA60）＋窗口真实性，全进整行悬停 */
function boHover(r: RowX, th: Record<string, number>) {
  const f = (v: number | null | undefined, dp = 2, suffix = "") =>
    typeof v === "number" ? `${v.toFixed(dp)}${suffix}` : dash(null);
  return [
    `板块：${dash(r.board)}`,
    `上次放量日：${dash(r.last_surge_date)}，那波放量的最大倍数 ${f(r.surge_vol_ratio)}×`
      + `（判据是 vol ≥ 前 5 日均量 ×${th.surge_mult ?? "—"}）`,
    `突破日收盘 ${f(r.break_close)} 对当日 MA60 ${f(r.break_ma60)}`,
    `观察窗 ${dash(r.window_days)} 个交易日，这只票实际只有 ${dash(r.bars_used)} 根 K 线`
      + (typeof r.bars_used === "number" && typeof r.window_days === "number" && r.bars_used < r.window_days
        ? "（不足满窗，不能与满窗的票横比）" : ""),
  ].join("\n");
}

/** 右栏：原来的突破信号榜，12 列精简到 8 列 */
function BreakoutBoard() {
  const [nonce, refresh] = useNonce();
  const [signalType, setSignalType] = useState("");
  const hits = useProbe<BreakoutData>(
    `/quant/volume-breakout?signal_type=${encodeURIComponent(signalType)}`, nonce);
  const vdSort = useTableSort();
  /** 单击格 → 整行＋整列交叉高亮、双击行 → 另开窗口看个股深度复盘：全站一份实现
   *  （`lib/useStockRowInteraction.ts`，用户 2026-10-15）。原来这份 `selectedIdx` 只做整行
   *  高亮（这一屏没有明细行可展开），那一半交给共用 hook。 */
  const bo = useStockRowInteraction("突破信号");
  const sortBo = (k: string) => { bo.clearAll(); vdSort.toggle(k); };

  const cur = hits.kind === "ok" ? hits.data : null;
  const sel0 = cur?.type_options ?? [];

  const controls = (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <label className="flex items-center gap-1 text-muted-foreground">
          信号类型（库里筛）：
          <select value={signalType} aria-label="信号类型" disabled={hits.kind === "loading"}
            onChange={e => { setSignalType(e.target.value); bo.clearAll(); }}
            className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]">
            <option value="">全部</option>
            {sel0.map(o => (
              <option key={String(o.value)} value={String(o.value)}>
                {o.label}（这一场 {o.count === undefined || o.count === null ? "—" : o.count} 只）
              </option>
            ))}
          </select>
        </label>
        {signalType && (
          <button type="button" className="text-muted-foreground hover:text-foreground"
            onClick={() => { setSignalType(""); bo.clearAll(); }}>
            回到全部三类
          </button>
        )}
        <button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
        下拉里那个数是这一场母体内实有几只（母体见下面那一行），不是「已经交出的这些行里有几只」；
        类型在库里生效、发生在取数上限之前，所以它是真筛，不是在截断后的名单里挑。
        {(cur?.filter?.signal_type_rejected ?? []).length > 0
          ? `｜这个值没被认出来、没有参与筛选：${cur?.filter?.signal_type_rejected?.join("、")}` : ""}
      </p>
    </>
  );

  return (
    <Panel title="突破信号（右侧已确认）" state={hits} controls={controls}
      note="这一栏要的是已经走出来的那一天：放量波之后缩量收敛，第一次同时站上均线并放量。买在这里叫右侧，代价是成本已经抬高。">
      {d => {
        const all = d.rows ?? [];
        const opts = d.type_options ?? [];
        const th = d.thresholds ?? {};
        const lim = d.filter?.limit;
        const labelOf = (v?: string | null) =>
          opts.find(o => o.value === v)?.label ?? v ?? null;
        return (
          <>
            <Row label="基准交易日" value={dash(d.date)} />
            <Row label="观察窗" value={`${dash(d.window_days)} 个交易日`} />
            <Row label="这一场命中"
              value={`${dash(d.day_total)} 只｜本页交出 ${all.length} 行`} />
            <Row label="母体" tone={universePartial(d) ? "warn" : undefined}
              value={universeText(d)} />
            {opts.length > 0 && (
              <Row label="各类实有几只（这一场母体内）"
                value={opts.map(o => `${o.label}: ${o.count === undefined || o.count === null ? "—" : o.count}`).join('、')} />
            )}
            {/* 用户 2026-10-21：描述性内容收进一处折叠；缺数/截断类由 CaliberFold 分流留在屏上 */}
            <CaliberFold caveats={d.caveats} definitions={d.definitions}
              coverage={d.coverage} provenance={[d.source]} />
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  <SortHeader label="代码" sortKey="code" dir={vdSort.dirOf("code")} onSort={sortBo} />
                  <SortHeader label="名称" sortKey="name" dir={vdSort.dirOf("name")} onSort={sortBo} />
                  <SortHeader label="信号类型" sortKey="signal_type_label" dir={vdSort.dirOf("signal_type_label")} onSort={sortBo}
                    title={opts.map(o => `${o.label}：${o.meaning ?? ""}`).join("\n")} />
                  <SortHeader label="突破倍数" sortKey="break_vol_ratio" dir={vdSort.dirOf("break_vol_ratio")} onSort={sortBo}
                    title={`突破那天的放量倍数（vol / 前 5 日均量），首破的门槛是 ${th.break_mult ?? "—"} 倍以上；没突破的行这一列是空的，两个方向都排最后`} />
                  <SortHeader label="突破价" sortKey="break_close" dir={vdSort.dirOf("break_close")} onSort={sortBo}
                    title="突破那天的收盘价（未复权）。突破日 MA60 在整行悬停里" />
                  <SortHeader label="突破偏离" sortKey="break_dev" dir={vdSort.dirOf("break_dev")} onSort={sortBo}
                    title={`突破那天的离线程度 |close/ma60-1|；收敛那一段要求的贴线带宽是 ${typeof th.dev_thresh === "number" ? (th.dev_thresh * 100).toFixed(0) : "—"}%，越小越是刚从均线上方起来`} />
                  <SortHeader label="收敛天数" sortKey="converge_days" dir={vdSort.dirOf("converge_days")} onSort={sortBo}
                    title={`连续 |close/ma60-1| 在贴线带内的天数（筹码沉淀了多久）；判据要求 >= ${th.converge_min_days ?? "—"} 天`} />
                  <SortHeader label="缩量天数" sortKey="shrink_days" dir={vdSort.dirOf("shrink_days")} onSort={sortBo}
                    title="从上次放量日到信号日之间，成交量 < 前 5 日均量的天数" />
                </tr></thead>
                <tbody>
                  {vdSort.apply(all).map((r, i) => (
                    <tr key={`${r.code}-${i}`} {...bo.rowProps(i, r.code, "hover:bg-primary/5", boHover(r, th))}>
                      <td {...bo.cellProps(i, "code", "whitespace-nowrap font-mono")}>
                        <StockLink code={r.code} name={null} className="hover:text-primary" />
                      </td>
                      <td {...bo.cellProps(i, "name", "whitespace-nowrap")}><StockLink code={r.code} name={r.name} className="hover:text-primary" /></td>
                      <td {...bo.cellProps(i, "signal_type_label", "whitespace-nowrap")}>{dash(r.signal_type_label ?? r.signal_type)}</td>
                      <td {...bo.cellProps(i, "break_vol_ratio", "font-mono font-semibold tabular-nums")}>
                        {typeof r.break_vol_ratio === 'number' ? `${r.break_vol_ratio.toFixed(2)}×` : dash(null)}
                      </td>
                      <td {...bo.cellProps(i, "break_close", "font-mono tabular-nums")}>
                        {typeof r.break_close === 'number' ? r.break_close.toFixed(2) : dash(null)}
                      </td>
                      <td {...bo.cellProps(i, "break_dev", "font-mono tabular-nums")}>
                        {typeof r.break_dev === 'number' ? `${(r.break_dev * 100).toFixed(2)}%` : dash(null)}
                      </td>
                      <td {...bo.cellProps(i, "converge_days", "font-mono tabular-nums")}>{dash(r.converge_days)}</td>
                      <td {...bo.cellProps(i, "shrink_days", "font-mono tabular-nums")}>{dash(r.shrink_days)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* 怎么点这一张表：通用一句（实现全站一份）；弹窗被拦的告警单独念、不折进任何默认收起的说明框 */}
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
              {bo.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{bo.popHint}</p>}
              {all.length === 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {signalType ? `选了「${labelOf(signalType) ?? signalType}」之后没有票：这一场母体内命中 ${dash(d.day_total)} 只，是这个筛条件把它们筛空了（不是库里没数）。` : "后端交出 0 行 —— 这一场没有满足三段式的票，还是加工链没跑到这一档？看上面的基准交易日。"}
                </p>
              )}
              {typeof lim === "number" && all.length >= lim && (
                <p className="mt-1 text-[10px] text-warning">
                  已达取数上限 {lim} 行：下面看到的不是这一场的全部，
                  上面「这一场命中」那个数才是母体内的总数。
                </p>
              )}
            </div>
          </>
        );
      }}
    </Panel>
  );
}

export function VolumeBreakout() {
  return (
    <div className="mx-auto max-w-[1720px]">
      <PageHeader title="突破信号" level={1}
        subtitle="左栏埋伏票（左侧机会，还没走出来）、右栏突破信号（右侧已确认）。两栏都来自本地日线，不看盘口。" />

      {/* 左右两栏并排（窄屏退回上下堆叠）：中间列能拿回的宽度靠顶栏那条 AI 栏收起，
          每栏 8 列、超宽就各自横向滚动，不共用一条横向滚动带。 */}
      <div className="grid items-start gap-3 xl:grid-cols-2">
        <AmbushBoard />
        <BreakoutBoard />
      </div>

      <Disclaimer compact />
    </div>
  );
}
