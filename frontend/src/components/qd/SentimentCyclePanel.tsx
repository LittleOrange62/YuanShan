import { AlertTriangle } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { plain, safeRecord } from "@/lib/agent";
import { STAGE_COLUMNS, dash, num, stageMismatch, stageReasons } from "@/lib/qd";
import type { Distribution, QdState, SentimentCycle } from "@/lib/qd";
import { ComputedLine, DeviationList, DistBars, PanelState, StagePill, TrackSection } from "./Track";

/**
 * 02 §2 情绪周期：三个 stage 列必须一起显示。
 *
 * quantdash 自己的页面 / AI / 飞书用的是**三套互相矛盾的阈值**，这是要呈现的
 * 事实而不是我们的 bug —— 所以绝不收敛成一个"最终阶段"。
 * 本轨目前**没有**阶段算法（情绪五档只有 UI 没有计算），这一格如实显示 `—`。
 */

const STAGE_CALIBER =
  "三列来自同一张 qd.sentiment_cycle 表的不同判定口径：\n" +
  "  stage_ui      quantdash 网页端阈值\n" +
  "  stage_mcp     quantdash 喂给自家 AI 的 MCP 阈值\n" +
  "  stage_feishu  quantdash 飞书机器人阈值\n" +
  "三者可以互相矛盾，且各自的命中条件都要能展开看。\n" +
  "判定强度 = 阶段基数 + 风险加成 + 量能加成 的确定性查表加和，理论最大 89；\n" +
  "⚠️ 它不是统计置信度，「判定强度 65」不代表 65% 的把握。";

/** 判定强度：文案必须是「判定强度」，不是「置信度」（02 §2 第 4 条） */
function Strength({ value }: { value?: number | null }) {
  const shown = num(value, 0);
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-2xl font-extrabold tabular-nums">{shown}</span>
      <span className="text-[11px] text-muted-foreground">/ 89 判定强度</span>
      <Caliber text="非统计置信度：由阶段/风险/量能三项查表相加得出，最大 89。数值高只说明它命中了更「热」的组合条件，不代表这个判断有对应的成功概率。" />
    </span>
  );
}

