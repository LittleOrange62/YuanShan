import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { jobCardClass, jobLabelClass } from "@/lib/colors";
import { snapshotAgeText } from "@/lib/jobs";
import { loadChainSteps, rerunChainStep, type ChainStepRow, type ChainStepsState } from "@/lib/chainSteps";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";

const DASH = "—";

/**
 * 系统配置 →「加工段逐步结果（最近一次）＋ 单步补跑」。
 *
 * 为什么必须有这一格：链跑完那几十步，界面原先只给一句"追平失败"或干脆什么都不说，
 * 于是**失败没人知道、知道了也没地方补救**。用户 2026-10-08 要的就是这两件事：
 *  ① 每一步成没成、为什么没成（网络抖 / 无权限 / 按口径拒写 / 超时），跑了多久、第几轮；
 *  ② 对着那一步点一次"补跑"，不用重跑整条链。
 *
 * 措辞纪律：`rc=2/3` 里有一类是**按口径拒写**（比如今天不是交易日，即时截面证明不了
 * 它属于哪一场），那是正常收工不是故障 —— 原因由后端 `reason` 给，页面只转述，
 * 绝不自己把 `fail` 一律翻译成"失败/故障"。
 *
 * 步数的纪律（2026-10-17 记）：加工链现为 40 步（第 29 步 `fbshape`、紧跟第 30 步 `quadtrack`）。
 * 这个数字只写在注释里当坐标，**屏幕上那一格永远读台账实到的 `steps.length`** ——
 * 抄在界面上的总数每加一步就成一句假话（`lib/chainSteps.ts` 的注释与 `System.tsx` 的能力行同理）。
 */
export function ChainStepsPanel() {
  const [s, setS] = useState<ChainStepsState | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [showAll, setShowAll] = useState(false);

  const reload = useCallback(() => loadChainSteps()
    .then(r => { setS(r); setErr(""); })
    .catch(e => setErr(e instanceof Error ? e.message : "加工段台账读不到")), []);
  useEffect(() => { void reload(); }, [reload]);

  const rerun = async (name: string) => {
    setBusy(name); setNotice("");
    try {
      setNotice(await rerunChainStep(name));
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "补跑没被接受");
    } finally { setBusy(""); }
  };

  const steps = s?.steps ?? [];
  const bad = steps.filter(x => x.status === "fail");
  const refused = steps.filter(x => x.status === "refused");
  const good = steps.filter(x => x.status === "ok");
  const rows = showAll ? steps : steps.filter(x => x.status !== "ok");
  const tone = !s ? "quiet" : err ? "bad" : bad.length ? "bad" : "normal";
  const rt = s?.retry;

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">加工段逐步结果与单步补跑</h2>
        <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]", jobLabelClass(tone))}>
          {s ? (s.verdict === "DONE" ? "上一次全部成功"
            : bad.length ? `${bad.length} 步没成` : "没有故障（只有按口径拒写）") : DASH}
        </span>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取
        </button>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {notice && <p className="mt-2 text-[11px] text-warning">{notice}</p>}

      {s && !s.available && (
        <p className="mt-2 text-[11px] text-muted-foreground">{s.reason ?? "后端没有给出原因"}</p>
      )}

      {s?.available && (
        <>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            目标交易日 {s.target ?? DASH}　台账写于 {s.written_at ?? DASH}
            （{snapshotAgeText(s.written_at, Date.now())}）
            {s.ran_only?.length ? `　这次只用 --only 跑了：${s.ran_only.join("、")}` : ""}
          </p>
          {s.line && <p className="mt-1 text-[11px]">{s.line}</p>}
          {!!s.lagging_tables?.length && (
            <p className="mt-1 text-[11px] text-warning">
              ⚠ 跑完仍没追上目标日的表：{s.lagging_tables.join("、")}
            </p>
          )}
          {s.merged && (
            <p className="mt-1 text-[11px] leading-relaxed text-warning/90">{plain(s.merged)}</p>
          )}
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            共 {steps.length} 步进过账：成功 {good.length}、没成 {bad.length}、按口径拒写 {refused.length}。
            跳过没跑的步骤不进账（它们本来就没执行）。「补跑这一步」走的是加工段的同一把锁
            —— 15:59 那条链正在跑时会直接回"本次未触发"，不会起第二个采集进程。
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            按口径拒写不是故障：那是"归属日证明不了就不写"（例如今天不是交易日）。
            它不会被夜间阶梯重试，也不该被读成坏了 —— 要看原因请展开那一行。
          </p>

          {rt && (
            <p className="mt-1.5 rounded-lg border border-border/60 px-2 py-1.5 text-[11px] leading-relaxed">
              {rt.available === false
                ? <>重试阶梯：{plain(rt.reason ?? "后端没给原因")}</>
                : <>失败重试阶梯：
                  {rt.enabled === false ? "关着（关掉后不会自己恢复，要有人再打开）" : "开着"}
                  {" · 待补 "}{rt.pending?.length ?? 0} 步
                  {rt.pending?.length ? `（${rt.pending.join("、")}）` : ""}
                  {" · 下一档 "}{rt.next_at ?? "没有（档期用完或没有要补的）"}
                  {rt.gave_up ? " · 这一场已放弃，等下一次收盘链一起补" : ""}
                  {" · 档期 "}{(rt.lanes ?? []).filter(x => !x.broken)
                    .map(x => `${x.label}${x.ran_at ? "(已用)" : ""}`).join(" / ")}
                  {` · 到点后认 ${rt.catch_up_min ?? 0} 分钟内的迟到`}
                  <br />
                  <span className="text-muted-foreground">
                    时点与档数在下方「常驻循环与定时任务」那一格的<em>重试档期</em>里改（逗号分隔 HH:MM，
                    早于 12:00 的算次日）；每一档跑之前都会重读台账，已经补成的不会再跑。
                  </span>
                </>}
            </p>
          )}

          {(bad.length + refused.length) > 0 && !showAll && (
            <button type="button" onClick={() => setShowAll(true)}
              className="mt-2 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary">
              看全部 {steps.length} 步（含成功的 {good.length} 步）
            </button>
          )}

          <div className="mt-2 grid gap-1.5">
            {rows.map(st => <StepRow key={st.name} st={st} busy={busy} onRerun={rerun} />)}
            {!rows.length && (
              <p className="text-[11px] text-muted-foreground">台账里一步都没有 —— 上一次可能全被跳过了。</p>
            )}
          </div>
        </>
      )}
    </GlassCard>
  );
}

