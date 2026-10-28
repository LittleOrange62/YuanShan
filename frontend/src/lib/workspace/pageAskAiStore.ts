/**
 * 页面 → 右侧「AI分析」栏的**本页上下文登记表**。
 *
 * 为什么要有这一层：「问 AI · 本页上下文」原来是各页头部各摆一个按钮，点开一个浮层抽屉。
 * 用户 2026-10-07 要求把这个入口收进右侧那一栏，于是页面必须能把"这一屏能带哪些材料"
 * 交出去，而栏在 `Layout` 里、拿不到页面内部的 state。
 *
 * 三条规矩（都能被 `test/pageAskAi.test.ts` 钉住）：
 *  1. **一次只有一屏在册**。换页时旧页必须放掉，否则你问的是自选页、带的是首页盘面 ——
 *     那种错位不会报错，只会让模型答得看着很对。
 *  2. **只有持有者能放**（按 token 认）。A 页的卸载钩子如果在 B 页登记之后才跑，
 *     它不许把 B 页那份抹掉，但它也不能覆盖 B —— 所以"放"只认自己那份。
 *  3. **内容没变不通知**。首页每 30~60 秒自动刷新，若每次渲染都通知，栏会跟着抖、
 *     正在流式回答的会话可能被重挂。
 *
 * 这里**不 import React**：纯逻辑才能被 `node --test` 直接钉（与 `lib/datastate.ts` 同一条路子）。
 * 也刻意不做"没登记时拿别的东西兜底"—— 持仓页（`/my-stocks`）永远不登记，
 * 那是"持仓数据不接入任何 AI prompt"那条红线的界面侧落点。
 */

export interface PageAskAi {
  /** 这一屏叫什么，栏里要写清楚带的是哪一页（用户原话：不要让人猜） */
  label: string;
  /** 随提问发给模型的本页材料，由页面自己拼，栏里不猜、不补 */
  context: string;
  suggestions?: string[];
}

export function samePageAskAi(a: PageAskAi | null, b: PageAskAi | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.label !== b.label || a.context !== b.context) return false;
  const x = a.suggestions ?? [];
  const y = b.suggestions ?? [];
  return x.length === y.length && x.every((v, i) => v === y[i]);
}

interface Holder { token: object; value: PageAskAi }

let entry: Holder | null = null;
const listeners = new Set<() => void>();

const notify = () => { for (const fn of listeners) fn(); };

/**
 * 登记（或更新）这一屏的上下文。传 `null` = 这一屏此刻没有可带的材料（还没查到数），
 * 此时若这份登记原本是自己放的，就放掉。
 * 返回是否真的改变了状态 —— 没变就不该通知订阅者。
 */
export function registerPageAskAi(token: object, value: PageAskAi | null): boolean {
  const mine = entry?.token === token;
  if (value === null) {
    if (!mine) return false;                       // 不是自己那份，不动别人的
    if (entry === null) return false;              // 本来就没登记
    entry = null;
    notify();
    return true;
  }
  if (mine && samePageAskAi(entry!.value, value)) return false;
  entry = { token, value };
  notify();
  return true;
}

/** 卸载时放掉自己那份；拿着别页的 token 来放是无效的（防"晚到的清理抹掉新页"）。 */
export function releasePageAskAi(token: object): boolean {
  if (entry?.token !== token) return false;
  entry = null;
  notify();
  return true;
}

/** 快照：引用稳定（同一份登记多次读取返回同一个对象），`useSyncExternalStore` 才不会死循环。 */
export function readPageAskAi(): PageAskAi | null {
  return entry?.value ?? null;
}

export function subscribePageAskAi(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
