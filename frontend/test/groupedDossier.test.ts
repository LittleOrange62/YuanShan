import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/**
 * 分组自选：选中明细里的个股之后，行列一起加亮，并把这只票在「量化选股」各屏的读数
 * 拼到下面那一叠（用户 2026-10-13 原话）。
 *
 * 钉的是五件**写错了也不会报错**的事：
 *  ① 高亮按行号记，所以「表格渲染的那份行序」与「档案取哪只票」必须来自同一个数组 ——
 *     两处各排一次的话，换排序后高亮的行与下面说的就不是同一只票；
 *  ② 换排序、按「重新取数」都要取消选中（行号一漂就张冠李戴）；
 *  ③ td 的列键必须是那一行真有的字段，否则交叉高亮里那一列永远不亮（不报错）；
 *  ④ 没选中任何票时**根本不发请求**（`skip`），不发一次注定 422 的空参数请求；
 *  ⑤ 中文标签只念后端交出的那一份（`capability_label`/`quadrant_label`/`ambush_type_labels`/
 *     `signal_type_label`），前端抄一张映射表就会与列注释一起走偏。
 */

const page = readFileSync(new URL('../src/pages/Grouped.tsx', import.meta.url), 'utf8');
// 10-15：档案那一叠与「选一只票」整体搬进「个股深度复盘」，涉及它们的断言改读这一份
// （**改读取路径，不删断言** —— 判据还在，只是宿主换了）。
const review = readFileSync(new URL('../src/pages/StockReview.tsx', import.meta.url), 'utf8');
const dossier = readFileSync(new URL('../src/components/quant/StockDossier.tsx', import.meta.url), 'utf8');

function ifaceKeys(src: string, name: string): string[] {
  const from = src.indexOf(`interface ${name}`);
  assert.ok(from >= 0, `找不到 interface ${name}`);
  // 收到"顶格的 }"为止：interface 里有嵌套对象字段时，按第一个 } 截会漏掉后半截字段
  const rest = src.slice(from);
  const body = rest.slice(0, rest.indexOf('\n}'));
  return [...body.matchAll(/\b([a-z0-9_]+)\?:/g)].map(m => m[1]);
}

