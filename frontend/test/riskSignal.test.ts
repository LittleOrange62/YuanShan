import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  fin, latestDivergence, levelTone, levelTextTone, numText, periodTooltip, signedText,
  thresholdsText, yiText,
  extremeLine, extremeMarks, hoverLine, optionSources, xTicks,
  caveatText, densityScale, divergenceScale, trendLine, trendText, turnLine, turnMarks,
  type RiskExtremeRow, type RiskPanel, type RiskPeriod, type RiskTurn,
} from '../src/lib/riskSignal.ts';

/**
 * 「风险提示」读法层 + 首页接线。
 * 每条断言对应一句"屏幕会说谎"的情形：缺数被摆成 0、null 背离被落成"无明显背离"、
 * 阈值在页面里写死第二份、阿姆斯没回填时宽度线陪绑消失。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const lib = fs.readFileSync(path.join(HERE, '../src/lib/riskSignal.ts'), 'utf8');
const comp = fs.readFileSync(path.join(HERE, '../src/components/home/RiskSignalPanel.tsx'), 'utf8');
const home = fs.readFileSync(path.join(HERE, '../src/pages/SummaryBoard.tsx'), 'utf8');
const mount = fs.readFileSync(path.join(HERE, '../src/components/home/MarketPanorama.tsx'), 'utf8');
const system = fs.readFileSync(path.join(HERE, '../src/pages/System.tsx'), 'utf8');

const p = (o: Partial<RiskPeriod>): RiskPeriod => ({
  period: '2026-W42', label: '2026-W42', bars_in_period: 5,
  up_count: 2500, down_count: 2000, net: 500, up_ratio: 55.5,
  up_amount: 8e11, down_amount: 6e11, arms_ratio: 1.333333,
  width_pctile: 80, arms_pctile: 35, divergence: 45,
  level: 'high', level_label: '高危：家数在走强、钱没跟上', ...o,
});

const TH = { window: 20, high: 40, moderate: 20, reverse: -40 };
const LEVELS: Record<string, string> = {
  high: '高危：家数在走强、钱没跟上', moderate: '中度背离',
  reverse: '反向：钱在进、家数没跟上', none: '无明显背离',
};

/** 一份"后端正常交齐"的面板：10-21 的日期/点位用例都从这份改，缺哪个字段就当场删哪个 */
const panel = {
  available: true, date: '2026-10-21', months: 3, group: 'day',
  window: { from: '2026-07-21', to: '2026-10-21', from_actual: '2026-07-21', to_actual: '2026-10-21' },
  thresholds: TH, levels: LEVELS, arms: { available: true },
  series: [], latest: null, caveats: [],
} as unknown as RiskPanel;

test('缺数是 —，0 与 false 是真读数', () => {
  assert.equal(fin(null), null);
  assert.equal(fin(Number.NaN), null);
  assert.equal(fin(Number.POSITIVE_INFINITY), null);   // 分母 0 的比值绝不上屏
  assert.equal(fin(0), 0);
  assert.equal(numText(null), '—');
  assert.equal(numText(0), '0');                       // 净家数 0 就是打平，不是缺数
  assert.equal(signedText(null), '—');
  assert.equal(signedText(45), '+45');
  assert.equal(signedText(-12.5), '-12.5');
  assert.equal(yiText(null), '—');
  assert.equal(yiText(8e11), '8,000 亿');
});

test('档位图例只从 thresholds/levels 拼；后端没交就交空串，页面不自己写死', () => {
  const t = thresholdsText(TH, LEVELS);
  assert.ok(t.includes('≥40「高危：家数在走强、钱没跟上」'), t);
  assert.ok(t.includes('20~40「中度背离」') && t.includes('≤-40「反向：钱在进、家数没跟上」'), t);
  assert.equal(thresholdsText(undefined, LEVELS), '');
  assert.equal(thresholdsText(TH, undefined), '');
  assert.equal(thresholdsText({ ...TH, high: null as unknown as number }, LEVELS), '');
  // lib 里不许出现"后端没交就补个阈值"的兜底数字
  assert.doesNotMatch(lib, /thresholds[^\n]*\?\? *(40|20|-40)/, '阈值兜底=第二份真相源');
});

