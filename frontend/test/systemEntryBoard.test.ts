import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 交易体系榜那一块（`components/quant/SystemEntryBoard.tsx`，挂在「选股汇总」下方）。
 * 钉的是写错了不报错的那几类：列键与列头不同序、getCellClass 没进 className、
 * 筛条件没发进接口、历史分布缺基线、页面自写句子里漏出 markdown 星号。
 */

const page = readFileSync(new URL('../src/components/quant/SystemEntryBoard.tsx', import.meta.url), 'utf8');
const host = readFileSync(new URL('../src/pages/Confluence.tsx', import.meta.url), 'utf8');
/** 列头文字与逐列说明的真源（历史分布那张表）—— 页面不许再抄一份中文列名 */
const compute = readFileSync(new URL('../../app/compute/system_entry.py', import.meta.url), 'utf8');

function ifaceKeys(name: string): string[] {
  const from = page.indexOf(`interface ${name}`);
  assert.ok(from >= 0, `找不到 interface ${name}`);
  const rest = page.slice(from);
  const body = rest.slice(0, rest.search(/\n\}/));
  return [...body.matchAll(/\b([a-z0-9_]+)\?:/g)].map(m => m[1]);
}

/** 从 Python 源码里把 STATS_COLUMNS_SPEC 那一段切出来（不执行它，只按文本钉） */
function statsSpec(): { block: string; keys: string[]; labelled: number } {
  const from = compute.indexOf('STATS_COLUMNS_SPEC = (');
  assert.ok(from >= 0, '后端没有 STATS_COLUMNS_SPEC：列头与说明没有那一份唯一出处了');
  const block = compute.slice(from, compute.indexOf('\n)', from));
  const keys = [...block.matchAll(/'key':\s*'([a-z0-9_]+)'/g)].map(m => m[1]);
  return { block, keys, labelled: (block.match(/'label':/g) ?? []).length };
}

const ENTRY_KEYS = ifaceKeys('Entry');

test('列头的 sortKey 与每一格的列键同序，且都是行里真有的字段', () => {
  const sortKeys = [...page.matchAll(/sortKey="([a-z0-9_]+)"/g)].map(m => m[1]);
  const cellKeys = [...page.matchAll(/<Cell i=\{i\} k="([a-z0-9_]+)"/g)].map(m => m[1]);
  assert.ok(sortKeys.length >= 12, `抓取到的列头太少：${sortKeys.length}`);
  assert.deepEqual(cellKeys, sortKeys, '格子顺序与列头顺序分叉了');
  for (const k of sortKeys) assert.ok(ENTRY_KEYS.includes(k), `${k} 不在 Entry 里，点了不会排序`);
});

