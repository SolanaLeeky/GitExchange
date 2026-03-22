#!/usr/bin/env python3
"""One-time bootstrap: fetch live GitHub metrics, calculate initial prices,
and seed market.json + the first price history snapshot."""

import random
import sys

from utils import (
    load_config,
    save_market,
    append_price_snapshot,
    get_repo_metrics,
    ticker_from_repo,
    now_iso,
    today_str,
    DATA_DIR,
)


def normalize_metrics(all_metrics: dict[str, dict]) -> dict[str, dict]:
    """Normalize each metric to 0-1000 scale across all repos."""
    keys = ["stars", "forks", "commits_week", "issue_response_hrs", "contributors"]
    normalized = {}

    for key in keys:
        values = [m.get(key, 0) for m in all_metrics.values()]
        max_val = max(values) if values else 1

        # issue_response_hrs is inverse — lower is better
        if key == "issue_response_hrs":
            max_val = max(max_val, 1)
            for repo, m in all_metrics.items():
                if repo not in normalized:
                    normalized[repo] = {}
                raw = m.get(key, max_val)
                # Invert: fastest response = highest score
                normalized[repo][key] = round((1 - raw / max_val) * 1000, 1)
        else:
            max_val = max(max_val, 1)
            for repo, m in all_metrics.items():
                if repo not in normalized:
                    normalized[repo] = {}
                normalized[repo][key] = round((m.get(key, 0) / max_val) * 1000, 1)

    return normalized


def calculate_initial_price(normalized: dict, weights: dict) -> float:
    """Weighted score from normalized metrics."""
    # Map config weight keys to metric keys
    key_map = {
        "stars": "stars",
        "commits_week": "commits_week",
        "forks": "forks",
        "issue_response": "issue_response_hrs",
        "contributors": "contributors",
    }
    score = 0.0
    for config_key, weight in weights.items():
        metric_key = key_map.get(config_key, config_key)
        score += normalized.get(metric_key, 0) * weight
    return round(score, 2)


def main():
    config = load_config()
    repos = config["listed_repos"]
    weights = config["price_weights"]

    print(f"Bootstrapping market with {len(repos)} repos...")

    # Fetch metrics for all repos
    all_metrics: dict[str, dict] = {}
    for repo_name in repos:
        print(f"  Fetching metrics for {repo_name}...", end=" ")
        try:
            metrics = get_repo_metrics(repo_name)
            all_metrics[repo_name] = metrics
            print(f"stars={metrics['stars']}, forks={metrics['forks']}")
        except Exception as e:
            print(f"FAILED: {e}")
            # Fallback: zeros
            all_metrics[repo_name] = {
                "stars": 0,
                "forks": 0,
                "commits_week": 0,
                "issue_response_hrs": 168,
                "contributors": 0,
            }

    # Normalize
    normalized = normalize_metrics(all_metrics)

    # Build market.json
    stocks = {}
    price_snapshot = {}
    timestamp = now_iso()

    for repo_name in repos:
        ticker = ticker_from_repo(repo_name)
        price = calculate_initial_price(normalized[repo_name], weights)

        # Add small random volatility so prices aren't exactly the formula
        volatility = random.uniform(-config["volatility_range"], config["volatility_range"])
        price = round(price * (1 + volatility), 2)

        stocks[ticker] = {
            "full_name": repo_name,
            "price": price,
            "prev_price": price,
            "change_pct": 0.0,
            "volume_24h": 0,
            "market_cap": round(price * 500, 2),
            "shares_outstanding": 500,
            "ipo_date": today_str(),
            "metrics": all_metrics[repo_name],
            "tags": [],
        }
        price_snapshot[ticker] = price
        print(f"  {ticker}: ${price}")

    total_cap = sum(s["market_cap"] for s in stocks.values())

    market = {
        "last_updated": timestamp,
        "market_status": "open",
        "total_market_cap": round(total_cap, 2),
        "stocks": stocks,
    }

    save_market(market)
    print(f"\nMarket saved to data/market.json ({len(stocks)} stocks)")

    # First price history snapshot
    time_part = timestamp.split("T")[1].replace("Z", "") + "Z"
    append_price_snapshot({"time": time_part, "prices": price_snapshot})
    print(f"Price snapshot saved to data/history/prices/{today_str()}.json")

    print("\nBootstrap complete!")


if __name__ == "__main__":
    main()
