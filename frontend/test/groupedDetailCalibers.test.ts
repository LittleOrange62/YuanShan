import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const rd = (p: string) => fs.readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8');
const rdPy = (p: string) => fs.readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');

/**
 * 用户 2026-10-14：「自选明细里的操作分我还不是很懂，麻烦解释内容更具体一些」
 *                「10日日均 %、20日日均 % 这两个指标也看不懂，为什么数值都这么小？是不是有问题」
 *
 * 现算的结论有两条，两条都要钉住：
 *  ① **数值小不是坏数**：`chg10_avg = MA(pct_change, 10)` 是每日涨跌的算术平均（正负抵消），
 *     与同键去掉 `_avg` 的 `chg10`（区间合计）差一个窗口长度 —— 2026-10-14 全场实测
 *     chg10_avg 中位 -0.56 / chg10 中位 -5.70。所以这一屏必须**两把尺子并排**，
 *     只给日均、不给合计，就是让人猜。
 *  ② 但**这一族确实有一处真问题**：`stock_daily.pre_close` 存的是昨日收盘价而不是除权参考价，
 *     除权那天库里记成假暴跌（002961.SZ 记 -35.08%，还原 -2.54% ⇒ 日均从 +0.24 被写成 -3.30）。
 *     这条不许被文案藏起来。
 *
 * 还有原来那句更要命的：表头提示与 `unit.oper` 都写「百分位化的相对量」—— **是错的**，
 * 那一格交的是 `analytics.score.value`（当日实测 -77.8~+16.2 的带符号合成分），
 * 同表的 `percent_rank` 才是百分位、这一屏根本没取。
 */

const PAGE = rd('pages/Grouped.tsx');
const WL = rdPy('duanxian/watchlist.py');

test('日均与区间并排列在同一行，且都进红绿着色集合', () => {
  assert.match(PAGE, /\["chg10_avg", "10日日均 %", "日均"\], \["chg10", "10日区间 %", "区间"\]/,
    '日均旁边必须紧跟区间，否则"小"没有参照');
  assert.match(PAGE, /\["chg20_avg", "20日日均 %", "日均"\], \["chg20", "20日区间 %", "区间"\]/);
  assert.match(PAGE, /const SIGNED = new Set<string>\(\["pct_change", "chg10_avg", "chg20_avg", "chg10", "chg20"\]\)/,
    '区间列也带正负号，不染色就会被读成"没涨跌"');
  assert.match(PAGE, /chg10\?: number \| null; chg20\?: number \| null;/, '行类型里有这两格（sortKey 才不是死键）');
});

test('表头提示与后端 unit 都不许再说操作分是百分位', () => {
  assert.doesNotMatch(PAGE, /百分位化的相对量/, '前端那行错的提示要清干净');
  assert.doesNotMatch(WL, /百分位化的相对量/, '后端 unit 里同一句也要清');
  assert.match(PAGE, /不是百分位、不是涨幅/, '改完必须正面说出它是什么');
  assert.match(WL, /不是百分位、不是涨幅/);
});

test('操作分的构成写在屏幕上，且只有一份（住在后端，页面原样列出）', () => {
  assert.match(WL, /"score_meaning": \{/, '构成只住后端这一处');
  assert.match(WL, /0\.40×个股质量\(X\) \+ 0\.60×环境共振\(Y\)/);
  assert.match(WL, /形态 0\.25 \+ 趋势 0\.25 \+ 位置 0\.20 \+ 筹码 0\.20 \+ 量能 0\.10/);
  assert.match(WL, /板块热度 0\.65 \+ 大盘情绪 0\.35/);
  assert.match(PAGE, /d\.score_meaning/, '页面要渲染它，不是躺在接口里');
  // 权重不许在两处各写一遍：页面的那句 note 只说合成式，分项权重仍由后端那一份给
  assert.equal((PAGE.match(/形态 0\.25/g) ?? []).length, 0,
    '分项权重只住后端一份，前端不许抄一遍（抄了就会漂）');
});

test('日均与区间的换算、以及除权那只假暴跌，都要在屏上明说', () => {
  assert.match(WL, /中位 -0\.56/, '日均的中位数（实测）');
  assert.match(WL, /chg10 中位 -5\.70/, '区间的中位数（实测）—— 两把尺子的差');
  assert.match(WL, /002961\.SZ/, '符号被除权反掉的那只票必须点名');
  assert.match(WL, /不是除权参考价/);
  assert.match(WL, /本批自选名单里 116 只/, '影响到多少只要说出来，不能只说"可能有偏差"');
});

test('后端原样给出的散文字段过 plain()，星号不许漏进渲染文本', () => {
  // 这两块是"后端散文"，与 CaveatText 同一个出口待遇；数据值（代码、数值）一律不包 plain()
  assert.match(PAGE, /\{Object\.entries\(d\.indicator_meaning\)\.map\(\(\[k, v\]\) => \(\s*<p key=\{k\}[^\n]*\{plain\(v\)\}/);
  assert.match(PAGE, /\{Object\.entries\(d\.score_meaning\)\.map\(\(\[k, v\]\) => \(\s*<p key=\{k\}[^\n]*\{plain\(v\)\}/);
  assert.match(PAGE, /import \{ plain \} from "@\/lib\/agent"/);
});
