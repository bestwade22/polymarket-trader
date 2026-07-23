# Polymarket Weather Trading Bot

Periodic Python bot for Polymarket "highest temperature" daily weather markets.

## Features

- **Daily fetch** (`fetch-daily`): discovers today's highest-temp events via Gamma API, enriches with city timezone (API Ninjas) and local noon UTC window.
- **Hourly trade** (`trade-hourly`): trades events inside the local trading window (default **14:00–16:00**). After position checks, refreshes each event's markets from the Gamma API and CLOB buy prices before selection and order placement.
- **Stop-loss check** (`check-stop-loss`): every 15 minutes, scans live wallet positions via the Polymarket Data API; for events whose slug/title contains `highest-temperature-in-`, only evaluates positions when city local time is at or after **4:30 PM** on the event date; sells only when **`STOP_LOSS_PCT_FLOOR`% < value_pct < `STOP_LOSS_PCT`%** (where \(value\_pct = (current\_mid / avgPrice) \times 100\)); skips when an open sell order already exists.
- **Sell-win check** (`check-sell-win`): every hour, scans live wallet positions and places tiered limit **sell orders** during each city's **15:00–18:00** local window. Tier floors default to **91¢ / 93¢ / 95¢** (or current price if higher); orders expire 5 minutes before the next tier hour; skips when an open sell order already exists.
- **Two strategies** (select via `STRATEGY` env or `--strategy`):
  - `highest_yes` — buy only when **CLOB midpoint** and **Gamma Yes %** agree on the same top market, and that market's selection price is below `YES_PRICE_MAX` (default 0.60); skip if they disagree.
  - `forecast_match` — fetch forecast max temp (Wunderground resolution source or Open-Meteo fallback), buy matching bucket.
- **Trade logging**: step-by-step JSON logs in `logs/trades/` and `logs/app.log`.
- **Dry-run default**: no real orders until `DRY_RUN=false` or `--live`.
- **Strategy simulator** (`simulate-trades`): replay `highest_yes` (or other strategies) on historical weather events using CLOB Yes-% history; optional sell-win tiers; dashboard at `web/simulator.html`.

## Setup

Requires **Python 3.9.10+** (3.12+ recommended). Live trading needs `py-clob-client-v2` (included in `requirements.txt`).

```bash
cd polymarket-trader
# Use Python 3.12+ if your system python is older than 3.9.10 (e.g. macOS 3.9.6)
python3.12 -m venv .venv   # or: /opt/homebrew/bin/python3.12 -m venv ../.venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with API_NINJAS_KEY and wallet credentials for live trading
```

## Usage

```bash
# Fetch today's events (run once daily)
python -m src.main fetch-daily

# Fetch events for a specific date
python -m src.main fetch-daily --date 2026-06-14

# Hourly trade (dry-run by default, uses today's events file)
python -m src.main trade-hourly
python -m src.main trade-hourly --date 2026-06-14
python -m src.main trade-hourly --strategy forecast_match
python -m src.main trade-hourly --strategy highest_yes --live
python -m src.main trade-hourly --date 2026-06-19 --live

# Manual run outside the noon window (trades every city for the date)
python -m src.main trade-hourly --date 2026-06-19 --all-cities --live

# Stop-loss check (dry-run by default)
python -m src.main check-stop-loss
python -m src.main check-stop-loss --live

# Sell-win check (live by default; use --dry-run to simulate)
python -m src.main check-sell-win --dry-run
python -m src.main check-sell-win --live

# Sync wallet trade history from Data API activity (no local bot audit files)
python -m src.main sync-trade-history --init-days 7
python -m src.main sync-trade-history

# Simulate strategies on historical weather events (writes sim_trade_history.json)
python -m src.main simulate-trades
python -m src.main simulate-trades --from 2026-07-13 --to 2026-07-19 --strategy highest_yes

# Run built-in scheduler (daily fetch + hourly trade)
python -m src.main run-scheduler
```

