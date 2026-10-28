<p align="center"><a href="README.md">简体中文</a> | <b>English</b></p>

<h1 align="center">YuanShan</h1>

<p align="center">
  <b>A local workbench for short-term A-share research</b><br>
  Local collection · rules computed into a database · boards and reviews · trading journal · AI interpretation
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License">
  <a href="LICENSE.local.md"><img src="https://img.shields.io/badge/use-non--commercial-red.svg" alt="Use limits"></a>
  <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/react-19-61DAFB.svg?logo=react&logoColor=white" alt="React">
  <a href="https://github.com/simonlin1212/vibe-astock/releases/tag/v1.1.0"><img src="https://img.shields.io/badge/version-v1.1.0-orange.svg" alt="v1.1.0"></a>
</p>

<p align="center">
  <a href="#use-limits">Use limits</a> · <a href="#what-this-is">What this is</a> ·
  <a href="#interface-nine-groups">Interface</a> ·
  <a href="#where-the-data-comes-from">Data chain</a> · <a href="#how-it-runs-itself-every-day">Automation</a> ·
  <a href="#quick-start">Quick start</a> · <a href="#connecting-ai">Connect AI</a> ·
  <a href="#data-and-privacy">Data and privacy</a> · <a href="#documentation-map">Docs</a> ·
  <a href="#validation-scope">Validation</a> · <a href="CHANGELOG.md">Upstream changelog</a>
</p>

## Use limits

**This project is for educational and non-commercial purposes only. Commercial use is strictly prohibited.**

In plain terms: using it yourself, processing your own data and modifying the code are fine; **commercial use is not**, and **it may not be handed to a team of more than six people**. Ask first before distributing it anywhere.

