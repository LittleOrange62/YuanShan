import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  COHORTS_FALLBACK, COPY, DASH, JUMP_DAYS_NULL_COPY, JUMP_DAYS_ZERO_COPY, JUMP_PCTS, LIMITS,
  MONTHS_FALLBACK, PRESCREENED_COHORTS, SEG4_PRESET_COPY, WINDOWS_FALLBACK,
  answerCellText, answerColumns, answerRows, caveatItems, clampJumpPct, clampLimit, clampMonths,
  clampWindow, cohortOptionsFor, coverageSentence, detailCellText, detailColumns,
  ex5AccountText, gapText, groupCellText, groupCellTitle, groupColumns, intText,
  isPrescreenedCohort, jointCellText, jointColumns, jointHeadline, jointRows,
  jumpCellText, jumpColumns, jumpDaysMeaning, jumpDaysText, jumpHeadline, jumpPctParam, jumpRows, optionsFor,
  queryParams, ratioToPctText, rangeText, rateText, seg4CellText, shareText, shortTitle, signedText,
  spikeHeadline, versionWarning, windowShort,
  type SsdAggRow, type SsdJoint, type SsdJumps, type SsdPayload, type SsdRow,
} from "../src/lib/spikeDays.ts";
// 量纲那两件事是跨屏的：象限轨迹那一屏刚修过「小数比例被当百分数」的错（ratioText 就是为它加的），
// 这一块不许长出第三份读法 —— 语义分离在这里一起钉住。
import { ratioText as sqRatioText, rateText as sqRateText } from "../src/lib/quadrantTrack.ts";

/**
 * 「启动前 30 场爆量与突然拉高：落在量增的天还是量减的天」判读层的测试。
 *
 * 钉的是会把读数说反的那几处，不是排版：
 *  ① `ratioText`／`rateText` 的语义分离（接口交百分数的列乘一百就错一百倍，交小数比例的列不乘就错一百倍）；
 *  ② null 一律 `—`，绝不印成 0（0 是一条方向相反的结论）；
 *  ③ `jump_days` 的 0 与 null **文案必须不同**：0 是窗口凑满而真没有突拉，null 是没凑满／没行情；
 *  ④ `queryParams` 真的把 cohort/months/window/jump_pct/limit 五个参数都拼进 URL；
 *  ⑤ 母体兜底三档里 `anyfirst` 排第一，接口换了名单也要把它提到第一（默认选中它）；
 *  ⑥ 联合对照与突拉那两张表用的字段名**与合同逐字一致**：fixture 就是合同那份字段名，
 *     lib 里拼错一个名字，这一格就取不到值 → 断言当场红。
 *
 * 排序键与接线只做到"键名在这份契约的行上真存在、组件确实发那条请求"为止 ——
 * 浏览器里逐列点击实测由人做，这里不声称实测过排序，也不声称接口出过数。
 */

const lib = readFileSync(new URL("../src/lib/spikeDays.ts", import.meta.url), "utf8");
const comp = readFileSync(new URL("../src/components/quant/StartupSpikeDaysBoard.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/pages/VolumeDouble.tsx", import.meta.url), "utf8");
const system = readFileSync(new URL("../src/pages/System.tsx", import.meta.url), "utf-8");
const features = readFileSync(new URL("../src/lib/workspace/features.ts", import.meta.url), "utf-8");
const ledger = readFileSync(new URL("../docs/公用组件：一份实现多处调用的读数块清单.md", import.meta.url), "utf-8");

// ============================================ 契约样本：字段名逐个照 ssd-1 合同抄（拼错就红）

