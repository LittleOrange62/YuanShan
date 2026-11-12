import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { CaliberFold, Panel, Row } from "@/components/ui/DataPanel";
import { DrawerSection } from "@/components/ui/DocDrawer";
import { NoticeBar } from "@/components/ui/NoticeBar";
import { SortHeader } from "@/components/ui/SortHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { plain } from "@/lib/agent";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { dash } from "@/lib/datastate";
import { num } from "@/lib/qd";
import { cn } from "@/lib/utils";

/**
 * 近期各量化方案的名次与趋势带动（用户 2026-11-09 点名新增，挂在「该支票的趋势量化计算」上方）。
 *
 * 原话两问：①「把这个个股的近五个交易日 得分、名次 都展示一下，包括左侧机会等」；
 * ②「各个量化评分的趋势，展示每个指标近2／5／10／30／60日趋势的连带情况 ——
 *    数字越小的日期范围的趋势在带动数字大的趋势」。
 *
 * 这一块只做展示与对照，全部读数由 `duanxian/scheme_radar.py` 一处算好交出：
 * 名次母体是三把尺（每格自带 `universe`）、能不能排名次先判后念（`rank_kind`）、
 * 带动用长期斜率自己的标准误归一（`z`）。页面不重算、不补 0、不把四族合成一个总分。
 *
 * ⚠️ 排序键的纪律：只有 `sortKey` 等于那一格真正读的那个字段名才排得动。
 * 场次矩阵的日期列是**展示格**（一格四样数），没有单一可排序的字段，所以那些列头是普通 `<th>`
 * 而不是 SortHeader —— 挂着排序控件却排不动，比没有控件更坏（这条踩过两次）。
 */

interface Cell {
  date: string;
  state: "ok" | "not_selected" | "no_value" | "out_of_calendar" | string;
  value?: number | null;
  rank?: number | null;
  share?: number | null;
  universe?: number | null;
  quadrant?: string | null;
  stale?: boolean | null;
  distinct_values?: number | null;
  tie_at_value?: number | null;
  biggest_tie?: number | null;
  rank_kind?: string | null;
  rank_note?: string | null;
  rank_x?: number | null;
  rank_y?: number | null;
  types?: string | null;
  strict_pass?: boolean | null;
}
interface WindowTrend {
  window: number;
  points_used: number;
  window_days: number;
  min_points: number;
  usable: boolean;
  sparse: boolean;
  no_se: boolean;
  slope?: number | null;
  r2?: number | null;
  slope_se?: number | null;
  from?: string | null;
  to?: string | null;
  why?: string | null;
}
interface Carry {
  short: number;
  long: number;
  adjacent: boolean;
  state: string;
  label: string;
  z?: number | null;
  delta?: number | null;
  turns_left?: number | null;
  long_prev_from?: string | null;
  long_prev_to?: string | null;
}
interface Delta { date: string; rank?: number | null; delta?: number | null; gap?: boolean | null }
interface Series {
  key: string;
  label: string;
  family: string;
  family_label: string;
  unit: string;
  direction: string;
  says: string;
  capability_note?: string | null;
  cells: Cell[];
  trend: WindowTrend[];
  carry: Carry[];
  rank_deltas: Delta[];
  points_available: number;
  calendar_days: number;
}
interface Radar {
  available?: boolean | null;
  reason?: string | null;
  code?: string | null;
  as_of?: string | null;
  sessions?: number | null;
  sessions_default?: number | null;
  sessions_min?: number | null;
  sessions_max?: number | null;
  windows?: number[] | null;
  spec_version?: string | null;
  source?: string | null;
  series?: Series[] | null;
  carry_states?: Record<string, string> | null;
  columns?: { key: string; label: string; says: string }[] | null;
  rules?: Record<string, string> | null;
  guide?: { heading: string; body: string }[] | null;
  definitions?: Record<string, string> | null;
  thresholds?: Record<string, unknown> | null;
  min_points_rule?: string | null;
  names_source?: string | null;
  calendar?: { sessions?: number | null; from?: string | null; to?: string | null } | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  generated_at?: string | null;
  cached?: boolean | null;
  cache_age_seconds?: number | null;
  cache_ttl?: number | null;
  elapsed_ms?: number | null;
  caveats?: { code?: string | null; label?: string | null; text?: string | null }[] | null;
  /** 四态各自的只数与各自的句子（都住后端，屏上那一排缺数声明按这里逐态念） */
  state_counts?: Record<string, number> | null;
  cell_states?: Record<string, string> | null;
}

