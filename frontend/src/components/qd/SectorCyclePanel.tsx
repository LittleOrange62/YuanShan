import { SortHeader } from '@/components/ui/SortHeader';
import { useTableSort } from '@/lib/useSort';
import { PAGE_SIZES, clampPage, pageCount, pageSentence, slicePage } from '@/lib/paging';
import { useState } from "react";
import { Caliber } from "@/components/ui/Caliber";
import { FLAT_TEXT, heatBg, pctColor, strengthColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { finite, plain, safeArray, safeRecord } from "@/lib/agent";
import { dash, num, pct } from "@/lib/qd";
import type { OwnSector, OwnSectorCycle, QdState, RotationCell, RotationRow, SectorCycle, SectorFollow } from "@/lib/qd";
import { ComputedLine , DeviationList , PanelState , TrackSection } from "./Track";
import { LeaderFollowPanel } from "./LeaderFollow";

/**
 * 02 §5 板块周期：**两套东西不是同一个东西**，所以分两块、不合并成一张表。
 *
 * 本轨（analytics.ignition）回答"这个板块处在启动的哪个阶段"；
 * qd 轨（qd.sector_persistence）回答"领涨板块换了几手、谁连庄最久、强度在增还是减"，
 * quantdash 的板块周期**根本没有阶段枚举**。
 */

const OWN_STAGES = ["ignite", "follow", "coil", "fade", "none"] as const;
const STAGE_CN: Record<string, string> = {
  ignite: "点火", follow: "跟随", coil: "蓄势", fade: "退潮", none: "未触发",
};
const ELEMENTS = ["squeeze", "position", "ignition", "volume", "breadth"] as const;

const OWN_CALIBER =
  "五档生命周期阶段，由后端算：score 由高到低、同级按后端顺序，前端**不重排**\n" +
  "（重排会把「点火」挤出首屏）。\n" +
  "五要素 squeeze/position/ignition/volume/breadth 是打分依据，NULL 表示这一项\n" +
  "算不出来（缺涨跌家数之类），不是 0 分。\n" +
  "synthetic=true 的是「昨日涨停/昨日突涨/板块趋势/最近情绪指数」这类通达信行为\n" +
  "统计板块，不是可交易题材 —— 按「打标不裁」保留但默认收进开关。";

export function SectorCyclePanel({
  own, qd, follow, ownDate,
}: {
  own: QdState<OwnSectorCycle>;
  qd: QdState<SectorCycle>;
  follow: QdState<SectorFollow>;
  ownDate?: string | null;
}) {
  const [showSynthetic, setShowSynthetic] = useState(false);
  /** 这一张表原来是**硬截前 40 行**（共约 1,068 行），40 行一屏本来就看着累，
   *  而且第 41 行往后的人根本翻不到。改成全站那一份分页：一页 25 行，翻得完。 */
  const [ownPage, setOwnPage] = useState(1);
  const scSort = useTableSort();
  return (
    <div className="space-y-3">
      <TrackSection track="own" title="生命周期阶段（本轨）" provenance={false}
        hint="回答「这个板块处在启动的哪个阶段」。">
        <PanelState<OwnSectorCycle> state={own}>{data => {
          const all = safeArray<OwnSector>(data.sectors);
          const counts: Record<string, number> = {};
          for (const s of all) counts[s.stage ?? "none"] = (counts[s.stage ?? "none"] ?? 0) + 1;
          // 01 规范第五条「打标不裁」：风格类与行为统计板块默认收起，但数据不删。
          const hidden = (s: OwnSector) => s.synthetic === true || s.sector_type === "风格";
          const tradeable = all.filter(s => !hidden(s));
          const shown = showSynthetic ? all : tradeable;
          const types: Record<string, number> = {};
          for (const s of all) types[s.sector_type ?? "—"] = (types[s.sector_type ?? "—"] ?? 0) + 1;
          // 排序在分页之前（否则每页都在换口径）；页数、页码、切片一律走 lib/paging 那一份
          const sorted = scSort.apply(shown);
          const pageSize = PAGE_SIZES[0];
          const pages = pageCount(sorted.length, pageSize);
          const cur = clampPage(ownPage, pages);
          const paged = slicePage(sorted, cur, pageSize);
          /** 换排序档就回到第一页：第 3 页的行号在另一把尺子下没有意义。 */
          const sortBy = (key: string) => { setOwnPage(1); scSort.toggle(key); };
          return (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                {OWN_STAGES.map(k => (
                  <span key={k} className={cn("rounded px-1.5 py-0.5 tabular-nums",
                    counts[k] ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground/70")}>
                    {STAGE_CN[k]} {counts[k] ?? 0}
                  </span>
                ))}
                <span className="ml-auto text-muted-foreground/80">
                  分母 {all.length} 个板块（{Object.entries(types).map(([k, v]) => `${k} ${v}`).join(" / ")}）
                </span>
              </div>

              <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <input type="checkbox" checked={showSynthetic}
                  onChange={e => { setShowSynthetic(e.target.checked); setOwnPage(1); }} />
                显示风格与行为统计板块（{all.length - tradeable.length} 个）
                <Caliber text="这些不是可交易题材：「昨日突涨正在蓄势」是同义反复，风格类（如「中小银行」）也不是一篮子可买的票。默认收起，但数据没有被裁掉（打标不裁），阶段计数与分母仍按全量统计。" />
              </label>

              {data.caveat && (
                <p className="rounded-lg bg-muted/40 p-2 text-[11px] leading-relaxed text-muted-foreground">
                  ⚠ {plain(data.caveat)}
                </p>
              )}

              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead className="text-muted-foreground">
                    <tr>
                      <SortHeader label="板块" sortKey="name" dir={scSort.dirOf("name")} onSort={sortBy}
                        title="板块名（文本档）；标了「统计」的是行为统计板块，与行业/概念不是一类" />
                      <SortHeader label="阶段" sortKey="stage" dir={scSort.dirOf("stage")} onSort={sortBy}
                        title="阶段是分类档，按字面排，不代表强弱次序" />
                      <SortHeader label="score" sortKey="score" dir={scSort.dirOf("score")} onSort={sortBy}
                        title="周期综合分；缺分的行排最后，不当 0" />
                      {ELEMENTS.map(k => <SortHeader key={k} label={k} sortKey={`elements.${k}`}
                        dir={scSort.dirOf(`elements.${k}`)} onSort={sortBy}
                        title="分项原值（0~100 一档）；后端没交这一项的行排最后，显示 — 而不是 0" />)}
                      <SortHeader label="类型" sortKey="sector_type" dir={scSort.dirOf("sector_type")} onSort={sortBy} />
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((s, i) => {
                      const el = safeRecord<unknown>(s.elements);
                      return (
                        <tr key={`${s.code}-${i}`} className="border-t border-border/50">
                          <td className="py-1 pr-2">
                            <span className="font-medium">{dash(s.name)}</span>
                            {s.synthetic === true && <span className="ml-1 rounded bg-muted px-1 text-[9px] text-muted-foreground">统计</span>}
                          </td>
                          <td className={cn("py-1 pr-2 font-semibold",
                            strengthColor(s.stage === "ignite" || s.stage === "follow"))}>
                            {STAGE_CN[s.stage ?? ""] ?? dash(s.stage)}
                          </td>
                          <td className="py-1 pr-2 text-right tabular-nums">{num(s.score, 1)}</td>
                          {ELEMENTS.map(k => {
                            const v = finite(el[k]);
                            return (
                              <td key={k} className={cn("py-1 pr-2 text-right tabular-nums", v == null && FLAT_TEXT)}>
                                {v == null ? "—" : v.toFixed(1)}
                              </td>
                            );
                          })}
                          <td className="py-1 text-right text-muted-foreground">{dash(s.sector_type)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {/* 原来这里是一句"仅列出排序后的前 40 行"——那是把裁掉的说一声就当交代过去了。
                    现在分页，句子换成全站那一份 `pageSentence`：共多少行、每页多少、第几页。 */}
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="text-muted-foreground">{pageSentence(sorted.length, pageSize, cur)}
                    {scSort.sort ? "" : "（没点列头时是后端序）"}</span>
                  {cur > 1 && <button type="button" onClick={() => setOwnPage(cur - 1)}
                    className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>}
                  {cur < pages && <button type="button" onClick={() => setOwnPage(cur + 1)}
                    className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>}
                </div>
              </div>
              <p className="text-[10px] text-muted-foreground/80">交易日 {dash(ownDate ?? data.date)}（分母与阶段计数按全量板块统计，不受上面开关影响）</p>
              <Caliber text={OWN_CALIBER} />
            </div>
          );
        }}</PanelState>
      </TrackSection>

      <TrackSection track="qd" title="轮动矩阵与持续性（quantdash 口径）"
        hint="回答「领涨板块换了几手、谁连庄最久、强度在增还是减」。quantdash 的板块周期没有阶段枚举，所以不和本轨并成一张表。">
        <PanelState<SectorCycle> state={qd}>{data => (
          <div className="space-y-2">
            <ComputedLine env={data} />
            <RotationMatrix rows={safeArray<RotationRow>(data.rotation)}
              newLeader={(data.persistence?.strength_delta_pct ?? null) === null} />
            <Persistence p={data.persistence ?? null} scope={data.scope} />
            <LeaderFollowPanel state={follow} />
            <DeviationList items={data.deviation} />
          </div>
        )}</PanelState>
      </TrackSection>
    </div>
  );
}

/** 最近 5 个交易日 × 每日前 8 的纯涨幅热度矩阵 */
function RotationMatrix({ rows, newLeader = false }: { rows: RotationRow[]; newLeader?: boolean }) {
  if (!rows.length) {
    return <p className="text-[12px] text-muted-foreground">后端给了空 <code>rotation</code>。</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-separate border-spacing-0.5 text-[10px]">
        <thead>
          <tr>
            <th className="text-left font-medium text-muted-foreground">交易日</th>
            <th colSpan={8} className="text-left font-medium text-muted-foreground">
              当日领涨板块（前 8）· 格子底色=当日涨幅
              <Caliber text="配色阈值照搬 quantdash：涨幅 ≥4 深红 / ≥2 红 / ≥0 浅红 / <0 绿。\n⚠️ 这是纯涨幅配色，不含资金流 —— 红色不代表资金流入。" />
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const items = safeArray<RotationCell>(r.items);
            return (
              <tr key={String(r.trade_date ?? i)}>
                <td className="whitespace-nowrap pr-1 font-mono text-muted-foreground">
                  {dash(r.trade_date)}
                </td>
                <td colSpan={8} className="p-0">
                  {items.length === 0
                    ? <span className="text-muted-foreground">该行 <code>items</code> 为空（当日无领涨板块，或后端未按契约给 items）</span>
                    : (
                      <div className="flex flex-wrap gap-0.5">
                        {items.map((it, j) => {
                          const p = finite(it.pct_change);
                          const name = String(it.sector_name ?? it.name ?? "").trim() || "—";
                          // "新晋领涨"的语义出自 persistence.strength_delta_pct 为 NULL，
                          // 那是**当日第一名**的属性，所以标在 rank 1 的格子上。
                          const first = (it.day_rank ?? j + 1) === 1;
                          return (
                            <span key={`${name}-${j}`}
                              title={`${name}${it.day_rank != null ? `｜当日第 ${it.day_rank} 名` : ""}｜涨幅 ${p == null ? "—" : p.toFixed(2)}%（纯涨幅配色，不含资金流）`}
                              className={cn("rounded px-1.5 py-0.5 font-medium tabular-nums",
                                heatBg(p), (p == null || (p >= 0 && p < 2)) ? "text-foreground" : "text-white/95")}>
                              {name}
                              <span className="ml-1 opacity-80">{p == null ? "—" : `${p > 0 ? "+" : ""}${p.toFixed(1)}`}</span>
                              {newLeader && first && <span className="ml-1 opacity-80">新晋领涨</span>}
                            </span>
                          );
                        })}
                      </div>
                    )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Persistence({ p, scope }: { p: SectorCycle["persistence"] | null; scope?: string | null }) {
  if (!p) return <p className="text-[12px] text-muted-foreground">后端未给 <code>persistence</code>。</p>;
  const repeat = p.strongest_repeat_name;
  return (
    <div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-4">
        <Cell label="当前领涨板块" value={dash(p.current_leader_name)}
          hint={p.current_leader_name == null ? "后端未给 current_leader_name" : undefined} />
        <Cell label="连庄天数" value={p.streak_days == null ? "—" : `${p.streak_days}`} />
        <Cell label="前三甲出现次数"
          value={p.top_three_appearances == null ? "—" : `${p.top_three_appearances}`}
          hint="0 就显示 0（quantdash 缺数据时默认填 1，我们不跟）" />
        <Cell label="强度变化"
          value={pct(p.strength_delta_pct)}
          valueClass={pctColor(p.strength_delta_pct)}
          hint="NULL 的语义是「新晋领涨」（quantdash 原 UI 语义，不是缺数据）；该标注落在矩阵里当日第一名的格子上" />
        <Cell label="最长连庄板块" value={repeat == null || String(repeat).trim() === "" ? "无连庄板块" : plain(repeat)}
          hint="为 NULL 时显示「无连庄板块」，不回落成当前领涨板块" />
      </div>
      <p className="mt-1 text-[10px] text-muted-foreground/80">
        取样范围（分母）：{dash(scope)} —— 与 qd 轨自身口径一致，不等同于本轨的板块全集。
      </p>
    </div>
  );
}

function Cell({ label, value, valueClass, hint }: { label: string; value: string; valueClass?: string; hint?: string }) {
  return (
    <div title={hint}>
      <div className={cn("text-[15px] font-bold tabular-nums", valueClass)}>{value}</div>
      <div className="text-[10px] text-muted-foreground">{label}</div>
      {hint && <div className="text-[9px] leading-tight text-muted-foreground/70">{plain(hint)}</div>}
    </div>
  );
}
