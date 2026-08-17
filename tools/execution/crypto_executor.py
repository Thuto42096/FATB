"""
FATB Hermes Agent — Crypto Executor
=====================================
Places crypto orders via CCXT with a mandatory Risk Guard pre-check.

Modes:
  paper  — Simulates fill at live market price. No API keys required.
           Order is recorded in live_orders.db exactly as a real trade would be.
  live   — Places a real market order on Binance (API keys required in config).

CLI Usage:
    python3 tools/execution/crypto_executor.py \
        --symbol BTC/USDT --action BUY --amount 0.01 --sl 60000 --tp 68000

    python3 tools/execution/crypto_executor.py \
        --symbol ETH/USDT --action SELL --amount 0.05 --sl 2100 --tp 1800
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap for intra-package imports
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import ccxt
from tools.execution.account_state import init_db, log_order, get_account_snapshot
from tools.execution.risk_guard import validate_trade

import json as _json

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = _REPO / "config" / "execution_config.json"


def _load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return _json.load(f)


# ---------------------------------------------------------------------------
# Live price fetch (no auth required — public endpoint)
# ---------------------------------------------------------------------------

def _fetch_live_price(symbol: str, exchange_name: str = "binance") -> float:
    """Fetch best bid/ask midpoint from public ticker."""
    exchange_cls = getattr(ccxt, exchange_name)
    exchange = exchange_cls({"enableRateLimit": True})
    ticker = exchange.fetch_ticker(symbol)
    # Use last traded price; fall back to bid/ask midpoint
    if ticker.get("last"):
        return float(ticker["last"])
    bid = float(ticker.get("bid") or 0)
    ask = float(ticker.get("ask") or 0)
    if bid and ask:
        return round((bid + ask) / 2, 8)
    raise RuntimeError(f"Could not determine price for {symbol}")


# ---------------------------------------------------------------------------
# Paper execution (no credentials)
# ---------------------------------------------------------------------------

def _execute_paper(
    symbol: str,
    action: str,
    amount: float,
    sl: float,
    tp: float,
    exchange_name: str,
) -> dict[str, Any]:
    """Simulate order fill at live market price."""
    filled_price = _fetch_live_price(symbol, exchange_name)
    order_id = f"PAPER-{uuid.uuid4().hex[:12].upper()}"

    return {
        "status": "EXECUTED",
        "order_id": order_id,
        "symbol": symbol,
        "action": action,
        "amount": amount,
        "filled_price": filled_price,
        "sl": sl,
        "tp": tp,
        "mode": "paper",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Live execution (API keys required)
# ---------------------------------------------------------------------------

def _execute_live(
    symbol: str,
    action: str,
    amount: float,
    sl: float,
    tp: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Place a real market order via CCXT."""
    crypto_cfg = config["crypto"]
    exchange_name = crypto_cfg.get("default_exchange", "binance")

    exchange_cls = getattr(ccxt, exchange_name)
    exchange = exchange_cls(
        {
            "apiKey": crypto_cfg.get("api_key", ""),
            "secret": crypto_cfg.get("api_secret", ""),
            "enableRateLimit": True,
        }
    )

    if crypto_cfg.get("use_testnet", True):
        exchange.set_sandbox_mode(True)

    side = action.lower()  # "buy" | "sell"
    order = exchange.create_order(
        symbol=symbol,
        type="market",
        side=side,
        amount=amount,
        params={
            "stopLoss": {"type": "market", "stopPrice": sl},
            "takeProfit": {"type": "market", "stopPrice": tp},
        },
    )

    return {
        "status": "EXECUTED",
        "order_id": str(order.get("id", "N/A")),
        "symbol": symbol,
        "action": action,
        "amount": amount,
        "filled_price": float(order.get("average") or order.get("price") or 0),
        "sl": sl,
        "tp": tp,
        "mode": "live",
        "raw_exchange_response": order,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

def execute_crypto_order(
    symbol: str,
    action: str,
    amount: float,
    sl: float,
    tp: float,
) -> dict[str, Any]:
    """
    Full execution pipeline:
      1. Load config & fetch live price
      2. Run risk guard
      3. Execute (paper or live)
      4. Log to SQLite
      5. Return result dict
    """
    config = _load_config()
    mode = config.get("mode", "paper")
    exchange_name = config["crypto"].get("default_exchange", "binance")

    # --- Fetch live price for risk calculation ---
    try:
        entry_price = _fetch_live_price(symbol, exchange_name)
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason": f"Could not fetch live price: {exc}",
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # --- Risk Guard ---
    verdict = validate_trade(
        asset_class="crypto",
        symbol=symbol,
        action=action,
        amount=amount,
        entry_price=entry_price,
        sl=sl,
        tp=tp,
    )

    if not verdict["approved"]:
        result = {
            "status": "REJECTED",
            "order_id": None,
            "symbol": symbol,
            "action": action,
            "amount": amount,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
            "reason": verdict["reason"],
            "checks": verdict["checks"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Log rejection to DB for audit trail
        conn = init_db()
        log_order(
            conn,
            {
                "order_id": f"REJECTED-{uuid.uuid4().hex[:8].upper()}",
                "instrument": symbol,
                "asset_class": "crypto",
                "action": action,
                "amount": amount,
                "filled_price": entry_price,
                "sl": sl,
                "tp": tp,
                "status": "REJECTED",
                "pnl": None,
                "closed_at": None,
                "timestamp": result["timestamp"],
            },
        )
        conn.close()
        return result

    # --- Execute ---
    try:
        if mode == "paper":
            result = _execute_paper(symbol, action, amount, sl, tp, exchange_name)
        else:
            result = _execute_live(symbol, action, amount, sl, tp, config)
    except Exception as exc:
        result = {
            "status": "FAILED",
            "order_id": None,
            "symbol": symbol,
            "action": action,
            "amount": amount,
            "sl": sl,
            "tp": tp,
            "reason": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # --- Log to SQLite ---
    conn = init_db()
    log_order(
        conn,
        {
            "order_id": result.get("order_id") or f"FAILED-{uuid.uuid4().hex[:8].upper()}",
            "instrument": symbol,
            "asset_class": "crypto",
            "action": action,
            "amount": amount,
            "filled_price": result.get("filled_price", entry_price),
            "sl": sl,
            "tp": tp,
            "status": result["status"],
            "pnl": None,
            "closed_at": None,
            "timestamp": result["timestamp"],
        },
    )
    conn.close()

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FATB Hermes Agent — Crypto Executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 tools/execution/crypto_executor.py \\\n"
            "    --symbol BTC/USDT --action BUY --amount 0.01 --sl 60000 --tp 68000\n"
            "  python3 tools/execution/crypto_executor.py \\\n"
            "    --symbol ETH/USDT --action SELL --amount 0.05 --sl 2100 --tp 1800\n"
        ),
    )
    parser.add_argument("--symbol", required=True, help="Trading pair, e.g. BTC/USDT")
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--amount", required=True, type=float, help="Amount in base asset")
    parser.add_argument("--sl", required=True, type=float, help="Stop-loss price")
    parser.add_argument("--tp", required=True, type=float, help="Take-profit price")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = execute_crypto_order(
        symbol=args.symbol,
        action=args.action,
        amount=args.amount,
        sl=args.sl,
        tp=args.tp,
    )
    print(json.dumps(result, indent=2))
    if result["status"] not in ("EXECUTED",):
        sys.exit(1)


if __name__ == "__main__":
    main()
