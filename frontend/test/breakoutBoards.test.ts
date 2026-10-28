import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 突破信号页改成左右两栏（用户 2026-10-13）：右栏是原来的突破信号，左栏是埋伏票。
 * 钉的四件"写错了也不会报错"的事：
 *  ① 列头的 sortKey 必须是那一行数据里真有的字段 —— 死键不报错、不换序，aria-sort 还会亮；
 *  ② 筛条件必须发进接口（`types=` / `signal_type=`），在前端筛就只能筛截断后的那些行；
 *  ③ 中文标签只住后端一份，前端抄一份就会和列注释一起走偏；
 *  ④ 那一行筛控件要挂在 Panel 的 `controls` 上：挂进 children 的话，一旦筛空，
 *     下拉与日期框跟着数据一起消失，人就被卡在自己刚选的那个值上。
 */

const page = readFileSync(new URL('../src/pages/VolumeBreakout.tsx', import.meta.url), 'utf8');
const board = readFileSync(new URL('../src/components/quant/AmbushBoard.tsx', import.meta.url), 'utf8');

function ifaceKeys(src: string, name: string): string[] {
  const from = src.indexOf(`interface ${name}`);
  assert.ok(from >= 0, `找不到 interface ${name}`);
  const body = src.slice(from, src.indexOf('}', from));
  // 一行可以声明好几个字段（`code?: string | null; name?: ...`），所以按 `标识符?:` 逐个取
  return [...body.matchAll(/\b([a-z0-9_]+)\?:/g)].map(m => m[1]);
}

function sortKeysIn(src: string, from = 0, to = src.length): string[] {
  return [...src.slice(from, to).matchAll(/sortKey="([a-z0-9_]+)"/g)].map(m => m[1]);
}

test('页面确实是左右两栏：左栏埋伏、右栏突破，各一张表', () => {
  assert.match(page, /import \{ AmbushBoard \} from "@\/components\/quant\/AmbushBoard"/);
  assert.match(page, /<div className="grid items-start gap-3 xl:grid-cols-2">/);
  const grid = page.slice(page.indexOf('xl:grid-cols-2'));
  assert.ok(grid.indexOf('<AmbushBoard />') < grid.indexOf('<BreakoutBoard />'), '埋伏在左、突破在右');
  assert.match(page, /max-w-\[1720px\]/, '两栏并排要把页面放宽（原来是 max-w-5xl）');
  assert.match(page, /左栏埋伏票（左侧机会，还没走出来）、右栏突破信号（右侧已确认）/);
});

test('两栏各 8 列，且每个列头的键都在那一行数据里', () => {
  const leftKeys = ifaceKeys(board, 'AmbushRow');
  const lk = sortKeysIn(board);
  assert.equal(lk.length, 8, `左栏要 8 列，实际 ${lk.length}`);
  for (const k of lk) assert.ok(leftKeys.includes(k), `左栏 sortKey ${k} 不在 AmbushRow 里`);

  const rightKeys = ifaceKeys(page, 'RowX');
  const from = page.indexOf('function BreakoutBoard');
  const rk = sortKeysIn(page, from);
  assert.equal(rk.length, 8, `右栏要 8 列，实际 ${rk.length}`);
  for (const k of rk) assert.ok(rightKeys.includes(k), `右栏 sortKey ${k} 不在 RowX 里`);
  assert.ok(rk.includes('signal_type_label'), '那一格显示的是中文标签，排的就必须是它');
});

test('被砍掉的列进整行悬停而不是凭空消失', () => {
  // 左栏砍：箱体位置 / 60 日涨幅 / 距大阳 / 连升周数 / 本周场数 / 缺数
  for (const f of ['box_pos_pct', 'trend60_pct', 'days_since_big', 'weekly_streak',
                   'week_sessions_cur', 'missing']) {
    assert.match(board, new RegExp(`r\\.${f}`), `${f} 要在悬停里读得到`);
  }
  // 右栏砍：板块 / 上次放量日 / 那波放量倍数 / 突破日 MA60
  for (const f of ['board', 'last_surge_date', 'surge_vol_ratio', 'break_ma60']) {
    assert.match(page, new RegExp(`r\\.${f}`), `${f} 要在悬停里读得到`);
  }
});

