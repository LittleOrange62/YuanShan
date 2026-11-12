import { GlassCard } from "@/components/ui/GlassCard";
import { DocDrawer } from "@/components/ui/DocDrawer";
import { Contract } from "@/components/ui/DataPanel";
import { plain } from "@/lib/agent";
import { ageText, describeState, hasData } from "@/lib/datastate";
import { useProbe } from "@/lib/probe";
import { jobCardClass, jobLabelClass } from "@/lib/colors";
import { ghostList, pidList, probeTimeLine, readTri, watchVerdict, type WatchFacts } from "@/lib/tdxWatch";
import { cn } from "@/lib/utils";

/**
 * 系统配置 →「通达信客户端看护（只读）」。
 *
 * 判读三态与文案在 `lib/tdxWatch.ts`（单独测过），后端交来
 * `{last_probe_at, process_running, live, ghost, usable, state_label, window_visible, login_dialog, syncing}`
 * 就直接成真数；读不到就照实显示这一屏没有数据来源 —— 不放示例数字，也不放"看起来正常"。
 *
 * 三态不许塌成两态：`未探测到` 与 `没在跑` 是两件事。
 * 2026-11-11 起还多一件：**残影条目**（tasklist 列着但 0 线程）既不是"在跑"也不是"没探到"，
 * 它是"探到了，且结论是进程已死" —— 单独一格说破，不然用户对着任务管理器那一行没法理解。
 */
export function TdxWatchPanel({ nonce }: { nonce: number }) {
  const state = useProbe<WatchFacts>("/tdx/watch", nonce);
  const text = describeState(state);
  const f = hasData(state) ? state.data : null;
  const v = f ? watchVerdict(f) : null;
  // "只读"这件事要说在前面：这一格不会去点客户端、不会启动它，读到的只是探测结果。
  const items: { label: string; r: ReturnType<typeof readTri> }[] = f ? [
    { label: "进程（活着的）", r: readTri(f.process_running, "在跑", "没有活着的实例") },
    { label: "活着的实例 pid", r: pidList(f.live, "没有") },
    { label: "残影条目", r: ghostList(f.ghost) },
    { label: "主窗口", r: readTri(f.window_visible, "可见", "窗口不可见") },
    { label: "登录框", r: readTri(f.login_dialog, "弹出了（要人工点）", "没弹出") },
    { label: "同步", r: readTri(f.syncing, "正在同步", "当前没在同步") },
  ] : [];

  // 「只读」这三句是这一块的口径说明（用户 10-31：从标题行旁的悬停口径牌换成全站那颗「这块的说明」，
  // 摆标题行最右边、排在 `GET /api/tdx/watch` 那枚出处标记之前）。原句一字不动，只是换了出口。
  const docLines = [
    "这一格只读不做任何操作：不启动客户端、不点窗口、不提交登录。",
    "进程／窗口／登录框／同步四项是三态：在、不在、未探测到。未探测到不等于不在 —— 它只说明界面不知道。",
    "判活只认线程数：任务管理器里还列着、但已经 0 线程的那一行叫残影条目 —— 进程其实死了，杀不掉，也不挡新实例启动。它单独摆一格，不算「在跑」。",
    "能不能取数由后端一处判（活着＋没停在登录框＋窗口不是假死；缩到托盘照样算可用）。收盘链也读同一份：只有判为不可用才先收掉再重新拉起，可用就复用，不无脑重启。",
    "最近探测时间来自后端的探测记录，不是本机时钟；没有记录就直说没有记录，不拿现在这一刻充数。",
  ];

  return (
    <GlassCard className={cn("mt-4 border", v && jobCardClass(v.tone === "unknown" ? "warn" : v.tone === "bad" ? "bad" : "normal"))}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">通达信客户端看护（只读探测）</h2>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <DocDrawer title="通达信客户端看护（只读探测）">
            {docLines.map(line => <p key={line.slice(0, 12)}>{plain(line)}</p>)}
          </DocDrawer>
          <span className="text-[11px] text-muted-foreground">GET /api/tdx/watch</span>
        </span>
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
            {(f.note ?? f.reason)?.trim() ? `　后端说明：${(f.note ?? f.reason)!.trim()}` : ""}</p>
        </>
      )}

      <Contract path="GET /api/tdx/watch" source="只读探测：进程与窗口状态由本机探测结果写入 jsonb 状态表，界面只转述、不启动也不点击" fields={[
        ["available", "true 才是这一格有数；缺失算「没表态」，不算可用"],
        ["last_probe_at", "最近一次探测成功的时间，空值直说是没有记录"],
        ["process_running", "有没有活着的实例 true/false / 未探测到 null —— 三态，不许把 null 当 false；残影条目不算在跑"],
        ["live", "真正活着的实例 pid 清单（线程数 > 0）"],
        ["ghost", "残影条目 pid 清单：tasklist 还列着但 0 线程，进程已死、杀不掉也不挡启动"],
        ["usable", "后端那一份可用性判据（活着＋没停在登录框＋不是假死；缩到托盘算可用）"],
        ["state_label", "六档处境的中文标签，措辞只住后端 watchman.STATE_LABELS"],
        ["window_visible", "主窗口是否可见（缩到托盘时不可见，但那不等于用不了 —— 看 usable）"],
        ["login_dialog", "登录框是否弹出（弹出＝自动链路停下等人）"],
        ["syncing", "是否正在同步"],
        ["age_seconds", "探测结果距今多久，用来分「刚探的」与「几小时前的」"],
      ]} />
    </GlassCard>
  );
}
