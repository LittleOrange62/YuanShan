import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  DEFAULT_SERIES, DEFAULT_RANGE, MISSING_TEXT, MIN_PX_PER_SESSION, NULL_TEXT, VIEW_MIN,
  alertCells, alertHeadline, alertSampleNotes,
  alertTitle, anomalyIndex, bandLegend, bandText, bridgeSegments, viewLen, RAW_MIN_STEP,
  dateTicks, dayReadout, lineAvailability, missingText, pairScales, pointValue, trendNote, turnMarks,
} from '../src/lib/nationalTeam.ts';

/**
 * 复盘页顶部「国家队代理指标」的判读层 + 调用点接线（用户 2026-10-26 点名的那张图）。
 *
 * 这一层只做格式化和"缺数怎么说话"：分档、异动、阈值、序列名与单位全部来自后端
 * `duanxian/national_team.py`（随 `thresholds`/`levels`/`series_options` 交出）。
 * 钉的是四条会静默说谎的事：
 *  · 占比的分母比分子晚入库 ⇒ 早期就是 null，画成 0 或连过去都是假读数；
 *  · 净申赎没有历史档（有正负的量级取分位不成立）⇒ 不许落成"中性"；
 *  · 点一个点必须交出**完整日期**（悬停只有数字不算交付）；
 *  · 屏幕上不许念机器码（`float_mv_yuan.partial` 那种要翻成句子）。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const READ = fs.readFileSync(path.join(HERE, '../src/lib/nationalTeam.ts'), 'utf8');
const PANEL = fs.readFileSync(path.join(HERE, '../src/components/NationalTeamPanel.tsx'), 'utf8');
const PAGE = fs.readFileSync(path.join(HERE, '../src/pages/AgentReview.tsx'), 'utf8');

const P = (day: string, extra: Record<string, unknown> = {}) => ({
  trade_date: day, etf_count: 4, share_yi: 800, size_yi: 3400, net_yi: 2,
  float_mv_wy: 48, ratio_float_pct: 0.7, ratio_total_pct: 0.66, missing: null, ...extra,
});
const RATIO_OPT = { key: 'ratio_float', label: '占沪深300流通市值', unit: '%',
  field: 'ratio_float_pct' as const, kind: 'stock', judge: 'jump', stock: true, read: '分母本地自算' };
const FLOW_OPT = { key: 'flow', label: '当日净申赎', unit: '亿份', field: 'net_yi' as const,
  kind: 'flow', judge: 'level', stock: false, read: '走分区 LAG 的视图' };
const VIEW = (opt: typeof RATIO_OPT, over: Record<string, unknown> = {}) => ({
  series: opt, bands: { available: true, samples: 45, cuts: { p10: 0.44, p25: 0.45, p50: 0.46,
    p75: 0.48, p90: 0.55 }, from: '2026-07-22', to: '2026-10-27' },
  current: 0.7, band_key: 'ultra_high', band_label: '超高',
  anomaly_meta: { judge: opt.judge, threshold: 0.05, samples: 44, rule: null },
  anomalies: [], anomaly_count_all: 0, anomaly_count_window: 0, ...over,
});
const SCALE = (key: string, short: string, pts: Record<string, unknown>[]) => ({
  key, label: `${short}（清单说明）`, short, note: '这一把尺怎么圈的',
  etf_count_latest: 4, share_sessions: pts.length, share_from: pts[0]?.trade_date ?? null,
  share_to: pts[pts.length - 1]?.trade_date ?? null,
  ratio_sessions: pts.filter(p => p.ratio_float_pct != null).length,
  ratio_from: pts.find(p => p.ratio_float_pct != null)?.trade_date ?? null,
  points: pts,
  series_view: { ratio_float: VIEW(RATIO_OPT), flow: VIEW(FLOW_OPT, {
    bands: { available: false, not_applicable: true, samples: 3, reason: '净申赎有正负，分位档不成立' },
    band_key: null, band_label: null, current: 13.5 }) },
});

test('两把尺拉成同一根日期轴，缺的那一天留空洞而不是补一个假点', () => {
  const a = SCALE('big4', '四大', [P('2026-01-02'), P('2026-01-03'), P('2026-01-05')]);
  const b = SCALE('all', '全部', [P('2026-01-02'), P('2026-01-04')]);
  const { dates, byScale } = pairScales([a, b] as never);
  assert.deepEqual(dates, ['2026-01-02', '2026-01-03', '2026-01-04', '2026-01-05']);
  assert.equal(byScale.big4[2], undefined, 'big4 没有 01-04 这一场 —— 不许拿前一天的值顶上');
  assert.equal(byScale.all[3], undefined);
  assert.equal(pointValue(byScale.big4[0], 'ratio_float_pct'), 0.7);
  assert.equal(pointValue(byScale.all[1], 'ratio_float_pct'), null, '轴上缺的那一格没有点，不是 0');
  assert.equal(pointValue(undefined, 'ratio_float_pct'), null);
});

test('线有多长要说出来：null 不算读数，也不算 0', () => {
  const pts = [P('2026-01-02'), { ...P('2026-01-03'), ratio_float_pct: null },
    { ...P('2026-01-04'), ratio_float_pct: null }, P('2026-01-05')] as never[];
  const av = lineAvailability(pts, 'ratio_float_pct');
  assert.deepEqual({ have: av.have, total: av.total, first: av.first },
    { have: 2, total: 4, first: '2026-01-02' });
  assert.equal(lineAvailability([], 'ratio_float_pct').total, 0);
});

test('档位文字三种情况分开念：判不了 / 不建档 / 缺当期，且不建档那句读后端原话', () => {
  assert.equal(bandText(VIEW(RATIO_OPT)), '超高（这把尺历史 45 场）');
  assert.match(bandText(VIEW(RATIO_OPT, { bands: { available: false, samples: 6,
    reason: '样本不够分不出档' } })), /样本不够分不出档/);
  // 后端那句 reason 原样交出来 —— 页面复述一遍就是第二份会漂移的说法
  assert.match(bandText(VIEW(RATIO_OPT, { bands: { available: false, not_applicable: true,
    samples: 60, reason: '净申赎有正负，历史分位档对它不成立' } })),
  /净申赎有正负，历史分位档对它不成立/);
  // 分档在、当期值缺 ⇒ —（不许落到"中性"）
  assert.equal(bandText(VIEW(RATIO_OPT, { band_label: null })), '—');
  assert.equal(bandText(undefined), '—');
  // 2026-10-29：后端交跨度句时**必须念跨度句**。只念"这把尺历史 45 场"会被读成历史位置，
  // 而那 45 场其实是 2026-07-22 起的一个多月（占比线的分母入库晚）。
  assert.equal(bandText(VIEW(RATIO_OPT, { bands: { available: true, samples: 45,
    span_note: '46 个样本，日期 2026-07-22 ~ 2026-09-05（跨 45 天）' } })),
  '超高（这一条 46 个样本，日期 2026-07-22 ~ 2026-09-05（跨 45 天） 的分位）');
});

test('临界线的图例全部读后端那五个切点，页面一个数都不写死', () => {
  const t = bandLegend(VIEW(RATIO_OPT), { ultra_low: '超低', low: '偏低', mid: '中性',
    high: '偏高', ultra_high: '超高' });
  assert.match(t, /超低 ≤0\.44/);
  assert.match(t, /超高 >0\.55/);
  assert.match(t, /p10\/p25\/p75\/p90/);
  assert.equal(bandLegend(VIEW(RATIO_OPT, { bands: { available: false, samples: 3 } }), {}), '');
});

test('横轴刻度首尾必留，一天也不许多塞', () => {
  assert.deepEqual(dateTicks([], 6), []);
  const one = dateTicks(['2026-01-02'], 6);
  assert.equal(one.length, 1);
  const t = dateTicks(Array.from({ length: 100 }, (_v, i) => `D${String(i).padStart(3, '0')}`), 6);
  assert.equal(t[0].i, 0);
  assert.equal(t[t.length - 1].i, 99, '最后一场必须落在刻度上，否则读者以为线断在半路');
  assert.ok(t.length <= 6);
});

test('点一个点交出完整日期，两把尺同一天并排，缺数标记翻成句子', () => {
  const a = SCALE('big4', '四大', [P('2026-07-22', { net_yi: 12.5 }),
    P('2026-07-23', { ratio_float_pct: null, float_mv_wy: null, missing: ['float_mv_yuan'] })]);
  const b = SCALE('all', '全部', [P('2026-07-22')]);
  const d = { scales: [a, b], series_options: [RATIO_OPT, FLOW_OPT] } as never;
  const { dates } = pairScales([a, b] as never);
  const r = dayReadout(d, dates, 0, 'ratio_float')!;
  assert.equal(r.date, '2026-07-22');
  assert.equal(r.lines.length, 2, '两把尺都要交，用户裁的是"但凡有一个报警都提示"');
  assert.match(r.lines[0], /占沪深300流通市值 0\.7%/);
  assert.match(r.lines[0], /净申赎 \+12\.5 亿份/);
  assert.match(r.lines[0], /分母 48 万亿元/);
  assert.match(r.lines[0], /清单 4 只/);
  const r2 = dayReadout(d, dates, dates.indexOf('2026-07-23'), 'ratio_float')!;
  assert.match(r2.lines[0], /占沪深300流通市值 —/, '缺数读 —，不补 0');
  // 10-31 分母换尺（快照求和 → 当日日线自算），这句词表跟着改口：断言强度不变 ——
  // 仍然要求屏幕上念出"为什么算不出"，而且不许念成机器码。
  assert.match(r2.lines[0], /那天 300 成分的日线整段没取到/);
  assert.match(missingText(['float_mv_yuan.no_implied']) ?? '', /反推不出流通股本/);
  assert.match(missingText(['float_mv_yuan.partial']) ?? '', /分母偏小/);
  assert.match(r2.lines[1], /这一天库里没有行/, '一把尺有、另一把没有 ⇒ 必须明说没有行，不是 0');
});

test('机器码不许上屏：已知码给句子，未知码原样交出来（词表没跟上要看得见）', () => {
  assert.match(missingText(['float_mv_yuan'])!, /占比算不出/);
  assert.match(missingText(['float_mv_yuan', 'size_yuan.partial'])!, /；/);
  assert.match(missingText(['brand_new_code'])!, /brand_new_code/);
  assert.equal(missingText(null), null);
  assert.equal(missingText([]), null);
  assert.ok(Object.keys(MISSING_TEXT).length >= 3);
});

test('异动菱形只钉在这一根轴上真有的那一天', () => {
  const view = ['2026-07-22', '2026-07-23', '2026-07-24'];
  const v = VIEW(RATIO_OPT, { anomalies: [{ date: '2026-07-23', kind: 'jump', value: 0.6,
    threshold: 0.05, text: '2026-07-23 占沪深300流通市值+0.051pp' },
  { date: '2025-01-01', kind: 'jump', value: 0.5, threshold: 0.05, text: '轴外的一天' }] });
  const m = anomalyIndex(view, v as never);
  assert.deepEqual([...m.keys()], [1], '轴外那一天不许悄悄挪到最近的点上');
  assert.equal(anomalyIndex(view, undefined).size, 0);
});

test('提示条那句话由后端判：没交判定就不猜"没有报警"', () => {
  assert.deepEqual(alertTitle(null), { fired: false, text: '接口还没交提示判定 —— 这一屏不猜有没有报警。' });
  assert.equal(alertTitle({ alert: { fired: true, text: '两把尺里至少有一条越界' } } as never).fired, true);
  assert.equal(alertTitle({ alert: { fired: false, text: '都没有越界' } } as never).text, '都没有越界');
});

test('缺数那些天的连接线：两端都有就连、某一头整段没有就水平延伸、一条都没有就不画', () => {
  const x = (i: number) => i * 10, y = (v: number) => 100 - v;
  const seg = (s: string) => s.split(" ").map(p => p.split(",").map(Number));
  // 中间断两场：连的是"最后一个有数"与"下一个有数"这两个真端点
  let out = bridgeSegments([1, 2, null, null, 5], x, y);
  assert.equal(out.length, 1, '一个断口一段连接');
  assert.deepEqual(seg(out[0]), [[10, 98], [40, 95]], '连的是两端那两个读数（不是把 null 当 0 落到底边）');
  // 左半整段没有（备用尺「占总市值」就是这个形状）：把最早那个读数水平延伸到窗口左边
  out = bridgeSegments([null, null, 3, 4], x, y);
  assert.equal(out.length, 1);
  assert.deepEqual(seg(out[0]), [[20, 97], [0, 97]], '水平延伸：值取第一个读数，另一头落在窗口边上');
  // 右半整段没有：对称地水平延伸最后一个读数
  out = bridgeSegments([3, 4, null, null], x, y);
  assert.deepEqual(seg(out[0]), [[10, 96], [30, 96]]);
  assert.deepEqual(bridgeSegments([null, null], x, y), [], '一条都没有就没有可连的两端，不许凭空画一条线');
  assert.deepEqual(bridgeSegments([], x, y), [], '空窗口就是空');
  assert.deepEqual(bridgeSegments([1, 2, 3], x, y), [], '都在就没有连接线（实线那条已经连着）');
  assert.deepEqual(bridgeSegments([1, NaN, 3], x, y).length, 1, 'NaN 按缺数处理（同一条规则）');
});

test('一屏画几场按「这一档的场数 ∩ 绘图区宽度」定，档按钮真的换得了画面', () => {
  assert.equal(DEFAULT_SERIES, 'ratio_float', '用户点名的那一条就是"占沪深300流通市值"');
  // 旧版钉的是 VIEW_LEN 常数（写死 130）⇒ 点「全部」与点「近 3 个月」画出来是同一根线。
  // 现在钉的是规则：档里有多少场就画多少场，只有密到每场不足 MIN_PX_PER_SESSION 像素才截。
  // 下限像素在 11-01 从 1.5 降到 0.5：用户读到的「历史数据只到 2026-04-14，还欠早」不是库里短，
  // 是这一屏只画得下 269 场 —— 整段画得下 比 每场几像素重要（糊成带的那一层由页面撤掉并当场说破）。
  assert.ok(MIN_PX_PER_SESSION > 0 && MIN_PX_PER_SESSION <= 2, '每场的下限像素要低到"整段历史一屏画得完"');
  assert.ok(VIEW_MIN >= 40, '再窄也要给一个能看出形状的底');
  assert.equal(viewLen(0, 800), 0, '没有场次就是 0，不许兜一个默认窗口');
  assert.equal(viewLen(66, 800), 66, '近 3 个月这一档一共 66 场：全画，一个都不落');
  assert.equal(viewLen(240, 800), 240, '近 1 年 240 场，800 像素画得开就整档画完');
  assert.equal(viewLen(680, 404), 680,
    '全部那一档（库里实测 680 场）在最窄的列（绘图区实测 404 像素）也要整段画完 —— 这条就是用户那句"还欠早"');
  assert.equal(viewLen(1330, 404), Math.floor(404 / MIN_PX_PER_SESSION),
    '真多到一屏装不下了才截，剩下的靠滑块平移');
  assert.equal(viewLen(50, 800), 50, '不足底的时候按实际场数，不编出没采到的那几场');
  assert.equal(viewLen(679, 0), VIEW_MIN, '宽度还没量到（0）时用底撑着，不许算出 0 场把图关掉');
  // 实测：这一列窄的时候绘图区只有 404 像素。按旧的 3 像素/场，这里只能画 134 场 ⇒
  // 点「近 1 年」(240 场) 与点「近 6 个月」(130 场) 画面一样大 —— 就是用户读到的"档按钮没接"。
  assert.equal(viewLen(130, 404), 130, '近 6 个月在 404 像素下整档画完');
  assert.equal(viewLen(240, 404), 240, '近 1 年也要在 404 像素下整档画完（这一档按钮点下去画面必须变）');
  assert.equal(viewLen(483, 404), 483, '近 2 年那一档在这个宽度下也整档画完');
  assert.equal(DEFAULT_RANGE, 'all', '首屏默认给整段（用户 11-01：至少 2 年）');
  assert.ok(RAW_MIN_STEP >= MIN_PX_PER_SESSION, '逐日淡线的门槛不能低于每场像素下限，否则那一层永远画不出来');
  assert.ok(RAW_MIN_STEP <= 2.2, '淡线要比小点（页面里 2.2 像素那个门槛）更晚撤：小点糊得更早');
});

test('提示那一排：一格一行、五样齐摆（指标名／时间／数值／与阈值比较／结果含义），措辞全来自后端', () => {
  const ratioItem = {
    date: '2026-10-29', family: 'ratio', family_label: '占比单日跳变', alert_type: '异动提醒',
    head: '2026-10-29 占比单日跳变——2 个读数说的是同一件事：',
    body: '四大·占沪深300流通市值 -0.109pp（≥ 这一条 |变动| p95＝0.043pp），全部… -0.104pp（…）',
    tail: '｜阈值样本：678 个样本（679 个读数日），日期 2024-01-02 ~ 2026-10-29（跨 1031 天 ≈ 33.9 个月）',
    text: '整句',
    parts: [
      { scale_key: 'big4', scale_short: '四大', series: 'ratio_float', series_label: '占沪深300流通市值',
        value: 0.9, delta: -0.109, threshold: 0.043,
        metric: '四大·占流通市值', value_text: '-0.109pp（当日 0.6821%）',
        compare_text: '|-0.109pp| ≥ 这一条 |变动| 的 p95＝0.043pp（超 2.5 倍）',
        alert_type: '异动提醒', meaning_text: '这一天占比相对上一场单日跳变，越过了这一条自身历史的 |变动| p95',
        phrase: '四大·占沪深300流通市值 -0.109pp（≥ 这一条 |变动| p95＝0.043pp）' },
      { scale_key: 'all', scale_short: '全部被动沪深300 ETF', series: 'ratio_total', series_label: '占沪深300总市值',
        value: 1.1, delta: -0.104, threshold: 0.054,
        metric: '全部被动沪深300 ETF·占总市值', value_text: '-0.104pp（当日 0.7%）',
        compare_text: '|-0.104pp| ≥ 这一条 |变动| 的 p95＝0.054pp（超 1.9 倍）',
        alert_type: '异动提醒', meaning_text: '这一天占比相对上一场单日跳变，越过了这一条自身历史的 |变动| p95',
        phrase: '全部被动沪深300 ETF·占沪深300总市值 -0.104pp（≥ 这一条 |变动| p95＝0.054pp）' },
    ],
  };
  const bandItem = { date: null, family_label: '当期档', kind: 'band', parts: [], text: '当期档：…' };
  const c = alertCells(ratioItem as never);
  assert.equal(c.date, '2026-10-29', '时间＝这一行第一格');
  assert.equal(c.family, '占比单日跳变', '动作那一格念后端 family_label，页面不另起中文名');
  assert.equal(c.type, '异动提醒', '类型（异动提醒／当期档提醒）也只认后端那一份');
  assert.equal(c.cells.length, 2, '两把尺的两个读数都要摆出来，不许只摆"最大的那一个"');
  assert.deepEqual(c.cells.map(x => x.metric), ['四大·占流通市值', '全部被动沪深300 ETF·占总市值'],
    '指标名那一格用后端 `metric`（短名 `compact` 只住 `SERIES`），页面不截断、不重拼');
  assert.deepEqual(c.cells.map(x => x.valueText), ['-0.109pp（当日 0.6821%）', '-0.104pp（当日 0.7%）'],
    '数值那一格带单位与"这是什么数"');
  assert.match(c.cells[0].compareText, /≥ 这一条 \|变动\| 的 p95＝0\.043pp（超 2\.5 倍）/,
    '与阈值的比较那一格：方向＋阈值＋倍数，全部后端算好');
  assert.match(c.cells[0].meaningText, /^异动提醒：这一天占比相对上一场单日跳变/,
    '含义那一格＝类型接那句解释（lib 只接这两个后端串，不自己造句）');
  assert.equal(c.incomplete, false, '五格齐了就别说"没交齐"');
  // 同一件事的几条读数，含义往往是那一句 ⇒ 收成一份（四行复读同一句＝表格版那 800 像素黄字）
  assert.equal(c.sharedMeaning, c.cells[0].meaningText, '两句一样就该合并');
  const differ = alertCells({
    date: '2026-10-29', alert_type: '异动提醒',
    parts: [{ metric: '四大·净申赎', value_text: '+3 亿份', compare_text: '≥ p95 2 亿份',
               alert_type: '异动提醒', meaning_text: '异动提醒：进' },
            { metric: '全部·净申赎', value_text: '+9 亿份', compare_text: '≥ p95 2 亿份',
              alert_type: '异动提醒', meaning_text: '异动提醒：出的也有' }],
  } as never);
  assert.equal(differ.sharedMeaning, '', '两句不一样就不许合并（合了就是把另一句藏起来）');

  // 后端还没交五格（旧版接口）时：退回短版整句，并把"缺了哪一样"标出来（incomplete）——
  // 宁可屏上出现那句兜底声明，也不许页面自己拼一个看起来像的读数
  const legacy = alertCells({
    date: '2026-10-29', parts: [{ phrase: '四大·占沪深300流通市值 -0.109pp（≥ 这一条 |变动| p95＝0.043pp）',
                                  phrase_short: '四大·占流通市值 -0.109pp（≥p95 0.043pp）' }],
  } as never);
  assert.equal(legacy.cells[0].phraseShort, '四大·占流通市值 -0.109pp（≥p95 0.043pp）',
    '短版优先、没短版才退回整句（缩不缩由后端定）');
  assert.equal(legacy.incomplete, true, '五格没交齐就要如实标出来，不许静默拿短句充当含义');
  assert.doesNotMatch(PANEL, /\bcompact\b/,
    '短名那一份描述符只住后端 `SERIES[].compact`：页面读它就等于自己开始造句（改列名时屏幕会悄悄念错）');
  assert.equal(alertCells(bandItem as never).date, NULL_TEXT, '当期档不对应某一天：是 —，不是空串');
  assert.deepEqual(alertCells(bandItem as never).cells, [], '没有 parts 就是没有，不许拿整句拆出来的东西顶上');

  // 阈值样本那句：每个事件都带同一串 ⇒ 收成一份；不带 tail 的（当期档）不许混进来
  assert.deepEqual(alertSampleNotes([ratioItem as never, bandItem as never, ratioItem as never]),
    [ratioItem.tail.replace(/^｜阈值样本：/, '')]);  assert.doesNotMatch(alertSampleNotes([ratioItem as never])[0], /^｜/, '前缀那个竖线是句子内部的分隔符，摆成独立一行就不该再留');
  assert.deepEqual(alertSampleNotes([bandItem as never]), []);
  assert.deepEqual(alertSampleNotes([]), []);
});

// ------------------------------------------------ 调用点与纪律（源码级）

/** 注释与 docstring 里写"不许把 p95 抄上屏"是**讲规矩**，不是违反规矩：
 *  这类纪律断言只看代码本体，先把注释剥掉（否则第一句解释就会把自己的检查打红）。 */
