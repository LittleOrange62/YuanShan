/**
 * 明日展望（`/tomorrow-outlook`）的类型与纯函数。
 *
 * 搬进 lib 的理由与 `lib/trendStocks.ts`、`lib/sentimentCompare.ts` 同一条：
 * 判读逻辑（谁进哪一榜、名次能不能排、缺值念什么）圈在一个不打 DOM 就能测的地方，
 * 组件里只画。⚠️ 这里**一个新判据都不算**：两张榜、门槛、母体全部读后端交出的字段。
 */

import { NULL_TEXT, dash, withUnit } from "./datastate.ts";

export interface OutlookIndicator {
  key: string;
  name?: string | null;
  unit?: string | null;
  rankable?: boolean | null;
  decimals?: number | null;
  value?: number | string | null;
  rank?: number | null;
  family_n?: number | null;
}

export interface OutlookStock {
  code: string;
  name?: string | null;
  in_hits?: boolean | null;
  in_trends?: boolean | null;
  indicators?: OutlookIndicator[] | null;
  indicator_count?: number | null;
  missing_indicators?: string[] | null;
}

export interface HitRow {
  security_code: string;
  name?: string | null;
  board?: string | null;
  no?: number | null;
  hits_today?: number | null;
  hits_family?: number | null;
  days_in_top?: number | null;
  window_sessions?: number | null;
  rank_sum?: number | null;
  char_level?: string | null;
  top_inds?: string[] | null;
  consec?: number | null;
  reason?: string | null;
}

export interface TrendRow {
  security_code: string;
  name?: string | null;
  board?: string | null;
  no?: number | null;
  ma5_streak?: number | null;
  pct_change?: number | string | null;
  nh_20?: boolean | null;
  sealed?: boolean | null;
  pos250?: number | string | null;
  char_level?: string | null;
}

export interface OutlookParams {
  family?: string[] | null;
  family_names?: string[] | null;
  family_size?: number | null;
  top_n?: number | null;
  min_hits?: number | null;
  window_sessions?: number | null;
  hits_n?: number | null;
  trend_streak_min?: number | null;
  trend_n?: number | null;
  char_keep?: string[] | null;
  config_read?: boolean | null;
  config_unread_reason?: string | null;
}

export interface Caveat { code?: string | null; label?: string | null; text?: string | null; hard?: boolean }

export interface OutlookPayload {
  available?: boolean | null;
  reason?: string | null;
  date?: string | null;
  generated_at?: string | null;
  cache_age_seconds?: number | null;
  cached?: boolean | null;
  ttl_seconds?: number | null;
  params?: OutlookParams | null;
  hits?: HitRow[] | null;
  trends?: TrendRow[] | null;
  overlap?: { code: string; name?: string | null }[] | null;
  top_boards?: { name?: string | null; consec?: number | null }[] | null;
  expanded?: boolean | null;
  stocks?: Record<string, OutlookStock> | null;
  rank_note?: string | null;
  caliber?: string | null;
  pushed?: {
    body?: string | null; at?: string | null; sent_ok?: boolean | null;
    delivered?: boolean | null; channels?: string | null; send_err?: string | null;
  } | null;
  universe?: { indicator_codes_day?: number | null; character_scored_day?: number | null } | null;
  caveats?: Caveat[] | null;
}

/** 中文档名只从后端那份标签走（真源 `app/compute/stock_character.py::CHAR_LEVEL_LABELS`）。 */
export function charLevelText(level?: string | null): string {
  if (level === "strong") return "股性强";
  if (level === "middle") return "股性中";
  if (level === "weak") return "股性弱";
  // NULL 不是"股性差"：上市不足 120 场或档位价缺 ⇒ 判不了，屏上是 —（后端把它照留在榜上并点名）
  return level ? String(level) : NULL_TEXT;
}

/** 「今日 6/9 项进 top100 · 3/3 场上榜」——一、那一行的命中摘要，全部字段来自后端。 */
export function hitSummary(r: HitRow): string {
  return `今日 ${dash(r.hits_today)}/${dash(r.hits_family)} 项 · 近 ${dash(r.window_sessions)} 场里 ${dash(r.days_in_top)} 场上榜`;
}

/** 二、那一行的命中摘要。`pct_change` 为空要说"今日缺数"，不写 0。 */
export function trendSummary(r: TrendRow): string {
  const bits = [`站上 5 日线 ${dash(r.ma5_streak)} 日`];
  if (r.nh_20) bits.push("20 日新高");
  if (r.sealed) bits.push("今封板");
  return bits.join(" · ");
}

export interface StockRow {
  code: string;
  name: string;
  board: string;
  summary: string;
  in_hits: boolean;
  in_trends: boolean;
  indicator_count: number;
  missing_count: number;
  /** sortKey 走 `ind.<key>.v` 这一层嵌套取数（`lib/useSort.ts` 支持 `a.b.c`） */
  ind: Record<string, { v: number | string | null; r: number | null; n: number | null }>;
}

