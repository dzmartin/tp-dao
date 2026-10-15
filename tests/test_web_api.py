import json
import unittest
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from container import create_application
from web_server import RetailWebHandler


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.application = create_application(":memory:")
        handler = type("TestRetailWebHandler", (RetailWebHandler,), {"application": self.application})
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.server.timeout = 5
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.server_close()
        self.application.close()

    def request(self, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, bytes]:
        result: dict[str, object] = {}

        def send_request() -> None:
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            request = Request(self.base_url + path, data=payload, method=method)
            try:
                with urlopen(request, timeout=5) as response:
                    result["status"] = response.status
                    result["body"] = response.read()
            except HTTPError as error:
                with error:
                    result["status"] = error.code
                    result["body"] = error.read()
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

    def test_web_app_and_full_business_workflow(self):
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Tp | Operaciones comerciales", html)

        status, bootstrap = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertIn(b'"locations": []', bootstrap)

        self.assertEqual(self.post("/api/locations", {"code": "CD", "name": "Centro", "type": "CD"})[0], 201)
        self.assertEqual(self.post("/api/locations", {"code": "L01", "name": "Local", "type": "LOCAL"})[0], 201)
        supplier_status, supplier = self.post("/api/suppliers", {"tax_id": "TEST-1", "name": "Proveedor"})
        self.assertEqual(supplier_status, 201)
        product_status, product = self.post(
            "/api/products",
            {"sku": "SKU-1", "name": "Producto", "category": "General", "cost": "4.50", "price": "10.00", "minimum_stock": 1, "supplier_id": supplier["result"]},
        )
        self.assertEqual(product_status, 201)
        self.assertEqual(self.post("/api/stock", {"location_code": "CD", "sku": "SKU-1", "delta": 10})[0], 201)

        order_status, order = self.post("/api/orders", {"destination_code": "L01", "items": {"SKU-1": 4}})
        self.assertEqual(order_status, 201)
        package_status, package = self.post("/api/packages", {"order_id": order["result"], "code": "B-1", "dispatch_note": "R-1", "items": {"SKU-1": 4}})
        self.assertEqual(package_status, 201)
        self.assertEqual(self.post("/api/packages/B-1/seal", {"seal": "PREC-1"})[0], 201)
        self.assertEqual(self.post("/api/packages/B-1/dispatch", {})[0], 201)
        self.assertEqual(self.post("/api/packages/B-1/receive", {"seal_intact": True, "dispatch_note_ok": True})[1]["result"], "Recibido")

        status, error = self.post("/api/sales", {"location_code": "L01", "items": [{"sku": "SKU-1", "quantity": 1}], "payments": [{"method": "efectivo", "amount": "10.00"}]})
        self.assertEqual(status, 400)
        self.assertIn("Stock verificado insuficiente", error["error"])

        self.assertEqual(self.post("/api/packages/B-1/verify", {})[0], 201)
        sale_status, sale = self.post("/api/sales", {"location_code": "L01", "items": [{"sku": "SKU-1", "quantity": 2}], "payments": [{"method": "efectivo", "amount": "20.00"}]})
        self.assertEqual(sale_status, 201)
        self.assertTrue(sale["result"].startswith("V-"))
        return_status, returned = self.post("/api/returns", {"receipt": sale["result"], "items": [{"sku": "SKU-1", "quantity": 1}], "restock": True})
        self.assertEqual(return_status, 201)
        self.assertEqual(returned["result"], 1000)

        report_status, reports = self.request("/api/reports?start=2000-01-01&end=2999-12-31")
        self.assertEqual(report_status, 200)
        self.assertEqual(json.loads(reports)["best_sellers"][0]["net_units"], 1)


if __name__ == "__main__":
    unittest.main()