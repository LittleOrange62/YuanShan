import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { SortHeader } from "@/components/ui/SortHeader";
import { CaliberFold, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { StockLink } from "@/components/ui/StockLink";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { PAGE_SIZES, clampPage, pageCount, pageSentence, slicePage } from "@/lib/paging";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import {
  cohortLabel, coverageSentence, definitionOf, equivText, gainText, groupReading,
  launchRange, moodText, moodTone, nameIsCodeCount, numOrNull, ratioExtent, ratioSegments, ratioText,
  rowCountLine, windowShort, type Cohort, type StartupRow, type StartupSummary,
} from "@/lib/startupVolume";

/**
 * 倍量形态页 →「启动前量能分布」（用户 2026-10-07 定名：标题里不把"启动前一定放量"这种待验证的直觉
 * 当成结论写出来；也不反过来在标题里否定放量）。
 *
 * 后端已把每只票**启动日之前**那一段自己的有效日线倍数算好落库，这一屏只做三件事：
 * 母体切换、逐票读数、形状示意。界面**不解释字段含义** —— 每列悬停与「字段是什么意思」
 * 全部读接口 `definitions`，definitions 里没有的列就明说"后端没给说明"，不自己编一份。
 *
 * 三条由取数侧定下、界面上不许糊掉的红线：
 *  · 四类母体不并榜（档位不同，见 `definitions.cohort`）；跨板块比强度只读 `equiv_limit_ups`。
 *    `board4_45`（用户 2026-10-15 加）是 `board4` 的**子集**：同一批启动日，
 *    只再筛「前四根累计涨幅大于 45%」（基准=首板日前一天收盘，读数就在「前4日涨幅」那一列）。
 *    `board4_or_gain45`（用户 2026-10-17 要的是**或**）是**并集**：4 板及以上 或 启动日起 4 个交易日涨过 45%，
 *    所以双创 3 连板（1.20³−1=72.8%）、北证 2 连板（1.30²−1=69%）这些"段长不够但四天够猛"的票只在这一档里。
 *  · `bars_used < window_days` 的行整行标灰并写明不可与满窗票并排排名（次新/长期停牌）。
 *  · 这是描述统计不是买入信号：`caveats.note` 原句上屏，不缩写。
 * 另有一条 `caveats.limit_band`（主板风险警示股档位存疑 → 板数不可信）混在长列表里容易看漏，
 * 单独挑出来摆在表前，正文仍是后端原句。
 */

interface StartupPayload {
  available?: boolean; reason?: string | null;
  cohort?: string | null; cohort_text?: string | null; months?: number | null;
  window_days?: number | null; latest_launch?: string | null; spec_version?: string | null;
  /** sv-2 那批起后端交出的覆盖坐标：这一档窗口起点 / 判据表最早算到哪天 / 这一档库里共几行 */
  window_from?: string | null; profile_earliest_launch?: string | null; profile_rows_total?: number | null;
  rows_returned?: number | null; rows_total?: number | null; limit?: number | null;
  caliber?: string | null;
  /** 情绪那一列的现场坐标：读了几天、缺几天、权重与各档极值（值本身在后端 pg_sources） */
  mood?: { days?: number | null; missing?: number | null; failed?: boolean | null;
           weights?: Record<string, number> | null; extremes?: Record<string, number> | null;
           source?: string | null } | null;
  definitions?: Record<string, string> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  summary?: StartupSummary | null;
  rows?: StartupRow[] | null;
}

const MONTHS = [3, 6, 12];

/** 走势示意：只表形状。断点是"那天没有效日线"，绝不连成 0。 */
function Spark({ row }: { row: StartupRow }) {
  const pts = row.ratios ?? [];
  const ext = ratioExtent(pts);
  if (!pts.length || !ext) {
    return <span className="text-[10px] text-muted-foreground/60" title="后端没交逐日倍数序列">无序列</span>;
  }
  const W = 84, H = 20, PAD = 2;
  const span = ext.hi - ext.lo || 1;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / Math.max(1, pts.length - 1);
  const y = (v: number) => H - PAD - ((v - ext.lo) / span) * (H - 2 * PAD);
  const segs = ratioSegments(pts).map(s =>
    pts.slice(s.from, s.to + 1).map((p, k) => `${x(s.from + k)},${y(numOrNull(p.v) as number)}`).join(" "));
  const first = pts[0]?.d ?? "", last = pts[pts.length - 1]?.d ?? "";
  const caption = `${first} ~ ${last} 共 ${pts.length} 根，最高 ${ratioText(ext.hi)}／最低 ${ratioText(ext.lo)}（断线＝那天没有效日线，不补 0）`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-5 w-20 shrink-0" role="img" aria-label={caption}>
      {/* svg 不吃 title 属性，用 <title> 子元素，悬停才有这句说明 */}
      <title>{caption}</title>
      {/* 倍数 1.0 那条参考线：等于 1 就是"量和上一根一样"，没有它整条线的高低无从判断 */}
      {ext.lo < 1 && ext.hi > 1 && (
        <line x1={PAD} x2={W - PAD} y1={y(1)} y2={y(1)} stroke="currentColor" strokeOpacity={0.28} strokeDasharray="2 2" />
      )}
      {segs.map((points, i) => <polyline key={i} points={points} fill="none" stroke="currentColor"
        strokeOpacity={0.85} strokeWidth={1.2} />)}
    </svg>
  );
}

