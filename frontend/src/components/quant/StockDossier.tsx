import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { CaliberFold, Panel, Row } from "@/components/ui/DataPanel";
import { StockLink } from "@/components/ui/StockLink";
import { useProbe, useNonce } from "@/lib/probe";
import { dash, describeState, num } from "@/lib/datastate";
import { pctColor } from "@/lib/colors";
import { plain } from "@/lib/agent";
// 名次与小数显示的那三条线只住 `lib/ranking`（与后端 `duanxian/ranking.py` 同一份纪律）。
import { rankText, rankTitle, scoreRankCell, trimNum } from "@/lib/ranking";
import { cn } from "@/lib/utils";

/**
 * 这只票的量化档案（用户 2026-10-13）：选中一只票之后，
 * 把量化选股那一组各自算好的读数**按这一只票**摆到一起。
 *
 * 调用方（一份实现，两处调用；2026-10-14 用户点名不要再写第二份）：
 *  · `pages/StockReview.tsx` —— 个股深度复盘（2026-10-15 起这一叠的主家：顶部选一只，
 *    或从分组自选/持仓/本机名单**双击**带 `?symbol=` 开进来）；
 *  · `pages/Confluence.tsx` —— 选股汇总的交叉命中榜里点中的那一行。
 * 两个入口交给它的都只是「哪只票 + 哪一天」，所以这一份界面改一处，两屏一起跟着改。
 *
 * 三条页面纪律：
 *  ① 一个数都不自己算：八块全部读 `/api/quant/stock-dossier` 交出的字段，
 *     连象限标签、类型标签、能力名称都是后端带来的；
 *  ② 缺席分三种念法：`read_failed`（查询失败，带异常原文）／`not_computed`（榜还没算到这天）／
 *     `not_on_board`（算了但没进榜）—— 第三种绝不能缩成「它没有这个形态」；
 *  ③ 每块自己说它是哪一天的数（`as_of`）：埋伏那一链还在回算，
 *     它的最近一次进榜可能是半年前，与今天的象限并排展示时必须看得见日期。
 */

interface Blk {
  available?: boolean | null;
  status?: string | null;
  reason?: string | null;
  as_of?: string | null;
  caliber?: string | null;
}
interface Dossier {
  available?: boolean | null;
  reason?: string | null;
  code?: string | null;
  name?: string | null;
  board?: string | null;
  is_st?: boolean | null;
  list_date?: string | null;
  date?: string | null;
  quote?: { d?: string | null; close?: number | null; pct_change?: number | null;
            turnover_rate_percent?: number | null; amount_yuan?: number | null } | null;
  blocks?: Record<string, Blk | undefined> | null;
  boards_covered?: { key?: string; label?: string; board?: string; tables?: string }[] | null;
  not_covered?: { where?: string; why?: string }[] | null;
  caliber?: string | null;
  coverage?: Record<string, unknown> | null;
  caveats?: { code?: string; text?: string }[] | null;
  source?: string | null;
}

const STATUS_TONE: Record<string, string> = {
  read_failed: "缺这一块的数（查询失败，原文在下面）",
  not_computed: "这一块还没算到",
  not_on_board: "这一场它没进这一榜",
};

/** 后端散文进 `title`（悬停提示）之前也过同一道闸：那些字段里写着成对的粗体标记，
 *  提示气泡是按纯文本显示的，不剥就会把 `**` 原样印出来。数据值不走这里。 */
const tip = (v?: string | null) => (v ? plain(v) : undefined);

function Shell({ title, hint, blk, to, children }: {
  title: string; hint?: string | null; blk?: Blk; to?: string; children?: ReactNode;
}) {
  if (!blk || blk.available !== true) {
    const st = blk?.status ?? "not_computed";
    return (
      <section className="rounded-lg border border-border/60 p-2">
        <header className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-[12px] font-medium">{title}</span>
          <span className="text-[10px] text-warning">{STATUS_TONE[st] ?? "这一块没有可用读数"}</span>
        </header>
        {/* 三种缺席念法不同，异常原文必须看得见 —— 兜成一句「没有数据」会把库坏了读成这只票没形态 */}
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{plain(blk?.reason ?? "这一块没交出 reason，也没交出数")}</p>
      </section>
    );
  }
  return (
    <section className="rounded-lg border border-border/60 p-2" title={tip(hint)}>
      <header className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-[12px] font-medium">{title}</span>
        <span className="font-mono text-[10px] text-muted-foreground">数出自 {dash(blk.as_of)}</span>
        {to && <Link to={to} className="text-[10px] text-primary underline">整屏</Link>}
      </header>
      <div className="mt-1">{children}</div>
    </section>
  );
}

