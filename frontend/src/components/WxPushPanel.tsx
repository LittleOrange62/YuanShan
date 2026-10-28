import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import {
  blockedList, checkTone, COMBO_HINT, emptyTemplateForm, guardValueLabel, historyLine,
  historyTone, loadWxPush, needsCombo, patchWxPush, preflightLine, sendResultLine, sendWxPush,
  stepFormFrom, stepMissing, stepPayload, stepSummary, stepTone, templateFormFrom, templateMissing,
  templatePayload, usesPoint,
  type StepForm, type TemplateForm, type Tone, type WxGuard, type WxState, type WxStep,
} from "@/lib/wxpush";

/**
 * 系统配置 →「个人微信推送」（用户 2026-10-20 圈的流程与配置面）。
 *
 * 这一条通道与隔壁「消息推送」那四个渠道的根本差别，界面上必须一直说清：
 * 那些是网络请求，这一条是**在这台机器的桌面上替你按键盘**。所以本面板有三件事是别的面板没有的：
 *
 * 1. **发送前的检查（只看不按）**每次读数都现算并摊开逐条列出 —— 这一条通道最常见的失败
 *    不是"发错了"，而是"这台机器现在根本没有桌面，于是什么都没发生"。
 *    实测：远程桌面最小化/断开后，整屏取色全黑、`GetForegroundWindow()` 回 0、
 *    按键被系统直接拒绝。那种状态下点「真发」只会得到一个静默的失败。
 * 2. **主按钮不静默禁用**。「真发这一条」永远点得动；被拦住时在原地列出「还差哪几样」，
 *    并且**不发那次注定被拒的请求**。
 * 3. **「按完了」不等于「送达」**。这一侧读不回任何回执（客户端不暴露控件树），
 *    所以结果只说"按完了 N 个动作"，并要用户自己去手机上看那一条。
 *
 * 步骤表、每一行的可选做法、闸的上下限、内容档的来源种类 —— 全部来自后端读数，
 * 这里**一份都不抄**（抄的那份迟早与执行体不同，界面上就会出现"能改但没人读"的格子）。
 */

const TONE: Record<Tone, string> = {
  ok: "bg-primary/10 text-primary",
  warn: "bg-warning/15 text-warning",
  bad: "bg-destructive/15 text-destructive",
};
const DASH = "—";

function Badge({ tone, children, title }: { tone: Tone; children: React.ReactNode; title?: string }) {
  return <span title={title} className={cn("shrink-0 rounded-md px-1.5 py-0.5 text-[11px]", TONE[tone])}>
    {children}</span>;
}

/** 「看着就能改」那一格的样子：实心边框 + 聚焦环 + 一句「改完按哪颗」。
 *  10-19 用户对着一个没有边框的输入框说过「这里没办法让我进行赋分啊」。 */
const CELL = "rounded-md border border-border bg-background px-2 py-1 text-[12px] "
  + "focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30";
const BTN = "rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground "
  + "hover:border-primary/40 hover:text-primary disabled:opacity-60";
const BTN_MAIN = "rounded-lg border border-primary/60 bg-primary/10 px-2.5 py-1 text-[11px] "
  + "text-primary hover:bg-primary/15 disabled:opacity-60";

