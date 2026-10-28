import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { SortHeader } from "@/components/ui/SortHeader";
import { CaliberFold, ExplainBox, Row } from "@/components/ui/DataPanel";
import { Caliber } from "@/components/ui/Caliber";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { num } from "@/lib/qd";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";

/**
 * 趋势评分页的「自选股新高」这一屏（用户 2026-10-07：「趋势评分——这里面新增一个历史新高
 * （针对自选分组里的自选股）」）。
 *
 * 标题按数据现实叫**库内新高**、不叫"历史新高"：本机日线最早 2021-08-02（PG 与通达信本地
 * `.day` 同一起点，10-07 实测），而 `reference_data.security.list_date` 全表为空 ——
 * 上市日拿不到，写"历史新高"就是拿数据的边界当事实卖。每行的 `hist_truncated` 说得更细：
 * 为真 = "这票在库起点之前就有历史，更早的价格本机看不到"；为假 = 库里覆盖了它在库里的
 * 全部日线（仍不等于上市以来）。
 *
 * 判据、复权口径、母体、缓存年龄全部来自后端 `duanxian/quant.py::new_high()`；
 * 这一格只转述 —— 不自己算新高、不自己减百分比、不自己排"谁最接近新高"。
 */

interface HighRow {
  code?: string | null; name?: string | null; is_st?: boolean | null;
  bars?: number | null; first_date?: string | null; last_date?: string | null;
  close?: number | null; high?: number | null; pct_change?: number | null;
  peak_close_adj?: number | null; peak_high_adj?: number | null;
  peak_close_date?: string | null; peak_close_raw?: number | null;
  peak_high_date?: string | null; peak_high_raw?: number | null;
  pct_from_peak?: number | null; pct_from_high_peak?: number | null;
  new_high_close?: boolean | null; new_high_intraday?: boolean | null;
  traded_on_date?: boolean | null; hist_truncated?: boolean | null;
  bars_without_factor?: number | null;
}
interface NewHigh {
  available?: boolean | null; reason?: string | null; date?: string | null;
  scope?: string | null; group_id?: number | null; group_name?: string | null;
  group_count?: number | null;
  window?: string | number | null; window_label?: string | null; history_from?: string | null;
  universe_total?: number | null; universe_with_data?: number | null; universe_without_data?: number | null;
  new_high_in_rows?: number | null; rows_returned?: number | null; rows_total?: number | null;
  cache_age_seconds?: number | null; limit?: number | null;
  rows?: HighRow[] | null; caliber?: string | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}
interface Groups {
  available?: boolean | null; reason?: string | null;
  groups?: { group_id?: number | null; name?: string | null; stock_count?: number | null }[] | null;
}

const WIN_CHOICES = [["all", "库内全部（≈五年）"], ["250", "近 250 天"], ["500", "近 500 天"],
  ["750", "近 750 天"], ["1250", "近 1,250 天"]] as const;

/** 慢在哪、为什么值得等：第一次要扫自选里上千只票 × 五年日线，之后一小时走进程内缓存。 */
const SLOW_HINT = "这一屏第一次要现扫（自选里上千只票 × 本机五年日线，实测十几秒）；"
  + "同一范围与窗口一小时内二次打开走进程内缓存，缓存年龄随数据一起交出，要立刻看新数点「重新取数」。";

/** 状态列的文字。**排序键要用格子里读到的这串**，所以先算出来再交给 `useTableSort`
 *  （死键不报错也不换序，10-06 那种"点了标题没反应"就是这么来的）。 */
function stateText(r: HighRow): string {
  if (!r.traded_on_date) return "目标日没有行情";
  if (r.new_high_close) return r.new_high_intraday ? "收盘新高·盘中也新高" : "收盘新高";
  if (r.new_high_intraday) return "盘中新高·收盘未及";
  return typeof r.pct_from_peak === "number" ? `差 ${Math.abs(r.pct_from_peak).toFixed(2)}%` : "距峰算不出";
}

