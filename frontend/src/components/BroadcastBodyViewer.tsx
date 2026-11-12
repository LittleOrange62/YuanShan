import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { DocDrawer } from "@/components/ui/DocDrawer";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { bodiesTally, bodyClock, bodyDayCaption, bodyStatusText, neighborDay, taskNoBadge,
  type BroadcastBodies, type BroadcastBodyItem, type BroadcastTask } from "@/lib/intradayBroadcastReads";
import { loadBroadcastBodies } from "@/lib/intradayBroadcast";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 推送任务 →「按日期回看推送正文」（用户 2026-11-10：「5、9、10、11 这些分析的推送任务，
 * 都是先入库再推送或者推送的同时入库，并且在系统页面"任务推送"中，可以选择日期查看推送
 * 具体内容。默认都显示最近一天的数据」）。
 *
 * 三条取舍：
 *  · 读的是**当时落库的那一串**（`analytics.intraday_broadcast.body`），不重算 ——
 *    重算会拿到另一场数（源站翻页、盘后补数），而这一格的用途正是"那天群里收到的是什么"。
 *  · 默认落在**最近一个有正文的日子**，不是"今天"：早上打开时今天那几路还没到点，
 *    默认今天会看起来像"一条都没发过"。
 *  · ①⑥只落库那两路不列（后端 `PUSH_KEYS` 筛），否则同一份变化量会在①和③各出现一次。
 *
 * 号与路名从上面那张配置表的 `tasks` 借（同一份 `no`/`label`），这里不另抄一套名字。
 */
export function BroadcastBodyViewer({ tasks }: { tasks: BroadcastTask[] }) {
  const [s, setS] = useState<BroadcastBodies | null>(null);
  const [day, setDay] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);
  const [epoch, setEpoch] = useState(0);

  const load = useCallback((want?: string | null) => {
    setBusy(true); setErr("");
    return loadBroadcastBodies(want)
      .then(r => {
        if (!r) { setErr("推送正文接口没回 data —— 屏上这些正文不能当真"); return; }
        setS(r);
        setDay(r.day ?? "");
        setErr(r.readable === false ? (r.reason ?? "推送正文读不出来") : "");
      })
      .catch(e => setErr(e instanceof Error ? e.message : "推送正文读不出来"))
      .finally(() => setBusy(false));
  }, []);
  useEffect(() => { void load(); }, [load]);

  const days = s?.days ?? [];
  const items = s?.items ?? [];
  const tally = bodiesTally(items);
  const byKey = new Map(tasks.map(t => [t.key, t]));
  const older = neighborDay(days, day, -1);
  const newer = neighborDay(days, day, 1);

  /** 换一天：先把上一次读到的整份摘掉再读。留着旧内容会出"日期格已经改了、下面还是上一天的正文"
   *  这种读法（`s` 与正文是同一次读回来的，拆开展示就是假配对）。 */
  const pick = (want: string) => {
    setDay(want); setS(null);
    void load(want);
  };

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">推送正文（按日期回看）</h2>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <button type="button" disabled={busy || !older} onClick={() => older && pick(older)}
            aria-label="上一个有正文的日子"
            className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
            <ChevronLeft className="h-3 w-3" />上一天
          </button>
          <input type="date" value={day} disabled={busy} aria-label="回看日期"
            onChange={e => {
              const v = e.target.value;
              setDay(v);
              // 原生日期控件在挑定时给的是完整日子；手打时会给半截，半截不发请求（后端会回 400）
              if (DAY_RE.test(v)) pick(v);
            }}
            className="rounded-lg border border-border bg-background px-2 py-1 font-mono text-[11px]" />
          <button type="button" disabled={busy || !newer} onClick={() => newer && pick(newer)}
            aria-label="下一个有正文的日子"
            className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
            下一天<ChevronRight className="h-3 w-3" />
          </button>
          <button type="button" disabled={busy || !s?.latest || day === s?.latest}
            onClick={() => s?.latest && pick(s.latest)}
            className="rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
            最近一天
          </button>
          <button type="button" disabled={busy} onClick={() => void load(day || undefined)}
            className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
            <RefreshCw className="h-3 w-3" />重新读取
          </button>
          <button type="button" onClick={() => { setOpen(o => !o); setEpoch(e => e + 1); }}
            className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary">
            {open ? "全部收起" : "全部展开"}
          </button>
          <DocDrawer title="推送正文（按日期回看）">
            <p>
              这几路都是<b className="font-medium">先落库再推送</b>：算完先把正文写进
              <code>analytics.intraday_broadcast</code>（<code>body</code> 那一列），再发；发完再写一条
              <code>what=&apos;sent&apos;</code> 的回执。所以这一屏读到的就是<b className="font-medium">当时那一串原文</b>，
              不重算 —— 事后源站翻页或盘后补数都不会让这里改口，也是 <code>--resend</code> 补发时读的那一份。
            </p>
            <p className="mt-1">
              ⑤午间那一路另有一份结构化快照在 <code>analytics.noon_review</code>（家数、量能、情绪那几格），
              这里给的是<b className="font-medium">发出去的正文</b>，两者同一炉写的。
              ⑨每日复盘、⑩明日展望、⑪最强模式同一条路子：正文先落库，再发。
            </p>
            <p className="mt-1">
              默认落在<b className="font-medium">最近一个有正文的日子</b>而不是今天：早上打开时今天那几路还没到点，
              默认今天会看起来像"一条都没发过"。「上一天／下一天」在真有正文的日子之间跳（不按日历 ±1，
              周末与停机日跳过去是一片空白）。日期也可以直接手输，那天没内容会明说。
            </p>
            <p className="mt-1">
              每条正文上面那一行是三件不同的事：<b className="font-medium">钟点</b>是脚本写进库的时刻（精确到秒），
              <b className="font-medium">送达</b>看业务码判出来的 <code>delivered</code>（不是 HTTP 200），
              <b className="font-medium">字数</b>是那一串的字符数。「只有正文，库里没有发送回执」与「没发成」不是一回事：
              前者是库里有正文但没有 <code>sent</code> 那一行，后者是回执明说没送到（⑧微信镜像失败时写的是
              <code>wx_warn</code>，原话在下面红字里）。
            </p>
            <p className="mt-1">
              <b className="font-medium">①涨跌停池事件差</b>与<b className="font-medium">⑥交易体系榜取数</b>不在这张列表里：
              那两路只落库不发送，它们留在 <code>body</code> 里的是池摘要／取数摘要，不是发出去的正文
              （发送分别归③与⑦，列进来同一份内容会出现两次）。
            </p>
          </DocDrawer>
        </span>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{plain(err)}</p>}

      {!s && !err && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          正在读库里那一天真发出去的正文…… —— 在读出来之前**不说"这天没发过"**，
          那一句得等真拿到库里的行数才讲得出。
        </p>
      )}

      {s && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          现在看的是 <b className="font-medium text-foreground">{bodyDayCaption(s.day, s.latest)}</b>
          {s.readable !== false && <> · {tally.routes} 路 {tally.rounds} 条正文 · 共 {tally.chars} 字</>}
        </p>
      )}
      {!!days.length && (
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          库里 {days.length} 天有这几路的正文（{days[days.length - 1]} ~ {days[0]}）
        </p>
      )}

      {s && s.readable !== false && !items.length && !err && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          这一天库里没有这几路的正文 —— 可能还没到点、那几路关着，或者跑起来没算出内容。
          每一路到底是哪一种，看上面那张表它自己那一行的留痕。
        </p>
      )}

      <div className="mt-2 grid gap-1.5">
        {items.map(it => (
          <BodyRoute key={`${it.task}-${epoch}`} item={it} task={byKey.get(it.task)} open={open} />
        ))}
      </div>
    </GlassCard>
  );
}

