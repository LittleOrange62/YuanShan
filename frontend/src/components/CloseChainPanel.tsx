import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { TdxTrail } from "@/components/TdxTrail";
import { jobCardClass, jobLabelClass } from "@/lib/colors";
import { loadChain, loadCompleteness, patchChain, patchCompleteness,
  type ChainStage, type ChainState, type Completeness } from "@/lib/closeChain";
import { retryVerdict } from "@/lib/chainRetry";
import { cn } from "@/lib/utils";
import { Save, ShieldAlert } from "lucide-react";
import { plain } from "@/lib/agent";

const DASH = "—";

/**
 * 系统配置 →「每日收盘链（15:59）」。这是"今天到底会不会自动采/算"的唯一可见处。
 *
 * 为什么单独立一格：`enabled=false` 是会**一直留着**的持久状态（默认值救不回来），
 * 而任务调度页上它只剩一个小灰标签「已停用」—— 那措辞分不出"用户自己不想跑"和
 * "被谁误关"。09-28 那天这两段在同一天被关了三次，全程只能靠翻文件 mtime 还原时间线。
 * 所以这一格必须同时说四件事，少一件就又回到猜：
 *  ① 开还是关，以及默认本该是什么（关着≠正常）；
 *  ② 谁、什么时候、从哪条路改的（每次写入自动盖章，不靠人记得）；
 *  ③ 人自己写的理由 `note`；
 *  ④ **今天到底会不会跑** —— 开关、真交易日历、宿主循环三样合起来才算。
 */
export function CloseChainPanel() {
  const [s, setS] = useState<ChainState | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [slotDraft, setSlotDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState<Record<string, string>>({});

  const reload = useCallback(() => loadChain()
    .then(r => { setS(r); setErr(""); setSlotDraft(r.slot ?? ""); })
    .catch(e => setErr(e instanceof Error ? e.message : "收盘链开关读不到")), []);
  useEffect(() => { void reload(); }, [reload]);

  const run = async (label: string, fn: () => Promise<{ data: ChainState; reason: string }>) => {
    setBusy(label); setNotice("");
    try {
      const r = await fn();
      setS(r.data); setNotice(r.reason);
    } catch (e) {
      // 拒绝要原样带出原因（非法时点 / 未知作业 / 一次改多件），不许折叠成"失败了"
      setNotice(e instanceof Error ? e.message : "修改失败");
    } finally { setBusy(""); }
  };

  const offStages = (s?.stages ?? []).filter(x => !x.enabled);
  const tone = !s ? "quiet" : s.today_will_run ? "normal" : offStages.length ? "bad" : "warn";

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">每日收盘链（15:59）</h2>
        <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]", jobLabelClass(tone))}>
          {s ? (s.today_will_run ? "今天会自动跑" : "今天不会自动跑") : DASH}
        </span>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取
        </button>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {notice && <p className="mt-2 text-[11px] text-warning">{notice}</p>}

      {s && (
        <>
          {/* "今天会不会跑"的三个条件各说各的：混成一句"未就绪"就没人知道去修哪一个 */}
          <ul className="mt-2 space-y-0.5 text-[11px] leading-4 text-muted-foreground">
            <li>· 交易日判定：{s.is_trade_day === true ? "今天是交易日"
              : s.is_trade_day === false ? "日历说今天不开市（不跑是正常的）"
                : "判不了 —— 日历读不到或没覆盖今天，宿主不会猜着跑"}</li>
            <li>· 宿主循环：{s.host_loop_alive ? "daily-close 在本进程里活着"
              : <span className="text-destructive">daily-close 不在这个进程里（改完宿主没重启，或进程是旧的）</span>}
              {s.host_loop_missing.length > 0 && `｜还缺 ${s.host_loop_missing.join("、")}`}</li>
            <li>· 时点 <code>{s.slot ?? DASH}</code>（{s.slot_source ?? DASH}）；到点后 3 小时内会补跑</li>
          </ul>

          <div className="mt-3 space-y-2">
            {s.stages.map(st => (
              <StageRow key={st.key} st={st} busy={busy}
                noteDraft={noteDraft[st.key] ?? ""}
                onNoteDraft={v => setNoteDraft(n => ({ ...n, [st.key]: v }))}
                onToggle={() => run(`toggle:${st.key}`, () => patchChain(st.key, "enabled", !st.enabled))}
                onNote={() => run(`note:${st.key}`, () => patchChain(st.key, "note", noteDraft[st.key] ?? ""))}
              />
            ))}
          </div>

          <div className="mt-3 flex items-center gap-1.5">
            <label className="text-[11px] text-muted-foreground" htmlFor="chain-slot">获取段时点</label>
            <input id="chain-slot" value={slotDraft} onChange={e => setSlotDraft(e.target.value)}
              placeholder="15:59" className="w-24 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
            <button type="button" disabled={busy !== "" || slotDraft === s.slot}
              onClick={() => run("slot", () => patchChain("daily_close", "run_at", slotDraft))}
              className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
              改时点
            </button>
            <span className="text-[11px] text-muted-foreground">下一个心跳就生效，不用重启</span>
          </div>

          <LaunchKnob s={s} busy={busy} run={run} />
          <CompletenessCard />

          <TdxWatch s={s} />

          <p className="mt-2 flex items-start gap-1 text-[11px] leading-4 text-muted-foreground">
            <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" />
            关掉之后<strong>到点也不会跑，而且不会自己恢复</strong> —— 会一直关到有人再打开。
            当日不采就是永久少这一天（快讯、自选成员快照、个股资金流这类没有日期参数的源补不回来）。
          </p>
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
            {s.explain ?? ""}
          </p>
        </>
      )}
      {!s && !err && <p className="mt-2 text-[11px] text-muted-foreground">正在读取收盘链状态…</p>}
    </GlassCard>
  );
}

