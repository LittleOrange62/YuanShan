import { RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CacheLine, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import { useState } from "react";

/**
 * 数据覆盖体检（规划 §2 维度一）。
 *
 * 这一屏的主角是**缺**，不是量：`rows` 再大也可能只覆盖三天。
 * 所以每行先看"应有场次 / 实有场次 / 缺几场 / 停在哪天"，行数放最后且标清是估的还是精确的
 * （`rows_kind=estimated` 来自 `pg_class.reltuples`，长期不 VACUUM 会偏）。
 *
 * 判据是**数据日期**，不是写入时间戳：`stale_sessions` 就是"数据日期落后几个交易日"，
 * 而 `last_touched` 只作辅助展示 —— 一张昨天被写过但内容停在 09-17 的表，写时间再新也是陈的。
 */

interface TableX {
  key?: string | null; table?: string | null; label?: string | null; known_issue?: string | null;
  ts_column?: string | null; rows?: number | null; rows_kind?: string | null; date_semantics?: string | null;
  min_date?: string | null; max_date?: string | null; last_touched?: string | null;
  calendar_sessions_to_today?: number | null; sessions_present?: number | null;
  missing_sessions?: number | null; stale_sessions?: number | null; last_stale_date?: string | null;
  first_missing?: string | null; missing_samples?: string[] | null; status?: string | null; thin_coverage?: boolean | null;
  /** 「一天应该有 N 只/行」的表才有：本场条数、动态基线、以及这条场次是基线前第几场 */
  entities_latest?: number | null; entities_baseline?: number | null; baseline_date?: string | null;
  baseline_basis?: string | null; sessions_back?: number | null; entity_partial?: boolean | null;
  /** 「允许少几条」与"这个数从哪层取的"（默认 / 有人在系统配置里改过）。
   *  后端 `duanxian/coverage.py::_entity_check` 交的是**条数**，不是比例。 */
  entity_deviation?: number | null; deviation_from?: string | null;
  coverage_note?: string | null; completeness_why?: string | null;
}
interface Coverage {
  available?: boolean | null; reason?: string | null; generated_at?: string | null;
  /** 真实字段名是 `cache{}`（不是布尔的 `cached`）：磁盘缓存的来源、存于何时、距今多久 */
  cache?: { source?: string | null; saved_at?: string | null; age_seconds?: number | null; is_stale?: boolean | null; very_stale?: boolean | null; fresh_within_seconds?: number | null } | null;
  freshness_basis?: string | null;
  trade_calendar?: { available?: boolean | null; open_days?: number | null; from?: string | null; to?: string | null } | null;
  counts?: Record<string, number> | null; tables?: TableX[] | null;
}

const STATUS_LABEL: Record<string, string> = {
  ok: "正常", gaps: "有缺口", no_data: "空表", missing_table: "表不存在", no_access: "无权限", error: "读取失败",
};

export function Coverage() {
  const [nonce, refresh] = useNonce();
  const cov = useProbe<Coverage>("/coverage", nonce);
  const covSort = useTableSort();
  const [showSamples, setShowSamples] = useState(false);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="覆盖体检" level={1}
        subtitle="每张表覆盖哪段、缺哪几天、停在哪个交易日。缺的日子比行数更要紧 —— 它决定哪些指标永远算不出来。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <Panel title="逐表覆盖" state={cov} slowHint="这一屏要扫全库逐表统计，实测冷跑过 53.6 秒（命中缓存时不到 0.1 秒）。没有进度可依，所以只说在跑；跑完会把「算于几分钟前」一起交出来。"
          note="「应有 / 实有」的分母是交易日历从该表最早日期到今天的场次数；快照表没有日期列，所以不参与这一对比。「本场 / 基线」是条数判据：一场里应该有 N 只（或 N 行）的那几张表，本场条数必须 ≥ 前一天登记的条数减去偏离值（默认 30 条、分钟线类 200 条，两个数在系统配置里改得动）—— 新股只会让条数往上走，所以条数变少就是半截下载，日期齐了也不算全；偏离值给的是退市/停牌那几天的正常回退留的口子。">
          {d => {
            const tables = d.tables ?? [];
            return (
              <>
                <CacheLine generatedAt={d.generated_at} cache={d.cache} />
                <Row label="判据" value={dash(d.freshness_basis)} />
                <Row label="交易日历" value={d.trade_calendar?.available === true
                  ? `${dash(d.trade_calendar.from)} ~ ${dash(d.trade_calendar.to)}，共 ${dash(d.trade_calendar.open_days)} 个交易日`
                  : "交易日历不可用 —— 那么所有「缺几场」的数字都算不出，只能看 min/max"}
                  tone={d.trade_calendar?.available === true ? undefined : "warn"} />
                <Row label="分类计数" value={Object.entries(d.counts ?? {}).map(([k, v]) => `${STATUS_LABEL[k] ?? k} ${v}`).join(" · ")} />
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead><tr className="text-left text-muted-foreground">
                      <SortHeader label="表" sortKey="label" dir={covSort.dirOf("label")} onSort={covSort.toggle} />
                      <SortHeader label="状态" sortKey="status" dir={covSort.dirOf("status")} onSort={covSort.toggle}
                        title="ok / gaps / no_data / missing_table / no_access / error 是六件事，排序只按字面聚堆" />
                      <SortHeader label="日期区间" sortKey="max_date" dir={covSort.dirOf("max_date")} onSort={covSort.toggle}
                        title="排的是覆盖终点（数据日期）；无日期列的快照表恒排最后" />
                      <SortHeader label="应有/实有" sortKey="calendar_sessions_to_today" dir={covSort.dirOf("calendar_sessions_to_today")} onSort={covSort.toggle}
                        title="应有场次 / 实有场次" />
                      <SortHeader label="缺" sortKey="missing_sessions" dir={covSort.dirOf("missing_sessions")} onSort={covSort.toggle}
                        title="区间内缺的场次数" />
                      <SortHeader label="停在" sortKey="stale_sessions" dir={covSort.dirOf("stale_sessions")} onSort={covSort.toggle}
                        title="数据日期落后于日历的天数" />
                      <SortHeader label="行数" sortKey="rows" dir={covSort.dirOf("rows")} onSort={covSort.toggle}
                        title="标「估」的是 reltuples 估算值，只看量级" />
                    </tr></thead>
                    <tbody>
                      {covSort.apply(tables).map((t, i) => (
                        <tr key={`${t.key}-${i}`} className="border-t border-border/40 align-top">
                          <td className="py-1 pr-2">
                            <p className="font-medium text-foreground">{dash(t.label)}</p>
                            <p className="font-mono text-[10px] text-muted-foreground">{dash(t.table)}</p>
                            {t.date_semantics === "snapshot" && (
                              <p className="text-[10px] text-muted-foreground">快照表：无交易日期列，不许当时间序列用</p>
                            )}
                            {t.known_issue && <p className="mt-0.5 max-w-md text-[10px] leading-relaxed text-warning">{plain(t.known_issue)}</p>}
                          </td>
                          <td className="pr-2">
                            <span className={cn("rounded px-1 py-0.5 text-[10px]",
                              t.status === "ok" ? "bg-primary/12 text-primary" : t.status === "no_data" || t.status === "error" || t.status === "missing_table" ? "bg-destructive/15 text-destructive" : "bg-warning/15 text-warning")}>
                              {STATUS_LABEL[t.status ?? ""] ?? dash(t.status)}
                            </span>
                            {t.thin_coverage === true && <span className="ml-1 text-[10px] text-warning">覆盖极薄</span>}
                            {t.entities_latest != null && (
                              <p className={"mt-0.5 font-mono text-[10px] "
                                + (t.entity_partial === true ? "text-warning" : "text-muted-foreground")}
                                title="「一天应该有 N 只/行」的表才给这一行。判据是本场条数 ≥ 前一天登记的条数 − 偏离值（条数，不是百分比；默认 30、分钟类 200，在「系统配置 → 收盘链 → 条数完整性」改得动）。基线由系统每天登记下来的条数自己长，只有判为「全」的那些场才进池">
                                本场 {t.entities_latest.toLocaleString()}
                                {t.entities_baseline != null ? ` / 基线 ${t.entities_baseline.toLocaleString()}` : ""}
                                {t.baseline_date
                                  ? `（${t.baseline_date}${t.sessions_back && t.sessions_back > 1
                                    ? `，隔 ${t.sessions_back} 场，中间那些没登记` : "，前一天"}）` : ""}
                                {t.entity_deviation != null
                                  ? ` 允许少 ${t.entity_deviation.toLocaleString()} 条（${t.deviation_from ?? "出处后端没说"}）` : ""}
                              </p>
                            )}
                            {t.entity_partial === true && t.coverage_note && (
                              <p className="mt-0.5 max-w-md text-[10px] leading-relaxed text-warning">{plain(t.coverage_note)}</p>
                            )}
                          </td>
                          <td className="whitespace-nowrap pr-2 font-mono">{dash(t.min_date)} ~ {dash(t.max_date)}</td>
                          <td className="whitespace-nowrap pr-2 font-mono tabular-nums">
                            {t.calendar_sessions_to_today == null ? "—" : `${dash(t.calendar_sessions_to_today)}/${dash(t.sessions_present)}`}
                          </td>
                          <td className={cn("font-mono tabular-nums pr-2",
                            (t.missing_sessions ?? 0) > 0 || t.missing_sessions == null ? "text-warning" : "")}>
                            {dash(t.missing_sessions)}
                            {t.missing_sessions == null && (
                              <span className="block text-[10px] text-muted-foreground">算不出（不是零缺口）</span>
                            )}
                            {showSamples && Array.isArray(t.missing_samples) && t.missing_samples.length > 0 && (
                              <span className="block max-w-52 text-[10px] text-muted-foreground">
                                {t.missing_samples.slice(0, 10).join(" ")}{t.missing_samples.length > 10 ? ` …共 ${t.missing_samples.length} 天` : ""}
                              </span>
                            )}
                          </td>
                          <td className="whitespace-nowrap font-mono tabular-nums">
                            {dash(t.stale_sessions)}{t.last_stale_date ? <span className="text-muted-foreground">（{t.last_stale_date}）</span> : null}
                          </td>
                          <td className="whitespace-nowrap font-mono tabular-nums">
                            {dash(t.rows)}{t.rows_kind === "estimated" ? <span className="text-[10px] text-muted-foreground"> 估</span> : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <label className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground">
                  <input type="checkbox" checked={showSamples} onChange={e => setShowSamples(e.target.checked)} />
                  列出具体缺的是哪几天（每表最多显示 10 天）
                </label>
                {/* 列口径（行数为什么是「估」、判据为什么不用写入时间戳）折进通用说明框（用户 10-14）。
                    「算不出（不是零缺口）」、「覆盖极薄」、known_issue、缺样本周期的「共 N 天」都留在原位 ——
                    那些是这一格读数的判读告警与截断声明，藏起来等于没写。 */}
                <ExplainBox title="行数为什么标「估」· 判据为什么不用写入时间戳">
                  <p>
                    行数标「估」的是 <code>pg_class.reltuples</code> 估算值（长期不 VACUUM 会偏），只用于看量级，不当精确条数用。
                    写入时间戳（<code>last_touched</code>）这里刻意不做状态判据 —— 判据是数据日期。
                  </p>
                </ExplainBox>
              </>
            );
          }}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">这些缺口会直接决定哪些页面永远是空的</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            <b className="text-foreground">1 分钟线只有 2026-09-22 一天</b>：首封时刻、炸板时点、盘中路径这类盘中事实在其它任何交易日
            <b className="text-foreground">永远算不出来</b> —— 那
            <Link className="text-primary underline" to="/qd-compare"> quantdash 口径对照</Link>页里"封板时间不可得"的灰底不是 bug；
            quantdash 原版在这种日子用代码哈希编一个时间出来，我们不编。
            同理 <code>fund_flow_rank</code> 从 09-28 才开始按场入库（新浪即时截面，目前三场、每场约 5,570 行），
            <b className="text-foreground">一天不采就永久少一天、不可回补</b>，所以逐日资金流的历史仍然短；
            板块份额那一档在 <code>analytics.sector_flow_trend</code>（1252 场），是另一个口径。
            <code>qd.news_item</code> 0 行是预期 —— 快讯落在本地 jsonl。
          </p>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
