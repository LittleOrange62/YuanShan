import type { LiveEmotion, MarketSentiment, ShortTermEmotion } from "@/lib/api";

/**
 * 首页「市场情绪」三口径对照：行描述符与取数收在这一份里，组件只渲染。
 *
 * 三把尺各数各的，合并只许并排、不许挑一个当统一真值：
 *  · 乐咕乐股 —— 源站自己的算法（涨停用它的「真实涨停」口径，活跃度也是它算的）；
 *  · 东财实时池 —— 盘中即时截面，炸板按「开板事件」数；
 *  · 本地库（`analytics.limit_up_pool_daily`，lup-1）—— 已收盘那一场按本地日线判据自算，
 *    含北交所与 ST，炸板按「收盘没封住的只数」算。
 * 同一场 2026-10-23 两侧实测：封住 84 对 82、炸板 19 对 12、跌停 9 对 6、2 板以上 13 对 12。
 */

export type CaliberKey = "legu" | "live" | "local";

export interface CaliberCell {
  /** 缺数一律 null（界面念 —），不许填 0 —— 0 在这几张尺子上都是一个实质读数 */
  v: number | string | null;
  /** 这一格的分子分母是哪一天／哪一批，非空必须上屏 */
  note?: string | null;
}

export interface CompareRow {
  key: string;
  label: string;
  /** count=只数/家数、rate=比率、text=源站原样字符串（不参与差值） */
  kind: "count" | "rate" | "text";
  unit: string;
  hint: string;
  values: Record<CaliberKey, CaliberCell>;
  /** 差值＝本地库 − 这一列；算不出来（任一边缺、或两边根本不是同一件事）就是 null */
  diff: { vsLegu: number | null; vsLive: number | null };
  /** 不给算差的理由，非空就写在差值格子里 */
  noDiff?: { vsLegu?: string; vsLive?: string };
}

export const CALIBER_COLUMNS: { key: CaliberKey; label: string; sub: string }[] = [
  { key: "legu", label: "乐咕 · 源站口径", sub: "不是我们算的，算法未公开" },
  { key: "live", label: "东财实时池 · 盘中", sub: "随盘变化 · 炸板按开板事件数" },
  { key: "local", label: "本地库 · 已收盘自算", sub: "日线判据 · 含北交所与 ST" },
];

const NULLS: Record<CaliberKey, CaliberCell> = {
  legu: { v: null }, live: { v: null }, local: { v: null },
};

/** 两边都有数才给差；比率类换算成百分点，家数类就是只数之差。 */
function minus(a: number | null | undefined, b: number | null | undefined,
              kind: "count" | "rate" | "text"): number | null {
  if (kind === "text") return null;
  if (a == null || b == null) return null;
  const d = kind === "rate" ? (a - b) * 100 : a - b;
  // 差值只到小数一位：0.05pp 的差不值得占一格，也不会让 — 和 0 分不清
  return Math.round(d * 10) / 10;
}

