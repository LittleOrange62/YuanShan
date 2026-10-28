import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  bandText, boardSpanText, devCell, forwardHeadline, hotHeadline, moveStatusText,
  nextDayState, pctText, ptsText, priceText, recentHeadline, severeState, truncationNote,
  type AbnormalMove, type AbnForwardRow, type AbnHotRow, type AbnRecentRow,
} from '../src/lib/abnormalMove.ts';

/**
 * 异动提醒的判读层 + 组件接线。
 * 每条断言都对应一句"屏幕会说谎"的情形，不是格式洁癖。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const comp = fs.readFileSync(path.join(HERE, '../src/components/home/AbnormalMoveBoard.tsx'), 'utf8');
const lib = fs.readFileSync(path.join(HERE, '../src/lib/abnormalMove.ts'), 'utf8');
const mount = fs.readFileSync(path.join(HERE, '../src/components/home/MarketPanorama.tsx'), 'utf8');
const system = fs.readFileSync(path.join(HERE, '../src/pages/System.tsx'), 'utf8');

const hot = (o: Partial<AbnHotRow>): AbnHotRow => ({ security_code: '600000.SH', ...o });
const fwd = (o: Partial<AbnForwardRow>): AbnForwardRow => ({ security_code: '600000.SH', ...o });
const rec = (o: Partial<AbnRecentRow>): AbnRecentRow => ({ security_code: '600000.SH', ...o });

test('缺数是「—／判不了」，不是 0', () => {
  assert.equal(pctText(null), '—');
  assert.equal(pctText(undefined), '—');
  assert.equal(pctText(Number.NaN), '—');
  assert.equal(pctText(0), '0.00%');          // 真平盘才写 0.00%
  assert.equal(pctText(7.5223), '+7.52%');
  assert.equal(pctText(-2.1481), '-2.15%');
  assert.equal(ptsText(null), '判不了');       // 缺数与 0 点是两件事
  assert.equal(ptsText(0), '0.0 点');
  assert.equal(ptsText(55.703), '+55.7 点');
  assert.equal(priceText(null), '—');
  assert.equal(priceText(25.58), '25.58');
});

test('档位取不到说明的是判不了，不是一整天不涨不跌', () => {
  assert.match(bandText(fwd({ band_pct: null })), /取不到/);
  assert.equal(bandText(fwd({ band_pct: 10 })), '10%');
});

test('severe 的 null 绝不并进「没踩到」', () => {
  assert.equal(severeState(hot({ severe: true })).label, '已踩到');
  assert.equal(severeState(hot({ severe: false })).label, '没踩到');
  const unk = severeState(hot({ severe: null, severe_reason: 'bars_lt_window' }));
  assert.equal(unk.tone, 'warn');
  assert.match(unk.label, /判不了/);
  assert.match(unk.label, /bars_lt_window/);   // 为什么判不了要一起交出来
  assert.notEqual(unk.label, '没踩到');
});

test('异动状态那一格念的是后端交的中文条款', () => {
  const r = hot({ severe: true, severe_how: 'dev10, times', severe_how_label: '10 日累计偏离过 ±100/−50、10 日内同向异常波动凑够次数', severe_dir: 'up', severe_dir_label: '涨向' });
  assert.equal(moveStatusText(r), '涨向 · 10 日累计偏离过 ±100/−50、10 日内同向异常波动凑够次数');
  assert.match(moveStatusText(hot({ severe: null, severe_reason: 'no_index_pct' })), /判不了/);
  // 后端交了整格文本时以它为准：那一列的 sortKey 就是它，排序才与屏幕上读到的一致
  assert.equal(moveStatusText(hot({ severe: true, status_label: '跌向 · 10 日内同向异常波动凑够次数',
                                    severe_how_label: '只言半句', severe_dir_label: '涨向' })),
    '跌向 · 10 日内同向异常波动凑够次数');
});

test('次日那一格：停牌、缺席、还没到、有涨幅 四种分开说', () => {
  assert.equal(nextDayState(rec({ next_pct: -3.2, next_trade_date: '2026-10-16' })).text, '-3.20%');
  assert.equal(nextDayState(rec({ next_halted: true, next_trade_date: '2026-10-16' })).text, '次日停牌（有证据的缺席）');
  assert.match(nextDayState(rec({ next_missing: true, next_trade_date: '2026-10-16' })).text, /缺席/);
  // 「还没到」绝不能读成停牌：next_trade_date 是 null
  const waiting = nextDayState(rec({ next_trade_date: null }));
  assert.equal(waiting.text, '次日还没到');
  assert.equal(waiting.tone, 'muted');
  assert.doesNotMatch(waiting.text, /停牌/);
});

test('几天几板：整批没读到 / 单只不在轮里 / 有轮，三种说得开', () => {
  assert.equal(boardSpanText(hot({})), '没读到');
  assert.equal(boardSpanText(hot({ board_span: null })), '没读到');
  assert.equal(boardSpanText(hot({ board_span: { days: 5, boards: 3, label: '5天3板' } })), '5天3板');
  assert.equal(boardSpanText(hot({ board_span: { days: 3, boards: 2 } })), '3天2板');
  // 这一只没在轮里，但同批别人有 → 说"不在当前涨停轮"，不说成读库失败
  assert.equal(boardSpanText(hot({ board_span: { days: null, boards: null, batch_empty: false } })),
    '不在当前涨停轮');
  // 整批都空 → 分不清"都没板"与"没读到库"，两句都要说出来
  assert.match(boardSpanText(hot({ board_span: { batch_empty: true } })), /没读到/);
  assert.doesNotMatch(boardSpanText(hot({ board_span: { batch_empty: true } })), /^0天0板$/);
});

test('窗口根数不够那一格写窗口不够，不写一个偏小的累计值', () => {
  assert.equal(devCell(12.3, false), '窗口不够');
  assert.equal(devCell(null, false), '窗口不够');
  assert.equal(devCell(12.3, true), '+12.3 点');
  assert.equal(devCell(12.3, null), '+12.3 点');   // 后端没交 complete 时不谎称"窗口不够"
});

test('没有一只会触发时也要把判不了的那几只点名', () => {
  const d: AbnormalMove = { summary: { universe: 422, forward: 0, forward_unknown: 7, no_band: 1 } };
  const h = forwardHeadline(d);
  assert.match(h.text, /没有一只/);
  assert.match(h.text, /判不了的 7 只/);
  assert.match(h.text, /1 只连明日涨停幅度都取不到/);
  assert.doesNotMatch(h.text, /不会触发。$/);   // 不能说成"这些票都安全"
});

test('热门股那句说的是这把尺子本身', () => {
  const d: AbnormalMove = {
    summary: { universe: 422 },
    thresholds: { universe_dev10: 25 },
    hot: { limit: 20, rows: [hot({ security_code: '301190.SZ', name: 'X', dev_10: 104.3 })] },
  };
  const t = hotHeadline(d);
  assert.match(t, /10 日累计偏离/);
  assert.match(t, /母体 422 只/);
  assert.match(t, /\+104\.3 点/);
  assert.match(t, /≥25 点进母体/);
  // 10-27 累计换成了区间复利：这句要是还写"逐日相加"，屏幕上就是一把不存在的尺子
  assert.match(t, /区间复利/);
  assert.doesNotMatch(t, /逐日相加|不复利/);
});

test('前瞻榜两类并存时那句要把后一类点名（只报聚合数会对不上行数）', () => {
  const d: AbnormalMove = {
    summary: { universe: 436, forward: 4, forward_unknown: 2, no_band: 0 },
    forward: { rows: [
      fwd({ security_code: '600001.SH', fwd_state: 'will_trigger', trigger_pct: 1.2 }),
      fwd({ security_code: '600002.SH', fwd_state: 'already_partial', trigger_pct: 181.0 }),
      fwd({ security_code: '600003.SH', fwd_state: 'already_partial', trigger_pct: 6.0 }),
    ] },
  };
  const t = forwardHeadline(d).text;
  assert.match(t, /会新踩到一条严重异动的有 4 只（全场聚合）/);
  assert.match(t, /另有 2 只今天已经踩到某一条/);
  assert.match(t, /按已交出的行数算/);     // 这一类只有行数、没有全场聚合，必须说清
  // 「今天已踩到的不重复报」是 abn-1 那版的说法，现在这句是假话
  assert.doesNotMatch(t, /不重复报/);
  // 一只也没有后一类时不许凭空报出一个 0 只的从句
  assert.doesNotMatch(forwardHeadline({
    summary: { universe: 10, forward: 1 },
    forward: { rows: [fwd({ security_code: '600001.SH', fwd_state: 'will_trigger' })] },
  } as AbnormalMove).text, /另有/);
});

test('这一屏自己写的句子与新口径一致（页面不重写判据，但写了的必须是真的）', () => {
  // 组件顶部那格 Caliber 与四张卡片的 hint 都是页面自己写的字，口径一换就得跟着换
  assert.match(comp, /累计偏离是区间复利/);
  assert.doesNotMatch(comp, /逐日相加/);
  assert.doesNotMatch(comp, /今天已踩到的不重复报/);
  // ① 那一列念的是后端交的整格文本（已踩到哪条 + 还差多少个点），页面不再自己拼条款
  assert.match(comp, /Th\(fwdSort, "明日涨停会怎样", "fwd_state_label", "fwd_state"\)/);
  assert.match(comp, /cell\(i, "fwd_state_label", .*dash\(r\.fwd_state_label\)/s);
  assert.doesNotMatch(comp, /fwd_clauses_label/, '条款那一格已经并进「明日涨停会怎样」，两处各拼一遍必漂');
});

test('近期严重异动那句把「还没到」与停牌分开', () => {
  const d: AbnormalMove = {
    recent_severe: {
      sessions: 10, total: 42,
      rows: [rec({ trade_date: '2026-10-13', security_code: '600001.SH', next_halted: true, next_trade_date: '2026-10-14' }),
             rec({ trade_date: '2026-10-15', security_code: '600002.SH', next_trade_date: null })],
    },
  };
  const t = recentHeadline(d);
  assert.match(t, /近 10 场/);
  assert.match(t, /共 42 只次/);
  assert.match(t, /1 只次次日就停牌/);
  assert.match(t, /1 只次的?次日还没到/);
  assert.match(t, /不代表交易所已认定/);
});

test('近期那块的全场总数没读到时不许说成 0', () => {
  const d: AbnormalMove = {
    recent_severe: { sessions: 10, total: null, total_unread: true,
                     rows: [rec({ trade_date: '2026-10-15', security_code: '600009.SH' })] },
  };
  const t = recentHeadline(d);
  assert.match(t, /全场总数没读到/);
  assert.doesNotMatch(t, /共 0 只次/);
});

test('榜被截断时那句声明不能省', () => {
  assert.equal(truncationNote({ forward: { truncated: false, returned: 3 } }), null);
  assert.match(truncationNote({ forward: { truncated: true, returned: 3 } } as AbnormalMove),
    /前瞻榜被上限截断（只交 3 行）/);
  assert.match(truncationNote({ recent_severe: { truncated: true, rows: [] } }), /不代表全场/);
});

test('组件里每个可排序列的 sortKey 就是那一格里读的字段（死键不报错也不换序）', () => {
  const keys = [...comp.matchAll(/Th\(\w+, "[^"]+", "([^"]+)"/g)].map(m => m[1]);
  assert.ok(keys.length >= 24, `列头只有 ${keys.length} 个，组件大概被改坏了`);
  const cellKeys = new Set([...comp.matchAll(/cell\(i, "([^"]+)"/g)].map(m => m[1]));
  // nameCell / boardCell / severeCell 三个公共格子的键在 helper 里，一并收进可比集合
  for (const m of comp.matchAll(/cell\(\s*rowNo\(r\.security_code\), "([^"]+)"/g)) cellKeys.add(m[1]);
  for (const m of comp.matchAll(/cell\(i, "severe_how_label"/g)) cellKeys.add('severe_how_label');
  for (const k of ['security_code', 'board']) cellKeys.add(k);
  const nested = keys.filter(k => k.includes('.'));
  assert.deepEqual(nested, ['board_span.days'], '嵌套键只允许几天几板那一列');
  for (const k of keys) {
    if (k === 'board_span.days') continue;
    assert.ok(cellKeys.has(k), `列头 sortKey ${k} 在任何一格里都没出现过 —— 点它不报错，也永远不换序`);
  }
});

test('三块榜都挂在首页短线情绪下面（不是又开一页）', () => {
  assert.match(mount, /import \{ AbnormalMoveBoard \} from "@\/components\/home\/AbnormalMoveBoard"/);
  const at = mount.indexOf('<AbnormalMoveBoard />');
  assert.ok(at > mount.indexOf('{/* 4b. 短线情绪'), '异动提醒必须排在短线情绪之后');
  assert.ok(at < mount.indexOf('{/* 5. 份额轮动'), '异动提醒必须排在份额轮动之前');
  assert.match(system, /"\/market\/abnormal-move/, '自检清单 CAPABILITIES 里要有这一条，否则接口在跑却没人验');
});

test('交叉高亮用的是全站那一份交互块，且被拦那句不折', () => {
  assert.match(comp, /useStockRowInteraction\("异动提醒"\)/);
  assert.match(comp, /\{STOCK_ROW_HINT\}/);
  assert.match(comp, /ri\.popHint && <p role="status"/);
  assert.match(comp, /\{truncationNote\(am\)/);
});

test('三块榜共用一份行序：行号按代码首现编号，不各数各的', () => {
  assert.match(comp, /if \(!idx\.has\(r\.security_code\)\) idx\.set\(r\.security_code, idx\.size\)/);
  assert.match(comp, /\.\.\.\(am\?\.recent_severe\?\.rows \?\? \[\]\)/);
});

test('这一屏没有把后端的星号带进渲染文本，也没自己造条款', () => {
  // lib 与组件里写死的中文句子都不该含 markdown 粗体标记（caveat/caliber 走 plain() 出口）
  assert.doesNotMatch(lib.replace(/\/\*[\s\S]*?\*\//g, ''), /\*\*/, '判读层交出的文本里不该有 **');
  const rendered = comp.match(/<p[^>]*>([\s\S]*?)<\/p>/g) ?? [];
  for (const b of rendered) assert.doesNotMatch(b, /\*\*/, '组件自己写的句子不该含 **');
  // 用户 2026-10-21「描述性内容全部收缩」之后，这一屏的 caveat/字段义/口径原句都走共用折叠块：
  // 组件不再自己摊一份，也不自己 plain()——plain 与"缺数不许折"的分流都在 `CaliberFold` + `lib/caveatKind.ts` 那一处。
  assert.match(comp, /<CaliberFold foldAll caveats=\{am\.caveats\} definitions=\{am\.definitions\}/,
    'caveat 与字段义只能念后端那一套（走共用折叠块；foldAll＝用户 10-22 点名首页这四条也收，缺数条数由 summary 报出来）');
  assert.match(comp, /notes=\{\[am\.caliber \? `取数口径（后端原句，页面未改写）：\$\{am\.caliber\}` : null\]\}/,
    '后端口径原文仍逐字上屏（只是收进折叠块，页面没改写它）');
  assert.doesNotMatch(comp, /plain\(/, '页面不该再自己 plain 一遍——折叠块出口已经剥过星号，两处剥＝两处可能不一致');
  assert.doesNotMatch(comp, /caveats\?\.filter/, '不许在这一个组件里按 code 挑着念（分流只住 lib/caveatKind.ts，那边有自己的闸）');
});