export function NewHighBoard() {
  const [nonce, refresh] = useNonce();
  const [groupId, setGroupId] = useState("");
  const [win, setWin] = useState<string>("all");
  const groups = useProbe<Groups>("/watchlist/groups", nonce);
  const sort = useTableSort();
  // 单击交叉高亮 / 双击开深度复盘：全站一份实现（`lib/useStockRowInteraction.ts`）。
  const x = useStockRowInteraction("自选股历史新高");
  // 换排序＝换行序，高亮按行号记着，不清就会漂到别的票身上（连同弹窗告警一起清）。
  const sortBy = (k: string) => { x.clearAll(); sort.toggle(k); };
  const state = useProbe<NewHigh>("/quant/new-high", nonce,
    { ...(groupId ? { group_id: groupId } : {}), window: win, limit: 300 });
  const d = state.kind === "ok" ? state.data : null;
  const rows = (d?.rows ?? []).map(r => ({ ...r, state_text: stateText(r) }));

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-bold">自选股新高（库内，不是上市以来）</h2>
        <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]",
          d && d.new_high_in_rows ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>
          {d ? `本页创新高 ${dash(d.new_high_in_rows)} 只` : "取数中"}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <label className="text-[11px] text-muted-foreground" htmlFor="nh-group">范围</label>
          <select id="nh-group" value={groupId} onChange={e => { x.clearAll(); setGroupId(e.target.value); }}
            className="max-w-[15rem] rounded-lg border border-border bg-background px-2 py-1 text-[11px]">
            <option value="">全部自选{d ? `（${dash(d.universe_total)} 只）` : ""}</option>
            {(groups.kind === "ok" ? groups.data.groups ?? [] : []).map(g => (
              <option key={String(g.group_id)} value={String(g.group_id)}>
                {g.name}（{dash(g.stock_count)} 只）
              </option>
            ))}
          </select>
          <label className="text-[11px] text-muted-foreground" htmlFor="nh-window">窗口</label>
          <select id="nh-window" value={win} onChange={e => { x.clearAll(); setWin(e.target.value); }}
            className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]">
            {WIN_CHOICES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <button type="button" onClick={() => { x.clearAll(); refresh(); }}
            className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
            <RefreshCw className="h-3.5 w-3.5" /> 重新取数
          </button>
        </div>
      </div>

      {state.kind === "loading" && (
        <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">取数中…　{SLOW_HINT}</p>
      )}
      {(state.kind === "error" || state.kind === "offline") && (
        <p className="mt-2 text-[12px] leading-relaxed text-destructive">{state.reason}</p>
      )}
      {state.kind === "not_wired" && (
        <p className="mt-2 text-[12px] leading-relaxed text-warning">
          新高这一屏要的接口 <code>/api/quant/new-high</code> 还没接上（HTTP 404）。
          这不是"今天没有新高"，是这一格还没有数据来源，页面不放任何示例数字。
        </p>
      )}
      {/* 后端打 `available:false` 时不套 `Panel` 那句「本地计算尚未覆盖该项」：
          这一格最常见的假因是"自选清单空 / 分组不存在 / 窗口参数不对"，
          后端三句各写各的，套上那句反而把真原因盖掉（所以这里用 GlassCard 自己画）。 */}
      {d && d.available === false && (
        <p className="mt-2 text-[12px] leading-relaxed text-warning">{dash(d.reason)}</p>
      )}
      {d && d.available !== false && (<>
        <Row label="范围 / 有数" value={
          `${d.scope === "group" ? `分组「${dash(d.group_name)}」` : "全部自选"}`
          + `：${dash(d.universe_total)} 只，窗口内有条线 ${dash(d.universe_with_data)} 只，`
          + `一根都没有 ${dash(d.universe_without_data)} 只（停牌/退市/没入库，不会静默丢掉）`} />
        <Row label="窗口 / 历史深度" value={
          `${dash(d.window_label)}；本机日线最早 ${dash(d.history_from)} —— 所以这是「库内新高」，`
          + "不是上市以来新高（上市日字段 list_date 全表为空，通达信本地 .day 文件也是同一天起）"} />
        <Row label="取出 / 截断" value={
          `按「是否新高 → 距峰由近到远」排序后取前 ${dash(d.limit)} 行（本轮交回 ${dash(d.rows_returned)} 行，母体 ${dash(d.rows_total)} 行）；`
          + "没列出来的那些是离库内高点更远，不是没算"} />
        {typeof d.cache_age_seconds === "number" && (
          <Row label="缓存" value={`这份数实算于 ${d.cache_age_seconds.toFixed(0)} 秒前（一小时内重复打开走缓存）`} />
        )}
        <Caliber text={d.caliber ?? ""} />
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead><tr className="text-left text-muted-foreground">
              <SortHeader label="名称" sortKey="name" dir={sort.dirOf("name")} onSort={sortBy} />
              <SortHeader label="代码" sortKey="code" dir={sort.dirOf("code")} onSort={sortBy} />
              <SortHeader label="现价" sortKey="close" dir={sort.dirOf("close")} onSort={sortBy} />
              <SortHeader label="当日涨跌" sortKey="pct_change" dir={sort.dirOf("pct_change")} onSort={sortBy} />
              <SortHeader label="距库内最高" sortKey="pct_from_peak" dir={sort.dirOf("pct_from_peak")} onSort={sortBy}
                title="现价（后复权）÷ 窗口内最高收盘价 − 1，0 就是新高；负数是还差几个点。分母是复权后的数，不是屏幕上这个现价" />
              <SortHeader label="状态" sortKey="state_text" dir={sort.dirOf("state_text")} onSort={sortBy} />
              <SortHeader label="最高日" sortKey="peak_close_date" dir={sort.dirOf("peak_close_date")} onSort={sortBy} />
              <SortHeader label="峰那天收盘" sortKey="peak_close_raw" dir={sort.dirOf("peak_close_raw")} onSort={sortBy}
                title="后复权最高的那一天的**未复权**收盘价。它和左边「现价」不可以直接比大小：中间只要有一次除权送转，两者就不在同一个基准上（实测 688808 现价 1,546、峰那天 2,700，距库内最高其实只有 −15.22%）。要比高低看「距库内最高」那一列" />
              <SortHeader label="K线根数" sortKey="bars" dir={sort.dirOf("bars")} onSort={sortBy} />
            </tr></thead>
            <tbody>
              {sort.apply(rows).map((r, i) => (
                <tr key={String(r.code)} {...x.rowProps(i, r.code, "border-t border-border/40")}>
                  <td {...x.cellProps(i, "name", "whitespace-nowrap")}>{dash(r.name)}
                    {r.is_st ? <span className="ml-1 text-[10px] text-warning">ST</span> : null}</td>
                  <td {...x.cellProps(i, "code", "whitespace-nowrap font-mono text-muted-foreground")}
                    title={`库里第一根 K 线在 ${dash(r.first_date)}，最后 ${dash(r.last_date)}`}>{dash(r.code)}</td>
                  <td {...x.cellProps(i, "close", "font-mono tabular-nums")}>{num(r.close, 2)}</td>
                  <td {...x.cellProps(i, "pct_change", cn("font-mono tabular-nums", pctColor(r.pct_change ?? null)))}>{num(r.pct_change, 2)}</td>
                  <td {...x.cellProps(i, "pct_from_peak", cn("font-mono tabular-nums", pctColor(r.pct_from_peak ?? null)))}>{num(r.pct_from_peak, 2)}</td>
                  <td {...x.cellProps(i, "state_text", "whitespace-nowrap")}>
                    <span className={cn("text-[10px]",
                      r.new_high_close ? "rounded bg-primary/15 px-1 text-primary" : "text-muted-foreground")}>
                      {r.state_text}
                    </span>
                    {r.hist_truncated === false && (
                      <span className="ml-1 text-[10px] text-muted-foreground"
                        title="库里从它第一根 K 线起就有 —— 仍不等于上市以来（本机没有上市日字段）">全史在库</span>)}
                    {typeof r.bars_without_factor === "number" && r.bars_without_factor > 0 && (
                      <span className="ml-1 text-[10px] text-warning"
                        title="这些交易日的复权因子在库里是空值，按 1 参与计算">缺因子 {r.bars_without_factor}</span>)}
                  </td>
                  <td {...x.cellProps(i, "peak_close_date", "whitespace-nowrap font-mono text-muted-foreground")}
                    title={`盘中最高（复权）出现在 ${dash(r.peak_high_date)}`}>{dash(r.peak_close_date)}</td>
                  <td {...x.cellProps(i, "peak_close_raw", "font-mono tabular-nums text-muted-foreground")}>{num(r.peak_close_raw, 2)}</td>
                  <td {...x.cellProps(i, "bars", "font-mono tabular-nums")}>{dash(r.bars)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* 弹窗被拦是**告警**，不折进下面默认收起的说明框；"怎么点"那句描述随下面这块一起收（用户 2026-10-19 夜：
            「把选项留出，描述内容都收缩起来」） */}
        {x.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{x.popHint}</p>}
        {rows.length === 0 && (
          <p className="mt-1 text-[11px] leading-relaxed text-warning">
            后端这条查询正常返回，但一行都没给 —— 这个范围与窗口里没有"有行情可比"的票
            （不是"没有新高"，是连一根 K 线都没有）。
          </p>
        )}
        {/* 后端声明 + 那一句"慢在哪、缓存多久"的读法说明收成一处默认收起的折叠块
            （用户 2026-10-21：「把页面上的描述性内容全部收缩起来，避免过多占屏」——
            这句 SLOW_HINT 与取数中那一行是同一句话，重复占着表下两行）。
            **没跟着折的**：上面「取出 / 截断」那行读数、缓存年龄那行、`rows.length === 0` 与
            `available:false` 两句告警、`x.popHint`；缺数／截断一族的 caveat 由折叠块自己留在屏上。 */}
        <CaliberFold caveats={d.caveats} notes={[SLOW_HINT]} />
        <ExplainBox title="这一格的出处 · 判据住在哪一处 · 这一张表怎么点">
          <p>
            接口：<code>GET /api/quant/new-high</code>；判据只住 <code>duanxian/quant.py::new_high()</code> 一处，
            这一格只转述 —— 页面不自己算新高、不自己减百分比。
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
        </ExplainBox>
      </>)}
    </GlassCard>
  );
}