export function StartupVolumeBoard() {
  const [nonce, refresh] = useNonce();
  const [cohort, setCohort] = useState<Cohort>("board4");
  const [months, setMonths] = useState(3);
  const [onlyFullWindow, setOnlyFullWindow] = useState(false);
  /** 这张表曾经**不分页**：后端 limit 200，一行 31 格 ⇒ 一次单击要重建约 6,200 个 td。
   *  分页用全站那一份 `lib/paging.ts`（一页 25 行），不在这里各写一遍 ceil/slice。 */
  const [page, setPage] = useState(1);
  /** 单击交叉高亮 / 双击开深度复盘：全站一份实现（`lib/useStockRowInteraction.ts`），
   *  原来这里另写了一份 `selectedCell` state 加本地 `cellCls(...)`，已删。 */
  const x = useStockRowInteraction("启动前量能画像");
  const svSort = useTableSort();
  const state = useProbe<StartupPayload>("/quant/startup-volume", nonce, { cohort, months, limit: 200 });

  return (
    <div className="mt-8">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">启动前量能分布</h2>
        <Caliber text={
          "每只票看的是它启动日之前那一段自己的有效日线倍数；完整口径是表下那句「这一屏的取数口径（后端原句）」，页面不改写。\n" +
          "四类母体的涨跌停档位不同，所以这一屏没有合计榜（board4_45 是 board4 的子集、board4_or_gain45 是两者的并集，不是另一种票）；跨板块比强度读「等效涨停」那一列。\n" +
          "「情绪」那一格是启动日那天的全市场环境读数（−100~100），同一场所有票同一个数，只用来读环境不比强弱。"
        } />
        <button type="button" onClick={() => { x.clearAll(); refresh(); }} title="重新取数"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      <GlassCard className="mb-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-muted-foreground">母体</span>
            {([
              { key: "board4", label: "4 板及以上" },
              { key: "board4_45", label: "4 板及以上 · 前 4 日涨幅>45%" },
              { key: "board4_or_gain45", label: "4 板及以上 或 4 日涨幅>45%" },
              { key: "jump40", label: "4 日涨幅超四成（双创／北证）" },
            ] as const).map(c => (
              <button key={c.key} type="button" onClick={() => { setCohort(c.key); x.clearAll(); setPage(1); }}
                aria-pressed={cohort === c.key}
                className={cn("rounded-lg px-2 py-1 text-[11px]",
                  cohort === c.key ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
                {c.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-muted-foreground">回溯</span>
            {MONTHS.map(m => (
              <button key={m} type="button" onClick={() => { setMonths(m); x.clearAll(); setPage(1); }} aria-pressed={months === m}
                className={cn("rounded-lg px-2 py-1 text-[11px]",
                  months === m ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
                近 {m} 个月
              </button>
            ))}
          </div>
        </div>
        {/* 这里刻意不给"合并两类母体"这个选项，也不写死门槛数字：档位差异由 definitions.cohort 说 */}
        <ExplainBox title="为什么这一屏没有合计榜 · 跨板块比强度看哪一列">
          <p>
            一次只看一类母体，<b className="text-foreground">没有合计榜</b> ——
            两类的涨跌停档位不同（各自的说明见「字段是什么意思」的 <code>cohort</code> 条），
            并到一列排名就是拿一个档位的尺子量另一个档位的票。要跨板块比强度，看「等效涨停」那一列。
          </p>
        </ExplainBox>
      </GlassCard>

      <Panel title="启动前逐日量能比对" state={state}
        note="行数、窗口根数、母体说明都由后端交出，页面不自己数。">
        {d => {
          const all = d.rows ?? [];
          const shortRows = all.filter(r => windowShort(r) === true);
          const nameOnly = nameIsCodeCount(all);
          const rows = onlyFullWindow ? all.filter(r => windowShort(r) !== true) : all;
          const sorted = svSort.apply(rows);
          const pageSize = PAGE_SIZES[0];
          const pages = pageCount(sorted.length, pageSize);
          const cur = clampPage(page, pages);
          const paged = slicePage(sorted, cur, pageSize);
          const reading = groupReading(d.summary, numOrNull(d.window_days));
          const hasMinBars = (d.caveats ?? []).some(c => c.code === "min_bars");
          const noDef = (k: string) => definitionOf(d.definitions, k)
            ?? `后端 definitions 里没有 ${k} 这一条 —— 这一列的含义没有出处，页面不自己编`;
          const Th = (label: string, key: string) => (
            <SortHeader label={label} sortKey={key} dir={svSort.dirOf(key)}
              onSort={() => { x.clearAll(); setPage(1); svSort.toggle(key); }} title={noDef(key)} />
          );
          return (
            <>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {cohortLabel(String(d.cohort ?? cohort), d.cohort_text)}
                　窗口 {dash(d.window_days)} 根有效日线／只，回溯 {dash(d.months)} 个月，
                最后一场启动日 {dash(d.latest_launch)}，判据版本 {dash(d.spec_version)}。{rowCountLine(d)}
              </p>
              {/* sv-2 起后端交出窗口起点与表内最早启动日，这一行因此能说准话而不是"分不出" */}
              <p className="text-[11px] leading-relaxed text-muted-foreground/80">
                {coverageSentence(launchRange(all), d)}
              </p>
              {/* 整组汇总：后端 summary 算好的，界面不按行再算一遍（行被 limit 截断过，页面算出来的不是母体） */}
              <div className="mt-1 rounded-lg border border-border/60 bg-muted/20 p-2">
                <p className={cn("text-[12px] leading-relaxed", reading.tone === "warn" ? "text-warning" : "text-foreground")}>
                  {reading.text}
                </p>
                {d.summary && Object.keys(d.summary).length > 0 ? (
                  <>
                    <ul className="mt-1.5 grid gap-x-4 gap-y-0.5 text-[11px] sm:grid-cols-2 lg:grid-cols-3">
                      {Object.entries(d.summary).map(([k, v]) => (
                        <li key={k} className="flex items-baseline justify-between gap-2 border-b border-border/20">
                          <span className="font-mono text-muted-foreground">{k}</span>
                          <span className="font-mono tabular-nums">{v === null || v === undefined ? "—" : String(v)}</span>
                        </li>
                      ))}
                    </ul>
                    <ExplainBox title="上面那些整组读数的键是什么 · 谁算的 · 为什么不由界面算">
                      <p className="text-[11px] leading-relaxed text-muted-foreground">
                        上面这些是后端 summary 的原键原值（页面没有再算一遍，也没换算单位）。
                        倍数 1.0 = 当日量与上一根一样；这批键后端还没逐条写进 definitions，要更细的口径得问取数侧。
                        整组数与下面逐票榜是同一批过滤条件，但榜被上限截断时，逐票那几行不等于整组的样本。
                      </p>
                    </ExplainBox>
                  </>
                ) : (
                  <p className="mt-1 text-[11px] text-warning">后端这次没交 summary —— 整组读数这一屏说不出，界面不自己按行凑一个。</p>
                )}
              </div>
              {/* 名称列里出现"名字就是代码"的行时，读者会以为页面截断了或链接坏了，所以点数要摆在榜外 */}
              {nameOnly.length > 0 && (
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  名称列里有 {nameOnly.length} 只的"名字"就是代码本身
                  （{nameOnly.slice(0, 5).join("、")}{nameOnly.length > 5 ? " 等" : ""}）——
                  本地股票字典里没登记它的中文名，不是这一屏截断了名字，也不是链接坏了。
                </p>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                <label className="flex items-center gap-1">
                  <input type="checkbox" checked={onlyFullWindow}
                    onChange={e => { setOnlyFullWindow(e.target.checked); x.clearAll(); setPage(1); }} />
                  只看满窗票（窗口不满的整行本来就已标灰）
                </label>
                {shortRows.length > 0 && (
                  <span className="text-warning">
                    本表 {shortRows.length} 只窗口不满（往前数不够 {dash(d.window_days)} 根，多为次新或长期停牌）——
                    {/* "分布列为空是口径"这句只在后端真给了 min_bars 那条时引用；没给就不替它说话 */}
                    {hasMinBars && <>凑不满判据下限的那几只，八个分布读数按口径直接留空（下面 <code>min_bars</code> 那条是后端原话），</>}
                    <b className="text-foreground">这些行都不与满窗票并排排名</b>。
                  </span>
                )}
                {onlyFullWindow && shortRows.length > 0 && (
                  <span>已按勾选滤掉 {shortRows.length} 只，表里剩 {rows.length} 只 —— 母体总数仍是 {dash(d.rows_total)}。</span>
                )}
              </div>
              {(d.caveats ?? []).filter(c => c.code === "limit_band").map(c => (
                <p key={c.code} className="mt-2 rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-[11px] leading-relaxed text-destructive">
                  <b>这一条要摆在表前看（后端原句，未改写）：</b>{plain(c.text)}
                </p>
              ))}
              {/* 声明与字段义字典收成一处默认收起的折叠块（用户 2026-10-21：
                  「把页面上的描述性内容全部收缩起来，避免过多占屏」—— 字段义原先单独摊在榜下面，
                  与上面这串声明是同一类东西，分两处各占一段）。
                  **没跟着折的**：上面那行「名字就是代码」的点数、`limit_band` 那条单独摆在榜前的
                  后端原句（它是 `text-destructive` 的档位存疑声明）、表里逐行的「不满」徽标、
                  `x.popHint`；缺数／截断一族的 caveat 由折叠块自己留在屏上。 */}
              <CaliberFold caveats={(d.caveats ?? []).filter(c => c.code !== "limit_band")}
                definitions={d.definitions} />

              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      {Th("代码", "code")}{Th("名称", "name")}{Th("板块", "board")}{Th("启动日", "launch_date")}
                      {/* 用户 2026-10-15：启动日后面加一列「情绪」（−100~100 的大盘与环境读数），依据在列名悬停 */}
                      {Th("情绪", "mood_score")}
                      {Th("连板数", "trigger_len")}{Th("区间涨幅", "trigger_gain")}
                      {Th("前4日涨幅", "gain_first4")}{Th("等效涨停", "equiv_limit_ups")}
                      {Th("用了几根", "bars_used")}
                      {Th("增", "up_days")}{Th("减", "down_days")}{Th("持平", "flat_days")}{Th("量没变", "still_days")}
                      {Th("倍量", "dbl_days")}{Th("准倍量", "near_dbl_days")}{Th("缩量", "calm_days")}{Th("复牌间隔", "long_gap_days")}
                      {/* 用户 2026-10-11 要的 5 个倍量区间统计 */}
                      <th className="px-2 py-1 font-medium whitespace-normal break-words w-[48px] min-w-[48px]"
                        title="1.6~1.8 倍的天数（窗口内当日量÷上一根量落在这个区间的日子）">1.6<br/>~1.8</th>
                      <th className="px-2 py-1 font-medium whitespace-normal break-words w-[48px] min-w-[48px]"
                        title="1.4~1.6 倍的天数">1.4<br/>~1.6</th>
                      <th className="px-2 py-1 font-medium whitespace-normal break-words w-[48px] min-w-[48px]"
                        title="1.2~1.4 倍的天数">1.2<br/>~1.4</th>
                      <th className="px-2 py-1 font-medium whitespace-normal break-words w-[48px] min-w-[48px]"
                        title="1.0~1.2 倍的天数">1.0<br/>~1.2</th>
                      <th className="px-2 py-1 font-medium whitespace-normal break-words w-[48px] min-w-[48px]"
                        title="0.8~1.0 倍的天数">0.8<br/>~1.0</th>
                      {Th("均值", "ratio_avg")}{Th("中位", "ratio_median")}{Th("60 分位", "ratio_p60")}
                      {Th("80 分位", "ratio_p80")}{Th("90 分位", "ratio_p90")}{Th("最大", "ratio_max")}
                      {Th("最小", "ratio_min")}{Th("波动", "std_days")}
                      <th className="whitespace-nowrap px-2 py-1 font-medium" title="形状示意，不参与排序">走势</th>
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((r, i) => {
                      const short = windowShort(r) === true;
                      return (
                        <tr key={`${r.code}-${r.launch_date ?? ""}-${i}`}
                          {...x.rowProps(i, r.code, short ? "bg-muted/30 text-muted-foreground" : undefined,
                            short ? `窗口不满：只用了 ${dash(r.bars_used)} 根（窗口 ${dash(r.window_days)} 根），不与满窗票并排排名` : undefined)}>
                          <td {...x.cellProps(i, "code", "whitespace-nowrap font-mono")}>{dash(r.code)}</td>
                          <td {...x.cellProps(i, "name", "whitespace-nowrap")}><StockLink code={r.code} name={r.name} className="hover:text-primary" /></td>
                          <td {...x.cellProps(i, "board", "whitespace-nowrap")}>{dash(r.board)}</td>
                          <td {...x.cellProps(i, "launch_date", "font-mono tabular-nums")}>{dash(r.launch_date)}</td>
                          {/* 情绪：启动日那天的全市场环境读数。缺值必须与 0 分得开 —— 0 是「中性」，
                              空是"那天没行/两档缺一档"，所以缺的时候把后端那句 missing 挂在悬停上。 */}
                          <td {...x.cellProps(i, "mood_score", "font-mono tabular-nums")}
                            title={[r.mood_parts || "", r.mood_missing || "",
                              d.mood?.failed ? "整批读不回来：档位表查询失败" : "",
                              "同一场全市场同一个值，只读环境不读强弱"].filter(Boolean).join("\n")}>
                            <span className={cn(moodTone(r.mood_score) === "hot" && "text-primary font-semibold",
                              moodTone(r.mood_score) === "cold" && "text-destructive",
                              moodTone(r.mood_score) === "flat" && "text-muted-foreground",
                              moodTone(r.mood_score) === "none" && "text-muted-foreground/60")}>
                              {moodText(r.mood_score)}
                            </span>
                          </td>
                          <td {...x.cellProps(i, "trigger_len", "font-mono tabular-nums")}>{dash(r.trigger_len)}</td>
                          <td {...x.cellProps(i, "trigger_gain", "font-mono tabular-nums")}>{gainText(r.trigger_gain)}</td>
                          <td {...x.cellProps(i, "gain_first4", "font-mono tabular-nums")}>{gainText(r.gain_first4)}</td>
                          <td {...x.cellProps(i, "equiv_limit_ups", "font-mono font-semibold tabular-nums")}>{equivText(r.equiv_limit_ups)}</td>
                          <td {...x.cellProps(i, "bars_used", "whitespace-nowrap font-mono tabular-nums")}>
                            {dash(r.bars_used)}
                            {short && <span className="ml-1 rounded bg-warning/15 px-1 text-[10px] text-warning">不满</span>}
                          </td>
                          <td {...x.cellProps(i, "up_days", "font-mono tabular-nums")}>{dash(r.up_days)}</td>
                          <td {...x.cellProps(i, "down_days", "font-mono tabular-nums")}>{dash(r.down_days)}</td>
                          <td {...x.cellProps(i, "flat_days", "font-mono tabular-nums")}>{dash(r.flat_days)}</td>
                          <td {...x.cellProps(i, "still_days", "font-mono tabular-nums")}>{dash(r.still_days)}</td>
                          <td {...x.cellProps(i, "dbl_days", "font-mono font-semibold tabular-nums")}>{dash(r.dbl_days)}</td>
                          <td {...x.cellProps(i, "near_dbl_days", "font-mono tabular-nums")}>{dash(r.near_dbl_days)}</td>
                          <td {...x.cellProps(i, "calm_days", "font-mono tabular-nums")}>{dash(r.calm_days)}</td>
                          <td {...x.cellProps(i, "long_gap_days", cn("font-mono tabular-nums", (r.long_gap_days ?? 0) > 0 && "text-warning"))}>{dash(r.long_gap_days)}</td>
                          {/* 5 个倍量区间统计列 */}
                          <td {...x.cellProps(i, "range_16_18", "font-mono tabular-nums")}>{dash(r.range_16_18)}</td>
                          <td {...x.cellProps(i, "range_14_16", "font-mono tabular-nums")}>{dash(r.range_14_16)}</td>
                          <td {...x.cellProps(i, "range_12_14", "font-mono tabular-nums")}>{dash(r.range_12_14)}</td>
                          <td {...x.cellProps(i, "range_10_12", "font-mono tabular-nums")}>{dash(r.range_10_12)}</td>
                          <td {...x.cellProps(i, "range_08_10", "font-mono tabular-nums")}>{dash(r.range_08_10)}</td>
                          <td {...x.cellProps(i, "ratio_avg", "font-mono tabular-nums")}>{ratioText(r.ratio_avg)}</td>
                          <td {...x.cellProps(i, "ratio_median", "font-mono tabular-nums")}>{ratioText(r.ratio_median)}</td>
                          <td {...x.cellProps(i, "ratio_p60", "font-mono tabular-nums")}>{ratioText(r.ratio_p60)}</td>
                          <td {...x.cellProps(i, "ratio_p80", "font-mono tabular-nums font-semibold")}>{ratioText(r.ratio_p80)}</td>
                          <td {...x.cellProps(i, "ratio_p90", "font-mono tabular-nums")}>{ratioText(r.ratio_p90)}</td>
                          <td {...x.cellProps(i, "ratio_max", "font-mono tabular-nums")}>{ratioText(r.ratio_max)}</td>
                          <td {...x.cellProps(i, "ratio_min", "font-mono tabular-nums")}>{ratioText(r.ratio_min)}</td>
                          <td {...x.cellProps(i, "std_days", "font-mono tabular-nums")}>{ratioText(r.std_days)}</td>
                          <td className="px-2 py-1 text-muted-foreground"><Spark row={r} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {/* 分页那句与按钮全站一份算法（`lib/paging.ts`）：换页要清行选中，
                    因为高亮是按**页内行号**记的，不清就会把上一页选中的那一格映到本页同一行号上。 */}
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="text-muted-foreground">{pageSentence(sorted.length, pageSize, cur)}</span>
                  {cur > 1 && <button type="button" onClick={() => { setPage(cur - 1); x.clearAll(); }}
                    className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>}
                  {cur < pages && <button type="button" onClick={() => { setPage(cur + 1); x.clearAll(); }}
                    className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>}
                </div>
                {/* 怎么点这一张表：通用一句（实现全站一份），收进默认收起的说明框；
                    被拦告警（x.popHint）是告警，留在框外。 */}
                <ExplainBox title="这一张表怎么点 · 单击高亮什么 · 双击去哪里">
                  <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
                </ExplainBox>
                {x.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{x.popHint}</p>}
                {rows.length === 0 && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {onlyFullWindow ? "这一场里没有满窗的票（全是往前数不够的次新/停牌票）—— 不是母体空了。"
                      : "后端交出 0 行。"}
                  </p>
                )}
              </div>

              {d.caliber && (
                <ExplainBox title="这一屏的取数口径（后端原句，页面未改写）">
                  <p className="text-[10px] leading-relaxed text-muted-foreground">{plain(d.caliber)}</p>
                </ExplainBox>
              )}
            </>
          );
        }}
      </Panel>
    </div>
  );
}