const STATE_WORD: Record<string, string> = {
  ok: "",
  not_selected: "没入选",
  no_value: "没读数",
  out_of_calendar: "库里没这场",
};

/** 带动那一格的颜色只表示方向：短期与长期当前方向一致＝暖、不一致＝警示、没动静或算不出＝灰。
 *  好坏留给红绿（全站一种颜色一个意思），这里不掺。
 *  `turned_over` 是短期把长期推过了 0（推完两档同向）⇒ 暖；`turned_own` 是长期自己跨过了 0
 *  （Δ 不朝短期方向，来自窗口滑走的老点）⇒ 不许念成带动，走警示色。词表与判据都住后端。 */
const CARRY_TONE: Record<string, string> = {
  accelerating: "text-primary",
  hint: "text-primary/70",
  dragging_slow: "text-warning",
  turned_over: "text-primary",
  turned_own: "text-warning",
  turned_flat: "text-muted-foreground",
  opposite_stuck: "text-muted-foreground",
  silent: "text-muted-foreground",
  unknown: "text-muted-foreground/60",
};

const pairKey = (s: number, l: number) => `p${s}_${l}`;
const winKey = (w: number) => `w${w}`;

/** 一行一族：把后端那三个数组摊成**格子里读的那个字段名本身**，
 *  这样 `sortKey` 与那一格读的不是"看起来像"的两个键，而是同一个。 */
function buildRows(series: Series[], windows: number[]) {
  return series.map(s => {
    const byW = new Map(s.trend.map(t => [t.window, t]));
    const carry = new Map(s.carry.map(c => [pairKey(c.short, c.long), c]));
    const last = s.cells[s.cells.length - 1];
    const row: Record<string, unknown> = {
      key: s.key,
      label: s.label,
      family_label: s.family_label,
      unit: s.unit,
      says: s.says,
      points_available: s.points_available,
      calendar_days: s.calendar_days,
      latest_value: last?.state === "ok" ? last.value ?? null : null,
      latest_rank: last?.state === "ok" ? last.rank ?? null : null,
      cells: s.cells,
      trend: s.trend,
      carry: s.carry,
    };
    windows.forEach(w => {
      const t = byW.get(w);
      row[winKey(w)] = t?.usable ? t.slope ?? null : null;
    });
    windows.forEach((sw, i) => windows.slice(i + 1).forEach(lw => {
      const c = carry.get(pairKey(sw, lw));
      row[pairKey(sw, lw)] = c && c.state !== "unknown" ? c.z ?? null : null;
    }));
    return row;
  });
}

function stateBadge(c: Cell) {
  if (c.state === "ok") return null;
  return (
    <span className="text-[10px] text-muted-foreground">
      {STATE_WORD[c.state] ?? c.state}
    </span>
  );
}

/** 一格四样数：分值／名次（带母体）／本屏分位／比上一场几名。
 *  母体必须跟着名次一起念，缺数按状态念词而不是 —。 */
