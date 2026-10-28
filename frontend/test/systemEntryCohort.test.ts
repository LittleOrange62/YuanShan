import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 「走完三条的个股 · 近 N 个月名单」那一叠（`components/quant/SystemEntryCohort.tsx`，
 * 挂在「选股汇总」的交易体系榜之后）。
 *
 * 钉的是写错了不报错的那几类：列键与列头不同序、格子没走 `getCellClass`、
 * 把后端那份源清单/列名自己抄一遍、缺数被 `?? 0` 归成 0%、翻页越界、
 * 以及那个"重新取数"按钮到底带没带 `refresh=1`（不带就是假按钮，任务 #105 同族）。
 */

const page = readFileSync(new URL('../src/components/quant/SystemEntryCohort.tsx', import.meta.url), 'utf8');
const host = readFileSync(new URL('../src/pages/Confluence.tsx', import.meta.url), 'utf8');
const sys = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
const compute = readFileSync(new URL('../../app/compute/confluence.py', import.meta.url), 'utf8');
const cohort = readFileSync(new URL('../../duanxian/system_entry_cohort.py', import.meta.url), 'utf8');

function ifaceKeys(name: string): string[] {
  const from = page.indexOf(`interface ${name}`);
  assert.ok(from >= 0, `找不到 interface ${name}`);
  const rest = page.slice(from);
  const body = rest.slice(0, rest.search(/\n\}/));
  return [...body.matchAll(/\b([a-z0-9_]+)\?:/g)].map(m => m[1]);
}

const ROW_KEYS = ifaceKeys('RowT');
const FIXED = ['date', 'code', 'name', 'close', 'pct_change', 'gap_pct'];

test('固定列的 sortKey 与格子列键同序，且都是行里真有的字段', () => {
  const sortKeys = [...page.matchAll(/sortKey="([a-z0-9_]+)"/g)].map(m => m[1]);
  assert.deepEqual(sortKeys, FIXED, '列头与格子的固定列分叉了');
  const cellKeys = [...page.matchAll(/<Cell i=\{i\} k="([a-z0-9_]+)"/g)].map(m => m[1]);
  assert.deepEqual(cellKeys, FIXED, '格子顺序与列头顺序分叉了');
  for (const k of sortKeys) assert.ok(ROW_KEYS.includes(k), `${k} 不在 RowT 里，点了不会排序`);
});

test('涨幅那几列由后端的 span_columns 驱动，页面不抄列名也不抄键', () => {
  const spec = cohort.slice(cohort.indexOf('SPAN_COLUMNS = ('), cohort.indexOf(')\n\n\ndef clear_cache'));
  const labels = [...spec.matchAll(/'label': '([^']+)'/g)].map(m => m[1]);
  const cumKeys = [...spec.matchAll(/'cum': '([a-z0-9_]+)'/g)].map(m => m[1]);
  assert.deepEqual(labels, ['次日', '第2日', '第3日', '5日', '10日', '15日']);
  assert.equal(labels.length, 6, '后端那一份的档数变了：这里要一起看（不是放宽，是分叉）');
  for (const l of labels) assert.ok(!page.includes(`"${l}"`), `页面硬抄了列名：${l}`);
  for (const k of cumKeys) assert.ok(!page.includes(`sortKey="${k}"`), `${k} 被写死了，应读 span_columns`);
  assert.ok(page.includes('cols.map(c =>'), '涨幅列没按后端清单渲染');
  assert.ok(page.includes('sortKey={String(c.cum)}'), '涨幅列的排序键不是后端那一份');
  assert.ok(page.includes('const k = String(c.cum);'), '格子的列键与列头不是同一个来源');
  for (const k of cumKeys) assert.ok(ROW_KEYS.includes(k), `${k} 不在 RowT 里`);
});

