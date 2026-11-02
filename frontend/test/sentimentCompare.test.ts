import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  buildCompareRows, cellText, columnStamps, diffText, CALIBER_COLUMNS,
} from '../src/lib/sentimentCompare.ts';

/**
 * 首页「市场情绪」三口径对照的判读层 + 调用点接线。
 * 核心那条：三把尺并排只许各自带时间与分母，缺数交 null（屏上 —），
 * 不许把「没读到」画成 0，也不许把两个不同分母的比率算成一个差。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const panorama = fs.readFileSync(
  path.join(HERE, '../src/components/home/MarketPanorama.tsx'), 'utf8');

const LEGU = {
  up: 3772, down: 1670, flat: 122, zt: 60, zt_real: 57, dt: 25, dt_real: 21,
  active: "48.6%", breadth: "偏强", speculation: "活跃", date: "2026-10-26",
  breadth_score: 1, theme_score: 0.5,
};
const LIVE = {
  available: true, date: "2026-10-26", as_of: "14:55", phase: "尾盘",
  zt_count: 58, dt_count: 24, zb_count: 19, max_boards: 4, lianban_count: 14,
  seal_rate: 0.712, break_rate: 0.288, promotion_rate: 0.21,
  promotion_base: 82, promotion_base_date: "2026-10-23",
};
const LOCAL = {
  date: "2026-10-26", available: true, zt_count: 59, dt_count: 28, zb_count: 28,
  max_boards: 4, lianban_count: 15, seal_rate: 0.678, break_rate: 0.322,
  promotion_rate: 0.179, prev_date: "2026-10-23", prev_sealed: 84,
  ladder: [], lianban_stocks: [],
};

const rows = () => buildCompareRows(LEGU as any, LIVE as any, LOCAL as any);
const byKey = (k: string) => {
  const r = rows().find((x) => x.key === k);
  assert.ok(r, `少了 ${k} 这一行`);
  return r!;
};

test('十四行按点名的顺序交出来，一列都不许偷偷少', () => {
  assert.deepEqual(rows().map((r) => r.key), [
    "zt", "zt_real", "dt", "dt_real", "zb", "max_boards", "lianban",
    "seal_rate", "break_rate", "promotion_rate", "up", "down", "flat", "active",
  ]);
  assert.deepEqual(CALIBER_COLUMNS.map((c) => c.key), ["legu", "live", "local"]);
});

test('三列各就各位：乐咕独有的那几行，另两列是 null 不是 0', () => {
  const active = byKey("active");
  assert.equal(active.values.live.v, null);
  assert.equal(active.values.local.v, null);
  assert.equal(cellText(active, "legu"), "48.6%");
  // 文本档原样交出，不参与差值
  assert.equal(diffText(active, "vsLegu"), "—");
  assert.equal(diffText(active, "vsLive"), "—");
});

test('家数差按只数、比率差按百分点，两边都有数才算得出来', () => {
  assert.equal(diffText(byKey("zt"), "vsLegu"), "-1");        // 59 − 60（乐咕 zt 是普通档）
  assert.equal(diffText(byKey("zt"), "vsLive"), "+1");        // 59 − 58
  assert.equal(diffText(byKey("dt"), "vsLegu"), "+3");        // 28 − 25
  assert.equal(diffText(byKey("seal_rate"), "vsLive"), "-3.4pp");  // 0.678 − 0.712 = −3.4pp
  assert.equal(cellText(byKey("seal_rate"), "local"), "67.8%");
});

test('任一边缺数就交不出差，并说清是哪一种缺', () => {
  const half = buildCompareRows(LEGU as any,
                                { ...LIVE, zt_count: null } as any, LOCAL as any);
  const zt = half.find((r) => r.key === "zt")!;
  assert.equal(zt.values.live.v, null);
  assert.equal(cellText(zt, "live"), "—");
  assert.equal(diffText(zt, "vsLive"), "—");
  // 乐咕本来就没有炸板这一档 ⇒ 那一格不是"—（没读到）"，要念出"没这一档"
  assert.equal(diffText(byKey("zb"), "vsLegu"), "乐咕没这一档");
});

test('晋级率两列的分母不同一场，这一行永远不算差', () => {
  const promo = byKey("promotion_rate");
  assert.equal(promo.diff.vsLive, null);
  assert.match(diffText(promo, "vsLive"), /不比/);
  assert.equal(promo.values.live.note, "分母 2026-10-23 涨停 82 家");
  assert.equal(promo.values.local.note, "分母 2026-10-23 封住 84 只");
});

test('真读到 0 就是 0，与「没读到」分得开', () => {
  const zero = buildCompareRows(LEGU as any, LIVE as any,
                                { ...LOCAL, zt_count: 0 } as any);
  const zt = zero.find((r) => r.key === "zt")!;
  assert.equal(cellText(zt, "local"), "0 家");
  assert.equal(diffText(zt, "vsLive"), "-58");
});

test('实时池取不到数：整列 null，但 reason 必须由表头念出来', () => {
  const off = buildCompareRows(LEGU as any,
                               { available: false, date: "2026-10-27",
                                 reason: "尚无今日行情 · 显示 2026-10-26 数据" } as any,
                               LOCAL as any);
  assert.equal(off.find((r) => r.key === "zt")!.values.live.v, null);
  const stamps = columnStamps(LEGU as any,
                              { available: false, reason: "尚无今日行情" } as any,
                              LOCAL as any);
  assert.equal(stamps.live.stamp, "");
  assert.equal(stamps.live.warn, "尚无今日行情");
  assert.equal(stamps.legu.stamp, "2026-10-26 收盘");
  assert.equal(stamps.local.stamp, "2026-10-26 收盘快照");
});

test('库里没有这一场 ≠ 今天没有涨停：local 列空并念 reason', () => {
  const none = buildCompareRows(LEGU as any, LIVE as any,
                                { date: "2026-10-27", available: false,
                                  reason: "库里没有 2026-10-27 的行，该跑 e1 的 sentiment 步" } as any);
  assert.equal(none.find((r) => r.key === "lianban")!.values.local.v, null);
  const stamps = columnStamps(LEGU as any, LIVE as any,
                              { date: "2026-10-27", available: false, reason: "库里没有这一场" } as any);
  assert.equal(stamps.local.warn, "库里没有这一场");
});

// ---------------------------------------------------------------- 调用点接线

test('三块并成一块：旧的实时池标题与短线情绪头部格子都不许留着', () => {
  assert.match(panorama, /buildCompareRows\(/, "组件没调用判读层");
  assert.match(panorama, /columnStamps\(/, "表头没时间戳，三列会被读成同一时刻");
  assert.doesNotMatch(panorama, /东财实时池 · 盘中读数\n?\s*<Caliber/, "4a 那块还立着标题");
  assert.doesNotMatch(panorama, /关键计数/, "短线情绪头部那组格子没删干净");
  assert.doesNotMatch(panorama, /打板情绪比率/, "短线情绪头部那组格子没删干净");
  // 实时池的标题只准作为**列名**出现（CALIBER_COLUMNS 里那份），组件不再自己抄一份中文列名
  const columnLabels = (panorama.match(/liveEmo\.zt_count/g) || []).length;
  assert.equal(columnLabels, 0, "组件里还在逐格读 liveEmo 的字段，说明老格子没拆");
  assert.equal((panorama.match(/emotion\.seal_rate/g) || []).length, 0, "组件里还在读 emotion.seal_rate");
});

test('判读层交出去的每一段屏上文字都没有裸星号', () => {
  // 差值格与说明格是裸 JSX 文字、不过 `plain()`，星号会原样上屏；
  // 所以查的是**函数交出来的字符串**，不是源码里的注释与 Caliber 文案。
  const cases: any[][] = [
    [LEGU, LIVE, LOCAL],
    [LEGU, { ...LIVE, available: false, reason: "尚无今日行情" }, LOCAL],
    [null, LIVE, { date: "2026-10-27", available: false, reason: "库里没有这一场" }],
  ];
  for (const [g, l, e] of cases) {
    for (const r of buildCompareRows(g as any, l as any, e as any)) {
      const texts = [r.label, r.hint, cellText(r, "legu"), cellText(r, "live"), cellText(r, "local"),
                     diffText(r, "vsLegu"), diffText(r, "vsLive"),
                     r.values.live.note || "", r.values.local.note || ""];
      for (const t of texts) assert.ok(!/\*\*/.test(t), `裸星号会直接上屏：${t}`);
    }
    const stamps = columnStamps(g as any, l as any, e as any);
    for (const c of CALIBER_COLUMNS) {
      const s = stamps[c.key];
      assert.ok(!/\*\*/.test(`${s.stamp}${s.warn || ""}`), `表头那行有星号：${c.key}`);
    }
  }
});