/**
 * 通达信看护那一格：现在在不在跑、有没有登录、看护循环活着没，**外加待人工清单**。
 *
 * 清单本体住在 `./TdxTrail.tsx`（系统配置页与通达信控制页共用一份，两处各写一份
 * 迟早分叉）。这里只补看护状态那几行 —— 判据与文字都来自后端 `jobsboard.tdx_watch`，
 * 页面不自己枚举进程。
 */
function TdxWatch({ s }: { s: ChainState }) {
  const t = s.tdx ?? null;
  const trail = s.tdx_trail ?? null;
  return (
    <div className="mt-3 rounded-lg border border-border p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold">通达信客户端与自动动作</h3>
        <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]",
          !t ? jobLabelClass("quiet") : (trail?.pending?.length ?? 0) ? jobLabelClass("warn") : jobLabelClass("normal"))}>
          {!t ? "后端没给这一格" : (trail?.pending?.length ?? 0) ? "有动作要人补" : "自动动作正常"}
        </span>
      </div>
      {t && (
        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
          {t.running === true ? "客户端在跑" : t.running === false ? "客户端没在跑" : "探测没记录"}
          {t.login_box === true ? "，但停在登录框上" : ""}；
          同步子进程 {t.sync?.trim() ? t.sync : "无"}；
          看护循环 {t.loop_alive ? "活着" : <span className="text-destructive">不在这个进程里</span>}
          {t.last_run_at ? `（最后一次探测 ${t.last_run_at}）` : ""}。
          {t.reason ? ` ${plain(t.reason)}` : ""}
        </p>
      )}
      {!t && !trail && (
        <p className="mt-1 text-[11px] leading-4 text-warning">
          后端没有回 tdx / tdx_trail 两格 —— 这是旧后端，不是"一切正常"，重启 8910 才会带上这一层。
        </p>
      )}
      {trail && <TdxTrail count={trail.count} pending={trail.pending} path={trail.path} />}
    </div>
  );
}

function StageRow({ st, busy, noteDraft, onNoteDraft, onNote, onToggle }: {
  st: ChainStage; busy: string; noteDraft: string;
  onNoteDraft: (v: string) => void; onNote: () => void; onToggle: () => void;
}) {
  return (
    <div className={cn("rounded-lg border p-2.5", st.enabled ? "border-border" : jobCardClass("bad"))}>
      <label className="flex items-start gap-2">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary" checked={st.enabled}
          disabled={busy !== ""} onChange={onToggle} />
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-medium">
            {st.key}　<span className="font-normal text-muted-foreground">
              {st.enabled ? "会自动跑" : "已停用（不是默认状态）"}</span>
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-muted-foreground" title={st.name}>
            {st.name}
          </span>
        </span>
      </label>
      <dl className="mt-1.5 grid gap-0.5 pl-6 text-[11px] text-muted-foreground">
        {/* "最近跑过"与"最近成功"是两个字段，空值各自说清 —— 空绝不能读成"跑过了" */}
        <div>最近跑过：{st.last_run_at?.trim() ? st.last_run_at : "没有记录（一次都没执行过）"}
          　最近成功：{st.last_ok_at?.trim() ? st.last_ok_at : "从未成功"}</div>
        {typeof st.consecutive_failures === "number" && st.consecutive_failures > 0 && (
          <div className="text-destructive">连败 {st.consecutive_failures} 次
            {st.last_error ? `：${st.last_error}` : ""}</div>
        )}
        {(() => {
          // 三种情况（还会再试 / 档用完 / 后端没说）由 `chainRetry.ts` 判，页面不自己猜。
          const rv = retryVerdict(st);
          return rv && <div className={rv.tone === "bad" ? "text-destructive" : "text-warning"}>{rv.text}</div>;
        })()}
        <div>这个键最后一次改动：{st.changed_at ?? "没有留痕（早于留痕机制）"}
          {st.changed_by ? ` 由 ${st.changed_by}` : ""}</div>
        {st.note && <div>理由：{st.note}</div>}
        {!st.enabled && !st.note && (
          <div className="text-warning">没写理由 —— 关掉时请写一句"谁/为什么"，否则与被人误关分不开</div>
        )}
        {st.warnings.map(w => <div key={w} className="text-warning">⚠ {w}</div>)}
      </dl>
      <div className="mt-1.5 flex items-center gap-1.5 pl-6">
        <input value={noteDraft} onChange={e => onNoteDraft(e.target.value)}
          placeholder={st.note ?? "写一句为什么（可留空）"} aria-label={`${st.key} 的停用理由`}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-[11px]" />
        <button type="button" disabled={busy !== ""} onClick={onNote}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
          <Save className="h-3 w-3" />记理由
        </button>
      </div>
    </div>
  );
}

