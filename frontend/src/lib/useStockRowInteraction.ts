import { useCallback, useState, type MouseEvent } from "react";
import { cn } from "@/lib/utils";
import { useTableCellHighlight } from "@/lib/useTableInteraction";
import { useOpenStockReview } from "@/lib/openStockReview";

/** 要读「现在选中的是哪一格」的页面从这里拿，不要自己去引低一层的 `useTableInteraction`
 *  （那条规矩钉在 `test/groupedDossier.test.ts`：页面直接引低一层＝第二份接线）。
 *  用法与它自带的红线：只准在**依赖选中态的那一小块子组件**里调，提到页面组件等于把单击又接回整张表。 */
export { useCellSelection, type CellSel, type CellStore } from "@/lib/useTableInteraction";

/**
 * 「按一只票一行」的表格交互，全站只留这一份实现（用户 2026-10-15：
 * 「这边的个股都支持单击高亮行和列，双击跳转到个股深度复盘的新页面 —— 这个也应该是一个
 * 全软件的通用脚本或者插件或者代码块哦。但凡有用到的地方，都应该引用他才对」）。
 *
 * 它把原来分开的三件事合成一个：
 *  · 单击一格 → 整行＋整列交叉高亮（`useTableCellHighlight`，实现另有一份，这里只组合）；
 *  · 双击一行 → 另开浏览器窗口看那只票的个股深度复盘（`useOpenStockReview`）；
 *  · 弹窗被拦时的告警文字与它的 state（以前每个调用方自己 `useState("")` 一份，
 *    于是"成功清空告警"这种细节在各屏写得不一样 —— 有的屏换排序会留下上一次的告警）。
 *
 * 三条纪律：
 *  1. **调用方只交"这一屏叫什么"**（`from`），判据与串地址的规矩都在被组合的那两份里；
 *  2. `popHint` 渲染时**不许折进默认收起的说明框**（收着等于没说，见 `ExplainBox` 那条红线）；
 *  3. 换排序／换筛选／翻页一律走 `clearSelection()`，否则高亮跟着行号漂到别的票身上。
 *     本 hook 的 `clearAll()` 把高亮与告警一起清（排序按钮上就是用它）。
 */
export function useStockRowInteraction(from: string) {
  const cell = useTableCellHighlight();
  const openReviewWindow = useOpenStockReview();
  const [popHint, setPopHint] = useState("");

  const openReview = useCallback((code: string | null | undefined) => {
    setPopHint(openReviewWindow(code, from));
  }, [openReviewWindow, from]);

  const clearAll = useCallback(() => {
    cell.clearSelection();
    setPopHint("");
  }, [cell.clearSelection]);

  /** 一行 `<tr>` 上挂的：行高亮 + 双击开复盘。⚠️ 这里**故意不带 onClick** ——
   *  整行吞掉点击会把"点的是哪一列"抹平（列高亮就没得选了），点击归每一格自己。
   *  `extraTitle` 给本屏特有的悬停文字（比如口径说明），通用那半句由本 hook 追加，
   *  免得以后的屏把"双击去哪里"这句话各写一遍。 */
  const rowProps = useCallback((i: number, code: string | null | undefined, baseClass?: string, extraTitle?: string) => ({
    className: cell.getRowClass(i, cn("cursor-pointer border-t border-border/40", baseClass)),
    onDoubleClick: () => openReview(code),
    title: `${extraTitle ? `${extraTitle}｜` : ""}双击这一行：另开一个浏览器窗口看这一只的「个股深度复盘」`,
  }), [cell.getRowClass, openReview]);

  /** 一格 `<td>` 上挂的：列键就是这一列在表头里的名字，与交叉高亮共用同一份。
   *  点击把**这一格自己**交给高亮那一份实现（`e.currentTarget`）—— 一屏多张表时只有它知道自己在哪张表里。 */
  const cellProps = useCallback((i: number, k: string, baseClass?: string) => ({
    className: cell.getCellClass(i, k, baseClass),
    onClick: (e: MouseEvent<HTMLTableCellElement>) => cell.handleCellClick(e.currentTarget, i, k),
  }), [cell.getCellClass, cell.handleCellClick]);

  return { ...cell, openReview, popHint, clearAll, rowProps, cellProps };
}

/**
 * 提示语也只有一份。各屏原来自己写过「点上面明细的一格…」这类句子，措辞一漂就等于
 * 界面上有两套说法；需要补充本屏特有的形状（比如"双击带过来的"）时在它后面另起一句。
 */
export const STOCK_ROW_HINT =
  "单击任意一格：那一整行与那一整列一起高亮（再点同一格取消）。双击任意一行：另开一个浏览器窗口，"
  + "里面是那只票的「个股深度复盘」。";
