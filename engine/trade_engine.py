#!/usr/bin/env python3
"""Trade engine: parse issue title, validate, execute BUY/SELL/SHORT/COVER,
update trader state, post receipt, close issue.

Includes abuse protection: account age, rate limiting, duplicate detection."""

import os
import re
import sys
import time as _time
from datetime import datetime, timezone

from utils import (
    load_config,
    load_market,
    save_market,
    load_trader,
    save_trader,
    update_trader_stats,
    append_trade_history,
    post_issue_comment,
    close_issue,
    get_user_account_age_days,
    validate_state,
    log_engine_run,
    now_iso,
    today_str,
    load_json,
    HISTORY_DIR,
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

TRADE_REGEX = re.compile(r"^(BUY|SELL|SHORT|COVER)\s+(\w+)\s+(\d+)$", re.IGNORECASE)
PORTFOLIO_REGEX = re.compile(r"^PORTFOLIO$", re.IGNORECASE)


def parse_trade(title: str) -> tuple[str, str, int] | None:
    """Parse issue title into (action, ticker, quantity) or None."""
    m = TRADE_REGEX.match(title.strip())
    if not m:
        return None
    action = m.group(1).upper()
    ticker = m.group(2).lower()
    qty = int(m.group(3))
    return action, ticker, qty


def is_portfolio_request(title: str) -> bool:
    return bool(PORTFOLIO_REGEX.match(title.strip()))


# ---------------------------------------------------------------------------
# Abuse protection
# ---------------------------------------------------------------------------

MIN_ACCOUNT_AGE_DAYS = 7
MAX_TRADES_PER_HOUR = 5
DUPLICATE_WINDOW_SECONDS = 300  # 5 minutes


def check_account_age(username: str) -> str | None:
    """Reject accounts younger than 7 days. Returns error or None."""
    age = get_user_account_age_days(username)
    if age is not None and age < MIN_ACCOUNT_AGE_DAYS:
        return (
            f"Your GitHub account is {age} day(s) old. "
            f"Accounts must be at least {MIN_ACCOUNT_AGE_DAYS} days old to trade."
        )
    return None


def check_rate_limit_user(username: str) -> str | None:
    """Max 5 trades per user per hour. Returns error or None."""
    trades_path = HISTORY_DIR / "trades" / f"{today_str()}.json"
    data = load_json(trades_path)
    trades = data.get("trades", []) if data else []

    now = datetime.now(timezone.utc)
    one_hour_ago = now.timestamp() - 3600

    count = 0
    for t in trades:
        if t.get("user") != username:
            continue
        try:
            ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
            if ts.timestamp() > one_hour_ago:
                count += 1
        except (ValueError, KeyError):
            continue

    if count >= MAX_TRADES_PER_HOUR:
        return f"Rate limit: max {MAX_TRADES_PER_HOUR} trades per hour. Try again later."
    return None


def check_duplicate_trade(username: str, action: str, ticker: str) -> str | None:
    """Reject identical trade (same user+action+ticker) within 5 minutes."""
    trades_path = HISTORY_DIR / "trades" / f"{today_str()}.json"
    data = load_json(trades_path)
    trades = data.get("trades", []) if data else []

    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - DUPLICATE_WINDOW_SECONDS

    for t in reversed(trades):
        if t.get("user") != username:
            continue
        try:
            ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
            if ts.timestamp() < cutoff:
                break  # older trades, stop checking
        except (ValueError, KeyError):
            continue
        if t.get("action") == action and t.get("ticker") == ticker:
            return (
                f"Duplicate trade detected: you already submitted {action} {ticker} "
                f"within the last {DUPLICATE_WINDOW_SECONDS // 60} minutes."
            )
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_trade(
    action: str,
    ticker: str,
    qty: int,
    trader: dict,
    market: dict,
    config: dict,
) -> str | None:
    """Return error message string if trade is invalid, else None."""
    stocks = market.get("stocks", {})

    # Ticker exists?
    if ticker not in stocks:
        available = ", ".join(sorted(stocks.keys()))
        return f"Ticker `{ticker}` not found. Available: {available}"

    # Market open?
    if market.get("market_status") != "open":
        return "Market is currently closed."

    # Delisted?
    stock = stocks[ticker]
    if stock.get("market_status") == "DELISTED":
        return f"Stock `{ticker}` has been delisted and cannot be traded."

    price = stock["price"]

    # Quantity bounds
    min_qty = config.get("min_trade_qty", 1)
    max_qty = config.get("max_trade_qty", 100)
    if qty < min_qty or qty > max_qty:
        return f"Quantity must be between {min_qty} and {max_qty}."

    fee_pct = config.get("trading_fee_pct", 0.001)

    if action == "BUY":
        total_cost = price * qty * (1 + fee_pct)
        if trader["cash"] < total_cost:
            return (
                f"Insufficient cash. Need ${total_cost:,.2f} "
                f"but you have ${trader['cash']:,.2f}."
            )
        # Position limit
        current_qty = trader.get("portfolio", {}).get(ticker, {}).get("qty", 0)
        new_qty = current_qty + qty
        position_value = new_qty * price
        total_value = max(trader.get("total_value", trader["cash"]), 1)
        max_pct = config.get("max_position_pct", 0.40)
        if position_value / total_value > max_pct:
            return (
                f"Position limit exceeded. Max {max_pct*100:.0f}% of portfolio "
                f"in one stock (${position_value:,.2f} / ${total_value:,.2f})."
            )

    elif action == "SELL":
        held = trader.get("portfolio", {}).get(ticker, {}).get("qty", 0)
        if held < qty:
            return f"You only hold {held} shares of {ticker}."

    elif action == "SHORT":
        margin_pct = config.get("short_margin_pct", 1.50)
        margin_required = price * qty * margin_pct
        if trader["cash"] < margin_required:
            return (
                f"Insufficient margin. Need ${margin_required:,.2f} "
                f"({margin_pct*100:.0f}% margin) but you have ${trader['cash']:,.2f}."
            )

    elif action == "COVER":
        shorted = trader.get("shorts", {}).get(ticker, {}).get("qty", 0)
        if shorted < qty:
            return f"You only have {shorted} shares shorted on {ticker}."

    return None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_trade(
    action: str,
    ticker: str,
    qty: int,
    trader: dict,
    market: dict,
    config: dict,
) -> dict:
    """Execute the trade, mutating trader and market in place.
    Returns a trade record dict."""
    stock = market["stocks"][ticker]
    price = stock["price"]
    fee_pct = config.get("trading_fee_pct", 0.001)
    fee = round(price * qty * fee_pct, 2)
    total = round(price * qty, 2)

    if action == "BUY":
        cost = total + fee
        trader["cash"] = round(trader["cash"] - cost, 2)

        portfolio = trader.setdefault("portfolio", {})
        if ticker in portfolio:
            pos = portfolio[ticker]
            old_qty = pos["qty"]
            old_cost = pos["avg_cost"]
            new_qty = old_qty + qty
            pos["avg_cost"] = round((old_cost * old_qty + price * qty) / new_qty, 2)
            pos["qty"] = new_qty
        else:
            portfolio[ticker] = {"qty": qty, "avg_cost": price}

    elif action == "SELL":
        revenue = total - fee
        trader["cash"] = round(trader["cash"] + revenue, 2)

        pos = trader["portfolio"][ticker]
        pos["qty"] -= qty
        if pos["qty"] <= 0:
            del trader["portfolio"][ticker]

    elif action == "SHORT":
        margin_pct = config.get("short_margin_pct", 1.50)
        margin = round(price * qty * margin_pct, 2)
        trader["cash"] = round(trader["cash"] - margin, 2)

        shorts = trader.setdefault("shorts", {})
        if ticker in shorts:
            s = shorts[ticker]
            old_qty = s["qty"]
            old_entry = s["entry_price"]
            new_qty = old_qty + qty
            s["entry_price"] = round((old_entry * old_qty + price * qty) / new_qty, 2)
            s["qty"] = new_qty
            s["margin"] = round(s.get("margin", 0) + margin, 2)
        else:
            shorts[ticker] = {
                "qty": qty,
                "entry_price": price,
                "margin": margin,
            }

    elif action == "COVER":
        short_pos = trader["shorts"][ticker]
        entry = short_pos["entry_price"]
        pnl = round((entry - price) * qty - fee, 2)

        margin_return = round(short_pos["margin"] * (qty / short_pos["qty"]), 2)
        trader["cash"] = round(trader["cash"] + margin_return + pnl, 2)

        short_pos["qty"] -= qty
        short_pos["margin"] = round(short_pos["margin"] - margin_return, 2)
        if short_pos["qty"] <= 0:
            del trader["shorts"][ticker]

    # Update volume
    stock["volume_24h"] = stock.get("volume_24h", 0) + qty

    # Bump trade count
    trader["trade_count"] = trader.get("trade_count", 0) + 1

    # Recalc stats
    update_trader_stats(trader, market)

    issue_number = int(os.environ.get("ISSUE_NUMBER", 0))
    return {
        "id": f"t_{today_str().replace('-', '')}_{issue_number:04d}",
        "timestamp": now_iso(),
        "user": trader["username"],
        "action": action,
        "ticker": ticker,
        "qty": qty,
        "price": price,
        "total": total,
        "fee": fee,
        "issue_number": issue_number,
    }


# ---------------------------------------------------------------------------
# Receipt
# ---------------------------------------------------------------------------


def format_receipt(trade: dict, trader: dict, market: dict) -> str:
    """Markdown receipt for the issue comment, including per-trade P&L."""
    action_emoji = {
        "BUY": "📈", "SELL": "📉", "SHORT": "📉", "COVER": "📈"
    }
    emoji = action_emoji.get(trade["action"], "💹")

    lines = [
        f"## {emoji} Trade Executed",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Action** | {trade['action']} |",
        f"| **Stock** | {trade['ticker']} |",
        f"| **Quantity** | {trade['qty']} |",
        f"| **Price** | ${trade['price']:,.2f} |",
        f"| **Total** | ${trade['total']:,.2f} |",
        f"| **Fee** | ${trade['fee']:,.2f} |",
    ]

    # Per-trade P&L for SELL/COVER
    if trade["action"] == "SELL":
        avg_cost = trader.get("portfolio", {}).get(trade["ticker"], {}).get("avg_cost", trade["price"])
        pnl = round((trade["price"] - avg_cost) * trade["qty"] - trade["fee"], 2)
        pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
        lines.append(f"| **Trade P&L** | {pnl_str} |")
    elif trade["action"] == "COVER":
        # For cover, profit = (entry - current) * qty - fee (already closed, use trade data)
        lines.append(f"| **Trade P&L** | *see portfolio* |")

    lines.extend([
        f"| **Cash Remaining** | ${trader['cash']:,.2f} |",
        f"| **Portfolio Value** | ${trader['total_value']:,.2f} |",
        f"| **Overall P&L** | {'+' if trader['pnl'] >= 0 else ''}{trader['pnl_pct']:.1f}% (${trader['pnl']:+,.2f}) |",
        "",
        f"*Trade #{trader['trade_count']} by @{trader['username']}*",
    ])

    # Context line: position summary for BUY, realized P&L for SELL
    ticker = trade["ticker"]
    ticker_upper = ticker.upper()
    base_url = "https://github.com/SolanaLeeky/GitExchange/issues/new"

    if trade["action"] == "BUY":
        pos = trader.get("portfolio", {}).get(ticker, {})
        total_qty = pos.get("qty", trade["qty"])
        avg_cost = pos.get("avg_cost", trade["price"])
        lines.extend([
            "",
            f"> You now hold **{total_qty} total shares** of **{ticker_upper}** at **${avg_cost:,.2f}** avg cost.",
        ])
    elif trade["action"] == "SELL":
        # Recompute the same P&L shown in the table for the context line
        avg_cost = trader.get("portfolio", {}).get(ticker, {}).get("avg_cost", trade["price"])
        pnl = round((trade["price"] - avg_cost) * trade["qty"] - trade["fee"], 2)
        pnl_label = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
        pnl_word = "profit" if pnl >= 0 else "loss"
        lines.extend([
            "",
            f"> Realized **{pnl_label}** {pnl_word} on this sale of **{trade['qty']} {ticker_upper}**.",
        ])

    # Quick Actions footer
    lines.extend([
        "",
        "---",
        f"**Quick Actions:** "
        f"[View Portfolio]({base_url}?title=PORTFOLIO&body=Check+my+holdings)"
        f" | "
        f"[Buy More {ticker_upper}]({base_url}?title=BUY+{ticker}+10&body=Adjust+quantity+in+the+title+then+submit)",
    ])

    return "\n".join(lines)


def format_portfolio(trader: dict, market: dict) -> str:
    """Markdown portfolio view for PORTFOLIO command."""
    stocks = market.get("stocks", {})

    lines = [
        f"## 💼 Portfolio — @{trader['username']}",
        "",
        f"**Cash**: ${trader['cash']:,.2f}",
        f"**Portfolio Value**: ${trader['total_value']:,.2f}",
        f"**P&L**: {'+' if trader['pnl'] >= 0 else ''}{trader['pnl_pct']:.1f}% (${trader['pnl']:+,.2f})",
        f"**Trades**: {trader['trade_count']}",
        "",
    ]

    # Holdings
    portfolio = trader.get("portfolio", {})
    if portfolio:
        lines.extend([
            "### Holdings",
            "",
            "| Stock | Qty | Avg Cost | Current | Value | P&L |",
            "|-------|-----|----------|---------|-------|-----|",
        ])
        for ticker, pos in sorted(portfolio.items()):
            qty = pos["qty"]
            avg = pos["avg_cost"]
            current = stocks.get(ticker, {}).get("price", 0)
            value = round(current * qty, 2)
            pnl = round((current - avg) * qty, 2)
            pnl_pct = round(((current - avg) / avg) * 100, 1) if avg > 0 else 0
            pnl_str = f"+${pnl:,.2f} (+{pnl_pct:.1f}%)" if pnl >= 0 else f"-${abs(pnl):,.2f} ({pnl_pct:.1f}%)"
            lines.append(f"| **{ticker.upper()}** | {qty} | ${avg:,.2f} | ${current:,.2f} | ${value:,.2f} | {pnl_str} |")
        lines.append("")
    else:
        lines.extend(["### Holdings", "", "*No stocks held.*", ""])

    # Shorts
    shorts = trader.get("shorts", {})
    if shorts:
        lines.extend([
            "### Short Positions",
            "",
            "| Stock | Qty | Entry | Current | Margin | P&L |",
            "|-------|-----|-------|---------|--------|-----|",
        ])
        for ticker, pos in sorted(shorts.items()):
            qty = pos["qty"]
            entry = pos["entry_price"]
            margin = pos.get("margin", 0)
            current = stocks.get(ticker, {}).get("price", 0)
            pnl = round((entry - current) * qty, 2)
            pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
            lines.append(f"| **{ticker.upper()}** | {qty} | ${entry:,.2f} | ${current:,.2f} | ${margin:,.2f} | {pnl_str} |")
        lines.append("")

    # Achievements
    achievements = trader.get("achievements", [])
    if achievements:
        badge_map = {
            "first-trade": "🎯 First Trade",
            "100-trades": "💯 Century Trader",
            "10x-return": "🚀 10x Return",
            "diamond-hands": "💎 Diamond Hands",
            "paper-hands": "📄 Paper Hands",
            "short-king": "👑 Short King",
            "diversified": "🌐 Diversified",
            "whale": "🐋 Whale",
            "survivor": "🛡️ Survivor",
            "ipo-hunter": "🔔 IPO Hunter",
        }
        badge_list = ", ".join(badge_map.get(a, a) for a in achievements)
        lines.extend(["### Achievements", "", badge_list, ""])

    # Top Performer / Worst Performer
    base_url = "https://github.com/SolanaLeeky/GitExchange/issues/new"
    if portfolio:
        perf = {}
        for ticker, pos in portfolio.items():
            avg = pos["avg_cost"]
            current = stocks.get(ticker, {}).get("price", 0)
            pnl_pct = round(((current - avg) / avg) * 100, 1) if avg > 0 else 0.0
            perf[ticker] = pnl_pct

        best_ticker = max(perf, key=perf.get)
        worst_ticker = min(perf, key=perf.get)
        best_pct = perf[best_ticker]
        worst_pct = perf[worst_ticker]

        best_sign = "+" if best_pct >= 0 else ""
        worst_sign = "+" if worst_pct >= 0 else ""

        lines.extend([
            f"🏆 **Top Performer:** {best_ticker.upper()} ({best_sign}{best_pct:.1f}%)"
            f"  &nbsp;|&nbsp;  "
            f"📉 **Worst Performer:** {worst_ticker.upper()} ({worst_sign}{worst_pct:.1f}%)",
            "",
        ])

    # Quick Actions per held stock
    if portfolio:
        lines.append("### Quick Actions")
        lines.append("")
        for ticker in sorted(portfolio.keys()):
            t_upper = ticker.upper()
            buy_link = f"{base_url}?title=BUY+{ticker}+10&body=Adjust+quantity+in+the+title+then+submit"
            sell_link = f"{base_url}?title=SELL+{ticker}+5&body=Adjust+quantity+in+the+title+then+submit"
            lines.append(f"**{t_upper}**: [Buy]({buy_link}) | [Sell]({sell_link})")
        lines.append("")

    # Profile Badge
    username = trader["username"]
    badge_url = f"https://solanaleaky.github.io/GitExchange/badges/{username}.svg"
    badge_raw = f"https://raw.githubusercontent.com/SolanaLeeky/GitExchange/main/docs/badges/{username}.svg"
    lines.extend([
        "### Your Profile Badge",
        "",
        f"![GitExchange Badge]({badge_url})",
        "",
        "Add this to your GitHub profile README:",
        "",
        f"```markdown",
        f"[![GitExchange]({badge_raw})](https://github.com/SolanaLeeky/GitExchange)",
        f"```",
        "",
    ])

    return "\n".join(lines)


def format_rejection(reason: str) -> str:
    """Markdown rejection comment."""
    # Build a contextual tip based on common rejection reasons
    reason_lower = reason.lower()
    if "insufficient cash" in reason_lower or "insufficient margin" in reason_lower:
        tip = "Try a smaller quantity, or sell an existing position to free up cash."
    elif "not found" in reason_lower:
        tip = "Double-check the ticker symbol. Use `PORTFOLIO` to see what is available."
    elif "rate limit" in reason_lower:
        tip = "Take a breather. You can submit more trades in a few minutes."
    elif "duplicate" in reason_lower:
        tip = "Your previous trade is already being processed. No need to resubmit."
    elif "could not parse" in reason_lower:
        tip = "Format your issue title like: `BUY react 10` or `SELL nextjs 5`."
    elif "closed" in reason_lower:
        tip = "The market reopens after the next price update. Check back soon."
    elif "position limit" in reason_lower:
        tip = "Diversify! Spread your trades across multiple stocks to stay within limits."
    elif "account" in reason_lower and "day" in reason_lower:
        tip = "This safeguard protects the exchange. Your account will be eligible soon."
    elif "only hold" in reason_lower or "only have" in reason_lower:
        tip = "Use `PORTFOLIO` to check your current holdings before selling or covering."
    elif "delisted" in reason_lower:
        tip = "This stock has been removed from the exchange and can no longer be traded."
    else:
        tip = "Review the command format and your current portfolio, then try again."

    return (
        f"## ❌ Trade Rejected\n\n"
        f"{reason}\n\n"
        f"💡 **Tip:** {tip}\n\n"
        f"[📖 Read the trading rules](https://github.com/SolanaLeeky/GitExchange#rules)"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    start = _time.time()

    title = os.environ.get("ISSUE_TITLE", "")
    username = os.environ.get("ISSUE_USER", "")
    issue_number = int(os.environ.get("ISSUE_NUMBER", 0))

    if not title or not username:
        print("Missing ISSUE_TITLE or ISSUE_USER environment variables.")
        sys.exit(1)

    # 0. Data integrity pre-check
    errors = validate_state()
    if errors:
        print(f"WARNING: {len(errors)} data integrity issue(s):")
        for e in errors[:5]:
            print(f"  - {e}")

    # 0.5. PORTFOLIO command
    if is_portfolio_request(title):
        market = load_market()
        trader = load_trader(username)
        update_trader_stats(trader, market)
        portfolio_msg = format_portfolio(trader, market)
        post_issue_comment(issue_number, portfolio_msg)
        close_issue(issue_number)
        print(f"Portfolio view for @{username}")
        log_engine_run("trade", _time.time() - start, {"result": "portfolio_view", "user": username})
        return

    # 1. Parse
    parsed = parse_trade(title)
    if not parsed:
        msg = format_rejection(
            f"Could not parse trade: `{title}`\n\n"
            "Expected format: `BUY <ticker> <quantity>`, "
            "`SELL <ticker> <quantity>`, `SHORT <ticker> <quantity>`, "
            "`COVER <ticker> <quantity>`, or `PORTFOLIO`."
        )
        post_issue_comment(issue_number, msg)
        close_issue(issue_number)
        print(f"Rejected: invalid format '{title}'")
        return

    action, ticker, qty = parsed
    print(f"Trade: {action} {ticker} x{qty} by @{username}")

    # 2. Abuse checks
    age_error = check_account_age(username)
    if age_error:
        post_issue_comment(issue_number, format_rejection(age_error))
        close_issue(issue_number)
        print(f"Rejected (account age): {age_error}")
        log_engine_run("trade", _time.time() - start, {"result": "rejected_age", "user": username})
        return

    rate_error = check_rate_limit_user(username)
    if rate_error:
        post_issue_comment(issue_number, format_rejection(rate_error))
        close_issue(issue_number)
        print(f"Rejected (rate limit): {rate_error}")
        log_engine_run("trade", _time.time() - start, {"result": "rejected_rate", "user": username})
        return

    dup_error = check_duplicate_trade(username, action, ticker)
    if dup_error:
        post_issue_comment(issue_number, format_rejection(dup_error))
        close_issue(issue_number)
        print(f"Rejected (duplicate): {dup_error}")
        log_engine_run("trade", _time.time() - start, {"result": "rejected_dup", "user": username})
        return

    # 3. Load state
    config = load_config()
    market = load_market()
    trader = load_trader(username)

    # 4. Validate
    error = validate_trade(action, ticker, qty, trader, market, config)
    if error:
        post_issue_comment(issue_number, format_rejection(error))
        close_issue(issue_number)
        print(f"Rejected: {error}")
        log_engine_run("trade", _time.time() - start, {"result": "rejected_validation", "user": username})
        return

    # 5. Execute
    trade = execute_trade(action, ticker, qty, trader, market, config)

    # 6. Save state
    save_trader(username, trader)
    save_market(market)
    append_trade_history(trade)

    # 7. Post receipt + close issue
    receipt = format_receipt(trade, trader, market)
    post_issue_comment(issue_number, receipt)
    close_issue(issue_number)

    duration = _time.time() - start
    log_engine_run("trade", duration, {
        "result": "executed",
        "user": username,
        "action": action,
        "ticker": ticker,
        "qty": qty,
        "price": trade["price"],
    })

    print(f"Executed: {action} {qty} {ticker} @ ${trade['price']:,.2f}")
    print(f"  Cash: ${trader['cash']:,.2f} | Portfolio: ${trader['total_value']:,.2f}")


if __name__ == "__main__":
    main()
