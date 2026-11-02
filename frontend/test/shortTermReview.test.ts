import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

/**
 * 短线复盘页 2026-10-29 那一步并表的钉子（用户四点裁定 ①②③④）。
 *
 * 这一屏最容易漂的两件事，都在这里钉住：
 *  · **块序**：并表之后「涨停分析」与「跌停分析」是同一大块里的两张表，首板分析跟在后面 ——
 *    顺序写在页面上是给用户看的路线图，改一处就要跟着改说明，所以用源码文本钉住；
 *  · **两把尺不许并成一格**：涨停读 lup-1 库表、跌停读 stock_daily、两张榜读东财实时池，
 *    三份母体在同一屏上，任何一处把它们的数并成一句话，屏幕就开始说谎。
 * 还有一条是这一族反复栽过的：告警与缺数声明**留在屏上**，不折进说明抽屉。
 */

const read = (rel: string) => fs.readFileSync(new URL(rel, import.meta.url), 'utf8');
const page = read('../src/pages/FirstBoard.tsx');
const up = read('../src/components/review/LimitUpBoard.tsx');
const down = read('../src/components/review/LimitDownBoard.tsx');
const tier = read('../src/components/ui/TierPicker.tsx');
const apiSrc = read('../src/lib/api.ts');
const home = read('../src/components/home/MarketPanorama.tsx');
const system = read('../src/pages/System.tsx');

test('块序：事件流 → 两张榜 → 短线情绪（涨停分析＋跌停分析）→ 首板分析 → 首板四层过滤', () => {
  const at = (needle: string) => {
    const i = page.indexOf(needle);
    assert.ok(i >= 0, `页面上找不到「${needle}」`);
    return i;
  };
  // 钉的是**渲染出去的那一行**（文件头注释里也写着这些名字，直接 indexOf 会指到注释上）
  const 事件流 = at('<EventStreamPanel />');
  const 选择器 = at('本页交易日（只列库里真有的日子，下面四段共用）');
  const 原因榜 = at('title="涨停原因 · 重复出现的类别"');
  const 行业榜 = at('title="所属行业 · 重复出现的类别"');
  const 大块 = at('<Flame className="h-5 w-5 text-primary" /> 短线情绪');
  const 涨停 = at('<LimitUpBoard day={day} />');
  const 跌停 = at('<LimitDownBoard day={day} />');
  const 首板 = at('<FirstBoardDailyPanel />');
  assert.ok(事件流 < 原因榜 && 原因榜 < 行业榜, '两张榜要在事件流与短线情绪之间（用户 10-28 裁定 ④）');
  assert.ok(行业榜 < 大块 && 大块 < 涨停 && 涨停 < 跌停, '涨停分析与跌停分析同属短线情绪这一块');
  assert.ok(跌停 < 首板, '首板分析挂在短线情绪之后（用户 10-28 裁定 ③）');
  const 深析 = page.indexOf('id="first-board-deep"');
  assert.ok(首板 < 深析, '首板四层过滤在首板分析之后 —— 用户 10-29 点名的位置是"短线复盘最下方"');
  assert.ok(选择器 < 原因榜, '交易日选择器在它控制的每一块之前');
  // 一个 day 喂四段：各自造一个选择器就会读出"上面是今天、下面是上周"
  // 三块都要跟着顶部那一个交易日选择器走（涨停分析／跌停分析／首板四层深析）：
  // 各自造一个选择器就会读出「上面是今天、下面是上周」，而它们讲的是同一场。
  assert.equal((page.match(/day=\{day\}/g) ?? []).length, 3, '三块表共用同一个 day，不许其中一块自取场次');
  assert.match(page, /api\.firstBoard\(day \?\? undefined\)/, '两张榜也跟着这一个 day 走');
});

test('首页那份连板股表与首板分析都撤干净了（同一块屏不许挂两处）', () => {
  assert.doesNotMatch(home, /短线情绪 · |<FirstBoardDailyPanel|lianban_stocks\.map/,
    '首页还留着那份名单＝同一批票在两个屏各排一次序、各说一次口径');
  // 首页的三口径对照表仍然读 `emotion`（家数与比率在那一格），这条不许顺手删掉
  assert.match(home, /api\.emotion\(\)/, '首页对照表还在用那份读数，取数入口不许跟着撤');
});

