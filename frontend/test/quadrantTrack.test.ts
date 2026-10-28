import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  COHORTS_FALLBACK, COPY, DASH, KEYS_FALLBACK, LIMITS, MONTHS_FALLBACK, PRESCREENED_COHORTS,
  RANK_DELTA_HEADER, SEG4_PRESET_COPY, WINDOWS_FALLBACK, answerCellText, answerColumns, answerRows,
  caveatItems, coverageSentence, clampLimit, clampMonths, clampWindow, deltaTone, deltaWord,
  detailCellText, detailColumns, ex5GapText, familyHasQuadrant, groupCellText, groupColumns,
  groupRows, intText, isPrescreenedCohort, keyCellText, keyColumns, keyHeadline, keyOptionsFor,
  noQuadrantNote, optionsFor, queryParams, quadCountText, rankDeltaText, ratioText, rateText,
  rangeText, seg4CellText, shareText, sharesText, shortCoverageText, shortTitle, valueText,
  versionWarning, windowShort,
  type SqAggRow, type SqKeyRow, type SqPayload,
} from "../src/lib/quadrantTrack.ts";

/**
 * 「启动前 30 场四维象限轨迹」判读层的测试。钉的是**会把读数说反**的那几处，不是排版：
 *  ① `quad_*` 为 null 的格子必须是 `—`，绝不能印成 0（0 会被读成"翻转过 0 次"这条实质结论）；
 *  ② `rank_delta` 的方向：正数＝名次上升（rank 越小越靠前），列头必须把这句写出来；
 *  ③ `bars < window` 的灰显判定（不满窗仍进中位数聚合，但不与满窗并排读强弱）；
 *  ④ 后端 caveats 是**字符串数组**，映射成 `{code,text}` 时要编号、丢空串；
 *  ⑤ 五档母体名单，`anyfirst`（全部首板，不筛结果）排第一；另外四档是按结果筛过的，
 *     那一档里「≥4 板率」这一格不许印成一个像结论的百分比（`seg4CellText`）；
 *  ⑥ `queryParams` 真的把五个参数都拼进 URL（换控件必须重发请求，不是前端再筛一遍）；
 *  ⑦ **量纲**：`outcome_gain4` 后端交的是小数比例（0.4626＝+46.26%），只能走 `ratioText`；
 *     `rateText` 的入参已经是百分数，两者语义不许并成一份（今天屏上 `0.5%` 就是这么来的）。
 *
 * 排序键与接线只做到"键名在这份契约里真存在、组件确实发那条请求"为止 ——
 * 浏览器里逐列点击实测由人做，这里不声称实测过排序。
 */

const lib = readFileSync(new URL("../src/lib/quadrantTrack.ts", import.meta.url), "utf8");
const comp = readFileSync(new URL("../src/components/quant/StartupQuadrantTrackBoard.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/pages/VolumeDouble.tsx", import.meta.url), "utf8");
const system = readFileSync(new URL("../src/pages/System.tsx", import.meta.url), "utf-8");

// ============================================ 契约样本：字段名逐个照接口合同抄（拼错只会永远显示 —）
// ⚠️ `outcome_gain4` 合同里交的是**小数比例**（definitions 原句：sv-2 的 trigger_gain，0.4641＝+46.41%），
// 所以这几份 fixture 也按小数比例写 —— 写成 61.2 就等于替后端撒了个谎，测试还会绿。

