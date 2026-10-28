/**
 * 异动提醒的纯判读层（零 import，能被 `node --test` 直接钉住）。
 *
 * 这一屏每一块都有一句"读错了就当实话"的话，全部收在这里：
 *
 * 1. `severe=null` 是判不了，不是没踩到。 窗口根数不够（新股／长停牌）、上市前 5 个
 *    竞价交易日、那天指数那根线没入库 —— 三种都会交 null。把它并进"没踩到"的计数里，
 *    屏幕上就是一句"今天只有 10 只踩到"的假话（实测 2026-10-15：另有 1 只是判不了）。
 * 2. 前瞻那块问的是"明日涨停会不会**新**踩到一条"。所以：`band_next=null` ⇒ 整行判不了（不是 0%）；
 *    `trigger_pct > 涨停幅度` ⇒ 明天涨停也够不着**下一条**，它不进榜的那一支；
 *    今天已经踩到某一条的票 10-27 起**照样进榜**（旧版整只挡在外面，用户点名的就是这几只消失），
 *    靠 `fwd_state` 分成两类念：`will_trigger` = 明天涨停就踩到下一条，
 *    `already_partial` = 已踩到哪条、离下一条还差多少个点。整张榜两类并存，只报一个数就会对不上行数。
 *    ⚠️ 「距 100/200 还差几个点」与「触发所需涨幅」是两个数：累计走区间复利后，
 *    明日每涨 1 个点会把已经涨过的那一段再放大一层，缺口 ≠ 涨幅。
 * 3. 次日涨幅 null 有两种：次日还没到（跑的就是最新一场），与次日整场缺席（停牌核查）。
 *    前者不能说"停牌"，后者不能说"平盘" —— 靠 `next_trade_date` / `next_missing` 分开。
 * 4. 「几天几板」读不到时是"没读到"，不是"这只票没有板"：那份实现与首页连板榜同一处，
 *    它读不到库的时候交回空对象，界面必须说这一句。
 */

export interface AbnCaveat { code?: string | null; text?: string | null; hard?: boolean | null }

export interface AbnForwardRow {
  security_code: string; name?: string | null; board?: string | null;
  is_st?: boolean | null; close?: number | null; pct_change?: number | null;
  dev_10?: number | null; dev_30?: number | null; up_cnt_10?: number | null;
  band_next?: number | null; band_pct?: number | null; limit_price_next?: number | null;
  trigger_pct?: number | null; trigger_price?: number | null;
  need_dev_to_100?: number | null; need_dev_to_200?: number | null;
  dev_10_if_limit?: number | null; dev_30_if_limit?: number | null;
  fwd_clauses?: string | null; fwd_clauses_label?: string | null;
  times_if_limit?: boolean | null;
  // 10-27 起这张榜两类并存，"已踩到哪一条"必须由后端交出来（页面不重算判据）
  severe?: boolean | null; severe_how?: string | null; severe_how_label?: string | null;
  severe_reason?: string | null; status_label?: string | null;
  fwd_state?: string | null; fwd_state_label?: string | null;
}

export interface AbnHotRow {
  security_code: string; name?: string | null; board?: string | null;
  is_st?: boolean | null; pct_change?: number | null;
  dev_3?: number | null; dev_10?: number | null; dev_30?: number | null;
  dev_3_complete?: boolean | null; dev_10_complete?: boolean | null; dev_30_complete?: boolean | null;
  up_cnt_10?: number | null; down_cnt_10?: number | null;
  severe?: boolean | null; severe_how?: string | null; severe_dir?: string | null;
  severe_reason?: string | null; severe_how_label?: string | null; severe_dir_label?: string | null;
  status_label?: string | null;
  universe_by?: string | null; universe_by_label?: string | null;
  bars_used?: number | null; listed_days?: number | null;
  board_span?: { days?: number | null; boards?: number | null; label?: string | null;
                 sealed_today?: boolean | null; batch_empty?: boolean | null } | null;
}

export interface AbnRecentRow extends AbnHotRow {
  trade_date?: string | null;
  next_trade_date?: string | null; next_pct?: number | null;
  next_halted?: boolean | null; next_missing?: boolean | null;
}

