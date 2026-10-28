import { useMemo, useRef, useState } from 'react';
import { NULL_TEXT } from './datastate.ts';

/**
 * 表格点列头排序。**缺数永远排最后**，而且升序降序都一样在最后 ——
 * 本项目的规矩是 `NULL ≠ 0`：把"未获取"混进 0 一起排，会让一列看起来全是低值，
 * 读者据此判断"这些板块今天没量"，那就是拿缺失当证据了。
 *
 * 两个使用约束（页面接线时必须守）：
 *  1. **排序要在 `.slice()` 之前**。后端给的榜是"按某个口径取前 N"，先截断再排 = 只在
 *     前 N 里换顺序，读者会以为整张表排过了；截断口径要写在表头里。
 *  2. key 支持 `a.b.c` 取嵌套值（榜单行里 heat/signal 这类分组字段很常见）。
 */
export type SortDir = 'asc' | 'desc';
export interface SortState { key: string; dir: SortDir }

/** `'heat.score'` → `row.heat.score`；取不到返回 undefined（按缺数处理）。 */
function pick(row: Record<string, unknown>, key: string): unknown {
  if (!key.includes('.')) return row[key];
  let cur: unknown = row;
  for (const part of key.split('.')) {
    if (cur === null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

function numeric(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string') {
    // 只脱千分位与百分号。**不**换算 亿/万 —— 一列里混了量纲就排序就是错的，
    // 那种列要在页面侧先统一单位再交给这里。
    const s = v.trim().replace(/[,%]/g, '');
    if (!s || s === NULL_TEXT) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function isMissing(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === 'number') return !Number.isFinite(v);
  if (typeof v === 'boolean') return false;
  const s = String(v).trim();
  return s === '' || s === NULL_TEXT || s === '-';
}


/** 单格比较：缺数永远在后；能读成数值的按数值比，其余按中文串比。 */
export function compareCells(a: unknown, b: unknown): number {
  const am = isMissing(a);
  const bm = isMissing(b);
  if (am || bm) return am && bm ? 0 : am ? 1 : -1;
  const an = numeric(a);
  const bn = numeric(b);
  if (an !== null && bn !== null) return an - bn;
  if (an !== null) return -1;          // 数值列与文字列混排时数值在前
  if (bn !== null) return 1;
  return String(a).localeCompare(String(b), 'zh-Hans-CN');
}

export function sortRows<T extends Record<string, unknown>>(rows: T[], sort: SortState | null): T[] {
  if (!sort) return rows;
  const { key, dir } = sort;
  const sign = dir === 'asc' ? 1 : -1;
  // 带上标号做二次比较：同值时保持后端给的原始顺序（稳定，且刷新后顺序不漂）
  return rows.map((row, i) => ({ row, i, v: pick(row, key) }))
    .sort((x, y) => {
      // 缺数那一档**不乘 sign**：升序降序都要留在最后，翻向只翻有数的部分
      const xm = isMissing(x.v);
      const ym = isMissing(y.v);
      if (xm || ym) return xm && ym ? x.i - y.i : xm ? 1 : -1;
      const cmp = compareCells(x.v, y.v);
      return cmp !== 0 ? cmp * sign : x.i - y.i;
    })
    .map((x) => x.row);
}

export function useSort<T extends Record<string, unknown>>(rows: T[], initial: SortState | null = null) {
  const [sort, setSort] = useState<SortState | null>(initial);
  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort]);
  /** 同一个列头再点一次翻向；换列则从降序开始（榜单先看大的）。 */
  function toggle(key: string) {
    setSort(prev => (prev && prev.key === key
      ? (prev.dir === 'desc' ? { key, dir: 'asc' } : null)
      : { key, dir: 'desc' }));
  }
  return { sorted, sort, toggle, dirOf: (key: string) => (sort?.key === key ? sort.dir : null) };
}

/**
 * 表格式渲染常用的一档：状态在页面顶层拿，`apply(rows)` 在渲染里就地排。
 *
 * 为什么不用上面的 `useSort(rows, …)`：很多表格是 `<Panel>{d => …}</Panel>` 这种
 * render-prop，把 hook 调用挪进去会踩"每次渲染顺序要一致"的规矩。
 *
 * `initial` 给"这张表默认按哪一列排"。为什么要有它：不传就只能吃后端给的行序，
 * 于是页面把默认序写在标题里（"按连续场次由多到少"）却没有任何机制保证它成立 ——
 * 传进来之后 `dirOf` 会在那一列表头上亮出降序标记，标题与列头说的是同一件事。
 * 连点三次仍会回到 null（= 后端的行序），不把人锁死在默认序里。
 */
export function useTableSort(initial: SortState | null = null) {
  const [sort, setSort] = useState<SortState | null>(initial);
  // 上一次排好的结果留在实例上：同一份行数组 + 同一个排序档，就不该再排一遍。
  // 为什么要这一格缓存：`apply` 是写在 JSX 里的（全站 63 处），而交叉高亮、hover、
  // 任何页面级 state 一变就整页重渲染 —— 没有它，每次重渲染都把那张表重新 map+sort 一次。
  const memo = useRef<{ rows: unknown; sort: SortState | null; out: unknown } | null>(null);
  function toggle(key: string) {
    setSort(prev => (prev && prev.key === key
      ? (prev.dir === 'desc' ? { key, dir: 'asc' } : null)
      : { key, dir: 'desc' }));
  }
  function apply<T>(rows: T[]): T[] {
    const last = memo.current;
    if (last && last.rows === rows && last.sort === sort) return last.out as T[];
    const out = sortRows(rows as Record<string, unknown>[], sort) as T[];
    memo.current = { rows, sort, out };
    return out;
  }
  return {
    sort,
    toggle,
    dirOf: (key: string) => (sort?.key === key ? sort.dir : null),
    apply,
  };
}