const KEY_OPER: SqKeyRow = {
  key: "oper_score", label: "综合分", n: 412,
  rank_first_med: 1820, rank_last_med: 640, rank_delta_med: 1180.5,
  rank_up_share: 0.62, rank_down_share: 0.31,
  pct_first_med: 35.2, pct_last_med: 71.4,
  value_first_med: -6.5, value_last_med: 21.3,
  quad_switches_med: 2, quad_last_switch_gap_med: 1,
  quad_first: { Q1: 0.31, Q2: 0.22, Q3: 0.19, Q4: 0.28 },
  quad_last: { Q1: 0.44, Q2: 0.18, Q3: 0.12, Q4: 0.26 },
};
const KEY_VOL: SqKeyRow = {
  key: "vol_trend", label: "量能趋势", n: 402,
  rank_first_med: 990, rank_last_med: 1210, rank_delta_med: -220,
  rank_up_share: 0.28, rank_down_share: 0.66,
  pct_first_med: 55.0, pct_last_med: 41.8,
  value_first_med: 1.06, value_last_med: 0.98,
  // 其余六族：象限那四列后端交 null（不是 0）
  quad_switches_med: null, quad_last_switch_gap_med: null, quad_first: null, quad_last: null,
};
const B_Q1: SqAggRow = {
  label: "Q1", range: [null, -40], n: 150, rank_delta_med: -260,
  seg4_rate: 8.4, seg_len_med: 2, r5_med: -3.1, ex5_med: -5.2, win5: 31.5, quad_switches_med: 1,
};
const B_Q2: SqAggRow = {
  label: "Q2", range: [-40, 300], n: 160, rank_delta_med: 12,
  seg4_rate: 14.2, seg_len_med: 3, r5_med: 1.4, ex5_med: -0.8, win5: 44.0, quad_switches_med: 2,
};
const B_Q3: SqAggRow = {
  label: "Q3", range: [300, null], n: 102, rank_delta_med: 1450,
  seg4_rate: 26.3, seg_len_med: 4, r5_med: 6.9, ex5_med: null, win5: 52.8, quad_switches_med: 3,
};
const BASE: SqAggRow = {
  label: "全部母体事件", range: null, n: 412, rank_delta_med: 18,
  seg4_rate: 15.0, seg_len_med: 3, r5_med: 1.9, ex5_med: -1.4, win5: 42.2, quad_switches_med: 2,
};
const DETAIL_OPER = {
  code: "300750.SZ", name: "宁德时代", board: "创业板", launch_date: "2026-06-15",
  cohort: "board4_or_gain45", capability_key: "oper_score", capability_label: "综合分",
  bars: 30, value_first: -6.5, value_last: 21.3, value_delta: 27.8,
  rank_first: 2400, rank_last: 500, rank_delta: 1900, rank_best: 380, rank_worst: 2400,
  quad_first: "Q4", quad_last: "Q1", quad_switches: 3, quad_last_switch_gap: 0,
  outcome_len: 5, outcome_gain4: 0.612,
};
const DETAIL_VOL = {
  code: "300750.SZ", name: "宁德时代", board: "创业板", launch_date: "2026-06-15",
  cohort: "board4_or_gain45", capability_key: "vol_trend", capability_label: "量能趋势",
  bars: 30, value_first: 1.06, value_last: 0.98, value_delta: -0.08,
  rank_first: 1200, rank_last: 1500, rank_delta: -300, rank_best: 980, rank_worst: 1900,
  quad_first: null, quad_last: null, quad_switches: null, quad_last_switch_gap: null,
  outcome_len: 5, outcome_gain4: 0.612,
};
const DETAIL_SHORT = {
  code: "688223.SH", name: "晶科能源", board: "科创板", launch_date: "2026-02-03",
  cohort: "board4", capability_key: "oper_score", capability_label: "综合分",
  bars: 12, value_first: 3.1, value_last: 9.9, value_delta: 6.8,
  rank_first: 700, rank_last: 400, rank_delta: 300, rank_best: 380, rank_worst: 700,
  quad_first: "Q2", quad_last: "Q1", quad_switches: 1, quad_last_switch_gap: 2,
  outcome_len: 4, outcome_gain4: 0.4626,
};
const DETAIL_BJ = {
  code: "839790.BJ", name: "北交所样本", board: "北交所", launch_date: "2026-05-08",
  cohort: "jump40", capability_key: "oper_score", capability_label: "综合分",
  bars: 30, value_first: 0.5, value_last: 4.0, value_delta: 3.5,
  rank_first: 3000, rank_last: 2200, rank_delta: 800, rank_best: 2100, rank_worst: 3400,
  quad_first: "Q3", quad_last: "Q2", quad_switches: 2, quad_last_switch_gap: 5,
  outcome_len: 3, outcome_gain4: null,
};

const PAYLOAD: SqPayload = {
  available: true, spec_version: "sqt-1",
  params: { cohort: "board4_or_gain45", cohort_text: "4 板及以上 或 4 日涨幅>45%",
            months: 6, window: 30, key: "", limit: 400 },
  coverage: { from: "2026-01-27", to: "2026-10-16", events: 412, codes: 268,
              earliest_score_date: "2026-01-27", window_full_events: 302, window_short_events: 110 },
  keys: [KEY_OPER, KEY_VOL],
  buckets: [B_Q1, B_Q2, B_Q3],
  base: BASE,
  rows: [DETAIL_OPER, DETAIL_VOL, DETAIL_SHORT, DETAIL_BJ],
  caveats: ["窗口不含启动日当天。", "", "不满窗的行仍进中位数聚合。"],
  caliber: "母体＝analytics.launch_event；名次取自 analytics.score 的 rank_in_market。",
  definitions: { rank_delta: "rank_first − rank_last，正数＝名次上升" },
  options: {
    cohorts: COHORTS_FALLBACK,
    keys: [{ value: "", label: "全部七个分量" }, { value: "oper_score", label: "综合分" }],
    months: MONTHS_FALLBACK,
    windows: WINDOWS_FALLBACK,
  },
};

