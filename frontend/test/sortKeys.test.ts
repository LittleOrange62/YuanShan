import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { sortRows } from '../src/lib/useSort.ts';

/**
 * 列头排序键必须真的落在行数据上。
 *
 * 起因：用户 2026-09-27 指着倍量榜说「板块这个字段的排序，没生效啊」。
 * 原因是 `sortKey="sector"` 而接口给的字段叫 `board` —— `pick()` 取不到值，
 * 每一行都被当成"缺数"，缺数永远排最后且互相比原序，于是**点上去毫无反应**：
 * 不报错、不空表，只是不动。这类键名写错只有"点一下看有没有换序"才发现得了，
 * 而 `aria-sort` 照样会变，所以看 DOM 属性也骗得过人。
 *
 * 这条测试把整个 bug 族按源码扫一遍：每个 `sortKey="K"` 必须能在同一个文件里
 * 找到它的出处 —— 要么是某张 interface 的字段（`K?:` / `K:`），要么真的被当属性读过（`.K`）。
 * 扫不出"字段存在但表头接错表"那种（两个 interface 撞名），那一档要靠点击实测。
 */

const SRC = new URL('../src/', import.meta.url);
const files: URL[] = [];
(function walk(dir: URL) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const u = new URL(e.name + (e.isDirectory() ? '/' : ''), dir);
    if (e.isDirectory()) walk(u);
    else if (/\.(tsx|ts)$/.test(e.name)) files.push(u);
  }
})(SRC);

test('sort keys resolve to a real row field (板块 sortKey=sector 那一类)', () => {
  const dead: string[] = [];
  for (const f of files) {
    const s = fs.readFileSync(f, 'utf8');
    if (!s.includes('sortKey=')) continue;
    for (const m of s.matchAll(/sortKey="([^"]+)"/g)) {
      const key = m[1];
      const leaf = key.split('.').pop() as string;
      const declared = new RegExp(`(^|[\\s{,])${leaf}\\??\\s*[?:]`, 'm').test(s);
      const read = new RegExp(`\\.${leaf}\\b`).test(s);
      const byStringKey = new RegExp(`['"\`]${leaf}['"\`]`).test(s);   // 动态列表格：['timestamp','symbol'].map(k => f[k])
      const bracket = new RegExp(`\[["'\`]${leaf}["'\`]\]`).test(s); // 带 % 等符号的中文键：c["跌超5%"]
      const nested = key.includes('.') && new RegExp(`\\.${leaf}\\b`).test(s);
      if (!(declared || read || byStringKey || bracket || nested)) {
        dead.push(`${f.href.slice(SRC.href.length)} → sortKey="${key}"`);
      }
    }
  }
  assert.deepEqual(dead, [], '这些列头排序键在文件里找不到对应字段，点上去不会换序');
});

test('每张表里的键，必须在这张表自己的行上取得到值（防"字段存在但接错表"）', () => {
  // 只有文件级扫描会漏这一类：同一个字段名在另一张表的行上存在，本表的行却没有。
  // 实例：板块趋势那张表的「斜率」列印的是 slope_short/mid/long，键却写成热度表的 slope_21
  // —— 点了不换序，页面上一切正常。
  // 三种合法写法不算死键：① 本块内 `.key` 读过；② 本块把行交给子组件渲染（`<Row h={…} />`，
  // 读法在组件里）；③ 动态列表格按字符串键取值（`['timestamp','symbol'].map(k => f[k])`）。
  const dead: string[] = [];
  for (const f of files) {
    const s = fs.readFileSync(f, 'utf8');
    for (const chunk of s.match(/<table[\s\S]*?<\/table>/g) ?? []) {
      for (const raw of chunk.match(/dirOf\("([^"]+)"\)/g) ?? []) {
        const leaf = raw.replace(/dirOf\("|"\)/g, '').split('.').pop() as string;
        if (new RegExp(`\\.\\s*${leaf}\\b`).test(chunk)) continue;
        if (/<[A-Z]\w*[\s/>]/.test(chunk)) continue;
        if (new RegExp(`['"\`]${leaf}['"\`]`).test(chunk)) continue;
        if (new RegExp(`\[["'\`]${leaf}["'\`]\]`).test(chunk)) continue;
        if (new RegExp(`(^|[\\s{,])${leaf}\\??\\s*[?:]`, 'm').test(chunk)) continue;
        dead.push(`${f.href.slice(SRC.href.length)} → ${leaf}`);
      }
    }
  }
  assert.deepEqual(dead, [], '这些排序键在它自己那张表的行上取不到值');
});

