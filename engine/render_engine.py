#!/usr/bin/env python3
"""Render engine: generate README.md from template, SVG charts, and
leaderboard visuals from current market + trader data."""

import os
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from utils import (
    load_market,
    load_trader,
    list_traders,
    load_json,
    update_trader_stats,
    ROOT_DIR,
    CHARTS_DIR,
    HISTORY_DIR,
)

# GitHub repo for issue links — filled from env or fallback
REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "SolanaLeeky/GitExchange")


# ═══════════════════════════════════════════════════════════════════════════
# 1. MARKET TABLE
# ═══════════════════════════════════════════════════════════════════════════


def render_daily_movers(market: dict) -> str:
    """Return a markdown string showing the top gainer and top loser by change_pct."""
    stocks = market.get("stocks", {})
    active = {t: s for t, s in stocks.items() if s.get("market_status") != "DELISTED"}
    if not active:
        return "*No market data yet.*"

    top_gainer = max(active.items(), key=lambda x: x[1].get("change_pct", 0))
    top_loser = min(active.items(), key=lambda x: x[1].get("change_pct", 0))

    g_ticker, g_data = top_gainer
    g_pct = g_data.get("change_pct", 0)
    g_price = g_data.get("price", 0)

    l_ticker, l_data = top_loser
    l_pct = l_data.get("change_pct", 0)
    l_price = l_data.get("price", 0)

    return (
        f"\U0001f4c8 **Top Gainer**: {g_ticker.upper()} +{g_pct:.2f}% (${g_price:,.2f})"
        f" | "
        f"\U0001f4c9 **Top Loser**: {l_ticker.upper()} {l_pct:.2f}% (${l_price:,.2f})"
    )