const col = (list: ReturnType<typeof keyColumns>, k: string) => {
  const c = list.find(x => x.key === k);
  assert.ok(c, `列 ${k} 不存在`);
  return c;
};

// ============================================ 一、null 不许渲染成 0（本项目最贵的一类错）

test("quad_* 为 null 的格子交出 —，真 0 才写 0", () => {
  assert.equal(quadCountText(null), DASH);
  assert.equal(quadCountText(undefined), DASH);
  assert.equal(quadCountText(Number.NaN), DASH);
  assert.equal(quadCountText(0), "0", "翻转过 0 次是实质结论，必须与 — 长得不一样");
  assert.equal(keyCellText(col(keyColumns(), "quad_switches_med"), KEY_VOL), DASH,
    "量能趋势这一族没有象限：不许显示 0");
  assert.equal(keyCellText(col(keyColumns(), "quad_last_switch_gap_med"), KEY_VOL), DASH);
  assert.equal(keyCellText(col(keyColumns(), "quad_switches_med"), KEY_OPER), "2");
  assert.equal(familyHasQuadrant(KEY_VOL), false);
  assert.equal(familyHasQuadrant(KEY_OPER), true);
  assert.match(noQuadrantNote(KEY_VOL), /这一族没有象限读数/);
  assert.match(noQuadrantNote(KEY_VOL), /不是翻转过 0 次|0 才是翻转过零次/);
  assert.equal(noQuadrantNote(KEY_OPER), "", "有象限的那一族不该念这句");
  // 分布整族 null → —；分布里的 0 是真占比 0，照原样印
  assert.equal(sharesText(null), DASH);
  assert.equal(sharesText({}), DASH);
  assert.equal(sharesText({ Q1: null, Q2: null }), DASH, "全 null 的空壳不是 0%");
  assert.match(sharesText(KEY_OPER.quad_first), /Q1 31\.0%/);
  assert.equal(sharesText({ Q1: 0, Q2: 0.5, Q3: 0.2, Q4: 0.3 }), "Q1 0.0% · Q2 50.0% · Q3 20.0% · Q4 30.0%");
});

test("缺数一律 —：整数、比率、占比、分量值都不拿 0 顶上", () => {
  assert.equal(intText(null), DASH);
  assert.equal(intText(0), "0");
  assert.equal(intText(57992), "57,992");
  assert.equal(rankDeltaText(null), DASH);
  assert.equal(shareText(null), DASH);
  assert.equal(valueText(null), DASH);
  assert.equal(answerCellText(col(answerColumns(), "ex5_med"), B_Q3), DASH,
    "北交所那批没有分类指数：超额缺的是 —，不是 0.00%");
  assert.equal(answerCellText(col(answerColumns(), "ex5_med"), B_Q2), "-0.80%");
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_Q1, "anyfirst"), "8.4%");
});

test("占比读数不猜量纲：0~1 的分数与 0~100 的百分数都印成百分数", () => {
  assert.equal(shareText(0.62), "62.0%");
  assert.equal(shareText(62), "62.0%", "接口若直接交百分数，不许印成 6200.0%");
  assert.equal(shareText(1), "100.0%");
  assert.equal(shareText(0), "0.0%", "0 是有效读数（这一族没有一条上升），不许吞成 —");
});

// ============================================ 二、rank_delta 的方向（最容易读反的一条）

test("名次变化：正数＝上升，列头把这句写死", () => {
  assert.equal(RANK_DELTA_HEADER, "名次变化（正＝上升）");
  assert.equal(rankDeltaText(1180.5), "+1180.5");
  assert.equal(rankDeltaText(1900), "+1900");
  assert.equal(rankDeltaText(-220), "-220");
  assert.equal(rankDeltaText(0), "0");
  assert.equal(deltaTone(5), "up");
  assert.equal(deltaTone(-5), "down");
  assert.equal(deltaTone(0), "flat");
  assert.equal(deltaTone(null), "none");
  assert.equal(deltaWord(5), "上升");
  assert.equal(deltaWord(-5), "下降");
  assert.equal(deltaWord(null), DASH, "没数不说方向");
  assert.equal(COPY.rankDelta.includes("正数＝名次上升"), true);
  assert.equal(keyCellText(col(keyColumns(), "rank_delta_med"), KEY_OPER), "+1180.5");
  assert.equal(groupCellText(col(groupColumns(), "rank_delta_worst"),
    groupRows([DETAIL_OPER, DETAIL_VOL], 30)[0]), "-300", "最差的那一族是下滑的，不能被均值盖掉");
});

