import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { SortHeader } from "@/components/ui/SortHeader";
import { Caveats, Definitions, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import {
  agreeText, agreeTone, columnsFor, coverageText, direction, fmtKeyValue, headline, intText,
  observedText, rangeText, rateText, signedText,
  type FbOption, type FbPayload, type FbRow,
} from "@/lib/firstBoardShape";

/**
 * 倍量形态页 →「首板形状预示：能走到几板 · 进场划不划算」
 * （用户 2026-10-17 点名：先做一稿**可以调参数**的计算界面，再按实际读数改）。
 *
 * 这一块存在的理由是上面那屏答不了的问题：`StartupVolumeBoard` 的母体是"已经走出 4 板"，
 * 那是**回头看**才成立的条件，它那一列"启动后收益"含着头几板白送的一段
 * （同一批实测：从启动日收盘起算 5 场 +32%，从真能下单那天开盘起算 −6.3%、胜率 37%）。
 * 这里的母体是**每一个连板段的第一天** —— 当天收盘就知道、当天就能决定明早买不买，
 * 所以"后来走到几板"在这里是**结果**而不是筛子。
 *
 * 三条界面上不许糊掉的线：
 *  · 分档切点是**当前这批数据现算**的：换窗口、换板别之后同一个 Q1 指的是另一段，
 *    所以每档必须把区间与实测值一起念出来，不许只写 Q1/Q2。
 *  · `year_agree` 那串"6/6"是"**与整族同向**的年份数"，不是"6 年都赚钱"（几乎每档的超额都是负的，
 *    比 0 会什么都算稳）；界面按 `agreeText` 原样翻译，不美化。
 *  · 缺就是 `—`：北交所没有对应分类指数 ⇒ 超额那一列空。0 会被读成"没赚"，那是相反的话。
 *
 * 所有参数都走接口（切点、分档、逐年都在库里算）：改一个控件就重发一次请求，界面不做二次计算。
 */

const DEFAULTS = { months: 60, bucket: "dbl_days", nBuckets: 4, metric: "ex5",
                   board: "", completeOnly: true, excludeLocked: true };

const FALLBACK = { months: [{ value: 60, label: "近 60 个月" }],
                   buckets: [{ value: "dbl_days", label: "启动前倍量天数" }],
                   metrics: [{ value: "ex5", label: "进场后 5 场（超额）" }],
                   boards: [{ value: "", label: "全部板别" }],
                   n_buckets: [{ value: 4, label: "分 4 档" }] } as const;

function Select({ label, value, onChange, options, title }: {
  label: string; value: string | number; onChange: (v: string) => void;
  options: readonly FbOption[]; title?: string;
}) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-muted-foreground" title={title}>
      {label}
      <select value={String(value)} onChange={e => onChange(e.target.value)}
        className="rounded border border-border/60 bg-background px-1.5 py-0.5 text-[11px] text-foreground">
        {options.map(o => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function FirstBoardShapeBoard() {
  const [p, setP] = useState(DEFAULTS);
  const [nonce, refresh] = useNonce();
  const [openYear, setOpenYear] = useState<string | null>(null);
  const state = useProbe<FbPayload>("/quant/first-board-shape", nonce, {
    months: p.months, bucket: p.bucket, buckets: p.nBuckets, metric: p.metric, board: p.board,
    complete_only: String(p.completeOnly), exclude_locked: String(p.excludeLocked), min_bars: 20 });
  const { toggle, dirOf, apply } = useTableSort();
  const { openReview, popHint } = useStockRowInteraction("倍量形态·首板形状预示");

  const data = state.kind === "ok" ? state.data : null;
  const opts = data?.options;
  const metricLabel = (opts?.metrics ?? FALLBACK.metrics).find(m => m.value === p.metric)?.label ?? p.metric;
  const mk = `${p.metric}_med`;
  const buckets = data?.buckets ?? [];
  const cols = columnsFor(p.metric);
  const sorted = apply(buckets);
  const dirSeg4 = direction(buckets, "seg4_rate");
  const dirMetric = direction(buckets, mk);
  const rows: FbRow[] = data?.base ? [data.base, ...sorted] : sorted;

  return (
    <Panel title="首板形状预示：能走到几板 · 次日开盘进场划不划算"
      note="母体＝每一个连板段的第一天（当天可构造、真能下单），段长是结果不是筛子"
      slowHint="切点与逐年都在库里现算：第一发约 1~3 秒（5 年、约 6 万条首板事件）"
      state={state} contract="/api/quant/first-board-shape"
      controls={<div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Select label="窗口" value={p.months} options={opts?.months ?? FALLBACK.months}
          title="窗口越短样本越少；换窗口之后每档的切点会重算"
          onChange={v => setP(s => ({ ...s, months: Number(v) }))} />
        <Select label="板别" value={p.board} options={opts?.boards ?? FALLBACK.boards}
          onChange={v => setP(s => ({ ...s, board: v }))} />
        <Select label="分桶列" value={p.bucket} options={opts?.buckets ?? FALLBACK.buckets}
          title={data?.definitions?.bucket}
          onChange={v => setP(s => ({ ...s, bucket: v }))} />
        <Select label="档数" value={p.nBuckets} options={opts?.n_buckets ?? FALLBACK.n_buckets}
          onChange={v => setP(s => ({ ...s, nBuckets: Number(v) }))} />
        <Select label="结果指标" value={p.metric} options={opts?.metrics ?? FALLBACK.metrics}
          title="能连板（左半边几列）与能赚钱（这一列）是两件事，分开看"
          onChange={v => setP(s => ({ ...s, metric: v }))} />
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
          title={data?.definitions?.seg2_rate}>
          <input type="checkbox" checked={p.completeOnly}
            onChange={e => setP(s => ({ ...s, completeOnly: e.target.checked }))} />
          只数走完的段
        </label>
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
          title={data?.definitions?.locked_rate}>
          <input type="checkbox" checked={p.excludeLocked}
            onChange={e => setP(s => ({ ...s, excludeLocked: e.target.checked }))} />
          剔掉次日一字（买不进）
        </label>
        <button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
          <RefreshCw size={11} />重算一次
        </button>
      </div>}>
      {() => {
      if (!data) return null;
      return <>
      {data.available === false && (
        <p className="text-[12px] text-warning">{data.reason || "后端没交出读数"}</p>
      )}
      {data.available && (
        <>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {coverageText(data)}。当前分桶：{data.params?.bucket_label}；结果指标：{metricLabel}。
            一句话方向：<b className="text-foreground">≥4 板率{dirSeg4.word}</b>
            {dirSeg4.span !== null && `（头尾差 ${dirSeg4.span.toFixed(1)} 个百分点）`}；
            <b className="text-foreground">{metricLabel}{dirMetric.word}</b>
            {dirMetric.span !== null && `（头尾差 ${dirMetric.span.toFixed(2)}）`}。
            {headline(buckets, data.base, "seg4_rate", "≥4 板率")}
          </p>
          {/* 「这一张表怎么点 · 双击去哪里」那句是描述，收进默认收起的说明框
              （用户 2026-10-21：「把页面上的描述性内容全部收缩起来，避免过多占屏」）；
              `popHint` 是弹窗被拦的**告警**，留在框外单独念，不跟着折 */}
          <ExplainBox title="这一张表怎么点 · 双击去哪里">
            <p className="text-[10.5px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
          </ExplainBox>
          {!!popHint && <p role="status" className="mt-1 text-[10.5px] leading-relaxed text-warning">{popHint}</p>}
          <div className="mt-2 overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  {cols.map(c => (
                    <SortHeader key={c.key} label={c.label} sortKey={c.key} dir={dirOf(c.key)}
                      onSort={toggle} title={data.definitions?.[c.key]} className="" />
                  ))}
                  <th className="whitespace-nowrap px-1 py-1 text-right">逐年</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const isBase = r.label === "全部首板";
                  const key = `${r.label}-${i}`;
                  return (
                    <tr key={key} className="border-b border-border/40">
                      <td className="whitespace-nowrap px-1 py-1">
                        {isBase
                          ? <b>全部首板（基线）</b>
                          : <>
                            <b className="text-foreground">{r.label}</b>
                            <span className="text-muted-foreground"> {rangeText(r)}</span>
                          </>}
                        {!isBase && !!observedText(r) && (
                          <div className="text-[10px] text-muted-foreground">{observedText(r)}</div>
                        )}
                      </td>
                      <td className="px-1 py-1 tabular-nums">{intText(r.n)}</td>
                      <td className="px-1 py-1 tabular-nums">{rateText(r.seg2_rate)}</td>
                      <td className="px-1 py-1 tabular-nums">{rateText(r.seg3_rate)}</td>
                      <td className="px-1 py-1 tabular-nums"><b className="text-foreground">{rateText(r.seg4_rate)}</b></td>
                      <td className="px-1 py-1 tabular-nums">{rateText(r.seg5_rate)}</td>
                      <td className="px-1 py-1 tabular-nums">{fmtKeyValue(mk, r[mk])}</td>
                      <td className="px-1 py-1 tabular-nums">{rateText(r.win5)}</td>
                      <td className="px-1 py-1 tabular-nums">{signedText(r.r5_med)}</td>
                      <td className="px-1 py-1 tabular-nums">{signedText(r.low5_med)}</td>
                      <td className="px-1 py-1 tabular-nums">{rateText(r.locked_rate)}</td>
                      <td className="px-1 py-1 text-right">
                        {isBase
                          ? <span className="text-[10px] text-muted-foreground">整族逐年见下</span>
                          : (
                            <button type="button"
                              onClick={() => setOpenYear(openYear === key ? null : key)}
                              title={agreeText(r.year_agree)}
                              className={`text-[10px] underline decoration-dotted ${agreeTone(r.year_agree) === "good" ? "text-danger" : agreeTone(r.year_agree) === "mid" ? "text-warning" : "text-muted-foreground"}`}>
                              {agreeText(r.year_agree)}
                            </button>
                          )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {(() => {
            const idx = openYear ? Number(openYear.split("-").pop()) : -1;
            const row = idx >= 0 ? buckets[idx] : null;
            if (!row) return null;
            return (
              <div className="mt-1 rounded border border-dashed border-border/60 bg-muted/20 p-2 text-[11px]">
                <b>{row.label}（{rangeText(row)}）逐年 —— 年年同向才叫规律，一年独大不是</b>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  {(row.per_year ?? []).map(y => (
                    <span key={y.year} className="text-muted-foreground">
                      {y.year}：n={intText(y.n)}，≥4 板 {rateText(y.seg4_rate)}，{metricLabel} {fmtKeyValue(mk, y[mk])}
                    </span>
                  ))}
                </div>
                <div className="mt-1 text-[10.5px] text-muted-foreground">
                  整族同年：{(data.years ?? []).map(y => `${y.year.slice(2)}年 ${rateText(y.seg4_rate)}`).join("、")}
                </div>
                <div className="mt-1.5 text-[10.5px] text-muted-foreground">
                  这一档最近 6 条（双击一行开个股深度复盘）：
                </div>
                <div className="flex flex-wrap gap-x-2 gap-y-1">
                  {(row.samples ?? []).map(s => (
                    <button key={`${s.code}-${s.launch_date}`} type="button"
                      onDoubleClick={() => openReview(s.code)}
                      title="双击：另开一个浏览器窗口看这一只的「个股深度复盘」"
                      className="rounded border border-border/50 px-1.5 py-0.5 text-left">
                      <b className="font-mono">{s.code}</b> {s.name ?? ""} {s.launch_date}
                      <span className="block text-[10px] text-muted-foreground">
                        {s.seg_len} 板{s.seg_complete ? "" : "（还在走）"} · 值 {s.value ?? "—"} ·
                        5 场 {signedText(s.r5)} · 超额 {signedText(s.ex5)}{s.locked ? " · 次日一字买不进" : ""}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })()}
          <Caveats items={(data.caveats ?? []).map((t, i) => ({ code: `fb${i}`, text: t }))} />
          <Caliber text={data.caliber ?? ""} />
          <Definitions map={data.definitions} />
          <ExplainBox title="这一块与上面那屏的分工（为什么要两块）">
            <p>上面「启动前量能分布」的母体是<b>已经走出 4 板</b>的那批票 —— 它回答"这种票启动前长什么样"，
            但它的前提要回头看才成立，所以它那一列"启动后收益"里含着头几板白送的一段
            （同一批实测：从启动日收盘起算 5 场 +32%，从真能下单那天开盘起算 −6.3%、胜率 37%）。</p>
            <p className="mt-1">这一块的母体是<b>每一个连板段的第一天</b>：那天收盘就知道它是首板，
            当天就能决定明早买不买。于是"后来走到几板"在这里是<b>结果</b>。
            两问的答案方向是相反的：<b>倍量天数越多越容易续板，但进场后的收益反而更差</b>
            （五年、{intText(data.base?.n)} 条样本上，≥4 板率从第一档到最后一档{dirSeg4.word}，
            而{metricLabel}反而{dirMetric.word}）—— 所以"能连板"不等于"买得到还赚得到"。</p>
            <p className="mt-1 text-muted-foreground">分桶切点、逐年同向、剔掉买不进的比例都由库里现算；
            界面只排版，不重算任何一个数。每列含义悬停即读接口原文（上方「这些字段是什么意思」）。</p>
          </ExplainBox>
        </>
      )}
      </>;
      }}
    </Panel>
  );
}
