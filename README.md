# GitExchange — The GitHub Stock Market

> Trade open-source repos like stocks. Prices driven by real GitHub activity.

🟢 **Market OPEN** | Total Cap: $1.40M | 5 Stocks | 1 Traders | Last Update: 2026-03-22 02:38 UTC

---

## Market Board

| Ticker | Name | Price | 24h Change | Volume | Market Cap | Trade |
|--------|------|-------|------------|--------|------------|-------|
| **VSCODE** | microsoft/vscode | $840.78 | +1.43% | 0 | $420.4K | [Buy](https://github.com/SolanaLeeky/GitExchange/issues/new?title=BUY+vscode+10&body=Adjust+quantity+in+the+title+then+submit) / [Sell](https://github.com/SolanaLeeky/GitExchange/issues/new?title=SELL+vscode+5&body=Adjust+quantity+in+the+title+then+submit) |
| **REACT** | facebook/react | $694.65 | -1.17% | 7 | $347.3K | [Buy](https://github.com/SolanaLeeky/GitExchange/issues/new?title=BUY+react+10&body=Adjust+quantity+in+the+title+then+submit) / [Sell](https://github.com/SolanaLeeky/GitExchange/issues/new?title=SELL+react+5&body=Adjust+quantity+in+the+title+then+submit) |
| **NEXTJS** | vercel/next.js | $553.43 | -2.10% | 5 | $276.7K | [Buy](https://github.com/SolanaLeeky/GitExchange/issues/new?title=BUY+nextjs+10&body=Adjust+quantity+in+the+title+then+submit) / [Sell](https://github.com/SolanaLeeky/GitExchange/issues/new?title=SELL+nextjs+5&body=Adjust+quantity+in+the+title+then+submit) |
| **SVELTE** | sveltejs/svelte | $393.19 | +2.44% | 0 | $196.6K | [Buy](https://github.com/SolanaLeeky/GitExchange/issues/new?title=BUY+svelte+10&body=Adjust+quantity+in+the+title+then+submit) / [Sell](https://github.com/SolanaLeeky/GitExchange/issues/new?title=SELL+svelte+5&body=Adjust+quantity+in+the+title+then+submit) |
| **DENO** | denoland/deno | $309.76 | +6.59% | 0 | $154.9K | [Buy](https://github.com/SolanaLeeky/GitExchange/issues/new?title=BUY+deno+10&body=Adjust+quantity+in+the+title+then+submit) / [Sell](https://github.com/SolanaLeeky/GitExchange/issues/new?title=SELL+deno+5&body=Adjust+quantity+in+the+title+then+submit) |

---

## Leaderboard

| Rank | Trader | Portfolio Value | P&L | Trades | Achievements |
|------|--------|-----------------|-----|--------|--------------|
| 🥇 | @SolanaLeeky | $9,997.23 | -$2.77 (-0.0%) | 1 |  |

---

## Price Chart (7 days)

![Market Overview](charts/market_overview.svg)

---

## Recent Trades

| Time | Trader | Action | Stock | Qty | Price | Total |
|------|--------|--------|-------|-----|-------|-------|
| 2026-03-22 03:42 | @SolanaLeeky | 📈 BUY | NEXTJS | 5 | $553.43 | $2,767.15 |

---

## How to Trade

1. **Buy** — Click a Buy link above (or open an issue titled `BUY <ticker> <quantity>`)
2. **Sell** — Click a Sell link (or `SELL <ticker> <quantity>`)
3. **Short** — Open an issue titled `SHORT <ticker> <quantity>`
4. **Cover** — Close a short with `COVER <ticker> <quantity>`

Your trade executes automatically. You'll get a receipt comment on the issue.

## Rules

| Rule | Value |
|------|-------|
| Starting cash | $10,000 |
| Max position | 40% of portfolio in one stock |
| Trading fee | 0.1% per trade |
| Short margin | 150% of position value |
| Trade size | 1–100 shares per trade |
| Price updates | Every 6 hours (GitHub API metrics) |
| Dividends | Daily — 0.5% of price to holders of repos with 50+ commits/week |

## How Prices Work

Stock prices are calculated from five GitHub metrics, updated every 6 hours:

| Metric | Weight |
|--------|--------|
| Stars | 30% |
| Commits/week | 25% |
| Forks | 15% |
| Issue response time | 15% |
| Contributors | 15% |

Plus momentum (trend-following, capped at 8%) and volatility (random ±3%).

## Market Events

- **IPO** — Trending repos with 1,000+ stars get auto-listed
- **Crash** — Archived or deleted repos go to $0, holders wiped out
- **Short Squeeze** — If short interest exceeds 60% and price rises 10%+, all shorts force-closed
- **Dividends** — Repos with 50+ weekly commits pay shareholders daily

---

*Powered by GitHub Actions. Infrastructure cost: $0.*
*Prices update every 6 hours. Market data is committed to this repo.*