const JOINT: SsdJoint = {
  dbl_total_days: 51234,
  dbl_up_share: 0.62, dbl_down_share: 0.31,
  near_up_share: 0.55, near_down_share: 0.38,
  calm_up_share: 0.41, calm_down_share: 0.52,
};
const JUMPS: SsdJumps = {
  jump_days_med: 2.5,
  jump_on_dbl_share: 0.68, jump_on_near_share: 0.17, jump_on_calm_share: 0.15,
  jump_limit_share: 0.44,
  first_jump_gap_med: 6,
  jump_days_zero_events: 118, jump_days_null_events: 37,
};
const B_LOW: SsdAggRow = {
  label: "突拉少", range: [null, 1], n: 150,
  dbl_up_days_med: 1, dbl_down_days_med: 2, jump_days_med: 0, jump_on_dbl_share: 0.21,
  seg4_rate: 8.4, r5_med: -3.1, ex5_med: -5.2, win5: 31.5,
};
const B_MID: SsdAggRow = {
  label: "突拉中", range: [1, 3], n: 160,
  dbl_up_days_med: 3, dbl_down_days_med: 2, jump_days_med: 2, jump_on_dbl_share: 0.52,
  seg4_rate: 14.2, r5_med: 1.4, ex5_med: -0.8, win5: 44.0,
};
const B_HIGH: SsdAggRow = {
  label: "突拉多", range: [3, null], n: 102,
  dbl_up_days_med: 6, dbl_down_days_med: 1, jump_days_med: 5, jump_on_dbl_share: 0.74,
  seg4_rate: 26.3, r5_med: 6.9, ex5_med: null, win5: 52.8,
};
const BASE: SsdAggRow = {
  label: "全部母体事件", range: null, n: 412,
  dbl_up_days_med: 3, dbl_down_days_med: 2, jump_days_med: 2, jump_on_dbl_share: 0.55,
  seg4_rate: 15.0, r5_med: 1.9, ex5_med: -1.4, win5: 42.2,
};
/** 一条满窗、真有突拉的事件 */
const ROW_A: SsdRow = {
  code: "300750.SZ", name: "宁德时代", board: "创业板", launch_date: "2026-06-15",
  cohort: "anyfirst", bars: 30,
  up_days: 16, down_days: 12, flat_days: 1, still_days: 1,
  dbl_days: 8, near_dbl_days: 6, calm_days: 16,
  dbl_up_days: 6, dbl_down_days: 2, near_up_days: 3, near_down_days: 3,
  calm_up_days: 7, calm_down_days: 9,
  jump_days: 4, jump_dbl_days: 3, jump_near_days: 1, jump_calm_days: 0,
  jump_limit_days: 2, limit_days: 3,
  first_dbl_gap: 5, first_jump_gap: 6,
  outcome_len: 5, outcome_r5: 8.2, outcome_ex5: 5.1,
};
/** 一条突拉真 0 的（窗口凑满而确实没有） */
const ROW_ZERO: SsdRow = {
  ...ROW_A, code: "688223.SH", name: "晶科能源", board: "科创板", launch_date: "2026-02-03",
  cohort: "board4", bars: 30, jump_days: 0, jump_dbl_days: 0, jump_near_days: 0,
  jump_calm_days: null, jump_limit_days: 0, first_jump_gap: null,
  outcome_len: 4, outcome_r5: -1.2, outcome_ex5: -3.4,
};
/** 一条窗口没凑满、超额读不出的（北交所：没有对应分类指数） */
const ROW_BJ: SsdRow = {
  ...ROW_A, code: "839790.BJ", name: "北交所样本", board: "北交所", launch_date: "2026-01-30",
  cohort: "anyfirst", bars: 12, jump_days: null, jump_dbl_days: null, jump_near_days: null,
  jump_calm_days: null, jump_limit_days: null, first_dbl_gap: null, first_jump_gap: null,
  outcome_len: 3, outcome_r5: 0.6, outcome_ex5: null,
};

const PAYLOAD: SsdPayload = {
  available: true, spec_version: "ssd-1",
  params: { cohort: "anyfirst", cohort_text: "全部首板（不筛结果）",
            months: 6, window: 30, jump_pct: 0.07, limit: 400 },  // 接口那把尺是比例（0.03~0.15），控件上那个 7 是百分数
  coverage: { from: "2026-01-27", to: "2026-10-16", events: 412, codes: 268,
              vr_earliest: "2025-09-01", window_full_events: 302, window_short_events: 110 },
  joint: JOINT, jumps: JUMPS,
  buckets: [B_LOW, B_MID, B_HIGH], base: BASE,
  rows: [ROW_A, ROW_ZERO, ROW_BJ],
  caveats: ["倍量的分母是这只票自己的上一根日线量（vr-1），不是前 5 日均量。", "", "分钟线这一轮没做。"],
  caliber: "母体＝analytics.first_board_shape（fb-2）的首板事件；倍数取自 analytics.volume_ratio_daily（vr-1），一天一个数。",
  definitions: { jump_on_dbl_share: "突拉当天同时是倍量日的比例（分母＝突拉手数）" },
  options: {
    cohorts: COHORTS_FALLBACK, months: MONTHS_FALLBACK, windows: WINDOWS_FALLBACK,
  },
};

const col = <T extends { key: string; label: string; sortable: boolean; title: string }>(
  list: T[], k: string): T => {
  const c = list.find(x => x.key === k);
  assert.ok(c, `列 ${k} 不存在`);
  return c;
};

// ============================================ 一、量纲：ratioText 与 rateText 不许并成一份

test("小数比例与百分数是两份读法：ratioText 乘一百，rateText 绝不乘", () => {
  // 参照那一屏（象限轨迹）的 outcome_gain4 交的是小数比例（0.4626＝+46.26%）
  assert.equal(sqRatioText(0.4626), "46.3%");
  assert.equal(sqRatioText(0.21), "21.0%");
  assert.equal(sqRatioText(-0.035), "-3.5%", "负数也要有符号");
  assert.equal(sqRatioText(null), DASH);
  assert.equal(sqRateText(26.3), "26.3%", "rateText 的入参已经是百分数：再乘一百就错一百倍");
  // 本屏的 rateText 与参照那份同一种语义（seg4_rate／win5 接口按百分数交）
  assert.equal(rateText(26.3), "26.3%");
  assert.equal(rateText(null), DASH);
  assert.equal(lib.includes("x * 100"), true, "本屏只该在 shareText 里出现一次乘一百（0~1 的占比）");
  assert.equal(lib.split("\n").filter(l => l.includes("* 100")).length, 1,
    "乘一百只许有一处（多一处就多一份量纲），其余列一律按接口交的量纲原样印");
  // 占比那两族：0~1 与 0~100 都印成百分数（阈值 1.5）
  assert.equal(shareText(0.62), "62.0%");
  assert.equal(shareText(62), "62.0%", "接口若直接交百分数，不许印成 6200.0%");
  assert.equal(shareText(0), "0.0%", "0 是有效读数，不许吞成 —");
  assert.equal(shareText(null), DASH);
});

