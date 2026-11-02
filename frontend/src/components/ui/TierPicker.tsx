import { cn } from "@/lib/utils";
import { NoticeBar } from "@/components/ui/NoticeBar";

/**
 * 连板／连续跌停档位的多选控件（短线复盘页那两块共用这一份）。
 *
 * 为什么收成组件而不是两块各写一遍：涨停与跌停的档位**是同一套键**（后端 `market.TIER_KEYS`），
 * 只有中文不一样（跌停没有「板」这个字）。两处各写一份，迟早一边能筛四档、另一边只能筛三档。
 *
 * 三条界面上的线：
 *  · 每个按钮后面那个数是**本场这一档的家数**（后端算的，母体不跟着勾选走），
 *    不是这一屏筛完剩下的行数 —— 拿筛后的行数标"全场"是本项目的老毛病；
 *  · `0` 与「没读到」是两种读数：0 就是这一档今天真没有票，按钮照样可点；
 *  · 一档都没勾 = 这张表没有母体，**不许静默发一个空 `tiers`**（空档名在后端是"用默认那批"，
 *    屏幕上会读出与勾选完全不同的名单）。所以这里拦下来并把话说清楚。
 */
export function TierPicker({ keys, labels, counts, total, selected, onToggle, side }: {
  keys: string[]; labels: Record<string, string>; counts?: Record<string, number> | null;
  /** 档位计数的母体一共几只（这一排数字加起来应该等于它，屏上要说清分母是谁） */
  total?: number | null;
  selected: string[]; onToggle: (k: string) => void;
  /** 只有措辞不同：涨停那一侧勾满叫"二板及以上"，跌停那一侧勾不到"板"这个字 */
  side: "up" | "down";
}) {
  const none = selected.length === 0;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted-foreground">
        {side === "up" ? "连板档位（可多选）：" : "连续跌停档位（可多选）："}
      </span>
      {keys.map(k => (
        <button key={k} type="button" aria-pressed={selected.includes(k)}
          onClick={() => onToggle(k)}
          className={cn("rounded px-2 py-0.5 text-[11px]",
            selected.includes(k) ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground hover:text-foreground")}>
          {labels[k] ?? k}
          {/* 这个数是全场这一档的家数（不是筛后的行数），所以勾与不勾它都不变 */}
          　{typeof counts?.[k] === "number" ? counts[k] : "—"}
        </button>
      ))}
      <span className="text-[11px] text-muted-foreground/60">
        合计 {typeof total === "number" ? total : "—"} 只（这一排数加起来应当等于它）
      </span>
      {/* 一档没勾：不发请求（空 `tiers` 在接口那边是"用默认那批"），并把这句留在屏上 */}
      {none && <NoticeBar kind="missing" className="mt-0 w-full">
        没有勾任何一档 ⇒ 这张表不发请求、也没有名单。这是「筛到没东西」，
        不是「{side === "up" ? "今天没有涨停" : "今天没有跌停"}」。
      </NoticeBar>}
    </div>
  );
}

/** 勾选 → 接口参数：升序按后端给的那份键序排（前端不自己排序，键序也是一份口径）。 */
export function tiersParam(keys: string[], selected: string[]): string {
  return keys.filter(k => selected.includes(k)).join(",");
}
