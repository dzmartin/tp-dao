import json
import random
import re
import unittest
from datetime import date
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.container import create_application
from backend.datos.seed import create_random_seed
from backend.api.web_server import RetailWebHandler


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.application = create_application(":memory:")
        handler = type("TestRetailWebHandler", (RetailWebHandler,), {"application": self.application})
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.server.timeout = 5
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.cookie = ""

    def tearDown(self):
        self.server.server_close()
        self.application.close()

    def request(self, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, bytes]:
        result: dict[str, object] = {}

        def send_request() -> None:
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            headers = {"Cookie": self.cookie} if self.cookie else {}
            request = Request(self.base_url + path, data=payload, method=method, headers=headers)
            try:
                with urlopen(request, timeout=5) as response:
                    result["status"] = response.status
                    result["body"] = response.read()
                    set_cookie = response.headers.get("Set-Cookie")
                    if set_cookie:
                        self.cookie = set_cookie.split(";", 1)[0]
            except HTTPError as error:
                with error:
                    result["status"] = error.code
                    result["body"] = error.read()
                    set_cookie = error.headers.get("Set-Cookie")
                    if set_cookie:
                        self.cookie = set_cookie.split(";", 1)[0]
            except Exception as error:
                result["error"] = error

        client = Thread(target=send_request)
        client.start()
        self.server.handle_request()
        client.join(timeout=5)
        if client.is_alive():
            self.fail("HTTP client quedó esperando la respuesta.")
        if "error" in result:
            raise result["error"]
        return int(result["status"]), result["body"]

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        status, content = self.request(path, "POST", body)
        return status, json.loads(content)

    def initialize_accounts(self):
        status, result = self.post(
            "/api/auth/setup",
            {
                "cd_username": "centro",
                "cd_password": "clave-segura-centro",
                "local_username": "local01",
                "local_password": "clave-segura-local",
                "create_cd_location": {"code": "CD", "name": "Centro"},
                "create_local_location": {"code": "L01", "name": "Local"},
            },
        )
        self.assertEqual(status, 201, result)
        self.application.auth.create_user("admin", "clave-segura-admin", "ADMIN", None)
        self.login("admin", "clave-segura-admin")

    def test_local_can_download_report_summary_with_requester_and_own_location_details(self):
        self.initialize_accounts()
        self.application.retail.create_location(
            "L02", "Local Norte", "LOCAL", "Calle Norte 456", "30-22222222-2"
        )
        with self.application.repository.transaction():
            self.application.repository.execute(
                "UPDATE locations SET address=?,cuit=? WHERE code='L01'",
                ("Calle Principal 123", "30-11111111-1"),
            )
        service = self.application.retail
        product_id = service.create_product(
            "SKU-PDF", "Producto PDF", "General", "1.00", "2.00", 0
        )
        local_id = self.application.repository.one(
            "SELECT id FROM locations WHERE code='L01'"
        )["id"]
        with self.application.repository.transaction():
            self.application.repository.execute(
                "INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)",
                (local_id, product_id, 3),
            )
        service.sell("L01", {"SKU-PDF": 1}, {"transferencia": "2.00"}, "local01")

        self.login("local01", "clave-segura-local")
        today = date.today().isoformat()
        status, content = self.request(
            f"/api/reports/summary.pdf?start={today}&end={today}&location=L02"
        )

        self.assertEqual(status, 200)
        self.assertTrue(content.startswith(b"%PDF-1.4"))
        self.assertIn(b"Solicitado por: local01", content)
        self.assertIn(b"Generado el:", content)
        self.assertIn(b"Local \\050L01\\051", content)
        self.assertIn(b"Calle Principal 123", content)
        self.assertIn(b"30-11111111-1", content)
        self.assertIn(b"Producto PDF", content)
        self.assertNotIn(b"Local Norte", content)
        self.assertIn(b"%%EOF", content)

    def test_report_summary_pdf_rejects_an_invalid_date_range(self):
        self.initialize_accounts()

        status, content = self.request(
            "/api/reports/summary.pdf?start=2026-02-01&end=2026-01-01"
        )

        self.assertEqual(status, 400)
        self.assertIn("La fecha inicial", json.loads(content)["error"])

    def test_only_cd_can_resolve_rejected_packages(self):
        self.initialize_accounts()
        service = self.application.retail
        service.create_product("SKU-REJECT", "Producto rechazado", "General", 4, 8, 0)
        service.adjust_stock("CD", "SKU-REJECT", 3, "compra")
        order_id = service.create_replenishment("L01", {"SKU-REJECT": 3})
        service.pack(order_id, "B-REJECT", "R-REJECT", {"SKU-REJECT": 3})
        service.seal_package("B-REJECT", "PREC-REJECT")
        service.dispatch("B-REJECT")
        service.receive("B-REJECT", seal_intact=False)

        self.login("local01", "clave-segura-local")
        status, _ = self.post(
            "/api/packages/B-REJECT/disposition", {"disposition": "stock"}
        )
        self.assertEqual(status, 403)

        self.login("centro", "clave-segura-centro")
        status, result = self.post(
            "/api/packages/B-REJECT/disposition", {"disposition": "stock"}
        )
        self.assertEqual(status, 201, result)
        self.assertEqual(result["result"], "Mercadería devuelta al stock del CD.")
        _, bootstrap = self.request("/api/bootstrap")
        package = next(
            item for item in json.loads(bootstrap)["packages"] if item["code"] == "B-REJECT"
        )
        self.assertEqual(package["disposition"], "stock")

    def test_only_cd_can_cancel_a_sealed_package_and_repack_its_order(self):
        self.initialize_accounts()
        service = self.application.retail
        service.create_product("SKU-CANCEL", "Producto cancelable", "General", 4, 8, 0)
        service.adjust_stock("CD", "SKU-CANCEL", 5, "compra")
        order_id = service.create_replenishment("L01", {"SKU-CANCEL": 5})
        service.pack(order_id, "B-CANCEL", "R-CANCEL", {"SKU-CANCEL": 3})
        service.seal_package("B-CANCEL", "PREC-CANCEL")

        self.login("local01", "clave-segura-local")
        status, _ = self.post("/api/packages/B-CANCEL/cancel", {})
        self.assertEqual(status, 403)

        self.login("centro", "clave-segura-centro")
        status, result = self.post("/api/packages/B-CANCEL/cancel", {})
        self.assertEqual(status, 201, result)
        self.assertIn("cantidades", result["result"])
        self.assertEqual(
            self.application.repository.one(
                "SELECT status FROM replenishment_orders WHERE id=?", (order_id,)
            )["status"],
            "Abierta",
        )
        status, result = self.post(
            "/api/packages",
            {
                "destination_code": "L01",
                "code": "B-CANCEL-RETRY",
                "dispatch_note": "R-CANCEL-RETRY",
                "items": {"SKU-CANCEL": 5},
            },
        )
        self.assertEqual(status, 201, result)

    def test_local_can_edit_own_order_only_before_cd_prepares_it(self):
        self.initialize_accounts()
        service = self.application.retail
        service.create_product("SKU-EDIT-A", "Producto A", "General", 1, 2, 0)
        service.create_product("SKU-EDIT-B", "Producto B", "General", 1, 2, 0)
        service.adjust_stock("CD", "SKU-EDIT-A", 10, "compra")
        second_location = service.create_location("L02", "Local Norte", "LOCAL")
        self.application.auth.create_user(
            "local02",
            "clave-segura-local02",
            "LOCAL",
            second_location,
        )

        self.login("local01", "clave-segura-local")
        _, order = self.post(
            "/api/orders",
            {"destination_code": "L01", "items": {"SKU-EDIT-A": 5}},
        )
        order_id = order["result"]
        self.login("local02", "clave-segura-local02")
        other_store_status, _ = self.post(
            f"/api/orders/{order_id}/update",
            {"items": {"SKU-EDIT-A": 4}},
        )
        self.assertEqual(other_store_status, 403)

        self.login("local01", "clave-segura-local")
        update_status, update = self.post(
            f"/api/orders/{order_id}/update",
            {"items": {"SKU-EDIT-A": 3, "SKU-EDIT-B": 2}},
        )
        self.assertEqual(update_status, 201, update)
        updated_lines = self.application.repository.all(
            """SELECT p.sku,rl.quantity FROM replenishment_lines rl
               JOIN products p ON p.id=rl.product_id WHERE rl.order_id=? ORDER BY p.sku""",
            (order_id,),
        )
        self.assertEqual(
            [(line["sku"], line["quantity"]) for line in updated_lines],
            [("SKU-EDIT-A", 3), ("SKU-EDIT-B", 2)],
        )

        self.login("centro", "clave-segura-centro")
        service.pack(order_id, "B-EDIT", "R-EDIT", {"SKU-EDIT-A": 1})
        self.login("local01", "clave-segura-local")
        prepared_status, prepared_error = self.post(
            f"/api/orders/{order_id}/update",
            {"items": {"SKU-EDIT-A": 2}},
        )
        self.assertEqual(prepared_status, 400)
        self.assertIn("Solo se pueden modificar órdenes abiertas", prepared_error["error"])

    def login(self, username: str, password: str):
        status, result = self.post("/api/auth/login", {"username": username, "password": password})
        self.assertEqual(status, 200, result)

    def test_web_app_and_full_business_workflow(self):
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"La pulga mercado | Ventas e Inventario", html)
        self.assertIn("🐐".encode("utf-8"), html)
        self.assertIn(b'id="receipt-dialog"', html)
        self.assertIn(b'id="view-usuarios" data-roles="ADMIN"', html)
        self.assertIn(b'data-view="mermas" data-roles="CD"', html)
        self.assertIn(b'id="daily-sales-chart"', html)
        self.assertIn(b'id="user-role"', html)
        self.assertIn(b'Crear perfil de acceso', html)
        self.assertIn(b'Ventas e Inventario', html)
        self.assertIn(b'Registrar usuario', html)
        self.assertIn(b'id="register-form"', html)
        self.assertIn(b'<option value="LOCAL">Local</option>', html)
        self.assertIn(b'<option value="CD">Centro de Distribuci\xc3\xb3n</option>', html)
        self.assertIn(b'<option value="ADMIN">Administrador</option>', html)
        self.assertIn(b'id="setup-form"', html)
        self.assertIn(b'Rol del perfil', html)

        _, auth_status = self.request("/api/auth/status")
        self.assertEqual(json.loads(auth_status)["demo_shortcuts"], [])
        status, bootstrap = self.request("/api/bootstrap")
        self.assertEqual(status, 401)
        self.initialize_accounts()
        status, bootstrap = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        bootstrap_data = json.loads(bootstrap)
        self.assertEqual(len(bootstrap_data["locations"]), 2)
        self.assertNotIn("lots", bootstrap_data)
        self.assertNotIn("serials", bootstrap_data)
        supplier_status, supplier = self.post("/api/suppliers", {"tax_id": "TEST-1", "name": "Proveedor"})
        self.assertEqual(supplier_status, 201)
        product_status, product = self.post(
            "/api/products",
            {"sku": "SKU-1", "name": "Producto", "category": "General", "cost": "4.50", "price": "10.00", "minimum_stock": 1, "supplier_id": supplier["result"]},
        )
        self.assertEqual(product_status, 201)
        _, bootstrap = self.request("/api/bootstrap")
        product_data = next(item for item in json.loads(bootstrap)["products"] if item["sku"] == "SKU-1")
        self.assertNotIn("tracking_mode", product_data)
        self.assertEqual(self.post("/api/stock", {"location_code": "CD", "sku": "SKU-1", "delta": 10})[0], 201)

        self.login("local01", "clave-segura-local")
        order_status, order = self.post("/api/orders", {"destination_code": "L01", "items": {"SKU-1": 4}})
        self.assertEqual(order_status, 201)
        self.login("centro", "clave-segura-centro")
        package_status, package = self.post(
            "/api/packages",
            {
                "destination_code": "L01",
                "code": "B-1",
                "dispatch_note": "R-1",
                "items": {"SKU-1": 4},
            },
        )
        self.assertEqual(package_status, 201)
        _, bootstrap = self.request("/api/bootstrap")
        recorded_package = next(
            item for item in json.loads(bootstrap)["packages"] if item["code"] == "B-1"
        )
        self.assertEqual(recorded_package["destination_name"], "Local")
        self.assertEqual(
            [linked_order["id"] for linked_order in recorded_package["orders"]],
            [order["result"]],
        )
        self.assertEqual(
            recorded_package["order_lines"],
            [{"order_id": order["result"], "sku": "SKU-1", "quantity": 4}],
        )
        self.assertEqual(self.post("/api/packages/B-1/seal", {"seal": "PREC-1"})[0], 201)
        self.assertEqual(self.post("/api/packages/B-1/dispatch", {})[0], 201)
        self.assertEqual(self.post("/api/packages/B-1/receive", {"seal_intact": True, "dispatch_note_ok": True})[0], 403)
        self.login("local01", "clave-segura-local")
        self.assertEqual(self.post("/api/packages/B-1/receive", {"seal_intact": True, "dispatch_note_ok": True})[1]["result"], "Recibido")

        status, error = self.post("/api/sales", {"location_code": "L01", "items": [{"sku": "SKU-1", "quantity": 1}], "payments": [{"method": "efectivo", "amount": "10.00"}]})
        self.assertEqual(status, 400)
        self.assertIn("Stock verificado insuficiente", error["error"])

        self.assertEqual(self.post("/api/packages/B-1/verify", {})[0], 201)
        sale_status, sale = self.post(
            "/api/sales",
            {
                "location_code": "L01",
                "items": [{"sku": "SKU-1", "quantity": 2}],
                "payments": [
                    {"method": "efectivo", "amount": "10.00"},
                    {"method": "efectivo", "amount": "15.00"},
                ],
            },
        )
        self.assertEqual(sale_status, 201)
        self.assertTrue(sale["result"].startswith("V-"))
        with self.application.repository.transaction():
            self.application.repository.execute(
                "UPDATE locations SET address=?,cuit=? WHERE code='L01'",
                ("Mitre 123, Rosario, Santa Fe", "30-12345678-9"),
            )
            self.application.repository.execute(
                "UPDATE locations SET address=? WHERE code='CD'",
                ("Rivadavia 456, Buenos Aires, Buenos Aires",),
            )
            self.application.repository.execute(
                "UPDATE suppliers SET address=? WHERE id=?",
                ("Belgrano 789, Córdoba, Córdoba", supplier["result"]),
            )
        _, bootstrap = self.request("/api/bootstrap")
        sale_data = next(
            item
            for item in json.loads(bootstrap)["sales"]
            if item["receipt"] == sale["result"]
        )
        self.assertEqual(sale_data["location_address"], "Mitre 123, Rosario, Santa Fe")
        self.assertEqual(sale_data["location_cuit"], "30-12345678-9")
        self.assertEqual(sale_data["seller_name"], "local01")
        self.assertEqual(
            sale_data["distribution_center_address"],
            "Rivadavia 456, Buenos Aires, Buenos Aires",
        )
        self.assertEqual(sale_data["suppliers"][0]["address"], "Belgrano 789, Córdoba, Córdoba")
        pdf_status, pdf = self.request(f"/api/sales/{sale['result']}/receipt.pdf")
        self.assertEqual(pdf_status, 200)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"COMPROBANTE DE VENTA", pdf)
        section_y = re.search(rb"BT /F2 8 Tf 48 (\d+) Td \(COMPROBANTE DE VENTA\)", pdf)
        title_y = re.search(rb"BT /F2 19 Tf 48 (\d+) Td \(Comprobante de venta\)", pdf)
        self.assertIsNotNone(section_y)
        self.assertIsNotNone(title_y)
        self.assertGreaterEqual(int(section_y.group(1)) - int(title_y.group(1)), 20)
        self.assertIn(sale["result"].encode("ascii"), pdf)
        self.assertIn(b"Producto", pdf)
        self.assertIn(b"Sucursal Local", pdf)
        self.assertIn(b"Domicilio del local: Mitre 123, Rosario, Santa Fe", pdf)
        self.assertIn(b"CUIT del local: 30-12345678-9", pdf)
        self.assertIn(b"Vendedor/a: local01", pdf)
        self.assertIn(b"Domicilio del centro de distribuci", pdf)
        self.assertIn(b"Proveedor", pdf)
        self.assertIn(b"Belgrano 789, C", pdf)
        self.assertIn(b"Subtotal", pdf)
        self.assertIn(b"$ 20,00", pdf)
        self.assertIn(b"$ 4,00", pdf)
        self.assertIn(b"$ 16,00", pdf)
        self.assertIn(b"TOTAL", pdf)
        self.assertIn(b"Descuento por medio de pago", pdf)
        self.assertIn(b"TOTAL", pdf)
        self.assertIn(b"Pagado con", pdf)
        self.assertIn(b"Efectivo", pdf)
        self.assertNotIn(b"SN-", pdf)
        self.assertNotIn(b"LOTE-", pdf)
        self.assertNotIn(b"Trazabilidad", pdf)
        status, bootstrap = self.request("/api/bootstrap")
        recorded_sale = next(item for item in json.loads(bootstrap)["sales"] if item["receipt"] == sale["result"])
        self.assertEqual(recorded_sale["state"], "Confirmada")
        self.assertEqual(recorded_sale["location_name"], "Local")
        self.assertEqual(recorded_sale["lines"][0]["sku"], "SKU-1")
        self.assertNotIn("trace", recorded_sale["lines"][0])
        self.assertEqual(recorded_sale["lines"][0]["unit_price_cents"], 1000)
        self.assertEqual(recorded_sale["payments"], [{"method": "efectivo", "amount_cents": 1600}])
        return_status, returned = self.post("/api/returns", {"receipt": sale["result"], "items": [{"sku": "SKU-1", "quantity": 1}], "restock": True})
        self.assertEqual(return_status, 201)
        self.assertEqual(returned["result"], 800)
        _, bootstrap = self.request("/api/bootstrap")
        recorded_sale = next(item for item in json.loads(bootstrap)["sales"] if item["receipt"] == sale["result"])
        self.assertEqual(recorded_sale["state"], "Devolución parcial")

        report_status, reports = self.request("/api/reports?start=2000-01-01&end=2999-12-31&location=L01")
        self.assertEqual(report_status, 200)
        report_data = json.loads(reports)
        self.assertEqual(report_data["best_sellers"][0]["net_units"], 1)
        self.assertEqual(
            report_data["category_sales"],
            [{"category": "General", "net_units": 1, "net_revenue_cents": 800}],
        )
        self.assertEqual(report_data["logistics"]["verified_packages"], 1)
        self.assertEqual(sum(row["net_cents"] for row in report_data["daily_sales"]), 800)

    def test_seeded_accounts_expose_demo_shortcuts_and_can_login(self):
        create_random_seed(self.application, days=1, rng=random.Random(23))
        status, response = self.request("/api/auth/status")
        self.assertEqual(status, 200)
        shortcuts = json.loads(response)["demo_shortcuts"]
        self.assertEqual(len(shortcuts), 4)
        self.assertIn(b'id="demo-access-buttons"', self.request("/")[1])

        status, result = self.post(
            "/api/auth/login",
            {"username": shortcuts[0]["username"], "password": shortcuts[0]["password"]},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["user"]["role"], "ADMIN")

    def test_purchase_and_inventory_count_endpoints(self):
        self.initialize_accounts()
        _, supplier = self.post("/api/suppliers", {"tax_id": "SUP-API", "name": "Proveedor API"})
        _, product = self.post(
            "/api/products",
            {
                "sku": "API-1", "name": "Artículo API", "category": "General", "cost": 2,
                "price": 5, "minimum_stock": 2, "supplier_id": supplier["result"],
            },
        )
        _, bootstrap = self.request("/api/bootstrap")
        suggestion = next(item for item in json.loads(bootstrap)["purchase_suggestions"] if item["sku"] == "API-1")
        self.assertEqual(suggestion["suggested_quantity"], 2)

        order_status, order = self.post(
            "/api/purchase-orders", {"supplier_id": supplier["result"], "items": {"API-1": 2}}
        )
        self.assertEqual(order_status, 201)
        receive_status, _ = self.post(
            f"/api/purchase-orders/{order['result']}/receive", {"items": {"API-1": 2}}
        )
        self.assertEqual(receive_status, 201)

        count_status, count = self.post("/api/inventory-counts", {"location_code": "L01"})
        self.assertEqual(count_status, 201)
        lines_status, _ = self.post(
            f"/api/inventory-counts/{count['result']}/lines", {"quantities": {"API-1": 1}}
        )
        self.assertEqual(lines_status, 201)
        close_status, closed = self.post(f"/api/inventory-counts/{count['result']}/close", {})
        self.assertEqual(close_status, 201)
        self.assertEqual(closed["result"], "Inventario físico conciliado.")

        _, bootstrap = self.request("/api/bootstrap")
        result = json.loads(bootstrap)
        self.assertEqual(result["inventory_counts"][0]["status"], "Cerrado")
        for removed_key in ("customers", "price_lists", "price_list_items", "promotions"):
            self.assertNotIn(removed_key, result)
        self.assertEqual(self.post("/api/customers", {})[0], 403)

    def test_category_management_and_consolidated_replenishment_api(self):
        self.initialize_accounts()
        self.post("/api/locations", {"code": "L02", "name": "Local Norte", "type": "LOCAL"})
        category_status, category = self.post("/api/categories", {"name": "Bebidas"})
        self.assertEqual(category_status, 201)
        _, product = self.post(
            "/api/products",
            {"sku": "DRINK-1", "name": "Agua", "category": "Bebidas", "cost": 1, "price": 2, "minimum_stock": 1},
        )
        _, bootstrap = self.request("/api/bootstrap")
        data = json.loads(bootstrap)
        self.assertIn({"id": category["result"], "name": "Bebidas"}, data["categories"])
        self.assertEqual(next(item for item in data["products"] if item["id"] == product["result"])["category_id"], category["result"])
        rename_status, _ = self.post(
            f"/api/categories/{category['result']}/rename", {"name": "Bebidas sin alcohol"}
        )
        self.assertEqual(rename_status, 201)
        delete_status, delete_error = self.post(
            f"/api/categories/{category['result']}/delete", {}
        )
        self.assertEqual(delete_status, 400)
        self.assertIn("usan productos", delete_error["error"])

        batch_status, batch = self.post(
            "/api/auth/login", {"username": "centro", "password": "clave-segura-centro"}
        )
        self.assertEqual(batch_status, 200)
        batch_status, batch = self.post(
            "/api/replenishment-batches",
            {"destinations": [
                {"destination_code": "L01", "items": {"DRINK-1": 2}},
                {"destination_code": "L02", "items": {"DRINK-1": 3}},
            ]},
        )
        self.assertEqual(batch_status, 201)
        _, bootstrap = self.request("/api/bootstrap")
        orders = [item for item in json.loads(bootstrap)["orders"] if item["batch_code"]]
        self.assertEqual(len(orders), 2)
        self.assertEqual({item["batch_id"] for item in orders}, {batch["result"]})

    def test_master_data_update_and_safe_delete_api(self):
        self.initialize_accounts()
        cd_id = int(self.application.repository.one("SELECT id FROM locations WHERE code='CD'")["id"])
        assigned_store_id = int(self.application.repository.one("SELECT id FROM locations WHERE code='L01'")["id"])
        _, store = self.post("/api/locations", {"code": "L02", "name": "Local", "type": "LOCAL"})
        _, supplier = self.post("/api/suppliers", {"tax_id": "TAX-CRUD", "name": "Proveedor"})
        _, category = self.post("/api/categories", {"name": "Limpieza"})
        _, product = self.post(
            "/api/products",
            {
                "sku": "CRUD-1", "name": "Detergente", "category": "Limpieza",
                "cost": 3, "price": 5, "minimum_stock": 2, "supplier_id": supplier["result"],
            },
        )

        self.assertEqual(self.post(
            f"/api/locations/{store['result']}/update",
            {"code": "L03", "name": "Local Norte", "type": "LOCAL"},
        )[0], 201)
        self.assertEqual(self.post(
            f"/api/suppliers/{supplier['result']}/update",
            {"tax_id": "TAX-CRUD-2", "name": "Proveedor editado", "phone": "123", "email": "p@example.test"},
        )[0], 201)
        self.assertEqual(self.post(
            f"/api/products/{product['result']}/update",
            {
                "sku": "CRUD-2", "name": "Detergente líquido", "category": "Limpieza",
                "cost": 3.5, "price": 6, "minimum_stock": 4,
                "supplier_id": supplier["result"],
            },
        )[0], 201)
        self.assertEqual(self.post(f"/api/products/{product['result']}/delete", {})[0], 201)
        self.assertEqual(self.post(f"/api/categories/{category['result']}/delete", {})[0], 201)
        self.assertEqual(self.post(f"/api/suppliers/{supplier['result']}/delete", {})[0], 201)
        self.assertEqual(self.post(f"/api/locations/{store['result']}/delete", {})[0], 201)
        self.assertEqual(self.post(f"/api/locations/{assigned_store_id}/delete", {})[0], 400)
        self.assertEqual(
            self.post(
                f"/api/locations/{assigned_store_id}/update",
                {"code": "L01", "name": "Local", "type": "CD"},
            )[0],
            400,
        )
        self.assertEqual(self.post(f"/api/locations/{cd_id}/delete", {})[0], 400)

    def test_public_registration_creates_local_and_distribution_center_accounts(self):
        self.initialize_accounts()
        self.post("/api/auth/logout", {})
        self.cookie = ""

        status, response = self.request("/api/auth/status")
        self.assertEqual(status, 200)
        local_location = next(
            location for location in json.loads(response)["locations"]
            if location["code"] == "L01"
        )
        status, result = self.post(
            "/api/auth/register",
            {
                "username": "nuevo-local",
                "password": "clave-segura-nuevo",
                "role": "LOCAL",
                "location_id": local_location["id"],
            },
        )
        self.assertEqual(status, 201, result)
        self.assertEqual(result["user"]["role"], "LOCAL")
        self.assertEqual(result["user"]["location_code"], "L01")

        status, result = self.post(
            "/api/auth/register",
            {
                "username": "nuevo-centro",
                "password": "clave-segura-centro",
                "role": "CD",
                "location_id": "",
            },
        )
        self.assertEqual(status, 201, result)
        self.assertEqual(result["user"]["role"], "CD")
        self.assertIsNone(result["user"]["location_id"])

        status, result = self.post(
            "/api/auth/register",
            {
                "username": "perfil-invalido",
                "password": "clave-segura-invalid",
                "role": "ADMIN",
                "location_id": "",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Centro de Distribución o Local", result["error"])

    def test_first_start_registers_one_selected_role(self):
        status, result = self.post(
            "/api/auth/setup",
            {
                "role": "CD",
                "username": "primer-centro",
                "password": "clave-segura-centro",
                "create_location": {"code": "CD", "name": "Centro"},
            },
        )
        self.assertEqual(status, 201, result)
        self.assertEqual(self.application.auth.authenticate("primer-centro", "clave-segura-centro")["role"], "CD")
        self.assertEqual(
            self.application.auth.authenticate("primer-centro", "clave-segura-centro")["location_id"],
            None,
        )

    def test_first_start_admin_and_role_permissions(self):
        status, result = self.post(
            "/api/auth/setup",
            {
                "role": "ADMIN",
                "username": "primer-admin",
                "password": "clave-segura-admin",
            },
        )
        self.assertEqual(status, 201, result)
        self.login("primer-admin", "clave-segura-admin")
        self.assertEqual(self.application.auth.session_user(self.cookie.split("=", 1)[1])["role"], "ADMIN")
        self.assertEqual(
            self.application.repository.one("SELECT type FROM locations WHERE code='CD'")["type"],
            "CD",
        )
        store_id = self.post(
            "/api/locations", {"code": "ADMIN-LOCAL", "name": "Local Admin", "type": "LOCAL"}
        )[1]["result"]
        self.assertEqual(
            self.post(
                "/api/users",
                {
                    "username": "usuario-admin",
                    "password": "clave-segura-usuario",
                    "role": "LOCAL",
                    "location_id": store_id,
                },
            )[0],
            201,
        )
        self.assertEqual(self.post("/api/sales", {"location_code": "ADMIN-LOCAL"})[0], 403)
        self.login("usuario-admin", "clave-segura-usuario")
        self.assertEqual(
            self.post("/api/users", {"username": "no-admin", "password": "clave-segura-otro", "role": "ADMIN"})[0],
            403,
        )

    def test_first_start_registers_local_and_creates_central_location(self):
        status, result = self.post(
            "/api/auth/setup",
            {
                "role": "LOCAL",
                "username": "primer-local",
                "password": "clave-segura-local",
                "create_location": {"code": "L01", "name": "Local"},
            },
        )
        self.assertEqual(status, 201, result)
        user = self.application.auth.authenticate("primer-local", "clave-segura-local")
        self.assertEqual(user["role"], "LOCAL")
        self.assertEqual(user["location_code"], "L01")
        central_location = self.application.repository.one(
            "SELECT code,name,type FROM locations WHERE type='CD'"
        )
        self.assertIsNotNone(central_location)

    def test_deactivated_user_can_be_reactivated(self):
        self.initialize_accounts()
        location_id = self.application.repository.one(
            "SELECT id FROM locations WHERE code='L01'"
        )["id"]
        create_status, created = self.post(
            "/api/users",
            {
                "username": "local-reactivable",
                "password": "clave-segura-reactivable",
                "role": "LOCAL",
                "location_id": location_id,
            },
        )
        self.assertEqual(create_status, 201)
        user_id = created["result"]

        self.assertEqual(
            self.post(f"/api/users/{user_id}/delete", {})[0],
            201,
        )
        self.assertIsNone(
            self.application.auth.authenticate("local-reactivable", "clave-segura-reactivable")
        )

        activate_status, activated = self.post(f"/api/users/{user_id}/activate", {})
        self.assertEqual(activate_status, 201, activated)
        self.assertEqual(activated["result"], "Cuenta reactivada.")
        self.assertEqual(
            self.application.auth.authenticate(
                "local-reactivable", "clave-segura-reactivable"
            )["role"],
            "LOCAL",
        )
        self.assertEqual(
            self.post(f"/api/users/{user_id}/activate", {})[0],
            400,
        )

    def test_authentication_and_role_scoping(self):
        status, response = self.request("/api/auth/status")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(response)["setup_required"])
        self.assertEqual(self.request("/api/bootstrap")[0], 401)
        self.initialize_accounts()
        self.assertEqual(
            self.post("/api/auth/login", {"username": "centro", "password": "incorrect-password"})[0],
            401,
        )
        self.assertEqual(
            self.post(
                "/api/products",
                {"sku": "AUTH-1", "name": "Producto", "category": "General", "cost": 1, "price": 2, "minimum_stock": 0},
            )[0],
            201,
        )

        _, second_location = self.post(
            "/api/locations", {"code": "L02", "name": "Local Norte", "type": "LOCAL"}
        )
        self.assertEqual(
            self.post(
                "/api/users",
                {
                    "username": "local02",
                    "password": "clave-segura-local02",
                    "role": "LOCAL",
                    "location_id": second_location["result"],
                },
            )[0],
            201,
        )
        self.assertEqual(self.post("/api/stock", {"location_code": "CD", "sku": "AUTH-1", "delta": 2})[0], 201)
        self.assertEqual(
            self.post(
                "/api/sales",
                {"location_code": "L01", "items": [], "payments": []},
            )[0],
            403,
        )

        self.login("local02", "clave-segura-local02")
        _, remote_order = self.post(
            "/api/orders", {"destination_code": "L02", "items": {"AUTH-1": 1}}
        )
        self.login("centro", "clave-segura-centro")
        self.assertEqual(
            self.post(
                "/api/packages",
                {
                    "order_id": remote_order["result"],
                    "code": "B-REMOTE",
                    "dispatch_note": "R-REMOTE",
                    "items": {"AUTH-1": 1},
                },
            )[0],
            201,
        )
        self.assertEqual(self.post("/api/packages/B-REMOTE/seal", {"seal": "S-REMOTE"})[0], 201)
        self.assertEqual(self.post("/api/packages/B-REMOTE/dispatch", {})[0], 201)

        self.login("local01", "clave-segura-local")
        self.assertEqual(
            self.post(
                "/api/packages/B-REMOTE/receive",
                {"seal_intact": True, "dispatch_note_ok": True},
            )[0],
            403,
        )
        order_status, _ = self.post("/api/orders", {"destination_code": "L01", "items": {"AUTH-1": 1}})
        self.assertEqual(order_status, 201)
        self.assertEqual(
            self.post("/api/orders", {"destination_code": "L02", "items": {"AUTH-1": 1}})[0],
            403,
        )
        self.assertEqual(
            self.post("/api/products", {"sku": "BLOCKED", "name": "Blocked"})[0],
            403,
        )
        local_bootstrap = json.loads(self.request("/api/bootstrap")[1])
        self.assertNotEqual(
            self.application.repository.one(
                "SELECT password_hash FROM app_users WHERE username='local01'"
            )["password_hash"],
            "clave-segura-local",
        )
        self.assertEqual(
            {location["code"] for location in local_bootstrap["locations"]},
            {"CD", "L01"},
        )
        self.assertEqual({item["destination_code"] for item in local_bootstrap["orders"]}, {"L01"})
        self.assertNotIn("users", local_bootstrap)

        self.login("local02", "clave-segura-local02")
        second_bootstrap = json.loads(self.request("/api/bootstrap")[1])
        self.assertEqual(
            {location["code"] for location in second_bootstrap["locations"]},
            {"CD", "L02"},
        )
        self.assertEqual({item["destination_code"] for item in second_bootstrap["orders"]}, {"L02"})
        self.assertEqual({item["destination_code"] for item in second_bootstrap["packages"]}, {"L02"})
        report_status, report_body = self.request(
            "/api/reports?start=2000-01-01&end=2999-12-31&location=L01"
        )
        self.assertEqual(report_status, 200)
        report = json.loads(report_body)
        self.assertTrue(all(item["location"] == "L02" for item in report["inventory"]))
        self.assertEqual(self.post("/api/auth/logout", {})[0], 200)
        self.assertEqual(self.request("/api/bootstrap")[0], 401)

        self.login("centro", "clave-segura-centro")
        cd_bootstrap = json.loads(self.request("/api/bootstrap")[1])
        self.assertNotIn("users", cd_bootstrap)
        self.login("admin", "clave-segura-admin")
        admin_bootstrap = json.loads(self.request("/api/bootstrap")[1])
        local_user_id = next(user["id"] for user in admin_bootstrap["users"] if user["username"] == "local01")
        self.login("local01", "clave-segura-local")
        old_local_session = self.cookie
        self.login("admin", "clave-segura-admin")
        self.assertEqual(
            self.post(
                f"/api/users/{local_user_id}/password",
                {"password": "nueva-clave-segura"},
            )[0],
            201,
        )
        self.cookie = old_local_session
        self.assertEqual(self.request("/api/bootstrap")[0], 401)
        self.login("local01", "nueva-clave-segura")


if __name__ == "__main__":
    unittest.main()