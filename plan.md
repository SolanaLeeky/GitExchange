# GitExchange — Implementation Plan

> A virtual stock exchange where GitHub repos are stocks, issues are trades,
> Actions are compute, commits are state, and README is the UI.

---

## Phase 1: Foundation — Data Layer + Utilities

**Goal**: Establish the JSON-backed data layer, configuration, and shared utilities
that every engine depends on.

### 1.1 Project scaffolding

Create the directory structure:

```
GitExchange/
├── .github/workflows/
├── .github/ISSUE_TEMPLATE/
├── engine/
├── data/
│   ├── traders/
│   └── history/
│       ├── prices/
│       ├── trades/
│       └── events/
├── charts/
├── docs/
└── requirements.txt
```

**Deliverables**: empty directories, `requirements.txt` with dependencies
(`PyGithub`, `requests`, `matplotlib`).

### 1.2 Configuration file — `data/config.json`

Seed the market with initial settings:

| Setting                | Value   |
|------------------------|---------|
| `starting_cash`        | 10000   |
| `max_position_pct`     | 0.40    |
| `short_margin_pct`     | 1.50    |
| `min_trade_qty`        | 1       |
| `max_trade_qty`        | 100     |
| `trading_fee_pct`      | 0.001   |
| `dividend_threshold`   | 50      |
| `ipo_threshold_stars`  | 1000    |
| `volatility_range`     | 0.03    |
| `momentum_range`       | 0.08    |
| `price_weights`        | stars 0.30, commits 0.25, forks 0.15, issue_response 0.15, contributors 0.15 |
| `listed_repos`         | 5 starter repos (react, vscode, deno, next.js, svelte) |

### 1.3 Shared utilities — `engine/utils.py`

Functions needed by all engines:

- `load_json(path)` / `save_json(path, data)` — atomic JSON I/O
- `get_github_client()` — authenticated PyGithub client from `GITHUB_TOKEN` env
- `get_repo_metrics(repo_full_name)` — fetch stars, forks, commits/week,
  avg issue response time, contributor count via GitHub API
- `load_market()` / `save_market(data)` — market.json wrapper
- `load_trader(username)` / `save_trader(username, data)` — trader file wrapper
  with auto-creation for new traders (starting cash = config value)
- `load_config()` — config.json reader
- `today_str()` — UTC date string for history file paths
- `append_trade_history(trade_record)` — append to daily trades log
- `append_event_history(event_record)` — append to daily events log
- `post_issue_comment(issue_number, body)` — comment on an issue via API
- `close_issue(issue_number)` — close an issue via API

### 1.4 Seed market data — `data/market.json`

Run a one-time bootstrap script (`engine/bootstrap.py`) that:

1. Reads `config.json` for listed repos
2. Fetches live GitHub metrics for each
3. Calculates initial prices using the price formula
4. Writes `data/market.json` with full stock entries
5. Writes the first `data/history/prices/{date}.json` snapshot

### Phase 1 acceptance criteria

- [ ] All directories exist
- [ ] `config.json` is valid and complete
- [ ] `utils.py` functions work (unit-testable with mock data)
- [ ] `market.json` has 5 stocks with real GitHub metrics
- [ ] `bootstrap.py` runs end-to-end and produces valid JSON

---

## Phase 2: Core Engines — Trade + Price

**Goal**: Implement the two engines that make the market functional:
executing trades and updating prices.

### 2.1 Price engine — `engine/price_engine.py`

Entry point: `python engine/price_engine.py`

**Steps executed on each run:**

1. Load `config.json` for listed repos and weights
2. For each repo, call `get_repo_metrics()` to fetch fresh data
3. Normalize each metric to 0-1000 scale across all listed stocks:
   `normalized = (metric / max_across_all) * 1000`
4. Calculate weighted base score:
   `base = sum(normalized[k] * weights[k] for k in weights)`
5. Apply momentum modifier (compare to previous price, clamp to +/-8%):
   ```
   if prev_price > 0:
       momentum = clamp((base - prev_price) / prev_price, -0.08, 0.08)
   ```
6. Apply random volatility: `random.uniform(-0.03, 0.03)`
7. Compute final price: `price = base * (1 + momentum * 0.5) * (1 + volatility)`
8. Update `market.json` with new prices, change percentages, metrics
9. Append snapshot to `data/history/prices/{date}.json`
10. Recalculate every trader's `total_value` and `pnl` fields

**Edge cases to handle:**
- New stock with no `prev_price` — momentum = 0
- GitHub API failure for one repo — keep previous price, log warning
- Issue response time = 0 (no issues) — use median of other repos

### 2.2 Trade engine — `engine/trade_engine.py`