### `trade-hourly` commands explained

| Command | Date | Strategy | Real orders? |
|---------|------|----------|--------------|
| `trade-hourly` | Today (or `EVENT_DATE` env) | `STRATEGY` env (default `highest_yes`) | No — dry-run |
| `trade-hourly --date 2026-06-14` | June 14 events file | `STRATEGY` env | No — dry-run |
| `trade-hourly --strategy forecast_match` | Today | `forecast_match` | No — dry-run |
| `trade-hourly --strategy highest_yes --live` | Today | `highest_yes` | Yes |
| `trade-hourly --date 2026-06-19 --live` | June 19 | `STRATEGY` env | Yes |
| `trade-hourly --date 2026-06-19 --all-cities --live` | June 19 | `STRATEGY` env | Yes (all cities, skip noon filter) |

`--strategy highest_yes` and omitting `--strategy` are **identical** when `STRATEGY=highest_yes` in `.env` (the default). Use `--strategy` only to override the env var.

`--live` overrides `DRY_RUN=true` in `.env` and places real Polymarket orders. Without `--live`, the bot may select markets and log `DRY RUN buy`, but no orders are sent.

### When trades run

By default, the bot trades cities inside **`TRADING_WINDOW_START_HOUR`–`TRADING_WINDOW_END_HOUR`** (default **14:00–16:00** local). Window bounds use each city's timezone and `event_date` in the events file.

- Run during a city's trading window → tradable.
- Run outside the window → `Found 0 tradable events` (expected).
- Past event dates → all windows have passed; use `--all-cities` for a manual run.
- `run-scheduler` and AWS Lambda call `trade-hourly` at **:05 and :35 UTC** each hour; the gate skips when no event is in its local window.

Cities are skipped when:
1. You have an **open buy order** on any market for that city (checked first — one API call), or
2. The selected market's live price is **≥ `YES_PRICE_MAX`** (no extra API calls), or
3. You already hold **`SHARE_COUNT`** Yes shares on any market in that city, or
4. You hold a **partial** position on a **different** market than the one selected (`partial_on_other_market`).

If you hold a **partial** position (`0 < shares < SHARE_COUNT`) on the **selected** market, the bot still trades and orders only the gap: `SHARE_COUNT - held_shares` (rounded up).

If your order is gone (filled, cancelled, or expired) and you have no position, the city can trade again. `data/positions/bought_events.json` is an audit log only.

### Prices: selection vs order

Both selection and orders use **live CLOB book** prices (after `refresh_prices`).

| Field | Role |
|-------|------|
| `selection_price` / `yes_price` | **Market selection price** after agreement — live book via `SELECTION_PRICE_SOURCE` (default `midpoint`). |
| `order_price` | **Order limit price** — `ORDER_PRICE_SOURCE` (default `midpoint`). |
| `gamma_yes_price` | Gamma `outcomePrices` (Polymarket UI %). Used with CLOB mid for `highest_yes` agreement. |
| `midpoint` | CLOB mid or (bid+ask)/2 — default for selection price and orders. |
| `buy_price` / `best_ask` | Lowest ask on the book. |
| `best_bid` | Highest bid. |

**Default (`highest_yes`):** require the same market to be highest by CLOB `midpoint` **and** Gamma Yes %; then place limit buy at refreshed `ORDER_PRICE_SOURCE`. Skip the city when the two leaders disagree. `YES_PRICE_MAX` and `SPREAD_MAX` are checked before the position check and again after the final price refresh.

**Flow:** city win-summary skip (bottom `CITY_SKIP_BOTTOM_N`) → refresh all markets (Gamma + CLOB) → open-order filter → select only if CLOB mid + Gamma agree on top market → drop if selection price ≥ `YES_PRICE_MAX` → drop if bid–ask spread ≥ `SPREAD_MAX` → position check (only survivors) → refresh selected market → re-check `YES_PRICE_MAX` and `SPREAD_MAX` → place order at `ORDER_PRICE_SOURCE`.