/**
 * 把 `stocks` 那本字典换成"一行一只票"的数组，并按榜单顺序排（一、在前、二、在后、交集不重复列）。
 *
 * 顺序只跟两张榜的原始行序，不在页面重排 —— 后端已经按"今日命中项数、重复上榜场次、XY 合计名次"
 * 排过一次，页面再排一遍就是第二把尺。
 */
export function stockRows(d: OutlookPayload | null | undefined): StockRow[] {
  const map = d?.stocks ?? {};
  const order: string[] = [];
  for (const r of d?.hits ?? []) if (map[r.security_code] && !order.includes(r.security_code)) order.push(r.security_code);
  for (const r of d?.trends ?? []) if (map[r.security_code] && !order.includes(r.security_code)) order.push(r.security_code);
  for (const c of Object.keys(map)) if (!order.includes(c)) order.push(c);
  const hitBy = new Map((d?.hits ?? []).map(r => [r.security_code, r]));
  const trBy = new Map((d?.trends ?? []).map(r => [r.security_code, r]));
  return order.map(code => {
    const s = map[code];
    const ind: StockRow["ind"] = {};
    for (const i of s?.indicators ?? []) {
      ind[i.key] = { v: i.value ?? null, r: i.rank ?? null, n: i.family_n ?? null };
    }
    const h = hitBy.get(code); const t = trBy.get(code);
    return {
      code,
      name: s?.name || (h?.name ?? t?.name) || code,
      board: h?.board ?? t?.board ?? "",
      summary: [h ? hitSummary(h) : null, t ? trendSummary(t) : null].filter(Boolean).join(" ｜ ") || "—",
      in_hits: Boolean(s?.in_hits ?? h),
      in_trends: Boolean(s?.in_trends ?? t),
      indicator_count: s?.indicator_count ?? (s?.indicators ?? []).length,
      missing_count: (s?.missing_indicators ?? []).length,
      ind,
    };
  });
}

/** 全部指标列的键与中文名：按中文名排序（同一族自然挨在一起），清单只从后端 `stocks[]` 里收。 */
export function indicatorColumns(rows: StockRow[], d: OutlookPayload | null | undefined): OutlookIndicator[] {
  const seen = new Map<string, OutlookIndicator>();
  for (const r of rows) {
    const s = d?.stocks?.[r.code];
    for (const i of s?.indicators ?? []) if (!seen.has(i.key)) seen.set(i.key, i);
  }
  return [...seen.values()].sort((a, b) => (a.name ?? a.key).localeCompare(b.name ?? b.key, "zh-Hans-CN"));
}

/**
 * 一格里读到的东西：值（带单位）与名次两行。
 *
 * ⚠️ `rankable=false` 的那 15 项（单位 yuan 的绝对价格族 + 涨幅排名）**不许出现名次**：
 * `ma5` 排全场第 8 名只说明它贵，把名次摆出来就是让屏幕说一句假话。
 */
export function indicatorCell(i: OutlookIndicator, v: number | string | null,
                              r: number | null, n: number | null): { text: string; rank: string; hint: string } {
  const text = withUnit(v, i.unit ?? "", i.decimals ?? null);
  const rank = i.rankable && r !== null && r !== undefined ? `第 ${r} 名` : "";
  const bits: string[] = [`${i.name ?? i.key}（键 ${i.key}）`];
  if (i.unit) bits.push(`单位 ${i.unit}`);
  if (i.rankable) {
    bits.push(r === null || r === undefined
      ? "这一项当天没有值 ⇒ 没有名次（不补 0，也不排最后）"
      : `全场第 ${r} 名，母体＝当天这一项有值的 ${dash(n)} 只（各项母体不一样，别拿一个分母解释所有名次）`);
  } else {
    bits.push(i.unit === "yuan"
      ? "绝对价格族：跨票不可比（高价股天然靠前），只给值不给名次"
      : "这一项本身就是名次/计数，不再排第二次");
  }
  bits.push("悬停里的名次解释；排序按值（同一列里名次随值单调，两者是一个东西的两种写法）");
  return { text, rank, hint: bits.join(" ｜ ") };
}

/** 顶部那几块大数字。标签与数值都从后端字段拼，页面不自己数。 */
export function headlineCards(d: OutlookPayload): { label: string; value: string; hint: string }[] {
  const p = d.params ?? {};
  const u = d.universe ?? {};
  const both = (d.overlap ?? []).length;
  return [
    { label: "交易日", value: dash(d.date), hint: `这份读数和 19:00 那一炉用的是同一份判据（`
      + `${dash(d.cache_age_seconds)} 秒前实算，缓存 15 分钟）` },
    { label: "一、多指标靠前", value: `${(d.hits ?? []).length} 只`,
      hint: `家族 ${dash(p.family_size)} 项各取当日前 ${dash(p.top_n)} 名，今日命中 ≥${dash(p.min_hits)} 项者进榜；`
        + `近 ${dash(p.window_sessions)} 场重复上榜一起排` },
    { label: "二、趋势最正", value: `${(d.trends ?? []).length} 只`,
      hint: `趋势管线当日 selected5 且收盘在 5 日线上方、站上 ≥${dash(p.trend_streak_min)} 日` },
    { label: "两榜都进", value: both ? `${both} 只` : NULL_TEXT,
      hint: both ? (d.overlap ?? []).map(o => o.name || o.code).join("、")
        : "今天两榜没有交集 —— 这是实质读数（一只都没重合），不是没读到" },
    { label: "逐票摊开", value: `${Object.keys(d.stocks ?? {}).length} 只`,
      hint: `当日有名次母体的票 ${dash(u.indicator_codes_day)} 只、股性判得了 ${dash(u.character_scored_day)} 只` },
  ];
}

