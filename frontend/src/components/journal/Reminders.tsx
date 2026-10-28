import { useState } from "react";
import { CalendarClock, Loader2 } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { probe, useNonce, useProbe } from "@/lib/probe";
import { postJson } from "@/lib/jsonPost";
import { dash } from "@/lib/datastate";
import { StockLink } from "@/components/ui/StockLink";
import { StockSearch } from "@/components/ui/StockSearch";
import { cn } from "@/lib/utils";

/**
 * 关注提醒（`ys.reminder`）：一条"盯到哪天"的备忘 + 按**交易日**排的时间轴。
 *
 * 两条不能让步的地方：
 *  · 到期日一律由后端算（`due_on()`）。界面不许自己"今天 + 7 天" ——
 *    那是自然日，会把第 7 个交易日算进国庆假期里，而那一天根本没有行情。
 *  · 建之前就把到期日摆出来（点 3/5/10/20 就现调 `/reminders/due` 预览），
 *    所见即所存；日历覆盖不到时明说是"跳周末估的"，不冒充交易日。
 */

interface Row {
  id: number; kind: string; security_code?: string | null; security_name?: string | null;
  note?: string | null; reason?: string | null; base_date: string; sessions: number;
  due_date: string; calendar_covered: boolean; status: string;
  days_to_due?: number; overdue_days?: number; due_label?: string;
  done_at?: string | null; created_at?: string | null;
}
interface List {
  available: boolean; reason?: string; rows: Row[]; next: Row | null; overdue: Row[];
  open_count: number; today: string; calendar_horizon: string | null;
}
interface Due { due_date: string; calendar_covered: boolean; sessions: number; explain: string }

const KINDS: [string, string][] = [["stock", "个股"], ["theme", "板块 / 题材"], ["free", "不带标的"]];
const PRESETS = [3, 5, 7, 10, 20];

const today = () => new Date().toISOString().slice(0, 10);

