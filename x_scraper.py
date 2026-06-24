"""X (Twitter) scraper for RIGA dashboard.

On-demand scraper that uses a persistent Playwright browser profile to read
tweets from a small set of configured accounts. Writes results to x_dump.json
in the same handshake pattern as news_dump.json.

Usage:
    python x_scraper.py --login    # First-time setup: open browser, log in, close
    python x_scraper.py            # Scrape configured handles, write x_dump.json
    python x_scraper.py --headed   # Scrape with visible browser (debugging)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows so unicode characters in logs don't crash.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

PROJECT_DIR = Path(__file__).parent
CONFIG_PATH = PROJECT_DIR / "x_config.json"
PROFILE_DIR = PROJECT_DIR / "browser_profile"
DUMP_PATH = PROJECT_DIR / "x_dump.json"
DUMP_TMP = PROJECT_DIR / "x_dump.json.tmp"
SNAPSHOT_DIR = PROJECT_DIR / "x_snapshots"

# Stealth init: hide the most obvious automation tells before any page loads.
# This script runs in every new document context — must be set before navigation.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin' },
        { name: 'Chrome PDF Viewer' },
        { name: 'Native Client' }
    ]
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""


def _launch_context(p, headless: bool):
    """Launch persistent context using real Chrome (channel='chrome') for proper fingerprint."""
    PROFILE_DIR.mkdir(exist_ok=True)
    ctx = p.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome",  # use installed Chrome, not bundled Chromium-for-Testing
        headless=headless,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        timezone_id="Asia/Jakarta",
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx.add_init_script(STEALTH_INIT_SCRIPT)
    return ctx


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing config: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def is_logged_in(page: Page) -> bool:
    """Quick heuristic: logged-in users see a Home link in the nav."""
    try:
        page.wait_for_selector('a[href="/home"]', timeout=5000)
        return True
    except PlaywrightTimeoutError:
        return False


def run_login() -> int:
    """Open a visible browser so the user can log in. Profile persists for next run."""
    print(f"Opening browser. Profile will be saved to: {PROFILE_DIR}")
    print("Log in with your X account, then close the browser window when done.")
    with sync_playwright() as p:
        ctx = _launch_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/login")
        # Wait until the browser is closed by the user
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()
    print("Login session saved. You can now run: python x_scraper.py")
    return 0


def parse_tweet(article) -> dict | None:
    """Extract tweet fields from a tweet <article> element.

    Distinguishes the main tweet text from a quoted tweet's text by checking
    whether the <div data-testid="tweetText"> sits inside a [role="link"]
    wrapper (which X uses to make the quoted tweet card clickable).
    """
    try:
        # Detect social context (Pinned / reposted by ...)
        social_ctx_el = article.query_selector('[data-testid="socialContext"]')
        social_ctx = social_ctx_el.inner_text().strip() if social_ctx_el else ""
        is_pinned = "Pinned" in social_ctx
        is_retweet = "reposted" in social_ctx.lower()

        # Timestamp (the first <time> is always the main tweet's timestamp)
        time_el = article.query_selector("time")
        if not time_el:
            return None
        iso_ts = time_el.get_attribute("datetime")
        if not iso_ts:
            return None

        # Permalink
        link_el = article.query_selector('a[href*="/status/"]')
        permalink = link_el.get_attribute("href") if link_el else ""
        if permalink and not permalink.startswith("http"):
            permalink = f"https://x.com{permalink}"

        # Tweet text — separate main from quoted-tweet
        text = ""
        quote_text = ""
        quote_author = ""
        for el in article.query_selector_all('[data-testid="tweetText"]'):
            is_in_quote = bool(el.evaluate('(n) => !!n.closest(\'[role="link"]\')'))
            if is_in_quote:
                if not quote_text:
                    quote_text = el.inner_text().strip()
            else:
                if not text:
                    text = el.inner_text().strip()
        if quote_text:
            qa = article.query_selector('[role="link"] [data-testid="User-Name"]')
            if qa:
                # Author block contains display name + @handle on separate lines
                lines = [l for l in qa.inner_text().split("\n") if l.strip()]
                for l in lines:
                    if l.startswith("@"):
                        quote_author = l.strip()
                        break

        # Engagement counts
        def _stat(testid: str) -> str:
            el = article.query_selector(f'[data-testid="{testid}"]')
            if not el:
                return ""
            return (el.get_attribute("aria-label") or "").strip()

        # Media flag — has a photo or video attached
        has_media = bool(
            article.query_selector('[data-testid="tweetPhoto"]')
            or article.query_selector('[data-testid="videoPlayer"]')
        )

        return {
            "text": text,
            "quote_text": quote_text,
            "quote_author": quote_author,
            "timestamp": iso_ts,
            "permalink": permalink,
            "is_pinned": is_pinned,
            "is_retweet": is_retweet,
            "has_media": has_media,
            "social_context": social_ctx,
            "reply_label": _stat("reply"),
            "retweet_label": _stat("retweet"),
            "like_label": _stat("like"),
            "snapshot": None,  # filled in by scrape_handle when relevant
        }
    except Exception as e:
        print(f"  parse error: {e}", file=sys.stderr)
        return None


def _snapshot_article(article, handle: str, tweet_id: str) -> str | None:
    """Screenshot a tweet's <article> element. Returns relative path for the dump, or None on failure."""
    try:
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        fname = f"{handle}_{tweet_id}.png"
        out = SNAPSHOT_DIR / fname
        # Scroll the article into view so its media has loaded before snapping
        article.scroll_into_view_if_needed(timeout=3000)
        article.screenshot(path=str(out), timeout=5000)
        return f"x_snapshots/{fname}"
    except Exception as e:
        print(f"  snapshot failed for {handle}/{tweet_id}: {e}", file=sys.stderr)
        return None


