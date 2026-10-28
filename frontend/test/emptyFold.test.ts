import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { isQuiet } from '../src/lib/datastate.ts';

/**
 * 「后端说这一项本机没有数 ⇒ 整块默认收起」（用户 2026-10-22）。
 * 钉的不是"收了几块"，而是**哪一型该收、哪几型绝不许收**，以及收起来之后屏幕上还剩什么。
 */

const panel = readFileSync(new URL('../src/components/ui/DataPanel.tsx', import.meta.url), 'utf8');
const fold = panel.slice(panel.indexOf('export function QuietFold'), panel.indexOf('export function Panel'));
const win = panel.slice(panel.indexOf('const ready = hasData(state)'));

test('六态里只有 available:false 算“这块没数”，其余四型照旧摊着', () => {
  assert.equal(isQuiet({ kind: 'loading' } as never), false, '取数中不算空态（那块还在等，收了像是坏了）');
  assert.equal(isQuiet({ kind: 'idle', reason: '还没选一只' } as never), false);
  assert.equal(isQuiet({ kind: 'offline', reason: '连不上后端' } as never), false, '连不上是故障，必须响');
  assert.equal(isQuiet({ kind: 'error', reason: '502' } as never), false, '取数失败必须响');
  assert.equal(isQuiet({ kind: 'not_wired', path: '/api/x' } as never), false,
    '404 是“这一屏还没有数据来源”，与“今天没数”是两件事，都得摊着');
  assert.equal(isQuiet({ kind: 'ok', data: { available: true } } as never), false);
  assert.equal(isQuiet({ kind: 'ok', data: {} } as never), false,
    '没交 available 字段的载荷一律不算空态 —— 不能靠"看起来没数"就收');
  assert.equal(isQuiet({ kind: 'ok', data: { available: null } } as never), false,
    'null 是没表态，不是 false');
  assert.equal(isQuiet({ kind: 'ok', data: { available: false, reason: '库里没这场' } } as never), true);
});

test('收起来的那一行仍然把原因整句说完，只把口径与契约折进去', () => {
  assert.doesNotMatch(fold, /<details[^>]*\sopen/, '默认必须收起（用户点名的就是这一条）');
  assert.match(fold, /\{title \? `\$\{title\} —— ` : ""\}\{text\}/,
    'summary 里是标题＋describeState 那句完整原因（不 slice、不改写）');
  assert.doesNotMatch(fold, /\.slice\(/, '原因句子不许在屏幕上被截断——截了就变成"这块只说了半句"');
  assert.match(fold, /取到数会自动展开/, '要告诉人这是数据决定的收起，不是把它藏了');
  assert.match(fold, /\{note && <div/, '口径说明在框里（bare 的一份，不再套第二个 details）');
  assert.doesNotMatch(fold.slice(fold.indexOf('<summary'), fold.indexOf('</summary>')), /<details/,
    'summary 里不许再嵌一层折叠（全站那条"折叠套折叠"的教训）');
});

test('Panel 那一支：quiet 时标题与 note 折叠都让位给这一行，其余三型一支没动', () => {
  assert.match(win, /const quiet = isQuiet\(state\);/);
  assert.match(win, /\{!quiet && <h2/, 'quiet 时标题移进 summary，不再单占一行');
  assert.match(win, /\{note && !quiet && <ExplainBox/, 'note 的折叠框在 quiet 时不再出现（它的内容进 summary 那个框）');
  assert.match(win, /<QuietFold title=\{title\} text=\{plain\(text\)\}/, '走共用那一个 QuietFold');
  // 另外三型（error/offline/not_wired/loading）仍走下面那支：整句摊着、坏了红着
  assert.match(win, /state\.kind === "error" \|\| state\.kind === "offline" \? "text-destructive" : "text-warning"/,
    '非 ready 那一支的配色判据没被动过');
  assert.ok(win.indexOf('const quiet') < win.indexOf('text-destructive'),
    '源码顺序：quiet 那支在前、故障那支在后（顺序就是渲染顺序）');
});

/** 全站套用清单：`Panel` 覆盖 40 个文件里那 63 处；剩下几块不走 Panel，必须逐个接上这一个实现。
 *  少接一块就是"同一件事在有的屏收、有的屏摊"，那正是本项目反复出过的两份真相。 */
test('不走 Panel 的那几块也接上了同一个实现', () => {
  const users = [
    '../src/components/home/AbnormalMoveBoard.tsx',
    '../src/components/home/RiskSignalPanel.tsx',
    '../src/components/home/FirstBoardDailyPanel.tsx',
    '../src/components/quant/IndicatorGrid.tsx',
  ];
  for (const f of users) {
    const src = readFileSync(new URL(f, import.meta.url), 'utf8');
    assert.match(src, /<QuietFold/, `${f} 没接共用空态折叠（还是自己摆那一框黄字）`);
    // 接的是"available:false"这一型，不是把"坏了"那一支也一起收掉
    // （两种写法都算：自己分两支摆；或 `{!quiet && note && …}` —— note 就是 describeState，非 ok 那几型也在里面）
    assert.match(src, /kind !== "ok"[\s\S]{0,220}describeState|available === false[\s\S]{0,200}QuietFold|!st\.data\.available[\s\S]{0,160}QuietFold|!quiet && note && <p/,
      `${f} 上 available:false 与"接口坏了"两型应当分着摆`);
  }
  // 刻意没接的两块，理由写在各文件头部注释里，这里点名防"顺手全上"：
  //  · SectorFlowCumulative：它自己那份说明讲清了"端点实现了但表里没场次"与 404 必须长得不一样；
  //  · TdxWatchPanel：那是能力/开关面板，available:false 本身就是它的读数。
  for (const f of ['../src/components/SectorFlowCumulative.tsx', '../src/components/TdxWatchPanel.tsx'])
    assert.doesNotMatch(readFileSync(new URL(f, import.meta.url), 'utf8'), /QuietFold/,
      `${f} 是**故意不套**的两块之一；要改请先改它头部那条理由`);
});