function StepRow({ st, busy, onRerun }: {
  st: ChainStepRow; busy: string; onRerun: (name: string) => void;
}) {
  const ok = st.status === "ok";
  const refused = st.status === "refused";
  const tone = ok ? "normal" : refused ? "quiet" : "bad";
  return (
    <div className={cn("rounded-lg border p-2", ok || refused ? "border-border/60" : jobCardClass("bad"))}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs font-medium">{st.label ?? st.name}</span>
        <code className="text-[11px] text-muted-foreground">{st.name}</code>
        <span className={cn("rounded px-1 py-0.5 text-[10px]", jobLabelClass(tone))}>
          {ok ? "成功" : refused ? `按口径拒写 rc=${st.rc ?? DASH}（不算故障）` : `没成 rc=${st.rc ?? DASH}`}
        </span>
        {st.core && <span className="text-[10px] text-muted-foreground">骨干</span>}
        <button type="button" disabled={busy !== ""} onClick={() => onRerun(st.name)}
          className="ml-auto rounded-lg border border-border px-2 py-0.5 text-[11px] hover:border-primary/40 hover:text-primary disabled:opacity-60"
          title={refused ? "这一格是按口径拒写的（归属日证明不了就不写）；夜间阶梯不会重试它，手动补跑要自己确认归属日可信"
            : "只重跑这一步：走 --only，与 15:59 那条链抢同一把锁"}>
          {busy === st.name ? "已提交" : "补跑这一步"}
        </button>
      </div>
      <div className="mt-1 grid gap-0.5 text-[11px] text-muted-foreground">
        <div>用时 {st.seconds ?? DASH} 秒{typeof st.rounds === "number" && st.rounds > 1 ? `　第 ${st.rounds} 轮（前面重试过）` : ""}
          　{st.module ? `模块 ${st.module}` : ""}</div>
        {st.reason && <div className={ok ? "" : refused ? "" : "text-warning"}>原因：{st.reason}</div>}
        {!ok && st.tail && (
          <details className="text-[11px]">
            <summary className="cursor-pointer">看输出末尾</summary>
            <pre className="mt-1 whitespace-pre-wrap break-all rounded bg-muted/40 p-1.5">{st.tail}</pre>
          </details>
        )}
      </div>
    </div>
  );
}
