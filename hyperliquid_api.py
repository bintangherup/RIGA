"""Minimal REST client for Hyperliquid's public info endpoint.

No auth needed for market data. WebSocket would give lower latency but adds
state-management complexity that doesn't pay off until we need sub-second updates.
"""
from __future__ import annotations

import time

import requests

INFO_URL = "https://api.hyperliquid.xyz/info"
TIMEOUT = 5


def _post(payload: dict) -> dict | list:
    resp = requests.post(INFO_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_candles(coin: str, interval: str = "1h", lookback_hours: int = 168) -> list[dict]:
    """Return OHLCV candles for a coin. Each entry: {t, T, s, i, o, c, h, l, v, n} with string-valued OHLCV."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - lookback_hours * 3_600_000
    return _post({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
    })


def fetch_asset_contexts(dex: str | None = None) -> dict[str, dict]:
    """Return {coin: {markPx, midPx, oraclePx, funding, openInterest, prevDayPx, dayNtlVlm}}.

    Funding is per-hour as a decimal (Hyperliquid pays funding hourly).
    `dex=None` returns the main HL perp universe (~230 crypto perps).
    `dex="xyz"` etc. returns a third-party HIP-3 DEX universe (assets are pre-prefixed like `xyz:BRENTOIL`).
    """
    payload: dict = {"type": "metaAndAssetCtxs"}
    if dex:
        payload["dex"] = dex
    meta, ctxs = _post(payload)
    out: dict[str, dict] = {}
    for asset, ctx in zip(meta["universe"], ctxs):
        try:
            out[asset["name"]] = {
                "markPx": float(ctx["markPx"]),
                "midPx": float(ctx.get("midPx") or ctx["markPx"]),
                "oraclePx": float(ctx.get("oraclePx", ctx["markPx"])),
                "funding": float(ctx["funding"]),
                "openInterest": float(ctx["openInterest"]),
                "prevDayPx": float(ctx["prevDayPx"]),
                "dayNtlVlm": float(ctx["dayNtlVlm"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_perp_dexs() -> list:
    """Return the list of all perp DEXs on Hyperliquid (HIP-3).
    Entry 0 is None (the main HL perp universe). Others have {name, fullName, deployer, ...}.
    """
    return _post({"type": "perpDexs"})


def fetch_l2_book(coin: str) -> dict:
    """Return raw L2 book: {coin, time, levels: [bids, asks]} where each level is {px, sz, n}."""
    return _post({"type": "l2Book", "coin": coin})


def book_metrics(book: dict, depth: int = 10) -> dict:
    """Compute spread and bid/ask imbalance over the top `depth` levels."""
    bids, asks = book["levels"][0], book["levels"][1]
    bids_top, asks_top = bids[:depth], asks[:depth]
    best_bid = float(bids_top[0]["px"]) if bids_top else 0.0
    best_ask = float(asks_top[0]["px"]) if asks_top else 0.0
    spread = best_ask - best_bid
    spread_bps = (spread / best_bid * 10_000) if best_bid else 0.0
    bid_sz = sum(float(level["sz"]) for level in bids_top)
    ask_sz = sum(float(level["sz"]) for level in asks_top)
    total = bid_sz + ask_sz
    imbalance = (bid_sz / total) if total else 0.5
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_bps": spread_bps,
        "bid_size_top": bid_sz,
        "ask_size_top": ask_sz,
        "imbalance": imbalance,
    }