test('the four columns fixed on 09-27 are wired to the field the cell actually prints', () => {
  const vd = fs.readFileSync(new URL('pages/VolumeDouble.tsx', SRC), 'utf8');
  for (const [label, key] of [['板块', 'board'], ['对昨倍量', 'dbl_prev_count'], ['日均成交额', 'avg_amt_yuan']] as const) {
    assert.match(vd, new RegExp(`label="${label}" sortKey="${key}" dir=\\{vdSort\\.dirOf\\("${key}"\\)`),
      `${label} 的 sortKey 必须与 vdSort.dirOf 用同一个真实字段名`);
  }
  assert.doesNotMatch(vd, /sortKey="sector"|sortKey="dbl_prev"|sortKey="avg_amt"/, '旧的空转键名不许复活');

  const fb = fs.readFileSync(new URL('pages/FirstBoard.tsx', SRC), 'utf8');
  assert.match(fb, /label="炸板" sortKey="break_count"/, '炸板那一列的字段是 break_count，不是 broken_times');

  const qd = fs.readFileSync(new URL('pages/Quadrant.tsx', SRC), 'utf8');
  assert.match(qd, /sortKey="x_quality"/); assert.match(qd, /sortKey="y_env"/);
  assert.match(qd, /sortKey="oper_value"/); assert.match(qd, /sortKey="rank_in_market"/);
  assert.match(qd, /sortKey="point_count"/);
  assert.doesNotMatch(qd, /sortKey="(x|y|oper|rank|points)"/, '单字母/短名那种凭空的键不许回来');

  // 板块趋势那张表的「斜率」列印的是 短/中/长（5/11/21 日），排序键必须跟着同一档
  const sh = fs.readFileSync(new URL('pages/SectorHeat.tsx', SRC), 'utf8');
  const trendTable = (sh.match(/<table[\s\S]*?<\/table>/g) ?? []).filter(c => c.includes('trendSort.apply')).join('');
  assert.ok(trendTable.length > 0, '没找到板块趋势那张表');
  assert.match(trendTable, /sortKey="slope_long"/, '斜率那一列按 21 日档（slope_long）排');
  assert.doesNotMatch(trendTable, /sortKey="slope_21"/, 'slope_21 是热度表的字段，接不到这张表上');
  assert.match(trendTable, /r\.slope_long/, '列头键与格子里印的值必须是同一个');
});

test('派生排序键的语义：与格子里显示的一致，且"没有这一档"不等于 0', () => {
  // ① 日志表「金额/持有」：持仓中与未填明细都没有 realized_pnl，两个方向都要在最后
  const journal = [
    { id: 'a', settled: { realized_pnl: -500 } },
    { id: 'b', settled: { has_fills: true, closed: false } },   // 持仓中：没有已实现金额
    { id: 'c', settled: { realized_pnl: 1200 } },
    { id: 'd', note: '没填明细' },                                // 连 settled 都没有
  ];
  for (const dir of ['asc', 'desc'] as const) {
    const order = sortRows(journal, { key: 'settled.realized_pnl', dir }).map(r => r.id);
    assert.deepEqual(order.slice(0, 2), dir === 'asc' ? ['a', 'c'] : ['c', 'a'],
      '有数的那两档排在前，方向只翻有数的部分');
    assert.deepEqual(order.slice(2).sort(), ['b', 'd'], '持仓中/未填明细恒最后，不被当成 −∞ 或 0');
  }

  // ② 「标的」：格子印的是 name || code，排序必须用同一串（缺名字的走代码）
  const rows = [{ code: '600000', name: '平安银行' }, { code: '000001', name: null }];
  const labelled = rows.map(r => ({ ...r, stock_label: r.name || r.code }));
  const desc = sortRows(labelled, { key: 'stock_label', dir: 'desc' }).map(r => r.stock_label);
  assert.ok(desc.includes('000001') && desc.includes('平安银行'));
  assert.equal(new Set(desc).size, 2, '两行都要参与排序，缺名字的不能整体沉底');

  // ③ 事件概率「24h 成交量」：未覆盖 ≠ 零成交
  const ev = [{ t: '甲', volume: 0, volume_missing: false }, { t: '乙', volume: 0, volume_missing: true }];
  const derived = ev.map(r => ({ ...r, volume_sort: r.volume_missing ? null : r.volume }));
  assert.deepEqual(sortRows(derived, { key: 'volume_sort', dir: 'asc' }).map(r => r.t), ['甲', '乙'],
    '真 0 在前、未覆盖在最后；直接排 volume 会把未覆盖读成零成交');

  // ④ r²：格子印的是 corr_coef 的平方，所以排序键必须是平方值（|r| 大的排前面，与符号无关）
  const tr = [{ d: '陡但反号', corr_coef: -0.95 }, { d: '平', corr_coef: 0.4 }];
  const withR2 = tr.map(r => ({ ...r, r2: r.corr_coef * r.corr_coef }));
  assert.equal(sortRows(withR2, { key: 'r2', dir: 'desc' })[0].d, '陡但反号');
  assert.equal(sortRows(tr, { key: 'corr_coef', dir: 'desc' })[0].d, '平',
    '对照：直接按 corr_coef 排会把拟合最好的那条判到后面 —— 这就是要派生的原因');
});

