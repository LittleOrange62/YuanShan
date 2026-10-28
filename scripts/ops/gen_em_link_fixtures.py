# -*- coding: utf-8 -*-
"""生成 `frontend/test/emUrl.fixtures.json` —— 行情外链的前后端共同真值表。

规则本体在 `app/quote_link.py`；浏览器里那份镜像 `frontend/src/lib/quoteLink.ts`
不可能回后端逐个问地址（全站 60 个渲染点），所以两边钉在同一张表上：

    python scripts/ops/gen_em_link_fixtures.py        # 改了规则就跑一次
    node --test frontend/test/quoteLink.test.ts       # 前端镜像对不对
    pytest tests/test_quote_link.py                  # 后端本体 + 这张表没漂

表里的码都是真实代码段：ETF/债/B股/指数/北交所/板块/伪代码各占几个，
`null` 表示"没有行情页，页面必须退回纯文本"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app import quote_link as ql  # noqa: E402

CASES = [
    # 沪深个股（东财池子给的是裸 6 位，所以三种写法都要归到同一个地址）
    '600367.SH', '600519.SH', '000001.SZ', '002827.SZ', '300750.SZ', '302132.SZ',
    '600367', 'sh600367', 'sz000001', '688981',
    # 基金 / 债券 / B 股
    '510300.SH', '159915.SZ', '588000.SH', '204001.SH', '113050.SH', '900901.SH', '200011.SZ',
    # 指数用 zs，且 000xxx 在 SH 是指数、在 SZ 是个股
    '000001.SH', '399001.SZ', '000300.SH', '899050.BJ',
    # 北交所：路径带斜杠。裸码那两条是 10-21 用户点名"换手率 TOP20 里北证的票没链接"钉进来的
    # （东财 clist 交的 `f12` 就是裸 6 位；`920` 必须抢在 `'9'→沪` 之前判，`900901` 是反过来那条：
    #  裸的 900 段仍要归沪 B，不能被 BJ 抢走）。
    '920000.BJ', '920047.BJ', '430047.BJ', '830799.BJ', '837174.BJ',
    '920296', 'bj920296', '920296.BJ', '900901',
    # 没有行情页的
    '880501.SH', '881000.SH', 'TDX_880501', '999999.SH', '60051', '', '一段中文',
]


def main() -> int:
    out = {
        '_comment': '由 scripts/ops/gen_em_link_fixtures.py 生成，不要手改；改了规则请重跑生成命令',
        'cases': {code: ql.em_url(code) for code in CASES},
    }
    path = ROOT / 'frontend/test/emUrl.fixtures.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    linked = sum(1 for v in out['cases'].values() if v)
    print(f'已写 {path.relative_to(ROOT)}：{len(CASES)} 条（{linked} 条有链接 / '
          f'{len(CASES) - linked} 条应为 null）', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
