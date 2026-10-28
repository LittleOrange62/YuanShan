-- ys 影子 schema：d1~d7 的加工产出（双跑对账期与 analytics.* 并存，绝不同名双写）
-- 结构由 information_schema 实测镜像，列/类型/可空性/PK 与源表一致；
-- 唯一差异：**不分区**（源表按 trade_date 范围分区）。分区是物理布局不影响行内容，
-- 接管生产时再按同策略重建。生成方式见本次派活记录，改源表结构后要重新生成。

create table if not exists ys.indicator_value (
    security_code          text not null,
    trade_date             date not null,
    indicator_key          text not null,
    value                  numeric(24,8),
    task_id                bigint,
    primary key (security_code, trade_date, indicator_key)
);
comment on table ys.indicator_value is '双跑对账影子表，镜像 analytics.indicator_value；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.score (
    security_code          text not null,
    trade_date             date not null,
    capability_key         text not null,
    value                  numeric(16,4),
    rank_in_market         integer,
    percent_rank           numeric(8,4),
    quadrant               text,
    task_id                bigint,
    is_stale               boolean not null default false,
    calculated_at          timestamp with time zone not null default now(),
    primary key (security_code, trade_date, capability_key)
);
comment on table ys.score is '双跑对账影子表，镜像 analytics.score；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.score_detail (
    security_code          text not null,
    trade_date             date not null,
    capability_key         text not null,
    component_key          text not null,
    component_label        text,
    component_value        numeric(16,4),
    weight                 numeric(8,4),
    contribution           numeric(16,4),
    script_key             text,
    script_version         text,
    primary key (security_code, trade_date, capability_key, component_key)
);
comment on table ys.score_detail is '双跑对账影子表，镜像 analytics.score_detail；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.score_trend (
    security_code          text not null,
    trade_date             date not null,
    window_days            smallint not null,
    point_count            smallint,
    slope                  numeric(16,6),
    intercept              numeric(16,6),
    corr_coef              numeric(10,6),
    x_plus_y_max           numeric(16,4),
    x_plus_y_max_date      date,
    up_streak_days         smallint,
    first_quadrant         text,
    sparkline              text,
    task_id                bigint,
    calculated_at          timestamp with time zone not null default now(),
    primary key (security_code, trade_date, window_days)
);
comment on table ys.score_trend is '双跑对账影子表，镜像 analytics.score_trend；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.sector_heat (
    sector_code            text not null,
    trade_date             date not null,
    heat                   numeric(16,4),
    member_scored_count    integer,
    task_id                bigint,
    calculated_at          timestamp with time zone not null default now(),
    source                 text not null default 'member_agg'::text,
    part_count             smallint,
    primary key (sector_code, trade_date)
);
comment on table ys.sector_heat is '双跑对账影子表，镜像 analytics.sector_heat_daily；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.sector_trend (
    sector_code            text not null,
    trade_date             date not null,
    window_days            smallint not null,
    slope                  numeric(16,4),
    point_count            smallint,
    task_id                bigint,
    calculated_at          timestamp with time zone not null default now(),
    primary key (sector_code, trade_date, window_days)
);
comment on table ys.sector_trend is '双跑对账影子表，镜像 analytics.sector_trend；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.ignition (
    scope                  text not null,
    object_key             text not null,
    trade_date             date not null,
    stage                  text not null,
    score                  numeric(8,2),
    squeeze                numeric(8,2),
    position_score         numeric(8,2),
    ignition               numeric(8,2),
    volume                 numeric(8,2),
    breadth                numeric(8,2),
    term_count             smallint,
    detail                 jsonb not null default '{}'::jsonb,
    task_id                bigint,
    calculated_at          timestamp with time zone not null default now(),
    primary key (scope, object_key, trade_date)
);
comment on table ys.ignition is '双跑对账影子表，镜像 analytics.ignition；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.volume_double (
    trade_date             date not null,
    security_code          text not null,
    win_days               smallint not null,
    bars                   smallint,
    ma60_days              smallint,
    dbl_count              smallint,
    dbl_prev_count         smallint,
    cross_count            smallint,
    dev_med                numeric(10,2),
    dev_max                numeric(10,2),
    above_ratio            numeric(6,2),
    ma60_chg               numeric(10,2),
    avg_amt_yuan           numeric(20,2),
    close_last             numeric(16,4),
    ma60_last              numeric(16,4),
    task_id                bigint,
    calculated_at          timestamp with time zone not null default now(),
    primary key (trade_date, security_code)
);
comment on table ys.volume_double is '双跑对账影子表，镜像 analytics.volume_double；对账连续≥10交易日无差异之前，不是任何页面的数据源';

create table if not exists ys.market_breadth (
    trade_date             date not null,
    stocks                 integer,
    limit_up_count         integer,
    limit_down_count       integer,
    touched_count          integer,
    broken_count           integer,
    broken_rate            numeric(8,2),
    highest_board          smallint,
    board2_count           integer,
    board3_count           integer,
    board4plus_count       integer,
    prev_limit_up          integer,
    promotion_rate         numeric(8,2),
    red_rate               numeric(8,2),
    up_count               integer,
    down_count             integer,
    up_ratio               numeric(8,2),
    median_pct             numeric(10,4),
    task_id                bigint,
    calculated_at          timestamp with time zone not null default now(),
    primary key (trade_date)
);
comment on table ys.market_breadth is '双跑对账影子表，镜像 analytics.market_breadth；对账连续≥10交易日无差异之前，不是任何页面的数据源';
