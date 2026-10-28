import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

/**
 * 「分组整体分析」那一叠的四条纪律（原独立屏 `/watchlist-analytics`，2026-10-14 整块搬进
 * 「我的股票 · 分组自选」最上方 —— 用户：先分析分组的整体情况，再分析分组自选）。
 *
 * 起因还是用户 2026-09-27 的点名：这一叠**不要**当日涨/跌/未取到的只数、不要平均成本、
 * 不要「同花顺导入」那一列（"自选股都是同花顺导入的，显示就是多此一举"），
 * 要的是组级的分数、趋势、近 X 日平均涨跌幅与组内振幅。
 * 所以这里钉的是"撤掉的别自己长回来"和"新加的每一列都能排、缺数不糊 0"。
 *
 * ⚠️ 搬家后读的是两个文件，分工必须钉死：
 *  · 表格/折线/口径的实现 = `components/quant/GroupAnalyticsBoard.tsx`（**一份**，别再长第二张）；
 *  · 取数 = `pages/Grouped.tsx`（一发 `useProbe`，顶部那块表与组卡片那 6 格共用同一个 DataState）。
 */

const BOARD = fs.readFileSync(new URL('../src/components/quant/GroupAnalyticsBoard.tsx', import.meta.url), 'utf8');
const PAGE = fs.readFileSync(new URL('../src/pages/Grouped.tsx', import.meta.url), 'utf8');
const src = BOARD;   // 下面四条老纪律钉的都是那一叠呈现层，名字沿用 src 免得逐条改

