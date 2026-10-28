import { useEffect, useMemo, useState } from "react";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { pctColor } from "@/lib/colors";
import { Plus, X, RefreshCw, Star } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { StockSearch } from "@/components/ui/StockSearch";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { api, type Quote } from "@/lib/api";
import { loadWatch, saveWatch, addCodes } from "@/lib/watchlist";
import { cn } from "@/lib/utils";
import { StockLink } from "@/components/ui/StockLink";

const color = pctColor;   // 红涨绿跌口径见 lib/colors.ts
const pct = (v: number | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`);

export function Watchlist({ embedded = false }: { embedded?: boolean }) {
  const [codes, setCodes] = useState<string[]>(loadWatch);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const wlSort = useTableSort();
  const [input, setInput] = useState("");
  const [pick, setPick] = useState("");
  const [loading, setLoading] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  /** 双击一行 → 另开一个浏览器窗口看这一只的「个股深度复盘」，外加单击一格整行＋整列交叉高亮：
   *  全站一份实现（`lib/useStockRowInteraction.ts`，用户 2026-10-15）。`reviewHint` 是 `popHint`
   *  的别名（弹窗被拦的告警由表下那一句念出来）；换排序／删一只时也把它与高亮一起清掉。 */
  const wl = useStockRowInteraction("本机名单");
  const reviewHint = wl.popHint;
  const sortWl = (k: string) => { wl.clearAll(); wlSort.toggle(k); };

  const refresh = (cs: string[]) => {
    if (!cs.length) { setQuotes({}); return; }
    setLoading(true);
    api.quote(cs.join(",")).then(setQuotes).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(() => { refresh(loadWatch()); }, []);

  /** 加进名单的唯一出口：批量粘贴那一格与上面「选一只」都走这里
   *  （两处各写一份去重与保存，迟早对不上）。上限与去重由 `lib/watchlist.ts` 那一份判。 */
  const addSome = (raw: string) => {
    try {
      const { next, added } = addCodes(codes, raw);
      if (added === 0) {
        setHint(raw.trim() ? "没识别到新的 6 位代码（可能已在名单里）" : null);
        return;
      }
      saveWatch(next); setCodes(next); setHint(`已添加 ${added} 只`);
      refresh(next);
    } catch(e) { setHint(e instanceof Error ? e.message : "自选保存失败"); }
  };
  const add = () => { addSome(input); setInput(""); };
  const remove = (c: string) => {
    try {
    const next = codes.filter((x) => x !== c);
    saveWatch(next); setCodes(next); refresh(next);
    wl.clearAll();
    } catch(e) { setHint(e instanceof Error ? e.message : "自选保存失败"); }
  };

  const aiContext = useMemo(
    () =>
      codes.length
        ? "我的自选股（本地）：\n" +
          codes
            .map((c) => {
              const q = quotes[c];
              return q
                ? `${q.name}(${c}) 现价${q.price} ${pct(q.change_pct)} PE(TTM)${q.pe_ttm ?? "—"} 换手${q.turnover_pct ?? "—"}%`
                : `${c}（行情未取到）`;
            })
            .join("\n")
        : "还没有自选股。",
    [codes, quotes],
  );
  // 「让 AI 读自选」的入口挪进右侧「AI分析」栏（用户 2026-10-07）。
  // 这份名单是**浏览器本地的自选**，不是库里的自选，label 上写明，免得读成同一份。
  usePageAskAi(codes.length ? {
    label: `自选股 · 本地名单（${codes.length} 只）`,
    context: aiContext,
    suggestions: ["这几只里哪些估值偏高", "帮我按赛道分组看看", "各自最大的风险点是什么"],
  } : null);

  return (
    <div>
      <PageHeader level={embedded ? 2 : 1}
        title="自选股"
        subtitle="批量添加、一屏总览你关注的标的。数据只存本地、不上传。"
      />

      <GlassCard className="mb-4">
        <div className="mb-2 flex flex-wrap items-end gap-3">
          {/* 一只一只加：换成全站共用的 `StockSearch`（用户 10-15：「添加股票请调用统一的那个
              筛选模块代码块，就是支持数字和拼音搜索个股的」，并且这类入口全站统一成一个控件）。
              判据与四种匹配（代码/名称/全拼/拼音首字母）只在后端那一处。 */}
          <div>
            <label className="mb-1.5 block text-xs text-muted-foreground">添加一只 —— 代码、名称或拼音首字母都行</label>
            <StockSearch value={pick} kinds="stock,etf" widthClass="w-64"
              ariaLabel="添加自选股：输入代码、名称或拼音首字母"
              onType={v => setPick(v.toUpperCase().slice(0, 12))}
              onPick={c => { setPick(""); addSome(c); }}
              onEnter={() => { addSome(pick); setPick(""); }}
              submit={<button type="button" onClick={() => { addSome(pick); setPick(""); }}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary/15 px-3 text-[12px] font-medium text-primary shadow-glow hover:bg-primary/25">
                <Plus className="h-3.5 w-3.5" /> 加进来
              </button>} />
          </div>
        </div>
        <label className="mb-1.5 block text-xs text-muted-foreground">
          批量添加 —— 粘贴一串代码即可（逗号 / 空格 / 换行都行，自动识别 6 位 A 股代码）
        </label>
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add();
            }}
            rows={2}
            placeholder={"如：600519 000858, 002463\n300750 688017"}
            className="flex-1 resize-y rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50"
          />
          <button
            onClick={add}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 self-start rounded-lg bg-primary/15 px-4 text-sm font-medium text-primary shadow-glow hover:bg-primary/25"
          >
            <Plus className="h-4 w-4" /> 添加
          </button>
        </div>
        {hint && <p role="status" className="mt-2 text-xs text-muted-foreground/70">{hint}</p>}
        {/* 弹窗被拦的告警（reviewHint＝popHint）不折进任何默认收起的说明框，摆在下面那张表的表下 */}
      </GlassCard>

      <GlassCard glow>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="flex items-center gap-1.5 font-semibold">
            <Star className="h-4 w-4 text-primary" /> 自选总览
            <span className="text-xs font-normal text-muted-foreground">（{codes.length}）</span>
          </h3>
          <button
            onClick={() => refresh(codes)}
            disabled={loading}
            className="text-muted-foreground hover:text-primary"
            title="刷新价格"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
        {codes.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground/60">
            还没有自选股，用上面的框粘贴一串代码批量添加。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  <th className="whitespace-nowrap px-2 py-2 font-medium"></th>
                  {([["名称", "name"], ["代码", "code"], ["现价", "price"], ["涨跌%", "change_pct"],
                     ["PE(TTM)", "pe_ttm"], ["PB", "pb"], ["换手%", "turnover_pct"]] as const).map(([label, key]) => (
                    <SortHeader key={key} label={label} sortKey={key}
                      dir={wlSort.dirOf(key)} onSort={sortWl}
                      title="这一列来自当日行情快照；拿不到行情的票没有这一格，恒排最后（空不等于 0）"
                      className="whitespace-nowrap px-2 py-2 font-medium" />
                  ))}
                  <th className="whitespace-nowrap px-2 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {wlSort.apply(codes.map((c0) => ({ c: c0, ...(quotes[c0] ?? {}) }))).map(({ c: cq }, i) => {
                  const c = cq;
                  const q = quotes[c];
                  return (
                    <tr key={c} {...wl.rowProps(i, c, "border-b border-border/30")}>
                      <td {...wl.cellProps(i, "name", "px-2 py-2.5 font-medium")}>{q?.name ? <StockLink code={c} name={q.name} /> : "—"}</td>
                      <td {...wl.cellProps(i, "code", "px-2 py-2.5 font-mono text-xs text-muted-foreground")}><StockLink code={c}>{c}</StockLink></td>
                      <td {...wl.cellProps(i, "price", cn("px-2 py-2.5 font-mono", color(q?.change_pct)))}>{q ? q.price : "—"}</td>
                      <td {...wl.cellProps(i, "change_pct", cn("px-2 py-2.5 font-mono", color(q?.change_pct)))}>{q ? pct(q.change_pct) : "—"}</td>
                      <td {...wl.cellProps(i, "pe_ttm", "px-2 py-2.5 font-mono text-muted-foreground")}>{q?.pe_ttm ?? "—"}</td>
                      <td {...wl.cellProps(i, "pb", "px-2 py-2.5 font-mono text-muted-foreground")}>{q?.pb ?? "—"}</td>
                      <td {...wl.cellProps(i, "turnover_pct", "px-2 py-2.5 font-mono text-muted-foreground")}>{q?.turnover_pct ?? "—"}</td>
                      <td className="px-2 py-2.5">
                        <button
                          onClick={() => remove(c)}
                          className="text-muted-foreground/50 hover:text-destructive"
                          title="移除"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {/* 怎么点这一张表：通用一句（实现全站一份）；弹窗被拦的告警单独念、不折进任何默认收起的说明框 */}
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
            {reviewHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{reviewHint}</p>}
          </div>
        )}
      </GlassCard>

      <Disclaimer />
    </div>
  );
}
