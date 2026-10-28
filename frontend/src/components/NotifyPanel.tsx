import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import {
  channelDraft, channelHealth, channelPayload, emptyChannelDraft, fieldChoices, KIND_LABELS,
  limitLabel, loadNotify, sceneFieldList, supportsSign,
  urlPlaceholder, patchNotify, planHealth, previewPlan, scenesText, scheduleText,
  SCENE_LABELS, todayTone, WEEK_DAY_LABELS,
  type ChannelDraft, type NotifyChannel, type NotifyState, type SceneField,
} from "@/lib/notify";
import {
  emptyForm, formFromPlan, formToPlan, missingParts, type Form, type SceneValue,
} from "@/lib/notifyPlanForm";

const DASH = "—";
/** 「今天这一档」的轻重（`todayTone` 的三档）。文案本身来自后端，这里只管颜色。 */
const TODAY_TONE: Record<"ok" | "warn" | "bad", string> = {
  ok: "text-muted-foreground",
  warn: "text-warning",
  bad: "font-medium text-destructive",
};

/**
 * 系统配置 →「消息推送」：四个渠道 + 可多选的内容 + 到点自动发（用户 10-15 的口径）。
 *
 * 四件设计上的决定，改之前先读完：
 *
 * 1. **凭据只写不读**。后端交回来的永远是 `url_masked`，所以编辑已有渠道时 URL 框留空
 *    表示"不改这一条的凭据"（不是清空）。推荐把真串放 `.env`、这里只填变量名 ——
 *    那样截图/贴会话都不会把群机器人的 webhook 带出去，而那种泄漏只能去群里重建机器人。
 * 2. **候选项一个都不写死**。渠道种类、内容块、筛选规则、自选分组、票池、每一档的参数格
 *    全部来自 `catalog`（后端现读）。写死一份的迟早与库里不同，用户勾的就是个不存在的东西 ——
 *    那种"能填但没人读"的框本项目已经吃过好几次。
 * 3. **「发之前先看一眼」必须有**。多选内容之后光看勾是看不出对错的，而一条已经发到群里的
 *    错消息收不回来。所以每行都有「预览正文」，走 `/api/notify/preview`，它不发送。
 * 4. **每一档的参数格由描述符渲染**（`catalog.scenes[].fields`，真源在 `notifyconfig.SCENE_FIELDS`）。
 *    原先这里是一档一段手写 if（confluence 一段、screen 一段……），17 档 × 每档几格就是三十多段
 *    会与后端分叉的代码；现在加一档内容或加一格参数**只动后端**，界面自动跟上。
 *    表单的读回/写回逻辑在 `lib/notifyPlanForm.ts`（那边有 `node --test` 的真往返用例）。
 */

const toneClass = (tone: string) => (tone === "ok" ? "bg-primary/10 text-primary"
  : tone === "warn" ? "bg-warning/15 text-warning" : "bg-destructive/15 text-destructive");

function Badge({ tone, children, title }: { tone: string; children: React.ReactNode; title?: string }) {
  return <span title={title} className={cn("shrink-0 rounded-md px-1.5 py-0.5 text-[11px]", toneClass(tone))}>
    {children}</span>;
}

