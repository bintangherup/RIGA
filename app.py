import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

WIB = timezone(timedelta(hours=7))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

import hyperliquid_api as hl

st.set_page_config(page_title="Hyperliquid Trading Dashboard", layout="wide")

st.markdown("""<style>
@media (max-width: 768px) {
    /* Stack columns vertically on mobile */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 100% !important;
    }
    /* Compact metrics */
    [data-testid="stMetric"] {
        padding: 0.3rem 0 !important;
    }
    /* Smaller title */
    h1 { font-size: 1.4rem !important; }
    h2, [data-testid="stSubheader"] { font-size: 1.1rem !important; }
    /* Tighter padding */
    .block-container { padding: 0.5rem 0.8rem !important; }
    section[data-testid="stSidebar"] { min-width: 260px !important; }
    /* Dataframes scroll horizontally */
    [data-testid="stDataFrame"] { overflow-x: auto !important; }
}
/* 2-col grid for tablets (signal stack, trade plan) */
@media (min-width: 481px) and (max-width: 768px) {
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 48% !important;
        min-width: 48% !important;
    }
}
/* Phone portrait: always single column */
@media (max-width: 480px) {
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 100% !important;
        min-width: 100% !important;
    }
}
</style>""", unsafe_allow_html=True)

DEFAULT_ASSETS = ["HYPE", "ASTER", "ZEC", "TAO"]
DEFAULT_MACRO = ["^GSPC", "DX-Y.NYB", "GC=F"]  # S&P 500, DXY, Gold futures
BOOK_DEPTH = 10
MOVERS_MIN_VOL_USD = 1_000_000  # filter illiquid noise from top-movers ranking
MOVERS_TOP_N = 5

TV_TIMEFRAMES = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1D": "D",
}

TV_DEFAULT_STUDIES = ["MACD@tv-basicstudies", "VWAP@tv-basicstudies"]

# Hyperliquid doesn't have a TradingView data-feed prefix, so we map to whichever
# external venue has the deepest book + a TradingView listing for each token.
TV_SYMBOL_OVERRIDES = {
    "HYPE": "BYBIT:HYPEUSDT.P",
    "ASTER": "MEXC:ASTERUSDT",
    "TAO": "BINANCE:TAOUSDT.P",
    "ZEC": "BINANCE:ZECUSDT.P",
}

# Backup symbols to suggest if the primary doesn't load
TV_SYMBOL_ALTERNATIVES = {
    "HYPE": ["MEXC:HYPEUSDT", "KUCOIN:HYPEUSDT", "BYBIT:HYPEUSDT"],
    "ASTER": ["BYBIT:ASTERUSDT.P", "KUCOIN:ASTERUSDT", "GATEIO:ASTERUSDT"],
    "TAO": ["BYBIT:TAOUSDT.P", "MEXC:TAOUSDT", "KUCOIN:TAOUSDT"],
    "ZEC": ["COINBASE:ZECUSD", "KRAKEN:ZECUSD", "BYBIT:ZECUSDT.P"],
    "BTC": ["COINBASE:BTCUSD", "KRAKEN:BTCUSD", "BINANCE:BTCUSDT"],
    "ETH": ["COINBASE:ETHUSD", "KRAKEN:ETHUSD", "BINANCE:ETHUSDT"],
    "SOL": ["COINBASE:SOLUSD", "BINANCE:SOLUSDT", "KRAKEN:SOLUSD"],
}

# Tokens for which Binance perps are the deepest-liquidity TradingView listing
TV_BINANCE_PERP = {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "DOT"}


def _guess_tv_symbol(asset: str) -> str:
    if asset in TV_SYMBOL_OVERRIDES:
        return TV_SYMBOL_OVERRIDES[asset]
    if asset in TV_BINANCE_PERP:
        return f"BINANCE:{asset}USDT.P"
    return f"BINANCE:{asset}USDT"

PROJECT_DIR = Path(__file__).parent
NEWS_DUMP_PATH = PROJECT_DIR / "news_dump.json"
NEWS_SUMMARY_PATH = PROJECT_DIR / "news_summary.md"
X_DUMP_PATH = PROJECT_DIR / "x_dump.json"


def write_news_dump(assets: list[str], macro: list[str], items_by_ticker: dict) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assets": assets,
        "macro": macro,
        "headlines": items_by_ticker,
    }
    tmp = NEWS_DUMP_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(NEWS_DUMP_PATH)


def read_summary() -> tuple[str, datetime] | None:
    if not NEWS_SUMMARY_PATH.exists():
        return None
    text = NEWS_SUMMARY_PATH.read_text(encoding="utf-8")
    mtime = datetime.fromtimestamp(NEWS_SUMMARY_PATH.stat().st_mtime, tz=timezone.utc)
    return text, mtime


