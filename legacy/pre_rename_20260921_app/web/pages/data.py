# -*- coding: utf-8 -*-
"""
数据管理 —— 一页回答关于数据的三个问题，分三个标签：

- **① 数据加工**：拿到数据之后怎么变成分数（一键数据加工 + 按计算维度拆的功能点）
- **② 数据现状**：数据到齐了吗（同步/迁移批次、表行数、每日覆盖度、迁移基线）
- **③ 数据质量**：到的数据对吗（校验结果、规则登记、字段口径字典）

2026-08-30 把原来的 `/quality`「数据质量」并进来。分两页的实际后果是每天要点
两个地方，而且看之前得先想一下"这个问题算现状还是算质量" —— 而这两件事本来
就是同一次盘后检查的上下半场。质量那半截的卡片仍然写在 `quality.py` 里
（`quality.panel()`），这里只提供页面壳子和标签。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from app.db import engine
from app.web import queries, ui
from app.web.pages import dataprocess, quality


def _baseline_vs_now():
    """迁移前基线 vs 现在，一眼看出哪张表迁了、迁了多少。"""
    return engine.query(
        """
        WITH b AS (
            SELECT src_db, src_table, row_count
              FROM ops.migration_baseline
             WHERE row_count > 0
        )
        SELECT src_db AS 旧库, src_table AS 旧表, row_count AS 迁移前行数
          FROM b ORDER BY row_count DESC LIMIT 40
        """)


def _now_rows():
    return engine.query(
        """
        SELECT table_schema || '.' || table_name AS 表, n_live_tup AS 约行数
          FROM information_schema.tables t
          JOIN pg_stat_user_tables s
            ON s.schemaname = t.table_schema AND s.relname = t.table_name
         WHERE t.table_schema IN ('md','ref','fct','config','ops')
           AND n_live_tup > 0
         ORDER BY n_live_tup DESC LIMIT 40
        """)


def _status_panel():
    """① 数据现状：到齐了没有。"""
    return html.Div([
        dbc.Row([
            dbc.Col(ui.card('最近同步/迁移批次',
                            ui.table(queries.q_recent_batches(15)),
                            help_key='data.batches'),
                    width=12, lg=7, className='mb-3'),
            dbc.Col(ui.card('当前表行数',
                            ui.table(_now_rows()),
                            help_key='data.rows'),
                    width=12, lg=5, className='mb-3'),
        ], className='g-3'),
        dbc.Row([
            dbc.Col(ui.card('每日覆盖度（个股条数 / 有换手率 / 有涨跌停）',
                            ui.table(queries.q_coverage(60)),
                            help_key='data.coverage',
                            footer='条数突降的日期要查是真停牌还是迁漏了'),
                    width=12, lg=7, className='mb-3'),
            dbc.Col(ui.card('迁移前基线（ops.migration_baseline）',
                            ui.table(_baseline_vs_now()),
                            help_key='data.baseline',
                            footer='迁移动手前抓的快照，B6 对账用它当判据'),
                    width=12, lg=5, className='mb-3'),
        ], className='g-3'),
    ])


def render():
    """
    两个标签都在首屏一次性渲染完（`dbc.Tabs` 的 children 是静态的），
    不做懒加载：两边加起来 7 张卡、都是小查询，实测秒级；为它引一套
    tab 切换回调等于给一个不存在的性能问题加一层状态。
    """
    return dbc.Tabs([
        dbc.Tab(label='① 数据加工', tab_id='process',
                children=dataprocess.panel()),
        dbc.Tab(label='② 数据现状', tab_id='status',
                children=_status_panel()),
        dbc.Tab(label='③ 数据质量', tab_id='quality',
                children=quality.panel()),
    ], id='data-tabs', active_tab='process', className='mb-2')


def register_callbacks(app) -> None:
    """
    质量那半截有交互（自动补齐审计 + 轮询），所以这一页现在要注册回调。

    ★两个标签都是**静态渲染**的，所以 ② 里的 `dcc.Interval` 从首屏起就在跑 ——
    "打开 /data 就自动补齐审计"不需要先切到那个标签。
    """
    quality.register_callbacks(app)
    dataprocess.register_callbacks(app)
