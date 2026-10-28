import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { useTableSort } from "@/lib/useSort";
import { useNonce, useProbe } from "@/lib/probe";
import { STOCK_ROW_HINT, type useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { pct } from "@/lib/qd";
import { rankText, rankTitle, trimNum } from "@/lib/ranking";
import { pctColor } from "@/lib/colors";

/**
 * 倍量形态页 →「加权分进榜后的逐日涨跌幅」（`GET /api/quant/energy-track`）。
 *
 * 上面那张倍量榜读的是**这一场**的加权分；这一块读的是**走向**：母体＝基准日这一场
 * **得分前 N 名**（口径改于 2026-10-19 第二稿：以前的「加权分 ≥ 门槛」作废，分值一改门槛对应的
 * 只数就跟着漂，而「前 N 名」与档表无关），每只票的起算日是它在全史里**第一次排进前 N 名**那一场，
 * 其后每一场的当日涨跌幅一格一格铺到今天。两个时点是两件事，界面分开念
 * （后端那条 caveat `energy_track_scope` 说的就是它）。
 *
 * ⚠️ **前 N 名是上限，不是恰好 N 只**：边界那一名并列几只就一起进来，所以实际只数可以**多于 N**
 * （接口 `rows_capped` 为 true 就是这种情况；用户举的例子：取前 120 名，而第 115 名有 10 只 ⇒ 一共 125 只）。
 *
 * 这个形状最容易被写成假的五件事，各有各的钉：
 *  1. **列数＝`max_series_len`，不是日历日数**。第 0 列是起算日当天，往后 `+k 场` 一列一场；
 *     同一列在不同票身上代表各自起算日之后的第 k 场，**不是同一个日历日** —— 所以每一格悬停
 *     都交出它自己的 `trade_date`。
 *  2. **空格子是 —，不是 0%**。这一场它没有行（起算日越晚这一行越短、中途停牌也缺）与
 *     涨跌幅读不到，都不许画成 0.00%（0 是「一分钱没动」，是一条实质结论）。
 *  3. **`chg_cum=null` 不许显示 0%**：没有基期就没有整体涨跌幅。
 *  4. **起算日可能被覆盖起点截断**：`first_censored=true` 念「不晚于 X」并说明为什么，
 *     不许念成「就是 X 日」。那一天是哪一天读接口的 `coverage.lo`，页面不背这个日期。
 *  5. **名次那一列**（`rank_in_day`，当日全场名次）与「第一次排进前 N 名」是两个时点；
 *     规则原文与分母都走 `lib/ranking.ts` 那份实现，这一块不自己写规则那句话。
 *
 * 行数一次交完（接口没有 limit 参数），分页每页行数读接口 `page_size`；默认顺序就是后端给的
 * 「整体涨跌幅从低到高」（`useTableSort()` 不给初始键＝不动后端行序），列头仍可点。
 * 单击行列高亮与双击开深度复盘沿用上面那张榜那一份实现 —— hook 实例由页面交进来，
 * 两块各有各的选中态（同一次点击不该把另一张表的第 3 行也点亮）。
 */

interface TrackCell {
  day_no?: number | null;
  trade_date?: string | null;
  pct?: number | null;
  cum?: number | null;
}
interface TrackRow {
  code?: string | null;
  name?: string | null;
  board?: string | null;
  market?: string | null;
  dbl_energy?: number | null;
  /** 当日全场名次（按 dbl_energy 排，规则原文读接口 `rank.rule`）；缺分＝null → —，不是 0 */
  rank_in_day?: number | null;
  bars?: number | null;
  win_days?: number | null;
  first_date?: string | null;
  first_censored?: boolean | null;
  sessions_since?: number | null;
  chg_cum?: number | null;
  missing_base?: boolean | null;
  base_day?: string | null;
  series?: TrackCell[] | null;
}
interface TrackPayload {
  available?: boolean | null;
  reason?: string | null;
  date?: string | null;
  top_n?: number | null;
  top_n_default?: number | null;
  /** 每页行数（只住后端 volume_energy.PAGE_SIZE 一处，界面读它、不写死 60） */
  page_size?: number | null;
  /** 边界同分并列一起进来 ⇒ 实际只数多于 top_n 时为 true（不是出错，更不是截断） */
  rows_capped?: boolean | null;
  /** 名次规则原文（`rank.rule` / `rank_rule_short`）与排的是哪一列；分母在这块里没有，由文案说清 */
  rank?: { rule?: string | null; rule_short?: string | null; by?: string | null } | null;
  fingerprint?: string | null;
  coverage?: { lo?: string | null; hi?: string | null; sessions?: number | null } | null;
  rows?: TrackRow[] | null;
  rows_returned?: number | null;
  max_series_len?: number | null;
  pct_rule?: string | null;
  definitions?: Record<string, string> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  source?: string | null;
}

/** 每页行数的兜底：正常读接口 `page_size`，只有它没交出来时才用这个数（2026-10-19 第二稿）。 */
const PAGE_FALLBACK = 60;

export function EnergyTrackBoard({ nonce, date, vd }: {
  /** 页面右上角那个「重新取数」的计数：跟着它一起重取 */
  nonce: number;
  /** 跟着倍量榜那一格的基准交易日走（留空＝库里最新一场） */
  date: string;
  /** 行列交叉高亮＋双击开深度复盘那一份实现，由页面交进来（两块各一份实例，选中态不互相串） */
  vd: ReturnType<typeof useStockRowInteraction>;
}) {
  /** 母体＝当日前 N 名；null＝不给参数、吃后端 `top_n_default`。界面不自己写 120。 */
  const [topN, setTopN] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [localNonce, bump] = useNonce();
  const track = useProbe<TrackPayload>(
    `/quant/energy-track?date=${encodeURIComponent(date)}`
    + (topN !== null ? `&top_n=${encodeURIComponent(String(topN))}` : "")
    // 按过「重取这一块」之后就不吃那一小时缓存（`refresh=1` 清的是后端 `energy_track_` 那几个键）。
    // 10-26 这一发接口加了进程内缓存：不带这个参数，那个按钮会变成「亮着、却交回同一份」——
    // 全站那条「主按钮不许静默兜旧值」的规矩在这里落地的方式，就是把按钮和参数接在同一件事上。
    + (localNonce > 0 ? "&refresh=1" : ""),
    nonce + localNonce);
  const sort = useTableSort();
  const sortBy = (k: string) => { vd.clearAll(); sort.toggle(k); };
  /** 默认显示值读接口 `top_n_default`（读到之前留空）：界面不背 120 这个数。 */
  const shownDefault = track.kind === "ok" ? track.data.top_n_default : undefined;

  return (
    <Panel title="加权分进榜后的逐日涨跌幅" state={track}
      slowHint="母体、每只票的全史首次进榜日、起算日到今天每一场的后复权涨跌幅都在库里现取：首取实测 3.1 秒（10-26，改前 12.5 秒），之后一小时内同一场同一批 0.69 秒 —— 那 0.69 秒是出体与压缩，库里一件没跑；按过上面「重取这一块」就连那层缓存一起绕过去。同一份返回未压缩 1.88 MB、压缩后 21.4 万字节（列一直铺到今天）"
      note="母体＝基准日这一场加权分的前 N 名（N 是上限，边界同分并列一起进来，所以只数可以多于 N）；每行的起算日是它全史里第一次排进前 N 名那一场，格子里是起算日之后每一场的当日涨跌幅（后复权，与上面榜尾那两列同一把尺）。今天的第几名与最早哪天进榜是两个时点，分开读；名次规则读接口的 rank.rule，分页每页行数读 page_size。"
      controls={<div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
          title="母体＝当日加权分前 N 名。N 是上限、不是恰好几只：名次线落在 N 以内时，边界那一批并列的票一起算进来，所以只数可以比 N 多几只（接口 rows_capped 为 true 就是这种时候）——这张表的行数不保证正好等于你填的 N。为什么不用分数门槛：分值一改，门槛对应的只数就跟着漂，而「前 N 名」与档表无关 —— 怎么改配置这张表都是同样的页数。留空或清空就吃接口回显的 top_n_default（出厂值只住后端一处）。改了立刻重新取数。">
          取前 N 名：
          <input type="number" min={10} max={500} step={10}
            value={topN ?? shownDefault ?? ""}
            onChange={e => {
              const raw = e.target.value.trim();
              vd.clearAll(); setPage(1);
              if (raw === "") setTopN(null);
              else { const n = Number(raw); if (Number.isFinite(n)) setTopN(Math.min(500, Math.max(10, Math.round(n)))); }
            }}
            className="w-20 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
        </label>
        <button type="button" onClick={() => { vd.clearAll(); setPage(1); bump(); }}
          className="flex items-center gap-1 rounded-lg border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
          <RefreshCw className="h-3 w-3" />重取这一块
        </button>
        {/* 用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」——
            控件那一行只留输入框与按钮；母体与上面那张榜的口径区别那一段描述
            （同一件事后端 caveat energy_track_scope 默认在屏上讲）搬进下面默认收起的说明框，措辞原样未改 */}
      </div>}>
      {d => {
        const all = d.rows ?? [];
        /** 每页行数读接口 `page_size`（出厂值只住后端一处）；没交出来才用上面那个兜底。 */
        const PAGE = Number.isFinite(d.page_size ?? NaN) && (d.page_size ?? 0) > 0
          ? Math.trunc(d.page_size as number) : PAGE_FALLBACK;
        const sorted = sort.apply(all);
        const pages = Math.max(1, Math.ceil(sorted.length / PAGE));
        const cur = Math.min(Math.max(1, page), pages);
        const shown = sorted.slice((cur - 1) * PAGE, cur * PAGE)
          .map((r, i) => ({ r, gi: (cur - 1) * PAGE + i }));
        /** 列数读接口 `max_series_len`（这批里最深的一条）。没读到就不开列，不自己猜一个跨度。 */
        const cols = typeof d.max_series_len === "number" && d.max_series_len > 0 ? Math.floor(d.max_series_len) : 0;
        const censored = all.filter(r => r.first_censored === true).length;
        const noCum = all.filter(r => r.chg_cum === null || r.chg_cum === undefined).length;
        const baseDay = d.coverage?.lo ?? null;
        /** 边界并列到底并进来了几只（只数 − N）；读不到就不说这个数，不拿 0 顶。 */
        const extraCount = typeof d.rows_returned === "number" && typeof d.top_n === "number"
          ? d.rows_returned - d.top_n : null;

        return (
          <>
            <Row label="基准交易日" value={dash(d.date)} />
            <Row label="母体档位（后端真应用的值）"
              value={`当日前 ${dash(d.top_n)} 名${d.rows_capped === true
                ? `：边界那一名并列的票一起进来，所以实际是 ${dash(d.rows_returned)} 只（比 ${dash(d.top_n)} 多${extraCount !== null && extraCount > 0 ? ` ${extraCount} 只` : ""}）—— N 是上限，不是恰好 N 只`
                : d.rows_capped === false ? "（这一档边界没有并列，只数正好到这个数）" : ""}`} />
            <Row label="母体只数" value={`${dash(d.rows_returned)} 只（接口没有取数上限参数：进榜的排多少交多少，分页只在这张表下面切，每页 ${PAGE} 行读 page_size）`} />
            <Row label="这一场生效的档表指纹" value={dash(d.fingerprint)} />
            <Row label="每行最多几列" value={`${dash(d.max_series_len)} 列（第 0 场是起算日当天，往后每场一列）`} />

            {/* 截断声明与「同一列不是同一日历日」不许折进默认收起的说明框：藏起来等于没说 */}
            <p className="mt-1 text-[11px] leading-relaxed text-warning">
              <b className="text-foreground">同一列在不同票身上不是同一个日历日</b>：列头那个 +k 场说的是
              这一只自己起算日之后的第 k 场，所以横向对齐读的是<b className="text-foreground">第几场</b>，
              不是<b className="text-foreground">哪一天</b> —— 每一天是哪一天看每一格的悬停。
              {censored > 0 && <> 这一批里有 {censored} 只的起算日带着截断声明（那一格念「不晚于」）。</>}
              {noCum > 0 && <> 另有 {noCum} 只算不出整体涨跌幅：那一格是 —，不是 0%。</>}
            </p>
            {baseDay && (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                这张表最早一场是 {baseDay}：{censored > 0
                  ? <>贴到这一场或更早的那些票，真实的首次进榜<b className="text-foreground">可能更早、库里看不到</b>，
                      所以起算日那一格念「不晚于 {baseDay}」，不念「就是 {baseDay}」。</>
                  : "目前这批票的起算日都没有贴到覆盖起点，所以这一批不需要截断声明。"}
              </p>
            )}

            {/* 声明 / 字段含义 / 覆盖 / 出处收成一处默认收起的折叠块（用户 2026-10-21：
                「把页面上的描述性内容全部收缩起来，避免过多占屏」）—— 原先摊在表前的这三行加起来比表还长。
                **不跟着折的是**：上面那句「同一列不是同一个日历日」的防误读声明、「不晚于」那类截断、
                以及 `vd.popHint`：缺数／截断／失败一族的 caveat 由 `CaliberFold` 自己留在屏上。 */}
            <CaliberFold caveats={d.caveats} definitions={d.definitions}
              coverage={{ volume_double: { from: d.coverage?.lo ?? null, to: d.coverage?.hi ?? null, sessions: d.coverage?.sessions ?? null } }}
              provenance={[d.source]} />

            <div className="mt-2 overflow-x-auto">
              <table className="text-[11px]">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    <SortHeader label="代码" sortKey="code" dir={sort.dirOf("code")} onSort={sortBy} />
                    <SortHeader label="名称" sortKey="name" dir={sort.dirOf("name")} onSort={sortBy} />
                    <SortHeader label="板块" sortKey="board" dir={sort.dirOf("board")} onSort={sortBy} />
                    <SortHeader label="加权分" sortKey="dbl_energy" dir={sort.dirOf("dbl_energy")} onSort={sortBy}
                      title="基准日这一场的倍量加权分（与上面那张榜同一个字段、同一把尺），小数尾零不显示；算式与单位看上面那份返回的 energy.scale，口径原文在 definitions.dbl_energy 里，这一块不另写一份。单位是分，不是次数。" />
                    {/* 今日名次（`rank_in_day`）：这一列是**当日全场**名次，与「第一次排进前 N 名」那个起算日是两个时点。
                        规则原文与分母都在 `lib/ranking.ts` 那份实现里，这一块一个字都不补。 */}
                    <SortHeader label="今日名次" sortKey="rank_in_day" dir={sort.dirOf("rank_in_day")} onSort={sortBy}
                      title={rankTitle(plain(d.rank?.rule ?? ""))} />
                    <SortHeader label="起算日" sortKey="first_date" dir={sort.dirOf("first_date")} onSort={sortBy}
                      title={`这只票在全史里第一次排进前 ${dash(d.top_n)} 名的那一场。库里最早一场是 ${dash(baseDay)}：贴到起点的票真实首进榜可能更早，那一格念「不晚于」。`} />
                    <SortHeader label="至今场数" sortKey="sessions_since" dir={sort.dirOf("sessions_since")} onSort={sortBy}
                      title="起算日之后（起算日当天那一格不算在内）到基准日一共几场；空着是没算出来，不是 0 场。" />
                    <SortHeader label="整体涨跌幅" sortKey="chg_cum" dir={sort.dirOf("chg_cum")} onSort={sortBy}
                      title="基期＝起算日前一场那天的收盘（基期没有行就没有这个数：显示 —，不是 0%）；起算日当天那一格的涨跌算在这一段里。不点列头时行的顺序就是后端给的这一列从低到高。" />
                    {Array.from({ length: cols }, (_, k) => (
                      <th key={k} className="whitespace-nowrap px-1 py-1.5 text-left font-normal text-muted-foreground"
                        title={`第 ${k} 场：这只票起算日之后第 ${k} 场（第 0 场＝起算日当天）。不同票同一列不是同一个日历日，具体哪天看每一格的悬停。`}>
                        {k === 0 ? "第 0 场·起算日" : `+${k} 场`}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shown.map(({ r, gi }) => {
                    const series = r.series ?? [];
                    const cumMissing = r.chg_cum == null;
                    return (
                      <tr key={`${r.code}-${gi}`} {...vd.rowProps(gi, r.code, "hover:bg-primary/5")}>
                        <td {...vd.cellProps(gi, "code", "whitespace-nowrap font-mono")}>
                          <Link className="hover:text-primary"
                            to={`/stock-review?symbol=${encodeURIComponent(String(r.code ?? "").split(".")[0])}`}>{dash(r.code)}</Link>
                        </td>
                        <td {...vd.cellProps(gi, "name", "whitespace-nowrap")}><StockLink code={r.code} name={r.name} className="hover:text-primary" /></td>
                        <td {...vd.cellProps(gi, "board", "whitespace-nowrap text-muted-foreground")}>{dash(r.board)}</td>
                        <td {...vd.cellProps(gi, "dbl_energy", "font-mono tabular-nums")}
                          title={`基准日 ${dash(d.date)} 这一场的加权分；空着是这一场它没有分（那天没有行，或还没按当前档表重算），不是 0 分`}>
                          {r.dbl_energy == null
                            ? <span className="text-muted-foreground/50">—</span>
                            : <span className="font-semibold">{trimNum(r.dbl_energy)}</span>}
                        </td>
                        <td {...vd.cellProps(gi, "rank_in_day", "font-mono tabular-nums")}
                          title={rankTitle(plain(d.rank?.rule ?? ""), null, r.rank_in_day ?? null)}>
                          {r.rank_in_day == null || !Number.isFinite(r.rank_in_day)
                            ? <span className="text-muted-foreground/50">{rankText(r.rank_in_day)}</span>
                            : rankText(r.rank_in_day)}
                        </td>
                        <td {...vd.cellProps(gi, "first_date", "whitespace-nowrap font-mono")}
                          title={r.first_censored === true
                            ? `库里最早一场是 ${dash(baseDay)}，这一只第一次排进前 ${dash(d.top_n)} 名就落在它上面或更早 —— 真实首进榜可能更早、库里看不到，所以这句是「不晚于」，不是「就是这一天」`
                            : r.first_date
                              ? `首次进榜日（第 0 场那一格就是这一天）；整体涨跌幅的分母是它前一场 ${dash(r.base_day)} 的收盘`
                              : "找不到首次进榜日：当前这批里没有它的进榜行（改了档表而重算还没跑完就是这样）。这不等于「它从没进过榜」。"}>
                          {r.first_date
                            ? <>{r.first_censored === true ? "不晚于 " : ""}{r.first_date}</>
                            : <span className="text-muted-foreground/50">—</span>}
                        </td>
                        <td {...vd.cellProps(gi, "sessions_since", "font-mono tabular-nums")}
                          title={`起算日当天算第 0 场，它到基准日一共 ${series.length} 场 ⇒ 之后 ${dash(r.sessions_since)} 场；空着是没算出来，不是 0 场`}>
                          {dash(r.sessions_since)}
                        </td>
                        <td {...vd.cellProps(gi, "chg_cum", "whitespace-nowrap font-mono tabular-nums")}
                          title={cumMissing
                            ? (r.missing_base === true
                              ? "没有基期就没有整体涨跌幅：起算日那一天的前一场在日线里没有行（那天才上市，或基期缺数）。这一格是 —，不是 0%（0% 是一分钱没动）"
                              : "整体涨跌幅没读到：交回的不是 0，界面不拿 0 顶")
                            : `基期 ${dash(r.base_day)} 的收盘 → ${dash(d.date)}：起算日当天那一格的涨跌也算在这一段里`}>
                          {cumMissing
                            ? <span className="text-muted-foreground/50">—</span>
                            : <span className={pctColor(r.chg_cum)}>{pct(r.chg_cum, 2)}</span>}
                        </td>
                        {Array.from({ length: cols }, (_, k) => {
                          const cell = series[k];
                          const p = cell?.pct;
                          const has = typeof p === "number" && Number.isFinite(p);
                          return (
                            <td key={k}
                              {...vd.cellProps(gi, `day_${k}`, "px-1 py-1 font-mono tabular-nums")}
                              title={has
                                ? `${String(cell?.trade_date ?? "这一格的日期后端没交出")}｜这是该票起算日后第 ${k} 场（不同票同一列不是同一个日历日）｜当日 ${p}%`
                                : cell
                                  ? `${String(cell.trade_date ?? "这一格的日期后端没交出")}｜第 ${k} 场：这一天的涨跌幅读不到（不是 0%、也不是平盘）`
                                  : `第 ${k} 场没有行：这只票从起算日到 ${dash(d.date)} 一共只有 ${series.length} 场`
                                    + "（起算日越晚这一行越短，中途停牌也缺）—— 这一格是 —，不是 0%"}>
                              {has
                                ? <span className={pctColor(p)}>{pct(p, 2)}</span>
                                : <span className="text-muted-foreground/50">—</span>}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!all.length && (
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  接口交出 0 行：这一场没有可排名的加权分（库里这一批还没按当前档表重算，或当天压根没数），不是接口坏了 ——
                  两件事后端分开说，上面那句 reason 是原话。
                </p>
              )}
              {/* 弹窗被拦的告警是**告警**，留在框外（下面那句"怎么点"收进说明框了） */}
              {vd.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{vd.popHint}</p>}
              {sorted.length > PAGE && (
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                  <span>共 {sorted.length} 行 · 每页 {PAGE} 行，现在是第 {cur}/{pages} 页
                    （这一页是第 {(cur - 1) * PAGE + 1}~{Math.min(sorted.length, cur * PAGE)} 行）</span>
                  <button type="button" disabled={cur <= 1} onClick={() => { vd.clearAll(); setPage(cur - 1); }}
                    className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70 disabled:opacity-40">上一页</button>
                  <button type="button" disabled={cur >= pages} onClick={() => { vd.clearAll(); setPage(cur + 1); }}
                    className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70 disabled:opacity-40">下一页</button>
                  <span>排序作用在整批 {sorted.length} 行之后再分页；不点列头就是后端给的顺序（整体涨跌幅从低到高）。</span>
                </div>
              )}
            </div>

            <ExplainBox title="这一块怎么读 · 口径原文（后端给出，页面未改写）">
              <p>
                这一块的母体与上面那张榜的「最少加权分」是两回事：榜按分数筛今天这一场，
                这里按<b className="text-foreground">名次</b>取今天这一场的前 N 名，
                但每行往后追的是<b className="text-foreground">这只票第一次排进前 N 名那一场</b>之后的走势。
              </p>
              <p className="mt-1">{STOCK_ROW_HINT}</p>
              <p>{plain(d.pct_rule ?? "接口没交 pct_rule —— 这几列的复权口径就没有出处，页面不自己解释什么叫后复权。")}</p>
              <p className="mt-1">
                列数＝<code>max_series_len</code>（这批里最深的那条）：第 0 列是起算日当天，往后每场一列。
                一只票一行，所以最长的票铺满时，起算日晚的那些票右边会短一截 ——
                短出来的格子是 —（那些场次它还没走到，或那天没有行），不是 0%。
              </p>
              <p className="mt-1 text-muted-foreground">
                「整体涨跌幅」与逐格涨跌幅同尺（后复权）：基期是起算日<b>前一场</b>那天的收盘，
                起算日当天那一格的涨跌也算在这一段里 —— 只想看进榜之后的走势，就从第 1 列起读。
              </p>
            </ExplainBox>
          </>
        );
      }}
    </Panel>
  );
}
