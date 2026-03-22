#!/usr/bin/env python3
"""Price engine: fetch GitHub metrics, recalculate all stock prices,
update market.json, write price snapshot, recalculate trader values."""

import random
import sys
import time as _time

from utils import (
    load_config,
    load_market,
    save_market,
    load_trader,
    save_trader,
    list_traders,
    get_repo_metrics,
    append_price_snapshot,
    update_trader_stats,
    validate_state,
    log_engine_run,
    ticker_from_repo,
    now_iso,
)


# ---------------------------------------------------------------------------
# Normalization + pricing (same logic as bootstrap, but with momentum)
# ---------------------------------------------------------------------------

WEIGHT_KEY_MAP = {
    "stars": "stars",
    "commits_week": "commits_week",
    "forks": "forks",
    "issue_response": "issue_response_hrs",
    "contributors": "contributors",
}


def normalize_metrics(all_metrics: dict[str, dict]) -> dict[str, dict]:
    """Normalize each metric to 0-1000 across all repos."""
    keys = ["stars", "forks", "commits_week", "issue_response_hrs", "contributors"]
    normalized: dict[str, dict] = {}

    for key in keys:
        values = [m.get(key, 0) for m in all_metrics.values()]
        max_val = max(values) if values else 1
        max_val = max(max_val, 1)

        for repo, m in all_metrics.items():
            if repo not in normalized:
                normalized[repo] = {}

            if key == "issue_response_hrs":
                # Invert: lower response time = higher score
                raw = m.get(key, max_val)
                normalized[repo][key] = round((1 - raw / max_val) * 1000, 1)
            else:
                normalized[repo][key] = round((m.get(key, 0) / max_val) * 1000, 1)

    return normalized


def calculate_price(
    normalized: dict,
    weights: dict,
    prev_price: float,
    config: dict,
) -> float:
    """Calculate stock price from normalized metrics with momentum + volatility."""
    # Base weighted score
    base = 0.0
    for config_key, weight in weights.items():
        metric_key = WEIGHT_KEY_MAP.get(config_key, config_key)
        base += normalized.get(metric_key, 0) * weight

    # Momentum: trend from previous price, clamped
    momentum = 0.0
    if prev_price > 0:
        momentum = (base - prev_price) / prev_price
        max_m = config.get("momentum_range", 0.08)
        momentum = max(-max_m, min(momentum, max_m))

    # Random volatility
    vol_range = config.get("volatility_range", 0.03)
    volatility = random.uniform(-vol_range, vol_range)

    price = base * (1 + momentum * 0.5) * (1 + volatility)
    return round(max(price, 0.01), 2)  # floor at $0.01


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    start = _time.time()
    config = load_config()
    market = load_market()
    stocks = market.get("stocks", {})
    weights = config["price_weights"]
    repos = config["listed_repos"]

    # Pre-flight validation
    errors = validate_state()
    if errors:
        print(f"WARNING: {len(errors)} data integrity issue(s):")
        for e in errors[:5]:
            print(f"  - {e}")

    print(f"Price update: {len(repos)} stocks")

    # 1. Fetch fresh metrics for every listed repo
    all_metrics: dict[str, dict] = {}
    ticker_map: dict[str, str] = {}  # repo_name -> ticker

    for repo_name in repos:
        ticker = ticker_from_repo(repo_name)
        ticker_map[repo_name] = ticker

        print(f"  {ticker}: fetching...", end=" ")
        try:
            metrics = get_repo_metrics(repo_name)
            all_metrics[repo_name] = metrics
            print(f"stars={metrics['stars']}")
        except Exception as e:
            # Graceful degradation: use cached metrics from market.json
            cached = stocks.get(ticker, {}).get("metrics", {})
            if cached:
                all_metrics[repo_name] = cached
                print(f"API FAILED ({e}), using cached metrics")
            else:
                all_metrics[repo_name] = {
                    "stars": 0, "forks": 0, "commits_week": 0,
                    "issue_response_hrs": 168, "contributors": 0,
                }
                print(f"API FAILED ({e}), using zeros")

    # 2. Normalize across all repos
    normalized = normalize_metrics(all_metrics)

    # 3. Calculate new prices
    price_snapshot = {}
    for repo_name in repos:
        ticker = ticker_map[repo_name]
        prev_price = stocks.get(ticker, {}).get("price", 0)
        new_price = calculate_price(normalized[repo_name], weights, prev_price, config)

        change_pct = 0.0
        if prev_price > 0:
            change_pct = round(((new_price - prev_price) / prev_price) * 100, 2)

        # Update stock entry
        if ticker not in stocks:
            # New stock added via config but not yet in market.json
            stocks[ticker] = {
                "full_name": repo_name,
                "shares_outstanding": 500,
                "ipo_date": market.get("last_updated", "")[:10],
                "volume_24h": 0,
                "tags": [],
            }

        stocks[ticker]["prev_price"] = prev_price
        stocks[ticker]["price"] = new_price
        stocks[ticker]["change_pct"] = change_pct
        stocks[ticker]["market_cap"] = round(new_price * stocks[ticker]["shares_outstanding"], 2)
        stocks[ticker]["metrics"] = all_metrics[repo_name]

        price_snapshot[ticker] = new_price
        direction = "+" if change_pct >= 0 else ""
        print(f"  {ticker}: ${prev_price} -> ${new_price} ({direction}{change_pct}%)")

    # 4. Update market totals
    market["stocks"] = stocks
    market["total_market_cap"] = round(sum(s["market_cap"] for s in stocks.values()), 2)
    market["last_updated"] = now_iso()
    save_market(market)

    # 5. Write price snapshot to history
    timestamp = now_iso()
    time_part = timestamp.split("T")[1]
    append_price_snapshot({"time": time_part, "prices": price_snapshot})

    # 6. Recalculate all trader portfolio values
    traders = list_traders()
    for username in traders:
        trader = load_trader(username)
        update_trader_stats(trader, market)
        save_trader(username, trader)

    duration = _time.time() - start
    api_failures = sum(1 for r in repos if all_metrics.get(r) == stocks.get(ticker_map.get(r, ""), {}).get("metrics"))
    log_engine_run("price", duration, {
        "stocks_updated": len(repos),
        "traders_recalculated": len(traders),
        "api_failures": api_failures,
    })

    print(f"\nDone. {len(repos)} prices updated, {len(traders)} traders recalculated ({duration:.1f}s).")


if __name__ == "__main__":
    main()