function MatrixCell({ c, prev }: { c: Cell | undefined; prev: Cell | undefined }) {
  if (!c) return <td className="text-muted-foreground/60">—</td>;
  if (c.state !== "ok") {
    return (
      <td className="whitespace-nowrap align-top">
        {stateBadge(c)}
        {c.state === "not_selected" && (
          <p className="text-[9px] leading-tight text-muted-foreground/70">这一场它不在那批入选票里</p>
        )}
      </td>
    );
  }
  const dl = prev && prev.state === "ok" && c.rank != null && prev.rank != null ? prev.rank - c.rank : null;
  const warn = c.rank_kind === "degenerate" || c.rank_kind === "coarse";
  return (
    <td className="whitespace-nowrap align-top" title={c.rank_note || undefined}>
      <div className="font-mono tabular-nums">{num(c.value ?? null, 2)}</div>
      <div className={cn("font-mono text-[10px] tabular-nums", warn ? "text-warning" : "text-muted-foreground")}>
        {c.rank == null ? "名次 —" : `第 ${c.rank}／${c.universe == null ? "—" : c.universe.toLocaleString()} 只`}
        {c.tie_at_value != null && c.tie_at_value > 1 ? `｜并列 ${c.tie_at_value}` : ""}
      </div>
      <div className="font-mono text-[10px] tabular-nums text-muted-foreground/80">
        {c.share == null ? "分位 —" : `前 ${c.share}%`}
        {dl != null ? `｜${dl >= 0 ? "▲" : "▼"}${Math.abs(dl)}` : ""}
        {c.stale === true ? "｜旧值" : ""}
        {c.quadrant ? `｜${c.quadrant}` : ""}
      </div>
    </td>
  );
}

