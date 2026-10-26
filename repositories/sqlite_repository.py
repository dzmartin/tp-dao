import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('CD', 'LOCAL'))
);
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('CD', 'LOCAL')),
    location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    CHECK ((role='CD' AND location_id IS NULL) OR (role='LOCAL' AND location_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    tax_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    minimum_stock INTEGER NOT NULL CHECK (minimum_stock >= 0),
    supplier_id INTEGER REFERENCES suppliers(id),
    tracking_mode TEXT NOT NULL DEFAULT 'none' CHECK (tracking_mode IN ('none','lot','serial')),
    category_id INTEGER REFERENCES categories(id)
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
    reference TEXT NOT NULL DEFAULT '',
    unit_cost_cents INTEGER NOT NULL DEFAULT 0,
    cost_is_estimated INTEGER NOT NULL DEFAULT 0 CHECK (cost_is_estimated IN (0,1)),
    waste_quantity INTEGER NOT NULL DEFAULT 0 CHECK (waste_quantity >= 0)
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    destination_id INTEGER NOT NULL REFERENCES locations(id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_order_lines (
    order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    received_quantity INTEGER NOT NULL DEFAULT 0 CHECK (received_quantity >= 0 AND received_quantity <= quantity),
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE IF NOT EXISTS price_lists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_list_items (
    price_list_id INTEGER NOT NULL REFERENCES price_lists(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    PRIMARY KEY (price_list_id, product_id)
);
CREATE TABLE IF NOT EXISTS promotions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('percent','fixed')),
    value INTEGER NOT NULL CHECK (value > 0),
    product_id INTEGER REFERENCES products(id),
    category TEXT,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    CHECK (product_id IS NOT NULL OR category IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    code TEXT NOT NULL,
    expiry_date TEXT,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    UNIQUE(location_id, product_id, code)
);
CREATE TABLE IF NOT EXISTS serial_numbers (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    serial TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('available','sold','retired'))
);
CREATE TABLE IF NOT EXISTS replenishment_orders (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    origin_id INTEGER NOT NULL REFERENCES locations(id),
    destination_id INTEGER NOT NULL REFERENCES locations(id),
    batch_id INTEGER REFERENCES replenishment_batches(id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replenishment_batches (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    origin_id INTEGER NOT NULL REFERENCES locations(id),
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
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    state TEXT NOT NULL DEFAULT 'Confirmada',
    customer_id INTEGER REFERENCES customers(id),
    price_list_id INTEGER REFERENCES price_lists(id),
    loyalty_points_earned INTEGER NOT NULL DEFAULT 0 CHECK (loyalty_points_earned >= 0)
);
CREATE TABLE IF NOT EXISTS sale_lines (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0),
    unit_price_cents INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL,
    discount_cents INTEGER NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
    promotion_id INTEGER REFERENCES promotions(id)
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
CREATE TABLE IF NOT EXISTS sale_line_trace (
    id INTEGER PRIMARY KEY,
    sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id),
    lot_id INTEGER REFERENCES lots(id),
    serial_id INTEGER REFERENCES serial_numbers(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    CHECK ((lot_id IS NOT NULL) != (serial_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS return_line_trace (
    id INTEGER PRIMARY KEY,
    return_line_id INTEGER NOT NULL REFERENCES return_lines(id),
    sale_line_trace_id INTEGER NOT NULL REFERENCES sale_line_trace(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0)
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    credit_limit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_limit_cents >= 0),
    balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    loyalty_points INTEGER NOT NULL DEFAULT 0 CHECK (loyalty_points >= 0)
);
CREATE TABLE IF NOT EXISTS inventory_counts (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    status TEXT NOT NULL CHECK (status IN ('Abierto','Cerrado')),
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS inventory_count_lines (
    count_id INTEGER NOT NULL REFERENCES inventory_counts(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    expected_quantity INTEGER NOT NULL CHECK (expected_quantity >= 0),
    counted_quantity INTEGER CHECK (counted_quantity IS NULL OR counted_quantity >= 0),
    trace_detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (count_id, product_id)
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
        sale_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(sales)")}
        if "state" not in sale_columns:
            self.connection.execute(
                "ALTER TABLE sales ADD COLUMN state TEXT NOT NULL DEFAULT 'Confirmada'"
            )
        self._ensure_column("products", "tracking_mode", "TEXT NOT NULL DEFAULT 'none'")
        self._ensure_column("products", "category_id", "INTEGER REFERENCES categories(id)")
        self._ensure_column("replenishment_orders", "batch_id", "INTEGER REFERENCES replenishment_batches(id)")
        self._ensure_column("sales", "customer_id", "INTEGER REFERENCES customers(id)")
        self._ensure_column("sales", "price_list_id", "INTEGER REFERENCES price_lists(id)")
        self._ensure_column("sales", "loyalty_points_earned", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("sale_lines", "discount_cents", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("sale_lines", "promotion_id", "INTEGER REFERENCES promotions(id)")
        self._ensure_column("inventory_count_lines", "trace_detail", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("serial_numbers", "lot_id", "INTEGER REFERENCES lots(id)")
        categories = self.connection.execute(
            "SELECT DISTINCT category FROM products WHERE trim(category)<>''"
        ).fetchall()
        for category in categories:
            self.connection.execute(
                "INSERT OR IGNORE INTO categories(name) VALUES(?)", (category["category"].strip(),)
            )
        self.connection.execute(
            """UPDATE products SET category_id=(
                   SELECT id FROM categories WHERE categories.name=products.category COLLATE NOCASE
               ) WHERE category_id IS NULL"""
        )
        self.connection.execute(
            """UPDATE products SET category=(
                   SELECT name FROM categories WHERE categories.id=products.category_id
               ) WHERE category_id IS NOT NULL"""
        )
        self.connection.execute(
            """UPDATE promotions SET category=(
                   SELECT name FROM categories WHERE categories.name=promotions.category COLLATE NOCASE
               ) WHERE EXISTS(
                   SELECT 1 FROM categories WHERE categories.name=promotions.category COLLATE NOCASE
               )"""
        )
        movement_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(inventory_movements)")}
        self._ensure_column("inventory_movements", "unit_cost_cents", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(
            "inventory_movements", "cost_is_estimated", "INTEGER NOT NULL DEFAULT 0"
        )
        self._ensure_column(
            "inventory_movements", "waste_quantity", "INTEGER NOT NULL DEFAULT 0"
        )
        if "unit_cost_cents" not in movement_columns:
            self.connection.execute(
                """UPDATE inventory_movements SET unit_cost_cents=(
                       SELECT cost_cents FROM products WHERE products.id=inventory_movements.product_id
                   ), cost_is_estimated=1"""
            )
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS package_unit_serials (
                package_id INTEGER NOT NULL REFERENCES packages(id),
                serial_id INTEGER NOT NULL REFERENCES serial_numbers(id),
                origin_location_id INTEGER NOT NULL REFERENCES locations(id),
                origin_lot_id INTEGER REFERENCES lots(id),
                destination_lot_id INTEGER REFERENCES lots(id),
                PRIMARY KEY (package_id, serial_id)
            );
            """
        )
        self._backfill_stock_serials()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _backfill_stock_serials(self) -> None:
        stocks = self.connection.execute(
            """SELECT s.location_id,s.product_id,s.quantity,p.tracking_mode
               FROM stock s JOIN products p ON p.id=s.product_id"""
        ).fetchall()
        for stock in stocks:
            if stock["tracking_mode"] == "lot":
                lots = self.connection.execute(
                    """SELECT id,quantity FROM lots
                       WHERE location_id=? AND product_id=? AND quantity>0""",
                    (stock["location_id"], stock["product_id"]),
                ).fetchall()
                for lot in lots:
                    unassigned = self.connection.execute(
                        """SELECT id FROM serial_numbers WHERE location_id=? AND product_id=?
                           AND lot_id IS NULL AND status='available' LIMIT ?""",
                        (stock["location_id"], stock["product_id"], lot["quantity"]),
                    ).fetchall()
                    for serial in unassigned:
                        self.connection.execute("UPDATE serial_numbers SET lot_id=? WHERE id=?", (lot["id"], serial["id"]))
                    available = self.connection.execute(
                        """SELECT COUNT(*) AS count FROM serial_numbers
                           WHERE location_id=? AND product_id=? AND lot_id=? AND status='available'""",
                        (stock["location_id"], stock["product_id"], lot["id"]),
                    ).fetchone()["count"]
                    for _ in range(max(0, lot["quantity"] - available)):
                        self.connection.execute(
                            """INSERT INTO serial_numbers(location_id,product_id,serial,status,lot_id)
                               VALUES(?,?,?,'available',?)""",
                            (stock["location_id"], stock["product_id"], f"SN-{uuid4().hex.upper()}", lot["id"]),
                        )
                continue

            available = self.connection.execute(
                """SELECT COUNT(*) AS count FROM serial_numbers
                   WHERE location_id=? AND product_id=? AND status='available'""",
                (stock["location_id"], stock["product_id"]),
            ).fetchone()["count"]
            for _ in range(max(0, stock["quantity"] - available)):
                self.connection.execute(
                    """INSERT INTO serial_numbers(location_id,product_id,serial,status)
                       VALUES(?,?,?,'available')""",
                    (stock["location_id"], stock["product_id"], f"SN-{uuid4().hex.upper()}"),
                )

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