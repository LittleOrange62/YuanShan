import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { Contract } from "@/components/ui/DataPanel";
import { ageText, describeState, hasData } from "@/lib/datastate";
import { useProbe } from "@/lib/probe";
import { jobCardClass, jobLabelClass } from "@/lib/colors";
import { probeTimeLine, readTri, watchVerdict, type WatchFacts } from "@/lib/tdxWatch";
import { cn } from "@/lib/utils";

/**
 * 系统配置 →「通达信客户端看护（只读）」。
 *
 * 这一格是**先备好位置**的：判读三态与文案已经写完并单独测过，
 * 后端交来 `{last_probe_at, process_running, window_visible, login_dialog, syncing}` 就直接成真数。
 * 现在还没接上，就照实显示这一屏还没有数据来源 —— 不放示例数字，也不放"看起来正常"。
 *
 * 三态不许塌成两态：`未探测到` 与 `没在跑` 是两件事（判读在 `lib/tdxWatch.ts`）。
 */
export function TdxWatchPanel({ nonce }: { nonce: number }) {
  const state = useProbe<WatchFacts>("/tdx/watch", nonce);
  const text = describeState(state);
  const f = hasData(state) ? state.data : null;
  const v = f ? watchVerdict(f) : null;
  // "只读"这件事要说在前面：这一格不会去点客户端、不会启动它，读到的只是探测结果。
  const items: { label: string; r: ReturnType<typeof readTri> }[] = f ? [
    { label: "进程", r: readTri(f.process_running, "在跑", "进程不在") },
    { label: "主窗口", r: readTri(f.window_visible, "可见", "窗口不可见") },
    { label: "登录框", r: readTri(f.login_dialog, "弹出了（要人工点）", "没弹出") },
    { label: "同步", r: readTri(f.syncing, "正在同步", "当前没在同步") },
  ] : [];

  return (
    <GlassCard className={cn("mt-4 border", v && jobCardClass(v.tone === "unknown" ? "warn" : v.tone === "bad" ? "bad" : "normal"))}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">通达信客户端看护（只读探测）</h2>
        <Caliber text={
          "这一格只读不做任何操作：不启动客户端、不点窗口、不提交登录。\n" +
          "进程／窗口／登录框／同步四项是三态：在、不在、未探测到。未探测到不等于不在 —— 它只说明界面不知道。\n" +
          "最近探测时间来自后端的探测记录，不是本机时钟；没有记录就直说没有记录，不拿现在这一刻充数。"
        } />
        <span className="ml-auto text-[11px] text-muted-foreground">GET /api/tdx/watch</span>
      </div>

      {text && <p className="mb-2 text-[12px] leading-relaxed text-warning">{text}</p>}

      {f && (
        <>
          <p className="mb-2 text-[12px] leading-relaxed">
            <span className={cn("mr-2 rounded-lg px-2 py-0.5 text-[11px]",
              jobLabelClass(v!.tone === "unknown" ? "warn" : v!.tone === "bad" ? "bad" : "normal"))}>
              {v!.tone === "bad" ? "要人管" : v!.tone === "unknown" ? "判断不了" : "正常"}
            </span>
            {v!.text}
          </p>
          <dl className="grid gap-1 text-[12px] sm:grid-cols-2">
            {items.map(it => (
              <div key={it.label} className="flex items-baseline justify-between gap-2 border-b border-border/30 pb-1">
                <dt className="text-muted-foreground">{it.label}</dt>
                <dd className={it.r.tone === "unknown" ? "text-muted-foreground"
                  : it.r.tone === "bad" ? "text-destructive" : "text-foreground"}>{it.r.text}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-[11px] text-muted-foreground">{probeTimeLine(f, ageText)}
            {f.reason?.trim() ? `　后端说明：${f.reason.trim()}` : ""}</p>
        </>
      )}

      <Contract path="GET /api/tdx/watch" source="只读探测：进程与窗口状态由本机探测结果写入 jsonb 状态表，界面只转述、不启动也不点击" fields={[
        ["available", "true 才是这一格有数；缺失算「没表态」，不算可用"],
        ["last_probe_at", "最近一次探测成功的时间，空值直说是没有记录"],
        ["process_running", "在 true / 不在 false / 未探测到 null —— 三态，不许把 null 当 false"],
        ["window_visible", "主窗口是否可见（不可见时自动化取数拿不到数）"],
        ["login_dialog", "登录框是否弹出（弹出＝自动链路停下等人）"],
        ["syncing", "是否正在同步"],
        ["age_seconds", "探测结果距今多久，用来分「刚探的」与「几小时前的」"],
      ]} />
    </GlassCard>
  );
}
