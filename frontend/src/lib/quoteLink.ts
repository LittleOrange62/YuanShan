/**
 * 东财行情外链：把 `600367` / `600367.SH` / `sh600367` 换成 `https://quote.eastmoney.com/…`。
 *
 * 规则的真源是 Python 侧 `app/quote_link.py`（四条路径 2026-08-29 逐个真实请求核过，
 * 看返回的 `<title>` 不是猜的）。这里是它在浏览器里的镜像 —— 全站 60 个渲染点不可能
 * 每处回后端要一次地址。**两边的共同真值表**是 `test/emUrl.fixtures.json`
 * （由 `python scripts/ops/gen_em_link_fixtures.py` 生成）：改了任何一边，
 * `node --test frontend/test/quoteLink.test.ts` 与 `pytest tests/test_quote_link.py` 就会红。
 *
 * 最容易写错的是北交所：东财的路径是 `bj/920000`（**带斜杠**），`bj920000` 是 404。
 * 指数用 `zs` 前缀（`000001.SH` 是上证指数，`000001.SZ` 才是平安银行个股）。
 * 认不出的（板块 `880/881`、通达信伪代码 `999xxx`、脏串）一律返回 null，
 * 调用方退回纯文本 —— 少一个链接可以接受，少一个代码不行。
 */

const EM_TEMPLATE = 'https://quote.eastmoney.com/{path}.html';

/** 代码占位符，与后端 `app/quote_link.py:PLACEHOLDER` 同一个串。 */
const PLACEHOLDER = '******';

const BOND_PREFIX = ['10', '11', '12', '13', '19', '20'];
const FUND_PREFIX = ['50', '51', '52', '53', '55', '56', '58', '15', '16', '18'];
const B_SHARE_PREFIX = ['900', '200'];
const TDX_PSEUDO_PREFIX = ['999'];
const SECTOR_PREFIX = ['880', '881'];
const STOCK_PREFIX: Record<string, string[]> = {
  SH: ['600', '601', '603', '605', '688', '689'],
  SZ: ['000', '001', '002', '003', '300', '301', '302'],
  BJ: ['430', '830', '831', '832', '833', '834', '835', '836', '837', '838', '839',
    '870', '871', '872', '873', '874', '875', '876', '877', '920'],
};
const INDEX_PREFIX: Record<string, string[]> = { SH: ['000'], SZ: ['399'] };
const LINKABLE = ['stock', 'fund', 'bond', 'bshare', 'index'];

/** 裸 6 位码猜市场。**不只服务手输**：东财 clist 那些榜单（首页换手率 TOP20 等）交回来的就是裸 6 位。
 *  顺序就是判据 —— **长前缀先判**：`920`（北交所新码段）必须排在 `'9'`（沪 B 是 900xxx）之前，
 *  否则 920296 被认成沪市 → 归类 unknown → 按"认不出不给链接"退回纯文本（10-21 用户点名那条）。
 *  与后端 `app/quote_link._BARE_MARKET` 逐字同序，两边钉在 `emUrl.fixtures.json` 上。 */
const BARE_MARKET: Array<[string[], string]> = [
  [['920'], 'BJ'],
  [['6', '9', '5'], 'SH'],
  [['0', '1', '2', '3'], 'SZ'],
  [['4', '8'], 'BJ'],
];

const TS_CODE = /^(\d{6})\.(SH|SZ|BJ)$/i;
const PREFIXED_CODE = /^(SH|SZ|BJ)(\d{6})$/i;
const BARE_CODE = /^(\d{6})$/;

function starts(prefixes: string[], symbol: string): boolean {
  return prefixes.some(p => symbol.startsWith(p));
}

/** `'sh600367'` / `'600367.SH'` / `'600367'` → `['600367', 'SH']`；认不出返回 null。 */
export function normalize(code?: string | null): [string, string] | null {
  const s = String(code ?? '').trim();
  if (!s) return null;
  let m = TS_CODE.exec(s);
  if (m) return [m[1], m[2].toUpperCase()];
  m = PREFIXED_CODE.exec(s);
  if (m) return [m[2], m[1].toUpperCase()];
  m = BARE_CODE.exec(s);
  if (m) {
    for (const [heads, market] of BARE_MARKET) {
      if (heads.some(h => m![1].startsWith(h))) return [m[1], market];
    }
  }
  return null;
}

