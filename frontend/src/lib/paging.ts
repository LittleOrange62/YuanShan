/**
 * 表的分页（一页摆多少行、现在第几页）——**纯函数叶子**，零 import，所以 `node --test` 能直接钉住。
 *
 * 为什么要收这一处：全站十几块榜各自写过一遍 `Math.ceil(rows.length / size)` 与
 * `Math.min(Math.max(1, page), pages)`，漂起来的样子是同一份数据在两块屏上页数不一样、
 * 或者翻到最后一页重复出行（clamp 少一步就会越界后 `slice` 出空页而句子还说"第 3/2 页"）。
 * 这里只收**算法与那一句人话**；按钮那一排是各屏自己的交互（换页要不要清行选中，
 * 只有调用方知道自己有没有交叉高亮的状态要清），所以 UI 不在这份里。
 */

/** 每页几行全站用同一组：给少了首页要连点，给多了又回到"看着累"。 */
export const PAGE_SIZES = [25, 50, 100];

export function pageCount(total: number, size: number): number {
  return Math.max(1, Math.ceil((Number.isFinite(total) ? Math.max(0, total) : 0) / Math.max(1, size)));
}

/** 行/每页/筛档任一变了都可能让页数变小 ⇒ 先把页码夹回有效范围，再切片。 */
export function clampPage(page: number, pages: number): number {
  return Math.min(Math.max(1, page || 1), Math.max(1, pages));
}

/** 按当前页取那一截。`rows` 必须是**排完序之后**的数组（排序在截断之前，不然每页都在换口径）。 */
export function slicePage<T>(rows: readonly T[], page: number, size: number): T[] {
  const cur = clampPage(page, pageCount(rows.length, size));
  return rows.slice((cur - 1) * size, cur * size);
}

/**
 * 页脚那一句。三件事必须一起说：**共多少行**（这一屏真有几条，别让人把"这一页 25 行"读成"全场 25 只"）、
 * 每页几行、现在第几页。行数与页码都来自本次实数，不写"全部"这种没有依据的词。
 */
export function pageSentence(total: number, size: number, page: number): string {
  const pages = pageCount(total, size);
  const cur = clampPage(page, pages);
  return `共 ${total} 行 · 每页 ${size} 行 · 现在是第 ${cur}/${pages} 页`;
}

/** 翻页按钮该不该出现（越界按钮点了没反应，等于摆两个假控件） */
export function canStep(page: number, size: number, total: number, dir: -1 | 1): boolean {
  const cur = clampPage(page, pageCount(total, size));
  return dir < 0 ? cur > 1 : cur < pageCount(total, size);
}
