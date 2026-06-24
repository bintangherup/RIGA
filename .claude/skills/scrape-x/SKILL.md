---
name: scrape-x
description: Run the on-demand X (Twitter) scraper for the user's RIGA trading dashboard. Launches Playwright via the project's venv to fetch recent tweets from configured accounts and write x_dump.json. Invoke when the user says "/scrape-x", "fetch tweets", "scrape X", "update X feed", or "get latest tweets".
---

# scrape-x

You are triggering the on-demand X scraper for the RIGA dashboard. The user runs this manually before screening — never on a timer — to keep the bot-detection risk low.

## Steps

1. **Check prerequisites.** The browser profile must exist at `D:\Bintang\RIGA\browser_profile\`. If it doesn't, stop and tell the user:
   > "No browser profile found. Run this once in PowerShell to log in: `cd D:\Bintang\RIGA; .\.venv\Scripts\python.exe x_scraper.py --login`"

2. **Check freshness of any existing dump.** If `x_dump.json` exists and `generated_at` is less than 5 minutes old, ask the user before re-scraping — frequent runs increase ban risk.

3. **Run the scraper** via the project's venv:
   ```
   D:\Bintang\RIGA\.venv\Scripts\python.exe D:\Bintang\RIGA\x_scraper.py
   ```
   Stream the output so the user sees per-handle progress.

4. **Report results.** Read the freshly-written `x_dump.json` and summarize in one line:
   - Total tweets fetched per handle
   - Any handles that errored (with the error message)
   - Generation timestamp

5. **Push to cloud.** If the project is a git repo with a remote, commit and push the updated file so Streamlit Cloud picks it up:
   ```powershell
   cd D:\Bintang\RIGA
   git add x_dump.json
   git commit -m "Update X feed data"
   git push
   ```
   If git is not set up or push fails, skip silently — the local dashboard still reads the file.

6. **Offer next step.** If the scrape succeeded, suggest: *"Want me to run `/summarize-news` now? It'll fold these tweets into the screening summary."*

## Error handling

- **"Session is not logged in"** → tell user to run: `.\.venv\Scripts\python.exe x_scraper.py --login`
- **Playwright not installed / Chromium missing** → tell user to re-run `.\run.ps1` (it installs Chromium on first run) or manually: `.\.venv\Scripts\python.exe -m playwright install chromium`
- **No handles configured** → point user to `x_config.json` to add handles
- **Scraper hangs >60s** → kill it and report; X may be rate-limiting or DOM changed

## Important constraints

- **On-demand only.** Never schedule this or wrap it in a loop. The user invokes it before each screening session.
- **Don't scrape if a fresh dump exists** (<5 min old) without confirming. Repeated rapid scrapes look like a bot.
- **The scraper writes to `x_dump.json`** — do not edit that file manually.
