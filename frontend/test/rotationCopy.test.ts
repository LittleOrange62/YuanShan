import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

/**
 * 「资金轮动」这一屏的两类规矩：
 *  A. 已经被数据推翻的旧文案不许留在界面上 —— 那句"本地不留历史 / fund_flow_rank 现 0 行 /
 *     连续流入算不出来"写的时候是真的，09-28 起库里按场入库了，留着就是假话；
 *     用户读到"算不出来"就不会再去看下面那张真榜（09-30 派活 #39 的前半段）。
 *  B. 包装话术不许出现 —— 实测份额连续 n 场抬升后次日仍抬升的概率 46%→31%（基线 46%），
 *     是均值回复；"主力连续吸金"这类说法把统计量说成了信号。
 */

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src');
const read = (rel: string) => fs.readFileSync(path.join(SRC, rel), 'utf8');
const allFiles = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
  const p = path.join(dir, e.name);
  return e.isDirectory() ? allFiles(p) : (/\.(tsx?|ts)$/.test(e.name) ? [p] : []);
});

test('被推翻的"本地没历史 / 0 行 / 算不出来"不许留在界面上', () => {
  const banned = [/fund_flow_rank[^。\n]{0,20}(现|当前)\s*0\s*行/, /连续流入[^。\n]{0,12}算不出来/, /是空表/];
  const hits: string[] = [];
  for (const f of allFiles(SRC)) {
    const s = fs.readFileSync(f, 'utf8');
    for (const re of banned) {
      const m = s.match(re);
      if (m) hits.push(`${path.relative(SRC, f).replace(/\\/g, '/')} → ${m[0]}`);
    }
  }
  assert.deepEqual(hits, [], '库里 09-28 起已按场入库（每场约 5,570 行）、份额榜有 1252 场，这些句子已经是假话');
});

test('份额榜不许被说成主力净流入或吸金', () => {
  const src = read('components/home/SectorRotationBoard.tsx');
  for (const phrase of ["主力连续吸金", "主力在吸", "资金抢筹", "连续吸金", "看好", "即将"]) {
    assert.ok(!src.includes(phrase), `界面上出现了包装话术：${phrase}`);
  }
  // 允许"不能当主力净流入读"这类否定式说明存在，所以只查上面对这些词的直接断言
});

test('均值回复那条实证由后端交出，界面不自己抄一份数', () => {
  const src = read('components/home/SectorRotationBoard.tsx');
  assert.match(src, /data\.evidence/, "evidence 必须来自接口，否则库里换了判据版本界面还说旧数");
  assert.doesNotMatch(src, /46%|31%/, "46%→31% 这组数只该住在库里的参数行与文档，不该硬编码在组件里");
});

test('榜上没有箭头符号（箭头会被读成"还会继续"）', () => {
  const src = read('components/home/SectorRotationBoard.tsx');
  assert.doesNotMatch(src, /[▲▼↑↓→⇅]/, "连续场次与累计那两列只讲已经发生的事，不画方向箭头");
});

test('days_N 与 cum_N 必须同屏（攒不满窗口的差不与满窗的差混排）', () => {
  const src = read('components/home/SectorRotationBoard.tsx');
  // 累计格必须把实有场次一起读出来 —— 调用点在组件里。
  // 2026-10-12 加了第二把尺子（换手口径）后，组件调的是 `cumCellB`（多一个 basis 参数）；
  // 判据没变（days 必须与 cum 同一次调用一起给），只是函数名多了个 B，所以这里放宽**名字**、不放宽**语义**。
  assert.match(src, /cumCellB?\([\s\S]{0,200}days_\$\{w\}/, "累计格必须把实有场次一起读出来");
  // "攒不满时写明只几场"那句话住在 `lib/rotation.ts`（读数格式的唯一出处，组件只做布局）。
  // 2026-10-06 之前这条断言在组件文件里找它，重构把格式搬进 lib 之后就长期红了 ——
  // 判据没变（那句话必须在），只是换了它该住的地方。
  assert.match(read('lib/rotation.ts'), /只 \$\{d\} 场/, "攒不满时要写明只几场");
});