function BodyRoute({ item, task, open }: {
  item: BroadcastBodyItem; task?: BroadcastTask; open: boolean;
}) {
  const last = item.rounds[item.rounds.length - 1];
  const failed = item.rounds.some(r => r.delivered === false || r.ok === false);
  return (
    <details open={open} className={cn("rounded-lg border p-2",
      failed ? "border-destructive/50 bg-destructive/5" : "border-border/60")}>
      <summary className="flex cursor-pointer flex-wrap items-baseline gap-2 text-xs">
        {!!taskNoBadge(task?.no) &&
          <span className="text-sm leading-none text-primary">{taskNoBadge(task?.no)}</span>}
        <span className="font-medium">{task?.label ?? item.task}</span>
        <code className="text-[10px] text-muted-foreground">{item.task}</code>
        <span className="ml-auto flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
          <span>{item.rounds.length} 条</span>
          <span>最后 {bodyClock(last?.at)}</span>
          <span className={cn(failed && "text-destructive")}>{last ? bodyStatusText(last) : ""}</span>
        </span>
      </summary>
      <div className="mt-1.5 grid gap-1.5">
        {item.rounds.map((r, i) => (
          <div key={`${r.at}-${i}`} className="rounded-lg border border-border/60 bg-background/40 p-2">
            <div className="flex flex-wrap items-baseline gap-2 text-[10px] text-muted-foreground">
              <span className="font-mono">{bodyClock(r.at)}</span>
              <span className={cn((r.delivered === false || r.ok === false) && "text-destructive")}>
                {bodyStatusText(r)}
              </span>
              <span>{r.chars} 字</span>
              {/* 正常那一路的 what 与路名同字（或 sent），不必念；不一样的才是降级/告警那一种 */}
              {r.kinds.filter(k => k !== "sent" && k !== item.task).map(k => (
                <code key={k} className="rounded bg-warning/15 px-1 py-0.5 text-warning">{k}</code>
              ))}
            </div>
            <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-foreground">
              {r.body}
            </pre>
            {!!r.err && <p className="mt-1 text-[11px] text-destructive">失败原话：{plain(r.err)}</p>}
          </div>
        ))}
      </div>
    </details>
  );
}