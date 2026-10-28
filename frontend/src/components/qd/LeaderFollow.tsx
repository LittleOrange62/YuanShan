import { SortHeader } from '@/components/ui/SortHeader';
import { useTableSort } from '@/lib/useSort';
import { Caliber } from "@/components/ui/Caliber";
import { pctColor } from "@/lib/colors";
import { safeArray } from "@/lib/agent";
import { dash, num, pct } from "@/lib/qd";
import type { FollowCell, LeaderFollow, QdState, SectorFollow, SimilarSector } from "@/lib/qd";
import { PanelState } from "./Track";

/**
 * 前三甲 · 前五场名次 · 与它们轨迹最像的板块（用户 2026-10-25 点名）。
 *
 * 三件事都只念后端交出的字段：名次、平均名次差、秩相关一个都不在这儿算
 * （判据只住写侧 `app/compute/qd_sector_follow.py`，读侧 `duanxian/qd_follow.py` 只读落库行）。
 * 口径文字读 `calibers`，前端不自写句子；
 * 缺行交 `null` 一律显示 `—`，不折成第 0 名、也不折成 0%。
 *
 * 板块名不给外链是刻意的：这一轨的板块是通达信 `TDX_` 一族，
 * 而《板块名→东财板块页》那张对表只认东财 `BK` 码 —— 没有码就退回纯文本，不猜地址。
 */

/** 表里一行 = 「某个领涨板块」与某个相似板块的一对 */
interface Pair {
  key: string;
  template_rank: number;
  template_name: string;
  sector_name: string;
  sector_type: string;
  gap_mean_rank: number | null;
  gap_pct_of_universe: number | null;
  rank_corr: number | null;
  current_rank: number | null;
}

function pairs(leaders: LeaderFollow[]): Pair[] {
  const out: Pair[] = [];
  for (const lead of leaders) {
    for (const s of safeArray<SimilarSector>(lead.similar?.items)) {
      out.push({
        key: `${lead.day_rank}-${s.sector_code}`,
        template_rank: lead.day_rank,
        template_name: dash(lead.sector_name),
        sector_name: dash(s.sector_name),
        sector_type: dash(s.sector_type),
        gap_mean_rank: s.gap_mean_rank ?? null,
        gap_pct_of_universe: s.gap_pct_of_universe ?? null,
        rank_corr: s.rank_corr ?? null,
        current_rank: s.current_rank ?? null,
      });
    }
  }
  return out;
}

