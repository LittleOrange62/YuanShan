import { useState } from "react";
import { Caliber } from "@/components/ui/Caliber";
import { FLAT_TEXT } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";
import type { NewsItemRow, QdNews, QdState } from "@/lib/qd";
import { DeviationList } from "./Track";

/**
 * 02 §6 信息采集：**宁可空，不可假**。
 *
 * 三条最容易被打折的地方在这里收口：
 *  · 没有快讯就写"当日无采集到的资讯"，绝不回落任何看起来像新闻的东西；
 *  · `sentiment` 为 NULL 就**不显示情感标签**（NULL 是"没做情感分析"，不是"中性"）；
 *  · `published_at` 缺失时显示 `captured_at` 并明确标注它是**采集时刻**不是发布时刻。
 * 每条卡片同时给两个时刻，因为两者可能差很久（快讯不可回补，差值本身就是健康度）。
 */

export function NewsPanel({ state }: { state: QdState<QdNews> }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (state.kind === "loading") return <p className="text-[12px] text-muted-foreground">取数中…</p>;
  if (state.kind === "error") {
    return <p className="rounded-lg border border-dashed border-border bg-muted/30 p-2 text-[11px] leading-relaxed text-muted-foreground">{plain(state.reason)}</p>;
  }
  const payload = state.payload as Partial<QdNews> & { available?: boolean; reason?: string | null };
  const items = Array.isArray(payload.items) ? payload.items : [];

  // 0 条 / available:false 都走这一支。文案必须是"当日无采集到的资讯"，
  // 而不是"暂无数据"—— 前者说的是事实，后者看不出是坏了还是真没有。
  if (payload.available !== true || !items.length) {
    return (
      <p className="rounded-lg border border-dashed border-border bg-muted/30 p-2 text-[11px] leading-relaxed text-muted-foreground">
        当日无采集到的资讯。后端给出原因：
        {payload.reason && String(payload.reason).trim()
          ? plain(payload.reason)
          : <b>（缺失 —— available=false 未带 reason，违反展示规范 01 第一条，请反馈）</b>}
      </p>
    );
  }
  const data = payload as QdNews;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        <span>采集日 {plain(data.date)}</span>
        <span>共 {data.count ?? items.length} 条</span>
        {data.truncated && <span>已截断，仅显示前 {items.length} 条</span>}
        {!!data.corrupt_lines && <span className="text-warning">{data.corrupt_lines} 行解析失败（未静默丢弃）</span>}
        {data.supports_code_filter === false && (
          <span>不支持按个股过滤 —— 存储里没有个股关联字段，<b>不用标题匹配硬凑</b></span>
        )}
        <Caliber text="快讯不可回补：当天没采就永久没有。所以这里的日期是「采集日」，不是「发布日区间」。" />
      </div>
      <ul className="space-y-1.5">
        {items.map((it, i) => (
          <NewsRow key={it.item_id ?? i} it={it}
            open={expanded === (it.item_id ?? String(i))}
            onToggle={() => setExpanded(v => (v === (it.item_id ?? String(i)) ? null : (it.item_id ?? String(i))))} />
        ))}
      </ul>
      <DeviationList items={items.find(x => Array.isArray(x.deviation) && x.deviation.length)?.deviation} />
    </div>
  );
}

function NewsRow({ it, open, onToggle }: { it: NewsItemRow; open: boolean; onToggle: () => void }) {
  // published_at 缺失 → 退回 captured_at，但必须标注它是采集时刻
  const published = it.published_at ? plain(it.published_at) : null;
  const shownTime = published ?? plain(it.captured_at);
  return (
    <li className="rounded-lg border border-border bg-background/50 p-2">
      <button type="button" onClick={onToggle} aria-expanded={open} className="block w-full text-left">
        <span className="text-[12px] font-medium leading-snug">{plain(it.title) || "（无标题）"}</span>
      </button>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
        <span className={cn("font-mono", !published && FLAT_TEXT)} title={published ? "发布时刻（来自报文）" : "发布时刻缺失，此处显示的是采集时刻"}>
          {published ? `发布 ${shownTime}` : "采集 "}
          {!published && <b className="text-warning">{shownTime}</b>}
          {!published && <span> （采集时刻，非发布时刻）</span>}
        </span>
        {published && <span className="font-mono">采集 {plain(it.captured_at) || "—"}</span>}
        <span>{plain(it.source) || "—"}</span>
        {it.news_type && <span>· {plain(it.news_type)}</span>}
        {/* level / sentiment 为 NULL 就是不显示，绝不代填"中性" */}
        {it.level != null && <span className="rounded bg-muted px-1">级别 {String(it.level)}</span>}
        {it.sentiment != null && <span className="rounded bg-muted px-1">情感 {plain(it.sentiment)}</span>}
        {it.url && <a className="text-primary underline" href={it.url} target="_blank" rel="noopener noreferrer">原文</a>}
      </div>
      {open && (
        <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-muted-foreground">
          {plain(it.content) || "（该条没有正文，只有标题）"}
        </p>
      )}
    </li>
  );
}