Cities are ranked by **win summary %** on current `trade_history.json` (opens and shares &lt; 1 excluded).

Example: Gamma shows 23°C highest (44%) but book midpoint peaks on 22°C (40¢ mid on a wide spread) — **skip**, do not buy.

Selection snapshots in `data/selections/` include `order_price`, `order_status`, and `order_id` after the run.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `API_NINJAS_KEY` | — | API Ninjas timezone lookup |
| `PRIVATE_KEY` | — | Wallet private key for CLOB |
| `DEPOSIT_WALLET_ADDRESS` | — | Polymarket proxy/funder address (from your profile) |
| `SIGNATURE_TYPE` | `1` | `0`=MetaMask EOA, `1`=email/Magic proxy, `2`=Gnosis Safe. Avoid `3` until SDK fix |
| `STRATEGY` | `highest_yes` | `highest_yes` or `forecast_match` |
| `SHARE_COUNT` | `10` | Shares per buy (min 5 on weather markets) |
| `YES_PRICE_MAX` | `0.60` | Max live selection price for highest_yes (checked after price refresh) |
| `SPREAD_MAX` | `0.15` | Max bid–ask spread; skip market if spread ≥ this value (all strategies) |
| `CITY_SKIP_ENABLED` | `true` | Skip cities with the worst historical win summary % |
| `CITY_SKIP_BOTTOM_N` | `7` | How many lowest win-summary cities to skip |
| `SELECTION_PRICE_SOURCE` | `midpoint` | Rank markets by live book: `midpoint`, `buy_price`, `best_bid`, `best_ask`, `yes_price` |
| `ORDER_PRICE_SOURCE` | `midpoint` | Order limit price: `midpoint`, `buy_price`, `yes_price`, `best_bid`, `best_ask` |
| `ORDER_EXPIRY_MINUTES` | `25` | Minutes until unfilled orders expire (`GTD`). Set `ORDER_EXPIRY_HOURS=0` for no expiry (`GTC`). |
| `TRADING_WINDOW_START_HOUR` | `14:00` | Local time when trading opens: `14`, `14:00`, or `1400` (city timezone) |
| `TRADING_WINDOW_END_HOUR` | `16:00` | Local time when trading closes: `16`, `16:00`, or `1600` (city timezone) |
| `DRY_RUN` | `true` | Skip real order placement |
| `DAILY_FETCH_HOUR_UTC` | `6` | Scheduler daily fetch hour |
| `EVENT_DATE` | _(empty)_ | Default date `YYYY-MM-DD` for fetch/trade (today if empty) |
| `STOP_LOSS_DRY_RUN` | `true` | Stop-loss-only dry-run flag (independent from `DRY_RUN`) |
| `STOP_LOSS_SCHEDULE_ENABLED` | `false` | Enable/disable the AWS stop-loss scheduler (schedule is disabled by default) |
| `STOP_LOSS_ORDER_EXPIRY_MINUTES` | `13` | Stop-loss sell order expiry (independent from `ORDER_EXPIRY_MINUTES`) |
| `STOP_LOSS_PCT_FLOOR` | `10` | Stop-loss lower bound: only sell when value_pct is above this and below `STOP_LOSS_PCT` |
| `STOP_LOSS_MIN_LOCAL_TIME` | `16:30` | Stop-loss only runs at or after this local time on the event date (city timezone) |
| `SELL_WIN_DRY_RUN` | `false` | Sell-win dry-run flag (independent from `DRY_RUN` and `STOP_LOSS_DRY_RUN`) |
| `SELL_WIN_SCHEDULE_ENABLED` | `true` | Enable/disable the AWS sell-win scheduler |
| `SELL_WIN_WINDOW_START` | `15:00` | Sell-win local window start (city timezone) |
| `SELL_WIN_WINDOW_END` | `18:00` | Sell-win local window end (exclusive) |
| `SELL_WIN_TIER1_PRICE` / `TIER2_PRICE` / `TIER3_PRICE` | `0.91` / `0.93` / `0.95` | Tier floor prices for limit sell orders |

