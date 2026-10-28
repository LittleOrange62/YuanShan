import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * 滚到附近才挂载这一块（2026-10-26 首页滚动/挂载提速，用户点名「界面功能点从上到下陆续加载和显示」）。
 *
 * 三条设计取舍，都比"加个 IO"这件事本身容易做错：
 *  1. **只延后、不卸载**：一旦挂载就留着。往回滚若重新卸载，读者会看到同一块数据两次"取数中"，
 *     而 `useProbe` 重新发一次还等于多打一发接口（首页那些接口里有 3~5 秒的聚合）。
 *  2. **不预留高度**：预留要靠猜（这些块明天就不是这个高度了），猜错会在读者**眼皮底下**把行挪走；
 *     哨兵只有 1px、挂载发生在视口外 `near` px 那一侧 ⇒ 位置跳动发生在看不见的地方，
 *     代价只是滚动条滚到下面才长齐 —— 这比"读的位置突然跳"轻。
 *  3. **占位那一行必须说实话**：写清"还没画、不是没数"。空白的框会被读成"这一块今天没数据"，
 *     那是假读数（同《数据口径展示规范》第一条）。
 *
 * ⚠️ 它延后的只是**渲染**：包在里面那块自己的取数（`useProbe`）会跟着晚发，
 *    所以接进来的块必须是"自己取数、自己带控件"的整块；
 *    把父页已经取好、别的块还要共用的一份数据塞进来延后＝下面那些块会读到空的。
 * 浏览器没有 `IntersectionObserver` 时直接画（宁可多画，不许永远不画）。
 */
export function LazySection({ label, near = 900, children }: {
  /** 占位行上要念出来的块名（和真正的标题不是一套词就不许接：读者靠它认这是哪一块） */
  label: string;
  /** 提前多少像素挂载。太小＝滚快了会看见占位行；太大＝省不到东西 */
  near?: number;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState(() => typeof IntersectionObserver !== "function");

  useEffect(() => {
    if (on) return;
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) {
        setOn(true);
        io.disconnect();
      }
    }, { rootMargin: `${near}px 0px ${near}px 0px` });
    io.observe(el);
    return () => io.disconnect();
  }, [on, near]);

  if (on) return <>{children}</>;
  return (
    <div ref={box} className="mb-6 rounded-lg border border-dashed border-border/70 px-3 py-2">
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        <b className="text-foreground">{label}</b> —— 还没画：这一块在下面，滚近才渲染。
        <b className="text-warning">这不是「没有数据」</b>，取数与读数都还没发，所以这里不给任何数字。
      </p>
    </div>
  );
}
