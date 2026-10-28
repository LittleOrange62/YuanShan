import { Portfolio } from './Portfolio';
import { Watchlist } from './Watchlist';
import { PageHeader } from '@/components/ui/PageHeader';
import { ExplainBox } from '@/components/ui/DataPanel';

/**
 * 重点关注（持仓）。
 *
 * 2026-09-28 用户裁定：原来「我的股票」下并列的两项 —— 「持仓自选」与「持仓股」——
 * 合成这一页。它们本来就是同一本账的两个视图（持仓由交易日志的成交明细聚合而来），
 * 摆成两个入口只会让人不确定"该在哪一页看浮盈、在哪一页改备注"。
 *
 * 2026-10-15 用户第二次裁定（同一句话里说的两件事）：
 * 「这两边都有自选股，和重点关注请统一功能吧」+「重点关注（持仓）应该要有能够维护本地
 *   重点关注个股列表的功能」⇒ 这一页也摆上本机名单那一叠（`<Watchlist embedded />`）。
 * ⚠️ 那是**同一个组件、同一个 localStorage 键 `vr-watchlist`**，不是第二本账：
 * 在这里加一只、在「分组自选」那一页立刻看得见，反之也一样。原来 09-28 写的
 * 「这里再放一份就是第二个真相源」针对的是**再写一份实现**，不是第二个调用点 ——
 * 现在的形状是「一份实现、两处调用」（同页台账《公用组件：一份实现多处调用的读数块清单》）。
 *
 * 为什么不新造一张「重点关注表」：库里没有这张表、本机也没有第二个键，
 * 凭空造一个第三名单只会让「自选 ⊃ 持仓」那层包含关系多出一个说不清的中间集。
 * 要真分「自选」与「重点关注」两叠，得先定：存哪儿、跟同花顺那份 ini 怎么对账 —— 那是后话。
 */
export function MyStocks() {
  return <div className="space-y-8">
    <PageHeader title="重点关注（持仓）" level={1}
      subtitle="持仓由交易日志的成交明细聚合而来 · 数据只存本机 · 改数量去交易日志改那笔成交，这里跟着变。本机名单（自选）也在这一页维护，与「分组自选」那一页是同一份。" />
    <section aria-label="持仓"><Portfolio embedded /></section>
    <section aria-label="本机名单">
      <ExplainBox title="这一叠与「分组自选」那叠是什么关系">
        <p>
          同一份数据、同一个组件，不是两叠名单。它存在这台浏览器的 <code>vr-watchlist</code> 里
          （上限 100 只、没有分组、没有成本价），在两页任一处加删，另一页刷新就看到。
          库里那 48 组自选是另一套东西（真源是同花顺本机 <code>stockblock.ini</code>，每天 20:40 同步），
          这一叠不上传、也不参与那次同步。
        </p>
        <p>
          双击名单里的一行 → 另开一个浏览器窗口看那只票的「个股深度复盘」；
          「添加一只」那格与深度复盘页顶部那一个是同一个共用控件（代码 / 名称 / 拼音首字母都能搜）。
        </p>
      </ExplainBox>
      <div className="mt-2"><Watchlist embedded /></div>
    </section>
  </div>;
}
