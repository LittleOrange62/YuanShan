import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../src/pages/VolumeDouble.tsx', import.meta.url), 'utf8');

/**
 * 倍量榜新加的两块读数（2026-10-09）：一列四维象限得分 + 一张概念板块反向计数表。
 * 钉的都是"会被写成假的东西"：键名接错不报错、说明在前端抄一份会走偏、
 * 分母不写清就会被读成全市场。
 */

test('四维象限那一列接的是真字段，说明来自后端那份', () => {
  const table = src.slice(src.indexOf('<SortHeader label="代码"'), src.indexOf('</tbody>'));
  assert.match(table, /label="四维象限" sortKey="oper_value"/, '键名必须就是后端交的那个字段');
  assert.match(table, /r\.oper_value/, '列头键与格子里印的值是同一个');
  assert.match(table, /r\.quadrant\b[\s\S]{0,200}dash\(null\)/, '没有象限行显示 —，不显示 0');
  assert.match(table, /title=\{qTip\}/, '悬停内容取自后端 quadrant_axes 现拼的那一段');
  assert.match(src, /const ax = d\.quadrant_axes \?\? \{\}/);
  // 前端不许自己抄一份合成式或四档含义
  assert.doesNotMatch(src, /oper_score = 0\.40/, '合成式只在后端那一处');
  assert.doesNotMatch(src, /X 正 Y 负 ——/, '四档含义只在后端那一处');
});

