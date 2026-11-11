from __future__ import annotations

import json
import sqlite3
from http.cookies import CookieError, SimpleCookie
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .app import Sistema
from .domain import TipoEstablecimiento
from .receipts import crear_pdf_comprobante


STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"El campo {key} es obligatorio.")
    return value.strip()


def _positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"El campo {key} debe ser un entero positivo.")
    return value


def _nonnegative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"El campo {key} debe ser un entero no negativo.")
    return value


class ApiAuthorizationError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class FrontendHandler(BaseHTTPRequestHandler):
    database_path = "data/sistema.db"

    def _current_profile(self) -> dict[str, Any] | None:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        cookie = cookies.get("faro_profile")
        if cookie is None:
            return None
        try:
            profile_id = int(cookie.value)
        except ValueError:
            return None
        with Sistema(self.database_path) as system:
            row = system.db.connection.execute(
                "SELECT id, code, name, type FROM establishments WHERE id = ?",
                (profile_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def _require_profile(
        self, allowed_types: tuple[str, ...] | None = None
    ) -> dict[str, Any]:
        profile = self._current_profile()
        if profile is None:
            raise ApiAuthorizationError(401, "Elegí un perfil para continuar.")
        if allowed_types is not None and profile["type"] not in allowed_types:
            raise ApiAuthorizationError(
                403, "Este perfil no tiene permiso para realizar esa operación."
            )
        return profile

    def _send_bytes(
        self, body: bytes, content_type: str, status: int = 200
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        payload: object,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_pdf(self, body: bytes, sale_id: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header(
            "Content-Disposition", f"inline; filename=venta-{sale_id}.pdf"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("El tamaño de la solicitud no es válido.") from error
        if length <= 0 or length > 1_000_000:
            raise ValueError("El cuerpo de la solicitud está vacío o es demasiado grande.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("El cuerpo debe ser un JSON válido.") from error
        if not isinstance(payload, dict):
            raise ValueError("El cuerpo debe ser un objeto JSON.")
        return payload

    def _api_error(self, message: str, status: int) -> None:
        self._send_json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path in STATIC_FILES:
            filename, content_type = STATIC_FILES[parsed.path]
            body = (STATIC_DIR / filename).read_bytes()
            self._send_bytes(body, content_type)
            return
        if parsed.path == "/api/profiles":
            try:
                with Sistema(self.database_path) as system:
                    profiles = [
                        dict(row)
                        for row in system.db.connection.execute(
                            """SELECT id, code, name, type FROM establishments
                               ORDER BY CASE type WHEN 'CD' THEN 0 ELSE 1 END, name"""
                        ).fetchall()
                    ]
                    self._send_json(profiles)
            except sqlite3.Error as error:
                self._api_error(f"No se pudo consultar la base: {error}", 500)
            return
        if parsed.path == "/api/session":
            try:
                self._send_json({"profile": self._current_profile()})
            except sqlite3.Error as error:
                self._api_error(f"No se pudo consultar la base: {error}", 500)
            return
        if parsed.path == "/api/bootstrap":
            try:
                profile = self._require_profile()
                is_center = profile["type"] == "CD"
                with Sistema(self.database_path) as system:
                    connection = system.db.connection
                    location_filter = "" if is_center else " WHERE id = ?"
                    location_params = () if is_center else (profile["id"],)
                    locations = [
                        dict(row)
                        for row in connection.execute(
                            "SELECT id, code, name, type FROM establishments"
                            + location_filter
                            + " ORDER BY name",
                            location_params,
                        ).fetchall()
                    ]
                    cost_column = ", p.cost_cents" if is_center else ""
                    products = [
                        dict(row)
                        for row in connection.execute(
                            f"""SELECT p.id, p.sku, p.name, c.name AS category,
                                      p.price_cents{cost_column}
                               FROM products p
                               JOIN categories c ON c.id = p.category_id
                               ORDER BY p.name"""
                        ).fetchall()
                    ]
                    suppliers = [
                        dict(row)
                        for row in connection.execute(
                            "SELECT id, tax_id, name FROM suppliers ORDER BY name"
                        ).fetchall()
                    ] if is_center else []
                    for product in (products if is_center else []):
                        product["supplier_ids"] = [
                            int(row["supplier_id"])
                            for row in connection.execute(
                                """SELECT supplier_id FROM product_suppliers
                                   WHERE product_id = ? ORDER BY supplier_id""",
                                (product["id"],),
                            ).fetchall()
                        ]
                    inventory_filter = "" if is_center else " WHERE e.id = ?"
                    inventory_params = () if is_center else (profile["id"],)
                    valuation_column = (
                        "COALESCE(i.quantity, 0) * p.cost_cents"
                        if is_center
                        else "NULL"
                    )
                    inventory = [
                        dict(row)
                        for row in connection.execute(
                            f"""SELECT e.id AS establishment_id,
                                      e.name AS establecimiento, p.sku,
                                      p.name AS producto,
                                      i.quantity AS existencia,
                                      i.minimum_stock AS minimo,
                                      {valuation_column} AS valorizacion_centavos
                               FROM establishments e CROSS JOIN products p
                               JOIN inventory i
                                 ON i.establishment_id = e.id
                                AND i.product_id = p.id
                               {inventory_filter}
                               ORDER BY e.name, p.sku""",
                            inventory_params,
                        ).fetchall()
                    ]
                    today = date.today().isoformat()
                    sales_scope = "" if is_center else " AND establishment_id = ?"
                    sales_params = (
                        (today,)
                        if is_center
                        else (today, profile["id"])
                    )
                    sales_today = connection.execute(
                        "SELECT COUNT(*) AS count, COALESCE(SUM(total_cents), 0) AS total "
                        "FROM sales WHERE date(sold_at) = ?" + sales_scope,
                        sales_params,
                    ).fetchone()
                    recent_filter = "" if is_center else " WHERE s.establishment_id = ?"
                    recent_params = () if is_center else (profile["id"],)
                    recent_sales = [
                        dict(row)
                        for row in connection.execute(
                            f"""SELECT s.id, s.receipt, s.sold_at, s.status,
                                      s.total_cents, e.name AS store
                               FROM sales s
                               JOIN establishments e ON e.id = s.establishment_id
                               {recent_filter}
                               ORDER BY s.sold_at DESC LIMIT 8""",
                            recent_params,
                        ).fetchall()
                    ]
                    package_filter = "" if is_center else " WHERE o.destination_id = ?"
                    package_params = () if is_center else (profile["id"],)
                    packages = [
                        dict(row)
                        for row in connection.execute(
                            f"""SELECT p.id, p.dispatch_note, p.seal, p.status,
                                      o.source_id, o.destination_id,
                                      p.prepared_at, source.name AS center,
                                      destination.name AS store
                               FROM packages p
                               JOIN replenishment_orders o ON o.id = p.order_id
                               JOIN establishments source ON source.id = o.source_id
                               JOIN establishments destination
                                 ON destination.id = o.destination_id
                               {package_filter}
                               ORDER BY p.id DESC LIMIT 50""",
                            package_params,
                        ).fetchall()
                    ]
                    request_filter = "" if is_center else " WHERE rr.requester_id = ?"
                    request_params = () if is_center else (profile["id"],)
                    replenishment_requests = [
                        dict(row)
                        for row in connection.execute(
                            f"""SELECT rr.id, rr.status, rr.created_at,
                                       rr.requester_id, e.name AS store,
                                       rr.order_id
                                FROM replenishment_requests rr
                                JOIN establishments e ON e.id = rr.requester_id
                                {request_filter}
                                ORDER BY CASE rr.status WHEN 'Pendiente' THEN 0 ELSE 1 END,
                                         rr.created_at DESC LIMIT 100""",
                            request_params,
                        ).fetchall()
                    ]
                    center_inventory: dict[str, int] = {}
                    reserved_stock: dict[str, int] = {}
                    if is_center:
                        center_inventory = {
                            str(row["sku"]): int(row["existencia"])
                            for row in inventory
                            if int(row["establishment_id"]) == int(profile["id"])
                        }
                        reserved_stock = {
                            str(row["sku"]): int(row["quantity"])
                            for row in connection.execute(
                                """SELECT pr.sku, SUM(pl.quantity) AS quantity
                                   FROM package_lines pl
                                   JOIN packages pk ON pk.id = pl.package_id
                                   JOIN replenishment_orders ro ON ro.id = pk.order_id
                                   JOIN products pr ON pr.id = pl.product_id
                                   WHERE ro.source_id = ?
                                     AND pk.status IN ('Preparado', 'Precintado')
                                   GROUP BY pr.sku""",
                                (profile["id"],),
                            ).fetchall()
                        }
                    for replenishment_request in replenishment_requests:
                        items = [
                            dict(row)
                            for row in connection.execute(
                                """SELECT p.sku, p.name, l.requested AS quantity,
                                          l.fulfilled,
                                          l.requested - l.fulfilled AS remaining_quantity
                                   FROM replenishment_request_lines l
                                   JOIN products p ON p.id = l.product_id
                                   WHERE l.request_id = ? ORDER BY p.name""",
                                (replenishment_request["id"],),
                            ).fetchall()
                        ]
                        if is_center:
                            for item in items:
                                item["available_quantity"] = max(
                                    0,
                                    center_inventory.get(str(item["sku"]), 0)
                                    - reserved_stock.get(str(item["sku"]), 0),
                                )
                        replenishment_request["items"] = items
                    self._send_json(
                        {
                            "profile": profile,
                            "locations": locations,
                            "products": products,
                            "suppliers": suppliers,
                            "inventory": inventory,
                            "sales_today": dict(sales_today),
                            "recent_sales": recent_sales,
                            "packages": packages,
                            "replenishment_requests": replenishment_requests,
                        }
                    )
            except ApiAuthorizationError as error:
                self._api_error(str(error), error.status)
            except ValueError as error:
                self._api_error(str(error), 400)
            except sqlite3.Error as error:
                self._api_error(f"No se pudo consultar la base: {error}", 500)
            return
        if parsed.path == "/api/sales":
            try:
                profile = self._require_profile()
                sales_filter = (
                    "" if profile["type"] == "CD" else " WHERE s.establishment_id = ?"
                )
                params = () if profile["type"] == "CD" else (profile["id"],)
                with Sistema(self.database_path) as system:
                    sales = [
                        dict(row)
                        for row in system.db.connection.execute(
                            f"""SELECT s.id, s.receipt, s.sold_at, s.status,
                                      s.total_cents, e.name AS store
                               FROM sales s
                               JOIN establishments e ON e.id = s.establishment_id
                               {sales_filter}
                               ORDER BY s.sold_at DESC LIMIT 50""",
                            params,
                        ).fetchall()
                    ]
                    self._send_json(sales)
            except ApiAuthorizationError as error:
                self._api_error(str(error), error.status)
            except sqlite3.Error as error:
                self._api_error(f"No se pudo consultar la base: {error}", 500)
            return
        if (
            parsed.path.startswith("/api/sales/")
            and parsed.path.endswith("/receipt.pdf")
        ):
            try:
                profile = self._require_profile()
                sale_id = self._receipt_sale_id_from_path(parsed.path)
                with Sistema(self.database_path) as system:
                    connection = system.db.connection
                    sale_row = connection.execute(
                        """SELECT s.id, s.receipt, s.sold_at, s.status,
                                  s.total_cents, s.establishment_id,
                                  e.name AS store
                           FROM sales s
                           JOIN establishments e ON e.id = s.establishment_id
                           WHERE s.id = ?""",
                        (sale_id,),
                    ).fetchone()
                    if sale_row is None:
                        self._api_error(f"No existe la venta {sale_id}.", 404)
                        return
                    if (
                        profile["type"] == "SUCURSAL"
                        and int(sale_row["establishment_id"]) != int(profile["id"])
                    ):
                        self._api_error(
                            "No podés consultar ventas de otra sucursal.", 403
                        )
                        return
                    items = [
                        dict(row)
                        for row in connection.execute(
                            """SELECT p.sku, p.name, sl.quantity AS sold_quantity,
                                      sl.unit_price_cents
                               FROM sale_lines sl
                               JOIN products p ON p.id = sl.product_id
                               WHERE sl.sale_id = ? ORDER BY sl.id""",
                            (sale_id,),
                        ).fetchall()
                    ]
                    payments = [
                        dict(row)
                        for row in connection.execute(
                            """SELECT method, amount_cents, kind
                               FROM payments WHERE sale_id = ? ORDER BY id""",
                            (sale_id,),
                        ).fetchall()
                    ]
                    pdf = crear_pdf_comprobante(
                        dict(sale_row), items, payments
                    )
                    self._send_pdf(pdf, sale_id)
            except ApiAuthorizationError as error:
                self._api_error(str(error), error.status)
            except ValueError as error:
                self._api_error(str(error), 400)
            except sqlite3.Error as error:
                self._api_error(f"No se pudo consultar la base: {error}", 500)
            return
        if parsed.path.startswith("/api/sales/"):
            try:
                profile = self._require_profile()
                sale_id = self._sale_id_from_path(parsed.path)
                with Sistema(self.database_path) as system:
                    connection = system.db.connection
                    sale = connection.execute(
                        """SELECT s.id, s.receipt, s.sold_at, s.status,
                                  s.total_cents, s.establishment_id,
                                  e.name AS store
                           FROM sales s
                           JOIN establishments e ON e.id = s.establishment_id
                           WHERE s.id = ?""",
                        (sale_id,),
                    ).fetchone()
                    if sale is None:
                        self._api_error(f"No existe la venta {sale_id}.", 404)
                        return
                    if (
                        profile["type"] == "SUCURSAL"
                        and int(sale["establishment_id"]) != int(profile["id"])
                    ):
                        self._api_error(
                            "No podés consultar ventas de otra sucursal.", 403
                        )
                        return
                    lines = [
                        dict(row)
                        for row in connection.execute(
                            """SELECT sl.id AS line_id, p.sku, p.name,
                                      sl.quantity AS sold_quantity,
                                      sl.unit_price_cents,
                                      sl.quantity - COALESCE((
                                          SELECT SUM(rl.quantity)
                                          FROM return_lines rl
                                          WHERE rl.sale_line_id = sl.id
                                      ), 0) AS remaining_quantity
                               FROM sale_lines sl
                               JOIN products p ON p.id = sl.product_id
                               WHERE sl.sale_id = ? ORDER BY sl.id""",
                            (sale_id,),
                        ).fetchall()
                    ]
                    payments = [
                        dict(row)
                        for row in connection.execute(
                            """SELECT method, amount_cents, kind, occurred_at
                               FROM payments WHERE sale_id = ? ORDER BY id""",
                            (sale_id,),
                        ).fetchall()
                    ]
                    self._send_json(
                        {"sale": dict(sale), "items": lines, "payments": payments}
                    )
            except ApiAuthorizationError as error:
                self._api_error(str(error), error.status)
            except ValueError as error:
                self._api_error(str(error), 400)
            except sqlite3.Error as error:
                self._api_error(f"No se pudo consultar la base: {error}", 500)
            return
        if parsed.path == "/api/reports":
            try:
                self._require_profile(("CD",))
            except ApiAuthorizationError as error:
                self._api_error(str(error), error.status)
                return
            query = parse_qs(parsed.query)
            start = query.get("from_date", [date.today().isoformat()])[0]
            end = query.get("to_date", [date.today().isoformat()])[0]
            try:
                with Sistema(self.database_path) as system:
                    connection = system.db.connection
                    result = {
                        "ventas_e_ingresos": system.reportes.ventas_e_ingresos(
                            start, end
                        ),
                        "productos_y_margen": system.reportes.productos_y_margen(
                            start, end
                        ),
                        "existencias_y_minimos": system.reportes.existencias_y_minimos(),
                        "bultos_rechazados": system.reportes.bultos_rechazados(
                            start, end
                        ),
                        "transito_promedio": system.reportes.transito_promedio(
                            start, end
                        ),
                        "movimientos_y_valorizacion": {
                            str(row["name"]): system.reportes.movimientos_y_valorizacion(
                                int(row["id"]), start, end
                            )
                            for row in connection.execute(
                                "SELECT id, name FROM establishments ORDER BY name"
                            ).fetchall()
                        },
                    }
                    self._send_json(result)
            except ValueError as error:
                self._api_error(str(error), 400)
            except sqlite3.Error as error:
                self._api_error(f"No se pudo consultar la base: {error}", 500)
            return
        if parsed.path.startswith("/api/"):
            self._api_error("No se encontró ese recurso.", 404)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            if self.path == "/api/session":
                profile_id = _positive_int(payload, "profile_id")
                with Sistema(self.database_path) as system:
                    profile = system.db.connection.execute(
                        "SELECT id, code, name, type FROM establishments WHERE id = ?",
                        (profile_id,),
                    ).fetchone()
                if profile is None:
                    raise ValueError("No existe el perfil seleccionado.")
                self._send_json(
                    {"profile": dict(profile)},
                    headers={
                        "Set-Cookie": (
                            f"faro_profile={profile_id}; Path=/; HttpOnly; SameSite=Strict"
                        )
                    },
                )
                return
            if self.path == "/api/sales":
                self._create_sale(payload)
                return
            if self.path == "/api/locations":
                self._create_location(payload)
                return
            if self.path == "/api/replenishment-requests":
                self._create_replenishment_request(payload)
                return
            if self.path == "/api/suppliers":
                self._create_supplier(payload)
                return
            if self.path == "/api/products":
                self._create_product(payload)
                return
            if self.path.startswith("/api/products/"):
                self._update_product(payload)
                return
            if self.path == "/api/stock-adjust":
                self._adjust_stock(payload)
                return
            if self.path == "/api/stock-minimum":
                self._set_stock_minimum(payload)
                return
            if self.path == "/api/replenishments":
                self._create_replenishment(payload)
                return
            if self.path.startswith("/api/packages/"):
                self._advance_package(payload)
                return
            if self.path.startswith("/api/sales/"):
                self._create_return(payload)
                return
            self._api_error("No se encontró ese recurso.", 404)
        except sqlite3.IntegrityError as error:
            self._api_error(f"No se pudo guardar la operación: {error}", 409)
        except sqlite3.Error as error:
            self._api_error(f"No se pudo guardar la operación: {error}", 500)
        except ApiAuthorizationError as error:
            self._api_error(str(error), error.status)
        except ValueError as error:
            self._api_error(str(error), 400)

    def do_DELETE(self) -> None:
        if self.path != "/api/session":
            self._api_error("No se encontró ese recurso.", 404)
            return
        self._send_json(
            {"message": "Perfil cerrado."},
            headers={
                "Set-Cookie": (
                    "faro_profile=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                )
            },
        )

    def do_PUT(self) -> None:
        try:
            payload = self._read_json()
            if self.path.startswith("/api/products/"):
                self._update_product(payload)
                return
            self._api_error("No se encontró ese recurso.", 404)
        except sqlite3.IntegrityError as error:
            self._api_error(f"No se pudo guardar la operación: {error}", 409)
        except sqlite3.Error as error:
            self._api_error(f"No se pudo guardar la operación: {error}", 500)
        except ApiAuthorizationError as error:
            self._api_error(str(error), error.status)
        except ValueError as error:
            self._api_error(str(error), 400)

    def _create_location(self, payload: dict[str, Any]) -> None:
        code = _required_string(payload, "code")
        name = _required_string(payload, "name")
        kind = _required_string(payload, "type")
        try:
            location_type = TipoEstablecimiento(kind)
        except ValueError as error:
            raise ValueError("El tipo debe ser CD o SUCURSAL.") from error
        with Sistema(self.database_path) as system:
            establishments_count = system.db.connection.execute(
                "SELECT COUNT(*) FROM establishments"
            ).fetchone()[0]
            if establishments_count == 0:
                if location_type != TipoEstablecimiento.CENTRO_DISTRIBUCION:
                    raise ValueError(
                        "La configuración inicial debe crear un centro de distribución."
                    )
            else:
                self._require_profile(("CD",))
                if location_type != TipoEstablecimiento.SUCURSAL:
                    raise ApiAuthorizationError(
                        403, "Desde un centro solo se pueden dar de alta sucursales."
                    )
            location = system.catalogo.crear_establecimiento(
                code, name, location_type
            )
        self._send_json({"id": location.id, "code": location.codigo}, 201)

    def _create_supplier(self, payload: dict[str, Any]) -> None:
        self._require_profile(("CD",))
        tax_id = _required_string(payload, "tax_id")
        name = _required_string(payload, "name")
        with Sistema(self.database_path) as system:
            supplier_id = system.catalogo.crear_proveedor(tax_id, name)
        self._send_json({"id": supplier_id, "tax_id": tax_id}, 201)

    def _create_product(self, payload: dict[str, Any]) -> None:
        self._require_profile(("CD",))
        sku = _required_string(payload, "sku")
        name = _required_string(payload, "name")
        category = _required_string(payload, "category")
        supplier_id = payload.get("supplier_id")
        if supplier_id is None:
            tax_id = _required_string(payload, "supplier_tax_id")
            supplier_name = _required_string(payload, "supplier_name")
        else:
            supplier_id = _positive_int(payload, "supplier_id")
        cost_cents = _nonnegative_int(payload, "cost_cents")
        price_cents = _positive_int(payload, "price_cents")
        with Sistema(self.database_path) as system:
            connection = system.db.connection
            with system.db.transaccion():
                if connection.execute(
                    "SELECT 1 FROM products WHERE sku = ?", (sku,)
                ).fetchone():
                    raise ValueError(f"El SKU {sku} ya está en uso.")
                if supplier_id is None:
                    supplier = connection.execute(
                        "SELECT id FROM suppliers WHERE tax_id = ?", (tax_id,)
                    ).fetchone()
                    supplier_id = (
                        int(supplier["id"])
                        if supplier
                        else system.catalogo.crear_proveedor(tax_id, supplier_name)
                    )
                product_id = system.catalogo.crear_producto(
                    sku,
                    name,
                    category,
                    cost_cents,
                    price_cents,
                    supplier_id,
                )
        self._send_json({"id": product_id, "sku": sku}, 201)

    def _update_product(self, payload: dict[str, Any]) -> None:
        self._require_profile(("CD",))
        path = urlsplit(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "products"]:
            self._api_error("La ruta del producto no es válida.", 404)
            return
        sku = unquote(parts[2])
        name = _required_string(payload, "name")
        category = _required_string(payload, "category")
        supplier_id = _positive_int(payload, "supplier_id")
        cost_cents = _nonnegative_int(payload, "cost_cents")
        price_cents = _positive_int(payload, "price_cents")
        with Sistema(self.database_path) as system:
            system.catalogo.actualizar_producto(
                sku,
                name,
                category,
                cost_cents,
                price_cents,
                supplier_id,
            )
        self._send_json({"sku": sku, "message": "Producto actualizado."})

    def _set_stock_minimum(self, payload: dict[str, Any]) -> None:
        profile = self._require_profile(("CD", "SUCURSAL"))
        sku = _required_string(payload, "sku")
        minimum_stock = _nonnegative_int(payload, "minimum_stock")
        with Sistema(self.database_path) as system:
            system.inventario.configurar_stock_minimo(
                int(profile["id"]), sku, minimum_stock
            )
        self._send_json({"sku": sku, "minimum_stock": minimum_stock})

    @staticmethod
    def _sale_id_from_path(path: str) -> int:
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "sales"]:
            raise ValueError("La ruta de la venta no es válida.")
        try:
            sale_id = int(parts[2])
        except ValueError as error:
            raise ValueError("El identificador de venta no es válido.") from error
        if sale_id <= 0:
            raise ValueError("El identificador de venta no es válido.")
        return sale_id

    @staticmethod
    def _receipt_sale_id_from_path(path: str) -> int:
        parts = path.strip("/").split("/")
        if (
            len(parts) != 4
            or parts[:2] != ["api", "sales"]
            or parts[3] != "receipt.pdf"
        ):
            raise ValueError("La ruta del comprobante no es válida.")
        try:
            sale_id = int(parts[2])
        except ValueError as error:
            raise ValueError("El identificador de venta no es válido.") from error
        if sale_id <= 0:
            raise ValueError("El identificador de venta no es válido.")
        return sale_id

    def _create_return(self, payload: dict[str, Any]) -> None:
        profile = self._require_profile(("SUCURSAL",))
        path = urlsplit(self.path).path
        parts = path.strip("/").split("/")
        if (
            len(parts) != 4
            or parts[:2] != ["api", "sales"]
            or parts[3] != "returns"
        ):
            self._api_error("La ruta de la devolución no es válida.", 404)
            return
        try:
            sale_id = int(parts[2])
        except ValueError as error:
            raise ValueError("El identificador de venta no es válido.") from error
        if sale_id <= 0:
            raise ValueError("El identificador de venta no es válido.")
        method = _required_string(payload, "method")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("La devolución debe incluir al menos un producto.")
        items: list[tuple[int, int, bool]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("Cada producto devuelto debe ser un objeto.")
            line_id = _positive_int(item, "line_id")
            quantity = _positive_int(item, "quantity")
            restock = item.get("restock")
            if not isinstance(restock, bool):
                raise ValueError("Debe indicarse si cada producto vuelve al stock.")
            items.append((line_id, quantity, restock))
        with Sistema(self.database_path) as system:
            sale = system.db.connection.execute(
                "SELECT establishment_id FROM sales WHERE id = ?", (sale_id,)
            ).fetchone()
            if sale is None:
                raise ValueError(f"No existe la venta {sale_id}.")
            if int(sale["establishment_id"]) != int(profile["id"]):
                raise ApiAuthorizationError(
                    403, "No podés registrar devoluciones de otra sucursal."
                )
            refund = system.ventas.registrar_devolucion(sale_id, items, method)
        self._send_json(
            {
                "id": refund.id,
                "amount_cents": refund.importe_centavos,
                "restocked": refund.vuelve_al_stock,
            },
            201,
        )

    def _create_sale(self, payload: dict[str, Any]) -> None:
        profile = self._require_profile(("SUCURSAL",))
        store_id = int(profile["id"])
        requested_store = payload.get("store_id")
        if requested_store is not None and requested_store != store_id:
            raise ApiAuthorizationError(
                403, "La venta solo puede registrarse en la sucursal del perfil."
            )
        method = _required_string(payload, "method")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("La venta debe incluir al menos un producto.")
        products: dict[str, int] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("Cada producto debe ser un objeto.")
            sku = _required_string(item, "sku")
            quantity = _positive_int(item, "quantity")
            if sku in products:
                raise ValueError(f"El producto {sku} está repetido.")
            products[sku] = quantity
        with Sistema(self.database_path) as system:
            total = 0
            for sku, quantity in products.items():
                product = system.db.connection.execute(
                    "SELECT price_cents FROM products WHERE sku = ?", (sku,)
                ).fetchone()
                if product is None:
                    raise ValueError(f"No existe el producto {sku}.")
                total += int(product["price_cents"]) * quantity
            sale = system.ventas.registrar_venta(
                store_id, products, {method: total}
            )
        self._send_json(
            {
                "id": sale.id,
                "receipt": sale.comprobante,
                "total_cents": sale.total_centavos,
            },
            201,
        )

    def _adjust_stock(self, payload: dict[str, Any]) -> None:
        profile = self._require_profile(("CD",))
        center_id = _positive_int(payload, "center_id")
        if center_id != int(profile["id"]):
            raise ApiAuthorizationError(
                403, "Solo podés ajustar el inventario del centro seleccionado."
            )
        sku = _required_string(payload, "sku")
        reference = _required_string(payload, "reference")
        delta = payload.get("quantity_delta")
        if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
            raise ValueError("La variación debe ser un entero distinto de cero.")
        with Sistema(self.database_path) as system:
            system.inventario.ajustar_stock_centro(
                center_id, sku, delta, reference
            )
        self._send_json({"message": "Ajuste de stock registrado."}, 201)

    def _create_replenishment_request(self, payload: dict[str, Any]) -> None:
        profile = self._require_profile(("SUCURSAL",))
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("El pedido debe incluir al menos un producto.")
        demand: dict[str, int] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("Cada producto debe ser un objeto.")
            sku = _required_string(item, "sku")
            quantity = _positive_int(item, "quantity")
            if sku in demand:
                raise ValueError(f"El producto {sku} está repetido.")
            demand[sku] = quantity
        with Sistema(self.database_path) as system:
            connection = system.db.connection
            with system.db.transaccion():
                pending = connection.execute(
                    """SELECT 1 FROM replenishment_requests
                       WHERE requester_id = ? AND status = 'Pendiente'""",
                    (profile["id"],),
                ).fetchone()
                if pending is not None:
                    raise ValueError(
                        "Ya hay un pedido pendiente para esta sucursal. "
                        "Esperá a que el centro lo atienda."
                    )
                product_ids: dict[str, int] = {}
                for sku in demand:
                    product = connection.execute(
                        "SELECT id FROM products WHERE sku = ?", (sku,)
                    ).fetchone()
                    if product is None:
                        raise ValueError(f"No existe el producto {sku}.")
                    product_ids[sku] = int(product["id"])
                cursor = connection.execute(
                    """INSERT INTO replenishment_requests(requester_id, created_at)
                       VALUES (?, ?)""",
                    (profile["id"], date.today().isoformat()),
                )
                request_id = int(cursor.lastrowid)
                connection.executemany(
                    """INSERT INTO replenishment_request_lines
                       (request_id, product_id, requested) VALUES (?, ?, ?)""",
                    (
                        (request_id, product_ids[sku], quantity)
                        for sku, quantity in demand.items()
                    ),
                )
        self._send_json({"id": request_id, "status": "Pendiente"}, 201)

    def _create_replenishment(self, payload: dict[str, Any]) -> None:
        profile = self._require_profile(("CD",))
        center_id = _positive_int(payload, "center_id")
        if center_id != int(profile["id"]):
            raise ApiAuthorizationError(
                403, "El origen debe ser el centro seleccionado."
            )
        raw_orders = payload.get("orders")
        if not isinstance(raw_orders, list) or not raw_orders:
            raise ValueError("La reposición debe incluir al menos una sucursal.")
        orders: list[dict[str, Any]] = []
        seen_stores: set[int] = set()
        seen_requests: set[int] = set()
        seen_notes: set[str] = set()
        for raw_order in raw_orders:
            if not isinstance(raw_order, dict):
                raise ValueError("Cada orden debe ser un objeto.")
            request_id = _positive_int(raw_order, "request_id")
            remito = _required_string(raw_order, "remito")
            seal = _required_string(raw_order, "seal")
            raw_items = raw_order.get("items")
            if not isinstance(raw_items, list) or not raw_items:
                raise ValueError(
                    "Cada pedido debe indicar al menos un producto y su cantidad."
                )
            fulfillment: dict[str, int] = {}
            for item in raw_items:
                if not isinstance(item, dict):
                    raise ValueError("Cada producto preparado debe ser un objeto.")
                sku = _required_string(item, "sku")
                quantity = _positive_int(item, "quantity")
                if sku in fulfillment:
                    raise ValueError(f"El producto {sku} está repetido.")
                fulfillment[sku] = quantity
            if request_id in seen_requests:
                raise ValueError("El mismo pedido no puede incluirse dos veces.")
            if remito in seen_notes:
                raise ValueError("Cada bulto debe tener un remito diferente.")
            seen_requests.add(request_id)
            seen_notes.add(remito)
            orders.append(
                {
                    "request_id": request_id,
                    "remito": remito,
                    "seal": seal,
                    "demand": fulfillment,
                }
            )
        with Sistema(self.database_path) as system:
            connection = system.db.connection
            with system.db.transaccion():
                center = connection.execute(
                    "SELECT type FROM establishments WHERE id = ?", (center_id,)
                ).fetchone()
                if center is None or center["type"] != "CD":
                    raise ValueError("El origen debe ser un centro de distribución.")
                existing_notes = {
                    str(row["dispatch_note"])
                    for row in connection.execute(
                        "SELECT dispatch_note FROM packages"
                    ).fetchall()
                }
                if existing_notes.intersection(seen_notes):
                    raise ValueError("Uno de los remitos ya está en uso.")
                needs: dict[int, dict[str, int]] = {}
                for order in orders:
                    request_id = int(order["request_id"])
                    request_row = connection.execute(
                        """SELECT requester_id, status FROM replenishment_requests
                           WHERE id = ?""",
                        (request_id,),
                    ).fetchone()
                    if request_row is None or request_row["status"] != "Pendiente":
                        raise ValueError(f"El pedido {request_id} ya no está pendiente.")
                    store_id = int(request_row["requester_id"])
                    if store_id in seen_stores:
                        raise ValueError(
                            "Solo se puede atender un pedido por sucursal en cada lote."
                        )
                    seen_stores.add(store_id)
                    demand_rows = connection.execute(
                        """SELECT p.id AS product_id, p.sku, l.requested, l.fulfilled
                           FROM replenishment_request_lines l
                           JOIN products p ON p.id = l.product_id
                           WHERE l.request_id = ?""",
                        (request_id,),
                    ).fetchall()
                    if not demand_rows:
                        raise ValueError(f"El pedido {request_id} no tiene productos.")
                    remaining = {
                        str(row["sku"]): int(row["requested"])
                        - int(row["fulfilled"])
                        for row in demand_rows
                    }
                    lines_by_sku = {
                        str(row["sku"]): row for row in demand_rows
                    }
                    for sku, quantity in order["demand"].items():
                        if sku not in remaining:
                            raise ValueError(
                                f"El producto {sku} no pertenece al pedido {request_id}."
                            )
                        if quantity > remaining[sku]:
                            raise ValueError(
                                f"La cantidad de {sku} supera las "
                                f"{remaining[sku]} unidades pendientes."
                            )
                    order["store_id"] = store_id
                    order["lines_by_sku"] = lines_by_sku
                    needs[store_id] = order["demand"]
                batch_id, order_ids = system.reposicion.consolidar_necesidades(
                    center_id, needs
                )
                packages = []
                for order in orders:
                    store_id = int(order["store_id"])
                    connection.execute(
                        """UPDATE replenishment_orders SET request_id = ?
                           WHERE id = ?""",
                        (order["request_id"], order_ids[store_id]),
                    )
                    package_id = system.logistica.preparar_bulto(
                        order_ids[store_id],
                        str(order["remito"]),
                        str(order["seal"]),
                        order["demand"],
                    )
                    for sku, quantity in order["demand"].items():
                        updated_line = connection.execute(
                            """UPDATE replenishment_request_lines
                               SET fulfilled = fulfilled + ?
                               WHERE request_id = ? AND product_id = ?
                                 AND requested - fulfilled >= ?""",
                            (
                                quantity,
                                order["request_id"],
                                order["lines_by_sku"][sku]["product_id"],
                                quantity,
                            ),
                        )
                        if updated_line.rowcount != 1:
                            raise ValueError(
                                f"El producto {sku} ya no tiene esa cantidad pendiente."
                            )
                    remaining_items = [
                        dict(row)
                        for row in connection.execute(
                            """SELECT p.sku, p.name,
                                      l.requested - l.fulfilled AS quantity
                               FROM replenishment_request_lines l
                               JOIN products p ON p.id = l.product_id
                               WHERE l.request_id = ? AND l.fulfilled < l.requested
                               ORDER BY p.name""",
                            (order["request_id"],),
                        ).fetchall()
                    ]
                    request_status = "Atendida" if not remaining_items else "Pendiente"
                    updated_request = connection.execute(
                        """UPDATE replenishment_requests
                           SET status = ?, order_id = ?
                           WHERE id = ? AND status = 'Pendiente'""",
                        (
                            request_status,
                            order_ids[store_id],
                            order["request_id"],
                        ),
                    )
                    if updated_request.rowcount != 1:
                        raise ValueError(
                            f"El pedido {order['request_id']} ya fue atendido."
                        )
                    packages.append(
                        {
                            "store_id": store_id,
                            "request_id": order["request_id"],
                            "order_id": order_ids[store_id],
                            "package_id": package_id,
                            "dispatch_note": order["remito"],
                            "status": "Preparado",
                            "remaining_items": remaining_items,
                        }
                    )
        self._send_json(
            {"batch_id": batch_id, "packages": packages},
            201,
        )

    def _advance_package(self, payload: dict[str, Any]) -> None:
        profile = self._require_profile(("CD", "SUCURSAL"))
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["api", "packages"]:
            self._api_error("La ruta del bulto no es válida.", 404)
            return
        try:
            package_id = int(parts[2])
        except ValueError as error:
            raise ValueError("El identificador del bulto no es válido.") from error
        if package_id <= 0 or parts[3] != "action":
            self._api_error("La ruta del bulto no es válida.", 404)
            return
        action = _required_string(payload, "action")
        with Sistema(self.database_path) as system:
            package_scope = system.db.connection.execute(
                """SELECT o.source_id, o.destination_id
                   FROM packages p
                   JOIN replenishment_orders o ON o.id = p.order_id
                   WHERE p.id = ?""",
                (package_id,),
            ).fetchone()
            if package_scope is None:
                raise ValueError(f"No existe el bulto {package_id}.")
            if profile["type"] == "CD":
                allowed_actions = {"precintar", "despachar", "cancelar"}
                belongs_to_profile = int(package_scope["source_id"]) == int(profile["id"])
            else:
                allowed_actions = {"recibir", "verificar"}
                belongs_to_profile = int(package_scope["destination_id"]) == int(profile["id"])
            if action not in allowed_actions or not belongs_to_profile:
                raise ApiAuthorizationError(
                    403, "Ese perfil no puede ejecutar esta acción sobre el bulto."
                )
            actions = {
                "precintar": system.logistica.precintar,
                "despachar": system.logistica.despachar,
                "recibir": system.logistica.recibir,
                "cancelar": system.logistica.cancelar_preparacion,
            }
            if action in actions:
                actions[action](package_id)
            elif action == "verificar":
                intacto = payload.get("intacto")
                if not isinstance(intacto, bool):
                    raise ValueError("La verificación debe indicar si el sello está intacto.")
                coincide_remito = payload.get("coincide_remito")
                if not isinstance(coincide_remito, bool):
                    raise ValueError(
                        "La verificación debe indicar si el contenido coincide con el remito."
                    )
                state = system.logistica.verificar_recepcion(
                    package_id, intacto, coincide_remito
                )
                message = (
                    "Bulto verificado y disponible para la sucursal."
                    if state.value == "Verificado"
                    else "Bulto rechazado; no se agregó al stock vendible."
                )
            else:
                raise ValueError("La acción logística no es válida.")
        if action != "verificar":
            message = "Estado logístico actualizado."
        self._send_json({"message": message})


def create_server(
    database_path: str | Path = "data/sistema.db",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> ThreadingHTTPServer:
    handler = type(
        "ConfiguredFrontendHandler",
        (FrontendHandler,),
        {"database_path": str(database_path)},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    database_path: str | Path = "data/sistema.db",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    server = create_server(database_path, host, port)
    print(f"Interfaz disponible en http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()