One boundary that should not be glossed over: the base of this repository is the upstream vibe-astock v1.1.0, published under **Apache-2.0** (root [`LICENSE`](LICENSE)), which itself permits commercial use and redistribution; `backtest/` and `research_data/` are MIT (see their [NOTICE](backtest/NOTICE.md) files). So the sentence above governs **the parts this repository writes itself**, and is not a re-licensing of the whole repository as non-commercial (that would need the upstream rights holders' agreement, which does not exist). **The licence text and its scope are in [`LICENSE.local.md`](LICENSE.local.md)** (Chinese original; the English summary below is not the operative text) — it splits everything into three tiers: directories written entirely locally, mixed directories judged file by file, and third-party components staying under their own licences. Where authorship cannot be established, the upstream licence applies and this licence makes no claim.

Before sharing or committing anything, check that it does not contain: `.env` (database passwords, the TDX account, provider tokens), the LAN gateway key prefilled inside `frontend/dist` (see the warning under [Connecting AI](#connecting-ai)), or any local data directory (`~/.duanxian-agents/`, `~/.vibe-astock-agent/`, database backups).

## What this is

YuanShan collects the work that repeats every day in short-term A-share research onto one machine: pull quotes, compute each rule set into a database, read the boards and reviews in a local web page, and let AI interpret the material when asked.

**Two layers, one boundary.** The product base is vibe-astock v1.1.0 (Apache-2.0: the official OpenAI Codex Harness, review, debate, backtesting and AI access all come from that line). On top of it this repository adds a **local data asset**: a PostgreSQL database, `ingest/` collectors, `app/compute/` rule computations, and `duanxian/*_view.py` as the read-only layer. The boundary: **every number on a board comes from this machine's own database** — no external call is made while a request is being served.

Market data, program computation, manual notes and AI interpretation are presented separately. Quotes and statistics are never AI-generated; AI explains material, contrasts viewpoints and lists conditions still to be verified. Sentiment tiers are an analysis framework, not objective fact, and no single indicator may drive a trading conclusion.

### Interface preview

Dark is the default theme; light is kept. The sidebar groups by workflow, second-level sections expanded by default and individually collapsible. Tables share the same basics: click a column header to sort (the whole batch, not just the current page), selecting a cell highlights its row and column, double-clicking a stock row opens that stock's deep review in a new tab, and sector names link to the East Money board page.

![Dark web review interface](assets/screenshots/09-web-review-dark-20260909.png)

Screenshot taken 2026-09-09, showing the 2026-09-08 public market review and its coverage notes. It is not live quotes and not a return example. **This image shows the upstream v1.1.0 interface**; the section below lists today's navigation.

## Interface: nine groups

In the order the code registers them (`frontend/src/lib/workspace/modules.ts`):

| Group | Pages |
|---|---|
| Home | Chat entry, market panorama, risk signals (breadth × Arms Index × range divergence), consecutive-limit ladder, first-board day readings, capital rotation, intraday anomaly board |
| Data chain | Collection, computation, quality gate, coverage check, TDX client control, pipeline overview |
| Quant screening | Four-dimension quadrant, screener, sector heat and ignition, volume-double pattern, breakout signals, confluence board, anomaly screening, trend stocks, allocator view |
| Review | Review reports, first-board analysis, 5-day heat, historical statistics, quantdash caliber comparison |
| News radar | News aggregation and event probabilities, with source, timestamp and coverage state |
| Stock research | Deep stock review (dossier, trend quantification, price/volume history, similar samples, bull-bear debate, AI read-back — one screen, one source of truth per ticker) |
| Backtest | AI arranges a historical rule, user confirms scope, deterministic engine computes, report archived |
| My stocks | Positions, grouped watchlist (local list plus group analytics), research notes |
| Trading journal | Ledger · review notes · reminders (three tabs, one screen) |

Counts: **9 top-level groups, 28 navigation entries; the route table has 35 entries** (31 real pages + 3 legacy redirects + 1 catch-all). The sidebar footer holds a fixed operations area that is not in the table above: **AI access** (sources and models), **System config** (rule thresholds, parameters, capability checklist), **Job scheduler** (switches, times and ledgers for the registered loops).

Removed or merged (so that old documents do not send you looking for pages that are gone): the "Market watch" top-level module was retired on 2026-09-27 (real-time feed, yesterday's ladder, intraday verification), the daily market page merged into Home, trend scoring merged into the grouped watchlist, watchlist analytics merged into the top of that page, and stock research plus deep dive merged into one "Deep stock review" screen. `/portfolio`, `/stock-data` and `/agent/deepdive` now only redirect.

## Where the data comes from

### Collection: local jobs, one channel per boundary

| What | Channel | Boundary (what is said when it fails) |
|---|---|---|
| Daily bars | TDX local `vipdoc/*.day`; second leg is the TQ local interface of the logged-in client | No running client, no new bars; a failed source is named, never presented as the whole market |
| 1 / 5-minute bars | TDX `*.lc1` / `*.lc5` downloaded after the close | This leg depends on a desktop window and can fail when nobody is at the machine; a day that never lands is gone for good |
| Closing snapshot (market cap, PE, PB) | Local snapshot table | Missing fields stay `NULL`, they do not enter rankings |
| Sector bars, membership, sector money flow | TDX 880 / 881 series plus an East Money board crosswalk | `880001~880099` are market-level statistical indices, not sectors, and are kept out of sector boards |
| Broad-market capital flow (main / super-large / large / medium / small orders) | East Money datacenter interface | One call returns a look-back of trading days; when the host is unreachable the source is named |
| Per-stock main capital flow | **Sina, the only channel on this machine** (the old East Money `push2` lane drops roughly every 5-6 requests) | The payload carries no date field, so **a day you do not collect is a day you never have** |
| Dragon-Tiger list, margin trading, ETF shares, dividends, share count | Public interfaces | Coverage and missing days are recorded per source |
| Multi-period financials (cumulative + single quarter) | Tonghuashun | When the base period is near zero the source can emit absurd ratios; the read side caps them, returns `NULL`, **and names the field** |
| Newswire | 30-second polling into local jsonl | It lands; it does not promise every item |
| Watchlist / positions | Mirrored from Tonghuashun `stockblock.ini` | Full overwrite from the ini by default; unrecognised market codes are named, not silently dropped |

**Price caliber:** every rule uses the back-adjusted price (`close × adjustment_factor`, continuous across ex-dividend days) rather than the unadjusted `pct_change` column; returns are counted in **market sessions**, not in that stock's own candle count. If a ticker has no row on a session (halted), the cell stays empty instead of borrowing an earlier or later day.

### The daily computation chain: 42 steps in one command

`STAGES` in `ingest/jobs/e1_fill_all.py` — **42 steps as measured 2026-10-22** (the count grows as rules are added; query it live):

```text
tdxdl daily snapshot breadth sentiment voldbl breakout ambush volratio startupvol
spike sysentry trendstock boardnode abnormalmove qd_ladder qd_sentiment qd_sector
etf margin lhb fundflow fundflow_rank sectorflow boardlink dividend share financials
fbshape fbdaily quadtrack spikedays indicators scores sectortrend ignition scoretrend
signal flowtrend allocator confluence quality
```

The first steps fetch and land data, the middle ones compute each board (volume-double, breakout, ambush, pre-startup volume, intraday spikes, the three-condition system entry, trend stocks, board nodes, anomaly alerts, the qd comparison track…), `indicators` / `scores` / `sectortrend` are backbone steps that downstream work depends on, `confluence` merges six boards into the cross-hit ranking, and `quality` writes the gate verdicts. Re-computation and single-step re-runs use the same command — `-m ingest.jobs.e1_fill_all --only <step>` — and every step keeps its own ledger (whether it ran, how many sessions, why it failed).

### Why these boards can be trusted: four rules

1. **Compute first, land it, then only read rows.** The read side never re-aggregates, which is why a six-month, 123-session, 672-row cohort takes about 8 seconds to build and 0.29 seconds when the in-process cache answers it (measured today).
2. **Every rule table carries `spec_version` in its primary key.** Changing a caliber bumps the version and keeps the old rows for comparison, instead of overwriting history beyond recovery.
3. **Missing data is `NULL`, never 0.** Zeros do not enter ranking (otherwise "no data" is ranked as "terrible"); the UI shows `—`; a detector that breaks is not allowed to return an empty value, because downstream it would be written up as a perfectly reasonable false reading.
4. **Each caliber sentence exists in exactly one place,** read by both the column hover and the collapsed explanation box; identifiers are never shown on screen — every internal code has a Chinese label.

### Local data reality (queried 2026-10-22)

- PostgreSQL 17, database `financetool`: **7 business schemas, 221 base tables** — `market_data` 85, `analytics` 54, `config` 24, `ys` 19, `operations` 15, `reference_data` 15, `qd` 9.
- `market_data.stock_daily`: **2021-08-02 through 2026-10-21, 1,263 sessions, 6,452,485 rows**.
- 51 DDL files under `app/db/sql/`, checksums registered in `operations.schema_migration` (**there is no migration runner**; the exact `psql` invocation is in the data-processing guide 04).
- HTTP routes: 55 in `server.py`, plus 138 `/api/*` routes merged from `vr/app.py` at startup (all except `/api/chat`, which this repository implements itself). Every response is wrapped as `{"data": …}`, and when something is unavailable the payload says so (`available:false` plus the reason).

## How it runs itself every day

Nobody types. There are **12 registered loops**; 11 start with the service (the intraday polling loop starts on demand, when the first client subscribes to live frames). Each has a switch, a time and a ledger, visible and editable on the Job scheduler page and via `scripts/jobs.py status`.

| Loop | What it does | Notes |
|---|---|---|
| Close chain | Starts at **15:59**, two stages: collection (pull and land) → computation (the 42 steps) | Runs only on real trading days, judged by quote timestamps; up to 5 in-stage retries |
| Failed-step retry ladder | Defaults 21:00 / 22:00 / 23:00 / 01:00 / 02:30 / 04:20 | **Re-runs only the steps that failed**, times can be added or removed, then it gives up and leaves a record |
| Database backup | Full dump + `pg_basebackup` on the 1st of each month, full dumps on weekends, core schemas otherwise | The database is the one non-reproducible asset on this machine; backups include restore verification |
| Intraday snapshots | Minute bars and board slots | Past that moment the snapshot never exists again — it cannot be recovered |
| Newswire | Polls every 30 seconds | Stopping only stops growth; landed history is untouched |
| Stock capital flow (Sina) | Once per day | A missed day is gone forever |
| Watchlist sync | ini → database mirror | Full overwrite from the ini by default |
| Tonghuashun cloud sync | Launch client → wait for the ini to land → close the window it opened | **Off by default**: it moves desktop windows, so the first run must be rehearsed with a person at the keyboard |
| TDX client watch | Probe whether the client runs, reap stuck processes, close the download window once the ladder judges the data complete | Probing only; window actions have their own gate |
| Coverage pre-compute | Pre-computes the heavy first-screen aggregates into a cache | Without it the first screen waits tens of seconds |
| Notification push | Scans the schedule every 60 seconds and sends Feishu / DingTalk / WeCom / email at the configured time | Content reuses the on-screen reading blocks; an HTTP 200 from a bot is not delivery — the response body decides |
| Intraday polling | 3-second live frames and anomalies | On demand; it should not exist at night when nobody is watching |

Two more safeguards: the Windows scheduled task `YuanShan-KeepAlive8910` brings the service back if it dies (the watchdog only starts, never kills), and `scripts/manage.py doctor` runs **17 checks (9 hard, 8 advisory)**, where "loop completeness" reads that same registry — so the page, the health check and the ledger use one list. **A loop that is written but not wired gets named by the health check** instead of quietly pretending to run.

Anything that touches the desktop, leaves the network, or spends quota has a confirmation dialog or ships disabled. A rule that fails to run must make noise; it may not be smoothed over into a plausible reading.

## Review and backtesting

**Review** assembles five sections from the existing limit-up sample (sentiment, capital, themes, dragon-tiger list, leader tracking), then summarises the basis and the conditions still to be verified. You pick a target trading day, including days with no report yet; the page distinguishes the day you selected, the day the current report covers, and when it was generated, and never passes an existing report off as the new choice. Each fetch lane shows its category, waits at most 90 seconds and is bounded by the remaining time of the run; timeouts and cancellations clean up their processes. A failed run keeps the previous draft, re-generating the same day stores a new version, and re-running an older date does not roll the latest report back. New reports expand "view basis"; number display and supported comparisons are handled by the program, and AI interpretation is bound by citation checking.

Historical sessions remain readable at any time: they are read from the finalized records and snapshots saved at the time. A missed intraday moment cannot be reconstructed from current quotes. Old drafts stay as they were and do not gain new verification verdicts from an upgrade.

**Backtesting** checks explicitly stated historical rules: input conditions → AI arranges an executable scope → the user confirms → a deterministic engine computes → a local report. AI does not decide what you should trade now. Reports disclose market rules, data range, costs, fills and reasons for no fill; zero trades are shown as zero trades. Missing data and unsupported conditions are stated as limits, not disguised as measured results.

**Historical statistics are not returns:** what you observe from an already-selected limit-up sample carries selection bias, and sample statistics must not be read as tradable returns. Rates computed inside groups already filtered by the outcome are tautologies; only the unfiltered parent population can be said to predict anything. Past performance does not guarantee future results.

Positions and realised outcomes are built on the trading journal (the watchlist is a manual observation list, not a second ledger). Positions with no stated risk boundary are listed separately; a known partial never masquerades as full account risk. Missing quotes show as unavailable, summaries say they are incomplete, and a missing quote is not counted as a zero on the asset.

## Quick start

Requirements: Python 3.10+ (3.12 recommended), Node.js 22+ (with npm), PostgreSQL 17 (local port 5432, database `financetool`). Preparing the environment needs network access to download dependencies.

On Windows 10/11 double-click `启动 YuanShan.cmd`, or from the repository root in PowerShell / CMD:

```powershell
py -3 -X utf8 scripts/manage.py setup
.venv\Scripts\python.exe -X utf8 scripts/manage.py doctor
.venv\Scripts\python.exe -X utf8 scripts/manage.py start
```

On macOS / Linux double-click `启动 YuanShan.command`, or run `sh scripts/setup`, `sh scripts/doctor`, `sh scripts/start`. `manage.py` has exactly four subcommands: `setup` (install dependencies and build the UI), `doctor` (health check), `start` (run the service), `auto` (headless start for the scheduled task).

Default address `http://127.0.0.1:8910`; add `--port 8911` after `start` to change it. Stop with `scripts/stop.py` — **run `--dry-run` first to see who it would hit**; it stops launcher → guard tree → port holder in that order and never kills by image name.

**Network boundary:** the single source for the bind address and the origin allowlist is `netconfig.py`, with its settings stored at `<ASTOCK_DATA_HOME>/net/config.json` (`lan_enabled` + `allowlist`) — not in PostgreSQL. By default only loopback is bound; enabling LAN access **requires a restart** (the bind address is fixed at startup), and the allowlist is the only gate (this project has no API key). The boundary can only be changed from loopback — a whitelisted machine that could add itself would make the gate meaningless.

Database credentials come from `.env` (`FT_DSN_ADMIN` / `FT_DSN_APP` / `FT_DSN_ETL` / `FT_DSN_READER`; four roles with read/write separated — long reads use `reader`, which has a 60-second statement timeout), and write statements must explicitly use the `etl` role. **`.env` holds passwords: never commit it, never paste it into logs or screenshots.**

Before updating the source, stop the service and back up what the privacy section lists; afterwards re-run `setup` and `doctor`. After a front-end change the "installation matches source" check necessarily turns red — that is the fingerprint gate; re-run `setup` and it turns green again. It is not a functional failure.

## Connecting AI

All web AI entry points share the source and model you tested and saved in AI access. A source being listed does not mean the account can still call it; the connection test and task results decide.

| Method | Current implementation and verification boundary |
|---|---|
| Codex subscription | Product-specific ChatGPT authorisation with an isolated engine; real login, connection and business runs are on record; model availability is limited by account quota |
| Claude subscription | Local official CLI; real reviews and debates have been run through it; requires your own install, login and active subscription |
| WorkBuddy / CodeBuddy | Local CLI login; upstream tested only the macOS WorkBuddy-bundled CodeBuddy CLI 2.137.1 for connection, controlled tools, plain chat and one full single-day review |
| OpenAI API | Your own configuration; a subscription test is not used to substitute for API testing |
| new-api gateway / OpenAI-compatible | For endpoints offering only Chat Completions: the local backend rewrites the engine's Responses calls into chat calls on loopback and converts the streamed results and tool calls back into Responses. The address may be loopback or a LAN plain-http endpoint (the page warns that it is not encrypted); public addresses still require HTTPS. Provider keys go only to the local backend, never into the engine subprocess |
| DeepSeek / MiMo / Zhipu / Kimi / Qwen / SiliconFlow / MiniMax / OpenRouter / Groq / Together / custom | Editable address and model presets are provided; **a preset is not proof of compatibility**, and such endpoints must support the Responses API and tool calls |

Agent mode is off by default: plain chat enables no tools and no network. Only when enabled do the applicable entry points use their allowed evidence or public-data tools. Generating reviews, debates and backtests still requires an explicit request; the switch never triggers them. AI configuration has no popup — every entry goes straight to this page.

**"Fall through to the next source" is on by default:** a source must fail twice in a row before it is marked unreachable and hands over; list order is priority order. Switching only happens before the next task starts — a conversation in progress keeps the source it chose. Context overflow, local storage disabled, or a missing backend key do not count as source failures.

The right-hand "AI analysis" rail is an on-demand research channel: any screen can hand its reading blocks to the model for interpretation, with the material boundary and red lines implemented in one place. Prompts are "type + optional template", editable in System config, but the clause that forbids price targets and forward-looking judgements on individual stocks is a hard constraint that cannot be deleted. Session archives live in the local PostgreSQL `ys` schema (19 tables; migrated off the machine's SQLite on 2026-09-25, with the old file kept as a read-only backup and a `.migrated.json` recording how many rows moved). Classification is deterministic keyword rules, not a model, so the same question always yields the same category; sessions predating the rules show "uncategorised" and are never back-filled with guesses. Cross-session memory/recall is not implemented, and the standing decision is that archives are for the user to search, never injected into a prompt.

**Key risk in this local build (read before shipping):** `LOCAL_GATEWAY` in `frontend/src/lib/ai-access.ts` prefills a LAN gateway's address, model and key, so AI access opens with that source preselected. **The key is embedded into the static JS in `frontend/dist` by `npm run build`** — anyone who can read that file, including anything reaching port 8910 over the LAN, can take it. Before sharing the source, committing it, or exposing the service beyond loopback, clear `LOCAL_GATEWAY.key` and rebuild.

Every web AI source carries research constraints; plain chat and report paths check for explicit trading advice, and evidence answers have separate citation and number checking. These checks reduce overreach and misquotation; **they are not a guarantee of semantic correctness**. The product issues no investment decisions, buy/sell advice or position instructions.

## Data and privacy

| Data | Default location and notes |
|---|---|
| **PostgreSQL business database** (quotes, rules, boards, quality gate, job ledgers) | Local `financetool`; backup policy above. **This is the only non-reproducible asset on the machine** |
| Agent sessions, tasks, evidence ledger | `~/.vibe-astock-agent/`, overridable with `ASTOCK_AGENT_HOME` |
| Reviews, versions, trading journal, raw archives | `~/.duanxian-agents/`, absolute path via `ASTOCK_DATA_HOME` |
| Market material, intraday cache, job config and state, network settings | `~/.vibe-astock-agent/market-data/` (`VR_DATA_DIR` overrides); job config/state live under the data root in `jobs/`, **never in `.local/`** |
| TDX local files (source of daily and minute bars) | `FT_TDX_DIR` in `.env` points at the client directory |
| Watchlist source of truth | Tonghuashun `stockblock.ini` (a local file; what the UI shows is its mirror) |
| Research notes, UI state | The browser's localStorage for this site — **not covered by database backups**; changing browser, port or clearing site data loses them, so export what matters |
| Development logs and diagnostics | Repository `.local/`, excluded from version control and from backups |

These locations are not migrated by one switch: changing `ASTOCK_DATA_HOME` does not move browser data. Check logs for personal information before sharing; never commit private records, API keys or login files. The local web page still needs network access for data; when a remote AI is used, the question, the selected material and necessary context are sent to the provider you chose — "stored locally" does not mean "everything computed offline".

## Documentation map

| Where | What |
|---|---|
| `docs/数据获取工作指南/` (15 files) | How each channel is collected, idempotency and re-verification, whether a missing day can be recovered |
| `docs/数据加工工作指南/` (12) | Step order of the 42 steps, schema and naming, the master table of rules/indicators/parameters (answers "how was this number produced"), pitfall list |
| `docs/页面展示工作指南/` (7) | How calibers are stated in the UI, explanation-box and hover rules, ranking and colour conventions |
| `docs/系统配置工作指南/` (12) | Environment variables and job configuration, loops and switches, the capability checklist |
| `docs/功能点说明/` (38, by screen) | One document per screen: what the rule set answers, the five chain steps, parameter table, worked example, how to reproduce acceptance |
| `docs/MCP服务登记表.md` | The registered read-only endpoints (72) and 32 agent tools |
| `docs/上游改动与归属清单.md` | Licence-obligation ledger (Chinese): provenance of each layer, the four obligations, what is stamped and what is still unswept |

`CHANGELOG.md` is the **upstream v1.1.0 release notes** (2026-09-10). Changes made locally since the 2026-09-26 assembly are not in it: per screen they are in `docs/功能点说明/`, per computation step in the data-processing guide, per job in the collection guide.

## Validation scope

All figures below are **measured on this machine on 2026-10-22**, not promises:

```bash
.venv/Scripts/python.exe -X utf8 -m pytest tests backtest/tests research_data/tests -q --tb=no
node --test frontend/test/*.test.ts          # from the repo root; from frontend/ it is test/*.test.ts
npm --prefix frontend run build              # tsc -b && vite build — type errors stop here
.venv/Scripts/python.exe -X utf8 scripts/manage.py doctor
```

- Backend suite: **3,294 passed / 8 skipped / 16 failed** (about 20 minutes). Those 16 are this round's baseline list, not new breakage: 8 in `test_subscription_bridge`, 3 in `test_core_logic`, and one each in `test_agent_access`, `test_full_audit_fixes`, `test_sysboard`, `test_intraday_spike`, `test_startup_quadrant_track` (mostly the timing / subprocess / source-reading families, which are intermittent). **Regression is judged only by diffing the failed name set against the baseline, never by the red count.**
- Frontend: **737 tests, all green** — including navigation, routes, caliber display rules, sort keys, explanation boxes not nested in folds, and "no identifiers on screen" style document checks.
- Health check: **17 items, 9 hard (participating in the exit code) and 8 advisory** (backup freshness, today's data landed, collection jobs, close-chain switches, desktop actions awaiting a human, loop completeness, and others).
- End to end: endpoints are hit with `curl` and the body is checked to be parseable by `JSON.parse` (a `NaN` there means the output cleaner missed a field); pages are exercised in a real browser — every column header clicked to verify ordering, bare `**` counted in the rendered plain text, double-click navigation confirmed.

These results do not mean every provider, every Windows machine, the full Linux flow, or a consecutive complete trading day has been accepted; and they are no promise of returns. Known local limits include: minute bars can fail during unattended hours, per-stock capital flow gaps cannot be back-filled, and the sector membership table has no point-in-time history — using today's constituents to look back is a look-ahead function and therefore not eligible as a rule.

## Disclaimer

This tool organises data for research and historical re-checking. It provides no investment decision or trading instruction. Public data, program computation, manual notes and AI interpretation can all be wrong; a citation existing does not make the inference correct, and past results do not indicate future performance. Investing carries risk — verify the material independently and judge for yourself.

## License

Three licences coexist and none of them overrides the others:

| File | What it covers |
|---|---|
| [`LICENSE.local.md`](LICENSE.local.md) | **What this repository writes itself** (`app/`, `ingest/`, `mcp_service/`, `docs/`, plus files added locally inside mixed directories): educational and non-commercial use only, no commercial use, no team of more than six |
| [`LICENSE`](LICENSE) | The upstream product base, vibe-astock v1.1.0, Apache-2.0 |
| [`backtest/NOTICE.md`](backtest/NOTICE.md), [`research_data/NOTICE.md`](research_data/NOTICE.md), [`docs/upstream/Research-LICENSE.txt`](docs/upstream/Research-LICENSE.txt) | Ported third-party components (HKUDS/Vibe-Trading, Vibe-Research), each MIT |
| [`docs/上游改动与归属清单.md`](docs/上游改动与归属清单.md) | The compliance ledger (Chinese): which code is not written here, which of the four obligations are met, the files now carrying a "modified locally" notice and their line offsets, and what to do when authorship cannot be established |

The one-paragraph statement of the use limits is in [Use limits](#use-limits) above. Sources and licences for event-probability and backtest code are in those two NOTICE files.

**Upstream author:** Simon Lin · X [@linsizhen](https://x.com/linsizhen) · [simonlin0423@gmail.com](mailto:simonlin0423@gmail.com) (this repository localises that v1.1.0 base; local changes are indexed in the documentation map above)