test("一句话方向只数接口交的中位数，正负说的是名次不是好坏", () => {
  const s = keyHeadline([KEY_OPER, KEY_VOL]);
  assert.match(s, /综合分（\+1180\.5，上升）/);
  assert.match(s, /量能趋势（-220，下降）/);
  assert.match(s, /2 族里有 1 族的中位名次在上升/);
  assert.match(keyHeadline([KEY_VOL]), /往后列走得最多的是 量能趋势/);
  assert.match(keyHeadline([]), /说不出方向/, "一族都没交就不下结论");
  assert.match(keyHeadline([{ ...KEY_VOL, rank_delta_med: null }]), /说不出方向/);
});

// ============================================ 三、bars < window：灰显判定与那句说明

test("窗口攒满判定：缺任一边都是不知道，不当满窗也不当不满", () => {
  assert.equal(windowShort(30, 30), false);
  assert.equal(windowShort(31, 30), false);
  assert.equal(windowShort(12, 30), true);
  assert.equal(windowShort(null, 30), null);
  assert.equal(windowShort(12, null), null);
});

test("不满窗的行仍进中位数聚合，但不与满窗并排读强弱", () => {
  const t = shortTitle(12, 30);
  assert.match(t, /不满窗：往前只有 12 个有效交易日/);
  assert.match(t, /仍进后端的中位数聚合/);
  assert.match(t, /不与满窗的行并排读强弱/);
  assert.equal(shortTitle(30, 30).startsWith("满窗"), true);
  assert.match(shortTitle(null, 30), /无从判断/);
});

test("分组以后不满窗的账要落在事件行上（任一分量不满就算这一条不满）", () => {
  const gs = groupRows([DETAIL_OPER, DETAIL_VOL, DETAIL_SHORT], 30);
  assert.equal(gs.length, 2);
  const a = gs.find(g => g.code === "300750.SZ");
  const b = gs.find(g => g.code === "688223.SH");
  assert.equal(a?.window_short, false);
  assert.equal(b?.window_short, true);
  assert.equal(a?.key_count, 2, "两族并在同一只票的同一场启动下");
  assert.equal(a?.bars, 30);
  assert.equal(b?.bars, 12);
});

test("覆盖那两句点名不满窗条数，并说清这是覆盖不是缺数", () => {
  const t = coverageSentence(PAYLOAD);
  assert.match(t, /窗口 2026-01-27 → 2026-10-16/);
  assert.match(t, /共 412 条启动事件 \/ 268 只票/);
  assert.match(t, /满窗 302 条、不满窗 110 条/);
  assert.match(t, /不是库里缺数/);
  assert.match(t, /仍进中位数聚合/);
  assert.match(t, /母体「4 板及以上 或 4 日涨幅>45%」/);
  assert.match(t, /启动日前 30 场/);
  assert.match(shortCoverageText({}), /没交满窗\/不满窗的条数/, "后端没交就明说，不自己数");
});

// ============================================ 四、caveats 是字符串数组：映射要编号

test("后端字符串数组 → Caveats 的 {code,text}：编号连续、空串丢号不占位", () => {
  assert.deepEqual(caveatItems(["甲", "乙"]), [{ code: "sqt1", text: "甲" }, { code: "sqt2", text: "乙" }]);
  assert.deepEqual(caveatItems(PAYLOAD.caveats), [
    { code: "sqt1", text: "窗口不含启动日当天。" },
    { code: "sqt2", text: "不满窗的行仍进中位数聚合。" },
  ], "空串不占一个号（界面上会出现空条目）");
  assert.deepEqual(caveatItems(null), []);
  assert.deepEqual(caveatItems([null, undefined, "   "]), []);
  assert.deepEqual(caveatItems(["  带空格的一句  "]), [{ code: "sqt1", text: "带空格的一句" }]);
});

// ============================================ 五、四档母体名单与控件选项

test("母体控件确实是五档，anyfirst（全部首板，不筛结果）排第一", () => {
  assert.deepEqual(COHORTS_FALLBACK.map(c => c.value),
    ["anyfirst", "board4", "board4_45", "board4_or_gain45", "jump40"]);
  assert.equal(COHORTS_FALLBACK[0].label, "全部首板（不筛结果）");
  assert.equal(COHORTS_FALLBACK.find(c => c.value === "board4_or_gain45")?.label,
    "4 板及以上 或 4 日涨幅>45%");
  // 接口交了就吃接口的，没交才退回这份兜底（兜底里不许藏编造的键名）
  assert.equal(optionsFor(null, COHORTS_FALLBACK).length, 5);
  assert.equal(optionsFor([{ value: "board4", label: "只有一档" }], COHORTS_FALLBACK).length, 1);
  assert.equal(optionsFor([{ value: 1, label: "" }], MONTHS_FALLBACK).length, 12, "没标签的选项不算数");
  assert.equal(WINDOWS_FALLBACK.map(w => w.value).join(","), "10,20,30");
  assert.equal(KEYS_FALLBACK.length, 1, "七族的名字由接口交，界面只兜底一档全部");
  assert.equal(LIMITS[2], 600);
});

