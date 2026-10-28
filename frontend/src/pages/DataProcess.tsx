import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CacheLine, CaveatText, Contract, ExplainBox, Panel, Provenance, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { plain } from "@/lib/agent";
import { dash } from "@/lib/datastate";
import { cn } from "@/lib/utils";

/**
 * 数据加工（映射表 #8：FT 的 `/data` —— "拿到数据之后怎么变成分数"）。
 *
 * FT 那一页是 Dash 的提交表单 + 状态回显，界面不搬（PolyForm + 判定 e）；
 * **它回答的问题必须搬**：加工这一层算到哪一天、哪张派生表停在哪儿、哪个环节还没有产物。
 * 所以这一页不重复"提交按钮"，而是把**派生表逐张**列出来，每张指明它的产物在哪个页面被消费 ——
 * 加工没跑，产物那一屏就会旧，两边说的是同一件事，必须能一眼连起来。
 *
 * 判据仍是**数据日期**：`max_date` 是"算到哪一天"，`last_touched` 只是"什么时候写的"，
 * 后者再新也不算新鲜（一张昨天被写过、内容停在 09-17 的表是陈的）。
 */

interface TableX {
  key?: string | null; table?: string | null; label?: string | null; known_issue?: string | null;
  rows?: number | null; rows_kind?: string | null; date_semantics?: string | null;
  min_date?: string | null; max_date?: string | null; last_touched?: string | null;
  sessions_present?: number | null; calendar_sessions_to_today?: number | null;
  missing_sessions?: number | null; stale_sessions?: number | null; last_stale_date?: string | null;
  status?: string | null; thin_coverage?: boolean | null;
}
interface Coverage {
  available?: boolean | null; reason?: string | null; generated_at?: string | null;
  freshness_basis?: string | null; tables?: TableX[] | null;
  cache?: { source?: string | null; saved_at?: string | null; age_seconds?: number | null; is_stale?: boolean | null; very_stale?: boolean | null; fresh_within_seconds?: number | null } | null;
  trade_calendar?: { available?: boolean | null; from?: string | null; to?: string | null; open_days?: number | null } | null;
}

/** 派生表 → 它的产物在哪个页面被消费。对不上的表不猜，落到"未见页面消费" */
const CONSUMERS: [string, string, string][] = [
  ["analytics.indicator_value", "35 项技术指标", "/stock-review"],
  ["analytics.score", "八项能力加权分", "/grouped"],
  ["analytics.score_detail", "分量的权重与贡献", "/grouped"],
  ["analytics.score_trend", "窗口斜率与迷你走势", "/grouped"],
  ["analytics.sector_heat_daily", "板块热度", "/sector-heat"],
  ["analytics.ignition", "五要素与点火阶段", "/sector-heat"],
  ["analytics.sector_trend", "板块趋势窗口", "/sector-heat"],
  ["analytics.volume_double", "倍量形态榜", "/volume-double"],
  ["analytics.market_breadth", "全市场宽度（涨停数等）", "/"],
  ["qd.sentiment_cycle", "quantdash 口径情绪周期", "/qd-compare"],
  ["qd.board_ladder", "quantdash 口径连板天梯", "/qd-compare"],
  ["qd.board_ladder_member", "天梯成员与首封时刻", "/qd-compare"],
  ["qd.sector_rotation", "qd 轨轮动矩阵", "/qd-compare"],
  ["qd.stock_strength", "qd 轨个股强弱", "/qd-compare"],
  ["qd.news_item", "快讯镜像（现行落在本地 jsonl）", "/qd-compare"],
];

const DERIVED = /^(analytics|qd|reference_data)\./;

