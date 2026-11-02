/**
 * 页面 → 顶部面包屑的「这一屏现在吃的是哪一只」登记表（2026-10-28 用户点名）。
 *
 * 用户原话：「"现在复盘的是哪一只"这个展示框不要了。在页面最上方 "工作空间 / 个股深度复盘"
 * 后面，动态的跟上个股代码和名称。整体展示即可。」
 * 面包屑住在 `Layout` 里、拿不到页面内部的 state，所以和右侧「AI分析」栏同一套路：页面登记、栏读。
 *
 * 三条规矩（与 `pageAskAiStore.ts` 同一套，都能被 `test/pageCrumb.test.ts` 钉住）：
 *  1. **一次只有一屏在册**：换页时旧页必须放掉，否则你人在自选页、顶栏还挂着刚才那深度复盘的票。
 *  2. **只有持有者能放**（按 token 认）：A 页晚到的卸载钩子不许抹掉 B 页刚登记的那一份。
 *  3. **内容没变不通知**：顶栏跟着每次渲染抖，等于在页面上放一个闪烁的装饰。
 *
 * 与「AI分析」栏那条红线不同的一点：**这里不许拿别的东西兜底**。顶栏那串字是"现在看的是哪一只"，
 * 读不到名字就只能念代码 —— 把上一只票的名字留在屏幕上，人会拿着茅台的名字看平安银行的数。
 * 所以 `hint` 那一格是"为什么这里没有名字"，不是可有可无的注释。
 */

export interface PageCrumb {
  /** 顶栏紧跟在页面标题后面的那串字，例如 `600519.SH 贵州茅台`；读不到名字时只有代码 */
  text: string;
  /** 悬停解释：这一只是从哪儿带来的、名字为什么没跟上（字典读不到 ≠ 没有这只票） */
  hint?: string;
}

export function samePageCrumb(a: PageCrumb | null, b: PageCrumb | null): boolean {
  if (a === null || b === null) return a === b;
  return a.text === b.text && (a.hint ?? '') === (b.hint ?? '');
}

interface Holder { token: object; value: PageCrumb }

let entry: Holder | null = null;
const listeners = new Set<() => void>();

const notify = () => { for (const fn of listeners) fn(); };

/** 登记（或更新）这一屏的顶栏后缀；传 `null` = 这一屏此刻没有对象（还没选一只）。 */
export function registerPageCrumb(token: object, value: PageCrumb | null): boolean {
  const mine = entry?.token === token;
  if (value === null) {
    if (!mine || entry === null) return false;
    entry = null;
    notify();
    return true;
  }
  if (mine && samePageCrumb(entry!.value, value)) return false;
  entry = { token, value };
  notify();
  return true;
}

/** 卸载时放掉自己那份；拿别页的 token 来放无效（防"晚到的清理抹掉新页"）。 */
export function releasePageCrumb(token: object): boolean {
  if (entry?.token !== token) return false;
  entry = null;
  notify();
  return true;
}

/** 快照：引用稳定，`useSyncExternalStore` 才不会死循环。 */
export function readPageCrumb(): PageCrumb | null {
  return entry?.value ?? null;
}

export function subscribePageCrumb(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
