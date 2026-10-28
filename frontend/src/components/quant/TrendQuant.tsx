import { RefreshCw } from "lucide-react";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { Caliber } from "@/components/ui/Caliber";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { num } from "@/lib/qd";
import { scoreRankCell } from "@/lib/ranking";
import { pctColor, heatText } from "@/lib/colors";
import { cn } from "@/lib/utils";

/**
 * 该支票的趋势量化计算（用户 2026-10-14：趋势评分本来就是针对个股的，并入「我的股票 · 分组自选」
 * 的量化档案下方）。两块，都不重算判据，各打各的只读接口：
 *  · 八项 capability 的**分量明细**（分是怎么加权出来的、权重齐不齐）；
 *  · **窗口斜率**（分数在往哪走：10 个有评分日的最小二乘斜率、r²、连续上行天数）。
 *
 * 35 项技术指标不在这里 —— 它在同一屏下面的「个股研究」那一块（同一个组件、只挂一份），档案里再放一份就是两份同源表。
 * 合成一个"总分"也不做：62 分是加权结果，不是客观量。
 */

interface Component {
  capability_key?: string | null; component_key?: string | null;
  component_name?: string | null; component_label?: string | null;
  component_value?: number | null; weight?: number | null; contribution?: number | null;
  script_key?: string | null; script_version?: string | null;
}
interface Capability {
  capability_key?: string | null; capability_name?: string | null; capability_note?: string | null;
  value?: number | null; rank_in_market?: number | null;
  percent_rank?: number | null; quadrant?: string | null; is_stale?: boolean | null;
  /** 名次这一格能不能念、为什么 —— 判据与句子都由后端 `_rank_view` 交出，页面只转述 */
  rank_kind?: string | null; rank_note?: string | null;
  universe?: number | null; distinct_values?: number | null;
  tie_at_value?: number | null; biggest_tie?: number | null;
  security_code?: string | null;
  components?: Component[] | null; component_count?: number | null;
  weight_sum?: number | null; weight_missing?: number | null; weight_sum_ok?: boolean | null;
  registry_binding?: { script_key?: string | null; script_name?: string | null; path?: string | null; version?: string | null; raw_weight?: number | null; eff_weight?: number | null }[] | null;
}
interface Scores {
  available?: boolean | null; reason?: string | null; code?: string | null; date?: string | null;
  source?: string | null; capabilities?: Capability[] | null; capability_keys?: string[] | null;
  n_components?: number | null; weights_registry_table?: string | null;
  names_source?: string | null; component_names_source?: string | null;
  definitions?: Record<string, string> | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; label?: string | null; text?: string | null }[] | null;
}
interface TrendRow {
  trade_date?: string | null; point_count?: number | null; slope?: number | null;
  intercept?: number | null; corr_coef?: number | null; x_plus_y_max?: number | null;
  x_plus_y_max_date?: string | null; up_streak_days?: number | null; sparkline?: string | null;
  window_entry_quadrant?: string | null;
  /** 峰值在窗口内第几个**有评分日**（后端现算，1＝窗口第一天）。缺＝定位不了，显示 `—` */
  x_plus_y_max_day_no?: number | null;
  /** 上面那个序号翻成的词（后端给的：就在这一场／最近两天／中途见顶／起点附近） */
  x_plus_y_max_position?: string | null;
}
interface Trend {
  available?: boolean | null; reason?: string | null; security_code?: string | null;
  window_days?: number | null; min_point_count?: number | null; sessions_returned?: number | null;
  first_session?: string | null; last_session?: string | null;
  /** 序号→词 的阈值那句话，住在后端（`_peak_position`），页面只转述 */
  peak_rule?: string | null;
  definitions?: Record<string, string> | null; rows?: TrendRow[] | null; source?: string | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null; available_windows_for_this_stock?: unknown[] } | unknown[]> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

/**
 * 名次那一格：中文名、名次、前百分之几、并列几只、这一项全场几个取值。
 *
 * 判据（"取值只剩一档 ⇒ 名次没意义"）与那句解释都只住后端 `duanxian/quant.py::_rank_view`，
 * 这里只把交出的 `rank_kind`/`rank_note`/计数转成文字（`lib/ranking.ts::scoreRankCell` 一份实现，
 * 档案那一屏用的是同一个函数）。所以 `market_mood` 这类"同场全市场同一个值"的项，
 * 屏上念的是 `名次 —` 而不是「全市场第 1（前 50.0%）」。
 */