export function buildCompareRows(
  legu: MarketSentiment | null | undefined,
  live: LiveEmotion | null | undefined,
  local: ShortTermEmotion | null | undefined,
): CompareRow[] {
  const l = live?.available === true ? live : null;
  // 库里没有这一场的行 ≠ 今天没有涨停：available:false 时整列交 null，reason 由表头念
  const e = local && local.available !== false && local.zt_count !== undefined ? local : null;
  const g = legu ?? null;

  const livePromoNote = l?.promotion_base != null
    ? `分母 ${l.promotion_base_date || "上一场"} 涨停 ${l.promotion_base} 家` : null;
  const localPromoNote = e?.prev_sealed != null
    ? `分母 ${e.prev_date} 封住 ${e.prev_sealed} 只` : null;

  const rows: CompareRow[] = [
    {
      key: "zt", label: "涨停", kind: "count", unit: " 家", hint: "收盘封在涨停档位上的家数；乐咕另有「真实涨停」那一档",
      values: { legu: { v: g?.zt ?? null }, live: { v: l?.zt_count ?? null }, local: { v: e?.zt_count ?? null } },
      diff: { vsLegu: minus(e?.zt_count, g?.zt, "count"), vsLive: minus(e?.zt_count, l?.zt_count, "count") },
    },
    {
      key: "zt_real", label: "真实涨停", kind: "count", unit: " 家", hint: "乐咕自定义，差额从哪来它没公开",
      values: { ...NULLS, legu: { v: g?.zt_real ?? null } },
      diff: { vsLegu: null, vsLive: null },
    },
    {
      key: "dt", label: "跌停", kind: "count", unit: " 家", hint: "含 ST 与北交所的是本地那一列，源站池基本不收",
      values: { legu: { v: g?.dt ?? null }, live: { v: l?.dt_count ?? null }, local: { v: e?.dt_count ?? null } },
      diff: { vsLegu: minus(e?.dt_count, g?.dt, "count"), vsLive: minus(e?.dt_count, l?.dt_count, "count") },
    },
    {
      key: "dt_real", label: "真实跌停", kind: "count", unit: " 家", hint: "乐咕自定义",
      values: { ...NULLS, legu: { v: g?.dt_real ?? null } },
      diff: { vsLegu: null, vsLive: null },
    },
    {
      key: "zb", label: "炸板（没封住）", kind: "count", unit: " 家",
      hint: "两边不是同一件事：东财数「开板事件」，本地数「收盘没封住的只数」⇒ 差值只作规模参照，不是谁错了",
      values: { ...NULLS, live: { v: l?.zb_count ?? null }, local: { v: e?.zb_count ?? null } },
      diff: { vsLegu: null, vsLive: minus(e?.zb_count, l?.zb_count, "count") },
      noDiff: { vsLegu: "乐咕没这一档" },
    },
    {
      key: "max_boards", label: "最高连板", kind: "count", unit: " 板", hint: "只给板数；是哪只票看下面那张连板股表",
      values: { ...NULLS, live: { v: l?.max_boards ?? null }, local: { v: e?.max_boards ?? null } },
      diff: { vsLegu: null, vsLive: minus(e?.max_boards, l?.max_boards, "count") },
      noDiff: { vsLegu: "乐咕没这一档" },
    },
    {
      key: "lianban", label: "连板（2板+）", kind: "count", unit: " 家", hint: "连续两天及以上封住的只数",
      values: { ...NULLS, live: { v: l?.lianban_count ?? null }, local: { v: e?.lianban_count ?? null } },
      diff: { vsLegu: null, vsLive: minus(e?.lianban_count, l?.lianban_count, "count") },
      noDiff: { vsLegu: "乐咕没这一档" },
    },
    {
      key: "seal_rate", label: "封板率", kind: "rate", unit: "%",
      hint: "封住 ÷ 本场摸过涨停的家数（按家数，不按开板次数）",
      values: { ...NULLS, live: { v: l?.seal_rate ?? null }, local: { v: e?.seal_rate ?? null } },
      diff: { vsLegu: null, vsLive: minus(e?.seal_rate, l?.seal_rate, "rate") },
      noDiff: { vsLegu: "乐咕没这一档" },
    },
    {
      key: "break_rate", label: "炸板率", kind: "rate", unit: "%",
      hint: "没封住 ÷ 同上分母；两边的分母与分子口径都不同，差值往往是「只数 vs 事件」带来的",
      values: { ...NULLS, live: { v: l?.break_rate ?? null }, local: { v: e?.break_rate ?? null } },
      diff: { vsLegu: null, vsLive: minus(e?.break_rate, l?.break_rate, "rate") },
      noDiff: { vsLegu: "乐咕没这一档" },
    },
    {
      key: "promotion_rate", label: "晋级率", kind: "rate", unit: "%",
      hint: "上一场封住的票今天又封住的比例 —— 两列的「上一场」不是同一天，所以这一行不算差",
      values: {
        ...NULLS,
        live: { v: l?.promotion_rate ?? null, note: livePromoNote },
        local: { v: e?.promotion_rate ?? null, note: localPromoNote },
      },
      diff: { vsLegu: null, vsLive: null },
      noDiff: { vsLegu: "乐咕没这一档", vsLive: "两列的分母不是同一天，不比" },
    },
    {
      key: "up", label: "上涨家数", kind: "count", unit: " 家", hint: "乐咕的涨跌家数是同一批母体，宽度那一档按它分档",
      values: { ...NULLS, legu: { v: g?.up ?? null } },
      diff: { vsLegu: null, vsLive: null },
    },
    { key: "down", label: "下跌家数", kind: "count", unit: " 家", hint: "",
      values: { ...NULLS, legu: { v: g?.down ?? null } }, diff: { vsLegu: null, vsLive: null } },
    { key: "flat", label: "平盘", kind: "count", unit: " 家", hint: "",
      values: { ...NULLS, legu: { v: g?.flat ?? null } }, diff: { vsLegu: null, vsLive: null } },
    {
      key: "active", label: "活跃度", kind: "text", unit: "",
      hint: "源站自有算法，不能按上涨家数占比来理解",
      values: { ...NULLS, legu: { v: g?.active ?? null } },
      diff: { vsLegu: null, vsLive: null },
    },
  ];
  return rows;
}

/** 一格怎么念：缺数是 —（不是 0），比率带一位小数，文本原样。 */
export function cellText(row: CompareRow, key: CaliberKey): string {
  const v = row.values[key].v;
  if (v == null || v === "") return "—";
  if (row.kind === "rate") return `${(Number(v) * 100).toFixed(1)}%`;
  if (row.kind === "count") return `${v}${row.unit ? ` ${row.unit.trim()}` : ""}`;
  return String(v);
}

/** 差值格：算不出来就写清为什么算不出来，不留一句空 —。 */
export function diffText(row: CompareRow, which: "vsLegu" | "vsLive"): string {
  const d = row.diff[which];
  if (d != null) {
    const sign = d > 0 ? "+" : "";
    return row.kind === "rate" ? `${sign}${d.toFixed(1)}pp` : `${sign}${d}`;
  }
  return row.noDiff?.[which] || "—";
}

/** 三列表头各自的时间戳与"取不到数"的说明 —— 并排摆的第一条纪律就是别让人以为三列同时刻。 */
export function columnStamps(
  legu: MarketSentiment | null | undefined,
  live: LiveEmotion | null | undefined,
  local: ShortTermEmotion | null | undefined,
): Record<CaliberKey, { stamp: string; warn: string | null }> {
  return {
    legu: {
      stamp: legu?.date ? `${legu.date} 收盘` : "",
      warn: legu ? null : "乐咕这一场没读到",
    },
    live: {
      stamp: live?.available && live.date ? `${live.date} ${live.as_of || ""} · ${live.phase || ""}` : "",
      warn: live && live.available !== true ? (live.reason || "今日暂无数据") : null,
    },
    local: {
      stamp: local?.date ? `${local.date} 收盘快照` : "",
      warn: local && local.available === false ? (local.reason || "库里没这一场的行") : null,
    },
  };
}