## Data layout

```
data/events_*.json           # daily event cache per date
data/selections/              # markets_yes_DATE_TIME.json snapshots
data/positions/bought_events.json
data/analysis/trade_history.json   # wallet trade ledger (from Data API activity)
data/analysis/sync_state.json
data/analysis/resolutions_cache.json  # cached winning temp per event (slim)
logs/app.log
logs/trades/                  # per-event step logs
web/                          # trade history dashboard (GitHub Pages)
```

## Trade history analysis

Builds a unified ledger of **all wallet highest-temperature market trades** from Polymarket Data API `/activity` (TRADE + REDEEM) and `/closed-positions`. Does **not** use `bought_events.json`, `sold_events.json`, or `selections/` snapshots.

```bash
# Initial backfill (last 7 days)
python -m src.main sync-trade-history --init-days 7

# Incremental sync (since last run)
python -m src.main sync-trade-history

# Skip slow CLOB price-history lookups
python -m src.main sync-trade-history --skip-price-drop
```

Output: `data/analysis/trade_history.json` with per-trade rows (date, city, temp, result, P&L, winning temp comparison, sell regret flags) plus summary and strategy insights.

**Dashboard:** https://bestwade22.github.io/polymarket-trader/web/

**Simulator:** https://bestwade22.github.io/polymarket-trader/web/simulator.html

Local preview: `python3 -m http.server 8080` from repo root → http://localhost:8080/web/ (simulator: http://localhost:8080/web/simulator.html)

**GitHub Pages setup (one-time, if the URL 404s):** Settings → Pages → Build and deployment → **Source: Deploy from a branch** → branch **gh-pages** → folder **/ (root)**. The workflow pushes site files to `gh-pages` on each run (no PAT required).

| Column | Description |
|--------|-------------|
| `result` | `win`, `loss`, `sold`, or `open` |
| `win_temp_vs_bought` | Whether the winning bucket was higher/lower/same vs bought temp |
| `sold_but_would_have_won` | Sold early but bought temp matched final winner |
| `sell_value_pct` | Sell price as % of buy price (stop-loss timing analysis) |
| `final_value_usd` | Realized P&L (from closed-positions when available) |

Scheduled on AWS: `sync-trade-history` Lambda every **3 hours UTC** commits `data/analysis/*` to git.

## Strategy simulator

Offline replay of trade strategies on past highest-temperature events (no Lambda, no live orders).

```bash
# Default: last 7 days ending yesterday, STRATEGY=highest_yes
python -m src.main simulate-trades

python -m src.main simulate-trades --from 2026-07-13 --to 2026-07-19
python -m src.main simulate-trades --strategy highest_yes --yes-price-max 0.55 --spread-max 0.15
python -m src.main simulate-trades --force  # redo even if dates already simulated
```

**Flow:** for each date → load `events_YYYY-MM-DD.json` (fetch if missing) → at city-local **:05 / :35** inside the trading window → price each bucket from CLOB `/prices-history` (Polymarket chart %) → run strategy + `YES_PRICE_MAX` → apply `SPREAD_MAX` only when nearest `markets_yes_*` has spread → first pass buys `SHARE_COUNT` (100% fill at that %) → simulate sell-win tiers on the same history → else resolve win/loss via Gamma cache.

**Idempotency:** `sim_trade_history.json` stores `process_version`, `completed_dates`, and `simulated_events`. Re-running the same strategy/process skips already-completed dates; only new dates (or `--force` / bumped process version / changed strategy params) are simulated.

**Output:** `data/analysis/sim_trade_history.json`