def read_x_dump() -> dict | None:
    if not X_DUMP_PATH.exists():
        return None
    try:
        return json.loads(X_DUMP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


COMMODITY_NAMES = {"BRENTOIL", "CL", "GOLD", "SILVER", "COPPER", "CORN", "WHEAT", "NATGAS",
                   "ALUMINIUM", "OIL", "USOIL", "GOLDJM", "SILVERJM", "PAXG", "XAUT0", "XAUM", "NUCLEAR"}
FX_NAMES = {"DXY", "EUR", "GBP", "JPY", "KRW", "CNY", "AUD", "CAD", "USD", "CHF"}
INDEX_NAMES = {"JP225", "KR200", "IBOV", "VIX", "EWY", "EWJ", "EWZ", "EWT", "SPX", "NDX", "DJI", "KOSPI"}


def _classify_asset(canonical: str, dex: str) -> str:
    symbol = canonical.split(":", 1)[1] if ":" in canonical else canonical
    sym_u = symbol.upper()
    if sym_u in COMMODITY_NAMES:
        return "Commodity"
    if sym_u in FX_NAMES:
        return "FX"
    if sym_u in INDEX_NAMES:
        return "Index"
    if dex == "main":
        return "Crypto"
    # non-main DEX + not commodity/fx/index → assume equity (xyz lists US/global stocks)
    return "Equity"


# ---------- cached fetchers ----------

@st.cache_data(ttl=2, show_spinner=False)
def get_hl_contexts(dex: str | None = None) -> dict:
    return hl.fetch_asset_contexts(dex=dex)


def get_context(canonical: str) -> dict | None:
    """Live context for any asset, main or cross-DEX. Uses dex prefix in `canonical`."""
    if ":" in canonical:
        dex_name, _ = canonical.split(":", 1)
        ctxs = get_hl_contexts(dex=dex_name)
    else:
        ctxs = get_hl_contexts(dex=None)
    return ctxs.get(canonical)


def resolve_asset(name: str, universe: dict) -> str | None:
    """Resolve a typed asset name (any case, with or without dex prefix) to its canonical form
    by searching the full multi-DEX universe.

    - 'brentoil' / 'BRENTOIL' / 'xyz:brentoil' → 'xyz:BRENTOIL'
    - 'hype' / 'HYPE' → 'HYPE'
    - Unknown → None
    """
    if not name or not universe:
        return None
    name = name.strip()

    # Direct hit (case-sensitive)
    if name in universe:
        return name

    # Try with symbol part uppercased
    if ":" in name:
        dex, sym = name.split(":", 1)
        candidate = f"{dex.lower()}:{sym.upper()}"
        if candidate in universe:
            return candidate
    else:
        upper = name.upper()
        if upper in universe:
            return upper
        # Search across all DEXs for a matching symbol
        matches = [k for k, v in universe.items() if v["symbol"].upper() == upper]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Prefer main DEX, then by highest 24h volume
            matches.sort(key=lambda k: (universe[k]["dex"] != "main", -universe[k]["dayNtlVlm"]))
            return matches[0]
    return None


@st.cache_data(ttl=300, show_spinner=False)
def get_full_universe() -> dict[str, dict]:
    """Union of all DEXs' assets. Used by the sidebar browser. 5-min TTL (stale prices acceptable for picking)."""
    out: dict[str, dict] = {}
    # Main DEX
    for name, ctx in get_hl_contexts(dex=None).items():
        out[name] = {**ctx, "dex": "main", "symbol": name, "class": _classify_asset(name, "main")}
    # Builder DEXs
    try:
        dexs = hl.fetch_perp_dexs()
    except Exception:
        dexs = []
    for d in dexs:
        if d is None:
            continue
        dex_name = d.get("name")
        if not dex_name:
            continue
        try:
            for name, ctx in get_hl_contexts(dex=dex_name).items():
                symbol = name.split(":", 1)[1] if ":" in name else name
                out[name] = {**ctx, "dex": dex_name, "symbol": symbol, "class": _classify_asset(name, dex_name)}
        except Exception:
            continue
    return out


@st.cache_data(ttl=2, show_spinner=False)
def get_hl_book(coin: str) -> dict:
    return hl.fetch_l2_book(coin)


@st.cache_data(ttl=15, show_spinner=False)
def get_yahoo_quote(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    fi = t.fast_info
    hist = t.history(period="1d", interval="5m")
    last = float(hist["Close"].iloc[-1]) if not hist.empty else float(fi.last_price)
    prev = float(fi.previous_close) if fi.previous_close else last
    change = last - prev
    pct = (change / prev * 100) if prev else 0.0
    return {"ticker": ticker, "price": last, "change": change, "pct": pct, "history": hist}


@st.cache_data(ttl=60, show_spinner=False)
def get_yahoo_news(ticker: str) -> list[dict]:
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []
    items = []
    for n in raw[:8]:
        content = n.get("content") or n
        title = content.get("title") or n.get("title")
        if not title:
            continue
        provider = (content.get("provider") or {}).get("displayName") if isinstance(content.get("provider"), dict) else n.get("publisher")
        link = (content.get("canonicalUrl") or {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else n.get("link")
        published = content.get("pubDate") or content.get("displayTime")
        items.append({"title": title, "publisher": provider, "link": link, "published": published})
    return items


# ---------- fragments (independent refresh rates) ----------

def _rank_movers(ctxs: dict) -> dict:
    """Compute top movers across all Hyperliquid perps, filtered by minimum volume."""
    rows = []
    for coin, ctx in ctxs.items():
        vol = ctx["dayNtlVlm"]
        if vol < MOVERS_MIN_VOL_USD:
            continue
        prev = ctx["prevDayPx"]
        if not prev:
            continue
        rows.append({
            "coin": coin,
            "mark": ctx["markPx"],
            "pct_24h": (ctx["markPx"] - prev) / prev * 100,
            "funding_hr": ctx["funding"] * 100,
            "oi_usd": ctx["openInterest"] * ctx["markPx"],
            "vol24h_usd": vol,
        })
    return {
        "gainers": sorted(rows, key=lambda r: r["pct_24h"], reverse=True)[:MOVERS_TOP_N],
        "losers": sorted(rows, key=lambda r: r["pct_24h"])[:MOVERS_TOP_N],
        "over_longed": sorted(rows, key=lambda r: r["funding_hr"], reverse=True)[:MOVERS_TOP_N],
        "over_shorted": sorted(rows, key=lambda r: r["funding_hr"])[:MOVERS_TOP_N],
        "volume_leaders": sorted(rows, key=lambda r: r["vol24h_usd"], reverse=True)[:MOVERS_TOP_N],
    }


def _movers_table(rows: list[dict], pct_col_label: str = "24h %") -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Coin": r["coin"],
            pct_col_label: f"{r['pct_24h']:+.2f}%",
            "Mark": f"${r['mark']:,.4f}".rstrip("0").rstrip("."),
            "Fund/hr": f"{r['funding_hr']:+.4f}%",
            "OI ($M)": f"{r['oi_usd']/1e6:,.1f}",
            "Vol24h ($M)": f"{r['vol24h_usd']/1e6:,.1f}",
        }
        for r in rows
    ])


@st.cache_data(ttl=300, show_spinner=False)
def get_candles_1h(coin: str, lookback_hours: int = 168) -> list[dict]:
    """Cached per-coin 1h candle fetch — shared by the scanner and chart panel."""
    return hl.fetch_candles(coin, "1h", lookback_hours)


def _consolidation_score(candles: list[dict]) -> dict | None:
    """Composite score on 1h candles: BB-width percentile + 24-bar range/price + centeredness.

    Higher score = tighter, lower-vol, more centered → better breakout-watch candidate.
    """
    if len(candles) < 30:
        return None
    closes = pd.Series([float(c["c"]) for c in candles])
    highs = pd.Series([float(c["h"]) for c in candles])
    lows = pd.Series([float(c["l"]) for c in candles])

    # BB(20) width = (upper - lower) / middle = (4 * stdev) / sma
    sma = closes.rolling(20).mean()
    sd = closes.rolling(20).std()
    bb_width = (4 * sd) / sma
    cur_bb = bb_width.iloc[-1]
    valid_bb = bb_width.dropna()
    bb_pctile = float((valid_bb <= cur_bb).sum() / len(valid_bb)) if len(valid_bb) else 1.0

    # 24-bar rolling range as % of close (vectorized via .rolling)
    window = 24
    high_w = highs.rolling(window).max()
    low_w = lows.rolling(window).min()
    range_pct = (high_w - low_w) / closes
    cur_range = float(range_pct.iloc[-1]) if not pd.isna(range_pct.iloc[-1]) else None
    valid_r = range_pct.dropna()
    range_pctile = float((valid_r <= cur_range).sum() / len(valid_r)) if cur_range is not None else 1.0

    # Centeredness over last 24 bars: 0 = at midpoint, 1 = at edge
    recent_high = highs.iloc[-window:].max()
    recent_low = lows.iloc[-window:].min()
    half_range = (recent_high - recent_low) / 2
    midpoint = (recent_high + recent_low) / 2
    centeredness = float(abs(closes.iloc[-1] - midpoint) / half_range) if half_range > 0 else 0.0
    centeredness = min(centeredness, 1.0)

    score = ((1 - bb_pctile) * 0.4 + (1 - range_pctile) * 0.4 + (1 - centeredness) * 0.2) * 100

    return {
        "score": score,
        "bb_pctile": bb_pctile,
        "range_pctile": range_pctile,
        "centeredness": centeredness,
        "current_close": float(closes.iloc[-1]),
        "range_24h_pct": (cur_range or 0.0) * 100,
        "bb_width_pct": float(cur_bb) * 100 if not pd.isna(cur_bb) else 0.0,
    }


@st.cache_data(ttl=300, show_spinner=False)
def _scan_consolidation(coins: tuple[str, ...]) -> pd.DataFrame:
    def _one(coin: str) -> dict | None:
        try:
            candles = get_candles_1h(coin)
        except Exception:
            return None
        result = _consolidation_score(candles)
        if result is None:
            return None
        result["coin"] = coin
        return result

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = [r for r in pool.map(_one, coins) if r]

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)