/** 收盘前「提前拉起并登录通达信」+「只在交易日执行」（用户 2026-10-07 要的两格）。
 *
 *  提前拉起为什么值得一个配置项：客户端登录之后自己还要同步 7~10 分钟数据，
 *  到点（15:59）才拉起的话，链里问 TQ 那一步正好撞上"它正在写 vipdoc"，
 *  而那正是历史上天天把链跑死的时刻。默认 0 = 不提前，行为与今天完全一致。
 *  「只在交易日执行」关掉的真实含义是"每天都发一次"，**不是"每天都会有数"** ——
 *  段脚本自己按日历拒写。留这一档是因为日历只登到 2026-12-31，跨年那几天它判不了。 */
function LaunchKnob({ s, busy, run }: {
  s: ChainState; busy: string;
  run: (label: string, fn: () => Promise<{ data: ChainState; reason: string }>) => Promise<void>;
}) {
  const dc = s.stages.find(x => x.key === "daily_close");
  const [leadDraft, setLeadDraft] = useState("");
  const lead = dc?.launch_lead_min ?? 0;
  const onlyTrade = dc?.trading_days_only ?? true;
  if (!dc) return null;
  return (
    <div className="mt-3 rounded-lg border border-border/60 p-2.5">
      <div className="text-[11px] font-medium">通达信自动拉起与执行日</div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px]">
        <label htmlFor="launch-lead">提前拉起（分钟）</label>
        <input id="launch-lead" type="number" min={0} max={240} value={leadDraft || String(lead)}
          onChange={e => setLeadDraft(e.target.value)}
          className="w-20 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
        <button type="button" disabled={busy !== "" || !leadDraft || Number(leadDraft) === lead}
          onClick={() => run("launch-lead", () => patchChain("daily_close", "launch_lead_min", Number(leadDraft)))}
          className="rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
          改提前量
        </button>
        <span className="text-muted-foreground">
          {lead > 0 ? <>实际拉起时刻 <code>{dc.launch_at ?? DASH}</code>（时点 {s.slot ?? DASH} 前 {lead} 分钟），一天只发一次</>
            : `0 = 不提前，跟着链在 ${s.slot ?? "15:59"} 那一次里拉起`}
        </span>
      </div>
      <label className="mt-1.5 flex items-start gap-2 text-[11px]">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary" checked={onlyTrade}
          disabled={busy !== ""}
          onChange={() => run("only-trade", () => patchChain("daily_close", "trading_days_only", !onlyTrade))} />
        <span>
          只在交易日执行<span className="text-muted-foreground">
            （判据是 <code>reference_data.trade_calendar</code>，不是"看周六日"，三层：
            库里的交易日历 → 日历没排到那天时按星期几判周末休市 →
            工作日再拿实时行情的时间戳自证（交易所不开市那天，时间戳就停在上一个交易日），
            所以日历跨年之后程序自己判得出来，不用人年底去试。
            日历现在登到 {s.calendar_through ?? "读不到"}
            {typeof s.days_left === "number" ? `（还剩 ${s.days_left} 天）` : ""}
            {s.running_out ? "，余量已不足两个月 —— 次年的排班一发布，明天就会自己延长" : ""}
            ；三层都答不上来的日子才会记"判不了"并停下，或者关掉这一档让它照跑，
            段脚本自己会把非交易日拒写）</span>
        </span>
      </label>
      <label className="mt-1.5 flex items-start gap-2 text-[11px]">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary"
          checked={dc.close_client_after === true} disabled={busy !== ""}
          onChange={() => run("close-client", () =>
            patchChain("daily_close", "close_client_after", dc.close_client_after !== true))} />
        <span>
          链收工后自动关闭通达信客户端<span className="text-muted-foreground">
            （默认{dc.close_client_default === true ? "开" : "关"}。保持登录是历史决定：
            TQ 直取要它登录着、次日也省一次重登，所以不默认关。
            打开后也只发一次「点 X」：TPyth 还在同步、或 vipdoc 还在往前写就不动；
            冒出的决定框绝不猜按钮，收不掉会写进待人工清单，不强杀主程序）</span>
        </span>
      </label>
    </div>
  );
}

