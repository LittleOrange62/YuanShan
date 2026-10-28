/**
 * 趋势股榜的**纯判读层**（零 import，能被 `node --test` 直接钉住）。
 *
 * 这一屏最容易在界面上糊过去的四件事，每件都对应一句会被读者信以为真的话：
 *
 * 1. **三组必须分开交**。主板／创业板／科创板的涨跌停档位不同（10%／20%／20%），
 *    并成一列排名就是拿一个档位的尺子量另一个档位的票；某一组当天一只都没有，
 *    也要保留标题写「当日暂无」—— 少了那个标题，读者以为页面把这一组滤掉了。
 * 2. **「连续站上天数」与「整个窗口一天都没掉过」不是一个问题的答案**。
 *    中间掉一天，前者从 1 重新数，后者整档不成立；页面不许把 `ma5_streak` 当入选条件显示。
 * 3. **新高有两把尺子，而且都不含当日**。`nh_10`／`nh_20` 比的是**前 N 根的最高价**，
 *    与「历史新高」、与库里那种含当日的自然日新高都不是同一个数。
 * 4. **榜被上限截断过时，组内计数与整组汇总只代表已交出的那一段**。
 *    `group_truncated` 由后端交出，页面照着说，不许自己数行充成"全场"。
 */

export type TrendBoard = "主板" | "创业板" | "科创板";

export interface TrendSector {
  code?: string | null; name?: string | null; type?: string | null; pct?: number | null;
}

export interface TrendRow {
  security_code: string; name?: string | null; board?: string | null;
  close?: number | null; pct_change?: number | null;
  amount_yi?: number | null; float_mv_yi?: number | null;
  ma5_streak?: number | null; bars_used?: number | null;
  nh_10?: boolean | null; nh_20?: boolean | null;
  vol_gt_prev?: boolean | null; vol_gt_vma5?: boolean | null;
  limit_ups_in_window?: number | null;
  sectors?: TrendSector[] | null;
}

export interface TrendSummary {
  n?: number | null; scope?: string | null;
  mean_pct?: number | null; median_pct?: number | null;
  mean_amount_yi?: number | null; mean_float_mv_yi?: number | null;
  cnt_nh_10?: number | null; cnt_nh_20?: number | null;
  cnt_vol_gt_prev?: number | null; cnt_vol_gt_vma5?: number | null;
  cnt_with_limit_up?: number | null; boards_present?: number | null;
  [k: string]: unknown;
}

export interface TrendPayload {
  available?: boolean | null; reason?: string | null;
  date?: string | null; date_fell_back?: boolean | null; latest_date?: string | null;
  lookback?: number | null; spec_version?: string | null; min_bars?: number | null;
  boards?: string[] | null; group_counts?: Record<string, number | null> | null;
  group_truncated?: boolean | null;
  universe_rows?: number | null; selected_total?: number | null;
  rows_returned?: number | null; limit?: number | null;
  short_bars_rows?: number | null; no_mv_rows?: number | null; calculated_at?: string | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
  summary?: TrendSummary | null; rows?: TrendRow[] | null;
}

