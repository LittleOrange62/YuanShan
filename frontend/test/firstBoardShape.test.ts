import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  agreeText, agreeTone, columnsFor, coverageText, direction, fmtKeyValue, headline,
  intText, observedText, rangeText, rateText, signedText,
  type FbPayload, type FbRow,
} from "../src/lib/firstBoardShape.ts";

/**
 * 「首板形状预示」的判读层测试。钉的是**会把读数说错**的那几处，不是排版：
 * 区间端点开闭、缺数必须空、"逐年同向 6/6" 不能被读成"6 年都赚钱"、
 * 以及 sortKey 必须等于格子里读到的字段名（写错不报错、也不换序）。
 */

const row = (o: Partial<FbRow>): FbRow => ({ label: "Q1", n: 100, ...o }) as FbRow;
const page = readFileSync(new URL("../src/pages/VolumeDouble.tsx", import.meta.url), "utf8");
const comp = readFileSync(new URL("../src/components/quant/FirstBoardShapeBoard.tsx", import.meta.url), "utf8");
const system = readFileSync(new URL("../src/pages/System.tsx", import.meta.url), "utf-8");

// ============================================================ 一、缺数一律空，不拿 0 顶上

test("收益类缺数是 —，0 老老实实写 0.00%", () => {
  assert.equal(signedText(null), "—");
  assert.equal(signedText(undefined), "—");
  assert.equal(signedText(Number.NaN), "—");
  assert.equal(signedText(0), "+0.00%");
  assert.equal(signedText(-1.9165), "-1.92%");
  assert.equal(signedText(2.7), "+2.70%");
  assert.equal(rateText(null), "—");
  assert.equal(rateText(2.7), "2.7%");
  assert.equal(intText(57992), "57,992");
  assert.equal(fmtKeyValue("ex5_med", null), "—");
  assert.equal(fmtKeyValue("seg4_rate", 2.7), "2.7%", "比例类列不该带正号");
});

// ============================================================ 二、分档区间：切点是左开右闭

test("区间写法与 SQL 的 col <= cut 一致：边界值只归一档", () => {
  assert.equal(rangeText(row({ label: "Q1", range: [null, 1] })), "≤1");
  assert.equal(rangeText(row({ label: "Q2", range: [1, 2] })), "1～2（不含 1）");
  assert.equal(rangeText(row({ label: "Q4", range: [3, null] })), ">3");
  assert.equal(rangeText(row({ label: "全部首板", range: [null, null] })), "全部");
  assert.equal(rangeText(row({ label: "Q2", range: null })), "—", "没交区间就说没交，不猜");
  assert.equal(rangeText(row({ label: "Q2", range: [0.35, 0.44] })), "0.35～0.44（不含 0.35）");
  assert.equal(observedText(row({ lo_obs: 0, hi_obs: 1 })), "实测 0～1");
  assert.equal(observedText(row({ lo_obs: 2, hi_obs: 2 })), "实测都是 2");
  assert.equal(observedText(row({ lo_obs: null, hi_obs: 2 })), "");
});

// ============================================================ 三、"6/6" 的原意

test("逐年同向那串数必须翻成人话，不许被读成几年赚钱", () => {
  assert.equal(agreeText("6/6"), "逐年与整族同向 6/6");
  assert.equal(agreeText(null), "—");
  assert.equal(agreeText("0/0"), "—", "分母为零不是 0/0 稳");
  assert.equal(agreeTone("6/6"), "good");
  assert.equal(agreeTone("3/6"), "mid");
  assert.equal(agreeTone("1/6"), "bad");
  assert.equal(agreeTone(null), "bad", "没算出来就不该绿着");
});

// ============================================================ 四、方向那句话怎么说

const up: FbRow[] = [row({ label: "Q1", seg4_rate: 1.4, range: [null, 1], year_agree: "1/6" }),
  row({ label: "Q2", seg4_rate: 1.8, range: [1, 2], year_agree: "4/6" }),
  row({ label: "Q3", seg4_rate: 2.1, range: [2, 3], year_agree: "5/6" }),
  row({ label: "Q4", seg4_rate: 2.7, range: [3, null], year_agree: "6/6" })];
const down: FbRow[] = [...up].reverse().map((r, i) => ({ ...r, label: `Q${i + 1}` }));