test('概念榜说清分母是这批票，并给出只数之外的两个读数', () => {
  // 2026-10-14：这张反向计数榜的表体与四维象限那一屏收成一份（`components/quant/ConceptBoard.tsx`）。
  // 本屏剩下的责任只有两件事：**用共用那一份**（不另起一张表）、把"分母是哪一批票"说清。
  assert.match(src, /import \{ ConceptBoard, type ConceptRow \} from "@\/components\/quant\/ConceptBoard"/,
    '没有复用那份实现（各写一遍已经漂过一次：集中度的悬停文字两份说法）');
  assert.match(src, /<ConceptBoard state=\{hits\} pageSize=\{40\}/, '没接到调用点上，或把每页条数换了');
  assert.match(src, /poolLine=\{d => `上面那张倍量榜交出的 \$\{dash\(d\.concept_pool\?\.size/);
  assert.match(src, /只（不是全市场）/s, '分母必须写出来');
  assert.doesNotMatch(src, /const cdSort/, '排法归那份实现管，本屏不许再留一份排序状态');

  const board = readFileSync(new URL('../src/components/quant/ConceptBoard.tsx', import.meta.url), 'utf8');
  assert.match(board, /参考数据没覆盖/, '缺归属 ≠ 不属于任何题材');
  assert.match(board, /查询失败，不是这批票没有概念/, '读不到与真的没有要分开');
  const from = board.indexOf('概念板块 · 这批票里的出现次数');
  assert.ok(from > 0, '共用那块里没找到这张表');
  const head = board.slice(from, board.indexOf('</thead>', from));   // 本表自己的 thead，不是文件里第一个
  for (const key of ['concept', 'stocks', 'pool_share_pct', 'members_total', 'lift_pctile']) {
    assert.match(head, new RegExp(`sortKey="${key}"`), `${key} 这一列要能点`);
  }
  assert.match(head, /全市场成员/, '只数第一名是宽概念，必须同时给出成员数');
  assert.match(head, /集中度/);
});

test('默认序点亮在只数上，表尾说明这张表不含热度与资金', () => {
  const board = readFileSync(new URL('../src/components/quant/ConceptBoard.tsx', import.meta.url), 'utf8');
  assert.match(board, /useTableSort\(\{ key: "stocks", dir: "desc" \}\)/,
    '默认排法与列头上亮着的那支箭头必须是同一件事');
  assert.match(src, /不含热度、资金与涨跌/);
});

test('上屏的文案里没有裸的 markdown 星号', () => {
  // 这一页是纯文本上屏（不走 markdown 渲染），`**粗体**` 会原样显示成脏字符。
  // 注释里可以写，所以先把注释剥掉再判 —— 剥完还留着的就是会进 DOM 的。
  const rendered = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  const hits = rendered.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
  assert.deepEqual(hits, [], `这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
});

test('榜尾两列涨幅：键是真字段、口径来自后端、缺数不当平盘', () => {
  const table = src.slice(src.indexOf('<SortHeader label="代码"'), src.indexOf('</tbody>'));
  assert.match(table, /label="当日涨幅" sortKey="chg_today"/);
  assert.match(table, /sortKey="chg_15"/);
  assert.match(table, /r\.chg_today/, '列头键与格子里印的值必须是同一个字段');
  assert.match(table, /r\.chg_15/);
  // 口径（复权、基期、跨度）只在后端那一份（`duanxian/day_returns.py`），页面显示它给的文字
  assert.match(table, /title=\{[\s\S]{0,260}pctRule\}/, '悬停必须透出后端的 pct_rule');
  assert.match(src, /const pctRule = d\.pct_rule/, '口径文字取自后端，不自己复述');
  assert.doesNotMatch(src, /收盘价\s*×\s*adjustment_factor/, '复权公式只在后端那一处，前端抄一份早晚走偏');
  // 「今日」这个词是假的：这一屏锚在库里那一档，周末打开根本没有「今天」的行情
  assert.match(table, /label="当日涨幅"/);
  assert.doesNotMatch(table, /label="今日涨幅"/, '不许写「今日涨幅」');
  assert.match(src, /「当日」= 本页基准交易日/, '列头要说清这个当日是哪一天');
  // 缺数显示 —，不显示 0.00%（那是"平盘"，一句相反的结论）
  assert.match(table, /不是平盘 0%/, "两处空格子都要说清缺的原因");
  assert.match(table, /r\.pct_base_day/, "悬停要能自证基期是哪一场");
  assert.match(table, /r\.pct_bars/, "停牌过的票要能看出窗口里真有几场有行");
});

test('四维象限那一格：多选、由后端筛、档与只数都不在前端写死', () => {
  // 用户 2026-10-13 要点名的就是这一格。最容易糊过去的三件事各有各的钉法。
  // ① 真的进了取数参数（在前端筛 = 只能筛到倍量次最高的那 150 行，是另一种谎）
  assert.match(src, /&quadrants=\$\{encodeURIComponent\(quadrants\.join\(","\)\)\}/,
    '象限必须是发给后端的参数，不是本地 filters 的一项');
  // ② 档与标签来自后端那份（`quadrant_options`：值 + config.setting 的标签 + 全场只数 + 含义）
  assert.match(src, /\{\s*label: "四维象限", type: "multi", key: "quadrants",/,
    '列筛选面板里要有这一格，而且是多选');
  assert.match(src, /options: \(d\.quadrant_options \?\? \[\]\)\.map/,
    '选项取自后端，不自己列一份 Q1~Q4');
  assert.doesNotMatch(src, /\[\s*"Q1"[\s\S]{0,60}"Q4"\s*\]/,
    '四个档在前端写死 = 与「四维象限」页的标签两份口径');
  // ③ 改了要发出去、清除要一起清（本地 state 与 URL 参数各清一半就是"清了还在筛"）
  assert.match(src, /key === "quadrants"[\s\S]{0,10}setQuadrants\(/,
    '这一格不能写进本地 filters：写进去就不会重新取数（10-15 这屏改成交叉高亮共用块后，onChange 的分支从三元式改成了 if，断言跟着指过去、要求不变）');
  assert.match(src, /setFiltersState\(\{\}\); setQuadrants\(\[\]\);/,
    '清除筛选要把两类条件一起清掉');
  // ④ 范围要说清：象限筛在 limit 之前，其余数值格只在已交出的行里筛
  assert.match(src, /作用在取数上限/, '两种筛选的范围不一样，必须写在面板上');
  assert.match(src, /全场实有几只，不是「已经交出的这些行里有几只」/, '每档那个数的含义要说死');
  // ⑤ 榜上真没有 oper_score 的那几只也是一档，不勾就看不见它们
  assert.match(src, /没取到象限/, '「没取到象限」必须是一等选项，不是被筛掉就消失');
  // ⑥ 回显读后端应用了什么，不读界面自己的 state
  assert.match(src, /d\.filter\?\.quadrants/, '界面回显以后端真应用的档为准');
  // ⑦ 勾了象限之后那张概念榜的分母跟着缩，不写就会被读成"与象限无关"
  assert.match(src, /已经按上面选的四维象限筛过/, '概念榜的分母要说跟随筛选');
});

test('筛选面板的多选格把「没读到」与 0 只分开显示', () => {
  const panel = readFileSync(new URL('../src/components/ui/TableFilterPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /type: "select" \| "range" \| "boolean" \| "number-min" \| "multi"/);
  assert.match(panel, /count === null \? "—"/, 'null（没读到）显示 —，不能显示 0');
  assert.match(panel, /Array\.isArray\(v\) \|\| v\.length > 0/, '空数组算没筛：徽章不许点亮');
  assert.match(panel, /不限/, '多选要有一键回到不限');
});

test('取数上限是界面上的一格，截断时点名全场实有几只', () => {
  // 用户 2026-10-14 要「近 40 个交易日倍量次 ≥5 的都展示出来」：那一场满足的有 381 只，
  // 而默认上限 150 会静默切掉后面 231 只 —— 所以这一格必须能自己抬，且抬之前要先知道差多少。
  assert.match(src, /&limit=\$\{limit\}/, '上限要进取数参数，不是前端切片');
  assert.match(src, /const \[limit, setLimit\] = useState\(150\)/, '默认仍是后端的 150，不自作主张抬高');
  assert.match(src, /min=\{1\} max=\{800\}/, '输入框的档要与后端 le=800 对齐，否则打出 900 会被 422 拒');
  assert.match(src, /Math\.min\(800, Math\.max\(1, /, '越界要在界面夹紧，不发注定失败的请求');
  // 截断那条不许只说"到上限了"：要给"满足条件的一共几只"，人才知道该抬到多少
  const warn = src.slice(src.indexOf('交出行数已到上限'), src.indexOf('</p>', src.indexOf('交出行数已到上限')));
  assert.match(warn, /全场实有 \{qcTotal\} 只/, '要给出全场数');
  assert.match(warn, /不受上限影响/, '那个数的口径要说清不受 limit 影响');
  // 全场数超过后端硬顶时，"把上限调到 N 以上"是一句做不到的建议（实测 min_count=0 全场 5,526 只）
  assert.match(warn, /一次装不下/, '超过 800 时要改口，不许给做不到的建议');
  assert.match(warn, /qcTotal <= 800/, '两条建议按后端硬顶分岔');
  assert.match(src, /qcs\.every\(c => typeof c === "number"\)/, '任何一档计数没读到就不给合计，不拿半个数当全场');
});

test('倍量榜分页而不是只画前 60 行，且选中态按整批行号记', () => {
  // 口径改于 2026-10-19 第二稿：每页行数读接口 `thresholds.page_size`，界面只留一个有名字的兜底
  assert.match(src, /const PAGE = Number\.isFinite\(d\.thresholds\?\.page_size \?\? NaN\)/,
    '一页多少行读接口，不许在页脚之外写死');
  assert.match(src, /const PAGE_FALLBACK = 60/, '兜底值要有名字（散写 60 才是问题）');
  assert.match(src, /vdSort\.apply\(filteredRows\)/, '先排再筛完整批，最后才切页');
  assert.match(src, /shown = sorted\.slice\(\(cur - 1\) \* PAGE, cur \* PAGE\)/, '切页用的是排好的整批');
  assert.match(src, /gi: \(cur - 1\) \* PAGE \+ i/, '行号按整批序号记：页内下标会让第 1 页跟着亮');
  assert.match(src, /每页 \{PAGE\} 行，现在是第 \{cur\}\/\{pages\} 页/, '页脚要说清这一页是第几名到第几名');
  assert.doesNotMatch(src, /\.slice\(0, 60\)/, '不许再出现静默截到 60 行');
});

test('10-19 夜「描述收缩」自查：怎么点那句折进说明框，截断声明与 caveat 仍在默认可见位置', () => {
  // 用户 2026-10-19：「把选项留出，描述内容都收缩起来」——本页那一长段 note 早就由 Panel
  // 收进默认折叠的说明框（闸在 explainBox.test.ts），这一轮把表下裸渲染的 STOCK_ROW_HINT 也收进框。
  const boxStart = src.indexOf('<ExplainBox title="这一张表怎么点');
  const boxEnd = src.indexOf('</ExplainBox>', boxStart);
  assert.ok(boxStart > 0 && boxEnd > boxStart, '倍量榜要有"这一张表怎么点"的默认收起说明框');
  const box = src.slice(boxStart, boxEnd);
  assert.match(box, /\{STOCK_ROW_HINT\}/, '"怎么点"那句在框里（点开可见，措辞原样）');
  assert.doesNotMatch(box, /popHint|text-warning|<Caveats/, '告警与 caveat 不许在框里');
  // 截断声明（filter.limit 到顶那段）与 Caveats 渲染在第一个说明框之前＝默认可见位置
  const firstBox = src.indexOf('<ExplainBox');
  const before = src.slice(0, firstBox);
  assert.match(before, /交出行数已到上限/, '截断声明不许折');
  assert.match(before, /全场实有 \{qcTotal\} 只/);
  assert.match(before, /<CaliberFold caveats=\{d\.caveats\} definitions=\{d\.definitions\}/,
    'caveat／字段义／覆盖交给共用折叠块（用户 10-21 要密度），位置仍在第一个说明框之前＝缺数类没被折');
  assert.match(before, /coverage=\{d\.coverage\}/, '覆盖区间跟着进折叠块，但同一段里，不缺出处');
  // popHint 留在框外（这块 box 之后紧跟告警行）
  assert.match(src.slice(boxEnd, boxEnd + 400), /vd\.popHint && <p role="status"/,
    '弹窗被拦的告警渲染在收起的说明框外面');
});
