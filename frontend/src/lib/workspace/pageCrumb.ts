import { useEffect, useRef, useSyncExternalStore } from "react";
import {
  readPageCrumb, registerPageCrumb, releasePageCrumb, subscribePageCrumb, type PageCrumb,
} from "./pageCrumbStore";

/**
 * 页面登记「这一屏现在吃的对象」，顶部面包屑读它（用户 2026-10-28：深度复盘页那个
 * 「现在复盘的是哪一只」展示框撤掉，代码与名称改跟在「工作空间 / 个股深度复盘」后面）。
 *
 * 用法：`usePageCrumb(code ? { text: code, hint } : null)`。没对象时传 `null` ⇒ 顶栏回到
 * 只有页面标题，不拿上一只票的名字充数（规矩都住在 `pageCrumbStore.ts`）。
 */
export function usePageCrumb(value: PageCrumb | null): void {
  const token = useRef({}).current;
  useEffect(() => () => { releasePageCrumb(token); }, [token]);
  useEffect(() => { registerPageCrumb(token, value); });
}

export function useReadPageCrumb(): PageCrumb | null {
  return useSyncExternalStore(subscribePageCrumb, readPageCrumb, readPageCrumb);
}