// ============================================ 五之二、anyfirst：≥4 板率什么时候才算一个读数

test("按结果筛过的那四档：≥4 板率这一格不许印成一个像结论的百分比", () => {
  assert.deepEqual(PRESCREENED_COHORTS, ["board4", "board4_45", "board4_or_gain45", "jump40"]);
  assert.equal(isPrescreenedCohort("board4"), true);
  assert.equal(isPrescreenedCohort("board4_or_gain45"), true);
  assert.equal(isPrescreenedCohort("anyfirst"), false, "全部首板这一档必须有区分度，不许被一起关掉");
  assert.equal(isPrescreenedCohort(null), false, "母体没交就照普通比率印，不下那句同义反复的结论");
  assert.equal(isPrescreenedCohort(""), false);
  // 那四档里：这一格上屏的是那句话本身（不是 100.0%，也不是 26.3%）
  assert.equal(SEG4_PRESET_COPY, "这一档按结果筛过，这个数恒等于母体定义");
  assert.equal(seg4CellText(26.3, "board4"), SEG4_PRESET_COPY);
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_Q3, "board4_or_gain45"), SEG4_PRESET_COPY);
  // anyfirst（以及母体没交）：这才是一个结果读数
  assert.equal(seg4CellText(26.3, "anyfirst"), "26.3%");
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_Q3, "anyfirst"), "26.3%");
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_Q3), "26.3%");
  assert.equal(seg4CellText(null, "anyfirst"), DASH, "缺还是缺，别拿那句盖掉 —");
  // 其余列不许被这一档带跑：胜率仍是百分比、超额仍是带符号收益
  assert.equal(answerCellText(col(answerColumns(), "win5"), B_Q3, "board4"), "52.8%");
  assert.equal(answerCellText(col(answerColumns(), "r5_med"), B_Q3, "board4"), "+6.90%");
  assert.match(COPY.cohort, /全部首板（不筛结果）/);
  assert.match(COPY.cohort, /先按结果筛过/);
  assert.match(COPY.cohort, /恒等于|等于筛条件|读它等于读自己/);
  assert.match(col(answerColumns(), "seg4_rate").title, /anyfirst/);
});

test("这一档默认选中 anyfirst，且那句区分度说明在折叠框外", () => {
  assert.match(comp, /cohort: "anyfirst"/, "默认母体必须是全部首板（不筛结果）");
  assert.match(comp, /answerCellText\(c, r, echoedCohort\)/, "≥4 板率那一格必须按接口回显的母体来判");
  assert.ok(comp.includes("d.params?.cohort ?? p.cohort"), "母体以接口回显为准，state 只兜底");
  assert.match(comp, /\{COPY\.cohort\}/);
  assert.ok(comp.indexOf("{COPY.cohort}") < comp.indexOf("<ExplainBox"), "区分度那句不许折进默认收起的框");
  assert.match(comp, /SEG4_PRESET_COPY/, "那句同义反复要由 lib 交，组件不许自己抄一遍");
});

test("接口没交「全部」那一档时界面补一条在最前（当前值必须在选项里找得到）", () => {
  const seven = Array.from({ length: 7 }, (_, i) => ({ value: `k${i}`, label: `族 ${i + 1}` }));
  const got = keyOptionsFor(seven);
  assert.equal(got.length, 8);
  assert.equal(got[0].value, "");
  assert.equal(keyOptionsFor([{ value: "", label: "全部" }, ...seven]).length, 8, "接口已经交了全部那一档就不重复补");
  assert.equal(keyOptionsFor(null)[0].value, "", "接口没交键清单时也只能有「全部」这一档，不编族名");
});

// ============================================ 六、参数真的发出去（换控件＝换一次取数）

test("queryParams 把五个参数都拼进 URL", () => {
  const q = queryParams({ cohort: "board4_or_gain45", months: 6, window: 20, key: "oper_score", limit: 400 });
  assert.deepEqual(Object.keys(q).sort(), ["cohort", "key", "limit", "months", "window"]);
  const url = `/api/quant/startup-quadrant-track?${new URLSearchParams(
    Object.entries(q).map(([k, v]) => [k, String(v)])).toString()}`;
  for (const k of ["cohort", "months", "window", "key", "limit"]) assert.ok(url.includes(`${k}=`), `${k} 没进 URL`);
  assert.match(url, /cohort=board4_or_gain45/);
  assert.match(url, /months=6/);
  assert.match(url, /window=20/);
  assert.match(url, /key=oper_score/);
  assert.match(url, /limit=400/);
});

