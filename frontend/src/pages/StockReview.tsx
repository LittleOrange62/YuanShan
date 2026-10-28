import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { StockSearch } from "@/components/ui/StockSearch";
import { StockDossier } from "@/components/quant/StockDossier";
import { TrendQuant } from "@/components/quant/TrendQuant";
import { FinancialPeriodBoard } from "@/components/quant/FinancialPeriodBoard";
import { VolumeSimilarityBoard } from "@/components/quant/VolumeSimilarityBoard";
import { StockFacts } from "@/components/stock/StockFacts";
import { StockMetricsLedger } from "@/components/stock/StockMetricsLedger";
import { PriceVolumeTrend } from "@/components/stock/PriceVolumeTrend";
import { StockData } from "@/pages/StockData";
import { DeepDive } from "@/pages/DeepDive";

/**
 * 个股深度复盘（用户 2026-10-15 点名新做一屏，且要「浏览器打开新窗口」那种）：
 * 「把 分组自选 里的 个股点击分析 改成选中上面的票双击即可，然后把
 *   要分析哪一只 / 这只票的量化档案 / 该支票的趋势量化计算 / 窗口趋势
 *   这几个个股分析的功能点都整合过去……把个股研究、多空辩论这两个功能点都移动到这个页面」。
 *
 * 三条实现约束（这一晚定下来的，别改回去）：
 *  · 一只票只有一个真源 = 地址里的 `symbol`。页首那一个 `StockSearch` 写它，下面几块读它；
 *    哪一块自己再存一份「当前票」，就会出现「框里是茅台、档案是另一只」（10-14 在 /grouped 上真抓到过）。
 *  · 几块都是一份实现、多处调用：`StockDossier`/`TrendQuant` 是档案那两叠的共用组件；
 *    `StockData`/`DeepDive` 就是原「个股研究」「多空辩论」两屏的同一份取数代码 ——
 *    撤掉的只是页面外壳（大标题与各自的选票框），老地址 `/stock-data`、`/agent/deepdive` 只做跳转。
 *  · 不带传参进来就明说「还没选一只」，不自动挑一只：默认值是「随便给一只」的话，
 *    人会把那一屏的结论当成自己想看的票的结论。
 *
 * 带代码的那一路是从 `/grouped` 明细、`/my-stocks` 持仓与本机名单「双击」开新窗口；
 * 导航与主界面卡片那个入口跳的是本页「不带传参」的版本（用户明确区分了这两种）。
 *
 * 2026-10-16 用户第二次点名这一屏要「更深」，定了块序：
 *   个股信息 → 量化档案 → 趋势量化 → 财务多期与外推 → 近 30 根量价与相似样本 → 个股研究 → 多空辩论。
 * 「个股信息」提到第一块（原话「把个股信息这些挪上去」）：它只读本机库、不出网，所以选中就出数，
 * 读者先有身份与最近一场价，再看后面每一面。下面两块是这一轮新加的判读屏
 * （`FinancialPeriodBoard`／`VolumeSimilarityBoard`），判据都不在前端：
 * 三条财务外推只住 `app/compute/financial_forecast.py`，六个相似度分量与权重只住 `duanxian/volume_similarity.py`。
 */
export function StockReview() {
  const [query, setQuery] = useSearchParams();
  const code = query.get('symbol') || '';
  // 打字不写地址：每敲一个字就换 URL，会让下面「个股研究」那一块对着一半的代码出网取数，
  // 也会把浏览器历史塞满。地址里只在「选中 / 提交」那一刻变。
  const [draft, setDraft] = useState(code);
  useEffect(() => { setDraft(code); }, [code]);
  const commit = (value: string) => {
    const v = value.trim().toUpperCase();
    setQuery(previous => {
      const next = new URLSearchParams(previous);
      if (v) next.set('symbol', v); else next.delete('symbol');
      return next;
    }, { replace: true });
  };
  const from = query.get('from') || '';

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="个股深度复盘" level={1}
        subtitle="一只票的信息 · 档案 · 趋势量化 · 财务多期与外推 · 量价走势（日/周/月）· 近 30 根量价与历史最相近样本 · 公开资料 · 多空辩论，集中在这一屏（可新开窗口并排放着看）"
        actions={<div className="flex items-center gap-2">
          <StockSearch value={draft} ariaLabel="选一只票做深度复盘：代码、名称或拼音首字母"
            kinds="stock,etf"
            widthClass="w-72" placeholder="代码 / 名称 / 拼音首字母，如 600519、茅台、GZMT"
            onType={setDraft} onPick={c => commit(c)} onEnter={() => commit(draft)}
            submit={<button type="button" onClick={() => commit(draft)}
              className="rounded-lg border border-primary/50 bg-primary/10 px-2.5 py-1 text-[12px] text-primary hover:bg-primary/20">
              看这一只
            </button>} />
          {!!code && (
            <button type="button" onClick={() => commit('')}
              className="rounded-lg border border-border/60 px-2 py-1 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
              清掉这一只
            </button>
          )}
        </div>} />

      <GlassCard>
        <h2 className="text-sm font-bold">现在复盘的是哪一只</h2>
        {code
          ? <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            <b className="font-mono text-foreground">{code}</b>
            {from ? <>；从「{from}」双击那一行带过来的</> : <>；由上方那个搜索框选中（地址里的 <code>symbol</code> 是唯一真源）</>}
            。下面每一块吃的都是这一只，各块自己标注它的数出自哪一天。
          </p>
          : <p className="mt-1 text-[12px] leading-relaxed text-warning">
            还没选一只，这一页不替你挑（挑了就等于把别人的票当成你想看的那只）。
            在上方输入代码、名称或拼音首字母选一只，或从「分组自选」「重点关注（持仓）」里双击某一行开这一页。
          </p>}
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          美 / 港 / 韩股不走本机字典（那里没有它们）：直接敲 AAPL、00700、005930.KS 再回车，
          下面「个股研究」那一块会按市场走公开源。
        </p>
        <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
          进页之后谁会自动跑、谁要你点一下，这里说清（不看代码就别猜）：
          <b className="text-foreground">个股信息 / 档案 / 趋势量化 / 窗口趋势 / 财务多期 / 量价走势 / 量能相似样本</b>
          只读本机库，选中就出数（其中「量能相似样本」第一次约 6 秒，之后走进程内缓存）；
          <b className="text-foreground">个股研究</b> 也自动跑 —— 地址里一有代码，半秒后它就并发打十来个只读接口，
          其中多数要出网抓公开源（行情 / 估值 / 研报 / 新闻 / 财务 / 公告 / 资金面），所以要等几秒，那块里另给了一个「重新取这一只的公开资料」按钮；
          <b className="text-foreground">多空辩论</b> 不自动跑（它要用已接入的 AI 来源现场生成，一次约 2-3 分钟、且要出网），
          想看得点那一块里的「开始多空辩论」。
        </p>
      </GlassCard>

      <StockFacts code={code || null} />
      <StockDossier code={code || null} date={undefined} />
      <TrendQuant code={code || null} />
      <FinancialPeriodBoard code={code || null} />
      <PriceVolumeTrend code={code || null} />
      <VolumeSimilarityBoard code={code || null} />
      <StockData />
      <DeepDive />
      <StockMetricsLedger code={code || null} />

      <Disclaimer compact />
    </div>
  );
}
