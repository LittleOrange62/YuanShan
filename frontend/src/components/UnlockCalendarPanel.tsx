import { useEffect, useMemo, useState } from "react";
import { api, type UnlockCalendar } from "@/lib/api";
import { useNonce, useProbe } from "@/lib/probe";
import { GlassCard } from "@/components/ui/GlassCard";
import { StockLink } from "@/components/ui/StockLink";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { PAGE_SIZES, clampPage, pageCount, pageSentence, slicePage } from "@/lib/paging";

/**
 * 限售解禁日历。
 *
 * 「只看自选」的自选名单原先读的是**本浏览器的 localStorage**（首页那块自选卡带的），
 * 首页卡 2026-10-06 下线后如果还那么读，这个勾选框就会在一个没存过本地名单的浏览器上
 * 永远筛出空集 —— 所以改成读库里的自选（`/api/watchlist/stocks`，同花顺同步过来的那份）。
 * 匹配用 6 位代码：库里是 `600519.SH` 这种带后缀的形式，解禁接口的 code 形态不完全一致。
 *
 * 两处 2026-10-25 的改动，理由都是实测数字：
 * · 名单只要 `fields=codes` 那一列（原来整明细一发 457,717 字节 / 1.4 秒，而这里只读代码）；
 * · 表分页，一页 25 行（该窗口实测 68 条事件全量直出，首页一屏塞不下也不该塞）。
 *   分页用全站那一份 `lib/paging.ts`，页数与"共几行"不在这里各算一遍。
 */
/** `/api/watchlist/stocks?fields=codes` 只回代码列；明细留给分组自选页去读 */
interface WatchRow { security_code?: string | null }
interface WatchStocks { rows?: WatchRow[] | null }

export function UnlockCalendarPanel() {
  const nonce = useNonce()[0];
  const watch = useProbe<WatchStocks>("/watchlist/stocks", nonce, { fields: "codes" });
  const watchCodes = useMemo(() => new Set(
    (watch.kind === "ok" ? watch.data.rows ?? [] : [])
      .map((r) => String(r.security_code ?? "").slice(0, 6)).filter(Boolean)),
    [watch]);
  const [window, setWindow] = useState<"upcoming" | "recent">("upcoming");
  const [onlyWatch, setOnlyWatch] = useState(false);
  const [data, setData] = useState<UnlockCalendar | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [page, setPage] = useState(1);
  const ukSort = useTableSort();
  useEffect(() => {
    let active = true;
    setData(null); setError("");
    api.unlockCalendar(window).then(value => { if (active) setData(value); })
      .catch(() => { if (active) setError("解禁日历取数失败，暂不能判断有无事件。"); });
    return () => { active = false; };
  }, [window, reload]);
  const events = data?.events.filter(row => !onlyWatch || watchCodes.has(String(row.code).slice(0, 6))) ?? [];
  const number = (value: number | null) => value == null ? "—" : value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  // 排序在分页之前（否则每页都在换口径）；页数与页码一律走 lib/paging，不在这儿重算
  const sorted = ukSort.apply(events.map(row => ({ ...row, stock_label: row.name || row.code })));
  const pageSize = PAGE_SIZES[0];
  const pages = pageCount(sorted.length, pageSize);
  const cur = clampPage(page, pages);
  const paged = slicePage(sorted, cur, pageSize);
  /** 换排序档回到第一页：第 3 页的那个行号在另一把尺子下不是同一批事件。 */
  const sortBy = (key: string) => { setPage(1); ukSort.toggle(key); };
  return <GlassCard className="mb-4">
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <h3 className="text-sm font-semibold">限售解禁日历</h3>
      <select aria-label="解禁日期范围" value={window} onChange={event => { setWindow(event.target.value as typeof window); setPage(1); }} className="rounded border border-border bg-background px-2 py-1 text-sm">
        <option value="upcoming">未来十天（含今天）</option><option value="recent">最近十天（含今天）</option>
      </select>
      <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={onlyWatch} onChange={event => { setOnlyWatch(event.target.checked); setPage(1); }} />只看自选</label>
      {error && <button type="button" onClick={() => setReload(n => n + 1)} className="text-sm text-primary">重试</button>}
    </div>
    <p className="mb-3 text-xs text-muted-foreground">来源：东方财富公开解禁计划；解禁不等于实际减持，以公司公告为准。按日历日统计，自选取库里那份（分组自选页维护的），跨浏览器一致。</p>
    {error ? <p role="alert" className="text-sm text-warning">{error}</p> : !data ? <p role="status" className="text-sm text-muted-foreground">正在读取解禁日历…</p> : <>
      <p className="mb-2 text-xs text-muted-foreground">{data.start} 至 {data.end} · {events.length} 条{data.truncated ? "（仅覆盖前 500 条，无法据此排除其他事件）" : ""}</p>
      {events.length === 0 ? <p className="text-sm text-muted-foreground">{onlyWatch && watch.kind !== "ok" ? "自选名单这一会儿没读到，所以没法按自选筛 —— 先取消勾选看全部，或到分组自选页重取。" : onlyWatch && watchCodes.size === 0 ? "库里还没有自选 —— 到「分组自选」页加，或从同花顺同步。" : data.truncated ? "已返回的部分记录中未匹配事件。" : "数据源在该范围内未返回匹配的解禁记录。"}</p> :
        <><div className="max-h-80 overflow-auto"><table className="w-full text-left text-xs">
          <caption className="sr-only">限售解禁计划；数量为万股，占比为占总股本百分比</caption>
          <thead><tr>
            <SortHeader label="日期" sortKey="date" dir={ukSort.dirOf("date")} onSort={sortBy} />
            <SortHeader label="标的" sortKey="stock_label" dir={ukSort.dirOf("stock_label")} onSort={sortBy}
              title="按格子里显示的名字排（缺名字回落代码）；同一只票多条解禁批次会挨在一起" />
            <SortHeader label="类型" sortKey="type" dir={ukSort.dirOf("type")} onSort={sortBy} />
            <SortHeader label="本次解禁（万股）" sortKey="shares" dir={ukSort.dirOf("shares")} onSort={sortBy}
              title="万股原值，前端不换算成亿元/亿股" />
            <SortHeader label="占总股本" sortKey="ratio" dir={ukSort.dirOf("ratio")} onSort={sortBy}
              title="占比缺失的行排最后，显示为 — 而不是 0%" />
          </tr></thead>
          <tbody>{paged.map((row, i) => <tr key={`${row.code}-${row.date}-${i}`} className="border-t border-border/50">
            <td className="whitespace-nowrap px-2 py-2">{row.date}</td><td className="whitespace-nowrap px-2 py-2"><StockLink code={row.code} name={row.name} className="hover:text-primary" /> <StockLink code={row.code} className="font-mono hover:text-primary">{row.code}</StockLink>{watchCodes.has(String(row.code).slice(0, 6)) && <span className="ml-1 text-primary">自选</span>}</td>
            <td className="px-2 py-2">{row.type}</td><td className="px-2 py-2">{number(row.shares)}</td><td className="px-2 py-2">{row.ratio == null ? "—" : `${number(row.ratio)}%`}</td>
          </tr>)}</tbody>
        </table></div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-muted-foreground">{pageSentence(sorted.length, pageSize, cur)}</span>
          {cur > 1 && <button type="button" onClick={() => setPage(cur - 1)}
            className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>}
          {cur < pages && <button type="button" onClick={() => setPage(cur + 1)}
            className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>}
        </div></>}
    </>}
  </GlassCard>;
}