test("参数越界在发出之前就被夹住（后端会拒的值不该发过去）", () => {
  assert.equal(clampMonths(0), 1);
  assert.equal(clampMonths(99), 12);
  assert.equal(clampMonths("x"), 6);
  assert.equal(clampWindow(15), 30, "只认 10/20/30，别的回默认 30");
  assert.equal(clampWindow(10), 10);
  assert.equal(clampLimit(9999), 600, "明细上限后端硬顶 600");
  assert.equal(clampLimit(-1), 1);
  const q = queryParams({ cohort: "board4", months: 40, window: 7, key: "", limit: 5000 });
  assert.deepEqual(q, { cohort: "board4", months: 12, window: 30, key: "", limit: 600 });
});

// ============================================ 七、表二：三档 × 结果 + 并排的 base

test("答案表把 base 那一行钉在最前，三档照接口原样", () => {
  const rows = answerRows([B_Q1, B_Q2, B_Q3], BASE);
  assert.equal(rows.length, 4);
  assert.equal(rows[0], BASE, "base 必须在第一行，且不参与分档排序");
  assert.equal(answerRows([B_Q1], null)[0], B_Q1, "后端没交 base 就只画档，不编一行基线");
  assert.equal(answerCellText(col(answerColumns(), "label"), rows[0]), "全部母体事件");
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_Q3), "26.3%");
  assert.equal(answerCellText(col(answerColumns(), "seg_len_med"), B_Q3), "4");
  assert.equal(answerCellText(col(answerColumns(), "r5_med"), B_Q3), "+6.90%");
  assert.equal(answerCellText(col(answerColumns(), "win5"), B_Q3), "52.8%");
  assert.equal(answerCellText(col(answerColumns(), "rank_delta_med"), B_Q3), "+1450");
  assert.equal(answerCellText(col(answerColumns(), "quad_switches_med"), B_Q3), "3");
});

test("分档区间与首板那块同一种写法：左开右闭，没交就说没交", () => {
  assert.equal(rangeText(B_Q1), "≤-40");
  assert.equal(rangeText(B_Q2), "-40～300（不含 -40）");
  assert.equal(rangeText(B_Q3), ">300");
  assert.equal(rangeText(BASE), "全部", "base 那行没区间就是全部母体");
});

test("超额的账只念接口真交出来的数：缺几档、几行是北交所", () => {
  const t = ex5GapText([B_Q1, B_Q2, B_Q3, BASE], [DETAIL_OPER, DETAIL_VOL, DETAIL_SHORT, DETAIL_BJ]);
  assert.match(t, /有 1 档读不出中位/);
  assert.match(t, /4 行明细里有 1 行是北交所/);
  assert.match(t, /格子上是 —，不是 0%/);
  assert.match(ex5GapText([B_Q1, B_Q2], [DETAIL_OPER]), /各档都有数/, "全都有的时候也如实说没有空格子");
  assert.equal(ex5GapText([], []), "", "一档都没交就不念这句（不硬凑）");
});

// ============================================ 八、明细展开：每票每分量的起止名次与象限

// ============================================ 八之二、量纲：接口交小数比例的那些列必须乘回一百

test("ratioText：小数比例 → 百分数（一位小数、负数带符号、缺是 —），rateText 语义不许动", () => {
  // 后端 definitions 原句：sv-2 的 trigger_gain（小数比例，0.4641＝+46.41%）
  assert.equal(ratioText(0.4626), "46.3%");
  assert.equal(ratioText(0.21), "21.0%");
  assert.equal(ratioText(0), "0.0%", "真 0 是平盘，照原样印");
  assert.equal(ratioText(-0.035), "-3.5%", "跌了要带符号");
  assert.equal(ratioText(-0.4626), "-46.3%");
  assert.equal(ratioText(null), DASH);
  assert.equal(ratioText(undefined), DASH);
  assert.equal(ratioText(Number.NaN), DASH);
  assert.equal(ratioText("0.4626"), DASH, "字符串不是数：不猜量纲，交 —");
  // rateText 收的仍是**百分数**（别处传进来的已经是百分数，乘一百会把那几列放大一百倍）
  assert.equal(rateText(26.3), "26.3%");
  assert.equal(rateText(8.4), "8.4%");
  assert.equal(rateText(100), "100.0%");
  assert.equal(rateText(null), DASH);
  assert.equal(ratioText(26.3), "2630.0%", "两份函数各司其职：把百分数喂给 ratioText 会放大一百倍（所以列不许混用）");
});