export function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** 涨幅读数：0 是有效值（平盘就是平盘），只有没读到才给 `—`。 */
export function pctText(v: unknown, digits = 2): string {
  const n = numOrNull(v);
  return n === null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

/** 金额读数（亿）。缺值给 `—`，**不补 0** —— 0 亿会被读成"没人交易"。 */
export function yiText(v: unknown): string {
  const n = numOrNull(v);
  return n === null ? "—" : `${n.toFixed(2)} 亿`;
}

/** 三组的固定顺序：空组也留着，页面据此写「当日暂无符合条件的趋势股」。 */
export const BOARD_ORDER: TrendBoard[] = ["主板", "创业板", "科创板"];

export function groupRows(rows: TrendRow[]): { board: TrendBoard; rows: TrendRow[] }[] {
  return BOARD_ORDER.map(board => ({
    board,
    rows: rows.filter(r => String(r.board ?? "").trim() === board),
  }));
}

/**
 * 亮起的量价特征。顺序照那份说明：两把新高尺子 → 两把放量尺子 → 近 N 日 X 板。
 * `近 N 日 X 板` 只在次数 >0 时才写；0 次不写这一枚（写了就等于凭空多出一枚标签）。
 * 五日线趋势已经是入选条件，不再单独做成标签 —— 整屏每一行都成立的那件事没有信息量。
 */
export function featureTags(r: TrendRow, lookback: number | null): { label: string; on: boolean }[] {
  const out = [
    { label: "10 日新高", on: r.nh_10 === true },
    { label: "20 日新高", on: r.nh_20 === true },
    { label: "较昨日放量", on: r.vol_gt_prev === true },
    { label: "较 5 日均量放量", on: r.vol_gt_vma5 === true },
  ];
  const n = numOrNull(r.limit_ups_in_window);
  if (n !== null && n > 0) out.push({ label: `近 ${numOrNull(lookback) ?? "?"} 日 ${n} 板`, on: true });
  return out;
}

/** 相关板块：名称 + 那个指数当天的涨幅。后端交出几个显几个，页面不补到 3 个。 */
export function sectorTags(list?: TrendSector[] | null): string {
  const arr = (list ?? []).filter(s => String(s?.name ?? "").trim());
  if (!arr.length) return "—";
  return arr.map(s => `${String(s.name)} ${pctText(s.pct)}`).join("、");
}

/**
 * 这一场读不读得动，一句话交给界面。
 *
 * `date_fell_back` 是"你要的那天没有，我用库里最新那天顶上了"—— 这句必须说，
 * 否则读者会把自己指定的日期当成屏幕上这一天。`selected_total=0` 与"没读到"也是两件事：
 * 前者是**今天确实一只都不符合**，后者要说链路。
 */
export function stateSentence(d: TrendPayload): { tone: "ok" | "warn"; text: string } {
  const sel = numOrNull(d.selected_total);
  if (d.available === false) {
    return { tone: "warn", text: `这一屏没读到数：${String(d.reason ?? "原因未交出")} —— 不是"没有趋势股"。` };
  }
  if (d.date_fell_back) {
    return { tone: "warn",
      text: `你点的那天（${String(d.date ?? "?")}）库里没有这一版的行，`
        + `页面显示的是库里最新的一场 ${String(d.latest_date ?? "?")} —— 别把它读成你点的那一天。` };
  }
  if (sel === 0) {
    return { tone: "ok", text: "这一天确实一只都不符合四条入选条件（不是没读到，也不是页面滤掉了）。" };
  }
  return { tone: "ok", text: "" };
}

/** 截断那句：只在后端说"截断了"时才说，且说清组内计数与汇总的代表范围。 */
export function truncationSentence(d: TrendPayload): string {
  if (d.group_truncated !== true) return "";
  const back = numOrNull(d.rows_returned), whole = numOrNull(d.selected_total), lim = numOrNull(d.limit);
  return `榜被上限截断了：交出 ${back === null ? "?" : back} 行 / 这一场实际入选 ${whole === null ? "?" : whole} 只`
    + `（后端 limit=${lim === null ? "未交出" : lim}）。组内计数与整组汇总只代表已交出的那一段，不代表全场 ——`
    + " 要读全场请把上限调大。";
}

/** 母体与覆盖那一句：只念后端交出的数，页面不自己数。 */
export function coverageSentence(d: TrendPayload): string {
  const uni = numOrNull(d.universe_rows), sel = numOrNull(d.selected_total);
  const win = numOrNull(d.lookback), min = numOrNull(d.min_bars);
  const short = numOrNull(d.short_bars_rows), noMv = numOrNull(d.no_mv_rows);
  const parts = [
    `母体 ${uni === null ? "未交出" : uni} 行（主板／创业板／科创板全部有日线的票，北交所不纳入）`,
    `入选条件里的周期 = 近 ${win === null ? "?" : win} 个交易日，`
      + `至少要有 ${min === null ? 9 : min} 根收盘价才谈得上形状`,
  ];
  if (short !== null && short > 0) parts.push(`其中 ${short} 行往前凑不满根数（次新或长期停牌），按口径不进榜`);
  if (noMv !== null && noMv > 0) parts.push(`${noMv} 行没有流通市值（既无分段股本也无当前快照），那一格留空不补 0`);
  parts.push(`这一场实际入选 ${sel === null ? "?" : sel} 只`);
  return parts.join("；") + "。";
}

/** 整组读数一句话：只读 `summary`，不碰行。 */
export function groupReading(s: TrendSummary | null | undefined): { tone: "ok" | "warn"; text: string } {
  const n = numOrNull(s?.n);
  if (n === null || n === 0) {
    return { tone: "warn", text: s ? "这一场整组没有票（summary.n=0）—— 界面不猜它们为什么都不符合。"
      : "后端没交 summary —— 整组读数这一屏说不出，界面不自己按行凑一个。" };
  }
  const nh = numOrNull(s?.cnt_nh_10), mv = numOrNull(s?.cnt_vol_gt_vma5);
  const med = numOrNull(s?.median_pct);
  return { tone: "ok",
    text: `这批 ${n} 只票（${String(s?.scope ?? "")}）里，当日中位涨幅 ${pctText(med)}；`
      + `站在 10 日新高上的 ${nh === null ? "?" : nh} 只，较 5 日均量放量的 ${mv === null ? "?" : mv} 只。`
      + " 这只描述它们现在长什么样，不说明接下来会怎样 —— 母体本来就是每天收盘都在 5 日线上方的那批票。" };
}
