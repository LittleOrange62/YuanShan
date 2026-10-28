import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { sortRows } from '../src/lib/useSort.ts';

/**
 * 倍量形态页那块「加权分进榜后的逐日涨跌幅」（`components/quant/EnergyTrackBoard.tsx`，
 * 端点 `GET /api/quant/energy-track`，2026-10-19 新增）。
 *
 * ⚠️ 口径改于 2026-10-19 第二稿：母体从「加权分 ≥ 门槛（出厂 0.8）」换成**当日前 N 名**
 * （`top_n`，出厂 `top_n_default`＝120，只住后端一处）。旧那四条断言（参数名 min_energy、
 * 回显 threshold_default、界面不许出现 0.8、端点 `min_energy: float = Query(None, ge=0, le=40)`）
 * 逐条换成钉 top_n／top_n_default／`top_n: int | None = Query(None, ge=10, le=500)`，
 * 并把「N 是上限、边界同分并列一起进来（只数可以多于 N）」这条新语义钉进文案。
 *
 * 这张表最容易被写成假的五件事，各有各的钉法：
 *  ① **列数＝接口 `max_series_len`**，不是页面自己数的、也不是日历日数；
 *  ② **空格子是 —，不是 0%**：这一场没行、涨跌幅读不到、整体涨跌幅没有基期，三样都不许画成 0.00%
 *     （0% 是「一分钱没动」，是一条与「没数」相反的结论）；真 0.00% 又必须照常显示出来；
 *  ③ **每页行数读接口 `page_size`、取数不限行**：接口没有 limit 参数，交多少排多少；
 *  ④ **列头 sortKey 必须等于那一格真读的字段**（写错不报错、也不换序 —— 本项目踩过 sortKey=sector）；
 *     默认顺序＝后端给的顺序（整体涨跌幅从低到高），点列头才动；
 *  ⑤ 中文串不嵌 ASCII 双引号、渲染文本没有裸 `**`（项目红线）。
 * 另外把「不许长出第二份行交互实现」也钉住：那个 hook 实例是页面交进来的 prop，
 * 本块自己**不调用** `useStockRowInteraction`（调用点台账 §六 因此不需要改）。
 */

const board = readFileSync(new URL('../src/components/quant/EnergyTrackBoard.tsx', import.meta.url), 'utf8');
const page = readFileSync(new URL('../src/pages/VolumeDouble.tsx', import.meta.url), 'utf8');
const vrApp = readFileSync(new URL('../../vr/app.py', import.meta.url), 'utf8');
const quantPy = readFileSync(new URL('../../duanxian/quant.py', import.meta.url), 'utf8');