export function DataProcess() {
  const [nonce, refresh] = useNonce();
  const cov = useProbe<Coverage>("/coverage", nonce);
  const dpSort = useTableSort();

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="数据加工" level={1}
        subtitle="拿到数据之后怎么变成分数：每张派生表算到哪一天、停在哪儿、被哪个页面消费。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <Panel title="派生表逐张" state={cov} slowHint="这一屏要扫全库逐表统计，实测冷跑过 53.6 秒（命中缓存时不到 0.1 秒）。没有进度可依，所以只说在跑；跑完会把「算于几分钟前」一起交出来。"
          note="只列派生层（analytics.* / qd.* / reference_data.*）。原始行情表的覆盖在覆盖体检那一屏。">
          {d => {
            const all = d.tables ?? [];
            const tables = all.filter(t => DERIVED.test(String(t.table ?? "")));
            return (
              <>
                <CacheLine generatedAt={d.generated_at} cache={d.cache} />
                <Row label="判据" value={dash(d.freshness_basis)} />
                <Row label="派生表 / 全部表" value={`${tables.length} / ${all.length} 张`} />
                <CaveatText text={d.trade_calendar?.available === false ? "交易日历不可用：「应有场次」算不出，只能看日期区间" : null} />
                <Provenance bits={[`覆盖统计来自 GET /api/coverage`]} />
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead><tr className="text-left text-muted-foreground">
                      <SortHeader label="派生表" sortKey="label" dir={dpSort.dirOf("label")} onSort={dpSort.toggle} />
                      <SortHeader label="算到哪一天" sortKey="max_date" dir={dpSort.dirOf("max_date")} onSort={dpSort.toggle}
                        title="排的是最后一场数据日期；快照表无日期列，恒排最后" />
                      <SortHeader label="应有/实有" sortKey="calendar_sessions_to_today" dir={dpSort.dirOf("calendar_sessions_to_today")} onSort={dpSort.toggle} />
                      <SortHeader label="缺" sortKey="missing_sessions" dir={dpSort.dirOf("missing_sessions")} onSort={dpSort.toggle} />
                      <SortHeader label="停在" sortKey="stale_sessions" dir={dpSort.dirOf("stale_sessions")} onSort={dpSort.toggle}
                        title="数据日期落后几个交易日（不是写入时间）" />
                      <SortHeader label="行数" sortKey="rows" dir={dpSort.dirOf("rows")} onSort={dpSort.toggle}
                        title="标「估」的行数来自 pg_class.reltuples，只看量级，别当精确值比" />
                      <th className="font-normal">产物被谁消费</th>
                    </tr></thead>
                    <tbody>
                      {dpSort.apply(tables).map((t, i) => {
                        const consumers = CONSUMERS.filter(([tbl]) => tbl === t.table);
                        return (
                          <tr key={`${t.table}-${i}`} className="border-t border-border/40 align-top">
                            <td className="py-1 pr-2">
                              <p className="font-medium text-foreground">{dash(t.label)}</p>
                              <p className="font-mono text-[10px] text-muted-foreground">{dash(t.table)}</p>
                              {t.known_issue && <p className="mt-0.5 max-w-72 text-[10px] leading-relaxed text-warning">{plain(t.known_issue)}</p>}
                            </td>
                            <td className="whitespace-nowrap pr-2 font-mono">
                              {t.date_semantics === "snapshot"
                                ? <span className="text-muted-foreground">快照表（无日期列）</span>
                                : <>{dash(t.min_date)} ~ <b className="text-foreground">{dash(t.max_date)}</b></>}
                            </td>
                            <td className="whitespace-nowrap pr-2 font-mono tabular-nums">
                              {t.calendar_sessions_to_today == null ? "—" : `${dash(t.calendar_sessions_to_today)}/${dash(t.sessions_present)}`}
                            </td>
                            <td className={cn("pr-2 font-mono tabular-nums",
                              ((t.missing_sessions ?? 0) > 0 || t.missing_sessions == null) && "text-warning")}>
                              {dash(t.missing_sessions)}
                              {t.missing_sessions == null && <span className="block text-[10px] text-muted-foreground">算不出（不是零缺口）</span>}
                            </td>
                            <td className={cn("whitespace-nowrap pr-2 font-mono tabular-nums", (t.stale_sessions ?? 0) > 0 && "text-warning")}>
                              {dash(t.stale_sessions)}{t.last_stale_date ? <span className="text-muted-foreground">（{t.last_stale_date}）</span> : null}
                            </td>
                            <td className="whitespace-nowrap font-mono tabular-nums">
                              {dash(t.rows)}{t.rows_kind === "estimated" ? <span className="text-[10px] text-muted-foreground"> 估</span> : null}
                            </td>
                            <td className="whitespace-nowrap">
                              {consumers.length > 0
                                ? consumers.map(([tbl, what, where]) => (
                                  <Link key={tbl} className="mr-2 text-primary underline" to={where}>{what}</Link>
                                ))
                                : <span className="text-[10px] text-muted-foreground">未见页面消费</span>}
                            </td>
                          </tr>
                        );
                      })}
                      {tables.length === 0 && <tr><td colSpan={7} className="py-2 text-[11px] text-muted-foreground">后端交出 0 张派生表。</td></tr>}
                    </tbody>
                  </table>
                </div>
                {/* 列口径折进通用说明框（用户 10-14：说明占太多屏幕空间）。
                    「算不出（不是零缺口）」「快照表（无日期列）」那两格仍在表格单元格里，没跟着折。 */}
                <ExplainBox title="这一张表的列怎么读">
                  <p>
                    行数标「估」的是 <code>pg_class.reltuples</code> 估算值，只看量级。
                    <b className="text-foreground">「停在」这一列才是加工新不新鲜</b>：它数的是数据日期与交易日历差几个交易日，
                    <code>last_touched</code>（什么时候写入的）不参与判据。
                  </p>
                </ExplainBox>
              </>
            );
          }}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">提交与重算：这一屏刻意没有的按钮</h2>
          {/* 只折「为什么不摆按钮」这一段分工说明；h2 与下面的 Contract 留在屏上 ——
              这一屏没有重跑入口、缺哪个端点，必须一眼看得见。 */}
          <ExplainBox title="这一屏为什么不摆重跑按钮 · 重跑入口在哪一屏">
            <p className="mt-1">
              FT 的 <code>/data</code> 上有「提交某天的 d1~d7」这种操作入口。这一屏<b className="text-foreground">仍然不摆重跑按钮</b>，
              但理由已经不是"没有端点"：链子接上了（<code>ingest/jobs/e1_fill_all.py</code> 里 <code>signal</code> 那一步就是本页那些派生表；步序会随链增减，这里不抄序号），
              触发端点也在（<code>POST /api/jobs/&lt;key&gt;/run</code>，实测对没有执行体的作业回 403 并写明「有真执行体才给按钮」）。
              真正的原因是<b className="text-foreground">这一屏读的是覆盖统计，不持有 job_key 与执行体的对应关系</b> ——
              在这里摆按钮就只能猜哪个键跑哪张表，猜错就是拿"看起来按下去了"骗人。
              重跑入口在 <Link className="text-primary underline" to="/jobs">任务调度</Link>：那一屏按接口给的
              <code>available</code> 决定摆不摆，摆了就真能跑。
            </p>
          </ExplainBox>
          <div className="mt-2">
            <Contract path="POST /api/data-process/run" source="按派生层依赖顺序重算（indicator → score → detail → trend → heat → ignition）" fields={[
              ["body: date + stages[]", "哪天、哪几层。不给默认全跑（全跑是分钟级到十分钟级的事，不能误触）"],
              ["run_id + status", "异步返回；页面轮 run_id，不阻塞等待"],
              ["每层产出行数与 max(trade_date)", "跑完必须回答「算到哪一天了」—— 这与本页第一列是同一个判据"],
              ["409 + 原因", "同一层正在跑时不得并发起第二个（会互写派生表）"],
            ]} />
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            加工链上游的原始数据缺不缺，看 <Link className="text-primary underline" to="/coverage">覆盖体检</Link>；
            加工产物本身对不对，看 <Link className="text-primary underline" to="/quality">质量闸</Link>；
            该跑没跑，看 <Link className="text-primary underline" to="/jobs">任务调度</Link>。
          </p>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