test('背离判不了 ≠ 无明显背离：null 走 —，并说清是哪种判不了', () => {
  const armsOff: RiskPanel = {
    available: true, thresholds: TH, levels: LEVELS,
    arms: { available: false, reason: '还没回填：python -m app.compute.breadth --backfill' },
    series: [p({ divergence: null, level: null, level_label: null, arms_ratio: null })],
    latest: null,
  };
  const r = latestDivergence(armsOff);
  assert.equal(r.value, '—');
  assert.equal(r.level, '—');                          // 不许落成「无明显背离」
  assert.match(r.note, /阿姆斯/);
  const thin: RiskPanel = { available: true, thresholds: TH, levels: LEVELS,
    arms: { available: true }, series: [], latest: null };
  assert.match(latestDivergence(thin).note, /不够/);
  const ok: RiskPanel = { available: true, thresholds: TH, levels: LEVELS,
    arms: { available: true }, series: [],
    latest: { divergence: 45, level: 'high', level_label: LEVELS.high, width_pctile: 80, arms_pctile: 35 } };
  const o = latestDivergence(ok);
  assert.equal(o.value, '+45');
  assert.equal(o.level, LEVELS.high);
  assert.match(o.note, /宽度分位 80 − 阿姆斯分位 35/);
  // 0 背离是真读数，不是缺数
  assert.equal(latestDivergence({ ...ok, latest: { ...ok.latest, divergence: 0, level: 'none', level_label: LEVELS.none } }).value, '0');
});

test('颜色只按后端 level 键上色；null 不落任何色', () => {
  assert.equal(levelTone('high'), 'fill-danger');
  assert.equal(levelTone('moderate'), 'fill-warning');
  assert.equal(levelTone('reverse'), 'fill-info');
  assert.equal(levelTone('none'), 'fill-muted-foreground/40');
  assert.equal(levelTone(null), 'fill-transparent');
  assert.match(levelTextTone('high'), /text-danger/);
  assert.match(levelTextTone(undefined), /muted/);
});

test('悬停给宽度三个原始数与阿姆斯两个合计；缺的那侧说"读不到"不是 0', () => {
  const panel: RiskPanel = { available: true, thresholds: TH, levels: LEVELS,
    arms: { available: true }, series: [] };
  const tip = periodTooltip(p({}), 'week', panel);
  assert.match(tip, /涨 2,500 \/ 跌 2,000 \/ 净 \+500 家/);
  assert.match(tip, /55\.5%/);
  assert.match(tip, /涨额 8,000 亿 ÷ 跌额 6,000 亿 = 1\.333/);
  assert.match(tip, /2026-W42（按周，5 个成员日.*合计重算/);
  assert.match(tip, /高危/);
  const off: RiskPanel = { available: true, thresholds: TH, levels: LEVELS,
    arms: { available: false, reason: '这一族列还没回填' }, series: [] };
  const tip2 = periodTooltip(p({ up_amount: null, down_amount: null, arms_ratio: null }), 'day', off);
  assert.match(tip2, /阿姆斯：读不到（这一族列还没回填）/);
  assert.doesNotMatch(tip2, /跌额 0/);
  // 分位缺失时 tooltip 不补 50
  const tip3 = periodTooltip(p({ width_pctile: null, arms_pctile: null, divergence: null, level: null, level_label: null }), 'day', panel);
  assert.match(tip3, /分位\/背离 —（宽度 —、阿姆斯 —/);
  assert.doesNotMatch(tip3, /—（宽度 50/);
});

