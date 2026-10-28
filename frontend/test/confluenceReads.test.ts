import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 选股汇总那一屏（`pages/Confluence.tsx`）的读侧接线。
 *
 * 四件写错了不报错的事：
 *  ① **三份列序必须一致**：`COLS`（交叉高亮按它取列键）、`getCellClass(i, "键")` 的先后、
 *     `SortHeader` 的 `sortKey` 先后 —— 任一份漂了，点了列头不报错、只是那一列永不亮；
 *  ② sortKey 必须是那一行真有的字段（死键不排序，aria-sort 却照样亮）；
 *  ③ 筛条件要**发进接口**（`min_hits=`/`sources=`/`date=`）：前端筛只能筛已交出的那些行；
 *  ④ 空榜的措辞要分得清"筛空了"与"链没跑到这一天"，全场那几个数只能来自不带筛选的聚合。
 */

const page = readFileSync(new URL('../src/pages/Confluence.tsx', import.meta.url), 'utf8');

function ifaceKeys(src: string, name: string): string[] {
  const from = src.indexOf(`interface ${name}`);
  assert.ok(from >= 0, `找不到 interface ${name}`);
  // 收到"顶格的 }"为止：这些 interface 里有嵌套对象字段（`metrics?: Record<string, { … }>`），
  // 按第一个 `}` 截会把后面一半字段漏掉，于是"字段不存在"变成假红。
  const rest = src.slice(from);
  const body = rest.slice(0, rest.search(/\n\}/));
  return [...body.matchAll(/\b([a-z0-9_]+)\?:/g)].map(m => m[1]);
}

const COLS = /const COLS = \[([^\]]+)\]/.exec(page)![1]
  .split(',').map(s => s.trim().replace(/^"|"$/g, '')).filter(Boolean);

