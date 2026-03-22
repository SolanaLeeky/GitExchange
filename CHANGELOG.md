# Changelog

## v1.1.0 — Bugfix Release (2026-03-22)

### Bug 1: Trade workflow crashes — missing matplotlib (CRITICAL)

**Symptom**: Trade workflows (BUY vscode 1, BUY react 1) show red X failure. Trade executes and receipt posts, but state is never committed to git.

**Root cause**: `trade.yml` only installs `PyGithub` but the "Regenerate dashboard" step runs `render_engine.py` which imports `matplotlib`. The crash at the render step prevents the commit+push step from running, so trade data is lost.

**Fix**:
- Added `requests matplotlib` to `pip install` in `trade.yml`
- Split commit into two steps: commit trade data FIRST, then render dashboard
- Added `continue-on-error: true` to render step so trade state is always persisted even if chart generation fails

### Bug 2: IPO Scanner — invalid GitHub search query

**Symptom**: Event engine logs `422 Validation Failed: "The search contains only logical operators (AND / OR / NOT) without any search terms"`

**Root cause**: The search query used `OR` between `language:` qualifiers, but GitHub Search API only allows logical operators between text terms, not qualifiers. Query: `stars:>1000 created:>date language:python OR language:javascript...`

**Fix**: Removed the `language:` qualifiers from the search query. GitHub search for `stars:>1000 created:>date` returns all languages, which is fine since GitExchange is language-agnostic.

### Bug 3: GitHub Pages — "Could not load market data"

**Symptom**: The GitHub Pages dashboard at `solanaleaky.github.io/GitExchange/` shows "Could not load market data" with all stats showing `--`.

**Root cause**: `docs/index.html` fetched data via `../data/market.json` (relative path). GitHub Pages only serves files within the `/docs` directory — files in `/data/` are inaccessible from the Pages site.

**Fix**: Changed the fetch base URL from `..` (relative parent) to `https://raw.githubusercontent.com/SolanaLeeky/GitExchange/main` so the dashboard loads JSON data from the raw GitHub content API, which can access any file in the repo.

### Bug 4: Trade state lost on render failure

**Symptom**: When render_engine.py crashes (Bug 1), the entire workflow fails. The trade was executed (comment posted, issue closed) but the JSON state changes were never committed. Next trade starts from stale state.

**Fix**: Restructured `trade.yml` to commit trade data (`data/`) as a separate step BEFORE the render step. Even if rendering fails, the trade state is safely committed. The render step now has `continue-on-error: true`.

---

## v1.0.0 — Initial Release (2026-03-22)

- Trade engine: BUY/SELL/SHORT/COVER via GitHub issues
- Price engine: GitHub API metrics with momentum + volatility
- Event engine: dividends, IPOs, crashes, short squeezes, achievements
- Render engine: README dashboard, SVG charts, GitHub Pages
- Abuse protection: account age, rate limiting, duplicate detection
- Data integrity validation, margin calls, history rotation
- 3 GitHub Actions workflows with shared concurrency group