function StepRow({ step, busy, onSaved, onNotice }: {
  step: WxStep; busy: string;
  onSaved: (data: WxState, reason: string) => void; onNotice: (t: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<StepForm>(() => stepFormFrom(step));
  const tone = stepTone(step);
  const dirty = !!stepPayload(step, form);
  const miss = stepMissing(step, form);

  const save = async () => {
    if (miss.length) { onNotice(`「${step.label}」还差：${miss.join('；')}`); return; }
    const payload = stepPayload(step, form);
    if (!payload) { onNotice(`「${step.label}」没有改动，不必保存`); return; }
    try {
      const res = await patchWxPush("step", payload);
      onSaved(res.data, "");
      onNotice(`${res.reason} 保存不等于下一次就按它 —— 想确认改完到底按哪些键，`
        + "在最下面「先看一遍」里重跑一次，那边逐条列出动作序列。");
      setForm(stepFormFrom(nextOf(res.data, step.key)));
      setOpen(false);
    } catch (e) { onNotice((e as Error).message); }
  };

  return (
    <li className={cn("rounded-lg border p-2", tone === "bad" ? "border-destructive/50" : "border-border")}>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-[12px]">
          <input type="checkbox" className="h-4 w-4 accent-primary" disabled={busy !== ""}
            checked={step.enabled} aria-label={`启用步骤 ${step.label}`}
            onChange={async () => {
              try {
                const res = await patchWxPush("step", { key: step.key, enabled: !step.enabled });
                onSaved(res.data, "");
              } catch (e) { onNotice((e as Error).message); }
            }} />
          <span className="font-medium">{step.label}</span>
        </label>
        <Badge tone={tone}>{step.enabled ? (step.mode === "skip" ? "跳过" : "开") : "关"}</Badge>
        <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground"
          title={stepSummary(step)}>{stepSummary(step)}</span>
        {step.risky && <Badge tone="warn">会动你桌上的东西</Badge>}
        {!step.is_default && <Badge tone="ok">已改过</Badge>}
        <button type="button" className={BTN} onClick={() => { setForm(stepFormFrom(step)); setOpen(!open); }}>
          {open ? "收起" : "改这一格"}</button>
      </div>
      {step.invalid_reason && (
        <p className="mt-1 text-[11px] text-destructive">这一格现在不合法：{step.invalid_reason}</p>
      )}
      {step.hint && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">这一步的说明</summary>
          <p className="mt-0.5 text-[11px] text-muted-foreground">{plain(step.hint)}</p>
        </details>
      )}
      {open && (
        <div className="mt-2 space-y-2 rounded-lg border border-border bg-muted/20 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-muted-foreground">做法</span>
            {(step.modes || []).map((m) => (
              <label key={m.value} className="flex items-center gap-1 text-[11px]">
                <input type="radio" name={`mode-${step.key}`} className="h-3.5 w-3.5 accent-primary"
                  checked={form.mode === m.value}
                  onChange={() => setForm({ ...form, mode: m.value })} />
                {m.label}</label>
            ))}
            {!(step.modes || []).length && (
              <span className="text-[11px]">这一格没有可换的做法（按固定按键）</span>)}
          </div>
          {(needsCombo({ ...step, mode: form.mode, enabled: form.enabled })) && (
            <label className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] text-muted-foreground">按哪个组合键</span>
              <input className={cn(CELL, "w-40")} value={form.value} aria-label={`组合键 ${step.label}`}
                onChange={(e) => setForm({ ...form, value: e.target.value })} />
              <Badge tone="ok">可改</Badge>
              <span className="text-[11px] text-muted-foreground">{COMBO_HINT}</span>
            </label>
          )}
          {usesPoint({ ...step, mode: form.mode }) && (
            <label className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] text-muted-foreground">点哪里（相对微信主窗口左上角的偏移）</span>
              <input type="number" className={cn(CELL, "w-20")} value={form.pointX} aria-label={`X 偏移 ${step.label}`}
                onChange={(e) => setForm({ ...form, pointX: Number(e.target.value) })} />
              <input type="number" className={cn(CELL, "w-20")} value={form.pointY} aria-label={`Y 偏移 ${step.label}`}
                onChange={(e) => setForm({ ...form, pointY: Number(e.target.value) })} />
              <Badge tone="ok">可改</Badge>
              <span className="text-[11px] text-muted-foreground">
                这个客户端读不到控件位置，坐标是量出来的；窗口挪动后要重新量
              </span>
            </label>
          )}
          <label className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-muted-foreground">这一步之后等多久（毫秒）</span>
            <input type="number" className={cn(CELL, "w-24")} value={form.waitMs} aria-label={`等待毫秒 ${step.label}`}
              onChange={(e) => setForm({ ...form, waitMs: Number(e.target.value) })} />
            <span className="text-[11px] text-muted-foreground">出厂 {step.wait_default}；
              搜索/结果列表出得慢就加大这一格，别压小</span>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={BTN_MAIN} disabled={!dirty || busy !== ""} onClick={() => void save()}>
              保存这一格</button>
            {!dirty && <span className="text-[11px] text-muted-foreground">现在改的和存着的一样，不必保存</span>}
            {dirty && !!miss.length && (
              <span className="text-[11px] text-destructive">还差：{miss.join('；')}</span>)}
          </div>
        </div>
      )}
    </li>
  );
}

