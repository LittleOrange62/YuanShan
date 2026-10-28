import { useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";
import { Caliber } from "@/components/ui/Caliber";
import { OWN_TRACK_LABEL, QD_PROVENANCE, QD_TRACK_LABEL, computedNote } from "@/lib/qd";
import type { Deviation, QdEnvelope } from "@/lib/qd";
import type { QdState } from "@/lib/qd";
import type { QdPayload } from "@/lib/qd";

/**
 * 双轨展示的共用外壳。02 §1 的三条"不许"都在这里收口：
 * 两轨永不合并（每个面板各自渲染两个 TrackSection），不做"综合"值，
 * qd 轨首次出现必须带来源声明。
 */

/**
 * 「这屏的数是谁算的」一行。后端 2026-09-25 起在 qd 轨端点上给了 `computed_by`，
 * 但它放在响应顶层、不在 TrackSection 的作用域里，所以做成独立行组件，
 * 由各个面板摆在自己能拿到 data 的位置。
 */
export function ComputedLine({ env }: { env?: QdEnvelope | null }) {
  const note = computedNote(env);
  if (!note) return null;
  return <p className="mb-2 text-[10px] leading-relaxed text-muted-foreground/90">{plain(note)}</p>;
}

export function TrackTag({ track, className }: { track: "own" | "qd"; className?: string }) {
  const own = track === "own";
  return (
    <span
      className={cn(
        "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold leading-none",
        own
          ? "bg-primary/15 text-primary"
          : "border border-dashed border-primary/40 text-primary/80",
        className,
      )}
      title={own
        ? "本地口径：analytics.* / 本地日线实算"
        : "quantdash 口径：qd schema，重实现 quantdash 的算法口径"}
    >
      {own ? OWN_TRACK_LABEL : QD_TRACK_LABEL}
      {!own && <span className="ml-1 rounded bg-primary/10 px-1 text-[9px]">QD</span>}
    </span>
  );
}

export function TrackSection({
  track, title, hint, provenance, className, children,
}: {
  track: "own" | "qd"; title: string; hint?: string; provenance?: boolean;
  className?: string; children: React.ReactNode;
}) {
  const qd = track === "qd";
  return (
    <section className={cn(
      "rounded-xl p-3",
      qd ? "border border-dashed border-primary/35 bg-muted/20" : "border border-border bg-background/40",
      className,
    )}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <TrackTag track={track} />
        <h4 className="text-[13px] font-semibold">{title}</h4>
      </div>
      {hint && <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">{plain(hint)}</p>}
      {children}
      {/* 02 §1 第 3 条：qd 轨首次出现的卡片要带这句 —— 既是法律要求也是诚实要求 */}
      {qd && provenance !== false && (
        <p className="mt-2 border-t border-dashed border-primary/25 pt-1.5 text-[10px] leading-relaxed text-muted-foreground/80">
          {plain(QD_PROVENANCE)}
        </p>
      )}
    </section>
  );
}

/** 02 §8 第 2 条：deviation 必须原样透出并可展开。藏起来=让用户以为 1:1 复刻 */
export function DeviationList({ items, count }: { items?: Deviation[]; count?: number }) {
  const [open, setOpen] = useState(false);
  const list = Array.isArray(items) ? items.filter(x => x && (x.code || x.note)) : [];
  if (!list.length) {
    return (
      <p className="mt-1.5 text-[10px] text-muted-foreground/70">
        差异清单：后端未给出 <code>deviation[]</code>（无法判断是否与 quantdash 原实现等价）
      </p>
    );
  }
  const total = count ?? list.length;
  return (
    <div className="mt-2 border-t border-dashed border-primary/25 pt-1.5">
      <button type="button" onClick={() => setOpen(v => !v)} aria-expanded={open}
        className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        与 quantdash 原实现的差异 {list.length} 条{count != null && count !== list.length ? `（本屏共涉及 ${total} 处口径改写）` : ""}
      </button>
      {open && (
        <ul className="mt-1 space-y-1">
          {list.map((d, i) => (
            <li key={`${d.code ?? "d"}-${i}`} className="flex gap-1.5 text-[11px] leading-relaxed">
              <span className="shrink-0 rounded bg-muted px-1 font-mono text-[10px]">{plain(d.code) || "—"}</span>
              <span className="text-muted-foreground">{plain(d.note) || "（后端未给说明）"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 阶段胶囊：必须能展开"命中了哪一条"（02 §2 第 1 条），兜底也要给理由 */
export function StagePill({
  label, stage, reason, muted, emptyText = "该列为 NULL",
}: {
  label: string; stage?: string | null; reason?: string | null;
  muted?: boolean; emptyText?: string;
}) {
  const [open, setOpen] = useState(false);
  const has = stage != null && String(stage).trim() !== "";
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <button type="button" onClick={() => setOpen(v => !v)} disabled={has && !reason}
        aria-expanded={open}
        className={cn(
          "mt-0.5 inline-flex max-w-full items-center gap-1 rounded-lg px-2 py-1 text-[12px] font-semibold",
          !has && "border border-dashed border-border text-muted-foreground",
          has && muted && "bg-muted/50 text-muted-foreground",
          has && !muted && "bg-primary/15 text-primary",
          has && reason && "hover:bg-primary/25",
        )}>
        <span className="truncate">{has ? plain(stage) : "—"}</span>
        {has && reason && <ChevronRight className={cn("h-3 w-3 shrink-0 transition-transform", open && "rotate-90")} />}
      </button>
      {open && (
        <p className="mt-1 rounded-lg bg-background p-2 text-[11px] leading-relaxed text-muted-foreground ring-1 ring-border">
          {reason
            ? <>命中条件：{plain(reason)}</>
            : "后端未给出命中条件（契约 §8 要求 stage_reasons 带 column）"}
        </p>
      )}
      {!has && <p className="mt-0.5 text-[10px] text-muted-foreground/70">{plain(emptyText)}</p>}
    </div>
  );
}

/**
 * 区间分布条。给情绪周期的三列 stage 和天梯的 tie-break 共用。
 *
 * 三条设计纪律：
 * · **零值档照样占一行**并显式写 0 —— "冰点一次都没出现"是结论，不是缺失；
 * · 按后端给的**优先级顺序**排（不是按大小排），因为要让人看见"谁挡在谁前面"；
 * · 颜色只表"存在与否"，不表好坏（分布不是涨跌）。
 */
export function DistBars({
  title, order, counts, sampleTotal, neverSeen, caliber, labelOf,
}: {
  title: string; order: string[]; counts: Record<string, number>;
  sampleTotal?: number | null; neverSeen?: string[]; caliber?: string;
  labelOf?: (key: string) => string;
}) {
  const total = sampleTotal ?? order.reduce((n, k) => n + (counts[k] ?? 0), 0);
  const keys = [...order, ...Object.keys(counts).filter(k => !order.includes(k))];
  return (
    <div className="mt-2 border-t border-dashed border-primary/25 pt-2">
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-semibold text-muted-foreground">{title}</span>
        <span className="text-[10px] text-muted-foreground/70">共 {total ?? "—"} 场</span>
        {caliber && <Caliber text={caliber} />}
      </div>
      <ul className="space-y-0.5">
        {keys.map((k, i) => {
          const n = counts[k] ?? 0;
          const pctv = total ? (n / total) * 100 : 0;
          const zero = n === 0;
          return (
            <li key={k} className="flex items-center gap-2 text-[11px]">
              <span className="w-3 shrink-0 text-right font-mono text-[9px] text-muted-foreground/60" title="优先级（1 = 最先命中，后面的档被它挡住）">
                {i + 1}
              </span>
              <span className={cn("w-32 shrink-0 truncate", zero && "text-muted-foreground/70")}>
                {labelOf ? labelOf(k) : k}
              </span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted/60">
                {!zero && <span className={cn("block h-full rounded-full bg-primary/60")} style={{ width: `${Math.max(1, Math.round(pctv))}%` }} />}
              </span>
              <span className={cn("w-24 shrink-0 text-right tabular-nums", zero ? "text-muted-foreground/70" : "text-foreground")}>
                {n} 场{total ? ` · ${pctv.toFixed(1)}%` : ""}
                {zero && neverSeen?.includes(k) && <span className="ml-1 text-[9px]">从未命中</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * 面板状态机。四种状态视觉必须可区分：
 * 加载中 / 接口或后端异常 / 后端明说不可用（available:false + reason）/ 正常。
 * 后两种尤其不能合并 —— 前者是"这一屏现在就没数"，后者是"契约说取不到且给了原因"。
 */
export function PanelState<T extends object>({
  state, emptyReason, children,
}: {
  state: QdState<T>; emptyReason?: string | null; children: (data: T & { available: true }) => React.ReactNode;
}) {
  if (state.kind === "loading") {
    return <p className="text-[12px] text-muted-foreground">取数中…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="flex items-start gap-1.5 rounded-lg bg-muted/40 p-2 text-[11px] leading-relaxed text-muted-foreground">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{plain(state.reason)}</span>
      </p>
    );
  }
  const payload = state.payload as QdPayload<T>;
  if (payload.available !== true) {
    return (
      <p className="flex items-start gap-1.5 rounded-lg border border-dashed border-border bg-muted/30 p-2 text-[11px] leading-relaxed text-muted-foreground">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          本屏无数据。{emptyReason ? plain(emptyReason) : ""}
          后端给出原因：{payload.reason && String(payload.reason).trim()
            ? plain(payload.reason)
            : <b>（缺失 —— available=false 未带 reason，违反展示规范 01 第一条，请反馈）</b>}
        </span>
      </p>
    );
  }
  return <>{children(payload)}</>;
}