function RankCell({ c }: { c: Capability }) {
  const cell = scoreRankCell(c);
  if (!cell) return null;
  return (
    <span className={cn("text-[11px]", cell.warn ? "text-warning" : "text-muted-foreground")}
      title={cell.title}>
      {cell.head}
      {cell.pct ? `（${cell.pct}）` : ""}
      {cell.extras.length > 0 ? `｜${cell.extras.join("｜")}` : ""}
    </span>
  );
}

function Breakdown({ code, nonce }: { code: string; nonce: number }) {
  const scores = useProbe<Scores>("/quant/scores", nonce, { code });
  const cmpSort = useTableSort();
  return (
    <Panel title="八项能力加权明细（每项由哪些分量、按多大权重合成）" state={scores}
      note="每项能力 = 若干分量按注册表权重加权。权重不为 1 或有缺失时，这一项的分数不可跨票比较，页面上会标出来。">
      {d => (
        <>
          <Row label="代码 / 评分日" value={`${dash(d.code)} · ${dash(d.date)}`} />
          <Row label="分量总数" value={dash(d.n_components)} />
          <Row label="权重出处" value={dash(d.weights_registry_table)} />
          {/* 名字的两个出处也念出来：屏上的中文名不是前端手抄的表，是注册表里那一份 */}
          <Row label="能力名出处" value={dash(d.names_source)} />
          <Row label="分量名出处" value={dash(d.component_names_source)} />
          {/* 声明 / 字段含义 / 覆盖 / 出处收成一处默认收起的折叠块（用户 2026-10-21：
              「把页面上的描述性内容全部收缩起来，避免过多占屏」—— 这四行原先摊在八张分量表之前，
              读者先撞见一屏口径才看到分是怎么加出来的）。
              **没跟着折的**：上面几行读数、每一格里的「用的是上一场的值」与「权重和不对 ⇒ 今天不可比」
              那两枚告警徽标、名次那一格后面那句为什么；缺数／失败一族的 caveat 由折叠块自己留在屏上。 */}
          <CaliberFold caveats={d.caveats} definitions={d.definitions}
            coverage={d.coverage} provenance={[d.source]} />
          <ul className="mt-2 space-y-1.5">
            {(d.capabilities ?? []).map(c => (
              <li key={c.capability_key} className="rounded-lg border border-border p-2">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-[12px] font-semibold" title={c.capability_note ?? undefined}>
                    {dash(c.capability_name ?? c.capability_key)}
                  </span>
                  {/* 机器键不藏：对账、推送、查库都要用它，但它是配角，不是格子的名字 */}
                  <span className="font-mono text-[10px] text-muted-foreground/70"
                    title="capability_key（库里的机器键，config.capability 的主键）">{c.capability_key}</span>
                  <span className="font-mono text-[12px] tabular-nums">{num(c.value, 3)} 分</span>
                  <RankCell c={c} />
                  {c.capability_key === "oper_score" && c.quadrant && (
                    <span className="rounded bg-primary/15 px-1 text-[10px] text-primary">象限 {c.quadrant}</span>
                  )}
                  {c.is_stale === true && <span className="rounded bg-warning/15 px-1 text-[10px] text-warning">用的是上一场的值</span>}
                  {c.weight_sum_ok === false && (
                    <span className="rounded bg-destructive/15 px-1 text-[10px] text-destructive">
                      权重和 {num(c.weight_sum, 3)}、缺 {dash(c.weight_missing)} 项 —— 这一项今天不可比
                    </span>
                  )}
                </div>
                {/* 名次判不了／挤在档位里时，后端那句话就摆在格子下面（悬停不算"看得见"） */}
                {(c.rank_kind === "degenerate" || c.rank_kind === "unknown" || c.rank_kind === "no_rank")
                  && !!c.rank_note && (
                  <p className="mt-1 text-[10px] leading-relaxed text-warning">{c.rank_note}</p>
                )}
                <table className="mt-1 w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="分量" sortKey="component_name" dir={cmpSort.dirOf("component_name")}
                      onSort={cmpSort.toggle}
                      title="按格子里显示的那串中文分量名排（后端从注册表带来；对不上中文名时才退回机器键）。sortKey 就是这一格读的 component_name" />
                    <SortHeader label="值" sortKey="component_value" dir={cmpSort.dirOf("component_value")} onSort={cmpSort.toggle} />
                    <SortHeader label="权重" sortKey="weight" dir={cmpSort.dirOf("weight")} onSort={cmpSort.toggle} />
                    <SortHeader label="贡献" sortKey="contribution" dir={cmpSort.dirOf("contribution")} onSort={cmpSort.toggle} />
                    <SortHeader label="脚本（机器键）" sortKey="script_key" dir={cmpSort.dirOf("script_key")} onSort={cmpSort.toggle}
                      title="算这个分量的那段脚本的键与版本；分量那一列已经是中文名，这一列留着对账" />
                  </tr></thead>
                  <tbody>
                    {cmpSort.apply((c.components ?? []).map(p => ({
                      ...p, component_name: p.component_name ?? p.component_label ?? p.component_key ?? null,
                    }))).map((p, i) => (
                      <tr key={`${p.component_key}-${i}`} className="border-t border-border/30">
                        <td title={`component_key=${p.component_key ?? "—"}｜机器键在最后一列的脚本名里`}>
                          {dash(p.component_name ?? p.component_label ?? p.component_key)}
                        </td>
                        <td className="font-mono tabular-nums">{num(p.component_value, 4)}</td>
                        <td className="font-mono tabular-nums">{num(p.weight, 2)}</td>
                        <td className={cn("font-mono tabular-nums", pctColor(p.contribution ?? null))}>{num(p.contribution, 4)}</td>
                        <td className="font-mono text-[10px] text-muted-foreground">
                          {dash(p.script_key)}{p.script_version ? ` v${p.script_version}` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(c.components ?? []).length === 0 && (
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    后端没交这一项的分量明细 —— 只给了合成分，所以这里不猜它由什么组成。
                  </p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

function Windows({ code, nonce }: { code: string; nonce: number }) {
  const trend = useProbe<Trend>("/quant/score-trend", nonce, { code });
  const trSort = useTableSort();
  return (
    <Panel title={`窗口趋势（斜率 / 高点位置 / 连续上行 / 迷你走势）· ${code}`} state={trend}
      note={'每行是一个「基准交易日 + 往前若干个有评分的交易日」的窗口（窗口长度见下面「窗口 / 门槛」那一格）。'
        + '窗口天数不是日历天数：长假里 10 个日历日可能只剩 5 个交易日。'
        + '斜率说方向与快慢，「高点在第几天」说这个方向是哪一种形状 —— 两个一起才读得对。'}>
      {d => {
        // 「X+Y 最高」那一列的深浅：在本屏这几行之间归一（同一列的 min~max 分 5 档）。
        // 深浅只表示大小、不表示好坏；方向（正/负）继续留给红绿，两种颜色语义不许混。
        const vals = (d.rows ?? []).map(r => r.x_plus_y_max).filter((v): v is number => typeof v === "number");
        const lo = vals.length ? Math.min(...vals) : NaN;
        const hi = vals.length ? Math.max(...vals) : NaN;
        const win = d.window_days ?? null;
        const peakTitle = `${d.definitions?.x_plus_y_max_day_no ?? ""}｜${d.peak_rule ?? ""}`;
        return (
        <>
          {/* 用户 2026-10-22：这两句描述（看的是谁的窗口、窗口多长门槛多少）收进默认收起的框。
              **没跟着折的三条**：下面那行「交出 …；表里列前 40 行」是截断声明，
              `cached_aggregates`／`thin_window`／`window_entry_quadrant_null` 三条是缓存年龄与缺数计数
              —— 前两类一折起来，屏幕就能把"只列了 40 行"念成"这票只有 40 个窗口"、
              把"7 场窗口点数不足"念成"这 59 场都算得动"（全站那条红线，见《01》§十三之二）。 */}
          <Row label="交出" value={`${dash(d.sessions_returned)} 场（${dash(d.first_session)} ~ ${dash(d.last_session)}）；表里列前 40 行`} />
          <Caliber text={
            "【母体】这不是横排多只票的榜单：它是「这一只票」按交易日滚动的若干个窗口。" +
            "行数 = 交出多少场窗口，每行一个基准日。\n" +
            "【窗口】每个窗口 = 基准日 + 往前数 10 个「有操作分的交易日」（缺分的日子不进窗口，" +
            "所以窗口跨度是交易日数、不是自然日数）。\n" +
            "【斜率】这 10 个点的最小二乘斜率：分为单位、每 1 个窗口日涨/跌多少分。" +
            "它说的是「分数在往哪走」，不是股价涨跌。\n" +
            "【r²】窗口内分数对时间的线性拟合优度（相关系数平方）。低 r² + 大斜率 = 中间有一天跳了一下，" +
            "不代表趋势；所以相关性弱的行会自己标出来。\n" +
            "【X+Y 最高 / 哪天最高 / 高点在第几天】这个窗口里 X+Y（四维象限那两轴）的最高值、它出现在哪一天、" +
            "以及那天是窗口里第几个有评分日。最高值落在窗口末尾＝这轮还在往上；落在开头＝高点早过了。\n" +
            "【连续上行】从基准日往前数，操作分连续严格上行的天数 —— 中断即归零重数。\n" +
            "【入口档】这个窗口第一天落在哪个象限（Q1~Q4）。\n" +
            "【形状】sparkline 只画形状、不带绝对高低，别按它排序 —— 要比大小用「斜率」或「X+Y 最高」。\n" +
            "【截断】表只列最近 40 行（`交出` 那一格是后端给的全部场数），排序在截断之前。"
          } />
          <CaliberFold caveats={d.caveats} definitions={d.definitions} />
          <ExplainBox title="这一屏看的是谁的窗口 · 窗口与门槛是多少">
            <Row label="这是谁的窗口"
              value={`${dash(d.security_code || code)} 一只票的滚动窗口 —— 表里每一行都是同一只票，只是基准日不同`} />
            <Row label="窗口 / 门槛"
              value={`${dash(d.window_days)} 个有评分日，min_point_count=${dash(d.min_point_count)}`} />
          </ExplainBox>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead><tr className="text-left text-muted-foreground">
                <SortHeader label="基准日" sortKey="trade_date" dir={trSort.dirOf("trade_date")} onSort={trSort.toggle} />
                <SortHeader label="点数" sortKey="point_count" dir={trSort.dirOf("point_count")} onSort={trSort.toggle} />
                <SortHeader label="斜率" sortKey="slope" dir={trSort.dirOf("slope")} onSort={trSort.toggle} />
                <SortHeader label="r²" sortKey="r2" dir={trSort.dirOf("r2")} onSort={trSort.toggle} />
                <SortHeader label="X+Y 最高" sortKey="x_plus_y_max" dir={trSort.dirOf("x_plus_y_max")} onSort={trSort.toggle}
                  title={`颜色深浅＝这一列在本屏这几行之间从低到高（${Number.isFinite(lo) ? num(lo, 1) : "—"} → ${Number.isFinite(hi) ? num(hi, 1) : "—"}）分 5 档，只表示大小不表示好坏；方向的正负仍由红绿表示`} />
                <SortHeader label="哪天最高" sortKey="x_plus_y_max_date" dir={trSort.dirOf("x_plus_y_max_date")} onSort={trSort.toggle} />
                {/* 用户 2026-10-15 要的：只有日期时，"离今天几天"得人自己心算。序号与词都由后端算交
                    （阈值住在 `duanxian/quant.py::_peak_position`），页面不自己数、也不自己拼句子。 */}
                <SortHeader label="高点在第几天" sortKey="x_plus_y_max_day_no" dir={trSort.dirOf("x_plus_y_max_day_no")} onSort={trSort.toggle}
                  title={peakTitle} />
                <SortHeader label="连续上行" sortKey="up_streak_days" dir={trSort.dirOf("up_streak_days")} onSort={trSort.toggle} />
                <SortHeader label="入口档" sortKey="window_entry_quadrant" dir={trSort.dirOf("window_entry_quadrant")} onSort={trSort.toggle} />
                <th className="whitespace-nowrap font-normal"
                  title="这一列是当日 X+Y 的形状示记（sparkline），不是数值，按它排序没有读数意义；要比大小用「斜率」或「X+Y 最高」">形状</th>
              </tr></thead>
              <tbody>
                {trSort.apply((d.rows ?? []).map(r => ({
                  ...r, r2: typeof r.corr_coef === "number" ? r.corr_coef * r.corr_coef : null,
                }))).slice(0, 40).map(r => (
                  <tr key={r.trade_date ?? ""} className="border-t border-border/40">
                    <td className="whitespace-nowrap font-mono">{dash(r.trade_date)}</td>
                    <td className="font-mono tabular-nums">{dash(r.point_count)}</td>
                    <td className={cn("font-mono tabular-nums", pctColor(r.slope ?? null))}>{num(r.slope, 4)}</td>
                    <td className="font-mono tabular-nums">{typeof r.corr_coef === "number" ? (r.corr_coef * r.corr_coef).toFixed(3) : dash(null)}</td>
                    <td className={cn("font-mono tabular-nums", heatText(r.x_plus_y_max ?? null, lo, hi))}>{num(r.x_plus_y_max, 2)}</td>
                    <td className="whitespace-nowrap font-mono text-muted-foreground">{dash(r.x_plus_y_max_date)}</td>
                    <td className="whitespace-nowrap">
                      {r.x_plus_y_max_day_no == null ? dash(null)
                        : <>第 <b className="font-mono tabular-nums">{r.x_plus_y_max_day_no}</b>
                          /{dash(win)} 天
                          <span className="ml-1 text-[10px] text-muted-foreground">{dash(r.x_plus_y_max_position)}</span></>}
                    </td>
                    <td className="font-mono tabular-nums">{dash(r.up_streak_days)}</td>
                    <td className="font-mono">{dash(r.window_entry_quadrant)}</td>
                    <td className="whitespace-nowrap font-mono" title="sparkline 只表示形状，不表示绝对高低">
                      {dash(r.sparkline)}
                      {typeof r.corr_coef === "number" && Math.abs(r.corr_coef) < 0.3
                        ? <span className="ml-1 text-[10px] text-warning">相关性弱</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ExplainBox title="这一张表怎么读：形状 · 深浅 · 高点位置">
            <p>
              「形状」这一列：后端给了 <code>sparkline</code> 才画，且只表示形状不表示绝对高低；
              相关系数 r 的绝对值小于 0.3 时这条趋势线本身就不成立，所以这里标「弱」而不是画一条看着确定的线。
            </p>
            <p>
              「X+Y 最高」的<b className="text-foreground">深浅</b>＝这一列在<b className="text-foreground">本屏这几行之间</b>
              从低到高（{Number.isFinite(lo) ? num(lo, 1) : "—"} → {Number.isFinite(hi) ? num(hi, 1) : "—"}）分 5 档，
              <b className="text-foreground">只表示大小，不表示好坏</b>；换一批行、换一只票，深浅就不能互相对照。
              方向的正负仍旧归红绿（全站同一套：红＝正、绿＝负），一种颜色只有一个意思。
            </p>
            <p>
              「高点在第几天」为什么要有：斜率只说"平均每天动几分"，<b className="text-foreground">同一个平均数可以由完全相反的形状拼出来</b> ——
              高点落在窗口末尾＝这轮还在往上；落在开头＝高点早过了、这些天在回落。
              序号与那句词都由后端算好交出（阈值只住 <code>duanxian/quant.py</code> 一处），页面不自己数：
              {d.peak_rule ?? "（后端没交出 peak_rule，所以这里不自己写阈值）"}。
              数的是<b className="text-foreground">有评分日</b>，与斜率同一把尺子，不数日历日。
            </p>
          </ExplainBox>
        </>
        );
      }}
    </Panel>
  );
}

/** 档案下方的延伸块：选中哪只票就算哪只票；没选票时两个请求都不发。
 *  2026-10-15 起这一块的宿主是「个股深度复盘」（`pages/StockReview.tsx`）—— 空态那句话必须说
 *  这一屏的真源（页首那一只搜索框 / 别处双击带进来），不能再讲"点上面表里任意一格"。 */
export function TrendQuant({ code }: { code: string | null }) {
  const [nonce, refresh] = useNonce();
  const sec = (code ?? "").trim();
  if (!sec) {
    return (
      <GlassCard className="mt-3">
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          <b className="text-foreground">该支票的趋势量化计算</b>：还没选中任何一只票 ——
          用页首那一个搜索框选一只（代码 / 名称 / 拼音首字母都行），
          或从「分组自选」「重点关注（持仓）」里双击某一行开这一页。
          没选中时这两个请求根本不发（后端只会拿一个假代码去查）。
        </p>
      </GlassCard>
    );
  }
  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-semibold">该支票的趋势量化计算 · {sec}</h3>
        <span className="text-[11px] text-muted-foreground">
          量化档案的延伸：分怎么加权来的、以及在往哪走。35 项技术指标在同页下面「个股研究」那一块。
        </span>
        <button type="button" onClick={refresh}
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3 w-3" /> 重新取数
        </button>
      </div>
      <Breakdown code={sec} nonce={nonce} />
      <Windows code={sec} nonce={nonce} />
    </div>
  );
}

/** 原「趋势评分」那一屏的两块内容，供它自己复用（不再挂路由，文件留着）。 */
export function TrendQuantForPage({ code, nonce }: { code: string; nonce: number }) {
  return (
    <>
      <Breakdown code={code} nonce={nonce} />
      <Windows code={code} nonce={nonce} />
    </>
  );
}