Entry point: `python engine/trade_engine.py`
(reads `ISSUE_TITLE`, `ISSUE_USER`, `ISSUE_NUMBER` from env)

**Step-by-step flow:**

1. **Parse** issue title with regex: `^(BUY|SELL|SHORT)\s+(\w+)\s+(\d+)$`
   - On parse failure: comment "Invalid trade format", close issue, exit

2. **Validate** (any failure = comment reason + close issue):
   - Ticker exists in `market.json`
   - Market status is `"open"`
   - Quantity is within `[min_trade_qty, max_trade_qty]`
   - For BUY: `trader.cash >= price * qty * (1 + fee_pct)`
   - For BUY: position after trade <= `max_position_pct` of total portfolio
   - For SELL: trader holds >= `qty` of that stock
   - For SHORT: trader has enough cash for margin (`price * qty * short_margin_pct`)

3. **Load state**: `market.json` + `traders/{user}.json` (create if new trader)

4. **Execute**:
   - **BUY**: deduct `price * qty * (1 + fee)` from cash, add to portfolio
     (update avg_cost via weighted average)
   - **SELL**: add `price * qty * (1 - fee)` to cash, reduce portfolio qty
     (if qty hits 0, remove the position)
   - **SHORT**: lock margin, record entry price in `shorts` dict

5. **Write state**: save trader JSON, append to daily trade history

6. **Receipt**: comment on issue with trade confirmation table, close issue

**Trade receipt format (issue comment):**

```markdown
## Trade Executed

| Field    | Value              |
|----------|--------------------|
| Action   | BUY                |
| Stock    | react              |
| Quantity | 10                 |
| Price    | $847.32            |
| Total    | $8,473.20          |
| Fee      | $8.47              |
| Cash     | $1,518.33 remaining|

Portfolio updated. Good luck!
```

### 2.3 GitHub Actions — `trade.yml` + `price-update.yml`

**trade.yml** (trigger: `issues: opened`):
- Concurrency group: `trade-execution`, cancel-in-progress: false
- Filter: issue title starts with BUY/SELL/SHORT
- Steps: checkout, setup python, install deps, run trade engine, git commit+push

**price-update.yml** (trigger: `cron: 0 */6 * * *` + `workflow_dispatch`):
- Same concurrency group (critical — prevents trade/price race conditions)
- Steps: checkout, setup python, install deps, run price engine,
  run render engine, git commit+push

### Phase 2 acceptance criteria

- [ ] Price engine produces correct prices from live GitHub data
- [ ] Trade engine handles BUY, SELL, SHORT with all validation
- [ ] New traders auto-created with starting cash on first trade
- [ ] Trade receipts posted as issue comments
- [ ] Invalid trades rejected with clear error messages
- [ ] Both workflows use the same concurrency group
- [ ] No data loss on concurrent trade attempts (queued, not cancelled)

---

## Phase 3: Market Events Engine

**Goal**: Add the daily event cycle that makes the market dynamic —
dividends, IPOs, crashes, short squeezes, and achievements.

### 3.1 Event engine — `engine/event_engine.py`

Entry point: `python engine/event_engine.py`

Four event processors run in sequence:

#### 3.1a Dividends

```
for each stock in market.json:
    if stock.metrics.commits_week >= config.dividend_threshold:
        dividend_per_share = stock.price * 0.005
        for each trader file in data/traders/:
            if trader holds this stock:
                trader.cash += dividend_per_share * trader.portfolio[ticker].qty
                save trader
        log DIVIDEND event
```

#### 3.1b IPO scanner

```
trending_repos = fetch_github_trending()  # or search API: stars>1000 created:>30d
for each repo in trending_repos:
    if repo not in config.listed_repos:
        if repo.stars >= config.ipo_threshold_stars:
            calculate initial price from metrics
            add to market.json (shares_outstanding=500, ipo_date=today)
            add repo to config.listed_repos
            log IPO event
            create announcement issue: "IPO: {repo} now tradeable!"
```

#### 3.1c Crash detector

```
for each stock in market.json:
    try:
        repo = github.get_repo(stock.full_name)
        if repo.archived:
            raise CrashDetected
    except (404, CrashDetected):
        stock.price = 0
        stock.market_status = "DELISTED"
        for each trader holding this stock:
            force-sell at $0 (total loss)
            remove from portfolio
        log CRASH event
        create alert issue
```

#### 3.1d Short squeeze detector

