import { AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { plain, safeArray } from "@/lib/agent";
import { TIE_BREAK_COPY, TIE_BREAK_UNKNOWN, dash, pct } from "@/lib/qd";
import type { BoardLadder, Distribution, LadderMember, QdState } from "@/lib/qd";
import { ComputedLine, DeviationList, DistBars, PanelState, TrackSection } from "./Track";

/**
 * 02 §3 + §4 连板天梯。
 *
 * 这一屏的核心不是好看，是**把"数据没有"显示出来**：quantdash 原版在没有分钟线时
 * 用哈希函数编一个封板时间（`derivePseudoTime`，D-28），页面上完全看不出破绽。
 * 所以 `seal_time_available=false` 时这里整列灰底 + 顶部横幅，视觉上要无法忽略。
 */

/**
 * 横幅不写死"仅某天可得"：分钟线覆盖会变，写死的日期过两天就成了假话。
 * 具体覆盖几场，由下面的区间分布给数字（seal_time 命中几场 / 区间共几场）。
 */
const SEAL_BANNER =
  "⚠ 本场次封板时间不可得。本地分钟线只覆盖极少数交易日，其余全部没有封板时刻；\n" +
  "  quantdash 原版在这种情况下用代码哈希编一个时间出来，我们不编 ——\n" +
  "  并列的龙头改按成交额选出，这与它的原口径不等价，见「龙头是怎么选出来的」与下方区间分布。";

/** tie-break 取值 → 短标签（长文案在龙头那一行，见 TIE_BREAK_COPY） */
const TIE_SHORT: Record<string, string> = {
  seal_time: "seal_time（原口径）",
  amount_no_seal_time: "amount_no_seal_time（按额重选）",
  code_asc: "code_asc（代码兜底）",
  no_candidate: "no_candidate（无涨停）",
};

export function BoardLadderPanel({ state, dist }: {
  state: QdState<BoardLadder>;
  dist: QdState<Distribution>;
}) {
  return (
    <PanelState<BoardLadder> state={state}>{data => {
      const sealOk = data.seal_time_available === true;
      const tiers = safeArray<NonNullable<BoardLadder["tiers"]>[number]>(data.tiers);
      return (
        <div className="space-y-3">
          {!sealOk && (
            <p className="flex items-start gap-1.5 rounded-lg border border-dashed bg-muted/50 p-2 text-[11px] leading-relaxed">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="whitespace-pre-line">{plain(SEAL_BANNER)}</span>
            </p>
          )}

          <TrackSection track="qd" title={`板级梯队（含首板，共 ${tiers.length} 档 / ${tiers.reduce((n, t) => n + safeArray(t.members).length, 0)} 只）`}
            hint="板级行含 board_tier=1 的首板。本轨没有对应行就留空 —— 不会把首板并进「七板以上」（quantdash 原版这么错过，见 D-12）。">
            <ComputedLine env={data} />
            {tiers.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">后端给了空 <code>tiers</code>：当日无涨停，或该日尚未入库。</p>
            ) : (
              <div className="space-y-3">
                {tiers.map((tier, i) => {
                  const members = safeArray<LadderMember>(tier.members);
                  return (
                    <div key={`${tier.board_tier ?? "t"}-${i}`}>
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-bold tabular-nums",
                          tier.is_highest_tier ? "bg-primary text-primary-foreground" : "bg-primary/15 text-primary")}>
                          {tier.board_tier == null ? "—" : `${tier.board_tier} 板`}
                        </span>
                        <span className="text-[11px] text-muted-foreground">{plain(tier.tier_label ?? tier.label ?? "")}</span>
                        <span className="text-[11px] text-muted-foreground/70">
                          {tier.stock_count ?? members.length} 只{tier.stock_count != null && tier.stock_count !== members.length ? `（本屏只列出 ${members.length} 只）` : ""}
                        </span>
                      </div>
                      {members.length === 0 ? (
                        <p className="text-[11px] text-muted-foreground/70">该档无成员。</p>
                      ) : (
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                          {members.map((m, j) => <MemberCard key={`${m.security_code ?? m.code ?? "m"}-${j}`} m={m} sealOk={sealOk} />)}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            <Leader leader={data.leader ?? null} />
            <TieBreakDistribution dist={dist} />
            <DeviationList items={data.deviation} />
          </TrackSection>

          <TrackSection track="own" title="本轨天梯在别处" provenance={false}
            hint="本地口径的梯队按「前一交易日收盘板数」固定分组看今日表现，与 qd 轨的「今日收盘板数分档」不是同一件事，因此不在这里合并成一张表。">
            <div className="flex flex-wrap gap-2 text-[12px]">
              <Link className="rounded-lg bg-primary/10 px-2 py-1 text-primary hover:bg-primary/20" to="/">首页 · 盘面数据（昨日连板股今日表现，含首封时间）</Link>
              <Link className="rounded-lg bg-primary/10 px-2 py-1 text-primary hover:bg-primary/20" to="/first-board">首板分析</Link>
            </div>
          </TrackSection>
        </div>
      );
    }}</PanelState>
  );
}

function MemberCard({ m, sealOk }: { m: LadderMember; sealOk: boolean }) {
  // DDL 列名是 security_code / stock_name；code / name 是端点可能用的别名。两者都是明确命名，不是猜
  const name = m.stock_name ?? m.name;
  const code = m.security_code ?? m.code;
  // 逐行的 seal_time_available 比屏级更准，给了就以它为准
  const rowSeal = m.seal_time_available ?? sealOk;
  const tags = [
    m.is_one_word === true && { text: "一字", tone: "bg-muted text-muted-foreground" },
    m.limit_up_pct_regime != null && {
      text: `${m.limit_up_pct_regime}%`, tone: "bg-muted text-muted-foreground",
      title: "该股适用的涨停幅度制度（10% / 20% / 30% / 5%），不是涨幅",
    },
    // 本轨扩展：quantdash 没有这个字段，角标必须注明来源（02 §3）
    m.is_turnover_board === true && {
      text: "换手+", tone: "border border-dashed border-primary/40 text-primary/80",
      title: "非 quantdash 口径：本项目按换手率 ≥5% 判定",
    },
  ].filter(Boolean) as { text: string; tone: string; title?: string }[];

  return (
    <div className="rounded-lg border border-border bg-background/50 p-2">
      <div className="flex min-w-0 items-baseline gap-1.5">
        <span className="truncate text-[13px] font-semibold">{dash(name)}</span>
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{dash(code)}</span>
        <span className="ml-auto shrink-0 text-[11px] font-bold tabular-nums">
          {m.board_count == null ? "—" : `${m.board_count} 板`}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        {/* NULL 就显示 —，绝不按代码前缀硬推 10/20/30 冒充（D-14） */}
        <span className={cn("font-semibold tabular-nums", pctColor(m.pct_change_actual ?? null))}>
          {pct(m.pct_change_actual)}
        </span>
        {tags.map(t => (
          <span key={t.text} title={t.title} className={cn("rounded px-1 py-0.5 text-[10px]", t.tone)}>{t.text}</span>
        ))}
      </div>
      {/* 封板时间列：不可得时灰底 + 明确文案，不显示 -- */}
      <div className={cn("mt-1 flex items-center gap-1 rounded px-1 py-0.5 text-[10px]",
        rowSeal ? "text-muted-foreground" : "bg-muted text-muted-foreground/70")}>
        首封
        <span className="font-mono font-semibold tabular-nums">
          {rowSeal ? dash(m.first_seal_time) : "封板时间不可得"}
        </span>
      </div>
      {/* 行业 / 概念：NULL 就整块不显示，不填 市场热点 / 连板 / Unknown（D-13） */}
      {(m.industry_name || m.concept_name) && (
        <div className="mt-1 flex flex-wrap gap-1 border-t border-dashed border-border pt-1 text-[10px] text-muted-foreground">
          {m.industry_name && <span className="truncate">{plain(m.industry_name)}</span>}
          {m.concept_name && <span className="truncate">· {plain(m.concept_name)}</span>}
        </div>
      )}
    </div>
  );
}

/**
 * 龙头 tie-break 的区间分布。
 *
 * 这块的存在理由：单场次的降级文案容易被划过去。把区间摊开后，
 * "按成交额重选"占多少场、真正按封板时间选出的有几场，是一眼能看见的数字。
 */
function TieBreakDistribution({ dist }: { dist: QdState<Distribution> }) {
  return (
    <PanelState<Distribution> state={dist}>{data => {
      const counts = data.tie_break_distribution ?? {};
      const total = data.sample_total?.tie_break ?? null;
      const original = counts.seal_time ?? 0;
      const replaced = counts.amount_no_seal_time ?? 0;
      return (
        <div>
          {total ? (
            <p className="mb-1 text-[11px] leading-relaxed text-muted-foreground">
              近 {data.days ?? "—"} 场里，按 quantdash 原口径（最早封板时间）选出龙头的只有{" "}
              <b className="text-foreground">{original} 场</b>，
              按成交额重选的有 <b className="text-foreground">{replaced} 场</b>
              （{((replaced / total) * 100).toFixed(1)}%）。
              <b>这不是过渡状态</b>：分钟线不补齐，这个比例就不会变。
            </p>
          ) : null}
          <DistBars title="龙头是怎么选出来的 · 区间分布"
            order={Object.keys(TIE_SHORT)} counts={counts} sampleTotal={total}
            labelOf={k => TIE_SHORT[k] ?? k}
            caliber={"四种选法的含义与降级规则见契约 02 §4。\nseal_time 才是 quantdash 原口径；其余三种是本实现为了不留空做的确定性选择。"} />
          <p className="mt-1 text-[10px] text-muted-foreground/80">统计口径来源：{dash(data.basis)}</p>
        </div>
      );
    }}</PanelState>
  );
}

function Leader({ leader }: { leader: NonNullable<BoardLadder["leader"]> | null }) {
  if (!leader) {
    return <p className="mt-2 text-[11px] text-muted-foreground">后端未给 <code>leader</code>（不是"当日无龙头"，是这一项没算出来）。</p>;
  }
  const copy = TIE_BREAK_COPY[String(leader.leader_tie_break ?? "")] ?? TIE_BREAK_UNKNOWN;
  const nextMissing = leader.leader_next_close_pct == null;
  return (
    <div className={cn("mt-3 border-t border-dashed border-primary/25 pt-2", copy.degraded && "opacity-70")}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[11px] font-semibold text-muted-foreground">龙头</span>
        <span className={cn("text-[13px] font-bold", copy.degraded && "italic")}>{dash(leader.leader_stock_name ?? leader.name)}</span>
        <span className="font-mono text-[10px] text-muted-foreground">{dash(leader.leader_security_code ?? leader.code)}</span>
        {(leader.leader_board_count ?? leader.board_count) != null && (
          <span className="text-[11px] tabular-nums">{leader.leader_board_count ?? leader.board_count} 板</span>
        )}
        {leader.leader_status_label && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">{plain(leader.leader_status_label)}</span>}
      </div>
      <p className={cn("mt-1 text-[11px] leading-relaxed", copy.degraded ? "italic text-muted-foreground" : "text-muted-foreground")}>
        龙头是怎么选出来的：{plain(copy.text)}
        {!(leader.leader_tie_break && String(leader.leader_tie_break) in TIE_BREAK_COPY) &&
          <span className="ml-1">（后端值：<code>{dash(leader.leader_tie_break)}</code>）</span>}
      </p>
      {nextMissing && (
        <p className="mt-0.5 text-[10px] text-muted-foreground/80">
          次日数据未到 —— 上面那个状态标签还不是判断结论。
        </p>
      )}
    </div>
  );
}
