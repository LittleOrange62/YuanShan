import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { sortRows } from '../src/lib/useSort.ts';

/**
 * 历史回溯第 2 块「近期倍量表走势分析」（`components/quant/EnergyTrendBoard.tsx`，
 * 端点 `GET /api/quant/energy-trend`，2026-11-08 用户推翻上一版逐票热力表后重做）。
 *
 * 用户原话：「1-10名、11-20，每10个名次汇总分析，加权计算每10名后几天涨跌的趋势变化。」
 * 所以这张表的行是**名次档**、列是**往后第几场**，不再有逐票那一族列。
 *
 * 这一块最容易写成假话的六件事，各有各的钉法：
 *  ① **sortKey 必须等于那一格真读的字段**：描述符里的 `excess`/`step` 不是真实字段
 *     （真字段带尺子后缀 `excess_weighted`…），所以行先摊平成 `h1/h2/h3/h5/h10`，
 *     列头与格子读同一个键 —— 写错不报错也不换序，`aria-sort` 还会骗人；
 *  ② **名次档那一列不许做成可排序列**：它是「1-10」这样的区间文本，按字符串排会把
 *     101-110 排到 11-20 前面；
 *  ③ **缺数三态不许合并**：pending（还没走到）／missing（走到了读不到）／加权分母为 0
 *     各念各的，任何一类都不是 0%；真 0 又必须照常显示；
 *  ④ **基准那一行钉在最后不参与排序**，且它只有等权那一份 —— 选加权时整行是 —；
 *  ⑤ **列名与口径只从后端描述符取**（`columns`/`guide`），前端不抄中文名、不自己解释加权式；
 *  ⑥ 中文串不嵌 ASCII 双引号、渲染文本没有裸 `**`（项目红线）。
 *
 * 另外钉住「不静默无作用的控件」：上涨占比只有等权那一份口径，切到它时尺子那两个按钮
 * 必须 disabled 并说清为什么，而不是亮着却什么都不换。
 */

const board = readFileSync(new URL('../src/components/quant/EnergyTrendBoard.tsx', import.meta.url), 'utf8');
const page = readFileSync(new URL('../src/pages/StrategyBacktest.tsx', import.meta.url), 'utf8');
const vrApp = readFileSync(new URL('../../vr/app.py', import.meta.url), 'utf8');
const mod = readFileSync(new URL('../../duanxian/energy_trend.py', import.meta.url), 'utf8');
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');

test('取数：走 energy-trend 端点，参数是 date／window／top_n 与重取用的 refresh', () => {
  assert.match(board, /`\/quant\/energy-trend\?date=\$\{encodeURIComponent\(date\)\}`/,
    '基准日要真的进 query（留空＝倍量表里最新的一场）');
  assert.match(board, /windowN !== null \? `&window=\$\{encodeURIComponent\(String\(windowN\)\)\}` : ""/,
    '回看场数：没改过就不给参数，吃后端默认');
  assert.match(board, /topN !== null \? `&top_n=\$\{encodeURIComponent\(String\(topN\)\)\}` : ""/);
  assert.match(board, /nonce > 0 \? "&refresh=1" : ""/,
    '按过「重取这一块」就必须把 refresh=1 发出去，否则那颗按钮亮着却交回同一小时缓存');
  assert.doesNotMatch(board, /limit=/, '接口没有取数上限参数：一次交全部档位');
  assert.doesNotMatch(board, /energy-track|EnergyTrack|min_energy/,
    '上一版那个端点与第一稿那个分数门槛都不许复活');
  assert.match(board, /const \[windowN, setWindowN\] = useState<number \| null>\(null\)/);
  assert.match(board, /const \[topN, setTopN\] = useState<number \| null>\(null\)/);
  const code = strip(board);
  assert.doesNotMatch(code, /window=120|top_n=120|useState<number \| null>\(120\)/,
    '出厂值只住后端一处，界面不写死 120');
});