**Dashboard:** https://bestwade22.github.io/polymarket-trader/web/simulator.html (also linked from History). Local: http://localhost:8080/web/simulator.html

GitHub Pages deploys `web/` plus `sim_trade_history.json` / `trade_history.json` / `resolutions_cache.json` on each push to `main` that touches those paths.

**Assumptions / limits:**
- Fills are 100% at historical Yes % (optimistic vs live book).
- Bid/ask/`SPREAD_MAX` only when a local `markets_yes_*` snapshot exists near the sample time; otherwise spread is ignored.
- When Gamma snapshot is missing, both CLOB mid and Gamma are set from the same history series (`gamma_proxy` badge on the simulator page).
- Price history is fetched for selection in memory; **disk cache only for bought tokens** under `data/simulation/price_cache/` (gitignored).
- One simulated buy max per event; wallet open-order / position checks are skipped.
- Parameter sweeps are out of scope for v1 (single-run CLI only).


## Cron

See [`scripts/cron.example`](scripts/cron.example).

## AWS Lambda (scheduled jobs)

Fetch and trade run on **AWS Lambda in ap-east-1** (Hong Kong), avoiding Polymarket geo-blocks (Singapore is close-only). A thin GitHub Actions workflow deploys code on push to `main`.

| Job | Schedule | What it does |
|-----|----------|--------------|
| `fetch-daily` | **00:01 HKT** daily | Fetches that day's events and commits `data/events_YYYY-MM-DD.json` |
| `trade-hourly` | **:05 and :35 UTC** each hour | Fetches events JSON from GitHub, skips when no event is in its local trading window; otherwise runs trade and commits `data/selections/*.json` |
| `stop-loss-check` | **Disabled (manual only)** | Stop-loss code and Lambda remain available, but the scheduler is disabled by default |
| `sell-win-check` | **Hourly (UTC)** | Sell-win scheduler; disable with `SELL_WIN_SCHEDULE_ENABLED=false` |
| `sync-trade-history` | **Every 3 hours UTC** | Syncs wallet activity to `data/analysis/trade_history.json` |

```mermaid
flowchart LR
  subgraph github [GitHub]
    Repo[polymarket-trader]
    DeployGHA[deploy-lambda.yml]
  end
  subgraph aws [AWS ap-east-1]
    EB1[Scheduler fetch 00:01 HKT]
    EB2[Scheduler hourly UTC]
    EB3[Scheduler stop-loss 15m]
    EB4[Scheduler sell-win hourly]
    LF[fetch-daily Lambda]
    LT[trade-hourly Lambda]
    LS[stop-loss-check Lambda]
    LSW[sell-win-check Lambda]
    SM[Secrets Manager]
  end
  DeployGHA -->|sam deploy| LF
  DeployGHA -->|sam deploy| LT
  DeployGHA -->|sam deploy| LS
  EB1 --> LF
  EB2 --> LT
  EB3 --> LS
  LF --> Repo
  LT --> Repo
  LS --> Repo
  SM --> LF
  SM --> LT
  SM --> LS
```

### One-time AWS setup

Full console walkthrough (steps 1–6) and region migration: **[`docs/aws-console-setup.md`](docs/aws-console-setup.md)**

| Step | What |
|------|------|
| 1 | IAM OIDC provider for GitHub |
| 2 | Deploy role trust policy |
| 3 | Deploy role permissions policy |
| 4 | GitHub variables + first deploy (GHA) |
| 5 | Secrets Manager credentials |
| 6 | Verify CloudFormation, Lambda, Scheduler |

Policy files: [`infrastructure/iam/github-deploy-trust.json`](infrastructure/iam/github-deploy-trust.json), [`infrastructure/iam/github-deploy-policy.json`](infrastructure/iam/github-deploy-policy.json).

### Manual invoke