/** 股性过滤那一档的中文（`char_keep` 是后端参数，页面不写死"强"）。 */
export function keepText(p: OutlookParams | null | undefined): string {
  const keep = p?.char_keep ?? [];
  return keep.length ? keep.map(charLevelText).join("、") : "不过滤";
}

/* ─────────────────────────── 6 个月回溯（策略有效性那一块） ─────────────────────────── */
/**
 * 用户 2026-11-05 点名：策略最近 6 个月每天算出来的个股，往后 1/2/3 个交易日的涨跌、
 * 冲高率、整体高点数值涨幅，算整体平均值看有效性。后端只读
 * `analytics.outlook_pick_daily` / `outlook_baseline_daily`（写侧
 * `app/compute/outlook_backtest.py`，判据与本屏同一份），页面不重算。
 */

export interface BtMetric {
  mean?: number | null;
  median?: number | null;
  win_pct?: number | null;
  ge3_pct?: number | null;
  le3_pct?: number | null;
}

export interface BtSummary {
  n?: number | null;
  gap?: BtMetric | null;
  hi1?: BtMetric | null;
  cl1?: BtMetric | null;
  cl2?: BtMetric | null;
  cl3?: BtMetric | null;
  hi3?: BtMetric | null;
  up3d_rate_pct?: number | null;
  up3d_n?: number | null;
}

export interface BtBaseline {
  sessions?: number | null;
  gap?: number | null;
  hi?: number | null;
  cl?: number | null;
  cl2?: number | null;
  cl3?: number | null;
  hi3?: number | null;
  hi3_win_pct?: number | null;
}

export interface BtPick {
  code: string;
  name?: string | null;
  board?: string | null;
  sec: "hits" | "trend";
  in_trend?: boolean | null;
  rank?: number | null;
  rank_trend?: number | null;
  gap?: number | null;
  hi?: number | null;
  cl?: number | null;
  cl2?: number | null;
  cl3?: number | null;
  hi3?: number | null;
  up3d?: boolean | null;
}

export interface BtDay { date: string; picks: BtPick[] }

export interface BtPayload {
  available?: boolean | null;
  reason?: string | null;
  window?: {
    from?: string | null; to?: string | null; sessions?: number | null;
    filled_this_request?: { filled_days?: number | null; note?: string | null } | null;
  } | null;
  params?: OutlookParams | null;
  params_snapshot?: Record<string, unknown> | null;
  algo_stamp?: string | null;
  drift?: boolean | null;
  summary?: { hits?: BtSummary | null; trend?: BtSummary | null; both?: BtSummary | null } | null;
  baseline?: BtBaseline | null;
  days?: BtDay[] | null;
  computed_at?: string | null;
  caliber?: string | null;
  caveats?: Caveat[] | null;
  cache_age_seconds?: number | null;
  cached?: boolean | null;
  ttl_seconds?: number | null;
}

/** 带符号百分数；缺值念 `—`（NULL 是"判不了/还没发生"，不是 0%）。 */
export function signedPct(v: number | string | null | undefined, digits = 2): string {
  const n = typeof v === "string" ? Number(v) : v;
  return typeof n === "number" && Number.isFinite(n)
    ? `${n > 0 ? "+" : ""}${n.toFixed(digits)}%` : NULL_TEXT;
}

/** 汇总那一格的主体：均值 +（胜率 / 可判数，视指标而定）。 */
export function btCell(m: BtMetric | null | undefined, upN?: number | null): string {
  if (!m || m.mean == null) return NULL_TEXT;
  let s = signedPct(m.mean);
  if (m.win_pct != null) s += `（胜 ${Math.round(m.win_pct)}%）`;
  else if (upN != null) s += `（可判 ${upN} 笔）`;
  return s;
}

/** 逐场那一格的主体；两榜都进的票在 pick 表里是两行，合成一行显示两个名次。 */
export function btPickTag(p: BtPick): string {
  const bits: string[] = [];
  if (p.sec === "hits") bits.push(`靠前榜 #${dash(p.rank)}`);
  if (p.sec === "trend") bits.push(`趋势榜 #${dash(p.rank)}`);
  else if (p.in_trend) bits.push(`趋势榜 #${dash(p.rank_trend)}`);
  return bits.join(" · ");
}
