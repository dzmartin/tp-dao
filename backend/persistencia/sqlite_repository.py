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
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('ADMIN', 'CD', 'LOCAL')),
    location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    CHECK ((role IN ('ADMIN','CD') AND location_id IS NULL) OR (role='LOCAL' AND location_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_account_markers (
    username TEXT PRIMARY KEY
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
CREATE TABLE IF NOT EXISTS package_orders (
    package_id INTEGER NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    order_id INTEGER NOT NULL REFERENCES replenishment_orders(id),
    PRIMARY KEY (package_id, order_id)
);
CREATE TABLE IF NOT EXISTS package_order_lines (
    package_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (package_id, order_id, product_id),
    FOREIGN KEY (package_id, order_id) REFERENCES package_orders(package_id, order_id) ON DELETE CASCADE
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
CREATE TABLE IF NOT EXISTS rejected_package_dispositions (
    package_id INTEGER PRIMARY KEY REFERENCES packages(id),
    disposition TEXT NOT NULL CHECK (disposition IN ('stock','discard')),
    resolved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    receipt TEXT NOT NULL UNIQUE,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    sold_at TEXT NOT NULL,
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    state TEXT NOT NULL DEFAULT 'Confirmada'
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
    PRIMARY KEY (count_id, product_id)
);
"""


class SQLiteRepository:
    """Repository pattern: the application accesses persistence through this gateway."""

    def __init__(
        self, database_path: str | Path = Path(__file__).resolve().parents[1] / "data" / "retail.db"
    ) -> None:
        if str(database_path) != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        has_rejected_dispositions = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rejected_package_dispositions'"
        ).fetchone() is not None
        self.connection.executescript(SCHEMA)
        if not has_rejected_dispositions:
            self.connection.execute(
                """INSERT INTO rejected_package_dispositions(package_id,disposition,resolved_at)
                   SELECT id,'stock',COALESCE(received_at,prepared_at)
                   FROM packages WHERE status='Rechazado'"""
            )
            self.connection.commit()
        self._migrate_admin_role()
        self._migrate_package_orders()
        sale_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(sales)")}
        if "state" not in sale_columns:
            self.connection.execute(
                "ALTER TABLE sales ADD COLUMN state TEXT NOT NULL DEFAULT 'Confirmada'"
            )
        self._ensure_column("products", "category_id", "INTEGER REFERENCES categories(id)")
        self._ensure_column("replenishment_orders", "batch_id", "INTEGER REFERENCES replenishment_batches(id)")
        self._clear_legacy_traceability()
        self._clear_legacy_optional_commerce()
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
        self.connection.commit()
    def _migrate_admin_role(self) -> None:
        definition = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='app_users'"
        ).fetchone()
        if not definition or "'ADMIN'" in definition["sql"].upper():
            return

        self.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            self.connection.execute("BEGIN")
            self.connection.execute(
                """CREATE TABLE app_users_new (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('ADMIN','CD','LOCAL')),
                    location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
                    created_at TEXT NOT NULL,
                    CHECK ((role IN ('ADMIN','CD') AND location_id IS NULL)
                           OR (role='LOCAL' AND location_id IS NOT NULL))
                )"""
            )
            self.connection.execute(
                """INSERT INTO app_users_new(id,username,password_hash,role,location_id,active,created_at)
                   SELECT id,username,password_hash,role,location_id,active,created_at FROM app_users"""
            )
            self.connection.execute(
                """CREATE TABLE auth_sessions_new (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL
                )"""
            )
            self.connection.execute(
                """INSERT INTO auth_sessions_new(token_hash,user_id,expires_at)
                   SELECT token_hash,user_id,expires_at FROM auth_sessions"""
            )
            self.connection.execute("DROP TABLE auth_sessions")
            self.connection.execute("DROP TABLE app_users")
            self.connection.execute("ALTER TABLE app_users_new RENAME TO app_users")
            self.connection.execute("ALTER TABLE auth_sessions_new RENAME TO auth_sessions")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys=ON")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_package_orders(self) -> None:
        self.connection.execute(
            """INSERT OR IGNORE INTO package_orders(package_id,order_id)
               SELECT id,order_id FROM packages"""
        )
        self.connection.execute(
            """INSERT OR IGNORE INTO package_order_lines(package_id,order_id,product_id,quantity)
               SELECT p.id,p.order_id,pl.product_id,pl.quantity
               FROM packages p JOIN package_lines pl ON pl.package_id=p.id"""
        )

    def _clear_legacy_traceability(self) -> None:
        tables = {
            row["name"]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in (
            "return_line_trace",
            "sale_line_trace",
            "package_unit_serials",
            "serial_numbers",
            "lots",
        ):
            if table in tables:
                self.connection.execute(f"DELETE FROM {table}")
                self.connection.execute(f"DROP TABLE {table}")
        product_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(products)")
        }
        if "tracking_mode" in product_columns:
            self.connection.execute("UPDATE products SET tracking_mode='none'")
        count_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(inventory_count_lines)")
        }
        if "trace_detail" in count_columns:
            self.connection.execute("UPDATE inventory_count_lines SET trace_detail=''")
        self.connection.commit()

    def _clear_legacy_optional_commerce(self) -> None:
        tables = {
            row["name"]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        sale_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(sales)")
        }
        line_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(sale_lines)")
        }
        optional_sale_columns = {"customer_id", "price_list_id", "loyalty_points_earned"}
        optional_line_columns = {"discount_cents", "promotion_id"}
        optional_tables = {"price_list_items", "price_lists", "promotions", "customers"}
        if not (
            sale_columns & optional_sale_columns
            or line_columns & optional_line_columns
            or tables & optional_tables
        ):
            return

        self.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            self.connection.execute("BEGIN")
            if sale_columns & optional_sale_columns:
                self.connection.execute(
                    """CREATE TABLE sales_new (
                        id INTEGER PRIMARY KEY,
                        receipt TEXT NOT NULL UNIQUE,
                        location_id INTEGER NOT NULL REFERENCES locations(id),
                        sold_at TEXT NOT NULL,
                        total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
                        state TEXT NOT NULL DEFAULT 'Confirmada'
                    )"""
                )
                self.connection.execute(
                    """INSERT INTO sales_new(id,receipt,location_id,sold_at,total_cents,state)
                       SELECT id,receipt,location_id,sold_at,total_cents,state FROM sales"""
                )
                self.connection.execute("DROP TABLE sales")
                self.connection.execute("ALTER TABLE sales_new RENAME TO sales")
            if line_columns & optional_line_columns:
                self.connection.execute(
                    """CREATE TABLE sale_lines_new (
                        id INTEGER PRIMARY KEY,
                        sale_id INTEGER NOT NULL REFERENCES sales(id),
                        product_id INTEGER NOT NULL REFERENCES products(id),
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0),
                        unit_price_cents INTEGER NOT NULL,
                        unit_cost_cents INTEGER NOT NULL
                    )"""
                )
                self.connection.execute(
                    """INSERT INTO sale_lines_new(
                           id,sale_id,product_id,quantity,returned_quantity,unit_price_cents,unit_cost_cents
                       )
                       SELECT id,sale_id,product_id,quantity,returned_quantity,unit_price_cents,unit_cost_cents
                       FROM sale_lines"""
                )
                self.connection.execute("DROP TABLE sale_lines")
                self.connection.execute("ALTER TABLE sale_lines_new RENAME TO sale_lines")
            for table in ("price_list_items", "price_lists", "promotions", "customers"):
                if table in tables:
                    self.connection.execute(f"DROP TABLE {table}")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys=ON")
        violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"Optional commerce migration left {len(violations)} foreign-key violation(s)."
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