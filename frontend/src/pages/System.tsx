import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Stethoscope } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { FEATURE_GROUPS } from "@/lib/workspace/features";
import { ArrowUpRight } from "lucide-react";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CloseChainPanel } from "@/components/CloseChainPanel";
import { ChainStepsPanel } from "@/components/ChainStepsPanel";
import { LogTailPanel } from "@/components/LogTailPanel";
import { QuoteLinksPanel } from "@/components/QuoteLinksPanel";
import { VolumeEnergyPanel } from "@/components/VolumeEnergyPanel";
import { LoopSwitchboard } from "@/components/LoopSwitchboard";
import { NotifyPanel } from "@/components/NotifyPanel";
import { WxPushPanel } from "@/components/WxPushPanel";
import { SectionNav } from "@/components/SectionNav";
import { TdxWatchPanel } from "@/components/TdxWatchPanel";
import { LanAccessPanel } from "@/components/LanAccessPanel";
import { PromptSlotsPanel } from "@/components/PromptSlotsPanel";
import { Contract, Measured, Panel, Provenance, Row } from "@/components/ui/DataPanel";
import { probe, useNonce, useProbe } from "@/lib/probe";
import { ageText, dash, hasData } from "@/lib/datastate";
import { verdictOf, type ProbeVerdict } from "@/lib/probeVerdict";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";

/**
 * 系统配置（运维区）。
 *
 * 用户问的是同一件事的两面：**采集与加工的功能点在页面上看得见吗？**
 * 采集/加工的「产物」早就有页面（象限、评分、倍量、板块热度），
 * 但「它本身跑得怎么样」原先只散在终端里（`manage.py doctor`）和几个还没接上的端点上。
 * 所以这一页分两半：上半把 doctor 的逐项体检读回来（几项由接口说，别在这页写死数字；年龄必须一起显示），
 * 下半做 doctor 做不到的事：**从界面这一侧真实探测每个能力在不在**，
 * 404 / 报错 / `available:false` 三种情况分开显示 —— 因为「接口没实现」「实现了但坏了」
 * 「实现了但今天没数」是三件不同的事，混成一句「不可用」就是那次三天没落库能藏住的原因。
 */

interface Health { service?: string | null; version?: string | null; launch_id?: string | null; ready?: boolean | null }
interface Cli { kind?: string | null; allowed?: boolean | null; installed?: boolean | null; reason?: string | null }
interface CliList { clis?: Cli[] | null; optInEnv?: string | null; optedIn?: string[] | null }
interface DoctorItem { key?: string | null; label?: string | null; ok?: boolean | null; detail?: string | null; advisory?: boolean | null }
interface Doctor {
  available?: boolean | null; reason?: string | null; source?: string | null;
  generated_at?: string | null; computed_at?: string | null; checked_at?: string | null;
  age_seconds?: number | null; ttl_seconds?: number | null; refreshing?: boolean | null;
  blocking_failures?: number | null; advisory_failures?: number | null; items?: DoctorItem[] | null;
}
/** 自检只关心「这个端点在不在、有没有报错」，所以统一按这个最小形状取 */
interface Ping { available?: boolean | null; reason?: string | null }

