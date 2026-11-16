import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.persistencia.sqlite_repository import SQLiteRepository
from backend.servicios.auth_service import AuthService
from backend.servicios.retail_service import RetailService


class AdminRoleMigrationTests(unittest.TestCase):
    def test_existing_rejected_packages_are_marked_as_already_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy-rejection.db"
            repository = SQLiteRepository(database_path)
            service = RetailService(repository)
            cd_id = service.create_location("CD", "Centro", "CD")
            service.create_location("L01", "Local", "LOCAL")
            service.create_product("SKU-1", "Producto", "General", 4, 8, 0)
            service.adjust_stock("CD", "SKU-1", 2, "compra")
            order_id = service.create_replenishment("L01", {"SKU-1": 2})
            package_id = service.pack(order_id, "B-LEGACY-REJECT", "R-LEGACY-REJECT", {"SKU-1": 2})
            service.seal_package("B-LEGACY-REJECT", "PREC-LEGACY")
            service.dispatch("B-LEGACY-REJECT")
            repository.execute(
                "UPDATE packages SET status='Rechazado',received_at='2026-01-01T00:00:00+00:00' WHERE id=?",
                (package_id,),
            )
            repository.connection.commit()
            service.adjust_stock("CD", "SKU-1", 2, "despacho rechazado")
            repository.execute("DROP TABLE rejected_package_dispositions")
            repository.connection.commit()
            repository.close()

            migrated_repository = SQLiteRepository(database_path)
            try:
                disposition = migrated_repository.one(
                    "SELECT disposition FROM rejected_package_dispositions WHERE package_id=?",
                    (package_id,),
                )
                stock = migrated_repository.one(
                    "SELECT quantity FROM stock WHERE location_id=? AND product_id=1", (cd_id,)
                )
                self.assertEqual(disposition["disposition"], "stock")
                self.assertEqual(stock["quantity"], 2)
            finally:
                migrated_repository.close()

    def test_existing_packages_migrate_to_single_order_allocations(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy-packages.db"
            repository = SQLiteRepository(database_path)
            service = RetailService(repository)
            service.create_location("CD", "Centro", "CD")
            service.create_location("L01", "Local", "LOCAL")
            service.create_product("SKU-1", "Producto", "General", 4, 8, 0)
            service.adjust_stock("CD", "SKU-1", 2, "compra")
            order_id = service.create_replenishment("L01", {"SKU-1": 2})
            package_id = service.pack(order_id, "B-LEGACY", "R-LEGACY", {"SKU-1": 2})
            repository.close()

            connection = sqlite3.connect(database_path)
            connection.execute("DELETE FROM package_order_lines")
            connection.execute("DELETE FROM package_orders")
            connection.commit()
            connection.close()

            migrated_repository = SQLiteRepository(database_path)
            try:
                allocation = migrated_repository.one(
                    """SELECT order_id,product_id,quantity FROM package_order_lines
                       WHERE package_id=?""",
                    (package_id,),
                )
                self.assertEqual(
                    (allocation["order_id"], allocation["quantity"]),
                    (order_id, 2),
                )
            finally:
                migrated_repository.close()

    def test_existing_users_and_sessions_survive_admin_role_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE locations (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL CHECK (type IN ('CD','LOCAL'))
                );
                INSERT INTO locations(code,name,type) VALUES('CD','Centro','CD');
                CREATE TABLE app_users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('CD','LOCAL')),
                    location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
                    created_at TEXT NOT NULL,
                    CHECK ((role='CD' AND location_id IS NULL) OR (role='LOCAL' AND location_id IS NOT NULL))
                );
                INSERT INTO app_users(username,password_hash,role,location_id,created_at)
                    VALUES('centro','hash','CD',NULL,'2026-01-01T00:00:00+00:00');
                CREATE TABLE auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL
                );
                INSERT INTO auth_sessions(token_hash,user_id,expires_at)
                    VALUES('session-hash',1,'2999-01-01T00:00:00+00:00');
                """
            )
            connection.close()

            repository = SQLiteRepository(database_path)
            try:
                users = AuthService(repository)
                self.assertEqual(users.list_users()[0]["role"], "CD")
                self.assertEqual(
                    repository.one(
                        "SELECT user_id FROM auth_sessions WHERE token_hash='session-hash'"
                    )["user_id"],
                    1,
                )
                users.create_user("admin", "clave-segura-admin", "ADMIN", None)
                self.assertEqual(
                    repository.one(
                        "SELECT role FROM app_users WHERE username='admin'"
                    )["role"],
                    "ADMIN",
                )
                self.assertEqual(repository.all("PRAGMA foreign_key_check"), [])
            finally:
                repository.close()

    def test_optional_commerce_migration_preserves_financial_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy-commerce.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE locations (
                    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    type TEXT NOT NULL CHECK (type IN ('CD','LOCAL'))
                );
                INSERT INTO locations VALUES(1,'L01','Local','LOCAL');
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    category TEXT NOT NULL, cost_cents INTEGER NOT NULL, price_cents INTEGER NOT NULL,
                    minimum_stock INTEGER NOT NULL, supplier_id INTEGER
                );
                INSERT INTO products VALUES(1,'SKU-1','Producto','General',400,800,0,NULL);
                CREATE TABLE customers (
                    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
                    credit_limit_cents INTEGER NOT NULL DEFAULT 0, balance_cents INTEGER NOT NULL DEFAULT 0,
                    loyalty_points INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO customers VALUES(1,'CLI-1','Cliente','', '',0,0,0);
                CREATE TABLE price_lists (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                INSERT INTO price_lists VALUES(1,'Lista',1,'2026-01-01');
                CREATE TABLE price_list_items (
                    price_list_id INTEGER NOT NULL REFERENCES price_lists(id),
                    product_id INTEGER NOT NULL REFERENCES products(id), price_cents INTEGER NOT NULL,
                    PRIMARY KEY(price_list_id,product_id)
                );
                INSERT INTO price_list_items VALUES(1,1,700);
                CREATE TABLE promotions (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, value INTEGER NOT NULL,
                    product_id INTEGER REFERENCES products(id), category TEXT, starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO promotions VALUES(1,'Promo','percent',1000,1,NULL,'2026-01-01','2026-12-31',1);
                CREATE TABLE sales (
                    id INTEGER PRIMARY KEY, receipt TEXT NOT NULL UNIQUE,
                    location_id INTEGER NOT NULL REFERENCES locations(id), sold_at TEXT NOT NULL,
                    total_cents INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'Confirmada',
                    customer_id INTEGER REFERENCES customers(id), price_list_id INTEGER REFERENCES price_lists(id),
                    loyalty_points_earned INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO sales VALUES(1,'V-LEGACY',1,'2026-01-01T10:00:00+00:00',800,'Confirmada',1,1,0);
                CREATE TABLE sale_lines (
                    id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
                    product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL,
                    returned_quantity INTEGER NOT NULL DEFAULT 0, unit_price_cents INTEGER NOT NULL,
                    unit_cost_cents INTEGER NOT NULL, discount_cents INTEGER NOT NULL DEFAULT 0,
                    promotion_id INTEGER REFERENCES promotions(id)
                );
                INSERT INTO sale_lines VALUES(1,1,1,1,1,800,400,200,1);
                CREATE TABLE payments (
                    id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
                    method TEXT NOT NULL, amount_cents INTEGER NOT NULL
                );
                INSERT INTO payments VALUES(1,1,'efectivo',800);
                CREATE TABLE returns (
                    id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
                    returned_at TEXT NOT NULL, total_cents INTEGER NOT NULL
                );
                INSERT INTO returns VALUES(1,1,'2026-01-02T10:00:00+00:00',800);
                CREATE TABLE return_lines (
                    id INTEGER PRIMARY KEY, return_id INTEGER NOT NULL REFERENCES returns(id),
                    sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id), quantity INTEGER NOT NULL,
                    restocked INTEGER NOT NULL
                );
                INSERT INTO return_lines VALUES(1,1,1,1,1);
                CREATE TABLE return_payments (
                    id INTEGER PRIMARY KEY, return_id INTEGER NOT NULL REFERENCES returns(id),
                    method TEXT NOT NULL, amount_cents INTEGER NOT NULL
                );
                INSERT INTO return_payments VALUES(1,1,'efectivo',800);
                """
            )
            connection.close()

            repository = SQLiteRepository(database_path)
            try:
                tables = {
                    row["name"] for row in repository.all(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for removed_table in ("customers", "price_lists", "price_list_items", "promotions"):
                    self.assertNotIn(removed_table, tables)
                sales_columns = {row["name"] for row in repository.all("PRAGMA table_info(sales)")}
                line_columns = {row["name"] for row in repository.all("PRAGMA table_info(sale_lines)")}
                self.assertFalse({"customer_id", "price_list_id", "loyalty_points_earned"} & sales_columns)
                self.assertFalse({"discount_cents", "promotion_id"} & line_columns)
                self.assertEqual(
                    repository.one("SELECT total_cents FROM sales WHERE receipt='V-LEGACY'")["total_cents"],
                    800,
                )
                self.assertEqual(
                    repository.one("SELECT unit_price_cents FROM sale_lines WHERE id=1")["unit_price_cents"],
                    800,
                )
                self.assertEqual(repository.one("SELECT amount_cents FROM payments WHERE id=1")["amount_cents"], 800)
                self.assertEqual(repository.one("SELECT total_cents FROM returns WHERE id=1")["total_cents"], 800)
                self.assertEqual(repository.one("SELECT amount_cents FROM return_payments WHERE id=1")["amount_cents"], 800)
                self.assertEqual(repository.all("PRAGMA foreign_key_check"), [])

                repository.execute(
                    "INSERT INTO stock(location_id,product_id,quantity) VALUES(1,1,1)"
                )
                repository.connection.commit()
                receipt = RetailService(repository).sell("L01", {"SKU-1": 1}, {"efectivo": 8})
                self.assertEqual(
                    repository.one("SELECT total_cents FROM sales WHERE receipt=?", (receipt,))["total_cents"],
                    640,
                )
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
