import { useCallback, useRef, useState, useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

/**
 * 表格单元格选中高亮 Hook —— **选中态不在 React 里**。
 *
 * 为什么（2026-10-26 实测）：原先这里是一份 `useState`，每一格 className 都跟着选中态变，
 * 于是一次单击要让整表重新协调一遍。`/volume-double` 那张 25 行 × 32 列（800 格）的表上
 * **一次单击记到 461 毫秒的长任务**，而 React 把第一格改动提交出去只要 6~10 毫秒 ——
 * 钱全花在协调，不在渲染。行级 `React.memo` 治不了它：列高亮会动到**每一行的那一格**，
 * 所以每一行的 props 都变了。现在的做法是每格只交一份**与选中无关**的静态 className，
 * 点击时在 DOM 上写三个类（行／列／那一格），一次单击＝几十次 classList 写，不过 React。
 *
 * 用法（与改前一样，调用点不必动）：
 * ```tsx
 * const { store, handleCellClick, clearSelection, getCellClass, getRowClass } = useTableCellHighlight();
 *
 * <td className={getCellClass(i, "code")} onClick={(e) => handleCellClick(e.currentTarget, i, "code")}>…</td>
 * <tr className={getRowClass(i)}>…</tr>
 * ```
 * 需要在渲染里读选中的那一格（只有两处：选股汇总、分组自选）走 `useCellSelection(store)`，
 * 而且要把**依赖它的那一小块**放进子组件里去订阅，别在外层组件里读 —— 外层一读，
 * 整张表又回到"每次点击重渲染一遍"，这块改动就白做。
 */

/** 三个类只在 DOM 上出现，颜色只住 `src/index.css` 一份（`.xl-row`/`.xl-col`/`.xl-cell`）。 */
export const XL_ROW = "xl-row";
export const XL_COL = "xl-col";
export const XL_CELL = "xl-cell";

export type CellSel = { row: number; col: string };

/** 选中态的存储：`get()` 交的是**同一个对象**（不变就不换身份），订阅才拿得到稳定快照。 */
export type CellStore = {
  get: () => CellSel | null;
  subscribe: (fn: () => void) => () => void;
};

function clearPaint(table: HTMLElement) {
  table.querySelectorAll(`.${XL_ROW},.${XL_COL},.${XL_CELL}`).forEach((el) => {
    el.classList.remove(XL_ROW, XL_COL, XL_CELL);
  });
}

/** 在 DOM 上把那一行、那一列、那一格各自标记出来；返回这一格所在的 `<table>`（清场要用）。 */
function paint(td: HTMLTableCellElement, sel: CellSel | null): HTMLElement | null {
  const table = td.closest("table") as HTMLElement | null;
  if (!table) return null;
  clearPaint(table);
  if (!sel) return table;
  const tr = td.parentElement;
  if (tr) Array.from(tr.children).forEach((c) => c.classList.add(XL_ROW));
  const colIdx = td.cellIndex;
  table.querySelectorAll("tbody tr").forEach((r) => {
    const c = r.children[colIdx];
    if (c) c.classList.add(XL_COL);
  });
  td.classList.add(XL_CELL);
  return table;
}

export function useTableCellHighlight() {
  const selRef = useRef<CellSel | null>(null);
  const tableRef = useRef<HTMLElement | null>(null);
  const subsRef = useRef<Set<() => void>>(new Set());
  const storeRef = useRef<CellStore | null>(null);
  if (!storeRef.current) {
    const subs = subsRef.current;
    storeRef.current = {
      get: () => selRef.current,
      subscribe: (fn: () => void) => {
        subs.add(fn);
        return () => { subs.delete(fn); };
      },
    };
  }

  const notify = () => subsRef.current.forEach((f) => f());

  /** 交的是**被点那一格**（`e.currentTarget`），不是行列号凑出来的选择器：
   *  一屏多张表（倍量页两块、分组自选几张）时，只有格子自己知道自己属于哪张表。 */
  const handleCellClick = useCallback((target: HTMLTableCellElement, row: number, col: string) => {
    const prev = selRef.current;
    const next = prev && prev.row === row && prev.col === col ? null : { row, col };
    selRef.current = next;
    tableRef.current = paint(target, next) ?? tableRef.current;
    notify();
  }, []);

  const clearSelection = useCallback(() => {
    selRef.current = null;
    if (tableRef.current) clearPaint(tableRef.current);
    notify();
  }, []);

  // `_row`/`_col` 留着不用是故意的：调用点交的仍是这两个参数（签名不许改），
  // 而高亮既然不进 React， className 就不该再依赖它们。
  const getCellClass = useCallback((_row: number, _col: string, baseClass?: string) =>
    cn(baseClass, "cursor-pointer hover:bg-primary/5"), []);

  const getRowClass = useCallback((_row: number, baseClass?: string) => cn(baseClass), []);

  return { store: storeRef.current, handleCellClick, clearSelection, getCellClass, getRowClass };
}

/** 订阅交叉高亮的选中那一格。见 `useTableCellHighlight` 头上那条"要放进子组件"的规矩。 */
export function useCellSelection(store: CellStore): CellSel | null {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

/**
 * 表格列筛选 Hook（前端过滤）。
 *
 * 用法：
 * ```tsx
 * const { filters, setFilter, clearFilters, applyFilter, activeCount } = useTableFilter<Row>();
 *
 * // 设置筛选条件
 * setFilter("board", v => v === "主板");
 * setFilter("dbl_count", v => (v ?? 0) >= 3);
 *
 * // 应用筛选
 * const filtered = applyFilter(rows);
 * ```
 */
export function useTableFilter<T extends Record<string, unknown>>() {
  const [filters, setFilters] = useState<Record<string, (value: unknown) => boolean>>({});

  const setFilter = useCallback((key: string, predicate: (value: unknown) => boolean) => {
    setFilters(prev => ({ ...prev, [key]: predicate }));
  }, []);

  const removeFilter = useCallback((key: string) => {
    setFilters(prev => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const clearFilters = useCallback(() => setFilters({}), []);

  const applyFilter = useCallback(
    (rows: T[]): T[] => {
      const keys = Object.keys(filters);
      if (keys.length === 0) return rows;
      return rows.filter(row =>
        keys.every(key => {
          const value = row[key];
          return filters[key](value);
        })
      );
    },
    [filters]
  );

  const activeCount = Object.keys(filters).length;

  return { filters, setFilter, removeFilter, clearFilters, applyFilter, activeCount };
}

/**
 * 数值范围筛选器工厂函数。
 *
 * 用法：
 * ```tsx
 * const { minMaxFilter, MinMaxInput } = createMinMaxFilter("dbl_count");
 * // 在组件中使用 MinMaxInput 渲染输入框
 * ```
 */
export function createRangeFilter(key: string) {
  const filter = (min?: number, max?: number) => (value: unknown) => {
    const n = typeof value === "number" ? value : null;
    if (n === null) return false;
    if (min !== undefined && n < min) return false;
    if (max !== undefined && n > max) return false;
    return true;
  };
  return { key, filter };
}

/**
 * 精确匹配筛选器工厂函数。
 */
export function createExactFilter<T extends string | number | boolean>(_key: string, value: T) {
  return (rowValue: unknown) => rowValue === value;
}

/**
 * 布尔筛选器工厂函数。
 */
export function createBooleanFilter(_key: string, value: boolean) {
  return (rowValue: unknown) => rowValue === value;
}
