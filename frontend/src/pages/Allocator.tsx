import { RefreshCw, GitBranch } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { Definitions, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { finite, plain } from "@/lib/agent";
import { cn } from "@/lib/utils";

/**
 * 分配者视角（来源：用户 2026-10-06 发来的一段《A股找分配者》视频原文，
 * 逐段对照与算法取舍写在 `docs/分配者视角工作指南.md`）。
 *
 * 一句话：**不问"谁创造了财富"，只算"钱必须经过谁"和"谁正站在被灌满的河道上"。**
 *
 * 三个刻意的选择，改动前先读完：
 *
 * 1. **每一项都是"形状代理"，不是因果。** 本库没有财务报表、没有牌照与市占率、
 *    没有产能与政策文本，所以"绕不过去的收费站"只能用**股息率 + 成交额稳定性 +
 *    份额长期中位 + 板块市值占比**去逼近。形状像 ≠ 制度上是；这句话必须留在页面上，
 *    否则这一屏会被读成它没有的能力（判"这只票是不是好公司"）。
 * 2. **温度 100 不是卖出信号。** 视频原话是"A股最危险的时刻不是没人相信故事，
 *    而是所有人终于达成共识"——所以温度只描述"有多挤"，**不给动作**。
 *    全站都不给交易动作，这一屏尤其不给：它离"该跑了"的诱惑最近。
 * 3. **NULL 不是 0。** 算不出的项不参与排名、不给分，同时交出覆盖度与 `missing`，
 *    所以榜上会出现"—"，而那是设计而不是坏掉。
 *
 * 数据只读 `analytics.sector_allocator`（写侧 `app/compute/allocator.py`，
 * 加工链里叫 `allocator` 步）。这张表**从落库那天起才有历史**，
 * 所以历史曲线要等每天攒 —— 只有一场时页面明说"历史还没有"，不画一条假的。
 */

interface AllocRow {
  sector_code?: string | null; sector_name?: string | null; sector_type?: string | null;
  toll_score?: number | null; toll_rank?: number | null; crowding_temp?: number | null;
  crowding_rank?: number | null; imagine_score?: number | null; quadrant?: string | null;
  quadrant_label?: string | null; stage?: string | null; stage_label?: string | null;
  stage_reason?: string | null; share?: number | null; share_pctile?: number | null;
  share_med250?: number | null; share_slope20?: number | null; amount_stability?: number | null;
  div_yield_med?: number | null; div_coverage?: number | null; mv_share?: number | null;
  mv_coverage?: number | null; member_count?: number | null; member_delta?: number | null;
  margin_crowd?: number | null; mom60?: number | null; breadth20?: number | null;
  missing?: string | null;
}
interface ReturnRow {
  code?: string | null; label?: string | null; available?: boolean | null; reason?: string | null;
  note?: string | null; trades?: number | null; sessions_used?: number | null;
  buy_hold_total?: number | null; buy_hold_annualized?: number | null;
  rule_total?: number | null; rule_annualized?: number | null; peak_above?: number | null;
}
interface AllocData {
  available?: boolean | null; reason?: string | null; date?: string | null;
  spec_version?: string | null; universe?: number | null; generated_at?: string | null;
  sessions?: { sessions?: number | null; from?: string | null; to?: string | null } | null;
  rows?: AllocRow[] | null; crowding?: AllocRow[] | null; scatter?: AllocRow[] | null;
  stages?: { stage?: string | null; n?: number | null; avg_temp?: number | null }[] | null;
  quadrants?: { quadrant?: string | null; n?: number | null; avg_toll?: number | null; avg_imagine?: number | null }[] | null;
  heat_vs_return?: { basis?: string | null; rows?: ReturnRow[] | null } | null;
  calibers?: Record<string, string> | null;
  data_reality?: { div_missing?: number | null; note?: string | null } | null;
}

const QUADRANT_HINT: Record<string, string> = {
  toll: "位置高·想象力低：视频说的「无聊的收费站」就是这一格",
  consensus: "两头都高：所有人终于达成共识的那一格",
  story: "只有想象力：靠故事定价",
  nowhere: "两头都不占",
};

/** 百分数：`null` 一律交回 `—`，不许变成 `0%`（0% 是一个结论，`—` 才是没数）。 */
function pct(v: unknown, digits = 1): string {
  const n = finite(v as number | null);
  return n === null ? "—" : `${(n * 100).toFixed(digits)}%`;
}
function num(v: unknown, digits = 1): string {
  const n = finite(v as number | null);
  return n === null ? "—" : n.toFixed(digits);
}

export function Allocator() {
  const [nonce, refresh] = useNonce();
  const st = useProbe<AllocData>("/quant/allocator", nonce);
  // DataState 是判别联合：只有 `kind === "ok"` 才有 `data`，别的地方一律当"没数"处理。
  const d0 = st.kind === "ok" ? st.data : null;
  const cal = d0?.calibers ?? {};

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="分配者视角" level={1}
        subtitle="不判谁创造了财富，只算钱必须经过谁、以及谁正站在被灌满的河道上。四块读数全部来自本库，每一项都写着它代理的是什么、代理不了什么。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <Panel title="这一屏在算什么" state={st}
          note="判据与权重只在 `app/compute/allocator.py` 一处；接口和页面都不重算。换判据版本 = 换 `spec_version`，旧行不覆盖。"
          slowHint="当日一批约 900 个板块，接口只读库加一次规则复算，实测 1~2 秒。">
          {d => (
            <>
              <Row label="行情日" value={`${dash(d.date)}（母体 ${dash(d.universe)} 个板块，判据版本 ${dash(d.spec_version)}）`} />
              <Row label="这张表已有几场" value={d.sessions?.sessions
                ? `${dash(d.sessions.sessions)} 场（${dash(d.sessions.from)} ~ ${dash(d.sessions.to)}）—— 历史曲线要等加工链每天攒，只有一场时不画假的线`
                : "—"} />
              <Row label="股息率算不出的板块" value={`${dash(d.data_reality?.div_missing)} 个（这一项就按可用项重新归一化，缺的当 NULL 不当 0）`} />
              <p className="mt-1 flex items-start gap-1 text-[11px] leading-relaxed text-muted-foreground">
                <GitBranch className="mt-0.5 h-3 w-3 shrink-0" />
                来源与逐段对照在 <code>docs/分配者视角工作指南.md</code>；
                本地复算用的是<b className="text-foreground">价格指数</b>，比视频里的"全收益"口径低，最后那块写清了差在哪。
              </p>
            </>
          )}
        </Panel>

        <Panel title="① 收费站榜（位置分）" state={st}
          slowHint="读当日全量榜单，不重新判据。">
          {d => <TollTable rows={d.rows ?? []} calibers={cal} />}
        </Panel>

        <Panel title="② 拥挤度温度榜（钱正在往哪里灌）" state={st}
          note="温度只说「有多挤」，不给动作。100 的意思是「所有人终于达成共识」，不是「该卖了」。">
          {d => <CrowdingTable rows={d.crowding ?? []} calibers={cal} />}
        </Panel>

        <Panel title="③ 位置 × 想象力 四象限" state={st}>
          {d => <>
            <Scatter points={d.scatter ?? []} />
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              {(d.quadrants ?? []).map(q => (
                <p key={String(q.quadrant)} className="rounded-lg border border-border px-2 py-1.5 text-[11px] leading-relaxed">
                  <span className="font-medium text-foreground">{q.quadrant}</span>
                  <span className="ml-1 text-muted-foreground">{QUADRANT_HINT[String(q.quadrant)] ?? ""}</span>
                  <span className="ml-1 font-mono text-muted-foreground">
                    {dash(q.n)} 个 · 位置均值 {num(q.avg_toll)} · 想象力均值 {num(q.avg_imagine)}
                  </span>
                </p>
              ))}
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              分界线是<b className="text-foreground">当日中位数</b>（不是写死的 50）—— 写死的话分布一动，整个象限全体搬家。
              <Caliber text={cal.quadrant ?? ""} />
            </p>
          </>}
        </Panel>

        <Panel title="④ 阶段分布（再分配生命周期）" state={st}
          note="判据只有资金与价格的形状，不含政策文本（本地没有政策库）。都不满足就是「不在五段里」，不硬贴一个阶段。">
          {d => <>
            <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
              {(d.stages ?? []).map(s => (
                <p key={String(s.stage)} className="rounded-lg border border-border px-2 py-1.5 text-[11px]">
                  <span className="font-medium text-foreground">
                    {s.stage ? STAGE_LABEL[s.stage] ?? s.stage : "不在五段里"}
                  </span>
                  <span className="ml-1 font-mono text-muted-foreground">{dash(s.n)} 个 · 平均温度 {num(s.avg_temp)}</span>
                </p>
              ))}
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              <Caliber text={cal.stage ?? ""} />
            </p>
          </>}
        </Panel>

        <Panel title="⑤ 故事的热度，与回报是两笔账（本地复算）" state={st}
          note={d0?.heat_vs_return?.basis ?? ""}>
          {d => {
            const rows = d.heat_vs_return?.rows ?? [];
            return (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead><tr className="text-left text-[11px] text-muted-foreground">
                    <th className="whitespace-nowrap px-2 py-1.5 font-normal">指数</th>
                    <th className="whitespace-nowrap px-2 py-1.5 font-normal">用了几场</th>
                    <th className="whitespace-nowrap px-2 py-1.5 font-normal" title="本库这段窗口里，收盘高出 120 日均线最多的一次">最高高出均线</th>
                    <th className="whitespace-nowrap px-2 py-1.5 font-normal">规则触发次数</th>
                    <th className="whitespace-nowrap px-2 py-1.5 font-normal">一直拿着（年化）</th>
                    <th className="whitespace-nowrap px-2 py-1.5 font-normal">按规则（年化）</th>
                  </tr></thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={String(r.code)} className="border-t border-border/40 align-top">
                        <td className="px-2 py-1.5">
                          <p className="font-medium">{dash(r.label)}</p>
                          <p className="font-mono text-[10px] text-muted-foreground">{dash(r.code)}</p>
                          {r.available === false && <p className="text-[10px] text-warning">{dash(r.reason)}</p>}
                          {r.note && <p className="mt-0.5 max-w-md text-[10px] leading-relaxed text-warning">{plain(r.note)}</p>}
                        </td>
                        <td className="px-2 py-1.5 font-mono tabular-nums">{dash(r.sessions_used)}</td>
                        <td className="px-2 py-1.5 font-mono tabular-nums">{pct(r.peak_above, 1)}</td>
                        <td className="px-2 py-1.5 font-mono tabular-nums">{dash(r.trades)}</td>
                        <td className="px-2 py-1.5 font-mono tabular-nums">{pct(r.buy_hold_annualized, 2)}</td>
                        <td className={cn("px-2 py-1.5 font-mono tabular-nums",
                          finite(r.rule_annualized) !== null && finite(r.buy_hold_annualized) !== null
                            && r.rule_annualized! > r.buy_hold_annualized! ? "text-primary" : "text-muted-foreground")}>
                          {pct(r.rule_annualized, 2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                  <Caliber text={cal.backtest ?? ""} />
                </p>
              </div>
            );
          }}
        </Panel>

        <GlassCard>
          <Definitions map={d0?.calibers ?? undefined} />
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}

const STAGE_LABEL: Record<string, string> = {
  s1_seed: "S1 起步·权力刚转身", s2_boom: "S2 繁荣", s3_inflow: "S3 资本涌入",
  s4_consensus: "S4 共识·估值疯狂", s5_overhang: "S5 过剩·股价先掉头",
};

function TollTable({ rows, calibers }: { rows: AllocRow[]; calibers: Record<string, string> }) {
  const sort = useTableSort();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead><tr className="text-left text-[11px] text-muted-foreground">
          <SortHeader label="板块" sortKey="sector_name" dir={sort.dirOf("sector_name")} onSort={sort.toggle} />
          <SortHeader label="位置分" sortKey="toll_score" dir={sort.dirOf("toll_score")} onSort={sort.toggle}
            title="四项分位加权：股息率 35 + 稳定性 25 + 份额中位 20 + 市值占比 20。权重是可商量的先验，不是规律" />
          <SortHeader label="股息率" sortKey="div_yield_med" dir={sort.dirOf("div_yield_med")} onSort={sort.toggle}
            title="成员的中位数；括号里是覆盖度——有几成成员算得出这个数" />
          <SortHeader label="稳定性" sortKey="amount_stability" dir={sort.dirOf("amount_stability")} onSort={sort.toggle}
            title="1 − 日成交额变异系数（近 250 场）。越接近 1 = 不管行情怎样钱都路经这里" />
          <SortHeader label="份额中位" sortKey="share_med250" dir={sort.dirOf("share_med250")} onSort={sort.toggle}
            title="相对口径：分母是全部板块成交额之和，板块之间有重叠，不能读成占全市场的百分比" />
          <SortHeader label="市值占比" sortKey="mv_share" dir={sort.dirOf("mv_share")} onSort={sort.toggle} />
          <SortHeader label="温度" sortKey="crowding_temp" dir={sort.dirOf("crowding_temp")} onSort={sort.toggle}
            title="只说有多挤，不给动作" />
          <SortHeader label="象限" sortKey="quadrant_label" dir={sort.dirOf("quadrant_label")} onSort={sort.toggle} />
          <SortHeader label="阶段" sortKey="stage_label" dir={sort.dirOf("stage_label")} onSort={sort.toggle} />
        </tr></thead>
        <tbody>
          {sort.apply(rows).map((r, i) => (
            <tr key={`${r.sector_code}-${i}`} className="border-t border-border/40 align-top">
              <td className="max-w-44 px-2 py-1.5">
                <p className="font-medium" title={r.sector_code ?? ""}>{dash(r.sector_name)}</p>
                <p className="text-[10px] text-muted-foreground">{dash(r.sector_type)}
                  {r.missing ? ` · 缺 ${r.missing}` : ""}</p>
              </td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{num(r.toll_score)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">
                {pct(r.div_yield_med, 2)}
                <span className="ml-1 text-[10px] text-muted-foreground">覆 {pct(r.div_coverage, 0)}</span>
              </td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{num(r.amount_stability, 3)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{pct(r.share_med250, 3)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{pct(r.mv_share, 1)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{num(r.crowding_temp)}</td>
              <td className="px-2 py-1.5 text-[11px]">{dash(r.quadrant_label)}</td>
              <td className="px-2 py-1.5 text-[11px]" title={r.stage_reason ?? ""}>
                {r.stage ? STAGE_LABEL[r.stage] ?? r.stage : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
        <Caliber text={calibers.toll ?? ""} /> <Caliber text={calibers.nulls ?? ""} />
      </p>
    </div>
  );
}

function CrowdingTable({ rows, calibers }: { rows: AllocRow[]; calibers: Record<string, string> }) {
  const sort = useTableSort();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead><tr className="text-left text-[11px] text-muted-foreground">
          <SortHeader label="板块" sortKey="sector_name" dir={sort.dirOf("sector_name")} onSort={sort.toggle} />
          <SortHeader label="温度" sortKey="crowding_temp" dir={sort.dirOf("crowding_temp")} onSort={sort.toggle} />
          <SortHeader label="份额分位" sortKey="share_pctile" dir={sort.dirOf("share_pctile")} onSort={sort.toggle}
            title="自身近 250 场的分位，不是名次" />
          <SortHeader label="涌入速度" sortKey="share_slope20" dir={sort.dirOf("share_slope20")} onSort={sort.toggle}
            title="当日份额 ÷ 前 20 场（跳过最近 5 场）的中位 − 1" />
          <SortHeader label="成员Δ(年)" sortKey="member_delta" dir={sort.dirOf("member_delta")} onSort={sort.toggle}
            title="板块成分数量与约一年前的差：产业政策落地时「所有人同时上项目」在本地唯一看得见的形状" />
          <SortHeader label="融资/流通市值" sortKey="margin_crowd" dir={sort.dirOf("margin_crowd")} onSort={sort.toggle}
            title="本地两融只有 2026-06 起；更早的场次这一项是空的" />
          <SortHeader label="60 场动量" sortKey="mom60" dir={sort.dirOf("mom60")} onSort={sort.toggle} />
          <th className="whitespace-nowrap px-2 py-1.5 font-normal">为什么判在这一段</th>
        </tr></thead>
        <tbody>
          {sort.apply(rows).map((r, i) => (
            <tr key={`${r.sector_code}-${i}`} className="border-t border-border/40 align-top">
              <td className="max-w-40 px-2 py-1.5">
                <p className="font-medium" title={r.sector_code ?? ""}>{dash(r.sector_name)}</p>
                <p className="text-[10px] text-muted-foreground">{dash(r.sector_type)}</p>
              </td>
              <td className={cn("px-2 py-1.5 font-mono tabular-nums",
                (r.crowding_temp ?? 0) >= 80 ? "text-warning" : "")}>{num(r.crowding_temp)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{pct(r.share_pctile, 0)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{r.share_slope20 === null || r.share_slope20 === undefined ? "—" : `${r.share_slope20 > 0 ? "+" : ""}${(r.share_slope20 * 100).toFixed(0)}%`}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{dash(r.member_delta)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{pct(r.margin_crowd, 1)}</td>
              <td className="px-2 py-1.5 font-mono tabular-nums">{r.mom60 === null || r.mom60 === undefined ? "—" : `${r.mom60 > 0 ? "+" : ""}${r.mom60.toFixed(1)}%`}</td>
              <td className="max-w-md px-2 py-1.5 text-[10px] leading-relaxed text-muted-foreground">{dash(r.stage_reason)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
        <Caliber text={calibers.crowding ?? ""} /> <Caliber text={calibers.member_delta ?? ""} />
      </p>
    </div>
  );
}

/** 象限散点：一屏 900 多个点，所以只画点不画标签；悬停靠原生 title，不引新库。 */
function Scatter({ points }: { points: AllocRow[] }) {
  const xs = points.map(p => finite(p.imagine_score)).filter((n): n is number => n !== null);
  const ys = points.map(p => finite(p.toll_score)).filter((n): n is number => n !== null);
  if (xs.length < 10 || ys.length < 10) {
    return <p className="p-3 text-[12px] text-muted-foreground">散点数据不够（两个坐标都算得出的板块少于 10 个）。</p>;
  }
  const W = 640, H = 320, PAD = 26;
  const x = (v: number) => PAD + (v / 100) * (W - 2 * PAD);
  const y = (v: number) => H - PAD - (v / 100) * (H - 2 * PAD);
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-72 w-full" role="img"
        aria-label={`位置（纵轴）对想象力（横轴）的散点，共 ${points.length} 个板块`}>
        <line x1={x(50)} y1={PAD / 2} x2={x(50)} y2={H - PAD / 2} className="stroke-border" strokeDasharray="4 3" />
        <line x1={PAD / 2} y1={y(50)} x2={W - PAD / 2} y2={y(50)} className="stroke-border" strokeDasharray="4 3" />
        <text x={PAD} y={12} className="fill-current text-[9px] text-muted-foreground">位置分（纵）· 收费站在上</text>
        <text x={W - 150} y={H - 6} className="fill-current text-[9px] text-muted-foreground">想象力分（横）→</text>
        <text x={PAD + 2} y={y(50) - 4} className="fill-current text-[9px] text-muted-foreground">当日中位</text>
        {points.map((p, i) => {
          const px = finite(p.imagine_score), py = finite(p.toll_score);
          if (px === null || py === null) return null;
          const temp = finite(p.crowding_temp) ?? 0;
          return <circle key={`${p.sector_code}-${i}`} cx={x(px)} cy={y(py)} r={temp >= 80 ? 3.2 : 2}
            className={cn(temp >= 80 ? "fill-warning/70" : p.quadrant === "toll" ? "fill-primary/70" : "fill-muted-foreground/40")}>
            <title>{`${p.sector_name ?? p.sector_code}　位置 ${num(p.toll_score)} / 想象力 ${num(p.imagine_score)} / 温度 ${num(p.crowding_temp)}`}</title>
          </circle>;
        })}
      </svg>
      {/* 这张图怎么读收进通用说明框（用户 2026-10-14），句子逐字留着；
          轴标签与「当日中位」那三行写在 svg 里，仍然摊在屏上。 */}
      <ExplainBox title="这张散点图怎么读 · 为什么这一屏不引图表库">
        <p>
          橙点 = 温度 ≥80（已经挤到共识区）；蓝点 = 落在"收费站"那一格。点上的说明靠浏览器原生的悬停提示，
          这一屏不引图表库：900 多个点，能看清分布就够，花哨的交互反而会把注意力从分布上带走。
        </p>
      </ExplainBox>
    </div>
  );
}