// ============================================ 二、null 不渲染成 0（本项目最贵的一类错）

test("缺数一律 —：天数、占比、收益、隔几场都不拿 0 顶上", () => {
  assert.equal(intText(null), DASH);
  assert.equal(intText(0), "0", "0 只天是读数，必须与 — 长得不一样");
  assert.equal(shareText(null), DASH);
  assert.equal(signedText(null), DASH);
  assert.equal(signedText(0), "+0.00%", "真 0 收益照原样印");
  assert.equal(gapText(null), DASH);
  assert.equal(gapText(5), "5 场");
  assert.equal(answerCellText(col(answerColumns(), "ex5_med"), B_HIGH), DASH,
    "北交所那批没有分类指数：超额缺的是 —，不是 0.00%");
  assert.equal(answerCellText(col(answerColumns(), "r5_med"), B_HIGH), "+6.90%");
  assert.equal(answerCellText(col(answerColumns(), "win5"), B_HIGH), "52.8%");
  assert.equal(answerCellText(col(answerColumns(), "jump_on_dbl_share"), B_HIGH), "74.0%");
  assert.equal(answerCellText(col(answerColumns(), "jump_days_med"), B_LOW), "0",
    "这一档的突拉中位真是 0：印 0，不印 —");
  assert.equal(jointCellText(col(jointColumns(), "total_days"), jointRows(JOINT)[1]), DASH,
    "准倍量与缩量的合计天数合同没交：格子是 —，不是 0 天");
});

// ============================================ 三、jump_days 的 0 与 null：两件事两套文案

test("突拉天数：0 是窗口凑满而真没有，— 是没凑满／没行情，两句不许同文", () => {
  assert.equal(jumpDaysText(0), "0");
  assert.equal(jumpDaysText(null), DASH);
  assert.equal(jumpDaysText(4), "4");
  const zeroCopy = jumpDaysMeaning(0);
  const nullCopy = jumpDaysMeaning(null);
  assert.notEqual(zeroCopy, nullCopy, "0 与 — 的文案必须不同（用户点名这一条）");
  assert.equal(zeroCopy, JUMP_DAYS_ZERO_COPY);
  assert.equal(nullCopy, JUMP_DAYS_NULL_COPY);
  assert.match(zeroCopy, /确实一天突然拉高都没有|确实没有/);
  assert.match(zeroCopy, /凑满/);
  assert.match(nullCopy, /没凑满|没有行情/);
  assert.match(nullCopy, /不是 0 次|不是 0/, "— 那句必须明说它不是 0，否则读者又当没有");
  assert.match(jumpDaysMeaning(3), /真有 3 天/);
  // 明细那一格：0 与 — 一起体现在悬停上（组件把 lib 那句交给 title）
  assert.equal(groupCellText(col(groupColumns(), "jump_days"), ROW_ZERO), "0");
  assert.equal(groupCellText(col(groupColumns(), "jump_days"), ROW_BJ), DASH);
  assert.equal(groupCellTitle(col(groupColumns(), "jump_days"), ROW_ZERO), JUMP_DAYS_ZERO_COPY);
  assert.equal(groupCellTitle(col(groupColumns(), "jump_days"), ROW_BJ), JUMP_DAYS_NULL_COPY);
  assert.match(COPY.zeroOrNull, /0 与 — 是两件事/);
  // 聚合那一句要点名"真 0 的几条、算不出的几条"（两个数都来自接口 jumps）
  const head = jumpHeadline(JUMPS);
  assert.match(head, /118 条/);
  assert.match(head, /37 条/);
  assert.match(head, /2\.5 天/);
  assert.match(jumpHeadline({}), /没交 jumps/, "jumps 空壳就明说说不出，不凑一句");
  assert.match(jumpHeadline({ jump_days_med: 2 }), /没交「真 0 天」与「算不出」/,
    "后端没交那两个计数就不编条数");
});

// ============================================ 四、参数真的发出去（换控件＝换一次取数）

test("queryParams 把五个参数都拼进 URL（jump_pct 拼的是接口那份名）", () => {
  const q = queryParams({ cohort: "anyfirst", months: 6, window: 20, jumpPct: 9, limit: 400 });
  assert.deepEqual(Object.keys(q).sort(), ["cohort", "jump_pct", "limit", "months", "window"]);
  const url = `/api/quant/startup-spike-days?${new URLSearchParams(
    Object.entries(q).map(([k, v]) => [k, String(v)])).toString()}`;
  for (const k of ["cohort", "months", "window", "jump_pct", "limit"]) {
    assert.ok(url.includes(`${k}=`), `${k} 没进 URL`);
  }
  assert.match(url, /cohort=anyfirst/);
  assert.match(url, /months=6/);
  assert.match(url, /window=20/);
  assert.match(url, /jump_pct=0.09/, "控件叫 jumpPct，发出去的键必须是合同里的 jump_pct");
  assert.match(url, /limit=400/);
});

