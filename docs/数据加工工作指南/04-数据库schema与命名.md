# 04 · 数据库 schema 与命名

库：PostgreSQL `financetool`（`127.0.0.1:5432`，数据目录 `D:\finance\TataPG`）。
DSN 在本项目根目录 `.env`：`FT_DSN_READER/APP/ETL/ADMIN` 四个角色（`FT_` 前缀是历史包袱、不是依赖，
`app/config.py` 就按这个名字读）。**本项目是这套库唯一的写入方**（读可以有别的消费者，写/更新/删除只有这里）。
**没有 `FT_DATABASE_URL` 这个变量**，统一走 `FT_DSN_*`。

## ⚠️ 2026-09-22 全库改名：简写 → 全称

写 SQL **一律用全称 schema 名**。旧的 `md./ref./fct./ops.` 简写已不存在
（那是早期文档/口头简写，standalone 用会 `UndefinedTable`）：

| 旧简写 | 真实 schema | 装什么 |
|---|---|---|
| `md.` | `market_data` | 行情：stock_daily / index_daily / fund_daily / sector_daily / stock_minute / stock_snapshot |
| `ref.` | `reference_data` | 基础：security / trade_calendar / sector_info / sector_member / dividend / share_capital / stock_profile |
| `fct.` | `analytics` | 因子/评分/热度 |
| `ops.` | `operations` | 任务/审计/血缘/质量/schema_migration |
| `config.` | `config` | 注册表/自选/用户（本来就是全称） |

## 关键列名坑（与直觉不同）

- `reference_data.security` 主键列是 **`security_code`**，不是 `ts_code`。
- `reference_data.dividend` / `sector_member` 里标的列也是 **`security_code`**。
- `market_data.stock_snapshot` 用 **`security_code`** + `trade_date`。
- ~~新表 `reference_data.stock_profile` 用的是 `ts_code`~~ **10-18 现查不成立**：它的首列是 `security_code`（`security_code, hq_date` 是主键，见 `app/db/sql/08_tdx_profile.sql`）⇒ 这张表从一开始就跟规范一致，本行原先写反了。**要背的规矩没变**：全库一律 `security_code`，`ts_code` 只准出现在外部源的原样字段名里。
- 板块主键统一 `TDX_` + 六位码（如 `TDX_880301`），和 `market_data.sector_daily` 对齐才能 join；
  历史遗留 `GN_`/`FG_` 前缀走 `reference_data.sector_alias` 别名表。

## stock_minute（分钟线）要点

- 按**月**分区（10-18 现查 49 个分区，如 `stock_minute_202608`），主键 `(dt, period, security_code)`，`period ∈ {1,5}`。⚠️ 分区粒度写错会让人按年去建索引/回补；旧文本写"按年 + ts_code"两处都不对。
- `dt` 是 `timestamptz`；连接会话 timezone=Asia/Shanghai，入库显式带 `ZoneInfo('Asia/Shanghai')` 最稳。
- 只有两条 CHECK：`period IN (1,5)`、`low<=high`。**没有**日线那种单位一致性触发器。

## stock_profile（个股档案，本次新增）

- 迁移：`app/db/sql/08_tdx_profile.sql`（在迁移清单 `APPLY_ORDER` 里；该清单所在的一键跑器见下节，
  本项目当前**只留了归档件、没有现役入口，待补**）。
- 一行 = 一票一个快照日（`get_more_info` 只给当前值）。
- 挑了 11 个常用列（主营/涨跌停/历史高低/IPO/Beta/员工数/换手/PE/PB），
  其余 80+ 字段全进 `raw` JSONB，将来要哪个再挑。

## 涨跌停价的**当日**权威（2026-10-07 定）

一句话说清：**"那一天是哪一档"和"这只票现在算哪一档"是两个问题**，以前只用后者回算全部历史，
所以历史是错的。现在的分工：

