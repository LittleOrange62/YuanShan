import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaveatText, Contract, ExplainBox, Panel, Provenance, Row } from "@/components/ui/DataPanel";
import { dash } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import { useNonce, useProbe } from "@/lib/probe";
import { useState } from "react";

/**
 * 数据质量闸（映射表 #12）。
 *
 * 与既有的 preflight（复盘前体检）**语义正交**，两套都保留：preflight 判「这场数据够不够格进复盘」，
 * 本页判「这批数据进库后坏没坏」。合成一张表的后果是**登记在册的入库规则**被复盘前的检查表顶掉。
 *
 * ⚠️ 这一屏最容易被画成假绿：登记规则的 `passed` 为 **null 表示本场没审过它**，不是「通过」。
 * 把 null 折进「通过」，就把「0/N 审过」报成「N/N 正常」—— 那正是质量闸唯一要防的事。
 * 所以三种结局分开数、分开筛、分开配色。
 */

interface RuleHit {
  rule_key?: string | null; name?: string | null; scope?: string | null; severity?: string | null;
  target?: string | null; check_kind?: string | null; threshold?: string | null;
  blocking?: boolean | null; enabled?: boolean | null; description?: string | null;
  passed?: boolean | null; actual?: string | null; expected?: string | null;
  sample?: string | null; run_at?: string | null; trade_date?: string | null;
}
interface Quality {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  rules?: RuleHit[] | null; failed?: RuleHit[] | null;
  rules_total?: number | null; rules_audited?: number | null; rules_blocking?: number | null;
  unregistered_results?: RuleHit[] | null; last_full_audit?: string | null; caveat?: string | null;
}

type Verdict = "pass" | "fail" | "unaudited" | "disabled";
const VERDICT: Record<Verdict, { text: string; cls: string }> = {
  pass: { text: "通过", cls: "bg-primary/12 text-primary" },
  fail: { text: "未通过", cls: "bg-destructive/15 text-destructive" },
  unaudited: { text: "本场未审", cls: "bg-muted text-muted-foreground" },
  disabled: { text: "规则已停用", cls: "bg-muted/60 text-muted-foreground/70" },
};

/** 停用的规则永远不会有结果，先判停用 —— 否则它被算进「本场未审」，看着像漏审 */
function verdictOf(r: RuleHit): Verdict {
  if (r.enabled === false) return "disabled";
  if (r.passed === true) return "pass";
  if (r.passed === false) return "fail";
  return "unaudited";
}