test('COLS 与每一格的列键、列头的 sortKey 是同一份顺序', () => {
  const cellKeys = [...page.matchAll(/getCellClass\(i, "([a-z_]+)"/g)].map(m => m[1]);
  const sortKeys = [...page.matchAll(/sortKey="([a-z_]+)"/g)].map(m => m[1]);
  assert.deepEqual(cellKeys, COLS, `列键顺序与 COLS 不一致：${cellKeys} vs ${COLS}`);
  assert.deepEqual(sortKeys, COLS, `列头顺序与 COLS 不一致：${sortKeys} vs ${COLS}`);
});

test('sortKey 都是行里真有的字段', () => {
  const keys = ifaceKeys(page, 'RowX');
  for (const k of COLS) assert.ok(keys.includes(k), `${k} 不在 RowX 里，点了不会排序`);
});

test('筛条件都发进接口而不是在前端筛', () => {
  // URL 是分两行拼的模板串，所以按"参数名出现在请求串里"认，不要求一整行匹配
  assert.match(page, /\?date=\$\{encodeURIComponent\(date\)}`/, '基准日要进接口');
  assert.match(page, /&min_hits=\$\{minHits\}&sources=\$\{encodeURIComponent\(picked\.join\(","\)\)\}/,
    '至少命中几个榜与交叉源多选都要进接口');
  // 榜内不许再 filter 行（前端筛只能筛到截断后的那些行）。勾源、拼 hover 那两处 `.filter(`
  // 是正当的，所以只钉"行的来源"那一句。
  assert.match(page, /const all = d\.rows \?\? \[\];/, '行必须整份来自后端');
  assert.doesNotMatch(page, /const all = [^\n]*\.filter\(/, '把全场行筛过一遍再交给表格');
  assert.doesNotMatch(page, /srt\.apply\([a-z]+\.filter/, '排序前先筛＝只排了已交出的那些行');
});

test('筛控件挂在 controls 上：筛空时下拉与日期框必须还在', () => {
  assert.match(page, /<Panel title="交叉命中榜" state=\{hits\} controls=\{controls\}/);
  const from = page.indexOf('const controls =');
  const body = page.slice(from, page.indexOf('return (', from));
  assert.ok(from > 0 && body.length > 200, '没截到 controls 那一段');
  for (const el of ['至少命中', 'type="date"', '回到默认', '交叉源']) {
    assert.ok(body.includes(el), `controls 里少了 ${el}`);
  }
});

test('全场的数只念后端那一份，空榜分两种说法', () => {
  const keys = ifaceKeys(page, 'Board');
  for (const f of ['day_total', 'day_repeat_total', 'hits_distribution', 'source_count', 'source_total']) {
    assert.ok(keys.includes(f), `Board 少了 ${f}（这些必须是不带筛选的全场聚合）`);
    assert.match(page, new RegExp(`d\\.${f}`), `页面没读 ${f}`);
  }
  assert.ok(!/小于 5/.test(page), '分母的满值写死成 5 —— 源清单变了这句话就成了假读数（现在 6 个）');
  assert.match(page, /没读到（那条聚合失败了）/, '聚合读不到要显式说读不到，不能拿空分布冒充"没有一只重复"');
  assert.match(page, /这一场没有票同时进过 \$\{minHits\} 个榜/, '筛空的措辞');
  assert.match(page, /汇总链没跑到这一天/, '空榜的另一条措辞');
});

test('每个源后面那个数是场内的，缺席的源单独点名', () => {
  assert.match(page, /o\.count/, '每源进了几只要来自后端');
  const keys = ifaceKeys(page, 'SourceOpt');
  for (const f of ['count', 'size', 'limit', 'meaning', 'board']) {
    assert.ok(keys.includes(f), `SourceOpt 少了 ${f}`);
  }
  assert.match(page, /sources_rejected/, '认不得的源键要点名，不能静默当"不筛源"');
});

test('页面自己写的句子里不许有裸星号', () => {
  // 后端散文一律走 Provenance/Caveats（它们内部过 plain），页面自写的句子不许带 **
  const own = [...page.matchAll(/>([^<>{}]*\*\*[^<>{}]*)</g)].map(m => m[1]);
  assert.deepEqual(own, [], `JSX 文本里还有裸星号：${own}`);
  // 用户 2026-10-21：「描述性内容全部收缩起来」——出处/字段含义/覆盖这一摞改走共用折叠块，
  // 缺数与截断那一族仍由 CaliberFold 自己留在屏上（分流只住 `lib/caveatKind.ts` 一处）。
  assert.match(page, /<CaliberFold caveats=\{d\.caveats\} definitions=\{d\.definitions\}/,
    '尾巴那一摞走共用折叠块（不再四件各摊一段）');
  assert.match(page, /coverage=\{d\.coverage\} provenance=\{\[d\.source, d\.caliber\]\}/,
    '覆盖与出处要一起交给折叠块，漏一项就等于那一件回到摊开状态');
});

test('选中一只票才取历史，没选中时不发请求', () => {
  assert.match(page, /const hist = useProbe<Hist>\(/);
  assert.match(page, /nonce, undefined, !focus/, '未选中时 skip=true，不发一次注定查不到任何东西的请求');
  assert.match(page, /repeat_days/, '时间维度（历史上哪些天重复出现过）必须在场');
});

/* —— 用户 2026-10-14：点一行要在下面展开这只票的针对性全面分析，而且必须复用「分组自选」
      那一份档案组件，不许在本屏再写一份。写错了不报错的四件事：
   ① 本屏自己打 `/quant/stock-dossier` 或抄一份八块读数 —— 界面就会分叉，改一处只亮一屏；
   ② 行序排两次（表格一份、档案一份）—— 换排序后高亮的行与档案说的不是同一只票；
   ③ 档案吃的对象与历史那发请求吃的对象不是同一个变量；
   ④ 换排法/换筛条件/重新取数没取消选中 —— 行号一漂就张冠李戴。 */
test('档案用的是共用那一份，本屏不自己打档案接口', () => {
  assert.match(page, /import \{ StockDossier \} from "@\/components\/quant\/StockDossier"/,
    '没有复用 components/quant/StockDossier 那份实现');
  assert.ok(page.includes('<StockDossier code={focus} date={date} />'),
    '档案的对象必须是点中的那一行，日期必须是这一场基准日（不是今天）');
  // 本屏自己只发两发（榜 + 这只票的重复出现史）；档案那一发住在共用组件里。
  assert.equal((page.match(/useProbe</g) ?? []).length, 2,
    '页面里多发了一发取数 —— 档案的请求必须由 StockDossier 自己发，两处各打就会各拿一份');
  assert.doesNotMatch(page, /useProbe<[^>]*>\(\s*["`?]\/quant\/stock-dossier/,
    '本屏不许自己再打一次档案接口');
  assert.doesNotMatch(page, /<Quadrant |<VolumeDouble |<Ambush |<Breakout /,
    '八块读数的子组件在共用实现里，本屏不许各抄一份');
});

test('行序只算一份，档案与历史吃同一个 focus', () => {
  assert.equal((page.match(/srt\.apply\(/g) ?? []).length, 1,
    'srt.apply 出现多处就是各排一次——高亮的行与下面分析的票会错开');
  assert.match(page, /const shown = srt\.apply\(cur\?\.rows \?\? \[\]\);/);
  // 10-26：档案＋历史那两块搬进 `FocusPane`（选中态的订阅圈在这两块里，一次单击不再重排整张表）。
  // 「对象只能由选中行号在那一份排好的数组里取」这条一个字没松，只是取的位置换了：
  assert.match(page, /const focus = sel \? codeAt\(sel\.row\) : null/,
    'focus 必须由选中行号在行序里取，不能再自己 setFocus');
  assert.ok(page.includes('codeAt={(row) => shown[row]?.code ?? null}'),
    '行序必须由页面交进来（读的就是表格渲染的那一份 shown），组件里不许再排一次');
  assert.ok(page.includes('<FocusPane store={cell.store}'),
    '订阅选中态的是 FocusPane，不是页面组件本体');
  assert.match(page, /\{shown\.map\(\(r, i\) => \(/, '表格体必须渲染同一份');
  assert.doesNotMatch(page, /const \[sel, setSel\]|setFocus|setSel\(/,
    '旧的 sel/focus 两份状态是重复实现（高亮与档案对象各记一遍），已收进共享 hook');
  assert.doesNotMatch(page.split('function FocusPane')[0] + '§', /useCellSelection\(/,
    'useCellSelection 只许出现在 FocusPane 里 —— 提到页面组件就等于把单击又接回整张表的重渲染');
});

test('换排法、换筛条件、重新取数都取消选中', () => {
  // 10-15 起这一屏的交互走全站那一份 `lib/useStockRowInteraction.ts`，清选中的手叫 `clearAll()`
  // （它连"弹窗被拦"那句告警一起清）。断言跟着改指向，**不是放宽**：该清的地方一处都没减。
  assert.match(page, /const pickSort = \(key: string\) => \{ cell\.clearAll\(\); srt\.toggle\(key\); \};/);
  assert.equal((page.match(/onSort=\{pickSort\}/g) ?? []).length, 8, '列头没都走带取消选中的排序');
  assert.doesNotMatch(page, /onSort=\{srt\.toggle\}/, '漏一个列头就会留着不清选中');
  assert.match(page, /setMinHits\(Number\(e\.target\.value\)\); cell\.clearAll\(\)/);
  assert.match(page, /cell\.clearAll\(\); refresh\(\)/);
  assert.match(page, /setPicked\(\[\]\); setDate\(""\); setMinHits\(2\); cell\.clearAll\(\)/,
    '「回到默认」换了整张榜，选中行号必须一起清');
  assert.match(page, /setPicked\(p => \(p\.includes\(k\)[\s\S]{0,80}?cell\.clearAll\(\);/,
    '勾源会在库里换掉行集，选中行号也要清');
});

test('10-19 夜「描述收缩」自查：怎么点那句折进说明框，全场数/真筛声明与截断告警留在外面', () => {
  // 用户 2026-10-19：「把选项留出，描述内容都收缩起来」。这一屏控件下面那句
  // 「源后面那个数与下面的命中分布都是全场的…是真筛，不是在截断后的名单里挑」属于截断/口径声明，
  // 按红线**不折**；本轮把表下的 STOCK_ROW_HINT 收进默认折叠的说明框，告警仍留在框外。
  const boxStart = page.indexOf('<ExplainBox title="这一张表怎么点');
  const boxEnd = page.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '交叉命中榜要有默认收起的"怎么点"说明框');
  const box = page.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/);
  assert.doesNotMatch(box, /popHint|text-warning|text-destructive|<Caveats/);
  const before = page.slice(0, boxStart);
  assert.match(before, /都是全场的（那条聚合不带任何筛条件）/, '全场数与"真筛"声明渲染在框之前＝默认可见');
  assert.match(before, /已达取数上限/, '截断告警不许折');
  assert.match(before, /<CaliberFold caveats=\{d\.caveats\}/,
    'caveat 交给共用折叠块的位置在框之前；缺数／截断那几族由它渲染在屏上（分流见 lib/caveatKind）');
  assert.match(page.slice(boxEnd, boxEnd + 400), /cell\.popHint && <p role="status"/,
    '弹窗被拦的告警留在说明框外面');
});