def _consolidation_chart(coin: str, candles: list[dict], score: float, lookback: int = 24) -> go.Figure:
    """Candlestick chart with BB(20) bands + a shaded box marking the consolidation zone."""
    df = pd.DataFrame({
        "time": pd.to_datetime([c["t"] for c in candles], unit="ms", utc=True),
        "open": [float(c["o"]) for c in candles],
        "high": [float(c["h"]) for c in candles],
        "low": [float(c["l"]) for c in candles],
        "close": [float(c["c"]) for c in candles],
    })

    sma = df["close"].rolling(20).mean()
    sd = df["close"].rolling(20).std()
    bb_upper = sma + 2 * sd
    bb_lower = sma - 2 * sd

    recent = df.tail(lookback)
    cons_high = recent["high"].max()
    cons_low = recent["low"].min()
    cons_start = recent["time"].iloc[0]
    cons_end = recent["time"].iloc[-1]

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["time"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name=coin,
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=df["time"], y=bb_upper, line=dict(width=1, color="rgba(150,150,200,0.45)"),
        showlegend=False, hoverinfo="skip", name="BB upper",
    ))
    fig.add_trace(go.Scatter(
        x=df["time"], y=bb_lower, line=dict(width=1, color="rgba(150,150,200,0.45)"),
        fill="tonexty", fillcolor="rgba(150,150,200,0.08)",
        showlegend=False, hoverinfo="skip", name="BB lower",
    ))

    fig.add_shape(
        type="rect",
        x0=cons_start, x1=cons_end,
        y0=cons_low, y1=cons_high,
        line=dict(color="orange", width=1.5, dash="dash"),
        fillcolor="rgba(255, 165, 0, 0.10)",
        layer="below",
    )
    fig.add_hline(y=cons_high, line=dict(color="orange", width=1, dash="dot"))
    fig.add_hline(y=cons_low, line=dict(color="orange", width=1, dash="dot"))

    last_close = df["close"].iloc[-1]
    fig.add_annotation(
        x=cons_end, y=cons_high, text=f"H {cons_high:.6g}",
        showarrow=False, font=dict(size=10, color="orange"),
        xanchor="right", yanchor="bottom",
    )
    fig.add_annotation(
        x=cons_end, y=cons_low, text=f"L {cons_low:.6g}",
        showarrow=False, font=dict(size=10, color="orange"),
        xanchor="right", yanchor="top",
    )

    fig.update_layout(
        title=dict(text=f"<b>{coin}</b> · score {score:.1f} · last ${last_close:,.6g}", x=0.01, font=dict(size=13)),
        xaxis_rangeslider_visible=False,
        height=320,
        margin=dict(l=10, r=10, t=30, b=10),
        template="plotly_dark",
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)")
    return fig