test('默认只摆本地那一列；另一把尺切出来才显示，切过去才去取（2026-10-28 用户点名）', () => {
  // ① 默认档是"不对照"——不是默认摆三列再藏两列
  assert.match(panorama, /const \[cmp, setCmp\] = useState<"none" \| "legu" \| "live">\("none"\)/,
    '默认必须是不对照（只看本地库）');
  // ② 显示哪几列由下拉决定，本地永远在第一列；**列名仍只住 sentimentCompare 那一份**
  assert.match(panorama, /wanted: CaliberKey\[\] = cmp === "none" \? \["local"\] : \["local", cmp\]/);
  assert.match(panorama, /CALIBER_COLUMNS\.filter\(\(c\) => c\.key !== "local"\)\.map/);
  assert.doesNotMatch(panorama, /<option value="legu">乐咕/, '下拉选项不许再抄一份列名（两处各写一遍迟早走偏）');
  // ③ 东财实时池不进默认的 5 秒轮询组：不切过去就一发都不打
  assert.match(panorama, /if \(cmpRef\.current === "live"\) api\.liveEmotion\(\)/,
    'loadLive 里必须条件化，否则默认状态每 5 秒白打一发');
  assert.match(panorama, /useEffect\(\(\) => \{ cmpRef\.current = cmp; \}, \[cmp\]\)/,
    '定时器复用的 loadLive 会带旧闭包，开关必须走 ref');
  assert.match(panorama, /if \(k === "live" && !liveEmo\) api\.liveEmotion\(\)/,
    '切过去那一刻要发第一发（否则表上只有"—"却没人去取）');
  // ④ 只留一根差值列；没对照时差值列整根不存在
  assert.match(panorama, /const diffKey = cmp === "legu" \? "vsLegu" as const : cmp === "live" \? "vsLive" as const : null/);
  assert.match(panorama, /\{diffKey && \(\n\s*<th/);
  assert.doesNotMatch(panorama, /diffText\(r, "vsLegu"\)[\s\S]{0,160}diffText\(r, "vsLive"\)/,
    '两根差值列同时在 = 旧形状，说明没跟着下拉走');
  // ⑤ 刚切过去、第一发没回来要说"取数中"，不能只留一个 —（会被读成"这一刻没数"）
  assert.match(panorama, /盘中截面取数中/);
  // ⑥ 乐咕那一列不能谎称"不取"：同一份 overview 还喂上面两格卡片与板块资金 ⇒ 这话必须在悬停里说清
  assert.match(panorama, /marketOverview[\s\S]{0,220}乐咕|乐咕那一列不额外花请求/);
});

test('只摆一列时不许摊成两列的表：紧凑两栏、该说的照样说（10-31 用户点名）', () => {
  /* 实测默认档（不对照）那张表 14 行 × 37px ≈ 518px 高，每行只有一个数字，
     行高还被「口径」那颗小芯片顶成两行 —— 纵向空间全花在表格骨架上。 */
  const code = panorama.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\/\/.*$/gm, '');
  assert.match(code, /const compact = cols\.length === 1 && !diffKey;/,
    '判据必须是"只有一列且不算差值"（列数由那个下拉决定，写死档名就会漂）');
  assert.match(code, /\{compact \? compactView : \(/, '紧凑栏与那张表二选一，不许叠着画');
  // 紧凑栏不能因为"省地方"把话说少：列名与它自己的时间戳、每格的分子分母、列级没读到的原因
  assert.match(code, /stamp\.stamp \|\| "日期未确认"/);
  assert.match(code, /r\.values\[c\.key\]\.note &&/);
  assert.match(code, /stamp\.warn && /);
  assert.match(code, /grid gap-x-6 sm:grid-cols-2/, '要两栏网格，不是一条长列表（那还是每行一个数）');
  // 缺数声明写在条件之外：两种形状都得有（写在表里面＝紧凑档没有）
  const closing = code.indexOf('compactView : (');
  const opened = code.indexOf('<NoticeBar kind="missing">');
  const condEnd = code.indexOf(')}', code.indexOf('</table>'));
  assert.ok(closing > 0 && opened > 0 && condEnd > closing && opened > condEnd,
    `顺序应为「条件开 → 表 → 条件关 → NoticeBar」：${closing}/${condEnd}/${opened}`);
});