/** 能力清单：路径 + 它在页面上的落点。探测是 GET，不带参数（快的那些） */
const CAPABILITIES: { path: string; label: string; where: string; slow?: boolean }[] = [
  { path: "/quality", label: "质量闸（登记规则的逐条判定；条数由接口现算，这里不抄）", where: "/quality" },
  { path: "/coverage", label: "覆盖体检（逐表缺几天）", where: "/coverage", slow: true },
  { path: "/watchlist/groups", label: "自选分组", where: "/grouped" },
  { path: "/watchlist/group-analytics", label: "分组整体分析（组热度表＋20 场折线，现场聚合；10-14 起在分组自选最上方）", where: "/grouped", slow: true },
  { path: "/quant/quadrant", label: "四维象限", where: "/quadrant", slow: true },
  { path: "/quant/rank-stability?days=7&limit=50", label: "名次稳定榜（近 3/7/11 场全市场名次之和从低到高，前 50；只收凑满场次的票）", where: "/quadrant", slow: true },
  { path: "/quant/rank-momentum?metric=xy&limit=80", label: "名次稳步前进榜（近 6 场名次一直在往前走；xy 两轴合名次 / 总分名次两档可切）", where: "/quadrant", slow: true },
  { path: "/quant/momentum-follow?metric=xy&limit=80", label: "上一场稳步前进榜前 N 名 · 今日与近 5 日股价表现（母体＝上一场那张榜、同档 xy/oper 可切；今日＝母体后第一场、近 5 日＝读至日往前 5 场，两栏后复权涨跌幅 + 母体场当天的综合分名次、倍量名次、突破信号、埋伏榜内名次、异动母体、趋势入选；缺数一律 —）", where: "/quadrant", slow: true },
  { path: "/quant/sector-heat", label: "板块热度与点火", where: "/sector-heat" },
  { path: "/quant/sector-trend?limit=20", label: "板块趋势（位置分×阶段那一族档位，判据版本现读 config.setting；与上面热度是两套判据，排序会分叉）", where: "/sector-heat", slow: true },
  { path: "/quant/volume-double", label: "倍量形态", where: "/volume-double" },
  { path: "/quant/energy-track", label: "加权分进榜后的逐日涨跌幅（母体＝基准日这一场得分前 N 名，N 是上限、边界同分并列一起进来；每行追到它全史第一次进榜之后每一场的后复权当日涨跌幅，列数＝这批最深的那条）", where: "/volume-double", slow: true },
  { path: "/config/volume-energy", label: "倍量加权分档（各档分值＝一天落在这一档算几分、按交易所作用域；算式是每档出现次数 × 这一档的分值再累加，没有系数那一层。档表条数与区间以接口为准。改完自动起一次后台全量重算，保存不等于已生效）", where: "/system" },
  { path: "/quant/volume-breakout", label: "突破信号（缩量收敛后首次破 MA60 + 放量）", where: "/volume-breakout" },
  { path: "/quant/ambush", label: "埋伏票（左栏：均线汇集 + 周线重心抬升 + 缩量，多类型一张榜）", where: "/volume-breakout" },
  { path: "/quant/confluence", label: "选股汇总（各榜交叉命中：命中榜数优先、其次各榜最好名次）", where: "/confluence" },
  { path: "/quant/confluence-history?code=000001.SZ", label: "单票交叉命中历史（历史上重复出现过哪些天）", where: "/confluence" },
  { path: "/quant/system-entry", label: "交易体系榜（近 20 场有涨停 × 向上跳空缺口三天不回补 × 缩量回调后阳线确认，附与基线并排的历史分布）", where: "/confluence", slow: true },
  { path: "/quant/system-entry-cohort", label: "走完三条的个股近六个月名单（每行后 1/2/3/5/10/15 场涨幅 × 前 2 场~后 5 场的交叉榜位次与象限档位；进程内缓存一小时）", where: "/confluence", slow: true },
  { path: "/quant/intraday-spike", label: "异动筛查（盘中瞬间冲高／摸到涨停的逐事件榜 + 与同期基线并排的复盘分布）", where: "/spike", slow: true },
  { path: "/quant/trend-stocks?lookback=5", label: "趋势股（近 5／10 个交易日每天收盘严格高于当日 5 日线 × 当天没涨停，主板／创业板／科创板分组；判据落库在 analytics.trend_stock_daily）", where: "/trend-stocks", slow: true },
  { path: "/market/abnormal-move?recent=10&hot_limit=20", label: "异动提醒（明日涨停会不会踩到严重异动／热门股 10 日累计偏离前 20／近期已踩到含次日涨幅与停牌；判据落库在 analytics.abnormal_move_daily）", where: "/（首页短线情绪下面）", slow: true },
  { path: "/market/emotion", label: "短线情绪（连板梯队／最高连板／炸板率／封板率／晋级率／涨跌停家数，全站共享缓存 5 分钟）", where: "/（首页短线情绪那一格）" },
  { path: "/quant/risk-signal?months=3&group=day", label: "风险提示（市场宽度 × 阿姆斯＝涨额÷跌额 双折线＋区间背离；只读 analytics.market_breadth，20 期分位与档位阈值只住 duanxian/risk_signal.py；那一族列没回填时 arms.available=false 并给补跑命令）", where: "/（首页盘面数据里，异动提醒上面）" },
  { path: "/qd/sector-follow", label: "板块前三甲与相似板块（quantdash 口径轨：领涨那一名 + 后面两名 × 前五场名次 × 错开两场的轨迹匹配；母体与排名尺子和写侧 qd_sector 同一条，读侧现算约两秒、进程内缓存一小时）", where: "/（首页轮动矩阵与持续性那一块）", slow: true },
  { path: "/quant/first-board-daily", label: "首板分析（当日首板个股 + 涨停原因 + 前一天的走势与形态档 + 近期 5/10/15 场复权涨幅；母体只住 analytics.first_board_shape 的 fb-2 一把尺，判据落 analytics.first_board_daily；原因与封板时间来自同花顺涨停揭秘池，ST/北交所不收＝源站没这只，与「本场整池没取到」是两件事）", where: "/（首页换手率 TOP20 上面）", slow: true },
  { path: "/intraday/timeline", label: "盘中涨跌停事件流（东财涨跌停池每 5 分钟一针的逐条事件差：新封／回封／开板又回封／炸板 + 跌停那一轨四条，附每一针的整池家数；判据只住 app/intraday/pool_events.py，落库 analytics.intraday_pool_event，取数带 spec_version；这张表从 2026-10-26 才开线，更早的日子 available=false 并说明「库里还没有事件行」，不是那天没有涨跌停）", where: "/first-board（上半部分）" },
  { path: "/quant/stock-dossier?code=000001.SZ", label: "个股量化档案（象限/八分量/倍量/启动/板块热度/埋伏/突破/交叉，按这一只票拼起来；10-15 起住在个股深度复盘）", where: "/stock-review", slow: true },
  { path: "/stock/indicators?code=600519.SH", label: "35 项技术指标读数（库里实算，逐项带单位与小数位；10-15 起个股研究与趋势评分共用这一份渲染）", where: "/stock-review" },
  { path: "/stock/facts?code=600519.SH", label: "个股信息（主档 + 最近一场行情与估值 + 通达信个股档案 + 板块归属 + 本机分组 + 那一格 AI 说明的材料回执，只读本机库不出网；10-16 起是个股深度复盘的第一块，10-22 起带 brief）", where: "/stock-review" },
  { path: "/stock/fundamentals?code=600519.SH", label: "财务多期与外推（近 5 份年报 + 近 20 个单季逐期，下一季/当年/明年三格加权外推；源＝同花顺，落库在 reference_data.stock_financial_period）", where: "/stock-review" },
  { path: "/stock/metrics-ledger?code=600519.SH", label: "全指标总账（这一屏有哪几块、每块出自哪张表、每个字段说什么话；字段清单是后端现场取各模块那份 definitions）", where: "/stock-review" },
  { path: "/stock/price-volume-trend?code=600519.SH", label: "量价走势三块（日 / 周 / 月，由本机日线表聚合，价格是前复权形状、最后一根标未走完）", where: "/stock-review" },
  { path: "/stock/volume-similarity?code=600519.SH&k=10", label: "近 30 根量价形状与历史最相近样本（六个分量按权重合成的相近分，样本库＝sv-2 那批启动窗口；第一次约 6 秒，之后走进程内缓存）", where: "/stock-review", slow: true },
  { path: "/quant/scores?code=000001.SZ", label: "趋势评分：八项能力与分量明细（10-15 起在个股深度复盘的档案下面）", where: "/stock-review" },
  { path: "/quant/score-trend?code=000001.SZ", label: "趋势评分：窗口斜率 / 高点在第几天 / 连续上行 / 迷你走势（10 个有评分日一场）", where: "/stock-review" },
  { path: "/quant/security-search?q=600&kinds=stock,etf", label: "选一只票：代码 / 名称 / 全拼 / 拼音首字母四种匹配（全站那一只搜索框的判据在这一处）", where: "/stock-review" },
  { path: "/quant/new-high", label: "自选股历史新高（清单级母体，可限定一个分组）", where: "/grouped", slow: true },
  { path: "/quant/startup-volume", label: "启动前量能分布（四类母体分开，逐票窗口比对 + 启动日情绪 + 整组汇总）", where: "/volume-double" },
  { path: "/quant/first-board-shape", label: "首板形状预示（母体＝每个连板段的第一天，段长是结果；分档/逐年同向都在库里现算，参数可改）", where: "/volume-double", slow: true },
  { path: "/quant/startup-quadrant-track", label: "启动前 30 场四维象限轨迹（七个分量在启动日前 N 场的全市场名次走向 + T−1 象限分布 + 按名次变化分档看段长与收益；母体/月数/根数/键都可改）", where: "/volume-double", slow: true },
  { path: "/quant/startup-spike-days", label: "启动前爆量与突然拉高（日线代理：倍量/准倍量/缩量日各收涨收几天、突然拉高落在哪一天）", where: "/volume-double", slow: true },
  { path: "/quant/allocator", label: "分配者视角（位置分/温度/阶段/象限）", where: "/allocator", slow: true },
  { path: "/screen/rules", label: "筛选条件登记", where: "/screen" },
  { path: "/jobs", label: "任务与调度 / 调度器 / 任务大全", where: "/jobs" },
  { path: "/jobs/chain-steps", label: "加工段逐步结果与单步补跑（谁没成、为什么、多久；步数以现读为准，本行不抄总数）", where: "/system" },
  { path: "/system/log", label: "原始文本日志尾部（哪几份、多大、多久没写、最后几十行）", where: "/system" },
  { path: "/config/quote-links", label: "行情外链地址（按市场可配置，北证=东财 bj/代码）", where: "/system" },
  { path: "/datasource", label: "数据源健康（30 个源）", where: "/datasource" },
  { path: "/data-process", label: "加工重跑触发端点（404 = 这一屏没有可点的重跑按钮）", where: "/data-process" },
  { path: "/pipeline", label: "链路四环进度", where: "/pipeline" },
  { path: "/tdx/watch", label: "通达信看护（探测 + 待人工清单）", where: "/system" },
  { path: "/wxpush", label: "个人微信推送（在这台机器桌面上按键盘的那条通道：步骤、发送前的检查逐条实情、内容档；步数与闸数由接口现算，本行不抄）", where: "/system" },
  { path: "/tdx/status", label: "通达信客户端与文件日期", where: "/tdx" },
];