test("明细那一列「前 4 日涨幅」渲染走 ratioText，屏上的 46.3% 才是库里那个 0.4626", () => {
  const g = groupRows([DETAIL_SHORT], 30)[0];
  assert.equal(groupCellText(col(groupColumns(), "outcome_gain4"), g), "46.3%",
    "0.4626 必须印成 46.3%（修之前屏上是 0.5%）");
  assert.equal(groupCellText(col(groupColumns(), "outcome_gain4"),
    { ...g, outcome_gain4: null }), DASH, "没有这一回事就是 —，不是 0.0%");
  // 列键与渲染函数一一对应：这一列的 sortKey 就是它读的字段名
  assert.equal(col(groupColumns(), "outcome_gain4").key, "outcome_gain4");
  const line = lib.split("\n").find(l => l.includes('case "outcome_gain4"') && l.includes("return"));
  assert.ok(line, "groupCellText 里找不到 outcome_gain4 那一支");
  assert.match(line as string, /return ratioText\(g\.outcome_gain4\)/);
  assert.doesNotMatch(line as string, /rateText/, "这一列不许走 rateText：接口交的是小数比例，不乘一百就是 0.5%");
  // 反向钉住：那几列读的确实是百分数，仍走 rateText（不许顺手把 rateText 改成乘一百）
  const rateLine = lib.split("\n").find(l => l.includes('case "win5"') && l.includes("rateText"));
  assert.ok(rateLine, "win5 那一支应该仍走 rateText");
  assert.doesNotMatch(rateLine as string, /ratioText/);
  assert.match(col(groupColumns(), "outcome_gain4").title, /小数比例/);
});

test("明细展开读的就是那一格的字段名（拼错只会永远显示 —）", () => {
  const g = groupRows([DETAIL_OPER, DETAIL_VOL], 30)[0];
  assert.equal(groupCellText(col(groupColumns(), "code"), g), "300750.SZ");
  assert.equal(groupCellText(col(groupColumns(), "key_count"), g), "2");
  assert.equal(groupCellText(col(groupColumns(), "rank_delta_best"), g), "+1900");
  assert.equal(groupCellText(col(groupColumns(), "outcome_len"), g), "5");
  assert.equal(groupCellText(col(groupColumns(), "outcome_gain4"), g), "61.2%");
  assert.equal(detailCellText(col(detailColumns(), "rank_delta"), DETAIL_OPER), "+1900");
  assert.equal(detailCellText(col(detailColumns(), "rank_first"), DETAIL_OPER), "2400");
  assert.equal(detailCellText(col(detailColumns(), "quad_first"), DETAIL_OPER), "Q4");
  assert.equal(detailCellText(col(detailColumns(), "value_delta"), DETAIL_VOL), "-0.08");
  assert.equal(detailCellText(col(detailColumns(), "quad_switches"), DETAIL_VOL), DASH,
    "没有象限的那一族：展开行里也是 —，不是 0");
  assert.equal(detailCellText(col(detailColumns(), "capability_label"), { ...DETAIL_VOL, capability_label: null }),
    "vol_trend", "标签没交就退回 capability_key，格子不许空着");
});

test("分量原值不追加百分号：vol_stage 那一族是档位 1~5", () => {
  assert.equal(valueText(3.4), "3.40");
  assert.equal(valueText(3), "3");
  assert.equal(COPY.volStage.includes("档位 1~5"), true);
  assert.equal(COPY.moodFamily.includes("market_mood"), true);
  assert.equal(COPY.window.includes("不含启动日当天"), true);
  assert.equal(COPY.coverage.includes("2026-01-27"), true);
});

// ============================================ 九、版本与接口不接上的时候说什么

test("spec_version 对不上就当场念出来（字段改名不会报错，只会永远显示 —）", () => {
  assert.equal(versionWarning(PAYLOAD), "");
  assert.match(versionWarning({ ...PAYLOAD, spec_version: "sqt-2" }), /sqt-2 与本页合同 sqt-1 不一致/);
  assert.match(versionWarning({ ...PAYLOAD, spec_version: undefined }), /没交 spec_version/);
});

// ============================================ 十、接线与措辞红线（漏一处就是哑卡片）

test("这一块真挂在倍量形态页上，并登记进系统自检清单", () => {
  assert.ok(page.includes("<StartupQuadrantTrackBoard />"), "调用点没接上");
  assert.ok(comp.includes('useProbe<SqPayload>("/quant/startup-quadrant-track"'), "组件没发那条请求");
  assert.ok(!comp.includes('useProbe<SqPayload>("/api'), "页面里绝不写 /api（useProbe 自己补前缀）");
  assert.ok(system.includes('"/quant/startup-quadrant-track"'), "System.tsx 的 CAPABILITIES 里没有这条");
  assert.match(system, /path: "\/quant\/startup-quadrant-track".*slow: true \}/, "慢接口要标 slow");
});