test('className 走了 getCellClass（只挂 onClick 的话列高亮会静默失效）', () => {
  assert.match(page, /td className=\{cell\.getCellClass\(i, k, cn\(/);
  assert.match(page, /onClick=\{\(e\) => cell\.handleCellClick\(e\.currentTarget, i, k\)\}/);
});

test('三个筛条件都发进接口，而不是在前端筛已交出的行', () => {
  assert.ok(page.includes('q.set("date", date)'), '日期没发进接口');
  assert.ok(page.includes('q.set("only_entry"'), '上车/观察池没发进接口');
  assert.ok(page.includes('q.set("min_gap", String(minGap))'), '缺口幅度没发进接口');
  assert.ok(page.includes('/quant/system-entry?'), '没走 probe 的 /api 前缀口径');
  assert.ok(!/all\.filter\(/.test(page), '前端又筛了一遍（只能筛到已交出的那些行）');
});

test('点列头先取消选中；默认不给初始排序', () => {
  assert.match(page, /const rowSort = useTableSort\(\);/);
  const sortBy = /const sortBy = \(k: string\) => \{[^}]*\}/.exec(page)?.[0] ?? '';
  assert.ok(sortBy.includes('cell.clearAll()') && sortBy.includes('rowSort.toggle(k)'), sortBy);
  assert.ok(page.includes('onSort={sortBy}'));
});

test('历史分布必须与同期基线并排，且三档都要在', () => {
  const { keys } = statsSpec();
  for (const k of ['win_next', 'p20_up20', 'hit_take', 'hit_stop', 'rule_expect', 'd20_med']) {
    assert.ok(keys.includes(k), `${k} 不在后端列清单里：那一列根本不会上屏，只报半边结论`);
  }
  assert.ok(page.includes('(hs.columns ?? [])'), '列头没按后端交出的 columns 渲染');
  assert.ok(/cols\.map\(c =>/.test(page), '列头/格子不是同一份清单驱动（就会不同序）');
  assert.equal((page.match(/cols\.map/g) ?? []).length, 2, '表头与表体各自 map，少一处就是列序分叉');
  assert.ok(page.includes('x.bucket_key === "baseline"'), '基线那行没钉住（着色与对比都以它为尺子）');
  assert.ok(page.includes('c.versus_base'), '比基线着色由后端点名，不在页面里挑哪几列比');
  assert.ok(page.includes('{x.bucket}'), '档位名要读后端那一份，不在页面另写一遍');
  for (const label of ['基线（同期全部票·日）', '①② 门票 + 缺口未回补']) {
    assert.ok(!page.includes(label), `页面里硬抄了档位名：${label}`);
  }
  assert.ok(page.includes('plain(cal.history ?? "")') && page.includes('plain(cal.path ?? "")'));
  assert.ok(page.includes('rows.length < 3'), '三档没数齐时不吭声 = 会被读成"就这么差"');
});

test('后 2/3/4/5 日均值四列：列、说明、自己的分母三样都在同一份清单里', () => {
  const { block, keys } = statsSpec();
  for (const n of [2, 3, 4, 5]) {
    assert.ok(keys.includes(`avg_${n}d`), `avg_${n}d 没进列清单`);
    assert.ok(keys.includes(`n_${n}d`) || block.includes(`'denom_key': 'n_${n}d'`),
      `avg_${n}d 没有自己的分母 n_${n}d：不同批样本会被摆成一排比大小`);
  }
  assert.ok(/'key': 'avg_next'[\s\S]{0,400}?'key': 'avg_2d'/.test(block),
    '次日均值与后 N 日均值不连着排：读者会把它们当成两把尺');
  assert.ok(page.includes('c.denom_key'), '页面没读分母键');
  assert.ok(page.includes('N≥'), '列头没把"最短那档的分母"标出来');
});

test('逐列中文说明只住后端那一份，页面上默认收起且不含硬抄的列名', () => {
  const { block, keys, labelled } = statsSpec();
  assert.equal(labelled, keys.length, '有列头没配说明（label 数与 key 数不齐）');
  assert.equal((block.match(/'note':/g) ?? []).length, keys.length, '有列没写 note');
  // 中文列名一处都不许在组件里出现（手抄一份就会与后端分叉）
  for (const label of ['后10最大上涨中位', '后20冲出两成', '这条规则的期望', '先碰+20%']) {
    assert.ok(block.includes(`'${label}'`), `后端清单里没有这一列：${label}`);
    assert.ok(!page.includes(label), `页面硬抄了列头/说明文字：${label}`);
  }
  const from = page.indexOf('<ExplainBox title={`这三档与每一列怎么读');
  const to = page.indexOf('</ExplainBox>', from);
  assert.ok(from > 0 && to > from, '逐列说明要在一个默认收起的说明框里（用户 2026-10-22：说明默认收缩）');
  assert.match(page.slice(from, to), /<Definitions map=\{notes\} bare \/>/);
});

test('旧批次没算的列要自己念出来，且不跟着折', () => {
  assert.ok(page.includes('hs.uncomputed_columns'), '整列念 — 而不说"没跑过" = 会被读成"一根都没凑齐"');
  assert.ok(page.includes('text-warning'), '那句话得是告警色');
  const warnStart = page.indexOf('{(hs.uncomputed_columns ?? []).length > 0');
  const foldStart = page.indexOf('<ExplainBox title={`这三档与每一列怎么读');
  assert.ok(warnStart > 0 && warnStart < foldStart, '告警必须在折叠框之前（折起来就等于没说）');
});

test('空榜的措辞分得清"没确认"与"链没跑到这一天"', () => {
  assert.ok(page.includes('这一场没有三条全过的票'));
  assert.ok(page.includes('是这张表这一天没算'));
});

test('被筛条件筛空时不许说成"这一场没有"', () => {
  assert.ok(page.includes('minGap > 0'), '0 行要先看是不是缺口下限把它们筛空了');
  assert.ok(page.includes('把它们筛空了'));
});

test('页面自己写的句子里不许有 markdown 星号', () => {
  const body = page.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(!body.includes('**'), '源码正文里有字面 ** —— 会被当正文渲染出来');
});

test('鱼塘那一维没被偷偷做进判据', () => {
  assert.ok(page.includes('plain(cal.no_fish_pond ?? "")'), '要写明这一维还没进母体');
  assert.ok(!/sector|板块热度.*(判据|门槛)/.test(page), '板块维度不该出现在这块的判据里');
});

test('块挂在选股汇总页下方，端点也登记进自检清单', () => {
  assert.ok(host.includes('<SystemEntryBoard />'), '选股汇总页没挂上这一块');
  const sys = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
  assert.ok(sys.includes('/quant/system-entry'), '自检清单少这一条接口');
  assert.ok(sys.includes('where: "/confluence"'), '两条新接口都要指回同一屏');
});

test('10-19 夜「描述收缩」自查：怎么点那句折进说明框，母体口径与截断告警留在外面', () => {
  const boxStart = page.indexOf('<ExplainBox title="这一张表怎么点');
  const boxEnd = page.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '交易体系榜要有默认收起的"怎么点"说明框');
  const box = page.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/);
  assert.doesNotMatch(box, /popHint|text-warning|text-destructive|<Caveats/);
  const before = page.slice(0, boxStart);
  assert.match(before, /<Row label="母体口径" tone="warn"/, '母体口径那行（含门槛数字，全部读接口）留在默认可见位置');
  assert.match(before, /已达取数上限/, '截断告警不许折');
  assert.match(before, /<CaliberFold caveats=\{d\.caveats\} definitions=\{foldDefs\}/,
    'caveat 与字段义交给共用折叠块，位置仍在框之前（缺数类由它渲染在 details 外）');
  assert.match(before, /const foldDefs = Object\.fromEntries\(\(d\.caliber_items \?\? \[\]\)/,
    '折叠块那份字典的头必须念中文标题（caliber_items），不念 mother/no_fish_pond 这种机器码');
  assert.ok(!/definitions=\{d\.calibers\}/.test(page),
    '把机器键字典直接当 definitions 传＝屏幕上会念出 mother、gap、stop 这种标识符');
  assert.match(page.slice(boxEnd, boxEnd + 400), /cell\.popHint && <p role="status"/,
    '弹窗被拦的告警留在说明框外面');
});
