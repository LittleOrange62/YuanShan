import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { loadLoops, patchLoop, WEEK_DAY_LABELS, freqProblem, parseWeeklyDays, switchBadge,
  type LoopsState, type LoopRow } from "@/lib/closeChain";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";

const DASH = "—";

/**
 * 系统配置 →「常驻循环与定时任务」（用户 2026-10-07：「是不是有一大堆的定时任务，
 * 这块内容在系统配置里吗？是不是叫做调度器？梳理一下这个维度的任务情况」）。
 *
 * 为什么非要有这一屏：`/api/jobs/loops` 从 10-06 起就交齐了每一条常驻循环的开关、节奏、
 * 最近记账、留痕与"关掉会失去什么"，但**全仓没有任何前端消费它** —— 于是界面上只有
 * 收盘链那两段能关，其余几条只能开命令行。"有一大堆任务却看不见"这件事，
 * 缺的从来不是数据，是一个转述它们的格子。
 *
 * 三条读法必须在这里分开，混一条就会误判：
 *  · `alive` 只回答"线程在不在"；有没有记账看"最近记账"（活着但零笔记账是真实形状）；
 *  · 没有 `run_at` 的键**不渲染时点框**（间隔型/跟段跑的键给它一个框 = 改了也不生效）；
 *  · 关掉之后会失去什么逐条写出来（`fundflow` 当天不采永久少一天 vs `coverage` 只是首屏要等）。
 *
 * "调度器"这个词在本机同时指五件东西，所以这一屏刻意叫"常驻循环与定时任务"，
 * 并在底下把五个所指列出来 —— 让人知道改名比继续混着叫便宜。
 */

/** 同一个词在本机的五个所指。措辞与 `docs/系统配置工作指南/05-调度宿主.md` §二/§八 对齐。 */
const NAMING = [
  ["本页这一批", "本进程（uvicorn）里的常驻循环 —— 真在跑的那批，开关就在这屏。"
    + "条数跟着后端清单走（顶部那行「N 条」就是现读的数），本页不写死数字"],
  ["任务调度页顶部那条「调度器」", "说的是同一个本进程宿主的汇总，不是另一个调度器"],
  ["FinanceTool 内置调度器", "旧项目那套（`config.job` 36 行 + worker）：实测心跳停在 2026-09-25、"
    + "`enabled=false / dry_run=true / fired=0` —— 进程已死，页面上那些行是只读历史"],
  ["15:59 收盘链", "上面 `daily_close` / `fill_all` 那两条的合称，不是第三个东西"],
  ["Windows 计划任务", "本机现在**只有一条** `YuanShan-KeepAlive8910`（登录时 + 每 5 分钟，"
    + "动作是 `scripts\\keepalive_8910.vbs` 这个隐藏启动器，它再去跑 `scripts\\keepalive_8910.ps1`"
    + "—— 10-12 加的这层：任务直接起 powershell.exe 时系统会先建控制台窗口，`-WindowStyle Hidden` 是建完才藏，"
    + "所以每 5 分钟闪一下黑窗）：它只判「8910 活着没有」并把服务拉起来，"
    + "**不跑任何数据作业**；健康不通但端口有人监听时它只写告警、绝不 kill（怕打断正在跑的 15:59 链）。"
    + "三档备份任务仍未注册（注册要用户点头）。旧的两条 `FinanceTool-DailyCloseSync`、`FinToolboxWeb` 都已停用（没删）"],
];

