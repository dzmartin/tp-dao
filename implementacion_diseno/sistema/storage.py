from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS establishments (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('CD', 'SUCURSAL'))
);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    tax_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
    price_cents INTEGER NOT NULL CHECK (price_cents > 0)
);
CREATE TABLE IF NOT EXISTS product_suppliers (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    PRIMARY KEY (product_id, supplier_id)
);
CREATE TABLE IF NOT EXISTS inventory (
    establishment_id INTEGER NOT NULL REFERENCES establishments(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    minimum_stock INTEGER NOT NULL DEFAULT 0 CHECK (minimum_stock >= 0),
    PRIMARY KEY (establishment_id, product_id)
);
CREATE TABLE IF NOT EXISTS replenishment_batches (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replenishment_orders (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER REFERENCES replenishment_batches(id),
    source_id INTEGER NOT NULL REFERENCES establishments(id),
    destination_id INTEGER NOT NULL REFERENCES establishments(id),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pendiente',
    request_id INTEGER REFERENCES replenishment_requests(id)
);
CREATE TABLE IF NOT EXISTS replenishment_lines (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES replenishment_orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    requested INTEGER NOT NULL CHECK (requested > 0),
    UNIQUE (order_id, product_id)
);
CREATE TABLE IF NOT EXISTS replenishment_requests (
    id INTEGER PRIMARY KEY,
    requester_id INTEGER NOT NULL REFERENCES establishments(id),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pendiente',
    order_id INTEGER REFERENCES replenishment_orders(id)
);
CREATE TABLE IF NOT EXISTS replenishment_request_lines (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES replenishment_requests(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    requested INTEGER NOT NULL CHECK (requested > 0),
    fulfilled INTEGER NOT NULL DEFAULT 0 CHECK (fulfilled >= 0 AND fulfilled <= requested),
    UNIQUE (request_id, product_id)
);
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES replenishment_orders(id),
    dispatch_note TEXT NOT NULL UNIQUE,
    seal TEXT NOT NULL,
    status TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    dispatched_at TEXT,
    received_at TEXT,
    verified_at TEXT
);
CREATE TABLE IF NOT EXISTS package_lines (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    UNIQUE (package_id, product_id)
);
CREATE TABLE IF NOT EXISTS package_events (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logistics_audit (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    occurred_at TEXT NOT NULL,
    state TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS package_events_are_immutable_update
BEFORE UPDATE ON package_events
BEGIN
    SELECT RAISE(ABORT, 'package event history is immutable');
END;
CREATE TRIGGER IF NOT EXISTS package_events_are_immutable_delete
BEFORE DELETE ON package_events
BEGIN
    SELECT RAISE(ABORT, 'package event history is immutable');
END;
CREATE TRIGGER IF NOT EXISTS logistics_audit_is_immutable_update
BEFORE UPDATE ON logistics_audit
BEGIN
    SELECT RAISE(ABORT, 'logistics audit is immutable');
END;
CREATE TRIGGER IF NOT EXISTS logistics_audit_is_immutable_delete
BEFORE DELETE ON logistics_audit
BEGIN
    SELECT RAISE(ABORT, 'logistics audit is immutable');
END;
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    establishment_id INTEGER NOT NULL REFERENCES establishments(id),
    sold_at TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt TEXT NOT NULL UNIQUE,
    total_cents INTEGER NOT NULL CHECK (total_cents > 0)
);
CREATE TABLE IF NOT EXISTS sale_lines (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
    UNIQUE (sale_id, product_id)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    method TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    kind TEXT NOT NULL CHECK (kind IN ('cobro', 'reintegro')),
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    returned_at TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
);
CREATE TABLE IF NOT EXISTS return_lines (
    id INTEGER PRIMARY KEY,
    return_id INTEGER NOT NULL REFERENCES returns(id),
    sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    restock INTEGER NOT NULL CHECK (restock IN (0, 1))
);
CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY,
    establishment_id INTEGER NOT NULL REFERENCES establishments(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    movement_type TEXT NOT NULL,
    quantity_delta INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
    reference TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sold_at);
CREATE INDEX IF NOT EXISTS idx_packages_status ON packages(status);
CREATE INDEX IF NOT EXISTS idx_movements_date ON inventory_movements(occurred_at);
"""


class BaseDeDatos:
    def __init__(self, path: str | Path = "sistema.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self._transaction_depth = 0
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        with self.transaccion() as connection:
            product_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(products)").fetchall()
            }
            inventory_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(inventory)").fetchall()
            }
            if "minimum_stock" not in inventory_columns:
                connection.execute(
                    """ALTER TABLE inventory
                       ADD COLUMN minimum_stock INTEGER NOT NULL DEFAULT 0
                       CHECK (minimum_stock >= 0)"""
                )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version INTEGER PRIMARY KEY,
                       applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            migration = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 1"
            ).fetchone()
            legacy_minimum = "minimum_stock" in product_columns
            if migration is None:
                if legacy_minimum:
                    connection.execute(
                        """UPDATE inventory
                           SET minimum_stock = (
                               SELECT minimum_stock FROM products
                               WHERE products.id = inventory.product_id
                           )"""
                    )
                default_minimum = "p.minimum_stock" if legacy_minimum else "0"
                connection.execute(
                    f"""INSERT INTO inventory(
                            establishment_id, product_id, quantity, minimum_stock
                        )
                        SELECT e.id, p.id, 0, {default_minimum}
                        FROM establishments e CROSS JOIN products p
                        WHERE NOT EXISTS (
                            SELECT 1 FROM inventory i
                            WHERE i.establishment_id = e.id AND i.product_id = p.id
                        )"""
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (1)"
                )
            else:
                connection.execute(
                    """INSERT OR IGNORE INTO inventory(
                           establishment_id, product_id, quantity, minimum_stock
                       )
                       SELECT e.id, p.id, 0, 0
                       FROM establishments e CROSS JOIN products p"""
                )
        order_columns = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(replenishment_orders)"
            ).fetchall()
        }
        if "request_id" not in order_columns:
            self.connection.execute(
                """ALTER TABLE replenishment_orders
                   ADD COLUMN request_id INTEGER REFERENCES replenishment_requests(id)"""
            )
            self.connection.execute(
                """UPDATE replenishment_orders
                   SET request_id = (
                       SELECT id FROM replenishment_requests
                       WHERE replenishment_requests.order_id = replenishment_orders.id
                   )
                   WHERE request_id IS NULL"""
            )
        request_line_columns = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(replenishment_request_lines)"
            ).fetchall()
        }
        if "fulfilled" not in request_line_columns:
            self.connection.execute(
                """ALTER TABLE replenishment_request_lines
                   ADD COLUMN fulfilled INTEGER NOT NULL DEFAULT 0
                   CHECK (fulfilled >= 0)"""
            )
            self.connection.execute(
                """UPDATE replenishment_request_lines
                   SET fulfilled = requested
                   WHERE request_id IN (
                       SELECT id FROM replenishment_requests
                       WHERE status = 'Atendida'
                   )"""
            )

    @contextmanager
    def transaccion(self) -> Iterator[sqlite3.Connection]:
        if self._transaction_depth:
            savepoint = f"transaction_{self._transaction_depth}"
            self.connection.execute(f"SAVEPOINT {savepoint}")
            self._transaction_depth += 1
            try:
                yield self.connection
            except Exception:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            finally:
                self._transaction_depth -= 1
            return

        self.connection.execute("BEGIN IMMEDIATE")
        self._transaction_depth = 1
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        finally:
            self._transaction_depth = 0

    def cerrar(self) -> None:
        self.connection.close()
