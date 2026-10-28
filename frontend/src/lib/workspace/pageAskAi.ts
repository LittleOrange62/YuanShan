import { useEffect, useRef, useSyncExternalStore } from "react";
import {
  readPageAskAi, registerPageAskAi, releasePageAskAi, subscribePageAskAi, type PageAskAi,
} from "./pageAskAiStore";

/**
 * 页面登记"这一屏可以带给 AI 的材料"，右侧「AI分析」栏读它。
 * 判定与规矩都住在 `pageAskAiStore.ts`（纯逻辑、可单测），这里只接 React 生命周期。
 *
 * 用法：`usePageAskAi(data ? { label, context, suggestions } : null)` —— 没数据时传 null，
 * 栏里就明说"这一屏没有可带的材料"，不拿旧内容充数。
 */
export function usePageAskAi(value: PageAskAi | null): void {
  const token = useRef({}).current;
  // 卸载：只放自己那一份（晚到的清理不许抹掉新页刚登记的）
  useEffect(() => () => { releasePageAskAi(token); }, [token]);
  // 每次渲染后按需覆盖：内容没变不通知，免得自动刷新把栏和流式回答抖重挂
  useEffect(() => { registerPageAskAi(token, value); });
}

export function useReadPageAskAi(): PageAskAi | null {
  return useSyncExternalStore(subscribePageAskAi, readPageAskAi, readPageAskAi);
}