test('聚合接口由页面发一次，呈现组件自己不发请求', () => {
  assert.match(PAGE, /useProbe<Analytics>\("\/watchlist\/group-analytics", nonce\)/,
    '取数要在页面里（搬到 /grouped 顶部之后，它跟着 nonce 一起重取）');
  assert.equal((PAGE.match(/"\/watchlist\/group-analytics"/g) ?? []).length, 1,
    '同一接口在页面里出现两处 = 每次进页聚合一发再聚合一发');
  assert.doesNotMatch(BOARD, /useProbe|probe\(|fetch\(/,
    'GroupAnalyticsBoard 只吃调用方交进来的 DataState；它自己再发一发就变成两个读法、两份口径');
  assert.doesNotMatch(PAGE, /useProbe<Groups>\("\/watchlist\/group-analytics"/);
  assert.doesNotMatch(BOARD, /"\/watchlist\/groups"/, '组分析这一叠不读浅的那份票数一览（那是 /grouped 的分组块）');
});

test('the three columns the user removed stay removed', () => {
  // 认**列与字段**，不认散文 —— 「本页不做什么」那张卡要写明撤了哪三列，那里出现名字是对的
  assert.doesNotMatch(src, /avg_cost|cost_filled/, "平均成本（覆盖）整列撤掉：连字段都不该再读");
  assert.doesNotMatch(src, /label="备注|label="平均成本|label="涨 \/ 跌/, '撤掉的三列不许以列的形式长回来');
  assert.doesNotMatch(src, /\bmissing\b\s*[?:]/, '涨/跌/未取到的只数不再作为字段出现');
  assert.match(src, /裁定撤掉了三列/, '撤了什么必须写在页面上，不能让读者自己猜');
});

test('every metric column is a sortable header with its own caliber', () => {
  const cols = src.slice(src.indexOf('const COLUMNS'), src.indexOf('export function GroupAnalyticsBoard'));
  const entries = cols.split('\n').filter(l => /^\s*\{ key:/.test(l));
  assert.ok(entries.length >= 14, `列数掉到 ${entries.length}，组级读数不该被砍回去`);
  for (const e of entries) {
    assert.match(e, /hint: "[^"]{8,}"/, '每一列都要有可解释的口径，不能只给一个抬头');
  }
  assert.match(src, /\{COLUMNS\.map\(c => \(\s*<SortHeader/, 'COLUMNS 必须渲染成 SortHeader，不是静态 th');
});

test('missing values render as dash and never as zero', () => {
  // 趋势三列不足 10 场是 NULL —— 补 0 会被读成"横着走"
  assert.doesNotMatch(src, /trend_score\s*\?\?\s*0|pred_score\s*\?\?\s*0|w_score\s*\?\?\s*0/);
  assert.match(src, /v == null \? dash\(null\)/);
  assert.match(src, /留空不补 0/);
});

test('近 N 场名次那一列只转述接口，不自己算名次也不自己写 N', () => {
  // 判据（同分同名顺延、没分不占号、方向怎么读、母体是哪一批）全部来自接口的 rank.* 与 calibers.*
  assert.match(src, /rankText\(r\.rank_now\)/, '名次数字要走 lib/ranking.ts 那一处出口（缺数是 —，不是 0）');
  assert.match(src, /近 \$\{dash\(d\.rank\?\.days\)\} 场排名/,
    '列头那个 N 必须读接口（rank.days）—— 写死 5 会让"改了窗口列头还在说 5"变成假话');
  assert.doesNotMatch(src, /近 ?5 ?场|GROUP_RANK_DAYS/, '页面不许自己抄窗口天数');
  assert.doesNotMatch(src, /rank_now\s*\?\?\s*0|rank_gain\s*\?\?\s*0/, '缺名次补 0 就是"编一个最后一名"');
  assert.doesNotMatch(src, /\.rank\s*=\s*/, '名次不在前端算：格子里那个号数只从接口读（判据住 ranking.py）');
  assert.match(src, /import \{ rankText \} from "@\/lib\/ranking";/,
    '前端这一侧只借 lib/ranking.ts 的"怎么念"，不借它算 —— 那里也没有算名次的能力');
  // 悬停三句全是接口原句（规则短版 / 方向读法 / 净变化口径），页面只补"这一串是什么"那一帧
  assert.match(src, /\[rk\?\.rule_short, rk\?\.direction, calibers\?\.rank_gain,/,
    '名次格的悬停必须转述后端那三句原话，别在组件里另写一份规则（全版 rule 是给推送与文档读的）');
  // sortKey 要等于格子里读的那个字段（名次按"最近那场"排，路径是文本不参与排序）
  assert.match(src, /sortKey="rank_now"/, '名次列的点排走 rank_now —— 与格子头部那个数字同一个字段');
  assert.match(src, /\["rows\[\]\{rank_now,rank_path,rank_days,rank_gain\}"/, '契约里要登记这一列的四个字段');
  // 母体：这张表只有有成员的组，"全场"用不得
  assert.match(src, /不是全市场/, '名次那一列的母体必须在屏上说清');
});

test('the trend chart shows three by default and breaks on missing days', () => {
  assert.match(src, /lines\.slice\(0, 3\)/, '默认只画斜率最好的 3 条，其余收进图例');
  // 断线：遇到空点必须把当前折线段落切开，而不是拿 0 顶上把线连过去
  assert.match(src, /if \(v == null\) \{ if \(cur\.length > 1\) segs\.push/);
  assert.doesNotMatch(src, /points=\{xs\.map\(\(i\) => .*pts\.get\(i\) \?\? 0/, '缺数点不得补 0 连线');
});

test('per-stock detail keeps linking out, group rows do not pretend to be a stock', () => {
  // 组名不是个股：这一叠里出现 StockLink 就是把组名链到不存在的 6 位代码上
  assert.doesNotMatch(src, /StockLink/);
  // 10-14 搬家后逐票明细不再"指向另一屏"，它就在同一页下面 —— 屏上的说法要跟着改口。
  // 10-15 档案那一叠又搬去「个股深度复盘」，所以这一句现在说的是"明细在本页下面 + 档案双击另开一窗"，
  // 不许再写「档案都在本页」（写着就是指向一个已经不存在的去处）。
  assert.match(src, /逐票明细与按组筛票在本页下面/);
  assert.match(src, /双击明细的那一行/, '要说清档案现在靠双击另开一窗看');
  assert.doesNotMatch(src, /量化档案，都在本页/, '档案已不在本页下面，这句是假话');
  assert.doesNotMatch(src, /to="\/grouped"/, '本页内部的说明不该再放一条通往自己的站内链');
  // 10-15：逐票那一叠（档案）搬去「个股深度复盘」，断言跟着改读取路径（**不删**）：
  // 这一页不再嵌档案，但那一叠必须真的挂在新的宿主上、且吃地址里那一只。
  assert.doesNotMatch(src, /<StockDossier/, '档案已搬走，本页再嵌一份就是第二个真相源');
  const review = fs.readFileSync(new URL('../src/pages/StockReview.tsx', import.meta.url), 'utf8');
  assert.match(review, /<StockDossier code=\{code \|\| null\}/, '逐票那一叠（档案）必须真的在深度复盘那一屏上');
});