test("参数越界在发出之前就被夹住（后端会拒的值不该发过去）", () => {
  assert.equal(clampMonths(0), 1);
  assert.equal(clampMonths(99), 12);
  assert.equal(clampMonths("x"), 6);
  assert.equal(clampWindow(15), 30, "只认 10/20/30，别的回默认 30");
  assert.equal(clampWindow(10), 10);
  assert.equal(clampJumpPct(0), 3);
  assert.equal(clampJumpPct(99), 15);
  assert.equal(clampJumpPct("x"), 7, "填不出数就回默认那一档，不发一个空值");
  assert.equal(clampLimit(9999), 600, "明细上限后端硬顶 600");
  assert.equal(clampLimit(-1), 1);
  const q = queryParams({ cohort: "board4", months: 40, window: 7, jumpPct: 500, limit: 5000 });
  assert.deepEqual(q, { cohort: "board4", months: 12, window: 30, jump_pct: 0.15, limit: 600 });
  assert.deepEqual(JUMP_PCTS, [5, 7, 9]);
  assert.equal(LIMITS[2], 600);
});

test("jump_pct 的单位换算与后端边界是同一份：前端那份 7 发出去必须是 0.07", () => {
  // 这一条是 10-18 那次「控件上写 7、后端要 0.07」的直接产物：单位错了不崩，只回一句 422，
  // 而面板当时把 422 说成"接口没接上"，于是排查方向整个反了。
  assert.equal(jumpPctParam(7), 0.07);
  assert.equal(jumpPctParam(9), 0.09);
  assert.equal(jumpPctParam(500), 0.15, "越界先夹到后端上界，再换算：0.15 是发得出去的数");
  assert.equal(ratioToPctText(0.07), "7.0%", "回显要念回百分数，不许念成 0.1%");
  assert.equal(ratioToPctText(null), DASH);
  // 夹范的上下界直接照抄接口签名里的那两个数（改了后端边界而界面没跟上 ⇒ 这一条红）
  const api = readFileSync(new URL("../../vr/app.py", import.meta.url), "utf-8");
  const m = /jump_pct: float = Query\(\s*([\d.]+)\s*,\s*ge=([\d.]+)\s*,\s*le=([\d.]+)\s*\)/.exec(api);
  assert.ok(m, "接口签名里那行 jump_pct 的 Query(默认, ge=, le=) 不在了 —— 后端改了参数形状，界面这边要跟着重看");
  const [, qdef, ge, le] = m as RegExpExecArray;
  assert.equal(clampJumpPct(0), Math.round(Number(ge) * 100), "界面下夹必须等于接口 ge 的百分数读法");
  assert.equal(clampJumpPct(9999), Math.round(Number(le) * 100), "界面上夹必须等于接口 le 的百分数读法");
  assert.equal(Number(qdef), 0.07, "默认档改了要一起改：界面 DEFAULTS 里那个 7 就是它");
  for (const n of JUMP_PCTS) {
    assert.equal(n >= Number(ge) * 100 && n <= Number(le) * 100, true, `兜底档位 ${n}% 发出去会被后端拒`);
  }
});

// ============================================ 五、母体名单：anyfirst 排第一并且不许被挤走

test("母体兜底三档含 anyfirst 且排第一；接口换了名单也把它提到第一", () => {
  assert.deepEqual(COHORTS_FALLBACK.map(c => c.value), ["anyfirst", "board4", "jump40"]);
  assert.equal(COHORTS_FALLBACK[0].label, "全部首板（不筛结果）");
  assert.equal(cohortOptionsFor(null)[0].value, "anyfirst", "接口没交清单也得有这一档，还在第一");
  // 接口把 anyfirst 交在中间：界面把它提到第一，其余顺序照接口原样（不自己重排）
  const shuffled = [
    { value: "board4", label: "4 板及以上" },
    { value: "anyfirst", label: "接口给的标签" },
    { value: "jump40", label: "4 日超四成" },
  ];
  const got = cohortOptionsFor(shuffled);
  assert.deepEqual(got.map(o => o.value), ["anyfirst", "board4", "jump40"]);
  assert.equal(got[0].label, "接口给的标签", "标签以接口为准，界面不覆盖");
  // 接口交了一档全新的：补上 anyfirst，不丢接口那一档
  assert.deepEqual(cohortOptionsFor([{ value: "newcohort", label: "新母体" }]).map(o => o.value),
    ["anyfirst", "newcohort"]);
  assert.deepEqual(PRESCREENED_COHORTS, ["board4", "board4_45", "board4_or_gain45", "jump40"]);
  assert.equal(PRESCREENED_COHORTS.indexOf("anyfirst"), -1, "anyfirst 不许被算进按结果筛过的那批");
  assert.equal(optionsFor(null, WINDOWS_FALLBACK).length, 3);
  assert.equal(MONTHS_FALLBACK.length, 12);
});