function TrendTable({ rows, windows, thresholds, states }: {
  rows: Record<string, unknown>[];
  windows: number[];
  thresholds: Record<string, unknown> | null | undefined;
  states: Record<string, string>;
}) {
  const sort = useTableSort();
  const pairs: [number, number][] = [];
  windows.forEach((sw, i) => windows.slice(i + 1).forEach(lw => pairs.push([sw, lw])));
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-left text-muted-foreground">
            <SortHeader label="量化方案" sortKey="label" dir={sort.dirOf("label")} onSort={sort.toggle}
              title="这一行是哪一套读数；括号里的族说它出自哪一类表" />
            {windows.map(w => (
              <SortHeader key={w} label={`近${w}场斜率`} sortKey={winKey(w)} dir={sort.dirOf(winKey(w))}
                onSort={sort.toggle}
                title={`斜率＝最近 ${w} 场这段日历上做最小二乘，单位是每场动多少（${"分／档／名各按各的量纲"}）；`
                  + "缺数与点数不足门槛的交 —，不是 0。按这一列排的就是按那格读的同一个字段。"} />
            ))}
            {pairs.map(([sw, lw]) => (
              <SortHeader key={`${sw}-${lw}`} label={`${sw}→${lw} 的 z`} sortKey={pairKey(sw, lw)}
                dir={sort.dirOf(pairKey(sw, lw))} onSort={sort.toggle}
                title={`短期近${sw}场的方向，把长期近${lw}场的斜率挪动了多少个「长期斜率自己的标准误」：`
                  + `z 的分母是长期档的 se，不是短期档的。|z| ≥ ${String(thresholds?.z_visible ?? 1)} 才算看得见，`
                  + `≥ ${String(thresholds?.z_hint ?? 0.5)} 记有苗头。算不出交 —，不猜一个状态。`} />
            ))}
            <SortHeader label="有值的场" sortKey="points_available" dir={sort.dirOf("points_available")}
              onSort={sort.toggle}
              title="整条回看日历里这一族有几个场交得出值：埋伏分这类只收入选票的族会明显小于日历长度" />
          </tr>
        </thead>
        <tbody>
          {sort.apply(rows).map(r => {
            const trend = r.trend as WindowTrend[];
            const carry = r.carry as Carry[];
            const byW = new Map(trend.map(t => [t.window, t]));
            const byPair = new Map(carry.map(c => [pairKey(c.short, c.long), c]));
            return (
              <tr key={String(r.key)} className="border-t border-border/40 align-top">
                <td className="whitespace-nowrap">
                  <div className="text-foreground">{String(r.label)}</div>
                  <div className="text-[9px] text-muted-foreground/70">{String(r.family_label)}</div>
                </td>
                {windows.map(w => {
                  const t = byW.get(w);
                  return (
                    <td key={w} className="whitespace-nowrap font-mono tabular-nums"
                      title={t?.usable
                        ? `用掉 ${t.points_used}/${t.window_days} 场（门槛 ${t.min_points}）｜`
                          + `r² ${t.r2 == null ? "—" : t.r2}｜se ${t.slope_se == null ? "—" : t.slope_se}`
                          + (t.sparse ? "｜稀疏：这条线只由那几个点撑著" : "")
                          + (t.no_se ? "｜两点定一条线，没有标准误" : "")
                        : (t?.why ?? "这一档窗口交不出斜率")}>
                      {t?.usable
                        ? <>
                          <span className={pctClass(t.slope)}>{num(t.slope ?? null, 3)}</span>
                          <span className="ml-1 text-[9px] text-muted-foreground/70">
                            r{t.r2 == null ? "—" : t.r2}
                            {t.points_used < t.window_days ? `·${t.points_used}点` : ""}
                            {t.no_se ? "·无se" : ""}
                          </span>
                        </>
                        : <span className="text-muted-foreground/60">—</span>}
                    </td>
                  );
                })}
                {pairs.map(([sw, lw]) => {
                  const c = byPair.get(pairKey(sw, lw));
                  return (
                    <td key={`${sw}-${lw}`} className={cn("whitespace-nowrap text-[10px]", CARRY_TONE[c?.state ?? "unknown"])}
                      title={c ? `${c.label}｜Δ ${c.delta == null ? "—" : c.delta}（长期斜率自己动掉的量）`
                        + `｜距拐头 ${c.turns_left == null ? "算不出（这一档不是在把长期往回拉，或外推超过上限）" : `${c.turns_left} 场`}`
                        + `｜长期档比较的是 ${c.long_prev_from ?? "—"} → ${c.long_prev_to ?? "—"}` : "这一格没算"}>
                      {c && c.state !== "unknown"
                        ? <>{stateWord(c.state, states)}<span className="ml-1 font-mono tabular-nums">z{num(c.z ?? null, 2)}</span></>
                        : <span className="text-muted-foreground/60">—</span>}
                    </td>
                  );
                })}
                <td className="font-mono tabular-nums">
                  {String(r.points_available)}/{String(r.calendar_days)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 带动状态在屏上只念两三个字（全句在悬停与抽屉里），词表由后端 `carry_states` 交出后截断，
 *  页面不自造一套名字。 */
function stateWord(state: string, states?: Record<string, string>) {
  const full = (states ?? {})[state] ?? state;
  return full.split("：")[0];
}

function pctClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-destructive";
  if (v < 0) return "text-emerald-500";
  return "text-muted-foreground";
}

function Matrix({ d }: { d: Radar }) {
  const series = d.series ?? [];
  const dates = (series[0]?.cells ?? []).map(c => c.date);
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="whitespace-nowrap font-normal"
              title={(d.columns ?? []).find(c => c.key === "label")?.says}>量化方案</th>
            {dates.map(dt => (
              <th key={dt} className="whitespace-nowrap font-normal font-mono"
                title={`这一格三行：分值／当日名次与母体／本屏分位与比上一场。`
                  + "日期列是展示格（一格几个数），没有单一可排序的字段，所以不做成排序控件；"
                  + "要按大小排用下面那张「斜率与带动」表。"}>{dt}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {series.map(s => (
            <tr key={s.key} className="border-t border-border/40 align-top">
              <td className="whitespace-nowrap">
                <div className="text-foreground" title={s.says}>{s.label}</div>
                <div className="text-[9px] text-muted-foreground/70">
                  {s.family_label}｜{s.direction === "lower_better" ? "名次越小越好" : "越大越靠前"}
                </div>
              </td>
              {s.cells.map((c, i) => (
                <MatrixCell key={c.date} c={c} prev={i > 0 ? s.cells[i - 1] : undefined} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Board({ code, nonce }: { code: string; nonce: number }) {
  const [sessions, setSessions] = useState<number | null>(null);
  // 参数一律拼进查询串（`useProbe` 的第三参那条路不给 refresh 留位置）：
  // 按过「重新取数」却不把 refresh 发出去，那颗按钮会亮着却交回同一小时缓存 —— 这条踩过两次。
  const path = `/quant/scheme-radar?code=${encodeURIComponent(code)}`
    + (sessions != null ? `&sessions=${encodeURIComponent(String(sessions))}` : "")
    + (nonce > 0 ? "&refresh=1" : "");
  const probe = useProbe<Radar>(path, nonce);
  const payload = probe.kind === "ok" ? probe.data : null;
  const rows = useMemo(
    () => buildRows(payload?.series ?? [], payload?.windows ?? []),
    [payload],
  );
  // 上下夹与默认值读接口回显，界面不写死一份自己的。
  const lo = payload?.sessions_min ?? 2;
  const hi = payload?.sessions_max ?? 10;
  const now = payload?.sessions ?? payload?.sessions_default ?? 5;
  return (
    <Panel title="近期各量化方案的名次与趋势带动" state={probe}
      slowHint="四张已落库的表各取一条 60 场序列，再对每一族做 5 档窗口的最小二乘与 10 个窗口对的带动判定：实测（2026-11-09，600519.SH）冷取约 12 秒，之后一小时内同一组参数 0 毫秒；按「重新取数」连那层缓存一起绕过去。"
      controls={<div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="text-muted-foreground">近几场：</span>
        {Array.from({ length: hi - lo + 1 }, (_, i) => lo + i).map(n => (
          <button key={n} type="button" onClick={() => setSessions(n)}
            className={cn("rounded px-2 py-0.5",
              now === n ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted")}>
            {n} 场
          </button>
        ))}
        <span className="text-muted-foreground/80" title={`接口回显：sessions_min=${lo}、sessions_max=${hi}、默认=${payload?.sessions_default ?? "—"}`}>
          场次数是探索用的对照参数（上下夹与默认都从接口读，界面不写死）：换一档就是换一段日历重看，不改任何判据。
        </span>
      </div>}>
      {d => {
        const windows = d.windows ?? [];
        const states = d.carry_states ?? {};
        // 四态各自的只数与句子都由后端交出（`state_counts` + `cell_states`）：合并成一个总数
        // 就是把「没入选」这一句事实念成「没读数」——那条红线全站都拦的是同一件事。
        const words = d.cell_states ?? {};
        const missing = Object.entries(d.state_counts ?? {})
          .filter(([st, n]) => st !== "ok" && n > 0);
        const missingTotal = missing.reduce((n, [, v]) => n + v, 0);
        const unknownCarry = (d.series ?? []).reduce(
          (n, s) => n + s.carry.filter(c => c.state === "unknown").length, 0);
        return (
          <>
            <Row label="基准日 / 场次" value={`${dash(d.as_of)}｜近 ${dash(d.sessions)} 场`
              + `｜趋势回看 ${dash(d.calendar?.sessions)} 场（${dash(d.calendar?.from)} ~ ${dash(d.calendar?.to)}）`} />
            <Row label="这几场的名次来自" value={`${dash(d.source)}；口径版本 ${dash(d.spec_version)}`} />
            {/* 新鲜度留屏（不折进抽屉）：这一块 cold 十几秒，命中缓存时必须说得清这是几分钟前的哪一发。 */}
            <Row label="这一屏算于"
              value={`${dash(d.generated_at)}${d.cached
                ? `｜命中缓存（${Math.round((d.cache_age_seconds ?? 0) / 60)} 分钟前算的，缓存 ${Math.round((d.cache_ttl ?? 0) / 60)} 分钟）`
                : `｜这一发是现算的（${dash(d.elapsed_ms)} 毫秒）`}；按「重新取数」连缓存一起绕过去`} />

            {/* 三类声明留屏、绝不进抽屉（全站那条红线）：缺数、算不出、告警。 */}
            <NoticeBar kind="missing" when={missingTotal > 0}>
              {missing.map(([st, n]) => `${n} 格「${STATE_WORD[st] ?? st}」＝${words[st] ?? "—"}`)
                .join("；")}
              ——合计 {missingTotal} 格，不是三种话混着念的一个 —：合并起来就是拿取不到冒充没有。
            </NoticeBar>
            <NoticeBar kind="warning" when={unknownCarry > 0}>
              {unknownCarry} 个窗口对的带动判定算不出（那一档点数不足，或长期斜率没有残差可言）⇒ 念 —，不猜一个状态。
            </NoticeBar>

            <Matrix d={d} />
            <TrendTable rows={rows} windows={windows} thresholds={d.thresholds} states={states} />

            <CaliberFold caveats={d.caveats} definitions={d.definitions} coverage={d.coverage}
              compactCaveats
              notes={[(d.rules ?? {}).sessions, (d.rules ?? {}).not_this]}
              extra={<>
                {(d.guide ?? []).map(s => (
                  <DrawerSection key={s.heading} heading={s.heading}>{plain(s.body)}</DrawerSection>
                ))}
                <DrawerSection heading="每一列的名字与口径（后端描述符原句）">
                  <ul className="space-y-1">
                    {(d.columns ?? []).map(c => (
                      <li key={c.key}><b className="text-foreground">{c.label}</b>：{plain(c.says)}</li>
                    ))}
                  </ul>
                </DrawerSection>
                <DrawerSection heading={`带动那 ${Object.keys(states).length} 种状态，逐个词是什么意思`}>
                  <ul className="space-y-1">
                    {Object.entries(states).map(([k, v]) => {
                      // 后端那一串本来就写成「短词：整句」，这里只加粗前半句，不重复念一遍词
                      const i = v.indexOf("：");
                      return (
                        <li key={k}>
                          <b className="text-foreground">{plain(i < 0 ? v : v.slice(0, i))}</b>
                          {i < 0 ? "" : `：${plain(v.slice(i + 1))}`}
                        </li>
                      );
                    })}
                  </ul>
                </DrawerSection>
                <DrawerSection heading="设计规则与阈值（只住后端这一处）">
                  <ul className="space-y-1">
                    {Object.entries(d.rules ?? {}).map(([k, v]) => (
                      k !== "sessions" && k !== "not_this"
                        ? <li key={k}><b className="text-foreground">{RULE_LABEL[k] ?? k}</b>：{plain(String(v))}</li>
                        : null
                    ))}
                  </ul>
                  <Row label="点数门槛" value={dash(d.min_points_rule)} />
                  <Row label="阈值" value={JSON.stringify(d.thresholds ?? {})} />
                  <Row label="能力中文名出处" value={dash(d.names_source)} />
                </DrawerSection>
                <DrawerSection heading="这一张表怎么点">
                  <p>
                    上面那张矩阵只读不排：一格三行（分值／名次与母体／分位与变化），没有单一可排序的字段，
                    所以列头是普通表头而不是排序控件。要按大小排，用下面那张「斜率与带动」表 ——
                    那里每一列的排序键就是那一格读的那个字段本身（斜率那一列读的是 w5 这样的窗口档，
                    带动那一列读的是 p5_10 这样的档对的 z）。
                  </p>
                  <p>
                    名次比上一场那几个箭头只在相邻两场都有数时才念；中间断一场就断开链条，
                    因为那已经不是差一场的变化了。
                  </p>
                </DrawerSection>
              </>} />
          </>
        );
      }}
    </Panel>
  );
}

const RULE_LABEL: Record<string, string> = {
  window: "窗口与点数",
  slope: "斜率与标准误",
  carry: "带动怎么变成一个数",
  rank: "名次与三把母体",
  direction: "方向为什么反过来",
};

/** 页面上的宿主：没选一只票时两个请求都不发（后端只会拿一个假代码去查）。 */
export function SchemeRadarBoard({ code }: { code: string | null }) {
  const [nonce, refresh] = useNonce();
  const sec = (code ?? "").trim();
  if (!sec) {
    return (
      <GlassCard className="mt-3">
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          <b className="text-foreground">近期各量化方案的名次与趋势带动</b>：还没选中任何一只票 ——
          用页首那一个搜索框选一只（代码 / 名称 / 拼音首字母都行），
          或从「分组自选」「重点关注（持仓）」里双击某一行开这一页。
        </p>
      </GlassCard>
    );
  }
  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-semibold">近期各量化方案的名次与趋势带动 · {sec}</h3>
        <span className="text-[11px] text-muted-foreground">
          近几场各套方案的得分与名次（含埋伏分那一族＝左侧机会），以及 2／5／10／30／60 场趋势谁在带动谁。
        </span>
        <button type="button" onClick={refresh}
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3 w-3" /> 重新取数
        </button>
      </div>
      <Board code={sec} nonce={nonce} />
    </div>
  );
}
