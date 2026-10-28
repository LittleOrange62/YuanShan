import { useState } from "react";
import { RefreshCw, Layers3 } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { Definitions, ExplainBox } from "@/components/ui/DataPanel";
import { pctColor } from "@/lib/colors";
import { finite, plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import { ageText, dash } from "@/lib/datastate";
import { cumCellB, fmtBps, sideStreak, streakText, valueCell, valueColumnLabel } from "@/lib/rotation";
import type { RotationBasis } from "@/lib/rotation";
import type { RotationRow, SectorRotation } from "@/lib/api";

/**
 * 首页「资金轮动 · 份额口径 / 换手口径（本地逐日）」—— 同一张榜的两把尺子，一次只上一把。
 *
 * 三个刻意的选择，改动前请先读完：
 *
 * 1. **只交统计量，不交信号。** 库里那次实测（一千多场 × 九百多个板块）说得很清楚：
 *    份额连续 n 场抬升之后、下一场仍抬升的概率随 n **单调下降**，而基线本来就那么高 ——
 *    是均值回复，不是惯性（这组数只住在库里的参数行与 `docs/数据加工工作指南/08`，
 *    界面读后端交出的 `evidence`，不自己抄一份，换了判据版本还说旧数就是假话）。
 *    所以这张榜**没有箭头、不把统计量说成资金动向**，连续场次那一列也不染涨跌色：
 *    颜色在这里会被读成方向判断。
 * 2. **判据是份额，不是绝对成交额。** 用"成交额 ÷ 自身若干场均额"测连续性会得到一条
 *    看着像惯性的"延续"曲线，其实全是全场共同放量的自相关（`.local/flow_proxy_test2.py` 复核过）。
 * 3. **`days_N` 与 `cum_N` 必须同屏。** 攒不满窗口时那是"前 6 场的差"，
 *    与"10 场的差"不可比；只给 cum 不给 days 就是造一个看起来可比的数。
 *
 * 4. **两把尺子一次只上一把（2026-10-12 加了换手强度）。** `basis` 由父层持有，换档＝重新取一次数
 *    （后端按 basis + 各自的判据版本查库），不是前端换个列名。`share` 的 `value` 是份额小数、
 *    `cum_N` 是份额差，都要 ×100；`turnover` 的两个数**本身就是百分数／百分点**，再乘一次屏幕就说谎
 *    （4.0 会印成 400.00%）。所以量纲与列名全部走 `lib/rotation.ts` 里按 basis 分支的那几个函数，
 *    组件里不许自己乘。均值回复那句实证**只在份额那一档说** —— 换手没做过同一个检验。
 *
 * 读数格式全部住在 `lib/rotation.ts`（可单测），这里只做布局。
 * 数据来源 `analytics.sector_flow_trend`（`basis=share` 与 `basis=turnover` 两批行，
 * 各自的 spec_version 由 `config.setting` 那一行说），口径文字由后端 `calibers` 给出，页面不自己抄一份。
 */

const COLORS = ["#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#fb7185"];

/** 份额曲线：一屏最多 9 条（后端每个窗口榜头给 3 条），画更多就没一条读得清。 */
function ShareChart({ series, active, onToggle, sessions, basis }: {
  series: NonNullable<SectorRotation["series"]>;
  active: Set<string>;
  onToggle: (code: string) => void;
  sessions: number;
  basis: RotationBasis;
}) {
  // 纵轴刻度也分尺子：份额那档库里存的是小数（0.004），要 ×100；
  // 换手那档存的就是百分数（4.0 = 4%），再乘一次就成了 400.00%。
  const tick = (v: number) => (basis === "turnover" ? v : v * 100).toFixed(2) + "%";
  const shown = series.filter(s => active.has(s.code) && s.points.length >= 2);
  if (series.length === 0) return <p className="p-3 text-[12px] text-muted-foreground">后端没交曲线数据。</p>;
  if (shown.length === 0) return <p className="p-3 text-[12px] text-muted-foreground">曲线全被点掉了 —— 点下面图例恢复。</p>;

  const all = shown.flatMap(s => s.points.map(p => finite(p.share)).filter((n): n is number => n !== null));
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = hi - lo || 1;
  const W = 560, H = 150, PAD = 6;
  const x = (i: number, n: number) => PAD + (i * (W - 2 * PAD)) / Math.max(1, n - 1);
  const y = (v: number) => H - PAD - ((v - lo) / span) * (H - 2 * PAD);

  return (
    <div className="p-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-40 w-full" role="img"
        aria-label={`${basis === "turnover" ? "换手率" : "份额"}走势（近 ${sessions} 场）：${shown.map(s => s.name).join("、")}`}>
        <text x={PAD} y={11} className="fill-current text-[9px] text-muted-foreground">{tick(hi)}</text>
        <text x={PAD} y={H - 2} className="fill-current text-[9px] text-muted-foreground">{tick(lo)}</text>
        {shown.map((s, k) => {
          const pts = s.points.map((p, i) => {
            const v = finite(p.share);
            return v === null ? null : `${x(i, s.points.length)},${y(v)}`;
          }).filter(Boolean).join(" ");
          return (
            <polyline key={s.code} points={pts} fill="none" strokeWidth={1.6}
              stroke={COLORS[series.indexOf(s) % COLORS.length]}
              strokeDasharray={k > 2 ? "4 3" : undefined} />
          );
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
        {series.map((s, i) => (
          <button key={s.code} type="button" onClick={() => onToggle(s.code)}
            className={cn("flex items-center gap-1 text-[11px] hover:text-foreground",
              active.has(s.code) ? "text-foreground" : "text-muted-foreground/50 line-through")}>
            <span className="inline-block h-2 w-2 rounded-full"
              style={{ background: COLORS[i % COLORS.length], opacity: active.has(s.code) ? 1 : 0.3 }} />
            {s.name}
            <span className="text-muted-foreground/60">{s.points.length} 场</span>
          </button>
        ))}
      </div>
      <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
        纵轴是{basis === "turnover" ? "当日换手率（本板块成交额 ÷ 本板块成分流通市值合计），剔的是板块盘子大小" : "份额（本板块成交额 ÷ 当日全场板块成交额），剔的是全场共同放量"}，不是金额、不是涨跌幅；
        每条线只画到最近一场，中间那天库里没数就断线，不拿邻近交易日补。
      </p>
    </div>
  );
}

function BoardTable({ rows, window: w, calibers, side, basis }: {
  rows: RotationRow[]; window: number; calibers?: Record<string, string>;
  side: "up" | "dn"; basis: RotationBasis;
}) {
  // 默认序按**这一侧的连续场次**由多到少（用户 2026-10-08 点名）。
  // 行是谁（入榜资格）仍由后端按累计选，这里只改排列 —— 所以标题与 `#` 的说明都要跟着说清。
  const sort = useTableSort({ key: "streak", dir: "desc" });
  const rk = `rank_${w}` as keyof RotationRow;
  const streakLabel = side === "up" ? "连续抬升场次" : "连续回落场次";
  const turnover = basis === "turnover";
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-border/50 text-left text-[11px] text-muted-foreground">
            <th className="whitespace-nowrap px-2 py-1.5 font-medium" title={`榜内序号（后端按近 ${w} 场累计排的），不是这一屏的行序；默认序是${streakLabel}由多到少，所以这一列会跳号`}>#</th>
            <SortHeader label="板块" sortKey="name" dir={sort.dirOf("name")} onSort={sort.toggle} />
            <SortHeader label="类型" sortKey="sector_type" dir={sort.dirOf("sector_type")} onSort={sort.toggle} />
            <SortHeader label={`近 ${w} 场累计`} sortKey={`cum_${w}`} dir={sort.dirOf(`cum_${w}`)} onSort={sort.toggle}
              title={calibers?.cum ?? "窗口末场减首场，百分点（差值，不是金额也不是流量）"} />
            <SortHeader label="名次" sortKey={String(rk)} dir={sort.dirOf(String(rk))} onSort={sort.toggle}
              title="当日母体内的纯排序，不含好坏" />
            <SortHeader label={valueColumnLabel(basis)} sortKey="share" dir={sort.dirOf("share")} onSort={sort.toggle}
              title={calibers?.value ?? (turnover ? "当日换手率 %" : "份额，不是金额")} />
            <SortHeader label="日变化" sortKey="delta_bps" dir={sort.dirOf("delta_bps")} onSort={sort.toggle} />
            {turnover && (
              /* 换手口径专属：分母（本板块成分流通市值合计）与"剔了几只成员"。
                 后者不是装饰 —— 分子分母必须同一母体，读者要能当场验：匹配 < 在册 就是剔过。 */
              <SortHeader label="分母/成员" sortKey="mv_yuan" dir={sort.dirOf("mv_yuan")} onSort={sort.toggle}
                title={calibers?.amount ?? "本板块成分流通市值合计（元）；括号里是「有换手率的成员数 / 那天在册成员数」"} />
            )}
            <SortHeader label={streakLabel} sortKey="streak" dir={sort.dirOf("streak")} onSort={sort.toggle}
              title={`${turnover ? "换手率" : "份额"}严格递增（或递减）的连续场次；相等即断链。这一列是本榜的默认序（由多到少）。`
                    + "另一侧没连着的就是 0，排在最后 —— 榜里允许出现「累计在涨但这一场已经开始回落」的板块。"
                    + "这一列故意不上涨跌色：份额那条实测越连续越不延续"} />
            <SortHeader label="当日成交额" sortKey="amount_yuan" dir={sort.dirOf("amount_yuan")} onSort={sort.toggle}
              title={calibers?.amount ?? "辅助列，不是资金流入"} />
          </tr>
        </thead>
        <tbody>
          {sort.apply(rows.map((r, i) => ({
            ...r,
            streak: sideStreak(r.streak_up, r.streak_dn, side),
            _i: i + 1,
          }))).map((r) => (
            <tr key={r.sector_code} className="border-b border-border/30">
              <td className="px-2 py-1.5 font-mono text-[11px] text-muted-foreground/50">{r._i}</td>
              <td className="max-w-40 truncate px-2 py-1.5 font-medium" title={r.name}>{r.name}</td>
              <td className="px-2 py-1.5 text-[11px] text-muted-foreground">{r.sector_type ?? "—"}</td>
              <td className={cn("whitespace-nowrap px-2 py-1.5 font-mono", pctColor(finite(r[`cum_${w}` as keyof RotationRow] as number | null)))}>
                {cumCellB(r[`cum_${w}` as keyof RotationRow] as number | null,
                         r[`days_${w}` as keyof RotationRow] as number | null, w, basis)}
              </td>
              <td className="px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
                {dash(r[rk] as number | null)}
              </td>
              <td className="px-2 py-1.5 font-mono">{valueCell(r.share, basis)}</td>
              <td className={cn("whitespace-nowrap px-2 py-1.5 font-mono", pctColor(r.delta_bps))}>{fmtBps(r.delta_bps)}</td>
              {turnover && (
                <td className="whitespace-nowrap px-2 py-1.5 font-mono text-[11px] text-muted-foreground"
                  title={r.mv_yuan == null ? "这一档没有分母（后端那行没交 mv_yuan）"
                    : `分母 ${(r.mv_yuan / 1e8).toFixed(1)} 亿元 = 本板块匹配成员的流通市值合计`
                      + `；分子 ${(Number(r.amount_matched ?? 0) / 1e8).toFixed(1)} 亿元。`
                      + `剔成员：${r.members_matched ?? "?"} 只有换手率 / 在册 ${r.members_total ?? "?"} 只`}>
                  {r.mv_yuan == null ? "—" : `${(r.mv_yuan / 1e8).toFixed(0)} 亿`}
                  <span className="ml-1 text-muted-foreground/60">
                    （{r.members_matched ?? "—"}/{r.members_total ?? "—"}）
                  </span>
                </td>
              )}
              {/* 连续场次：中性色 + 文字"抬升/回落 N 场"，不用箭头 —— 这列容易被读成方向信号，而实测它是均值回复 */}
              <td className="whitespace-nowrap px-2 py-1.5 font-mono text-muted-foreground">
                {streakText(r.streak_up, r.streak_dn)}
              </td>
              <td className="whitespace-nowrap px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
                {r.amount_yuan == null ? "—" : `${(r.amount_yuan / 1e8).toFixed(1)} 亿`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SectorRotationBoard({ data, loading, onRefresh, basis, onBasis }: {
  data: SectorRotation | null; loading: boolean; onRefresh: () => void;
  /** 两把尺子由父层持有：换档要重新取数（后端按 basis + 各自判据版本查库），不是前端换个列名 */
  basis: RotationBasis; onBasis: (b: RotationBasis) => void;
}) {
  const [win, setWin] = useState<number>(30);
  const turnover = basis === "turnover";
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const board = (data?.boards ?? []).find(b => b.window === win) ?? data?.boards?.[0];
  const series = data?.series ?? [];
  const activeCodes = new Set(series.map(s => s.code).filter(c => !hidden.has(c)));
  const st = data?.streak_stats;
  // 母体过滤的账目：库里那一场的总行数 = 榜上行数 + 被按类型排除的行数
  const excludedCount = (data?.excluded_types ?? []).reduce((n, e) => n + (e.sectors ?? 0), 0);

  if (!data) {
    return (
      <GlassCard className="mb-6">
        <p className="p-4 text-sm text-muted-foreground">
          {loading ? "份额轮动榜读取中…" : "份额轮动榜没读到（不是「没有轮动」，是这一屏取不到数）"}
        </p>
      </GlassCard>
    );
  }
  if (data.available === false) {
    return (
      <GlassCard className="mb-6">
        <p className="p-4 text-sm text-muted-foreground">{plain(data.reason ?? "后端没交这一屏，也没说不交的原因")}</p>
      </GlassCard>
    );
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <Layers3 className="h-4 w-4" /> 资金轮动 · {turnover ? "换手口径" : "份额口径"}（本地逐日）
        </h3>
        {/* 口径切换：一次只有一档在屏上（两档并排会让名次/累计同名列并排成四列，读的人分不清哪一名次是谁的） */}
        <div className="flex items-center gap-1" role="group" aria-label="轮动口径">
          {([["share", "份额"], ["turnover", "换手强度"]] as const).map(([k, label]) => (
            <button key={k} type="button" onClick={() => onBasis(k)} aria-pressed={basis === k}
              title={k === "turnover"
                ? "成交额 ÷ 本板块成分流通市值合计：剔掉「这个板块盘子本来就大」"
                : "成交额 ÷ 当日全场板块成交额合计：剔掉全场共同放量／缩量"}
              className={cn("rounded-lg px-2 py-0.5 text-[11px]",
                basis === k ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              {label}
            </button>
          ))}
        </div>
        <Caliber text={[
          "这是本地库里按场存的统计量，不是盘中即时净流入，也不是买卖信号。",
          data.calibers?.value ?? "",
          data.calibers?.cum ?? "",
          data.calibers?.cum_days ?? "",
          data.calibers?.streak ?? "",
          data.calibers?.rank ?? "",
          data.calibers?.amount ?? "",
          // 这句实证只在份额那一档成立：它是拿份额序列测出来的。换手那一档没做过同一个检验，
          // 借用过来就是替后端说一句它没说过的话（所以按 basis 分支，不做统一措辞）。
          // 这里是 title 悬停文本，不写 markdown 星号（native tooltip 不过 plain()）。
          turnover
            ? "⚠️ 换手强度这一档没有做过「连续 n 场后下一场仍延续」的检验：界面只说过去几场谁在抬／在落，"
              + "不说接下来会继续；那句实测的均值回复属于份额口径，要看切回份额那一档。"
            : "⚠️ 均值回复：份额连续 n 场抬升后、下一场仍抬升的概率随 n 单调下降（后端 evidence 给了实测数）。"
              + "所以这张榜只说「过去几场谁的份额在抬／在落」，不说「接下来会继续」。",
        ].filter(Boolean).join("\n")} />
        <div className="flex items-center gap-1">
          {(data.windows ?? []).map(w => (
            <button key={w} type="button" onClick={() => setWin(w)}
              className={cn("rounded-lg px-2 py-0.5 text-[11px]",
                w === win ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              近 {w} 场
            </button>
          ))}
        </div>
        <span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground/60">
          行情日 {dash(data.date)} · 算于 {dash(data.calculated_at?.slice(0, 16))}
          {data.age_seconds != null ? `（${ageText(data.age_seconds)}前）` : ""}
          <button type="button" onClick={onRefresh} title="重新读取"
            className="hover:text-primary disabled:opacity-50" disabled={loading}>
            {loading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          </button>
        </span>
      </div>

      <GlassCard className="mb-6">
        {/* 分母与母体先说：名次离开母体没有意义，而"有名次的只有一部分"必须写在榜外面 */}
        <p className="border-b border-border/40 px-3 pb-2 text-[11px] leading-relaxed text-muted-foreground">
          母体 {dash(data.universe)} 个板块（当日有成交额的行业＋概念），
          其中 {dash(board?.ranked)} 个在「近 {board?.window ?? win} 场」窗口里攒够了场次、拿得到名次
          {board && board.ranked < (data.universe ?? 0) ? `（另 ${board.ranked == null ? "?" : (data.universe ?? 0) - board.ranked} 个是新板块，库里只有两三场，不给名次）` : ""}。
          {/* 分母那句按尺子分开说：换手这一档**没有**全场分母（universe_amount 是刻意留 NULL 的），
              沿用份额那句就会印成「份额分母 = …未交出」—— 把设计上没有说成缺数。 */}
          {turnover
            ? <>换手分母 = 本板块成分流通市值合计，<b>每行各自一个</b>（见表内「分母/成员」列），不是全场合计数；母体与份额那一档同一个 {dash(data.universe)} 个。</>
            : <>份额分母 = 当日全场板块成交额合计 {data.universe_amount != null ? `${(data.universe_amount / 1e12).toFixed(2)} 万亿` : "未交出"}
                —— 那是 {dash(data.universe)} 个板块的成交额<b>重叠相加</b>（一只票同时挂在十几个板块里，同一笔钱被数了很多遍），不是当日全市场成交额。</>}
          两端各取前 {dash(data.top_per_window)} 个 —— 这是截断口径，不是全部。
        </p>

        {st && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-border/40 px-3 py-2 text-[11px] text-muted-foreground">
            <span>连续场次分布（全母体 {st.total} 个板块）：</span>
            <span>抬升端 {[...st.up].sort((a, b) => a.days - b.days).map(u => `${u.days}场 ${u.sectors}个`).join(" / ") || "无"}</span>
            <span>回落端 {[...st.down].sort((a, b) => a.days - b.days).map(u => `${u.days}场 ${u.sectors}个`).join(" / ") || "无"}</span>
            <span>两边都不连续 {st.no_streak} 个</span>
          </div>
        )}

        {data.evidence && (
          <p className="border-b border-border/40 bg-warning/10 px-3 py-2 text-[11px] leading-relaxed text-warning">
            实测判据（不是本界面算的，随库里的参数行交出）：{data.evidence}
            　——　所以"连续"这两列只能当"已经发生了几天"读，不能当"还会继续几天"读。
          </p>
        )}

        {board ? (
          <div className="grid gap-0 xl:grid-cols-2">
            <div className="border-b border-border/30 xl:border-b-0 xl:border-r">
              <h4 className="px-3 pt-2 text-[11px] font-semibold text-muted-foreground">
                {turnover ? "换手" : "份额"}抬升端（入榜按近 {board.window} 场累计；默认按连续抬升场次由多到少，点列头可改）
              </h4>
              <BoardTable rows={board.rising} window={board.window} calibers={data.calibers} side="up" basis={basis} />
            </div>
            <div>
              <h4 className="px-3 pt-2 text-[11px] font-semibold text-muted-foreground">
                {turnover ? "换手" : "份额"}回落端（入榜按近 {board.window} 场累计；默认按连续回落场次由多到少，点列头可改）
              </h4>
              <BoardTable rows={board.falling} window={board.window} calibers={data.calibers} side="dn" basis={basis} />
            </div>
          </div>
        ) : <p className="p-4 text-sm text-muted-foreground">这个窗口后端没交榜。</p>}

        <div className="border-t border-border/40">
          <h4 className="px-3 pt-2 text-[11px] font-semibold text-muted-foreground">
            {/* 标题要说清这张图画的是谁（用户 10-10 问"头部板块怎么定义的"）：
                不是规模最大的板块，而是各窗口按份额累计排在抬升端最前面的那几个；回落端不进这张图。 */}
            各窗口{turnover ? "换手" : "份额"}抬升端榜头的走势（每档取榜头 {dash(data.series_per_board)} 个，不含回落端；近 {dash(data.series_sessions)} 场）
          </h4>
          <ShareChart series={series} active={activeCodes} sessions={data.series_sessions ?? 0} basis={basis}
            onToggle={code => setHidden(prev => {
              const next = new Set(prev);
              if (next.has(code)) next.delete(code); else next.add(code);
              return next;
            })} />
        </div>

        <Definitions map={data.calibers} />
        {/* 母体少了 48 行不是 bug，也不是截断 —— 那批是市场级统计指数，不是一级板块。
            账目由后端交出（`excluded_types`），页面不自己数。 */}
        {!!data.excluded_types?.length && (
          <>
            {/* 账目（几行、排掉几行、各类型几个）是读数，留在屏上；为什么排掉、
                两把尺子凭什么可比 —— 那是说明，收进通用说明框（用户 10-14）。 */}
            <p className="px-3 pb-2 text-[10px] leading-relaxed text-muted-foreground">
              这一场库里 {(st?.total ?? 0) + excludedCount} 行，
              按母体口径（{data.sector_types?.join("、") || "行业、概念"}）排除了 {excludedCount} 行：
              {data.excluded_types.map(e => ` ${e.sector_type} ${e.sectors} 个`).join("、")}。
            </p>
            <ExplainBox title="排掉的是哪一批 · 两把尺子的名次为什么可比">
              <p>
                被排除的是通达信的市场级统计指数（总市值 / 涨跌家数 / 主板总值…），
                它们的成交额逐日等于全 A，混在榜里就必然霸榜。两把尺子的分母与名次都在同一批过滤后的母体上算，
                所以榜上是连续的；库里那批旧行还物理留着（读侧按类型挡住，页面上看不见）。
              </p>
            </ExplainBox>
          </>
        )}
        {!!data.name_is_code && (
          <p className="px-3 pb-2 text-[10px] leading-relaxed text-muted-foreground">
            榜上有 {data.name_is_code} 个板块的名字就是编号 —— 那是本地板块字典里没登记中文名、
            通达信名称表里也查不到（如 880595），不是界面截断；要补名要找板块字典那一侧。
          </p>
        )}
      </GlassCard>
    </>
  );
}
