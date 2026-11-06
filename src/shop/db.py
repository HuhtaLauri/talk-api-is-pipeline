"""The source system: a tiny OLTP database for 'Acme Shop'.

Every write goes through `apply()`, which updates the entity table AND appends
to the `events` table in the same transaction (a transactional outbox). The
v1/v2 APIs pretend that log doesn't exist; v3 exposes it.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("TALK_DATA", ROOT / "data"))
DB_PATH = DATA / "source.db"

COLUMNS = {
    "customers": ["id", "name", "email", "plan", "country", "mrr", "created_at", "updated_at"],
    "orders": ["id", "customer_id", "amount", "status", "created_at", "updated_at"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, email TEXT, plan TEXT, country TEXT, mrr REAL,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS customers_updated ON customers(updated_at);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER, amount REAL, status TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS orders_updated ON orders(updated_at);

-- What a `deleted_at` column / soft delete would give you. Only v2 (opt-in) exposes it.
CREATE TABLE IF NOT EXISTS tombstones (
    entity TEXT NOT NULL, id INTEGER NOT NULL, deleted_at TEXT NOT NULL,
    PRIMARY KEY (entity, id)
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    op TEXT NOT NULL,           -- insert | update | delete
    data TEXT NOT NULL          -- JSON after-image (before-image for deletes)
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts_event(t: datetime) -> str:
    """Event timestamps: microsecond precision."""
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ts_row(t: datetime) -> str:
    """Row updated_at: SECOND precision. Deliberately. (See v2.)"""
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    # backfill for databases seeded before tombstones existed
    con.execute("""INSERT OR IGNORE INTO tombstones
                   SELECT entity, entity_id, substr(ts, 1, 19) || 'Z' FROM events WHERE op = 'delete'""")


def apply(con: sqlite3.Connection, entity: str, op: str, row: dict, at: datetime) -> dict:
    """Write one change + its event. Caller owns the transaction."""
    cols = COLUMNS[entity]
    if op == "insert":
        row = {**row, "created_at": ts_row(at), "updated_at": ts_row(at)}
        fields = [c for c in cols if c != "id"]
        cur = con.execute(
            f"INSERT INTO {entity} ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
            [row[c] for c in fields],
        )
        row["id"] = cur.lastrowid
    elif op == "update":
        row = {**row, "updated_at": ts_row(at)}
        fields = [c for c in cols if c not in ("id", "created_at")]
        con.execute(
            f"UPDATE {entity} SET {','.join(f'{c}=?' for c in fields)} WHERE id=?",
            [row[c] for c in fields] + [row["id"]],
        )
    elif op == "delete":
        con.execute(f"DELETE FROM {entity} WHERE id=?", (row["id"],))
        con.execute("INSERT OR REPLACE INTO tombstones VALUES (?,?,?)", (entity, row["id"], ts_row(at)))
    else:
        raise ValueError(op)
    con.execute(
        "INSERT INTO events (ts, entity, entity_id, op, data) VALUES (?,?,?,?,?)",
        (ts_event(at), entity, row["id"], op, json.dumps({c: row.get(c) for c in cols})),
    )
    return row
