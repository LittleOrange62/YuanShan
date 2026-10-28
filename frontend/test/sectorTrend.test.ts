import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/** 源码路径按本文件位置解析：从仓库根 `node --test frontend/test/*.test.ts`
 *  与从 frontend/ 里跑，都必须同一条命令成立（README 记的是前者）。 */
const SRC = new URL('../src/', import.meta.url).pathname.replace(/^\//, '');

/**
 * 板块趋势与低吸这一块的三条纪律。都打在源码上，因为要防的是"界面自己又判了一遍"。
 *
 * 判据只允许存在一处（`app/compute/sector_trend_signal.py` + 文档 05）。本轮实测抓到
 * `sts-1` 两个错：把横截面名次当自身热度分位用（最冷的板块被判过热）、
 * 以及从 `dir_agree` 得分反推方向（三窗 `[+2.1, -13.06, +0.65]` 被判"同向"）。
 * 修法都是**回到落库列上判**，所以页面这边要钉住：读表、不反推、缺项要说上限。
 */
const page = () => readFileSync(SRC + 'pages/SectorHeat.tsx', 'utf8');

test('趋势档位读的是落库端点，不在前端重算', () => {
  const src = page();
  assert.match(src, /useProbe<Trend>\("\/quant\/sector-trend"/, '档位表必须走 /api/quant/sector-trend');
  // 只禁"给行赋档位"这种写法；`?regime=turn_up_seed` 是查询串，本来就该出现
  assert.doesNotMatch(src, /\.regime\s*=[^=>]/, '页面不许自己给行赋 regime（那等于第二套判据）');
  // 同向 / 刚转 / 过热 这些结论只能来自后端的列或 reasons，不许在 JSX 里比大小推出来
  assert.doesNotMatch(src, /slope_mid\s*[<>]=?\s*0\s*\?[^]*?(刚转|形成|过热)/, '不许从斜率在前端反推档位');
});

test('缺项会压低低吸分上限，这个上限必须显示', () => {
  const src = page();
  assert.match(src, /dip_score_ceiling/, 'reasons.dip_score_ceiling 要上屏');
  assert.match(src, /上限/, '上限低于 100 时屏幕上要看得见"这一行的上限只有 N"');
});

test('最新一场没有"刚转"要说成读数，不能说成缺数', () => {
  const src = page();
  assert.match(src, /刚转[\s\S]{0,120}读数，不是缺数|最新一场没有[^\n]*刚转[^\n]*读数/);
  assert.match(src, /后端交出 0 行[\s\S]{0,160}没有趋势/, '空榜要说"没命中筛选条件"，不能读成"没有趋势"');
});
