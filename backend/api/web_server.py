import argparse
import json
import sqlite3
from decimal import Decimal
from http.cookies import CookieError, SimpleCookie
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from backend.container import create_application
from backend.dominio.exceptions import BusinessRuleError


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "frontend"


def _pdf_text(value: object) -> str:
    encoded = str(value).encode("cp1252", errors="replace")
    return "".join(
        f"\\{byte:03o}" if byte < 32 or byte > 126 or byte in (40, 41, 92) else chr(byte)
        for byte in encoded
    )


def _pdf_money(cents: int) -> str:
    whole, fraction = divmod(cents, 100)
    return f"$ {whole:,}".replace(",", ".") + f",{fraction:02d}"


def _receipt_pdf(sale: dict, lines: list[dict], payments: list[dict]) -> bytes:
    pages: list[bytes] = []
    commands: list[str] = []
    y = 0
    page_bottom = 64

    def text(
        value: object, x: int = 48, baseline: int | None = None, size: int = 10, bold: bool = False
    ) -> None:
        baseline = y if baseline is None else baseline
        font = "F2" if bold else "F1"
        commands.append(f"BT /{font} {size} Tf {x} {baseline} Td ({_pdf_text(value)}) Tj ET")

    def right_text(value: object, right: int, baseline: int, size: int = 10, bold: bool = False) -> None:
        rendered = str(value)
        text(rendered, right - int(len(rendered) * size * 0.52), baseline, size, bold)

    def draw_line(x1: int, y1: int, x2: int, y2: int, color: str = "0.82 0.85 0.82") -> None:
        commands.append(f"{color} RG {x1} {y1} m {x2} {y2} l S 0 0 0 RG")

    def table_heading() -> None:
        nonlocal y
        y -= 8
        commands.append(f"0.93 0.94 0.95 rg 48 {y - 18} 499 26 re f 0 0 0 rg")
        baseline = y - 1
        text("Producto", 55, baseline, 10, True)
        right_text("Cant.", 365, baseline, 10, True)
        right_text("Precio", 455, baseline, 10, True)
        right_text("Importe", 540, baseline, 10, True)
        y -= 31

    def new_page(include_table_heading: bool = True) -> None:
        nonlocal commands, y
        if commands:
            pages.append("\n".join(commands).encode("ascii"))
        commands = []
        y = 794
        text("FARO | OPERACIONES COMERCIALES", size=11, bold=True)
        text(f"Sucursal {sale['location_name']} · {sale['location_code']}", baseline=y - 18, size=9)
        y -= 48
        text("COMPROBANTE DE VENTA", size=8, bold=True)
        y -= 26
        text("Comprobante de venta", size=19, bold=True)
        right_text(f"Nº {sale['receipt']}", 547, y, 12, True)
        y -= 28
        sold_at = datetime.fromisoformat(sale["sold_at"]).astimezone().strftime("%d/%m/%Y %H:%M")
        text(f"Fecha: {sold_at}", size=10)
        y -= 18
        text(f"Estado: {sale['state']}", size=9)
        y -= 11
        if include_table_heading:
            table_heading()

    def wrap(value: str, max_chars: int) -> list[str]:
        words = value.split()
        wrapped = []
        current = ""
        for word in words:
            while len(word) > max_chars:
                if current:
                    wrapped.append(current)
                    current = ""
                wrapped.append(word[:max_chars])
                word = word[max_chars:]
            if current and len(current) + len(word) + 1 > max_chars:
                wrapped.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            wrapped.append(current)
        return wrapped or [""]

    new_page()
    for line in lines:
        description = wrap(line["name"], 48)
        row_height = max(24, 13 * len(description))
        if y - row_height < page_bottom:
            new_page()
        baseline = y - 11
        text(description[0], 55, baseline, 10, True)
        right_text(line["quantity"], 365, baseline, 10)
        right_text(_pdf_money(line["unit_price_cents"]), 455, baseline, 10)
        right_text(_pdf_money(line["quantity"] * line["unit_price_cents"]), 540, baseline, 10)
        offset = 13
        for part in description[1:]:
            text(part, 55, baseline - offset, 8)
            offset += 12
        y -= row_height
        draw_line(48, y, 547, y)
        y -= 4

    totals_height = 55 + 13 * len(payments)
    if y - totals_height < page_bottom:
        new_page(include_table_heading=False)
    y -= 5
    right_text(f"TOTAL  {_pdf_money(sale['total_cents'])}", 540, y, 15, True)
    y -= 28
    text("Pagado con", 55, y, 9, True)
    y -= 15
    for payment in payments:
        text(f"{payment['method'].capitalize()}", 55, y, 9)
        right_text(_pdf_money(payment["amount_cents"]), 540, y, 9)
        y -= 14
    text("Gracias por su compra", 55, y - 4, 9)

    if commands:
        pages.append("\n".join(commands).encode("ascii"))

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    page_ids = []
    for page_content in pages:
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii")
        )
        objects.append(
            b"<< /Length " + str(len(page_content)).encode("ascii") + b" >>\nstream\n"
            + page_content + b"\nendstream"
        )
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    ).encode("ascii")

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, content in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(content)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