test('密度那一格的三种写法：档位配名次、分数用空格分开、日期用空格分开', () => {
  assert.ok(page.includes('function tierRankLine(s: SeqSource, n: number)'),
    '象限那一行要档位与全市场名次并在一起念（Q4(926)），分成两排就把每行密度翻倍');
  assert.ok(page.includes('s.tier ? tierRankLine(s, n) : tokens(s.rank, n)'),
    '有档位的源念档位+名次，没有的念名次');
  assert.ok(page.includes("tokens(s.value, n, ' ')"),
    '分数那一行用空格分隔：负号本身就是 `-`，用 `-` 当分隔符会念成 `5.8--12.26`');
  assert.ok(!page.includes('tokens(s.value, n)'), '分数行不许退回横杠分隔');
  assert.match(page, /function dayLine[\s\S]*?bits\.join\(" "\)/,
    '日期自己带横杠（10-19），分隔符必须是空格，否则一段日期糊成一串');
  assert.ok(page.includes('▸'), '符合日在那一排要标出来（前 2 场与后 5 场靠它对齐）');
});

test('交叉源的名字与顺序读 CF.SOURCES，页面一份都不写', () => {
  const labels = [...compute.matchAll(/label='([^']+)'/g)].map(m => m[1]);
  assert.ok(labels.length >= 6, `源清单没抓到：${labels.length}`);
  for (const l of labels) assert.ok(!page.includes(l), `页面硬抄了源名：${l}`);
  assert.ok(page.includes('s.label'), '位次那一段的源名要读接口交出的那一份');
  assert.ok(!/四维象限|倍量形态|埋伏票|龙虎榜/.test(page), '组件里出现了抄来的源名');
});

