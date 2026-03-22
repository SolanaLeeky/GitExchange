"""Shared utilities for the GitExchange stock market engine."""

import json
import math
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from github import Github, GithubException

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
TRADERS_DIR = DATA_DIR / "traders"
HISTORY_DIR = DATA_DIR / "history"
CHARTS_DIR = ROOT_DIR / "charts"

# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------


def load_json(path: str | Path) -> dict | list:
    """Load a JSON file. Returns empty dict if file doesn't exist."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str | Path, data: dict | list) -> None:
    """Atomically write JSON (write to tmp then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config() -> dict:
    return load_json(DATA_DIR / "config.json")


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------


def load_market() -> dict:
    return load_json(DATA_DIR / "market.json")


def save_market(data: dict) -> None:
    save_json(DATA_DIR / "market.json", data)


# ---------------------------------------------------------------------------
# Traders
# ---------------------------------------------------------------------------


def load_trader(username: str) -> dict:
    """Load a trader file, creating a new one with starting cash if absent."""
    path = TRADERS_DIR / f"{username}.json"
    data = load_json(path)
    if not data:
        config = load_config()
        data = {
            "username": username,
            "joined": now_iso(),
            "cash": config["starting_cash"],
            "starting_cash": config["starting_cash"],
            "portfolio": {},
            "shorts": {},
            "total_value": config["starting_cash"],
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "trade_count": 0,
            "rank": 0,
            "achievements": [],
        }
    return data


def save_trader(username: str, data: dict) -> None:
    save_json(TRADERS_DIR / f"{username}.json", data)


def list_traders() -> list[str]:
    """Return list of trader usernames (filenames without .json)."""
    if not TRADERS_DIR.exists():
        return []
    return [p.stem for p in TRADERS_DIR.glob("*.json")]


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_history(subdir: str, key: str, record: dict) -> None:
    """Append a record to today's history file under the given key list."""
    date = today_str()
    path = HISTORY_DIR / subdir / f"{date}.json"
    data = load_json(path)
    if not data:
        data = {"date": date, key: []}
    if key not in data:
        data[key] = []
    data[key].append(record)
    save_json(path, data)


def append_trade_history(trade_record: dict) -> None:
    _append_history("trades", "trades", trade_record)


def append_event_history(event_record: dict) -> None:
    _append_history("events", "events", event_record)


def append_price_snapshot(snapshot: dict) -> None:
    _append_history("prices", "snapshots", snapshot)


# ---------------------------------------------------------------------------
# GitHub API (with retry + rate-limit awareness)
# ---------------------------------------------------------------------------

_gh_client: Github | None = None


def get_github_client() -> Github:
    """Authenticated PyGithub client (singleton)."""
    global _gh_client
    if _gh_client is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("MARKET_TOKEN", "")
        _gh_client = Github(token, retry=3, timeout=30) if token else Github()
    return _gh_client


def check_rate_limit() -> tuple[int, int]:
    """Return (remaining, limit). Prints warning if low."""
    gh = get_github_client()
    rate = gh.get_rate_limit().core
    remaining = rate.remaining
    limit = rate.limit
    if remaining < 100:
        reset_time = rate.reset.strftime("%H:%M:%S UTC")
        print(f"  WARNING: GitHub API rate limit low: {remaining}/{limit} (resets {reset_time})")
    return remaining, limit


def api_call_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """Call a function with exponential backoff on failure."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except GithubException as e:
            if e.status == 403 and "rate limit" in str(e.data).lower():
                # Rate limited — check reset time
                gh = get_github_client()
                reset = gh.get_rate_limit().core.reset
                wait = max((reset - datetime.now(timezone.utc)).total_seconds(), 1)
                if wait > 300:
                    print(f"  Rate limit exceeded. Reset in {wait:.0f}s — skipping.")
                    raise
                print(f"  Rate limited. Waiting {wait:.0f}s...")
                time.sleep(wait + 1)
                continue
            if e.status >= 500 and attempt < max_retries - 1:
                delay = 2 ** attempt
                print(f"  GitHub API error {e.status}, retrying in {delay}s...")
                time.sleep(delay)
                continue
            raise
        except Exception:
            if attempt < max_retries - 1:
                delay = 2 ** attempt
                time.sleep(delay)
                continue
            raise


def get_repo_metrics(repo_full_name: str) -> dict:
    """Fetch key metrics for a repo with retry logic."""
    gh = get_github_client()
    repo = api_call_with_retry(gh.get_repo, repo_full_name)

    stars = repo.stargazers_count
    forks = repo.forks_count

    commits_week = 0
    try:
        stats = repo.get_stats_commit_activity()
        if stats:
            commits_week = stats[-1].total
    except GithubException:
        pass

    issue_response_hrs = _avg_issue_response(repo)

    contributors = 0
    try:
        contributors = repo.get_contributors(anon="false").totalCount
    except GithubException:
        pass

    return {
        "stars": stars,
        "forks": forks,
        "commits_week": commits_week,
        "issue_response_hrs": issue_response_hrs,
        "contributors": contributors,
    }


def _avg_issue_response(repo) -> float:
    """Average hours between issue creation and first comment for the
    last 10 closed issues. Returns 168 (1 week) if no data."""
    try:
        issues = repo.get_issues(state="closed", sort="updated", direction="desc")
        times = []
        for issue in issues[:10]:
            if issue.pull_request:
                continue
            if issue.closed_at and issue.created_at:
                delta = issue.closed_at - issue.created_at
                times.append(delta.total_seconds() / 3600)
        return round(sum(times) / len(times), 1) if times else 168.0
    except GithubException:
        return 168.0


def get_user_account_age_days(username: str) -> int | None:
    """Return the age of a GitHub account in days, or None on failure."""
    try:
        gh = get_github_client()
        user = api_call_with_retry(gh.get_user, username)
        created = user.created_at
        if created:
            return (datetime.now(timezone.utc) - created).days
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Issue interaction
# ---------------------------------------------------------------------------


def post_issue_comment(issue_number: int, body: str) -> None:
    """Post a comment on an issue in the current repo."""
    gh = get_github_client()
    repo_name = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo_name:
        print(f"[dry-run] Comment on #{issue_number}:\n{body}")
        return
    repo = gh.get_repo(repo_name)
    issue = repo.get_issue(number=issue_number)
    issue.create_comment(body)


def close_issue(issue_number: int) -> None:
    """Close an issue in the current repo."""
    gh = get_github_client()
    repo_name = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo_name:
        print(f"[dry-run] Close #{issue_number}")
        return
    repo = gh.get_repo(repo_name)
    issue = repo.get_issue(number=issue_number)
    issue.edit(state="closed")


# ---------------------------------------------------------------------------
# Portfolio helpers
# ---------------------------------------------------------------------------


def calc_portfolio_value(trader: dict, market: dict) -> float:
    """Calculate total portfolio value = cash + holdings at current prices."""
    stocks = market.get("stocks", {})
    total = trader["cash"]
    for ticker, pos in trader.get("portfolio", {}).items():
        price = stocks.get(ticker, {}).get("price", 0)
        total += price * pos["qty"]
    # Shorts: unrealised P&L = (entry_price - current_price) * qty
    for ticker, pos in trader.get("shorts", {}).items():
        price = stocks.get(ticker, {}).get("price", 0)
        total += (pos["entry_price"] - price) * pos["qty"]
        total += pos.get("margin", 0)
    return round(total, 2)


def update_trader_stats(trader: dict, market: dict) -> None:
    """Recalculate total_value, pnl, pnl_pct in place."""
    trader["total_value"] = calc_portfolio_value(trader, market)
    trader["pnl"] = round(trader["total_value"] - trader["starting_cash"], 2)
    starting = trader["starting_cash"]
    trader["pnl_pct"] = round((trader["pnl"] / starting) * 100, 1) if starting else 0.0


# ---------------------------------------------------------------------------
# Data integrity validation
# ---------------------------------------------------------------------------


def validate_market(market: dict) -> list[str]:
    """Validate market.json structure. Returns list of error strings."""
    errors = []
    if not isinstance(market, dict):
        return ["market.json is not a dict"]

    if "stocks" not in market:
        errors.append("Missing 'stocks' key")
        return errors

    for ticker, stock in market["stocks"].items():
        for field in ("price", "prev_price", "market_cap", "shares_outstanding"):
            val = stock.get(field)
            if val is None:
                errors.append(f"{ticker}: missing '{field}'")
            elif isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                errors.append(f"{ticker}: '{field}' is NaN/Inf")
            elif isinstance(val, (int, float)) and val < 0 and field != "change_pct":
                errors.append(f"{ticker}: '{field}' is negative ({val})")

    return errors


def validate_trader(trader: dict) -> list[str]:
    """Validate a trader dict. Returns list of error strings."""
    errors = []
    username = trader.get("username", "?")

    cash = trader.get("cash", 0)
    if isinstance(cash, float) and (math.isnan(cash) or math.isinf(cash)):
        errors.append(f"@{username}: cash is NaN/Inf")
    elif cash < 0:
        errors.append(f"@{username}: negative cash (${cash:.2f})")

    for ticker, pos in trader.get("portfolio", {}).items():
        qty = pos.get("qty", 0)
        if qty <= 0:
            errors.append(f"@{username}: zero/negative qty for {ticker} ({qty})")

    for ticker, pos in trader.get("shorts", {}).items():
        qty = pos.get("qty", 0)
        if qty <= 0:
            errors.append(f"@{username}: zero/negative short qty for {ticker} ({qty})")
        margin = pos.get("margin", 0)
        if margin < 0:
            errors.append(f"@{username}: negative margin for {ticker} (${margin:.2f})")

    return errors


def validate_state() -> list[str]:
    """Run all validation checks. Returns list of errors (empty = healthy)."""
    errors = []

    market = load_market()
    errors.extend(validate_market(market))

    stocks = market.get("stocks", {})
    for username in list_traders():
        trader = load_trader(username)
        errors.extend(validate_trader(trader))

        # Check for positions in delisted stocks
        for ticker in trader.get("portfolio", {}):
            if stocks.get(ticker, {}).get("market_status") == "DELISTED":
                errors.append(f"@{username}: holds delisted stock {ticker}")

    return errors


# ---------------------------------------------------------------------------
# History rotation (archive files older than N days)
# ---------------------------------------------------------------------------


def rotate_history(max_days: int = 30) -> list[str]:
    """Delete history files older than max_days. Returns list of deleted paths."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    deleted = []

    for subdir in ("prices", "trades", "events"):
        history_path = HISTORY_DIR / subdir
        if not history_path.exists():
            continue
        for path in history_path.glob("*.json"):
            # Filename is the date: 2026-03-22.json
            date_part = path.stem
            if date_part < cutoff_str:
                path.unlink()
                deleted.append(str(path))

    return deleted


# ---------------------------------------------------------------------------
# Engine logging
# ---------------------------------------------------------------------------


def log_engine_run(engine_name: str, duration_s: float, details: dict | None = None) -> None:
    """Append a run record to data/engine_log.json."""
    log_path = DATA_DIR / "engine_log.json"
    log = load_json(log_path)
    if not isinstance(log, dict):
        log = {"runs": []}
    if "runs" not in log:
        log["runs"] = []

    record = {
        "engine": engine_name,
        "timestamp": now_iso(),
        "duration_s": round(duration_s, 2),
    }
    if details:
        record["details"] = details

    # Keep only last 100 entries
    log["runs"] = log["runs"][-99:] + [record]
    save_json(log_path, log)


# ---------------------------------------------------------------------------
# Short position margin check
# ---------------------------------------------------------------------------


def check_margin_calls(market: dict) -> list[dict]:
    """Check all traders for shorts exceeding their margin. Force-close if needed.
    Returns list of margin call event dicts."""
    events = []
    stocks = market.get("stocks", {})

    for username in list_traders():
        trader = load_trader(username)
        shorts_to_close = []

        for ticker, pos in trader.get("shorts", {}).items():
            price = stocks.get(ticker, {}).get("price", 0)
            entry = pos["entry_price"]
            qty = pos["qty"]
            margin = pos.get("margin", 0)

            # Loss = (current - entry) * qty (positive when price rose above entry)
            loss = (price - entry) * qty
            if loss >= margin:
                shorts_to_close.append(ticker)

        for ticker in shorts_to_close:
            pos = trader["shorts"][ticker]
            price = stocks.get(ticker, {}).get("price", 0)
            qty = pos["qty"]
            margin = pos.get("margin", 0)
            loss = round((price - pos["entry_price"]) * qty, 2)

            # Return whatever margin minus loss (could be negative → cash goes down)
            net = round(margin - loss, 2)
            trader["cash"] = round(trader["cash"] + net, 2)
            del trader["shorts"][ticker]

            events.append({
                "type": "MARGIN_CALL",
                "ticker": ticker,
                "user": username,
                "qty": qty,
                "entry_price": pos["entry_price"],
                "close_price": price,
                "loss": loss,
            })

        if shorts_to_close:
            update_trader_stats(trader, market)
            save_trader(username, trader)

    return events
