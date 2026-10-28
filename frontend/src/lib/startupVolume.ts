/**
 * 「启动前量能分布」的**纯判读层**（零 import，能被 `node --test` 直接钉住）。
 *
 * 这里收的是四件最容易在界面上糊过去的事，每件都对应一句会被读者信以为真的话：
 *
 * 1. **两类母体不许并成一个榜。** `board4`（连续涨停 ≥4）只在 10% 档成立，
 *    创业板/科创板 20%、北证 30%；把它们和 jump40（4 日累计涨幅 >40%）混在一列里排名，
 *    等于用 10% 的尺子量 30% 的票。跨板块比强度只能读 `equiv_limit_ups`。
 * 2. **窗口不满的票不可与满窗票并排排名。** `bars_used < window_days` 是次新或长期停牌，
 *    它的"平均倍数"是十几天平均出来的，与三十天平均的不具可比性 —— 必须标出来，不能悄悄留着。
 * 3. **停牌复牌那天不算"今天突然放量"。** `long_gap_days` 的分母是很久以前的量，
 *    读数天然偏大；`flat_days` 是"精确等于 1.0000"，"量基本没变"要读 `still_days`。
 * 4. **空值不补 0。** `ratios` 里 `v:null` 是那天没有有效日线，画成 0 就是在形状图上造假。
 */

export type Cohort = "board4" | "board4_45" | "board4_or_gain45" | "jump40";

export interface StartupRow {
  code: string; name?: string | null; board?: string | null;
  launch_date?: string | null;
  trigger_len?: number | null; trigger_gain?: number | null; equiv_limit_ups?: number | null;
  /** 前四根累计涨幅（基准=启动日前一天收盘）。第三类母体 board4_45 就是按这一列筛的 */
  gain_first4?: number | null;
  /** 启动日那天的全市场环境读数（−100~100）。合成口径只在后端 `pg_sources.mood_score()` 一处 */
  mood_score?: number | null; mood_breadth?: string | null; mood_breadth_score?: number | null;
  mood_theme?: string | null; mood_theme_score?: number | null;
  mood_parts?: string | null; mood_missing?: string | null; mood_source?: string | null;
  window_days?: number | null; bars_used?: number | null;
  up_days?: number | null; down_days?: number | null; flat_days?: number | null; still_days?: number | null;
  dbl_days?: number | null; near_dbl_days?: number | null; calm_days?: number | null; long_gap_days?: number | null;
  ratio_avg?: number | null; ratio_median?: number | null; ratio_p60?: number | null;
  ratio_p80?: number | null; ratio_p90?: number | null; ratio_max?: number | null; ratio_min?: number | null;
  std_days?: number | null;
  /** 倍量区间统计（用户 2026-10-11）：30 根里落在各区间段的天数 */
  range_16_18?: number | null; range_14_16?: number | null; range_12_14?: number | null;
  range_10_12?: number | null; range_08_10?: number | null;
  ratios?: { d?: string | null; v?: number | null }[] | null;
}

/** 窗口是否攒满。缺 `bars_used` 或缺 `window_days` 都算"不知道"，不默认成满窗。 */
export function windowShort(r: StartupRow): boolean | null {
  const used = numOrNull(r.bars_used), need = numOrNull(r.window_days);
  if (used === null || need === null) return null;
  return used < need;
}

export function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * 涨幅读数。`trigger_gain` 是小数（0.4648 = 46.48%）。
 * 空值给 `—`，**0 是有效值**（4 日涨幅真的是 0 的票存在，不许吞成 `—`）。
 */
export function gainText(v: unknown, digits = 2): string {
  const n = numOrNull(v);
  return n === null ? "—" : `${(n * 100).toFixed(digits)}%`;
}

/** 等效涨停数：跨板块唯一可比的强度读数。保留两位，原样不四舍五入成整数。 */
export function equivText(v: unknown): string {
  const n = numOrNull(v);
  return n === null ? "—" : n.toFixed(2);
}

/** 情绪读数（−100~100 的整数）。
 *  空值不补 0 —— 0 在这把尺子上是「中性」，而空说的是"那天读不到"或"两档缺一档"。
 *  读不到时把后端那句 `mood_missing` 原样交给调用方（悬停要念得出来）。 */
export function moodText(v: unknown): string {
  const n = numOrNull(v);
  if (n === null) return "—";
  return n > 0 ? `+${n}` : String(n);
}

/** 情绪高矮的着色：正数偏冷色还是暖色由屏幕定，但**缺值不许着色成任何一种情绪**（它是"没读到"）。 */
export function moodTone(v: unknown): "hot" | "cold" | "flat" | "none" {
  const n = numOrNull(v);
  if (n === null) return "none";
  if (n >= 40) return "hot";
  if (n <= -40) return "cold";
  return "flat";
}

/** 倍数读数（0.852 这种）。空给 —，0 与 1 都是有效值。 */
export function ratioText(v: unknown, digits = 3): string {
  const n = numOrNull(v);
  return n === null ? "—" : n.toFixed(digits);
}