function nextOf(data: WxState, key: string): WxStep {
  return (data.steps || []).find((x) => x.key === key) || ({ ...({} as WxStep), key });
}

function GuardRow({ g, busy, onSaved, onNotice }: {
  g: WxGuard; busy: string; onSaved: (d: WxState) => void; onNotice: (t: string) => void;
}) {
  const [num, setNum] = useState(String(g.value ?? 0));
  useEffect(() => { setNum(String(g.value ?? 0)); }, [g.value]);
  const commit = async (value: boolean | number) => {
    try {
      const res = await patchWxPush("guard", { key: g.key, value });
      onSaved(res.data);
      onNotice(`${res.reason}（下一次发送起按它判，不用重启服务）`);
    } catch (e) { onNotice((e as Error).message); }
  };
  return (
    <li className="rounded-lg border border-border p-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] font-medium">{g.label}</span>
        {g.type === "bool" ? (
          <label className="flex items-center gap-1 text-[11px]">
            <input type="checkbox" className="h-4 w-4 accent-primary" disabled={busy !== ""}
              checked={!!g.value} aria-label={g.label}
              onChange={() => void commit(!g.value)} />
            {g.value ? "开着" : "关着"}</label>
        ) : (
          <label className="flex items-center gap-1">
            <input type="number" className={cn(CELL, "w-24")} value={num} aria-label={g.label}
              min={g.min ?? undefined} max={g.max ?? undefined}
              onChange={(e) => setNum(e.target.value)} />
            <span className="text-[11px] text-muted-foreground">{g.unit || DASH}</span>
            <Badge tone="ok">可改</Badge>
            <button type="button" className={BTN} disabled={Number(num) === g.value || busy !== ""}
              onClick={() => void commit(Number(num))}>保存</button>
          </label>
        )}
        <Badge tone={g.is_default ? "warn" : "ok"}>{g.is_default ? "出厂值" : "已改过"}</Badge>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-[11px] text-muted-foreground">
          现在：{guardValueLabel(g)} —— 这一项在判什么</summary>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{plain(g.note)}</p>
      </details>
    </li>
  );
}

