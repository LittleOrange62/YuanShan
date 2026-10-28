import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { scoreRankCell } from '../src/lib/ranking.ts';

/**
 * 「八项能力加权明细」那一格名次（2026-10-22，用户点名：
 * `market_mood -46.200 分 全市场第 1（前 50.0%）怎么就排名第 1 了？`）。
 *
 * 钉三件事：
 *  ① `scoreRankCell` 只转述后端交出的 `rank_kind`/`rank_note`/计数，**自己不判**"几个取值算粗"；
 *  ② 判不了那一档（degenerate／unknown／no_rank／名次没交）→ 屏上念 `名次 —` 且 `warn` 为真，
 *     绝不回落成「第 1」或「前 50%」；
 *  ③ 两个用它的屏（趋势量化明细、个股档案）都调这一个函数，组件里没有第二句"为什么"。
 */

const trend = readFileSync(new URL('../src/components/quant/TrendQuant.tsx', import.meta.url), 'utf8');
const dossier = readFileSync(new URL('../src/components/quant/StockDossier.tsx', import.meta.url), 'utf8');

test('degenerate（同场同一个值）：名次与分位都是 —，不是第 1', () => {
  const cell = scoreRankCell({
    rank_kind: 'degenerate', rank_in_market: null, percent_rank: null,
    universe: 5568, distinct_values: 1, tie_at_value: 5568, biggest_tie: 5568,
    rank_note: '这一场这一项全市场 5,568 只都是同一个值（-46.2004）',
  })!;
  assert.equal(cell.head, '名次 —');
  assert.equal(cell.pct, '', '分位也判不了 ⇒ 这一格不提"前百分之几"，不摆一个 50.0% 出来');
  assert.ok(cell.warn, '这一档要用告警色，不能长得像一条正常读数');
  assert.ok(cell.extras.includes('5,568 只有分'));
  assert.ok(cell.title.startsWith('这一场这一项全市场 5,568 只都是同一个值'), '悬停就是后端那句原文');
});

test('coarse（档位化名次）：名次照念，并列只数与取值个数一起上屏', () => {
  const cell = scoreRankCell({
    rank_kind: 'coarse', rank_in_market: 2809, percent_rank: 46.6961,
    universe: 5554, distinct_values: 5, tie_at_value: 306, biggest_tie: 2779,
    rank_note: '这一场这一项只有 5 个不同取值',
  })!;
  assert.equal(cell.head, '全市场第 2809');
  assert.equal(cell.pct, '前 53.3%', '100 − 分位，页面不自己判分档');
  assert.ok(cell.extras.includes('并列 306 只'));
  assert.ok(cell.extras.includes('这项全场 5 个取值'));
  assert.equal(cell.warn, false);
});

test('ok：并列 1 只不提并列，其余照常', () => {
  const cell = scoreRankCell({
    rank_kind: 'ok', rank_in_market: 1736, percent_rank: 68.8398,
    universe: 5568, distinct_values: 5533, tie_at_value: 1, biggest_tie: 2, rank_note: '',
  })!;
  assert.equal(cell.head, '全市场第 1736');
  assert.ok(cell.extras.includes('5,568 只有分'));
  assert.ok(!cell.extras.some(x => x.startsWith('并列')), '并列 1 只不是信息，别占格');
});

test('并列只数 >1 时即便 kind=ok 也要念出来', () => {
  const cell = scoreRankCell({ rank_kind: 'ok', rank_in_market: 3, percent_rank: 90,
    universe: 5568, distinct_values: 4000, tie_at_value: 12 })!;
  assert.ok(cell.extras.includes('并列 12 只'));
});

test('后端没交 kind：照原样念但说明没判过', () => {
  const cell = scoreRankCell({ rank_in_market: 9, percent_rank: 80, universe: 100 })!;
  assert.equal(cell.warn, true);
  assert.ok(cell.title.includes('rank_kind 缺字段'));
  assert.ok(cell.title.includes('后端没交这一项的分布判据'));
});

test('缺数不落到 0：rank=null、universe=null 都念 — 或不提', () => {
  assert.equal(scoreRankCell({ rank_kind: 'no_rank', rank_in_market: null })!.head, '名次 —');
  assert.ok(!scoreRankCell({ rank_kind: 'ok', rank_in_market: 5 })!.extras
    .some(x => x.includes('0 只有分')), '分母没读到不许说成 0 只');
  assert.equal(scoreRankCell(null), null);
});

test('两个用它的屏都调这一个函数，组件里没有第二句"为什么"', () => {
  for (const [name, src] of [['TrendQuant', trend], ['StockDossier', dossier]] as const) {
    assert.ok(/scoreRankCell\(/.test(src), `${name} 没接公用件`);
    assert.ok(!/全市场第\s*\{/.test(src), `${name} 自己拼了一句名次`);
    assert.ok(!/(100 - c(0|C)?\w*\.percent_rank)/.test(src), `${name} 自己把分位翻成"前百分之几"`);
  }
});

test('趋势量化明细那一屏：中文名走后端、机器键留在悬停与脚本列', () => {
  assert.ok(/capability_name \?\? c\.capability_key/.test(trend));
  assert.ok(/component_name \?\? p\.component_label/.test(trend), '分量名回落机器键要按顺序来');
  assert.ok(/<CaliberFold caveats=\{d\.caveats\} definitions=\{d\.definitions\}/.test(trend));
  assert.ok(/names_source/.test(trend) && /component_names_source/.test(trend), '名字的两个出处也念出来');
});

test('公用件自己与两个组件：会渲染的中文串里没有 markdown 粗体', () => {
  const files: [string, string][] = [
    ['ranking.ts', readFileSync(new URL('../src/lib/ranking.ts', import.meta.url), 'utf8')],
    ['TrendQuant.tsx', trend], ['StockDossier.tsx', dossier]];
  let seen = 0;
  for (const [name, src] of files) {
    // 带汉字的字符串字面量都可能被渲染到屏幕（含 native title，那边不过 plain()）
    for (const m of src.matchAll(/(["'`])([^"'`\n]*[一-鿿][^"'`\n]*)\1/g)) {
      seen += 1;
      assert.ok(!m[2].includes('**'), `${name} 的「${m[2].slice(0, 24)}」里带了 markdown 粗体`);
    }
  }
  assert.ok(seen > 40, `只扫到 ${seen} 条中文串 —— 正则大概坏了，这条断言就白写了`);
});
