"""
FATB Hermes Agent — Forex Executor
====================================
Places forex orders with a mandatory Risk Guard pre-check.

Backend Selection (set in config/execution_config.json → forex.forex_backend):
  "oanda"  — OANDA v20 REST API (cross-platform, works on macOS/Linux).
             Requires: oanda_account_id + oanda_api_key in execution_config.json.
             Use oanda_environment = "practice" for demo account.
  "mt5"    — MetaTrader 5 via the MetaTrader5 Python package.
             Windows-only. Requires MT5 terminal to be running locally.

Modes:
  paper   — Uses OANDA practice or MT5 demo. No real money at risk.
  live    — Uses OANDA live or MT5 live server. Real money.

CLI Usage:
    python3 tools/execution/forex_executor.py \
        --symbol EURUSD --action BUY --lot-size 0.1 --sl 1.0750 --tp 1.0900

    python3 tools/execution/forex_executor.py \
        --symbol USDJPY --action SELL --lot-size 0.05 --sl 152.50 --tp 148.00
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
# Path bootstrap
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools.execution.account_state import init_db, log_order
from tools.execution.risk_guard import validate_trade

import json as _json
import requests as _requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = _REPO / "config" / "execution_config.json"


def _load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return _json.load(f)


# ---------------------------------------------------------------------------
# Ticker normalisation
# ---------------------------------------------------------------------------

def _normalise_symbol(symbol: str) -> str:
    """Normalise symbol: EURUSD, EUR/USD → EURUSD (OANDA format)."""
    return symbol.upper().replace("/", "").replace("=X", "")


# ---------------------------------------------------------------------------
# OANDA REST backend
# ---------------------------------------------------------------------------

_OANDA_ENDPOINTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


def _oanda_fetch_price(instrument: str, env: str, api_key: str) -> float:
    """Fetch OANDA mid price for instrument."""
    base_url = _OANDA_ENDPOINTS.get(env, _OANDA_ENDPOINTS["practice"])
    url = f"{base_url}/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    params = {"count": 1, "granularity": "S5", "price": "M"}
    resp = _requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    candles = resp.json().get("candles", [])
    if not candles:
        raise RuntimeError(f"No price data returned for {instrument}")
    mid = candles[-1]["mid"]
    return float(mid["c"])


def _oanda_place_order(
    instrument: str,
    action: str,
    lot_size: float,
    sl: float,
    tp: float,
    account_id: str,
    api_key: str,
    env: str,
) -> dict[str, Any]:
    """Place a market order with SL/TP on OANDA v20."""
    base_url = _OANDA_ENDPOINTS.get(env, _OANDA_ENDPOINTS["practice"])
    url = f"{base_url}/v3/accounts/{account_id}/orders"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # OANDA uses negative units for SELL
    units = lot_size * 100_000  # 1 lot = 100,000 units
    if action == "SELL":
        units = -units

    payload = {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(int(units)),
            "stopLossOnFill": {"price": str(round(sl, 5))},
            "takeProfitOnFill": {"price": str(round(tp, 5))},
            "timeInForce": "FOK",
        }
    }

    resp = _requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    fill = data.get("orderFillTransaction", {})
    order_id = fill.get("id") or data.get("relatedTransactionIDs", ["N/A"])[0]
    filled_price = float(fill.get("price", 0))

    return {
        "order_id": str(order_id),
        "filled_price": filled_price,
        "raw": data,
    }


def _execute_oanda(
    instrument: str,
    action: str,
    lot_size: float,
    sl: float,
    tp: float,
    forex_cfg: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Full OANDA execution path."""
    account_id = forex_cfg.get("oanda_account_id", "")
    api_key = forex_cfg.get("oanda_api_key", "")
    env = forex_cfg.get("oanda_environment", "practice")

    # Paper mode always forces practice environment
    if mode == "paper":
        env = "practice"

    if not account_id or not api_key:
        # No credentials — simulate locally (full paper simulation)
        return _simulate_oanda(instrument, action, lot_size, sl, tp)

    filled = _oanda_place_order(instrument, action, lot_size, sl, tp, account_id, api_key, env)
    return {
        "status": "EXECUTED",
        "order_id": filled["order_id"],
        "instrument": instrument,
        "action": action,
        "lot_size": lot_size,
        "filled_price": filled["filled_price"],
        "sl": sl,
        "tp": tp,
        "backend": "oanda",
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _simulate_oanda(
    instrument: str,
    action: str,
    lot_size: float,
    sl: float,
    tp: float,
) -> dict[str, Any]:
    """Simulate OANDA paper fill (no credentials needed)."""
    # Derive a plausible simulated price from SL/TP midpoint
    sim_price = round((sl + tp) / 2, 5)
    order_id = f"PAPER-FX-{uuid.uuid4().hex[:12].upper()}"
    return {
        "status": "EXECUTED",
        "order_id": order_id,
        "instrument": instrument,
        "action": action,
        "lot_size": lot_size,
        "filled_price": sim_price,
        "sl": sl,
        "tp": tp,
        "backend": "oanda-simulated",
        "mode": "paper",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# MT5 backend (Windows only)
# ---------------------------------------------------------------------------

def _execute_mt5(
    instrument: str,
    action: str,
    lot_size: float,
    sl: float,
    tp: float,
    forex_cfg: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """
    MetaTrader 5 execution path.
    Only available on Windows with the MetaTrader5 package installed.
    """
    try:
        import MetaTrader5 as mt5  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "MetaTrader5 package not available. "
            "Install on Windows: pip install MetaTrader5, "
            "or switch forex_backend to 'oanda' in execution_config.json"
        )

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

    symbol_info = mt5.symbol_info(instrument)
    if symbol_info is None:
        mt5.shutdown()
        raise RuntimeError(f"Symbol {instrument} not found in MT5")

    if not symbol_info.visible:
        mt5.symbol_select(instrument, True)

    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    price = mt5.symbol_info_tick(instrument).ask if action == "BUY" else mt5.symbol_info_tick(instrument).bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": instrument,
        "volume": float(lot_size),
        "type": order_type,
        "price": price,
        "sl": float(sl),
        "tp": float(tp),
        "magic": int(forex_cfg.get("magic_number", 202608)),
        "comment": "FATB-Hermes",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    mt5.shutdown()

    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else "N/A"
        raise RuntimeError(f"MT5 order_send failed. Retcode: {retcode}")

    return {
        "status": "EXECUTED",
        "order_id": str(result.order),
        "instrument": instrument,
        "action": action,
        "lot_size": lot_size,
        "filled_price": float(result.price),
        "sl": sl,
        "tp": tp,
        "backend": "mt5",
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

def execute_forex_order(
    symbol: str,
    action: str,
    lot_size: float,
    sl: float,
    tp: float,
) -> dict[str, Any]:
    """
    Full forex execution pipeline:
      1. Normalise symbol
      2. Fetch indicative price (for risk calculation)
      3. Run risk guard
      4. Execute via OANDA or MT5
      5. Log to SQLite
      6. Return result dict
    """
    config = _load_config()
    mode = config.get("mode", "paper")
    forex_cfg = config["forex"]
    backend = forex_cfg.get("forex_backend", "oanda")

    instrument = _normalise_symbol(symbol)

    # --- Indicative price for risk calculation ---
    # Use SL/TP midpoint if no credentials available
    try:
        if backend == "oanda" and forex_cfg.get("oanda_api_key"):
            env = forex_cfg.get("oanda_environment", "practice")
            entry_price = _oanda_fetch_price(
                instrument, env, forex_cfg["oanda_api_key"]
            )
        else:
            # Fallback: midpoint of SL and TP
            entry_price = round((sl + tp) / 2, 5)
    except Exception:
        entry_price = round((sl + tp) / 2, 5)

    # --- Risk Guard ---
    verdict = validate_trade(
        asset_class="forex",
        symbol=instrument,
        action=action,
        amount=lot_size,
        entry_price=entry_price,
        sl=sl,
        tp=tp,
    )

    ts = datetime.now(timezone.utc).isoformat()

    if not verdict["approved"]:
        result: dict[str, Any] = {
            "status": "REJECTED",
            "order_id": None,
            "instrument": instrument,
            "action": action,
            "lot_size": lot_size,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
            "reason": verdict["reason"],
            "checks": verdict["checks"],
            "timestamp": ts,
        }
        conn = init_db()
        log_order(
            conn,
            {
                "order_id": f"REJECTED-FX-{uuid.uuid4().hex[:8].upper()}",
                "instrument": instrument,
                "asset_class": "forex",
                "action": action,
                "amount": lot_size,
                "filled_price": entry_price,
                "sl": sl,
                "tp": tp,
                "status": "REJECTED",
                "pnl": None,
                "closed_at": None,
                "timestamp": ts,
            },
        )
        conn.close()
        return result

    # --- Execute ---
    try:
        if backend == "mt5":
            result = _execute_mt5(instrument, action, lot_size, sl, tp, forex_cfg, mode)
        else:
            result = _execute_oanda(instrument, action, lot_size, sl, tp, forex_cfg, mode)
    except Exception as exc:
        result = {
            "status": "FAILED",
            "order_id": None,
            "instrument": instrument,
            "action": action,
            "lot_size": lot_size,
            "sl": sl,
            "tp": tp,
            "reason": str(exc),
            "timestamp": ts,
        }

    # --- Log to SQLite ---
    conn = init_db()
    log_order(
        conn,
        {
            "order_id": result.get("order_id") or f"FAILED-FX-{uuid.uuid4().hex[:8].upper()}",
            "instrument": instrument,
            "asset_class": "forex",
            "action": action,
            "amount": lot_size,
            "filled_price": result.get("filled_price", entry_price),
            "sl": sl,
            "tp": tp,
            "status": result["status"],
            "pnl": None,
            "closed_at": None,
            "timestamp": result.get("timestamp", ts),
        },
    )
    conn.close()

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FATB Hermes Agent — Forex Executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 tools/execution/forex_executor.py \\\n"
            "    --symbol EURUSD --action BUY --lot-size 0.1 --sl 1.0750 --tp 1.0900\n"
            "  python3 tools/execution/forex_executor.py \\\n"
            "    --symbol USDJPY --action SELL --lot-size 0.05 --sl 152.50 --tp 148.00\n"
        ),
    )
    parser.add_argument("--symbol", required=True, help="Forex pair, e.g. EURUSD or EURUSD=X")
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--lot-size", required=True, type=float, dest="lot_size")
    parser.add_argument("--sl", required=True, type=float, help="Stop-loss price")
    parser.add_argument("--tp", required=True, type=float, help="Take-profit price")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = execute_forex_order(
        symbol=args.symbol,
        action=args.action,
        lot_size=args.lot_size,
        sl=args.sl,
        tp=args.tp,
    )
    print(json.dumps(result, indent=2))
    if result["status"] not in ("EXECUTED",):
        sys.exit(1)


if __name__ == "__main__":
    main()