export function LeaderFollowBlock({ data }: { data: SectorFollow }) {
  const sort = useTableSort();
  const leaders = safeArray<LeaderFollow>(data.leaders);
  const cal = data.calibers ?? {};
  if (!leaders.length) {
    return <p className="text-[12px] text-muted-foreground">后端给了 <code>available:true</code> 但没有 <code>leaders</code>，这块不猜内容。</p>;
  }
  return (
    <div className="space-y-2">
      <div className="text-[11px] font-medium text-muted-foreground">
        当日前三甲（领涨那一名 + 后面两名）· 前五场名次 · 与它们轨迹最像的板块
        <Caliber text={`${cal.leaders ?? ""}\n${cal.trajectory ?? ""}`} />
      </div>

      <ol className="space-y-1">
        {leaders.map(l => (
          <li key={l.sector_code} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px]">
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] tabular-nums">第 {l.day_rank} 名</span>
            <span className="font-semibold">{dash(l.sector_name)}</span>
            <span className={pctColor(l.pct_change)}>{pct(l.pct_change)}</span>
            <span className="text-muted-foreground">前五场名次</span>
            <span className="tabular-nums">
              {safeArray<FollowCell>(l.trajectory).map((c, i) => (
                <span key={c.trade_date ?? i} title={`${dash(c.trade_date)}｜那场涨幅 ${pct(c.pct_change)}`}>
                  {i ? " · " : ""}{c.rank == null ? "—" : c.rank}
                </span>
              ))}
            </span>
            <span className="text-[10px] text-muted-foreground/80">
              {safeArray<FollowCell>(l.trajectory).map(c => dash(c.trade_date).slice(5)).join(" ")}
            </span>
            {l.similar?.incomplete_template === true && (
              <span className="text-[10px] text-warning">模板缺场次，本轮不比对</span>
            )}
          </li>
        ))}
      </ol>

      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-0.5 text-[10px]">
          <thead>
            <tr>
              <th className="font-normal whitespace-nowrap p-0"><span className="px-2 py-1.5">领涨板块</span></th>
              <SortHeader label="相似板块" sortKey="sector_name" dir={sort.dirOf('sector_name')}
                onSort={sort.toggle} title="通达信板块名（这一轨没有东财板块码，所以不给外链）" />
              <SortHeader label="平均名次差" sortKey="gap_mean_rank" dir={sort.dirOf('gap_mean_rank')}
                onSort={sort.toggle} title={cal.similar ?? ""} />
              <SortHeader label="占母体" sortKey="gap_pct_of_universe" dir={sort.dirOf('gap_pct_of_universe')}
                onSort={sort.toggle} title="平均名次差 ÷ 当天参与排名的板块数" />
              <SortHeader label="秩相关" sortKey="rank_corr" dir={sort.dirOf('rank_corr')}
                onSort={sort.toggle} title="两条名次序列的同形程度；算不出来交空，不折成 0" />
              <SortHeader label="当日第" sortKey="current_rank" dir={sort.dirOf('current_rank')}
                onSort={sort.toggle} title="这个相似板块自己在基准日那场的名次" />
            </tr>
          </thead>
          <tbody>
            {sort.apply(pairs(leaders)).map(r => (
              <tr key={r.key}>
                <td className="whitespace-nowrap"><span className="px-2">第 {r.template_rank} 名 {r.template_name}</span></td>
                <td className="whitespace-nowrap" title={`类型：${r.sector_type}`}>
                  <span className="px-2">{r.sector_name}</span>
                </td>
                <td className="p-0 text-right tabular-nums"><span className="px-2">{num(r.gap_mean_rank)}</span></td>
                <td className="p-0 text-right tabular-nums">
                  <span className="px-2">{r.gap_pct_of_universe == null ? "—" : `${num(r.gap_pct_of_universe)}%`}</span>
                </td>
                <td className="p-0 text-right tabular-nums"><span className="px-2">{num(r.rank_corr)}</span></td>
                <td className="p-0 text-right tabular-nums">
                  <span className="px-2">{r.current_rank == null ? "—" : r.current_rank}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] leading-relaxed text-muted-foreground/90">
        对齐方式：错开 {data.lag == null ? "—" : data.lag} 场 —— 模板取领涨板块 t-{data.lag} 起的
        {data.shape_points == null ? "—" : data.shape_points} 个名次，候选取它自己 t 起的同样场次的名次。
        <Caliber text={`${cal.lag ?? ""}\n${cal.similar ?? ""}\n${cal.coverage ?? ""}\n${cal.landing ?? ""}`} />
        <br />
        分母：参与比对 {data.match_total == null ? "—" : data.match_total} 个·人次，因缺行未参与 {data.skip_total == null ? "—" : data.skip_total} 个·人次；母体 {data.universe_size == null ? "—" : data.universe_size} 个板块；基准日 {dash(data.date)}。
        {data.cache_age_seconds != null
          ? `　本批算于 ${dash(data.generated_at).slice(0, 19).replace("T", " ")}（加工链 qd_sector 那一步落库的批次，${data.cache_age_seconds} 秒前；这块不在请求时现算）。`
          : ""}
      </p>
      {safeArray<{ code?: string; label?: string; note?: string }>(data.caveats).map(c => (
        <p key={c.code ?? c.label} className="text-[10px] leading-relaxed text-warning/90">
          {dash(c.label)}：{dash(c.note)}
        </p>
      ))}
    </div>
  );
}

/** 取数状态包一层：loading / 连不上 / 后端说没有，三种要分别说，不合并成一片空白 */
export function LeaderFollowPanel({ state }: { state: QdState<SectorFollow> }) {
  return (
    <PanelState<SectorFollow> state={state}>{data => <LeaderFollowBlock data={data} />}</PanelState>
  );
}