@st.fragment(run_every="600s")
def consolidation_scanner_panel() -> None:
    st.subheader("Consolidation scanner — 1h coiled setups")
    st.caption(
        f"Updated {datetime.now(WIB).strftime('%H:%M:%S WIB')} · refresh every 10min · "
        "composite of BB squeeze + tight range + centered position on 1h candles (168-bar lookback). "
        "Higher score = more consolidated."
    )

    try:
        ctxs = get_hl_contexts()
    except Exception as e:
        st.error(f"Hyperliquid API error: {e}")
        return

    coins = tuple(sorted(coin for coin, c in ctxs.items() if c["dayNtlVlm"] >= MOVERS_MIN_VOL_USD))
    if not coins:
        st.warning("No coins pass the liquidity filter.")
        return

    with st.spinner(f"Scanning {len(coins)} tokens (1h candles, parallel)..."):
        df = _scan_consolidation(coins)

    if df.empty:
        st.warning("No tokens scanned successfully.")
        return

    top = df.head(15)
    display = pd.DataFrame({
        "Coin": top["coin"],
        "Score": top["score"].map(lambda x: f"{x:.1f}"),
        "BB pctile": top["bb_pctile"].map(lambda x: f"{x*100:.0f}%"),
        "24h range": top["range_24h_pct"].map(lambda x: f"{x:.2f}%"),
        "BB width": top["bb_width_pct"].map(lambda x: f"{x:.2f}%"),
        "Centered": top["centeredness"].map(lambda x: f"{x:.2f}"),
        "Mark": top["current_close"].map(lambda x: f"${x:,.6f}".rstrip("0").rstrip(".")),
    })
    st.dataframe(display, hide_index=True, width="stretch")
    st.caption(
        "**Score** weighted 40/40/20 (BB pctile / range pctile / centeredness). "
        "**BB pctile** = current Bollinger band width's percentile rank in last 168h (lower = more squeezed). "
        "**24h range** = (high − low) / current price. "
        "**Centered** = 0 means closing at midpoint of 24h range, 1 means at the edge. "
        "Use as a breakout watchlist — confirm with chart + orderbook before entry."
    )

    # Top 5 charts with consolidation zone marker
    st.markdown("**Top 5 — 1h candles with consolidation zone marker**")
    top5 = top.head(5).reset_index(drop=True)
    rows = [top5.iloc[:3], top5.iloc[3:5]]  # 3-up then 2-up grid
    for row in rows:
        if row.empty:
            continue
        cols = st.columns(3 if len(row) == 3 else len(row))
        for col, (_, item) in zip(cols, row.iterrows()):
            coin = item["coin"]
            try:
                candles = get_candles_1h(coin)
            except Exception as e:
                with col:
                    st.error(f"{coin}: {e}")
                continue
            with col:
                fig = _consolidation_chart(coin, candles, score=float(item["score"]))
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


@st.fragment(run_every="5s")
def top_movers_panel() -> None:
    st.subheader("Top movers — Hyperliquid discovery")
    st.caption(
        f"Updated {datetime.now(WIB).strftime('%H:%M:%S WIB')} · refresh every 5s · "
        f"filtered to vol24h ≥ ${MOVERS_MIN_VOL_USD/1e6:.0f}M"
    )

    try:
        ctxs = get_hl_contexts()
    except Exception as e:
        st.error(f"Hyperliquid API error: {e}")
        return

    ranked = _rank_movers(ctxs)
    if not ranked["gainers"]:
        st.warning("No assets passed the liquidity filter.")
        return

    row1 = st.columns(2)
    with row1[0]:
        st.markdown("**:green[Top gainers (24h)]**")
        st.dataframe(_movers_table(ranked["gainers"]), hide_index=True, width="stretch")
    with row1[1]:
        st.markdown("**:red[Top losers (24h)]**")
        st.dataframe(_movers_table(ranked["losers"]), hide_index=True, width="stretch")

    row2 = st.columns(2)
    with row2[0]:
        st.markdown("**:orange[Over-longed (highest funding — squeeze candidates if reversal)]**")
        st.dataframe(_movers_table(ranked["over_longed"]), hide_index=True, width="stretch")
    with row2[1]:
        st.markdown("**:blue[Over-shorted (most negative funding — squeeze candidates if rally)]**")
        st.dataframe(_movers_table(ranked["over_shorted"]), hide_index=True, width="stretch")

    st.markdown("**Volume leaders (24h notional)**")
    st.dataframe(_movers_table(ranked["volume_leaders"]), hide_index=True, width="stretch")


@st.fragment(run_every="3s")
def hyperliquid_panel(assets: list[str]) -> None:
    st.subheader("Hyperliquid — live market")
    st.caption(f"Updated {datetime.now(WIB).strftime('%H:%M:%S WIB')} · refresh every 3s")

    # Top-line metrics — wrap to rows of 4
    chunks = [assets[i:i + 4] for i in range(0, len(assets), 4)]
    for chunk in chunks:
        cols = st.columns(len(chunk))
        for col, asset in zip(cols, chunk):
            with col:
                try:
                    ctx = get_context(asset)
                except Exception as e:
                    st.error(f"{asset}: {e}")
                    continue
                if not ctx:
                    st.warning(f"{asset}: not found on Hyperliquid")
                    continue
                change = ctx["markPx"] - ctx["prevDayPx"]
                pct = (change / ctx["prevDayPx"] * 100) if ctx["prevDayPx"] else 0.0
                funding_h_pct = ctx["funding"] * 100
                funding_apr = funding_h_pct * 24 * 365
                st.metric(asset, f"${ctx['markPx']:,.4g}", f"{pct:+.2f}% (24h)")
                st.caption(
                    f"Funding **{funding_h_pct:+.4f}%/h** (APR {funding_apr:+.1f}%)  \n"
                    f"OI **{ctx['openInterest']:,.0f}** · Vol24h **${ctx['dayNtlVlm']/1e6:,.1f}M**"
                )

    # Orderbook — wrap to rows of 4
    st.markdown("**Order book — top 10 levels**")
    for chunk in chunks:
        book_cols = st.columns(len(chunk))
        for col, asset in zip(book_cols, chunk):
            with col:
                try:
                    book = get_hl_book(asset)
                except Exception as e:
                    st.error(f"{asset}: {e}")
                    continue
                if not book or not isinstance(book.get("levels"), list) or len(book["levels"]) < 2:
                    st.caption(f"{asset}: book unavailable")
                    continue
                m = hl.book_metrics(book, depth=BOOK_DEPTH)
                imb_color = "green" if m["imbalance"] > 0.55 else ("red" if m["imbalance"] < 0.45 else "gray")
                st.markdown(
                    f"**{asset}** · spread {m['spread']:.4g} ({m['spread_bps']:.1f} bps) · "
                    f"imbalance :{imb_color}[{m['imbalance']:.2f}]"
                )
                bids = book["levels"][0][:BOOK_DEPTH]
                asks = book["levels"][1][:BOOK_DEPTH]
                n = max(len(bids), len(asks))
                df = pd.DataFrame({
                    "Bid size": [float(bids[i]["sz"]) if i < len(bids) else None for i in range(n)],
                    "Bid": [float(bids[i]["px"]) if i < len(bids) else None for i in range(n)],
                    "Ask": [float(asks[i]["px"]) if i < len(asks) else None for i in range(n)],
                    "Ask size": [float(asks[i]["sz"]) if i < len(asks) else None for i in range(n)],
                })
                st.dataframe(df, hide_index=True, height=280, width="stretch")