def scrape_handle(page: Page, handle: str, lookback_hours: int, max_tweets: int) -> list[dict]:
    """Scrape one handle's profile page. Returns list of parsed tweets (no filtering yet)."""
    url = f"https://x.com/{handle}"
    print(f"  -> {url}")
    page.goto(url, wait_until="domcontentloaded")

    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
    except PlaywrightTimeoutError:
        # Could be a login wall, a private/suspended account, or a slow load
        if not is_logged_in(page):
            raise RuntimeError("Session is not logged in. Run: python x_scraper.py --login")
        print(f"  no tweets found for @{handle} (private / suspended / empty?)")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    collected: dict[str, dict] = {}  # permalink -> tweet (dedup across scrolls)
    stalls = 0
    last_count = 0

    for _ in range(12):  # max 12 scrolls — usually finds 24h of tweets well before this
        articles = page.query_selector_all('article[data-testid="tweet"]')
        for art in articles:
            tw = parse_tweet(art)
            if not tw or not tw["permalink"]:
                continue
            if tw["permalink"] in collected:
                continue
            # Snapshot when we have no readable text — covers media-only posts,
            # subscription-locked tweets ("Subscribe to unlock"), and other opaque cases.
            if not tw["text"].strip() and not tw["quote_text"].strip():
                tweet_id = tw["permalink"].rsplit("/", 1)[-1]
                tw["snapshot"] = _snapshot_article(art, handle, tweet_id)
            collected[tw["permalink"]] = tw

        # Stop if we've gone past the cutoff (oldest non-pinned tweet is older than window)
        non_pinned = [t for t in collected.values() if not t["is_pinned"]]
        if non_pinned:
            oldest = min(datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00")) for t in non_pinned)
            if oldest < cutoff:
                break
        if len(collected) >= max_tweets + 5:  # +5 buffer for pinned
            break

        # Stall detection
        if len(collected) == last_count:
            stalls += 1
            if stalls >= 3:
                break
        else:
            stalls = 0
        last_count = len(collected)

        page.mouse.wheel(0, 3000)
        time.sleep(random.uniform(0.8, 1.6))

    return list(collected.values())


def filter_tweets(raw: list[dict], lookback_hours: int, include_retweets: bool, max_per_handle: int) -> list[dict]:
    """Apply lookback window + retweet/reply filters."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    out = []
    for tw in raw:
        if tw["is_pinned"]:
            continue  # pinned tweets are often old; ignore
        if tw["is_retweet"] and not include_retweets:
            continue
        try:
            ts = datetime.fromisoformat(tw["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        out.append(tw)
    # Newest first
    out.sort(key=lambda t: t["timestamp"], reverse=True)
    return out[:max_per_handle]


def atomic_write(path: Path, tmp: Path, data: dict) -> None:
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def run_scrape(headed: bool = False) -> int:
    cfg = load_config()
    handles: list[str] = cfg.get("handles", [])
    lookback = int(cfg.get("lookback_hours", 24))
    include_rt = bool(cfg.get("include_retweets", True))
    max_per_handle = int(cfg.get("max_tweets_per_handle", 30))

    if not handles:
        print("No handles configured in x_config.json")
        return 1
    if not PROFILE_DIR.exists():
        print(f"No browser profile found at {PROFILE_DIR}")
        print("Run first: python x_scraper.py --login")
        return 2

    # Reset snapshot dir each scrape — only the latest run's images need to exist
    if SNAPSHOT_DIR.exists():
        for f in SNAPSHOT_DIR.glob("*.png"):
            try:
                f.unlink()
            except Exception:
                pass

    print(f"Scraping {len(handles)} handles, lookback={lookback}h, headed={headed}")
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    with sync_playwright() as p:
        ctx = _launch_context(p, headless=not headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for i, handle in enumerate(handles):
            try:
                raw = scrape_handle(page, handle, lookback, max_per_handle)
                filtered = filter_tweets(raw, lookback, include_rt, max_per_handle)
                results[handle] = filtered
                print(f"  @{handle}: {len(filtered)} tweets in last {lookback}h")
            except Exception as e:
                errors[handle] = str(e)
                results[handle] = []
                print(f"  @{handle}: ERROR — {e}", file=sys.stderr)
            # Polite delay between handles
            if i < len(handles) - 1:
                time.sleep(random.uniform(3.0, 6.0))
        ctx.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback,
        "include_retweets": include_rt,
        "handles": handles,
        "tweets": results,
        "errors": errors,
    }
    atomic_write(DUMP_PATH, DUMP_TMP, payload)
    print(f"\nWrote {DUMP_PATH}")
    total = sum(len(v) for v in results.values())
    print(f"Total tweets: {total}")
    return 0 if not errors else 3


def main() -> int:
    ap = argparse.ArgumentParser(description="X scraper for RIGA dashboard")
    ap.add_argument("--login", action="store_true", help="Open browser for one-time login")
    ap.add_argument("--headed", action="store_true", help="Show browser during scrape (debugging)")
    args = ap.parse_args()
    if args.login:
        return run_login()
    return run_scrape(headed=args.headed)


if __name__ == "__main__":
    sys.exit(main())
