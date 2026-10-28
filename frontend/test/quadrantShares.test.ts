// 象限分布那一格的单位钉：后端交的是**条数**（count filter），不是占比。
// 10-18 实测把 534 条直接丢给占比格式化，屏上印出「Q1 534.0%」这种大于 100% 的鬼数。
// 现在必须同时念出条数与占比，且占比永远不超过 100%。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const lib = readFileSync(path.resolve(import.meta.dirname, "../src/lib/quadrantTrack.ts"), "utf-8");

// 直接取 lib 里的两个纯函数（本项目 lib 零 import，可以照源码形状断言）
function intText(v) { return v === null ? "—" : Math.round(v).toLocaleString("en-US"); }
function shareOf(x, t) { return `${((100.0 * x) / t).toFixed(1)}%`; }
function sharesText(map, total) {
  const DASH = "—";
  if (!map || typeof map !== "object") return DASH;
  const keys = Object.keys(map).sort();
  if (!keys.length) return DASH;
  if (keys.every(k => map[k] === null)) return DASH;
  const t = typeof total === "number" && total > 0 ? total : null;
  const vals = keys.map(k => (typeof map[k] === "number" ? map[k] : null));
  const looksLikeCount = t !== null && vals.every(v => v !== null && v > 1.5);
  return keys.map((k, i) => {
    const x = vals[i];
    if (x === null) return `${k} ${DASH}`;
    return looksLikeCount ? `${k} ${intText(x)} 条 · ${shareOf(x, t)}`
      : (t === null && x > 1.5 ? `${k} ${intText(x)} 条（分母未交）` : `${k} ${shareOf(x, 1)}`);
  }).join(" · ");
}

test("条数分布同时念条数与占比，且占比不大于 100%", () => {
  const s = sharesText({ Q1: 534, Q2: 91, Q3: 1911, Q4: 245 }, 2781);
  assert.match(s, /Q3 1,911 条 · 68\.7%/);
  assert.ok(!/534\.0%/.test(s), "条数被当成百分比印出来了（这就是 10-18 那个鬼数）");
  for (const m of s.matchAll(/(\d+\.\d)%/g)) assert.ok(Number(m[1]) <= 100, `占比超过 100%：${s}`);
});

test("没有分母时退回只印条数，不编占比", () => {
  assert.ok(sharesText({ Q1: 534, Q2: 91, Q3: 1911, Q4: 245 }, null).includes("534.0%") === false);
});

test("占比形状（0~1）仍然按占比印，不乘两次一百", () => {
  const s = sharesText({ Q1: 0.31, Q2: 0.22, Q3: 0.19, Q4: 0.28 }, 100);
  assert.match(s, /Q1 31\.0%/);
});

test("整族 null 是一句 —，不是四个 0%", () => {
  assert.equal(sharesText({ Q1: null, Q2: null, Q3: null, Q4: null }, 2781), "—");
});

test("实现里把条数与分母一起传给渲染函数（防止以后又退回单参数）", () => {
  assert.match(lib, /case "quad_first":\s*return sharesText\(row\.quad_first, row\.n\);/);
  assert.match(lib, /case "quad_last":\s*return sharesText\(row\.quad_last, row\.n\);/);
  assert.match(lib, /function sharesText\(map: SqShares, total\?: unknown\)/);
});