export interface AbnSummary {
  universe?: number | null; severe?: number | null; severe_unknown?: number | null;
  not_severe?: number | null; forward?: number | null; forward_unknown?: number | null;
  no_band?: number | null; ab_this_day?: number | null; severe_down?: number | null;
  max_dev10?: number | null; calculated_at?: string | null; scope?: string | null;
}

export interface AbnormalMove {
  available?: boolean; reason?: string | null;
  date?: string | null; latest_date?: string | null; date_fell_back?: boolean | null;
  spec_version?: string | null;
  summary?: AbnSummary | null;
  caliber?: string | null; definitions?: Record<string, string> | null;
  caveats?: AbnCaveat[] | null;
  thresholds?: Record<string, number> | null;
  forward?: { rows?: AbnForwardRow[] | null; returned?: number | null;
              limit?: number | null; truncated?: boolean | null } | null;
  hot?: { rows?: AbnHotRow[] | null; returned?: number | null; limit?: number | null } | null;
  recent_severe?: { rows?: AbnRecentRow[] | null; sessions?: number | null;
                    total?: number | null; total_unread?: boolean | null;
                    truncated?: boolean | null } | null;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** 涨幅：缺数给「—」，不给 0（0 会被读成平盘）。 */
export function pctText(v?: number | null, digits = 2): string {
  if (!isNum(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** 偏离值单位是百分点（10-27 起累计走区间复利，但交出来的仍是"几个点"，不是"百分之几"）。 */
export function ptsText(v?: number | null, digits = 1): string {
  if (!isNum(v)) return "判不了";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)} 点`;
}

/** 价格：null 说「取不到」，不说 0.00。 */
export function priceText(v?: number | null): string {
  return isNum(v) ? v.toFixed(2) : "—";
}

export function bandText(r: AbnForwardRow): string {
  if (!isNum(r.band_pct)) return "明日涨停幅度取不到（前瞻判不了）";
  return `${r.band_pct.toFixed(0)}%`;
}

/** 三态：踩到 / 没踩到 / 判不了。`null` 绝不并进"没踩到"。 */
export function severeState(r: { severe?: boolean | null; severe_reason?: string | null }): {
  label: string; tone: "danger" | "ok" | "warn" } {
  if (r.severe === true) return { label: "已踩到", tone: "danger" };
  if (r.severe === false) return { label: "没踩到", tone: "ok" };
  return { label: `判不了${r.severe_reason ? `（${r.severe_reason}）` : ""}`, tone: "warn" };
}

/** 「异动状态」= 后端交的那一整格文本（方向 · 条款）。
 *  列的 sortKey 必须就是这一格读到的字段，所以优先用 `status_label`；没有它才退回自己拼。 */
export function moveStatusText(r: AbnHotRow | AbnRecentRow): string {
  if (typeof r.status_label === "string" && r.status_label) return r.status_label;
  if (r.severe !== true) return severeState(r).label;
  const how = r.severe_how_label || r.severe_how || "踩到了却没交条款";
  return `${r.severe_dir_label || r.severe_dir || "方向未交"} · ${how}`;
}

/** 次日那一格：三种缺席分开说，一种都不写成 0%。 */
export function nextDayState(r: AbnRecentRow): { text: string; tone: "danger" | "ok" | "warn" | "muted" } {
  if (isNum(r.next_pct)) {
    return { text: pctText(r.next_pct), tone: r.next_pct < 0 ? "ok" : "danger" };
  }
  if (r.next_halted) return { text: "次日停牌（有证据的缺席）", tone: "warn" };
  if (r.next_missing) return { text: "次日整场没这根线（缺席，未证停牌）", tone: "warn" };
  if (!r.next_trade_date) return { text: "次日还没到", tone: "muted" };
  return { text: "次日涨幅没读到", tone: "warn" };
}

/** 几天几板：读不到就说"没读到"，不说"0 天 0 板"。 */
export function boardSpanText(r: AbnHotRow): string {
  const sp = r.board_span;
  if (!sp) return "没读到";
  if (sp.label) return sp.label;
  if (typeof sp.boards === "number") return `${sp.days ?? "?"}天${sp.boards}板`;
  // 整批一个都没有：分不清"这批此刻都不在涨停轮"与"那份实现没读到库"，两种都说出。
  return sp.batch_empty ? "没读到（这一批都没数出当前涨停轮）" : "不在当前涨停轮";
}

/** 窗口根数不够时那一格说"判不了"，不显示一个偏小的累计值。 */
export function devCell(v: number | null | undefined, complete?: boolean | null): string {
  if (complete === false) return "窗口不够";
  return ptsText(v);
}

export function forwardHeadline(d: AbnormalMove): { text: string; tone: "warn" | "muted" } {
  const s = d.summary ?? {};
  const n = s.forward ?? 0;
  const unk = s.forward_unknown ?? 0;
  const noBand = s.no_band ?? 0;
  // 这张榜 10-27 起两类并存：明天涨停就新踩到一条的、今天已踩到某一条而下一条还差得远的。
  // summary.forward 是全场聚合（只数前者），榜的行数却含两者 —— 不点名后一类，屏幕上就是"4 只 vs 11 行"。
  const already = (d.forward?.rows ?? []).filter(r => r.fwd_state === "already_partial").length;
  const more = already
    ? `；这一张榜里另有 ${already} 只今天已经踩到某一条、明日涨停够不着下一条（这一句按已交出的行数算，`
      + `不是全场聚合，榜被截断时只代表已交出的这一段）`
    : "";
  if (!n) {
    return { text: `明日涨停没有一只新踩到严重异动（母体 ${s.universe ?? 0} 只里逐只算过，`
      + `这一句只数"会新踩到下一条"的）`
      + more
      + (unk ? `；判不了的 ${unk} 只单列在上表第四格，不并进这一句` : "")
      + (noBand ? `；另有 ${noBand} 只连明日涨停幅度都取不到（前瞻整行判不了，不是 0%）` : ""),
      tone: "muted" };
  }
  return { text: `明日涨到涨停会新踩到一条严重异动的有 ${n} 只（全场聚合）`
    + more
    + (unk ? `；另有 ${unk} 只三档里有的算不出来（窗口根数不够），这几只不说它不会触发` : "")
    + (noBand ? `；这一场有 ${noBand} 只连明日涨停幅度都取不到` : ""), tone: "warn" };
}

export function hotHeadline(d: AbnormalMove): string {
  const rows = d.hot?.rows ?? [];
  const s = d.summary ?? {};
  const top = rows[0];
  return `热门股按 10 日累计偏离排（区间复利：窗口内个股逐日复利的区间涨幅 − 指数同样复利的区间涨幅，`
    + `已剔掉对应分类指数的影响）：`
    + `这一场母体 ${s.universe ?? 0} 只，这里交出前 ${rows.length} 只`
    + (top ? `，最远的一只是 ${top.name || top.security_code} ${ptsText(top.dev_10)}` : "")
    + `。「涨幅过大」在本机就用这一把尺子定档（≥${d.thresholds?.universe_dev10 ?? 25} 点进母体）。`;
}

export function recentHeadline(d: AbnormalMove): string {
  const g = d.recent_severe ?? {};
  const rows = g.rows ?? [];
  const halted = rows.filter(r => r.next_halted).length;
  const waiting = rows.filter(r => !r.next_trade_date).length;
  // 聚合没读到时不说"共 0 只次"，也不拿已交出的行数冒充全场总数
  const total = g.total_unread || g.total == null
    ? `这里交出 ${rows.length} 行（全场总数没读到 —— 聚合那一发没成，不是 0）`
    : `共 ${g.total} 只次，这里交出 ${rows.length} 行`;
  return `近 ${g.sessions ?? "?"} 场里按公开判据算出来踩到严重异动的 ${total}`
    + (halted ? `，其中 ${halted} 只次次日就停牌` : "")
    + (waiting ? `；${waiting} 只次的次日还没到（不是停牌，涨幅留空）` : "")
    + `。注意：本机没有"交易所公告日"，这是复算，不代表交易所已认定。`;
}

/** 截断声明：榜少于全场实际只数时必须出声，且不许折进说明框。 */
export function truncationNote(d: AbnormalMove): string | null {
  const bits: string[] = [];
  if (d.forward?.truncated) bits.push(`前瞻榜被上限截断（只交 ${d.forward.returned ?? 0} 行）`);
  if (d.recent_severe?.truncated) bits.push(`近期严重异动被上限截断`);
  return bits.length ? `${bits.join("；")} —— 下面的计数只代表已交出的这一段，不代表全场。` : null;
}
