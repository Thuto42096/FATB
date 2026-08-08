"""
FATB Hermes Agent — Forex Data Module
======================================
Fetches OHLCV price history from Yahoo Finance via yfinance,
computes RSI(14), SMA(20), SMA(50), and ATR(14), then
emits a clean JSON signal object to stdout.

CLI Usage:
    python3 tools/data/forex_data.py EURUSD
    python3 tools/data/forex_data.py USDZAR=X
    python3 tools/data/forex_data.py GBPUSD --period 60d
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import ta
import yfinance as yf

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_PERIOD: str = "60d"   # history window — must cover SMA-50
DEFAULT_INTERVAL: str = "1d"  # candle interval
RSI_PERIOD: int = 14
SMA_FAST: int = 20
SMA_SLOW: int = 50
ATR_PERIOD: int = 14

# ---------------------------------------------------------------------------
# Ticker normalisation
# ---------------------------------------------------------------------------

def _normalise_ticker(symbol: str) -> str:
    """Ensure the ticker has the Yahoo Finance '=X' forex suffix."""
    symbol = symbol.upper().strip()
    if not symbol.endswith("=X"):
        # Strip any existing slash (e.g. EUR/USD → EURUSD) before appending
        symbol = symbol.replace("/", "") + "=X"
    return symbol


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_history(ticker: str, period: str, interval: str) -> pd.DataFrame:
    """Download OHLCV history from Yahoo Finance."""
    raw: pd.DataFrame = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    if raw.empty:
        raise ValueError(
            f"No data returned for ticker '{ticker}'. "
            "Check that the symbol is valid (e.g. EURUSD=X, GBPUSD=X)."
        )
    # Flatten MultiIndex columns produced by yfinance when downloading one ticker
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [col.lower() for col in raw.columns]

    raw = raw.rename(columns={"adj close": "close"}) if "adj close" in raw.columns else raw
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Missing expected columns in yfinance response: {missing}")

    return raw.astype(float)


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------

def _compute_indicators(df: pd.DataFrame) -> dict[str, float | None]:
    """Compute RSI, SMA-20, SMA-50, and ATR on the fetched OHLCV data."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    rsi_series = ta.momentum.RSIIndicator(close=close, window=RSI_PERIOD).rsi()
    sma20_series = ta.trend.SMAIndicator(close=close, window=SMA_FAST).sma_indicator()
    sma50_series = ta.trend.SMAIndicator(close=close, window=SMA_SLOW).sma_indicator()
    atr_series = ta.volatility.AverageTrueRange(
        high=high, low=low, close=close, window=ATR_PERIOD
    ).average_true_range()

    def _last(series: pd.Series) -> float | None:
        val = series.dropna().iloc[-1] if not series.dropna().empty else None
        return round(float(val), 6) if val is not None else None

    return {
        "rsi_14": _last(rsi_series),
        "sma_20": _last(sma20_series),
        "sma_50": _last(sma50_series),
        "atr_14": _last(atr_series),
    }


# ---------------------------------------------------------------------------
# Signal logic
# ---------------------------------------------------------------------------

def _derive_signal(
    price: float,
    rsi: float | None,
    sma_20: float | None,
    sma_50: float | None,
) -> str:
    """
    Composite signal for forex:
      BULLISH  → RSI < 65 AND sma_20 > sma_50 AND price > sma_20
      BEARISH  → RSI > 65 OR (sma_20 < sma_50 AND price < sma_20)
      NEUTRAL  → everything else
    """
    if rsi is None or sma_20 is None or sma_50 is None:
        return "NEUTRAL"

    if rsi < 65 and sma_20 > sma_50 and price > sma_20:
        return "BULLISH"
    if rsi > 65 or (sma_20 < sma_50 and price < sma_20):
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_forex_signal(
    symbol: str,
    period: str = DEFAULT_PERIOD,
    interval: str = DEFAULT_INTERVAL,
) -> dict[str, Any]:
    """Fetch live forex data and return a fully-populated signal dict."""
    ticker = _normalise_ticker(symbol)
    df = _fetch_history(ticker, period=period, interval=interval)
    indicators = _compute_indicators(df)

    latest = df.iloc[-1]
    previous = df.iloc[-2] if len(df) > 1 else latest

    close_price = round(float(latest["close"]), 6)
    prev_close = float(previous["close"])
    change_pct = round(((close_price - prev_close) / prev_close) * 100, 4) if prev_close != 0 else 0.0

    signal = _derive_signal(
        price=close_price,
        rsi=indicators["rsi_14"],
        sma_20=indicators["sma_20"],
        sma_50=indicators["sma_50"],
    )

    # Derive human-readable pair name (e.g. EURUSD=X → EUR/USD)
    base_ticker = ticker.replace("=X", "")
    pair_name = f"{base_ticker[:3]}/{base_ticker[3:]}" if len(base_ticker) == 6 else base_ticker

    return {
        "pair": pair_name,
        "ticker": ticker,
        "bid_ask_close": close_price,
        "change_pct": change_pct,
        "rsi_14": indicators["rsi_14"],
        "sma_20": indicators["sma_20"],
        "sma_50": indicators["sma_50"],
        "atr_14": indicators["atr_14"],
        "signal": signal,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FATB Hermes Agent — Forex Data Fetcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 tools/data/forex_data.py EURUSD\n"
            "  python3 tools/data/forex_data.py USDZAR=X\n"
            "  python3 tools/data/forex_data.py GBPUSD --period 90d\n"
        ),
    )
    parser.add_argument(
        "symbol",
        help="Forex pair symbol, e.g. EURUSD or EURUSD=X",
    )
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help=f"History window for yfinance (default: {DEFAULT_PERIOD})",
    )
    parser.add_argument(
        "--interval",
        default=DEFAULT_INTERVAL,
        help=f"Candle interval (default: {DEFAULT_INTERVAL})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        result = get_forex_signal(
            symbol=args.symbol,
            period=args.period,
            interval=args.interval,
        )
        print(json.dumps(result, indent=2))
    except Exception as exc:  # noqa: BLE001
        error_payload = {
            "error": str(exc),
            "symbol": args.symbol,
        }
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