test("递增/递减只看实到档数，中间缺档就降为看不出", () => {
  assert.equal(direction(up, "seg4_rate").word, "递增");
  assert.equal(Number(direction(up, "seg4_rate").span?.toFixed(2)), 1.3, "浮点尾差不该渗进读数");
  assert.equal(direction(down, "seg4_rate").word, "递减");
  assert.equal(direction([up[0], up[2]], "seg4_rate").word, "递增", "只有两档也算方向");
  assert.equal(direction([up[0], row({ label: "Q2", seg4_rate: null }), up[2]], "seg4_rate").word,
    "档数不全，看不出", "中间那档没数时说单调是假话");
  assert.equal(direction([], "seg4_rate").word, "看不出");
});

test("一句话结论里点名最好/最差那档，并带上区间与逐年同向", () => {
  const s = headline(up, row({ label: "全部首板", n: 999, seg4_rate: 1.8 }), "seg4_rate", "≥4 板率");
  assert.match(s, /最突出的是 Q4（>3）/);
  assert.match(s, /最弱的是 Q1（≤1）/);
  assert.match(s, /比整族高 0.9 个百分点/);
  assert.match(s, /逐年与整族同向 6\/6/);
  assert.match(headline(up.map(r => ({ ...r, year_agree: null })), row({ label: "全部首板", n: 9, seg4_rate: 1.8 }),
    "seg4_rate", "≥4 板率"), /（— \/ —）/, "逐年没算出来就交 —，不许补一句好看的话");
  assert.equal(headline([], row({}), "seg4_rate", "x"), "", "少于两档不下结论");
});

// ============================================================ 五、窗口/筛选念出来的是接口给的坐标

test("覆盖句把窗口、样本、板别与两个筛子的状态都念出来", () => {
  const p: FbPayload = {
    coverage: { from: "2021-09-12", to: "2026-10-16", events: 57992, codes: 5094, latest_launch: "2026-10-16" },
    params: { months: 60, board: "全部", complete_only: true, exclude_locked: true },
  };
  const t = coverageText(p);
  assert.match(t, /窗口 2021-09-12 → 2026-10-16/);
  assert.match(t, /57,992 条首板事件/);
  assert.ok(!t.includes("含还在走的段"), "没关就不该说关");
  assert.match(coverageText({ ...p, params: { ...p.params, complete_only: false } }), /含还在走的段/);
  assert.match(coverageText({ ...p, params: { ...p.params, exclude_locked: false } }), /含次日一字买不进/);
  assert.match(coverageText({ ...p, params: { ...p.params, board: "创业板" } }), /只看创业板/);
});

// ============================================================ 六、接线：漏一处就是屏幕上少一块

test("表头每一列的 sortKey 都等于格子里读到的字段名", () => {
  const cols = columnsFor("ex5");
  assert.equal(cols.length, 11);
  const keys = cols.map(c => c.key);
  assert.ok(keys.includes("ex5_med"), "换了结果指标却没换排序键＝点了不排序");
  assert.ok(!keys.includes("ex10_med"));
  for (const k of keys) assert.ok(k === "label" || k === "n" || k.endsWith("_med") || k.endsWith("_rate")
    || k === "win5" || k === "seg2_rate" || k === "seg3_rate" || k === "seg4_rate" || k === "seg5_rate", k);
});

test("这一块真挂在倍量形态页上，并且登记进了系统自检清单", () => {
  assert.ok(page.includes('<FirstBoardShapeBoard />'), "调用点没接上");
  assert.ok(comp.includes('useProbe<FbPayload>("/quant/first-board-shape"'), "组件没发那条请求");
  assert.ok(system.includes('"/quant/first-board-shape"'), "System.tsx 的 CAPABILITIES 里没有这条");
  assert.ok(/path: "\/quant\/first-board-shape".*slow: true }/.test(system), "第一发 1~3 秒，要标 slow");
});

test("控件全部走接口参数（界面不自己再算一遍分档）", () => {
  for (const k of ["months", "bucket", "buckets", "metric", "board", "complete_only", "exclude_locked"]) {
    assert.ok(comp.includes(`${k}:`), `参数 ${k} 没进 query，改了控件等于没改`);
  }
  assert.match(comp, /if \(!data\) return null/, "Panel 的 children 是有数时才画的函数，缺这个判断会在没数时炸");
});

test("三处措辞红线：不美化、不空着、不编", () => {
  assert.match(comp, /逐年与整族同向|agreeText\(r\.year_agree\)/, "逐年那格必须用 agreeText 翻译");
  assert.match(comp, /取数口径|Caliber text=/, "口径原句要上屏");
  assert.match(comp, /<Caveats items=/, "caveats（含白名单退回、买不进被筛掉）不许折进默认收起的说明框");
  assert.match(comp, /双击一行开个股深度复盘|openReview\(s\.code\)/, "样本要能开复盘，且提示写在场");
});