/** 「条数完整性」：判"这一场的数全不全"的那两个偏离值（条），用户 2026-10-07 要求可配置。 */
function CompletenessCard() {
  const [c, setC] = useState<Completeness | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [dev, setDev] = useState("");
  const [devMin, setDevMin] = useState("");

  const reload = useCallback(() => loadCompleteness()
    .then(r => { setC(r); setErr(""); setDev(String(r.deviation ?? "")); setDevMin(String(r.deviation_minute ?? "")); })
    .catch(e => setErr(e instanceof Error ? e.message : "完整性读数取不到")), []);
  useEffect(() => { void reload(); }, [reload]);

  const save = async (what: "deviation" | "deviation_minute", value: number) => {
    setBusy(what); setNotice("");
    try {
      const r = await patchCompleteness(what, value);
      setC(r.data); setNotice(r.reason);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "修改失败");
    } finally { setBusy(""); }
  };

  return (
    <div className="mt-3 rounded-lg border border-border/60 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium">条数完整性（判"这一场全不全"）</span>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取
        </button>
      </div>
      {err && <p className="mt-1 text-[11px] text-destructive">{err}</p>}
      {notice && <p className="mt-1 text-[11px] text-warning">{notice}</p>}
      {!c && !err && <p className="mt-1 text-[11px] text-muted-foreground">正在读取判据…</p>}
      {c && (
        <>
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{c.rule}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
            {( [["deviation", "日线 / 板块 / 指数类", dev, setDev, c.deviation, c.deviation_from],
                ["deviation_minute", "分钟线类", devMin, setDevMin, c.deviation_minute,
                 c.deviation_minute_from]] as const
            ).map(([what, label, draft, setDraft, live, from]) => (
              <span key={what} className="flex items-center gap-1">
                <label htmlFor={what}>{label}允许少</label>
                <input id={what} type="number" min={0} value={draft}
                  onChange={e => setDraft(e.target.value)}
                  className="w-20 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
                <span className="text-muted-foreground">条</span>
                <button type="button" disabled={busy !== "" || draft === "" || Number(draft) === live}
                  onClick={() => void save(what, Number(draft))}
                  className="rounded-lg bg-primary/15 px-2 py-1 font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
                  改
                </button>
                {/* 「默认（没人改过）」与「系统配置里有人填了同一个数」是两件事：
                    只报数字的话，下一次判错时会去找一个不存在的改动。 */}
                <span className="text-muted-foreground">现值 {live ?? DASH}，取自 {from ?? "后端没说"}</span>
              </span>
            ))}
          </div>
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{plain(c.explain)}</p>
          <ul className="mt-1.5 space-y-0.5 text-[11px] text-muted-foreground">
            {(c.kinds ?? []).map(k => (
              <li key={k.kind}>
                <code>{k.kind}</code> {k.label ?? ""}：
                {k.latest
                  ? <>最新 {k.latest} 登记 {k.count?.toLocaleString("zh-CN") ?? DASH}{k.unit ?? ""}｜
                    {k.baseline_basis === "floor"
                      // 头几天没有可比场次，这时那个数是**绝对下线**（量级门），不是"昨天的条数"。
                      // 写成"基线"会让人以为昨天就只有 8,500 只，第二天跟它比就双双通过。
                      ? <>按绝对下线 {k.baseline?.toLocaleString("zh-CN") ?? DASH} 判（还没有可比场次）</>
                      : <>基线 {k.baseline?.toLocaleString("zh-CN") ?? DASH}（{k.baseline_date ?? "没有可比场次"}{k.sessions_back && k.sessions_back > 1 ? `，隔 ${k.sessions_back} 场，中间那些没登记` : "，前一天"}）</>}
                    ｜偏离 {k.deviation ?? DASH} 条（{k.deviation_from ?? DASH}）</>
                  : <>还没有任何一次登记（绝对下线 {k.floor ?? DASH}）—— 跑过收盘链或体检后会有</>}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