test('r² and 形状 sort on what the cell shows, not on a field that is not there', () => {
  // 10-14：这两张表从 `pages/Scores.tsx` 搬进了 `components/quant/TrendQuant.tsx`
  //（趋势评分并入分组自选的档案下方），判据本身一条没松。
  const sc = fs.readFileSync(new URL('components/quant/TrendQuant.tsx', SRC), 'utf8');
  // r² 那一格印的是 corr_coef 的平方，排序键必须是同一个算出来的值
  assert.match(sc, /r2: typeof r\.corr_coef === "number" \? r\.corr_coef \* r\.corr_coef : null/);
  assert.match(sc, /sortKey="r2"/);
  // 分量名那一格会回落机器键，排序键跟着同一串，否则显示与序不一致。
  // 2026-10-22 起第一选是后端从注册表带来的中文名 `component_name`（用户点名"看不懂 env_mood_v1"），
  // 回落顺序仍然是"格子里显示什么就按什么排"。
  assert.match(sc, /component_name: p\.component_name \?\? p\.component_label \?\? p\.component_key \?\? null/);
  // 「形状」是 sparkline 示记，不是数值 —— 它不该是可排序列
  assert.doesNotMatch(sc, /label="形状"[^>]*sortKey=/, 'sparkline 不是读数，按它排序没有意义');
  assert.match(sc, /title="这一列是当日 X\+Y 的形状示记/);
  // 同一张表不许有两份实现：旧那一屏只是薄薄一层调用
  const old = fs.readFileSync(new URL('pages/Scores.tsx', SRC), 'utf8');
  assert.doesNotMatch(old, /sortKey="slope"/, '窗口斜率那张表只剩一份实现');
  assert.match(old, /TrendQuantForPage/);
});

test('连板榜：默认序点亮在「连板」上，而「封板强度」接的是真字段、说明来自后端', () => {
  const mp = fs.readFileSync(new URL('../src/components/home/MarketPanorama.tsx', import.meta.url), 'utf8');
  // ① 默认序是后端给的（连板降序 → 同连板按首封先后）。列头必须把主键点亮，
  //    否则页面在标题里写"默认按连板"而列头一个箭头都没有，读者不知道现在吃的是后端行序。
  assert.match(mp, /const lbSort = useTableSort\(\{ key: "boards", dir: "desc" \}\)/);
  assert.match(mp, /同连板按首封先后/, '默认序要说清"同连板按首封先后（早→晚）"');

  // ② 那张表的列头顺序与格子顺序必须一致（错列不会被类型检查抓到，09-27 踩过）
  const from = mp.indexOf('<SortHeader label="名称" sortKey="name"');
  assert.ok(from > 0, '没定位到连板榜那张表');
  const block = mp.slice(from, mp.indexOf('</tbody>', from));
  const headOrder = ['名称', '连板', '几天几板', '首板日大盘情绪', '首封', '封板强度', '现价（实时）', '今日涨跌（实时）',
    '样本日成交额', '流通市值', '涨停原因', '概念'];
  for (let i = 0; i < headOrder.length - 1; i++) {
    assert.ok(block.indexOf(headOrder[i]) < block.indexOf(headOrder[i + 1]),
      `表头顺序应当是 ${headOrder.join(' → ')}，${headOrder[i]} 却排在 ${headOrder[i + 1]} 后面`);
  }

  // ③ 强度列的键 = 后端交的那个字段名，而且格子里读的是同一个
  assert.match(block, /label="封板强度" sortKey="seal_score"/);
  assert.match(block, /s\.seal_score/, '列头键与格子里印的值必须是同一个字段');
  assert.match(block, /title=\{s\.seal_note \|\| ""\}/, '每一格要答"这一行为是这个分"');
  // ④ 算法说明**只在后端那一份**（`duanxian/seal_strength.py` 现拼），页面显示它给的文字
  assert.match(block, /emotion\.seal_spec/, '悬停内容必须来自后端 spec，不许在前端另写一套分档');
  assert.doesNotMatch(mp, /封单资金\s*÷\s*流通市值（权重/, '前端不许抄一份权重表');
  // ⑤ 缺数是 —，不是 0
  assert.match(block, /三项都没给：算不出强度，不当成 0 分/);

  // ⑥「几天几板」：键是真字段，口径文字来自后端（页面只补一句它与「连板」的区别）
  assert.match(block, /label="几天几板" sortKey="board_ct"/);
  assert.match(block, /s\.days_boards/, '列头键与格子里显示的值必须同源（days_boards 就是那个字符串）');
  assert.match(block, /emotion\.days_boards_rule/, '悬停内容取自后端那份口径文字');
  assert.match(block, /数不出 ≠ 没有板|不是 0 板/, '数不出来要说清楚，不显示 0 板');
  assert.doesNotMatch(mp, /连续 2 个交易日没封/, '"几天算断"这个参数只在后端写一次');

  // ⑦「首板日大盘情绪」：档位是文字，能排的是它背后那个分值 —— 键必须就是格子里那个数
  assert.match(block, /label="首板日大盘情绪" sortKey="first_board_breadth_score"/);
  assert.match(block, /s\.first_board_breadth_score/, '列头键与格子里那个分值必须是同一个字段');
  assert.match(block, /s\.first_board_breadth\b/, '档位文字直接取后端那一个词，前端不重算分档');
  assert.match(block, /emotion\.first_board_rule/, '口径文字取自后端那一份（判据只住 duanxian/pg_sources.py）');
  assert.match(block, /s\.first_board_note/, '每一格要答"这一行的首板日是哪天、那天各档是多少"');
  assert.match(block, /没有行|无从定位/, '空着要分两种原因说，都不等于"那天没情绪"');
  assert.doesNotMatch(block, /冰点 −2 \/ 偏弱 −1/, '分档编码只在后端一份，前端只上色');
});

test('换手率榜的表头顺序与格子顺序一致（错列不会被任何类型检查抓到）', () => {
  const mp = fs.readFileSync(new URL('../src/components/home/MarketPanorama.tsx', import.meta.url), 'utf8');
  const from = mp.indexOf('<SortHeader label="换手率"');
  const body = mp.slice(from, mp.indexOf('</tbody>', from));      // 本表自己的行区，不是文件里第一个 tbody
  assert.ok(from > 0 && body.length > 200, '没定位到换手率榜那张表');
  const heads = [...mp.matchAll(/<SortHeader label="([^"]+)" sortKey="([^"]+)"/g)].map(m => [m[1], m[2]]);
  const at = (label: string) => heads.findIndex(([l]) => l === label);
  assert.ok(at('成交额') > at('换手率'), '表头里 换手率 必须在 成交额 之前');
  assert.ok(at('概念板块') > at('行业'), '表头里 概念板块 必须在 行业 之后');
  assert.equal(heads[at('换手率')][1], 'turnover');
  assert.equal(heads[at('概念板块')][1], 'concepts');
  // 格子顺序必须与表头同序：这一屏曾把换手率格插到行业前面，整列错位、类型检查一句都不报
  const cell = (re: RegExp) => body.search(re);
  assert.ok(cell(/s\.turnover == null/) >= 0 && cell(/yi\(s\.amount\)/) >= 0, '没找到换手率/成交额的格子');
  assert.ok(cell(/s\.turnover == null/) < cell(/yi\(s\.amount\)/), '格子里换手率要在成交额之前');
  assert.ok(cell(/yi\(s\.amount\)/) < cell(/yi\(s\.mcap\)/), '成交额要在总市值之前');
  assert.ok(cell(/s\.industry\}/) < cell(/s\.concepts \?\? /), '行业要在概念板块之前');
});