const stripComments = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

test('页面不写死阈值、不抄中文档名、不把 /api 写进探针路径', () => {
  const code = stripComments(PANEL);
  assert.match(PANEL, /useProbe<NtPanel>\("\/market\/national-team"/,
    '页面绝不写 /api（probe 自补）');
  assert.doesNotMatch(code, /0\.02|p95|MIN_SAMPLES/,
    '阈值与样本底只住后端一份，页面写了就是第二份真相源');
  assert.doesNotMatch(code, /"超低"|"偏高"|"中性"|"偏低"|"超高"/,
    '五档档名只从后端 levels/序列描述符来，页面不抄一份中文');
  assert.match(PANEL, /d\?\.range_options \?\? \[\]/, '时间档按钮只认后端交的那五档');
  assert.match(PANEL, /opts\.map\(o =>/, '图例与切换按钮的中文名只从 series_options 读');
});

test('控件那一行永远在（空态也要能换时间档与重新取数）', () => {
  assert.match(PANEL, /<Panel<NtPanel>[\s\S]{0,4000}controls=\{\(/);
  assert.match(PANEL, /rangeOpts\.map/);
  assert.match(PANEL, /<Contract/, '接口没接上时屏上至少留下这一屏该有哪些字段');
  assert.match(PANEL, /<CaliberFold/, '口径那几句收进共用折叠，缺数与告警那一族留在外面');
});

test('图上的线按 null 断段、点与拖都落在轴上、日期刻度在场', () => {
  assert.match(PANEL, /function segs\([\s\S]*?if \(v === null\)[\s\S]*?cur = \[\];/);
  assert.match(PANEL, /type="range"[\s\S]*?aria-label="窗口起点/);
  assert.match(PANEL, /onPointerDown[\s\S]*?onPointerMove[\s\S]*?onPointerUp/);
  assert.match(PANEL, /ticks\.map/, '横轴必须有日期刻度（悬停不算交付）');
  assert.match(PANEL, /reading\?\.date/, '点一个点要把完整日期钉在屏上');
  assert.match(PANEL, /回到最新一段/);
});

test('挂在复盘报告顶部，且不被"这天有没有生成过报告"牵着消失', () => {
  assert.match(PAGE, /import \{ NationalTeamPanel \} from "@\/components\/NationalTeamPanel"/);
  const at = PAGE.indexOf('<NationalTeamPanel />');
  assert.ok(at > 0, '挂载点不见了');
  const gate = PAGE.indexOf('{!data && (!running || jobDate !== date)');
  assert.ok(at < gate, '这一块读的是库，必须排在"还没有报告"那一段之前');
  const dataGate = PAGE.indexOf('{data && <>');
  assert.ok(at < dataGate, '不许塞进报告体内：那天没报告时这一屏照样应该有数');
});

test('屏幕上不许出现裸星号：交出去的句子先过 plain()', () => {  // 后端原文里可能有成对粗体标记，页面渲染前统一过 plain 那一刀
  assert.match(PANEL, /plain\(alert0\.text\)/);
  // 提示那一排现在是"一行一个读数、五格齐摆"（用户 11-01 点名：指标名／时间／数值／与阈值比较／结果含义），
  // 五格每一格都是后端交出的散文 ⇒ 钉子跟着搬到新的真实位置：渲染前一律过 plain。
  for (const f of ['cell.metric', 'cell.valueText', 'cell.compareText',
                    'c.sharedMeaning || cell.meaningText']) {
    assert.match(PANEL, new RegExp('plain\\(' + f.replace('.', '\\.') + '\\)'),
      `${f} 这一格没过 plain（裸星号会上屏）`);
  }
  assert.match(PANEL, /plain\(cell\.phraseShort\)/, '兜底那句短版也要过 plain');
  assert.match(PANEL, /plain\(l\)/);
  // 判读层自己交出去的句子（去掉注释与 docstring 之后的代码）里不许有裸星号：
  // 屏上念出来就是脏字（10-27 那次 sentimentCompare 的红就是这么抓出来的）
  assert.doesNotMatch(stripComments(READ), /\*\*/, '判读层的字符串里不留裸星号（屏上念出来就是脏字）');
});

// ───────────────────────── 2026-10-31 用户点名：「图表做的连贯一点，然后明确一些转折点」
// 连贯这一半是分母换尺（写侧 nt-2）的事，这一层只管：均线画不画、转折点落在哪一天、判不了怎么说。
const trendStub = (o) => ({ smooth: 10, th: 0.15, unit: '%', samples: 679,
  available: true, ma: [], rows: [], ...o });

test('转折点按日期回到轴上；找不到那一期的要报 unmatched，不悄悄丢', () => {
  const ds = ['2026-01-05', '2026-01-06', '2026-01-07'];
  const sv = { trend: trendStub({ rows: [
    { date: '2026-01-06', kind: 'peak', value: 2.1, text: '均线顶 2026-01-06' },
    { date: '2025-12-01', kind: 'trough', value: 0.4, text: '均线底 2025-12-01' }] }) };
  const got = turnMarks(ds, sv as never);
  assert.equal(got.marks.length, 1, '轴上没有的那一天不画标记');
  assert.equal(got.unmatched, 1, '但要说"另有 1 个在画出来的这一段之外"，否则点数对不上');
  assert.equal(got.marks[0].i, 1);
  assert.deepEqual(turnMarks([], undefined).marks, []);
});

test('趋势那句全部念后端原话：判不了念 reason、没拐点念 empty_note，页面不自己补一个数', () => {
  assert.match(trendNote({ trend: trendStub({ available: false,
    reason: '这一条只有 47 场有读数，不满 60 场 ⇒ 均线与转折点都不画' }) } as never),
  /不满 60 场/);
  const head = trendStub({ th_note: '均线近 10 场；整段 679 场里共确认 4 个，本窗口内 2 个',
    rows: [{ date: '2026-01-06', kind: 'peak', value: 2.1, text: '均线顶 2026-01-06' }] });
  assert.equal(trendNote({ trend: head } as never),
    '均线近 10 场；整段 679 场里共确认 4 个，本窗口内 2 个。点图上的标记或下面清单里的一行，就把那一天钉住。');
  assert.match(trendNote({ trend: { ...head, rows: [],
    empty_note: '这一段没有已确认的转折点（末端那个还没被反向走够）' } } as never),
  /末端那个还没被反向走够/);
  // 后端整块没交 ⇒ 明说"没交"，不退回去自己画一条均线
  assert.match(trendNote({} as never), /没交趋势块/);
});

test('均线画成第二条粗线、null 断段；量程把均线一起算进去（不然窗口左边缘那截被裁掉）', () => {
  assert.match(PANEL, /function segsVals\(/, '均线走自己那一份"按 null 断段"的画法，不复用逐日那份');
  assert.match(PANEL, /const maView = maAll\.slice\(from, from \+ len\)/,
    '均线按整段算好、只切出画出来的这一段（换时间档不许让同一个顶换位置）');
  assert.match(PANEL, /yDomain\(pts, spec, s, maView\)/, '量程必须含均线值');
  assert.match(PANEL, /strokeWidth=\{1\.1\} opacity=\{0\.62\}/, '逐日线画淡：方向由粗均线说');
  assert.match(PANEL, /strokeWidth=\{2\.6\}/, '均线是粗的那一条');
  assert.match(PANEL, /const peak = turn\.kind === "peak"/, '顶/底的分别只判一次');
  assert.match(PANEL, /\{peak \? "▼" : "▲"\}/, '顶▼底▲（与「风险提示」同一套形状）');
  assert.match(PANEL, /onClick=\{\(\) => jumpTo\(turn\.date\)\}/, '标记可点：点了钉住那一天');
  // 页面一个均线窗口/门槛数字都不写死：smooth 与 th 全读 trend
  assert.doesNotMatch(PANEL, /smooth:\s*\d+|\bth:\s*[\d.]+/, '均线窗口与门槛不许在页面里出现第二份');
});

test('判不了趋势那一句留在屏上（缺数类），读法那句进抽屉；两半不许互换', () => {
  assert.match(PANEL, /border-warning\/50 bg-warning\/10[\s\S]{0,240}这一条的趋势判不了/,
    '那一行必须是警示色、摊在屏上（措辞是告警、配色是普通小字，是这个仓库反复出过的病）');
  assert.match(PANEL, /所以图上这一条没有粗均线，也没有顶\/底标记/,
    '要把"屏上因此少画了什么"说破（只有"判不了"三个字，读者不知道自己错过哪一块）');
  // "判不了 ≠ 走得平"那一句由后端 `trend.reason` 原话交，页面不许再复述一遍（复述的那份会跟后端分叉）
  assert.doesNotMatch(PANEL, /这不是「这一段走得平」/, '同一句道理全站只在一处说：后端判据那句，或页面这句，二选一');
  // 读法（均线几个场、门槛多少、整段确认了几个）在抽屉那一份 notes 里 —— 屏上只留图与数
  const notes = PANEL.slice(PANEL.indexOf('notes={['), PANEL.indexOf('notes={[') + 3000);
  assert.match(notes, /的趋势：\$\{trendNote/, '抽屉里有那一句（正文不再摊一遍，两处各写一份迟早分叉）');
  assert.equal((PANEL.match(/这一条的趋势判不了/g) ?? []).length, 1,
    '判不了那一句全站只出现在屏上那一个分支里（不许在抽屉里再摊一份变成两处口径）');
});


test('收起那一行摘要自己就把话说全（收缩不许把"有 40 个事件"缩成"没事"）', () => {
  const fired = {
    alert: {
      fired: true, count_all: 40, raw_entries: 84, truncated: 0, text: '整句框架句',
      items: [{
        date: '2026-10-29', family_label: '占比单日跳变', kind: 'jump',
        parts: [
          { scale_short: '四大', series_label: '占沪深300流通市值', value: 0.68, delta: -0.09,
            threshold: 0.042, phrase: '四大·占沪深300流通市值 -0.090pp（≥ 这一条 |变动| p95＝0.042pp）',
            phrase_short: '四大·占流通市值 -0.090pp（≥p95 0.042pp）' },
          { scale_short: '全部被动沪深300 ETF', series_label: '占沪深300总市值', value: 0.7, delta: -0.1,
            threshold: 0.054, phrase_short: '全部被动沪深300 ETF·占总市值 -0.104pp（≥p95 0.054pp）' },
        ],
      }],
    },
  };
  const h = alertHeadline(fired as never);
  assert.match(h, /40 个事件/, '事件数要在摘要里（按 count_all，不是按交出的条数）');
  assert.match(h, /84/, '原始条目数也要在——"40 个事件、84 条读数"是两件事');
  assert.match(h, /2026-10-29 占比单日跳变/, '最近一次哪天、什么动作');
  assert.match(h, /四大·占流通市值 -0\.090pp/, '第一个读数用后端短版，页面不重拼');
  assert.match(h, /点开看/, '要说清这是一行收起态，点得开');
  assert.doesNotMatch(h, /没有越界|一切正常/, '有事件的时候不许出现安抚话');

  // 没交判定 / 一条都没有 / 判不了：那三种都只有一行，原样交出去（不折、不猜）
  assert.match(alertHeadline(null), /还没交提示判定/);
  assert.equal(alertHeadline({ alert: { fired: false, text: '这一段里一个越界都没有' , items: [] } } as never),
    '这一段里一个越界都没有');
  assert.equal(alertHeadline({ alert: { fired: true, text: '框架句', count_all: 3, items: [] } } as never),
    '框架句', 'fired 却交不出逐条时也念后端那句，页面不自己编一条摘要');
});

test('图不被宽度顶扁、提示那一排在图下方、标记带日期（10-31「效果不佳」三条真因）', () => {
  // ① 旧版 viewBox 写死 780 + width=100%：容器一窄整张图按比例缩，两把尺各剩 76 像素高。
  assert.match(PANEL, /const \[cw, setCw\] = useState\(W\)/, '绘图宽度是现量的');
  assert.match(PANEL, /new ResizeObserver\(measure\)/, '容器宽度变化要重测（不是只在挂载时量一次）');
  assert.match(PANEL, /viewBox=\{`0 0 \$\{cw\} \$\{H\}`\} width="100%" height=\{H\}/,
    '1 单位＝1 像素：高度由 PH 定死，不再被宽度顶');
  assert.doesNotMatch(PANEL, /viewBox=\{`0 0 \$\{W\} \$\{H\}`\}/, '写死 W 的那一版不许回来');
  assert.match(PANEL, /const len = viewLen\(total, cw - PADX - PADR\)/,
    '一屏画几场跟档与宽度走；写死 VIEW_LEN 那一版五个档按钮画出来是同一根线');
  // ② 转折点：符号 + stem + 日期，缺一样就读不出"哪天拐的"
  assert.match(PANEL, /strokeDasharray="2 2"/, '从均线那一天拉到顶/底边的虚线 stem');
  assert.match(PANEL, /\{turn\.date\}\s*<\/text>/, '日期写在标记旁边（不是只有悬停 title 里才有）');
  // ②-2 太密时撤掉的那一层画必须当场说破（11-01「全部」一屏 680 场 ⇒ 一场约 0.5 像素）
  assert.match(PANEL, /const showRaw = step >= RAW_MIN_STEP/, '逐日淡线有门槛：太密就不画');
  assert.match(PANEL, /DEFAULT_RANGE\)/, '首屏默认档从 lib 读，页面不许自己抄一份 "1y"');
  assert.match(PANEL, /\{showRaw && segs\(pts, spec, x, y\)\.map/, '门槛不过时那一层根本不进渲染');
  assert.match(PANEL, /\{!showRaw && `｜这一屏一场不足/, '撤掉的那一层要在屏上说破（不能让读者自己猜"线怎么不见了"）');
  assert.match(PANEL, /这一档共 \{total\} 场/, '画了几场／这一档一共几场要并报（"只画了一段"不许读成"只有这一段"）');
  // ③ 提示那一排：在图的 </svg> 之后，一行一个读数、五格齐摆，复读那句收成一份
  const svgAt = PANEL.indexOf('</svg>');
  const alertAt = PANEL.indexOf('<th className="py-0.5 text-left font-normal">结果含义</th>');
  assert.ok(svgAt > 0 && alertAt > svgAt, '图先说话：提示那一排挪到图下方（旧版 800 像素黄字压在第一屏）');
  assert.match(PANEL, /alertCells\(it\)/, '每一行的五格由判读层那份拆好（页面不自己算阈值）');
  for (const th of ['时间', '指标名', '数值', '与阈值的比较', '结果含义']) {
    assert.ok(PANEL.includes(`>${th}</th>`), `表头少了「${th}」这一格（用户 11-01 点名的五样，少一样就读不清）`);
  }
  assert.match(PANEL, /rowSpan=\{parts\.length\}/, '日期那一格跨这个事件的几行（同一件事不念四遍日期）');
  assert.match(PANEL, /rowSpan=\{c\.sharedMeaning \? parts\.length : 1\}/,
    '同一件事共用的那句"含义"跨行合并（四行复读一句＝表格版那 800 像素黄字）');
  assert.match(PANEL, /!\(k > 0 && c\.sharedMeaning\) &&/, '合并之后其余几行不再重复渲染那一格');
  assert.match(PANEL, /格子缺了后端该交的那五样之一/, '五格没交齐要在屏上出声，不许静默充当短句');
  assert.match(PANEL, /const sampleNotes = alertSampleNotes\(alertItems\)/,
    '阈值样本那句整屏一份；逐条复读八遍＝那 800 像素的来源');
  // 11-01 用户点名「以下内容收缩起来」：整排默认收成一行（仍不进抽屉、仍有才显示）
  assert.match(PANEL, /<summary className="flex cursor-pointer items-start gap-1\.5">[\s\S]{0,220}plain\(alertHeadline\(d\)\)/,
    '收起时那一行摘要走 alertHeadline（句子里必须带事件数与最近那一天，不是只写"点开看"）');
  assert.match(PANEL, /\{alertItems\.length === 0 \? \(/,
    '一条都没有／判不了那一句不折——本来就只有一行，折了要点两下才知道没数');
  assert.doesNotMatch(PANEL, /ALERT_VIEW_CAP|shownAlerts/,
    '外面已经折一层了，里面不许再套第二层折叠（两层收起没人找得到）');
  assert.match(PANEL, /max-h-72 overflow-y-auto/, '打开后是有界的上下滚窗口');
  // 11-01 用户点名「没有数据的日子，也用曲线连贯的连接起来」，并当场选了"只动显示、不动判据"那一条
  assert.match(PANEL, /bridgeSegments\(pts\.map\(p => valOf\(p, spec\)\), x, y\)/,
    '连接线走判读层那一个纯函数（页面不各画各的断口规则）');
  assert.match(PANEL, /strokeDasharray="4 4" opacity=\{0\.3\}/, '连接线必须是虚线＋淡（实线压在它上面）');
  assert.match(PANEL, /那是给你看形状的连接线，不是那些天的读数/,
    '「有几分之几」那一行要把虚线那段说破：连贯是显示，不是读数');
  assert.match(PANEL, /这一段是连接线（那些天没有分母，算不出占比），不是当天的读数/,
    '悬停虚线段也要能读到同一句（只有屏上那一行字的话，鼠标停在虚线上会以为是真值）');
  assert.doesNotMatch(PANEL.slice(PANEL.indexOf('本窗口 '), PANEL.indexOf('本窗口 ') + 700), /\*\*/,
    '那一段是裸 JSX 文字（不过 plain），不许混进成对星号');
  assert.doesNotMatch(PANEL, /\{plain\(it\.text\)\}/, '不再逐条把整句（含复读的阈值样本）摊在屏上');
});