test('档位多选只有一份实现，两侧共用同一套键、各用自己的中文', () => {
  for (const [who, src] of [['涨停分析', up], ['跌停分析', down]] as const) {
    assert.match(src, /from "@\/components\/ui\/TierPicker"/, `${who} 没接公用控件（自己排一档就会漂）`);
    assert.match(src, /tiersParam\(/, `${who} 的档位参数要走公用那份拼接（按后端键序排）`);
    assert.match(src, /DEFAULT_TIERS = \["2", "3", "4p"\]/, `${who} 默认勾满二档及以上（用户 10-28 点名）`);
    assert.match(src, /sessionStorage\.getItem\(TIER_KEY\)/, `${who} 的勾选要记住（用户那份说明的行为）`);
    assert.match(src, /<TierPicker[^>]*side="/, `${who} 没把这一侧告诉控件`);
  }
  // 档名与键序都由调用方从后端那份 join 出来交给控件（控件里不许长出第三份中文）
  assert.match(tier, /labels\[k\] \?\? k/);
  assert.doesNotMatch(tier, /['"](首板|二板|三板|四板及以上|首跌)['"]/, 'TierPicker 又抄了一份中文档名');
  // 一档没勾＝没有母体：不发那个会被后端理解成"用默认那批"的空请求
  assert.match(tier, /没有勾任何一档/, '这句话要留在屏上');
  assert.match(up, /if \(!tiers\.length\)[\s\S]{0,80}return;/, '涨停：一档没勾就不发请求');
  assert.match(down, /if \(!tiers\.length\)[\s\S]{0,80}return;/, '跌停：同上');
});

test('接口键名与信封：界面发的 date/tiers 必须就是 FastAPI 收的那两个', () => {
  for (const [name, path] of [['limitMove', 'limit-move'], ['limitDown', 'limit-down']] as const) {
    const seg = apiSrc.slice(apiSrc.indexOf(`  ${name}: (`));
    const body = seg.slice(0, seg.indexOf('},\n'));
    assert.match(body, /q\.set\("date", date\)/, `${name} 的键名不是 date`);
    assert.match(body, /q\.set\("tiers", tiers\)/, `${name} 的键名不是 tiers`);
    assert.ok(body.includes(`/market/${path}`), `${name} 没打到 /market/${path}`);
    assert.doesNotMatch(body, /\/api\//, `${name} 里不许写 /api（get 会自己补，写两次拼成 /api/api/）`);
  }
  assert.match(system, /"\/market\/limit-move\?date=&tiers=/, '涨停分析没进系统自检清单＝接口在跑却没人验');
  assert.match(system, /"\/market\/limit-down\?date=&tiers=/, '跌停分析同上');
});

test('跌停那一侧：本机没有的三族整列不出现，而不是摆 — 占位', () => {  for (const 禁 of ['封单额', '首封', '涨停原因']) {
    const hits = (down.match(new RegExp(`<SortHeader label="[^"]*${禁}`, 'g')) ?? []).length;
    assert.equal(hits, 0, `${禁}那一列在跌停侧没有读数来源，摆出来就是让屏幕说假话`);
  }
  assert.match(down, /整列不出现（不是摆 — 占位）/, '这句话要在说明里说清为什么少三列');
  assert.doesNotMatch(down, /useTemplatePicker|DeepDive|RunAllButton/,
    '话术库里没有「跌停股」这一型，借首板／连板那两套问法会把跌停股当涨停股问');
});

test('两把尺的差额与缺数声明都留在屏上（不折进说明抽屉）', () => {
  for (const [who, src] of [['涨停分析', up], ['跌停分析', down]] as const) {
    assert.match(src, /caliber_note && <NoticeBar kind="warning">/, `${who} 把后端那句日期口径/筛后声明折起来了`);
    assert.match(src, /available === false[\s\S]{0,200}<NoticeBar kind="warning">/,
      `${who} 库里没这一场时必须念"该跑哪一步"`);
    assert.match(src, /fb\.popHint && <NoticeBar kind="warning">/, `${who} 弹窗被拦那句告警不许藏`);
    assert.match(src, /勾选只筛这张表/, `${who} 要说清筛的是表、不是上面那排比率`);
    assert.match(src, /pageSentence\(/, `${who} 的分页走公用那份，页脚带共多少行`);
  }
  // 缺市值那一条是「缺数」型，后端已把它拼进 caliber_note；界面不许再自写一份
  assert.doesNotMatch(down, /流通市值读不到/, '界面不许自写一句缺数解释（第二份真相源）');
});

test('10-29 收口：首页留跳转入口、概念榜前 10 的截断与分母都留在屏上', () => {
  // ① 首页那块搬走后不是删掉了事：入口要能点到目的地的锚点（用户交付口径＝不问人也能找到）
  assert.match(home, /Link to="\/first-board\?to=first-board-daily"/, '首页没有留跳转入口');
  assert.match(page, /id="first-board-daily"/, '落地页没有那个锚点 ⇒ 链接点了不滚');
  assert.match(page, /new URLSearchParams\(loc\.search\)\.get\("to"\)/, '落地页不认 `?to=` ⇒ 入口等于摆设');
  // ② 概念榜：条数、集中度列、"只数不是百分比／集中度另有一把分母"那句、以及截断声明都在屏上
  const card = page.slice(page.indexOf('function ConceptCard'));
  assert.match(card, /只列前 \{rows\.length\} 个/, '截断了却不报被截掉的条数');
  assert.match(card, /<NoticeBar kind="truncated">/, '上限声明要走公用声明条（不许折进抽屉）');
  assert.match(card, /这一列加起来会大于分母，它不是占这批票的百分比/,
    '不写这句，那一列会被读成百分比（534.0% 那个鬼数犯过）');
  assert.match(card, /「集中度」是百分数，但分母换成了这个概念在全市场的成员数/,
    '右边那列确实是百分数，但分母是另一把尺 ⇒ 两列不是一把尺要说破');
  assert.match(card, /成员 \{r\.members_total \?\? "—"\}/, '宽概念天然排前面，不给成员数就是让人把"排第一"读成"最共性"');
  assert.match(card, /cb\?\.failed && <NoticeBar kind="warning">/, '查询坏了 ≠ 这批票没有题材，两种话要分开');
  // ③ 排序与挑选都在后端那一份里（`concept_rollup` 已按只数降序），界面不再 slice/排第二次
  assert.doesNotMatch(card, /\.slice\(0,|\.sort\(/, '界面又截一刀或排一次序 ⇒ 与后端 CONCEPT_TOP/排序分叉');
});