test('排序那一处：一次渲染只排一次（点一格高亮不该把整表重排）', () => {
  // 交叉高亮/hover/任何页面级 state 一变就整页重渲染，而 apply(rows) 是写在 JSX 里的。
  // 没缓存时每次重渲染都把这张表重新 map+sort 一遍（全站 63 处 .apply），
  // 用户看到的"点一下卡一下"就是这里。判据：实例上留一份"上次排好的结果"。
  const hook = fs.readFileSync(new URL('../src/lib/useSort.ts', import.meta.url), 'utf8');
  assert.match(hook, /const memo = useRef/, 'useTableSort 里要有那一格结果缓存');
  assert.match(hook, /last\.rows === rows && last\.sort === sort/, '缓存键必须是（同一份行数组 + 同一个排序档）');
  assert.match(hook, /memo\.current = \{ rows, sort, out \}/, '排完要写回缓存，否则下一轮还是重排');
  // 纯函数侧：没选排序档时必须原地返回（不新建数组），这是首页最常见的那一档
  const rows = [{ a: 3 }, { a: 1 }];
  assert.equal(sortRows(rows, null), rows, 'sort=null 还新建一份数组 = 每次渲染都白复制');
  const before = JSON.stringify(rows);
  const got = sortRows(rows as Record<string, unknown>[], { key: 'a', dir: 'asc' }) as { a: number }[];
  assert.deepEqual(got.map(r => r.a), [1, 3]);
  assert.equal(JSON.stringify(rows), before, '排序不许改动传进来的那份行（原地排会污染后端的原始顺序）');
});