export function WxPushPanel() {
  const [s, setS] = useState<WxState | null>(null);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [tplForm, setTplForm] = useState<TemplateForm | null>(null);
  const [tplOpen, setTplOpen] = useState("");
  const [to, setTo] = useState("");
  const [contentMode, setContentMode] = useState<"template" | "text">("template");
  const [tplKey, setTplKey] = useState("");
  const [freeText, setFreeText] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [blocked, setBlocked] = useState<string[]>([]);

  const reload = useCallback(async () => {
    try { setS(await loadWxPush()); setErr(""); }
    catch (e) { setErr(`读不到微信推送配置：${(e as Error).message}`); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { if (!to && s?.default_to) setTo(s.default_to); }, [s, to]);
  useEffect(() => {
    if (!tplKey && s && s.templates.length) setTplKey(s.templates[0].key);
  }, [s, tplKey]);

  const run = useCallback(async (tag: string, fn: () => Promise<{ data: WxState; reason: string }>) => {
    setBusy(tag); setErr(""); setNotice("");
    try {
      const res = await fn();
      setS(res.data); setNotice(res.reason);
      return true;
    } catch (e) { setErr((e as Error).message); return false; }
    finally { setBusy(""); }
  }, []);

  const submitTemplate = async () => {
    const f = tplForm;
    if (!f) return;
    const miss = templateMissing(f);
    if (miss.length) { setNotice(`这条内容档还差：${miss.join('；')}`); return; }
    const ok = await run(`tpl:${f.key}`, () => patchWxPush("template", templatePayload(f)));
    if (ok) { setTplForm(null); setTplOpen(""); }
  };

  const sendPayload = () => ({
    to, ...(contentMode === "template" ? { template: tplKey } : { text: freeText }),
  });

  /** 「先看一遍」：dry_run，后端一个键都不按。被拦住也照跑 —— 它正是用来看"为什么发不出去"的。 */
  const dryRun = async () => {
    setBusy("dry"); setErr(""); setBlocked([]); setResult(null);
    try {
      const res = await sendWxPush(sendPayload(), true);
      setResult(res.data);
      setNotice(res.reason);
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(""); }
  };

  /** 「真发这一条」：主按钮**不静默禁用**。预检没过时当场列出还差哪几样，并且不发那次注定被拒的请求。 */
  const realSend = async () => {
    setErr(""); setNotice(""); setResult(null);
    if (!s?.preflight?.ok) { setBlocked(blockedList(s?.preflight)); return; }
    if (!to.trim()) { setNotice("没填收件人：这一条要打进谁的会话，必须指名道姓"); return; }
    if (contentMode === "text" && !freeText.trim()) { setNotice("选了「直接写文字」却没写文字"); return; }
    if (contentMode === "template" && !tplKey) { setNotice("还没选内容档"); return; }
    setBlocked([]); setBusy("send");
    try {
      const res = await sendWxPush(sendPayload(), false);
      setResult(res.data);
      setNotice(res.reason);
      setS(await loadWxPush());
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(""); }
  };

  const pre = s?.preflight || null;
  const tone: Tone = !s ? "warn" : pre?.ok ? "ok" : "bad";

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">个人微信推送（在这台机器的桌面上按键盘）</h2>
        <Badge tone={s?.enabled ? "ok" : "warn"}>{s?.enabled ? "总开关开" : "总开关关"}</Badge>
        <Badge tone={tone}>{pre?.ok ? "发送前的检查全过" : preflightLine(pre)}</Badge>
        {s && (
          <span className="text-[11px] text-muted-foreground">
            流程 {s.steps_enabled ?? s.steps.length}/{s.steps.length} 步开着｜
            内容档 {s.counts?.templates_enabled ?? 0}/{s.templates.length} 条｜
            默认收件人 {s.default_to || "（空）"}
          </span>
        )}
        <label className="flex items-center gap-1 text-[11px]">
          <input type="checkbox" className="h-4 w-4 accent-primary" disabled={busy !== ""}
            checked={!!s?.enabled} aria-label="微信推送总开关"
            onChange={() => void run("toggle", () => patchWxPush("enabled", !s?.enabled))} />
          总开关
        </label>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className={cn(BTN, "ml-auto")}>重新读取</button>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {notice && <p className="mt-1 text-[11px] text-warning">{notice}</p>}
      {s?.warning && <p className="mt-1 text-[11px] text-warning">{plain(s.warning)}</p>}
      {s?.invalid_reason && (
        <p className="mt-1 text-[11px] text-destructive">整份配置读不通：{s.invalid_reason}</p>)}
      {s && (s.steps_dropped || []).length > 0 && (
        <p className="mt-1 text-[11px] text-warning">
          配置里有后端读不懂的步骤键：{(s.steps_dropped || []).join('、')}（已忽略，
          要真的多一步得先在后端执行体里加上）
        </p>)}

      {/* ------------------------------------------------ 发送前的检查（只看不按） */}
      <h3 className="mt-3 text-sm font-semibold text-muted-foreground">
        发送前的检查（只读，打开这一屏就现算一遍，不会为了试探去按键）</h3>
      <ul className="mt-1 space-y-1">
        {(pre?.checks || []).map((c) => (
          <li key={c.key} className="flex flex-wrap items-start gap-2 text-[11px]">
            <Badge tone={checkTone(c)}>{c.ok ? "过" : (c.required ? "拦住" : "提示")}</Badge>
            <span className="font-medium">{c.label}</span>
            <span className="min-w-0 flex-1 text-muted-foreground">{c.detail}</span>
          </li>
        ))}
        {!pre && <li className="text-[11px] text-muted-foreground">还没读回来</li>}
      </ul>
      <p className="mt-1 text-[11px] text-muted-foreground">
        这一条通道只在<b>有人连着这台机器的桌面</b>时可用：远程桌面一最小化或断开，
        会话既没有渲染表面也没有输入桌面，系统会直接拒绝注入按键。
        无人值守要推手机，请用它隔壁那「消息推送」的企微/钉钉/飞书/邮件。
      </p>

      {/* ------------------------------------------------ 流程 */}
      <h3 className="mt-3 text-sm font-semibold text-muted-foreground">
        发送流程（每一步都能改做法、改按键、改等待）</h3>
      <ul className="mt-1 space-y-1.5">
        {(s?.steps || []).map((st) => (
          <StepRow key={st.key} step={st} busy={busy}
            onSaved={(d) => setS(d)} onNotice={(t) => setNotice(t)} />
        ))}
        {!s && <li className="text-[11px] text-muted-foreground">正在读…</li>}
      </ul>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <button type="button" className={BTN} disabled={busy !== ""}
          onClick={() => void run("reset", () => patchWxPush("reset_steps", true))}>
          整套还原成出厂流程</button>
        <label className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-muted-foreground">默认收件人（填谁就搜谁）</span>
          <input className={cn(CELL, "w-48")} value={s?.default_to || ""} aria-label="默认收件人"
            onChange={(e) => setS(s ? { ...s, default_to: e.target.value } : s)} />
          <Badge tone="ok">可改</Badge>
          <button type="button" className={BTN} disabled={busy !== "" || !s}
            onClick={() => void run("to", () => patchWxPush("default_to", s?.default_to || ""))}>
            保存默认收件人</button>
        </label>
      </div>

      {/* ------------------------------------------------ 预检闸 */}
      <h3 className="mt-3 text-sm font-semibold text-muted-foreground">
        这六项检查本身的开关（默认全部开着）</h3>
      <ul className="mt-1 space-y-1.5">
        {(s?.guards || []).map((g) => (
          <GuardRow key={g.key} g={g} busy={busy} onSaved={(d) => setS(d)} onNotice={(t) => setNotice(t)} />
        ))}
      </ul>

      {/* ------------------------------------------------ 内容档 */}
      <h3 className="mt-3 text-sm font-semibold text-muted-foreground">
        要推什么内容（内容档：这一条通道与其它程序共用的那一层）</h3>
      <ul className="mt-1 space-y-1.5">
        {(s?.templates || []).map((t) => {
          const open = tplOpen === t.key;
          return (
            <li key={t.key} className={cn("rounded-lg border p-2",
              t.enabled === false ? "border-warning/50 bg-warning/5" : "border-border")}>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1.5 text-[12px]">
                  <input type="checkbox" className="h-4 w-4 accent-primary" disabled={busy !== ""}
                    checked={t.enabled !== false} aria-label={`启用内容档 ${t.label}`}
                    onChange={() => void run(`tplsw:${t.key}`,
                      () => patchWxPush("template", { key: t.key, enabled: t.enabled === false }))} />
                  <span className="font-medium">{t.label}</span>
                </label>
                <code className="rounded bg-muted px-1 text-[10px]">{t.key}</code>
                <Badge tone={t.invalid_reason ? "bad" : "ok"}>{t.source_label}</Badge>
                <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground"
                  title={t.summary}>{t.summary}</span>
                <button type="button" className={BTN}
                  onClick={() => { setTplForm(templateFormFrom(t)); setTplOpen(open ? "" : t.key); }}>
                  {open ? "收起" : "看明细与编辑"}</button>
                <button type="button" className={BTN} disabled={busy !== ""}
                  onClick={() => void run(`tpldel:${t.key}`, () => patchWxPush("remove_template", t.key))}>
                  删除</button>
              </div>
              {t.invalid_reason && (
                <p className="mt-1 text-[11px] text-destructive">这一条现在发不出去：{t.invalid_reason}</p>)}
              {open && t.source === "text" && (
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-1.5 text-[11px]">
                  {t.text || "（还没写文字）"}</pre>)}
              {open && t.source === "scene" && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  勾了的内容档：{Object.keys(t.scenes || {}).join('、') || '（一个都没勾）'}
                  —— 正文与企微/飞书那四个渠道收到的是同一段字（同一个组装器）
                </p>)}
            </li>
          );
        })}
        {!s?.templates.length && (
          <li className="text-[11px] text-muted-foreground">一条内容档都还没建（下面那颗按钮建第一条）</li>)}
      </ul>

      {tplForm && (
        <div className="mt-2 space-y-2 rounded-lg border border-border bg-muted/20 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-[11px]">
              <span className="text-muted-foreground">键名</span>
              <input className={cn(CELL, "w-36")} value={tplForm.key} aria-label="内容档键名"
                disabled={tplForm.editing} onChange={(e) => setTplForm({ ...tplForm, key: e.target.value })} />
            </label>
            <label className="flex items-center gap-1 text-[11px]">
              <span className="text-muted-foreground">名字</span>
              <input className={cn(CELL, "w-40")} value={tplForm.label} aria-label="内容档名字"
                onChange={(e) => setTplForm({ ...tplForm, label: e.target.value })} />
            </label>
            <Badge tone="ok">可改</Badge>
            <span className="text-[11px] text-muted-foreground">改哪格都行，改完按最下面那颗「保存内容档」</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-muted-foreground">内容从哪来</span>
            {[["text", "固定文字"], ["scene", "用现成内容档（与企微/飞书同源）"],
              ["script", "跑一个本地脚本生成"]].map(([v, l]) => (
              <label key={v} className="flex items-center gap-1 text-[11px]">
                <input type="radio" className="h-3.5 w-3.5 accent-primary" name="tpl-source"
                  checked={tplForm.source === v} onChange={() => setTplForm({ ...tplForm, source: v })} />
                {l}</label>
            ))}
          </div>
          {tplForm.source === "text" && (
            <label className="block">
              <span className="text-[11px] text-muted-foreground">
                固定文字（可写 {'{date}'} 与 {'{now}'} 两个占位）</span>
              <textarea className={cn(CELL, "mt-1 block w-full")} rows={3} value={tplForm.text}
                aria-label="内容档文字" onChange={(e) => setTplForm({ ...tplForm, text: e.target.value })} />
            </label>
          )}
          {tplForm.source === "scene" && (
            <>
              <label className="flex items-center gap-2 text-[11px]">
                <span className="text-muted-foreground">标题前缀（可留空）</span>
                <input className={cn(CELL, "w-40")} value={tplForm.title} aria-label="标题前缀"
                  onChange={(e) => setTplForm({ ...tplForm, title: e.target.value })} />
              </label>
              <details open>
                <summary className="cursor-pointer text-[11px] text-muted-foreground">
                  勾哪几档内容（已勾 {tplForm.sceneKeys.length} 档）</summary>
                <div className="mt-1 grid gap-1 sm:grid-cols-2">
                  {(s?.scene_choices || []).map((c) => (
                    <label key={c.value} className="flex items-start gap-1.5 text-[11px]">
                      <input type="checkbox" className="mt-0.5 h-3.5 w-3.5 accent-primary"
                        checked={tplForm.sceneKeys.includes(c.value)} aria-label={`内容档 ${c.label}`}
                        onChange={() => setTplForm({
                          ...tplForm,
                          sceneKeys: tplForm.sceneKeys.includes(c.value)
                            ? tplForm.sceneKeys.filter((k) => k !== c.value)
                            : [...tplForm.sceneKeys, c.value],
                        })} />
                      <span>{c.label}</span></label>
                  ))}
                </div>
              </details>
            </>
          )}
          {tplForm.source === "script" && (
            <label className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="text-muted-foreground">脚本（仓库内相对路径，stdout 当正文）</span>
              <input className={cn(CELL, "w-64")} value={tplForm.script} aria-label="脚本路径"
                onChange={(e) => setTplForm({ ...tplForm, script: e.target.value })} />
              <span className="text-muted-foreground">最多等</span>
              <input type="number" className={cn(CELL, "w-16")} value={tplForm.timeout_seconds}
                aria-label="脚本超时秒" onChange={(e) => setTplForm({ ...tplForm, timeout_seconds: Number(e.target.value) })} />
              <span className="text-muted-foreground">秒</span>
            </label>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={BTN_MAIN} disabled={busy !== ""} onClick={() => void submitTemplate()}>
              保存内容档</button>
            <button type="button" className={BTN} onClick={() => { setTplForm(null); setTplOpen(""); }}>
              关掉这张表单</button>
            <span className="text-[11px] text-muted-foreground">
              缺项：{templateMissing(tplForm).join('；') || "都齐了"}</span>
          </div>
        </div>
      )}
      {!tplForm && (
        <button type="button" className={cn(BTN, "mt-1.5")}
          onClick={() => setTplForm(emptyTemplateForm())}>＋ 新建一条内容档</button>)}

      {/* ------------------------------------------------ 试发 */}
      <h3 className="mt-3 text-sm font-semibold text-muted-foreground">试发一条</h3>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <label className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-muted-foreground">发给谁</span>
          <input className={cn(CELL, "w-48")} value={to} aria-label="收件人"
            onChange={(e) => setTo(e.target.value)} />
          <Badge tone="ok">可改</Badge>
        </label>
        <label className="flex items-center gap-1 text-[11px]">
          <input type="radio" className="h-3.5 w-3.5 accent-primary" name="wx-content"
            checked={contentMode === "template"} onChange={() => setContentMode("template")} />
          用内容档</label>
        <select className={CELL} value={tplKey} disabled={contentMode !== "template"}
          aria-label="选一条内容档" onChange={(e) => setTplKey(e.target.value)}>
          {(s?.templates || []).map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          {!s?.templates.length && <option value="">（一条都没建）</option>}
        </select>
        <label className="flex items-center gap-1 text-[11px]">
          <input type="radio" className="h-3.5 w-3.5 accent-primary" name="wx-content"
            checked={contentMode === "text"} onChange={() => setContentMode("text")} />
          直接写文字</label>
        {contentMode === "text" && (
          <input className={cn(CELL, "min-w-0 flex-1")} value={freeText} aria-label="临时正文"
            placeholder="这里写这一次要发的字"
            onChange={(e) => setFreeText(e.target.value)} />)}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" className={BTN} disabled={busy !== ""} onClick={() => void dryRun()}>
          先看一遍（后端一个键都不按）</button>
        <button type="button" className={BTN_MAIN} disabled={busy !== ""} onClick={() => void realSend()}>
          真发这一条（会在桌面上按键盘）</button>
        <span className="text-[11px] text-muted-foreground">
          先用左边那颗：它把「将要按哪些键」逐条列出来，且不改动任何东西
        </span>
      </div>
      {blocked.length > 0 && (
        <div className="mt-1 rounded-lg border border-destructive/50 bg-destructive/5 p-2">
          <p className="text-[11px] font-medium text-destructive">
            没发 —— 这次我没把请求发出去，因为发送前的检查没过。还差：{blocked.length} 样</p>
          <ul className="mt-1 space-y-0.5">
            {blocked.map((b) => <li key={b} className="text-[11px] text-destructive">· {b}</li>)}
          </ul>
        </div>
      )}
      {result && (() => {
        const r = sendResultLine(result);
        const ops = (result.ops || []) as { op: string; label: string; combo?: string | null }[];
        return (
          <div className={cn("mt-2 rounded-lg border p-2",
            r.tone === "bad" ? "border-destructive/50" : "border-border")}>
            <p className={cn("text-[11px]", r.tone === "bad" ? "text-destructive" : "text-warning")}>{r.text}</p>
            {!!(result.chars as number) && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                正文 {String(result.chars)} 字
                {result.meta ? `｜来源 ${JSON.stringify(result.meta).slice(0, 80)}` : ""}
                {result.elapsed_ms ? `｜耗时 ${String(result.elapsed_ms)} 毫秒` : ""}
                {result.clipboard_restore ? `｜剪贴板：${JSON.stringify(result.clipboard_restore)}` : ""}
              </p>)}
            {!!ops.length && (
              <details className="mt-1" open={r.tone === "ok"}>
                <summary className="cursor-pointer text-[11px] text-muted-foreground">
                  动作序列（{ops.length} 条）</summary>
                <ol className="mt-1 space-y-0.5">
                  {ops.map((o, i) => (
                    <li key={i} className="text-[11px] text-muted-foreground">
                      {i + 1}. <span className="opacity-70">{o.op}</span> {o.label}</li>))}
                </ol>
              </details>)}
            {Array.isArray(result.ops_done) && (result.ops_done as string[]).length > 0 && (
              <details className="mt-1">
                <summary className="cursor-pointer text-[11px] text-muted-foreground">
                  实际按完了哪几手（{(result.ops_done as string[]).length} 条）</summary>
                <ol className="mt-1 space-y-0.5">
                  {(result.ops_done as string[]).map((x, i) => (
                    <li key={i} className="text-[11px] text-muted-foreground">{i + 1}. {x}</li>))}
                </ol>
              </details>)}
            {!!(result.notes as string[])?.length && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                备注：{(result.notes as string[]).join('；')}</p>)}
          </div>
        );
      })()}

      {/* ------------------------------------------------ 最近留痕 */}
      <h3 className="mt-3 text-sm font-semibold text-muted-foreground">最近发送记录（成败都记）</h3>
      <ul className="mt-1 space-y-0.5">
        {(s?.history || []).slice(0, 12).map((h, i) => (
          <li key={i} className="flex flex-wrap items-start gap-2 text-[11px]">
            <Badge tone={historyTone(h)}>{h.status || DASH}</Badge>
            <span className="min-w-0 flex-1 text-muted-foreground">{historyLine(h)}</span>
          </li>
        ))}
        {!s?.history?.length && (
          <li className="text-[11px] text-muted-foreground">还没有过一次发送</li>)}
      </ul>

      {s && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            这条通道是什么、为什么不敢做得更稳、给别的程序怎么调</summary>
          <div className="mt-1 space-y-1 text-[11px] text-muted-foreground">
            <p>{plain(s.note || "")}</p>
            <p>{plain(s.explain || "")}</p>
            <p>{plain(s.api || "")}</p>
            <p>配置：<code>{s.config_path}</code>｜留痕：<code>{s.history_path}</code>
              {s.changed_at ? `｜最后一次改动：${s.changed_at}（${s.changed_by || DASH}）` : ""}
              {s.generated_at ? `｜本次读数：${s.generated_at}` : ""}</p>
          </div>
        </details>
      )}
    </GlassCard>
  );
}