/** 品种归类，顺序与 `app/ingest/instrument.py:classify()` 一致：代码段优先，市场只在个股/指数那一步才看。 */
export function classify(symbol: string, market: string): string {
  if (starts(SECTOR_PREFIX, symbol)) return 'sector';
  if (starts(TDX_PSEUDO_PREFIX, symbol)) return 'pseudo';
  if (starts(B_SHARE_PREFIX, symbol)) return 'bshare';
  if (starts(BOND_PREFIX, symbol)) return 'bond';
  if (starts(FUND_PREFIX, symbol)) return 'fund';
  if (starts(STOCK_PREFIX[market] || [], symbol)) return 'stock';
  if (starts(INDEX_PREFIX[market] || [], symbol)) return 'index';
  if (market === 'BJ' && (symbol.startsWith('899') || symbol.startsWith('810'))) return 'index';
  // 前端没有 `asset_types` 那层兜底，认不出就不给链接（宁缺勿错）
  return 'unknown';
}

/** 行情页路径段（`sh600367` / `zs000001` / `bj/920000`）；没有对应页面返回 null。 */
export function emPath(code?: string | null): string | null {
  const parts = normalize(code);
  if (!parts) return null;
  const [symbol, market] = parts;
  const kind = classify(symbol, market);
  if (!LINKABLE.includes(kind)) return null;
  // 指数排在北交所之前：北证50 只有 `zs899050` 打得开，`bj/899050` 实测 404
  if (kind === 'index') return `zs${symbol}`;
  if (market === 'BJ') return symbol.startsWith('920') ? `bj/${symbol}` : null;
  // 北交所老码段 43/83/87 换码后 `bj/<老码>` 全 404（2026-09-26 实测），不拼死链
  return `${market.toLowerCase()}${symbol}`;
}

/** 这行代码属于配置表里的哪一行（与后端 `app/quote_link.route_id` 同一套顺序）。 */
export function routeIdOf(code?: string | null): string | null {
  const parts = normalize(code);
  if (!parts) return null;
  const [symbol, market] = parts;
  const kind = classify(symbol, market);
  if (!LINKABLE.includes(kind)) return null;
  if (kind === 'index') return 'index';
  if (market === 'BJ') return symbol.startsWith('920') ? 'bj' : null;
  if (kind === 'stock' && starts(['300', '301', '302'], symbol)) return 'cyb';
  if (kind === 'stock' && starts(['688', '689'], symbol)) return 'kcb';
  return market.toLowerCase();
}

/** 模板合法性 —— `app/quote_link.py:bad_template` 的镜像，四条闸一条都不能少：
 *  非空、无空白、https 开头、**恰好一个** `******`。
 *  两个占位是最阴的一种坏法：`replace` 只换第一个，剩下那个 `******` 会原样进地址，
 *  点开是一只不存在的票。后端那格根本存不进去，这里防的是读到旧配置 / 直接改文件的场景。 */
export function badTemplate(tpl?: string | null): boolean {
  const s = String(tpl ?? '').trim();
  if (!s) return true;
  if (/\s/.test(s)) return true;
  if (!s.startsWith('https://')) return true;
  return s.split(PLACEHOLDER).length !== 2;
}

/** 完整地址；没有对应页面时返回 null，调用方原样显示文字。
 *  `templates` 是从系统配置那张表拿到的按市场模板（`******` = 代码占位）；
 *  没拿到（旧后端 / 还没加载完）就按上面的出厂路径拼 —— 两条路算出来的地址必须一样。 */
export function emUrl(code?: string | null, templates?: Record<string, string> | null): string | null {
  const parts = normalize(code);
  const rid = parts ? routeIdOf(code) : null;
  const tpl = rid ? (templates ?? {})[rid] : undefined;
  if (parts && rid && typeof tpl === 'string' && !badTemplate(tpl)) {
    // 只换代码：一行模板写坏了就整条退回出厂口径，
    // 宁可不跟配置走，也不拼出一条没有代码的死链出去。
    return tpl.trim().replace(PLACEHOLDER, parts[0]);
  }
  const path = emPath(code);
  return path ? EM_TEMPLATE.replace('{path}', path) : null;
}
