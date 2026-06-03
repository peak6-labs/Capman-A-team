"""SQLite audit journal — an append-only record of everything the system does.

Every signal, the gate outcome (compliance/risk), every order, and every fill is
written here so trading activity is fully auditable after the fact. This matters
especially for the PEAK6 compliance requirement: rejections are logged with reasons.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "trader.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_ts    INTEGER NOT NULL,
    closed_ts    INTEGER,
    ticker       TEXT NOT NULL,
    side         TEXT NOT NULL,
    action       TEXT DEFAULT 'sell',
    entry_price  TEXT NOT NULL,
    target_price TEXT NOT NULL,
    count        INTEGER NOT NULL,
    order_id     TEXT,
    expiry       TEXT,
    confidence   REAL,
    status       TEXT DEFAULT 'open',  -- open | closed | resolved
    close_reason TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    source        TEXT,              -- strategy/agent name
    market_ticker TEXT,
    side          TEXT,              -- yes/no
    target_price  TEXT,
    fair_prob     REAL,
    confidence    REAL,
    max_contracts TEXT,
    rationale     TEXT,
    outcome       TEXT NOT NULL,     -- placed | dry_run | rejected
    gate          TEXT,              -- compliance | risk | null
    reason        TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    client_order_id TEXT UNIQUE,
    kalshi_order_id TEXT,
    market_ticker   TEXT,
    side            TEXT,
    action          TEXT,
    order_type      TEXT,
    count           TEXT,
    price           TEXT,
    status          TEXT,
    raw             TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    fill_id         TEXT UNIQUE,
    market_ticker   TEXT,
    side            TEXT,
    count           TEXT,
    price           TEXT,
    raw             TEXT
);

CREATE TABLE IF NOT EXISTS reference_quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    source          TEXT NOT NULL,
    event_key       TEXT,
    market_ticker   TEXT,
    question        TEXT,
    yes_prob        TEXT NOT NULL,
    confidence      REAL,
    raw             TEXT
);

CREATE TABLE IF NOT EXISTS relative_value_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    source          TEXT NOT NULL,
    market_ticker   TEXT NOT NULL,
    title           TEXT,
    side            TEXT NOT NULL,
    action          TEXT NOT NULL,
    kalshi_price    TEXT NOT NULL,
    reference_prob  TEXT NOT NULL,
    edge            TEXT NOT NULL,
    confidence      REAL,
    outcome         TEXT NOT NULL,
    reason          TEXT
);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


class Journal:
    def __init__(self, db_path: Optional[str] = None) -> None:
        path = Path(db_path) if db_path else DEFAULT_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(positions)")
        }
        if "action" not in columns:
            self._conn.execute("ALTER TABLE positions ADD COLUMN action TEXT DEFAULT 'sell'")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def record_decision(
        self,
        *,
        outcome: str,
        source: Optional[str] = None,
        market_ticker: Optional[str] = None,
        side: Optional[str] = None,
        target_price: Optional[Any] = None,
        fair_prob: Optional[float] = None,
        confidence: Optional[float] = None,
        max_contracts: Optional[Any] = None,
        rationale: Optional[str] = None,
        gate: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO decisions
               (ts, source, market_ticker, side, target_price, fair_prob, confidence,
                max_contracts, rationale, outcome, gate, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _now_ms(), source, market_ticker, side,
                None if target_price is None else str(target_price),
                fair_prob, confidence,
                None if max_contracts is None else str(max_contracts),
                rationale, outcome, gate, reason,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def record_order(self, order: Dict[str, Any]) -> int:
        cur = self._conn.execute(
            """INSERT OR REPLACE INTO orders
               (ts, client_order_id, kalshi_order_id, market_ticker, side, action,
                order_type, count, price, status, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _now_ms(),
                order.get("client_order_id"),
                order.get("kalshi_order_id"),
                order.get("market_ticker"),
                order.get("side"),
                order.get("action"),
                order.get("order_type"),
                None if order.get("count") is None else str(order.get("count")),
                None if order.get("price") is None else str(order.get("price")),
                order.get("status"),
                json.dumps(order.get("raw")) if order.get("raw") is not None else None,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def record_fill(self, fill: Dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO fills
               (ts, fill_id, market_ticker, side, count, price, raw)
               VALUES (?,?,?,?,?,?,?)""",
            (
                _now_ms(),
                fill.get("fill_id"),
                fill.get("market_ticker"),
                fill.get("side"),
                None if fill.get("count") is None else str(fill.get("count")),
                None if fill.get("price") is None else str(fill.get("price")),
                json.dumps(fill.get("raw")) if fill.get("raw") is not None else None,
            ),
        )
        self._conn.commit()

    def record_reference_quote(self, quote: Dict[str, Any]) -> int:
        cur = self._conn.execute(
            """INSERT INTO reference_quotes
               (ts, source, event_key, market_ticker, question, yes_prob, confidence, raw)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                _now_ms(),
                quote.get("source"),
                quote.get("event_key"),
                quote.get("market_ticker"),
                quote.get("question"),
                str(quote.get("yes_prob")),
                quote.get("confidence"),
                json.dumps(quote.get("raw")) if quote.get("raw") is not None else None,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def record_relative_value_signal(self, signal: Dict[str, Any]) -> int:
        cur = self._conn.execute(
            """INSERT INTO relative_value_signals
               (ts, source, market_ticker, title, side, action, kalshi_price,
                reference_prob, edge, confidence, outcome, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _now_ms(),
                signal.get("source"),
                signal.get("market_ticker"),
                signal.get("title"),
                signal.get("side"),
                signal.get("action"),
                str(signal.get("kalshi_price")),
                str(signal.get("reference_prob")),
                str(signal.get("edge")),
                signal.get("confidence"),
                signal.get("outcome"),
                signal.get("reason"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def record_position(self, pos: Dict[str, Any]) -> int:
        cur = self._conn.execute(
            """INSERT INTO positions
               (opened_ts, ticker, side, action, entry_price, target_price, count,
                order_id, expiry, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                _now_ms(),
                pos["ticker"],
                pos["side"],
                pos.get("action", "sell"),
                str(pos["entry_price"]),
                str(pos["target_price"]),
                pos["count"],
                pos.get("order_id"),
                pos.get("expiry"),
                pos.get("confidence"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def open_positions(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY id"
            )
        )

    def closed_positions(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM positions WHERE status IN ('closed', 'resolved') ORDER BY id"
            )
        )

    def decisions_for_ticker(self, market_ticker: str) -> list[sqlite3.Row]:
        """All placed/dry_run decisions for a ticker, oldest first (for fair_prob recovery)."""
        return list(
            self._conn.execute(
                """SELECT * FROM decisions
                   WHERE market_ticker = ? AND outcome IN ('placed', 'dry_run')
                   ORDER BY ts""",
                (market_ticker,),
            )
        )

    def close_position(self, position_id: int, reason: str) -> None:
        self._conn.execute(
            "UPDATE positions SET status = 'closed', closed_ts = ?, close_reason = ? WHERE id = ?",
            (_now_ms(), reason, position_id),
        )
        self._conn.commit()

    def recent_decisions(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            )
        )
