import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  fracText, hitText, nullTally, parentTruncationSentence, windowSentence,
} from '../src/lib/momentumFollow.ts';
import { NULL_TEXT } from '../src/lib/datastate.ts';

/**
 * 「上一场稳步前进榜 · 后续表现」（`components/quant/MomentumFollowBoard.tsx`，
 * 端点 `GET /api/quant/momentum-follow`，2026-10-20 凌晨新增；母体＝上一场那张榜的前 N 名，
 * 表现＝「今日涨跌 + 近 5 日涨跌」两栏，不逐日全铺）。
 *
 * 2026-10-27 用户点名改名：母体是**前一场** ⇒ 它之后的第一场就是今天，旧列名「次日涨跌」让人
 * 以为要等明天才有数；「至今累计」在母体=前一场时与今日是同一个数，撤下换成「近 5 日涨跌」。
 *
 * 钉住的形状（逐条对应这块最容易写假的地方）：
 *  ① 取数走 `/quant/momentum-follow`（useProbe 自补 /api，页面里绝不写 /api）；
 *  ② **每一列的 sortKey 必须等于那一格真读的字段**（表头与格子从同一份 COLS 走，写错不报错也不换序）；
 *  ③ 三态格子：null → —，true/false → 在/不在 ——「没读到」与「不在榜」不许共用一个念法；
 *     涨跌幅缺数是 —，不是 0%（0% 是一分钱没动）；
 *  ④ 母体档位与前 N 与上面那张榜**共享页面状态**（组件里没有第二个默认 80、没有第二个 metric 状态）；
 *  ⑤ 名次规则原文读接口 `rank.rule`，页面不抄「同分同名」那句；复权口径读 `pct_rule`；
 *  ⑥ 截断声明、— 与 0 的分工、空栏计数在**默认可见**位置（不折进 ExplainBox），描述性长段折进框；
 *  ⑦ 中文串不嵌 ASCII 双引号、渲染文本没有裸 `**`（项目红线）；
 *  ⑧ System.tsx 自检清单登记且 path 去掉 /api 前缀、慢接口标 slow；
 *  ⑨ 那两栏的名字与出处：屏上念「今日涨跌」「近 5 日涨跌」，`至今`／`次日` 那一族的旧键
 *     一个都不许留在 COLS 与行类型里（口径换了还念旧名＝屏幕说假话）。
 */

const board = readFileSync(new URL('../src/components/quant/MomentumFollowBoard.tsx', import.meta.url), 'utf8');
const lib = readFileSync(new URL('../src/lib/momentumFollow.ts', import.meta.url), 'utf8');
const page = readFileSync(new URL('../src/pages/Quadrant.tsx', import.meta.url), 'utf8');
const vrApp = readFileSync(new URL('../../vr/app.py', import.meta.url), 'utf8');
const followPy = readFileSync(new URL('../../duanxian/momentum_follow.py', import.meta.url), 'utf8');

/* ── ① 接线 ── */