test("控件参数全部进 query，界面不自己再筛一遍", () => {
  assert.match(comp, /useProbe<SqPayload>.*queryParams\(p\)/s, "控件 state 没进请求参数");
  for (const k of ["cohort:", "months:", "window:", "key:", "limit:"]) {
    assert.ok(lib.includes(k), `queryParams 少交 ${k}`);
  }
  assert.match(comp, /useNonce\(\)/);
  assert.match(comp, /重算一次/);
});

test("明细用全站那一份行交互，不自己拼复盘地址", () => {
  assert.match(comp, /useStockRowInteraction\("启动前象限轨迹"\)/);
  assert.match(comp, /\{STOCK_ROW_HINT\}/);
  assert.match(comp, /x\.rowProps\(gi, g\.code/);
  assert.match(comp, /x\.popHint &&/);
  assert.doesNotMatch(comp, /window\.open|\/stock-review/, "地址只在 useOpenStockReview 那一处拼");
});

test("说明按项目纪律收：caveat、覆盖与这一族没有象限那几句都在框外", () => {
  assert.match(comp, /<Caveats items=\{caveatItems\(d\.caveats\)\} \/>/);
  assert.match(comp, /<Definitions map=\{d\.definitions\} \/>/);
  assert.match(comp, /<Caliber text=/);
  const box = comp.slice(comp.indexOf("<ExplainBox"), comp.indexOf("</ExplainBox>"));
  assert.ok(box.length > 100, "说明框要有内容");
  assert.ok(comp.indexOf("<Caveats") < comp.indexOf("<ExplainBox"), "caveats 不许折进默认收起的说明框");
  assert.ok(comp.indexOf("{coverageSentence(d)}") < comp.indexOf("<ExplainBox"), "覆盖声明要在框外");
  assert.ok(comp.indexOf("COPY.window") < comp.indexOf("<ExplainBox"), "三条口径要在框外");
  assert.doesNotMatch(box, /text-warning|text-destructive|<Caveats/, "告警一类不许折进折叠框");
});

test("渲染文本里没有裸 **（中文串也不嵌 ASCII 双引号）", () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const code = strip(comp);
  assert.doesNotMatch(strip(lib), /\*\*/, "判读层交出的文本里不该有 **");
  for (const b of code.match(/<p[^>]*>([\s\S]*?)<\/p>/g) ?? []) {
    assert.doesNotMatch(b, /\*\*/, "组件自己写的句子不该含 **");
  }
  // 中文串里嵌 ASCII 双引号会把字面量截断（本项目炸过三次）：汉字两侧都是引号就是那种写法
  assert.doesNotMatch(code, /[\u4e00-\u9fff]"[\u4e00-\u9fff]/,
    "中文句子中间出现了 ASCII 双引号（要用「」）");
  for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? []) {
    assert.doesNotMatch(b, /"/, `JSX 文本节点里出现了 ASCII 双引号：${b}`);
  }
});

test("available:false 交出后端那句 reason，404 交给 Panel（不放假数据）", () => {
  assert.match(comp, /if \(d\.available === false\)/);
  assert.match(comp, /\{d\.reason \|\| /, "reason 没给就明说不猜");
  assert.match(comp, /state=\{state\}/, "四态（含 not_wired 那句接口尚未接上）由 Panel 那一处判");
  assert.match(comp, /contract="\/api\/quant\/startup-quadrant-track"/, "404 时屏幕上要留着这份验收单");
  assert.doesNotMatch(comp, /rows=\[[^\]]+\.\.\.|const FAKE|示例数据/, "不许拿示例数字顶上去");
});

test("表头的排序键都在这份契约的行上取得到值", () => {
  const keyKeys = keyColumns().map(c => c.key);
  for (const k of keyKeys) assert.ok(k in KEY_OPER, `表一的键 ${k} 在接口行里没有这个字段（点了不排序）`);
  for (const c of answerColumns()) assert.ok(c.key in BASE && (c.key in B_Q1), `表二的键 ${c.key} 不在档行上`);
  const g = groupRows([DETAIL_OPER], 30)[0];
  for (const c of groupColumns()) assert.ok(c.key in g, `表三的键 ${c.key} 不在事件行上`);
  for (const c of detailColumns()) assert.ok(c.key in DETAIL_OPER, `展开行的键 ${c.key} 不在明细行上`);
  // 象限分布列印的是分布不是标量：那两列必须是不可排序的普通表头
  assert.equal(col(keyColumns(), "quad_first").sortable, false);
  assert.equal(col(keyColumns(), "quad_last").sortable, false);
});