test('组件接在首页「异动提醒」上面（2026-10-27 用户点名搬家），探的是不带 /api 前缀的那条路', () => {
  assert.match(mount, /import \{ RiskSignalPanel \} from "@\/components\/home\/RiskSignalPanel"/);
  const at = mount.indexOf('<RiskSignalPanel />');
  assert.ok(at > 0, '风险提示没挂在首页盘面那一屏里');
  assert.ok(at < mount.indexOf('<AbnormalMoveBoard />'), '风险提示要排在异动提醒上面');
  assert.ok(at > mount.indexOf('<FirstBoardDailyPanel'), '风险提示要排在首板分析下面（异动提醒紧上方）');
  // 只准有一处挂载：搬完还留在 `SummaryBoard` 里就是同一块画两遍（两份取数、两个 id）
  assert.doesNotMatch(home, /<RiskSignalPanel\s*\/>/, '风险提示不许在 SummaryBoard 里再挂一份');
  assert.match(comp, /useProbe<RiskPanel>\("\/quant\/risk-signal"/, 'useProbe 自己补 /api，页面里绝不写两次前缀');
  assert.match(comp, /\.thresholds/, '图上阈值读后端 thresholds');
  assert.match(comp, /levels/, '档位文字读后端 levels');
  assert.match(system, /"\/quant\/risk-signal/, '自检清单 CAPABILITIES 里要有这一条，否则接口在跑却没人验');
  // 阿姆斯不可用时宽度线不陪绑：黄条走 armsOff 分支，折线图仍在同一条件块里
  assert.ok(comp.indexOf('armsOff &&') < comp.indexOf('<svg'), '黄条说阿姆斯，svg（含宽度线）照常画');
});

// ───────────────────────────────── 10-21 追加：日期刻度、屏上悬停读数、极端点位
// 用户原话：「图表中要能看得到具体的日期啊！！」+「24 年的 10 月 7 号、最近的 2026 年 9 月 21 号
// 都是需要重点提示的极端点位」。下面每条钉的是"日期看不见"或"点位被页面自己挑"那两种说谎形状。

const dayRows = (n: number, startYear = 2026): RiskPeriod[] =>
  Array.from({ length: n }, (_, i) => {
    const d = new Date(Date.UTC(startYear, 6, 21 + i * 12));
    const iso = d.toISOString().slice(0, 10);
    return p({ period: iso, label: iso.slice(5) });
  });

test('横轴刻度真的交出日期：按日 MM-DD，跨年那一格带年份（24/60 个月档不设防就分不清是哪一年）', () => {
  const same = xTicks(dayRows(61), 6);
  assert.equal(same.length, 6, '默认 6 格');
  assert.equal(same[0].i, 0, '第一格必须是窗口第一期');
  assert.equal(same[same.length - 1].i, 60, '最后一格必须是窗口末期（"现在在哪"最该看见）');
  assert.match(same[0].text, /^\d{2}-\d{2}$/);
  // 跨年：2024-10-21 起 486 期，刻度必然跨到 2025/2026
  const cross = xTicks(dayRows(200, 2024), 6);
  const withYear = cross.filter(t => /^\d{4}-\d{2}-\d{2}$/.test(t.text));
  assert.ok(withYear.length >= 1, '跨过年界的那一格必须带年份：' + JSON.stringify(cross.map(t => t.text)));
  assert.deepEqual(xTicks([], 6), [], '没有一期就不许编刻度');
  assert.equal(xTicks(dayRows(1), 6).length, 1, '只有一期时不许除零（NaN 刻度比没刻度更糟）');
  const wk = xTicks([{ ...p({}), period: '2026-W38', label: '2026-W38' }], 6);
  assert.equal(wk[0].text, '2026-W38', '按周/月直接用后端的 label，页面不自己格式化日期');
});

test('悬停读数停在屏上（原生 title 只算补充）：日期打头，家数/涨停/家数比/阿姆斯/额占比/差值一排读完', () => {
  const row = p({ period: '2026-09-21', label: '09-21', up_ratio: 81.62, limit_up_count: 102,
    up_count: 2118, down_count: 438, count_ratio: 4.835, arms_ratio: 2.5127,
    amount_share_up: 71.53, share_gap: 10.09 });
  const one = hoverLine(row, 'day', panel);
  assert.match(one, /^2026-09-21/, '第一样必须是具体日期');
  assert.match(one, /涨停 102 家/);
  assert.match(one, /家数比 4\.83/, '后端给 4.835，两位是 4.83（写 4.84 就是把 rounding 当成判据）');
  assert.match(one, /涨票吃掉 71\.53% 的成交/);
  assert.match(one, /家数−钱 \+10\.09pp/);
  const gapless = hoverLine(p({ period: '2026-09-21', share_gap: null, amount_share_up: null }), 'day', panel);
  assert.match(gapless, /家数−钱 —/, '算不出就是 —，不许落成 +0.00pp 那种看着像读数的假数');
  assert.match(hoverLine(null, 'day', panel), /把鼠标移到图上/, '没悬停时给一句怎么用，不留空白格');
});

test('极端点位只认后端交的那份：页面按 period 找位置，找不到就报 unmatched，绝不自己补一个点', () => {
  const rows = dayRows(3);
  const d2 = { ...panel, available: true, series: rows, extremes: {
    periods_in_window: 3,
    divergence: { matched: 2, dropped_by_cap: 0, rows: [
      { period: rows[1].period, label: rows[1].label, kind: 'divergence', width_pct: 81.62,
        share_gap: 10.09, count_ratio: 4.835, arms_ratio: 2.51, amount_share_up: 71.53,
        limit_up_count: 102, severity: 10.09 }] },
    collapse: { matched: 1, dropped_by_cap: 0, rows: [
      { period: '2027-01-01', label: '01-01', kind: 'collapse', width_pct: 42.62, severity: 39,
        prev_period: rows[2].period, prev_label: rows[2].label, prev_width_pct: 81.62, drop_pp: 39 }] },
  } } as unknown as RiskPanel;
  const got = extremeMarks(rows, d2);
  assert.equal(got.marks.length, 1, '只有对得上期号的那一条能画到图上');
  assert.equal(got.marks[0].i, 1);
  assert.equal(got.unmatched, 1, '找不到对应期要报出来，不能悄悄丢');
  assert.match(extremeLine((d2.extremes!.divergence!.rows as RiskExtremeRow[])[0]),
    /宽度 81\.62%（涨停 102 家）.*家数比 4\.83 倍 而成交额只 2\.51 倍.*家数比钱多 \+10\.09pp/s);
  assert.match(extremeLine((d2.extremes!.collapse!.rows as RiskExtremeRow[])[0]),
    /08-14 宽度 81\.62% → 01-01 42\.62%（掉 39pp）/,
    '崩点必须点名上一期是哪天、多宽，以及这一期掉多少');
});

test('窗口/聚合档的按钮读后端 month_options；没交时兜底也要在屏上说清是兜底', () => {
  const a = optionSources({ ...panel, month_options: [3, 6, 12, 24, 36, 60], group_options: ['day', 'week', 'month'] } as RiskPanel);
  assert.deepEqual(a.months, [3, 6, 12, 24, 36, 60]);
  assert.equal(a.monthsFromBackend, true);
  const b = optionSources({ ...panel } as RiskPanel);
  assert.equal(b.monthsFromBackend, false, '后端没交就是要承认在兜底');
  assert.ok(b.months.includes(60), '兜底那份也得含 60 个月这一档（24 年 10 月那段在 24 个月窗口外）');
});

test('组件把日期与点位真的画出来，且不自己写死极端点阈值', () => {
  assert.match(comp, /xTicks\(rows/, '刻度由 lib 的 xTicks 算，页面不自己拼日期数组');
  assert.match(comp, /ticks\.map/, '刻度真的渲染进 svg');
  assert.match(comp, /AXIS/, 'svg 高度给横轴留了一行，否则刻度被裁掉');
  assert.match(comp, /role="status"/, '悬停读数是一块屏上的 status，不靠原生 title');
  assert.match(comp, /onMouseEnter=\{\(\) => setHover\(i\)\}/, '命中条要把鼠标所在期交进来');
  assert.match(comp, /extremeMarks\(rows, d\)/, '点位→索引只走共用那份');
  assert.match(comp, /d\?\.extremes\?\.thresholds/, '清单里那句阈值读后端 extremes.thresholds');
  assert.doesNotMatch(comp, /share_gap_pp[^}]*\|\|\s*8/, '不许写死 8pp 兜底');
  assert.doesNotMatch(comp, /width_min_pct[^}]*\|\|\s*80/, '不许写死 80% 兜底');
  assert.match(comp, /dropped_by_cap/, '被上限挤掉的条数要念出来，不然"图上只有两个点"会被读成就这么点危险');
  assert.match(comp, /skipped_no_gap/, '判不了的期数要念出来（判不了 ≠ 没命中）');
});

test('上屏那一行不留空档，组件源码里不许有裸 markdown 星号', () => {
  const day = hoverLine(p({ period: '2026-08-19', bars_in_period: 1 }), 'day', panel);
  assert.match(day, /^2026-08-19（按日） · 宽度 55\.5%/);
  assert.doesNotMatch(day, /（按日 · ）/, '把空的成员日段拼进来会念成"（按日 · ）"那种半截话');
  const wk = hoverLine(p({ period: '2026-W42' }), 'week', panel);
  assert.match(wk, /按周 · 5 个成员日/);
  // 这块屏底部那句"看法"是裸 JSX 文字，不过 plain()：星号会原样上屏（10-21 实测抓到过一次）
  const body = comp.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(body, /\*\*/, '组件正文里不许出现成对星号：' + (body.match(/.{0,40}\*\*.{0,40}/) || [''])[0]);
});

// ─────────────────────────────── 10-26 追加：趋势与转折点、背离条量程、密度线
// 用户原话：「这个图看不出趋势和转折点你帮我重新规划一下看」。
// 后端量过：背离逐期独立（lag1 自相关 0.01~0.03）⇒ 拐点只能打在水位线上，背离那一族只交密度。
// 下面每条钉的是「页面自己造趋势」或「量程把一整条压成一根线」那两种说谎形状。

test('背离条量程按窗口实际极值取，但至少要容得下后端那几条档位线', () => {
  const rows = [p({ divergence: 12 }), p({ divergence: -8 }), p({ divergence: 45 })];
  const s = divergenceScale(rows, TH)!;
  assert.equal(s.maxAbs, 45);
  assert.ok(s.bound >= 45 && s.bound >= TH.high, '档位线画到图外＝看着像从来没到过高危');
  assert.ok(s.bound < 100, '实测窗口取值只有 −40~+45，按 ±100 硬映射半高只剩 2.1 像素（原来那条线的病）');
  assert.equal(s.computable, 3);
  // 只有小背离时量程仍要容得下档位线
  const small = divergenceScale([p({ divergence: 3 }), p({ divergence: -2 })], TH)!;
  assert.equal(small.bound, Math.round(40 * 1.06 * 10) / 10);
  // 一期都算不出 ⇒ null：页面不画条、改念后端那句「判不了」，绝不拿 0 顶
  assert.equal(divergenceScale([p({ divergence: null })], TH), null);
  assert.equal(divergenceScale([], TH), null);
});

test('密度线量程 0~窗口最大值；判不了是 null 不是 0', () => {
  const s = densityScale([p({ risk_density: 0 }), p({ risk_density: 3 }), p({ risk_density: 1 })])!;
  assert.deepEqual(s, { max: 3, computable: 3 });
  // 全是 0 是真读数（这段一期都没达到中度），但量程不许是 0（除零＝NaN 坐标）
  assert.equal(densityScale([p({ risk_density: 0 }), p({ risk_density: 0 })])!.max, 1);
  assert.equal(densityScale([p({ risk_density: null })]), null);
  assert.equal(densityScale([p({})]), null, '后端没交这一列就是判不了，不许当成 0 期');
});

test('转折点只认后端交的那份：按 period 找位置，找不到报 unmatched，绝不自己补拐点', () => {
  const rows = dayRows(4);
  const d2 = { ...panel, series: rows, trend: {
    smooth: 10, zigzag_pp: 10, density: { window: 20, threshold: 20, computable: 0, judged: 0, periods: 4 },
    rows: [
      { period: rows[1].period, label: rows[1].label, kind: 'peak', ma_value: 62.31, raw_up_ratio: 66.2, swing_pp: null },
      { period: rows[3].period, label: rows[3].label, kind: 'trough', ma_value: 41.02, raw_up_ratio: 38.4, swing_pp: 21.29 },
    ],
    boundary: { period: rows[0].period, label: rows[0].label, kind: 'trough', ma_value: 44.1, raw_up_ratio: 47.0 },
  } } as unknown as RiskPanel;
  const got = turnMarks(rows, d2);
  assert.deepEqual(got.marks.map(m => m.i), [1, 3]);
  assert.equal(got.unmatched, 0);
  assert.equal(got.boundary?.i, 0);
  const off = turnMarks(rows, { ...d2, trend: { ...d2.trend!, rows: [
    { period: '2027-01-01', label: '01-01', kind: 'peak', ma_value: 60, raw_up_ratio: 60 }] } } as RiskPanel);
  assert.equal(off.marks.length, 0);
  assert.equal(off.unmatched, 1, '窗口换了导致对不上期号要报出来，不能悄悄丢');
  assert.equal(turnMarks(rows, panel).marks.length, 0, '后端没交 trend 就是没有拐点，页面不自己算一份');
});

test('拐点那一行日期打头、说清顶还是谷、带上当日原值与前一个拐点的落差', () => {
  const peak: RiskTurn = { period: '2026-09-30', label: '09-30', kind: 'peak',
    ma_value: 49.99, raw_up_ratio: 52.3, swing_pp: 13.91 };
  assert.match(turnLine(peak, 10), /^2026-09-30 10 期平滑宽度顶 49\.99%/, '日期打头（用户：要能看得到具体日期）');
  assert.match(turnLine(peak, 10), /当日 52\.3%/);
  assert.match(turnLine(peak, 10), /相差 13\.9pp/);
  assert.match(turnLine({ ...peak, kind: 'trough' }, 10), /宽度谷/);
  assert.doesNotMatch(turnLine({ ...peak, swing_pp: null }, 10), /相差 —pp/, '第一个拐点没有前一个可比，别拼出半截话');
  assert.match(turnLine(peak, 10, true), /窗口第一期.*不算「转折点」/s);
});

test('当前读数下面那一行：平滑与密度各自可能是「判不了」，都不许落成 0', () => {
  const ok: RiskPanel = { ...panel, trend: { smooth: 10, density: { window: 20, threshold: 20 } },
    latest: { up_ratio: 63.1, up_ratio_ma: 60.5, risk_density: 3 } };
  assert.match(trendLine(ok), /平滑 10 期宽度 60\.5%（当日 63\.1%）/);
  assert.match(trendLine(ok), /近 20 期里 3 期达到中度及以上背离/);
  const none: RiskPanel = { ...panel, trend: { smooth: 10, density: { window: 20, threshold: 20 } },
    latest: { up_ratio: 63.1, up_ratio_ma: null, risk_density: null } };
  const line = trendLine(none);
  assert.match(line, /平滑 10 期宽度 —/);
  assert.match(line, /判不了/);
  assert.doesNotMatch(line, /0 期达到/, '密度 null 不许念成「近 20 期里 0 期」—— 那是把判不了画成很安全');
  assert.match(trendLine(panel), /平滑宽度 —/, '后端连 trend 都没交时，参数也不许页面自己写死');
});

test('趋势图例的参数全读后端；后端没交齐就交空串，页面不退回自己那份 MA10/10pp', () => {
  const t = trendText({ smooth: 10, zigzag_pp: 10, rows: [{ period: 'x', label: 'x', kind: 'peak', ma_value: 1 }],
    density: { window: 20, threshold: 20 } });
  assert.match(t, /近 10 期均线/);
  assert.match(t, /≥10pp/);
  assert.match(t, /本窗口确认了 1 个/);
  assert.match(t, /近 20 期里有几期背离 ≥20/);
  assert.equal(trendText(undefined), '');
  assert.equal(trendText({ zigzag_pp: 10 }), '', '少了平滑窗口就不许拼半句');
  // 组件源码里不许出现「后端没交就补个 10」的兜底
  assert.doesNotMatch(comp, /smooth[^\n]*\?\? *(10|20)\b/, '平滑窗口兜底=第二份真相源');
  assert.doesNotMatch(comp, /zigzag_pp[^\n]*\?\? *10/, '幅度门槛兜底=第二份真相源');
});

test('「判不了」的那几句念后端 caveat 原句；组件真的把三条新东西画进 svg', () => {
  const withCav: RiskPanel = { ...panel, caveats: [
    { code: 'pct_window', text: '这一窗口 14 期，分位要满 20 期 ⇒ 背离整列 null。' },
    { code: 'density_unjudged', text: '密度要满 20 期滚动样本 ⇒ 密度线整条 null。' },
    { code: 'turns_none', text: '这一窗口没有任何已确认的反转。' }] };
  assert.equal(caveatText(withCav, 'pct_window'), '这一窗口 14 期，分位要满 20 期 ⇒ 背离整列 null。');
  assert.equal(caveatText(withCav, 'turns_none'), '这一窗口没有任何已确认的反转。');
  assert.equal(caveatText(withCav, '没有这条'), null);
  assert.equal(caveatText(panel, 'pct_window'), null, '后端没交就是 null，页面自己兜一句话也行，但不许说成后端说的');
  assert.match(comp, /divergenceScale\(rows, th\)/, '背离条量程由 lib 算，页面不自己写死 ±100');
  assert.doesNotMatch(comp, /\(v \/ 100\) \* \(DH/, '那根按 ±100 硬映射的旧尺子必须没了');
  assert.match(comp, /densityScale\(rows\)/);
  assert.match(comp, /turnMarks\(rows, d\)/);
  assert.match(comp, /trendLine\(d\)/, '当前读数下面要有平滑与密度那一行');
  assert.match(comp, /caveatText\(d, "density_unjudged"\)/, '密度判不了时念后端原句');
  assert.match(comp, /TOP_AX/, '横轴刻度跟到第三条带子下面，否则日期被裁掉');
});