export function NotifyPanel() {
  const [s, setS] = useState<NotifyState | null>(null);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");
  /** 那句回执**显示在哪**：`plan` ＝ 定时计划那一格里（贴着「保存计划」那颗按钮），其余 → 卡片顶上。
   *  10-19 用户第二次报"点了保存计划没反应"：10-17 只修了"缺项不说话"，但**真提交出去那一发**的成败
   *  只印在卡片最上面那行 —— 这一屏约 14 屏高，保存按钮在 13,900px 而回执在 12,790px，
   *  点完眼睛所在的区域一个字都没变，成功之后表单还收起 ⇒ 看起来就是"没反应"。 */
  const [noticeAt, setNoticeAt] = useState("");
  const [busy, setBusy] = useState("");
  const [form, setForm] = useState<Form | null>(null);
  /** 渠道表单：`null`＝没开；`editing=false`＝新增（开在列表下面），
   *  `editing=true` 且 `key` 命中某一行＝开在**那一行里面**。一份表单两用途，别再长第二份。 */
  const [chanDraft, setChanDraft] = useState<ChannelDraft | null>(null);
  /** 只看明细、不进编辑态的那一行（点开已配置的渠道先看它到底是什么形状） */
  const [chanOpen, setChanOpen] = useState("");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);

  const reload = useCallback(async () => {
    try { setS(await loadNotify()); setErr(""); }
    catch (e) { setErr(`读不到推送配置：${(e as Error).message}`); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  /** 提交一件事并把后端那句 `reason` 原样显示 —— 不在前端重抄一句"已保存"，
   *  否则后端改了措辞（比如"这条要重启才停"）页面上就还是旧话。 */
  const run = useCallback(async (tag: string,
    fn: () => Promise<{ data: NotifyState; reason: string }>, keepForm = false) => {
    setBusy(tag); setNotice("");
    // 回执跟着动作走：计划那一系列（保存/启用/立刻发/删除）落在计划格里，其余在卡片顶
    setNoticeAt(/^(plan|pe|now|pd):/.test(tag) ? "plan" : "");
    try {
      const res = await fn();
      setS(res.data); setErr(""); setNotice(res.reason);
      if (!keepForm) setForm(null);
      return true;
    } catch (e) { setErr((e as Error).message); return false; }
    finally { setBusy(""); }
  }, []);

  /** 新增与编辑走同一条提交（`channelPayload` 在纯判据层，那边有真用例）。
   *  保存成功后**收掉表单**：留着会让用户以为还有没写回的东西，而后端已经落盘了。 */
  const submitChannel = async () => {
    const d = chanDraft;
    if (!d) return false;
    const ok = await run(`ch:${d.key || "新渠道"}`, async () =>
      await patchNotify("channel", channelPayload(d)), true);
    if (ok) { setChanDraft(null); setChanOpen(""); }
    return ok;
  };

  const channels = s?.channels || [];
  const plans = s?.plans || [];
  const catalog = s?.catalog || {};
  const sceneChoices = (catalog.scenes || []).map((x) => x.value);
  // 每档条数的单位由后端标（只/层/组/条）：列表那一行要说「自选 5 只、天梯 2 层」就得用这个，
  // 在前端猜单位就会出现「市场概览 2 条」这种看着对其实错的话。
  const limitUnits = Object.fromEntries((catalog.scenes || [])
    .filter((x) => x.limit_unit).map((x) => [x.value, String(x.limit_unit)]));

  const submitPlan = async () => {
    // `catalog` 决定"这一档有哪些格子要写回"，所以它必须进来 —— 少了那份，界面就只能瞎猜键名
    const ok = await run(`plan:${form?.id}`, async () =>
      await patchNotify("plan", formToPlan(form as Form, catalog)));
    // 10-17 用户点名"保存了……也没有推送"：保存成功了不等于马上会发 —— 到点是第 12 条常驻循环
    // 自己按 `run_at` 判的，而休市日 + 勾了「只在交易日」的今天本来就不该发。这一句必须当场说，
    // 否则"配好了但手机上一条都没有"就只能靠人来问。判据本身仍用后端那句 `today_label`。
    if (ok) setNotice((n) => `${n} 保存不等于立刻发：到点是后台每 60 秒一轮自己判的，`
      + "这一条今天发不发看它那一行的「今天：…」。要现在就验一次，点那一行的「立刻发一次」"
      + "（那是真发到群里，也会进「最近发送记录」的账）。");
    return ok;
  };

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">消息推送（渠道与定时计划）</h2>
        <Badge tone={s?.enabled === false ? "warn" : "ok"}>
          {s?.enabled === false ? "总开关已关" : "总开关开"}</Badge>
        {s && (
          <span className="text-[11px] text-muted-foreground">
            可用渠道 {s.counts?.usable_channels ?? 0}/{channels.length} 条｜
            就绪计划 {s.counts?.plans_sendable ?? 0}/{s.counts?.plans_enabled ?? 0} 条（在册 {plans.length}）
          </span>
        )}
        <label className="flex items-center gap-1 text-[11px]">
          <input type="checkbox" className="h-4 w-4 accent-primary" disabled={busy !== ""}
            checked={s?.enabled !== false} aria-label="推送总开关"
            onChange={() => void run("toggle", () => patchNotify("enabled", s?.enabled === false), true)} />
          总开关
        </label>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取</button>
      </div>

      {noticeAt !== "plan" && err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {noticeAt !== "plan" && notice && <p className="mt-1 text-[11px] text-warning">{notice}</p>}
      {s?.warning && <p className="mt-1 text-[11px] text-warning">{plain(s.warning)}</p>}
      {s && (s.counts?.plans ?? 0) > (s.counts?.plans_sendable ?? 0) && (
        <p className="mt-1 text-[11px] text-destructive">
          有 {(s.counts?.plans ?? 0) - (s.counts?.plans_sendable ?? 0)} 条计划现在发不出去 ——
          下面每行都写了原因，别等它到点才发现。
        </p>
      )}
      {s?.last_run?.last_run_at && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          推送循环最近一次执行：{s.last_run.last_run_at}（{s.last_run.last_status || DASH}）
          {(s.last_run.consecutive_failures ?? 0) > 0 ? `｜连败 ${s.last_run.consecutive_failures} 次` : ""}
          {s.last_run.reason ? `｜${plain(s.last_run.reason)}` : ""}
        </p>
      )}

      {/* ---------------------------------------------------------- 渠道 */}
      <h3 className="mt-3 text-sm font-semibold text-muted-foreground">渠道（只有这四种，其余本程没有执行体）</h3>
      <ul className="mt-1 space-y-1.5">
        {channels.map((c: NotifyChannel) => {
          const h = channelHealth(c);
          const open = chanOpen === String(c.key);
          const editing = !!chanDraft?.editing && chanDraft.key === String(c.key);
          return (
            <li key={String(c.key)} className={cn("rounded-lg border p-2",
              c.enabled === false ? "border-warning/50 bg-warning/5" : "border-border")}>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex min-w-0 flex-1 items-start gap-2">
                  <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary" disabled={busy !== ""}
                    checked={c.enabled !== false} aria-label={`${c.label} 这条渠道`}
                    onChange={() => void run(`che:${c.key}`, () => patchNotify("channel",
                      { key: c.key, kind: c.kind, label: c.label, url_env: c.url_env,
                        note: c.note, enabled: c.enabled === false }), true)} />
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-medium">
                      {c.label || c.key}<span className="ml-1 font-mono text-[10px] text-muted-foreground">{c.key}</span></span>
                    <span className="block truncate font-mono text-[11px] text-muted-foreground"
                      title={c.secret_source || ""}>
                      {c.kind_label || KIND_LABELS[String(c.kind)] || c.kind}｜{c.url_masked || DASH}</span>
                    {/* 加签那一格的状态要说得出口：填了＝显示来源；没填而这类渠道可以加签＝把后端的
                        提醒原样搬上来（对面拒绝时错误码会出现在「发一条测试」的结果里，但那是事后；
                        这一格是事前）。密钥内容永不显示，连掩码都不给。 */}
                    {c.kind_supports_sign && (c.has_sign ? (
                      <span className="block text-[10px] text-muted-foreground"
                        title={c.sign_source || ""}>已带加签密钥（{c.sign_source || "配置文件内联"}）</span>)
                      : <span className="block text-[10px] text-warning"
                        title={c.sign_warning || "群机器人开了「签名校验」时，缺密钥会被对面直接拒掉"}>
                        没填加签密钥（开了「签名校验」就必须填）</span>)}
                  </span>
                </label>
                <Badge tone={h.tone} title={h.label}>{h.label}</Badge>
                <button type="button" disabled={busy !== ""}
                  onClick={() => { setChanOpen(open && !editing ? "" : String(c.key));
                    if (editing) setChanDraft(null); }}
                  className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary">
                  {open || editing ? "收起明细" : "看明细 / 编辑"}</button>
                <button type="button" disabled={busy !== "" || h.tone === "bad"}
                  onClick={() => void run(`test:${c.key}`, async () => {
                    return await patchNotify("test_channel", c.key);
                  })}
                  className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
                  发一条测试</button>
                <button type="button" disabled={busy !== ""}
                  onClick={() => void run(`del:${c.key}`, async () => {
                    return await patchNotify("remove_channel", c.key);
                  }, true)}
                  className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-destructive/50 hover:text-destructive">
                  删除</button>
              </div>
              {open && !editing && (
                <ChannelDetails c={c} health={h} configPath={s?.config_path || "本机的配置文件"} />)}
              {open && !editing && (
                <button type="button" disabled={busy !== ""}
                  onClick={() => setChanDraft(channelDraft(c))}
                  className="mt-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
                  改这条的备注 / 凭据</button>)}
              {editing && chanDraft && (
                <ChannelForm draft={chanDraft} setDraft={setChanDraft} s={s} busy={busy}
                  onSave={() => void submitChannel()}
                  onCancel={() => { setChanDraft(null); setChanOpen(""); }} />)}
            </li>
          );
        })}
        {!channels.length && (
          <li className="text-[11px] text-muted-foreground">还没有一条渠道 —— 下面加一条，然后「发一条测试」验通。</li>
        )}
      </ul>

      {!chanDraft?.editing && (
        <button type="button" onClick={() => setChanDraft(emptyChannelDraft())}
          className="mt-2 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary">
          ＋加一条渠道</button>)}
      {chanDraft && !chanDraft.editing && (
        <ChannelForm draft={chanDraft} setDraft={setChanDraft} s={s} busy={busy}
          onSave={() => void submitChannel()}
          onCancel={() => setChanDraft(null)} />)}
      {/* ---------------------------------------------------------- 计划 */}
      <h3 className="mt-4 text-sm font-semibold text-muted-foreground">定时计划（发什么、什么时候发）</h3>
      <ul className="mt-1 space-y-2">
        {plans.map((p) => {
          const h = planHealth(p);
          const open = form?.id === p.id;
          return (
            <li key={String(p.id)} className={cn("rounded-lg border p-2",
              p.enabled ? "border-border" : "border-warning/50 bg-warning/5")}>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex min-w-0 flex-1 items-start gap-2">
                  <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary" disabled={busy !== ""}
                    checked={!!p.enabled} aria-label={`${p.name} 这条计划`}
                    onChange={() => void run(`pe:${p.id}`, () => patchNotify("plan",
                      { id: p.id, name: p.name, run_at: p.run_at, run_freq: p.run_freq,
                        weekly_days: p.weekly_days, trade_days_only: p.trade_days_only,
                        channels: (p.channels || []).map((c) => c.key), scenes: p.scenes,
                        note: p.note, enabled: !p.enabled }), true)} />
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-medium">
                      {p.name || DASH}<span className="ml-1 font-mono text-[10px] text-muted-foreground">{p.id}</span></span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {scheduleText(p)}｜发往 {(p.channels || []).map((c) => c.label || c.key).join("、") || DASH}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      内容：{scenesText(p, limitUnits, catalog)}</span>
                    {/* 「今天这一档」那句**整段来自后端**（判据与执行同住 `notifyconfig.slot_state`）——
                        前端只决定轻重色，不重写句子。重写迟早与宿主说两句不同的话。 */}
                    {p.today_label ? (
                      <span className={cn("block truncate text-[11px]",
                        TODAY_TONE[todayTone(p.today_state)])} title={p.today_label}>
                        今天：{p.today_label}</span>) : null}
                  </span>
                </label>
                <Badge tone={h.tone} title={h.label}>{h.label}</Badge>
                <button type="button" disabled={busy !== ""}
                  onClick={() => setForm(open ? null : formFromPlan(p, catalog))}
                  className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary">
                  {open ? "收起" : "编辑"}</button>
                <button type="button" disabled={busy !== "" || h.tone === "bad"}
                  onClick={() => { setBusy(`pv:${p.id}`); setErr("");
                    void previewPlan(p).then(setPreview).catch((e) => setErr((e as Error).message))
                      .finally(() => setBusy("")); }}
                  className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
                  预览正文</button>
                <button type="button" disabled={busy !== "" || h.tone === "bad"}
                  onClick={() => void run(`now:${p.id}`, () => patchNotify("run_now", p.id), true)}
                  className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
                  立刻发一次</button>
                <button type="button" disabled={busy !== ""}
                  onClick={() => void run(`pd:${p.id}`, () => patchNotify("remove_plan", String(p.id)), true)}
                  className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-destructive/50 hover:text-destructive">
                  删除</button>
              </div>
              {p.note && <p className="mt-1 pl-6 text-[11px] text-muted-foreground">{plain(p.note)}</p>}
              {p.invalid_reason && (
                <p className="mt-1 pl-6 text-[11px] text-destructive">配置不合法：{p.invalid_reason}</p>)}
              {open && form && <PlanForm form={form} setForm={setForm} s={s} catalog={catalog}
                sceneChoices={sceneChoices} busy={busy}
                onSave={() => void submitPlan()} />}
            </li>
          );
        })}
        {!plans.length && (
          <li className="text-[11px] text-muted-foreground">还没有计划 —— 加一条：选内容、选渠道、填时刻。</li>
        )}
      </ul>
      {!form && (
        <button type="button" onClick={() => setForm(emptyForm())}
          className="mt-2 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary">
          ＋加一条计划</button>
      )}
      {/* ⚠️ 这一坨是 10-16 浏览器实测补的：上面那个表单原本**只渲染在 `plans.map` 的每一项内部**，
          所以零条计划时点「＋加一条计划」只会让按钮消失、表单无处可长 —— 第一条计划永远建不出来。
          新增计划（`form.id` 不在现有清单里）必须能在列表**外面**渲染。 */}
      {form && !plans.some((p) => p.id === form.id) && (
        <div className="mt-2 rounded-lg border border-border p-2">
          <PlanForm form={form} setForm={setForm} s={s} catalog={catalog}
            sceneChoices={sceneChoices} busy={busy}
            onSave={() => void submitPlan()} />
        </div>
      )}
      {/* 计划这一格的成败就说在这里（点了「保存计划」之后眼睛看的那一片），
          并且每次出现都把自己滚进视野 —— 表单在成功之后会收起，这一行留在原处。 */}
      {noticeAt === "plan" && (err || notice) && (
        <p ref={(el) => { if (el) el.scrollIntoView({ block: "center" }); }}
          className={`mt-2 text-[11px] font-medium ${err ? "text-destructive" : "text-warning"}`}>
          {err || notice}</p>)}

      {preview && (
        <div className="mt-3 rounded-lg border border-border p-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium">将要发出的正文（未发送）</span>
            <button type="button" onClick={() => setPreview(null)}
              className="ml-auto text-[11px] text-muted-foreground">关闭</button>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            标题：{String(preview.title || DASH)}｜
            会发给：{((preview.would_send_to || []) as string[]).join("、") || "（没有可用渠道）"}
          </p>
          {((preview.blocked || []) as string[]).map((b) => (
            <p key={b} className="text-[11px] text-warning">⚠ {plain(b)}</p>))}
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/40 p-2 text-[11px]">
            {String(preview.body || "")}</pre>
        </div>
      )}

      {(s?.history || []).length > 0 && (
        <details className="mt-3 rounded-lg border border-border/60 p-2 text-[11px]">
          <summary className="cursor-pointer font-medium">最近发送记录（{s!.history!.length} 条，成败都记，不含凭据）</summary>
          <ul className="mt-1 space-y-0.5">
            {s!.history!.slice(0, 12).map((h, i) => (
              <li key={i} className="font-mono text-[11px] text-muted-foreground">
                {String(h.at || DASH)}｜{String(h.name || h.plan || DASH)}｜
                {String(h.status === "ok" ? "已发" : h.status === "fail" ? "失败" : h.status ?? DASH)}｜
                {String(h.detail || "")}｜→ {((h.sent_to || []) as string[]).join("、") || DASH}
                {((h.skipped || []) as string[]).length ? `｜跳过 ${((h.skipped || []) as string[]).length} 条` : ""}
              </li>))}
          </ul>
        </details>
      )}

      <p className="mt-2 text-[11px] leading-4 text-muted-foreground">{plain(s?.note ?? "")}</p>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{plain(s?.explain ?? "")}</p>
    </GlassCard>
  );
}

function PlanForm({ form, setForm, s, catalog, sceneChoices, busy, onSave }: {
  form: Form; setForm: (f: Form) => void; s: NotifyState | null;
  catalog: NotifyState["catalog"]; sceneChoices: string[];
  busy: string; onSave: () => void;
}) {
  const set = (patch: Partial<Form>) => setForm({ ...form, ...patch });
  // 点了「保存计划」却没保存成 ⇒ 才把"还差什么"摊出来（没点之前不吓人的红字）
  const [tried, setTried] = useState(false);
  const missing = missingParts(form);
  const toggleScene = (k: string) => set({
    scenes: form.scenes.includes(k) ? form.scenes.filter((x) => x !== k) : [...form.scenes, k] });
  const setParam = (scene: string, key: string, value: SceneValue) => set({
    params: { ...form.params, [scene]: { ...(form.params[scene] || {}), [key]: value } } });
  const sceneMeta = (catalog?.scenes || []).filter((x) => form.scenes.includes(x.value));
  return (
    <div className="mt-2 space-y-2 border-t border-border/60 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <input value={form.name} placeholder="计划名字（必填，如：收盘后·自选与共振）" aria-label="计划名字（必填）"
          onChange={(e) => set({ name: e.target.value })}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
        <input value={form.run_at} type="time" aria-label="发送时刻"
          onChange={(e) => set({ run_at: e.target.value })}
          className="w-32 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
        <select value={form.run_freq} aria-label="频度"
          onChange={(e) => set({ run_freq: e.target.value })}
          className="rounded-lg border border-border bg-background px-2 py-1 text-[12px]">
          <option value="daily">每日</option><option value="weekly">每周（勾周几）</option>
        </select>
        <label className="flex items-center gap-1 text-[11px]">
          <input type="checkbox" className="h-4 w-4 accent-primary" checked={form.trade_days_only}
            aria-label="只在交易日发" onChange={(e) => set({ trade_days_only: e.target.checked })} />
          只在交易日</label>
      </div>

      {form.run_freq === "weekly" && (
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          {WEEK_DAY_LABELS.map((label, i) => (
            <label key={label} className="flex items-center gap-1">
              <input type="checkbox" className="h-3.5 w-3.5 accent-primary" aria-label={`${label}发送`}
                checked={form.days.includes(i)}
                onChange={() => set({ days: form.days.includes(i)
                  ? form.days.filter((d) => d !== i) : [...form.days, i].sort((a, b) => a - b) })} />
              {label}</label>))}
          {!form.days.length && <span className="text-warning">没勾周几 ⇒ 后端按「判不了就放行」会天天发，与「每周」相反</span>}
        </div>
      )}

      <div>
        <p className="text-[11px] font-medium text-muted-foreground">发什么（可多选，内容全部复用页面上那些读数块）<span className="ml-1 text-destructive">＊必选至少一档</span></p>
        <div className="mt-1 grid gap-x-4 gap-y-1 sm:grid-cols-2">
          {(catalog?.scenes || []).map((x) => (
            <label key={x.value} className="flex items-start gap-1 text-[11px]" title={x.hint || ""}>
              <input type="checkbox" className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-primary"
                aria-label={`发送 ${x.label || SCENE_LABELS[x.value] || x.value}`}
                checked={form.scenes.includes(x.value)} onChange={() => toggleScene(x.value)} />
              <span className="min-w-0">
                <span className="block">{x.label || SCENE_LABELS[x.value] || x.value}</span>
                {x.hint && x.hint !== x.label && (
                  <span className="block text-[10px] leading-3 text-muted-foreground">{x.hint}</span>)}
              </span>
            </label>))}
          {!sceneChoices.length && (
            <span className="text-[11px] text-warning">
              内容清单没读到（后端那份目录失败了）—— 现在一格也勾不了，先点「重新读取」</span>)}
        </div>
      </div>

      {/* 每一档**自己**的条数：候选与默认值/上限全从后端 `catalog.scenes[]` 来
          （`value` / `label` / `default_limit` / `max_limit`）。所以以后新加一档内容只动后端那张
          执行体表，这一排会自动多出一个框；反过来在前端写死"有哪几档、默认几条"必然会有人改漏。 */}
      {form.scenes.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-[11px] text-muted-foreground">各档条数（留空＝用默认）：</span>
          {(catalog?.scenes || []).filter((x) => form.scenes.includes(x.value)).map((x) => (
            <label key={x.value} className="flex items-center gap-1 text-[11px]">
              {limitLabel(catalog, x.value, x.label || SCENE_LABELS[x.value] || x.value)}
              <input type="number" min={1} max={x.max_limit || 500}
                placeholder={x.default_limit != null ? String(x.default_limit) : "默认"}
                aria-label={`条数上限 ${x.label || x.value}`}
                value={form.limits[x.value] || ""}
                onChange={(e) => set({ limits: { ...form.limits, [x.value]: e.target.value } })}
                className="w-16 rounded-lg border border-border bg-background px-1 py-0.5 text-[11px]" />
              {x.limit_unit && <span className="text-muted-foreground">{x.limit_unit}</span>}
            </label>))}
        </div>)}

      {/* 每一档**自己的那些参数格**：由 `catalog.scenes[].fields`（后端 `notifyconfig.SCENE_FIELDS`）
          渲染。17 档 × 每档 1~3 格，这里一处代码就够 —— 后端加一格，界面自动多一个框，
          不需要再来这里加一段 `form.scenes.includes("xxx") && (…)`。 */}
      {sceneMeta.map((x) => {
        const fields = sceneFieldList(catalog, x.value).filter((f) => f.key !== "limit");
        if (!fields.length) return null;
        return (
          <div key={x.value} className="space-y-1 rounded-lg border border-border/60 bg-muted/20 p-1.5">
            <p className="text-[11px] font-medium">{x.label || x.value}的参数</p>
            {fields.map((fd) => (
              <FieldControl key={fd.key} fd={fd} busy={busy}
                value={(form.params[x.value] || {})[fd.key]}
                onChange={(v) => setParam(x.value, fd.key, v)} />))}
          </div>);
      })}

      <div>
        <p className="text-[11px] font-medium text-muted-foreground">发到哪些渠道<span className="ml-1 text-destructive">＊必选至少一条</span></p>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
          {(s?.channels || []).map((c) => (
            <label key={String(c.key)} className="flex items-center gap-1 text-[11px]">
              <input type="checkbox" className="h-3.5 w-3.5 accent-primary" aria-label={`发送到 ${c.label}`}
                checked={form.channels.includes(String(c.key))}
                disabled={c.enabled === false || channelHealth(c).tone === "bad"}
                title={channelHealth(c).label}
                onChange={() => set({ channels: form.channels.includes(String(c.key))
                  ? form.channels.filter((x) => x !== c.key) : [...form.channels, String(c.key)] })} />
              {c.label || c.key}</label>))}
          {!(s?.channels || []).length && <span className="text-warning">还没有渠道，先在上方加一条</span>}
        </div>
      </div>

      {/* 分条还是合并（用户 2026-10-19：「具体的内容，按照对应的点，分条推送。不要合并推送。」）。
          选项与那句长说明**都从后端 `send_mode_choices` 来**（措辞只住 `notifyconfig.SEND_MODE_MEANING`），
          这里不抄第二份 —— 抄的那份一定会有人改漏，而这一格决定手机上收到一条还是六条。 */}
      {(catalog?.send_mode_choices || []).length > 0 && (
        <div>
          <p className="text-[11px] font-medium text-muted-foreground">一条还是几条</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <select value={form.send_mode} disabled={busy !== ""} aria-label="这条计划怎么发"
              onChange={(e) => set({ send_mode: e.target.value })}
              className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]">
              {(catalog?.send_mode_choices || []).map((m) => (
                <option key={m.value} value={m.value} title={m.hint || ""}>{m.label || m.value}</option>
              ))}
            </select>
            <span className="text-[11px] text-muted-foreground">
              分条时每一档各占一份渠道上限（企微那种 2048 字节硬墙逐条判，超长的那一条自己声明）
            </span>
          </div>
        </div>)}

      {/* ⚠️ 保存按钮**永远点得动**（10-17 用户原话："点击保存计划，没反应，也没生效配置"）。
          原本这里是 `disabled={!form.name.trim()}` —— 名字空着就灰掉，而灰掉的按钮点下去
          一句话都不说：屏幕上就是"没反应"，用户分不清是没保存、保存坏了、还是软件坏了。
          现在点下去把**还差哪几样**按名字列出来（判据与后端 `validate_plan` 前三条一字对应）。 */}
      {tried && missing.length > 0 && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-2">
          <p className="text-[11px] font-medium text-destructive">还差这几样，所以这条没保存：</p>
          <ul className="mt-0.5 list-disc pl-4 text-[11px] text-destructive">
            {missing.map((m) => <li key={m}>{m}</li>)}
          </ul>
        </div>)}
      <div className="flex flex-wrap items-center gap-2">
        <input value={form.note} placeholder="为什么要有这一条（可留空，给人快速看的）" aria-label="计划理由"
          onChange={(e) => set({ note: e.target.value })}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-[11px]" />
        <button type="button" disabled={busy !== ""}
          title={missing.length ? `还差：${missing.join("；")}` : "保存这条计划"}
          onClick={() => { if (missingParts(form).length) { setTried(true); return; } onSave(); }}
          className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
          保存计划</button>
        <button type="button" onClick={() => { setForm(emptyForm()); setTried(false); }}
          className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground">清空重填</button>
      </div>
    </div>
  );
}

