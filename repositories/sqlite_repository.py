import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('CD', 'LOCAL'))
);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    tax_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    minimum_stock INTEGER NOT NULL CHECK (minimum_stock >= 0),
    supplier_id INTEGER REFERENCES suppliers(id)
);
CREATE TABLE IF NOT EXISTS stock (
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (location_id, product_id)
);
CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity_delta INTEGER NOT NULL,
    movement_type TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS replenishment_orders (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    origin_id INTEGER NOT NULL REFERENCES locations(id),
    destination_id INTEGER NOT NULL REFERENCES locations(id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replenishment_lines (
    order_id INTEGER NOT NULL REFERENCES replenishment_orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    order_id INTEGER NOT NULL REFERENCES replenishment_orders(id),
    dispatch_note TEXT NOT NULL UNIQUE,
    seal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    dispatched_at TEXT,
    received_at TEXT,
    seal_intact INTEGER,
    dispatch_note_ok INTEGER
);
CREATE TABLE IF NOT EXISTS package_lines (
    package_id INTEGER NOT NULL REFERENCES packages(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (package_id, product_id)
);
CREATE TABLE IF NOT EXISTS package_events (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS package_events_no_update
BEFORE UPDATE ON package_events BEGIN SELECT RAISE(ABORT, 'package history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS package_events_no_delete
BEFORE DELETE ON package_events BEGIN SELECT RAISE(ABORT, 'package history is immutable'); END;
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    receipt TEXT NOT NULL UNIQUE,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    sold_at TEXT NOT NULL,
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0)
);
CREATE TABLE IF NOT EXISTS sale_lines (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0),
    unit_price_cents INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    method TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
);
CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    returned_at TEXT NOT NULL,
    total_cents INTEGER NOT NULL CHECK (total_cents > 0)
);
CREATE TABLE IF NOT EXISTS return_lines (
    id INTEGER PRIMARY KEY,
    return_id INTEGER NOT NULL REFERENCES returns(id),
    sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    restocked INTEGER NOT NULL CHECK (restocked IN (0, 1))
);
CREATE TABLE IF NOT EXISTS return_payments (
    id INTEGER PRIMARY KEY,
    return_id INTEGER NOT NULL REFERENCES returns(id),
    method TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
);
"""


class SQLiteRepository:
    """Repository pattern: the application accesses persistence through this gateway."""

    def __init__(self, database_path: str | Path = "data/retail.db") -> None:
        if str(database_path) != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN")
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, parameters)

    def one(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        return self.execute(sql, parameters).fetchone()

    def all(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        return self.execute(sql, parameters).fetchall()

    def close(self) -> None:
        self.connection.close()