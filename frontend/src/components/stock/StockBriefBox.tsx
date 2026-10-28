import { useEffect, useRef, useState } from "react";
import { Definitions, ExplainBox } from "@/components/ui/DataPanel";
import { TemplatePicker } from "@/components/ui/TemplatePicker";
import { hasLlm } from "@/lib/llm";
import { plain } from "@/lib/agent";
import { useTemplatePicker } from "@/lib/prompts";
import {
  autoRunDecision, contractSentence, metaSentence, missingPrerequisites, numericOf, receiptLines,
  rowMissingSentence, rowReceiptSentence, stageSentence,
} from "@/lib/stockBrief";
import { CALL_COUNT, runBrief } from "@/lib/stockBriefRun";
import type { FactsPayload } from "@/lib/stockFacts";

/**
 * 「个股信息」那一块里的 400 字公司说明（用户 2026-10-22：把下面「个股研究」那一整块喂给 AI，
 * 让它说清这家公司是干什么的、核心竞争力是什么、想象空间在哪、当前基本面如何，结论放回到这一格）。
 *
 * **2026-10-27 用户点名翻转了那一条「只有点按钮才发起」**：现在是
 * 「进这一页 → 先看库里有没有 → 没有就自己打这一趟 → 打完入库 → 以后进页面直接显示库里那份」，
 * 右上角那个按钮留着（有正文时它就是「重新生成」）。
 * 自动跑的四道闸（已有正文就不再打 / 前置不齐不打 / 话术没取到不打 / 刚失败过在冷却里不打）
 * 全部住 `lib/stockBrief.ts::autoRunDecision` 一处，这里只照它的判定接线 ——
 * 屏幕上必须说得出「这一轮为什么没自动打」，所以那句理由原样上屏，不当内部日志。
 *
 * 五条界面纪律：
 *  · **这一格摆的是 AI 生成的正文，不是本机读数** —— 那句限定写在标题旁边，不折进任何默认收起的框；
 *  · **它要出网、花你自己配的模型额度** —— 这话也摆在标题旁边，不藏；
 *  · **同一只票一次挂载只自动发一次**（`firedFor` 那道闸；切票会重新判，StrictMode 的重跑不会发第二趟）；
 *  · **缺数与失败摊在屏上**：写这份结论时哪几段没读到、最近一次重跑为什么失败，都在框外；
 *  · 按钮被拦下时列出"还差哪几样"并**不发注定被拒的请求**，不静默 disabled；冷却只管自动那一发，
 *    手动点下去照样打得动。
 */
