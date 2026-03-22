#!/usr/bin/env python3
"""Market event engine: dividends, IPO scanner, crash detector,
short squeeze detector, and achievement scanner. Runs daily via cron."""

import os
import sys
import time as _time
from datetime import datetime, timezone, timedelta

from github import GithubException

from utils import (
    load_config,
    load_market,
    save_market,
    load_trader,
    save_trader,
    list_traders,
    update_trader_stats,
    get_github_client,
    get_repo_metrics,
    append_event_history,
    validate_state,
    check_margin_calls,
    rotate_history,
    log_engine_run,
    ticker_from_repo,
    now_iso,
    today_str,
    save_json,
    load_json,
    DATA_DIR,
    HISTORY_DIR,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. DIVIDENDS
# ═══════════════════════════════════════════════════════════════════════════


def process_dividends(market: dict, config: dict) -> list[dict]:
    """Pay 0.5% of price per share to holders of repos with 50+ weekly commits."""
    events = []
    stocks = market.get("stocks", {})
    threshold = config.get("dividend_threshold_commits", 50)
    traders = list_traders()

    for ticker, stock in stocks.items():
        commits = stock.get("metrics", {}).get("commits_week", 0)
        if commits < threshold:
            continue

        dividend_per_share = round(stock["price"] * 0.005, 2)
        if dividend_per_share <= 0:
            continue

        total_paid = 0.0
        recipient_count = 0

        for username in traders:
            trader = load_trader(username)
            held = trader.get("portfolio", {}).get(ticker, {}).get("qty", 0)
            if held <= 0:
                continue

            payout = round(dividend_per_share * held, 2)
            trader["cash"] = round(trader["cash"] + payout, 2)
            update_trader_stats(trader, market)
            save_trader(username, trader)

            total_paid += payout
            recipient_count += 1

        if recipient_count > 0:
            event = {
                "type": "DIVIDEND",
                "ticker": ticker,
                "amount_per_share": dividend_per_share,
                "total_paid": round(total_paid, 2),
                "recipients": recipient_count,
                "commits_week": commits,
            }
            events.append(event)
            print(f"  DIVIDEND: {ticker} — ${dividend_per_share}/share to {recipient_count} holders (${total_paid:.2f} total)")

    if not events:
        print("  No dividends — no stock met the commit threshold.")
    return events


# ═══════════════════════════════════════════════════════════════════════════
# 2. IPO SCANNER
# ═══════════════════════════════════════════════════════════════════════════


def process_ipos(market: dict, config: dict) -> list[dict]:
    """Scan GitHub for trending repos not yet listed. List them if stars >= threshold."""
    events = []
    gh = get_github_client()
    threshold = config.get("ipo_threshold_stars", 1000)
    listed = set(config.get("listed_repos", []))
    stocks = market.get("stocks", {})

    # Search for recently created repos with high star velocity
    try:
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        query = f"stars:>{threshold} created:>{thirty_days_ago}"
        results = gh.search_repositories(query=query, sort="stars", order="desc")

        for repo in results[:5]:  # check top 5 trending
            if repo.full_name in listed:
                continue

            ticker = ticker_from_repo(repo.full_name)

            # Skip if ticker already exists (name collision)
            if ticker in stocks:
                continue

            # Fetch metrics and calculate initial price
            try:
                metrics = get_repo_metrics(repo.full_name)
            except Exception:
                continue

            # Calculate initial price using weighted metrics (same formula as price engine)
            # Normalize against existing stocks to get a fair starting price
            all_values = {"stars": [], "forks": [], "commits_week": [], "issue_response_hrs": [], "contributors": []}
            for s in stocks.values():
                for k in all_values:
                    all_values[k].append(s.get("metrics", {}).get(k, 0))
            for k in all_values:
                all_values[k].append(metrics.get(k, 0))

            weights = config.get("price_weights", {})
            key_map = {"stars": "stars", "commits_week": "commits_week", "forks": "forks",
                        "issue_response": "issue_response_hrs", "contributors": "contributors"}

            score = 0.0
            for cfg_key, weight in weights.items():
                mk = key_map.get(cfg_key, cfg_key)
                vals = all_values.get(mk, [0])
                max_val = max(max(vals), 1)
                raw = metrics.get(mk, 0)
                if mk == "issue_response_hrs":
                    norm = (1 - raw / max_val) * 1000
                else:
                    norm = (raw / max_val) * 1000
                score += norm * weight

            price = round(max(score, 10.0), 2)

            stocks[ticker] = {
                "full_name": repo.full_name,
                "price": price,
                "prev_price": price,
                "change_pct": 0.0,
                "volume_24h": 0,
                "market_cap": round(price * 500, 2),
                "shares_outstanding": 500,
                "ipo_date": today_str(),
                "metrics": metrics,
                "tags": ["ipo-new"],
            }

            # Add to config listed_repos
            config["listed_repos"].append(repo.full_name)

            event = {
                "type": "IPO",
                "ticker": ticker,
                "full_name": repo.full_name,
                "initial_price": price,
                "stars_at_ipo": metrics["stars"],
            }
            events.append(event)
            print(f"  IPO: {ticker} ({repo.full_name}) listed at ${price} ({metrics['stars']} stars)")

            # Create announcement issue
            _create_event_issue(
                f"IPO: {repo.full_name} now tradeable!",
                f"**{repo.full_name}** has been listed on GitExchange!\n\n"
                f"- **Ticker**: `{ticker}`\n"
                f"- **Initial Price**: ${price}\n"
                f"- **Stars**: {metrics['stars']:,}\n\n"
                f"Start trading now!"
            )

    except GithubException as e:
        print(f"  IPO scanner skipped: GitHub API error ({e})")
    except Exception as e:
        print(f"  IPO scanner skipped: {e}")

    if not events:
        print("  No new IPOs detected.")

    return events


# ═══════════════════════════════════════════════════════════════════════════
# 3. CRASH DETECTOR
# ═══════════════════════════════════════════════════════════════════════════


def process_crashes(market: dict, config: dict) -> list[dict]:
    """Check if any listed repo has been archived or deleted. Delist + force-sell."""
    events = []
    gh = get_github_client()
    stocks = market.get("stocks", {})
    traders = list_traders()

    for ticker, stock in list(stocks.items()):
        if stock.get("market_status") == "DELISTED":
            continue

        repo_name = stock["full_name"]
        crashed = False

        try:
            repo = gh.get_repo(repo_name)
            if repo.archived:
                crashed = True
                reason = "archived"
        except GithubException as e:
            if e.status == 404:
                crashed = True
                reason = "deleted/not found"
            else:
                continue  # transient API error, skip

        if not crashed:
            continue

        old_price = stock["price"]
        stock["price"] = 0
        stock["prev_price"] = old_price
        stock["change_pct"] = -100.0
        stock["market_cap"] = 0
        stock["market_status"] = "DELISTED"

        # Force-sell all holders at $0
        affected = 0
        for username in traders:
            trader = load_trader(username)
            held = trader.get("portfolio", {}).get(ticker, {}).get("qty", 0)
            if held > 0:
                del trader["portfolio"][ticker]
                affected += 1
                update_trader_stats(trader, market)
                save_trader(username, trader)

        event = {
            "type": "CRASH",
            "ticker": ticker,
            "full_name": repo_name,
            "reason": reason,
            "prev_price": old_price,
            "affected_holders": affected,
        }
        events.append(event)
        print(f"  CRASH: {ticker} ({repo_name}) — {reason}. {affected} holders wiped out.")

        _create_event_issue(
            f"CRASH: {ticker} has been {reason}!",
            f"**{repo_name}** has been {reason}.\n\n"
            f"- **Previous Price**: ${old_price}\n"
            f"- **Current Price**: $0.00 (DELISTED)\n"
            f"- **Affected Holders**: {affected}\n\n"
            f"All positions have been force-liquidated."
        )

    if not events:
        print("  No crashes detected.")
    return events


# ═══════════════════════════════════════════════════════════════════════════
# 4. SHORT SQUEEZE DETECTOR
# ═══════════════════════════════════════════════════════════════════════════


def process_short_squeezes(market: dict, config: dict) -> list[dict]:
    """If short interest > 60% and price rose > 10% in 24h, force-close all shorts."""
    events = []
    stocks = market.get("stocks", {})
    traders = list_traders()

    for ticker, stock in stocks.items():
        if stock.get("market_status") == "DELISTED":
            continue

        shares = stock.get("shares_outstanding", 500)
        price = stock["price"]
        prev = stock.get("prev_price", price)

        # Calculate total shorted across all traders
        total_shorted = 0
        for username in traders:
            trader = load_trader(username)
            total_shorted += trader.get("shorts", {}).get(ticker, {}).get("qty", 0)

        if shares <= 0:
            continue
        short_interest = total_shorted / shares

        price_change = ((price - prev) / prev) if prev > 0 else 0

        if short_interest <= 0.60 or price_change <= 0.10:
            continue

        # SHORT SQUEEZE! Force-close all shorts
        forced = 0
        total_losses = 0.0
        for username in traders:
            trader = load_trader(username)
            short_pos = trader.get("shorts", {}).get(ticker)
            if not short_pos:
                continue

            qty = short_pos["qty"]
            entry = short_pos["entry_price"]
            margin = short_pos.get("margin", 0)
            loss = round((price - entry) * qty, 2)

            # Return margin minus loss
            trader["cash"] = round(trader["cash"] + margin - loss, 2)
            del trader["shorts"][ticker]
            update_trader_stats(trader, market)
            save_trader(username, trader)

            forced += 1
            total_losses += loss

        event = {
            "type": "SHORT_SQUEEZE",
            "ticker": ticker,
            "short_interest_pct": round(short_interest * 100, 1),
            "price_change_pct": round(price_change * 100, 1),
            "forced_closures": forced,
            "total_losses": round(total_losses, 2),
        }
        events.append(event)
        print(f"  SHORT SQUEEZE: {ticker} — {forced} shorts force-closed (${total_losses:.2f} in losses)")

        _create_event_issue(
            f"SHORT SQUEEZE on {ticker}!",
            f"**{ticker}** triggered a short squeeze!\n\n"
            f"- **Short Interest**: {short_interest*100:.1f}%\n"
            f"- **Price Change (24h)**: +{price_change*100:.1f}%\n"
            f"- **Forced Closures**: {forced}\n"
            f"- **Total Losses**: ${total_losses:,.2f}"
        )

    if not events:
        print("  No short squeezes triggered.")
    return events


# ═══════════════════════════════════════════════════════════════════════════
# 5. ACHIEVEMENT SCANNER
# ═══════════════════════════════════════════════════════════════════════════

def _is_contrarian(trader: dict, trades: list[dict] | None, market: dict | None) -> bool:
    """Returns True if the trader bought a stock whose change_pct was less than -5%."""
    if not trades or not market:
        return False
    stocks = market.get("stocks", {})
    for t in trades:
        if t.get("user") != trader.get("username"):
            continue
        if t.get("action") != "BUY":
            continue
        ticker = t.get("ticker", "")
        change_pct = stocks.get(ticker, {}).get("change_pct", 0)
        if change_pct < -5:
            return True
    return False


def _is_early_bird(trades: list[dict] | None, trader: dict) -> bool:
    """Returns True if any trade happened within the first 60 minutes of a 6-hour price cycle.

    Price cycles start at hours 0, 6, 12, 18 UTC. The first 60 minutes of each
    cycle are minutes 0-59 of those hours.
    """
    if not trades:
        return False
    cycle_hours = {0, 6, 12, 18}
    for t in trades:
        if t.get("user") != trader.get("username"):
            continue
        try:
            ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
            if ts.hour in cycle_hours and ts.minute < 60:
                return True
        except (ValueError, KeyError):
            continue
    return False


ACHIEVEMENTS = [
    ("first-trade",   lambda t, **_: t.get("trade_count", 0) >= 1),
    ("100-trades",    lambda t, **_: t.get("trade_count", 0) >= 100),
    ("10x-return",    lambda t, **_: t.get("total_value", 0) >= t.get("starting_cash", 10000) * 10),
    ("diversified",   lambda t, **_: len(t.get("portfolio", {})) >= 10),
    ("whale",         lambda t, trades=None, **_: _has_whale_trade(trades)),
    ("diamond-hands", lambda t, trades=None, **_: _has_diamond_hands(t, trades)),
    ("paper-hands",   lambda t, trades=None, **_: _has_paper_hands(t, trades)),
    ("short-king",    lambda t, **_: _short_profit(t) >= 5000),
    ("ipo-hunter",    lambda t, trades=None, market=None, **_: _is_ipo_hunter(t, trades, market)),
    ("contrarian",    lambda t, trades=None, market=None, **_: _is_contrarian(t, trades, market)),
    ("early-bird",    lambda t, trades=None, **_: _is_early_bird(trades, t)),
]


def _has_whale_trade(trades: list[dict] | None) -> bool:
    """Any single trade total >= $5000."""
    if not trades:
        return False
    return any(t.get("total", 0) >= 5000 for t in trades)


def _has_diamond_hands(trader: dict, trades: list[dict] | None) -> bool:
    """Held any stock for 30+ days. Check earliest buy still in portfolio."""
    if not trades:
        return False
    now = datetime.now(timezone.utc)
    held_tickers = set(trader.get("portfolio", {}).keys())
    for t in trades:
        if t.get("action") == "BUY" and t.get("ticker") in held_tickers:
            try:
                ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
                if (now - ts).days >= 30:
                    return True
            except (ValueError, KeyError):
                continue
    return False


def _has_paper_hands(trader: dict, trades: list[dict] | None) -> bool:
    """Sold within 1 hour of buying."""
    if not trades:
        return False
    buys: dict[str, datetime] = {}
    for t in sorted(trades, key=lambda x: x.get("timestamp", "")):
        user = t.get("user", "")
        if user != trader.get("username"):
            continue
        ticker = t.get("ticker", "")
        try:
            ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue
        if t.get("action") == "BUY":
            buys[ticker] = ts
        elif t.get("action") == "SELL" and ticker in buys:
            if (ts - buys[ticker]).total_seconds() < 3600:
                return True
    return False


def _short_profit(trader: dict) -> float:
    """Estimate cumulative short profits from cash above starting + portfolio."""
    # Rough heuristic: if they've closed shorts profitably, cash will reflect it
    return max(0, trader.get("pnl", 0))


def _is_ipo_hunter(trader: dict, trades: list[dict] | None, market: dict | None) -> bool:
    """Bought a stock on its IPO day."""
    if not trades or not market:
        return False
    stocks = market.get("stocks", {})
    for t in trades:
        if t.get("user") != trader.get("username"):
            continue
        if t.get("action") != "BUY":
            continue
        ticker = t.get("ticker", "")
        ipo_date = stocks.get(ticker, {}).get("ipo_date", "")
        trade_date = t.get("timestamp", "")[:10]
        if ipo_date and trade_date == ipo_date:
            return True
    return False


def _load_all_trades() -> list[dict]:
    """Load all trade history files and return flat list of trades."""
    trades_dir = HISTORY_DIR / "trades"
    if not trades_dir.exists():
        return []
    all_trades = []
    for path in sorted(trades_dir.glob("*.json")):
        data = load_json(path)
        all_trades.extend(data.get("trades", []))
    return all_trades


def process_achievements(market: dict) -> int:
    """Scan all traders for new achievements. Returns count of new achievements granted."""
    traders = list_traders()
    all_trades = _load_all_trades()
    granted = 0

    for username in traders:
        trader = load_trader(username)
        existing = set(trader.get("achievements", []))
        user_trades = [t for t in all_trades if t.get("user") == username]

        new_achievements = []
        for name, check_fn in ACHIEVEMENTS:
            if name in existing:
                continue
            try:
                if check_fn(trader, trades=user_trades, market=market):
                    new_achievements.append(name)
            except Exception:
                continue

        if new_achievements:
            trader["achievements"] = list(existing | set(new_achievements))
            save_trader(username, trader)
            granted += len(new_achievements)
            print(f"  Achievement: @{username} earned {', '.join(new_achievements)}")

    if granted == 0:
        print("  No new achievements.")
    return granted


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _create_event_issue(title: str, body: str) -> None:
    """Create an announcement issue in the repo."""
    gh = get_github_client()
    repo_name = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo_name:
        print(f"  [dry-run] Would create issue: {title}")
        return
    try:
        repo = gh.get_repo(repo_name)
        repo.create_issue(title=title, body=body, labels=["market-event"])
    except GithubException as e:
        print(f"  Warning: could not create issue: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════


def main():
    start = _time.time()
    config = load_config()
    market = load_market()

    print(f"Daily market events — {today_str()}")
    print("=" * 50)

    # 0. Pre-flight validation
    errors = validate_state()
    if errors:
        print(f"\nWARNING: {len(errors)} data integrity issue(s):")
        for e in errors[:5]:
            print(f"  - {e}")

    all_events = []

    # 1. Dividends
    print("\n[1/7] Dividends")
    all_events.extend(process_dividends(market, config))

    # 2. IPO Scanner
    print("\n[2/7] IPO Scanner")
    ipo_events = process_ipos(market, config)
    all_events.extend(ipo_events)

    # 3. Crash Detector
    print("\n[3/7] Crash Detector")
    all_events.extend(process_crashes(market, config))

    # 4. Short Squeeze Detector
    print("\n[4/7] Short Squeeze Detector")
    all_events.extend(process_short_squeezes(market, config))

    # 5. Margin Calls
    print("\n[5/7] Margin Calls")
    margin_events = check_margin_calls(market)
    all_events.extend(margin_events)
    if margin_events:
        for me in margin_events:
            print(f"  MARGIN CALL: @{me['user']} — {me['ticker']} force-closed, loss ${me['loss']:.2f}")
    else:
        print("  No margin calls.")

    # Save market state (IPOs may have added stocks, crashes may have delisted)
    market["stocks"] = market.get("stocks", {})
    total_cap = sum(s.get("market_cap", 0) for s in market["stocks"].values())
    market["total_market_cap"] = round(total_cap, 2)
    market["last_updated"] = now_iso()
    save_market(market)

    # Save updated config (IPOs may have added repos)
    if ipo_events:
        save_json(DATA_DIR / "config.json", config)

    # 6. Achievement Scanner
    print("\n[6/7] Achievement Scanner")
    achievements_granted = process_achievements(market)

    # 7. History Rotation
    print("\n[7/7] History Rotation")
    deleted = rotate_history(max_days=30)
    if deleted:
        print(f"  Archived {len(deleted)} old history file(s).")
    else:
        print("  No old history files to rotate.")

    # Log all events to history
    for event in all_events:
        event["timestamp"] = now_iso()
        append_event_history(event)

    duration = _time.time() - start
    log_engine_run("events", duration, {
        "total_events": len(all_events),
        "dividends": sum(1 for e in all_events if e["type"] == "DIVIDEND"),
        "ipos": len(ipo_events),
        "margin_calls": len(margin_events),
        "achievements": achievements_granted,
        "history_rotated": len(deleted),
    })

    print(f"\n{'=' * 50}")
    print(f"Done. {len(all_events)} events processed in {duration:.1f}s.")


if __name__ == "__main__":
    main()