class RetailWebHandler(BaseHTTPRequestHandler):
    application = None
    session_cookie = "faro_session"

    def log_message(self, format: str, *args) -> None:
        print(f"{self.log_date_time_string()} {format % args}")

    def _send(self, status: int, payload: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size > 1_000_000:
            raise ValueError("La solicitud excede el tamaño permitido.")
        payload = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("El cuerpo de la solicitud debe ser un objeto JSON.")
        return payload

    def _all(self, sql: str, parameters: tuple = ()) -> list[dict]:
        return [dict(row) for row in self.application.repository.all(sql, parameters)]

    def _one(self, sql: str, parameters: tuple = ()) -> dict:
        row = self.application.repository.one(sql, parameters)
        return dict(row) if row else {}

    def _session_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get(self.session_cookie)
        return morsel.value if morsel else None

    def _current_user(self) -> dict | None:
        return self.application.auth.session_user(self._session_token())

    def _deny(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _require_role(self, user: dict, *roles: str) -> bool:
        if user["role"] in roles:
            return True
        self._deny(403, "Tu perfil no tiene permiso para realizar esta operación.")
        return False

    def _authorize_post(self, path: str, body: dict, user: dict) -> bool:
        role = user["role"]
        if role == "ADMIN":
            allowed = (
                path in (
                    "/api/users", "/api/locations", "/api/suppliers", "/api/categories",
                    "/api/products", "/api/stock", "/api/inventory-counts", "/api/purchase-orders",
                )
                or path.startswith((
                    "/api/users/", "/api/locations/", "/api/suppliers/", "/api/categories/",
                    "/api/products/", "/api/inventory-counts/", "/api/purchase-orders/",
                ))
            )
            if allowed:
                return True
            self._deny(403, "El perfil Administrador solo puede gestionar datos y usuarios.")
            return False
        if role == "CD":
            if path in (
                "/api/stock", "/api/inventory-counts", "/api/packages",
                "/api/purchase-orders", "/api/replenishment-batches",
            ):
                return True
            if path.startswith(("/api/inventory-counts/", "/api/purchase-orders/")):
                return True
            if path.startswith("/api/packages/"):
                action = path.rsplit("/", 1)[-1]
                if action in ("seal", "dispatch"):
                    return True
                return self._require_role(user, "LOCAL")
            self._deny(403, "El perfil Centro de Distribución no tiene permiso para esta operación.")
            return False

        local_code = user["location_code"]
        if path in ("/api/orders", "/api/replenishment-batches"):
            if path == "/api/orders":
                destinations = [body.get("destination_code")]
            else:
                batch = body.get("destinations")
                destinations = (
                    [item.get("destination_code") for item in batch if isinstance(item, dict)]
                    if isinstance(batch, list)
                    else []
                )
            if (
                not destinations
                or path == "/api/replenishment-batches"
                and len(destinations) != len(body.get("destinations", []))
                or any(code != local_code for code in destinations)
            ):
                self._deny(403, "Solo podés solicitar reposición para tu sucursal.")
                return False
            return True
        if path in ("/api/sales", "/api/inventory-counts"):
            if path == "/api/sales" and body.get("location_code") != local_code:
                self._deny(403, "Solo podés operar ventas en tu sucursal.")
                return False
            if path == "/api/inventory-counts" and body.get("location_code") != local_code:
                self._deny(403, "Solo podés inventariar tu sucursal.")
                return False
            return True
        if path.startswith("/api/inventory-counts/"):
            count_id = path.removeprefix("/api/inventory-counts/").rsplit("/", 1)[0]
            location = self._one(
                """SELECT l.code FROM inventory_counts c JOIN locations l ON l.id=c.location_id
                   WHERE c.id=?""",
                (count_id,),
            )
            if location.get("code") != local_code:
                self._deny(403, "Solo podés operar inventarios de tu sucursal.")
                return False
            return True
        if path.startswith("/api/packages/"):
            package_code, action = path.removeprefix("/api/packages/").rsplit("/", 1)
            if action not in ("receive", "verify"):
                return self._require_role(user, "CD")
            package = self._one(
                """SELECT dst.code FROM packages p
                   JOIN replenishment_orders o ON o.id=p.order_id
                   JOIN locations dst ON dst.id=o.destination_id WHERE p.code=?""",
                (package_code,),
            )
            if not package:
                self._deny(404, "No existe ese bulto.")
                return False
            if package["code"] != local_code:
                self._deny(403, "Solo podés recibir bultos destinados a tu sucursal.")
                return False
            return True
        if path == "/api/returns":
            sale = self._one(
                """SELECT l.code FROM sales s JOIN locations l ON l.id=s.location_id
                   WHERE s.receipt=?""",
                (body.get("receipt"),),
            )
            if not sale:
                self._deny(404, "No existe una venta con ese comprobante.")
                return False
            if sale["code"] != local_code:
                self._deny(403, "Solo podés gestionar devoluciones de tu sucursal.")
                return False
            return True
        if path == "/api/auth/logout":
            return True
        self._deny(403, "Tu perfil Local no tiene permiso para realizar esta operación.")
        return False

    def _bootstrap(self, user: dict | None = None) -> dict:
        repository = self.application.repository
        locations = self._all("SELECT id,code,name,type FROM locations ORDER BY type DESC,code")
        products = self._all(
            """SELECT p.id,p.sku,p.name,p.category,p.category_id,p.cost_cents,p.price_cents,p.minimum_stock,
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
            """SELECT o.id,o.code,o.status,o.created_at,src.code AS origin_code,dst.code AS destination_code,
                      b.id AS batch_id,b.code AS batch_code
               FROM replenishment_orders o JOIN locations src ON src.id=o.origin_id
               JOIN locations dst ON dst.id=o.destination_id
               LEFT JOIN replenishment_batches b ON b.id=o.batch_id ORDER BY o.id DESC"""
        )
        for order in orders:
            order["lines"] = self._all(
                """SELECT p.sku,p.name,rl.quantity FROM replenishment_lines rl
                   JOIN products p ON p.id=rl.product_id WHERE rl.order_id=? ORDER BY p.sku""",
                (order["id"],),
            )
        packages = self._all(
            """SELECT p.id,p.code,p.dispatch_note,p.seal,p.status,p.prepared_at,p.dispatched_at,p.received_at,
                      o.code AS order_code,o.id AS order_id,dst.code AS destination_code,
                      dst.name AS destination_name
               FROM packages p JOIN replenishment_orders o ON o.id=p.order_id
               JOIN locations dst ON dst.id=o.destination_id ORDER BY p.id DESC"""
        )
        for package in packages:
            package["lines"] = self._all(
                """SELECT p.sku,p.name,pl.quantity FROM package_lines pl JOIN products p ON p.id=pl.product_id
                   WHERE pl.package_id=? ORDER BY p.sku""",
                (package["id"],),
            )
            package["orders"] = self._all(
                """SELECT o.id,o.code FROM package_orders po
                   JOIN replenishment_orders o ON o.id=po.order_id
                   WHERE po.package_id=? ORDER BY o.created_at,o.id""",
                (package["id"],),
            )
            package["order_lines"] = self._all(
                """SELECT pol.order_id,p.sku,pol.quantity FROM package_order_lines pol
                   JOIN products p ON p.id=pol.product_id
                   WHERE pol.package_id=? ORDER BY pol.order_id,p.sku""",
                (package["id"],),
            )
            package["events"] = self._all(
                "SELECT state,occurred_at,detail FROM package_events WHERE package_id=? ORDER BY id",
                (package["id"],),
            )
        sales = self._all(
            """SELECT s.id,s.receipt,s.sold_at,s.total_cents,s.state,
                      l.code AS location_code,l.name AS location_name,
                      GROUP_CONCAT(p.method, ', ') AS payment_methods
               FROM sales s JOIN locations l ON l.id=s.location_id
               LEFT JOIN payments p ON p.sale_id=s.id
               GROUP BY s.id ORDER BY s.id DESC LIMIT 30"""
        )
        for sale in sales:
            sale["lines"] = self._all(
                """SELECT p.sku,p.name,sl.quantity,sl.returned_quantity,sl.unit_price_cents
                   FROM sale_lines sl JOIN products p ON p.id=sl.product_id
                   WHERE sl.sale_id=?""",
                (sale["id"],),
            )
            sale["payments"] = self._all(
                "SELECT method,amount_cents FROM payments WHERE sale_id=? ORDER BY id",
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
        result = {
            "locations": locations,
            "suppliers": self._all("SELECT id,tax_id,name,phone,email FROM suppliers ORDER BY name"),
            "categories": self._all("SELECT id,name FROM categories ORDER BY name"),
            "products": products,
            "stock": stock,
            "orders": orders,
            "packages": packages,
            "sales": sales,
            "purchase_suggestions": self.application.retail.suggested_purchases(),
            "purchase_orders": self._all(
                """SELECT po.id,po.code,po.status,po.created_at,s.name AS supplier_name
                   FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id ORDER BY po.id DESC"""
            ),
            "inventory_counts": self._all(
                """SELECT ic.id,ic.code,ic.status,ic.created_at,l.code AS location_code
                   FROM inventory_counts ic JOIN locations l ON l.id=ic.location_id ORDER BY ic.id DESC"""
            ),
            "summary": {
                "local_count": local_count,
                "product_count": product_count,
                "low_stock_count": low_stock,
                "in_transit_count": self._one("SELECT COUNT(*) AS count FROM packages WHERE status='En tránsito'").get("count", 0),
                "today_net_cents": sum(row["net_cents"] for row in sales_report),
            },
        }
        for order in result["purchase_orders"]:
            order["lines"] = self._all(
                """SELECT p.sku,p.name,pol.quantity,pol.received_quantity
                   FROM purchase_order_lines pol JOIN products p ON p.id=pol.product_id
                   WHERE pol.order_id=? ORDER BY p.sku""",
                (order["id"],),
            )
        for count in result["inventory_counts"]:
            count["lines"] = self._all(
                """SELECT p.sku,p.name,cl.expected_quantity,cl.counted_quantity
                   FROM inventory_count_lines cl JOIN products p ON p.id=cl.product_id
                   WHERE cl.count_id=? ORDER BY p.sku""",
                (count["id"],),
            )
        if user:
            result["user"] = {
                key: user[key]
                for key in ("id", "username", "role", "location_id", "location_code", "location_name")
            }
            if user["role"] == "ADMIN":
                result["users"] = self.application.auth.list_users()
            elif user["role"] == "LOCAL":
                location_code = user["location_code"]
                result["locations"] = [
                    location for location in locations
                    if location["code"] == location_code or location["type"] == "CD"
                ]
                result["stock"] = [
                    item for item in result["stock"] if item["location_code"] == location_code
                ]
                result["orders"] = [
                    item for item in orders if item["destination_code"] == location_code
                ]
                result["packages"] = [
                    item for item in packages if item["destination_code"] == location_code
                ]
                result["sales"] = [
                    item for item in sales if item["location_code"] == location_code
                ]
                result["inventory_counts"] = [
                    item for item in result["inventory_counts"] if item["location_code"] == location_code
                ]
                result["purchase_suggestions"] = []
                result["purchase_orders"] = []
                result["suppliers"] = []
                result["summary"]["local_count"] = 1
                result["summary"]["low_stock_count"] = sum(
                    1 for item in result["stock"] if item["quantity"] <= item["minimum_stock"]
                )
                result["summary"]["in_transit_count"] = sum(
                    1 for item in result["packages"] if item["status"] == "En tránsito"
                )
                result["summary"]["today_net_cents"] = sum(
                    row["net_cents"]
                    for row in self.application.reports.sales_by_period(
                        today, today, location_code
                    )
                )
        return result

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/status":
            self._json(200, self.application.auth.setup_status())
            return
        user = self._current_user() if parsed.path.startswith("/api/") else None
        if parsed.path.startswith("/api/") and parsed.path != "/api/auth/status" and not user:
            self._deny(401, "Iniciá sesión para continuar.")
            return
        if parsed.path == "/api/auth/me":
            self._json(200, {"user": user})
            return
        if parsed.path.startswith("/api/sales/") and parsed.path.endswith("/receipt.pdf"):
            receipt = unquote(parsed.path[len("/api/sales/") : -len("/receipt.pdf")])
            sale = self._one(
                """SELECT s.id,s.receipt,s.sold_at,s.total_cents,s.state,
                          l.code AS location_code,l.name AS location_name
                   FROM sales s JOIN locations l ON l.id=s.location_id WHERE s.receipt=?""",
                (receipt,),
            )
            if not sale:
                self._json(404, {"error": "No existe una venta con ese comprobante."})
                return
            if user["role"] == "LOCAL" and sale["location_code"] != user["location_code"]:
                self._deny(403, "Solo podés consultar comprobantes de tu sucursal.")
                return
            lines = self._all(
                """SELECT p.sku,p.name,p.category,sl.quantity,sl.returned_quantity,sl.unit_price_cents
                   FROM sale_lines sl JOIN products p ON p.id=sl.product_id
                   WHERE sl.sale_id=? ORDER BY sl.id""",
                (sale["id"],),
            )
            payments = self._all(
                "SELECT method,amount_cents FROM payments WHERE sale_id=? ORDER BY id",
                (sale["id"],),
            )
            filename = f"comprobante-{sale['receipt']}.pdf"
            self._send(
                200,
                _receipt_pdf(sale, lines, payments),
                "application/pdf",
                {"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return
        if parsed.path == "/api/bootstrap":
            self._json(200, self._bootstrap(user))
            return
        if parsed.path == "/api/reports":
            query = parse_qs(parsed.query)
            start = query.get("start", [date.today().replace(day=1).isoformat()])[0]
            end = query.get("end", [date.today().isoformat()])[0]
            location = query.get("location", [""])[0] or None
            if user["role"] == "LOCAL":
                location = user["location_code"]
            self._json(
                200,
                {
                    "sales": self.application.reports.sales_by_period(start, end, location),
                    "daily_sales": self.application.reports.net_sales_by_day(start, end, location),
                    "best_sellers": self.application.reports.best_sellers_and_margin(start, end, location),
                    "inventory": self.application.reports.inventory_and_minimums(location),
                    "logistics": self.application.reports.logistics_performance(start, end, location),
                    "movements": self.application.reports.inventory_movements(start, end, location),
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
            if path == "/api/auth/setup":
                payload = body
                if "role" in payload:
                    self.application.auth.initialize_profile(
                        str(payload.get("username", "")),
                        str(payload.get("password", "")),
                        str(payload.get("role", "")),
                        int(payload["location_id"]) if payload.get("location_id") else None,
                        payload.get("create_location"),
                    )
                    self._json(201, {"result": "Configuración inicial completada."})
                    return
                self.application.auth.initialize(
                    payload.get("cd_username", ""),
                    payload.get("cd_password", ""),
                    payload.get("local_username", ""),
                    payload.get("local_password", ""),
                    int(payload["local_location_id"]) if payload.get("local_location_id") else None,
                    payload.get("create_cd_location"),
                    payload.get("create_local_location"),
                )
                self._json(201, {"result": "Configuración inicial completada."})
                return
            if path == "/api/auth/login":
                if self.application.auth.setup_status()["setup_required"]:
                    self._deny(409, "Completá la configuración inicial antes de iniciar sesión.")
                    return
                user = self.application.auth.authenticate(
                    str(body.get("username", "")), str(body.get("password", ""))
                )
                if not user:
                    self._deny(401, "Usuario o contraseña incorrectos.")
                    return
                token, expires_at = self.application.auth.create_session(user["id"])
                max_age = int((expires_at - datetime.now(expires_at.tzinfo)).total_seconds())
                self._send(
                    200,
                    json.dumps({"user": user}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                    {"Set-Cookie": f"{self.session_cookie}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"},
                )
                return
            if path == "/api/auth/register":
                user_id = self.application.auth.register_user(
                    str(body.get("username", "")),
                    str(body.get("password", "")),
                    str(body.get("role", "")),
                    int(body["location_id"]) if body.get("location_id") else None,
                )
                user = self.application.auth.authenticate(
                    str(body.get("username", "")), str(body.get("password", ""))
                )
                if not user:
                    raise BusinessRuleError("La cuenta se creó, pero no se pudo iniciar sesión.")
                token, expires_at = self.application.auth.create_session(user_id)
                max_age = int((expires_at - datetime.now(expires_at.tzinfo)).total_seconds())
                self._send(
                    201,
                    json.dumps({"user": user}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                    {"Set-Cookie": f"{self.session_cookie}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"},
                )
                return
            if path == "/api/auth/logout":
                self.application.auth.revoke_session(self._session_token())
                self._send(
                    200,
                    json.dumps({"result": "Sesión cerrada."}).encode("utf-8"),
                    "application/json; charset=utf-8",
                    {"Set-Cookie": f"{self.session_cookie}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"},
                )
                return

            user = self._current_user()
            if not user:
                self._deny(401, "Iniciá sesión para continuar.")
                return
            if not self._authorize_post(path, body, user):
                return
            service = self.application.retail
            if path == "/api/users":
                result = self.application.auth.create_user(
                    body["username"],
                    body["password"],
                    body["role"],
                    int(body["location_id"]) if body.get("location_id") else None,
                )
            elif path.startswith("/api/users/") and path.endswith("/password"):
                user_id = int(path.removeprefix("/api/users/").removesuffix("/password").strip("/"))
                self.application.auth.reset_password(
                    user_id, body["password"], user["id"], self._session_token()
                )
                result = "Contraseña actualizada."
            elif path.startswith("/api/users/") and path.endswith("/delete"):
                user_id = int(path.removeprefix("/api/users/").removesuffix("/delete").strip("/"))
                self.application.auth.delete_user(user_id, user["id"])
                result = "Cuenta desactivada."
            elif path == "/api/locations":
                result = service.create_location(body["code"], body["name"], body["type"])
            elif path.startswith("/api/locations/"):
                location_id, action = path.removeprefix("/api/locations/").rsplit("/", 1)
                if action == "update":
                    service.update_location(
                        int(location_id), body["code"], body["name"], body["type"]
                    )
                    result = "Ubicación actualizada."
                elif action == "delete":
                    service.delete_location(int(location_id))
                    result = "Ubicación eliminada."
                else:
                    self._json(404, {"error": "Acción de ubicación desconocida."})
                    return
            elif path == "/api/suppliers":
                result = service.create_supplier(body["tax_id"], body["name"], body.get("phone", ""), body.get("email", ""))
            elif path.startswith("/api/suppliers/"):
                supplier_id, action = path.removeprefix("/api/suppliers/").rsplit("/", 1)
                if action == "update":
                    service.update_supplier(
                        int(supplier_id), body["tax_id"], body["name"],
                        body.get("phone", ""), body.get("email", ""),
                    )
                    result = "Proveedor actualizado."
                elif action == "delete":
                    service.delete_supplier(int(supplier_id))
                    result = "Proveedor eliminado."
                else:
                    self._json(404, {"error": "Acción de proveedor desconocida."})
                    return
            elif path == "/api/categories":
                result = service.create_category(body["name"])
            elif path.startswith("/api/categories/"):
                category_id, action = path.removeprefix("/api/categories/").rsplit("/", 1)
                if action == "rename":
                    service.rename_category(int(category_id), body["name"])
                    result = "Categoría actualizada."
                elif action == "delete":
                    service.delete_category(int(category_id))
                    result = "Categoría eliminada."
                else:
                    self._json(404, {"error": "Acción de categoría desconocida."})
                    return
            elif path == "/api/products":
                result = service.create_product(
                    body["sku"], body["name"], body["category"], body["cost"], body["price"],
                    int(body["minimum_stock"]), body.get("supplier_id") or None,
                )
            elif path.startswith("/api/products/"):
                product_id, action = path.removeprefix("/api/products/").rsplit("/", 1)
                if action == "update":
                    service.update_product(
                        int(product_id), body["sku"], body["name"], body["category"],
                        body["cost"], body["price"], int(body["minimum_stock"]),
                        int(body["supplier_id"]) if body.get("supplier_id") else None,
                    )
                    result = "Producto actualizado."
                elif action == "delete":
                    service.delete_product(int(product_id))
                    result = "Producto eliminado."
                else:
                    self._json(404, {"error": "Acción de producto desconocida."})
                    return
            elif path == "/api/stock":
                service.adjust_stock(
                    body["location_code"], body["sku"], int(body["delta"]), body.get("reason", "ajuste"),
                )
                result = "Stock actualizado."
            elif path == "/api/purchase-orders":
                result = service.create_purchase_order(
                    int(body["supplier_id"]), {key: int(value) for key, value in body["items"].items()}
                )
            elif path.startswith("/api/purchase-orders/") and path.endswith("/receive"):
                order_id = int(path.removeprefix("/api/purchase-orders/").removesuffix("/receive").strip("/"))
                service.receive_purchase_order(
                    order_id,
                    {key: int(value) for key, value in body["items"].items()},
                )
                result = "Recepción de compra registrada."
            elif path == "/api/inventory-counts":
                result = service.open_inventory_count(body["location_code"])
            elif path.startswith("/api/inventory-counts/"):
                count_id, action = path.removeprefix("/api/inventory-counts/").rsplit("/", 1)
                if action == "lines":
                    service.set_inventory_count(
                        int(count_id),
                        {key: int(value) for key, value in body["quantities"].items()},
                    )
                    result = "Conteo guardado."
                elif action == "close":
                    service.close_inventory_count(int(count_id))
                    result = "Inventario físico conciliado."
                else:
                    self._json(404, {"error": "Acción de inventario desconocida."})
                    return
            elif path == "/api/orders":
                result = service.create_replenishment(body["destination_code"], {key: int(value) for key, value in body["items"].items()})
            elif path == "/api/replenishment-batches":
                result = service.create_consolidated_replenishment(body["destinations"])
            elif path == "/api/packages":
                items = {key: int(value) for key, value in body["items"].items()}
                if "destination_code" in body:
                    result = service.pack_for_destination(
                        body["destination_code"], body["code"], body["dispatch_note"], items
                    )
                else:
                    result = service.pack(
                        int(body["order_id"]), body["code"], body["dispatch_note"], items
                    )
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
                payments = {}
                for payment in body["payments"]:
                    method = payment["method"]
                    payments[method] = payments.get(method, Decimal("0")) + Decimal(str(payment["amount"]))
                result = service.sell(
                    body["location_code"],
                    {line["sku"]: int(line["quantity"]) for line in body["items"]},
                    payments,
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
    parser = argparse.ArgumentParser(description="Inicia la interfaz web de ventas e inventario.")
    parser.add_argument("--database", default=None, help="Ruta de la base SQLite.")
    arguments = parser.parse_args()
    application = create_application(arguments.database) if arguments.database else create_application()
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