import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { DocDrawer } from "@/components/ui/DocDrawer";
import { RefreshCw, Save } from "lucide-react";
import { blockingReason, dirtyPatch, ledgerText, resultCodeText, runAtText, taskPatch,
  type BroadcastConfig, type BroadcastState, type BroadcastTask } from "@/lib/intradayBroadcastReads";
import { loadBroadcast, saveBroadcast } from "@/lib/intradayBroadcast";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";

const DASH = "—";

/**
 * 系统配置 →「盘中播报」。用户 11-02 点名：「这三个任务，是在你这套程序的系统设置里面
 * 可以配置时间和推送渠道的吗」—— 之前答案是"不能"（时点写死在两个 .ps1 生成的任务 XML 里、
 * 渠道写死在命令行参数里，界面上既看不到也改不了）。这一屏把那两格交出来。
 *
 * 两件事必须让使用者当场看见，不能靠他记住：
 *  · **保存会动任务计划程序**（覆盖式注册；关掉的那一路是注销任务，不是留个空跑的任务）；
 *  · **"配置开着"不等于"群里收到了"** —— 所以每一行都并排给机器上真注册的状态
 *    （Ready / Disabled / 下次时间）与今天库里真发出去几条。
 */
export function IntradayBroadcastPanel() {
  const [s, setS] = useState<BroadcastState | null>(null);
  const [cfg, setCfg] = useState<BroadcastConfig>({});
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  const reload = useCallback(() => loadBroadcast()
    .then(r => {
      if (!r) { setErr("盘中播报状态读不到（接口没回 data）—— 屏上这些格子不能当真"); return; }
      setS(r);
      setCfg({ ...(r.config ?? {}) });
      setErr(r.readable ? "" : `计划任务状态读不出来：${r.reason ?? "未知原因"}`);
    })
    .catch(e => setErr(e instanceof Error ? e.message : "盘中播报状态读不到")), []);
  useEffect(() => { void reload(); }, [reload]);

  const post = async (patch: BroadcastConfig, label: string) => {
    setBusy(label); setNotice("");
    try {
      const r = await saveBroadcast(patch);
      setNotice(r.reason);
      if (r.data) { setS(r.data); setCfg({ ...(r.data.config ?? {}) }); }
      else void reload();
    } catch (e) {
      // 后端拒绝要原样带出（"这一笔整条没写入，仍按原值运行"），不许折叠成"保存失败"
      setNotice(e instanceof Error ? e.message : "后端没有采纳这次修改");
      void reload();
    } finally { setBusy(""); }
  };

  const tasks = s?.tasks ?? [];
  const neverChanged = !!s && !s.changed?.changed_at;
  const options = s?.channel_options ?? [];
  const chosen = String(cfg.channels ?? "").split(",").map(x => x.trim()).filter(Boolean);

  const toggleChannel = (key: string) => {
    const next = chosen.includes(key) ? chosen.filter(x => x !== key) : [...chosen, key];
    setCfg(c => ({ ...c, channels: next.join(",") }));
  };

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">盘中播报（时点、渠道与微信镜像）</h2>
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <input type="checkbox" checked={!!cfg.enabled} disabled={busy !== ""}
              aria-label="盘中播报总开关"
              onChange={e => setCfg(c => ({ ...c, enabled: e.target.checked }))} />
            总开关
          </label>
          <button type="button" disabled={busy !== "" || cfg.enabled === s?.config?.enabled}
            onClick={() => void post({ enabled: !!cfg.enabled }, "总闸")}
            className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
            <Save className="h-3 w-3" />保存总闸
          </button>
          <button type="button" disabled={busy !== ""} onClick={() => void reload()}
            className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
            <RefreshCw className="h-3 w-3" />重新读取
          </button>
          <DocDrawer title="盘中播报（时点、渠道与微信镜像）">
            <p>
              这几路是「到点起一个短进程、跑完就退」，由 <b className="font-medium">Windows 任务计划程序</b>执行，
              不是本项目进程里的常驻循环 —— 外部 10-23 那次常驻 watcher 静默死亡、盘中 8 轮全瞎，
              之后看门狗就交给了调度器。所以<b className="font-medium">保存这一屏会动任务计划程序</b>：
              开着的那几路覆盖式注册，关掉的那一路直接把任务注销（不留"每天空跑一次然后不出声"的任务）。
            </p>
            <p className="mt-1">
              时点与渠道落在作业配置 <code>jobs/config.json</code> 的 <code>intraday_broadcast</code>；
              判据与正文不在这儿（住 <code>ingest/jobs/*.py</code> 与 <code>app/intraday/templates.json</code>）。
              渠道候选来自「消息推送」那一屏登记的渠道；要加群机器人先去那里加。
            </p>
            <p className="mt-1">
              每行右边两格是不同的事：<b className="font-medium">任务</b>是机器上真注册成什么状态，
              <b className="font-medium">今天</b>是库里 <code>analytics.intraday_broadcast</code> 的留痕
              （打了几针、真发出去几条）。针数比发出条数多属正常 —— 事件差那一炉要攒够变化才播一次。
              「上次 / 下次」那两格只到<b className="font-medium">分钟</b>：任务计划程序回的时间秒位不可信
              （本机实测它把秒印成分钟），要精确到秒看行末「今天 … 最后 …」那一格，那是脚本自己写进库的。
            </p>
            <p className="mt-1">
              <b className="font-medium">取数与推送是两层</b>（用户 11-03 定）：
              「涨跌停池事件差」那一行<b className="font-medium">只落库、不发送</b>，盘中每 5 分钟一针把事件差写进
              <code>analytics.intraday_pool_event</code>；到点发送归「盘中涨跌停变化量」那一行，它一次源站都不碰，
              只读库里的事件差汇总 —— 所以六点里任何一次都能事后重跑（<code>--since HH:MM</code> 补发），
              重跑拿到的数与当时一致。概念热度跟这六个时点走（各自 +10 秒，等事件差先落库），
              界面上它只有开关、没有自己的时点格。
            </p>
            <p className="mt-1">
              同一层分法也用在<b className="font-medium">交易体系榜</b>上：<code>14:39</code> 那一行取历史日线 +
              盘中快照、按 <code>sys-1</code> 判完逐票落 <code>analytics.intraday_sysentry</code>（不发东西）；
              <code>14:40:30</code> 那一行只读那张表排版再发。库里今天没有行时它播一句「行情未就绪」并写清为什么，
              <b className="font-medium">绝不把昨天的行当今天的榜</b>。这一对是 11-03 从外部
              <code>intraday_sysentry/</code> 收编进来的 —— 外部那条 <code>Guanlan-SysEntry1440</code> 是一次性触发，
              自 10-30 起就再没排过期（屏上"Ready"但下次时间为空），这里改成每日触发并已把它停用。
            </p>
          </DocDrawer>
        </span>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {notice && <p className="mt-2 text-[11px] text-warning">{plain(notice)}</p>}
      {!!s?.warnings?.length && <p className="mt-1 text-[11px] text-warning">⚠ {s.warnings.map(plain).join("；")}</p>}
      {s?.ledger_reason && <p className="mt-1 text-[11px] text-warning">⚠ {plain(s.ledger_reason)}</p>}

      {/* 渠道：勾的是"这几路发到哪些群"，候选一个都不写死 */}
      <div className="mt-2 rounded-lg border border-border/60 p-2">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-xs font-medium">发到哪些渠道</span>
          <code className="text-[10px] text-muted-foreground">channels</code>
          {cfg.channels !== s?.config?.channels &&
            <span className="rounded bg-primary/15 px-1 py-0.5 text-[10px] text-primary">已改</span>}
        </div>
        <div className="mt-1 flex flex-wrap gap-2">
          {options.map(o => (
            <label key={o.key} className="flex items-center gap-1 text-[11px]">
              <input type="checkbox" checked={chosen.includes(o.key)} disabled={busy !== ""}
                aria-label={`渠道 ${o.key}`} onChange={() => toggleChannel(o.key)} />
              {o.label || o.key}
              <code className="text-[10px] text-muted-foreground">{o.key}</code>
              {!o.usable && <span className="rounded bg-destructive/15 px-1 py-0.5 text-[10px] text-destructive">不可用</span>}
            </label>
          ))}
          {!options.length && (
            <span className="text-[11px] text-muted-foreground">
              渠道名单读不出来{s?.channel_options_reason ? `（${s.channel_options_reason}）` : ""} ——
              现在显示的是已存的值，勾不了新渠道。
            </span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <input value={String(cfg.channels ?? "")} disabled={busy !== ""} aria-label="渠道串"
            onChange={e => setCfg(c => ({ ...c, channels: e.target.value }))}
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 font-mono text-[11px]" />
          <button type="button" disabled={busy !== "" || cfg.channels === s?.config?.channels}
            onClick={() => void post({ channels: String(cfg.channels ?? "").trim() }, "渠道")}
            className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
            <Save className="h-3 w-3" />保存渠道
          </button>
        </div>
        {String(cfg.channels ?? "").trim() === "" && (
          <p className="mt-1 text-[11px] text-destructive">
            现在是空的 —— 空串保存会被拒（到点会跑但一条都发不出去）。想停某一路请关它那一行的开关。
          </p>
        )}
      </div>

      <div className="mt-2 grid gap-1.5">
        {tasks.map(t => (
          <TaskRow key={t.key} task={t} cfg={cfg} saved={s?.config ?? {}} setCfg={setCfg}
            busy={busy} onSave={patch => void post(patch, t.key)} day={s?.ledger_day} />
        ))}
        {!tasks.length && s && (
          <p className="text-[11px] text-muted-foreground">
            后端没有回任何一条 —— 这是配置读不到，不是「这几路都不存在」。
          </p>
        )}
      </div>

      {s && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          这一张表最后一次改动：{s.changed?.changed_at ?? DASH}
          {s.changed?.changed_by ? ` 由 ${s.changed.changed_by}` : ""}
          {neverChanged && " —— 从没被改过，现在走的是出厂默认（等价于原先写死在两个 .ps1 里的那套节奏）"}
        </p>
      )}
    </GlassCard>
  );
}