/**
 * 迷你走势的断段：把 `ratios` 切成若干"连续有值"的线段，`v:null` 处断开。
 * 返回每段的下标区间；组件据此画 polyline，**绝不把 null 当 0 连起来**。
 */
export function ratioSegments(ratios?: { v?: number | null }[] | null): { from: number; to: number }[] {
  const out: { from: number; to: number }[] = [];
  let start = -1;
  (ratios ?? []).forEach((p, i) => {
    const v = numOrNull(p?.v);
    if (v === null) {
      if (start >= 0) { out.push({ from: start, to: i - 1 }); start = -1; }
    } else if (start < 0) {
      start = i;
    }
  });
  if (start >= 0) out.push({ from: start, to: (ratios?.length ?? 0) - 1 });
  return out;
}

/** 形状图的取值范围（只含有值的点）；全空给 null，让调用方画"没有点"而不是画一条 0 线。 */
export function ratioExtent(ratios?: { v?: number | null }[] | null): { lo: number; hi: number } | null {
  const vals = (ratios ?? []).map(p => numOrNull(p?.v)).filter((v): v is number => v !== null);
  if (!vals.length) return null;
  return { lo: Math.min(...vals), hi: Math.max(...vals) };
}

/**
 * 覆盖那一行怎么说。
 *
 * 后端在 sv-2 那批起交出 `window_from`（这一档回溯的窗口起点）与 `profile_earliest_launch`
 * （判据表最早算到哪一天），所以这一行**能说准话了**：窗口起点早于表内最早一行时，
 * "那段时间没数"就不是"没有这种票"，而是判据表没算到 —— 这句必须说，
 * 否则读者会把"近 12 个月只有 355 只"当成市场事实。
 * 老批（sv-1）没有这两个字段，退回"分不出"的措辞，不假装知道。
 */
export function coverageSentence(rng: ReturnType<typeof launchRange>, d: StartupMeta): string {
  const from = (d.window_from ?? "").slice(0, 10);
  const earliest = (d.profile_earliest_launch ?? "").slice(0, 10);
  const got = numOrNull(d.rows_returned), total = numOrNull(d.rows_total);
  // 范围用"共 N 个启动日"而不是括号：截断那句也是括号，两个括号连着读起来很别扭
  const span = rng ? `交出行的启动日 ${rng.from} ~ ${rng.to}，共 ${rng.days} 个启动日` : "交出 0 行，没有启动日可说";
  // 榜被上限截断时，"启动日覆盖"只代表已交出的那一段 —— 这句必须说，
  // 否则读者会拿一个被截断的范围去判断整档回溯有没有覆盖到。母体总数上一行已经报过，这里不再重复。
  const cut = got !== null && total !== null && got < total
    ? `（榜被上限截到 ${got} 行，这个范围只代表已交出的那一段）` : "";
  if (from && earliest) {
    const gap = earliest > from;
    const pool = numOrNull(d.profile_rows_total);
    return `${span}${cut}；这一档窗口从 ${from} 起，判据表最早算到 ${earliest}`
      + (pool === null ? "。" : `，这一档库里共 ${pool} 行。`)
      + (gap
        ? ` ${from} ~ ${earliest} 那一段是判据表没算到，不是市场上没有这种票 —— 要看得让判据侧把那一段补算出来。`
        : " 窗口整段都在判据表的覆盖之内，母体行数的多少可以当成这类票的疏密来读。");
  }
  return `${span}${cut}。`
    + "这一批没交窗口起点与表内最早启动日，所以界面分不出「那段时间本来就没有这种票」与「判据表没算那么早」。";
}

/** 读侧元信息：覆盖行与截断判断要用到的那几个字段。 */
export interface StartupMeta {
  window_from?: string | null;
  profile_earliest_launch?: string | null;
  profile_rows_total?: number | null;
  rows_returned?: number | null;
  rows_total?: number | null;
}

/** 交出行的启动日范围（只描述已交出的这部分，不参与任何判定）。 */
export function launchRange(rows: StartupRow[]): { from: string; to: string; days: number } | null {
  const dates = rows.map(r => (r.launch_date ?? "").slice(0, 10)).filter(s => s.length === 10);
  if (!dates.length) return null;
  return {
    from: dates.reduce((a, b) => (a < b ? a : b)),
    to: dates.reduce((a, b) => (a > b ? a : b)),
    days: new Set(dates).size,
  };
}

/** 后端 `summary`：整组汇总**由服务端按同一批过滤条件算好**交出。
 *  界面一律不自己按行再算一遍 —— 行是被 limit 截断过的，页面算出来的"整组均值"
 *  与母体的均值不是同一个东西，那正是"看着像汇总、其实是前 200 行"的那类假数。 */
