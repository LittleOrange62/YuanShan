import { useMemo } from "react";
import { cn } from "@/lib/utils";

/**
 * 情绪两档的逐场折线（首页「市场情绪 · 乐咕乐股口径」里那两块卡各配一张）。
 *
 * 用户 2026-10-06 的原话："做一个折线图出来（中性为 0 轴，普通 和 活跃 中间值为 0 轴）
 * —— 如果折线图可以锐化就更好了，就是展现趋势 —— 20 日差不多吧。"
 * 所以这里定死三件事：
 *
 * 1. **0 轴是判据的一部分，不是画出来的装饰。** 编码住在 `app/compute/sentiment_daily.py`
 *    （大盘宽度：中性=0；题材投机：普通与活跃的**正中间**=0，故取值 ±0.5/±1.5）。
 *    前端不重新分档、不自己算均分 —— 一算就会出现"卡片说偏强、图上算出中性"。
 * 2. **锐化 = 让趋势可读**，不是加特效：面积朝 0 轴填充（在线上方=暖色、下方=冷色）、
 *    最后一点加粗、旁边给"近 N 场均分 + 近 5 场方向"。方向是用**首尾平均之差**算的，
 *    比"今天比昨天高"稳，比回归斜率简单到可以在脚注里说清。
 * 3. **点数不足就明说不画。** 只有 3 场时画一条线看起来像"趋势成立"，那是骗人；
 *    这时卡片上给"已落库 N 场，折线要 ≥6 场"。
 *
 * 数据来自 `analytics.market_sentiment_daily`（接口字段 `overview.sentiment_series`）。
 * 每个点带着 `source`：`legu` 是收盘后现抓乐咕的权威口径，`local` 是由
 * `analytics.market_breadth` 回算的（涨停数是单边判据，题材那一档可能偏乐观一档）。
 */

export interface TrendPoint {
  date?: string | null;
  breadth_score?: number | null;
  theme_score?: number | null;
  breadth_level?: string | null;
  theme_level?: string | null;
  limit_up_real?: number | null;
  up_count?: number | null;
  down_count?: number | null;
  ratio?: number | null;
  source?: string | null;
}

type Field = "breadth_score" | "theme_score";

const BANDS: Record<Field, [string, number][]> = {
  breadth_score: [["普涨", 2], ["偏强", 1], ["中性", 0], ["偏弱", -1], ["冰点", -2]],
  theme_score: [["亢奋", 1.5], ["活跃", 0.5], ["普通", -0.5], ["冰点", -1.5]],
};

const MIN_POINTS = 6;