export function System() {
  const [nonce, refresh] = useNonce();
  const health = useProbe<Health>("/astock/health", nonce);
  const cli = useProbe<CliList>("/cli/available", nonce);
  const doctor = useProbe<Doctor>("/system/doctor", nonce);
  // 体检有几项**由接口说**：这一页以前把"11 项"写死在标题里，doctor 加到 13 项后就成了假数字。
  const doctorCount = hasData(doctor) ? (doctor.data.items ?? []).length : null;
  const [checks, setChecks] = useState<Record<string, ProbeVerdict>>({});
  const [running, setRunning] = useState(false);

  const runChecks = async () => {
    setRunning(true);
    setChecks({});
    // 逐个探测、逐个回填：慢的那几项（覆盖体检、象限）不应该把整屏压到最后一起出
    for (const c of CAPABILITIES) {
      const state = await probe<Ping>(c.path);
      const v = verdictOf(state);
      setChecks(prev => ({ ...prev, [c.path]: v }));
    }
    setRunning(false);
  };

  const list = Object.values(checks);
  const okCount = list.filter(v => v.kind === "ok").length;
  const missing = list.filter(v => v.kind === "missing").length;
  const broken = list.filter(v => v.kind === "broken").length;
  const unusable = list.filter(v => v.kind === "unavailable").length;

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="系统配置" level={1}
        subtitle="这个能力到底接上了没有 —— 从界面这一侧逐个探测，404 / 报错 / 今天没数分开报。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新读取
        </button>} />

      {/* 这一屏约 14 屏高（用户 10-16：「系统设置里为什么我还是没有看到这个推送功能相关的按钮」
          —— 「消息推送」原本在第 12,646px，功能做完了但人翻不到）。目录是现扫标题生成的。 */}
      <SectionNav />

      {/* 原「首页 / 工作台」已取消（汇总看板改名当首页），这块功能格整体搬进系统配置。
          id 与 data-* 保持原样：验收脚本按 `#home-features` 与 `[data-feature-category]` 数卡片。 */}
      <section id="home-features" className="mt-4" aria-labelledby="feature-heading">
        <div className="mb-3 flex items-end justify-between gap-4">
          <div><p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">Workbench</p>
            <h2 id="feature-heading" className="mt-1 text-lg font-bold">复盘工具，一站直达</h2></div>
          <span className="text-xs text-muted-foreground">全部业务页</span>
        </div>
        <div data-feature-grid className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {FEATURE_GROUPS.map((group, index) => <div key={group.title} data-feature-category className="glass min-w-0 rounded-xl border border-primary/20 p-3">
            <div className="mb-2 flex items-center gap-2 border-b border-primary/15 pb-2"><span className="text-[10px] font-medium text-primary">0{index + 1}</span><h3 className="text-[13px] font-semibold">{group.title}</h3></div>
            <div className="grid gap-1.5">{group.features.map(({ to, title, detail }) => <Link key={to} to={to} title={detail} className="group flex min-h-10 items-center gap-2 rounded-lg border border-border bg-muted/30 px-2.5 py-2 hover:border-primary/40 hover:bg-primary/[0.06]"><span className="min-w-0 flex-1 text-xs font-medium group-hover:text-primary">{title}</span><ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground group-hover:text-primary" /></Link>)}</div>
          </div>)}
        </div>
      </section>

      <div className="space-y-4">
        <Panel title="服务身份" state={health}
          note="进程与版本。锁文件与端口归属在 manage.py 那边，页面读不到就不写。">
          {d => (
            <>
              <Row label="服务" value={`${dash(d.service)} v${dash(d.version)}`} />
              <Row label="本次启动" value={dash(d.launch_id)} />
              <Row label="就绪" value={d.ready === true ? "是" : d.ready === false ? "否" : "—"}
                tone={d.ready === false ? "bad" : undefined} />
            </>
          )}
        </Panel>

        <Panel title="AI 引擎可用性" state={cli}
          note="allowed=false 是策略性禁用（自动批准 / 无沙箱），与「没装」是两件事，分开显示。">
          {d => (
            <>
              <ul className="space-y-0.5">
                {(d.clis ?? []).map(c => (
                  <li key={`${c.kind}`} className="text-[12px] leading-relaxed">
                    <span className={cn("mr-1 rounded px-1 text-[10px]",
                      c.allowed !== true ? "bg-muted text-muted-foreground" : c.installed === true ? "bg-primary/12 text-primary" : "bg-warning/15 text-warning")}>
                      {c.allowed !== true ? "已禁用" : c.installed === true ? "已装" : "未装"}
                    </span>
                    <span className="font-mono">{dash(c.kind)}</span>
                    {c.reason && <span className="text-muted-foreground"> · {c.reason}</span>}
                  </li>
                ))}
              </ul>
              {(d.optedIn ?? []).length > 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  已用 <code>{dash(d.optInEnv)}</code> 放开的：{(d.optedIn ?? []).join(" · ")}
                </p>
              )}
            </>
          )}
        </Panel>

        <Panel title={`环境体检（manage.py doctor 逐项结论${doctorCount == null ? "" : ` 共 ${doctorCount} 项`}）`} state={doctor}
          note="终端侧那套体检原样读回来，页面不自己判一遍。它答的是「环境好不好」，与下面那块自检答的「接口在不在」是两件不同的事。"
          contract={<Contract path="GET /api/system/doctor" source="scripts/manage.py doctor（缓存结果，带 TTL）" fields={[
            ["items[]{key,label,ok,detail,advisory}", "逐项结论。advisory=true 是提醒、不是故障，画成红色会让人开始忽略真正的红色"],
            ["blocking_failures / advisory_failures", "两个数分开给：只报一个「有几项没通过」就读不出要不要紧"],
            ["computed_at / age_seconds / ttl_seconds / refreshing", "这一份是多久前算的、还要多久才重算 —— 不显出来就会把上午的结果当现在的"],
          ]} />}>
          {d => (
            <>
              <Row label="这份体检算于"
                value={`${dash(d.computed_at ?? d.checked_at ?? d.generated_at)}${typeof d.age_seconds === "number" ? `（${ageText(d.age_seconds)}前${typeof d.ttl_seconds === "number" ? ` · 每 ${ageText(d.ttl_seconds)} 重算一次` : ""}）` : "（后端未给 age_seconds，无法判断有多新）"}`}
                tone={typeof d.age_seconds === "number" && typeof d.ttl_seconds === "number" && d.age_seconds > d.ttl_seconds ? "warn" : undefined} />
              {d.refreshing === true && (
                <p className="text-[11px] text-warning">后端正在重跑（<code>refreshing=true</code>）：下面显示的是<b className="text-foreground">上一份</b>结果，不是刚跑完的那份。</p>
              )}
              <Row label="未通过"
                value={`拦停 ${dash(d.blocking_failures)} · 提醒 ${dash(d.advisory_failures)}（共 ${(d.items ?? []).length} 项）`}
                tone={(d.blocking_failures ?? 0) > 0 ? "bad" : (d.advisory_failures ?? 0) > 0 ? "warn" : undefined} />
              <ul className="mt-2 space-y-1">
                {(d.items ?? []).map((it, i) => (
                  <li key={`${it.key}-${i}`} className="text-[11px] leading-relaxed">
                    <span className={cn("mr-1 rounded px-1 text-[10px]",
                      it.ok === false ? "bg-destructive/15 text-destructive" : it.ok === true ? "bg-primary/12 text-primary" : "bg-muted text-muted-foreground")}>
                      {it.ok === false ? "未通过" : it.ok === true ? "通过" : "未知"}
                    </span>
                    <span className="font-medium">{dash(it.label)}</span>
                    {it.advisory === true && <span className="ml-1 rounded bg-warning/15 px-1 text-[10px] text-warning">提醒</span>}
                    {it.detail && <p className="break-all text-muted-foreground">{plain(it.detail)}</p>}
                  </li>
                ))}
              </ul>
              <Provenance bits={[d.source]} />
            </>
          )}
        </Panel>

        {/* 用户在系统配置里能改的**第二类东西**：不是阈值，是页面上那些「一键运行」的话术。
            存 `config.setting`（键 `ai_prompt.<槽位>`），数据仍由页面带入，见 `duanxian/prompt_slots.py`。 */}
        <PromptSlotsPanel />

        {/* 第二类可改项：网络边界。绑定地址要重启才换、白名单立即生效，
            两种生效时机都写在卡里，不靠注释。 */}
        <LanAccessPanel />

        {/* 第三类可改项：每日收盘链的两个自动开关与 15:59 时点。原来只有命令行能改，
            页面上只剩一个"已停用"小灰标签，分不出是用户不想跑还是被谁误关。 */}
        <CloseChainPanel />

        {/* 上面那一格只管"今天会不会跑"，这一格管"上一次到底跑成什么样"：
            加工段逐步结果（谁没成、为什么、多久）＋ 对着那一步补跑一次。步数会随链增减，这里不抄总数
            （注释里留一个坐标备查：2026-10-17 起链是 40 步，第 29 步 fbshape、紧跟第 30 步 quadtrack；
             屏幕上的总数永远读台账实到的条数，抄进界面每加一步就成假话）。
            没有这一格时，失败只存在于跑的那段 stdout 里，跑完就没地方查。 */}
        <ChainStepsPanel />

        {/* 第三层：链"上一次跑成什么样"看台账，而**跑的过程中发生了什么**只有原文知道。
            10-10 那次看护循环假绿（线程在跑、页面写"正常"、台账 18 小时没动）
            就是靠这份 stdout 定性的 —— 那时界面上任何一格都看不出来。 */}
        <LogTailPanel />

        {/* 另一类可改项：按市场的行情外链地址（用户 10-08 点名）。
            它不影响任何数据口径，只决定"点股票名跳到哪儿看行情"，但同样是改一处全站生效。 */}
        <QuoteLinksPanel />

        {/* 第三类可改项：**倍量榜那把尺子**（用户 2026-10-19：倍量次改加权分）。
            它改的是数据口径本身（分档分值→占比→加权分），不像外链地址那样只决定跳到哪儿，
            所以这一卡必须把「保存了≠已生效」写在回显里：指纹一变后端会自动重算历史场。 */}
        <VolumeEnergyPanel />

        {/* 第四类可改项：**全部**常驻循环（10-07 补）。上面那一格只管收盘链两段，
            另外六条一直只能命令行 —— 用户问"是不是有一大堆定时任务"时，
            答案不该是"有，但界面上看不见"。 */}
        <LoopSwitchboard />

        {/* 消息推送（用户 10-15 点名）：四个渠道 + 内容多选 + 到点自动发。
            放在循环清单后面是因为它本身就依赖那条 `notify-push` 循环 ——
            上面关掉了，这一格配得再对也不会发。 */}
        <NotifyPanel />

        {/* 个人微信推送（用户 10-20 点名）：它**不是第五个渠道** —— 上面那四条是网络请求，
            这一条是在这台机器的桌面上真按键盘，所以要独立带一套发送前的检查。
            紧挨着放是为了让"无人值守该用哪条"这件事在同一屏里就能比出来。 */}
        <WxPushPanel />

        {/* 收盘链依赖的那个客户端本身"在不在跑"，原先整机没有一个可见处：
            它挂了不会报错，只会让上游的数一天一天不动。这一格只读、带最近探测时间。 */}
        <TdxWatchPanel nonce={nonce} />

        <GlassCard>
          <h2 className="flex items-center gap-1.5 text-sm font-bold">
            <Stethoscope className="h-4 w-4 text-primary" /> 能力接入自检
          </h2>
          <p className="mb-2 mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            点一下，从浏览器这一侧真发 {CAPABILITIES.length} 个 GET。<b className="text-foreground">不缓存结果</b>：
            这一屏的价值就是「现在的状态」，缓存把它变成昨天。
            {running ? " 正在逐个探测…" : ""}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={runChecks} disabled={running}
              className={cn("flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-medium",
                running ? "cursor-wait bg-muted text-muted-foreground" : "bg-primary/15 text-primary hover:bg-primary/25")}>
              <RefreshCw className={cn("h-3.5 w-3.5", running && "animate-spin")} /> {running ? "探测中…" : "开始自检"}
            </button>
            {list.length > 0 && (
              <span className="text-[11px] text-muted-foreground">
                可用 {okCount} · 未实现 {missing} · 报错 {broken} · 接口在但没数 {unusable}（共 {CAPABILITIES.length} 项）
              </span>
            )}
          </div>

          {list.length > 0 && (
            <ul className="mt-3 space-y-1">
              {CAPABILITIES.map(c => {
                const v = checks[c.path];
                if (!v) return null;
                return (
                  <li key={c.path} className="flex flex-wrap items-baseline gap-x-2 text-[11px] leading-relaxed">
                    <span className={cn("w-14 shrink-0 rounded px-1 text-center text-[10px]",
                      v.kind === "ok" ? "bg-primary/12 text-primary"
                        : v.kind === "missing" || v.kind === "pending" ? "bg-muted text-muted-foreground"
                          : v.kind === "unavailable" ? "bg-warning/15 text-warning"
                            : "bg-destructive/15 text-destructive")}>
                      {v.code}
                    </span>
                    <span className="font-medium">{c.label}</span>
                    <code className="font-mono text-[10px] text-muted-foreground">/api{c.path}</code>
                    <Link className="text-primary underline" to={c.where}>{c.where}</Link>
                    {c.slow && <span className="text-[10px] text-muted-foreground">（这一项可能慢）</span>}
                    {v.kind !== "ok" && <span className="text-muted-foreground">{v.note}</span>}
                  </li>
                );
              })}
            </ul>
          )}

          <Measured>
            两半各答一件事：上面这 {doctorCount ?? "若干"} 项体检回答<b className="text-foreground">环境好不好</b>（它是缓存结果，
            <code>age_seconds</code> 一到 TTL 才重算，所以页面把"算于多久前"摆在第一行），
            下面的自检回答<b className="text-foreground">接口在不在</b>（现问现答、不缓存）。
            两半都<b className="text-foreground">不回答"数据对不对"</b> —— 那是质量闸那一屏的事
            （<Link className="text-primary underline" to="/quality">/quality</Link>：那一屏自己会报"本场审了几条 / 登记了几条"，
            条数由接口现算，本屏不替它抄一份 —— 那才是这类问题真正的落点）。
          </Measured>
        </GlassCard>

        <GlassCard>
          <h2 className="text-sm font-bold">运维入口</h2>
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <Link className="text-primary underline" to="/jobs">任务调度（含调度器与任务大全）</Link>
            <Link className="text-primary underline" to="/datasource">数据获取</Link>
            <Link className="text-primary underline" to="/data-process">数据加工</Link>
            <Link className="text-primary underline" to="/quality">质量闸</Link>
            <Link className="text-primary underline" to="/coverage">覆盖体检</Link>
            <Link className="text-primary underline" to="/tdx">通达信控制</Link>
            <Link className="text-primary underline" to="/pipeline">链路总览</Link>
            <Link className="text-primary underline" to="/settings?tab=access">AI 探索 · 接入设置</Link>
          </p>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            三条刻意没做入口的（理由写在 <code>docs/FinanceTool页面映射.md</code>，用户要否就改那一处）：
            自由 SQL 查询台（给只读成品项目开自由 SQL 等于自己放大权限）、
            端口访问控制（属部署层）、动态拼页器（React 固定路由下等价于改代码）。
          </p>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
