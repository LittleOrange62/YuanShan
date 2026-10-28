/**
 * 「个股信息」那一块的**纯判读层**（零 import，能被 `node --test` 直接钉住）。
 *
 * 接口是 `GET /api/stock/facts`，取数与字段义只住 `duanxian/stock_facts.py` 一处。
 * 这里只做三件界面上最容易糊的事，都不新增判据：
 *  1. **每个数各带自己的日期**：行情那一格说的是 `session.trade_date`，档案那一格说的是
 *     `profile.hq_date` 并给出"几天没刷"。把两者混成"今天的信息"就是假话。
 *  2. **null 不补 0**：主档的上市日/总股本本机全表为空、收盘快照的每股类全是 0，
 *     所以这些要么不显示，要么显示「—」，绝不显示 0（0 会被读成"这家公司没有员工/市值为零"）。
 *  3. **本机库 vs 公开源那一场的差**要说明白：这一块不出网，比下面「个股研究」那张卡可能晚一场。
 */

export interface FactsSession {
  trade_date?: string | null;
  close?: number | null; pre_close?: number | null; pct_change?: number | null;
  amount_yi?: number | null; turnover_rate?: number | null;
  limit_up?: number | null; limit_down?: number | null;
  pe_ttm?: number | null; pb?: number | null;
  total_mv_yi?: number | null; float_mv_yi?: number | null;
  snapshot_present?: boolean | null;
}

export interface FactsProfile {
  available?: boolean | null; reason?: string | null;
  hq_date?: string | null; main_business?: string | null; staff_num?: number | null;
  ipo_price?: number | null; his_high?: number | null; his_low?: number | null;
  beta?: number | null; profile_age_days?: number | null;
}

export interface FactsSector { sector_code: string; name?: string | null;
  sector_type?: string | null; members?: number | null; source?: string | null }

export interface FactsGroup { group_id?: string | null; group_name?: string | null;
  trade_date?: string | null }

/** 逐段取舍回执的一条（后端 `SEGMENTS` 一处算，页面只转述）。
 *  `total` 是这一段本轮源给了几条、`taken` 是实际喂进模型几条 —— 「研报 200 条只喂了 8 条」
 *  这句话就靠这两个数，别在页面上自己数一遍。 */
export interface BriefSegment { key: string; label: string; taken: number; total: number;
  truncated: boolean; dropped: boolean; note: string }

/** 「个股信息」那一格带的 AI 公司说明（`analytics.stock_brief` 的一行，读侧原样交出）。
 *  注意 `brief: null` 与"这一整个对象是 null"是两件事：后者是**还没生成过**。 */
export interface FactsBrief {
  brief?: string | null; brief_chars?: number | null; word_limit?: number | null;
  material_chars?: number | null; material_segments?: BriefSegment[] | null;
  material_missing?: string[] | null; model?: string | null; ai_provider?: string | null;
  prompt_slot?: string | null; prompt_source?: string | null;
  input_fetched_at?: string | null; generated_at?: string | null;
  error?: string | null; failed_at?: string | null;
  spec_version?: string | null; caliber?: string | null;
  definitions?: Record<string, string> | null;
}

export interface FactsPayload {
  available: boolean; reason?: string | null; code: string; spec?: string | null;
  name?: string | null; symbol?: string | null; market?: string | null;
  board?: string | null; asset_type?: string | null; is_st?: boolean | null;
  list_date?: string | null; pinyin_abbr?: string | null;
  total_share?: number | null; float_share?: number | null;
  session?: FactsSession | null; profile?: FactsProfile | null;
  sectors?: FactsSector[] | null; groups?: FactsGroup[] | null;
  brief?: FactsBrief | null; brief_spec_version?: string | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  caveats?: { code?: string; text?: string }[] | null;
}

export function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** 亿元读数：缺就是「—」，0 就显示 0（0 亿是真的 0，不是没数）。 */
export function yi(v: number | null | undefined, decimals = 2): string {
  const n = numOrNull(v);
  return n === null ? "—" : `${n.toFixed(decimals)} 亿`;
}

export function pct(v: number | null | undefined, decimals = 2): string {
  const n = numOrNull(v);
  if (n === null) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(decimals)}%`;
}

export function yuan(v: number | null | undefined): string {
  const n = numOrNull(v);
  return n === null ? "—" : n.toFixed(2);
}

/** 当日涨跌的配色档（涨/跌/平/缺）。缺数不给颜色 —— 给绿或给红都是在替读者编结论。 */
export function pctTone(v: number | null | undefined): "up" | "down" | "flat" | "missing" {
  const n = numOrNull(v);
  if (n === null) return "missing";
  if (n > 0) return "up";
  if (n < 0) return "down";
  return "flat";
}

/** 两个日期是不是同一场。不同就必须在界面上各说各的日期，不许并成一句。 */
export function sessionProfileSameDay(s?: FactsSession | null, p?: FactsProfile | null): boolean | null {
  if (!s?.trade_date || !p?.hq_date) return null;
  return s.trade_date === p.hq_date;
}

/** 档案多久没刷：≤30 天不吭声，31~90 报一句，>90 报"这份档案很旧"。 */
export function profileAgeSentence(days: number | null | undefined): string {
  const n = numOrNull(days);
  if (n === null) return "档案没交刷新时间，这一格的新旧不知道（不是「刚刷过」）";
  if (n <= 30) return `档案更新于 ${n} 天前`;
  if (n <= 90) return `这份个股档案 ${n} 天没刷新（它本来就一年刷几次）`;
  return `这份个股档案已 ${n} 天没刷新，主营/员工数/β 只能当旧闻读，别看成当期`;
}

/** 板块名单：行业在前、概念在后，名字后面标成分只数（窄概念 3 只与宽概念 600 只不是一回事）。 */
export function sectorLabels(rows: FactsSector[] | null | undefined, max = 10): string {
  const list = (rows ?? []).filter(r => r && r.name);
  if (!list.length) return "本机字典里没登记它的板块归属";
  return list.slice(0, max)
    .map(r => `${r.name}(${numOrNull(r.members) ?? "—"}只)`)
    .join("、") + (list.length > max ? `…共 ${list.length} 个` : "");
}

export function groupNames(rows: FactsGroup[] | null | undefined): string[] {
  return (rows ?? []).map(g => g.group_name || g.group_id || "").filter(Boolean);
}

/**
 * 「这票在本机是什么身份」那一句。三条独立事实拼出来，缺一条就少一条，不编：
 * 板块档 / ST 标记 / 是否在本机名单（哪几组）。
 */
export function identitySentence(d: FactsPayload): string {
  const bits: string[] = [];
  if (d.board) bits.push(String(d.board));
  if (d.is_st) bits.push("ST 档（涨跌停与异动判据都按 ST 走）");
  const gs = groupNames(d.groups);
  bits.push(gs.length ? `在本机名单：${gs.join("、")}` : "不在本机自选名单里");
  return bits.join(" · ");
}
