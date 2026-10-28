import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { TrackTag } from '@/components/qd/Track';
import type { GroundedFinding, GroundedReport, ReportEvidence } from "@/lib/report-grounding";

/**
 * 这条依据出自哪一轨（双轨契约 02 §7.1）。两条路径，都**不猜**：
 * 1. 后端给了结构化字段 `record.track`；
 * 2. 零后端改造的过渡法 —— 证据卡 label 以 `【本地口径】` / `【quantdash 口径】`
 *    开头，前端把它摘出来变成角标，正文里不重复显示。
 * 两条都没有时就是不显示角标，绝不按 label 内容推断口径。
 */
const TRACK_PREFIX: { re: RegExp; track: "own" | "qd" }[] = [
  { re: /^\s*【\s*(?:本地口径|本轨)\s*】\s*/, track: "own" },
  { re: /^\s*【\s*(?:quantdash\s*口径|quantdash|QD)\s*】\s*/, track: "qd" },
];

function evidenceTrack(record: ReportEvidence): { track: "own" | "qd"; label: string } | null {
  if (record.track === "own" || record.track === "qd") return { track: record.track, label: record.label ?? "" };
  for (const { re, track } of TRACK_PREFIX) {
    const m = re.exec(record.label ?? "");
    if (m) return { track, label: (record.label ?? "").slice(m[0].length) };
  }
  return null;   // 认不出就是认不出，不默认算本地口径
}

export function EvidenceReferences({ finding, report }: { finding?: GroundedFinding; report?: GroundedReport }) {
  if (!finding || !report || !Array.isArray(finding.citations) || !Array.isArray(report.records)) return null;
  const cited = report.records.filter(r => finding.citations.includes(r.id));
  if (!cited.length) return <p className="text-xs text-warning">本段依据未能读取，请重新生成后核对。</p>;
  // Comparison inputs remain available beside the result; no model arithmetic.
  const inputIds = cited.flatMap(r => r.inputs || []);
  const records = [...cited, ...report.records.filter(r => inputIds.includes(r.id) && !cited.some(c => c.id === r.id))];
  return (
    <details className="mt-2 text-xs" data-report-evidence>
      <summary className="cursor-pointer rounded text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
        查看依据（{cited.length}）
      </summary>
      <div className="mt-2 space-y-2">
        {records.map(record => {
          const tag = evidenceTrack(record);
          return (
          <div key={record.id} className="rounded-lg border border-border bg-muted/30 p-3">
            <p className="font-semibold">
              {/* 02 §7.1：AI 同时读两轨时，用户要能看出这条依据出自哪一轨 */}
              {tag && <TrackTag track={tag.track} className="mr-1 align-middle" />}
              {tag ? tag.label : record.label} · {record.target_date}
            </p>
            <p className="mt-1 text-muted-foreground">{record.kind === "comparison" ? "程序计算的比较" : record.kind === "metric" ? "程序提取的读数" : "本次输入摘录（下文日期以原文为准）"}</p>
            <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed">{record.text}</pre>
            {record.context && record.context !== record.text && <p className="mt-2 whitespace-pre-wrap break-words text-muted-foreground">{record.context}</p>}
            <p className="mt-2 text-muted-foreground">{record.note}</p>
          </div>
          );
        })}
      </div>
    </details>
  );
}

export function GroundedAnalysis({ report }: { report?: GroundedReport }) {
  if (!report || report.version !== 1 || !Array.isArray(report.sections)) return null;
  return (
    <section className="space-y-3" aria-label="分项复盘与依据">
      <h2 className="text-sm font-bold">分项复盘与依据</h2>
      <p className="text-xs text-muted-foreground">数字从输入材料展示，AI 负责解释。引用已核对，推断仍需结合样本范围判断。</p>
      <div className="grid gap-4 lg:grid-cols-2">
        {report.sections.map(section => (
          <article key={section.key} className="glass rounded-2xl p-5">
            <h3 className="mb-3 font-semibold">{section.title}</h3>
            {section.findings.map((finding, i) => (
              <div key={i} className="mb-4 text-sm leading-relaxed">
                <div className="prose prose-sm max-w-none dark:prose-invert"><ReactMarkdown remarkPlugins={[remarkGfm]}>{finding.text}</ReactMarkdown></div>
                <EvidenceReferences finding={finding} report={report} />
              </div>
            ))}
          </article>
        ))}
      </div>
    </section>
  );
}
