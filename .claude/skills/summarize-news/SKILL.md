---
name: summarize-news
description: Read the dashboard's news_dump.json (and x_dump.json if present) and write a screening-summary to news_summary.md. Invoke when the user wants the AI screening summary for their Hyperliquid trading dashboard — typical triggers include "/summarize-news", "summarize today's news", "screening summary", "what does the news say", "give me the AI summary".
---

# summarize-news

You are producing the Layer 1 screening summary for the user's Hyperliquid trading dashboard. This is a discretionary trading aid — not a recommendation engine.

## Steps

1. **Read `news_dump.json`** from `D:\Bintang\RIGA\news_dump.json`.
2. **Read `x_dump.json`** from `D:\Bintang\RIGA\x_dump.json` *if it exists*. This contains recent tweets from accounts the user follows for trading signals.
3. **Check freshness.**
   - If `news_dump.json` is older than 5 minutes, tell the user the dashboard hasn't refreshed recently and stop — do not produce a stale summary.
   - If `x_dump.json` exists but is older than 6 hours, note this in the X section ("X data is stale — last scrape was Xh ago") but still use it.
   - If `x_dump.json` does not exist, skip the X section entirely — do not mention it.
4. **Produce the analysis** following the exact structure below. Use Markdown.
5. **Write** the result to `D:\Bintang\RIGA\news_summary.md`, overwriting whatever was there.
6. **Push to cloud.** If the project is a git repo with a remote, commit and push the updated file so Streamlit Cloud picks it up:
   ```powershell
   cd D:\Bintang\RIGA
   git add news_summary.md
   git commit -m "Update AI screening summary"
   git push
   ```
   If git is not set up or push fails, skip silently — the local dashboard still reads the file.
7. **Confirm** to the user in one line: e.g., "Summary written and pushed. Cloud dashboard will update in ~30s."

## Required output structure

```markdown
### Themes today
- 2-3 cross-asset themes / catalysts. One sentence each, citing the specific headline (or tweet) that motivated it.

### Per-asset bias
- **BTC** — bullish / bearish / mixed: <1-2 sentences citing specific headlines/tweets>
- **ETH** — ...
- (one bullet per asset listed in the dump's `assets` field)

### From X (followed accounts)
- (Only include this section if `x_dump.json` exists and has tweets)
- **@handle** — 1-2 sentence read of what they're posting about. Cite specific tweets if making a directional claim. Flag if they're contradicting each other or aligned.
- Distill SIGNAL, not noise — skip generic market commentary, hype, and shitposts. Surface specific calls, technical reads, on-chain observations, or catalyst flags.

### Assets called on X today
- (Only include this section if `x_dump.json` exists and has tweets)
- Flat enumeration of every ticker/commodity mentioned by tracked accounts, even if not in the user's watchlist. The user trades on Hyperliquid which has 200+ assets — they may want to add hot rotations on the fly.
- Format: `**$TICKER** — direction (long/short/watch) · @account: short context citing the specific call`
- Aggregate per-ticker if multiple accounts mention it. Note any conflicting calls.
- Skip generic mentions (e.g., "$BTC is bullish" as a throwaway). Only list tickers with a specific directional or catalyst claim.

### Worth a deeper look (Layer 2)
- `<asset/topic>: <headline or tweet> — why it matters in one line`
- (2-4 items matching Layer 2 criteria: new product, policy/regulation, global event, major institutional move, specific call from a tracked X account)

### Cross-asset signals
- Optional. Only if headlines/tweets suggest a regime shift (e.g., DXY up + SPX down → crypto pressure). Skip if nothing notable.
```

## Tone

- **Concise.** The user is scanning fast during a trading session.
- **Never recommend a trade.** Screening only. The user decides entries.
- **Cite specific headlines or tweets** when claiming a directional bias. Don't invent or paraphrase loosely.
- **Treat X accounts as opinionated traders, not oracles.** A bullish tweet is a data point, not a thesis. Note when their reads diverge from the news flow.
- If headlines/tweets are thin, stale, or contradictory, **say so plainly** rather than overreaching.
- No hedging filler. Stake a read, or say there isn't enough info — don't do "could go either way".

## The user's trading workflow (context)

- **Layer 1 — Screening:** what's interesting today (this summary lives here).
- **Layer 2 — Deep dive:** catalysts for a specific asset.
- **Layer 3 — Technical entry:** MACD, VWAP, Hyperliquid orderbook, funding rate (handled elsewhere).