test("按结果筛过的那几档：≥4 板率这一格不许印成一个像结论的百分比", () => {
  assert.equal(isPrescreenedCohort("board4"), true);
  assert.equal(isPrescreenedCohort("anyfirst"), false);
  assert.equal(isPrescreenedCohort(null), false, "母体没交就照普通比率印，不下那句结论");
  assert.equal(SEG4_PRESET_COPY, "这一档按结果筛过，这个数恒等于母体定义");
  assert.equal(seg4CellText(26.3, "jump40"), SEG4_PRESET_COPY);
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_HIGH, "board4"), SEG4_PRESET_COPY);
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_HIGH, "anyfirst"), "26.3%");
  assert.equal(answerCellText(col(answerColumns(), "seg4_rate"), B_HIGH), "26.3%");
  // 其余列不许被这一档带跑
  assert.equal(answerCellText(col(answerColumns(), "win5"), B_HIGH, "board4"), "52.8%");
  assert.equal(answerCellText(col(answerColumns(), "ex5_med"), B_HIGH, "board4"), DASH);
});

// ============================================ 六、联合对照与突拉那两张表：字段名逐字对回合同

test("第一张表把天数与收涨收跌摆在同一行（不是两个独立天数）", () => {
  const rows = jointRows(JOINT);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(r => r.kind), ["dbl", "near", "calm"]);
  // 合同字段名逐字抄在 fixture 上：lib 里读错名字就取不到值（这里断言取到的正是合同那份数）
  assert.equal(rows[0].total_days, 51234, "dbl_total_days");
  assert.equal(rows[0].up_share, 0.62, "dbl_up_share");
  assert.equal(rows[0].down_share, 0.31, "dbl_down_share");
  assert.equal(rows[1].up_share, 0.55, "near_up_share");
  assert.equal(rows[1].down_share, 0.38, "near_down_share");
  assert.equal(rows[2].up_share, 0.41, "calm_up_share");
  assert.equal(rows[2].down_share, 0.52, "calm_down_share");
  // 每一行自己把出处字段名念出来（屏上有一列就是干这个的）
  assert.equal(rows[0].fields, "dbl_total_days · dbl_up_share · dbl_down_share");
  assert.match(rows[1].fields, /near_up_share · near_down_share/);
  assert.match(rows[2].fields, /calm_up_share · calm_down_share/);
  const cols = jointColumns();
  for (const c of cols) assert.ok(c.key in rows[0], `联合表的列键 ${c.key} 不在行上（点了不排序）`);
  assert.equal(jointCellText(col(cols, "up_share"), rows[0]), "62.0%");
  assert.equal(jointCellText(col(cols, "total_days"), rows[0]), "51,234");
  assert.match(col(cols, "fields").title, /字段/);
  // 空壳：后端没交 joint 时三行还在，格子上全是 —（不塌成一行也不编 0）
  const empty = jointRows(null);
  assert.equal(empty.length, 3);
  assert.equal(jointCellText(col(cols, "up_share"), empty[0]), DASH);
  assert.match(jointHeadline({}), /说不出/);
  assert.match(jointHeadline(JOINT), /收涨 62\.0%、收跌 31\.0%/);
  assert.match(jointHeadline(JOINT), /缩量日收涨 41\.0%/);
});

test("第二张表：突拉落在倍量／准倍量／缩量 + 封住涨停，字段名照合同", () => {
  const rows = jumpRows(JUMPS);
  assert.deepEqual(rows.map(r => r.field),
    ["jump_on_dbl_share", "jump_on_near_share", "jump_on_calm_share", "jump_limit_share"]);
  assert.deepEqual(rows.map(r => r.share), [0.68, 0.17, 0.15, 0.44]);
  const cols = jumpColumns();
  for (const c of cols) assert.ok(c.key in rows[0], `突拉表的列键 ${c.key} 不在行上`);
  assert.equal(jumpCellText(col(cols, "share"), rows[0]), "68.0%");
  assert.equal(jumpCellText(col(cols, "field"), rows[3]), "jump_limit_share");
  // 那两个非占比的读数也要上屏（第一_jump_gap 与突拉天数中位）
  const head = spikeHeadline(JUMPS);
  assert.match(head, /落在倍量日 68\.0%/);
  assert.match(head, /落在缩量日 15\.0%/);
  assert.match(head, /封住涨停的那批占 44\.0%/);
  assert.match(head, /日线代理/, "这句话不许被说成分时判据");
  assert.match(head, /不是分时图/);
  assert.match(spikeHeadline({}), /答不了/);
  assert.match(spikeHeadline({ jump_on_dbl_share: 0.68 }), /另一半没数/, "只有一个占比就不下对比结论");
});

// ============================================ 七、分档 × 结果：base 钉第一，区间念出来