export function StockBriefBox({ facts, refresh }: { facts: FactsPayload; refresh: () => void }) {
  const tpl = useTemplatePicker("stock_brief");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [auto, setAuto] = useState(false);
  const row = facts.brief ?? null;
  const hasBrief = !!row?.brief;
  const prereq = missingPrerequisites({
    hasLlm: hasLlm(), available: facts.available === true, numeric: numericOf(facts.code ?? ""),
  });
  const templateReady = !!tpl.current?.template;
  const decision = autoRunDecision({
    hasBrief, error: row?.error ?? null, failedAt: row?.failed_at ?? null,
    prereq, templateReady, promptsLoading: tpl.loading,
  });
  /** 一次挂载内、同一只票只自动发这一趟：`facts` 每次刷新都会重算判定，没有这道闸就会一遍遍地打。 */
  const firedFor = useRef("");

  const go = async (byAuto: boolean = false) => {
    if (busy) return;
    if (prereq.length > 0) {
      setNotice(`这一趟发不出去，还差：${prereq.join("；")}`);
      return;
    }
    if (!tpl.current?.template) {
      setNotice("话术没取到（`/api/prompts` 这一条通道没交回 stock_brief 那份）——"
        + "到「系统配置 → AI 话术」看一眼是不是这条通道空了");
      return;
    }
    setBusy(true); setAuto(byAuto); setNotice(null); setStage(null);
    try {
      const r = await runBrief({
        code: facts.code, numeric: numericOf(facts.code), facts,
        template: tpl.current.template, templateId: tpl.current.id,
        templateSource: tpl.current.source ?? tpl.current.kind,
        onStage: setStage,
      });
      if (r.kind === "rejected") setNotice(`正文没能入库：${r.reason}`);
      if (r.kind === "no_material") setNotice(`没有可喂的材料，这一趟没出网：${r.reason}`);
      refresh();          // 库里那一行是唯一真源：让上面那张卡自己把它读回来
    } catch (e) {
      setNotice(`${byAuto ? "这一页自己发起的这一趟失败" : "这一趟失败"}：`
        + `${e instanceof Error ? e.message : "原因未交"}。`
        + "上一次成功生成的结论仍在下面，没有被这次失败抹掉。");
      refresh();
    } finally {
      setBusy(false); setStage(null); setAuto(false);
    }
  };

  // 用户点名的那条：库里没有这一份就自己打一趟；有就只显示、不再出网。
  useEffect(() => {
    if (!decision.fire) return;
    const code = String(facts.code ?? "");
    if (!code || firedFor.current === code) return;
    firedFor.current = code;
    void go(true);
    // 依赖只放"会不会改变这一判定"的三样：busy 会把自己锁死在第一次，模板到位后要能补发一趟。
  }, [decision.fire, facts.code, templateReady]);

  const receipt = row?.material_segments ?? [];
  // 前置不齐那一族已经有「还差哪几样」那句话，别再把它复述一遍（同一条告警上屏两次是噪音）。
  const whyNotAuto = !decision.fire && !hasBrief && !busy && prereq.length === 0 ? decision.why : "";

  return (
    <div className="mt-2 rounded-lg border border-border/60 bg-card/40 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[12px] font-semibold text-foreground">
          这家公司是做什么的（AI 说明，{row?.word_limit ?? 400} 字以内）
        </h3>
        <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] text-primary">
          AI 生成的正文 · 不是本机判据
        </span>
        <span className="rounded bg-muted/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">
          库里没有这一份时，进这一页会自己出网打一趟（用你在「AI 探索 · 接入设置」里配的模型）
        </span>
        <TemplatePicker templates={tpl.templates} currentId={tpl.currentId} onPick={tpl.choose}
          scopeLabel="个股说明" />
        <button type="button" onClick={() => { void go(false); }}
          className="ml-auto rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          {busy ? (auto ? "自动生成中…" : "生成中…")
            : hasBrief ? "重新生成" : `让 AI 读下面「个股研究」那 ${CALL_COUNT} 段，写一份`}
        </button>
      </div>

      {busy && <p role="status" className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
        {auto && !stage ? "这一页自己发起的这一趟：" : ""}{stageSentence(stage, true)}
      </p>}
      {!!notice && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{notice}</p>}
      {!!whyNotAuto && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{whyNotAuto}</p>}
      {prereq.length > 0 && !busy && (
        <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">
          现在点它也会拦下来，还差：{prereq.join("；")}
          （这一页本来也不会自动打 —— 自动那一发同样要过这几道闸）
        </p>
      )}

      {hasBrief ? (
        <>
          {/* AI 正文原样摆（只剥成对粗体标记），不改写、不再摘要、不截断 */}
          <p className="mt-1 whitespace-pre-wrap text-[12px] leading-relaxed text-foreground">
            {plain(row?.brief ?? "")}
          </p>
          <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
            {metaSentence(row ?? {})}｜{contractSentence(row?.brief_chars, row?.word_limit)}
            {rowReceiptSentence(row) && <>｜{rowReceiptSentence(row)}</>}
          </p>
        </>
      ) : !busy ? (
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          还没生成过这一份（库里没有这一行，不是"生成出来是空的"）。
          它由模型读下面「个股研究」那一整块（行情 · 估值 · 研报 · 新闻 · 财务 · 公告 · 资金面）写成，
          这一页会自己发起这一趟 —— 要出网、用你在「AI 探索 · 接入设置」里配的那个模型，一次几十秒到几分钟。
          写成之后存进本机库，以后进这一页直接显示那一份，不再自动打；要换一份点右上角那个按钮。
        </p>
      ) : null}

      {/* 下面这两句是**缺数与失败**类声明，一律摊在框外（同全站那条红线：藏起来等于让屏幕说假话） */}
      {!!rowMissingSentence(row) && (
        <p className="mt-1 text-[11px] leading-relaxed text-warning">{rowMissingSentence(row)}</p>
      )}
      {!!row?.error && (
        <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">
          {hasBrief ? "上面这份是上一次成功生成的；" : "这一格还是空的；"}
          最近一次重跑失败（{String(row.failed_at ?? "").slice(0, 16)}）：{row.error}
        </p>
      )}

      {receipt.length > 0 && (
        <ExplainBox title="写这一份时看了哪些材料（每段喂几条 · 源本轮共几条）" count={receipt.length}>
          <ul className="space-y-1">
            {receiptLines(receipt).map((line, i) => (
              <li key={receipt[i].key}>
                <div>{line}</div>
                {/* 每段"这段是怎么读的"由后端逐段交出（`SEGMENTS[*].note`），页面不另编一句 */}
                {!!receipt[i].note && (
                  <div className="text-[10px] text-muted-foreground/80">{plain(receipt[i].note)}</div>
                )}
              </li>
            ))}
          </ul>
        </ExplainBox>
      )}
      {!!row?.caliber && (
        <ExplainBox title="这一格的口径 · 每个字段在说什么话">
          <p>{plain(row.caliber)}</p>
          {row.definitions && Object.keys(row.definitions).length > 0 && (
            <Definitions map={row.definitions} bare />
          )}
        </ExplainBox>
      )}
    </div>
  );
}
