"""
FATB Hermes Agent — Crypto Data Module
=======================================
Fetches OHLCV candles from Binance (KuCoin fallback) via ccxt,
computes RSI(14), SMA(20), SMA(50), and Bollinger Bands, then
emits a clean JSON signal object to stdout.

CLI Usage:
    python3 tools/data/crypto_data.py BTC/USDT
    python3 tools/data/crypto_data.py ETH/USDT --timeframe 1h
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import ccxt
import pandas as pd
import ta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEFRAME: str = "1h"
OHLCV_LIMIT: int = 100  # candles to fetch (enough for SMA-50)
RSI_PERIOD: int = 14
SMA_FAST: int = 20
SMA_SLOW: int = 50
BB_PERIOD: int = 20
BB_STD: int = 2

# ---------------------------------------------------------------------------
# Exchange initialisation
# ---------------------------------------------------------------------------

def _build_exchange(name: str) -> ccxt.Exchange:
    """Instantiate a public ccxt exchange (no API key required)."""
    cls = getattr(ccxt, name)
    return cls({"enableRateLimit": True})


def _fetch_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Try Binance first; fall back to KuCoin if the symbol is unavailable
    or Binance raises an error.
    """
    exchanges = ["binance", "kucoin"]
    last_error: Exception | None = None

    for exchange_name in exchanges:
        try:
            exchange = _build_exchange(exchange_name)
            raw: list[list[Any]] = exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=OHLCV_LIMIT
            )
            if not raw:
                continue
            df = pd.DataFrame(
                raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.astype(
                {"open": float, "high": float, "low": float, "close": float, "volume": float}
            )
            return df
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise RuntimeError(
        f"Failed to fetch OHLCV for '{symbol}' from all exchanges. "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------

def _compute_indicators(df: pd.DataFrame) -> dict[str, float | None]:
    """Compute RSI, SMA-20, SMA-50, and Bollinger Bands on close prices."""
    close = df["close"]

    rsi_series = ta.momentum.RSIIndicator(close=close, window=RSI_PERIOD).rsi()
    sma20_series = ta.trend.SMAIndicator(close=close, window=SMA_FAST).sma_indicator()
    sma50_series = ta.trend.SMAIndicator(close=close, window=SMA_SLOW).sma_indicator()

    bb = ta.volatility.BollingerBands(close=close, window=BB_PERIOD, window_dev=BB_STD)
    bb_upper_series = bb.bollinger_hband()
    bb_lower_series = bb.bollinger_lband()

    def _last(series: pd.Series) -> float | None:
        val = series.dropna().iloc[-1] if not series.dropna().empty else None
        return round(float(val), 6) if val is not None else None

    return {
        "rsi_14": _last(rsi_series),
        "sma_20": _last(sma20_series),
        "sma_50": _last(sma50_series),
        "bollinger_upper": _last(bb_upper_series),
        "bollinger_lower": _last(bb_lower_series),
    }


# ---------------------------------------------------------------------------
# Signal logic
# ---------------------------------------------------------------------------

def _derive_signal(
    price: float,
    rsi: float | None,
    bb_upper: float | None,
    bb_lower: float | None,
    sma_20: float | None,
    sma_50: float | None,
) -> str:
    """
    Simple composite signal:
      BULLISH  → RSI < 70 AND price > SMA-20 AND price > SMA-50 AND price < BB-upper
      BEARISH  → RSI > 70 OR price < BB-lower OR (sma_20 < sma_50 AND price < sma_20)
      NEUTRAL  → everything else
    """
    if rsi is None:
        return "NEUTRAL"

    bullish_conditions = [
        rsi < 70,
        sma_20 is not None and price > sma_20,
        sma_50 is not None and price > sma_50,
        bb_upper is not None and price < bb_upper,
    ]
    bearish_conditions = [
        rsi > 70,
        bb_lower is not None and price < bb_lower,
        sma_20 is not None and sma_50 is not None and sma_20 < sma_50 and price < sma_20,
    ]

    if all(bullish_conditions):
        return "BULLISH"
    if any(bearish_conditions):
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_crypto_signal(symbol: str, timeframe: str = DEFAULT_TIMEFRAME) -> dict[str, Any]:
    """Fetch live data and return a fully-populated signal dict."""
    df = _fetch_ohlcv(symbol, timeframe)
    indicators = _compute_indicators(df)

    latest = df.iloc[-1]
    price = round(float(latest["close"]), 6)
    volume_24h = round(float(df["volume"].sum()), 4)  # sum across fetched candles

    signal = _derive_signal(
        price=price,
        rsi=indicators["rsi_14"],
        bb_upper=indicators["bollinger_upper"],
        bb_lower=indicators["bollinger_lower"],
        sma_20=indicators["sma_20"],
        sma_50=indicators["sma_50"],
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "volume_24h": volume_24h,
        "rsi_14": indicators["rsi_14"],
        "sma_20": indicators["sma_20"],
        "sma_50": indicators["sma_50"],
        "bollinger_upper": indicators["bollinger_upper"],
        "bollinger_lower": indicators["bollinger_lower"],
        "signal": signal,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FATB Hermes Agent — Crypto Data Fetcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 tools/data/crypto_data.py BTC/USDT\n"
            "  python3 tools/data/crypto_data.py ETH/USDT --timeframe 4h\n"
        ),
    )
    parser.add_argument("symbol", help="Trading pair symbol, e.g. BTC/USDT")
    parser.add_argument(
        "--timeframe",
        default=DEFAULT_TIMEFRAME,
        help=f"OHLCV timeframe (default: {DEFAULT_TIMEFRAME})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        result = get_crypto_signal(symbol=args.symbol, timeframe=args.timeframe)
        print(json.dumps(result, indent=2))
    except Exception as exc:  # noqa: BLE001
        error_payload = {
            "error": str(exc),
            "symbol": args.symbol,
            "timeframe": args.timeframe,
        }
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
