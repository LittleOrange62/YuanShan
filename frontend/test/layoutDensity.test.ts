import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

/**
 * 界面宽度与列宽的三条纪律（用户 2026-10-10：左侧能收，右侧也要能收；中间要自适应变宽；
 * 列宽按内容定而不是按容器拉伸 —— 倍量榜 16 列在这个改动前永远出横向滚动条）。
 *
 * 这三条都写在 CSS / 外壳里，而不是逐页改 15 个页面的壳类：
 * 逐页改一定会有人新页忘了加，那时"这一屏能看全"就变成偶然。
 * 所以这里钉的是**那一处**规则还在、还有效、且没有被"更具体的类"顶掉。
 */

const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
const layout = readFileSync(new URL('../src/components/layout/Layout.tsx', import.meta.url), 'utf8');
const panel = readFileSync(new URL('../src/components/workspace/AiAnalysisPanel.tsx', import.meta.url), 'utf8');

test('中间列自适应：覆盖规则必须写在 @layer 之外，否则打不过 Tailwind 的工具类', () => {
  const relax = '.workspace-content > div:is(.max-w-5xl, .max-w-6xl, .max-w-7xl)';
  assert.ok(css.includes(relax), '页面外壳要能被放开（倍量榜被 max-w-5xl 卡死就是缺这条）');
  const rule = css.slice(css.indexOf(relax));
  assert.match(rule, /max-width:\s*min\(1900px,\s*100%\)/, '上限写成死宽就会在小屏溢出、在大屏白留空');
  // 未分层才赢：@tailwind utilities 之后、且不在 @layer 块里
  const at = css.indexOf('@tailwind utilities');
  assert.ok(at >= 0 && css.indexOf(relax) > at, '规则必须在 utilities 之后');
  // 未分层才赢：从文件开头数到大规则那一句，花括号必须正好闭合（不在任何 @layer 块里）
  const head = css.slice(0, css.indexOf(relax));
  const open = (head.match(/\{/g) || []).length, close = (head.match(/\}/g) || []).length;
  assert.equal(open - close, 0, '写在 @layer 里会被工具类顶掉，等于没改');
  // 文字页的壳不许一起放开（长句子拉到 1900px 没法读）
  assert.doesNotMatch(css, /> div:is\([^)]*max-w-(2|3|4)xl/, '2xl~4xl 是文字页，保持原宽');
});

test('列宽按内容：不用 width:auto（实测更宽），也不折行列头（实测列头变 4~5 行）', () => {
  assert.match(css, /\.workspace-content table \{\s*width:\s*min\(100%,\s*max-content\);?\s*\}/,
    '容器宽于内容时不再把空余摊给各列');
  assert.doesNotMatch(css, /\.workspace-content table \{\s*width:\s*auto/,
    'width:auto 会让 Chrome 取 max-content（实测 1301 > 1172），窄屏溢出更多');
  assert.doesNotMatch(css, /table th \{\s*white-space:\s*normal/,
    '列头折行实测把表压到 867px 但列头行高 95px —— 换成横向的读不动，不是解法');
  const pad = [...css.matchAll(/\.workspace-content table[^\n]*\{\s*padding-inline:\s*(\d+)px/g)].map(m => +m[1]);
  assert.ok(pad.length >= 2, '表的内边距要有一处收（列头 button 与单元格两处都要，只改一处不生效）');
  assert.ok(pad.every(p => p <= 4), `密表左右内边距收至 4px（实测 8px→1428、4px→1177），现在是 ${pad}`);
});

test('右侧 AI 栏可收起：状态存本地、收起后留一条把手', () => {
  assert.match(layout, /localStorage\.getItem\('va-ai-rail'\)/, '收起状态要跨刷新记住（与左侧栏同一套做法）');
  assert.match(layout, /localStorage\.setItem\('va-ai-rail'/);
  assert.match(layout, /railCollapsed \?/, '两条分支：开着 w-64 / 收起 w-9 把手');
  assert.match(layout, /aria-label="AI分析（已收起）"/, '收起后仍要有可点的把手，不能只剩一条看不懂的细条');
  assert.match(layout, /aria-label="展开 AI分析栏"/);
  assert.match(panel, /onCollapse/, '收起按钮在栏自己的标题行里（与内容同层，不飘在页面上）');
  assert.match(panel, /aria-label="收起 AI分析栏"/);
  // localStorage 可能禁用（隐私模式）：写失败要说出来，不能静默"以为记住了"
  assert.match(layout, /catch \{ setStorageError\('无法保存右侧栏选择。'\); \}/);
});
