import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  BOARD_ORDER, coverageSentence, featureTags, groupReading, groupRows, numOrNull,
  pctText, sectorTags, stateSentence, truncationSentence, yiText, type TrendRow,
} from '../src/lib/trendStocks.ts';

/**
 * 「趋势股」判读层 + 组件接线。
 * 每条断言都对应一句"屏幕会说谎"的情形，不是格式洁癖。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const comp = fs.readFileSync(path.join(HERE, '../src/components/quant/TrendStockBoard.tsx'), 'utf8');
const page = fs.readFileSync(path.join(HERE, '../src/pages/TrendStocks.tsx'), 'utf8');
const helper = fs.readFileSync(path.join(HERE, '../src/lib/trendStocks.ts'), 'utf8');

const row = (o: Partial<TrendRow>): TrendRow => ({ security_code: '600000.SH', ...o });

test('三组按主板→创业板→科创板固定顺序，空组也留着', () => {
  const g = groupRows([row({ board: '科创板' }), row({ board: '主板', security_code: '600001.SH' })]);
  assert.deepEqual(g.map(x => x.board), BOARD_ORDER);
  assert.equal(g[0].rows.length, 1);
  assert.equal(g[1].rows.length, 0, '创业板当天没有 ⇒ 空数组，不是把这一组抹掉');
  assert.equal(g[2].rows.length, 1);
});

test('北交所与没归进三组的票都不出现在任何一组', () => {
  const g = groupRows([row({ board: '北交所' }), row({ board: '' }), row({ board: null })]);
  assert.equal(g.reduce((s, x) => s + x.rows.length, 0), 0);
});

test('涨幅读数：0 是有效值，只有没读到才给 —', () => {
  assert.equal(pctText(0), '0.00%', '平盘被吞成 — 就等于把"没涨没跌"说成"不知道"');
  assert.equal(pctText(6.24), '+6.24%');
  assert.equal(pctText(-1.5), '-1.50%');
  assert.equal(pctText(null), '—');
  assert.equal(pctText('6.24'), '—', '字符串不是读数 —— 不替它偷偷换算');
  assert.equal(pctText(Number.NaN), '—');
});

test('金额读数缺值给 —，不补 0（0 亿会被读成没人交易）', () => {
  assert.equal(yiText(null), '—');
  assert.equal(yiText(0), '0.00 亿', '真 0 是有效值');
  assert.equal(yiText(14.9436), '14.94 亿');
  assert.equal(numOrNull(undefined), null);
});

test('量价特征：五档标签都在，命中的才亮；近 N 日 0 板不写这一枚', () => {
  const tags = featureTags(row({ nh_10: true, nh_20: false, vol_gt_prev: null, vol_gt_vma5: true,
    limit_ups_in_window: 0 }), 5);
  assert.deepEqual(tags.map(t => t.label),
    ['10 日新高', '20 日新高', '较昨日放量', '较 5 日均量放量'], '0 板时不该凭空多出一枚标签');
  assert.deepEqual(tags.filter(t => t.on).map(t => t.label), ['10 日新高', '较 5 日均量放量'],
    'null（没读到）不许算成命中，也不该被抹掉不显');
  const withLu = featureTags(row({ limit_ups_in_window: 2 }), 10);
  assert.equal(withLu[withLu.length - 1].label, '近 10 日 2 板');
  assert.equal(withLu[withLu.length - 1].on, true);
  assert.equal(featureTags(row({ limit_ups_in_window: null }), 5).length, 4, '没读到次数就不写');
});

test('相关板块：后端交出几个显几个，一个都没有给 —', () => {
  assert.equal(sectorTags([]), '—');
  assert.equal(sectorTags(null), '—');
  assert.equal(sectorTags([{ name: '银行', pct: 1.2 }, { name: '', pct: 3 }, undefined]), '银行 +1.20%',
    '名字为空的行不显；页面不补到 3 个');
});

test('这一场读不读得动：没读到 / 退到最新一场 / 确实一只都不符合，三种话不能混', () => {
  const off = stateSentence({ available: false, reason: '库里连不上' });
  assert.equal(off.tone, 'warn');
  assert.match(off.text, /库里连不上/);
  assert.doesNotMatch(off.text, /确实一只都不符合/, '读不到不能说成"确实没有"');

  const back = stateSentence({ available: true, date: '2026-10-01', date_fell_back: true, latest_date: '2026-10-15' });
  assert.equal(back.tone, 'warn');
  assert.match(back.text, /2026-10-01/);
  assert.match(back.text, /2026-10-15/);

  const zero = stateSentence({ available: true, selected_total: 0 });
  assert.equal(zero.tone, 'ok');
  assert.match(zero.text, /确实一只都不符合/);

  assert.equal(stateSentence({ available: true, selected_total: 212 }).text, '', '正常有数不该多一句警告');
});

test('截断那句只在后端说截断时才说，且点名代表范围', () => {
  assert.equal(truncationSentence({ group_truncated: false }), '');
  assert.equal(truncationSentence({}), '', '后端没交这个键就不替它下结论');
  const s = truncationSentence({ group_truncated: true, rows_returned: 300, selected_total: 412, limit: 300 });
  assert.match(s, /交出 300 行 \/ 这一场实际入选 412 只/);
  assert.match(s, /不代表全场/);
});

test('母体那句只念后端交出的数', () => {
  const s = coverageSentence({ universe_rows: 5214, selected_total: 212, lookback: 5,
    min_bars: 9, short_bars_rows: 37, no_mv_rows: 4 });
  assert.match(s, /母体 5214 行/);
  assert.match(s, /北交所不纳入/);
  assert.match(s, /近 5 个交易日/);
  assert.match(s, /9 根收盘价/);
  assert.match(s, /37 行往前凑不满/);
  assert.match(s, /4 行没有流通市值/);
  assert.match(s, /实际入选 212 只/);
  assert.doesNotMatch(coverageSentence({ universe_rows: 10, selected_total: 0, short_bars_rows: 0, no_mv_rows: 0 }),
    /凑不满|没有流通市值/, '0 只的档不该硬说一句"有 N 行不合格"');
});

test('整组读数只读 summary，没交就说没说', () => {
  const r = groupReading({ n: 212, scope: '这一场全部入选的 212 只', median_pct: -0.13,
    cnt_nh_10: 25, cnt_vol_gt_vma5: 171 });
  assert.equal(r.tone, 'ok');
  assert.match(r.text, /212 只/);
  assert.match(r.text, /-0.13%/);
  assert.match(r.text, /25 只/);
  assert.match(r.text, /171 只/);
  assert.match(r.text, /不说明接下来会怎样/);
  assert.equal(groupReading(null).tone, 'warn');
  assert.match(groupReading({ n: 0 }).text, /n=0/);
});

/* ── 组件接线：判读块真的被调用，参数真的发给后端 ── */