test('取数：页面探 /quant/momentum-follow，端点调 momentum_follow.board，异常按判据页那族抛 502', () => {
  assert.match(page, /useProbe<FollowData>\("\/quant\/momentum-follow"/);
  assert.doesNotMatch(page, /"\/api\/quant\/momentum-follow/, 'useProbe 自己补 /api 前缀，页面里不许出现两次');
  assert.match(vrApp, /@app\.get\("\/api\/quant\/momentum-follow"\)/);
  assert.match(vrApp, /momentum_follow\.board\(metric=metric, date=date, limit=limit, refresh=refresh\)/);
  assert.match(vrApp, /raise HTTPException\(502, f"名次稳步前进榜后续表现取数异常：\{e\}"\)/);
  assert.match(vrApp, /limit: int = Query\(80, ge=5, le=200\)/, '参数边界要在签名上写明');
});

test('挂在 <SteadyRankBoard /> 正下方，且档位与前 N 共享母体榜那一份状态', () => {
  assert.ok(page.indexOf('<MomentumFollowBoard') > page.indexOf('<SteadyRankBoard'),
    '新榜必须长在母体榜正下方（用户点名的位置）');
  assert.ok(page.indexOf('<MomentumFollowBoard') < page.indexOf('<ConceptBoard'),
    '也在反向概念榜之前：概念榜那句「上面那张榜」的指代不能被打断');
  assert.match(page, /<MomentumFollowBoard state=\{mf\} metric=\{rmMetric\} onMetricChange=\{setRmMetric\}/,
    '母体档位与上面那张榜同一个状态：切上面那张，这块跟着换母体，不会出现上总分下 xy 的错配');
  // 组件里没有第二份默认 80／第二个 metric 状态（上限与档位只由页面/接口交）
  const noComments = board.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  assert.doesNotMatch(noComments, /useState<FollowMetric|useState\(80\)|limit=80/,
    '组件不许自留档位/上限状态（那是页面那份）');
});

/* ── ② sortKey 与格子字段一致 ── */

test('列头与格子从同一份 COLS 走：sortKey 集合 = cellProps 键集合 = 接口行字段', () => {
  const colsBlock = board.match(/const COLS: \[string, string\]\[\] = \[([\s\S]*?)\];/);
  assert.ok(colsBlock, '表头清单必须是一份数组（两处各写一份就会错位）');
  const keys = [...colsBlock[1].matchAll(/\["([a-z0-9_]+)",/g)].map(m => m[1]);
  assert.ok(keys.length >= 15, `这一屏至少该有的名次/表现列都在这份清单里：${keys.length}`);
  const cellKeys = [...board.matchAll(/x\.cellProps\(i, "([a-z0-9_]+)"/g)].map(m => m[1]);
  for (const k of keys) assert.ok(cellKeys.includes(k), `列头有 ${k} 而格子没读这个字段（sortKey 是死键）`);
  for (const k of cellKeys) assert.ok(keys.includes(k), `格子读了 ${k} 却没有对应列头`);
  assert.match(board, /COLS\.map\(\(\[k, label\]\) => \(\s*<SortHeader key=\{k\} label=\{label\} sortKey=\{k\}/,
    '表头必须由 COLS 生成（键就是那一格读的字段名）');
  // 行上的字段与 lib 类型一致（后端 definitions 与行键一一对应由真库测试钉，这里钉前端这一半）
  const iface = lib.match(/export interface FollowRow \{([\s\S]*?)\n\}/);
  assert.ok(iface);
  for (const k of keys)
    assert.match(iface[1], new RegExp(`\\b${k}\\?:`), `行类型里没有 ${k}（界面在读一个接口没交的字段）`);
});

/* ── ③ 三态与 — 的纯函数 ── */

test('三态格子：在／不在／—；缺的涨跌幅是 —，不是 0%', () => {
  assert.equal(hitText(true), "在");
  assert.equal(hitText(false), "不在");
  assert.equal(hitText(null), NULL_TEXT);
  assert.equal(hitText(undefined), NULL_TEXT);
  assert.equal(fracText(94.8), "94.8%");
  assert.equal(fracText(0), "0.0%", "真 0 是一条实质读数（一分钱没动），照常念出来");
  assert.equal(fracText(null), NULL_TEXT);
  assert.equal(fracText(Number.NaN), NULL_TEXT);
  const rows = [{ chg_next_day: 1.2 }, { chg_next_day: null }, {}, { chg_next_day: 0 }] as never[];
  assert.equal(nullTally(rows, "chg_next_day"), 2, 'null 与没交是缺数；0 与 1.2 都是读数');
});

test('窗口那句只转述接口：哪一天都不自己算', () => {
  const d = {
    parent_date: "2026-10-16", limit: 80, date: "2026-10-19",
    window: { sessions: 6, steps: 5, first_day: "2026-10-09", last_day: "2026-10-16",
              next_day: "2026-10-19", to_date: "2026-10-19", sessions_after: 1 },
  } as never;
  const s = windowSentence(d);
  for (const frag of ["2026-10-16", "2026-10-09", "2026-10-19", "前 80", "1 场"])
    assert.ok(s.includes(frag), `缺了接口交出的那个读数：${frag} → ${s}`);
  const noNext = windowSentence({ ...d, window: { ...d.window, next_day: null } } as never);
  assert.match(noNext, /整栏是 —，不是 0%/);
  const noCount = windowSentence({ ...d, window: { ...d.window, sessions_after: null } } as never);
  assert.match(noCount, /读不到/, '场数没读到不许念成 0 场');
});

test('截断那句复用母体榜同一份实现（同一件事在两屏不许有两个算法）', () => {
  assert.match(lib, /import \{ truncationSentence, type SteadyData \} from "\.\/steadyRank\.ts"/);
  assert.match(lib, /truncationSentence\(\{[\s\S]*total_matched: d\.total_matched/,
    '本块的行数＝母体榜交出的行数，那句「符合判据共几只、交出前几名」直接复用 steadyRank 那份');
  const s = parentTruncationSentence({
    total_matched: 372, limit: 80,
    rows: Array.from({ length: 80 }, () => ({})),
  } as never);
  assert.match(s, /符合判据的共 372 只.*截断/s);
});

/* ── ⑤⑥ 文案只有一份：后端原句 + 默认可见的声明 ── */

test('名次与复权规则原文读接口，页面不抄第二份', () => {
  assert.match(board, /rankTitle\(ruleText/, '名次悬停走 lib/ranking.ts 那份（规则原文＝d.rank.rule）');
  assert.match(board, /plain\(d\.rank\?\.rule \?\? ""\)/);
  const noComments = board.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  assert.doesNotMatch(noComments, /同分同名/, '那句话只住后端 ranking.RULE_NOTE，抄一份早晚走偏');
  assert.doesNotMatch(noComments, /收盘价 × adjustment_factor/,
    '复权尺子那句原文在后端 day_returns.RULE_NOTE 里，抄一份早晚走偏（页面上「后复权」三个字可以出现，算式不行）');
  assert.match(board, /plain\(d\.pct_rule/);
  assert.match(board, /d\.rule \? plain\(d\.rule\)/, '母体判据原句也逐字转述');
});

test('截断声明、— 与 0 的分工、空栏计数在默认可见处；长描述折进说明框', () => {
  const boxStart = board.indexOf('<ExplainBox');
  const boxEnd = board.indexOf('</ExplainBox>');
  assert.ok(boxStart > 0 && boxEnd > boxStart, '这块要有默认收起的说明框');
  const before = board.slice(0, boxStart);
  assert.match(before, /parentTruncationSentence\(d\)/, '截断那句默认在屏上（80 行会被读成"全场只有 80 只"）');
  assert.match(before, /不是 0%、不是 0 分、不是「没进过榜」/, '— 与 0 的分工那句留在框外');
  assert.match(before, /今日读不到 \{missNext\} 只/, '空栏计数（按已交出行数的分布读数）留在框外');
  assert.match(before, /近 5 日读不到 \{miss5\} 只/, '两栏各数各的：一栏有数另一栏是 — 是常态');
  const box = board.slice(boxStart, boxEnd);
  assert.doesNotMatch(box, /parentTruncationSentence|missNext|popHint|<Caveats/,
    '截断、计数、告警、caveat 都不许折进框');
  assert.match(box, /STOCK_ROW_HINT/, '「怎么点」折进框');
  assert.match(board, /\{x\.popHint && <p role="status" className="[^"]*text-warning"/,
    '弹窗被拦的告警渲染在框外');
});

/* ── ⑨ 2026-10-27 点名的两处改名 ── */

test('两栏的名字与出处：屏上念「今日涨跌」「近 5 日涨跌」，旧的那一族一个不许留', () => {
  const colsBlock = board.match(/const COLS: \[string, string\]\[\] = \[([\s\S]*?)\];/);
  assert.ok(colsBlock);
  const pairs = [...colsBlock[1].matchAll(/\["([a-z0-9_]+)", "([^"]+)"\]/g)].map(m => [m[1], m[2]]);
  const labelOf = new Map(pairs);
  assert.equal(labelOf.get('chg_next_day'), '今日涨跌',
    '母体默认是前一场 ⇒ 基准后第一场就是今天；键名保留 chg_next_day（＝基准后第一场），名字跟着口径改');
  assert.equal(labelOf.get('chg_5d'), '近 5 日涨跌', '「至今累计」撤下：母体=前一场时它与今日是同一个数');
  // 旧的「至今」那一族：键、格子、悬停文字一个都不许留在界面这一半（口径换了还念旧名＝屏幕说假话）
  const code = board.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
  for (const dead of ['chg_to_date', 'to_date_day', 'missCum'])
    assert.doesNotMatch(code, new RegExp(dead), `界面上还留着作废的那一栏：${dead}`);
  assert.doesNotMatch(code, /次日|至今/, '渲染出去的字里不许再有那两种旧念法（注释里可以有）');
  assert.doesNotMatch(lib.replace(/\/\*[\s\S]*?\*\//g, ''), /chg_to_date|to_date_day/,
    '行类型里不许留作废字段（界面读到接口没交的字段会静默变 —，看不出来）');
  // 两栏各自的出处与「不许相减」这句都在，且原因码是两套
  assert.match(code, /r\.perf5_missing_note/, '近 5 日那一格的 — 用它自己的原因码，不借今日那句');
  assert.match(code, /不许相减/);
  assert.match(lib, /近 5 日那一栏不依赖母体日/);
});

test('后端那半边的承诺（这份测试只读源码文本核对）：母体调 rank_momentum、缺数交 null', () => {
  assert.match(followPy, /Q\.rank_momentum\(metric=metric, limit=limit, date=d, refresh=refresh\)/,
    '母体必须直接调用 rank_momentum —— 判据不许有第二份抄本');
  assert.doesNotMatch(followPy, /better_steps >= /, '这里没有第二份稳步前进 SQL');
  assert.match(followPy, /from \. import day_returns/);
  assert.match(followPy, /day_returns\.series_by_code\(\{c: str\(next_day\) for c in codes\}/,
    '每只票的起算日统一＝母体场之后的第一场（停牌不许桥接由 day_returns 兜）');
  assert.match(followPy, /day_returns\.returns_by_code\(codes, str\(to_date\), span=PERF_SPAN\)/,
    '近 5 日的终点是读至日、不是母体日；档位只住 PERF_SPAN 一处（与首板分析屏同一个出口）');
  assert.doesNotMatch(followPy, /chg_to_date|to_date_day/, '作废那一栏在后端也没有残留');
  assert.match(followPy, /'rule': RANKING\.RULE_NOTE/, '名次文案就是公用组件那个对象');
  assert.match(followPy, /_energy_rank_expr/, '倍量名次复用倍量榜同一个表达式');
  assert.match(followPy, /RANKING\.rank_sql/, '埋伏榜内名次走公用组件');
});

/* ── ⑦⑧ 卫生与登记 ── */

test('渲染文本没有裸 **，中文串不嵌 ASCII 双引号', () => {
  for (const [name, src] of [["component", board], ["lib", lib]]) {
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\r\n]*/g, '');
    const hits = code.match(/[^\s]\*\*|\*\*[^\s]/g) ?? [];
    assert.deepEqual(hits, [], `${name} 这些地方有裸的 **，会原样显示：${hits.join(' ')}`);
    assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
      `${name}：中文句子中间出现了 ASCII 双引号（要用「」）`);
  }
});

test('系统配置的自检清单登记了这个新端点（去 /api 前缀、慢接口标 slow），落点写四维象限', () => {
  const system = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
  assert.match(system, /path: "\/quant\/momentum-follow\?metric=xy&limit=80"/);
  const line = (system.match(/path: "\/quant\/momentum-follow[^\n]*/) ?? [''])[0];
  assert.match(line, /where: "\/quadrant"/);
  assert.match(line, /slow: true/, '母体那张榜本身要现算（实测首次约 19 秒），不标 slow 就是骗自检页');
  assert.doesNotMatch(line, /逐日|几百列/, '清单里别把「不逐日全铺」写反');
});
