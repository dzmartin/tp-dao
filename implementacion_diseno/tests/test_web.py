import json
import sqlite3
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from sistema import Sistema
from sistema.domain import TipoEstablecimiento
from sistema.repositories import RepositorioCatalogo
from sistema.storage import BaseDeDatos
from sistema.web import create_server


class InterfazWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "web-test.db"
        with Sistema(self.database) as system:
            self.center = system.catalogo.crear_establecimiento(
                "CD-WEB", "Centro web", TipoEstablecimiento.CENTRO_DISTRIBUCION
            )
            self.store = system.catalogo.crear_establecimiento(
                "LOC-WEB", "Sucursal web", TipoEstablecimiento.SUCURSAL
            )
            supplier_id = system.catalogo.crear_proveedor("TAX-WEB", "Proveedor web")
            system.catalogo.crear_producto(
                "SKU-WEB", "Producto web", "General", 500, 1200, supplier_id
            )
            system.inventario.ajustar_stock_centro(
                self.center.id, "SKU-WEB", 10, "Apertura"
            )
        self.server = create_server(self.database, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.select_profile(self.center.id)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        method: str | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with self.opener.open(request) as response:
            return json.loads(response.read())

    def request_bytes(self, path: str) -> tuple[dict[str, str], bytes]:
        request = Request(self.base_url + path)
        with self.opener.open(request) as response:
            return dict(response.headers.items()), response.read()

    def select_profile(self, profile_id: int) -> None:
        self.request("/api/session", {"profile_id": profile_id})

    def create_replenishment_request(
        self, store_id: int, items: list[dict[str, object]]
    ) -> int:
        self.select_profile(store_id)
        result = self.request("/api/replenishment-requests", {"items": items})
        self.select_profile(self.center.id)
        return int(result["id"])

    def request_status(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        method: str = "POST",
    ) -> int:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with self.assertRaises(HTTPError) as error:
            self.opener.open(request)
        return error.exception.code

    def test_existing_completed_requests_migrate_fulfilled_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database_path = Path(folder) / "legacy.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE replenishment_requests (
                    id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL
                );
                CREATE TABLE products (id INTEGER PRIMARY KEY);
                CREATE TABLE replenishment_request_lines (
                    id INTEGER PRIMARY KEY,
                    request_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    requested INTEGER NOT NULL
                );
                INSERT INTO replenishment_requests VALUES (1, 'Atendida');
                INSERT INTO products VALUES (1);
                INSERT INTO replenishment_request_lines
                    VALUES (1, 1, 1, 5);
                """
            )
            connection.close()

            database = BaseDeDatos(database_path)
            try:
                line = database.connection.execute(
                    """SELECT requested, fulfilled
                       FROM replenishment_request_lines WHERE id = 1"""
                ).fetchone()
                self.assertEqual(tuple(line), (5, 5))
            finally:
                database.cerrar()

    def test_role_profiles_scope_data_and_protect_actions(self) -> None:
        with Sistema(self.database) as system:
            other_store = system.catalogo.crear_establecimiento(
                "LOC-OTHER", "Otra sucursal", TipoEstablecimiento.SUCURSAL
            )
            product_id = system.db.connection.execute(
                "SELECT id FROM products WHERE sku = 'SKU-WEB'"
            ).fetchone()["id"]
            system.db.connection.execute(
                "UPDATE inventory SET quantity = 1 "
                "WHERE establishment_id = ? AND product_id = ?",
                (other_store.id, product_id),
            )
            other_sale = system.ventas.registrar_venta(
                other_store.id, {"SKU-WEB": 1}, {"efectivo": 1200}
            )
            other_sale_line = system.db.connection.execute(
                "SELECT id FROM sale_lines WHERE sale_id = ?", (other_sale.id,)
            ).fetchone()["id"]

        unauthenticated = build_opener()
        request = Request(self.base_url + "/api/bootstrap")
        with self.assertRaises(HTTPError) as missing_profile:
            unauthenticated.open(request)
        self.assertEqual(missing_profile.exception.code, 401)

        self.select_profile(self.store.id)
        branch_data = self.request("/api/bootstrap")
        branch_request = self.request(
            "/api/replenishment-requests",
            {"items": [{"sku": "SKU-WEB", "quantity": 2}]},
        )
        self.assertEqual(len(branch_data["replenishment_requests"]), 0)
        branch_data = self.request("/api/bootstrap")
        self.assertEqual(
            [request["id"] for request in branch_data["replenishment_requests"]],
            [branch_request["id"]],
        )
        self.assertEqual(
            [location["id"] for location in branch_data["locations"]],
            [self.store.id],
        )
        self.assertNotIn("cost_cents", branch_data["products"][0])
        self.assertEqual(
            {item["establishment_id"] for item in branch_data["inventory"]},
            {self.store.id},
        )
        self.assertEqual(self.request("/api/sales"), [])
        self.assertEqual(
            self.request_status(f"/api/sales/{other_sale.id}", method="GET"),
            403,
        )
        self.assertEqual(
            self.request_status(
                f"/api/sales/{other_sale.id}/receipt.pdf", method="GET"
            ),
            403,
        )
        self.assertEqual(
            self.request_status(
                f"/api/sales/{other_sale.id}/returns",
                {
                    "method": "efectivo",
                    "items": [
                        {
                            "line_id": other_sale_line,
                            "quantity": 1,
                            "restock": True,
                        }
                    ],
                },
            ),
            403,
        )
        self.assertEqual(
            self.request_status("/api/suppliers", {"tax_id": "X", "name": "Y"}),
            403,
        )
        self.assertEqual(
            self.request_status(
                "/api/replenishment-requests",
                {"items": [{"sku": "SKU-WEB", "quantity": 1}]},
            ),
            400,
        )
        self.assertEqual(
            self.request_status(
                "/api/stock-adjust",
                {
                    "center_id": self.center.id,
                    "sku": "SKU-WEB",
                    "quantity_delta": 1,
                    "reference": "No autorizado",
                },
            ),
            403,
        )
        self.assertEqual(
            self.request_status(
                "/api/sales",
                {
                    "store_id": self.center.id,
                    "method": "efectivo",
                    "items": [{"sku": "SKU-WEB", "quantity": 1}],
                },
            ),
            403,
        )
        self.assertEqual(
            self.request_status("/api/reports?from_date=2026-01-01&to_date=2026-12-31", method="GET"),
            403,
        )

        self.select_profile(self.center.id)
        center_data = self.request("/api/bootstrap")
        self.assertEqual(len(center_data["locations"]), 3)
        self.assertIn("cost_cents", center_data["products"][0])
        self.assertEqual(
            [request["id"] for request in center_data["replenishment_requests"]],
            [branch_request["id"]],
        )
        self.assertEqual(len(self.request("/api/sales")), 1)
        self.assertEqual(
            self.request_status(
                "/api/replenishment-requests",
                {"items": [{"sku": "SKU-WEB", "quantity": 1}]},
            ),
            403,
        )
        self.assertEqual(
            self.request_status(
                "/api/sales",
                {
                    "store_id": self.store.id,
                    "method": "efectivo",
                    "items": [{"sku": "SKU-WEB", "quantity": 1}],
                },
            ),
            403,
        )
        self.assertIn("ventas_e_ingresos", self.request(
            "/api/reports?from_date=2026-01-01&to_date=2026-12-31",
            method="GET",
        ))
        shipment = self.request(
            "/api/replenishments",
            {
                "center_id": self.center.id,
                "orders": [
                    {
                        "request_id": branch_request["id"],
                        "remito": "REM-ROLE",
                        "seal": "SEAL-ROLE",
                        "items": [{"sku": "SKU-WEB", "quantity": 2}],
                    }
                ],
            },
        )
        package_id = shipment["packages"][0]["package_id"]
        self.select_profile(self.store.id)
        self.assertEqual(
            self.request_status(
                f"/api/packages/{package_id}/action", {"action": "precintar"}
            ),
            403,
        )
        self.assertEqual(
            self.request_status(
                f"/api/packages/{package_id}/action", {"action": "cancelar"}
            ),
            403,
        )
        self.select_profile(self.center.id)
        self.assertEqual(
            self.request_status(
                f"/api/packages/{package_id}/action", {"action": "recibir"}
            ),
            403,
        )

    def test_first_run_requires_a_center_before_branches(self) -> None:
        database = Path(self.temp_dir.name) / "first-run.db"
        server = create_server(database, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        base_url = f"http://127.0.0.1:{server.server_port}"

        def send(path: str, payload: dict[str, object]) -> dict[str, Any]:
            request = Request(
                base_url + path,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with opener.open(request) as response:
                return json.loads(response.read())

        try:
            with self.assertRaises(HTTPError) as invalid_initial_location:
                send(
                    "/api/locations",
                    {"code": "LOC-FIRST", "name": "Local", "type": "SUCURSAL"},
                )
            self.assertEqual(invalid_initial_location.exception.code, 400)
            center = send(
                "/api/locations",
                {"code": "CD-FIRST", "name": "Centro", "type": "CD"},
            )
            with self.assertRaises(HTTPError) as unauthenticated_branch:
                send(
                    "/api/locations",
                    {"code": "LOC-FIRST", "name": "Local", "type": "SUCURSAL"},
                )
            self.assertEqual(unauthenticated_branch.exception.code, 401)
            send("/api/session", {"profile_id": center["id"]})
            branch = send(
                "/api/locations",
                {"code": "LOC-FIRST", "name": "Local", "type": "SUCURSAL"},
            )
            self.assertGreater(branch["id"], center["id"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_catalog_setup_api(self) -> None:
        location = self.request(
            "/api/locations",
            {"code": "LOC-NEW", "name": "Sucursal nueva", "type": "SUCURSAL"},
        )
        self.assertGreater(location["id"], 0)
        first = self.request(
            "/api/products",
            {
                "sku": "SKU-NEW",
                "name": "Producto nuevo",
                "category": "Varios",
                "cost_cents": 0,
                "price_cents": 250,
                "supplier_tax_id": "TAX-NEW",
                "supplier_name": "Proveedor nuevo",
            },
        )
        second = self.request(
            "/api/products",
            {
                "sku": "SKU-NEW-2",
                "name": "Otro producto",
                "category": "Varios",
                "cost_cents": 100,
                "price_cents": 500,
                "supplier_tax_id": "TAX-NEW",
                "supplier_name": "Proveedor existente",
            },
        )
        self.assertEqual(first["sku"], "SKU-NEW")
        self.assertEqual(second["sku"], "SKU-NEW-2")
        with Sistema(self.database) as system:
            supplier_count = system.db.connection.execute(
                "SELECT COUNT(*) FROM suppliers WHERE tax_id = 'TAX-NEW'"
            ).fetchone()[0]
            self.assertEqual(supplier_count, 1)
        supplier = self.request(
            "/api/suppliers",
            {"tax_id": "TAX-SECOND", "name": "Segundo proveedor"},
        )
        update = Request(
            self.base_url + "/api/products/SKU-NEW",
            data=json.dumps(
                {
                    "name": "Producto editado",
                    "category": "Actualizada",
                    "cost_cents": 150,
                    "price_cents": 750,
                    "supplier_id": supplier["id"],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with self.opener.open(update) as response:
            self.assertEqual(response.status, 200)
        with Sistema(self.database) as system:
            row = system.db.connection.execute(
                """SELECT p.name, c.name AS category, p.cost_cents,
                          p.price_cents, ps.supplier_id
                   FROM products p
                   JOIN categories c ON c.id = p.category_id
                   JOIN product_suppliers ps ON ps.product_id = p.id
                   WHERE p.sku = 'SKU-NEW'"""
            ).fetchone()
            self.assertEqual(row["name"], "Producto editado")
            self.assertEqual(row["category"], "Actualizada")
            self.assertEqual(row["price_cents"], 750)
            self.assertEqual(row["supplier_id"], supplier["id"])
            minimum = system.db.connection.execute(
                """SELECT minimum_stock FROM inventory
                   WHERE establishment_id = ? AND product_id = (
                       SELECT id FROM products WHERE sku = 'SKU-NEW'
                   )""",
                (self.center.id,),
            ).fetchone()
            self.assertEqual(minimum["minimum_stock"], 0)

    def test_center_stock_adjustment_updates_inventory(self) -> None:
        result = self.request(
            "/api/stock-adjust",
            {
                "center_id": self.center.id,
                "sku": "SKU-WEB",
                "quantity_delta": 5,
                "reference": "Reposición inicial",
            },
        )
        self.assertEqual(result["message"], "Ajuste de stock registrado.")
        with Sistema(self.database) as system:
            self.assertEqual(
                system.inventario.disponible(self.center.id, "SKU-WEB"), 15
            )

    def test_stock_minimum_is_configured_per_profile(self) -> None:
        self.request(
            "/api/stock-minimum",
            {"sku": "SKU-WEB", "minimum_stock": 4},
        )
        self.select_profile(self.store.id)
        self.request(
            "/api/stock-minimum",
            {"sku": "SKU-WEB", "minimum_stock": 1},
        )
        with Sistema(self.database) as system:
            values = system.db.connection.execute(
                """SELECT establishment_id, minimum_stock FROM inventory
                   WHERE product_id = (
                       SELECT id FROM products WHERE sku = 'SKU-WEB'
                   ) ORDER BY establishment_id"""
            ).fetchall()
            self.assertEqual(
                [(row["establishment_id"], row["minimum_stock"]) for row in values],
                [(self.center.id, 4), (self.store.id, 1)],
            )

    def test_legacy_product_minimum_migrates_to_each_establishment(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database_path = Path(folder) / "legacy-minimum.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE establishments (
                    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, type TEXT NOT NULL
                );
                CREATE TABLE categories (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, category_id INTEGER NOT NULL,
                    cost_cents INTEGER NOT NULL, price_cents INTEGER NOT NULL,
                    minimum_stock INTEGER NOT NULL
                );
                CREATE TABLE inventory (
                    establishment_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    PRIMARY KEY (establishment_id, product_id)
                );
                INSERT INTO establishments VALUES
                    (1, 'CD-OLD', 'Centro viejo', 'CD'),
                    (2, 'LOC-OLD', 'Sucursal vieja', 'SUCURSAL');
                INSERT INTO categories VALUES (1, 'General');
                INSERT INTO products VALUES
                    (1, 'SKU-OLD', 'Producto viejo', 1, 100, 200, 7);
                INSERT INTO inventory VALUES (1, 1, 12);
                """
            )
            connection.close()

            database = BaseDeDatos(database_path)
            try:
                migrated = database.connection.execute(
                    """SELECT establishment_id, quantity, minimum_stock
                       FROM inventory ORDER BY establishment_id"""
                ).fetchall()
                self.assertEqual(
                    [tuple(row) for row in migrated], [(1, 12, 7), (2, 0, 7)]
                )
                supplier_id = database.connection.execute(
                    "INSERT INTO suppliers(tax_id, name) VALUES ('TAX-NEW', 'Nuevo')"
                ).lastrowid
                RepositorioCatalogo(database.connection).crear_producto(
                    "SKU-NEW", "Nuevo", "General", 100, 200, (int(supplier_id),)
                )
                new_minima = database.connection.execute(
                    """SELECT minimum_stock FROM inventory
                       WHERE product_id = (SELECT id FROM products WHERE sku = 'SKU-NEW')
                       ORDER BY establishment_id"""
                ).fetchall()
                self.assertEqual([row["minimum_stock"] for row in new_minima], [0, 0])
                database.connection.execute(
                    """UPDATE inventory SET minimum_stock = 9
                       WHERE establishment_id = 1 AND product_id = 1"""
                )
            finally:
                database.cerrar()
            database = BaseDeDatos(database_path)
            try:
                minimum = database.connection.execute(
                    """SELECT minimum_stock FROM inventory
                       WHERE establishment_id = 1 AND product_id = 1"""
                ).fetchone()
                self.assertEqual(minimum["minimum_stock"], 9)
            finally:
                database.cerrar()

    def test_frontend_and_sale_api(self) -> None:
        with urlopen(self.base_url) as response:
            self.assertIn(b"Faro", response.read())
        bootstrap = self.request("/api/bootstrap")
        self.assertEqual(len(bootstrap["products"]), 1)

        replenishment_request = self.create_replenishment_request(
            self.store.id, [{"sku": "SKU-WEB", "quantity": 5}]
        )
        shipment = self.request(
            "/api/replenishments",
            {
                "center_id": self.center.id,
                "orders": [
                    {
                        "request_id": replenishment_request,
                        "remito": "REM-SALE",
                        "seal": "SEAL-SALE",
                        "items": [{"sku": "SKU-WEB", "quantity": 5}],
                    }
                ],
            },
        )
        package_id = shipment["packages"][0]["package_id"]
        for action in ("precintar", "despachar"):
            self.request(f"/api/packages/{package_id}/action", {"action": action})
        self.select_profile(self.store.id)
        self.request(f"/api/packages/{package_id}/action", {"action": "recibir"})
        self.request(
            f"/api/packages/{package_id}/action",
            {
                "action": "verificar",
                "intacto": True,
                "coincide_remito": True,
            },
        )
        sale = self.request(
            "/api/sales",
            {
                "store_id": self.store.id,
                "method": "efectivo",
                "items": [{"sku": "SKU-WEB", "quantity": 3}],
            },
        )
        self.assertEqual(sale["total_cents"], 3600)
        with Sistema(self.database) as system:
            self.assertEqual(
                system.inventario.disponible(self.store.id, "SKU-WEB"), 2
            )

    def test_remito_mismatch_rejects_package_via_api(self) -> None:
        replenishment_request = self.create_replenishment_request(
            self.store.id, [{"sku": "SKU-WEB", "quantity": 3}]
        )
        shipment = self.request(
            "/api/replenishments",
            {
                "center_id": self.center.id,
                "orders": [
                    {
                        "request_id": replenishment_request,
                        "remito": "REM-MISMATCH",
                        "seal": "SEAL-MISMATCH",
                        "items": [{"sku": "SKU-WEB", "quantity": 3}],
                    }
                ],
            },
        )
        package_id = shipment["packages"][0]["package_id"]
        for action in ("precintar", "despachar"):
            self.request(f"/api/packages/{package_id}/action", {"action": action})
        self.select_profile(self.store.id)
        self.request(f"/api/packages/{package_id}/action", {"action": "recibir"})
        result = self.request(
            f"/api/packages/{package_id}/action",
            {
                "action": "verificar",
                "intacto": True,
                "coincide_remito": False,
            },
        )
        self.assertIn("rechazado", result["message"])
        with Sistema(self.database) as system:
            self.assertEqual(
                system.inventario.disponible(self.store.id, "SKU-WEB"), 0
            )
            package = system.db.connection.execute(
                "SELECT status FROM packages WHERE id = ?", (package_id,)
            ).fetchone()
            self.assertEqual(package["status"], "Rechazado")

    def test_multi_store_replenishment_is_consolidated(self) -> None:
        second_store = Sistema(self.database)
        try:
            other = second_store.catalogo.crear_establecimiento(
                "LOC-WEB-2", "Sucursal web 2", TipoEstablecimiento.SUCURSAL
            )
        finally:
            second_store.cerrar()
        first_request = self.create_replenishment_request(
            self.store.id, [{"sku": "SKU-WEB", "quantity": 2}]
        )
        other_request = self.create_replenishment_request(
            other.id, [{"sku": "SKU-WEB", "quantity": 3}]
        )
        result = self.request(
            "/api/replenishments",
            {
                "center_id": self.center.id,
                "orders": [
                    {
                        "request_id": first_request,
                        "remito": "REM-MULTI-1",
                        "seal": "SEAL-MULTI-1",
                        "items": [{"sku": "SKU-WEB", "quantity": 2}],
                    },
                    {
                        "request_id": other_request,
                        "remito": "REM-MULTI-2",
                        "seal": "SEAL-MULTI-2",
                        "items": [{"sku": "SKU-WEB", "quantity": 3}],
                    },
                ],
            },
        )
        self.assertEqual(len(result["packages"]), 2)
        self.assertEqual(
            {package["store_id"] for package in result["packages"]},
            {self.store.id, other.id},
        )
        with Sistema(self.database) as system:
            batches = system.db.connection.execute(
                "SELECT COUNT(DISTINCT batch_id) FROM replenishment_orders"
            ).fetchone()[0]
            self.assertEqual(batches, 1)
            order_count = system.db.connection.execute(
                "SELECT COUNT(*) FROM replenishment_orders"
            ).fetchone()[0]
            self.assertEqual(order_count, 2)
            fulfilled_requests = system.db.connection.execute(
                """SELECT COUNT(*) FROM replenishment_requests
                   WHERE status = 'Atendida' AND order_id IS NOT NULL"""
            ).fetchone()[0]
            self.assertEqual(fulfilled_requests, 2)

    def test_partial_replenishment_keeps_unprepared_quantity_pending(self) -> None:
        self.request(
            "/api/stock-adjust",
            {
                "center_id": self.center.id,
                "sku": "SKU-WEB",
                "quantity_delta": -7,
                "reference": "Stock disponible limitado",
            },
        )
        replenishment_request = self.create_replenishment_request(
            self.store.id, [{"sku": "SKU-WEB", "quantity": 5}]
        )
        order_payload = {
            "center_id": self.center.id,
            "orders": [
                {
                    "request_id": replenishment_request,
                    "remito": "REM-PARTIAL",
                    "seal": "SEAL-PARTIAL",
                    "items": [{"sku": "SKU-WEB", "quantity": 4}],
                }
            ],
        }
        self.assertEqual(
            self.request_status("/api/replenishments", order_payload), 400
        )
        with Sistema(self.database) as system:
            request_line = system.db.connection.execute(
                """SELECT requested, fulfilled FROM replenishment_request_lines
                   WHERE request_id = ?""",
                (replenishment_request,),
            ).fetchone()
            self.assertEqual((request_line["requested"], request_line["fulfilled"]), (5, 0))
            self.assertEqual(
                system.db.connection.execute(
                    "SELECT COUNT(*) FROM packages"
                ).fetchone()[0],
                0,
            )

        order_payload["orders"][0]["items"][0]["quantity"] = 3
        result = self.request("/api/replenishments", order_payload)
        package = result["packages"][0]
        self.assertEqual(package["remaining_items"][0]["quantity"], 2)
        bootstrap = self.request("/api/bootstrap")
        request_view = bootstrap["replenishment_requests"][0]
        self.assertEqual(request_view["status"], "Pendiente")
        self.assertEqual(request_view["items"][0]["remaining_quantity"], 2)
        self.assertEqual(request_view["items"][0]["available_quantity"], 0)
        with Sistema(self.database) as system:
            package_line = system.db.connection.execute(
                """SELECT pl.quantity FROM package_lines pl
                   WHERE pl.package_id = ?""",
                (package["package_id"],),
            ).fetchone()
            self.assertEqual(package_line["quantity"], 3)

        retry_payload = {
            "center_id": self.center.id,
            "orders": [
                {
                    "request_id": replenishment_request,
                    "remito": "REM-PARTIAL-RETRY",
                    "seal": "SEAL-PARTIAL-RETRY",
                    "items": [{"sku": "SKU-WEB", "quantity": 1}],
                }
            ],
        }
        self.assertEqual(
            self.request_status("/api/replenishments", retry_payload), 400
        )
        for action in ("precintar", "despachar"):
            self.request(
                f"/api/packages/{package['package_id']}/action",
                {"action": action},
            )
        self.request(
            "/api/stock-adjust",
            {
                "center_id": self.center.id,
                "sku": "SKU-WEB",
                "quantity_delta": 2,
                "reference": "Ingreso de stock",
            },
        )
        retry_payload["orders"][0]["items"][0]["quantity"] = 2
        retry_result = self.request("/api/replenishments", retry_payload)
        self.assertEqual(retry_result["packages"][0]["remaining_items"], [])
        with Sistema(self.database) as system:
            request_line = system.db.connection.execute(
                """SELECT requested, fulfilled FROM replenishment_request_lines
                   WHERE request_id = ?""",
                (replenishment_request,),
            ).fetchone()
            request_status = system.db.connection.execute(
                "SELECT status FROM replenishment_requests WHERE id = ?",
                (replenishment_request,),
            ).fetchone()["status"]
            self.assertEqual((request_line["requested"], request_line["fulfilled"]), (5, 5))
            self.assertEqual(request_status, "Atendida")

    def test_failed_dispatch_can_be_cancelled_and_reprepared(self) -> None:
        replenishment_request = self.create_replenishment_request(
            self.store.id, [{"sku": "SKU-WEB", "quantity": 5}]
        )
        result = self.request(
            "/api/replenishments",
            {
                "center_id": self.center.id,
                "orders": [
                    {
                        "request_id": replenishment_request,
                        "remito": "REM-CANCEL",
                        "seal": "SEAL-CANCEL",
                        "items": [{"sku": "SKU-WEB", "quantity": 3}],
                    }
                ],
            },
        )
        package_id = result["packages"][0]["package_id"]
        self.request(f"/api/packages/{package_id}/action", {"action": "precintar"})
        with Sistema(self.database) as system:
            product_id = system.db.connection.execute(
                "SELECT id FROM products WHERE sku = 'SKU-WEB'"
            ).fetchone()["id"]
            system.db.connection.execute(
                """UPDATE inventory SET quantity = 2
                   WHERE establishment_id = ? AND product_id = ?""",
                (self.center.id, product_id),
            )
        self.assertEqual(
            self.request_status(
                f"/api/packages/{package_id}/action", {"action": "despachar"}
            ),
            400,
        )
        self.request(f"/api/packages/{package_id}/action", {"action": "cancelar"})
        with Sistema(self.database) as system:
            package = system.db.connection.execute(
                "SELECT status FROM packages WHERE id = ?", (package_id,)
            ).fetchone()
            request_line = system.db.connection.execute(
                """SELECT requested, fulfilled FROM replenishment_request_lines
                   WHERE request_id = ?""",
                (replenishment_request,),
            ).fetchone()
            request_status = system.db.connection.execute(
                "SELECT status FROM replenishment_requests WHERE id = ?",
                (replenishment_request,),
            ).fetchone()["status"]
            self.assertEqual(package["status"], "Cancelado")
            self.assertEqual(
                (request_line["requested"], request_line["fulfilled"]), (5, 0)
            )
            self.assertEqual(request_status, "Pendiente")
            self.assertEqual(system.inventario.disponible(self.center.id, "SKU-WEB"), 2)

        self.request(
            "/api/stock-adjust",
            {
                "center_id": self.center.id,
                "sku": "SKU-WEB",
                "quantity_delta": 3,
                "reference": "Reposición para reintento",
            },
        )
        retry = self.request(
            "/api/replenishments",
            {
                "center_id": self.center.id,
                "orders": [
                    {
                        "request_id": replenishment_request,
                        "remito": "REM-CANCEL-RETRY",
                        "seal": "SEAL-CANCEL-RETRY",
                        "items": [{"sku": "SKU-WEB", "quantity": 5}],
                    }
                ],
            },
        )
        self.assertEqual(retry["packages"][0]["remaining_items"], [])

    def test_web_return_refunds_cash_and_can_restock(self) -> None:
        replenishment_request = self.create_replenishment_request(
            self.store.id, [{"sku": "SKU-WEB", "quantity": 5}]
        )
        result = self.request(
            "/api/replenishments",
            {
                "center_id": self.center.id,
                "orders": [
                    {
                        "request_id": replenishment_request,
                        "remito": "REM-RETURN",
                        "seal": "SEAL-RETURN",
                        "items": [{"sku": "SKU-WEB", "quantity": 5}],
                    }
                ],
            },
        )
        package_id = result["packages"][0]["package_id"]
        for action in ("precintar", "despachar"):
            self.request(f"/api/packages/{package_id}/action", {"action": action})
        self.select_profile(self.store.id)
        self.request(f"/api/packages/{package_id}/action", {"action": "recibir"})
        self.request(
            f"/api/packages/{package_id}/action",
            {
                "action": "verificar",
                "intacto": True,
                "coincide_remito": True,
            },
        )
        sale = self.request(
            "/api/sales",
            {
                "store_id": self.store.id,
                "method": "efectivo",
                "items": [{"sku": "SKU-WEB", "quantity": 3}],
            },
        )
        detail = self.request(f"/api/sales/{sale['id']}")
        self.assertEqual(detail["sale"]["receipt"], sale["receipt"])
        self.assertEqual(detail["sale"]["total_cents"], sale["total_cents"])
        self.assertEqual(detail["items"][0]["remaining_quantity"], 3)
        pdf_headers, pdf_body = self.request_bytes(
            f"/api/sales/{sale['id']}/receipt.pdf"
        )
        self.assertEqual(pdf_headers["Content-Type"], "application/pdf")
        self.assertIn("inline", pdf_headers["Content-Disposition"])
        self.assertTrue(pdf_body.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf_body.rstrip().endswith(b"%%EOF"))
        self.assertEqual(
            detail["payments"],
            [
                {
                    "method": "efectivo",
                    "amount_cents": 3600,
                    "kind": "cobro",
                    "occurred_at": detail["sale"]["sold_at"],
                }
            ],
        )
        refund = self.request(
            f"/api/sales/{sale['id']}/returns",
            {
                "method": "efectivo",
                "items": [
                    {
                        "line_id": detail["items"][0]["line_id"],
                        "quantity": 1,
                        "restock": True,
                    }
                ],
            },
        )
        self.assertEqual(refund["amount_cents"], 1200)
        with Sistema(self.database) as system:
            self.assertEqual(
                system.inventario.disponible(self.store.id, "SKU-WEB"), 3
            )
            payments = system.reportes.ventas_e_ingresos(
                "2000-01-01", "2100-01-01"
            )
            self.assertEqual(payments[0]["importe_neto_centavos"], 2400)

    def test_replenishment_workflow_and_invalid_date(self) -> None:
        replenishment_request = self.create_replenishment_request(
            self.store.id, [{"sku": "SKU-WEB", "quantity": 4}]
        )
        replenishment = {
            "center_id": self.center.id,
            "orders": [
                {
                    "request_id": replenishment_request,
                    "remito": "REM-WEB",
                    "seal": "SEAL-WEB",
                    "items": [{"sku": "SKU-WEB", "quantity": 4}],
                }
            ],
        }
        result = self.request("/api/replenishments", replenishment)
        package_id = result["packages"][0]["package_id"]
        duplicate = Request(
            self.base_url + "/api/replenishments",
            data=json.dumps(replenishment).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as duplicate_error:
            self.opener.open(duplicate)
        self.assertEqual(duplicate_error.exception.code, 400)
        with Sistema(self.database) as system:
            order_count = system.db.connection.execute(
                "SELECT COUNT(*) FROM replenishment_orders"
            ).fetchone()[0]
            self.assertEqual(order_count, 1)

        for action in ("precintar", "despachar"):
            self.request(f"/api/packages/{package_id}/action", {"action": action})
        self.select_profile(self.store.id)
        self.request(f"/api/packages/{package_id}/action", {"action": "recibir"})
        self.request(
            f"/api/packages/{package_id}/action",
            {
                "action": "verificar",
                "intacto": True,
                "coincide_remito": True,
            },
        )
        with Sistema(self.database) as system:
            self.assertEqual(
                system.inventario.disponible(self.store.id, "SKU-WEB"), 4
            )

        self.select_profile(self.center.id)
        request = Request(
            self.base_url + "/api/reports?from_date=invalid&to_date=invalid"
        )
        with self.assertRaises(HTTPError) as error:
            self.opener.open(request)
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
