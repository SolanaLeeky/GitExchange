# Changelog

## v1.2.0 — Data Integrity Fix (2026-03-22)

### Bug 5: Duplicate tickers from inconsistent ticker derivation (CRITICAL)

**Symptom**: `CLI-Anything` repo appears twice in market.json — as `clianything` ($42.24) AND `cli-anything` ($447.56). The ghost `cli-anything` entry has `prev_price: $0` causing a phantom +0% change display.

**Root cause**: Three different ticker derivation functions across engines:
- `event_engine.py` (IPO): `repo.name.lower().replace(".", "").replace("-", "")` → strips hyphens → `clianything`
- `price_engine.py`: `repo_name.split("/")[-1].lower().replace(".", "")` → keeps hyphens → `cli-anything`
- `bootstrap.py`: same as price engine → keeps hyphens

When the price engine processed the IPO'd repo, it derived a different ticker and created a duplicate entry.

**Fix**: Created a single `ticker_from_repo()` function in `utils.py` (strips both dots and hyphens). All three engines now import and use this shared function. Cleaned corrupted market data to remove the ghost entry.

### Bug 6: IPO stocks inflate 140-348% on first price update

**Symptom**: IPO'd stocks show extreme price jumps on the next price update:
- autoresearch: $99 → $239 (+141%)
- gstack: $73 → $216 (+197%)
- paperclip: $64 → $216 (+239%)
- cli: $45 → $201 (+348%)

**Root cause**: The IPO scanner used a rough formula `(stars / max_stars) * 500` to set initial prices. The price engine uses full 5-metric weighted normalization which produces very different values. The momentum cap (±8%) does not help because the final price is `base_score * modifiers`, not `prev_price * modifiers` — the base score itself is the dominant factor.

**Fix**: Replaced the IPO scanner's rough formula with the same weighted normalization used by the price engine — normalizes all 5 metrics (stars, forks, commits, issue response, contributors) against existing stocks. IPO prices now start close to where the price engine will value them, preventing first-update spikes.

### Data cleanup

- Removed duplicate `cli-anything` ghost entry from market.json
- Removed orphaned `charts/ticker_cli-anything.svg`
- Deduplicated config.json listed_repos

---

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