export function LoopSwitchboard() {
  const [s, setS] = useState<LoopsState | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [slotDraft, setSlotDraft] = useState<Record<string, string>>({});
  // 通达信「保持在线的时间带」两格（`client_band_lo/hi`，用户 2026-10-19 点名可配）与
  // 「每日定时重启时刻」（`restart_daily_at`，默认 15:38）。和 slotDraft 一样是**本地草稿**：
  // 逐字符 PATCH 会把每个按键都变成一次写配置，而时点要的是"填完那一下"才写。
  const [bandDraft, setBandDraft] = useState<Record<string, string>>({});
  const [dailyAtDraft, setDailyAtDraft] = useState<Record<string, string>>({});
  const [noteDraft, setNoteDraft] = useState<Record<string, string>>({});
  const [timesDraft, setTimesDraft] = useState<Record<string, string>>({});

  const reload = useCallback(() => loadLoops()
    .then(r => { setS(r); setErr(""); })
    .catch(e => setErr(e instanceof Error ? e.message : "常驻循环清单读不到")), []);
  useEffect(() => { void reload(); }, [reload]);

  const run = async (label: string, fn: () => Promise<{ data: LoopsState; reason: string }>) => {
    setBusy(label); setNotice("");
    try {
      const r = await fn();
      setS(r.data); setNotice(r.reason);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "修改失败");
    } finally { setBusy(""); }
  };

  const loops = s?.loops ?? [];
  const offCount = loops.filter(l => l.switchable && l.enabled === false).length;

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">常驻循环与定时任务（本进程共 {s ? dashCount(s.declared_count) : DASH} 条在册）</h2>
        <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]",
          !s ? "bg-muted text-muted-foreground"
            : offCount ? "bg-warning/15 text-warning" : "bg-primary/15 text-primary")}>
          {!s ? "读取中" : `线程在 ${dashCount(s.alive_count)} 条`
            + (offCount ? `，其中 ${offCount} 条被关着` : "，在册的都在")}
        </span>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取
        </button>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {notice && <p className="mt-1 text-[11px] text-warning">{plain(notice)}</p>}
      {s && s.available === false && (
        <p className="mt-1 text-[11px] text-warning">{s.reason ?? "后端没给原因"}</p>
      )}
      {s && (s.missing_loops?.length ?? 0) > 0 && (
        <p className="mt-1 text-[11px] text-destructive">
          进程自报里没有这几条线程：{s.missing_loops!.join("、")} —— 通常是这份代码起了一个新进程还没重启，
          或那条启动失败（启动失败会在 stdout 留一行）。这里以进程自报为准，不拿清单充数。
        </p>
      )}
      {s && (
        <ul className="mt-2 space-y-2">
          {loops.map(l => (
            <LoopLine key={l.key} l={l} busy={busy}
              slotDraft={slotDraft[l.key] ?? ""} noteDraft={noteDraft[l.key] ?? ""}
              timesDraft={timesDraft[l.key] ?? ""}
              onTimesDraft={v => setTimesDraft(d => ({ ...d, [l.key]: v }))}
              onTimes={v => run(`times:${l.key}`, () => patchLoop(l.key, "times", v))}
              onSlotDraft={v => setSlotDraft(d => ({ ...d, [l.key]: v }))}
              onNoteDraft={v => setNoteDraft(d => ({ ...d, [l.key]: v }))}
              onToggle={() => run(`toggle:${l.key}`, () => patchLoop(l.key, "enabled", l.enabled !== true))}
              onSlot={() => run(`slot:${l.key}`, () => patchLoop(l.key, "run_at", (slotDraft[l.key] ?? "").trim()))}
              onNote={() => run(`note:${l.key}`, () => patchLoop(l.key, "note", noteDraft[l.key] ?? ""))}
              onMode={m => run(`mode:${l.key}`, () => patchLoop(l.key, "sync_mode", m))}
              onClientMode={m => run(`cmode:${l.key}`, () => patchLoop(l.key, "client_mode", m))}
              onExitClick={on => run(`exitclick:${l.key}`,
                () => patchLoop(l.key, "exit_confirm_click", on))}
              bandLo={bandDraft[`${l.key}:lo`] ?? (l.client_band_lo ?? "")}
              bandHi={bandDraft[`${l.key}:hi`] ?? (l.client_band_hi ?? "")}
              onBandDraft={(side, v) => setBandDraft(d => ({ ...d, [`${l.key}:${side}`]: v }))}
              onBandSave={() => run(`band:${l.key}`, async () => {
                const lo = (bandDraft[`${l.key}:lo`] ?? l.client_band_lo ?? "").trim();
                const hi = (bandDraft[`${l.key}:hi`] ?? l.client_band_hi ?? "").trim();
                // 一键一请求（后端 `_job_patch_guard` 就要求一次一键），但**先判方向再发**：
                // 只把"19:00 ~ 09:00"这种填反的发给后端，它会拒第二笔，留下半配的那一格更难查。
                const bad = lo && hi && lo >= hi;
                if (bad) return Promise.reject(new Error(
                  `时间带要开始早于结束（现在 ${lo} ~ ${hi}），这一笔没发出去`));
                await patchLoop(l.key, "client_band_lo", lo);
                return await patchLoop(l.key, "client_band_hi", hi);
              })}
              onDailyRestart={() => run(`dailyre:${l.key}`,
                () => patchLoop(l.key, "restart_daily_enabled", l.restart_daily_enabled !== true))}
              dailyAt={dailyAtDraft[l.key] ?? (l.restart_daily_at ?? "")}
              onDailyAtDraft={(v) => setDailyAtDraft(d => ({ ...d, [l.key]: v }))}
              onDailyAtSave={() => run(`dailyat:${l.key}`,
                () => patchLoop(l.key, "restart_daily_at",
                  (dailyAtDraft[l.key] ?? l.restart_daily_at ?? "").trim()))}
              onFreq={f => run(`freq:${l.key}`, () => patchLoop(l.key, "run_freq", f))}
              onDays={d => run(`days:${l.key}`, () => patchLoop(l.key, "weekly_days", d.join(",")))}
            />
          ))}
        </ul>
      )}

      {/* 后端这两句是纯文本：`plain()` 去掉 ** 粗体标记，不然星号会原样显示在页面上 */}
      <p className="mt-2 text-[11px] leading-4 text-muted-foreground">{plain(s?.note ?? "")}</p>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{plain(s?.explain ?? "")}</p>

      <details className="mt-2 rounded-lg border border-border/60 p-2 text-[11px] leading-relaxed">
        <summary className="cursor-pointer font-medium">「调度器」这个词在本机指五件不同的事（点开设别）</summary>
        <ul className="mt-1.5 space-y-1">
          {NAMING.map(([what, meaning]) => (
            <li key={what}><span className="font-medium">{what}</span>
              {/* 这段是前端自写的句子，但里面用了 ** 当强调：屏幕上就是裸星号。走同一个纯文本出口。 */}
              —— <span className="text-muted-foreground">{plain(meaning)}</span></li>
          ))}
        </ul>
        <p className="mt-1.5 text-muted-foreground">
          收盘链两段内部还串着几十步（获取段 10 步 ＋ 加工段若干步 —— 加工段每一步入链都会让总数变，
          所以这里那句总数以 <code>len(e1_fill_all.STAGES)</code> 现读为准，别抄屏幕上的数），
          那不是几十条定时任务 ——
          它们由 <code>daily_close</code> 这一条在 15:59 串起来跑，逐步说明在
          「任务调度」页与 <code>docs/系统配置工作指南/05-调度宿主.md</code>。
        </p>
      </details>
    </GlassCard>
  );
}

