import json
import sqlite3
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from container import create_application
from exceptions import BusinessRuleError


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "ui" / "web"


class RetailWebHandler(BaseHTTPRequestHandler):
    application = None

    def log_message(self, format: str, *args) -> None:
        print(f"{self.log_date_time_string()} {format % args}")

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size > 1_000_000:
            raise ValueError("La solicitud excede el tamaño permitido.")
        return json.loads(self.rfile.read(size) or b"{}")

    def _all(self, sql: str, parameters: tuple = ()) -> list[dict]:
        return [dict(row) for row in self.application.repository.all(sql, parameters)]

    def _one(self, sql: str, parameters: tuple = ()) -> dict:
        row = self.application.repository.one(sql, parameters)
        return dict(row) if row else {}

    def _bootstrap(self) -> dict:
        repository = self.application.repository
        locations = self._all("SELECT id,code,name,type FROM locations ORDER BY type DESC,code")
        products = self._all(
            """SELECT p.id,p.sku,p.name,p.category,p.cost_cents,p.price_cents,p.minimum_stock,
                      p.supplier_id,s.name AS supplier_name
               FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id ORDER BY p.name"""
        )
        stock = self._all(
            """SELECT l.code AS location_code,l.type AS location_type,p.sku,p.name,
                      COALESCE(s.quantity,0) AS quantity,p.minimum_stock,p.cost_cents,p.price_cents
               FROM locations l CROSS JOIN products p LEFT JOIN stock s
                 ON s.location_id=l.id AND s.product_id=p.id
               ORDER BY l.code,p.name"""
        )
        orders = self._all(
            """SELECT o.id,o.code,o.status,o.created_at,src.code AS origin_code,dst.code AS destination_code
               FROM replenishment_orders o JOIN locations src ON src.id=o.origin_id
               JOIN locations dst ON dst.id=o.destination_id ORDER BY o.id DESC"""
        )
        for order in orders:
            order["lines"] = self._all(
                """SELECT p.sku,p.name,rl.quantity FROM replenishment_lines rl
                   JOIN products p ON p.id=rl.product_id WHERE rl.order_id=? ORDER BY p.sku""",
                (order["id"],),
            )
        packages = self._all(
            """SELECT p.id,p.code,p.dispatch_note,p.seal,p.status,p.prepared_at,p.dispatched_at,p.received_at,
                      o.code AS order_code,o.id AS order_id,dst.code AS destination_code
               FROM packages p JOIN replenishment_orders o ON o.id=p.order_id
               JOIN locations dst ON dst.id=o.destination_id ORDER BY p.id DESC"""
        )
        for package in packages:
            package["lines"] = self._all(
                """SELECT p.sku,p.name,pl.quantity FROM package_lines pl JOIN products p ON p.id=pl.product_id
                   WHERE pl.package_id=? ORDER BY p.sku""",
                (package["id"],),
            )
            package["events"] = self._all(
                "SELECT state,occurred_at,detail FROM package_events WHERE package_id=? ORDER BY id",
                (package["id"],),
            )
        sales = self._all(
            """SELECT s.id,s.receipt,s.sold_at,s.total_cents,l.code AS location_code,
                      GROUP_CONCAT(p.method, ', ') AS payment_methods
               FROM sales s JOIN locations l ON l.id=s.location_id
               LEFT JOIN payments p ON p.sale_id=s.id
               GROUP BY s.id ORDER BY s.id DESC LIMIT 30"""
        )
        for sale in sales:
            sale["lines"] = self._all(
                """SELECT p.sku,p.name,sl.quantity,sl.returned_quantity,sl.unit_price_cents
                   FROM sale_lines sl JOIN products p ON p.id=sl.product_id WHERE sl.sale_id=?""",
                (sale["id"],),
            )
        today = date.today().isoformat()
        local_count = self._one("SELECT COUNT(*) AS count FROM locations WHERE type='LOCAL'").get("count", 0)
        product_count = self._one("SELECT COUNT(*) AS count FROM products").get("count", 0)
        low_stock = self._one(
            """SELECT COUNT(*) AS count FROM products p JOIN locations l ON l.type='LOCAL'
               LEFT JOIN stock s ON s.product_id=p.id AND s.location_id=l.id
               WHERE COALESCE(s.quantity,0)<=p.minimum_stock"""
        ).get("count", 0)
        sales_report = self.application.reports.sales_by_period(today, today)
        return {
            "locations": locations,
            "suppliers": self._all("SELECT id,tax_id,name,phone,email FROM suppliers ORDER BY name"),
            "products": products,
            "stock": stock,
            "orders": orders,
            "packages": packages,
            "sales": sales,
            "summary": {
                "local_count": local_count,
                "product_count": product_count,
                "low_stock_count": low_stock,
                "in_transit_count": self._one("SELECT COUNT(*) AS count FROM packages WHERE status='En tránsito'").get("count", 0),
                "today_net_cents": sum(row["net_cents"] for row in sales_report),
            },
        }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/bootstrap":
            self._json(200, self._bootstrap())
            return
        if parsed.path == "/api/reports":
            query = parse_qs(parsed.query)
            start = query.get("start", [date.today().replace(day=1).isoformat()])[0]
            end = query.get("end", [date.today().isoformat()])[0]
            self._json(
                200,
                {
                    "sales": self.application.reports.sales_by_period(start, end),
                    "best_sellers": self.application.reports.best_sellers_and_margin(start, end),
                    "inventory": self.application.reports.inventory_and_minimums(),
                    "logistics": self.application.reports.logistics_performance(),
                    "movements": self.application.reports.inventory_movements(start, end),
                },
            )
            return
        asset = "index.html" if parsed.path in ("/", "/index.html") else parsed.path.removeprefix("/assets/")
        asset_path = (WEB_ROOT / asset).resolve()
        if WEB_ROOT.resolve() not in asset_path.parents and asset_path != (WEB_ROOT / "index.html").resolve():
            self._json(404, {"error": "Recurso inexistente."})
            return
        if not asset_path.is_file():
            self._json(404, {"error": "Recurso inexistente."})
            return
        content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(asset_path.suffix, "application/octet-stream")
        self._send(200, asset_path.read_bytes(), content_type)

    def do_POST(self) -> None:
        try:
            body = self._body()
            path = unquote(urlparse(self.path).path)
            service = self.application.retail
            if path == "/api/locations":
                result = service.create_location(body["code"], body["name"], body["type"])
            elif path == "/api/suppliers":
                result = service.create_supplier(body["tax_id"], body["name"], body.get("phone", ""), body.get("email", ""))
            elif path == "/api/products":
                result = service.create_product(body["sku"], body["name"], body["category"], body["cost"], body["price"], int(body["minimum_stock"]), body.get("supplier_id") or None)
            elif path == "/api/stock":
                service.adjust_stock(body["location_code"], body["sku"], int(body["delta"]), body.get("reason", "ajuste"))
                result = "Stock actualizado."
            elif path == "/api/orders":
                result = service.create_replenishment(body["destination_code"], {key: int(value) for key, value in body["items"].items()})
            elif path == "/api/packages":
                result = service.pack(int(body["order_id"]), body["code"], body["dispatch_note"], {key: int(value) for key, value in body["items"].items()})
            elif path.startswith("/api/packages/"):
                package_code, action = path.removeprefix("/api/packages/").rsplit("/", 1)
                if action == "seal":
                    service.seal_package(package_code, body["seal"])
                    result = "Bulto precintado."
                elif action == "dispatch":
                    service.dispatch(package_code)
                    result = "Despacho registrado."
                elif action == "receive":
                    result = service.receive(package_code, bool(body["seal_intact"]), bool(body["dispatch_note_ok"]))
                elif action == "verify":
                    service.verify_package(package_code)
                    result = "Bulto verificado y stock liberado."
                else:
                    self._json(404, {"error": "Acción logística desconocida."})
                    return
            elif path == "/api/sales":
                result = service.sell(
                    body["location_code"],
                    {line["sku"]: int(line["quantity"]) for line in body["items"]},
                    {payment["method"]: payment["amount"] for payment in body["payments"]},
                )
            elif path == "/api/returns":
                result = service.return_items(body["receipt"], {line["sku"]: int(line["quantity"]) for line in body["items"]}, bool(body["restock"]))
            else:
                self._json(404, {"error": "Ruta inexistente."})
                return
            self._json(201, {"result": result})
        except (BusinessRuleError, sqlite3.IntegrityError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})


def main() -> None:
    application = create_application()
    handler = type("ApplicationRetailWebHandler", (RetailWebHandler,), {"application": application})
    server = HTTPServer(("127.0.0.1", 8000), handler)
    print("Sistema Ventas, Inventario y Logística disponible en http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()
        application.close()


if __name__ == "__main__":
    main()