/**
 * 一格参数的渲染。**只认 `fd.type`，不认档名、不认键名**（`fd` 来自后端 `SCENE_FIELDS`）。
 *
 * 三条纪律：
 * 1. 留空就是"这一格不写进配置"，含义由后端 `empty_means`/`default` 说 —— 界面不填一个自造的默认值，
 *    否则用户看到的数字与后端真正用的数字迟早是两份（10-17 之前就是这样：面板默认 2、后端默认 12）。
 * 2. `busy`（正在保存）时禁用：保存途中改出来的那份不是刚发出去的那份。
 * 3. 候选没读到时**当场说没读到**，不许静默给一张空下拉 —— 那是"能填但没人读"的框。
 */
function FieldControl({ fd, value, onChange, busy }: {
  fd: SceneField; value: SceneValue | undefined; onChange: (v: SceneValue) => void; busy: string;
}) {
  const cur = value ?? (fd.type === "bool" ? false : fd.type === "multi" ? [] : "");
  const choices = fieldChoices(fd);
  const emptyText = fd.empty_means
    || (fd.default !== null && fd.default !== undefined ? `默认 ${fd.default}` : "用后端默认");
  const box = "rounded-lg border border-border bg-background px-2 py-1 text-[11px]";
  const tip = fd.note ? <span className="text-[10px] text-muted-foreground" title={fd.note}>{fd.note}</span> : null;

  if (fd.type === "bool") {
    return (
      <label className="flex items-center gap-2 text-[11px]">
        <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={busy !== ""}
          checked={cur === true} aria-label={fd.label || fd.key}
          onChange={(e) => onChange(e.target.checked)} />
        {fd.label || fd.key}{tip}
      </label>);
  }
  if (fd.type === "multi") {
    const picked = Array.isArray(cur) ? cur : [];
    return (
      <div className="space-y-0.5">
        <p className="text-[11px] text-muted-foreground">{fd.label || fd.key}
          <span className="ml-1">（多选＝取并集；一个都不勾＝{emptyText}）</span>{tip}</p>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {choices.map((o) => (
            <label key={o.value} className="flex items-center gap-1 text-[11px]">
              <input type="checkbox" className="h-3.5 w-3.5 accent-primary" disabled={busy !== ""}
                aria-label={`${fd.label || fd.key} ${o.label}`}
                checked={picked.includes(o.value)}
                onChange={() => onChange(picked.includes(o.value)
                  ? picked.filter((x) => x !== o.value) : [...picked, o.value])} />
              {o.label}</label>))}
          {!choices.length && (
            <span className="text-[11px] text-warning">这一格的候选没读到 —— 只能留空（＝{emptyText}）</span>)}
        </div>
      </div>);
  }
  if (fd.type === "select") {
    return (
      <label className="flex items-center gap-2 text-[11px]">{fd.label || fd.key}
        <select value={String(cur)} disabled={busy !== ""} aria-label={fd.label || fd.key}
          onChange={(e) => onChange(e.target.value)} className={box}>
          <option value="">{fd.empty_means || "不限（用默认）"}</option>
          {choices.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {!choices.length && <span className="text-[11px] text-warning">候选没读到</span>}{tip}
      </label>);
  }
  if (fd.type === "codes") {
    return (
      <label className="flex flex-wrap items-center gap-2 text-[11px]">{fd.label || fd.key}
        <span className="text-muted-foreground">（逗号分隔；留空＝{emptyText}）</span>
        <input value={String(cur)} placeholder="600519,300750.SZ" disabled={busy !== ""}
          aria-label={fd.label || fd.key} onChange={(e) => onChange(e.target.value)}
          className={`min-w-0 flex-1 ${box}`} />{tip}
      </label>);
  }
  if (fd.type === "int" || fd.type === "num") {
    return (
      <label className="flex items-center gap-2 text-[11px]">{fd.label || fd.key}
        <input type="number" value={String(cur)} disabled={busy !== ""}
          min={fd.min ?? undefined} max={fd.max ?? undefined}
          placeholder={fd.default !== null && fd.default !== undefined ? String(fd.default) : "留空＝默认"}
          aria-label={fd.label || fd.key} onChange={(e) => onChange(e.target.value)}
          className={`w-20 ${box}`} />
        {fd.unit && <span className="text-muted-foreground">{fd.unit}</span>}
        <span className="text-muted-foreground">（{fd.min}~{fd.max}，留空＝{emptyText}）</span>{tip}
      </label>);
  }
  return (
    <label className="flex flex-wrap items-center gap-2 text-[11px]">{fd.label || fd.key}
      <input value={String(cur)} disabled={busy !== ""} aria-label={fd.label || fd.key}
        placeholder={fd.empty_means || "留空＝不筛"} onChange={(e) => onChange(e.target.value)}
        className={`min-w-0 flex-1 ${box}`} />{tip}
    </label>);
}

/**
 * 渠道表单 —— **新增与编辑共用这一份**（原先只有"新增"那一份，于是配好的渠道点不开、
 * 改不了备注、也不能换一种凭据写法，只能删了重填，而删重填要人手再抄一遍 token）。
 *
 * 三条不能松的规矩：
 * 1. `url` / `secret` 两格**永远空着开**，且是 `type="password"`：读接口只有掩码，
 *    把掩码回填进输入框再保存＝把 `******` 当凭据写回配置文件。留空＝后端保旧值。
 * 2. 键（`key`）在编辑态**不给改**：后端是按键找到那一条的，改名＝新建一条，
 *    指着旧键的计划会当场变成「渠道已删」。
 * 3. 提交只走 `channelPayload`（纯函数，`node --test` 里有真用例），
 *    这里不第二段拼 body —— 两份拼法迟早少一个字段（原来漏的就是 `note`）。
 */
function ChannelForm({ draft, setDraft, s, busy, onSave, onCancel }: {
  draft: ChannelDraft; setDraft: (d: ChannelDraft) => void; s: NotifyState | null;
  busy: string; onSave: () => void; onCancel: () => void;
}) {
  const set = (patch: Partial<ChannelDraft>) => setDraft({ ...draft, ...patch });
  const box = "rounded-lg border border-border bg-background px-2 py-1 text-[12px]";
  return (
    <div className="mt-2 space-y-1.5 rounded-lg border border-border p-2">
      <p className="text-[11px] font-medium">
        {draft.editing ? `改这条渠道：${draft.key}` : "加一条渠道"}
        <span className="ml-2 font-normal text-muted-foreground">
          （凭据那一格空着＝不改已存的那条；页面从不显示真串）</span>
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <select value={draft.kind} aria-label="渠道种类"
          onChange={(e) => set({ kind: e.target.value })}
          className={`rounded-lg border border-border bg-background px-2 py-1 text-[12px]`}>
          {(s?.kind_choices || Object.keys(KIND_LABELS).map((k) => ({ value: k, label: KIND_LABELS[k] })))
            .map((o) => <option key={String((o as { value: string }).value ?? o)}
              value={String((o as { value: string }).value ?? o)}>
              {(o as { label?: string }).label ?? KIND_LABELS[String(o)]}</option>)}
        </select>
        <input value={draft.key} placeholder="键（字母开头，如 feishu-main）" aria-label="渠道键"
          readOnly={draft.editing} disabled={draft.editing || busy !== ""}
          title={draft.editing ? "键是这条渠道的身份，改了等于新建一条、指着旧键的计划会变成「渠道已删」" : ""}
          onChange={(e) => set({ key: e.target.value })}
          className={`w-40 ${box} ${draft.editing ? "bg-muted/40 text-muted-foreground" : ""}`} />
        <input value={draft.label} placeholder="中文名（如：盯盘群）" aria-label="渠道名字"
          onChange={(e) => set({ label: e.target.value })} disabled={busy !== ""}
          className={`w-40 ${box}`} />
      </div>
      <input value={draft.url} type="password" autoComplete="off"
        placeholder={urlPlaceholder(draft.kind, s?.kind_choices)
          + (draft.editing ? "；留空＝不改已存的那条地址" : "")}
        aria-label="渠道 URL（凭据）"
        onChange={(e) => set({ url: e.target.value })} disabled={busy !== ""}
        className={`w-full ${box}`} />
      {/* 飞书「签名校验」/钉钉「加签」那串密钥：**单独一格**，因为用户在群机器人设置页看到的
          本来就是两样东西（一条 webhook 地址 + 一串密钥），塞一条 URL 里等于让他自己拼。
          这一格是 password 且**永不回显**：留空＝不改已存的密钥。 */}
      {supportsSign(draft.kind, s?.kind_choices) && (
        <div className="flex flex-wrap items-center gap-2">
          <input value={draft.secret} type="password" autoComplete="off"
            placeholder="加密密钥 / 加签串（群机器人开了「签名校验 / 加签」才要填；留空＝不改）"
            aria-label="加密密钥（选填）"
            onChange={(e) => set({ secret: e.target.value })} disabled={busy !== ""}
            className={`min-w-0 flex-1 ${box}`} />
          <input value={draft.secret_env} placeholder="或填 .env 里的变量名（如 FEISHU_SECRET）"
            aria-label="密钥所在的环境变量名"
            onChange={(e) => set({ secret_env: e.target.value })} disabled={busy !== ""}
            className={`min-w-0 flex-1 ${box}`} />
        </div>)}
      <div className="flex flex-wrap items-center gap-2">
        <input value={draft.url_env} placeholder="或填 .env 里的变量名（推荐，如 NOTIFY_FEISHU_URL）"
          aria-label="凭据所在的环境变量名"
          onChange={(e) => set({ url_env: e.target.value })} disabled={busy !== ""}
          className={`min-w-0 flex-1 ${box}`} />
      </div>
      <input value={draft.note} placeholder="这条是哪两个群？为什么留着它（给人快速认出来的，可留空）"
        aria-label="渠道备注" onChange={(e) => set({ note: e.target.value })} disabled={busy !== ""}
        className={`w-full ${box}`} />
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy !== "" || !draft.key.trim()}
          onClick={onSave}
          className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
          {draft.editing ? "保存这条渠道" : "保存渠道"}</button>
        <button type="button" onClick={onCancel}
          className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground">取消</button>
      </div>
      <p className="text-[11px] leading-4 text-muted-foreground">
        真串填在上方就只写进 <code>{s?.config_path || "本机的配置文件"}</code>（0600，页面上只显示掩码）；
        填变量名则真串留在 <code>.env</code>，两份都填时变量名优先。
        {draft.editing && " 已存的凭据不会因为这次保存被清掉 —— 只有在这一格里重新填了才换。"}
      </p>
    </div>);
}

