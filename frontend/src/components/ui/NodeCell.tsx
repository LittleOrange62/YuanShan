import { cn } from "@/lib/utils";
import { nodeText, nodeTone, titleOf, type NodeRow } from "@/lib/nodeCell";

/**
 * 「节点分析」那一格，全站一份实现（首板页与首页连板榜都调这里）。
 *
 * 判据、口径原句、"这一场读不读得动"都不在这一层：这里只把后端交出的 `node_hits`
 * 与 `node_reason` 画成一格，悬停文字由 `lib/nodeCell.ts` 拼（那一份也管短码翻译）。
 */
export function NodeCell({ row, rule, className }: {
  row: NodeRow; rule?: string | null; className?: string;
}) {
  const hit = nodeTone(row) === "hit";
  return (
    <span
      title={titleOf(row, rule)}
      className={cn("rounded px-1 py-0.5 font-mono text-[11px]",
        hit ? "bg-primary/15 font-semibold text-primary" : "text-muted-foreground/55",
        className)}>
      {nodeText(row)}
    </span>
  );
}

/** 整场那一句声明：非空就画，而且调用方不许把它塞进默认收起的说明框（收着等于没说）。 */
export function NodeWarn({ text }: { text?: string | null }) {
  const t = String(text ?? "").trim();
  if (!t) return null;
  return (
    <p className="mt-1.5 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
      {t}
    </p>
  );
}
