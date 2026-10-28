import { useEffect, useRef, useState } from "react";

/**
 * 长页目录：把当前屏里所有卡片标题扫出来排成一排，点一下跳过去。
 *
 * 为什么要有这一条（10-16 用户原话：「系统设置里为什么我还是没有看到这个推送功能相关的按钮」）：
 * `/system` 整屏约 **13,900px 高**，「消息推送」那块排在 **12,646px**，
 * 也就是要往下翻十几屏才第一次看得见它 —— 功能做完了但人找不到，等于没做。
 *
 * 这里刻意**不写死清单**：标题是从屏幕上现扫的，所以
 * ① 以后新加一块会自动出现在目录里（写死的清单第二天就会少一行，
 *    而"目录里少一行"与"那块不存在"在用户眼里是同一件事）；
 * ② 目录上的字与屏幕上那块的名字**必然逐字一致**（同一个节点读出来的）。
 */

interface Entry { id: string; text: string; level: number }

/** 目录自己那一行、以及右侧「AI分析」栏的标题不该进目录。 */
const SKIP = ["本页目录"];

function headingKey(h: HTMLElement, i: number): string {
  if (!h.id) {
    const slug = (h.textContent || "").trim().replace(/\s+/g, "").slice(0, 12) || "block";
    h.id = `sec-${i + 1}-${slug}`;
  }
  return h.id;
}

export function SectionNav() {
  const anchor = useRef<HTMLDivElement | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);

  useEffect(() => {
    const scan = () => {
      const host = anchor.current?.closest("main") || document.querySelector("main");
      if (!host) return;
      const nodes = Array.from(host.querySelectorAll("h2, h3")) as HTMLElement[];
      const next: Entry[] = [];
      nodes.forEach((h, i) => {
        const text = (h.textContent || "").trim();
        if (!text || SKIP.some((s) => text.startsWith(s))) return;
        if (anchor.current && anchor.current.contains(h)) return;   // 别把目录自己扫进来
        next.push({ id: headingKey(h, i), text, level: h.tagName === "H3" ? 2 : 1 });
      });
      // 内容还没渲染完时那一屏只有零星几块；块数变了才重排，避免边加载边闪。
      setEntries((prev) => (
        prev.length === next.length && prev.every((p, i) => p.id === next[i].id) ? prev : next));
    };
    scan();
    // 卡片是各自异步加载的（读接口回来才渲染），所以要在 DOM 变化后重扫一次。
    const host = anchor.current?.closest("main") || document.querySelector("main");
    let timer = 0;
    const obs = new MutationObserver(() => {
      window.clearTimeout(timer); timer = window.setTimeout(scan, 300);
    });
    if (host) obs.observe(host, { childList: true, subtree: true });
    return () => { window.clearTimeout(timer); obs.disconnect(); };
  }, []);

  const jump = (id: string) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    el.classList.add("ring-2", "ring-primary/40");
    window.setTimeout(() => el.classList.remove("ring-2", "ring-primary/40"), 1200);
  };

  if (entries.length < 4) return null;      // 短屏不需要目录（有了反而占地方）
  return (
    <div ref={anchor} className="sticky top-0 z-20 -mx-1 mt-3 bg-background/95 px-1 py-1.5 backdrop-blur">
      <div className="flex items-start gap-2">
        <span className="mt-1 shrink-0 text-[11px] font-medium text-muted-foreground">
          本页目录（{entries.length} 块）</span>
        <div className="flex flex-wrap gap-1">
          {entries.map((e) => (
            <button key={e.id} type="button" onClick={() => jump(e.id)}
              aria-label={`跳到 ${e.text}`} title={e.text}
              className={cnChip(e.level)}>{e.text}</button>))}
        </div>
      </div>
      <p className="mt-1 pl-[1px] text-[10px] leading-4 text-muted-foreground">
        这一屏很长，目录是现扫屏幕上的标题生成的：新加一块会自动出现在这里，
        所以这里没有的块就是真不在这一屏上，不是「藏起来了」。屏数不写在这里（写了就会过期）。</p>
    </div>
  );
}

function cnChip(level: number): string {
  return "rounded-lg border px-2 py-0.5 text-[11px] hover:border-primary/40 hover:text-primary "
    + (level === 2
      ? "border-dashed text-muted-foreground"
      : "border-border font-medium");
}
