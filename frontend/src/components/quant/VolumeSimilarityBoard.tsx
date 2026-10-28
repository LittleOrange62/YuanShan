import { RefreshCw } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { CaliberFold, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
// 与「启动前逐日量能比对」那张表同一批读数格式（一份实现，两处调用）：涨幅是小数不是百分点，
// 等效涨停保留两位，情绪缺值不补 0。
import { equivText, gainText, moodText, moodTone } from "@/lib/startupVolume";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import {
  PART_LABEL, afterSentence, cohortLabel, missingPartsSentence, numOrNull, points, ratio,
  readingSentences, scoreBand, sparkPath, topPartsSentence,
  type SimRow, type SimilarityPayload,
} from "@/lib/volumeSimilarity";

/**
 * 近 30 根量价形状 ＋ 历史最相近样本（用户 2026-10-16：
 * 「把这个股近 30 根有效日线里的量价关系，按照（启动前逐日量能比对）这个表的表头写一份，
 *   并且把那张表里面跟这个个股最相近的几个都摘录出来显示在一起，个股排第一行，
 *   接下来把最相近的 10 个按照相近程度排序」）。
 *
 * 表头与那张表同一把尺子：逐日倍数读 `analytics.volume_ratio_daily`，天数分类与五档区间
 * 调的是 `app/compute/startup_volume` 那一份实现 —— 本屏不重算、不自己数。
 * 四条不许破的线（都在后端一句句交出，页面只念）：
 *  · 相近分是六个分量按权重合成的 0~100，**高不代表会涨**（样本库全是"已经涨起来的票"）；
 *  · 只在同板块档里比，主板 10% 与双创 20% 的形状不并榜；
 *  · 缺的那一维从权重里剔掉再归一 —— 这一行的分不是"被扣了分"，是"按剩下的维算"；
 *  · 启动后 5/10/20 日说的是**那个历史样本后来怎么走**，不是对本票的预测。
 * 窗口方向与那张表相反（这里是"截止今天往前 30 根"），所以那一列的名字是「窗口末」不是「启动日」。
 */

type Row = SimRow & { __target?: boolean };

const COUNT_COLS: [string, string][] = [
  ["up_days", "增"], ["down_days", "减"], ["flat_days", "持平"], ["still_days", "量没变"],
  ["dbl_days", "倍量"], ["near_dbl_days", "准倍量"], ["calm_days", "缩量"], ["long_gap_days", "复牌间隔"],
  ["range_16_18", "1.6-1.8"], ["range_14_16", "1.4-1.6"], ["range_12_14", "1.2-1.4"],
  ["range_10_12", "1.0-1.2"], ["range_08_10", "0.8-1.0"],
];
const STAT_COLS: [string, string][] = [
  ["avg", "均值"], ["median", "中位"], ["p60", "60 分位"], ["p80", "80 分位"], ["p90", "90 分位"],
  ["max", "最大"], ["min", "最小"], ["std", "波动"],
];

function cellCounts(r: Row, key: string): number | null {
  const v = (r.counts ?? {} as Record<string, number | null>)?.[key as keyof SimRow["counts"] & string];
  return numOrNull(v as number | null | undefined);
}

export function VolumeSimilarityBoard({ code }: { code: string | null }) {
  const [nonce, refresh] = useNonce();
  const state = useProbe<SimilarityPayload>("/stock/volume-similarity", nonce,
    { code: code ?? "", k: 10 }, !code);
  const sort = useTableSort({ key: "score", dir: "desc" });
  const it = useStockRowInteraction("量能相似样本");
  const d = state.kind === "ok" ? state.data : null;
  const nb = d?.neighbours ?? [];

  usePageAskAi(nb.length ? {
    label: `量能相似样本（${code}，近 ${dash(d?.window)} 根）`,
    context: [
      `目标窗口 ${dash(d?.target?.window_from)} → ${dash(d?.target?.window_to)}，`
      + `本机有效日线 ${dash(d?.target?.features?.bars)} 根，均值倍数 ${ratio(d?.target?.features?.avg)}，`
      + `板块档 ${dash(d?.target?.features?.board_group)}，窗口末日大盘情绪 ${dash(d?.target?.features?.mood)}。`,
      `样本库 ${dash(d?.library_size)} 个「票·启动日」窗口（${dash(d?.spec_version)}），`
      + `过了硬过滤的同档样本 ${dash(d?.candidates_after_filter)} 个，本榜交出 ${nb.length} 只。`,
      "相近分＝六个分量按权重合成（形状 0.35 / 强度 0.20 / 节奏 0.15 / 量价配合 0.15 / 情绪 0.10 / 可比性 0.05），缺的维不算。",
      nb.map(n => `${n.name ?? ""}(${n.security_code}) ${n.launch_date} 分 ${dash(n.score)} `
        + `${scoreBand(n.score).label}｜像在哪：${topPartsSentence(n)}｜${cohortLabel(n)}｜`
        + `倍量 ${dash(n.counts?.dbl_days)} 天、均值 ${ratio(n.stats?.avg)}、窗口涨幅 ${points(n.gain_points)}｜`
        + `启动后 ${afterSentence(n)}`).join("\n"),
      (d?.caveats ?? []).map(c => `· ${c}`).join("\n"),
    ].join("\n"),
    suggestions: ["这些相似样本启动后 5/10/20 日的分布说明了什么", "本票与最像的那一只，差在哪两维"],
  } : null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">近 30 根量价形状 · 历史最相近样本</h2>
        <Caliber text={
          "表头与「启动前逐日量能比对」那张表同一把尺子：倍数读 analytics.volume_ratio_daily，"
          + "天数分类与五档区间调 app/compute/startup_volume 那一份实现，这一屏不重算。\n"
          + "唯一区别是窗口方向：那张表是「启动日往前 30 根」，这里是「截止今天往前 30 根」，"
          + "所以第一块那一列叫「窗口末」不叫「启动日」。\n"
          + "相近分与权重理由、样本库大小那句声明都由后端交出，页面不自己编。"
        } />
        <button type="button" onClick={() => { it.clearAll(); refresh(); }} title="重新取数"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      <Panel title="这一只最近的量能形状，与历史上最像它的十只" state={state}
        note="本票排第一行（深色标出），随后十只按相近分降序；点列头排序只排这十只，第一行不动。">
        {p => {
          const t = p.target ?? null;
          const targetRow: Row = {
            security_code: p.code, name: t?.name, board: t?.board,
            launch_date: t?.window_to, mood: t?.features?.mood ?? null,
            bars_used: t?.features?.bars ?? null, counts: t?.counts ?? null,
            stats: { avg: t?.features?.avg ?? null, median: t?.features?.median ?? null,
              p60: t?.features?.p60 ?? null, p80: t?.features?.p80 ?? null,
              p90: t?.features?.p90 ?? null, max: t?.features?.max ?? null,
              min: t?.features?.min ?? null, std: t?.features?.std ?? null },
            gain_points: t?.features?.price_volume?.gain_points ?? null,
            __target: true,
          };
          const sorted = sort.apply(nb);
          const rows: Row[] = [targetRow, ...sorted.map(n => ({ ...n }))] as Row[];
          const idx = new Map<Row, number>();
          rows.forEach((r, i) => idx.set(r, i));
          const noDef = (k: string) => (p.definitions ?? {})[k]
            ?? `后端 definitions 里没有 ${k} 这一条 —— 这一列的含义没有出处，页面不自己编`;
          const Th = (label: string, key: string, tip?: string) => (
            <SortHeader label={label} sortKey={key} dir={sort.dirOf(key)}
              onSort={() => { it.clearAll(); sort.toggle(key); }} title={tip ?? noDef(key)} />
          );
          const td = (r: Row, key: string, cls = "px-1.5 py-1 font-mono tabular-nums text-[11px]") => {
            const i = idx.get(r) ?? 0;
            return r.__target ? { className: cls } : { ...it.cellProps(i, key), className: cls };
          };

          return (
            <>
              <div className="mb-2 rounded-lg border border-border/60 bg-muted/20 p-2">
                {readingSentences(p).map((s, i) => (
                  <p key={i} className="text-[11px] leading-relaxed text-muted-foreground">{s}</p>
                ))}
              </div>
              {/* 声明 + 表下那两句散文（取数口径原句、字段义字典）收成一处默认收起的折叠块
                  （用户 2026-10-21：「把页面上的描述性内容全部收缩起来，避免过多占屏」——
                  原先口径那句摊在表前、字段义摊在表后，两处各占一段才凑得齐一张表）。
                  **没跟着折的**：上面那格读数（样本库多少、过了硬过滤多少只）、表里逐行的「缺哪一维」
                  那句 `text-warning`、`it.popHint`；缺数／截断一族的 caveat 由折叠块自己留在屏上。 */}
              <CaliberFold caveats={p.caveats ?? []} definitions={p.definitions}
                notes={[p.caliber ? `取数口径（后端原句，页面未改写）：${p.caliber}` : null]} />

              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      {Th("代码", "security_code")}{Th("名称", "name")}{Th("板块", "board")}
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title={noDef("window_to")}>
                        窗口末 / 启动日</th>
                      {Th("情绪", "mood")}
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title="那一只当年是几连板（本票这一格没有：它是「截止今天」的一段窗口，不是涨停启动日）">连板</th>
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title="启动区间涨幅（百分点）">区间涨幅</th>
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title="前 4 日涨幅（百分点）">前4涨幅</th>
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title="等效涨停数：把 20%/30% 档的涨幅折算成 10% 档的板数">等效涨停</th>
                      {Th("用了几根", "bars_used")}
                      {COUNT_COLS.map(([k, lab]) => Th(lab, `counts.${k}`))}
                      {STAT_COLS.map(([k, lab]) => Th(lab, `stats.${k}`))}
                      {Th("窗口涨幅", "gain_points")}
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title="30 根倍数逐日连线（空那格是那天没有效日线，不画成 0）">走势</th>
                      {Th("相近分", "score")}
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title="六个分量各自的分（满分 100），没算的那一维不在里面">像在哪</th>
                      <th className="whitespace-nowrap px-1.5 py-1 font-medium" title="那一只启动日之后 5/10/20 个交易日的累计涨跌幅（百分点之和）。说的是那个样本后来怎么走，不是对本票的预测">启动后</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => {
                      const spark = r.__target ? sparkPath(t?.ratios) : { path: "", gaps: [] };
                      const band = scoreBand(r.score);
                      const i = idx.get(r) ?? 0;
                      return (
                        <tr key={`${r.security_code}-${r.launch_date ?? ""}`}
                          {...(r.__target ? { className: "bg-primary/10" } : it.rowProps(i, r.security_code))}
                          className={cn(r.__target && "bg-primary/10", !r.__target && "border-t border-border/40")}>
                          <td {...td(r, "security_code")}>
                            <StockLink code={r.security_code} name={r.name} className="font-medium hover:text-primary" />{" "}
                            <span className="text-[10px] text-muted-foreground/60">{r.security_code}</span>
                            {r.__target && <span className="ml-1 rounded bg-primary/20 px-1 text-[10px] text-primary">本票</span>}
                          </td>
                          <td {...td(r, "name")}>{dash(r.name)}</td>
                          <td {...td(r, "board")}>{dash(r.board)}</td>
                          <td {...td(r, "launch_date")}>{dash(r.launch_date)}</td>
                          <td {...td(r, "mood")} className={cn("px-1.5 py-1 font-mono tabular-nums text-[11px]",
                            moodTone(r.mood) === "hot" ? "text-primary font-semibold"
                              : moodTone(r.mood) === "cold" ? "text-destructive"
                                : moodTone(r.mood) === "flat" ? "text-muted-foreground" : "")}>
                            {moodText(r.mood)}</td>
                          <td {...td(r, "trigger_len")}>{r.__target ? "—" : dash(r.trigger_len)}</td>
                          <td {...td(r, "trigger_gain")}>{r.__target ? "—" : gainText(r.trigger_gain)}</td>
                          <td {...td(r, "gain_first4")}>{r.__target ? "—" : gainText(r.gain_first4)}</td>
                          <td {...td(r, "equiv_limit_ups")}>{r.__target ? "—" : equivText(r.equiv_limit_ups)}</td>
                          <td {...td(r, "bars_used")}>{dash(r.bars_used)}</td>
                          {COUNT_COLS.map(([k]) => <td key={k} {...td(r, k)}>{dash(cellCounts(r, k))}</td>)}
                          {STAT_COLS.map(([k]) => (
                            <td key={k} {...td(r, `stats.${k}`)}>{ratio(r.stats?.[k as keyof NonNullable<SimRow["stats"]>] ?? null)}</td>
                          ))}
                          <td {...td(r, "gain_points")}>{points(r.gain_points)}</td>
                          <td className="px-1.5 py-1">
                            {spark.path ? (
                              <svg width="92" height="22" className="overflow-visible">
                                <path d={spark.path} fill="none" stroke="currentColor"
                                  className="text-primary" strokeWidth="1.2" />
                              </svg>
                            ) : <span className="text-[10px] text-muted-foreground/50">—</span>}
                          </td>
                          <td {...td(r, "score")}>
                            {r.__target ? <span className="text-muted-foreground">基准行</span> : (
                              <span className={cn("font-semibold",
                                band.tone === "high" ? "text-primary" : band.tone === "mid" ? "text-foreground"
                                  : band.tone === "missing" ? "text-muted-foreground" : "text-muted-foreground")}>
                                {dash(r.score)} <span className="text-[10px]">{band.label}</span>
                              </span>
                            )}
                          </td>
                          <td className="px-1.5 py-1 text-[10px] leading-tight text-muted-foreground">
                            {r.__target ? "—" : (
                              <>
                                {topPartsSentence(r)}
                                {missingPartsSentence(r) && (
                                  <div className="mt-0.5 text-warning">{missingPartsSentence(r)}</div>
                                )}
                              </>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-1.5 py-1 text-[10px] text-muted-foreground">
                            {r.__target ? "—" : afterSentence(r)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* 弹窗被拦的告警是**告警**，留在框外先念；"怎么点"那句描述收进
                  默认收起的说明框（用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」） */}
              {it.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{it.popHint}</p>}
              <ExplainBox title="这一张表怎么点 · 单击高亮什么 · 双击去哪里">
                <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
              </ExplainBox>
              <ExplainBox title="相近分到底怎么算出来的（六个分量、权重、缺维怎么办）">
                <p>
                  {(Object.entries(p.weights ?? {}) as [string, number][]).map(([k, w]) =>
                    `${PART_LABEL[k] ?? k} ${Math.round(w * 100)}%`).join(" · ")}。
                  形状比的是 12 格占比向量的余弦；强度比的是 p60/p80/p90/中位/最大各自除以自身均值后的欧氏距离；
                  节奏比的是近 10 根均倍数 ÷ 前 20 根与波动率；量价配合比的是窗口内逐日涨跌幅之和与涨天占比
                  （方向不同要打折）；大盘情绪比的是两端窗口末日的分差 ÷ 200；可比性只认同板块档同 ST。
                  {plain(p.score_note ?? "")}
                </p>
                <p className="mt-1">
                  为什么只和"进入过启动母体的窗口"比：本机没有把全市场历史上每个 30 根窗口都存下来，
                  样本库就是那批已经涨过一次的票。所以这一屏能回答
                  <b className="text-foreground">"历史上谁以这种量能形状启动过、之后走得怎样"</b>，
                  不能回答<b className="text-foreground">"这种形状出现后全市场平均会怎样"</b>。
                </p>
              </ExplainBox>
            </>
          );
        }}
      </Panel>
    </div>
  );
}