```bash
# Fetch today's events (HKT date)
aws lambda invoke --function-name polymarket-trader-fetch-daily \
  --region ap-east-1 \
  --payload '{"date":"2026-06-27"}' out.json && cat out.json

# Trade (respects trading window gate)
aws lambda invoke --function-name polymarket-trader-trade-hourly \
  --region ap-east-1 \
  --payload '{"date":"2026-06-27"}' out.json && cat out.json

# Force trade outside window (dry-run unless DRY_RUN=false in Secrets Manager / .env)
aws lambda invoke --function-name polymarket-trader-trade-hourly \
  --region ap-east-1 \
  --payload '{"force":true,"date":"2026-06-27"}' out.json && cat out.json

# Stop-loss check (skips clone when wallet has no positions)
aws lambda invoke --function-name polymarket-trader-stop-loss-check \
  --region ap-east-1 \
  --payload '{}' out.json && cat out.json

# Sell-win check (skips clone when wallet has no positions)
aws lambda invoke --function-name polymarket-trader-sell-win-check \
  --region ap-east-1 \
  --payload '{}' out.json && cat out.json

# Optional: verify Polymarket geoblock from your machine (HK/ap-east-1 is not restricted)
python scripts/check_geoblock.py
```

### Data storage

- **Events and selections** are committed to git (`data/events_*.json`, `data/selections/*.json`) via `git add -f` from Lambda.
- **Stop-loss and sell-win runs** commit `data/positions/sold_events.json` when a sell order is placed.
- **Verbose step logs** go to CloudWatch Logs (`/aws/lambda/polymarket-trader-trade-hourly`, `...-stop-loss-check`, `...-sell-win-check`).
- **`bought_events.json`** is force-committed when live trading updates it.

### Enabling live trading

Set `DRY_RUN=false` to enable live **buy** orders (default expiry `ORDER_EXPIRY_MINUTES=25`), set `STOP_LOSS_DRY_RUN=false` to enable live **stop-loss sell** orders, and set `SELL_WIN_DRY_RUN=false` (default) for live **sell-win** limit orders. They are independent flags.

### Deploy troubleshooting

**`ROLLBACK_FAILED` on `sam deploy` (Creating the required resources…)**

SAM creates a bootstrap stack `aws-sam-cli-managed-default` for the S3 artifact bucket. If the deploy role lacks `s3:TagResource` or `s3:DeleteBucket`, bucket creation fails and the stack gets stuck in `ROLLBACK_FAILED`.

**Fix (in order):**

1. **Update the deploy role policy** — attach [`infrastructure/iam/github-deploy-policy.json`](infrastructure/iam/github-deploy-policy.json) to `github-polymarket-trader-deploy` (or merge the missing actions into your existing policy). Key actions that are often missing:
   - `s3:TagResource`
   - `s3:DeleteBucket`
   - `cloudformation:ListStacks`

2. **Clean up the failed bootstrap stack** (AWS Console → **ap-east-1**):
   - **S3** → find bucket `aws-sam-cli-managed-default-samclisourcebucket-*` → empty and delete it
   - **CloudFormation** → delete stack `aws-sam-cli-managed-default` (if still present)
   - If delete is blocked, use an admin/root account — the deploy role may not have had `s3:DeleteBucket` when the stack was created

3. Re-run **Deploy Lambda** workflow

If deploy fails again, check the workflow step **Diagnose CloudFormation failure** for recent stack events.

**Orphan secret:** If you manually created `polymarket-trader/credentials` before deploy, delete it or the stack may fail on duplicate names (the template now uses an auto-generated secret name).

### Code updates

Push to `main` — [`.github/workflows/deploy-lambda.yml`](.github/workflows/deploy-lambda.yml) runs `sam build` and `sam deploy` automatically when `src/`, `lambda_handlers/`, `infrastructure/`, etc. change.


## Notes

- Polymarket may block trading from geo-restricted regions.
- Weather markets typically require `orderMinSize` of 5 shares.
- `outcomePrices` are probabilities 0.0–1.0 (0.60 = 60%).