```
for each stock in market.json:
    total_shorted = sum(t.shorts[ticker].qty for t in all_traders if ticker in t.shorts)
    short_interest = total_shorted / stock.shares_outstanding
    price_change_24h = (stock.price - stock.prev_price) / stock.prev_price

    if short_interest > 0.60 and price_change_24h > 0.10:
        for each trader with short on this stock:
            loss = (stock.price - trader.shorts[ticker].entry_price) * qty
            trader.cash -= loss
            release margin
            delete short position
        log SHORT_SQUEEZE event
        create alert issue
```

### 3.2 Achievement scanner

Runs at the end of the event engine. For each trader, check milestone conditions:

| Achievement     | Condition                                     |
|-----------------|-----------------------------------------------|
| first-trade     | `trade_count >= 1`                            |
| 100-trades      | `trade_count >= 100`                          |
| 10x-return      | `total_value >= starting_cash * 10`           |
| diamond-hands   | any position held 30+ days (check trade history) |
| paper-hands     | sold within 1 hour of buying (check trade history) |
| short-king      | cumulative short profit >= $5000              |
| diversified     | `len(portfolio) >= 10`                        |
| whale           | any single trade total >= $5000               |
| survivor        | held stock that had a CRASH event             |
| ipo-hunter      | bought a stock on its IPO day                 |

Add new achievements to trader's `achievements` list (no duplicates).

### 3.3 GitHub Actions — `daily-events.yml`

Trigger: `cron: 0 0 * * *` (midnight UTC daily)
Same concurrency group: `trade-execution`
Steps: checkout, setup python, run event engine, git commit+push

### Phase 3 acceptance criteria

- [ ] Dividends paid correctly to all holders of qualifying stocks
- [ ] IPO scanner detects new repos and lists them
- [ ] Crash detector handles archived/deleted repos gracefully
- [ ] Short squeeze force-closes positions and deducts losses
- [ ] Achievements granted correctly without duplicates
- [ ] All events logged to `data/history/events/{date}.json`
- [ ] Announcement issues created for IPOs, crashes, squeezes

---

## Phase 4: Render Engine + Dashboard

**Goal**: Make the market visible — generate the README dashboard, SVG charts,
and GitHub Pages site so traders can see prices and leaderboards.

### 4.1 Render engine — `engine/render_engine.py`

Entry point: `python engine/render_engine.py`

Reads `market.json`, trader files, and history. Produces:

#### 4.1a README generation

Read `README.template` with placeholders, replace:

- `<!-- MARKET_TABLE -->` — markdown table of all stocks:
  ```
  | Ticker | Price | 24h Change | Volume | Market Cap | Action |
  |--------|-------|------------|--------|------------|--------|
  | REACT  | $847  | +2.3%      | 156    | $423K      | [Buy](link) [Sell](link) |
  ```
  Each action links to a pre-filled issue template URL.

- `<!-- LEADERBOARD -->` — top 20 traders by portfolio value:
  ```
  | Rank | Trader | Portfolio Value | P&L | Achievements |
  |------|--------|-----------------|-----|--------------|
  | 1    | @alice | $24,830         | +148% | 10x, diamond |
  ```

- `<!-- PRICE_CHART -->` — embedded SVG chart image reference

- `<!-- RECENT_TRADES -->` — last 10 trades from today's history

Write result to `README.md`.

#### 4.1b SVG chart generation

Using matplotlib, generate:

- `charts/market_overview.svg` — sparklines for all stocks (last 7 days)
- `charts/leaderboard.svg` — horizontal bar chart of top 10 trader values
- `charts/ticker_{name}.svg` — per-stock price chart (30 days history)

Style: dark theme, minimal, readable at small sizes in a README.

#### 4.1c Issue template links

Generate pre-filled issue URLs for each stock:

```
https://github.com/{OWNER}/{REPO}/issues/new?title=BUY+{ticker}+10&body=...
```

Embed these as clickable buttons in the README market table.

### 4.2 README template — `README.template`

Static content around the dynamic placeholders:

```markdown
# GitExchange — The GitHub Stock Market

> Trade open-source repos like stocks. Prices based on real GitHub activity.

## How to Trade
1. Click a **Buy** or **Sell** link below
2. Adjust the quantity in the issue title
3. Submit the issue — your trade executes automatically

## Market Board
<!-- MARKET_TABLE -->

## Leaderboard
<!-- LEADERBOARD -->

## Price Chart (7 days)
<!-- PRICE_CHART -->

## Recent Trades
<!-- RECENT_TRADES -->

## Rules
- Starting cash: $10,000
- Max 40% of portfolio in one stock
- Trading fee: 0.1% per trade
- Prices update every 6 hours based on GitHub API metrics
- Dividends paid daily to holders of active repos (50+ commits/week)
```

### 4.3 GitHub Pages — `docs/`

