"""
FATB Hermes Agent — Execution Package
=======================================
Exports the public API of the execution layer.

  RiskGuard  : validate_trade()         — pre-trade risk validation
  AccountState: get_account_snapshot()  — live account state
  Executors  : execute_crypto_order()   — crypto paper/live execution
               execute_forex_order()    — forex OANDA/MT5 execution
"""

from tools.execution.risk_guard import validate_trade
from tools.execution.account_state import (
    get_account_snapshot,
    init_db,
    log_order,
)
from tools.execution.crypto_executor import execute_crypto_order
from tools.execution.forex_executor import execute_forex_order

__all__ = [
    "validate_trade",
    "get_account_snapshot",
    "init_db",
    "log_order",
    "execute_crypto_order",
    "execute_forex_order",
]