function TaskRow({ task, cfg, saved, setCfg, busy, onSave, day }: {
  task: BroadcastTask;
  cfg: BroadcastConfig;
  saved: BroadcastConfig;
  setCfg: (fn: (c: BroadcastConfig) => BroadcastConfig) => void;
  busy: string;
  onSave: (patch: BroadcastConfig) => void;
  day?: string | null;
}) {
  const enabledName = `${task.key}_enabled`;
  // 这一行的时点格叫什么由后端那一格给（`delta` 行是 `push_times`，推导出来的 heat 行没有）——
  // 前端不另起一套"谁算推导"的判断，否则后端改了归属，屏上还是老样子
  const timesName = task.times_field ?? "";
  const on = cfg[enabledName] !== false;
  const reg = task.registered;
  const block = blockingReason(task.key, cfg, task);
  const patch = taskPatch(task.key, cfg, task);
  const dirty = Object.keys(dirtyPatch(patch, saved)).length > 0;

  return (
    <div className={cn("rounded-lg border p-2",
      task.drift.length ? "border-destructive/50 bg-destructive/5"
        : !task.available ? "border-warning/50 bg-warning/5" : "border-border/60")}>
      <div className="flex flex-wrap items-baseline gap-2">
        <label className="flex items-center gap-1 text-xs font-medium">
          <input type="checkbox" checked={on} disabled={busy !== ""}
            aria-label={`${task.label} 开关`}
            onChange={e => setCfg(c => ({ ...c, [enabledName]: e.target.checked }))} />
          {task.label}
        </label>
        <code className="text-[10px] text-muted-foreground">{task.task_name}</code>
        <code className="text-[10px] text-muted-foreground" title="作业脚本（序号=本表行序）">{task.module.replace(/\./g, "/") + ".py"}</code>
        {!task.available && (
          <span className="rounded bg-warning/15 px-1 py-0.5 text-[10px] text-warning">
            执行体不在仓库（{task.module}）—— 保存会拒注册
          </span>
        )}
        <span className="ml-auto flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
          <span>任务 {reg?.state ?? "未注册"}</span>
          <span>下次 {runAtText(reg?.next) || DASH}</span>
          <span>上次 {runAtText(reg?.last) || "没跑过"}</span>
          {/* 结果码与"上次时间"同一个闸：新注册的任务 LastTaskResult 会是 0（从没跑过），
              单显示它等于谎称"上次成功" */}
          {!!runAtText(reg?.last) && <span>上次结果 {resultCodeText(reg?.result)}</span>}
        </span>
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        {task.key === 'watcher' ? (
          <>
            <Field cfg={cfg} setCfg={setCfg} name="watcher_start" label="起始" busy={busy} mono />
            <span className="text-[11px] text-muted-foreground">起</span>
            <Field cfg={cfg} setCfg={setCfg} name="watcher_interval_min" label="间隔分" busy={busy} mono />
            <Field cfg={cfg} setCfg={setCfg} name="watcher_duration_h" label="时长小时" busy={busy} mono />
          </>
        ) : timesName ? (
          <input value={String(cfg[timesName] ?? "")} disabled={busy !== "" || !task.editable}
            aria-label={`${task.label} 时点`}
            onChange={e => setCfg(c => ({ ...c, [timesName]: e.target.value }))}
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 font-mono text-[11px]" />
        ) : (
          <span className="text-[11px] text-muted-foreground">
            这一行没有自己的时点格：<b className="font-medium">{task.schedule}</b>
            （要改点，去动那一格的时点）
          </span>
        )}
        <button type="button" disabled={busy !== "" || !!block || (!dirty && !task.drift.length)}
          onClick={() => onSave(patch)}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
          <Save className="h-3 w-3" />保存并重注册
        </button>
      </div>
      {!!timesName && task.key !== 'watcher' && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          多个时点用逗号分隔，写成 <code>HH:MM</code> 或 <code>HH:MM:SS</code>；现在生效的是 {task.schedule}
          {timesName !== `${task.key}_times` &&
            <> —— 这一格（<code>{timesName}</code>）是六点共用的，概念热度跟着它各自 +10 秒</>}
        </p>
      )}

      {/* 拦下来要列"还差哪几样"；没改动也要说清按钮为什么是灰的，不许让人对着不动的按钮猜 */}
      {block && <p className="mt-1 text-[11px] text-destructive">保存拦下来：{block}</p>}
      {!block && !dirty && !task.drift.length && (
        <p className="mt-1 text-[11px] text-muted-foreground">这一行没有改动（改时点、间隔或开关才能保存）</p>
      )}

      {!!task.drift.length && (
        <p className="mt-1 text-[11px] text-destructive">
          与机器不一致：{task.drift.map(plain).join("；")} ——{" "}
          {task.editable
            ? "点上面那颗按钮按当前配置对齐"
            : "这一路现在没法从这里对齐（先补执行体或改判据，别当成保存没生效）"}
        </p>
      )}

      <p className={cn("mt-1 text-[11px]",
        (task.ledger?.failed ?? 0) > 0 ? "text-destructive" : "text-muted-foreground")}>
        {ledgerText(task, day) || "这一路已关，不注册任务"}
        {task.ledger?.last_err ? `　失败原因：${plain(task.ledger.last_err)}` : ""}
      </p>
      {!!task.ledger?.notes.length && (
        <p className="mt-0.5 text-[11px] text-warning">
          缺数/降级：{task.ledger.notes.map(n => `${n.what}×${n.n}${n.at ? ` @${n.at}` : ""}`).join("；")}
        </p>
      )}
    </div>
  );
}

function Field({ cfg, setCfg, name, label, busy, mono }: {
  cfg: BroadcastConfig; setCfg: (fn: (c: BroadcastConfig) => BroadcastConfig) => void;
  name: string; label: string; busy: string; mono?: boolean;
}) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
      {label}
      <input value={String(cfg[name] ?? "")} disabled={busy !== ""} aria-label={name}
        onChange={e => setCfg(c => ({ ...c, [name]: e.target.value }))}
        className={cn("w-20 rounded-lg border border-border bg-background px-2 py-1 text-[11px]",
          mono && "font-mono")} />
    </label>
  );
}