export interface StartupSummary {
  n?: number | null;
  med_of_median?: number | null; mean_of_avg?: number | null;
  mean_p60?: number | null; mean_p80?: number | null; mean_p90?: number | null;
  mean_dbl_days?: number | null; mean_up_days?: number | null; mean_down_days?: number | null;
  mean_still_days?: number | null; mean_calm_days?: number | null; mean_equiv?: number | null;
  p80_of_p80?: number | null; three_plus_dbl?: number | null; short_rows?: number | null;
  [k: string]: number | null | undefined;
}

/**
 * 这批票启动前到底放量没有 —— 只读 `summary`，不碰行。
 *
 * 结论会翻向，所以三种情况分开写：中位倍数的组均值 `<1` 时"启动前普遍放量"这个直觉**在本机数据里不成立**；
 * `>1` 时才可以说要放量；**null 时不许猜**（后端没算出来，界面就不能站任何一边）。
 * 两头都不许被写成买卖判断：这是描述统计，`caveats.note` 那条选择偏差一直挂在旁边。
 */
export function groupReading(s: StartupSummary | null | undefined, windowDays: number | null):
  { tone: "flat" | "warn" | "unknown"; text: string } {
  const med = numOrNull(s?.med_of_median), dbl = numOrNull(s?.mean_dbl_days), n = numOrNull(s?.n);
  const win = numOrNull(windowDays);
  const scope = n === null ? "这一批的只数后端没交" : `这 ${n} 只票`;
  const span = win === null ? "启动前那段有效日线" : `启动前 ${win} 根有效日线`;
  if (med === null || dbl === null) {
    return { tone: "unknown",
      text: `${scope}的整组读数后端没算出来（summary 里 med_of_median 或 mean_dbl_days 是空）—— `
        + "这一屏不猜它们启动前是放量还是缩量。" };
  }
  if (med < 1) {
    return { tone: "warn",
      text: `结论：${scope}${span}里，倍数的组内中位数是 ${ratioText(med)}（低于 1 就是"当日量小于上一根"），`
        + `平均只有 ${fmtDays(dbl)} 天出现倍量（≥2.0）—— `
        + "「启动前普遍放量」这个直觉在本机这批票上不成立。"
        + "但反过来也别读成「放量没用」：母体本来就是已经涨起来的票，这里只描述它们长什么样。" };
  }
  return { tone: "flat",
    text: `${scope}${span}里倍数的组内中位数是 ${ratioText(med)}（高于 1），平均 ${fmtDays(dbl)} 天出现倍量（≥2.0）`
      + "。这只说明这批票启动前普遍量在放大，不说明放量之后会涨 —— 母体本身就是已经涨起来的票。" };
}

/** 天数读数：后端给几位就显几位，0 是有效值。 */
export function fmtDays(v: unknown): string {
  const n = numOrNull(v);
  return n === null ? "—" : String(n);
}

/** 母体切换项的说明文字：只用后端那句 `cohort_text`，缺失时直说没交，不顶一句界面自己编的。 */
export function cohortLabel(cohort: string, cohortText?: string | null): string {
  const t = (cohortText ?? "").trim();
  return t || `这一类母体的说明后端没交（cohort=${cohort}）`;
}

/** 名字本身就是一串代码的行数（股票字典里没登记中文名）。
 * 界面必须数得出来：名称列里出现 `920305.BJ` 这种值，读者第一反应是"页面把名字截断了"或"链接坏了"，
 * 而真实情况是库里就只有代码 —— 不写出来就没人知道该找谁。 */
export function nameIsCodeCount(rows: StartupRow[]): string[] {
  return rows.filter(r => {
    const n = String(r.name ?? "").trim();
    return n === "" || n.toUpperCase() === String(r.code ?? "").toUpperCase();
  }).map(r => String(r.code));
}

/** 读侧把库里的 `security_code` 改名叫 `code` 交出来，而 `definitions` 的键仍是库列名。
 *  这不是两个字段，所以查解释时按别名回查一次 —— 不算替后端编解释，只是把同一个字段的名字对上。
 *  后端若哪天把 definitions 的键也改成 `code`，这条别名就自动失效，不用删。 */
export const DEFINITION_ALIAS: Record<string, string> = { code: "security_code" };

export function definitionOf(defs: Record<string, string> | null | undefined, key: string): string | undefined {
  if (!defs) return undefined;
  return defs[key] ?? defs[DEFINITION_ALIAS[key] ?? ""];
}

/** 交出多少行 / 母体共多少行 / 是否被 limit 截断。截断了就不能读成"全部"。 */
export function rowCountLine(d: { rows_returned?: number | null; rows_total?: number | null; limit?: number | null }): string {
  const got = numOrNull(d.rows_returned), total = numOrNull(d.rows_total), lim = numOrNull(d.limit);
  const base = got === null ? "交出行数未交出"
    : total === null ? `交出 ${got} 行（母体总数未交出，不能当作全貌）`
    : `交出 ${got} 行 / 母体共 ${total} 行`;
  if (got !== null && total !== null && got < total) {
    return `${base} —— 榜尾没在这里${lim === null ? "" : `（后端 limit=${lim}）`}，别读成"全部"`;
  }
  return base;
}
