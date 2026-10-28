import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { ExplainBox } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { BoardLadderPanel } from "@/components/qd/BoardLadderPanel";
import { NewsPanel } from "@/components/qd/NewsPanel";
import { SectorCyclePanel } from "@/components/qd/SectorCyclePanel";
import { SentimentCyclePanel } from "@/components/qd/SentimentCyclePanel";
import { TrackSection } from "@/components/qd/Track";
import { plain } from "@/lib/agent";
import { useBoardLadder, useDistribution, useLeaderFollow, useOwnSectorCycle, useQdNews, useSectorCycle, useSentimentCycle } from "@/lib/useQd";

/**
 * quantdash 口径双轨对照页（契约 02）。
 *
 * 页面存在的理由是**差异本身**：同一交易日，两套算法各判成什么、为什么不一样。
 * 所以这里没有任何"综合"值，也没有任何占位数据 —— 本地计算尚未覆盖该项时，qd 轨就
 * 显示"接口未提供"，本轨有数就显示本轨的数。
 */

/** 后端 date 缺省时自己取最新 trade_date；前端不预填"今天"（02 §8 第 3 条） */
export function QdCompare() {
  const [date, setDate] = useState("");
  const [nonce, setNonce] = useState(0);

  const sentiment = useSentimentCycle(date, nonce);
  const ladder = useBoardLadder(date, nonce);
  const sector = useSectorCycle(date, nonce);
  const ownSector = useOwnSectorCycle(date, nonce);
  const follow = useLeaderFollow(date, nonce);
  const news = useQdNews(date, nonce);
  // 区间分布是整段历史的统计，不跟随日期选择
  const dist = useDistribution(nonce);

  const shown = [sentiment, ladder, sector, ownSector]
    .map(s => (s.kind === "ok" ? (s.payload.date ?? "") : ""))
    .filter(Boolean);
  const effective = date || (shown.length ? shown[0] : "");

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="quantdash 口径对照" level={1}
        subtitle="两套算法并排给出，差异可见、来源可查。不做综合、不加权、不替谁更可信。"
        actions={
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-muted-foreground">
              交易日
              <input type="date" value={date} className="ml-1.5 rounded border border-border bg-background p-1.5 text-[12px]"
                onChange={e => setDate(e.target.value)} />
            </label>
            <button type="button" onClick={() => setNonce(n => n + 1)}
              className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
              <RefreshCw className="h-3.5 w-3.5" /> 重新取数
            </button>
          </div>
        } />

      <p className="mb-4 text-[11px] leading-relaxed text-muted-foreground">
        当前生效交易日：<b className="text-foreground">{effective || "—"}</b>
        {date ? "" : "（未指定 → 由后端取各表最新 trade_date，不同表可能落在不同日期）"}
      </p>

      <div className="space-y-5">
        <Section title="情绪周期" note="quantdash 自己的页面 / AI / 飞书用三套矛盾阈值，三列必须一起看。本轨暂无阶段算法。">
          <SentimentCyclePanel state={sentiment} dist={dist} />
        </Section>

        <Section title="连板天梯" note="qd 轨含首板分档；封板时间不可得的交易日会整列灰底，不像 quantdash 原版那样哈希编一个。">
          <BoardLadderPanel state={ladder} dist={dist} />
        </Section>

        <Section title="板块周期" note="两套东西不是同一个东西：本轨=生命周期阶段，qd 轨=轮动矩阵+持续性。合并会逼另一套字段留空，所以分块。">
          <SectorCyclePanel own={ownSector} qd={sector} follow={follow} />
        </Section>

        <Section title="信息采集" note="quantdash 口径的本地快讯轨（newswire 落的 jsonl）。规则：没有就是没有，绝不放示例新闻。">
          <div className="space-y-2">
            <p className="text-[11px] text-muted-foreground">
              本轨另有资讯入口：
              <Link className="mx-1 text-primary underline" to="/intel">「资讯雷达」</Link>
              （RSS 赛道聚合 + 关注股新闻/公告）。下面是 <code>qd</code> 轨（东财 7×24 主 + 新浪 7×24 备）。
            </p>
            <TrackSection track="qd" title="当日快讯（本地 jsonl）"
              hint="每条同时给「发布时刻」与「采集时刻」；发布时刻缺失时会标明显示的是采集时刻。">
              <NewsPanel state={news} />
            </TrackSection>
          </div>
        </Section>
      </div>

      <Disclaimer compact />
      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground/80">
        {plain("本项目要求 AI 在数据不足时明说，不做推测补齐；本页同理 —— 取不到就显示取不到，不拿上一场的数、不填 0、不猜。")}
      </p>
    </div>
  );
}

/** 每一块的口径说明走通用说明框（用户 2026-10-14：说明别摊在读数上面、默认收起）。
 *  改这一处，这一屏四块跟着收；这里只上屏页面自己写的句子，后端交来的告警不走这条路。 */
function Section({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <GlassCard>
      <h2 className="text-base font-bold">{title}</h2>
      <ExplainBox title={`${title} · 这一块的口径与边界`}>
        <p>{plain(note)}</p>
      </ExplainBox>
      {children}
    </GlassCard>
  );
}
