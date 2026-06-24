# RIGA — Claude Code Instructions

> 👤 **Human users:** see **[README.md](README.md)** for setup and usage. This file is targeted at Claude Code and contains protocol details for how Claude should behave when working in this repo.

A Streamlit dashboard that helps a discretionary trader screen news + watch live market data for Hyperliquid. Uses only public APIs (Hyperliquid `/info`, Yahoo Finance via `yfinance`). **No auto-execution** — the user places all trades themselves.

## How to run

```powershell
cd D:\Bintang\RIGA
.\run.ps1
```

The script auto-creates a `.venv`, installs deps, prints LAN URLs, and launches Streamlit bound to `0.0.0.0:8501` so LAN devices can connect.

## LAN access

After `run.ps1` is launched on this machine, **other devices on the same Wi-Fi / network** can reach the dashboard at `http://<this-machine-lan-ip>:8501` (the script prints the URL — e.g. `http://192.168.1.9:8501`).

**First time only:** Windows Firewall blocks inbound 8501 by default. Open an elevated PowerShell once and run:

```powershell
cd D:\Bintang\RIGA
.\setup-firewall.ps1
```

The script self-elevates via UAC and adds a `RIGA Dashboard` inbound rule for TCP 8501 (Private + Domain profiles). Remove later with `Remove-NetFirewallRule -DisplayName "RIGA Dashboard"`.

**Tailscale bonus:** if Tailscale is installed (it is on this machine), the dashboard is also reachable at `http://<tailscale-ip>:8501` from any Tailscale-connected device anywhere — no firewall changes, no port-forwarding.

## Files

- `app.py` — Streamlit dashboard (layout, fragments, fetchers)
- `hyperliquid_api.py` — REST client for Hyperliquid's `/info` endpoint
- `requirements.txt` — Python dependencies
- `run.ps1` — venv setup + launcher (LAN-bound on 0.0.0.0:8501)
- `setup-firewall.ps1` — one-time admin script to allow inbound TCP 8501
- `news_dump.json` — written by dashboard, read by Claude. Auto-generated every 60s. **Do not edit manually.**
- `news_summary.md` — written by Claude, read by dashboard. **The output of the `/summarize-news` skill.**
- `x_scraper.py` — on-demand Playwright scraper for X (Twitter) profiles. Writes `x_dump.json`.
- `x_config.json` — list of X handles to track + scraper settings. **Edit this file to add/remove handles.**
- `x_dump.json` — written by `/scrape-x`, read by `/summarize-news` and the dashboard. **Do not edit manually.**
- `browser_profile/` — persistent Playwright Chromium profile (auth cookies). **Gitignored; do not commit.**
- `.claude/skills/summarize-news/SKILL.md` — slash-command skill for generating the screening summary
- `.claude/skills/scrape-x/SKILL.md` — slash-command skill that triggers the X scraper

## The AI-summary protocol