export function SentimentCyclePanel({ state, dist }: {
  state: QdState<SentimentCycle>;
  dist: QdState<Distribution>;
}) {
  return (
    <PanelState<SentimentCycle> state={state}>{data => {
      const qd = data.qd ?? null;
      const own = data.own ?? null;
      const reasons = stageReasons(qd);
      const mismatch = stageMismatch(qd);
      const metrics = safeRecord<unknown>(qd?.metrics);
      return (
        <div className="space-y-3">
          {/* 本轨：没有阶段算法就如实空着，绝不拿 qd 的值冒充 */}
          <TrackSection track="own" title="情绪阶段" provenance={false}
            hint="本轨（analytics.*）目前只有指标，没有把指标映射成阶段的确定性算法。">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-lg border border-dashed border-border px-2 py-1 text-[12px] font-semibold text-muted-foreground">
                {own && own.stage != null ? plain(String(own.stage)) : "—"}
              </span>
              <span className="text-[11px] text-muted-foreground">
                {own ? "本轨已给出阶段值（口径见 deviation）" : "本轨暂无阶段算法"}
              </span>
            </div>
          </TrackSection>

          <TrackSection track="qd" title="三个判定口径并排"
            hint="同一份指标、三套阈值，判出来的阶段可以互相不同。">
            <ComputedLine env={data} />
            {qd ? (
              <>
                <div className="mb-2 flex items-center gap-1.5">
                  <span className="text-[11px] font-semibold text-muted-foreground">阶段判定口径</span>
                  <Caliber text={STAGE_CALIBER} />
                </div>
                <div className="flex flex-wrap gap-3">
                  {STAGE_COLUMNS.map(col => (
                    <StagePill key={col.column} label={col.label}
                      stage={qd[col.key] as string | null | undefined}
                      reason={reasons?.map[col.column]} />
                  ))}
                </div>

                {mismatch && (
                  <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-muted/50 p-2 text-[11px] leading-relaxed">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>⚠ 三者不一致 —— 原项目页面与 AI 读到的阶段本就不同。这里不做取舍，全部摆出来。</span>
                  </p>
                )}

                <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-2 border-t border-dashed border-primary/25 pt-2">
                  <div>
                    <Strength value={qd.confidence} />
                  </div>
                  <Field label="风险档" value={dash(qd.risk_level)} />
                  <Field label="量能状态" value={dash(qd.volume_state)} />
                </div>

                {Object.keys(metrics).length > 0 && (
                  <div className="mt-3 border-t border-dashed border-primary/25 pt-2">
                    <div className="mb-1 flex items-center gap-1.5">
                      <span className="text-[11px] font-semibold text-muted-foreground">判定所用指标</span>
                      <Caliber text="字段名里带 _pct 的是百分比、带 _rate 的是比率（分母已写进字段名，见 01 规范第四条）。NULL 与 0 是两件事：这里显示 — 的是 NULL。" />
                    </div>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3 lg:grid-cols-4">
                      {Object.entries(metrics).map(([k, v]) => (
                        <div key={k} className="min-w-0">
                          <div className="truncate font-mono text-[10px] text-muted-foreground/80" title={k}>{k}</div>
                          <div className="text-[13px] font-semibold tabular-nums">
                            {typeof v === "number" ? num(v, Math.abs(v) < 1 ? 3 : 2) : dash(v)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <DeviationList items={data.qd?.deviation} />
                <DistributionBlock dist={dist} />
              </>
            ) : (
              <p className="text-[12px] text-muted-foreground">后端没给 <code>qd</code> 这一键（契约 §8 要求 <code>own</code> / <code>qd</code> 两键，缺失即视为该轨无数据，不做兜底）。</p>
            )}
          </TrackSection>
        </div>
      );
    }}</PanelState>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[13px] font-semibold">{value}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  );
}

/**
 * 区间阶段分布。
 *
 * 只呈现，不解释成"好坏"：三套链都把"退潮"排在第一位、且带着"修复率<20"这类
 * 常态条件，于是排在其后的档位几乎轮不到 —— 这是 quantdash 优先级链自身的性质。
 * 本实现**没有改判定顺序**，所以这里不能写成"已修正"。
 */
const DIST_CALIBER =
  "按后端给出的优先级顺序排列（1 = 最先命中）。\n" +
  "零值档显式保留：某档 0 次是结论，不是缺数据。\n" +
  "本实现未改动 quantdash 的判定顺序，所以「退潮占大头 / 冰点从未命中」要按它原逻辑理解。";

function DistributionBlock({ dist }: { dist: QdState<Distribution> }) {
  return (
    <PanelState<Distribution> state={dist} emptyReason="区间分布取不到">{data => {
      const cols = STAGE_COLUMNS.map(c => c.column);
      const counts = data.stage_distribution ?? {};
      const prio = data.stage_priority ?? {};
      const never = data.never_seen_stages ?? {};
      const total = data.sample_total?.stage ?? null;
      const first = counts[cols[0]] ?? {};
      const top = Object.entries(first).sort((a, b) => b[1] - a[1])[0];
      return (
        <div>
          {top && total ? (
            <p className="mb-1 text-[11px] leading-relaxed text-muted-foreground">
              近 <b className="text-foreground">{data.days ?? "—"}</b> 个已判定交易日
              （{dash(data.from_date)} ~ {dash(data.to_date)}）里，{STAGE_COLUMNS[0].label} 有{" "}
              <b className="text-foreground">{top[1]} 场判为「{top[0]}」</b>
              （占 {((top[1] / total) * 100).toFixed(1)}%）。
              下面的序号是优先级顺序：排前面的档会截走后面的档。
            </p>
          ) : null}
          {cols.map(col => (
            <DistBars key={col}
              title={`${STAGE_COLUMNS.find(c => c.column === col)?.label ?? col} · 阶段分布`}
              order={prio[col] ?? []} counts={counts[col] ?? {}}
              sampleTotal={total} neverSeen={never[col]} caliber={DIST_CALIBER} />
          ))}
          <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground/80">
            统计口径来源：{dash(data.basis)}
            {data.observed_sources?.length ? ` · source 标记 ${data.observed_sources.join(" / ")}` : ""}
          </p>
        </div>
      );
    }}</PanelState>
  );
}
