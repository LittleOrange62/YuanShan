/**
 * 「这一块的数据还是最新一场吗」的判据（用户 2026-09-28 提的规矩：
 * **所有不是最新的数据，默认把这个模块收起来**）。
 *
 * 为什么单独成一个叶子模块：这条判断以前哪都没有 —— 页面上一律平铺，
 * 于是本地加工停在 09-24、今天已经 09-28 的那天，五块旧数和新样子的卡片混在一屏里，
 * 用户是**看完之后才发现自己读的是一天前的数**。这比空屏更坏。
 *
 * 两条判据必须分开，它们说的不是同一件事：
 *  · `behind`：这一块的 as-of 比本页主数旧 → 只有这一块落后（多半是它自己那张表没加工到）。
 *  · `libraryBehind`：**整库**都没跟上最近已收盘的一场（后端 `note` 给的就是这句话，
 *    判据 `trade_calendar.latest_data_session()`：日历那场 > 库里那场）。这时候每一块都旧，
 *    全部收起才对得起用户那句话，但必须同时给一句为什么，否则看着像页面坏了。
 *
 * ⚠️ 日期读不出（`asOf` 空）**不等于旧**：那种情况照常展开，只把"日期读不出"说出来。
 *    把"我不知道"渲染成"这是旧的"，是另一种造假。
 */
export type StaleLevel = "current" | "behind" | "libraryBehind" | "unknown" | "noNewest";

export interface StaleVerdict {
  level: StaleLevel;
  /** 该不该默认收起 */
  collapsed: boolean;
  /** 给人看的那一句，收起状态顶上直接显示，不用展开才知道 */
  label: string;
}

const d10 = (s?: string | null) => (s ?? "").slice(0, 10);

export function staleVerdict(asOf?: string | null, newest?: string | null, libraryBehind = false): StaleVerdict {
  const a = d10(asOf), n = d10(newest);
  if (libraryBehind) {
    return { level: "libraryBehind", collapsed: true,
             label: `整库只到 ${n || "未知"}，日历上已收盘的那场还没加工出来 —— 这一块不是最新` };
  }
  if (!a) return { level: "unknown", collapsed: false, label: "这一块没交出行情日（不是旧，是没给日期）" };
  // 基准日拿不到时**不能盖"最新一场"这个章**：判新旧的总闸自己坏了，全页就会把旧数互相比对
  // 成"都是最新的"（09-30 自查抓到的主路径：expected-date 一挂，整屏高枕无忧地展示旧数）。
  if (!n) return { level: "noNewest", collapsed: false, label: "基准日没拿到，判断不了新旧（这不等于「它是最新的」）" };
  if (a >= n) return { level: "current", collapsed: false, label: `最新一场 ${a}` };
  return { level: "behind", collapsed: true, label: `这一块停在 ${a}，本页主数是 ${n} —— 默认收起` };
}

/** 一屏里有几块被收起，用来在页顶说一句"下面 N 块是旧数"（不数就没人知道自己看到的是残屏）。 */
export function countCollapsed(verdicts: StaleVerdict[]) {
  return verdicts.filter(v => v.collapsed).length;
}