def render_market_table(market: dict) -> str:
    """Markdown table of all stocks with Buy/Sell action links."""
    stocks = market.get("stocks", {})
    if not stocks:
        return "*No stocks listed yet.*"

    lines = [
        "| Ticker | Name | Price | 24h Change | Volume | Market Cap | Trade |",
        "|--------|------|-------|------------|--------|------------|-------|",
    ]

    sorted_stocks = sorted(stocks.items(), key=lambda x: x[1].get("market_cap", 0), reverse=True)

    for ticker, s in sorted_stocks:
        if s.get("market_status") == "DELISTED":
            continue

        price = s["price"]
        change = s.get("change_pct", 0)
        volume = s.get("volume_24h", 0)
        cap = s.get("market_cap", 0)
        name = s.get("full_name", ticker)

        # Format change with arrow emoji
        if change > 0:
            change_str = f"🟢 +{change:.2f}%"
        elif change < 0:
            change_str = f"🔴 {change:.2f}%"
        else:
            change_str = "⚪ 0.00%"

        # Format market cap
        if cap >= 1_000_000:
            cap_str = f"${cap/1_000_000:.1f}M"
        elif cap >= 1_000:
            cap_str = f"${cap/1_000:.1f}K"
        else:
            cap_str = f"${cap:.0f}"

        # Issue links (Buy, Sell, Short)
        buy_url = f"https://github.com/{REPO_SLUG}/issues/new?title=BUY+{ticker}+10&body=Adjust+quantity+in+the+title+then+submit"
        sell_url = f"https://github.com/{REPO_SLUG}/issues/new?title=SELL+{ticker}+5&body=Adjust+quantity+in+the+title+then+submit"
        short_url = f"https://github.com/{REPO_SLUG}/issues/new?title=SHORT+{ticker}+10&body=Adjust+quantity+in+the+title+then+submit"

        trade_links = f"[Buy]({buy_url}) [Sell]({sell_url}) [Short]({short_url})"

        lines.append(
            f"| **{ticker.upper()}** | {name} | ${price:,.2f} | {change_str} | {volume} | {cap_str} | {trade_links} |"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 2. LEADERBOARD
# ═══════════════════════════════════════════════════════════════════════════


def render_leaderboard(market: dict) -> str:
    """Top 20 traders by portfolio value."""
    traders = list_traders()
    if not traders:
        return "*No traders yet. Be the first to open an issue!*"

    # Load and recalculate all traders
    trader_data = []
    for username in traders:
        t = load_trader(username)
        update_trader_stats(t, market)
        trader_data.append(t)

    # Sort by total value descending
    trader_data.sort(key=lambda t: t.get("total_value", 0), reverse=True)

    lines = [
        "| Rank | Trader | Portfolio Value | P&L | Trades | Achievements |",
        "|------|--------|-----------------|-----|--------|--------------|",
    ]

    for i, t in enumerate(trader_data[:20], 1):
        username = t["username"]
        value = t.get("total_value", 0)
        pnl = t.get("pnl", 0)
        pnl_pct = t.get("pnl_pct", 0)
        trades = t.get("trade_count", 0)
        achievements = t.get("achievements", [])

        # P&L formatting
        if pnl >= 0:
            pnl_str = f"+${pnl:,.2f} (+{pnl_pct:.1f}%)"
        else:
            pnl_str = f"-${abs(pnl):,.2f} ({pnl_pct:.1f}%)"

        # Achievement badges (show up to 3)
        badge_map = {
            "first-trade": "🎯",
            "100-trades": "💯",
            "10x-return": "🚀",
            "diamond-hands": "💎",
            "paper-hands": "📄",
            "short-king": "👑",
            "diversified": "🌐",
            "whale": "🐋",
            "survivor": "🛡️",
            "ipo-hunter": "🔔",
            "contrarian": "🦊",
            "early-bird": "🐦",
        }
        badges = " ".join(badge_map.get(a, "") for a in achievements[:5] if a in badge_map)

        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, str(i))

        lines.append(
            f"| {medal} | @{username} | ${value:,.2f} | {pnl_str} | {trades} | {badges} |"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 3. RECENT TRADES
# ═══════════════════════════════════════════════════════════════════════════


def render_recent_trades() -> str:
    """Last 10 trades from history."""
    # Gather trades from most recent history files
    trades_dir = HISTORY_DIR / "trades"
    if not trades_dir.exists():
        return "*No trades yet.*"

    all_trades = []
    for path in sorted(trades_dir.glob("*.json"), reverse=True)[:3]:
        data = load_json(path)
        all_trades.extend(data.get("trades", []))

    if not all_trades:
        return "*No trades yet.*"

    # Sort by timestamp descending, take last 10
    all_trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    recent = all_trades[:10]

    lines = [
        "| Time | Trader | Action | Stock | Qty | Price | Total |",
        "|------|--------|--------|-------|-----|-------|-------|",
    ]

    for t in recent:
        ts = t.get("timestamp", "")[:16].replace("T", " ")
        user = t.get("user", "?")
        action = t.get("action", "?")
        ticker = t.get("ticker", "?").upper()
        qty = t.get("qty", 0)
        price = t.get("price", 0)
        total = t.get("total", 0)

        action_emoji = {"BUY": "📈", "SELL": "📉", "SHORT": "📉", "COVER": "📈"}.get(action, "")

        lines.append(
            f"| {ts} | @{user} | {action_emoji} {action} | {ticker} | {qty} | ${price:,.2f} | ${total:,.2f} |"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 4. MARKET STATUS BANNER
# ═══════════════════════════════════════════════════════════════════════════


def render_market_status(market: dict) -> str:
    """Status banner with total market cap and last update time."""
    status = market.get("market_status", "unknown")
    cap = market.get("total_market_cap", 0)
    updated = market.get("last_updated", "never")
    stock_count = len([s for s in market.get("stocks", {}).values() if s.get("market_status") != "DELISTED"])
    trader_count = len(list_traders())

    if cap >= 1_000_000:
        cap_str = f"${cap/1_000_000:.2f}M"
    else:
        cap_str = f"${cap:,.2f}"

    status_icon = "🟢" if status == "open" else "🔴"

    stocks_label = "Stock" if stock_count == 1 else "Stocks"
    traders_label = "Trader" if trader_count == 1 else "Traders"

    return (
        f"{status_icon} **Market {status.upper()}** | "
        f"Total Cap: {cap_str} | "
        f"{stock_count} {stocks_label} | "
        f"{trader_count} {traders_label} | "
        f"Last Update: {updated[:16].replace('T', ' ')} UTC"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 5. SVG CHART GENERATION
# ═══════════════════════════════════════════════════════════════════════════

# Dark theme for all charts
CHART_STYLE = {
    "bg": "#0d1117",
    "fg": "#c9d1d9",
    "grid": "#21262d",
    "green": "#3fb950",
    "red": "#f85149",
    "blue": "#58a6ff",
    "accent": "#1f6feb",
}


def _load_price_history(days: int = 7) -> dict[str, list[float]]:
    """Load price history for the last N days. Returns {ticker: [prices]}."""
    prices_dir = HISTORY_DIR / "prices"
    if not prices_dir.exists():
        return {}

    files = sorted(prices_dir.glob("*.json"), reverse=True)[:days]
    files.reverse()  # chronological order

    history: dict[str, list[float]] = {}
    for path in files:
        data = load_json(path)
        for snap in data.get("snapshots", []):
            for ticker, price in snap.get("prices", {}).items():
                history.setdefault(ticker, []).append(price)

    return history


def _apply_dark_theme(fig, ax):
    """Apply dark theme to matplotlib figure and axes."""
    fig.patch.set_facecolor(CHART_STYLE["bg"])
    ax.set_facecolor(CHART_STYLE["bg"])
    ax.tick_params(colors=CHART_STYLE["fg"], labelsize=8)
    ax.spines["bottom"].set_color(CHART_STYLE["grid"])
    ax.spines["left"].set_color(CHART_STYLE["grid"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, color=CHART_STYLE["grid"], linewidth=0.5, alpha=0.5)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("$%.0f"))


def generate_market_overview(market: dict) -> str | None:
    """Generate market_overview.svg — single compact chart with top 5 stocks."""
    history = _load_price_history(7)
    stocks = market.get("stocks", {})
    active = {t: s for t, s in stocks.items() if s.get("market_status") != "DELISTED"}

    if not active:
        return None

    # Top 5 by market cap
    top5 = sorted(active.items(), key=lambda x: x[1].get("market_cap", 0), reverse=True)[:5]
    line_colors = ["#58a6ff", "#3fb950", "#f85149", "#d2a8ff", "#f0883e"]

    fig, ax = plt.subplots(figsize=(8, 3.5))
    _apply_dark_theme(fig, ax)

    for i, (ticker, stock) in enumerate(top5):
        prices = history.get(ticker, [stock["price"]])
        if len(prices) < 2:
            prices = [stock.get("prev_price", stock["price"]), stock["price"]]

        color = line_colors[i % len(line_colors)]
        ax.plot(prices, color=color, linewidth=2, label=ticker.upper())

        # Price label at the end
        ax.annotate(
            f"${prices[-1]:,.0f}",
            xy=(len(prices) - 1, prices[-1]),
            fontsize=8, color=color, fontweight="bold",
            ha="left", va="center",
            xytext=(5, 0), textcoords="offset points",
        )

    ax.legend(loc="upper right", fontsize=8, facecolor=CHART_STYLE["bg"],
              edgecolor=CHART_STYLE["grid"], labelcolor=CHART_STYLE["fg"])
    ax.set_title("Market Overview — Top 5", color=CHART_STYLE["fg"], fontsize=12, fontweight="bold")
    fig.tight_layout()

    path = CHARTS_DIR / "market_overview.svg"
    fig.savefig(path, format="svg", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return str(path)


def generate_ticker_chart(ticker: str, stock: dict) -> str | None:
    """Generate a per-stock sparkline SVG."""
    history = _load_price_history(30)
    prices = history.get(ticker, [stock.get("prev_price", stock["price"]), stock["price"]])
    if len(prices) < 2:
        prices = [stock.get("prev_price", stock["price"]), stock["price"]]

    fig, ax = plt.subplots(figsize=(6, 2.5))
    color = CHART_STYLE["green"] if prices[-1] >= prices[0] else CHART_STYLE["red"]

    ax.plot(prices, color=color, linewidth=2)
    ax.fill_between(range(len(prices)), prices, alpha=0.15, color=color)
    _apply_dark_theme(fig, ax)

    name = stock.get("full_name", ticker)
    change = stock.get("change_pct", 0)
    change_str = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"
    ax.set_title(f"{ticker.upper()} ({name}) — ${prices[-1]:,.2f} ({change_str})",
                 color=CHART_STYLE["fg"], fontsize=11, fontweight="bold")

    fig.tight_layout()
    path = CHARTS_DIR / f"ticker_{ticker}.svg"
    fig.savefig(path, format="svg", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return str(path)


def generate_leaderboard_chart(market: dict) -> str | None:
    """Horizontal bar chart of top 10 traders."""
    traders = list_traders()
    if not traders:
        return None

    trader_data = []
    for username in traders:
        t = load_trader(username)
        update_trader_stats(t, market)
        trader_data.append(t)

    trader_data.sort(key=lambda t: t.get("total_value", 0), reverse=True)
    top = trader_data[:10]

    if not top:
        return None

    fig, ax = plt.subplots(figsize=(8, max(3, len(top) * 0.6)))

    names = [f"@{t['username']}" for t in reversed(top)]
    values = [t.get("total_value", 0) for t in reversed(top)]
    colors = [CHART_STYLE["green"] if t.get("pnl", 0) >= 0 else CHART_STYLE["red"] for t in reversed(top)]

    bars = ax.barh(names, values, color=colors, height=0.6)
    _apply_dark_theme(fig, ax)
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("$%.0f"))

    # Value labels
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                f"${val:,.0f}", color=CHART_STYLE["fg"], va="center", fontsize=9)

    ax.set_title("Top Traders by Portfolio Value", color=CHART_STYLE["fg"], fontsize=13, fontweight="bold")
    ax.tick_params(axis="y", colors=CHART_STYLE["fg"], labelsize=10)

    fig.tight_layout()
    path = CHARTS_DIR / "leaderboard.svg"
    fig.savefig(path, format="svg", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return str(path)


def generate_all_charts(market: dict) -> None:
    """Generate all SVG charts."""
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    print("  Market overview...", end=" ")
    result = generate_market_overview(market)
    print("done" if result else "skipped")

    print("  Leaderboard...", end=" ")
    result = generate_leaderboard_chart(market)
    print("done" if result else "skipped (no traders)")

    stocks = market.get("stocks", {})
    for ticker, stock in stocks.items():
        if stock.get("market_status") == "DELISTED":
            continue
        print(f"  Ticker chart: {ticker}...", end=" ")
        generate_ticker_chart(ticker, stock)
        print("done")


# ═══════════════════════════════════════════════════════════════════════════
# 6. DASHBOARD DATA (for GitHub Pages)
# ═══════════════════════════════════════════════════════════════════════════


def generate_dashboard_data(market: dict) -> None:
    """Write docs/dashboard.json with trader count and leaderboard for Pages."""
    from utils import save_json

    traders = list_traders()
    leaderboard = []
    for username in traders:
        t = load_trader(username)
        update_trader_stats(t, market)
        leaderboard.append({
            "username": t["username"],
            "total_value": t["total_value"],
            "pnl": t["pnl"],
            "pnl_pct": t["pnl_pct"],
            "trade_count": t.get("trade_count", 0),
            "achievements": t.get("achievements", []),
        })

    leaderboard.sort(key=lambda x: x["total_value"], reverse=True)

    docs_dir = ROOT_DIR / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    save_json(docs_dir / "dashboard.json", {
        "trader_count": len(traders),
        "leaderboard": leaderboard[:20],
    })
    print(f"  Dashboard data written ({len(traders)} traders)")


# ═══════════════════════════════════════════════════════════════════════════
# 7. PROFILE BADGES (per-trader SVG)
# ═══════════════════════════════════════════════════════════════════════════


def _badge_svg(username: str, rank: int, pnl_pct: float, total_value: float, trade_count: int) -> str:
    """Generate a shields.io-style SVG badge for a trader."""
    # Colors
    if pnl_pct >= 50:
        pnl_color = "#3fb950"  # green
    elif pnl_pct >= 0:
        pnl_color = "#58a6ff"  # blue
    elif pnl_pct >= -20:
        pnl_color = "#f0883e"  # orange
    else:
        pnl_color = "#f85149"  # red

    rank_label = f"#{rank}" if rank <= 999 else "#999+"
    pnl_sign = "+" if pnl_pct >= 0 else ""
    pnl_label = f"{pnl_sign}{pnl_pct:.1f}%"

    if total_value >= 1_000_000:
        val_label = f"${total_value/1_000_000:.1f}M"
    elif total_value >= 1_000:
        val_label = f"${total_value/1_000:.1f}K"
    else:
        val_label = f"${total_value:.0f}"

    # Badge dimensions
    left_text = f"GitExchange {rank_label}"
    right_text = f"{pnl_label}  {val_label}  {trade_count} trades"
    left_width = len(left_text) * 6.5 + 16
    right_width = len(right_text) * 6.2 + 16
    total_width = left_width + right_width

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total_width:.0f}" height="22" role="img" aria-label="{username} on GitExchange">
  <title>{username} — Rank {rank_label} | P&amp;L {pnl_label} | {val_label} | {trade_count} trades</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{total_width:.0f}" height="22" rx="4" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{left_width:.0f}" height="22" fill="#24292f"/>
    <rect x="{left_width:.0f}" width="{right_width:.0f}" height="22" fill="{pnl_color}"/>
    <rect width="{total_width:.0f}" height="22" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{left_width/2:.0f}" y="15" fill="#010101" fill-opacity=".3">{left_text}</text>
    <text x="{left_width/2:.0f}" y="14">{left_text}</text>
    <text x="{left_width + right_width/2:.0f}" y="15" fill="#010101" fill-opacity=".3">{right_text}</text>
    <text x="{left_width + right_width/2:.0f}" y="14">{right_text}</text>
  </g>
</svg>'''


def _badge_json(rank: int, pnl_pct: float, total_value: float, trade_count: int) -> dict:
    """Generate a shields.io endpoint JSON for a trader badge."""
    if pnl_pct >= 50:
        color = "brightgreen"
    elif pnl_pct >= 0:
        color = "blue"
    elif pnl_pct >= -20:
        color = "orange"
    else:
        color = "red"

    rank_label = f"#{rank}" if rank <= 999 else "#999+"
    pnl_sign = "+" if pnl_pct >= 0 else ""

    if total_value >= 1_000_000:
        val_label = f"${total_value/1_000_000:.1f}M"
    elif total_value >= 1_000:
        val_label = f"${total_value/1_000:.1f}K"
    else:
        val_label = f"${total_value:.0f}"

    return {
        "schemaVersion": 1,
        "label": f"GitExchange {rank_label}",
        "message": f"{pnl_sign}{pnl_pct:.1f}% | {val_label} | {trade_count} trades",
        "color": color,
        "namedLogo": "github",
        "logoColor": "white",
    }


def generate_profile_badges(market: dict) -> int:
    """Generate SVG + JSON badges for all traders. Returns count generated."""
    from utils import save_json as _save_json

    badges_dir = ROOT_DIR / "docs" / "badges"
    badges_dir.mkdir(parents=True, exist_ok=True)

    traders = list_traders()
    if not traders:
        return 0

    # Build ranked list
    trader_data = []
    for username in traders:
        t = load_trader(username)
        update_trader_stats(t, market)
        trader_data.append(t)

    trader_data.sort(key=lambda t: t.get("total_value", 0), reverse=True)

    count = 0
    for rank, t in enumerate(trader_data, 1):
        username = t["username"]
        pnl_pct = t.get("pnl_pct", 0)
        total_value = t.get("total_value", 0)
        trade_count = t.get("trade_count", 0)

        # SVG badge (for direct embedding)
        svg = _badge_svg(
            username=username,
            rank=rank,
            pnl_pct=pnl_pct,
            total_value=total_value,
            trade_count=trade_count,
        )
        (badges_dir / f"{username}.svg").write_text(svg, encoding="utf-8")

        # JSON badge (for shields.io endpoint — works with GitHub camo proxy)
        badge_data = _badge_json(rank, pnl_pct, total_value, trade_count)
        _save_json(badges_dir / f"{username}.json", badge_data)

        count += 1

    return count


# ═══════════════════════════════════════════════════════════════════════════
# 8. README ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════


def render_readme(market: dict) -> None:
    """Read README.template, replace placeholders, write README.md."""
    template_path = ROOT_DIR / "README.template"
    output_path = ROOT_DIR / "README.md"

    if not template_path.exists():
        print("  WARNING: README.template not found, skipping README generation.")
        return

    template = template_path.read_text(encoding="utf-8")

    # Build price chart section
    overview_path = CHARTS_DIR / "market_overview.svg"
    if overview_path.exists():
        price_chart = f"![Market Overview](charts/market_overview.svg)"
    else:
        price_chart = "*Chart will appear after the first price update.*"

    replacements = {
        "<!-- MARKET_STATUS -->": render_market_status(market),
        "<!-- MARKET_TABLE -->": render_market_table(market),
        "<!-- LEADERBOARD -->": render_leaderboard(market),
        "<!-- PRICE_CHART -->": price_chart,
        "<!-- RECENT_TRADES -->": render_recent_trades(),
        "<!-- DAILY_MOVERS -->": render_daily_movers(market),
    }

    readme = template
    for placeholder, content in replacements.items():
        readme = readme.replace(placeholder, content)

    output_path.write_text(readme, encoding="utf-8")
    print(f"  README.md written ({len(readme)} chars)")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════


def main():
    market = load_market()
    if not market:
        print("No market.json found. Run bootstrap.py first.")
        return

    print("Render engine")
    print("=" * 40)

    print("\n[1/4] Generating charts...")
    generate_all_charts(market)

    print("\n[2/4] Generating README...")
    render_readme(market)

    print("\n[3/4] Generating dashboard data...")
    generate_dashboard_data(market)

    print("\n[4/4] Generating profile badges...")
    badge_count = generate_profile_badges(market)
    print(f"  {badge_count} badge(s) generated")

    print("\nDone.")


if __name__ == "__main__":
    main()