test('明细表接上了共享的行列交叉高亮 hook', () => {
  // 10-15 用户点名「单击高亮行和列、双击跳深度复盘 这个也应该是全软件通用的一块，但凡有用到的地方都应该引用它」
  // ⇒ 这一屏不再单独引 `useTableCellHighlight`，改引把高亮＋双击＋告警合成一份的那块（`lib/useStockRowInteraction.ts`）。
  // 断言跟着改指向，强度不降：仍然钉"走共享实现、页面不另造状态"。
  assert.match(page, /import \{[^}]*\buseStockRowInteraction\b[^}]*\} from "@\/lib\/useStockRowInteraction"/);
  assert.doesNotMatch(page, /from "@\/lib\/useTableInteraction"/,
    '低一层那份 hook 只给共用块自己用；页面直接引它就是第二份接线');
  // 10-15 档案搬去「个股深度复盘」之后，"点行要顺手清掉手输那只"这层包壳没有对象了（手输框跟着搬走），
  // 所以 `cell` 直接就是 hook 那一份 —— 这条断言跟着改成新形状，**不是放宽**：
  // 它钉的还是"高亮走共享 hook、不另造一份"。
  assert.match(page, /const cell = useStockRowInteraction\("分组自选"\)/, '高亮必须用共享 hook 那一份，不许页面另造');
  assert.match(page, /cell\.getRowClass\(i/, '整行要跟着亮');
  assert.match(page, /cell\.handleCellClick\(e\.currentTarget, i, k\)/, '点格＝选中这一行这一列（10-26 起把被点那一格自己交出去）');
  // 浏览器实测抓到过一次"只接了点击、没接列高亮"：点了以后整行亮、那一列不亮，
  // 而屏幕上没有任何报错 —— 所以这里钉 Td 必须把 getCellClass 用在 className 上。
  const td = page.slice(page.indexOf('function Td('), page.indexOf('type TrendRow = GroupStat'));
  assert.ok(td.length > 60, 'Td 那段切不出来（边界漂了就去核对函数名）');
  assert.match(td, /className=\{cell\.getCellClass\(i, k,/, 'Td 自己要把列键交给共享 hook');
});

test('td 的列键都是行里真有的字段（死键不会报错，只是那一列永远不亮）', () => {
  const keys = ifaceKeys(page, 'StockRow');
  const used = [...page.matchAll(/<Td[^>]*\s+k="([a-z0-9_]+)"/g)].map(m => m[1]);
  assert.ok(used.length >= 7, `字面量列键太少：${used}`);
  assert.match(page, /<Td key=\{k\} i=\{i\} k=\{String\(k\)\}/,
    'COLUMNS 那一组是 map 出来的，键要跟着 COLUMNS 走');
  for (const k of (page.match(/\["([a-z0-9_]+)", "[^"]*", "[^"]*"\]/g) ?? [])) {
    const key = /\["([a-z0-9_]+)"/.exec(k)![1];
    if (key !== 'pct_change') assert.ok(keys.includes(key) || key === 'amp_pred', `COLUMNS 里的 ${key} 不是行字段`);
  }
  for (const k of used) {
    assert.ok(keys.includes(k), `列键 ${k} 不在 StockRow 里`);
  }
  // 备注那一列没有排序（表头是 <th>），但也要能点：列键不许漏
  assert.ok(used.includes('note'), '备注格没接选中');
});

test('高亮的行、双击带出去的那一只、与档案的对象来自同一份行序（只排一次、只截一次）', () => {
  assert.equal((page.match(/\.slice\(0, 150\)/g) ?? []).length, 1,
    '表格与 focusRow 必须读同一份截断结果，两处各 slice 一次就会错行');
  assert.match(page, /const shownRows = sortedRows\.slice\(0, 150\)/);
  // 10-26：「已经选中哪一只」那句提示搬进 `SelectedHint`（选中态的订阅圈在这一行里，
  // 一次单击不再把这张 150 行 × 十几列的明细表整个重排一遍）。
  // 「对象必须由选中行号读同一份截断结果」这条一个字没松，只是取的位置换了：
  assert.match(page, /<SelectedHint store=\{cell\.store\} rowAt=\{\(row\) => shownRows\[row\] \?\? null\} \/>/,
    '提示必须由订阅选中态的子组件渲染，并且读的就是表格那一份 shownRows');
  assert.match(page, /const row = sel \? rowAt\(sel\.row\) : null/,
    '选中行号只能在交进来的行序里取，不许组件自己再排一次');
  assert.doesNotMatch(page.split('function SelectedHint')[0], /useCellSelection\(/,
    'useCellSelection 只许出现在 SelectedHint 里 —— 提到页面组件等于把单击又接回整张表的重渲染');
  // 10-15 档案搬走了，这一屏与那只票唯一的交接动作是**双击**：带出去的代码必须是
  // 表里这一行渲染出来的那一只（`shownRows.map((r, i) => ...)` 的 r），不能是页面另猜一只。
  assert.match(page, /onDoubleClick=\{\(\) => openReview\(r\.security_code\)\}/,
    '双击带的必须是这一行的代码 —— 换一只行就开错一只');
  // 档案与趋势量化那两叠现在住在「个股深度复盘」：两处必须吃**同一个** code（地址里的 symbol）
  assert.match(review, /const code = query\.get\('symbol'\) \|\| ''/, '新页的唯一真源是地址里的 symbol');
  assert.ok(review.includes('<StockDossier code={code || null} date={undefined} />'),
    '档案的对象必须就是地址里那一只');
  assert.ok(review.includes('<TrendQuant code={code || null} />'),
    '趋势量化那一叠必须跟着同一只票');
  assert.doesNotMatch(review, /<StockDossier code=\{draft|<TrendQuant code=\{draft/,
    '两叠都不许吃正在敲的那半截文本（10-14 实测过：半截代码会立刻发请求并换掉分析对象）');
  assert.doesNotMatch(page, /rowSort\.apply\(d\.rows/, '表体不许再排一次');
});

test('选一只票这件事只在新页那一处：打字不写地址、没有默认票、屏上说清吃的是哪一只', () => {
  // 10-15 搬家：原来住在 /grouped 的那个「手输选股」框（连同它的 `ys.ui_pref` 记忆
  // `grouped.dossier_code`、"它在自选明细里出现 N 次"那一格）整体撤掉，改由新页顶部
  // 那一个共用 `StockSearch` 承担。**这不是放宽**：原来钉的三条判据换了宿主继续钉 ——
  //  ① 认格式：现在由后端 `duanxian/security_search.py` + `app/db/repo/security.py` 判（有真库用例），
  //     页面不再自己写一张"只有 6 位数字才算"的规矩；
  //  ② 与点行互斥：新页只有一个入口，地址里同一时刻只可能有一只；
  //  ③ 界面状态那一句「已记住」随手输框一起退场（没有写入方才不写；那个键在后端仍登记着，
  //     但本页不再往库里写"上次选的那一只"，因为它与新页"不带传参就不替你挑"直接冲突）。
  assert.match(review, /import \{ StockSearch \} from ['"]@\/components\/ui\/StockSearch['"]/, '新页要挂共用那只控件');
  assert.match(review, /const \[draft, setDraft\] = useState\(code\)/, '打字的内容只留调用方一份');
  assert.match(review, /useEffect\(\(\) => \{ setDraft\(code\); \}, \[code\]\)/, '地址变了要带回框里，别留两份');
  assert.match(review, /if \(v\) next\.set\('symbol', v\)/, '只在选中/提交那一刻写地址');
  assert.doesNotMatch(review, /onType=\{[^}]*commit/, '每敲一个字就换地址 = 拿半截代码去出网取数');
  // 不默认给一只票（悄悄换一只等于让用户看一堆他没选过的数）
  assert.doesNotMatch(review, /setDraft\("\d{6}"\)|set\(Symbol\("symbol", "\d{6}"\)\)/, '不许写死一只默认票');
  assert.match(review, /还没选一只，这一页不替你挑/, '空态要明说这一页不挑');
  assert.match(review, /现在复盘的是哪一只/, '屏上必须说清现在吃的是哪一只');
  assert.match(review, /from=\{encodeURIComponent\("分组自选"\)\}|query\.get\('from'\)/,
    '双击带过来的来源要能说出来（否则人不知道这一只是从哪一叠开的）');
});

test('组级分析整块搬到了本页最上方，而且一份请求读两处', () => {
  // 位置：`space-y-4` 里第一个子元素就是那块表 —— 用户要的是"先看分组整体，再看组里的票"
  assert.match(page, /<GroupAnalyticsBoard got=\{trend\} \/>/);
  assert.ok(page.indexOf('<GroupAnalyticsBoard') < page.indexOf('<Panel title="分组"'),
    '组级分析没排在最上方');
  // 一份请求：页面自己只发这一发 group-analytics，卡片那 6 格与顶部那块读同一个 DataState
  assert.equal((page.match(/"\/watchlist\/group-analytics"/g) ?? []).length, 1,
    '同一接口在页面里出现两处 —— 那就会各聚合一发（每次进页多等 8 秒）');
  assert.match(page, /const trend = useProbe<Analytics>\("\/watchlist\/group-analytics", nonce\)/);
  assert.doesNotMatch(page, /await probe<Trend|loadTrend|setTrendBusy/,
    '那个手动聚合按钮（`probe()` 手一发）已经删了，不许再长回来');
  assert.doesNotMatch(page, /watchlist-analytics/, '通往已撤屏的链接一处不许剩');
  // 卡片那 6 格默认就显示（吃 trend.kind === "ok"，不再看某个"点过按钮没有"的标志）
  assert.match(page, /\{trend\.kind === "ok" && \(\(\) => \{/);
});

test('换排序与重新取数都取消选中', () => {
  // 同上：10-15 起这份清选中的手是共用 hook 的 `clearAll()`（连弹窗告警一起清），断言跟着指过去。
  assert.match(page, /const pickSort = \(key: string\) => \{ cell\.clearAll\(\); rowSort\.toggle\(key\); \}/);
  const heads = [...page.matchAll(/onSort=\{pickSort\}/g)].length;
  // COLUMNS 那一组是 map 出来的，源码里只有一处字面量，运行时覆盖 8 个列头
  assert.ok(heads >= 7, `列头没都走取消选中的排序：${heads} 处`);
  assert.doesNotMatch(page, /onSort=\{rowSort\.toggle\}/, '漏一个列头就会留着不清选中');
  assert.match(page, /onClick=\{\(\) => \{ cell\.clearAll\(\); refresh\(\); \}\}/);
});

test('档案没选中时不发请求，选中后按代码与行情日取数', () => {
  assert.match(dossier, /useProbe<Dossier>\("\/quant\/stock-dossier", nonce,/);
  assert.match(dossier, /code \? \{ code, date: date \?\? "" \} : undefined, !code/);
  assert.doesNotMatch(dossier, /fetch\(|axios/, '取数只走 probe，不自己发第二套');
});

test('页面一个判据都不自己算：标签与名次全念后端那一份', () => {
  for (const f of ['capability_label', 'quadrant_label', 'ambush_type_labels',
    'signal_type_label', 'hit_labels', 'sources_absent_labels']) {
    assert.match(dossier, new RegExp(f), `后端交出的 ${f} 没被用上`);
  }
  // 前端抄一份中文映射表就会与库里的注释一起走偏（本项目已有 TYPE_LABELS 单源规矩）
  assert.doesNotMatch(dossier, /const (TYPE_LABELS|STATUS_LABELS|CAP_LABELS)\s*=\s*\{/,
    '前端不许另起一张标签表');
  assert.doesNotMatch(dossier, /oper_score.*0\.40.*0\.60.*=/s, '公式只在悬停里复述，不自己重算');
});

test('后端散文字段上屏都过 plain 闸（裸星号不进屏幕）', () => {
  assert.match(dossier, /plain\(blk\?\.reason/, '缺席说明要过闸');
  assert.match(dossier, /plain\(x\.reason/, '整份不可用的说明要过闸');
  assert.match(dossier, /plain\(c\.capability_note\)/, '注册表说明要过闸');
  assert.match(dossier, /const tip = /, '悬停提示得有同一道闸');
  assert.match(dossier, /provenance=\{\[x\.source, x\.caliber\]\}/,
    '口径出处交给 CaliberFold（它内部用的还是那个过 plain 的 Provenance）');
  // 任何一处把后端散文原样塞进 title= 就会把 ** 印进气泡
  const risky = [...dossier.matchAll(/title=\{([^{}]*(?:\{[^{}]*\})?[^{}]*)\}/g)]
    .map(m => m[1])
    .filter(t => /\b(meaning|formula|caliber|note|reason|tables)\b/.test(t))
    .filter(t => !/\b(tip|plain)\(/.test(t));
  assert.deepEqual(risky, [], `这些提示没过闸：${risky.join(' | ')}`);
});

test('缺席分三种念法在界面上是分开的', () => {
  assert.match(dossier, /STATUS_TONE\[st\]/);
  for (const k of ['read_failed', 'not_computed', 'not_on_board']) {
    assert.ok(dossier.includes(`${k}:`), `少了 ${k} 那一档的措辞`);
  }
  // 不可用的块必须把原文带出来，不能只留一句「没有数据」
  assert.match(dossier, /plain\(blk\?\.reason/);
});

test('每块自己标注是哪一天的数（各链进度不同，混着看会读成同一天）', () => {
  assert.match(dossier, /数出自 \{dash\(blk\.as_of\)\}/);
  assert.match(dossier, /这一榜全场算到 \{dash\(d\.board_latest\)\}/,
    '埋伏那一链回算中：最后一次进榜与全场最近一场必须一起说');
});

test('「分组」那一叠：默认按当日涨幅从低到高，另可切组名/成员数等指标，缺数一律沉底', () => {
  // 用户 2026-10-14：默认还是"跌得最多的在第一行第一个"，但要能按组名、成员数量这类指标排。
  // 卡片是 `grid sm:grid-cols-2`，所以数组序就是"第一行第一个→第一行第二个→第二行…"的填法。
  assert.match(page, /sortGroups\(d\.groups \?\? \[\], gso, trendByGroup\)\.map/,
    '分组那一叠没走排序函数（还是库里 display_order 的序）');
  assert.match(page, /const \[groupSort, setGroupSort\] = useState\("avg_pct:asc"\)/,
    '默认排法必须是当日涨跌从低到高');
  assert.match(page, /GROUP_SORTS\.find\(o => o\.v === groupSort\) \?\? GROUP_SORTS\[0\]/,
    '排法取不到时要退回默认，不能静默变成"不排序"');
  const table = /const GROUP_SORTS: GroupSortOpt\[\] = \[[\s\S]*?\n\];/.exec(page)?.[0] ?? '';
  assert.ok(table.length > 0, '找不到 GROUP_SORTS');
  for (const [needle, why] of [
    [`v: 'avg_pct:asc'`, '缺"当日涨跌 从低到高"'],
    [`v: 'name:asc'`, '缺"组名"'],
    [`v: 'stock_count:desc'`, '缺"成员数 从多到少"'],
    [`v: 'display_order:asc'`, '缺"库里的原顺序"（回到同花顺同步序的出口）'],
    [`v: 'chg10_bp:asc'`, '缺"近 10 日日均涨跌"'],
  ] as const) assert.ok(table.includes(needle), why);
  assert.match(page, /const raw = o\.key === 'name' \? g\.name/, '组名那把尺子没读 g.name');
  const sorter = page.slice(page.indexOf('function groupValue'), page.indexOf('export function Grouped'));
  assert.ok(sorter.length > 60 && sorter.includes('function sortGroups'), '找不到 groupValue / sortGroups');
  assert.doesNotMatch(sorter, /\?\? 0/, '取数器里把缺值补成 0 参与排名 —— 方向是反的');
  assert.match(sorter, /x\.v == null && y\.v == null \? sameRank/, '两边都缺时不该随机定序');
  assert.match(page, /x\.v == null \? 1 : -1/, '缺数的那一组要沉底');
  assert.match(page, /return c \* o\.dir \|\| sameRank/, '方向只作用于主键，同值交给稳定序兜');
  // 要现场聚合才有数的选项，在顶上那块表还没出数时是 disabled（选了排出一片空比看不见更糟）
  assert.match(page, /disabled=\{o\.key === "trend" && !trendReady\}/);
  assert.match(page, /这一列的数由页面最上方「分组整体分析」现场聚合而来/,
    '禁用理由要说清是哪一块在算、要等多久，不能只写"不可用"');
  assert.match(page, /（等上方聚合出数）/, '选项本身要带上当前的状态');
  assert.doesNotMatch(page, /看各组趋势与打分|loadTrend/, '那个手动聚合按钮已经删了，措辞不许留在禁用提示里');
  // 屏上要说清现在吃的是哪一把尺（换一天、换一把尺都得看得出来）
  assert.match(page, /label="这一叠的顺序"/);
  assert.match(page, /value=\{`\$\{gso\.desc\}；缺这个读数的组排在最后/);
});
