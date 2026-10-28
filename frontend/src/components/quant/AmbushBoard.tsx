import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { StockLink } from "@/components/ui/StockLink";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";

/**
 * 左栏「埋伏票」（左侧机会）。右栏那半边是已经确认的突破（等它走出来了才买），
 * 这一边要的是**还没走出来的那种**：各均线挤在一起、横得紧、周线重心一档一档抬高。
 *
 * 三条不能糊过去的：
 * ① 类型多选**由后端筛**（拼在取数上限之前）。在前端筛就只能筛到合成分最高的那 150 只，
 *    "这一类剩 3 行"会被读成"全场只有 3 只"（实测全场 106 只）—— 与倍量榜四维象限同一条规矩；
 * ② 每个类型后面那个数是**全场**的（来自 `type_options.count`），不是"已交出的行里有几只"；
 * ③ 缺数显示 `—`，不当 0：极差 0% 会被读成"完美粘合"，缩量 0 会被读成"极端缩量"，方向正好相反。
 *
 * 阈值与判据都在后端一份（`app/compute/ambush_setup.py`），页面只把 `thresholds` 念出来。
 */

interface AmbushRow {
  code?: string | null; name?: string | null; board?: string | null;
  close_hfq?: number | null; ma_spread_pct?: number | null; range20_pct?: number | null;
  weekly_ups6?: number | null; weekly_streak?: number | null; weekly_bars?: number | null;
  week_sessions_cur?: number | null; week_partial?: boolean | null;
  shrink_ratio?: number | null; box_pos_pct?: number | null; trend60_pct?: number | null;
  obv_ratio?: number | null; days_since_big?: number | null; hold_vs_big_pct?: number | null;
  ambush_types?: string[] | null; ambush_type_labels?: string[] | null;
  strict_pass?: boolean | null; ambush_score?: number | null;
  bars_60?: number | null; missing?: string | null; big_day_date?: string | null;
  dev_ma20_pct?: number | null; dev_ma60_pct?: number | null;
  spec_version?: string | null; calculated_at?: string | null;
}
interface AmbushData {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  spec_version?: string | null; rows?: AmbushRow[] | null;
  filter?: { types?: string[] | null; types_rejected?: string[] | null;
             only_strict?: boolean | null; min_weekly_ups?: number | null;
             rows_returned?: number | null; limit?: number | null } | null;
  type_options?: { value?: string | null; label?: string | null;
                   count?: number | null; meaning?: string | null }[] | null;
  thresholds?: Record<string, number> | null;
  day_total?: number | null; day_strict_total?: number | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

/** 缺数就是 null，界面显示 —；0 是一个有意义的值（极差 0% = 完全重合），不能拿来顶"没读到" */
const cell = (v: number | null | undefined, dp = 2, suffix = "") =>
  typeof v === "number" ? `${v.toFixed(dp)}${suffix}` : dash(null);

export function AmbushBoard() {
  const [nonce, refresh] = useNonce();
  const [types, setTypes] = useState<string[]>([]);
  const [onlyStrict, setOnlyStrict] = useState(false);
  const [date, setDate] = useState("");
  const hits = useProbe<AmbushData>(`/quant/ambush?date=${encodeURIComponent(date)}`
    + `&types=${encodeURIComponent(types.join(","))}&only_strict=${onlyStrict}`, nonce);
  const srt = useTableSort();
  /** 单击格 → 整行＋整列交叉高亮、双击行 → 另开窗口看个股深度复盘：全站一份实现
   *  （`lib/useStockRowInteraction.ts`，用户 2026-10-15）。原来这份 `sel` state 只做整行
   *  高亮（没有明细行可展开），这一半交给共用 hook 后删掉；点行展开的行为本来就是高亮本身。 */
  const am = useStockRowInteraction("埋伏榜");
  const sortAm = (k: string) => { am.clearAll(); srt.toggle(k); };

  // 控件那一行画在 Panel 外面（走 `controls`），所以这里直接从状态里取数：
  // 取不到数（在转、或这一场 available:false）时 opts/th 是空的，但日期框和按钮必须还在。
  const cur = hits.kind === "ok" ? hits.data : null;
  const opts = cur?.type_options ?? [];
  const th = cur?.thresholds ?? {};

  const controls = (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <span className="text-muted-foreground">埋伏类型（多选，库里筛）：</span>
        {opts.length === 0 && (
          <span className="text-muted-foreground">（这一场的类型清单没取到，先把日期改回去或重新取数）</span>
        )}
        {opts.map(o => (
          <label key={String(o.value)} className="flex items-center gap-1"
            title={[o.meaning,
                    o.count === undefined || o.count === null
                      ? "这一类全场实有几只：没读到"
                      : `这一类全场 ${o.count} 只`].filter(Boolean).join("｜")}>
            <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={busy(hits)}
              checked={types.includes(String(o.value ?? ""))}
              aria-label={`埋伏类型 ${o.label}`}
              onChange={() => {
                const v = String(o.value ?? "");
                setTypes(t => (t.includes(v) ? t.filter(x => x !== v) : [...t, v]));
                am.clearAll();
              }} />
            {o.label}
            <span className="tabular-nums text-muted-foreground">
              {o.count === undefined || o.count === null ? "—" : o.count}
            </span>
          </label>
        ))}
        <label className="flex items-center gap-1"
          title={`只留严档三条全过的票：均线极差 < ${th.spread_max ?? "—"}%、`
            + `20 日高低差 < ${th.range20_max ?? "—"}%、周线重心近 6 个完整周里 >= ${th.weekly_ups6_min ?? "—"} 周抬升`
            + `（且满 ${th.weekly_min_bars ?? "—"} 个完整周）`}>
          <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={busy(hits)}
            checked={onlyStrict} aria-label="只看严档"
            onChange={e => { setOnlyStrict(e.target.checked); am.clearAll(); }} />
          只看严档
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          日期：
          <input type="date" value={date} onChange={e => { setDate(e.target.value); am.clearAll(); }}
            className="w-28 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
        </label>
        {(types.length > 0 || onlyStrict || date) && (
          <button type="button" className="text-muted-foreground hover:text-foreground"
            onClick={() => { setTypes([]); setOnlyStrict(false); setDate(""); am.clearAll(); }}>
            回到不限（含日期回到最近一场）
          </button>
        )}
        <button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
        每个类型后面那个数是全场实有几只，不是「已经交出的这些行里有几只」；
        类型勾选在库里生效、发生在取数上限之前，所以它是真筛，不是在截断后的名单里挑。
        {(cur?.filter?.types_rejected ?? []).length > 0
          ? `｜这几个值没被认出来、没有参与筛选：${cur?.filter?.types_rejected?.join("、")}` : ""}
      </p>
    </>
  );

  return (
    <Panel title="埋伏票（左侧机会）" state={hits} controls={controls}
      note="这一栏不等突破：看的是各周期成本挤到一起、横得紧、周线重心还在慢慢抬高。买在这里叫左侧，没走出来就是耗时间。">
      {d => {
        const all = d.rows ?? [];
        return (
          <>
            <Row label="基准交易日" value={dash(d.date)} />
            <Row label="判据版本" value={dash(d.spec_version)} />
            <Row label="这一场命中"
              value={`${dash(d.day_total)} 只（其中严档 ${dash(d.day_strict_total)} 只）｜本页交出 ${all.length} 行`} />
            {/* 声明 / 字段含义 / 覆盖 / 出处收成一处默认收起的折叠块（用户 2026-10-21：
                「把页面上的描述性内容全部收缩起来，避免过多占屏」）。summary 摆每一类的条数 ⇒ 收起不等于藏掉；
                缺数／截断／查询失败那一族声明由 `CaliberFold` 内部留在屏上，下面那句取数上限告警也没跟着折。 */}
            <CaliberFold caveats={d.caveats} definitions={d.definitions}
              coverage={d.coverage} provenance={[d.source, d.caliber]} />
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-muted-foreground">
                  <SortHeader label="代码" sortKey="code" dir={srt.dirOf("code")} onSort={sortAm} />
                  <SortHeader label="名称" sortKey="name" dir={srt.dirOf("name")} onSort={sortAm} />
                  <SortHeader label="埋伏类型" sortKey="ambush_type_labels" dir={srt.dirOf("ambush_type_labels")} onSort={sortAm}
                    title="一栏可以同场命中多个类型；点这一列按类型名把同类票归到一起（要按合成分排就点「埋伏分」）" />
                  <SortHeader label="均线极差" sortKey="ma_spread_pct" dir={srt.dirOf("ma_spread_pct")} onSort={sortAm}
                    title={`MA5/10/20/30/60 里最高与最低之差 ÷ 最低那根。越小 = 各周期成本越趋同。严档门槛 ${th.spread_max ?? "—"}%；全市场中位约 6.5%（实测 2026-10-12）`} />
                  <SortHeader label="20日高低差" sortKey="range20_pct" dir={srt.dirOf("range20_pct")} onSort={sortAm}
                    title={`近 20 日 (最高-最低) ÷ 当日后复权收盘。横得紧的量尺；严档门槛 ${th.range20_max ?? "—"}%`} />
                  <SortHeader label="周线抬升" sortKey="weekly_ups6" dir={srt.dirOf("weekly_ups6")} onSort={sortAm}
                    title="近 6 个完整周里重心（周均后复权价）比上一周高的周数。不完整周（节假日周只有 2~4 场）不计入" />
                  <SortHeader label="缩量比" sortKey="shrink_ratio" dir={srt.dirOf("shrink_ratio")} onSort={sortAm}
                    title={`近 5 场（含当日）均量 ÷ 第 6~60 场均量（SQL 里的 v5/v60p，判据只住 app/compute/ambush_setup.py）。<1 是缩量；严档用 ${th.shrink_max ?? "—"} 以下`} />
                  <SortHeader label="埋伏分" sortKey="ambush_score" dir={srt.dirOf("ambush_score")} onSort={sortAm}
                    title="只为排序的合成分（0~100，不是涨跌概率）" />
                </tr></thead>
                <tbody>
                  {srt.apply(all).map((r, i) => (
                    <tr key={`${r.code}-${i}`} {...am.rowProps(i, r.code, "hover:bg-primary/5", hover(r, th))}>
                      <td {...am.cellProps(i, "code", "whitespace-nowrap font-mono")}>
                        <StockLink code={r.code} name={null} className="hover:text-primary" />
                      </td>
                      <td {...am.cellProps(i, "name", "whitespace-nowrap")}><StockLink code={r.code} name={r.name} className="hover:text-primary" /></td>
                      <td {...am.cellProps(i, "ambush_type_labels", "whitespace-nowrap")}>
                        {(r.ambush_type_labels ?? []).join("、") || dash(null)}
                        {r.strict_pass && <span className="ml-1 rounded bg-primary/15 px-1 text-[10px] text-primary">严</span>}
                      </td>
                      <td {...am.cellProps(i, "ma_spread_pct", "font-mono tabular-nums")}>{cell(r.ma_spread_pct)}</td>
                      <td {...am.cellProps(i, "range20_pct", "font-mono tabular-nums")}>{cell(r.range20_pct, 1)}</td>
                      <td {...am.cellProps(i, "weekly_ups6", "font-mono tabular-nums")}>
                        {typeof r.weekly_ups6 === "number" ? `${r.weekly_ups6}/6` : dash(null)}
                      </td>
                      <td {...am.cellProps(i, "shrink_ratio", "font-mono tabular-nums")}>{cell(r.shrink_ratio)}</td>
                      <td {...am.cellProps(i, "ambush_score", "font-mono font-semibold tabular-nums")}>{cell(r.ambush_score, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* 怎么点这一张表：通用一句（实现全站一份）收进默认收起的说明框（用户 2026-10-19 夜：
                  「把选项留出，描述内容都收缩起来」）；弹窗被拦的告警是**告警**，留在框外单独念 */}
              <ExplainBox title="这一张表怎么点 · 单击高亮什么 · 双击去哪里">
                <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
              </ExplainBox>
              {am.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{am.popHint}</p>}
              {all.length === 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {types.length > 0 || onlyStrict
                    ? "当前筛选条件下这一场没有票（不是库里没有埋伏票：上面「这一场命中」那行是全场的数）。"
                    : "这一场后端交出 0 行 —— 埋伏链今天没跑，还是这张表还没回算到这一档？看上面的基准交易日。"}
                </p>
              )}
              {typeof d.filter?.limit === "number" && all.length >= d.filter.limit && (
                <p className="mt-1 text-[10px] text-warning">
                  已达取数上限 {d.filter.limit} 行：下面看到的不是这一场命中的全部，
                  上面「这一场命中」那个数才是全场。
                </p>
              )}
            </div>
          </>
        );
      }}
    </Panel>
  );
}

const busy = (state: { kind?: string }) => state?.kind === "loading";

/** 悬停：把砍掉的那几列（箱体位置、60 日涨幅、距大阳、连升周数、本周场数、缺数）原样说全。
 *  口径句子取后端 `caliber`，前端不自己重写一份。 */
function hover(r: AmbushRow, th: Record<string, number>) {
  const bits = [
    `后复权收盘 ${cell(r.close_hfq, 3)}｜60 日窗口里真有 ${dash(r.bars_60)} 根 K 线`
      + (typeof r.bars_60 === "number" && r.bars_60 < (th.bars_min ?? 60) ? "（不足判据线，本行按不判处理）" : ""),
    `箱体位置 ${cell(r.box_pos_pct, 1)}%（0 贴下沿、100 贴上沿）｜60 日涨幅 ${cell(r.trend60_pct, 1)}%`,
    `离 MA20 ${cell(r.dev_ma20_pct)}%｜离 MA60 ${cell(r.dev_ma60_pct)}%`,
    `周线：完整周 ${dash(r.weekly_bars)} 个、连升 ${dash(r.weekly_streak)} 周、近 6 周抬升 ${dash(r.weekly_ups6)} 周`
      + `；当前周 ${dash(r.week_sessions_cur)} 场${r.week_partial ? "（不完整，不计入连升）" : ""}`,
    typeof r.days_since_big === "number"
      ? `最近一根放量大阳/涨停：${r.big_day_date ?? "—"}，距今 ${r.days_since_big} 场，现价相对那根收盘 ${cell(r.hold_vs_big_pct, 1)}%`
      : "最近 60 场内没有放量大阳或涨停",
    typeof r.obv_ratio === "number" ? `近 20 日量价背离度 ${cell(r.obv_ratio, 3)}` : "量价背离度没读到",
    r.missing ? `缺的读数：${r.missing}（界面显示 —，不当 0）` : "读数齐全",
  ];
  return bits.filter(Boolean).join("\n");
}