test("第三张表把 base 那一行钉在最前，三档照接口原样", () => {
  const rows = answerRows([B_LOW, B_MID, B_HIGH], BASE);
  assert.equal(rows.length, 4);
  assert.equal(rows[0], BASE, "base 必须在第一行");
  assert.equal(answerRows([B_LOW], null)[0], B_LOW, "后端没交 base 就只画档，不编一行基线");
  assert.equal(answerCellText(col(answerColumns(), "label"), rows[0]), "全部母体事件");
  assert.equal(answerCellText(col(answerColumns(), "n"), rows[0]), "412");
  assert.equal(answerCellText(col(answerColumns(), "dbl_up_days_med"), B_HIGH), "6");
  assert.equal(answerCellText(col(answerColumns(), "dbl_down_days_med"), B_HIGH), "1");
  assert.equal(answerCellText(col(answerColumns(), "jump_days_med"), B_MID), "2");
  const cols = answerColumns();
  for (const c of cols) assert.ok(c.key in BASE, `分档表的列键 ${c.key} 不在 base 行上`);
  assert.equal(col(cols, "range").sortable, false, "区间列印的是切点不是标量，不给排序");
});

test("分档区间把两个切点念出来：左开右闭，没交就说全部", () => {
  assert.equal(rangeText(B_LOW), "≤1");
  assert.equal(rangeText(B_MID), "1～3（不含 1）");
  assert.equal(rangeText(B_HIGH), ">3");
  assert.equal(rangeText(BASE), "全部");
  assert.match(col(answerColumns(), "range").title, /左开右闭/);
});

test("超额的账点名剔了几条：缺的是从分母里剔掉，不是按 0 计", () => {
  const t = ex5AccountText([B_LOW, B_MID, B_HIGH, BASE], [ROW_A, ROW_ZERO, ROW_BJ]);
  assert.match(t, /3 行明细里有 1 行读不出 outcome_ex5/);
  assert.match(t, /另有 1 行是北交所/);
  assert.doesNotMatch(t, /其中 1 行是北交所/,
    "不许把「读不出超额的行」与「北交所的行」混成一回事：合同里没有那一个对应关系");
  assert.match(t, /聚合那一侧有 1 档读不出 ex5_med/);
  assert.match(t, /分母里剔掉/);
  assert.match(t, /不是按 0% 计/);
  assert.match(COPY.ex5, /北交所没有对应的分类指数/);
  assert.match(COPY.ex5, /从超额的中位数分母里剔掉/);
  assert.match(COPY.ex5, /不是 0%/);
  assert.equal(ex5AccountText([], []), "", "一档一行都没交就不念这句（不硬凑）");
  assert.match(ex5AccountText([B_LOW, B_MID], [ROW_A]), /各档、各行都有数/);
});

// ============================================ 八、明细：逐事件逐日那些天数，键名照合同

test("明细主表与展开行的列键都在接口那行上取得到值", () => {
  assert.equal(groupCellText(col(groupColumns(), "code"), ROW_A), "300750.SZ");
  assert.equal(groupCellText(col(groupColumns(), "bars"), ROW_A), "30");
  assert.equal(groupCellText(col(groupColumns(), "dbl_days"), ROW_A), "8");
  assert.equal(groupCellText(col(groupColumns(), "dbl_up_days"), ROW_A), "6");
  assert.equal(groupCellText(col(groupColumns(), "jump_dbl_days"), ROW_A), "3");
  assert.equal(groupCellText(col(groupColumns(), "outcome_r5"), ROW_A), "+8.20%");
  assert.equal(groupCellText(col(groupColumns(), "outcome_ex5"), ROW_BJ), DASH,
    "北交所那行超额是 —，不是 +0.00%");
  assert.match(groupCellTitle(col(groupColumns(), "outcome_ex5"), ROW_BJ) ?? "", /不是 0\.00%/);
  assert.equal(groupCellTitle(col(groupColumns(), "outcome_ex5"), ROW_A), undefined,
    "读得出超额的那行不该念这句");
  assert.equal(groupCellText(col(groupColumns(), "cohort"), ROW_ZERO), "board4");
  for (const c of groupColumns()) assert.ok(c.key in ROW_A, `明细列键 ${c.key} 不在行上（点了不排序）`);
  // 展开那一叠：合同 rows 里那几个天数一个都不许漏（漏了就等于界面少画一类天）
  const CONTRACT = ["up_days", "down_days", "flat_days", "still_days", "dbl_days", "near_dbl_days",
    "calm_days", "dbl_up_days", "dbl_down_days", "near_up_days", "near_down_days",
    "calm_up_days", "calm_down_days", "jump_days", "jump_dbl_days", "jump_near_days",
    "jump_calm_days", "jump_limit_days", "limit_days", "first_dbl_gap", "first_jump_gap"];
  const keys = detailColumns().map(c => c.key);
  for (const k of CONTRACT) assert.ok(keys.includes(k), `展开行少了合同字段 ${k}`);
  for (const k of keys) assert.ok(CONTRACT.includes(k), `展开行画了合同里没有的键 ${k}`);
  assert.equal(detailCellText(col(detailColumns(), "calm_up_days"), ROW_A), "7");
  assert.equal(detailCellText(col(detailColumns(), "first_jump_gap"), ROW_ZERO), DASH);
  assert.equal(detailCellText(col(detailColumns(), "jump_days"), ROW_ZERO), "0");
});

