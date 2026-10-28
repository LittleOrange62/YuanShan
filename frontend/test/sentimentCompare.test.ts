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