const dashCount = (v: unknown) => (typeof v === "number" ? String(v) : DASH);

function LoopLine({ l, busy, slotDraft, noteDraft, timesDraft, onSlotDraft, onNoteDraft,
  onTimesDraft, onToggle, onSlot, onNote, onMode, onFreq, onDays, onTimes, onClientMode,
  onExitClick, bandLo, bandHi, onBandDraft, onBandSave,
  onDailyRestart, dailyAt, onDailyAtDraft, onDailyAtSave }: {
    l: LoopRow; busy: string; slotDraft: string; noteDraft: string; timesDraft: string;
    onSlotDraft: (v: string) => void; onNoteDraft: (v: string) => void;
    onTimesDraft: (v: string) => void;
    onToggle: () => void; onSlot: () => void; onNote: () => void;
    onTimes: (v: string) => void;
    onMode: (m: string) => void; onFreq: (f: string) => void; onDays: (d: number[]) => void;
    onClientMode: (m: string) => void;
    onExitClick: (on: boolean) => void;
    bandLo: string; bandHi: string;
    onBandDraft: (side: "lo" | "hi", v: string) => void; onBandSave: () => void;
    onDailyRestart: () => void; dailyAt: string;
    onDailyAtDraft: (v: string) => void; onDailyAtSave: () => void;
  }) {
  const rt = l.retry;
  return (
    <li className={cn("rounded-lg border p-2.5",
      l.switchable && l.enabled === false ? "border-warning/50 bg-warning/5" : "border-border")}>
      <div className="flex flex-wrap items-center gap-2">
        {l.switchable ? (
          <label className="flex min-w-0 flex-1 items-start gap-2">
            <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary" disabled={busy !== ""}
              checked={l.enabled === true} onChange={onToggle}
              aria-label={`${l.key} 这条常驻循环`} />
            <span className="min-w-0">
              <span className="block truncate text-xs font-medium">{l.name}
                <span className="ml-1 font-mono text-[10px] text-muted-foreground">{l.key}</span></span>
              <span className="block truncate text-[11px] text-muted-foreground" title={l.loop ?? ""}>
                线程名 <code>{l.loop ?? DASH}</code>｜{plain(l.cadence ?? "") || DASH}</span>
            </span>
          </label>
        ) : (
          <span className="min-w-0 flex-1 text-xs">
            {l.name} <code className="text-[10px] text-muted-foreground">{l.key}</code>
            <span className="block text-[11px] text-muted-foreground">{plain(l.unswitchable_reason) || "这一条关不掉"}</span>
          </span>
        )}
        <span className={cn("shrink-0 rounded-md px-1.5 py-0.5 text-[11px]",
          l.alive ? "bg-primary/10 text-primary" : "bg-destructive/15 text-destructive")}>
          {l.alive ? "线程在" : "线程不在"}
        </span>
        {/* **关掉到底停不停**是逐条的事实，不是统一口径：10-08 有四条只在启动时读开关，
            面板上关掉后当前进程照跑。不显示这一格就等于让人以为已经关了。 */}
        {l.switchable && (
          <span className={cn("shrink-0 rounded-md px-1.5 py-0.5 text-[11px]",
            switchBadge(l).tone === "ok" ? "bg-primary/10 text-primary"
              : switchBadge(l).tone === "warn" ? "bg-warning/15 text-warning"
                : "bg-muted text-muted-foreground")}
            title={l.switch_effect ?? ""}>
            {switchBadge(l).label}
          </span>
        )}
      </div>

      <dl className="mt-1.5 grid gap-0.5 pl-6 text-[11px] text-muted-foreground">
        <div>最近跑过：{l.last_run_at?.trim() ? l.last_run_at : "没有记录"}
          最近成功：{l.last_ok_at?.trim() ? l.last_ok_at : "从未成功"}</div>
        {l.never_recorded && (
          <div className="text-warning">活着但至今零笔记账 —— 到点没跑、还是跑了不记账，要在这一格里现形</div>
        )}
        {typeof l.consecutive_failures === "number" && l.consecutive_failures > 0 && (
          <div className="text-destructive">连败 {l.consecutive_failures} 次{l.last_error ? `：${plain(l.last_error)}` : ""}</div>
        )}
        {l.has_slot && (
          <div>时点 {l.run_at ?? DASH}（{l.run_at_source ?? "来源后端没说"}）；
            改时点要写对 HH:MM，非法值会被拒并说明</div>
        )}
        <div>关掉会失去什么：{plain(l.off_effect) || DASH}</div>
        {l.switchable && (
          <div className={l.live_switch === false ? "text-warning" : undefined}>
            关掉之后：{plain(l.switch_effect) || "后端还没交这一格（这一版服务没装载新读数）"}
          </div>
        )}
        {l.has_freq && (
          <div>频度：{l.run_freq === "weekly" ? "每周" : "每日"}
            {l.run_freq === "weekly" && (parseWeeklyDays(l.weekly_days).days.length
              ? `（${parseWeeklyDays(l.weekly_days).days.map(d => WEEK_DAY_LABELS[d]).join("、")}）` : "")}</div>
        )}
        {l.has_freq && freqProblem(l) && <div className="text-warning">⚠ {freqProblem(l)}</div>}
        <div>这个键最后一次改动：{l.changed_at ?? "没有留痕（早于留痕机制）"}{l.changed_by ? ` 由 ${l.changed_by}` : ""}</div>
        {l.note && <div>理由：{plain(l.note)}</div>}
        {rt && (
          <div>
            {rt.available === false
              ? `阶梯读数：${rt.reason ?? "后端没给原因"}`
              : <>待补 {rt.pending?.length ?? 0} 步{rt.pending?.length ? `（${rt.pending.join("、")}）` : ""}
                · 档期 {(rt.lanes ?? []).filter(x => !x.broken).map(x =>
                    `${x.label}${x.ran_at ? "（已用）" : ""}`).join(" / ")}
                  {" · 下一档 "}{rt.next_at ?? "没有（都用完了）"}
                {rt.gave_up ? " · 这一场已放弃，等下一次收盘链" : ""}
                {!!rt.pending_soft?.length && ` · 按口径拒写不重试：${rt.pending_soft.join("、")}`}
                {rt.last_lane && ` · 最近一次跑的是 ${rt.last_lane} 档`}
              </>}
          </div>
        )}
        {(rt?.lanes ?? []).filter(x => x.broken).map(x => (
          <div key={x.label} className="text-warning">⚠ 档期里这一格不是 HH:MM：{x.label}（已跳过，其余照排）</div>
        ))}
        {l.warnings?.map(w => <div key={w} className="text-warning">⚠ {plain(w)}</div>)}
      </dl>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-6">
        {l.has_slot && (
          <>
            <input value={slotDraft || (l.run_at ?? "")} onChange={e => onSlotDraft(e.target.value)}
              aria-label={`${l.key} 的执行时点`}
              className="w-24 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
            <button type="button" disabled={busy !== "" || !slotDraft.trim() || slotDraft === l.run_at}
              onClick={onSlot}
              className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
              改时点
            </button>
          </>
        )}
        {l.times != null && (
          <label className="flex flex-1 items-center gap-1 text-[11px]">
            <span className="text-muted-foreground">重试档期</span>
            <input value={timesDraft || (l.times ?? "")} onChange={e => onTimesDraft(e.target.value)}
              aria-label={`${l.key} 的重试时点表`}
              title="逗号分隔的 HH:MM，逐格可改可增删；早于 12:00 的算次日（跨午夜的凌晨档）"
              className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
            <button type="button" disabled={busy !== "" || !timesDraft.trim() || timesDraft.trim() === l.times}
              onClick={() => onTimes(timesDraft.trim())}
              className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
              保存档期
            </button>
            {!!l.times_default && l.times !== l.times_default && (
              <button type="button" disabled={busy !== ""}
                onClick={() => { onTimesDraft(l.times_default ?? ""); onTimes(l.times_default ?? ""); }}
                className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
                退回默认
              </button>
            )}
          </label>
        )}
        {l.sync_mode_choices && (
          <label className="flex items-center gap-1 text-[11px]">
            <span className="text-muted-foreground">同步模式</span>
            <select value={l.sync_mode ?? "full"} disabled={busy !== ""}
              onChange={e => onMode(e.target.value)}
              className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]"
              title={Object.entries(l.sync_mode_choices).map(([k, v]) => `${k}：${v}`).join("\n")}>
              {Object.keys(l.sync_mode_choices).map(k => (
                <option key={k} value={k}>{k === "full" ? "全量更新（以同花顺为准）" : "增量添加（只加不删）"}</option>
              ))}
            </select>
          </label>
        )}
        {/* 通达信客户端两档（用户 10-13 点名）。只在本键真在册时才交，别的行不渲染这个框。
            短标签在这里，**长说明引后端那一份**（`CLIENT_MODE_MEANING`，行下面那段）；
            认不出的键原样显示键名 —— 悄悄套一个默认标签就是"改了也不生效的框"的另一种形态。 */}
        {l.client_mode_choices && (
          <label className="flex items-center gap-1 text-[11px]">
            <span className="text-muted-foreground">通达信客户端</span>
            <select value={l.client_mode ?? "on_demand"} disabled={busy !== ""}
              onChange={e => onClientMode(e.target.value)}
              className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]"
              title={Object.entries(l.client_mode_choices).map(([k, v]) => `${k}：${v}`).join("\n")}>
              {Object.keys(l.client_mode_choices).map(k => (
                <option key={k} value={k}>
                  {k === "on_demand" ? "需要时才运行（带外自动关闭）"
                    : k === "keep_alive" ? "保持运行（判坏就自动重启）" : k}
                </option>
              ))}
            </select>
          </label>
        )}
        {/* 那只「退出 / 重新登录 / 取消」由谁点（`tdx_watch.exit_confirm_click`，10-13）。
            这是**程序替人点按钮**，所以必须在界面上看得见、可关；措辞引后端那一份。 */}
        {l.exit_confirm_click !== undefined && l.exit_confirm_click !== null && (
          <label className="flex items-center gap-1 text-[11px]"
            title={plain(l.exit_confirm_click_help ?? "")}>
            <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={busy !== ""}
              checked={l.exit_confirm_click === true}
              onChange={() => onExitClick(l.exit_confirm_click !== true)}
              aria-label={`${l.key} 由程序点确认框上的退出`} />
            <span className="text-muted-foreground">确认框由程序点「退出」</span>
          </label>
        )}
        {/* 时间带两格 + 每日定时重启（用户 2026-10-19：「加到系统配置里面去，并且允许 9 点到 19 点
            都可以保持在线」「再加一个策略，定时自动重启通达信，默认时间是下午 15 点 38 分」）。
            **只在后端真交了这个键时才渲染**——给一条别的循环配时带框，就是又一个"改了也不生效的框"。
            长说明一律引后端那一份（`client_band_help` / `restart_daily_help`），前端不重抄口径。 */}
        {l.client_band_help && (
          <span className="flex flex-wrap items-center gap-1 text-[11px]"
            title={plain(l.client_band_help)}>
            <span className="text-muted-foreground">保持在线的时间带</span>
            <input value={bandLo} placeholder="留空＝按收盘链" aria-label={`${l.key} 时间带开始`}
              disabled={busy !== ""} onChange={e => onBandDraft("lo", e.target.value.trim())}
              className="w-24 rounded-lg border border-border bg-background px-2 py-1 text-[11px]" />
            <span className="text-muted-foreground">~</span>
            <input value={bandHi} placeholder="留空＝按收盘链" aria-label={`${l.key} 时间带结束`}
              disabled={busy !== ""} onChange={e => onBandDraft("hi", e.target.value.trim())}
              className="w-24 rounded-lg border border-border bg-background px-2 py-1 text-[11px]" />
            <button type="button" disabled={busy !== ""} onClick={onBandSave}
              className="rounded-lg border border-border px-2 py-1 text-[11px] hover:border-primary/40 hover:text-primary">
              保存时间带</button>
            <span className="text-muted-foreground">（例：09:00 ~ 19:00 含整个盘中）</span>
          </span>
        )}
        {l.restart_daily_help && (
          <span className="flex flex-wrap items-center gap-1 text-[11px]"
            title={plain(l.restart_daily_help)}>
            <label className="flex items-center gap-1">
              <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={busy !== ""}
                checked={l.restart_daily_enabled === true} onChange={onDailyRestart}
                aria-label={`${l.key} 每日定时重启通达信`} />
              <span className="text-muted-foreground">每日定时重启通达信</span>
            </label>
            <input value={dailyAt} aria-label={`${l.key} 每日定时重启时刻`} placeholder="15:38"
              disabled={busy !== "" || l.restart_daily_enabled !== true}
              onChange={e => onDailyAtDraft(e.target.value.trim())}
              className="w-20 rounded-lg border border-border bg-background px-2 py-1 text-[11px]" />
            <button type="button" disabled={busy !== "" || l.restart_daily_enabled !== true}
              onClick={onDailyAtSave}
              className="rounded-lg border border-border px-2 py-1 text-[11px] hover:border-primary/40 hover:text-primary">
              保存时刻</button>
          </span>
        )}
        {l.has_freq && (
          <label className="flex items-center gap-1 text-[11px]">
            <span className="text-muted-foreground">频度</span>
            <select value={l.run_freq ?? "daily"} disabled={busy !== ""}
              onChange={e => onFreq(e.target.value)}
              className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]"
              title="每日：到点就跑｜每周：只在勾中的周几跑（同花顺云端同步这类不想天天开客户端窗口）">
              <option value="daily">每日</option>
              <option value="weekly">每周（要勾周几）</option>
            </select>
          </label>
        )}
        {l.has_freq && l.run_freq === "weekly" && (
          <span className="flex flex-wrap items-center gap-1 text-[11px]">
            {WEEK_DAY_LABELS.map((label, day) => {
              const on = parseWeeklyDays(l.weekly_days).days.includes(day);
              return (
                <label key={label} className="flex items-center gap-0.5">
                  <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={busy !== ""}
                    checked={on} aria-label={`${l.key} 在${label}执行`}
                    onChange={() => onDays(
                      (on ? parseWeeklyDays(l.weekly_days).days.filter(d => d !== day)
                        : [...parseWeeklyDays(l.weekly_days).days, day]).sort((a, b) => a - b))} />
                  <span className={on ? "text-foreground" : "text-muted-foreground"}>{label}</span>
                </label>
              );
            })}
          </span>
        )}
        <input value={noteDraft} onChange={e => onNoteDraft(e.target.value)}
          placeholder={l.note ?? "写一句为什么（可留空）"} aria-label={`${l.key} 的停用理由`}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-[11px]" />
        <button type="button" disabled={busy !== ""} onClick={onNote}
          className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
          记理由
        </button>
      </div>
      {l.sync_mode_choices && (
        <p className="mt-1 pl-6 text-[11px] leading-4 text-muted-foreground">
          {plain(l.sync_mode_choices[l.sync_mode ?? "full"] ?? "当前模式后端没给说明")}
        </p>
      )}
      {l.client_mode_choices && (
        <p className="mt-1 pl-6 text-[11px] leading-4 text-muted-foreground">
          {plain(l.client_mode_choices[l.client_mode ?? "on_demand"] ?? "当前档后端没给说明")}
        </p>
      )}
      {l.exit_confirm_click !== undefined && l.exit_confirm_click !== null && (
        <p className="mt-1 pl-6 text-[11px] leading-4 text-muted-foreground">
          {plain(l.exit_confirm_click_help ?? "后端没给这一格的说明")}
        </p>
      )}
    </li>
  );
}