test('取数：走 energy-track 端点，参数是 date、top_n 与重取用的 refresh，没有 limit', () => {
  assert.match(board, /`\/quant\/energy-track\?date=\$\{encodeURIComponent\(date\)\}`/,
    '基准交易日要跟着上面那张榜那一格走');
  assert.match(board, /&top_n=\$\{encodeURIComponent\(String\(topN\)\)\}/,
    '母体参数名必须与端点一致（第二稿起这个端点上没有 min_energy 那一族）');
  assert.doesNotMatch(board, /limit=/, '接口没有取数上限参数：这块一次交整批，分页在前端');
  assert.doesNotMatch(board, /min_energy/, '第一稿那个分数门槛参数不许复活（分值一改门槛就漂）');
  assert.match(board, /const \[topN, setTopN\] = useState<number \| null>\(null\)/,
    '默认不给参数＝吃后端默认，界面不自己填一个数');
  assert.match(board, /track\.data\.top_n_default/, '输入框显示的出厂档要读接口回显的那一个');
  const noComments = board.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  assert.doesNotMatch(noComments, /&top_n=120|top_n:\s*120|useState<number \| null>\(120\)/,
    '界面不许写死 120（出厂值只住 volume_energy.DEFAULT_TOP_N 一处）');
  assert.doesNotMatch(noComments, /0\.8/, '界面也不许留着第一稿那个门槛 0.8（它随÷Σ 那把尺一起作废了）');
  // 端点那侧的边界与默认
  assert.match(vrApp, /@app\.get\("\/api\/quant\/energy-track"\)/);
  assert.match(vrApp, /top_n: int \| None = Query\(\s*None, ge=10, le=500/);
  assert.match(vrApp, /refresh: bool = Query\(\s*False,/);
  assert.match(vrApp, /quant\.energy_track\(date, top_n=top_n, refresh=refresh\)/);
  assert.match(quantPy, /def energy_track\(date: str = '', top_n: int \| None = None, refresh: bool = False\) -> dict:/);
  assert.match(quantPy, /if refresh:\s*\n\s*clear_agg_cache\('energy_track_'\)/,
    'refresh 清的是这一族自己的键，不许一锅端别的接口');
  // 按钮与参数接在同一件事上（10-26 这一发接口加了「场次＋配置指纹」的一小时进程内缓存）：
  // 不带 refresh=1 的「重取这一块」＝亮着却交回同一份，任务 #105 那一族在这里不许再犯一次。
  assert.match(board, /localNonce > 0 \? "&refresh=1" : ""/,
    '按过「重取这一块」就必须把 refresh=1 发出去');
});

test('母体文案：前 N 名是上限，边界同分并列一起进来（只数可以多于 N）', () => {
  assert.match(board, /当日前 \$\{dash\(d\.top_n\)\} 名/, '档位那一行念的是后端真应用的值（回显，不是界面 state）');
  assert.match(board, /N 是上限，不是恰好 N 只/);
  assert.match(board, /rows_capped === true/, '并列把只数撑过 N 这件事读接口的 rows_capped，不自己数');
  assert.match(board, /所以实际是 \$\{dash\(d\.rows_returned\)\} 只/, '给出现在到底几只');
  assert.match(board, /只数可以比 N 多几只/, '控件悬停里那句"可以多于 N"要在');
  assert.doesNotMatch(board, /母体＝基准日这一场加权分 ≥ 门槛|加权分≥门槛/,
    '第一稿那句"≥门槛"的母体说法不许在文案里复活');
});

test('列数＝接口 max_series_len；列头念第 0 场与 +k 场', () => {
  assert.match(board, /const cols = typeof d\.max_series_len === "number" && d\.max_series_len > 0 \? Math\.floor\(d\.max_series_len\) : 0/,
    '列数只能读 max_series_len；没读到就一列都不开（不自己猜跨度）');
  // 表头与表体用的是同一个 cols —— 两处都要在
  const uses = board.match(/Array\.from\(\{ length: cols \}/g) ?? [];
  assert.equal(uses.length, 2, `开列的地方应当是表头＋表体各一处，实到 ${uses.length} 处`);
  assert.match(board, /\{k === 0 \? "第 0 场·起算日" : `\+\$\{k\} 场`\}/,
    '第 0 场是起算日当天，其余念 +k 场');
  // 「同一列不是同一个日历日」与每格日期：这句不许折进默认收起的说明框
  const beforeBox = board.slice(0, board.indexOf('<ExplainBox'));
  assert.match(beforeBox, /同一列在不同票身上不是同一个日历日/);
  assert.match(beforeBox, /不晚于/, '截断声明在框外');
  assert.match(board, /这是该票起算日后第 \$\{k\} 场（不同票同一列不是同一个日历日）/,
    '每一格悬停给日期与第几场');
  assert.match(board, /String\(cell\?\.trade_date/, '日期读这一格自己的 trade_date');
});

test('空格子是 —、不是 0%；真 0 照常显示（缺数与读数两种长相）', () => {
  const body = board.slice(board.indexOf('<tbody>'), board.indexOf('</tbody>'));
  // ① 这一格没有行 / 涨跌幅读不到 → —
  assert.match(body, /\{has[\s\S]{0,240}<span className="text-muted-foreground\/50">—<\/span>/);
  assert.match(body, /const has = typeof p === "number" && Number\.isFinite\(p\)/,
    '只有 finite 的数才算读数；null／NaN 走 —，不参与颜色判断');
  assert.match(body, /这一天的涨跌幅读不到（不是 0%、也不是平盘）/);
  assert.match(body, /不是 0%/, '缺那一场的行也要说明不是 0%');
  // ② 整体涨跌幅：null → —，并说清「没有基期就没有整体涨跌幅」
  assert.match(body, /cumMissing[\s\S]{0,320}<span className="text-muted-foreground\/50">—<\/span>/);
  assert.match(body, /没有基期就没有整体涨跌幅/);
  assert.match(body, /不是 0%（0% 是一分钱没动）/);
  // ③ 加权分缺数 → —，不当 0 分
  assert.match(body, /r\.dbl_energy == null[\s\S]{0,240}<span className="text-muted-foreground\/50">—<\/span>/);
  assert.match(body, /不是 0 分/);
  // ④ 真 0 仍然是 0：后端给的 0 值原样画（不许被 == null 那一支吞掉）
  const zero = [{ chg_cum: 0 }, { chg_cum: null }, { chg_cum: -3 }];
  assert.deepEqual(sortRows(zero, { key: 'chg_cum', dir: 'asc' }).map(r => r.chg_cum), [-3, 0, null],
    'chg_cum 为 0 是读数、要参与排序（排在 −3 之后）；null 恒在最后');
  assert.deepEqual(sortRows(zero, { key: 'chg_cum', dir: 'desc' }).map(r => r.chg_cum), [0, -3, null],
    '反向也不许把没基期的那批顶到最前（顶上去会被读成「跌得最少」）');
  assert.doesNotMatch(board, /chg_cum\s*\?\?\s*0/, '?? 0 会把「没有基期」说成「一分钱没动」');
  assert.doesNotMatch(board, /dbl_energy\s*\?\?\s*0/, '同上：0 分是「这 40 天一次量都没放过」');
  // ⑤ 起算日缺失也是 —
  assert.match(body, /找不到首次进榜日/);
});

test('每页行数读接口 page_size、整批排完再切页；默认顺序就是后端给的', () => {
  assert.match(board, /const PAGE = Number\.isFinite\(d\.page_size \?\? NaN\)/,
    '每页行数读接口（第二稿起 page_size 随体交出，界面不写死 60）');
  assert.match(board, /const PAGE_FALLBACK = 60/, '兜底那个数要有名字、只准在接口没交时用');
  assert.match(board, /每页 \$\{PAGE\} 行读 page_size/, '母体那一行要说清每页行数是接口给的');
  assert.match(board, /const sort = useTableSort\(\);/, '不给初始键＝不动后端行序（后端那批已按整体涨跌幅从低到高）');
  assert.match(board, /const sorted = sort\.apply\(all\);/, '整批排完才切页');
  assert.match(board, /shown = sorted\.slice\(\(cur - 1\) \* PAGE, cur \* PAGE\)/);
  assert.match(board, /gi: \(cur - 1\) \* PAGE \+ i/, '行号按整批序号记：页内下标会让第 1 页跟着亮');
  assert.match(board, /每页 \{PAGE\} 行，现在是第 \{cur\}\/\{pages\} 页/);
  assert.match(board, /不点列头就是后端给的顺序（整体涨跌幅从低到高）/);
  assert.doesNotMatch(board, /\.slice\(0, 60\)/, '不许静默截到 60 行');
  // 后端那侧确实按 chg_cum 从低到高排好，且 null 不参与比较
  assert.match(quantPy, /rows\.sort\(key=lambda x: \(x\['chg_cum'\] is None,/);
});

test('列头 sortKey 与那一格真读的字段同名（点下去要真的换序）', () => {
  const head = board.slice(board.indexOf('<thead>'), board.indexOf('</thead>'));
  const sortKeys = [...head.matchAll(/sortKey="([a-z_]+)"/g)].map(m => m[1]);
  assert.deepEqual(sortKeys,
    ['code', 'name', 'board', 'dbl_energy', 'rank_in_day', 'first_date', 'sessions_since', 'chg_cum'],
    `固定列那几列的键（第二稿加了今日名次）：${sortKeys.join('、')}`);
  const body = board.slice(board.indexOf('<tbody>'), board.indexOf('</tbody>'));
  const cellKeys = [...body.matchAll(/cellProps\(gi, "([a-z_]+)"/g)].map(m => m[1]);
  assert.deepEqual(cellKeys, sortKeys, '格子顺序与列头顺序分叉了（点了会排到另一列身上）');
  for (const k of sortKeys) {
    assert.match(board, new RegExp(`\\b${k}\\?:`, 'm'), `${k} 要在 TrackRow 上登记过，否则点了不换序`);
  }
  // 逐日那些列是**位置**不是标量字段，老实做普通表头（按它们排序没有对应的行字段）
  assert.match(head, /<th key=\{k\} className="whitespace-nowrap/);
  assert.doesNotMatch(head, /<th key=\{k\}[\s\S]{0,200}SortHeader/);
});

test('今日名次那一格与母体是两个时点：名次走公用件，规则那句话不在本块里', () => {
  assert.match(board, /label="今日名次" sortKey="rank_in_day"/);
  assert.match(board, /import \{ rankText, rankTitle, trimNum \} from "@\/lib\/ranking"/);
  assert.match(board, /rankText\(r\.rank_in_day\)/, '名次那一格念的是公用件给的文本（缺分 → —）');
  assert.match(board, /rankTitle\(plain\(d\.rank\?\.rule \?\? ""\)/, '规则原文读接口 rank.rule');
  assert.doesNotMatch(board, /同分同名|按位置顺延|不占号/, '本块不许自己写名次规则那句话');
  assert.doesNotMatch(board, /rank_in_day\s*\?\?\s*0/, '没有名次不许折成第 0 名');
  // 加权分那一列改为去尾零那把尺（第一稿是 toFixed(4)，屏幕上是 8.1200）
  assert.match(board, /trimNum\(r\.dbl_energy\)/);
  assert.doesNotMatch(board, /num\(r\.dbl_energy/, '加权分不许再走 toFixed 那把旧尺');
});

test('行交互沿用全站那一份：不调用第二个 hook、不自己拼双击地址', () => {
  assert.doesNotMatch(board, /= useStockRowInteraction\(/,
    '本块不许再调一次 hook（调用点台账 §六 按文件计数，得由页面把实例交进来）');
  assert.match(board, /vd: ReturnType<typeof useStockRowInteraction>/, '实例是 prop');
  assert.match(board, /vd\.rowProps\(gi, r\.code/, '单击行高亮＋双击开深度复盘共用一份');
  assert.match(board, /STOCK_ROW_HINT/, '怎么点这一张表那句话也只有一份');
  assert.match(board, /vd\.popHint/, '弹窗被拦的告警要单独念');
  assert.match(board, /<StockLink code=\{r\.code\} name=\{r\.name\}/, '股票名走那个新标签外链');
  assert.match(board, /\/stock-review\?symbol=/, '代码那一格进个股深度复盘');
  // 页面侧：第二份实例、传 date 与 nonce，并且这一块长在概念榜之后（不打断「上面那张倍量榜」那句指代）
  assert.match(page, /const track = useStockRowInteraction\("加权分进榜后走势"\)/);
  assert.match(page, /<EnergyTrackBoard nonce=\{nonce\} date=\{date\} vd=\{track\} \/>/);
  assert.ok(page.indexOf('<EnergyTrackBoard') > page.indexOf('<ConceptBoard'),
    '新块长在概念榜之后：概念榜那句「上面那张倍量榜交出的 N 只」的指代不能被打断');
  assert.ok(page.indexOf('<EnergyTrackBoard') < page.indexOf('<StartupVolumeBoard'),
    '这一块属于倍量榜那一族，要长在启动前量能画像之前');
});

test('说明：caveat 与 pct_rule 逐字读接口，口径不自己复述', () => {
  assert.match(board, /<CaliberFold caveats=\{d\.caveats\} definitions=\{d\.definitions\}/,
    'caveat 走共用出口（`CaliberFold` 把缺数/截断类留在屏上、只折纯口径解释；分流见 lib/caveatKind）');
  assert.match(board, /plain\(d\.pct_rule \?\?/, '复权口径读后端那份并剥掉成对粗体标记（那句原文里带 **）；页面不自己解释什么叫后复权');
  assert.doesNotMatch(board, /收盘价 × adjustment_factor/, '那句原文在后端 day_returns.RULE_NOTE 里，前端抄一份早晚走偏');
  assert.match(board, /<ExplainBox title="这一块怎么读 · 口径原文（后端给出，页面未改写）">/);
  // 0 行时不许把"筛空了"说成"库没数"：后端那句 reason 原样在 Panel 里念，这一句是补刀
  assert.match(board, /接口交出 0 行/);
  assert.match(board, /不是接口坏了/);
});

/** 用户 2026-10-19 夜：「把选项留出，描述内容都收缩起来」——
 *  控件行里那段"母体与上面那张榜是两回事"的描述（同一件事由后端 caveat energy_track_scope
 *  默认在屏上讲）与"怎么点"那句，一起收进默认收起的说明框；告警、截断（「不晚于」）、caveat 留在框外。 */
test('描述性长句折进说明框，告警与截断声明留在框外、控件行只剩控件', () => {
  const boxStart = board.indexOf('<ExplainBox');
  const boxEnd = board.indexOf('</ExplainBox>');
  assert.ok(boxStart > 0 && boxEnd > boxStart, '这一块要有说明框（默认收起的闸在 explainBox.test.ts）');
  const box = board.slice(boxStart, boxEnd);
  assert.match(box, /这一块的母体与上面那张榜的「最少加权分」是两回事/, '那句母体区分搬进框里，措辞原样');
  assert.match(box, /\{STOCK_ROW_HINT\}/, '"怎么点"那句折进框');
  assert.doesNotMatch(box, /同一列在不同票身上不是同一个日历日|不晚于|vd\.popHint|<Caveats/,
    '防误读声明、截断声明、告警、caveat 都不许在框内');
  const before = board.slice(0, boxStart);
  assert.match(before, /同一列在不同票身上不是同一个日历日/);
  assert.match(before, /不晚于/);
  assert.match(before, /vd\.popHint && <p role="status"/, 'popHint 渲染在框之前、默认可见');
  // 控件那一行里不再有那段长描述（只剩输入框与按钮；那句 <span> 已搬走）
  const controls = board.slice(board.indexOf('controls={'), board.indexOf('>', board.indexOf('重取这一块')));
  assert.doesNotMatch(controls, /是两回事/, '控件行长描述已收进说明框，控件本身仍永远默认可见');
});

test('渲染文本没有裸 **，中文串不嵌 ASCII 双引号', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  const code = strip(board);
  const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
  assert.deepEqual(hits, [], `这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
  assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
    '中文句子中间出现了 ASCII 双引号（要用「」）');
  for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? []) {
    assert.doesNotMatch(b, /"/, `JSX 文本节点里出现了 ASCII 双引号：${b}`);
  }
});

test('系统配置的自检清单登记了这个新端点，落点写倍量形态那一屏', () => {
  const system = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
  assert.match(system, /path: "\/quant\/energy-track"/, '端点跑了却没进自检清单＝没人验（这一族已犯过多次）');
  assert.match(system, /path: "\/quant\/energy-track", label: [^\n]*where: "\/volume-double"/);
  const line = (system.match(/path: "\/quant\/energy-track"[^\n]*/) ?? [''])[0];
  assert.doesNotMatch(line, /加权分≥门槛|min_energy/, '自检清单里还写着第一稿那把门槛');
});
