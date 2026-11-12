import { useMemo, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { CaliberFold } from "@/components/ui/DataPanel";
import { NoticeList } from "@/components/ui/NoticeBar";
import { StockLink } from "@/components/ui/StockLink";
import { useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { SortHeader } from "@/components/ui/SortHeader";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import {
  btCell, btPickTag, signedPct,
  type BtDay, type BtMetric, type BtPayload, type BtSummary,
} from "@/lib/tomorrowOutlook";

/**
 * 「⑩ 明日展望」6 个月回溯板块（用户 2026-11-05 点名：列出策略最近 6 个月每天算出来的个股，
 * 往后 1/2/3 个交易日的涨跌、冲高率、整体高点数值涨幅，算整体平均值看策略有效性）。
 *
 * 挂载位置：**工作空间 → 策略回测**（`pages/StrategyBacktest.tsx`）—— 用户 11-06 点名
 * 「这个回测写到 工作空间 / 策略回测 里面去」。「明日展望」那一屏只留一条指路，不挂第二份：
 * 两处各挂一份，改天就会长成两个说法。
 *
 * 三条红线（与主屏同一条）：
 *  · 判据只住后端：这一块的每一行数字都读 `analytics.outlook_pick_daily`（写侧
 *    `app/compute/outlook_backtest.py` import 的正是主屏那两份 SQL），页面不重算、不改写。
 *  · 缺值一律 `—`：NULL＝那一天它没有行、或那几天还没发生（窗口末端两场），不是 0%。
 *  · **基线必须并排**：没有全市场等权同尺对照，名单那几个"平均冲高 +4.7%"没有意义 ——
 *    大盘自己那天盘中均值多少，差值才是判据的信息量。
 *
 * 数据是 15 分钟缓存（`cached`/`cache_age_seconds` 后端交），"重新取数"跳缓存重扫；
 * 懒回填（库里落后于最新场次）由后端在这一趟请求里补上，`filled_this_request` 说明这一趟补没补。
 */
export function OutlookBacktestBoard() {
  const [nonce, refresh] = useProbeNonce();
  const state = useProbe<BtPayload>("/quant/tomorrow-outlook/backtest", nonce,
    { refresh: nonce === 0 ? undefined : "true" });
  const d = state.kind === "ok" && state.data.available ? state.data : null;
  const [day, setDay] = useState<string>("");
  const [allDays, setAllDays] = useState(false);
  const sort = useTableSort();

  const shownDays: BtDay[] = useMemo(() => {
    const days = d?.days ?? [];
    if (allDays) return days;
    const pick = day || days[0]?.date || "";
    return days.filter(x => x.date === pick);
  }, [d, day, allDays]);

  return (
    <GlassCard docTitle="明日展望 · 6 个月回溯">
    <section className="mt-4 rounded-xl border border-border/60 bg-muted/10 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-[12px] font-bold">6 个月回溯 · 「明日展望」这套判据往后的表现（整体平均值）</h3>
        <Caliber text={"每天 19:00 同一份判据重算历史场次：往后 1/2/3 个交易日收盘对信号日收盘的涨跌、3 日内最高点（整体高点涨幅）、冲高率（3 日内冲过信号日收盘的占比）。"
          + "后复权价同尺，基线＝同批场次全市场等权均值 —— 差值才是信息量。窗口末端最近两场 2/3 日还没发生，是 — 不是 0%。"} />
        <button type="button" onClick={refresh} title="跳过 15 分钟缓存重扫一次（懒回填只补缺的那天，冷启动补全窗要几分钟）"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      {state.kind === "loading" && (
        <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> 回测在读库（冷启动懒回填要几分钟，热读约 1 秒）…
        </p>
      )}
      {state.kind === "error" && <p className="text-[11px] text-danger">回测取数失败：{state.reason}</p>}
      {d && (
        <>
          {d.window && (
            <p className="mb-2 text-[11px] text-muted-foreground">
              窗口 {dash(d.window.from)} ~ {dash(d.window.to)}（{dash(d.window.sessions)} 个交易日）
              {d.window.filled_this_request?.filled_days
                ? ` · 这一趟懒回填补了 ${d.window.filled_this_request.filled_days} 场` : ""}
              {d.drift
                ? " · ⚠ 判据改版：这组行是上一版判据算的（algo_stamp 对不上），重算再比" : ""}
            </p>
          )}
          {d.reason && <p className="mb-2 text-[11px] text-warning">{plain(d.reason)}</p>}

          {d.drift && (
            <NoticeList className="mb-2" items={(d.caveats ?? []).filter(c => c.hard).map(c => ({
              kind: "warning" as const, text: `${c.label ?? c.code}：${c.text ?? ""}`,
            }))} />
          )}

          <SummaryTable d={d} />
          <DayPicker d={d} day={day} setDay={setDay} allDays={allDays} setAllDays={setAllDays}
            shownDays={shownDays} sort={sort} />
          <CaliberFold caveats={d.caveats ?? []}
            notes={d.caliber ? [plain(d.caliber)] : []} />
        </>
      )}
    </section>
    </GlassCard>
  );
}

/** 独立 nonce：与主屏那块互相不牵连（主屏换日期不该把这一块的重取也带出去）。 */
function useProbeNonce(): [number, () => void] {
  const [n, setN] = useState(0);
  return [n, () => setN(x => x + 1)];
}

/** 汇总那一张表：行＝指标（往后 1/2/3 日 + 高点/冲高），列＝三组样本 + 基线对照。 */
function SummaryTable({ d }: { d: BtPayload }) {
  const s = d.summary ?? {};
  const base = d.baseline;
  const rows: {
    label: string;
    hint: string;
    get: (g: BtSummary | null | undefined) => BtMetric | null | undefined;
    base: () => number | null | undefined;
  }[] = [
    { label: "次日开盘（缺口）", hint: "往后第 1 个交易日开盘 ÷ 信号日收 − 1。平均为负＝这类名单次日多半跳空低开",
      get: g => g?.gap ?? null, base: () => base?.gap ?? null },
    { label: "第 1 日收盘", hint: "往后第 1 个交易日收盘 ÷ 信号日收 − 1（隔日收红没有）",
      get: g => g?.cl1 ?? null, base: () => base?.cl ?? null },
    { label: "第 1 日高点（冲高）", hint: "往后第 1 个交易日最高 ÷ 信号日收 − 1 —— 盘中冲得多高",
      get: g => g?.hi1 ?? null, base: () => base?.hi ?? null },
    { label: "第 2 日收盘", hint: "往后第 2 个交易日收盘 ÷ 信号日收 − 1",
      get: g => g?.cl2 ?? null, base: () => base?.cl2 ?? null },
    { label: "第 3 日收盘", hint: "往后第 3 个交易日收盘 ÷ 信号日收 − 1",
      get: g => g?.cl3 ?? null, base: () => base?.cl3 ?? null },
    { label: "3 日内最高点（整体高点涨幅）", hint: "往后 3 个交易日最高价取最大 ÷ 信号日收 − 1",
      get: g => g?.hi3 ?? null, base: () => base?.hi3 ?? null },
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-[11px]">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="px-2 py-1 font-medium">往后看</th>
            <th className="px-2 py-1 text-right font-medium" title="一、多指标靠前那一组，6 个月里每一场每一只票逐笔平均">
              多指标靠前 <SampleN g={s.hits} />
            </th>
            <th className="px-2 py-1 text-right font-medium" title="二、趋势最正那一组，同上">
              趋势最正 <SampleN g={s.trend} />
            </th>
            <th className="px-2 py-1 text-right font-medium" title="两榜都进的票（交集）。单独列出来只为回答'交集是不是更强'，别当更强的买入理由">
              两榜都进 <SampleN g={s.both} />
            </th>
            <th className="px-2 py-1 text-right font-medium"
              title="同批场次全市场等权同尺均值 —— 没有这一列，左边那几个数没有对照">
              全市场基线（等权）
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.label} className="border-t border-border/40">
              <td className="px-2 py-1.5" title={r.hint}>{r.label}</td>
              {[s.hits, s.trend, s.both].map((g, i) => (
                <td key={i} className={cn("px-2 py-1.5 text-right font-mono tabular-nums",
                  pctColor(g ? r.get(g)?.mean ?? null : null))}
                  title={r.hint + `｜n=${dash(g?.n)} 笔（缺哪一天按自己的分母算）`}>
                  {g ? btCell(r.get(g)) : dash(null)}
                </td>
              ))}
              <td className={cn("px-2 py-1.5 text-right font-mono tabular-nums",
                pctColor(r.base()))}
                title="同批场次全市场等权同尺均值（分母＝那场配对上的全市场票数）">
                {signedPct(r.base())}
              </td>
            </tr>
          ))}
          <tr className="border-t border-border/40">
            <td className="px-2 py-1.5"
              title="3 日内最高价 > 信号日收盘的占比（'冲过去了'的票占几成）。up3d 判不了的（3 日全无行）从分子分母都剔">
              冲高率（3 日内 {'>'}0）
            </td>
            {[s.hits, s.trend, s.both].map((g, i) => (
              <td key={i} className="px-2 py-1.5 text-right font-mono tabular-nums"
                title={`可判 ${dash(g?.up3d_n)} 笔（3 日哪天都没行判不了，不顶成 0%）`}>
                {g && g.up3d_rate_pct != null ? `${g.up3d_rate_pct.toFixed(1)}%` : dash(null)}
              </td>
            ))}
            <td className="px-2 py-1.5 text-right font-mono tabular-nums"
              title="基线的'冲高率'：那场全市场里 3 日内最高 > 当日收的占比">
              {base?.hi3_win_pct != null ? `${Number(base.hi3_win_pct).toFixed(1)}%` : dash(null)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function SampleN({ g }: { g?: BtSummary | null }) {
  return <span className="text-[10px] text-muted-foreground/70">n={dash(g?.n)}</span>;
}

/** 逐场逐票：默认只看最近一场（与主屏"今天那张榜"对得上），点日期换场、"展开全部 105 场"看全。 */
function DayPicker({ d, day, setDay, allDays, setAllDays, shownDays, sort }: {
  d: BtPayload; day: string; setDay: (s: string) => void;
  allDays: boolean; setAllDays: (b: boolean) => void;
  shownDays: BtDay[]; sort: ReturnType<typeof useTableSort>;
}) {
  const days = d.days ?? [];
  if (!days.length) {
    return <p className="mt-2 text-[11px] text-muted-foreground">没有逐场行（回测表空）。</p>;
  }
  const flat = shownDays.flatMap(x => x.picks.map(p => ({ day: x.date, ...p })));
  return (
    <div className="mt-3">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px]">
        <span className="text-muted-foreground">逐场名单（往后的表现已经算在每行）：</span>
        <select value={allDays ? "__all" : day}
          onChange={e => {
            if (e.target.value === "__all") setAllDays(true);
            else { setAllDays(false); setDay(e.target.value); }
          }}
          className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]">
          {!allDays && !days.some(x => x.date === day) && <option value={days[0]?.date}>{days[0]?.date}（最近一场）</option>}
          {!allDays && days.map(x => (
            <option key={x.date} value={x.date}>{x.date}（{x.picks.length} 只）</option>
          ))}
          <option value="__all">展开全部 {days.length} 场（{days.reduce((a, x) => a + x.picks.length, 0)} 行）</option>
        </select>
        <span className="text-[10px] text-muted-foreground/60">
          两榜都进的票在两张榜里各有一行名次，这里合成一行（两榜名次都念出来）
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-[11px]">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="px-2 py-1 font-medium">信号日</th>
              <SortHeader label="名称与代码" sortKey="code" dir={sort.dirOf("code")}
                onSort={() => sort.toggle("code")} title="点击按代码排" />
              <th className="px-2 py-1 font-medium" title="哪一榜点的它、榜内名次几">在榜</th>
              <th className="px-2 py-1 text-right font-medium" title="往后第 1 日开盘 ÷ 信号日收 − 1（负＝跳空低开）">第 1 日开</th>
              <th className="px-2 py-1 text-right font-medium" title="往后第 1 日最高 ÷ 信号日收 − 1">第 1 日高</th>
              <th className="px-2 py-1 text-right font-medium" title="往后第 1 日收盘 ÷ 信号日收 − 1">第 1 日收</th>
              <th className="px-2 py-1 text-right font-medium" title="往后第 2 日收盘 ÷ 信号日收 − 1（最近两场还没发生＝—）">第 2 日收</th>
              <th className="px-2 py-1 text-right font-medium" title="往后第 3 日收盘 ÷ 信号日收 − 1（最近三场还没发生＝—）">第 3 日收</th>
              <th className="px-2 py-1 text-right font-medium" title="往后 3 日最高价取最大 ÷ 信号日收 − 1（整体高点数值涨幅）">3 日最高</th>
              <th className="px-2 py-1 text-center font-medium" title="3 日内冲过信号日收盘没有（—＝判不了，不是没冲）">冲高</th>
            </tr>
          </thead>
          <tbody>
            {sort.apply(flat).map(p => (
              <tr key={`${p.day}-${p.code}`} className="border-t border-border/30">
                <td className="px-2 py-1 font-mono text-[10px] text-muted-foreground">{p.day}</td>
                <td className="whitespace-nowrap px-2 py-1">
                  <StockLink code={p.code} name={p.name} className="font-medium hover:text-primary" />
                  <span className="ml-1 font-mono text-[10px] text-muted-foreground/60">{p.code}</span>
                  {p.board ? <span className="ml-1 text-[10px] text-muted-foreground/70">{p.board}</span> : null}
                </td>
                <td className="px-2 py-1 text-[10px] text-muted-foreground">{btPickTag(p)}</td>
                <Pct v={p.gap} />
                <Pct v={p.hi} />
                <Pct v={p.cl} />
                <Pct v={p.cl2} />
                <Pct v={p.cl3} />
                <Pct v={p.hi3} />
                <td className="px-2 py-1 text-center">
                  {p.up3d == null ? dash(null) : p.up3d
                    ? <span className="text-[10px] text-danger">冲过</span>
                      : <span className="text-[10px] text-muted-foreground/70">没冲</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {flat.length > 200 && (
        <p className="mt-1 text-[10px] text-muted-foreground/60">
          展开模式先摆前 200 行（全部 {flat.length} 行都在排序与统计里，界面上限只为不卡浏览器）。
        </p>
      )}
    </div>
  );
}

function Pct({ v }: { v: number | null | undefined }) {
  return (
    <td className={cn("px-2 py-1 text-right font-mono tabular-nums", pctColor(v ?? null))}>
      {signedPct(v ?? null)}
    </td>
  );
}