test('筛条件发进接口、不在前端筛截断后的行', () => {
  assert.match(board, /&types=\$\{encodeURIComponent\(types\.join\(","\)\)\}/);
  assert.match(board, /&only_strict=\$\{onlyStrict\}/);
  assert.doesNotMatch(board, /all\.filter\([^)]*ambush_types/, '前端筛就只能筛合成分最高的那 150 只');
  assert.match(page, /`\/quant\/volume-breakout\?signal_type=\$\{encodeURIComponent\(signalType\)\}`/);
  assert.doesNotMatch(page, /all\.filter\([^)]*signal_type/);
});

test('中文标签与阈值只住后端一份', () => {
  assert.doesNotMatch(page, /const SIGNAL_LABELS/, '右栏那份中英对照已删');
  assert.doesNotMatch(board, /const TYPE_LABELS/, '左栏也不许另立一份');
  // 阈值出现在页面上时，一律是从 `thresholds` 里念出来的
  assert.match(board, /th\.spread_max \?\? "—"/);
  assert.match(page, /th\.break_mult \?\? "—"/);
});

test('筛控件挂在 controls 槽上，筛空了也还在', () => {
  assert.match(board, /<Panel title="埋伏票（左侧机会）" state=\{hits\} controls=\{controls\}/);
  assert.match(page, /<Panel title="突破信号（右侧已确认）" state=\{hits\} controls=\{controls\}/);
  // 这一句是那条洞的正面证据：children 里不能再有日期框（children 只在有数时画）
  const childPart = board.slice(board.indexOf('{d => {'));
  assert.doesNotMatch(childPart, /input type="date"/, '日期框要在 controls 里，不在 children 里');
});

test('共享出口 Panel 真的渲染 controls（否则上面两条是空炮）', () => {
  const dp = readFileSync(new URL('../src/components/ui/DataPanel.tsx', import.meta.url), 'utf8');
  assert.match(dp, /controls\?: ReactNode/);
  assert.match(dp, /\{controls && <div className="mb-1\.5">\{controls\}<\/div>\}/);
  const ready = dp.slice(dp.indexOf('const ready = hasData(state);'));
  // 10-22 起 Panel 有三条分支（quiet / ready / 其余四型），controls 必须在**任何一条之前**
  assert.ok(ready.indexOf('{controls &&') < ready.indexOf('{quiet'), 'controls 在空态分支之前');
  assert.ok(ready.indexOf('{quiet') < ready.indexOf('ready\n'), '空态分支排在上，有数分支排在下（源码顺序＝渲染顺序）');
});

test('10-19 夜「描述收缩」自查（埋伏榜）：怎么点那句折进说明框，全场数/真筛声明与截断告警留在外面', () => {
  // 用户 2026-10-19：「把选项留出，描述内容都收缩起来」。
  const boxStart = board.indexOf('<ExplainBox title="这一张表怎么点');
  const boxEnd = board.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '埋伏榜要有默认收起的"怎么点"说明框（ExplainBox＝原生 details 不带 open）');
  const box = board.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/);
  assert.doesNotMatch(box, /popHint|text-warning|text-destructive|<Caveats/);
  const before = board.slice(0, boxStart);
  assert.match(before, /类型勾选在库里生效、发生在取数上限之前/, '真筛与全场声明渲染在框之前＝默认可见');
  assert.match(board.slice(0, boxStart) + board.slice(boxEnd), /已达取数上限/, '截断告警不许折进框（它渲染在说明框之后、框外）');
  assert.match(before, /<CaliberFold caveats=\{d\.caveats\} definitions=\{d\.definitions\}/,
    'caveat/字段义交给共用折叠块（用户 10-21 要密度），缺数与截断那几族由它渲染在 `<details>` 之前＝仍在屏上');
  assert.match(board.slice(boxEnd, boxEnd + 400), /am\.popHint && <p role="status"/,
    '弹窗被拦的告警留在说明框外面');
});