export function SentimentTrend({ points, field, sessions = 20 }: {
  points: TrendPoint[]; field: Field; sessions?: number;
}) {
  const series = useMemo(() => {
    const tail = points.slice(-sessions);
    const vals = tail.map(p => p[field]);
    const ok = vals.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    return { tail, vals, n: ok.length };
  }, [points, field, sessions]);

  if (series.n < MIN_POINTS) {
    return (
      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground/70">
        折线先不画：库里这一档只有 {series.n} 场有数（要 ≥{MIN_POINTS} 场才画，
        三五个点连成线会被读成"趋势成立"）。每天收盘后由加工链的 `sentiment` 步续一格。
      </p>
    );
  }

  const bands = BANDS[field];
  const top = Math.max(...bands.map(b => b[1]));
  const bottom = Math.min(...bands.map(b => b[1]));
  const W = 300, H = 74, PAD = 8;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / Math.max(1, series.tail.length - 1);
  const y = (v: number) => H - PAD - ((v - bottom) / (top - bottom)) * (H - 2 * PAD);

  const pts = series.tail.map((p, i) => ({ p, i, v: p[field] })).filter(o => typeof o.v === "number");
  const line = pts.map(o => `${x(o.i)},${y(o.v as number)}`).join(" ");
  const zero = y(0);
  // 朝 0 轴填充：整条折线 + 0 轴两端封口，暖段/冷段各一块（同一段跨 0 会被切成两块，
  // 这里不切 —— 切了就要处理交叉点，脚注反而更长。用 clip 把上下分开着色更简单。）
  const area = `${x(pts[0].i)},${zero} ${line} ${x(pts[pts.length - 1].i)},${zero}`;
  const last = pts[pts.length - 1];
  const mean = pts.reduce((a, o) => a + (o.v as number), 0) / pts.length;
  const half = Math.max(1, Math.floor(pts.length / 2));
  const firstMean = pts.slice(0, half).reduce((a, o) => a + (o.v as number), 0) / half;
  const lastMean = pts.slice(-half).reduce((a, o) => a + (o.v as number), 0) / half;
  const drift = lastMean - firstMean;
  const dir = drift > 0.2 ? "↗ 走强" : drift < -0.2 ? "↘ 走弱" : "→ 走平";
  const pos = pts.filter(o => (o.v as number) > 0).length;

  return (
    <div className="mt-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-20 w-full" role="img"
        aria-label={`近 ${pts.length} 场这一档的逐场读数折线，0 轴是${field === "breadth_score" ? "中性" : "普通与活跃的中间"}，末点 ${(last.v as number).toFixed(1)}`}>
        <defs>
          <clipPath id={`above-${field}`}><rect x="0" y="0" width={W} height={zero} /></clipPath>
          <clipPath id={`below-${field}`}><rect x="0" y={zero} width={W} height={H - zero} /></clipPath>
        </defs>
        <polygon points={area} className="fill-primary/25" clipPath={`url(#above-${field})`} />
        <polygon points={area} className="fill-muted-foreground/20" clipPath={`url(#below-${field})`} />
        {bands.map(([label, v]) => (
          <g key={label}>
            <line x1={PAD} y1={y(v)} x2={W - PAD} y2={y(v)}
              className={cn("stroke-border/60", v === 0 && "stroke-muted-foreground/70")}
              strokeDasharray={v === 0 ? undefined : "2 4"} strokeWidth={v === 0 ? 1 : 0.6} />
            <text x={W - PAD} y={y(v) - 1.5} textAnchor="end"
              className="fill-current text-[7px] text-muted-foreground/70">{label}</text>
          </g>
        ))}
        <polyline points={line} fill="none" strokeWidth={1.8} className="stroke-primary" />
        <circle cx={x(last.i)} cy={y(last.v as number)} r={2.8} className="fill-primary" />
        {pts.map(o => (
          <circle key={String(o.p.date)} cx={x(o.i)} cy={y(o.v as number)} r={2.6}
            className="fill-transparent">
            <title>{`${o.p.date ?? "?"}　${bandName(field, o.v as number)}（编码 ${(o.v as number).toFixed(1)}）`
              + (field === "theme_score" ? `　真实涨停 ${o.p.limit_up_real ?? "—"}`
                : `　涨 ${o.p.up_count ?? "—"}/跌 ${o.p.down_count ?? "—"}`)
              + `　${o.p.source === "legu" ? "乐咕现抓" : "由 market_breadth 回算"}`}</title>
          </circle>
        ))}
      </svg>
      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10px] leading-relaxed text-muted-foreground/70">
        <span>近 {pts.length} 场（{series.tail[0].date} ~ {last.p.date}）</span>
        <span className={cn(drift > 0.2 ? "text-primary" : drift < -0.2 ? "text-muted-foreground" : "")}>
          {dir} {drift >= 0 ? "+" : ""}{drift.toFixed(2)}
        </span>
        <span>均值 {mean >= 0 ? "+" : ""}{mean.toFixed(2)}</span>
        <span>0 轴以上 {pos}/{pts.length} 场</span>
      </p>
    </div>
  );
}

function bandName(field: Field, v: number): string {
  let best = BANDS[field][0];
  for (const b of BANDS[field]) if (Math.abs(b[1] - v) <= Math.abs(best[1] - v)) best = b;
  return best[0];
}