test("不满窗的行标灰但仍进聚合：悬停那句说清它不是同一跨度", () => {
  assert.equal(windowShort(30, 30), false);
  assert.equal(windowShort(12, 30), true);
  assert.equal(windowShort(null, 30), null);
  assert.equal(windowShort(12, null), null);
  assert.match(shortTitle(12, 30), /不满窗：往前只有 12 个有效交易日/);
  assert.match(shortTitle(12, 30), /仍进后端的中位数聚合/);
  assert.equal(shortTitle(30, 30).startsWith("满窗"), true);
  assert.match(shortTitle(null, 30), /无从判断/);
});

test("覆盖那句把 vr_earliest 与满窗/不满窗的条数一起念出来", () => {
  const t = coverageSentence(PAYLOAD);
  assert.match(t, /窗口 2026-01-27 → 2026-10-16/);
  assert.match(t, /412 条启动事件 \/ 268 只票/);
  assert.match(t, /日线判据最早一场 2025-09-01/);
  assert.match(t, /满窗 302 条、不满窗 110 条/);
  assert.match(t, /不是库里缺数/);
  assert.match(t, /母体「全部首板（不筛结果）」/);
  assert.match(t, /突拉阈值 7\.0%/);
  assert.match(t, /启动日前 30 场/);
  assert.match(coverageSentence({ coverage: {}, params: {} }), /没交满窗\/不满窗的条数/,
    "后端没交就明说，界面不自己数");
});

test("后端字符串数组 → Caveats 的 {code,text}：编号连续、空串不占号", () => {
  assert.deepEqual(caveatItems(["甲", "乙"]), [{ code: "ssd1", text: "甲" }, { code: "ssd2", text: "乙" }]);
  assert.deepEqual(caveatItems(PAYLOAD.caveats), [
    { code: "ssd1", text: "倍量的分母是这只票自己的上一根日线量（vr-1），不是前 5 日均量。" },
    { code: "ssd2", text: "分钟线这一轮没做。" },
  ]);
  assert.deepEqual(caveatItems(null), []);
  assert.deepEqual(caveatItems([null, undefined, "   "]), []);
});

test("spec_version 对不上就当场念出来（字段改名不会报错，只会永远显示 —）", () => {
  assert.equal(versionWarning(PAYLOAD), "");
  assert.match(versionWarning({ ...PAYLOAD, spec_version: "ssd-2" }), /ssd-2 与本页合同 ssd-1 不一致/);
  assert.match(versionWarning({ ...PAYLOAD, spec_version: undefined }), /没交 spec_version/);
});

// ============================================ 九、三句口径的措辞红线（不许把日线代理说歪）

test("日线代理那句话把事实钉死：一天一个数、分钟线没做、不与异动筛查并表", () => {
  assert.match(COPY.proxy, /日线代理/);
  assert.match(COPY.proxy, /一天只出一个数/);
  assert.match(COPY.proxy, /上一根日线量/, "这一屏的倍量尺＝vr-1（当日量÷这只票自己的上一根日线量）");
  assert.doesNotMatch(COPY.proxy, /前 ?5 ?日均量/,
    "前 5 日均量是「倍量形态」那一屏的尺（volume_double），抄到这一屏就把两个数说成一个数");
  assert.match(COPY.proxy, /后复权/);
  assert.match(COPY.proxy, /分钟线这一轮没做/);
  assert.match(COPY.proxy, /几分钟/);
  assert.match(COPY.proxy, /异动筛查/, "要去看分钟线的那一屏得点名");
  assert.doesNotMatch(COPY.proxy, /这一屏读的就是分时/, "反了就是假话");
  assert.doesNotMatch(lib, /前 ?5 ?日均量/, "整份判读层里一处都不许出现前 5 日均量（那是另一屏的尺）");
  assert.match(jointColumns()[0].title, /2\.0/, "三类天的门槛要写在悬停里：≥2.0／1.8~2.0／≤0.5");
  assert.match(jointRows(JOINT)[0].label, /上一根日线量/);
  assert.match(jointRows(JOINT)[2].label, /上一根日线量的一半/);
  assert.match(lib, /jump_pct/);
});

// ============================================ 十、接线与呈现纪律（漏一处就是哑卡片）

test("这一块真挂在倍量形态页上、接在象限轨迹之后，并登记进系统自检清单", () => {
  assert.ok(page.includes("<StartupSpikeDaysBoard />"), "调用点没接上");
  assert.ok(page.indexOf("<StartupSpikeDaysBoard />") > page.indexOf("<StartupQuadrantTrackBoard />"),
    "必须接在 StartupQuadrantTrackBoard 之后");
  assert.ok(comp.includes('useProbe<SsdPayload>("/quant/startup-spike-days"'), "组件没发那条请求");
  assert.ok(!comp.includes('useProbe<SsdPayload>("/api'), "页面里绝不写 /api（useProbe 自己补前缀）");
  assert.ok(system.includes('"/quant/startup-spike-days"'), "System.tsx 的 CAPABILITIES 里没有这条");
  assert.match(system, /path: "\/quant\/startup-spike-days".*slow: true \}/, "慢接口要标 slow");
  assert.match(system, /启动前爆量与突然拉高.*where: "\/volume-double"/);
  assert.doesNotMatch(features, /三类母体/, "副标题不许还写着三类母体");
  assert.match(features, /爆量/, "新屏要进那一句副标题");
});

