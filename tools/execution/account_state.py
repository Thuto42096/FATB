"""
FATB Hermes Agent — Account State Module
==========================================
Provides a unified view of the paper/live trading account by reading
from the SQLite order ledger in storage/live_orders.db.

Computes:
  - Current simulated balance (paper: starts at $10,000)
  - Open position count
  - Today's realised PnL and drawdown %

CLI Usage:
    python3 tools/execution/account_state.py
    python3 tools/execution/account_state.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "storage" / "live_orders.db"

PAPER_STARTING_BALANCE: float = 10_000.0  # USD

# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT    NOT NULL,
    instrument   TEXT    NOT NULL,
    asset_class  TEXT    NOT NULL CHECK(asset_class IN ('crypto','forex')),
    action       TEXT    NOT NULL CHECK(action IN ('BUY','SELL')),
    amount       REAL    NOT NULL,
    filled_price REAL    NOT NULL,
    sl           REAL,
    tp           REAL,
    status       TEXT    NOT NULL CHECK(status IN ('EXECUTED','REJECTED','FAILED')),
    pnl          REAL,
    closed_at    TEXT,
    timestamp    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status    ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(timestamp);
CREATE INDEX IF NOT EXISTS idx_orders_instrument ON orders(instrument);
"""


def init_db() -> sqlite3.Connection:
    """Initialise the SQLite DB and return an open connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Ledger helpers
# ---------------------------------------------------------------------------

def _today_iso() -> str:
    return date.today().isoformat()


def get_open_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all EXECUTED orders that have not been closed (pnl IS NULL)."""
    cur = conn.execute(
        "SELECT * FROM orders WHERE status = 'EXECUTED' AND pnl IS NULL"
    )
    return [dict(row) for row in cur.fetchall()]


def get_today_closed_pnl(conn: sqlite3.Connection) -> float:
    """Sum of realised PnL for orders closed today."""
    today = _today_iso()
    cur = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM orders "
        "WHERE status = 'EXECUTED' AND pnl IS NOT NULL AND closed_at LIKE ?",
        (f"{today}%",),
    )
    return float(cur.fetchone()[0])


def get_all_realised_pnl(conn: sqlite3.Connection) -> float:
    """Total historical realised PnL across all closed trades."""
    cur = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM orders "
        "WHERE status = 'EXECUTED' AND pnl IS NOT NULL"
    )
    return float(cur.fetchone()[0])


def log_order(conn: sqlite3.Connection, order: dict[str, Any]) -> None:
    """Insert an order record into the ledger."""
    conn.execute(
        """
        INSERT INTO orders
            (order_id, instrument, asset_class, action, amount,
             filled_price, sl, tp, status, pnl, closed_at, timestamp)
        VALUES
            (:order_id, :instrument, :asset_class, :action, :amount,
             :filled_price, :sl, :tp, :status, :pnl, :closed_at, :timestamp)
        """,
        order,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Account snapshot
# ---------------------------------------------------------------------------

def get_account_snapshot() -> dict[str, Any]:
    """Return a full account state snapshot."""
    conn = init_db()

    open_positions = get_open_positions(conn)
    today_pnl = get_today_closed_pnl(conn)
    total_pnl = get_all_realised_pnl(conn)
    balance = PAPER_STARTING_BALANCE + total_pnl

    daily_drawdown_pct = 0.0
    if today_pnl < 0 and balance > 0:
        daily_drawdown_pct = round(abs(today_pnl) / balance * 100, 4)

    conn.close()

    return {
        "paper_starting_balance": PAPER_STARTING_BALANCE,
        "current_balance": round(balance, 2),
        "total_realised_pnl": round(total_pnl, 2),
        "today_realised_pnl": round(today_pnl, 2),
        "daily_drawdown_pct": daily_drawdown_pct,
        "open_position_count": len(open_positions),
        "open_positions": open_positions,
        "db_path": str(DB_PATH),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FATB — Account State Viewer")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as raw JSON (default: pretty-printed)",
    )
    return parser.parse_args()


def main() -> None:
    _parse_args()
    snapshot = get_account_snapshot()
    print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