The dashboard cannot call an LLM API directly (the user uses Claude via subscription, which doesn't expose programmatic access). Instead it uses a file-based handshake with Claude Code:

1. Dashboard writes `news_dump.json` every 60s with the latest headlines from all tracked tickers.
2. User invokes the **`/summarize-news`** skill in this Claude Code session (or asks "summarize today's news").
3. The skill reads `news_dump.json` (and `x_dump.json` if present), writes the analysis to `news_summary.md` following the structure documented in `.claude/skills/summarize-news/SKILL.md`.
4. Dashboard auto-displays `news_summary.md` (re-checks every 10s).

See the skill file for the required output structure and tone constraints.

## The X-scraper protocol

X has no free API tier for reading arbitrary timelines, so RIGA uses an on-demand Playwright browser to scrape a small set of accounts the user follows for trading signals. **On-demand only — never on a timer** (continuous polling raises ban risk).

1. One-time login: `.\.venv\Scripts\python.exe x_scraper.py --login` opens a visible browser. User logs in with their (preferably non-personal) X account. Session is persisted to `browser_profile/`.
2. User invokes **`/scrape-x`** in this Claude Code session. The skill runs `x_scraper.py`, which uses the saved profile to visit each handle in `x_config.json` and writes `x_dump.json`.
3. User invokes **`/summarize-news`** — the skill folds X tweets into the screening summary alongside Yahoo headlines.
4. Dashboard's X feed panel auto-displays `x_dump.json` contents (re-checks every 30s).

**Risk notes:**
- Scraper uses a real persistent browser profile + residential IP + low volume (2-4 profiles, on-demand) — risk of ban is low but non-zero.
- If the saved session expires, the scraper exits with a "not logged in" error — user re-runs `--login`.
- Edit `x_config.json` to change handles, lookback window, or filters. No code changes needed.

## The user's trading workflow (mental model)

The system is designed around the user's 3-layer decision process:

- **Layer 1 — Screening:** broad scan for "what's interesting today" across X accounts + Yahoo Finance news. Current implementation: live news headlines + `/summarize-news` skill.
- **Layer 2 — Deep dive:** for a candidate asset, gather news about catalysts likely to impact price (new products, policy, global events). *Not yet built.*
- **Layer 3 — Technical entry:** MACD, VWAP, Hyperliquid orderbook, funding rate. Current implementation: live orderbook + funding/OI in the Hyperliquid panel. Indicator signals not yet built.

Suggestions and features should map to a layer and reduce friction in the user's existing flow. Do not propose auto-execution or duplicate what Hyperliquid's own trading UI already does well (e.g., full TA charting).

## Panels (top to bottom)

1. **Top Movers** — auto-scans the entire Hyperliquid perp universe (vol24h ≥ $1M) and ranks gainers / losers / over-longed / over-shorted / volume leaders. 5s refresh. Matches the user's "ticker-agnostic momentum" style.
2. **Consolidation scanner** — fetches 1h candles for every perp passing the liquidity filter (parallel, 10 threads) and ranks by composite consolidation score (BB squeeze + 24-bar range/price + centeredness). 10-min refresh, 5-min internal cache. Higher score = tighter / more centered → better breakout-watch candidate. Below the ranking table, the **top 5 are rendered as 1h candlestick charts with the 24-bar consolidation zone shaded orange + dashed range lines + BB(20) bands overlay** (Plotly).
3. **Per-asset Hyperliquid live** — mark, funding, OI, top-10 orderbook + imbalance for the user's tracked list. **Multi-DEX-aware:** accepts both bare symbols (e.g. `HYPE`, main DEX) and prefixed symbols (e.g. `xyz:BRENTOIL`, builder DEX). Default: HYPE / ASTER / ZEC / TAO. Layout wraps to rows of 4 columns to handle larger watchlists. 3s refresh.
4. **TradingView chart** — embedded widget with candlesticks + MACD + VWAP by default. Asset / timeframe / symbol / theme / height all configurable. Not on a refresh timer — interactive iframe.
5. **Trade Decision (Layer 3)** — single-asset decision support. Asset dropdown (watchlist ∪ top-5 consolidation), Long/Short toggle, signal stack (1h MACD, 24h VWAP position, orderbook imbalance, funding interpretation, consolidation rank), then a position-sizing calculator (fixed % stop, R-multiple TPs, % of account risk, computes position notional + leverage + $ P&L per level). Link out to `https://app.hyperliquid.xyz/trade/{COIN}` for execution. No refresh timer — re-runs on widget interaction.
6. **Macro reference** — Yahoo prices for S&P, DXY, Gold by default. 15s refresh.
7. **AI screening summary** — displays `news_summary.md`. 10s refresh.
8. **X feed** — displays recent tweets from configured handles (`x_dump.json`). 30s refresh, scrape on demand via `/scrape-x`.
9. **News headlines** — Yahoo news per ticker + writes `news_dump.json`. 60s refresh.

## Multi-DEX architecture

Hyperliquid is a platform of multiple perp DEXs (HIP-3), not a single exchange. The main HL perp universe (`metaAndAssetCtxs` with no `dex` param) is one of nine DEXs at time of writing — others (xyz, flx, vntl, hyna, km, cash, abcd, para) list **traditional assets** (commodities, equities, FX, indices). For example `xyz:BRENTOIL`, `xyz:GOLD`, `xyz:AAPL`, `xyz:EUR`, `xyz:JP225`.

The sidebar **"Browse Hyperliquid markets"** expander loads the full multi-DEX universe (~420+ markets) classified into Crypto / Commodity / Equity / FX / Index, with filters and a multi-select picker. Picked assets flow into the per-asset live panel and the Trade Decision panel.

**Scope boundary:** Top Movers and Consolidation Scanner currently scan **main DEX only** (~60 tokens after liquidity filter). Cross-DEX assets (e.g. xyz:BRENTOIL) won't appear there yet — extending the scanners to be multi-DEX-aware is a future iteration.

**Naming convention:**
- Main DEX assets: bare symbol (`HYPE`, `ASTER`)
- Builder DEX assets: `<dex>:<symbol>` (`xyz:BRENTOIL`, `flx:OIL`, `hyna:GOLD`)
- The same conceptual asset can exist on multiple DEXs with different liquidity — compare 24h volume in the browser before picking.

## Tech notes

- Streamlit fragments (`@st.fragment(run_every=...)`) drive independent refresh rates. The TradingView chart is *not* in a fragment so the iframe doesn't reload constantly.
- All file paths in `app.py` are resolved via `Path(__file__).parent`, so the project is self-contained — no hardcoded absolute paths in the code.
- `news_dump.json` is written atomically (`.tmp` then rename) to avoid mid-write reads.
- TradingView widget pulls its own price data (typically Binance/Coinbase/Bybit). For Hyperliquid-native tokens like HYPE, the `HYPERLIQUID:HYPEUSD` symbol is the closest match — otherwise prices are venue-cross-listings and may diverge slightly from Hyperliquid's mark.