/** 一个「标签：值」的小格。值缺就交 `—`，不填 0（0 在这些列里是一个真实读数）。 */
function KV({ k, v, tone, title }: { k: string; v: ReactNode; tone?: string; title?: string }) {
  return (
    <span className="inline-flex items-baseline gap-1 text-[11px]" title={title}>
      <span className="text-muted-foreground">{k}</span>
      <b className={cn("font-mono tabular-nums font-medium text-foreground", tone)}>{v}</b>
    </span>
  );
}

const pctv = (v?: number | null, dp = 2) => (typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(dp)}%` : dash(null));
const money = (v?: number | null) => (typeof v === "number" ? `${(v / 1e8).toFixed(2)} 亿` : dash(null));

function Quadrant({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_Q;
  const o = d.oper ?? {};
  const path = d.quadrant_path;
  return (
    <Shell title="四维象限" to="/quadrant" blk={b} hint={d.caliber}>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <KV k="象限" v={`${dash(o.quadrant)}${o.quadrant_label ? ` ${o.quadrant_label}` : ""}`}
          title={tip(o.meaning)} />
        <KV k="oper" v={num(o.value, 2)} title={tip(d.formula)} />
        <KV k="当日分位" v={o.percent_rank == null ? dash(null) : `${num(o.percent_rank, 1)}%`} />
        <KV k="全场第" v={`${dash(o.rank_in_market)} 名`} />
        <KV k="X 个股质量" v={num(d.x_quality?.value, 2)} title={`分位 ${num(d.x_quality?.percent_rank, 1)}%`} />
        <KV k="Y 环境共振" v={num(d.y_env?.value, 2)} title={`分位 ${num(d.y_env?.percent_rank, 1)}%`} />
      </div>
      {path?.path && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          窗口内象限移动：<span className="font-mono text-foreground">{path.path}</span>
          （{dash(path.sessions)} 场，{dash(path.window_days)} 日窗口）—— {plain(path.meaning)}
        </p>
      )}
      {(d.trend ?? []).length > 0 && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          {d.trend!.map(t => `窗口 ${dash(t.window_days)} 场：斜率 ${num(t.slope, 3)}`
            + `｜连续上涨 ${dash(t.up_streak_days)} 场｜第一天 ${dash(t.first_quadrant)}`).join("　")}
        </p>
      )}
      {o.is_stale === true && (
        <p className="mt-1 text-[11px] text-warning">这一格标了 is_stale：分数是沿用旧场的，不与新算的横比。</p>
      )}
    </Shell>
  );
}

function Scores({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_S;
  const caps = d.capabilities ?? [];
  return (
    <Shell title="趋势评分八分量" blk={b} hint={d.caliber}>
      <ul className="grid gap-1 sm:grid-cols-2">
        {caps.map(c => {
          const rc = scoreRankCell(c);
          return (
          <li key={String(c.capability_key)} className="text-[11px]">
            <details>
              <summary className="cursor-pointer">
                <span className="text-muted-foreground">{dash(c.capability_label ?? c.capability_key)}</span>{" "}
                <b className="font-mono tabular-nums">{num(c.value, 2)}</b>{" "}
                <span className={cn("text-[10px]", rc?.warn ? "text-warning" : "text-muted-foreground")}
                  title={rc?.title}>
                  {rc ? `${rc.head}${rc.pct ? `（${rc.pct}）` : ""}${rc.extras.length ? `｜${rc.extras.join("｜")}` : ""}` : ""}
                  ｜{dash(c.component_count)} 个分量
                  {c.weight_sum_ok === false ? "｜分量权重之和不为 1" : ""}
                  {c.weight_mismatch ? "｜与注册表权重不一致" : ""}
                </span>
              </summary>
              <table className="mt-1 w-full text-[10px]">
                <thead><tr className="text-left text-muted-foreground">
                  <th className="font-normal">分量</th><th className="font-normal">值</th>
                  <th className="font-normal">权重</th><th className="font-normal">贡献</th>
                </tr></thead>
                <tbody>
                  {(c.components ?? []).map(p => (
                    <tr key={String(p.component_key)} className="border-t border-border/40">
                      <td>{dash(p.component_label ?? p.component_key)}</td>
                      <td className="font-mono tabular-nums">{num(p.component_value, 3)}</td>
                      <td className="font-mono tabular-nums">{num(p.weight, 3)}</td>
                      <td className="font-mono tabular-nums">{num(p.contribution, 3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {c.capability_note && (
                <p className="mt-1 text-[10px] text-muted-foreground">{plain(c.capability_note)}</p>
              )}
            </details>
          </li>
          );
        })}
      </ul>
      <p className="mt-1 text-[10px] text-muted-foreground">
        共 {dash(d.n_components)} 个分量；权重与贡献都取自 analytics.score_detail（这一场这一只票实际用的那一份）。
      </p>
      {/* 这一块的声明收进共用折叠块（用户 2026-10-21：「把页面上的描述性内容全部收缩起来，避免过多占屏」——
          档案里八块各自摊一段口径，一屏大半是字）。纯口径解释那几条折起来，
          缺数／截断／失败那一族仍留在屏上（`CaliberFold` 内部分流）；上面那句「权重之和不为 1」的徽标没跟着折。 */}
      <CaliberFold caveats={d.caveats ?? []} />
    </Shell>
  );
}

function VolumeDouble({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_V;
  const l = d.latest;
  return (
    <Shell title="倍量形态" to="/volume-double" blk={b} hint={d.caliber}>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <KV k="加权分" v={trimNum(l?.dbl_energy, 4)}
          title={'这一块的尺（2026-10-19 第二稿）：近 40 场每一天按「量 ÷ 前 5 日均量（不含当日）」落在哪一档，'
            + '取那一档的分值累加 —— 得分 = Σ 每档（这一档出现的次数 × 这一档的分值）。'
            + "档表与分值在「系统配置 → 倍量加权分档」里可改，口径原文读上面那句 hint。"} />
        <KV k="倍量次数（旧）" v={dash(l?.dbl_count)}
          title={'旧的硬计数：日量比 ≥2 才 +1，1.8 倍与 1.0 倍同样算 0 次。'} />
        <KV k="对昨日倍量次" v={dash(l?.dbl_prev_count)}
          title={'近 40 场里「量 ≥ 2×昨日量」的天数（`volume_double.dbl_prev_count`）。'
            + '与「加权分」是两把尺子：加权分比的是前 5 场均量（不含当日，`vma5`）并按档计几分，这一列比的是昨天。'
            + '昨天缩量会让这个数虚高，所以它只作对照、不用它排序（口径出处＝该列的库注释）。'} />
        <KV k="交叉" v={dash(l?.cross_count)} />
        <KV k="偏离中位" v={num(l?.dev_med, 2)} title="那几天收盘价对 MA60 偏离的中位数（%），越小越贴线" />
        <KV k="线上占比" v={l?.above_ratio == null ? dash(null) : `${num(l.above_ratio, 1)}%`} />
        <KV k="MA60 变化" v={pctv(l?.ma60_chg)} tone={pctColor(l?.ma60_chg ?? null)} />
        <KV k="当日全场名次" v={`第 ${rankText(d.rank_in_market)} / ${dash(d.universe_scored)} 只有分`}
          title={rankTitle(d.rank_rule, { scored: d.universe_scored, rows_total: d.universe },
                           d.rank_in_market)
            + '｜本表全市场每票一行都存，所以这是全场名次，不是页面那张 150 强榜的名次'} />
      </div>
      <table className="mt-1.5 w-full text-[10px]">
        <thead><tr className="text-left text-muted-foreground">
          <th className="font-normal">日期</th><th className="font-normal">加权分</th>
          <th className="font-normal">偏离中位</th><th className="font-normal">线上占比</th>
          <th className="font-normal">MA60 变化</th><th className="font-normal">有效根数</th>
        </tr></thead>
        <tbody>
          {(d.history ?? []).map(r => (
            <tr key={String(r.d)} className="border-t border-border/40">
              <td className="font-mono">{dash(r.d)}</td>
              <td className="font-mono tabular-nums">{trimNum(r.dbl_energy, 4)}</td>
              <td className="font-mono tabular-nums">{num(r.dev_med, 2)}</td>
              <td className="font-mono tabular-nums">{num(r.above_ratio, 1)}</td>
              <td className={cn("font-mono tabular-nums", pctColor(r.ma60_chg ?? null))}>{num(r.ma60_chg, 2)}</td>
              <td className="font-mono tabular-nums">{dash(r.bars)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-muted-foreground">
        近 {dash((d.history ?? []).length)} 场（这张表从 {(d.history ?? []).slice(-1)[0]?.d ?? "—"} 起有这只票的行）；
        累计在场 {dash(d.rows_total)} 场。
      </p>
    </Shell>
  );
}

function Startup({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_U;
  return (
    <Shell title="启动前量能画像" to="/volume-double" blk={b} hint={d.caliber}>
      <table className="w-full text-[10px]">
        <thead><tr className="text-left text-muted-foreground">
          <th className="font-normal">启动日</th><th className="font-normal">母体</th>
          <th className="font-normal">等效涨停</th><th className="font-normal">倍量天</th>
          <th className="font-normal">倍数中位</th><th className="font-normal">凑够根数</th>
        </tr></thead>
        <tbody>
          {(d.rows ?? []).map((r, i) => (
            <tr key={`${String(r.launch_date)}-${String(r.cohort)}-${i}`} className="border-t border-border/40">
              <td className="font-mono">{dash(r.launch_date)}</td>
              <td>{dash(r.board)}｜{dash(r.cohort)}</td>
              <td className="font-mono tabular-nums">{num(r.equiv_limit_ups, 2)}</td>
              <td className="font-mono tabular-nums">{dash(r.dbl_days)}</td>
              <td className="font-mono tabular-nums">{num(r.ratio_median, 3)}</td>
              <td className="font-mono tabular-nums">{dash(r.bars_used)} / {dash(r.window_days)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-muted-foreground">
        判据版本 {dash(d.spec_version)}；凑不满 10 根时那八个分布读数按口径留空（不是缺数）。
      </p>
    </Shell>
  );
}

function Sectors({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_X;
  const chips = (d.sectors ?? []).map(s => ({ ...s, kind: s.sector_type }));
  const cons = (d.concepts ?? []).map(s => ({ ...s, kind: "概念" as string | null | undefined }));
  const all = [...chips, ...cons];
  return (
    <Shell title="板块热度（所属板块）" to="/sector-heat" blk={b} hint={d.caliber}>
      <ul className="flex flex-wrap gap-1.5">
        {all.map((s, i) => (
          <li key={`${String(s.sector_code)}-${String(s.name)}-${i}`}
            className="rounded border border-border/60 px-1.5 py-0.5 text-[10px]">
            <span className="text-muted-foreground">{dash(s.kind)}｜</span>
            <span className="text-foreground">{dash(s.name)}</span>{" "}
            <span className="font-mono tabular-nums">
              热度 {num(s.heat, 2)}｜第 {dash(s.heat_rank)}
              {s.universe ? ` / ${s.universe}` : ""}
            </span>
            {s.members ? <span className="text-muted-foreground">（{s.members} 只成分）</span> : null}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[10px] text-muted-foreground">
        概念共 {dash(d.concept_count)} 个，这里只列最窄的 {dash((d.concepts ?? []).length)} 个
        （选法与四维象限榜同一对函数：同名多板块码取成员最少的那条、窄题材排前面）；
        归属是二值的，库里没有权重，所以没有「这只票在该概念占多少」。
      </p>
    </Shell>
  );
}

function Ambush({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_A;
  const l = d.latest;
  return (
    <Shell title="埋伏票（左侧机会）" to="/volume-breakout" blk={b} hint={d.caliber}>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <KV k="埋伏分" v={num(l?.ambush_score, 2)} />
        <KV k="类型" v={(l?.ambush_type_labels ?? []).join("、") || "—"} />
        <KV k="均线散度" v={l?.ma_spread_pct == null ? dash(null) : `${num(l.ma_spread_pct, 2)}%`}
          title="MA5/10/20/30/60 的散布幅度，越小越「汇集到一起」" />
        <KV k="周线重心连升" v={dash(l?.weekly_streak)} title="每周收盘重心连续上移的周数" />
        <KV k="6 周上涨周数" v={dash(l?.weekly_ups6)} />
        <KV k="箱体内位置" v={l?.box_pos_pct == null ? dash(null) : `${num(l.box_pos_pct, 1)}%`} />
        <KV k="偏离 MA60" v={pctv(l?.dev_ma60_pct, 1)} tone={pctColor(l?.dev_ma60_pct ?? null)} />
        <KV k="严档" v={l?.strict_pass === true ? "通过" : l?.strict_pass === false ? "未过" : "—"} />
        <KV k="当日榜内" v={`第 ${dash(l?.rank_in_board)} 名`} />
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        最近 {dash((d.history ?? []).length)} 次进榜：
        <span className="font-mono">{(d.history ?? []).map(r => String(r.d)).join("、") || "—"}</span>
        ｜累计在场 {dash(d.days_on_board)} 场。
        {d.as_of && d.as_of !== d.board_latest ? (
          <span className="text-warning"> 这一榜全场算到 {dash(d.board_latest)}，
            而这只票最后一次进榜是 {dash(d.as_of)} —— 中间那些天它没进，不是链没跑。</span>
        ) : null}
      </p>
    </Shell>
  );
}

function Breakout({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_B;
  const l = d.latest;
  return (
    <Shell title="突破信号" to="/volume-breakout" blk={b} hint={d.caliber}>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <KV k="类型" v={dash(l?.signal_type_label ?? l?.signal_type)}
          title={tip(l?.signal_type ? (d.type_meanings ?? {})[l.signal_type] : "")} />
        <KV k="放量倍数" v={num(l?.break_vol_ratio, 2)} />
        <KV k="突破偏离" v={num(l?.break_dev, 2)} />
        <KV k="收敛天数" v={dash(l?.converge_days)} />
        <KV k="缩量天数" v={dash(l?.shrink_days)} />
        <KV k="上次放量" v={dash(l?.last_surge_date)} />
        <KV k="那波倍数" v={num(l?.surge_vol_ratio, 2)} />
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        历次按类型：{(d.type_counts ?? []).map(t => `${t.signal_type_label ?? t.signal_type} ${dash(t.n)} 次`
          + `（最近 ${dash(t.last_day)}）`).join("｜") || "—"}
        ；累计 {dash(d.rows_total)} 行。
      </p>
      <p className="mt-1 text-[10px] text-muted-foreground">
        最近 {dash((d.history ?? []).length)} 次信号：
        <span className="font-mono">{(d.history ?? []).map(r => `${String(r.d)}(${String(r.signal_type_label ?? r.signal_type).slice(0, 4)})`).join("、") || "—"}</span>
      </p>
    </Shell>
  );
}

function Confluence({ b }: { b?: Blk }) {
  const d = (b ?? {}) as BLK_C;
  const ranks = d.ranks ?? {};
  const sizes = d.sizes ?? {};
  return (
    <Shell title="选股汇总（交叉命中）" to="/confluence" blk={b} hint={d.caliber}>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <KV k="命中榜数" v={dash(d.hits)} />
        <KV k="命中了哪些榜" v={(d.hit_labels ?? []).join("、") || "—"} />
        <KV k="最好名次" v={`第 ${dash(d.best_rank)} 名` + (d.best_source_label ? `（${d.best_source_label}）` : "")}
          title="名次分位 = 该榜名次 ÷ 该榜这一场的榜长" />
        <KV k="分位" v={d.best_rank_pct == null ? dash(null) : `${num(d.best_rank_pct, 1)}%`} />
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        各榜名次：{Object.keys(ranks).map(k => `${k} 第 ${ranks[k]}/${sizes[k] ?? "?"}`).join("｜") || "—"}
      </p>
      <p className="mt-1 text-[11px]">
        进过榜 {dash(d.days_in_boards)} 场，其中
        <b className={cn("font-mono tabular-nums", (d.repeat_days ?? 0) > 0 ? "text-primary" : "text-muted-foreground")}>
          {" "}{dash(d.repeat_days)} 场是重复出现（≥2 榜）
        </b>
        ；最近一次 {dash(d.last_repeat_day)}。
      </p>
      {(d.sources_absent ?? []).length > 0 && (
        <p className="mt-1 text-[11px] text-warning">
          这一场有源没算出数：{(d.sources_absent_labels ?? []).join("、") || (d.sources_absent ?? []).join("、")}
          —— 当天的命中榜数只数得出剩下的 {dash(d.source_count)} 个源。
        </p>
      )}
    </Shell>
  );
}

export function StockDossier({ code, date }: { code: string | null; date?: string | null }) {
  const [nonce, refresh] = useNonce();
  // 没选中任何一只票时**根本不发请求**（后端会拿一个假代码去查），交回 idle 由 Panel 说明。
  const got = useProbe<Dossier>("/quant/stock-dossier", nonce,
    code ? { code, date: date ?? "" } : undefined, !code);
  const d = got.kind === "ok" ? got.data : null;
  const b = d?.blocks ?? {};

  return (
    <Panel title={code ? `这只票的量化档案 · ${code}${d?.name ? ` ${d.name}` : ""}` : "这只票的量化档案"}
      state={got}
      slowHint="一次请求把八块读数并行取齐（八张表 × 每张 1~2 条查询），首屏实测比串行快一倍多。"
      note="把「量化选股」那一组各自算好的读数按这一只票拼到一起。一块判据都不在这儿重算，页面也只念后端交出的字段。">
      {x => x.available !== true ? (
        // 整份档案不可用只有两种：代码认不出来、或头两步查询失败。两种都要把原文念出来，
        // 不许缩成「这一屏没有数据」—— 那种措辞会让人去查回填，而真正要改的是代码或库。
        <p className="mt-1 text-[11px] leading-relaxed text-warning">
          这份档案没取到：{plain(x.reason ?? "后端既没交出 available:true，也没交出 reason")}
        </p>
      ) : (<>
        <Row label="行情日" value={`${dash(x.quote?.d)}（接口基准日 ${dash(x.date)}）`} />
        <Row label="当日" value={`收盘 ${num(x.quote?.close, 3)}｜涨幅 ${pctv(x.quote?.pct_change)}`
          + `｜换手 ${x.quote?.turnover_rate_percent == null ? "—" : `${num(x.quote?.turnover_rate_percent, 2)}%`}`
          + `｜成交额 ${money(x.quote?.amount_yuan)}｜${dash(x.board)}${x.is_st ? "（ST）" : ""}`} />
        <div className="mt-1 text-[11px] text-muted-foreground">
          代码 <StockLink code={x.code} className="font-mono text-primary underline" />
          ｜上市日 {dash(x.list_date)}
        </div>
        <div className="mt-2 grid items-start gap-2 md:grid-cols-2">
          <Quadrant b={b.quadrant} />
          <Scores b={b.scores} />
          <VolumeDouble b={b.volume_double} />
          <Startup b={b.startup} />
          <Sectors b={b.sectors} />
          <Ambush b={b.ambush} />
          <Breakout b={b.breakout} />
          <Confluence b={b.confluence} />
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          这一块覆盖的整屏：{(x.boards_covered ?? []).map(c => (
            <Link key={String(c.key)} to={String(c.board)} title={tip(c.tables)}
              className="text-primary underline">{c.label}</Link>
          )).reduce<ReactNode[]>((acc, el, i) => i === 0 ? [el] : [...acc, <span key={`s${i}`}>, </span>, el], [])}
          。不覆盖的：{(x.not_covered ?? []).map(n => `${n.where}（${n.why}）`).join("；")}
        </p>
        {/* 声明 / 覆盖区间 / 出处收成一处默认收起的折叠块（用户 2026-10-21：
            「把页面上的描述性内容全部收缩起来，避免过多占屏」—— 这三行原先摊在八块档案的最下面，
            是纯粹的口径与出处说明，不是读数）。
            **没跟着折的**：上面那句「覆盖哪些屏 / 不覆盖哪些」（带链接）、下面的重取按钮、
            取数状态那句 `text-warning`；缺数／截断一族的 caveat 由折叠块自己留在屏上。 */}
        <CaliberFold caveats={x.caveats ?? []} coverage={x.coverage}
          provenance={[x.source, x.caliber]} />
        <button type="button" onClick={refresh}
          className="mt-2 rounded-lg border border-border/60 px-2 py-1 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
          重取这一只票的档案
        </button>
        {got.kind !== "ok" && got.kind !== "idle" && (
          <p className="mt-1 text-[11px] text-warning">{describeState(got)}</p>
        )}
      </>)}
    </Panel>
  );
}

// —— 各块的形状（只声明页面要读的那几格；判据都不在这儿）——
interface BLK_Q extends Blk {
  oper?: { value?: number | null; quadrant?: string | null; quadrant_label?: string | null;
           meaning?: string | null; rank_in_market?: number | null; percent_rank?: number | null;
           is_stale?: boolean | null };
  x_quality?: { value?: number | null; percent_rank?: number | null; rank_in_market?: number | null };
  y_env?: { value?: number | null; percent_rank?: number | null; rank_in_market?: number | null };
  formula?: string | null;
  trend?: { window_days?: number | null; point_count?: number | null; slope?: number | null;
            up_streak_days?: number | null; first_quadrant?: string | null }[] | null;
  quadrant_path?: { path?: string | null; sessions?: number | null; window_days?: number | null;
                    meaning?: string | null } | null;
}
interface BLK_S extends Blk {
  capabilities?: { capability_key?: string | null; capability_label?: string | null;
                   capability_note?: string | null; value?: number | null;
                   rank_in_market?: number | null; percent_rank?: number | null;
                   rank_kind?: string | null; rank_note?: string | null;
                   universe?: number | null; distinct_values?: number | null;
                   tie_at_value?: number | null; biggest_tie?: number | null;
                   quadrant?: string | null; is_stale?: boolean | null;
                   component_count?: number | null; weight_sum?: number | null;
                   weight_sum_ok?: boolean | null; weight_mismatch?: boolean | null;
                   components?: { component_key?: string | null; component_label?: string | null;
                                  component_value?: number | null; weight?: number | null;
                                  contribution?: number | null }[] }[] | null;
  n_components?: number | null;
  names_source?: string | null;
  caveats?: { code?: string; label?: string | null; text?: string }[] | null;
}
interface BLK_V extends Blk {
  latest?: { d?: string | null; dbl_energy?: number | null; dbl_count?: number | null;
             dbl_prev_count?: number | null;
             cross_count?: number | null; dev_med?: number | null; above_ratio?: number | null;
             ma60_chg?: number | null; bars?: number | null } | null;
  history?: NonNullable<BLK_V["latest"]>[] | null;
  /** 名次规则的原文与分母都由后端交（`duanxian/ranking.py` 一份），页面不复述。 */
  rank_rule?: string | null; rank_by?: string | null;
  /** `universe`＝这一场这一批的行数；`universe_scored`＝其中有分的只数（＝名次真正的分母）。 */
  rank_in_market?: number | null; universe?: number | null; universe_scored?: number | null;
  rows_total?: number | null;
}
interface BLK_U extends Blk {
  rows?: { launch_date?: string | null; cohort?: string | null; board?: string | null;
           equiv_limit_ups?: number | null; dbl_days?: number | null; ratio_median?: number | null;
           bars_used?: number | null; window_days?: number | null }[] | null;
  spec_version?: string | null;
}
interface BLK_X extends Blk {
  sectors?: { sector_code?: string | null; name?: string | null; sector_type?: string | null;
              heat?: number | null; heat_rank?: number | null; universe?: number | null;
              part_count?: number | null; members?: number | null }[] | null;
  concepts?: { name?: string | null; sector_code?: string | null; members?: number | null;
               heat?: number | null; heat_rank?: number | null; universe?: number | null }[] | null;
  concept_count?: number | null; heat_source?: string | null;
}
interface BLK_A extends Blk {
  latest?: { d?: string | null; ambush_score?: number | null; ambush_type_labels?: string[] | null;
             ma_spread_pct?: number | null; weekly_streak?: number | null; weekly_ups6?: number | null;
             box_pos_pct?: number | null; dev_ma60_pct?: number | null; strict_pass?: boolean | null;
             rank_in_board?: number | null } | null;
  history?: { d?: string | null }[] | null;
  days_on_board?: number | null; board_latest?: string | null;
}
interface BLK_B extends Blk {
  latest?: { signal_type?: string | null; signal_type_label?: string | null;
             break_vol_ratio?: number | null; break_dev?: number | null; converge_days?: number | null;
             shrink_days?: number | null; last_surge_date?: string | null;
             surge_vol_ratio?: number | null } | null;
  history?: { d?: string | null; signal_type?: string | null; signal_type_label?: string | null }[] | null;
  type_counts?: { signal_type?: string | null; signal_type_label?: string | null;
                  n?: number | null; last_day?: string | null }[] | null;
  type_meanings?: Record<string, string> | null; rows_total?: number | null;
}
interface BLK_C extends Blk {
  hits?: number | null; hit_labels?: string[] | null;
  ranks?: Record<string, number> | null; sizes?: Record<string, number> | null;
  best_rank?: number | null; best_rank_pct?: number | null; best_source_label?: string | null;
  days_in_boards?: number | null; repeat_days?: number | null; last_repeat_day?: string | null;
  sources_absent?: string[] | null; sources_absent_labels?: string[] | null;
  source_count?: number | null; spec_version?: string | null;
}