/**
 * 一条已配置渠道的**明细**（点「看明细 / 编辑」展开）。
 * 设计上就是"能看到、但有一格必须看不见"：地址只交尾 4 位、加签密钥连掩码都不给，
 * 其余（种类、键、变量名、来源、备注、为什么现在发不出去）全部原样摊开 ——
 * 用户要核对的恰恰是"这条指到哪个变量、为什么标红"，而不是那串 token。
 */
function ChannelDetails({ c, health, configPath }: {
  c: NotifyChannel; health: { label: string; tone: string }; configPath: string;
}) {
  const line = (k: string, v?: string | null, tone?: string) => (
    <p key={k} className="text-[11px] leading-4">
      <span className="text-muted-foreground">{k}：</span>
      <span className={cn("font-mono", tone === "warn" && "text-warning",
        tone === "bad" && "text-destructive")}>{v || DASH}</span>
    </p>);
  return (
    <div className="mt-1 space-y-0.5 rounded-lg bg-muted/30 p-2 pl-6">
      {line("种类", c.kind_label || KIND_LABELS[String(c.kind)] || String(c.kind))}
      {line("键", String(c.key || ""))}
      {line("名字", String(c.label || ""))}
      {line("地址（只显示尾 4 位）", c.url_masked)}
      {line("凭据来自", c.secret_source || (c.url_env ? `环境变量 ${c.url_env}（本机读不到）` : "没填"))}
      {line("变量名（URL）", c.url_env)}
      {c.kind_supports_sign && line("加签密钥", c.has_sign
        ? `已带（${c.sign_source || "配置文件内联"}）—— 内容永不显示`
        : `没填${c.secret_env ? `，但变量名写着 ${c.secret_env}` : ""}`,
        c.has_sign ? undefined : "warn")}
      {line("启停", c.enabled === false ? "已停用（指着它的计划现在发不出去）" : "启用中")}
      {line("备注", c.note ? plain(String(c.note)) : "")}
      {line("现在能不能发", health.label, health.tone === "bad" ? "bad" : health.tone === "warn" ? "warn" : undefined)}
      {line("配置文件", configPath + "（0600）")}
      <p className="text-[10px] leading-4 text-muted-foreground">
        真串（webhook 地址里的 token、加签密钥）在任何读数里都不出现：日志、历史、这份明细都没有。
        要换凭据就点上面的编辑，在那一格里重新填一次（留空＝保留原来的）。
      </p>
    </div>);
}