test('组件调的是判读层那一份，不自己重写读数', () => {
  assert.match(comp, /from "@\/lib\/trendStocks"/);
  for (const fn of ['groupRows', 'featureTags', 'pctText', 'yiText', 'sectorTags',
    'stateSentence', 'truncationSentence', 'coverageSentence', 'groupReading']) {
    assert.match(comp, new RegExp(fn), `组件没用到 ${fn}`);
  }
  // 页面不许自己按行数组组数（那是"看着像全场、其实是前 300 行"的那类假数）
  assert.doesNotMatch(comp, /rows\.filter\(r => r\.board/, '组内只数应读后端交出的 group_counts，不是页面自己数');
});

test('周期与上限都作为参数发出去，切换时清掉交叉高亮', () => {
  assert.match(comp, /useProbe<TrendPayload>\("\/quant\/trend-stocks", nonce, \{ lookback, limit \}\)/);
  assert.match(comp, /setLookback\(n\); ts\.clearAll\(\)/);
  assert.match(comp, /setLimit\(Number\(e\.target\.value\)\); ts\.clearAll\(\)/);
});

test('接口路径不带 /api 前缀（useProbe 自己补），页面也不写两遍', () => {
  assert.doesNotMatch(comp, /"\/api\/quant/, '写成 /api/... 会被拼成 /api/api/...');
  assert.doesNotMatch(page, /\/api\//);
});

test('弹窗被拦那句告警不折进默认收起的说明框', () => {
  const i = comp.indexOf('ts.popHint &&');
  const j = comp.indexOf('<ExplainBox');
  assert.ok(i > 0, '组件要显式念一次 popHint');
  assert.ok(j > i, '告警必须在说明框之前（收着等于没说）');
});

test('空组保留标题并写「当日暂无符合条件的趋势股」', () => {
  assert.match(comp, /当日暂无符合条件的趋势股/);
  assert.match(comp, /一只都没有 —— 后端把这一组的标题留着并交 0/);
});

test('与板块卡片那份名单的差别在屏上说清（固定 5 天含涨停 vs 可选 5/10 排除涨停）', () => {
  assert.match(comp, /固定看 5 天[\s\S]*可以包含当天涨停/);
  assert.match(comp, /排除当天涨停/);
  assert.match(helper, /不是一个问题的答案|不代表全场/);
});

test('口径原话与字段解释都读接口，页面不另写一份公式', () => {
  assert.match(comp, /取数口径（后端原句，页面未改写）/);
  assert.match(comp, /后端 definitions 里没有/);
  assert.match(page, /判据、落库、每日加工段都不在这一层/);
});

test('AI 那一份登记在组件顶层，没数时登记 null', () => {
  assert.match(comp, /usePageAskAi\(askRows\.length \? \{/);
  assert.match(comp, /const cur = state\.kind === "ok" \? state\.data : null/);
  // render-prop 里调 hook 会按数据有无改变 hook 调用次数 —— 那条红线钉在这儿
  const body = comp.slice(comp.indexOf('{d => {'), comp.indexOf('</Panel>'));
  assert.doesNotMatch(body, /usePageAskAi\(/);
});

test('10-19 夜「描述收缩」自查：怎么点与取数口径折进说明框，覆盖/截断/caveat 留在外面', () => {
  // 用户 2026-10-19：「把选项留出，描述内容都收缩起来」。上面那条闸（popHint 必须排在
  // 第一个 <ExplainBox 之前）继续生效：本轮把"怎么点"与后端 caliber 收进框，二者都排在告警之后。
  const boxStart = comp.indexOf('<ExplainBox title="这一张表怎么点');
  const boxEnd = comp.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '趋势股榜要有默认收起的"怎么点"说明框');
  const box = comp.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/);
  assert.match(box, /取数口径（后端原句，页面未改写）/);
  assert.doesNotMatch(box, /popHint|text-warning|text-destructive|<Caveats/);
  const before = comp.slice(0, boxStart);
  assert.match(before, /coverageSentence\(d\)/, '覆盖那句读数渲染在框之前＝默认可见');
  assert.match(before, /留空不补 0|不补 0|判据版本/, '缺数与版本声明不许跟着搬进框');
  assert.match(before, /<CaliberFold caveats=\{d\.caveats \?\? \[\]\} definitions=\{d\.definitions\}/,
    'caveat 与字段义走共用折叠块，且排在说明框之前＝缺数类仍在屏上');
  assert.ok(comp.indexOf('ts.popHint') < boxStart, '告警在这块说明框之前（第一道闸的加强版）');
});
