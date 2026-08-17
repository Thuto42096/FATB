"""
FATB Hermes Agent — Risk Guard Engine
======================================
Every trade request MUST pass through this module before any broker API
is called. Implements four sequential pre-trade validation gates:

  1. SL/TP Presence Guard    — require_sl_tp rule
  2. Position Cap Guard      — max_open_positions rule
  3. Drawdown Guard          — max_daily_drawdown_pct rule
  4. Account Balance Check   — max_account_risk_pct rule ($ risk vs balance)

Returns a structured verdict dict for downstream executors to act on.

CLI Usage:
    python3 tools/execution/risk_guard.py \
        --asset-class crypto \
        --symbol BTC/USDT \
        --action BUY \
        --amount 0.01 \
        --price 65000 \
        --sl 60000 \
        --tp 68000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Intra-package imports (works both as module and CLI)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]

if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools.execution.account_state import (
    get_account_snapshot,
    init_db,
)

import json as _json

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
CONFIG_PATH = _REPO / "config" / "execution_config.json"


def _load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return _json.load(f)


# ---------------------------------------------------------------------------
# Individual guard functions
# ---------------------------------------------------------------------------

def _check_sl_tp(
    sl: float | None,
    tp: float | None,
    require_sl_tp: bool,
) -> dict[str, Any]:
    """Gate 1: Ensure SL and TP are provided when required."""
    if not require_sl_tp:
        return {"passed": True, "detail": "SL/TP not required by config"}
    if sl is None or tp is None:
        return {
            "passed": False,
            "detail": f"SL and TP are required. Got sl={sl}, tp={tp}",
        }
    return {"passed": True, "detail": f"SL={sl}, TP={tp} present"}


def _check_position_cap(
    open_count: int,
    max_open: int,
) -> dict[str, Any]:
    """Gate 2: Reject if position cap is reached."""
    if open_count >= max_open:
        return {
            "passed": False,
            "detail": (
                f"Open position cap reached: {open_count}/{max_open} positions open"
            ),
        }
    return {
        "passed": True,
        "detail": f"Position count OK: {open_count}/{max_open}",
    }


def _check_drawdown(
    daily_drawdown_pct: float,
    max_drawdown_pct: float,
) -> dict[str, Any]:
    """Gate 3: Reject if daily drawdown limit has been breached."""
    if daily_drawdown_pct >= max_drawdown_pct:
        return {
            "passed": False,
            "detail": (
                f"Daily drawdown limit breached: "
                f"{daily_drawdown_pct:.2f}% >= {max_drawdown_pct:.2f}%"
            ),
        }
    return {
        "passed": True,
        "detail": f"Drawdown OK: {daily_drawdown_pct:.2f}% < {max_drawdown_pct:.2f}%",
    }


def _check_account_risk(
    amount: float,
    entry_price: float,
    sl: float | None,
    balance: float,
    max_risk_pct: float,
    asset_class: str,
) -> dict[str, Any]:
    """
    Gate 4: Dollar-risk check.

    Dollar risk = |entry_price - sl| * amount  (crypto: amount in base units)
                = |entry_price - sl| * amount * pip_value  (forex: pip-adjusted)

    The risk must not exceed max_risk_pct % of the current account balance.
    """
    if sl is None:
        return {"passed": True, "detail": "SL absent — skipping risk % check"}

    if balance <= 0:
        return {"passed": False, "detail": "Account balance is zero or negative"}

    sl_distance = abs(entry_price - sl)

    if asset_class == "crypto":
        dollar_risk = sl_distance * amount
    else:
        # Forex: amount is lot_size; 1 standard lot = 100,000 units
        # Approximate USD risk = sl_distance * lot_size * 100_000
        dollar_risk = sl_distance * amount * 100_000

    risk_pct = (dollar_risk / balance) * 100

    if risk_pct > max_risk_pct:
        return {
            "passed": False,
            "detail": (
                f"Account risk too high: {risk_pct:.2f}% > {max_risk_pct:.2f}% max. "
                f"Dollar risk=${dollar_risk:.2f} on ${balance:.2f} balance"
            ),
            "dollar_risk": round(dollar_risk, 2),
            "risk_pct": round(risk_pct, 4),
        }

    return {
        "passed": True,
        "detail": (
            f"Risk OK: {risk_pct:.4f}% (${dollar_risk:.2f}) "
            f"<= {max_risk_pct:.2f}% max"
        ),
        "dollar_risk": round(dollar_risk, 2),
        "risk_pct": round(risk_pct, 4),
    }


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate_trade(
    *,
    asset_class: str,          # "crypto" | "forex"
    symbol: str,
    action: str,               # "BUY" | "SELL"
    amount: float,             # base units (crypto) or lot size (forex)
    entry_price: float,        # current market price
    sl: float | None = None,
    tp: float | None = None,
) -> dict[str, Any]:
    """
    Run all four risk gates against current account state.

    Returns:
        {
          "approved": bool,
          "reason": str,        # human-readable summary
          "checks": {           # individual gate results
            "sl_tp": {...},
            "position_cap": {...},
            "drawdown": {...},
            "account_risk": {...}
          }
        }
    """
    config = _load_config()
    rules = config["risk_rules"]

    snapshot = get_account_snapshot()

    checks: dict[str, Any] = {}

    # Gate 1 — SL/TP presence
    checks["sl_tp"] = _check_sl_tp(sl, tp, rules["require_sl_tp"])
    if not checks["sl_tp"]["passed"]:
        return _verdict(False, checks["sl_tp"]["detail"], checks)

    # Gate 2 — position cap
    checks["position_cap"] = _check_position_cap(
        snapshot["open_position_count"], rules["max_open_positions"]
    )
    if not checks["position_cap"]["passed"]:
        return _verdict(False, checks["position_cap"]["detail"], checks)

    # Gate 3 — daily drawdown
    checks["drawdown"] = _check_drawdown(
        snapshot["daily_drawdown_pct"], rules["max_daily_drawdown_pct"]
    )
    if not checks["drawdown"]["passed"]:
        return _verdict(False, checks["drawdown"]["detail"], checks)

    # Gate 4 — account risk %
    checks["account_risk"] = _check_account_risk(
        amount=amount,
        entry_price=entry_price,
        sl=sl,
        balance=snapshot["current_balance"],
        max_risk_pct=rules["max_account_risk_pct"],
        asset_class=asset_class,
    )
    if not checks["account_risk"]["passed"]:
        return _verdict(False, checks["account_risk"]["detail"], checks)

    return _verdict(True, "Passed all risk checks", checks)


def _verdict(
    approved: bool,
    reason: str,
    checks: dict[str, Any],
) -> dict[str, Any]:
    return {"approved": approved, "reason": reason, "checks": checks}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FATB — Risk Guard Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 tools/execution/risk_guard.py \\\n"
            "    --asset-class crypto --symbol BTC/USDT \\\n"
            "    --action BUY --amount 0.01 --price 65000 \\\n"
            "    --sl 60000 --tp 68000\n"
        ),
    )
    parser.add_argument("--asset-class", required=True, choices=["crypto", "forex"])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--amount", required=True, type=float)
    parser.add_argument("--price", required=True, type=float, help="Entry price")
    parser.add_argument("--sl", type=float, default=None)
    parser.add_argument("--tp", type=float, default=None)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = validate_trade(
        asset_class=args.asset_class,
        symbol=args.symbol,
        action=args.action,
        amount=args.amount,
        entry_price=args.price,
        sl=args.sl,
        tp=args.tp,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