def _macd_state(candles: list[dict]) -> dict | None:
    if len(candles) < 30:
        return None
    closes = pd.Series([float(c["c"]) for c in candles])
    ema_fast = closes.ewm(span=12, adjust=False).mean()
    ema_slow = closes.ewm(span=26, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal

    # Detect last sign-change of (macd - signal)
    diff = macd - signal
    sign = diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    last_cross_idx = None
    for i in range(len(sign) - 1, 0, -1):
        if sign.iloc[i] != sign.iloc[i - 1] and sign.iloc[i] != 0:
            last_cross_idx = i
            break

    hours_since_cross = (len(closes) - 1 - last_cross_idx) if last_cross_idx is not None else None
    cross_direction = "bullish" if sign.iloc[-1] > 0 else ("bearish" if sign.iloc[-1] < 0 else "neutral")
    hist_growing = bool(hist.iloc[-1] > hist.iloc[-2]) if len(hist) > 1 else None

    return {
        "macd": float(macd.iloc[-1]),
        "signal": float(signal.iloc[-1]),
        "histogram": float(hist.iloc[-1]),
        "state": cross_direction,
        "hours_since_cross": hours_since_cross,
        "hist_growing": hist_growing,
    }


def _vwap_state(candles: list[dict], last_n: int = 24) -> dict | None:
    if len(candles) < 2:
        return None
    df = pd.DataFrame({
        "h": [float(c["h"]) for c in candles],
        "l": [float(c["l"]) for c in candles],
        "c": [float(c["c"]) for c in candles],
        "v": [float(c["v"]) for c in candles],
    }).tail(last_n)
    typical = (df["h"] + df["l"] + df["c"]) / 3
    vol_sum = df["v"].sum()
    vwap = float((typical * df["v"]).sum() / vol_sum) if vol_sum > 0 else float(df["c"].iloc[-1])
    last = float(df["c"].iloc[-1])
    return {
        "vwap": vwap,
        "price": last,
        "diff_pct": (last - vwap) / vwap * 100 if vwap else 0.0,
        "above": last > vwap,
    }


def trade_decision_panel(watchlist_assets: list[str]) -> None:
    st.subheader("Trade Decision — Layer 3")
    st.caption(
        "Single-asset synthesis + position sizing. **Decision support — not a recommendation.** "
        "You decide whether to take the trade; the panel does the math."
    )

    # Top-5 consolidation list as additional candidates (main DEX only — scanner scope unchanged)
    try:
        main_ctxs = get_hl_contexts(dex=None)
        coins_full = tuple(sorted(c for c, x in main_ctxs.items() if x["dayNtlVlm"] >= MOVERS_MIN_VOL_USD))
        scan_df = _scan_consolidation(coins_full)
    except Exception:
        scan_df = pd.DataFrame()
    top_cons = scan_df.head(5)["coin"].tolist() if not scan_df.empty else []

    asset_options = list(dict.fromkeys(list(watchlist_assets) + top_cons))
    if not asset_options:
        st.warning("No assets available.")
        return

    head = st.columns([2, 1, 1])
    with head[0]:
        asset = st.selectbox("Asset", options=asset_options, key="td_asset")
    with head[1]:
        side = st.radio("Direction", ["Long", "Short"], horizontal=True, key="td_side")
    with head[2]:
        # Hyperliquid trade URL: cross-DEX assets use the colon-prefixed symbol
        st.link_button(
            f"Open {asset} on Hyperliquid →",
            f"https://app.hyperliquid.xyz/trade/{asset}",
        )

    try:
        ctx = get_context(asset)
    except Exception as e:
        st.error(f"{asset}: {e}")
        return
    if not ctx:
        st.warning(f"{asset}: not in current Hyperliquid contexts.")
        return

    try:
        candles = get_candles_1h(asset)
    except Exception as e:
        st.error(f"Candle fetch for {asset}: {e}")
        candles = []

    try:
        book = get_hl_book(asset)
        book_m = hl.book_metrics(book, depth=10)
    except Exception:
        book_m = None

    macd = _macd_state(candles)
    vwap = _vwap_state(candles)
    funding_h_pct = ctx["funding"] * 100

    cons_info = None
    if not scan_df.empty:
        row = scan_df[scan_df["coin"] == asset]
        if not row.empty:
            rank = int(row.index[0]) + 1
            cons_info = {
                "rank": rank,
                "score": float(row.iloc[0]["score"]),
                "range_pct": float(row.iloc[0]["range_24h_pct"]),
                "bb_pctile": float(row.iloc[0]["bb_pctile"]),
            }

    # Signal stack
    st.markdown("#### Signal stack")
    sig_cols = st.columns(5)

    def _color(state: str) -> str:
        return {"bull": "green", "bear": "red", "neutral": "gray"}[state]

    with sig_cols[0]:
        if macd:
            state = "bull" if macd["state"] == "bullish" else ("bear" if macd["state"] == "bearish" else "neutral")
            since = f"{macd['hours_since_cross']}h ago" if macd["hours_since_cross"] is not None else "no cross in lookback"
            grow = "↑" if macd["hist_growing"] else "↓"
            st.markdown(f"**MACD 1h**  \n:{_color(state)}[{macd['state']}]")
            st.caption(f"cross {since} · hist {grow}")
        else:
            st.markdown("**MACD 1h**  \n—")

    with sig_cols[1]:
        if vwap:
            state = "bull" if vwap["above"] else "bear"
            st.markdown(f"**VWAP 24h**  \n:{_color(state)}[{'above' if vwap['above'] else 'below'} ({vwap['diff_pct']:+.2f}%)]")
            st.caption(f"vwap ${vwap['vwap']:,.6g}")
        else:
            st.markdown("**VWAP 24h**  \n—")

    with sig_cols[2]:
        if book_m:
            imb = book_m["imbalance"]
            state = "bull" if imb > 0.55 else ("bear" if imb < 0.45 else "neutral")
            st.markdown(f"**OB imbalance (top 10)**  \n:{_color(state)}[{imb:.2f}]")
            st.caption(f"spread {book_m['spread_bps']:.1f} bps")
        else:
            st.markdown("**OB imbalance**  \n—")

    with sig_cols[3]:
        if funding_h_pct > 0.005:
            state, interp = "bear", "over-longed (squeeze risk)"
        elif funding_h_pct < -0.005:
            state, interp = "bull", "over-shorted (squeeze potential)"
        else:
            state, interp = "neutral", "balanced"
        st.markdown(f"**Funding/h**  \n:{_color(state)}[{funding_h_pct:+.4f}%]")
        st.caption(interp)

    with sig_cols[4]:
        if cons_info:
            st.markdown(f"**Consolidation**  \n:orange[⭐ #{cons_info['rank']} · score {cons_info['score']:.1f}]")
            st.caption(f"24h range {cons_info['range_pct']:.2f}% · BB pctile {cons_info['bb_pctile']*100:.0f}%")
        else:
            st.markdown("**Consolidation**  \nnot ranked")
            st.caption(f"vol24h ${ctx['dayNtlVlm']/1e6:,.1f}M")

    st.divider()

    # Trade plan
    st.markdown("#### Trade plan")
    plan_cols = st.columns(4)
    with plan_cols[0]:
        entry = st.number_input(
            "Entry price", value=float(ctx["markPx"]), format="%.6g",
            key=f"td_entry_{asset}",
            help="Default is current mark. Override if you want a limit at a different level.",
        )
    with plan_cols[1]:
        stop_pct = st.number_input("Stop %", value=1.5, min_value=0.1, max_value=20.0, step=0.1, key="td_stop_pct")
    with plan_cols[2]:
        account_usd = st.number_input("Account size $", value=1000.0, min_value=10.0, step=100.0, key="td_account")
    with plan_cols[3]:
        risk_pct = st.number_input("Risk % of account", value=1.0, min_value=0.05, max_value=10.0, step=0.05, key="td_risk_pct")

    tp_cols = st.columns(3)
    with tp_cols[0]:
        tp1_r = st.number_input("TP1 (R-multiple)", value=1.0, min_value=0.1, step=0.1, key="td_tp1")
    with tp_cols[1]:
        tp2_r = st.number_input("TP2 (R-multiple)", value=2.0, min_value=0.1, step=0.1, key="td_tp2")
    with tp_cols[2]:
        tp3_r = st.number_input("TP3 (R-multiple)", value=3.0, min_value=0.1, step=0.1, key="td_tp3")

    sign = 1 if side == "Long" else -1
    stop_dist = stop_pct / 100
    stop_price = entry * (1 - sign * stop_dist)
    tp_prices = [entry * (1 + sign * r * stop_dist) for r in (tp1_r, tp2_r, tp3_r)]

    risk_usd = account_usd * risk_pct / 100
    position_usd = risk_usd / stop_dist if stop_dist > 0 else 0
    leverage = position_usd / account_usd if account_usd > 0 else 0

    plan_df = pd.DataFrame({
        "Level": ["Entry", "Stop", "TP1", "TP2", "TP3"],
        "Price": [f"${entry:,.6g}", f"${stop_price:,.6g}",
                  f"${tp_prices[0]:,.6g}", f"${tp_prices[1]:,.6g}", f"${tp_prices[2]:,.6g}"],
        "R-multiple": ["—", "-1.0R", f"+{tp1_r:.1f}R", f"+{tp2_r:.1f}R", f"+{tp3_r:.1f}R"],
        "Δ from entry": ["—",
                          f"{-sign*stop_pct:+.2f}%",
                          f"{sign*tp1_r*stop_pct:+.2f}%",
                          f"{sign*tp2_r*stop_pct:+.2f}%",
                          f"{sign*tp3_r*stop_pct:+.2f}%"],
        "$ P&L (full pos)": ["—",
                              f"-${risk_usd:,.2f}",
                              f"+${risk_usd*tp1_r:,.2f}",
                              f"+${risk_usd*tp2_r:,.2f}",
                              f"+${risk_usd*tp3_r:,.2f}"],
    })
    st.dataframe(plan_df, hide_index=True, width="stretch")

    summary_cols = st.columns(4)
    with summary_cols[0]:
        st.metric("$ at risk", f"${risk_usd:,.2f}")
    with summary_cols[1]:
        st.metric("Position size $", f"${position_usd:,.2f}")
    with summary_cols[2]:
        st.metric("Leverage", f"{leverage:.2f}x")
    with summary_cols[3]:
        max_reward = risk_usd * max(tp1_r, tp2_r, tp3_r)
        st.metric(f"Max profit (TP @ {max(tp1_r, tp2_r, tp3_r):.1f}R)", f"${max_reward:,.2f}")

    if leverage > 10:
        st.warning(f"Leverage **{leverage:.1f}x** — verify Hyperliquid's max for {asset}. Tight stops + high leverage = small slippage can blow through your stop.")


def tradingview_panel(assets: list[str]) -> None:
    """TradingView candlestick chart. Not in a fragment — interactive iframe should not auto-reload."""
    st.subheader("Chart — TradingView")
    st.caption(
        "Candlesticks with MACD + VWAP enabled by default. "
        "Drawing tools and indicator changes work inside the widget. "
        "Symbol override field accepts any TradingView symbol (e.g. `COINBASE:BTCUSD`, `BYBIT:TAOUSDT.P`)."
    )

    asset_options = list(dict.fromkeys(assets + ["BTC", "ETH", "SOL"]))
    ctrl = st.columns([2, 1, 3, 1, 1])
    with ctrl[0]:
        asset = st.selectbox("Asset", options=asset_options, key="tv_asset")
    with ctrl[1]:
        tf_label = st.selectbox("Timeframe", options=list(TV_TIMEFRAMES.keys()), index=1, key="tv_tf")
    with ctrl[2]:
        default_sym = _guess_tv_symbol(asset)
        symbol = st.text_input(
            "TradingView symbol",
            value=default_sym,
            key=f"tv_sym_{asset}",
            help=(
                "Override the symbol if the default doesn't load.\n"
                "- BINANCE:BTCUSDT.P  → Binance perp\n"
                "- COINBASE:BTCUSD    → Coinbase spot\n"
                "- HYPERLIQUID:HYPEUSD\n"
                "- BYBIT:TAOUSDT.P"
            ),
        )
    with ctrl[3]:
        theme = st.selectbox("Theme", options=["dark", "light"], key="tv_theme")
    with ctrl[4]:
        height = st.selectbox("Height", options=[500, 650, 800, 1000], index=1, key="tv_height")

    interval = TV_TIMEFRAMES[tf_label]
    studies_json = json.dumps(TV_DEFAULT_STUDIES)

    # Unique container id per (symbol, interval, theme) so changes force a clean re-init
    container_id = f"tv_{abs(hash((symbol, interval, theme)))}"

    html = f"""
    <div class="tradingview-widget-container" style="height:{height}px;max-height:80vh;width:100%">
      <div id="{container_id}" style="height:100%;width:100%"></div>
    </div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    new TradingView.widget({{
        "autosize": true,
        "symbol": "{symbol}",
        "interval": "{interval}",
        "timezone": "Asia/Jakarta",
        "theme": "{theme}",
        "style": "1",
        "locale": "en",
        "enable_publishing": false,
        "withdateranges": true,
        "hide_side_toolbar": false,
        "allow_symbol_change": true,
        "studies": {studies_json},
        "container_id": "{container_id}"
    }});
    </script>
    """
    components.html(html, height=height + 20, scrolling=False)

    alternatives = TV_SYMBOL_ALTERNATIVES.get(asset, [])
    if alternatives:
        alt_str = " · ".join(f"`{a}`" for a in alternatives)
        st.caption(f"Symbol not loading? Try: {alt_str}  (paste into the symbol field above)")
    else:
        st.caption(
            "Symbol not loading? Click **Change symbol** inside the chart to search TradingView's catalog, "
            "or try a different venue prefix (e.g. `BYBIT:`, `MEXC:`, `KUCOIN:`, `COINBASE:`)."
        )


@st.fragment(run_every="15s")
def yahoo_macro_panel(macro_tickers: list[str]) -> None:
    if not macro_tickers:
        return
    st.subheader("Macro reference (Yahoo Finance)")
    cols = st.columns(len(macro_tickers))
    for col, tk in zip(cols, macro_tickers):
        with col:
            try:
                q = get_yahoo_quote(tk)
                arrow = "↑" if q["change"] >= 0 else "↓"
                st.metric(tk, f"{q['price']:,.2f}", f"{arrow} {q['pct']:+.2f}%")
            except Exception as e:
                st.warning(f"{tk}: {e}")


@st.fragment(run_every="30s")
def x_feed_panel() -> None:
    st.subheader("X feed (followed accounts)")
    dump = read_x_dump()
    if not dump:
        st.info(
            "**No X data yet.**\n\n"
            "Configured accounts live in `x_config.json`. To fetch tweets, switch to your "
            "Claude Code session and run:\n\n"
            "> **/scrape-x**\n\n"
            "First-time setup requires a one-time login — see the README."
        )
        return

    try:
        gen_at = datetime.fromisoformat(dump["generated_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - gen_at).total_seconds()
    except Exception:
        age = 0
        gen_at = datetime.now(timezone.utc)

    lookback = dump.get("lookback_hours", 24)
    st.caption(
        f"Scraped {_format_age(age)} ({gen_at.astimezone(WIB).strftime('%H:%M:%S WIB')}) · "
        f"last {lookback}h · run `/scrape-x` to refresh"
    )

    errors = dump.get("errors") or {}
    for h, err in errors.items():
        st.warning(f"@{h}: {err}")

    tweets_by_handle = dump.get("tweets", {})
    handles = dump.get("handles", list(tweets_by_handle.keys()))

    if not any(tweets_by_handle.get(h) for h in handles):
        st.caption("No tweets in the lookback window.")
        return

    cols = st.columns(min(len(handles), 4) or 1)
    for i, h in enumerate(handles):
        tweets = tweets_by_handle.get(h, [])
        with cols[i % len(cols)]:
            st.markdown(f"**@{h}** · {len(tweets)} tweets")
            if not tweets:
                st.caption("No recent tweets")
                continue
            def _safe(s: str) -> str:
                return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                         .replace("$", "&#36;").replace("\n", "<br>"))

            for tw in tweets[:8]:
                text = (tw.get("text") or "").strip()
                quote_text = (tw.get("quote_text") or "").strip()
                quote_author = (tw.get("quote_author") or "").strip()
                snapshot = tw.get("snapshot")
                if len(text) > 280:
                    text = text[:277] + "…"
                if len(quote_text) > 220:
                    quote_text = quote_text[:217] + "…"
                ts = tw.get("timestamp", "")
                try:
                    tw_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    tw_age = _format_age((datetime.now(timezone.utc) - tw_dt).total_seconds())
                except Exception:
                    tw_age = ""
                tag = " 🔁" if tw.get("is_retweet") else ""
                link = tw.get("permalink", "")
                header = f'<a href="{link}" target="_blank">{tw_age}{tag}</a>' if link else (tw_age + tag)

                # Build body: main text → quoted tweet block → snapshot image → fallback placeholder
                parts: list[str] = []
                if text:
                    parts.append(
                        f'<div style="font-size:0.85rem;opacity:0.9;margin:0.1rem 0 0.3rem 1rem">{_safe(text)}</div>'
                    )
                if quote_text:
                    qa = f'<b>Quoting {quote_author}:</b> ' if quote_author else '<b>Quoting:</b> '
                    parts.append(
                        f'<div style="font-size:0.8rem;opacity:0.75;margin:0.1rem 0 0.3rem 1.5rem;'
                        f'border-left:2px solid rgba(255,255,255,0.2);padding-left:0.5rem">'
                        f'{qa}{_safe(quote_text)}</div>'
                    )
                st.markdown(
                    f'<div style="font-size:0.85rem"><b>·</b> {header}</div>' + "".join(parts),
                    unsafe_allow_html=True,
                )
                if snapshot:
                    snap_path = PROJECT_DIR / snapshot
                    if snap_path.exists():
                        st.image(str(snap_path), width=320)
                elif not text and not quote_text:
                    st.markdown(
                        '<div style="font-size:0.8rem;opacity:0.5;margin:0 0 0.6rem 1rem">'
                        '<i>(media / no text — click timestamp to view on X)</i></div>',
                        unsafe_allow_html=True,
                    )
                st.markdown('<div style="margin-bottom:0.4rem"></div>', unsafe_allow_html=True)


@st.fragment(run_every="10s")
def ai_summary_panel() -> None:
    st.subheader("AI screening summary")
    summary = read_summary()
    if not summary:
        st.info(
            "**No summary yet.**\n\n"
            "Headlines are auto-dumped to `news_dump.json` every 60s.\n\n"
            "To generate a summary, switch to your Claude Code session in this project and ask:\n\n"
            "> **summarize today's news**\n\n"
            "The summary will appear here automatically once written."
        )
        return
    text, mtime = summary
    age = (datetime.now(timezone.utc) - mtime).total_seconds()
    st.caption(f"Generated {_format_age(age)} ({mtime.astimezone(WIB).strftime('%H:%M:%S WIB')})")
    st.markdown(text)


@st.fragment(run_every="60s")
def yahoo_news_panel(assets: list[str], macro_tickers: list[str]) -> None:
    st.subheader("News headlines (Yahoo Finance)")
    st.caption(f"Updated {datetime.now(WIB).strftime('%H:%M:%S WIB')} · refresh every 60s")

    yahoo_tickers = [f"{a}-USD" for a in assets] + macro_tickers
    items_by_ticker: dict[str, list[dict]] = {}

    cols = st.columns(min(len(yahoo_tickers), 4) or 1)
    for i, tk in enumerate(yahoo_tickers):
        with cols[i % len(cols)]:
            st.markdown(f"**{tk}**")
            items = get_yahoo_news(tk)
            items_by_ticker[tk] = items
            if not items:
                st.caption("No news")
                continue
            for n in items[:5]:
                title = n["title"]
                link = n["link"]
                meta = " · ".join(filter(None, [n["publisher"], n["published"]]))
                if link:
                    st.markdown(f"- [{title}]({link})  \n  <small>{meta}</small>", unsafe_allow_html=True)
                else:
                    st.markdown(f"- {title}  \n  <small>{meta}</small>", unsafe_allow_html=True)

    # Dump for Claude Code consumption
    try:
        write_news_dump(assets, macro_tickers, items_by_ticker)
    except Exception as e:
        st.caption(f"(news_dump.json write failed: {e})")


# ---------- main ----------

def main() -> None:
    st.title("Hyperliquid Trading Dashboard")

    with st.sidebar:
        st.header("Settings")

        # ---- multi-DEX market browser ----
        try:
            universe = get_full_universe()
        except Exception as e:
            st.error(f"Universe load failed: {e}")
            universe = {}

        with st.expander(f"🔍 Browse Hyperliquid markets ({len(universe)} total)", expanded=False):
            if not universe:
                st.warning("Universe couldn't load — try Force refresh below.")
            else:
                classes_avail = sorted({v["class"] for v in universe.values()})
                dexs_avail = sorted({v["dex"] for v in universe.values()})
                class_filter = st.multiselect("Asset class", classes_avail, default=[])
                dex_filter = st.multiselect("DEX", dexs_avail, default=[])
                min_vol_m = st.number_input("Min vol24h ($M)", value=0.0, min_value=0.0, step=1.0,
                                            help="Filter out illiquid listings — set to 0 to see all.")

                def _passes(v: dict) -> bool:
                    if class_filter and v["class"] not in class_filter:
                        return False
                    if dex_filter and v["dex"] not in dex_filter:
                        return False
                    if v["dayNtlVlm"] / 1e6 < min_vol_m:
                        return False
                    return True

                filtered = {k: v for k, v in universe.items() if _passes(v)}
                opts = sorted(filtered.keys(), key=lambda k: -filtered[k]["dayNtlVlm"])

                def _fmt(k: str) -> str:
                    v = filtered.get(k, universe.get(k, {}))
                    if not v:
                        return k
                    vol_m = v.get("dayNtlVlm", 0) / 1e6
                    return f"{k}  ·  {v.get('class','?')}  ·  ${vol_m:,.1f}M/24h"

                st.caption(f"{len(opts)} matches. Pick assets to follow:")
                browser_picks = st.multiselect(
                    "Selected from browser",
                    options=opts,
                    default=[a for a in DEFAULT_ASSETS if a in opts],
                    format_func=_fmt,
                    key="hl_browser_picks",
                    label_visibility="collapsed",
                )
        # ---- end browser ----

        manual_input = st.text_area(
            "Manual entries (one per line)",
            value="",
            height=80,
            help="Type any asset name (case-insensitive) — system auto-resolves across all DEXs. "
                 "E.g. `brentoil` finds `xyz:BRENTOIL` automatically. "
                 "Use explicit prefix (e.g. `flx:OIL`) only when you want a specific DEX.",
        )
        manual_raw = [a.strip() for a in manual_input.splitlines() if a.strip()]
        manual = []
        for raw in manual_raw:
            resolved = resolve_asset(raw, universe)
            if resolved:
                manual.append(resolved)
                if resolved != raw:
                    st.caption(f"↳ `{raw}` → `{resolved}`")
            else:
                st.warning(f"`{raw}`: not found in any Hyperliquid DEX")

        macro_input = st.text_area(
            "Macro / reference tickers (Yahoo)",
            value="\n".join(DEFAULT_MACRO),
            height=100,
            help="Yahoo Finance symbols for macro screening context (not tradeable here).",
        )
        if st.button("Force refresh (clear all caches)"):
            st.cache_data.clear()
            st.rerun()
        st.caption(
            "Refresh rates:\n"
            "- Top movers: 5s · Consolidation scan: 10min\n"
            "- Hyperliquid market: 3s · Yahoo macro prices: 15s\n"
            "- AI summary check: 10s · News + dump: 60s\n"
            "- Universe browser: 5min (stale prices acceptable for picking)"
        )

    # Resolve picks: respect user's empty selection, only fall back to defaults if universe itself failed to load
    if universe:
        browser_picks = st.session_state.get("hl_browser_picks", [])
    else:
        browser_picks = list(DEFAULT_ASSETS)

    # Union: browser picks ∪ manual entries (preserves order, dedups)
    assets = list(dict.fromkeys(list(browser_picks) + manual))
    macro = [m.strip() for m in macro_input.splitlines() if m.strip()]

    if not assets:
        st.warning("Add at least one Hyperliquid asset in the sidebar.")
        return

    top_movers_panel()
    st.divider()
    consolidation_scanner_panel()
    st.divider()
    hyperliquid_panel(assets)
    st.divider()
    tradingview_panel(assets)
    st.divider()
    trade_decision_panel(assets)
    st.divider()
    yahoo_macro_panel(macro)
    st.divider()
    ai_summary_panel()
    st.divider()
    x_feed_panel()
    st.divider()
    yahoo_news_panel(assets, macro)


if __name__ == "__main__":
    main()