export function Quality() {
  const [nonce, refresh] = useNonce();
  const [only, setOnly] = useState<"all" | Verdict>("all");
  const q = useProbe<Quality>("/quality", nonce);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="质量闸" level={1}
        subtitle="入库后校验：登记规则逐条过。通过 / 未通过 / 本场未审是三种结局，未审不是通过。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新读取
        </button>} />

      <div className="space-y-4">
        <Panel title="审计覆盖与结果" state={q}
          note="规则读 config.quality_rule、结果读 operations.quality_result。覆盖率不满时，「最近一次全量审计」比「通过几条」更能说明问题。">
          {d => {
            const rules = d.rules ?? [];
            const unregistered = d.unregistered_results ?? [];
            const n = (v: Verdict) => rules.filter(r => verdictOf(r) === v).length;
            const shown = only === "all" ? rules : rules.filter(r => verdictOf(r) === only);
            return (
              <>
                <Row label="判定日" value={dash(d.date)} />
                <Row label="本场审了" value={`${dash(d.rules_audited)} / ${dash(d.rules_total)} 条登记规则（拦停级 ${dash(d.rules_blocking)} 条）`}
                  tone={(d.rules_audited ?? 0) < (d.rules_total ?? 0) ? "warn" : undefined} />
                <Row label="最近一次全量审计" value={dash(d.last_full_audit)} />
                <Row label="结果分布" value={`通过 ${n("pass")} · 未通过 ${n("fail")} · 本场未审 ${n("unaudited")} · 已停用 ${n("disabled")}`}
                  tone={n("fail") ? "bad" : undefined} />
                <CaveatText text={d.caveat} />
                <Provenance bits={[d.source]} />

                <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
                  {([["all", `全部 ${rules.length}`], ["fail", `未通过 ${n("fail")}`], ["unaudited", `未审 ${n("unaudited")}`], ["pass", `通过 ${n("pass")}`]] as const).map(([k, label]) => (
                    <button key={k} type="button" onClick={() => setOnly(k as "all" | Verdict)} aria-pressed={only === k}
                      className={cn("rounded px-2 py-0.5", only === k ? "bg-primary/15 text-primary" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                      {label}
                    </button>
                  ))}
                </div>

                <ul className="mt-2 space-y-1">
                  {shown.map((r, i) => {
                    const v = verdictOf(r);
                    return (
                      <li key={`${r.rule_key}-${i}`} className="rounded-lg border border-border p-2 text-[11px] leading-relaxed">
                        <div className="flex flex-wrap items-baseline gap-x-2">
                          <span className={cn("rounded px-1 py-0.5 text-[10px]", VERDICT[v].cls)}>{VERDICT[v].text}</span>
                          <span className="font-medium">{dash(r.name)}</span>
                          <span className="font-mono text-[10px] text-muted-foreground">{dash(r.rule_key)}</span>
                          {r.blocking === true && <span className="rounded bg-destructive/15 px-1 text-[10px] text-destructive">拦停</span>}
                          <span className="text-[10px] text-muted-foreground">{dash(r.severity)} · {dash(r.scope)} · {dash(r.check_kind)}</span>
                        </div>
                        <p className="text-[10px] text-muted-foreground">对象：{dash(r.target)}{r.threshold ? ` · 阈值 ${r.threshold}` : ""}</p>
                        {r.description && <p className="mt-0.5 text-muted-foreground">{r.description}</p>}
                        {v !== "unaudited" && (
                          <p className="mt-0.5">
                            <span className="text-muted-foreground">实测 </span><span className="font-mono">{dash(r.actual)}</span>
                            <span className="text-muted-foreground"> / 应为 </span><span className="font-mono">{dash(r.expected)}</span>
                            {r.run_at && <span className="text-muted-foreground"> · {r.run_at}</span>}
                          </p>
                        )}
                        {r.sample && <p className="break-all text-muted-foreground">样本：{r.sample}</p>}
                      </li>
                    );
                  })}
                  {shown.length === 0 && <li className="text-[11px] text-muted-foreground">这一类下没有条目（0 条）—— 是筛完为空，不是接口没交数据。</li>}
                </ul>

                {(unregistered.length > 0) && (
                  <div className="mt-3 border-t border-dashed border-border pt-2">
                    <p className="text-[11px] font-semibold text-warning">
                      另有 {unregistered.length} 条结果来自未登记规则（不在 config.quality_rule 里）
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {(unregistered).map((r, i) => (
                        <li key={`${r.rule_key}-${i}`} className="text-[11px] leading-relaxed">
                          <span className={cn("mr-1 rounded px-1 text-[10px]", r.passed === false ? "bg-destructive/15 text-destructive" : "bg-muted text-muted-foreground")}>
                            {r.passed === false ? "未通过" : r.passed === true ? "通过" : "未审"}
                          </span>
                          <span className="font-mono">{dash(r.rule_key)}</span>
                          <span className="text-muted-foreground"> · {dash(r.run_at)}{r.trade_date ? ` · 场次 ${r.trade_date}` : " · 非场次类作业（如备份自检）"}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      未登记的结果不参与「{dash(d.rules_total)} 条」的分母 —— 混进去会让覆盖率看着比实际高。
                    </p>
                  </div>
                )}
                <p className="mt-2 text-[11px] text-muted-foreground">
                  缺了哪几天要去补，看 <Link className="text-primary underline" to="/coverage">覆盖体检</Link>；
                  谁该跑没跑，看 <Link className="text-primary underline" to="/jobs">任务调度</Link>；
                  源本身活不活，看 <Link className="text-primary underline" to="/datasource">数据获取</Link>。
                </p>
              </>
            );
          }}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">这一页拦的是什么，以及现在拦不动的部分</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            登记在册的规则绝大多数拦的是同一类事故：<b className="text-foreground">静默的数据错误</b> ——
            单位从元变成千元、某天起少了三成行、涨跌停用今天的 ST 状态回算历史。
            这类问题不会让任何页面报错，只会让数字一直「看着正常」。
            所以<b className="text-foreground">「本场审了几条」本身就是故障信号</b>：审 0 条不是「货没问题」，是闸没落下。
          </p>
          {/* 说明与分工折进通用说明框（用户 10-14）。**上面第一段没跟着折**：
              「审 0 条不是货没问题，是闸没落下」是三态判读的告警，藏进折叠框等于没写；
              下面的 Contract（缺触发端点）同样留在屏上。 */}
          <ExplainBox title="与 preflight 的分工 · 为什么只报本场不累计历史">
            <p className="mt-1">
              还有两处闸口与本页语义正交，都保留、不许合并：preflight（复盘前体检）判的是
              <b className="text-foreground">「这场数据够不够格进复盘」</b>，本页判的是
              <b className="text-foreground">「这批数据进库之后坏没坏」</b>。合成一张表的后果，
              是这些入库规则被复盘前的那份检查表顶掉 —— 谁也没拦谁，但两把尺子不能互翻。
              （规则现在登记了几条，以上面那行「本场审了 N / M 条登记规则」为准，本页不另抄一份数。）
            </p>
            <p className="mt-1">
              另一处口径本页刻意不做：把历史结果按条数累计成「共未通过 N 条」。
              结果表存在同一批被重复写入的情况，未去重前相加会凭空放大数字 —— 所以只报<b className="text-foreground">本场</b>的分布。
            </p>
          </ExplainBox>
          <div className="mt-2">
            <Contract path="POST /api/quality/audit" source="按 config.quality_rule 逐条实跑并写 operations.quality_result" fields={[
              ["（触发端点）", "本页只读结果。要「现在重跑一次」得有个能落库的触发端点；没有就不摆一个点了没反应的按钮"],
              ["返回", "本次审了哪几条、跳过哪几条与跳过原因 —— 与 GET 同一份口径，别另算一遍"],
            ]} />
          </div>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
