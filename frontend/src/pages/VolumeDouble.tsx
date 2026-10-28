import { RefreshCw } from "lucide-react";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { CaliberFold, ExplainBox, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { rankText, rankTitle, trimNum } from "@/lib/ranking";
import { num } from "@/lib/qd";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { StockLink } from "@/components/ui/StockLink";
import { StartupVolumeBoard } from "@/components/quant/StartupVolumeBoard";
import { FirstBoardShapeBoard } from "@/components/quant/FirstBoardShapeBoard";
import { StartupQuadrantTrackBoard } from "@/components/quant/StartupQuadrantTrackBoard";
import { StartupSpikeDaysBoard } from "@/components/quant/StartupSpikeDaysBoard";
import { ConceptBoard, type ConceptRow } from "@/components/quant/ConceptBoard";
import { EnergyTrackBoard } from "@/components/quant/EnergyTrackBoard";
import { FilterToggleButton, TableFilterPanel } from "@/components/ui/TableFilterPanel";

/**
 * 倍量形态（规划 §2 维度二）。
 *
 * 每列都是接口 `definitions` 里解释过的统计量，页面只负责摆出来 + 把口径写清：
 * 加权分与旧倍量数同一把基准 —— 分母是**前 5 日均量、不含当日**；MA60 不满 60 根 K 线记 NULL
 * （次新股不凑数）；`cross_count=0` 是单边、不算震荡。这些句子后端已经给了，这里逐字透出，不缩写。
 *
 * ⚠️ 2026-10-19 换了尺（用户裁定），同日第二稿定算式（口径改于 2026-10-19 第二稿）：
 * 主列是 `dbl_energy`（加权分），算式原文读接口 `energy.scale.formula`
 * ＝「每档（这一档在窗口里出现的次数 × 这一档的分值）累加」——
 * **没有系数、没有占比、没有归一化**（第一稿那句「占比＝分值÷Σ分值」已被用户否掉），
 * 所以这一屏不再念分母，只念 scale 给的上限与实测分位。
 * 档位分值在「系统配置 → 倍量加权分档」里可改。
 * 旧计数退役：payload 里仍交、但改名 `dbl_count_legacy`，**不上屏、不参与筛**，只进悬停作对照。
 * 名次那一列（`rank_in_day`）的规则原文读接口 `rank.rule`，实现只住 `lib/ranking.ts` 一处。
 */

interface RowX {
  code?: string | null; name?: string | null; board?: string | null;
  win_days?: number | null; bars?: number | null; ma60_days?: number | null;
  /** 倍量加权分（2026-10-19 起的主列）：窗口内每一天按它所在档的分值累加（算式读接口 scale.formula）。
   *  缺数＝null（显示 —），**不当 0 分** —— 0 是「这 40 天一次量都没放过」，与「没数」是两件事。 */
  dbl_energy?: number | null;
  /** 当日名次（按 dbl_energy 排）。规则原文读接口 `rank.rule`，缺分的是 null：
   *  显示 —，**不是 0 名**（名次公用件 `lib/ranking.ts`，组件不自造规则那句话）。 */
  rank_in_day?: number | null;
  /** 旧口径的硬计数（日量比 ≥2.0 才算一次）：**已退役**，不在列上、不参与筛，只进悬停作对照 */
  dbl_count_legacy?: number | null;
  dbl_prev_count?: number | null; cross_count?: number | null;
  dev_med?: number | null; dev_max?: number | null; above_ratio?: number | null;
  /** 加权分较前一整窗的增减（10-19 起是**浮点**）= 本窗 dbl_energy − 同一只票往前 40 **行**那场的 dbl_energy。
   *  没有可比前窗时 = null（显示 —），**不是 0**：0 的意思是「本窗比前窗一点没多」，是读数；
   *  — 是「没有可比的前一整窗」。每行带 `dbl_step_gap_null_reason`（三种之一）与那句原因。 */
  dbl_step_gap?: number | null;
  dbl_step_gap_null_reason?: string | null;
  dbl_step_gap_null_note?: string | null;
  /** 贴 MA60：|dev_med| ≤ 3% 记为 true；null＝算不出 dev_med，不是不贴 */
  near_ma60?: boolean | null;
  ma60_chg?: number | null; avg_amt_yuan?: number | null;
  /** 榜尾两列涨幅：**后复权**口径（今日 = 目标日÷前一市场场，15 日 = 目标日÷往前第 15 场）。
   *  缺行（基期那天停牌/未上市）= null，**不是 0%（平盘）**；口径文字由后端 `pct_rule` 交。 */
  chg_today?: number | null; chg_15?: number | null;
  pct_bars?: number | null; pct_base_day?: string | null;
  /** 四维象限（同一天 `analytics.score` 的原值，不是 0~100 的百分，可为负；缺数=null，不是 0） */
  quadrant?: string | null; quadrant_label?: string | null;
  oper_value?: number | null; x_quality?: number | null; y_env?: number | null;
  rank_in_market?: number | null; percent_rank?: number | null; is_stale?: boolean | null;
}
/** 反向概念榜的行形状住在共用那块里（`ConceptBoard`），这里只引用、不重定义一份。 */
interface VolumeDouble {
  available?: boolean | null; reason?: string | null; date?: string | null; source?: string | null;
  window_days?: number | null; rows?: RowX[] | null; count?: number | null;
  filter?: { /** 后端真应用的加权分下限（2026-10-19 起取代 `min_count`，浮点） */
             min_energy?: number | null; rows_returned?: number | null; limit?: number | null;
             /** 后端真应用了的分段差下限（走后端筛）；null＝没给、不筛 */
             step_gap_min?: number | null;
             /** 后端真应用了的象限档（多选）；`quadrants_rejected` 是没认得的原始值 */
             quadrants?: string[] | null; quadrants_rejected?: string[] | null } | null;
  /** 「稳步提升」阈值（只住后端 STEP_GAP_MIN 一处，界面从这里读，不自己写数字）。
   *  第二稿起这一族三个数都随 `thresholds` 交出：分段差下限、逐日走势的母体档（前 N 名）、每页行数；
   *  以前那个 `min_energy_default` 已经不在接口里了（门槛的默认就是 `filter.min_energy`＝0）。 */
  thresholds?: { step_gap_min?: number | null; top_n_default?: number | null;
                 page_size?: number | null } | null;
  /** 加权分那把尺自己：生效指纹、库里是否同一批、这一份配置的数学上限与实测分布。
   *  界面读它，不抄一份档表进界面（判据只住 app/compute/volume_energy.py 一处）。
   *  第二稿：`scale.formula` 是算式原文，**没有 normalize／量纲那一条**。 */
  energy?: { fingerprint?: string | null; in_sync?: boolean | null; profile_source?: string | null;
             scale?: { window_days?: number | null; max_per_day?: number | null;
                       max_score?: number | null; unit?: string | null; formula?: string | null;
                       note?: string | null } | null;
             distribution?: { session?: string | null; rows?: number | null; with_value?: number | null;
                              p50?: number | null; p90?: number | null;
                              p99?: number | null; max?: number | null; min?: number | null;
                              at_or_above_zero?: number | null } | null;
             stored_batches?: { fp?: string | null; rows?: number | null; sessions?: number | null }[] | null } | null;
  /** 名次这一列的规则原文与分母（后端 `duanxian/ranking.py` 一份，界面只转述不自造）。
   *  `universe.scored`＝当日有分的只数（参与排名的母体），缺数就是没读到，不折成 0。 */
  rank?: { rule?: string | null; rule_short?: string | null; by?: string | null;
           universe?: { scored?: number | null; rows_total?: number | null;
                        top_score?: number | null; unscored?: number | null } | null } | null;
  /** 这一场「加权分≥min_energy」母体里，较前窗增减 ≥阈值 实有几只（不受取数上限影响）。
   *  hits=null 是「那条聚合没读到」，不是 0 只。 */
  step_gap_pool?: { min?: number | null; hits?: number | null;
                    uncomparable?: number | null; total?: number | null } | null;
  /** 每一档**全场**实有几只 + 标签 + 含义（聚合失败时交 null，不是 0） */
  quadrant_options?: { value?: string | null; label?: string | null;
                       count?: number | null; meaning?: string | null }[] | null;
  definitions?: Record<string, string> | null;
  /** 象限那一列的轴语义/合成式/四档含义：与「四维象限」页同一份，页面不自己重写 */
  quadrant_axes?: {
    x?: { capability_key?: string | null; composition?: Record<string, number> | null };
    y?: { capability_key?: string | null; composition?: Record<string, number> | null };
    oper_formula?: string | null; meaning?: Record<string, string> | null;
    labels?: Record<string, string> | null; score_note?: string | null;
  } | null;
  /** 反向聚合：这批票的概念板块出现次数（分母就是本榜交出的行数） */
  concepts?: ConceptRow[] | null;
  /** 榜尾两列涨幅的口径文字 / 跨度 / 基期那一场（判据只住 duanxian/day_returns.py） */
  pct_rule?: string | null; pct_span?: number | null; pct_base_day?: string | null;
  concept_pool?: { size?: number | null; covered?: number | null; concepts_total?: number | null; single_stock?: number | null } | null;
  coverage?: Record<string, { from?: string | null; to?: string | null; sessions?: number | null }> | null;
  caveats?: { code?: string | null; text?: string | null }[] | null;
}

/** 只看"真震荡"：有翻转、且收在上方的比例不在两端 —— 阈值来自接口 definitions 的解释，不自己发明 */
const OSCILLATING = (r: RowX) => (r.cross_count ?? 0) >= 2 && (r.above_ratio ?? 0) > 5 && (r.above_ratio ?? 0) < 95;

/** 倍量榜一页多少行。这里以前是硬切 60 行且不告诉读者后面还有多少 ——
 *  用户 2026-10-14 要「≥5 次的都展示出来」（实测这一场 381 只），静默截到 60 就是又一处屏幕假话。
 *  那一句话里的「次」是旧口径（2026-10-19 已换成加权分），保留在这里只作为历史坐标。
 *  第二稿起每页行数读接口 `thresholds.page_size`（出厂值只住后端一处），下面这个数只是
 *  接口没交出那一格时的兜底，画面上页脚说的仍是界面真正在切的那个数。 */
const PAGE_FALLBACK = 60;

export function VolumeDouble() {
  const [nonce, refresh] = useNonce();
  const [onlyOsc, setOnlyOsc] = useState(false);
  const [date, setDate] = useState("");
  /** 「最少加权分」下限（2026-10-19 前这一格是「最少倍量次」`min_count`，整数）。
   *  单位是分不是次数：满窗上限读接口 `energy.scale.max_score`，界面不自己算。 */
  const [minEnergy, setMinEnergy] = useState(0);
  /** 取数上限（后端硬顶 800）。为什么要在界面上给这一格：门槛放低时满足的票远多于默认上限，
   *  静默切到 150 行就是「只在看得到的一截里说都在这儿」，所以这一格要能自己抬到够装为止。 */
  const [limit, setLimit] = useState(150);
  /** 四维象限多选（用户 2026-10-13）。这一格走**后端参数**：榜默认只交出 150 行，
   *  在前端筛就只能筛到"加权分最高的那 150 只"，选 Q2 剩 3 行会被读成"全场只有 3 只 Q2"
   *  （实测 2026-10-12 全场 1,151 只）。其余数值那几格仍是前端筛，两种范围在下面说明里分开写。 */
  const [quadrants, setQuadrants] = useState<string[]>([]);
  /** 「只看稳步提升」＝把后端 `thresholds.step_gap_min` 当参数发出去（库里筛、拼在取数上限之前）。
   *  null＝没给、不筛。阈值数字从接口读，界面不自己写（换尺之后它是浮点，且随换尺一起改档）。 */
  const [stepGapMin, setStepGapMin] = useState<number | null>(null);
  const hits = useProbe<VolumeDouble>(`/quant/volume-double?date=${encodeURIComponent(date)}`
    + `&min_energy=${minEnergy}&limit=${limit}&quadrants=${encodeURIComponent(quadrants.join(","))}`
    + (stepGapMin !== null ? `&step_gap_min=${stepGapMin}` : ""), nonce);
  const vdSort = useTableSort();
  /** 倍量榜的页码（越界由渲染处夹紧，不在这里做校验） */
  const [page, setPage] = useState(1);
  /** 单击一格 → 那一整行与那一整列一起高亮；双击一行 → 另开浏览器窗口看这只票的「个股深度复盘」
   *  （全站一份实现：`lib/useStockRowInteraction.ts`，用户 2026-10-15）。原来这份 `selectedIdx`
   *  只做整行高亮 —— 这一屏每行就是一只票，没有编组展开行；行号仍按整批序号 gi 记
   *  （与迁移前存 gi 同一份理由：高亮跟着整批的票走，不随翻页漂到别的票身上）。
   * 下面那块「进榜后逐日走势」用的是**第二份实例**（`track`）：两张表各记各的选中态，
   *  同一次点击不该把另一张表的第 3 行也点亮。 */
  const vd = useStockRowInteraction("倍量形态");
  const track = useStockRowInteraction("加权分进榜后走势");
  const sortVd = (k: string) => { vd.clearAll(); vdSort.toggle(k); };
  /** 筛选面板展开状态 */
  const [showFilters, setShowFilters] = useState(false);
  /** 各列的筛选条件 */
  const [filters, setFiltersState] = useState<{
    board?: string;
    dbl_energy_min?: number;
    dbl_energy_max?: number;
    cross_count_min?: number;
    dev_med_min?: number;
    dev_med_max?: number;
    above_ratio_min?: number;
    above_ratio_max?: number;
    near_ma60?: boolean;
    chg_today_min?: number;
    chg_today_max?: number;
    chg_15_min?: number;
    chg_15_max?: number;
  }>({});

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="倍量形态" level={1}
        subtitle="40 场窗口的量能加权分（日量比分档累加）、离 MA60 多远、窗口内翻转过几次。全部来自本地日线，不看盘口。"
        actions={<div className="flex items-center gap-2">
          {/* 同一族的现象：倍量形态看的是"哪些天放量"（日线），异动筛查看的是"那一天盘里是怎么冲上去的"
              （分钟线）。两个入口读的是各自的表，不共用筛条件，所以这里只放一条路标、不搬数据。 */}
          <Link to="/spike" className="rounded-lg border border-border/60 px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/50 hover:text-primary">
            盘中怎么冲上去的 · 异动筛查
          </Link>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <input type="checkbox" checked={onlyOsc}
              onChange={e => { setOnlyOsc(e.target.checked); vd.clearAll(); }} />
            只看真震荡（有上下翻转、且收在上方的比例不在两端）
          </label>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
            日期：<input type="date" value={date} onChange={e => { setDate(e.target.value); vd.clearAll(); }}
              className="w-28 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
          </label>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
            title="最少加权分（2026-10-19 起的参数名 min_energy）。单位是「分」不是次数：加权分＝窗口内每一天按它所在档的分值累加（算式原文读接口 energy.scale.formula，没有占比也没有系数），顶档一天记几分由系统配置那张档表决定。所以这一格填多少要照 energy.scale.max_score 与 energy.distribution 的分位来，不能照旧口径「倍量次」的直觉。能填 0~40 是端点的边界，不是尺子的上限。">
            最少加权分：<input type="number" min={0} max={40} step={0.05} value={minEnergy}
              onChange={e => {
                const raw = e.target.value.trim();
                const n = raw === "" ? 0 : Number(raw);
                setMinEnergy(Number.isFinite(n) ? Math.min(40, Math.max(0, n)) : 0);
                setPage(1); vd.clearAll();
              }}
              className="w-16 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
          </label>
          <span className="text-[11px] leading-relaxed text-muted-foreground">
            单位是分、不是次数，满窗上限见 <code>energy.scale.max_score</code>＝
            <b className="text-foreground">{trimNum(hits.kind === "ok" ? hits.data.energy?.scale?.max_score : null)}</b>
            （现在这把尺：{plain(hits.kind === "ok" ? hits.data.energy?.scale?.unit : null) || "后端没交 unit"}）
          </span>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
            title="一次最多从库里取多少行（后端硬顶 800）。它是截断用的上限，不是筛条件：满足「最少加权分」的票若比它多，榜尾会明说「只是前 N 名」。">
            取数上限：<input type="number" min={1} max={800} value={limit}
              onChange={e => { setLimit(Math.min(800, Math.max(1, parseInt(e.target.value || "1", 10)))); vd.clearAll(); }}
              className="w-16 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
          </label>
          <button type="button" onClick={refresh}
            className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
            <RefreshCw className="h-3.5 w-3.5" /> 重新取数
          </button>
        </div>} />

      <Panel title="倍量榜" state={hits}
        note="榜单按 dbl_energy（40 场窗口的量能加权分）排；算式原文在接口 energy.scale.formula（就在下面那一行，页面不抄第二份）——每档「这一档在窗口里出现几场 × 这一档的分值」累加，没有系数、没有占比；日量比那把基准（前 5 场均量、不含当日）见 definitions.dbl_energy 原文。档位分值在「系统配置 → 倍量加权分档」里可改，名次（rank_in_day）与它同一把尺、规则原文读接口 rank.rule。旧口径的硬计数 dbl_count 已退役，改名 dbl_count_legacy 只在悬停里作对照，不上屏、不参与筛。对昨倍量 dbl_prev_count 也只作对照口径，昨天缩量会让它虚高。">
        {d => {
          const all = d.rows ?? [];
          const ax = d.quadrant_axes ?? {};
          const qTip = [
            "「四维象限」页同一场的综合分原值（同一天 analytics.score 的 oper_score，不是本页算出来的），可为负。",
            ax.oper_formula ? `合成式：${ax.oper_formula}` : "",
            ax.score_note ?? "",
            ...Object.entries(ax.meaning ?? {}).map(
              ([k, v]) => `${k}${(ax.labels ?? {})[k] ? `（${(ax.labels ?? {})[k]}）` : ""}：${v}`),
            "缺数显示 —：那天 score 表里没有这只票的 oper_score 行，不当 0 分、也不当 0 位。",
          ].filter(Boolean).join("\n");
          const rows = onlyOsc ? all.filter(OSCILLATING) : all;
          // 「满足最少加权分的全场实有几只」＝各档象限计数相加：后端那条聚合不带象限条件、
          // 也不受取数上限影响，所以它是"应该有多少"，与榜上"交出了多少"是两个数。
          // 任何一档没读到就不给合计（半个合计会被读成全场）。
          const qcs = (d.quadrant_options ?? []).map(o => o.count);
          const qcTotal = qcs.length > 0 && qcs.every(c => typeof c === "number")
            ? qcs.reduce((a, c) => a + (c as number), 0) : null;
          
          // 应用筛选（象限与「稳步提升」那两格是后端筛，其余这几格是在已交出的行里筛 —— 计数一起算，徽章才诚实）
          const activeFilterCount = Object.values(filters).filter(v => v !== undefined).length
            + (quadrants.length > 0 ? 1 : 0)
            + (d.filter?.step_gap_min != null ? 1 : 0);
          const filteredRows = rows.filter(r => {
            if (filters.board && r.board !== filters.board) return false;
            // 加权分那一格：没有分的票**不折进 0**（0 是「这 40 天一次量都没放过」），
            // 所以给了下限就把它挡在外面（走 -Infinity，与 dev_med 同一族写法）
            if (filters.dbl_energy_min !== undefined && (r.dbl_energy ?? -Infinity) < filters.dbl_energy_min) return false;
            if (filters.dbl_energy_max !== undefined && (r.dbl_energy ?? Infinity) > filters.dbl_energy_max) return false;
            if (filters.cross_count_min !== undefined && (r.cross_count ?? 0) < filters.cross_count_min) return false;
            if (filters.dev_med_min !== undefined && (r.dev_med ?? -Infinity) < filters.dev_med_min) return false;
            if (filters.dev_med_max !== undefined && (r.dev_med ?? Infinity) > filters.dev_med_max) return false;
            if (filters.above_ratio_min !== undefined && (r.above_ratio ?? 0) < filters.above_ratio_min) return false;
            if (filters.above_ratio_max !== undefined && (r.above_ratio ?? 100) > filters.above_ratio_max) return false;
            if (filters.near_ma60 !== undefined && r.near_ma60 !== filters.near_ma60) return false;
            if (filters.chg_today_min !== undefined && (r.chg_today ?? -Infinity) < filters.chg_today_min) return false;
            if (filters.chg_today_max !== undefined && (r.chg_today ?? Infinity) > filters.chg_today_max) return false;
            if (filters.chg_15_min !== undefined && (r.chg_15 ?? -Infinity) < filters.chg_15_min) return false;
            if (filters.chg_15_max !== undefined && (r.chg_15 ?? Infinity) > filters.chg_15_max) return false;
            return true;
          });
          // 榜一屏一页地画（一页 PAGE 行）。每页行数读接口 `thresholds.page_size`
          // （出厂值只住后端 volume_energy.PAGE_SIZE 一处，界面不写死 60；读不到才用上面那个兜底）。
          // 行号按**整批的序号**记：选中态存的是行号，
          // 用页内下标的话，翻到第 2 页时第 1 页的同一行会跟着亮（张冠李戴的那种亮法）。
          const PAGE = Number.isFinite(d.thresholds?.page_size ?? NaN) && (d.thresholds?.page_size ?? 0) > 0
            ? Math.trunc(d.thresholds?.page_size as number) : PAGE_FALLBACK;
          const sorted = vdSort.apply(filteredRows);
          const pages = Math.max(1, Math.ceil(sorted.length / PAGE));
          const cur = Math.min(Math.max(1, page), pages);
          const shown = sorted.slice((cur - 1) * PAGE, cur * PAGE)
            .map((r, i) => ({ r, gi: (cur - 1) * PAGE + i }));
          // 涨幅两列：跨度与口径文字都以后端交出的为准（判据只住 duanxian/day_returns.py）。
          // 窗口场数 = 跨度 + 1（目标日也算一场），这个 +1 只在页面拼一次，不各处散写。
          const SPAN = d.pct_span ?? 15;
          const SPAN_WINDOW = SPAN + 1;
          const pctRule = d.pct_rule
            ?? "这一列的口径文字后端没交（按涨幅排还是能点的；要看复权与基期说明请重新取数）";
          // 库里批次 vs 生效配置：三态分开说（同批 / 旧批 / 没读到）。"没读到"不许说成"没问题"。
          const syncText = d.energy?.in_sync === true ? "库里最新批次与生效配置同指纹"
            : d.energy?.in_sync === false
              ? "库里还是旧批：这一榜筛的不是当前档表算出来的分（上面 caveat energy_batch_stale 说的就是它）"
              : "库里批次与生效配置是否同批：没读到（不是没问题，也不是有问题）";
          return (
            <>
              <Row label="基准交易日" value={dash(d.date)} />
              <Row label="观察窗" value={`${dash(d.window_days)} 个交易日`} />
              <Row label="加权分这把尺" value={`${dash(d.energy?.fingerprint)}｜${syncText}｜`
                + `算式（energy.scale.formula 原话）：${plain(d.energy?.scale?.formula) || "后端没交 formula，这一列的算式没有出处，页面不自己补一句"}｜`
                + `一天最多拿 ${trimNum(d.energy?.scale?.max_per_day)}、满窗上限 ${trimNum(d.energy?.scale?.max_score)}`
                + `（单位：${plain(d.energy?.scale?.unit) || "后端没交 unit"}）`
                + `｜这场实测 p50 ${trimNum(d.energy?.distribution?.p50)}、p90 ${trimNum(d.energy?.distribution?.p90)}、`
                + `p99 ${trimNum(d.energy?.distribution?.p99)}、最高 ${trimNum(d.energy?.distribution?.max)}`}
                tone={d.energy?.in_sync === false ? "warn" : undefined} />
              {/* 名次那一列的规则原文与分母都读接口（实现只住 `lib/ranking.ts` 一处，页面不自造那句话）。
                  缺分的票不在这把尺上：那一格是 —，不是 0 名。 */}
              <Row label="名次这把尺"
                value={`${plain(d.rank?.rule_short ?? "后端没交 rank.rule_short —— 名次规则没有出处，页面不自己编一句")}`
                  + `｜分母：当日有分的 ${dash(d.rank?.universe?.scored)} 只（这一场库里 ${dash(d.rank?.universe?.rows_total)} 行，`
                  + `没有分的 ${dash(d.rank?.universe?.unscored)} 只）`
                  + `｜排的是 ${dash(d.rank?.by)}`} />
              <Row label="交出多少行" value={`${all.length} 行${onlyOsc ? `（勾了真震荡后剩 ${rows.length} 行）` : ""}${filters.board || filters.dbl_energy_min || filters.cross_count_min || filters.dev_med_min || filters.dev_med_max || filters.above_ratio_min || filters.above_ratio_max || filters.near_ma60 !== undefined || filters.chg_today_min || filters.chg_today_max || filters.chg_15_min || filters.chg_15_max ? `，筛选后剩 ${filteredRows.length} 行` : ""}`} />
              {/* 这一格回显的是**后端真应用了哪些档**（`filter.quadrants`），不是界面自己的 state：
                  两边不一致（比如值被后端认不得）就该在这里露出来，而不是各说各话。 */}
              {(d.filter?.quadrants ?? []).length > 0 && (
                <Row label="象限筛选（在库里筛，先于取数上限）"
                  value={(d.filter?.quadrants ?? [])
                    .map(q => (d.quadrant_options ?? []).find(o => o.value === q)?.label ?? q)
                    .join("、") + `｜榜上这 ${all.length} 行只来自这些档`} />
              )}
              {/* 「稳步提升」那格同样回显**后端真应用的值**（`filter.step_gap_min`）：
                  界面勾了而后端没收到（或阈值没读到）时，这一行不会出现，谎就露在屏上而不是心里。 */}
              {d.filter?.step_gap_min != null && (
                <Row label="稳步提升筛选（在库里筛，先于取数上限）"
                  value={`较前窗增减 ≥${d.filter.step_gap_min}｜榜上没有可比前窗（显示 —）的票一律不进这一榜`} />
              )}
              {/* limit 与 rows_returned 相等时不能读成"全市场就这些"—— 它只是被截到上限 */}
              {typeof d.filter?.limit === "number" && all.length >= (d.filter.limit ?? Infinity) && (
                <p className="mt-0.5 text-[11px] leading-relaxed text-warning">
                  后端 <code>filter.limit={d.filter.limit}</code>、<code>rows_returned={dash(d.filter.rows_returned)}</code>
                  —— 交出行数已到上限，所以这是<b className="text-foreground">榜单前 {d.filter.limit} 名，不是全部满足条件的票</b>。
                  {qcTotal !== null && (
                    <>这一场满足「加权分 ≥ {dash(d.filter?.min_energy)}」的<b className="text-foreground">全场实有 {qcTotal} 只</b>
                      （那个数是各档象限相加，不受上限影响；门槛的单位是加权分，不是次数），
                      {qcTotal <= 800
                        ? <>把上面的取数上限调到 {qcTotal} 以上才是全部。</>
                        : <>而取数上限最高只能填 800，所以这一场<b className="text-foreground">一次装不下</b> —— 要看得全，就用「最少加权分」与象限把那 {qcTotal} 只缩到 800 以内（那两个条件都在库里生效、发生在截断之前）。</>}
                    </>
                  )}
                </p>
              )}
              {/* 用户 2026-10-21：「描述性内容全部收缩起来，避免过多占屏」。
                  缺数／截断／查询失败那一族 caveat 由 CaliberFold 自己留在屏上（分类只住一处）。 */}
              <CaliberFold caveats={d.caveats} definitions={d.definitions}
                coverage={d.coverage} provenance={[d.source]} />
              
              {/* 筛选按钮 + 「只看稳步提升」那一档（10-19 定稿，同日起阈值随加权分改成浮点）。
                  这一格与象限同一族：改的是**取数参数**（`step_gap_min`），在库里筛、拼在取数上限之前 ——
                  阈值数字一律读接口 `thresholds.step_gap_min`，界面不自己写数字（口径改于 2026-10-19 第二稿：换尺之后那个数也跟着换过档）。 */}
              <div className="mt-2 flex items-center gap-2 flex-wrap">
                <FilterToggleButton show={showFilters} activeCount={activeFilterCount} onClick={() => setShowFilters(!showFilters)} />
                <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
                  title={`${d.definitions?.dbl_step_gap ?? "后端 definitions 没交 dbl_step_gap 这一条 —— 这一档的口径没有出处，页面不自己编"}`
                    + `｜勾上后把 thresholds.step_gap_min 作为 step_gap_min 参数发出去，在库里筛、作用在取数上限之前；`
                    + `没有可比前窗（显示 —）的票一律不进榜。`
                    + `｜注意：那句原文里写的还是旧口径的措辞（次数与那个整数阈值），`
                    + `而这一格发出去的数字读的是同一份返回里的 thresholds.step_gap_min —— 页面不替后端改那句话，只指路。`}>
                  <input type="checkbox"
                    disabled={!Number.isFinite(d.thresholds?.step_gap_min ?? NaN)}
                    checked={stepGapMin !== null}
                    onChange={e => {
                      vd.clearAll();
                      setStepGapMin(e.target.checked ? (d.thresholds?.step_gap_min ?? null) : null);
                    }} />
                  只看稳步提升（较前窗增减 ≥{Number.isFinite(d.thresholds?.step_gap_min ?? NaN) ? d.thresholds?.step_gap_min : "没读到"}）
                </label>
                {stepGapMin !== null && (
                  <span className="text-[11px] text-muted-foreground">
                    {typeof d.step_gap_pool?.hits === "number"
                      ? <>这一场「加权分≥{dash(d.filter?.min_energy)}」的可比母体里实有<b className="text-foreground"> {d.step_gap_pool.hits} 只</b>稳步提升（那个数不受取数上限影响；另有 {dash(d.step_gap_pool.uncomparable)} 只没有可比前窗）</>
                      : "全场实有几只：没读到（那条聚合失败了，不拿 0 顶）"}
                  </span>
                )}
                {activeFilterCount > 0 && (
                  <button type="button" onClick={() => { setFiltersState({}); setQuadrants([]); setStepGapMin(null); vd.clearAll(); }}
                    className="text-[11px] text-muted-foreground hover:text-foreground">
                    清除筛选
                  </button>
                )}
              </div>

              {/* 筛选面板 - 使用通用组件。象限那一格虽然长在同一个面板里，
                  但它改的是**取数参数**（`quadrants`），不是本地那份 filters —— 分开存，
                  否则"清除筛选"会把一份已经发给后端的条件留在 URL 里。 */}
              <TableFilterPanel
                show={showFilters}
                filters={{ ...filters, quadrants }}
                onChange={(key, value) => {
                  vd.clearAll();
                  if (key === "quadrants") setQuadrants(Array.isArray(value) ? value as string[] : []);
                  else setFiltersState(f => ({ ...f, [key]: value }));
                }}
                onClear={() => { setFiltersState({}); setQuadrants([]); setStepGapMin(null); vd.clearAll(); }}
                sections={[
                  {
                    label: "四维象限", type: "multi", key: "quadrants",
                    options: (d.quadrant_options ?? []).map(o => ({
                      value: String(o.value ?? ""), label: String(o.label ?? o.value ?? ""),
                      count: o.count === undefined ? undefined : (o.count === null ? null : Number(o.count)),
                      meaning: o.meaning ?? null,
                    })),
                    note: "这一格由后端筛，作用在取数上限（默认 150 行）之前 —— 每个档位后面那个数是"
                      + "全场实有几只，不是「已经交出的这些行里有几只」。"
                      + "其余数值那几格（板块/加权分/离线中位…）只在已经交出的行里筛，"
                      + "两者范围不同：勾了象限之后行数还会再少，那是二次筛，不是后端少给了数。"
                      + "一个都不勾 = 不限；「没取到象限」那一档是当天 score 表里没有这只票的行，"
                      + "不勾它就看不到它们（榜上确实有这么几只）。",
                  },
                  { label: "板块", type: "select", key: "board", options: [
                    { value: "主板", label: "主板" },
                    { value: "创业板", label: "创业板" },
                    { value: "科创板", label: "科创板" },
                    { value: "北交所", label: "北交所" },
                  ]},
                  { label: "加权分", type: "range", key: "dbl_energy", step: 0.05 },
                  { label: "翻转次", type: "number-min", key: "cross_count_min", min: 0 },
                  { label: "离线中位", type: "range", key: "dev_med", step: 0.1 },
                  { label: "收上方%", type: "range", key: "above_ratio", step: 1, min: 0, max: 100 },
                  { label: "贴MA60", type: "boolean", key: "near_ma60" },
                  { label: "当日涨幅", type: "range", key: "chg_today", step: 0.1 },
                  { label: "15日涨幅", type: "range", key: "chg_15", step: 0.1 },
                ]}
              />

              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="代码" sortKey="code" dir={vdSort.dirOf("code")} onSort={sortVd} /><SortHeader label="名称" sortKey="name" dir={vdSort.dirOf("name")} onSort={sortVd} /><SortHeader label="板块" sortKey="board" dir={vdSort.dirOf("board")} onSort={sortVd} />
                    {/* 「四维象限」这一列的悬停**逐字用后端给的轴语义/合成式/四档含义**拼：
                        前端不另写一份判据（两处各写一套早晚走偏，而读者看到的是同一个列名）。 */}
                    <SortHeader label="四维象限" sortKey="oper_value" dir={vdSort.dirOf("oper_value")} onSort={sortVd}
                      title={qTip} />
                    <SortHeader label="有行情天" sortKey="bars" dir={vdSort.dirOf("bars")} onSort={sortVd}
                        title="窗口内真有行情的天数；停牌多的票明显小于窗口" />
                    <SortHeader label="有MA60天" sortKey="ma60_days" dir={vdSort.dirOf("ma60_days")} onSort={sortVd}
                        title="窗口内算得出 MA60 的天数" />
                    <SortHeader label="加权分" sortKey="dbl_energy" dir={vdSort.dirOf("dbl_energy")} onSort={sortVd}
                        title={`${d.energy?.scale?.formula ?? "后端没交 energy.scale.formula —— 这一列的算式没有出处，页面不自己补一句"}`
                          + `｜满窗上限 ${trimNum(d.energy?.scale?.max_score)}（energy.scale.max_score）、实测分位看 energy.distribution；`
                          + "档表在系统配置那一卡里可改。缺数显示 —（那天没有行，或还没按当前配置重算），不当 0 分。悬停另有旧口径的硬计数作对照。"
                          + "（口径原文在 definitions.dbl_energy，页面不抄第二份）"} />
                    {/* 名次那一列（2026-10-19 第二稿新增）：格子读的就是 rank_in_day，
                        规则原文与分母都走 `lib/ranking.ts` 那份（页面不自己写规则那句话）。 */}
                    <SortHeader label="名次" sortKey="rank_in_day" dir={vdSort.dirOf("rank_in_day")} onSort={sortVd}
                        title={rankTitle(plain(d.rank?.rule ?? ""), d.rank?.universe ?? null)} />
                    <SortHeader label="对昨倍量" sortKey="dbl_prev_count" dir={vdSort.dirOf("dbl_prev_count")} onSort={sortVd} />
                    {/* 10-19 定稿的「加权分稳步提升」：悬停**逐字读接口 definitions.dbl_step_gap**，
                        组件不另写一句口径（判据与阈值只住 duanxian/quant.py 那一处）。 */}
                    <SortHeader label="较前窗增减" sortKey="dbl_step_gap" dir={vdSort.dirOf("dbl_step_gap")} onSort={sortVd}
                        title={d.definitions?.dbl_step_gap ?? "后端 definitions 没交 dbl_step_gap 这一条 —— 这一列的口径没有出处，页面不自己编"} />
                    <SortHeader label="翻转次" sortKey="cross_count" dir={vdSort.dirOf("cross_count")} onSort={sortVd}
                        title="收盘价与 MA60 上下关系翻转次数；0 次是单边，不算震荡" />
                    <SortHeader label="离线下中位" sortKey="dev_med" dir={vdSort.dirOf("dev_med")} onSort={sortVd}
                        title="|收盘/MA60−1| 中位数（%）" />
                    <SortHeader label="离线最大" sortKey="dev_max" dir={vdSort.dirOf("dev_max")} onSort={sortVd}
                        title="同一指标的最大值；中位小而最大大＝中间拉过一次又回来" />
                    <SortHeader label="收上方占比" sortKey="above_ratio" dir={vdSort.dirOf("above_ratio")} onSort={sortVd}
                        title="收在 MA60 上方的天数占比；100 或 0 都不是震荡" />
                    <SortHeader label="贴 MA60" sortKey="near_ma60" dir={vdSort.dirOf("near_ma60")} onSort={sortVd}
                        title="|收盘/MA60−1| 中位数 ≤ 3% 记为贴线；null＝算不出 dev_med" />
                    <SortHeader label="MA60 变化" sortKey="ma60_chg" dir={vdSort.dirOf("ma60_chg")} onSort={sortVd}
                        title="窗口首尾 MA60 的变化率（%）；60 线自己陡涨陡跌时「贴着线」是趋势不是震荡" />
                    <SortHeader label="日均成交额" sortKey="avg_amt_yuan" dir={vdSort.dirOf("avg_amt_yuan")} onSort={sortVd}
                        title="单位随后端，不换算亿/万" />
                    {/* 榜尾两列涨幅（用户 2026-10-10 点名）：口径文字**逐字用后端那份**
                        （判据只住 duanxian/day_returns.py），前端不自己解释"复权"是怎么算的。
                        列名写「当日」而不是「今日」：这一屏锚在库里那一档（现在显示的是本页的基准交易日），
                        周末或复盘时"今天"根本没有行情，写"今日涨幅"就是一句假话。 */}
                    <SortHeader label="当日涨幅" sortKey="chg_today" dir={vdSort.dirOf("chg_today")} onSort={sortVd}
                        title={`「当日」= 本页基准交易日${d.date ? `（现在显示的是 ${d.date}）` : ""}那一档的涨跌幅，不是此刻的实时价。${pctRule}`} />
                    <SortHeader label={`${SPAN} 日涨幅`} sortKey="chg_15" dir={vdSort.dirOf("chg_15")} onSort={sortVd}
                        title={`窗口 ${SPAN_WINDOW} 场：基准交易日 + 往前 ${SPAN} 场；基期是 ${d.pct_base_day ?? "后端没交的那一场"}。${pctRule}`} />
                  </tr></thead>
                  <tbody>
                    {shown.map(({ r, gi }) => (
                      <tr key={`${r.code}-${gi}`} {...vd.rowProps(gi, r.code, "hover:bg-primary/5")}>
                        <td {...vd.cellProps(gi, "code", "whitespace-nowrap font-mono")}>
                          <Link className="hover:text-primary" to={`/stock-review?symbol=${encodeURIComponent(String(r.code ?? "").split(".")[0])}`}>{dash(r.code)}</Link>
                        </td>
                        <td {...vd.cellProps(gi, "name", "whitespace-nowrap")}><StockLink code={r.code} name={r.name} className="hover:text-primary" /></td>
                        <td {...vd.cellProps(gi, "board", "whitespace-nowrap text-muted-foreground")}>{dash(r.board)}</td>
                        {/* 分数是原值（可负）：颜色按正负走，与「四维象限」页同一套 `pctColor`。
                            悬停给出 X/Y 两轴与全市场位置，读者不用跳页就知道这个数是怎么来的。 */}
                        <td {...vd.cellProps(gi, "oper_value", "whitespace-nowrap font-mono tabular-nums")}
                          title={r.quadrant
                            ? `${r.quadrant}${r.quadrant_label ? `（${r.quadrant_label}）` : ""}｜X 质量 ${num(r.x_quality, 2)}｜Y 环境 ${num(r.y_env, 2)}`
                              + `${typeof r.rank_in_market === "number" ? `｜全市场第 ${r.rank_in_market} 名` : ""}`
                              + `${typeof r.percent_rank === "number" ? `（百分位 ${num(r.percent_rank, 1)}，越大越靠前）` : ""}`
                              + `${r.is_stale === true ? "｜这一行的输入不是该场最新（旧）" : ""}`
                            : "当天 score 表里没有这只票的 oper_score 行：没有象限，不是 0 分"}>
                          {r.quadrant
                            ? <>{r.quadrant} <span className={pctColor(r.oper_value ?? null)}>{num(r.oper_value, 1)}</span></>
                            : dash(null)}
                        </td>
                        <td {...vd.cellProps(gi, "bars", "font-mono tabular-nums")}>{dash(r.bars)}</td>
                        <td {...vd.cellProps(gi, "ma60_days", cn("font-mono tabular-nums", (r.ma60_days ?? 0) < (r.bars ?? 0) && "text-warning"))}>{dash(r.ma60_days)}</td>
                        <td {...vd.cellProps(gi, "dbl_energy", "font-mono font-semibold tabular-nums")}
                          title={r.dbl_energy == null
                            ? "这一场它没有加权分（基准日那天在日线里没有行，或这一场还没按当前档表重算）：显示 —，不当 0 分"
                            : `加权分 ${trimNum(r.dbl_energy)}｜旧口径硬计数 dbl_count_legacy＝${dash(r.dbl_count_legacy)}（日量比 ≥2.0 才算一次，2026-10-19 起退役：只作对照，不上屏、不参与筛）`}>
                          {r.dbl_energy == null
                            ? <span className="text-muted-foreground/50">—</span>
                            : trimNum(r.dbl_energy)}
                        </td>
                        {/* 名次：没有分就没有名次（那一格是 —，不是 0）。规则原文与分母都在 rankTitle 里，
                            那份实现只住 `lib/ranking.ts`，页面这里一个字都不补。 */}
                        <td {...vd.cellProps(gi, "rank_in_day", "font-mono tabular-nums")}
                          title={rankTitle(plain(d.rank?.rule ?? ""), d.rank?.universe ?? null, r.rank_in_day ?? null)}>
                          {r.rank_in_day == null || !Number.isFinite(r.rank_in_day)
                            ? <span className="text-muted-foreground/50">{rankText(r.rank_in_day)}</span>
                            : rankText(r.rank_in_day)}
                        </td>
                        <td {...vd.cellProps(gi, "dbl_prev_count", "font-mono tabular-nums text-muted-foreground")}>{dash(r.dbl_prev_count)}</td>
                        {/* 分段差：`—`（没有可比前窗）与 `0`（本窗比前窗一点没多）必须长得不一样 ——
                            原因句子来自后端 dbl_step_gap_null_note，组件不自己编。 */}
                        <td {...vd.cellProps(gi, "dbl_step_gap", cn("font-mono tabular-nums", r.dbl_step_gap == null && "text-muted-foreground/50"))}
                          title={r.dbl_step_gap == null
                            ? (r.dbl_step_gap_null_note ?? "没有可比的前一整窗，也没给出原因码：这一格是空的，不是 0（0 的意思是本窗比前窗一点没多）")
                            : `本窗加权分 ${trimNum(r.dbl_energy)} − 前一整窗 ${r.dbl_energy != null && r.dbl_step_gap != null ? trimNum(r.dbl_energy - r.dbl_step_gap) : "没读到"}（同一只票往前 ${dash(r.win_days)} 行那场）`}>
                          {r.dbl_step_gap == null
                            ? <span className="text-muted-foreground/50">—</span>
                            : <span className={pctColor(r.dbl_step_gap)}>{r.dbl_step_gap > 0 ? "+" : ""}{trimNum(r.dbl_step_gap)}</span>}
                        </td>
                        <td {...vd.cellProps(gi, "cross_count", cn("font-mono tabular-nums", (r.cross_count ?? 0) === 0 && "text-muted-foreground"))}>{dash(r.cross_count)}</td>
                        <td {...vd.cellProps(gi, "dev_med", "font-mono tabular-nums")}>{num(r.dev_med, 2)}</td>
                        <td {...vd.cellProps(gi, "dev_max", "font-mono tabular-nums")}>{num(r.dev_max, 2)}</td>
                        <td {...vd.cellProps(gi, "above_ratio", "font-mono tabular-nums")}>{num(r.above_ratio, 1)}</td>
                        <td {...vd.cellProps(gi, "near_ma60", "whitespace-nowrap font-mono tabular-nums")}
                          title={r.near_ma60 == null ? "dev_med 算不出，所以贴线这一格也是空（不是不贴）" : r.near_ma60 ? "中位偏离 ≤ 3%，算贴着 MA60" : "中位偏离 > 3%，不算贴线"}>
                          {r.near_ma60 === true ? "是" : r.near_ma60 === false ? "否" : dash(null)}
                        </td>
                        <td {...vd.cellProps(gi, "ma60_chg", "font-mono tabular-nums")}>{num(r.ma60_chg, 2)}</td>
                        <td {...vd.cellProps(gi, "avg_amt_yuan", "whitespace-nowrap font-mono tabular-nums")}>{typeof r.avg_amt_yuan === "number" ? `${(r.avg_amt_yuan / 1e8).toFixed(2)} 亿` : dash(null)}</td>
                        {/* 涨幅两列：值与颜色都取后端交出的百分数（已按后复权算好），页面不重算。
                            空着 = 那一窗口它没有行，悬停说清是哪一天缺 —— 不写成 0.00%（那是"平盘"）。 */}
                        <td {...vd.cellProps(gi, "chg_today", "whitespace-nowrap font-mono tabular-nums")}
                          title={r.chg_today == null ? "基准交易日或它前一场在日线里没有行：算不出当日涨幅，不是平盘 0%" : `当日涨幅 ${r.chg_today}%（后复权，口径见列头）`}>
                          {typeof r.chg_today === "number"
                            ? <span className={pctColor(r.chg_today)}>{r.chg_today > 0 ? "+" : ""}{num(r.chg_today, 2)}%</span>
                            : <span className="text-muted-foreground/50">—</span>}
                        </td>
                        <td {...vd.cellProps(gi, "chg_15", "whitespace-nowrap font-mono tabular-nums")}
                          title={r.chg_15 == null
                            ? `基期（${r.pct_base_day ?? "窗口首日"}）或目标日那天它没有行：算不出 ${SPAN} 日涨幅，不是平盘 0%`
                            : `基期 ${r.pct_base_day ?? "未交出"} → ${dash(d.date)}｜窗口 ${SPAN_WINDOW} 场里它真有行 ${r.pct_bars ?? "?"} 场${(r.pct_bars ?? 0) < SPAN_WINDOW ? "（中间停过牌或还没上市：跨度按市场场数算，所以这个数仍是这两场之间的差）" : ""}`}>
                          {typeof r.chg_15 === "number"
                            ? <span className={pctColor(r.chg_15)}>{r.chg_15 > 0 ? "+" : ""}{num(r.chg_15, 2)}%</span>
                            : <span className="text-muted-foreground/50">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {/* 怎么点这一张表：通用一句（实现全站一份）收进默认收起的说明框（用户 2026-10-19 夜：
                    「把选项留出，描述内容都收缩起来」）；弹窗被拦的告警是**告警**，单独念、不折进任何框 */}
                <ExplainBox title="这一张表怎么点 · 单击高亮什么 · 双击去哪里">
                  <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
                </ExplainBox>
                {vd.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{vd.popHint}</p>}
                {filteredRows.length === 0 && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {onlyOsc ? "勾了「只看真震荡」之后没有票满足：这不是库里没数，是被这个筛条件筛空了。" : "后端交出 0 行。"}
                    {filters.board || filters.dbl_energy_min || filters.cross_count_min || filters.dev_med_min || filters.dev_med_max || filters.above_ratio_min || filters.above_ratio_max || filters.near_ma60 !== undefined || filters.chg_today_min || filters.chg_today_max || filters.chg_15_min || filters.chg_15_max
                      ? "（当前有筛选条件，可能把结果筛空了——点「清除筛选」恢复）" : ""}
                  </p>
                )}
                {sorted.length > PAGE && (
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                    <span>共 {sorted.length} 行 · 每页 {PAGE} 行，现在是第 {cur}/{pages} 页
                      （这一页是第 {(cur - 1) * PAGE + 1}~{Math.min(sorted.length, cur * PAGE)} 行）</span>
                    <button type="button" disabled={cur <= 1}
                            onClick={() => setPage(cur - 1)}
                            className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70 disabled:opacity-40">上一页</button>
                    <button type="button" disabled={cur >= pages}
                            onClick={() => setPage(cur + 1)}
                            className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70 disabled:opacity-40">下一页</button>
                    <span>排序与筛选都作用于整批 {sorted.length} 行之后再分页，所以第 2 页仍是这一批里的第 {PAGE + 1}~{Math.min(sorted.length, 2 * PAGE)} 名。</span>
                  </div>
                )}
              </div>
              <ExplainBox title="日均成交额这一列的换算（页面自己说的）">
                <p>
                  日均成交额这一格是<b className="text-foreground">本页唯一一次换算</b>（元 → 亿），只为可读；
                  原值仍是接口给的 <code>avg_amt_yuan</code>，鼠标悬停列头可看口径。
                </p>
              </ExplainBox>
            </>
          );
        }}
      </Panel>

      {/* 反向聚合：把上面这张榜的票按概念板块数一遍（用户 2026-10-08 要的那张新表）。
          分母**只有本榜交出的这些票**，不是全市场 —— 这一点必须写在表头旁边，
          否则"融资融券 100 只"会被读成"全市场有 100 只融资融券倍量股"之类的话。
          表体与四维象限那一屏共用一份实现（`components/quant/ConceptBoard.tsx`）：
          两处原先各写了一遍，漂到「集中度」的悬停文字一份说"同样 30 只"、一份说"同样 20 只"。 */}
      <ConceptBoard state={hits} pageSize={40}
        note="口径：reference_data 的概念归属（sector_type=概念、当前仍在册），按概念名数、同名多条板块码只算一次；不做同义名合并（「新能源车」与「新能源汽车」在源站是两条）。"
        poolLine={d => `上面那张倍量榜交出的 ${dash(d.concept_pool?.size ?? (d.concepts ?? []).length)} 只（不是全市场）`
          + ((d.filter?.quadrants ?? []).length > 0
            // 勾了象限之后这张概念榜的分母跟着缩，不写出来就会被读成"这批概念与象限无关"
            ? "；这一批已经按上面选的四维象限筛过，下面这些只数是筛过之后那批票的概念计数"
            : "")}
        pickCaveats={d => d.caveats ?? []}
        footer={<>
          <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
            这张表只说明「这批倍量票共同挂在哪些题材上」，<b className="text-foreground">不含热度、资金与涨跌</b>；
            一个概念挂了多少只在榜上，与它今天是否值得看是两件事。要那两个读数去「板块热度」与「板块资金趋势榜」。
          </p>
          <ExplainBox title="这张概念榜怎么读 · 想换读法点哪一列">
            <p>
              默认这一页是榜上只数从高到低；想换一种读法点「集中度」列头 —— 哪些窄题材在这批票里挤得最满。
            </p>
          </ExplainBox>
        </>} />

      {/* 倍量榜之后补的一块（用户 2026-10-19 点名）：进榜那只票**之后每天怎么走**，
          读的是走向不是这一场的读数。它长在概念榜后面而不是紧贴榜下面，是为了不打断
          「上面那张倍量榜交出的 N 只」这句指代（概念榜的分母就是紧挨着它的那张榜）。
          只读 /api/quant/energy-track，接口没有取数上限参数，每页行数读接口 page_size。 */}
      <EnergyTrackBoard nonce={nonce} date={date} vd={track} />

      <StartupVolumeBoard />

      <FirstBoardShapeBoard />

      {/* 启动前量能画像 / 首板形状之后补的一块：读**走向**而不是读形状 ——
          启动日之前那 N 场里，七个分量的全市场名次往哪边走、T−1 那天站在哪个象限。
          名次与象限都是库里算好的，这一屏只读 /api/quant/startup-quadrant-track。 */}
      <StartupQuadrantTrackBoard />

      {/* 象限轨迹后面补的一块：读**量与价在同一天的联合** —— 倍量那天收涨还是收跌、
          突然拉高那种日子摊在倍量／准倍量还是缩量上（判据是日线代理，分钟线那套在 /spike）。
          只读 /api/quant/startup-spike-days，天数与占比都在库里算好交出。 */}
      <StartupSpikeDaysBoard />

      <Disclaimer compact />
    </div>
  );
}