test('两个数字框的上下夹与默认显示都读接口回显，不在前端写第二份', () => {
  assert.match(board, /min=\{payload\?\.window_min \?\? undefined\} max=\{payload\?\.window_max \?\? undefined\}/);
  assert.match(board, /min=\{payload\?\.top_n_min \?\? undefined\} max=\{payload\?\.top_n_max \?\? undefined\}/);
  assert.match(board, /value=\{windowN \?\? payload\?\.window_default \?\? ""\}/);
  assert.match(board, /value=\{topN \?\? payload\?\.top_n_default \?\? ""\}/);
  assert.match(board, /const lo = payload\?\.window_min \?\? 1/);
  assert.match(board, /const hi = payload\?\.top_n_max \?\? n/);
  const code = strip(board);
  assert.doesNotMatch(code, /\b(250|500)\b/, '上下夹的真值只住后端常量，前端不许出现这两个数');
});

test('端点那侧：路由、上下夹与本模块常量同源、refresh 真的接到 board', () => {
  const seg = vrApp.slice(vrApp.indexOf('@app.get("/api/quant/energy-trend")'));
  const body = seg.slice(0, seg.indexOf('\n@app.'));
  assert.match(body, /def quant_energy_trend\(date: str = ""/);
  const c = (name: string) => Number((mod.match(new RegExp(`^${name} = (\\d+)`, 'm')) ?? [])[1]);
  assert.ok(body.includes(`ge=${c('MIN_WINDOW')}, le=${c('MAX_WINDOW')}`),
    '窗口上下夹写两份 ⇒ 改一处另一处静默 422（界面发 7／接口收 0.07 那一族）');
  assert.ok(body.includes(`ge=${c('MIN_TOP_N')}, le=${c('MAX_TOP_N')}`));
  assert.match(body, /refresh: bool = Query\(/);
  assert.match(body, /energy_trend\.board\(date, window=window, top_n=top_n, refresh=refresh\)/);
  assert.match(body, /近期倍量表走势分析取数异常/, '错误风格与同类判据端点一致（502 带原话）');
});

test('列名与口径只从后端描述符取，前端不抄中文名', () => {
  assert.match(board, /function descOf\(d: TrendPayload \| null, key: string\)/);
  assert.match(board, /return descOf\(d, key\)\?\.label \?\? "列名后端没交"/,
    '描述符没交就老实说没交，不自己编一个中文列名');
  const head = board.slice(board.indexOf('<thead>'), board.indexOf('</thead>'));
  assert.match(head, /\{labelOf\(d, "label"\)\}/);
  assert.match(head, /label=\{labelOf\(d, "events"\)\}/);
  assert.match(head, /label=\{labelOf\(d, "codes"\)\}/);
  for (const name of ['样本笔数', '独立票数', '加权涨跌幅', '等权涨跌幅', '上涨占比', '超额', '每场增量'])
    assert.ok(!head.includes(name), `表头里抄了后端描述符的中文名「${name}」——改口径时两处会分叉`);
  assert.match(board, /\(d\.columns \?\? \[\]\)\.map\(c => \(/, '每一列的口径原句进抽屉');
  assert.match(board, /\(d\.guide \?\? \[\]\)\.map\(s => \(/, '说明分节逐节渲染后端 GUIDE');
  assert.match(board, /<DrawerSection key=\{s\.heading\} heading=\{s\.heading\}>\{plain\(s\.body\)\}/);
  const code = strip(board);
  assert.doesNotMatch(code, /Σ\(涨幅×分\)|收盘价 × adjustment_factor/,
    '加权式与复权口径的原句只住后端，前端抄一份早晚走偏');
});

test('尺子与读数映射到真实字段：描述符的 excess/step 不是字段名', () => {
  assert.match(board, /if \(view === "cum"\) return scale;/);
  assert.match(board, /if \(view === "excess"\) return `excess_\$\{scale\}` as keyof TrendCell;/);
  assert.match(board, /if \(view === "step"\) return `step_\$\{scale\}` as keyof TrendCell;/);
  assert.match(board, /return "win_pct";/);
  assert.match(board, /return view === "cum" \? scale : view === "win" \? "win_pct" : view;/,
    '描述符那一侧的 key 不带尺子后缀（后端 COLUMNS 只有 excess/step 两条）');
  for (const f of ['excess_weighted', 'excess_equal', 'step_weighted', 'step_equal'])
    assert.match(board, new RegExp(`${f}\\?: number \\| null;`), `${f} 要在 TrendCell 上登记过`);
});

test('列头 sortKey 与那一格真读的键同名（摊平成 h{k}）', () => {
  const head = board.slice(board.indexOf('<thead>'), board.indexOf('</thead>'));
  const fixed = [...head.matchAll(/sortKey="([a-z_]+)"/g)].map(m => m[1]);
  assert.deepEqual(fixed, ['events', 'codes'], `固定列那两个键：${fixed.join('、')}`);
  assert.match(head, /sortKey=\{`h\$\{k\}`\} dir=\{sort\.dirOf\(`h\$\{k\}`\)\}/);
  assert.match(head, /onSort=\{\(\) => sort\.toggle\(`h\$\{k\}`\)\}/);
  assert.match(board, /row\[`h\$\{k\}`\] = num\(cells\.find\(c => c\.k === k\)\?\.\[field\]\)/,
    '摊平进 h{k} 的就是当前尺子＋读数那一份真实字段');
  const body = board.slice(board.indexOf('<tbody>'), board.indexOf('</tbody>'));
  assert.match(body, /v=\{num\(r\[`h\$\{k\}`\]\)\}/, '格子读的与列头排的是同一个键');
  assert.match(body, /v=\{num\(baseRow\[`h\$\{k\}`\]\)\}/);
  // 真跑一遍排序：0 是读数、null 两个方向都排最后
  const rows = [{ h1: 0 }, { h1: null }, { h1: -3 }];
  assert.deepEqual(sortRows(rows, { key: 'h1', dir: 'asc' }).map(r => r.h1), [-3, 0, null]);
  assert.deepEqual(sortRows(rows, { key: 'h1', dir: 'desc' }).map(r => r.h1), [0, -3, null],
    '反向也不许把算不出的那档顶到最前（顶上去会被读成「跌得最少」）');
});

test('名次档那一列是普通表头：区间文本按字符串排会把 101-110 排到 11-20 前面', () => {
  const head = board.slice(board.indexOf('<thead>'), board.indexOf('</thead>'));
  assert.match(head, /<th className="whitespace-nowrap px-2 py-1 font-medium"/);
  assert.doesNotMatch(head, /sortKey="label"|sortKey="index"/);
  assert.match(board, /按字符串排会把 101-110 排到 11-20 前面/, '这个决定的理由要写在代码里');
  assert.match(board, /const sorted = sort\.apply\(rows\);/, '只排档位行');
  assert.match(board, /const sort = useTableSort\(\);/, '不给初始键＝不动后端给的档位顺序');
});

test('基准那一行钉在排序之后，选加权时整行是 — 而不是 0', () => {
  const body = board.slice(board.indexOf('<tbody>'), board.indexOf('</tbody>'));
  assert.ok(body.indexOf('{sorted.map(r => (') < body.indexOf('{baseRow && ('),
    '基准行不参与排序：它是另一把尺（全市场），排进档位里会被读成某一档');
  assert.match(body, /isBase \/>/);
  assert.match(board, /isBase && scale === "weighted" && view !== "win"/);
  assert.match(board, /基准只有等权那一份（全市场没有「按倍量分配权」这件事）：选加权时这一行是 —，不是 0/);
  assert.match(board, /基准那一行钉在最后不参与排序/, '这件事也要在抽屉里说一遍');
});

test('缺数三态分开念，任何一类都不是 0%；真 0 照常显示', () => {
  assert.match(board, /这一格的样本都还没走到那一天（窗口右端贴着基准日）：不是 0%，是那几天还没发生/);
  assert.match(board, /走到了那一天却算不出涨跌：名次日缺基期，或第 k 场它没有行（停牌\/未上市）。不是 0%/);
  assert.match(board, /加权分母为 0（这一格没有一笔既算得出涨跌又读得到加权分）：交 —，不悄悄退回等权/);
  assert.match(board, /这一格没有读数：后端交的是空，界面不拿 0 顶/);
  assert.match(board, /\{v === null \? <span className="text-muted-foreground\/50">—<\/span> : fmt\(v, view\)\}/);
  assert.match(board, /n=\{dash\(c\.n_ok\)\}\/\{dash\(c\.n_all\)\}/, '每一格的分母与总笔数上屏，不只藏在悬停里');
  assert.match(board, /分母 n_ok=\$\{dash\(c\.n_ok\)\} \/ 总笔数 n_all=\$\{dash\(c\.n_all\)\}/,
    '悬停里也要念出这两个分母（title 是模板串，不是 JSX 花括号）');
  assert.match(board, /进加权分母的 \$\{dash\(c\.n_w\)\} 笔、权重合计 \$\{dash\(c\.weight_sum\)\}/);
  const code = strip(board);
  // ?? 0 只许落在计数与档位序号上；落到读数上就是把「还没发生／停牌／分母为 0」念成平盘
  for (const m of code.matchAll(/(\w+)\s*\?\?\s*0\b/g))
    assert.ok(['n_pending', 'n_missing', 'n_ok', 'n_w', 'index'].includes(m[1]),
      `读数被 ?? 0 顶成了平盘：${m[0]}`);
  assert.match(board, /row\[`h\$\{k\}`\] = num\(cells\.find\(c => c\.k === k\)\?\.\[field\]\)/,
    '进表的那个数只经 num()：null／NaN 一律还是 null，不补 0');
  assert.match(board, /function num\(v: unknown\): number \| null \{\s*return typeof v === "number" && Number\.isFinite\(v\) \? v : null;/,
    '只有 finite 的数才算读数（0 算，NaN／null 不算）');
  assert.match(board, /if \(v === null\) return "—";/);
});

test('三类声明留在屏上：缺数计数那一条不进抽屉', () => {
  const foldAt = board.indexOf('<CaliberFold');
  const noticeAt = board.indexOf('<NoticeBar kind="missing"');
  assert.ok(foldAt > 0 && noticeAt > 0 && noticeAt < foldAt, '缺数声明渲染在折叠块之前、默认可见');
  assert.match(board, /when=\{nullCells > 0 \|\| pending > 0 \|\| missing > 0\}/,
    '没有这回事就整条不渲染，不画一条「今天没问题」占位');
  assert.match(board, /这张表有 \{nullCells\} 格是 —（不是 0%）/);
  assert.match(board, /还没走到那一天 \{pending\} 笔·格/);
  assert.match(board, /走到了却读不到 \{missing\} 笔·格/);
  const notice = board.slice(noticeAt, board.indexOf('</NoticeBar>', noticeAt));
  assert.doesNotMatch(notice, /DrawerSection|<details/, '声明不许折进抽屉');
});

test('说明只有一颗抽屉，标题带块名（不与宿主屏那颗撞车）', () => {
  assert.match(board, /const PANEL_TITLE = "近期倍量表走势分析";/);
  assert.match(board, /<Panel title=\{PANEL_TITLE\} state=\{probe\}/);
  assert.match(board, /drawerTitle=\{`\$\{PANEL_TITLE\} · 这块怎么读`\}/);
  assert.equal((board.match(/<CaliberFold/g) ?? []).length, 1, '一块一颗，不许再长第二颗说明按钮');
  assert.doesNotMatch(board, /<ExplainBox|<QuietFold/, '描述性长段统一走底部抽屉，不再用默认收起的框');
  assert.match(board, /notes=\{\[plain\(d\.rank\?\.rule \?\? ""\)\]\}/,
    '名次规则原文读接口 rank.rule 并剥成对粗体，页面不自己写那句规则');
  assert.doesNotMatch(strip(board), /同分同名|按位置顺延|不占号/, '名次规则那句话只住后端 ranking 一份');
  assert.match(board, /provenance=\{\[d\.source, d\.fingerprint\]\}/);
  assert.match(board, /coverage=\{\{ volume_double:/);
});

test('缓存自报生成时刻与年龄，不留一个恒为空的占位', () => {
  assert.match(board, /这份数什么时候算的/);
  assert.match(board, /d\.generated_at\s*\? `\$\{d\.generated_at\}\$\{d\.cached \?/, '命中与现算分开念');
  assert.match(board, /读的一小时缓存，已存 \$\{dash\(d\.cache_age_seconds\)\} 秒/);
  assert.match(board, /: "后端没交生成时刻"\}/, '没交就说没交');
});

test('上涨占比那一档：尺子按钮 disabled 并说清为什么，不做静默无作用的控件', () => {
  assert.match(board, /disabled=\{view === "win"\}/);
  assert.match(board, /aria-pressed=\{view !== "win" && scale === s\}/);
  assert.match(board, /上涨占比只有等权那一份口径（后端按算得出的样本数上涨的笔数），这一档不起作用；要看加权请先切回累计涨跌幅、超额或每场增量/);
  assert.match(board, /disabled:opacity-40/, ' disabled 要看得出来，不是亮着却点不动');
});

test('趋势折线：缺值断线不桥接，基准画虚线', () => {
  assert.match(board, /算不出的那一段线断开，不跨过去连成一条看着连续的曲线/);
  assert.match(board, /if \(v === null\) \{ if \(cur\.length > 1\) paths\.push\(cur\.join\(" "\)\); cur = \[\]; return; \}/,
    '遇到 — 就收段，绝不跨过它连一条看着完整的线');
  assert.match(board, /strokeDasharray=\{s\.key === "base" \? "5 4" : undefined\}/);
  assert.match(board, /这一份读数一格都算不出，画不了线（不是平盘）/);
  assert.match(board, /const lo = Math\.min\(0, \.\.\.vals\);/, '量程含 0 线：各档全负时也要看得出离 0 多远');
  assert.match(board, /<title>\{`\$\{s\.label\}｜后 \$\{horizons\[i\]\} 场：\$\{v\.toFixed\(2\)\}（\$\{unit\}）`\}<\/title>/);
});

test('单位跟着读数换：涨跌幅是 %，超额是个百分点，增量是百分点\/场', () => {
  assert.match(board, /const unit = view === "win" \? "%" : view === "cum" \? "%" : view === "excess" \? "个百分点" : "百分点\/场";/);
  assert.match(board, /（单位：\{unit\}）/, '屏上要说清这一份读数的单位');
  assert.match(board, /if \(view === "cum"\) return pct\(v, 2\);/, '累计涨跌幅走全站那把百分号尺');
});

test('宿主屏只挂组件：顺序在涨停样本统计之后、启动前量能画像之前', () => {
  assert.match(page, /<Retro n=\{2\}><EnergyTrendBoard \/>/);
  assert.doesNotMatch(page, /<EnergyTrendBoard[^>]*(nonce|date|window|topN)=/,
    '这一块自己取数，宿主再交一份状态进来就会长出两处日期');
  assert.doesNotMatch(page, /useStockRowInteraction|useProbe</, '宿主屏不代劳取数、也不串选中态');
  assert.ok(page.indexOf('<EnergyTrendBoard') > page.indexOf('<Backtest />'));
  assert.ok(page.indexOf('<EnergyTrendBoard') < page.indexOf('<StartupVolumeBoard'));
  assert.ok(!readFileSync(new URL('../src/pages/VolumeDouble.tsx', import.meta.url), 'utf8')
    .includes('<EnergyTrendBoard'), '倍量形态屏不许留第二份挂载');
});

test('这一块不接按票那一套交互：一行是一档名次，不是一只票', () => {
  // 与「首板形状预示」同一刀：主体是分档汇总表，硬套 rowProps 会把不相干的档一起点亮，
  // 也没有代码可双击开复盘。调用点台账 §六 因此 30 → 29（rankStabilityBoard.test.ts 那个数）。
  assert.doesNotMatch(board, /useStockRowInteraction|useCellSelection|openStockReview|rowProps|cellProps/,
    '名次档那一列没有个股代码，接了就是假交互');
  assert.doesNotMatch(board, /window\.open\(/, '开复盘新窗口只准住在 lib/openStockReview.ts 那一份');
});

test('系统配置的自检清单登记了这个端点，落点写历史回溯那一屏', () => {
  const system = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
  assert.match(system, /path: "\/quant\/energy-trend"/, '端点跑了却没进自检清单＝没人验');
  const line = (system.match(/path: "\/quant\/energy-trend"[^\n]*/) ?? [''])[0];
  assert.match(line, /where: "\/backtest-agent"/, '落点还写着旧屏＝清单说在 A 屏、A 屏找不到');
  assert.match(line, /slow: true/, '冷发十几秒，自检要按慢接口那一档等');
  assert.doesNotMatch(line, /逐日|进榜后|min_energy/, '自检说明里还写着被推翻那一版的形状');
});

test('渲染文本没有裸 **，中文串不嵌 ASCII 双引号', () => {
  const code = strip(board);
  const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
  assert.deepEqual(hits, [], `这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
  assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
    '中文句子中间出现了 ASCII 双引号（要用「」）');
  for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? [])
    assert.doesNotMatch(b, /"/, `JSX 文本节点里出现了 ASCII 双引号：${b}`);
});