export function Reminders() {
  const [nonce, refresh] = useNonce();
  const list = useProbe<List>("/reminders", nonce);
  const [kind, setKind] = useState("stock");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [base, setBase] = useState(today());
  const [sessions, setSessions] = useState(7);
  const [due, setDue] = useState<Due | null>(null);
  const [dueBusy, setDueBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const [showClosed, setShowClosed] = useState(false);

  async function preview(n: number) {
    setSessions(n); setDueBusy(true); setDue(null);
    // 预览走的是后端同一个函数，不是界面再算一遍：两处算法迟早分叉
    const r = await probe<Due>("/reminders/due", { base_date: base || today(), sessions: n });
    setDueBusy(false);
    if (r.kind === "ok") setDue(r.data);
    else setMsg(`到期日算不出来：${r.kind === "loading" ? "还在取" : "取数失败"}`);
  }

  async function create() {
    setSaving(true); setMsg("");
    const r = await postJson<List>("/reminders", {
      base_date: base || today(), sessions, kind,
      security_code: kind === "stock" ? code : "", security_name: name,
      note, reason,
    });
    setSaving(false);
    if (!r.ok) { setMsg(`没建成：${r.reason}`); return; }
    setCode(""); setName(""); setNote(""); setReason("");
    setMsg(`已建立：${r.data?.next?.due_date ?? "看下面的时间轴"}（第 ${sessions} 个交易日后）`);
    refresh();
  }

  async function setStatus(id: number, status: string) {
    setMsg("");
    const r = await postJson<List>(`/reminders/${id}/status`, { status });
    if (!r.ok) { setMsg(`这条没改成功：${r.reason}`); return; }
    refresh();
  }

  const rows = list.kind === "ok" ? list.data.rows : [];
  const open = rows.filter(r => r.status === "open");
  const closed = rows.filter(r => r.status !== "open");

  return (
    <div className="space-y-4">
      <GlassCard>
        <div className="flex flex-wrap items-center gap-2">
          <CalendarClock className="h-4 w-4 text-primary" />
          <b className="text-sm">今天到期或往后最近的一条</b>
          {list.kind === "loading" && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          {list.kind === "ok" && (list.data.next
            ? <span className="rounded-lg bg-primary/15 px-2 py-1 text-[12px] font-medium text-primary">
                {list.data.next.due_date} · {list.data.next.due_label} ·
                {list.data.next.security_name || list.data.next.security_code || "不带标的"} ·
                {dash(list.data.next.note || list.data.next.reason)}
              </span>
            : <span className="text-[12px] text-muted-foreground">现在一条开着的都没有</span>)}
          {list.kind === "ok" && !!list.data.overdue.length && (
            <span className="rounded-lg bg-warning/15 px-2 py-1 text-[12px] text-warning">
              另有 {list.data.overdue.length} 条已过期还没处理
            </span>
          )}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          到期日按<b className="text-foreground">交易日</b>算（周末与长假不计），由后端算、界面只转述。
          本机交易日历覆盖到 {dash(list.kind === "ok" ? list.data.calendar_horizon : null)}；
          超出这个日子的那条会标成"按跳周末估"，不冒充交易日。
        </p>
      </GlassCard>

      <GlassCard>
        <h3 className="text-sm font-bold">定一条提醒</h3>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {KINDS.map(([k, label]) => (
            <button key={k} type="button" onClick={() => setKind(k)} aria-pressed={kind === k}
              className={cn("rounded-lg border px-2.5 py-1 text-[12px]",
                kind === k ? "border-primary/70 bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground")}>
              {label}
            </button>
          ))}
          {kind === "stock" && (
            <>
              {/* 与「记一笔」「添加自选」同一只控件（用户 10-15：这类入口全站统一，要能按拼音首字母搜）。
                  点中一条建议顺手把名称那格填上，省得手打。 */}
              <StockSearch value={code} kinds="stock,etf" widthClass="w-44"
                ariaLabel="这条提醒盯的那一只：代码、名称或拼音首字母"
                placeholder="代码 / 名称 / 拼音首字母"
                onType={v => setCode(v.toUpperCase().slice(0, 12))}
                onPick={(c, n) => { setCode(c); setName(old => old.trim() || n); }} />
              <input value={name} onChange={e => setName(e.target.value)} placeholder="名称（可选）"
                className="w-28 rounded-lg border border-border bg-card px-2 py-1 text-[12px]" />
            </>
          )}
          {kind !== "stock" && (
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder={kind === "theme" ? "板块或题材名" : "一句话（可留空，到期时看下面那条理由）"}
              className="w-56 rounded-lg border border-border bg-card px-2 py-1 text-[12px]" />
          )}
          <label className="text-[12px] text-muted-foreground">从哪天起算
            <input type="date" value={base} onChange={e => setBase(e.target.value)}
              className="ml-1 rounded-lg border border-border bg-card px-2 py-1 text-[12px] text-foreground" />
          </label>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[12px] text-muted-foreground">几个交易日之后</span>
          {PRESETS.map(n => (
            <button key={n} type="button" onClick={() => void preview(n)} aria-pressed={sessions === n}
              className={cn("rounded-lg border px-2.5 py-1 text-[12px] tabular-nums",
                sessions === n ? "border-primary/70 bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground")}>
              {n} 日
            </button>
          ))}
          <input type="number" min={1} max={250} value={sessions}
            onChange={e => void preview(Math.max(1, Math.min(250, Number(e.target.value) || 1)))}
            className="w-16 rounded-lg border border-border bg-card px-2 py-1 text-[12px] text-right tabular-nums" />
          <span className="text-[12px] text-muted-foreground">
            {dueBusy ? "算到期日中…" : due
              ? <>到期 <b className={cn("tabular-nums", due.calendar_covered ? "text-foreground" : "text-warning")}>{due.due_date}</b>
                  （{due.explain}）{!due.calendar_covered && " ⚠ 不是交易日，是估的"}</>
              : <>点上面任一档，先看到是哪一天再建</>}
          </span>
        </div>

        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <input value={note} onChange={e => setNote(e.target.value)} placeholder="要盯的那件事，如「等它回踩二十日线再看」"
            className="rounded-lg border border-border bg-card px-2.5 py-1.5 text-[12px]" />
          <input value={reason} onChange={e => setReason(e.target.value)} placeholder="当时为什么定这个（到期时最值钱的一栏）"
            className="rounded-lg border border-border bg-card px-2.5 py-1.5 text-[12px]" />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void create()} disabled={saving || !due}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
            title={due ? "" : "先点上面任一档算出到期日"}>
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}建立提醒
          </button>
          <Caliber text="到期日 = 起算日**之后**第 N 个交易日，不含起算日当天（判据在 duanxian/review_notes.py:due_on()，界面预览与入库走同一处）。个股提醒必须 6 位代码；不想带标的选「不带标的」。" />
          {!!msg && <span className="text-[12px] text-muted-foreground">{msg}</span>}
        </div>
      </GlassCard>

      <section className="space-y-2">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-primary">
          时间轴 · 按到期日排（不是按创建时间）
        </h3>
        {list.kind === "loading" && <p className="text-[12px] text-muted-foreground">取提醒中…</p>}
        {list.kind !== "ok" && list.kind !== "loading" && (
          <GlassCard><p className="text-[12px] text-warning">
            提醒没取到：{list.kind === "offline" ? "连不到后端进程" : list.kind === "not_wired" ? "这个接口在本项目里还没实现" : "取数报错"}。
            这里空着不代表没有提醒 —— 是读不到。
          </p></GlassCard>
        )}
        {list.kind === "ok" && open.length === 0 && (
          <GlassCard><p className="py-6 text-center text-[12px] text-muted-foreground">
            现在一条开着的提醒都没有。定一条试试 —— 到期日由交易日历算，跨周末和长假。
          </p></GlassCard>
        )}
        {open.map(r => (
          <div key={r.id} className={cn("glass rounded-2xl p-3", r.days_to_due != null && r.days_to_due < 0 && "border-warning/50")}>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[12px]">
              <b className="font-mono tabular-nums">{r.due_date}</b>
              <span className={cn("rounded px-1.5 py-0.5 text-[10px]",
                (r.overdue_days ?? 0) > 0 ? "bg-warning/15 text-warning"
                  : r.days_to_due === 0 ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>
                {dash(r.due_label)}
              </span>
              <span className="text-muted-foreground">{r.sessions} 个交易日后 · 起算 {dash(r.base_date)}</span>
              {r.kind !== "stock" && <span className="text-[10px] text-muted-foreground/70">{r.kind === "theme" ? "板块/题材" : "不带标的"}</span>}
              {r.security_code
                ? <StockLink code={r.security_code} name={r.security_name || r.security_code} className="font-bold hover:text-primary" />
                : <span className="font-medium">{dash(r.security_name)}</span>}
              <span className="ml-auto flex items-center gap-1.5">
                <button type="button" onClick={() => void setStatus(r.id, "done")}
                  className="rounded border border-border px-1.5 py-0.5 text-[10px] text-primary hover:bg-primary/10">看过·办完了</button>
                <button type="button" onClick={() => void setStatus(r.id, "cancelled")}
                  className="rounded border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground">取消</button>
              </span>
            </div>
            <p className="mt-1 text-[12px]">{dash(r.note)}</p>
            {!!(r.reason ?? "").trim() && (
              <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">当时的理由：{r.reason}</p>
            )}
            {!r.calendar_covered && (
              <p className="mt-0.5 text-[10px] text-warning">这个到期日超出本机交易日历覆盖，是按"跳过周末"估的，不含节假日顺延。</p>
            )}
          </div>
        ))}

        {!!closed.length && (
          <div className="pt-1">
            <button type="button" onClick={() => setShowClosed(v => !v)} aria-expanded={showClosed}
              className="text-[11px] text-muted-foreground underline hover:text-foreground">
              已办完 / 已取消 {closed.length} 条（{showClosed ? "收起" : "展开"}）
            </button>
            {showClosed && closed.map(r => (
              <div key={r.id} className="mt-1 flex flex-wrap items-baseline gap-x-2 text-[11px] text-muted-foreground/70">
                <span className="font-mono">{r.due_date}</span>
                <span>{r.status === "done" ? "已办完" : "已取消"}</span>
                <span>{dash(r.security_name || r.security_code)}</span>
                <span>{dash(r.note)}</span>
                <button type="button" onClick={() => void setStatus(r.id, "open")}
                  className="rounded border border-border/60 px-1.5 py-0.5 text-[10px] hover:text-foreground">重新盯</button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