| 东西 | 位置 | 答什么 |
|---|---|---|
| 当日档位事实 | `reference_data.limit_price_daily`（DDL `app/db/sql/26_limit_price_daily.sql`） | 通达信当日 `ZTPrice/DTPrice` 反推出的**档位**（存 band 不存价格），一行 = 一票一场；当前 22 场（2026-09-01 起，只有带 `Code` 列的表可用） |
| 每行的出处 | `market_data.stock_daily.limit_basis` | 这行的 `limit_up/limit_down` 是按哪一层算出来的：`tdx` / `proved_non_st` / `st_period` / `rule_st_snapshot` / `rule` / `unbounded` / `no_limit_first5` / `contradicted` |
| 次新判据的物化 | `reference_data.security_session_span`（DDL `app/db/sql/27_security_session_span.sql`） | 一码一行：`first_session` / `fifth_session`。"这天是不是这只票的头 5 场"以前靠全表窗口或相关子查询，**两条都实测过、都贵**（40 分钟看不见进度 / 单日 4.6 秒）；`list_date` 全库 0/5,577 有值，指望不上 |
| 唯一写入口 | `app/compute/limit_prices.py` | `--seed-tdx` 灌档位、`--recompute` 重刷两列（可带 `--date` 或 `--since/--until --months` 按片跑）、`--check` 哨兵、`--restore <备份表>` 回退 |
| 兜底公式 | `reference_data.board_limit_ratio(board, is_st)` SQL 函数 | **只答"按当前快照属于哪一档"**（`security.is_st` 停在 2026-08-27）；仅在 `limit_up` 为空时作近似，不许再拿它写历史 |

`app/db/repo/quote.py::recompute_limits()` 现在只是转调 `limit_prices.recompute()` 的门面，
不再自己拼公式。口径与行情**同基**是硬约束：`limit = round(pre_close × (1 ± band), 2)`，
`pre_close` 与 OHLC 同基（除权日它是除权参考价），所以通达信那个**未复权**价一律不采信数值、
只用来反推档位。

配套的质量规则（⚠️ **没有 `23_quality_rules_limit_prices.sql` 这个文件** —— 10-18 现查 23 号是 `23_allocator.sql`；这三条规则的实际出处是**库里的 `config.quality_rule`**（`severity`/`check_kind`/`threshold` 那几列）+ 写入它们的迁移语句，一共 30 条 = 23 error + 7 warning）：
`close_within_limit`（error 级描述：收盘价越过当日涨跌停价）、`limit_on_band`
（limit/pre_close 不落在四个合法档）、`limit_basis_present`（warning：新列还空着＝没刷过）。

## 迁移怎么落地

⚠️ **本项目尚无该入口，待补（2026-09-25 核）**：原本按 `APPLY_ORDER` 用 psql 跑
`app/db/sql/NN_*.sql`（幂等，可重跑）、并把每个文件 sha256 记进 `operations.schema_migration`
的那个跑器（旧名 `scripts/init_db.py`）**没有现役副本** —— 项目树里只剩归档件
`legacy\pre_rename_20260921_scripts\init_db.py`，它按自己那套 `BASE_DIR` 找 SQL，直接拿来跑会指错目录。
校验和的判据照旧成立：**校验和变了＝脚本改过但库没重跑**。
在补回入口之前，单独应用一个文件走 psql 手动跑：
```bat
set PGCLIENTENCODING=UTF8
"D:\PostgreSQL\17\bin\psql.exe" "postgresql://<admin>@127.0.0.1:5432/financetool" -f app\db\sql\08_tdx_profile.sql
```
（口令按 psql 的提示输入，**别写进命令行** —— 会进 shell 历史与诊断输出。）
含中文的 SQL 必须 UTF-8 文件 + `PGCLIENTENCODING=UTF8 -f`（psql 的编码是会话级的，不设就按 GBK 读、中文注释进库即乱码）。