test("控件参数全部进 query，界面不自己再筛一遍", () => {
  assert.match(comp, /useProbe<SsdPayload>.*queryParams\(p\)/s, "控件 state 没进请求参数");
  for (const k of ["cohort:", "months:", "window:", "jump_pct:", "limit:"]) {
    assert.ok(lib.includes(k), `queryParams 少交 ${k}`);
  }
  assert.match(comp, /useNonce\(\)/);
  assert.match(comp, /重算一次/);
  assert.match(comp, /cohort: "anyfirst"/, "默认母体必须是全部首板（不筛结果）");
  assert.match(comp, /answerCellText\(c, r, echoedCohort\)/, "≥4 板率那一格按接口回显的母体来判");
  assert.ok(comp.includes("d.params?.cohort ?? p.cohort"), "母体以接口回显为准，state 只兜底");
});

test("明细用全站那一份行交互，只取 openReview 与 popHint，不接交叉高亮", () => {
  assert.match(comp, /useStockRowInteraction\("启动前爆量与突拉"\)/);
  assert.match(comp, /const \{ openReview, popHint(, clearAll)? \} = useStockRowInteraction/);
  assert.match(comp, /\{STOCK_ROW_HINT\}/);
  assert.match(comp, /onDoubleClick=\{\(\) => openReview\(r\.code\)\}/);
  assert.match(comp, /popHint &&/);
  assert.doesNotMatch(comp, /cellProps|rowProps|handleCellClick/,
    "这一块的主体是聚合表：交叉高亮不接（台账 §六 第 22 个调用点写的就是这个形状）");
  assert.doesNotMatch(comp, /window\.open|\/stock-review/, "地址只在 useOpenStockReview 那一处拼");
});

test("说明按项目纪律收：三句口径、覆盖、截断与告警都在折叠框外", () => {
  assert.match(comp, /<Caveats items=\{caveatItems\(d\.caveats\)\} \/>/);
  assert.match(comp, /<Definitions map=\{d\.definitions\} \/>/);
  assert.match(comp, /<Caliber text=/);
  const box = comp.slice(comp.indexOf("<ExplainBox"), comp.indexOf("</ExplainBox>"));
  assert.ok(box.length > 100, "说明框要有内容");
  assert.ok(comp.indexOf("<Caveats") < comp.indexOf("<ExplainBox"), "caveats 不许折进默认收起的说明框");
  assert.ok(comp.indexOf("{coverageSentence(d)}") < comp.indexOf("<ExplainBox"), "覆盖声明要在框外");
  for (const k of ["{COPY.proxy}", "{COPY.zeroOrNull}", "{COPY.ex5}"]) {
    assert.ok(comp.indexOf(k) < comp.indexOf("<ExplainBox"), `${k} 这三句要在框外`);
  }
  assert.ok(comp.indexOf("明细已触上限") < comp.indexOf("<ExplainBox"), "截断声明不许折进框");
  assert.ok(comp.indexOf("接口交出 0 行明细") < comp.indexOf("<ExplainBox"), "空表那句也要在框外");
  assert.doesNotMatch(box, /text-warning|text-destructive|<Caveats/, "告警一类不许折进折叠框");
});

test("三张表的顺序就是那一问的顺序：联合对照 → 突拉落在哪一天 → 分档 × 结果", () => {
  const a = comp.indexOf("一、联合对照");
  const b = comp.indexOf("二、突然拉高落在哪一天");
  const c = comp.indexOf("三、分档 × 结果");
  const d = comp.indexOf("逐票的窗口天数账");
  assert.ok(a > 0 && b > a && c > b && d > c, "界面顺序错了：读者要先看到联合对照，再看落在哪一天");
  assert.ok(comp.indexOf("<Caveats") > d, "caveats 排在最后，别把表切开");
});

test("available:false 交出后端那句 reason，404 说的是接口尚未接上（不放假数据）", () => {
  assert.match(comp, /if \(d\.available === false\)/);
  assert.match(comp, /\{d\.reason \|\| /, "reason 没给就明说不猜");
  assert.match(comp, /state=\{state\}/, "四态（含 not_wired）由 Panel 那一处判");
  assert.match(comp, /这一屏没拿到数据/, "404 时屏幕上要留着这句话");
  assert.match(comp, /quant\/startup-spike-days/, "404 时把真正请求的那个地址摆在屏上");
  assert.doesNotMatch(comp, /const FAKE|示例数据|rows=\[[^\]]+code:/, "不许拿示例数字顶上去");
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
  assert.doesNotMatch(strip(lib), /[\u4e00-\u9fff]"[\u4e00-\u9fff]/, "判读层同样不许");
  for (const b of code.match(/>([^<>{}]*[\u4e00-\u9fff][^<>{}]*)</g) ?? []) {
    assert.doesNotMatch(b, /"/, `JSX 文本节点里出现了 ASCII 双引号：${b}`);
  }
});

test("台账 §六 有第 22 个调用点这一行，且写的是只取 openReview 与 popHint", () => {
  assert.match(ledger, /StartupSpikeDaysBoard\.tsx/);
  assert.match(ledger, /第 22 个调用点/);
  assert.match(ledger, /只取 `openReview` 与 `popHint`/);
});