`docs/index.html` — a richer dashboard:

- Interactive Chart.js price charts (read from history JSON via fetch)
- Full searchable trade history table
- Per-stock detail pages
- Trader profile pages
- Mobile-responsive layout

### 4.4 Issue templates — `.github/ISSUE_TEMPLATE/`

Three YAML templates:

- `buy.yml` — title default: `BUY {ticker} {quantity}`, fields for ticker + qty
- `sell.yml` — title default: `SELL {ticker} {quantity}`
- `short.yml` — title default: `SHORT {ticker} {quantity}`

### Phase 4 acceptance criteria

- [ ] README renders with live market data, leaderboard, charts
- [ ] SVG charts generate correctly from price history
- [ ] Issue template links work and pre-fill correctly
- [ ] GitHub Pages site loads and displays interactive charts
- [ ] Render engine runs after every price update and trade
- [ ] All charts use consistent dark theme styling

---

## Phase 5: Hardening + Polish

**Goal**: Add abuse protection, error handling, edge-case coverage,
and operational resilience for a public-facing system.

### 5.1 Abuse protection in trade engine

Add validation checks at the top of trade execution:

- **Account age**: reject issues from GitHub accounts created < 7 days ago
  (query `user.created_at` via API)
- **Rate limiting**: max 5 trades per user per hour
  (check recent trades in today's history file)
- **Duplicate detection**: reject if user has an identical pending trade
  (same ticker + action) within the last 5 minutes
- **Input sanitization**: strip whitespace, case-normalize ticker,
  reject non-alphanumeric ticker names

### 5.2 Git push retry logic

The concurrency group prevents races, but network failures can cause push
failures. Add retry logic to the commit+push step:

```yaml
- name: Commit and push with retry
  run: |
    git add data/ charts/ README.md docs/
    git diff --cached --quiet && exit 0
    git commit -m "Trade/update"
    for i in 1 2 3; do
      git push && break
      git pull --rebase
    done
```

### 5.3 GitHub API resilience

In `utils.py`, wrap all GitHub API calls with:

- Retry with exponential backoff (3 attempts)
- Graceful degradation: if a repo's metrics fail, use cached values from
  `market.json` instead of crashing the entire price update
- Rate limit awareness: check `X-RateLimit-Remaining` header, pause if < 100

### 5.4 Data integrity checks

Add a `validate_state()` function that runs before every engine:

- `market.json` is valid JSON with required fields
- Every trader's cash is non-negative
- Every portfolio quantity is a positive integer
- No trader has a position in a delisted stock
- `total_value` matches recalculated value from current prices
- No NaN or Inf in any numeric field

### 5.5 History rotation

Add a cleanup step to the daily events workflow:

- Archive history files older than 30 days
- Create a GitHub release with the archived JSON as an artifact
- Delete archived files from the repo to keep clone size small

### 5.6 Orderbook — `data/orderbook.json` (stretch goal)

Add limit orders:

- `LIMIT BUY react 10 @ 800` — buy 10 shares if price drops to $800
- Store pending orders in `orderbook.json`
- Price engine checks orderbook after each update, executes matching orders
- Orders expire after 7 days if not filled

### 5.7 Short position maintenance

- **Margin calls**: if a short position's loss exceeds the locked margin,
  force-close the position (similar to short squeeze but per-trader)
- **Short cover**: `COVER {ticker} {qty}` command to close a short voluntarily
  (add to trade engine parser)

### 5.8 Logging + observability

- Write a `data/engine_log.json` with timestamped entries for every engine run
- Include: engine name, duration, repos processed, trades executed, errors
- Surface last engine run status in the README footer

### Phase 5 acceptance criteria

- [ ] Bot accounts and spam trades are rejected
- [ ] Rate limiting prevents abuse (5 trades/user/hour)
- [ ] Git push failures recover gracefully with retry
- [ ] GitHub API failures don't crash engines (graceful degradation)
- [ ] Data integrity validated on every run
- [ ] History files rotated after 30 days
- [ ] Short positions have margin call protection
- [ ] Engine runs logged for debugging

---

## Build Order Summary

```
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
Foundation   Trade +     Events      Dashboard   Hardening
             Price
```

Each phase is independently deployable:
- After Phase 1: data layer exists, bootstrap populates initial market
- After Phase 2: users can trade via issues, prices update on cron
- After Phase 3: market has dynamic events (dividends, IPOs, crashes)
- After Phase 4: README dashboard shows live data, charts render
- After Phase 5: system is production-hardened for public use

**Estimated file count**: ~15 Python files, ~5 YAML workflows, ~10 JSON data files,
3 issue templates, 1 HTML page, 1 README template.