test('格子走共用 Cell（高亮链一份实现），位次那一格刻意不参与交叉高亮', () => {
  assert.ok(page.includes('import { Cell, n2, pc, tone } from "@/components/quant/SystemEntryBoard";'),
    '格式化件与 Cell 必须与上面那块榜共用一份实现');
  const board = readFileSync(new URL('../src/components/quant/SystemEntryBoard.tsx', import.meta.url), 'utf8');
  assert.match(board, /td className=\{cell\.getCellClass\(i, k, cn\(/,
    '那份 Cell 的高亮链不在这里，在共用实现里');
  assert.ok(page.includes('{...cell.rowProps(i, r.code, undefined, hoverOf(r, cal, cols))}'),
    '整行悬停与双击跳转走全站那一份');
  assert.ok(page.includes('function SeqCell'), '位次那一格是独立的块');
  assert.ok(!/<Cell i=\{i\} k="seq"/.test(page), '位次格不该按"一格一个数"去交叉高亮');
});

test('缺数念 · 与 —，读数一处都不许被 ?? 0 归成 0', () => {
  assert.ok(page.includes('"·"'), '空位要有自己的记号');
  // 长度/下标类的 `?? 0` 是正当的（少一格就是少一格），坏的是把**读数**补成 0
  assert.ok(!/\b(pc|n2)\([^)]*\?\? 0\b/.test(page), '涨幅/读数补 0 = 屏幕会念成平盘');
  assert.ok(!/value \?\? 0/.test(page), '原值补 0 会被念成"那天真是 0"');
  assert.ok(!/parseFloat\(/.test(page), '自己解析字符串＝绕开了交出来的数');
  const tokens = /function tokens[\s\S]*?\n}/.exec(page)?.[0] ?? '';
  assert.ok(tokens.includes('"·"') && tokens.includes('String(v)'), tokens);
});

test('分页读接口的 page_size，越界自己收，翻页时清掉选中', () => {
  assert.ok(page.includes('cur?.page_size ?? 20'), '每页行数要读后端那一份');
  assert.ok(page.includes('Math.ceil(sorted.length / pageSize)'), '页数不是数出来的');
  assert.ok(page.includes('Math.min(page, pages - 1)'), '删行/换批后页码越界要收回来');
  assert.ok(page.includes('disabled={cur0 <= 0}') && page.includes('disabled={cur0 >= pages - 1}'),
    '边界按钮不许亮着');
  assert.ok((page.match(/cell\.clearAll\(\); setPage\(/g) ?? []).length >= 2, '换页要清掉行列选中');
  assert.ok(page.includes('cell.clearAll(); setPage(0); rowSort.toggle(k)'), '换排序要回到第 1 页');
  assert.ok(page.includes('整批 {sorted.length} 行'), '要说清排的是整批而不是这一页');
});

test('缓存自己交年龄，且重新取数真的带 refresh', () => {
  assert.ok(page.includes('if (nonce > 0) q.set("refresh", "1")'),
    '不带 refresh 的"重新取数"是假按钮（一小时内拿同一份缓存）');
  assert.ok(page.includes('/quant/system-entry-cohort?'), '没走 probe 的 /api 前缀口径');
  assert.ok(page.includes('cur?.generated_at') && page.includes('cache_age_seconds'),
    '缓存里的数必须写明算于何时');
  assert.ok(page.includes('刚刚现算'), '两种来源要分开念');
});

test('月份不硬写：标题与窗口行都读接口', () => {
  assert.ok(page.includes('monthsHint(cur?.months)'), '标题里的月份数要读接口');
  assert.ok(!/近 6 个月|最近 6 个月/.test(page), '组件里写死了 6（后端改默认值就会分叉）');
  assert.ok(page.includes('${d.months} 个自然月'), '窗口那一行读接口那份 months');
  assert.ok(page.includes('${dash(d.window?.from)} ~ ${dash(d.window?.to)}'), '窗口两端来自接口');
});

test('说明默认收起、告警不折、页面上不许有裸星号', () => {
  const boxStart = page.indexOf('title={`这些格子怎么读');
  const boxEnd = page.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '逐段口径要有默认收起的说明框');
  assert.match(page.slice(boxStart, boxEnd), /<Definitions map=\{noteMap\} bare \/>/);
  assert.ok(page.includes('c.key !== "cohort"'),
    'cohort 那句在默认可见位置（controls 槽），不重复摊进框');
  const warnStart = page.indexOf('{(d.self_check ?? []).length > 0');
  assert.ok(warnStart > 0 && warnStart < boxStart, '自检不过那句是告警，不许折');
  assert.ok(page.includes('<CaliberFold caveats={d.caveats}'),
    'caveat 交给共用折叠块（缺数/坏了一族由它留在屏上）');
  assert.ok(!/definitions=\{d\.calibers\}/.test(page),
    '机器键字典不许当 definitions 传：屏上会念出 cohort／forward／seq 这种标识符');
  assert.ok(page.includes('(d.caliber_items ?? [])') || page.includes('d.caliber_items'),
    '上屏那份字典要读「中文标题 + 句子」那一份（后端同源交出的 caliber_items）');
  assert.match(page, /cell\.popHint && \(\s*<p role="status"/, '弹窗被拦的告警留在说明框外面');
  const body = page.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!body.includes('**'), '源码正文里有字面 ** —— 会被当正文渲染出来');
});

test('块挂在交易体系榜之后，端点也登记进自检清单', () => {
  assert.ok(host.includes('<SystemEntryCohort />'), '选股汇总页没挂上这一块');
  assert.ok(host.indexOf('<SystemEntryCohort />') > host.indexOf('<SystemEntryBoard />'),
    '这一叠要排在榜单之后（用户说的"这个清单最后"）');
  assert.ok(sys.includes('/quant/system-entry-cohort'), '自检清单少这一条接口');
  const line = /"\/quant\/system-entry-cohort"[^\n]*\n/.exec(sys)?.[0] ?? '';
  assert.ok(line.includes('slow: true'), '整批一次算完要 8 秒，自检里得标慢接口');
  assert.ok(line.includes('where: "/confluence"'), '这一条要指回同一屏');
});
