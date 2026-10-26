import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from exceptions import BusinessRuleError, NotFoundError
from repositories.sqlite_repository import SQLiteRepository
from services.payment_strategies import PAYMENT_STRATEGIES
from services.sale_states import ConfirmedSaleState, sale_state_from_name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cents(value: int | float | str | Decimal) -> int:
    return int((Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) * 100)


def _require(row, message: str):
    if row is None:
        raise NotFoundError(message)
    return row


class RetailService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def _create_serials(self, location_id: int, product_id: int, quantity: int, lot_id: int | None = None) -> None:
        for _ in range(quantity):
            serial = f"SN-{uuid4().hex.upper()}"
            while self.repository.one("SELECT id FROM serial_numbers WHERE serial=?", (serial,)):
                serial = f"SN-{uuid4().hex.upper()}"
            self.repository.execute(
                """INSERT INTO serial_numbers(location_id,product_id,serial,status,lot_id)
                   VALUES(?,?,?,'available',?)""",
                (location_id, product_id, serial, lot_id),
            )

    def _sync_available_serials(
        self, location_id: int, product_id: int, quantity: int, lot_id: int | None = None
    ) -> None:
        available = self.repository.all(
            """SELECT id FROM serial_numbers WHERE location_id=? AND product_id=?
               AND lot_id IS ? AND status='available' ORDER BY id""",
            (location_id, product_id, lot_id),
        )
        if len(available) > quantity:
            retired = [serial["id"] for serial in available[quantity:]]
            placeholders = ",".join("?" for _ in retired)
            self.repository.execute(
                f"UPDATE serial_numbers SET status='retired' WHERE id IN ({placeholders})", tuple(retired)
            )
        elif len(available) < quantity:
            self._create_serials(location_id, product_id, quantity - len(available), lot_id)

    def _trace_allocations(self, product, location_id: int, quantity: int) -> list[dict]:
        if product["tracking_mode"] == "lot":
            lots = self.repository.all(
                """SELECT id,quantity FROM lots WHERE location_id=? AND product_id=? AND quantity>0
                   AND (expiry_date IS NULL OR expiry_date>=?)
                   ORDER BY expiry_date IS NULL,expiry_date,id""",
                (location_id, product["id"], date.today().isoformat()),
            )
            if sum(lot["quantity"] for lot in lots) < quantity:
                raise BusinessRuleError(f"Lotes vigentes insuficientes para {product['sku']}.")
            remaining = quantity
            allocations = []
            for lot in lots:
                allocated = min(remaining, lot["quantity"])
                if allocated:
                    serials = self.repository.all(
                        """SELECT id FROM serial_numbers WHERE location_id=? AND product_id=?
                           AND lot_id=? AND status='available' ORDER BY id LIMIT ?""",
                        (location_id, product["id"], lot["id"], allocated),
                    )
                    if len(serials) != allocated:
                        raise BusinessRuleError(f"Las series disponibles no coinciden con el lote de {product['sku']}.")
                    allocations.extend(
                        {"lot_id": lot["id"], "serial_id": serial["id"], "quantity": 1}
                        for serial in serials
                    )
                    remaining -= allocated
                if remaining == 0:
                    break
            return allocations
        serials = self.repository.all(
            """SELECT id FROM serial_numbers WHERE location_id=? AND product_id=? AND status='available'
               AND lot_id IS NULL ORDER BY id LIMIT ?""",
            (location_id, product["id"], quantity),
        )
        if len(serials) < quantity:
            raise BusinessRuleError(f"Números de serie disponibles insuficientes para {product['sku']}.")
        return [{"serial_id": serial["id"], "quantity": 1} for serial in serials]

    def create_location(self, code: str, name: str, location_type: str) -> int:
        if location_type not in ("CD", "LOCAL"):
            raise BusinessRuleError("El tipo de ubicación debe ser CD o LOCAL.")
        with self.repository.transaction():
            if location_type == "CD" and self.repository.one("SELECT id FROM locations WHERE type='CD'"):
                raise BusinessRuleError("Ya existe un Centro de Distribución central.")
            cursor = self.repository.execute(
                "INSERT INTO locations(code,name,type) VALUES(?,?,?)",
                (code.strip(), name.strip(), location_type),
            )
            return cursor.lastrowid

    def update_location(self, location_id: int, code: str, name: str, location_type: str) -> None:
        if location_type not in ("CD", "LOCAL") or not code.strip() or not name.strip():
            raise BusinessRuleError("Completa código, nombre y tipo de ubicación válidos.")
        with self.repository.transaction():
            current = _require(
                self.repository.one("SELECT * FROM locations WHERE id=?", (location_id,)),
                "Ubicación inexistente.",
            )
            if current["type"] == "CD" and location_type != "CD":
                raise BusinessRuleError("El Centro de Distribución central debe conservarse como origen logístico.")
            if location_type == "CD":
                other_cd = self.repository.one(
                    "SELECT id FROM locations WHERE type='CD' AND id<>?", (location_id,)
                )
                if other_cd:
                    raise BusinessRuleError("Ya existe un Centro de Distribución central.")
            references = self.repository.one(
                """SELECT EXISTS(SELECT 1 FROM stock WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM inventory_movements WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM lots WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM serial_numbers WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM package_unit_serials WHERE origin_location_id=?) OR
                          EXISTS(SELECT 1 FROM sales WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM inventory_counts WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM replenishment_orders WHERE origin_id=? OR destination_id=?) OR
                          EXISTS(SELECT 1 FROM purchase_orders WHERE destination_id=?) AS used""",
                (location_id,) * 10,
            )["used"]
            if code.strip() != current["code"] and references:
                raise BusinessRuleError("No se puede cambiar el código de una ubicación con historial u operaciones.")
            has_local_accounts = self.repository.one(
                "SELECT EXISTS(SELECT 1 FROM app_users WHERE location_id=?) AS used",
                (location_id,),
            )["used"]
            if location_type != current["type"] and (references or has_local_accounts):
                raise BusinessRuleError("No se puede cambiar el tipo de una ubicación con operaciones asociadas.")
            self.repository.execute(
                "UPDATE locations SET code=?,name=?,type=? WHERE id=?",
                (code.strip(), name.strip(), location_type, location_id),
            )

    def delete_location(self, location_id: int) -> None:
        with self.repository.transaction():
            _require(self.repository.one("SELECT id FROM locations WHERE id=?", (location_id,)), "Ubicación inexistente.")
            used = self.repository.one(
                """SELECT EXISTS(SELECT 1 FROM stock WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM inventory_movements WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM lots WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM serial_numbers WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM package_unit_serials WHERE origin_location_id=?) OR
                          EXISTS(SELECT 1 FROM sales WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM app_users WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM replenishment_orders WHERE origin_id=? OR destination_id=?) OR
                          EXISTS(SELECT 1 FROM purchase_orders WHERE destination_id=?) OR
                          EXISTS(SELECT 1 FROM inventory_counts WHERE location_id=?) AS used""",
                (location_id,) * 11,
            )["used"]
            if used:
                raise BusinessRuleError("No se puede eliminar una ubicación con stock, historial u operaciones asociadas.")
            location = self.repository.one("SELECT type FROM locations WHERE id=?", (location_id,))
            if location["type"] == "CD":
                raise BusinessRuleError("No se puede eliminar el Centro de Distribución central.")
            self.repository.execute("DELETE FROM locations WHERE id=?", (location_id,))

    def create_supplier(self, tax_id: str, name: str, phone: str = "", email: str = "") -> int:
        with self.repository.transaction():
            cursor = self.repository.execute(
                "INSERT INTO suppliers(tax_id,name,phone,email) VALUES(?,?,?,?)",
                (tax_id.strip(), name.strip(), phone.strip(), email.strip()),
            )
            return cursor.lastrowid

    def update_supplier(self, supplier_id: int, tax_id: str, name: str, phone: str = "", email: str = "") -> None:
        if not tax_id.strip() or not name.strip():
            raise BusinessRuleError("El identificador fiscal y la razón social son obligatorios.")
        with self.repository.transaction():
            _require(self.repository.one("SELECT id FROM suppliers WHERE id=?", (supplier_id,)), "Proveedor inexistente.")
            self.repository.execute(
                "UPDATE suppliers SET tax_id=?,name=?,phone=?,email=? WHERE id=?",
                (tax_id.strip(), name.strip(), phone.strip(), email.strip(), supplier_id),
            )

    def delete_supplier(self, supplier_id: int) -> None:
        with self.repository.transaction():
            _require(self.repository.one("SELECT id FROM suppliers WHERE id=?", (supplier_id,)), "Proveedor inexistente.")
            used = self.repository.one(
                "SELECT EXISTS(SELECT 1 FROM products WHERE supplier_id=?) OR EXISTS(SELECT 1 FROM purchase_orders WHERE supplier_id=?) AS used",
                (supplier_id, supplier_id),
            )["used"]
            if used:
                raise BusinessRuleError("No se puede eliminar un proveedor asociado a productos u órdenes de compra.")
            self.repository.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))

    def create_category(self, name: str) -> int:
        normalized = name.strip()
        if not normalized:
            raise BusinessRuleError("El nombre de la categoría es obligatorio.")
        with self.repository.transaction():
            cursor = self.repository.execute("INSERT INTO categories(name) VALUES(?)", (normalized,))
            return cursor.lastrowid

    def rename_category(self, category_id: int, name: str) -> None:
        normalized = name.strip()
        if not normalized:
            raise BusinessRuleError("El nombre de la categoría es obligatorio.")
        with self.repository.transaction():
            category = _require(
                self.repository.one("SELECT name FROM categories WHERE id=?", (category_id,)),
                "Categoría inexistente.",
            )
            self.repository.execute("UPDATE categories SET name=? WHERE id=?", (normalized, category_id))
            self.repository.execute("UPDATE products SET category=? WHERE category_id=?", (normalized, category_id))
            self.repository.execute(
                "UPDATE promotions SET category=? WHERE category=?",
                (normalized, category["name"]),
            )

    def delete_category(self, category_id: int) -> None:
        with self.repository.transaction():
            _require(
                self.repository.one("SELECT id FROM categories WHERE id=?", (category_id,)),
                "Categoría inexistente.",
            )
            used = self.repository.one(
                """SELECT EXISTS(SELECT 1 FROM products WHERE category_id=?) OR
                          EXISTS(SELECT 1 FROM promotions
                                 WHERE category=(SELECT name FROM categories WHERE id=?)) AS used""",
                (category_id, category_id),
            )["used"]
            if used:
                raise BusinessRuleError("No se puede borrar una categoría que usan productos o promociones.")
            self.repository.execute("DELETE FROM categories WHERE id=?", (category_id,))

    def create_product(
        self,
        sku: str,
        name: str,
        category: str,
        cost: int | float | str | Decimal,
        price: int | float | str | Decimal,
        minimum_stock: int,
        supplier_id: int | None = None,
        tracking_mode: str = "none",
    ) -> int:
        if minimum_stock < 0 or _cents(cost) < 0 or _cents(price) < 0:
            raise BusinessRuleError("Costo, precio y stock mínimo no pueden ser negativos.")
        if tracking_mode not in ("none", "lot", "serial"):
            raise BusinessRuleError("El seguimiento debe ser ninguno, por lote o por número de serie.")
        with self.repository.transaction():
            normalized_category = category.strip()
            if not normalized_category:
                raise BusinessRuleError("Selecciona una categoría.")
            self.repository.execute(
                "INSERT OR IGNORE INTO categories(name) VALUES(?)", (normalized_category,)
            )
            category_record = self.repository.one(
                "SELECT id,name FROM categories WHERE name=? COLLATE NOCASE", (normalized_category,)
            )
            cursor = self.repository.execute(
                """INSERT INTO products(sku,name,category,cost_cents,price_cents,minimum_stock,
                                        supplier_id,tracking_mode,category_id)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    sku.strip(), name.strip(), category_record["name"], _cents(cost), _cents(price),
                    minimum_stock, supplier_id, tracking_mode, category_record["id"],
                ),
            )
            return cursor.lastrowid

    def update_product(
        self,
        product_id: int,
        sku: str,
        name: str,
        category: str,
        cost: int | float | str | Decimal,
        price: int | float | str | Decimal,
        minimum_stock: int,
        supplier_id: int | None = None,
        tracking_mode: str = "none",
    ) -> None:
        normalized_category = category.strip()
        if not sku.strip() or not name.strip() or not normalized_category:
            raise BusinessRuleError("SKU, nombre y categoría son obligatorios.")
        if minimum_stock < 0 or _cents(cost) < 0 or _cents(price) < 0:
            raise BusinessRuleError("Costo, precio y stock mínimo no pueden ser negativos.")
        if tracking_mode not in ("none", "lot", "serial"):
            raise BusinessRuleError("El seguimiento debe ser ninguno, por lote o por número de serie.")
        with self.repository.transaction():
            current = _require(
                self.repository.one("SELECT sku,tracking_mode FROM products WHERE id=?", (product_id,)),
                "Producto inexistente.",
            )
            if current["sku"] != sku.strip():
                used = self.repository.one(
                    """SELECT EXISTS(SELECT 1 FROM sale_lines sl JOIN products p ON p.id=sl.product_id
                                     WHERE p.id=?) OR
                              EXISTS(SELECT 1 FROM purchase_order_lines pl JOIN products p ON p.id=pl.product_id
                                     WHERE p.id=?) OR
                              EXISTS(SELECT 1 FROM inventory_movements WHERE product_id=?) AS used""",
                    (product_id, product_id, product_id),
                )["used"]
                if used:
                    raise BusinessRuleError("No se puede cambiar el SKU de un producto con historial de ventas o compras.")
            if current["tracking_mode"] != tracking_mode:
                has_history = self.repository.one(
                    """SELECT EXISTS(SELECT 1 FROM stock WHERE product_id=? AND quantity>0) OR
                              EXISTS(SELECT 1 FROM lots WHERE product_id=?) OR
                              EXISTS(SELECT 1 FROM serial_numbers WHERE product_id=?) OR
                              EXISTS(SELECT 1 FROM inventory_movements WHERE product_id=?) OR
                              EXISTS(SELECT 1 FROM sale_lines WHERE product_id=?) OR
                              EXISTS(SELECT 1 FROM package_lines WHERE product_id=?) AS used""",
                    (product_id,) * 6,
                )["used"]
                if has_history:
                    raise BusinessRuleError("No se puede cambiar el seguimiento de un producto con existencias o historial.")
            self.repository.execute(
                "INSERT OR IGNORE INTO categories(name) VALUES(?)", (normalized_category,)
            )
            category_record = self.repository.one(
                "SELECT id,name FROM categories WHERE name=? COLLATE NOCASE", (normalized_category,)
            )
            self.repository.execute(
                """UPDATE products SET sku=?,name=?,category=?,category_id=?,cost_cents=?,price_cents=?,
                       minimum_stock=?,supplier_id=?,tracking_mode=? WHERE id=?""",
                (
                    sku.strip(), name.strip(), category_record["name"], category_record["id"],
                    _cents(cost), _cents(price), minimum_stock, supplier_id, tracking_mode, product_id,
                ),
            )

    def delete_product(self, product_id: int) -> None:
        with self.repository.transaction():
            _require(self.repository.one("SELECT id FROM products WHERE id=?", (product_id,)), "Producto inexistente.")
            references = self.repository.one(
                """SELECT EXISTS(SELECT 1 FROM stock WHERE product_id=? AND quantity>0) OR
                          EXISTS(SELECT 1 FROM inventory_movements WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM purchase_order_lines WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM replenishment_lines WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM package_lines WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM sale_lines WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM lots WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM serial_numbers WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM price_list_items WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM promotions WHERE product_id=?) OR
                          EXISTS(SELECT 1 FROM inventory_count_lines WHERE product_id=?) AS used""",
                (product_id,) * 11,
            )["used"]
            if references:
                raise BusinessRuleError("No se puede eliminar un producto con stock, precios u operaciones históricas.")
            self.repository.execute("DELETE FROM stock WHERE product_id=? AND quantity=0", (product_id,))
            self.repository.execute("DELETE FROM products WHERE id=?", (product_id,))

    def adjust_stock(
        self,
        location_code: str,
        sku: str,
        delta: int,
        reason: str = "ajuste",
        lot_code: str = "",
        expiry_date: str | None = None,
        serials: list[str] | None = None,
    ) -> None:
        if delta == 0:
            raise BusinessRuleError("El movimiento de stock no puede ser cero.")
        if expiry_date:
            try:
                date.fromisoformat(expiry_date)
            except ValueError as error:
                raise BusinessRuleError("La fecha de vencimiento debe tener formato AAAA-MM-DD.") from error
        with self.repository.transaction():
            location = _require(
                self.repository.one("SELECT id,type FROM locations WHERE code=?", (location_code,)),
                "Ubicación inexistente.",
            )
            product = _require(self.repository.one("SELECT id,tracking_mode FROM products WHERE sku=?", (sku,)), "Producto inexistente.")
            if location["type"] == "LOCAL" and delta > 0:
                raise BusinessRuleError("El stock de un local solo aumenta al verificar una recepción o conciliar un conteo físico.")
            serials = serials or []
            if delta > 0 and product["tracking_mode"] == "lot" and not lot_code.strip():
                raise BusinessRuleError("El producto requiere ingresar el código de lote.")
            if serials:
                raise BusinessRuleError("Los números de serie se generan automáticamente al ingresar stock.")
            if delta > 0 and product["tracking_mode"] == "lot" and expiry_date and expiry_date < date.today().isoformat():
                raise BusinessRuleError("No se puede ingresar un lote vencido.")
            if delta > 0 and product["tracking_mode"] == "serial" and (lot_code or expiry_date):
                raise BusinessRuleError("Los productos por serie no aceptan datos de lote.")
            if product["tracking_mode"] == "none" and (lot_code or expiry_date or serials):
                raise BusinessRuleError("Este producto no tiene seguimiento por lote ni número de serie.")
            if delta < 0 and product["tracking_mode"] == "lot":
                raise BusinessRuleError("Para descontar productos trazables, utiliza el inventario físico.")
            current = self.repository.one(
                "SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (location["id"], product["id"])
            )
            quantity = (current["quantity"] if current else 0) + delta
            if quantity < 0:
                raise BusinessRuleError("El ajuste dejaría el stock por debajo de cero.")
            if delta < 0:
                available = self.repository.all(
                    """SELECT id FROM serial_numbers WHERE location_id=? AND product_id=?
                       AND status='available' AND lot_id IS NULL ORDER BY id LIMIT ?""",
                    (location["id"], product["id"], -delta),
                )
                if len(available) != -delta:
                    raise BusinessRuleError("No hay números de serie suficientes para descontar esas unidades.")
                self.repository.execute(
                    """UPDATE serial_numbers SET status='retired' WHERE id IN
                       (SELECT id FROM serial_numbers WHERE location_id=? AND product_id=?
                        AND status='available' AND lot_id IS NULL ORDER BY id LIMIT ?)""",
                    (location["id"], product["id"], -delta),
                )
            self.repository.execute(
                """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                   ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=excluded.quantity""",
                (location["id"], product["id"], quantity),
            )
            lot_id = None
            if delta > 0 and product["tracking_mode"] == "lot":
                self.repository.execute(
                    """INSERT INTO lots(location_id,product_id,code,expiry_date,quantity) VALUES(?,?,?,?,?)
                       ON CONFLICT(location_id,product_id,code) DO UPDATE SET
                       quantity=quantity+excluded.quantity,expiry_date=COALESCE(excluded.expiry_date,lots.expiry_date)""",
                    (location["id"], product["id"], lot_code.strip(), expiry_date or None, delta),
                )
                lot_id = self.repository.one(
                    "SELECT id FROM lots WHERE location_id=? AND product_id=? AND code=?",
                    (location["id"], product["id"], lot_code.strip()),
                )["id"]
            if delta > 0:
                self._create_serials(location["id"], product["id"], delta, lot_id)
            self._record_inventory_movement(location["id"], product["id"], delta, reason)

    def suggested_purchases(self) -> list[dict]:
        rows = self.repository.all(
            """SELECT p.id AS product_id,p.sku,p.name,p.minimum_stock,p.supplier_id,s.name AS supplier_name,
                      l.id AS location_id,l.code AS location_code,COALESCE(st.quantity,0) AS quantity
               FROM products p JOIN suppliers s ON s.id=p.supplier_id
               JOIN locations l ON l.type='LOCAL'
               LEFT JOIN stock st ON st.product_id=p.id AND st.location_id=l.id
               ORDER BY s.name,p.sku,l.code"""
        )
        cd = self.repository.one("SELECT id FROM locations WHERE type='CD' ORDER BY id LIMIT 1")
        by_product: dict[int, dict] = {}
        for row in rows:
            entry = by_product.setdefault(row["product_id"], {
                "product_id": row["product_id"], "sku": row["sku"], "name": row["name"],
                "supplier_id": row["supplier_id"], "supplier_name": row["supplier_name"],
                "suggested_quantity": 0, "locations": [],
            })
            shortage = max(0, row["minimum_stock"] - row["quantity"])
            if shortage:
                entry["suggested_quantity"] += shortage
                entry["locations"].append({"code": row["location_code"], "shortage": shortage})
        if cd:
            for product_id, entry in by_product.items():
                available = self.repository.one(
                    "SELECT quantity FROM stock WHERE product_id=? AND location_id=?",
                    (product_id, cd["id"]),
                )
                on_order = self.repository.one(
                    """SELECT COALESCE(SUM(pol.quantity-pol.received_quantity),0) AS quantity
                       FROM purchase_order_lines pol JOIN purchase_orders po ON po.id=pol.order_id
                       WHERE pol.product_id=? AND po.status<>'Recibida'""",
                    (product_id,),
                )
                entry["suggested_quantity"] = max(
                    0,
                    entry["suggested_quantity"]
                    - (available["quantity"] if available else 0)
                    - on_order["quantity"],
                )
        return [item for item in by_product.values() if item["suggested_quantity"]]

    def create_purchase_order(self, supplier_id: int, quantities: dict[str, int]) -> int:
        if not quantities or any(quantity <= 0 for quantity in quantities.values()):
            raise BusinessRuleError("La orden de compra debe incluir cantidades positivas.")
        with self.repository.transaction():
            supplier = _require(self.repository.one("SELECT id FROM suppliers WHERE id=?", (supplier_id,)), "Proveedor inexistente.")
            destination = _require(self.repository.one("SELECT id FROM locations WHERE type='CD' ORDER BY id LIMIT 1"), "Debe existir un Centro de Distribución.")
            code = f"OC-{uuid4().hex[:10].upper()}"
            order = self.repository.execute(
                "INSERT INTO purchase_orders(code,supplier_id,destination_id,status,created_at) VALUES(?,?,?,'Pendiente',?)",
                (code, supplier["id"], destination["id"], _now()),
            )
            for sku, quantity in quantities.items():
                product = _require(self.repository.one("SELECT id,supplier_id FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                if product["supplier_id"] != supplier_id:
                    raise BusinessRuleError(f"El producto {sku} no pertenece al proveedor seleccionado.")
                self.repository.execute(
                    "INSERT INTO purchase_order_lines(order_id,product_id,quantity) VALUES(?,?,?)",
                    (order.lastrowid, product["id"], quantity),
                )
            return order.lastrowid

    def receive_purchase_order(
        self,
        order_id: int,
        quantities: dict[str, int],
        trace: dict[str, dict] | None = None,
    ) -> None:
        if not quantities or any(quantity <= 0 for quantity in quantities.values()):
            raise BusinessRuleError("La recepción debe incluir cantidades positivas.")
        with self.repository.transaction():
            order = _require(self.repository.one("SELECT * FROM purchase_orders WHERE id=?", (order_id,)), "Orden de compra inexistente.")
            if order["status"] == "Recibida":
                raise BusinessRuleError("La orden de compra ya fue recibida por completo.")
            for sku, quantity in quantities.items():
                product = _require(self.repository.one("SELECT id,tracking_mode FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                line = _require(
                    self.repository.one("SELECT quantity,received_quantity FROM purchase_order_lines WHERE order_id=? AND product_id=?", (order_id, product["id"])),
                    f"El producto {sku} no pertenece a la orden.",
                )
                if line["received_quantity"] + quantity > line["quantity"]:
                    raise BusinessRuleError(f"La recepción excede lo pedido para {sku}.")
                trace_item = (trace or {}).get(sku, {})
                if trace_item.get("serials"):
                    raise BusinessRuleError("Los números de serie se generan automáticamente al recibir la compra.")
                lot_id = None
                if product["tracking_mode"] == "lot":
                    lot_code = str(trace_item.get("lot_code", "")).strip()
                    expiry_date = trace_item.get("expiry_date") or None
                    if not lot_code:
                        raise BusinessRuleError(f"Indica el código de lote para {sku}.")
                    if expiry_date:
                        try:
                            date.fromisoformat(expiry_date)
                        except ValueError as error:
                            raise BusinessRuleError("La fecha de vencimiento debe tener formato AAAA-MM-DD.") from error
                        if expiry_date < date.today().isoformat():
                            raise BusinessRuleError(f"El lote de {sku} está vencido.")
                    self.repository.execute(
                        """INSERT INTO lots(location_id,product_id,code,expiry_date,quantity) VALUES(?,?,?,?,?)
                           ON CONFLICT(location_id,product_id,code) DO UPDATE SET
                           quantity=quantity+excluded.quantity,expiry_date=COALESCE(excluded.expiry_date,lots.expiry_date)""",
                        (order["destination_id"], product["id"], lot_code, expiry_date, quantity),
                    )
                    lot_id = self.repository.one(
                        "SELECT id FROM lots WHERE location_id=? AND product_id=? AND code=?",
                        (order["destination_id"], product["id"], lot_code),
                    )["id"]
                elif product["tracking_mode"] == "serial":
                    if trace_item.get("lot_code") or trace_item.get("expiry_date"):
                        raise BusinessRuleError(f"{sku} se controla por número de serie, no por lote.")
                elif trace_item:
                    raise BusinessRuleError(f"{sku} no requiere datos de lote o serie.")
                self._create_serials(order["destination_id"], product["id"], quantity, lot_id)
                self.repository.execute(
                    "UPDATE purchase_order_lines SET received_quantity=received_quantity+? WHERE order_id=? AND product_id=?",
                    (quantity, order_id, product["id"]),
                )
                self.repository.execute(
                    """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                       ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                    (order["destination_id"], product["id"], quantity),
                )
                self._record_inventory_movement(
                    order["destination_id"], product["id"], quantity, "compra recibida", order["code"]
                )
            remaining = self.repository.one(
                "SELECT COUNT(*) AS count FROM purchase_order_lines WHERE order_id=? AND received_quantity<quantity",
                (order_id,),
            )["count"]
            self.repository.execute(
                "UPDATE purchase_orders SET status=? WHERE id=?",
                ("Parcial" if remaining else "Recibida", order_id),
            )

    def create_price_list(self, name: str, prices: dict[str, int | float | str | Decimal]) -> int:
        if not name.strip() or not prices:
            raise BusinessRuleError("La lista debe tener nombre y precios.")
        with self.repository.transaction():
            cursor = self.repository.execute(
                "INSERT INTO price_lists(name,created_at) VALUES(?,?)", (name.strip(), _now())
            )
            for sku, price in prices.items():
                product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                amount = _cents(price)
                if amount < 0:
                    raise BusinessRuleError("Los precios de lista no pueden ser negativos.")
                self.repository.execute(
                    "INSERT INTO price_list_items(price_list_id,product_id,price_cents) VALUES(?,?,?)",
                    (cursor.lastrowid, product["id"], amount),
                )
            return cursor.lastrowid

    def update_price_list(
        self, price_list_id: int, name: str, prices: dict[str, int | float | str | Decimal]
    ) -> None:
        if not name.strip() or not prices:
            raise BusinessRuleError("La lista debe tener nombre y al menos un precio.")
        with self.repository.transaction():
            _require(
                self.repository.one("SELECT id FROM price_lists WHERE id=?", (price_list_id,)),
                "Lista de precios inexistente.",
            )
            self.repository.execute("UPDATE price_lists SET name=? WHERE id=?", (name.strip(), price_list_id))
            self.repository.execute("DELETE FROM price_list_items WHERE price_list_id=?", (price_list_id,))
            for sku, price in prices.items():
                product = _require(
                    self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)),
                    f"Producto {sku} inexistente.",
                )
                amount = _cents(price)
                if amount < 0:
                    raise BusinessRuleError("Los precios de lista no pueden ser negativos.")
                self.repository.execute(
                    "INSERT INTO price_list_items(price_list_id,product_id,price_cents) VALUES(?,?,?)",
                    (price_list_id, product["id"], amount),
                )

    def delete_price_list(self, price_list_id: int) -> None:
        with self.repository.transaction():
            _require(
                self.repository.one("SELECT id FROM price_lists WHERE id=?", (price_list_id,)),
                "Lista de precios inexistente.",
            )
            if self.repository.one("SELECT id FROM sales WHERE price_list_id=? LIMIT 1", (price_list_id,)):
                raise BusinessRuleError("No se puede eliminar una lista aplicada a ventas registradas.")
            self.repository.execute("DELETE FROM price_list_items WHERE price_list_id=?", (price_list_id,))
            self.repository.execute("DELETE FROM price_lists WHERE id=?", (price_list_id,))

    def create_promotion(
        self, name: str, kind: str, value: int, starts_at: str, ends_at: str,
        sku: str | None = None, category: str | None = None,
    ) -> int:
        if kind not in ("percent", "fixed") or value <= 0 or (kind == "percent" and value > 10000):
            raise BusinessRuleError("La promoción debe tener un descuento válido.")
        if not name.strip():
            raise BusinessRuleError("El nombre de la promoción es obligatorio.")
        if not sku and not category:
            raise BusinessRuleError("La promoción debe aplicarse a un SKU o categoría.")
        try:
            start_date = date.fromisoformat(starts_at)
            end_date = date.fromisoformat(ends_at)
        except ValueError as error:
            raise BusinessRuleError("La vigencia debe usar fechas con formato AAAA-MM-DD.") from error
        if start_date > end_date:
            raise BusinessRuleError("La fecha de inicio debe ser anterior al fin.")
        with self.repository.transaction():
            product = self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)) if sku else None
            if sku and product is None:
                raise BusinessRuleError(f"Producto {sku} inexistente.")
            cursor = self.repository.execute(
                """INSERT INTO promotions(name,kind,value,product_id,category,starts_at,ends_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (name.strip(), kind, value, product["id"] if product else None, category or None, starts_at, ends_at),
            )
            return cursor.lastrowid

    def create_customer(self, code: str, name: str, phone: str = "", email: str = "", credit_limit: int | float | str | Decimal = 0) -> int:
        limit_cents = _cents(credit_limit)
        if not code.strip() or not name.strip():
            raise BusinessRuleError("El código y el nombre del cliente son obligatorios.")
        if limit_cents < 0:
            raise BusinessRuleError("El límite de crédito no puede ser negativo.")
        with self.repository.transaction():
            cursor = self.repository.execute(
                "INSERT INTO customers(code,name,phone,email,credit_limit_cents) VALUES(?,?,?,?,?)",
                (code.strip(), name.strip(), phone.strip(), email.strip(), limit_cents),
            )
            return cursor.lastrowid

    def open_inventory_count(self, location_code: str) -> int:
        with self.repository.transaction():
            location = _require(self.repository.one("SELECT id FROM locations WHERE code=?", (location_code,)), "Ubicación inexistente.")
            code = f"INV-{uuid4().hex[:10].upper()}"
            cursor = self.repository.execute(
                "INSERT INTO inventory_counts(code,location_id,status,created_at) VALUES(?,?,'Abierto',?)",
                (code, location["id"], _now()),
            )
            self.repository.execute(
                """INSERT INTO inventory_count_lines(count_id,product_id,expected_quantity)
                   SELECT ?,p.id,COALESCE(st.quantity,0) FROM products p
                   LEFT JOIN stock st ON st.product_id=p.id AND st.location_id=?""",
                (cursor.lastrowid, location["id"]),
            )
            return cursor.lastrowid

    def set_inventory_count(
        self, count_id: int, quantities: dict[str, int], trace_details: dict[str, dict] | None = None
    ) -> None:
        if not quantities or any(quantity < 0 for quantity in quantities.values()):
            raise BusinessRuleError("El conteo debe incluir cantidades iguales o mayores a cero.")
        with self.repository.transaction():
            count = _require(self.repository.one("SELECT status FROM inventory_counts WHERE id=?", (count_id,)), "Conteo inexistente.")
            if count["status"] != "Abierto":
                raise BusinessRuleError("El inventario físico ya está cerrado.")
            for sku, quantity in quantities.items():
                product = _require(
                    self.repository.one("SELECT id,tracking_mode FROM products WHERE sku=?", (sku,)),
                    f"Producto {sku} inexistente.",
                )
                detail = (trace_details or {}).get(sku, {})
                if product["tracking_mode"] == "lot":
                    lots = detail.get("lots", [])
                    if sum(int(item.get("quantity", 0)) for item in lots) != quantity:
                        raise BusinessRuleError(f"El detalle de lotes de {sku} debe sumar el conteo físico.")
                    lot_codes = [str(item.get("code", "")).strip() for item in lots]
                    if len(set(lot_codes)) != len(lot_codes):
                        raise BusinessRuleError(f"Los códigos de lote de {sku} no pueden repetirse.")
                    for lot in lots:
                        if not lot.get("code") or int(lot.get("quantity", 0)) < 0:
                            raise BusinessRuleError(f"Revisa el detalle de lotes de {sku}.")
                        if lot.get("expiry_date"):
                            try:
                                date.fromisoformat(lot["expiry_date"])
                            except ValueError as error:
                                raise BusinessRuleError("La fecha de vencimiento debe tener formato AAAA-MM-DD.") from error
                elif product["tracking_mode"] == "serial":
                    serials = detail.get("serials", [])
                    if (
                        len(serials) != quantity
                        or any(not isinstance(serial, str) or not serial.strip() for serial in serials)
                        or len(set(serials)) != len(serials)
                    ):
                        raise BusinessRuleError(f"Indica un número de serie distinto por cada unidad de {sku}.")
                elif detail:
                    raise BusinessRuleError(f"{sku} no requiere detalle de lotes o series.")
                updated = self.repository.execute(
                    "UPDATE inventory_count_lines SET counted_quantity=?,trace_detail=? WHERE count_id=? AND product_id=?",
                    (quantity, json.dumps(detail, ensure_ascii=False), count_id, product["id"]),
                )
                if updated.rowcount != 1:
                    raise BusinessRuleError(f"El producto {sku} no pertenece a este inventario.")

    def close_inventory_count(self, count_id: int) -> None:
        with self.repository.transaction():
            count = _require(self.repository.one("SELECT * FROM inventory_counts WHERE id=?", (count_id,)), "Conteo inexistente.")
            if count["status"] != "Abierto":
                raise BusinessRuleError("El inventario físico ya está cerrado.")
            lines = self.repository.all(
                """SELECT cl.*,p.sku,p.tracking_mode FROM inventory_count_lines cl
                   JOIN products p ON p.id=cl.product_id WHERE cl.count_id=?""",
                (count_id,),
            )
            if not lines or any(line["counted_quantity"] is None for line in lines):
                raise BusinessRuleError("Completa el conteo de todos los productos antes de cerrar.")
            for line in lines:
                delta = line["counted_quantity"] - line["expected_quantity"]
                detail = json.loads(line["trace_detail"] or "{}")
                if line["tracking_mode"] == "lot":
                    lots = detail.get("lots", [])
                    self.repository.execute(
                        "UPDATE lots SET quantity=0 WHERE location_id=? AND product_id=?",
                        (count["location_id"], line["product_id"]),
                    )
                    for lot in lots:
                        self.repository.execute(
                            """INSERT INTO lots(location_id,product_id,code,expiry_date,quantity) VALUES(?,?,?,?,?)
                               ON CONFLICT(location_id,product_id,code) DO UPDATE SET
                               quantity=excluded.quantity,expiry_date=excluded.expiry_date""",
                            (count["location_id"], line["product_id"], lot["code"], lot.get("expiry_date") or None, int(lot["quantity"])),
                        )
                        lot_id = self.repository.one(
                            "SELECT id FROM lots WHERE location_id=? AND product_id=? AND code=?",
                            (count["location_id"], line["product_id"], lot["code"]),
                        )["id"]
                        self._sync_available_serials(
                            count["location_id"], line["product_id"], int(lot["quantity"]), lot_id
                        )
                    lot_codes = {str(lot["code"]).strip() for lot in lots}
                    for previous_lot in self.repository.all(
                        """SELECT id,code FROM lots WHERE location_id=? AND product_id=?""",
                        (count["location_id"], line["product_id"]),
                    ):
                        if previous_lot["code"] not in lot_codes:
                            self.repository.execute(
                                """UPDATE serial_numbers SET status='retired' WHERE location_id=? AND product_id=?
                                   AND lot_id=? AND status='available'""",
                                (count["location_id"], line["product_id"], previous_lot["id"]),
                            )
                elif line["tracking_mode"] == "serial":
                    serials = detail.get("serials", [])
                    self.repository.execute(
                        """UPDATE serial_numbers SET status='retired' WHERE location_id=? AND product_id=?
                           AND status='available'""",
                        (count["location_id"], line["product_id"]),
                    )
                    for serial in serials:
                        existing = self.repository.one("SELECT id FROM serial_numbers WHERE serial=?", (serial,))
                        if existing:
                            existing_record = self.repository.one(
                                "SELECT location_id,product_id FROM serial_numbers WHERE id=?", (existing["id"],)
                            )
                            if existing_record["location_id"] != count["location_id"] or existing_record["product_id"] != line["product_id"]:
                                raise BusinessRuleError(f"La serie {serial} está registrada en otra ubicación o producto.")
                            self.repository.execute(
                                "UPDATE serial_numbers SET location_id=?,product_id=?,status='available' WHERE id=?",
                                (count["location_id"], line["product_id"], existing["id"]),
                            )
                        else:
                            self.repository.execute(
                                "INSERT INTO serial_numbers(location_id,product_id,serial,status) VALUES(?,?,?,'available')",
                                (count["location_id"], line["product_id"], serial),
                            )
                else:
                    self._sync_available_serials(
                        count["location_id"], line["product_id"], line["counted_quantity"]
                    )
                if delta:
                    self.repository.execute(
                        """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                           ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=excluded.quantity""",
                        (count["location_id"], line["product_id"], line["counted_quantity"]),
                    )
                    self._record_inventory_movement(
                        count["location_id"], line["product_id"], delta,
                        "conciliación inventario físico", str(count_id),
                    )
            self.repository.execute(
                "UPDATE inventory_counts SET status='Cerrado',closed_at=? WHERE id=?",
                (_now(), count_id),
            )

    def create_replenishment(self, destination_code: str, quantities: dict[str, int]) -> int:
        with self.repository.transaction():
            destination = _require(self.repository.one("SELECT * FROM locations WHERE code=?", (destination_code,)), "Local de destino inexistente.")
            if destination["type"] != "LOCAL":
                raise BusinessRuleError("El destino de una reposición debe ser un local.")
            origin = _require(self.repository.one("SELECT * FROM locations WHERE type='CD' ORDER BY id LIMIT 1"), "Debe existir un Centro de Distribución.")
            if not quantities or any(quantity <= 0 for quantity in quantities.values()):
                raise BusinessRuleError("La orden debe incluir cantidades positivas.")
            code = f"OR-{uuid4().hex[:10].upper()}"
            cursor = self.repository.execute(
                "INSERT INTO replenishment_orders(code,origin_id,destination_id,status,created_at) VALUES(?,?,?,'Abierta',?)",
                (code, origin["id"], destination["id"], _now()),
            )
            order_id = cursor.lastrowid
            for sku, quantity in quantities.items():
                product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                self.repository.execute("INSERT INTO replenishment_lines VALUES(?,?,?)", (order_id, product["id"], quantity))
            return order_id

    def create_consolidated_replenishment(self, destinations: list[dict]) -> int:
        if not destinations:
            raise BusinessRuleError("La orden consolidada debe incluir al menos un local.")
        with self.repository.transaction():
            origin = _require(
                self.repository.one("SELECT id FROM locations WHERE type='CD' ORDER BY id LIMIT 1"),
                "Debe existir un Centro de Distribución.",
            )
            normalized: dict[str, dict[str, int]] = {}
            for destination_entry in destinations:
                destination_code = str(destination_entry.get("destination_code", "")).strip()
                items = destination_entry.get("items", {})
                if not destination_code or not isinstance(items, dict) or not items:
                    raise BusinessRuleError("Cada destino de la orden debe tener productos y cantidades.")
                destination = _require(
                    self.repository.one("SELECT id,type FROM locations WHERE code=?", (destination_code,)),
                    f"Local de destino {destination_code} inexistente.",
                )
                if destination["type"] != "LOCAL":
                    raise BusinessRuleError("Cada destino de reposición debe ser un local.")
                destination_items = normalized.setdefault(destination_code, {})
                for sku, raw_quantity in items.items():
                    quantity = int(raw_quantity)
                    if quantity <= 0:
                        raise BusinessRuleError("La orden debe incluir cantidades positivas.")
                    destination_items[sku] = destination_items.get(sku, 0) + quantity

            batch_code = f"ORC-{uuid4().hex[:10].upper()}"
            batch = self.repository.execute(
                "INSERT INTO replenishment_batches(code,origin_id,created_at) VALUES(?,?,?)",
                (batch_code, origin["id"], _now()),
            )
            for destination_code, items in normalized.items():
                destination = self.repository.one(
                    "SELECT id FROM locations WHERE code=?", (destination_code,)
                )
                order_code = f"OR-{uuid4().hex[:10].upper()}"
                order = self.repository.execute(
                    """INSERT INTO replenishment_orders
                       (code,origin_id,destination_id,batch_id,status,created_at)
                       VALUES(?,?,?,?, 'Abierta',?)""",
                    (order_code, origin["id"], destination["id"], batch.lastrowid, _now()),
                )
                for sku, quantity in items.items():
                    product = _require(
                        self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)),
                        f"Producto {sku} inexistente.",
                    )
                    self.repository.execute(
                        "INSERT INTO replenishment_lines(order_id,product_id,quantity) VALUES(?,?,?)",
                        (order.lastrowid, product["id"], quantity),
                    )
            return batch.lastrowid

    def pack(self, order_id: int, package_code: str, dispatch_note: str, lines: dict[str, int]) -> int:
        if not lines or any(quantity <= 0 for quantity in lines.values()):
            raise BusinessRuleError("El bulto debe incluir cantidades positivas.")
        with self.repository.transaction():
            order = _require(self.repository.one("SELECT * FROM replenishment_orders WHERE id=?", (order_id,)), "Orden de reposición inexistente.")
            if order["status"] not in ("Abierta", "Preparando", "Con rechazo"):
                raise BusinessRuleError("La orden ya no admite bultos.")
            cursor = self.repository.execute(
                "INSERT INTO packages(code,order_id,dispatch_note,status,prepared_at) VALUES(?,?,?,'Preparado',?)",
                (package_code, order_id, dispatch_note, _now()),
            )
            package_id = cursor.lastrowid
            for sku, quantity in lines.items():
                product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                requested = self.repository.one("SELECT quantity FROM replenishment_lines WHERE order_id=? AND product_id=?", (order_id, product["id"]))
                if requested is None:
                    raise BusinessRuleError(f"El producto {sku} no pertenece a la orden.")
                already_packed = self.repository.one(
                    "SELECT COALESCE(SUM(pl.quantity),0) AS quantity FROM package_lines pl JOIN packages p ON p.id=pl.package_id WHERE p.order_id=? AND p.status<>'Rechazado' AND pl.product_id=?",
                    (order_id, product["id"]),
                )["quantity"]
                if already_packed + quantity > requested["quantity"]:
                    raise BusinessRuleError(f"El bulto excede la cantidad solicitada de {sku}.")
                on_hand = self.repository.one("SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (order["origin_id"], product["id"]))
                on_hand_quantity = on_hand["quantity"] if on_hand else 0
                reserved = self.repository.one(
                    """SELECT COALESCE(SUM(pl.quantity),0) AS quantity FROM package_lines pl
                       JOIN packages p ON p.id=pl.package_id WHERE p.order_id=? AND pl.product_id=?
                       AND p.status IN ('Preparado','Precintado')""",
                    (order_id, product["id"]),
                )["quantity"]
                reserved_elsewhere = self.repository.one(
                    """SELECT COALESCE(SUM(pl.quantity),0) AS quantity FROM package_lines pl
                       JOIN packages p ON p.id=pl.package_id WHERE p.status IN ('Preparado','Precintado')
                       AND p.order_id<>? AND pl.product_id=?""",
                    (order_id, product["id"]),
                )["quantity"]
                if reserved + reserved_elsewhere + quantity > on_hand_quantity:
                    raise BusinessRuleError(f"Stock insuficiente en el CD para {sku}.")
                self.repository.execute("INSERT INTO package_lines VALUES(?,?,?)", (package_id, product["id"], quantity))
            self._event(package_id, "Preparado", "Bulto armado.")
            self.repository.execute("UPDATE replenishment_orders SET status='Preparando' WHERE id=?", (order_id,))
            return package_id

    def _event(self, package_id: int, state: str, detail: str = "") -> None:
        self.repository.execute(
            "INSERT INTO package_events(package_id,state,occurred_at,detail) VALUES(?,?,?,?)",
            (package_id, state, _now(), detail),
        )

    def _record_inventory_movement(
        self,
        location_id: int,
        product_id: int,
        quantity_delta: int,
        movement_type: str,
        reference: str = "",
        unit_cost_cents: int | None = None,
        waste_quantity: int = 0,
        occurred_at: str | None = None,
    ) -> None:
        if unit_cost_cents is None:
            product = self.repository.one("SELECT cost_cents FROM products WHERE id=?", (product_id,))
            unit_cost_cents = product["cost_cents"]
        self.repository.execute(
            """INSERT INTO inventory_movements
               (occurred_at,location_id,product_id,quantity_delta,movement_type,reference,
                unit_cost_cents,waste_quantity)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                occurred_at or _now(), location_id, product_id, quantity_delta, movement_type,
                reference, unit_cost_cents, waste_quantity,
            ),
        )

    def seal_package(self, package_code: str, seal: str) -> None:
        with self.repository.transaction():
            package = _require(self.repository.one("SELECT * FROM packages WHERE code=?", (package_code,)), "Bulto inexistente.")
            if package["status"] != "Preparado" or not seal.strip():
                raise BusinessRuleError("Solo se puede precintar un bulto preparado con un precinto informado.")
            self.repository.execute("UPDATE packages SET seal=?,status='Precintado' WHERE id=?", (seal.strip(), package["id"]))
            self._event(package["id"], "Precintado", f"Precinto {seal.strip()} asignado.")

    def dispatch(self, package_code: str) -> None:
        with self.repository.transaction():
            package = _require(self.repository.one("SELECT * FROM packages WHERE code=?", (package_code,)), "Bulto inexistente.")
            if package["status"] != "Precintado":
                raise BusinessRuleError("Solo se despachan bultos precintados.")
            order = self.repository.one("SELECT origin_id,destination_id FROM replenishment_orders WHERE id=?", (package["order_id"],))
            for line in self.repository.all("SELECT * FROM package_lines WHERE package_id=?", (package["id"],)):
                stock = self.repository.one("SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (order["origin_id"], line["product_id"]))
                if stock is None or stock["quantity"] < line["quantity"]:
                    raise BusinessRuleError("El stock del CD cambió; no se puede despachar el bulto.")
                product = self.repository.one("SELECT tracking_mode FROM products WHERE id=?", (line["product_id"],))
                if product["tracking_mode"] == "lot":
                    lots = self.repository.all(
                        """SELECT id,code,expiry_date,quantity FROM lots
                           WHERE location_id=? AND product_id=? AND quantity>0
                           AND (expiry_date IS NULL OR expiry_date>=?)
                           ORDER BY expiry_date IS NULL,expiry_date,id""",
                        (order["origin_id"], line["product_id"], date.today().isoformat()),
                    )
                    remaining = line["quantity"]
                    for lot in lots:
                        allocated = min(remaining, lot["quantity"])
                        if not allocated:
                            continue
                        serials = self.repository.all(
                            """SELECT id FROM serial_numbers WHERE location_id=? AND product_id=?
                               AND lot_id=? AND status='available' ORDER BY id LIMIT ?""",
                            (order["origin_id"], line["product_id"], lot["id"], allocated),
                        )
                        if len(serials) != allocated:
                            raise BusinessRuleError(f"Las series disponibles no coinciden con el lote {lot['code']}.")
                        self.repository.execute("UPDATE lots SET quantity=quantity-? WHERE id=?", (allocated, lot["id"]))
                        self.repository.execute(
                            """INSERT INTO lots(location_id,product_id,code,expiry_date,quantity) VALUES(?,?,?,?,?)
                               ON CONFLICT(location_id,product_id,code) DO UPDATE SET quantity=quantity+excluded.quantity,
                               expiry_date=COALESCE(excluded.expiry_date,lots.expiry_date)""",
                            (order["destination_id"], line["product_id"], lot["code"], lot["expiry_date"], allocated),
                        )
                        destination_lot_id = self.repository.one(
                            "SELECT id FROM lots WHERE location_id=? AND product_id=? AND code=?",
                            (order["destination_id"], line["product_id"], lot["code"]),
                        )["id"]
                        for serial in serials:
                            self.repository.execute(
                                "UPDATE serial_numbers SET location_id=?,lot_id=?,status='sold' WHERE id=?",
                                (order["destination_id"], destination_lot_id, serial["id"]),
                            )
                            self.repository.execute(
                                """INSERT INTO package_unit_serials
                                   (package_id,serial_id,origin_location_id,origin_lot_id,destination_lot_id)
                                   VALUES(?,?,?,?,?)""",
                                (package["id"], serial["id"], order["origin_id"], lot["id"], destination_lot_id),
                            )
                        remaining -= allocated
                        if remaining == 0:
                            break
                    if remaining:
                        raise BusinessRuleError(f"Lotes vigentes insuficientes para despachar {line['product_id']}.")
                else:
                    serials = self.repository.all(
                        """SELECT id FROM serial_numbers WHERE location_id=? AND product_id=?
                           AND lot_id IS NULL AND status='available' ORDER BY id LIMIT ?""",
                        (order["origin_id"], line["product_id"], line["quantity"]),
                    )
                    if len(serials) != line["quantity"]:
                        raise BusinessRuleError("Las series disponibles no coinciden con el stock del CD.")
                    for serial in serials:
                        self.repository.execute(
                            "UPDATE serial_numbers SET location_id=?,status='sold' WHERE id=?",
                            (order["destination_id"], serial["id"]),
                        )
                        self.repository.execute(
                            """INSERT INTO package_unit_serials
                               (package_id,serial_id,origin_location_id) VALUES(?,?,?)""",
                            (package["id"], serial["id"], order["origin_id"]),
                        )
                self.repository.execute("UPDATE stock SET quantity=quantity-? WHERE location_id=? AND product_id=?", (line["quantity"], order["origin_id"], line["product_id"]))
                self._record_inventory_movement(
                    order["origin_id"], line["product_id"], -line["quantity"], "despacho", package_code
                )
            self.repository.execute("UPDATE packages SET status='En tránsito',dispatched_at=? WHERE id=?", (_now(), package["id"]))
            self._event(package["id"], "En tránsito", "Despacho registrado desde el CD.")
            self.repository.execute("UPDATE replenishment_orders SET status='En tránsito' WHERE id=?", (package["order_id"],))

    def receive(self, package_code: str, seal_intact: bool, dispatch_note_ok: bool = True) -> str:
        with self.repository.transaction():
            package = _require(self.repository.one("SELECT * FROM packages WHERE code=?", (package_code,)), "Bulto inexistente.")
            if package["status"] != "En tránsito":
                raise BusinessRuleError("Solo se reciben bultos en tránsito.")
            self.repository.execute(
                "UPDATE packages SET status='Recibido',received_at=?,seal_intact=?,dispatch_note_ok=? WHERE id=?",
                (_now(), int(seal_intact), int(dispatch_note_ok), package["id"]),
            )
            self._event(package["id"], "Recibido", "Recepción registrada en el local destino.")
            if not seal_intact or not dispatch_note_ok:
                reason = "Precinto violado." if not seal_intact else "Remito no conforme."
                self.repository.execute("UPDATE packages SET status='Rechazado' WHERE id=?", (package["id"],))
                self._event(package["id"], "Rechazado", reason)
                self.repository.execute("UPDATE replenishment_orders SET status='Con rechazo' WHERE id=?", (package["order_id"],))
                order = self.repository.one("SELECT origin_id FROM replenishment_orders WHERE id=?", (package["order_id"],))
                for unit in self.repository.all(
                    """SELECT pus.*,l.code,l.expiry_date FROM package_unit_serials pus
                       LEFT JOIN lots l ON l.id=pus.origin_lot_id WHERE pus.package_id=?""",
                    (package["id"],),
                ):
                    if unit["origin_lot_id"] is not None:
                        self.repository.execute(
                            "UPDATE lots SET quantity=quantity+1 WHERE id=?", (unit["origin_lot_id"],)
                        )
                        self.repository.execute(
                            "UPDATE lots SET quantity=quantity-1 WHERE id=?", (unit["destination_lot_id"],)
                        )
                        self.repository.execute(
                            """UPDATE serial_numbers SET location_id=?,lot_id=?,status='available'
                               WHERE id=?""",
                            (order["origin_id"], unit["origin_lot_id"], unit["serial_id"]),
                        )
                    else:
                        self.repository.execute(
                            "UPDATE serial_numbers SET location_id=?,status='available' WHERE id=?",
                            (order["origin_id"], unit["serial_id"]),
                        )
                for line in self.repository.all("SELECT * FROM package_lines WHERE package_id=?", (package["id"],)):
                    self.repository.execute(
                        """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                           ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                        (order["origin_id"], line["product_id"], line["quantity"]),
                    )
                    self._record_inventory_movement(
                        order["origin_id"], line["product_id"], line["quantity"],
                        "despacho rechazado", package_code,
                    )
                return "Rechazado"
            return "Recibido"

    def verify_package(self, package_code: str) -> None:
        with self.repository.transaction():
            package = _require(self.repository.one("SELECT * FROM packages WHERE code=?", (package_code,)), "Bulto inexistente.")
            if package["status"] != "Recibido" or not package["seal_intact"] or not package["dispatch_note_ok"]:
                raise BusinessRuleError("Solo se verifican bultos recibidos con remito y precinto conformes.")
            order = self.repository.one("SELECT destination_id FROM replenishment_orders WHERE id=?", (package["order_id"],))
            for line in self.repository.all("SELECT * FROM package_lines WHERE package_id=?", (package["id"],)):
                self.repository.execute(
                    """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                       ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                    (order["destination_id"], line["product_id"], line["quantity"]),
                )
                self.repository.execute(
                    """UPDATE serial_numbers SET status='available' WHERE id IN
                       (SELECT serial_id FROM package_unit_serials WHERE package_id=?) AND product_id=?""",
                    (package["id"], line["product_id"]),
                )
                self._record_inventory_movement(
                    order["destination_id"], line["product_id"], line["quantity"],
                    "recepción verificada", package_code,
                )
            self.repository.execute("UPDATE packages SET status='Verificado' WHERE id=?", (package["id"],))
            self._event(package["id"], "Verificado", "Remito y contenido verificados; stock liberado.")
            unfulfilled = self.repository.one(
                """SELECT COUNT(*) AS count FROM replenishment_lines rl WHERE rl.order_id=? AND rl.quantity >
                   (SELECT COALESCE(SUM(pl.quantity),0) FROM package_lines pl JOIN packages p ON p.id=pl.package_id
                    WHERE p.order_id=rl.order_id AND p.status='Verificado' AND pl.product_id=rl.product_id)""",
                (package["order_id"],),
            )["count"]
            self.repository.execute(
                "UPDATE replenishment_orders SET status=? WHERE id=?",
                ("Preparando" if unfulfilled else "Completada", package["order_id"]),
            )

    def sell(
        self,
        location_code: str,
        items: dict[str, int],
        payments: dict[str, int | float | str | Decimal],
        customer_id: int | None = None,
        price_list_id: int | None = None,
    ) -> str:
        if not items or any(quantity <= 0 for quantity in items.values()):
            raise BusinessRuleError("La venta debe incluir cantidades positivas.")
        with self.repository.transaction():
            location = _require(self.repository.one("SELECT * FROM locations WHERE code=?", (location_code,)), "Local inexistente.")
            if location["type"] != "LOCAL":
                raise BusinessRuleError("Las ventas solo se registran en locales.")
            customer = self.repository.one("SELECT * FROM customers WHERE id=?", (customer_id,)) if customer_id else None
            if customer_id and customer is None:
                raise BusinessRuleError("Cliente inexistente.")
            price_list = self.repository.one("SELECT id FROM price_lists WHERE id=? AND active=1", (price_list_id,)) if price_list_id else None
            if price_list_id and price_list is None:
                raise BusinessRuleError("La lista de precios no existe o está inactiva.")
            prepared = []
            total = 0
            today = date.today().isoformat()
            for sku, quantity in items.items():
                product = _require(self.repository.one("SELECT * FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                stock = self.repository.one("SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (location["id"], product["id"]))
                if stock is None or stock["quantity"] < quantity:
                    raise BusinessRuleError(f"Stock verificado insuficiente para {sku}.")
                unit_price = product["price_cents"]
                if price_list:
                    listed = self.repository.one(
                        "SELECT price_cents FROM price_list_items WHERE price_list_id=? AND product_id=?",
                        (price_list["id"], product["id"]),
                    )
                    if listed:
                        unit_price = listed["price_cents"]
                promotions = self.repository.all(
                    """SELECT * FROM promotions WHERE active=1 AND starts_at<=? AND ends_at>=?
                       AND (product_id=? OR (product_id IS NULL AND category=?)) ORDER BY id""",
                    (today, today, product["id"], product["category"]),
                )
                best_price = unit_price
                best_promotion = None
                for promotion in promotions:
                    discount = (
                        unit_price * promotion["value"] // 10000
                        if promotion["kind"] == "percent"
                        else promotion["value"]
                    )
                    candidate = max(0, unit_price - discount)
                    if candidate < best_price:
                        best_price = candidate
                        best_promotion = promotion["id"]
                allocations = self._trace_allocations(product, location["id"], quantity)
                line_total = best_price * quantity
                total += line_total
                prepared.append((product, quantity, line_total, best_price, unit_price - best_price, best_promotion, allocations))
            payment_values = []
            for method, value in payments.items():
                strategy = PAYMENT_STRATEGIES.get(method)
                if strategy is None:
                    raise BusinessRuleError(f"Medio de pago no admitido: {method}.")
                amount = _cents(value)
                strategy.validate(amount)
                if method == "cuenta corriente":
                    if customer is None:
                        raise BusinessRuleError("Selecciona un cliente para cobrar en cuenta corriente.")
                    if customer["balance_cents"] + amount > customer["credit_limit_cents"]:
                        raise BusinessRuleError("El cobro supera el crédito disponible del cliente.")
                payment_values.append((method, amount))
            if not payment_values or sum(amount for _, amount in payment_values) != total:
                raise BusinessRuleError("La suma de los cobros debe coincidir exactamente con el total de la venta.")
            receipt = f"V-{uuid4().hex[:12].upper()}"
            earned_points = total // 1000 if customer else 0
            sale = self.repository.execute(
                """INSERT INTO sales(receipt,location_id,sold_at,total_cents,state,customer_id,price_list_id,loyalty_points_earned)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (receipt, location["id"], _now(), total, ConfirmedSaleState.name, customer_id, price_list_id, earned_points),
            )
            for product, quantity, _, unit_price, discount, promotion_id, allocations in prepared:
                self.repository.execute(
                    """INSERT INTO sale_lines(sale_id,product_id,quantity,unit_price_cents,unit_cost_cents,discount_cents,promotion_id)
                       VALUES(?,?,?,?,?,?,?)""",
                    (sale.lastrowid, product["id"], quantity, unit_price, product["cost_cents"], discount * quantity, promotion_id),
                )
                sale_line_id = self.repository.one("SELECT last_insert_rowid() AS id")["id"]
                self.repository.execute("UPDATE stock SET quantity=quantity-? WHERE location_id=? AND product_id=?", (quantity, location["id"], product["id"]))
                for allocation in allocations:
                    if allocation.get("lot_id") is not None:
                        self.repository.execute("UPDATE lots SET quantity=quantity-? WHERE id=?", (allocation["quantity"], allocation["lot_id"]))
                    self.repository.execute("UPDATE serial_numbers SET status='sold' WHERE id=?", (allocation["serial_id"],))
                    self.repository.execute(
                        "INSERT INTO sale_line_trace(sale_line_id,lot_id,serial_id,quantity) VALUES(?,?,?,?)",
                        (sale_line_id, None, allocation.get("serial_id"), allocation["quantity"]),
                    )
                self._record_inventory_movement(
                    location["id"], product["id"], -quantity, "venta", receipt,
                    unit_cost_cents=product["cost_cents"],
                )
            for method, amount in payment_values:
                self.repository.execute("INSERT INTO payments(sale_id,method,amount_cents) VALUES(?,?,?)", (sale.lastrowid, method, amount))
                if method == "cuenta corriente":
                    self.repository.execute("UPDATE customers SET balance_cents=balance_cents+? WHERE id=?", (amount, customer_id))
            if customer:
                self.repository.execute("UPDATE customers SET loyalty_points=loyalty_points+? WHERE id=?", (earned_points, customer_id))
            return receipt

    def return_items(self, receipt: str, items: dict[str, int], restock: bool = True) -> int:
        if not items or any(quantity <= 0 for quantity in items.values()):
            raise BusinessRuleError("La devolución debe incluir cantidades positivas.")
        with self.repository.transaction():
            sale = _require(self.repository.one("SELECT * FROM sales WHERE receipt=?", (receipt,)), "No existe una venta con ese comprobante.")
            state = sale_state_from_name(sale["state"])
            state.ensure_return_allowed()
            prepared = []
            total = 0
            for sku, quantity in items.items():
                product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                line = self.repository.one("SELECT * FROM sale_lines WHERE sale_id=? AND product_id=?", (sale["id"], product["id"]))
                if line is None or line["returned_quantity"] + quantity > line["quantity"]:
                    raise BusinessRuleError(f"La cantidad de devolución de {sku} excede lo vendido y aún no devuelto.")
                total += line["unit_price_cents"] * quantity
                prepared.append((line, quantity))
            refunded = self.repository.one("SELECT COALESCE(SUM(total_cents),0) AS total FROM returns WHERE sale_id=?", (sale["id"],))["total"]
            if refunded + total > sale["total_cents"]:
                raise BusinessRuleError("La devolución excede el importe pagado disponible.")
            sold_quantity = self.repository.one(
                "SELECT COALESCE(SUM(quantity),0) AS total FROM sale_lines WHERE sale_id=?",
                (sale["id"],),
            )["total"]
            returned_quantity = self.repository.one(
                "SELECT COALESCE(SUM(returned_quantity),0) AS total FROM sale_lines WHERE sale_id=?",
                (sale["id"],),
            )["total"] + sum(quantity for _, quantity in prepared)
            next_state = state.after_return(sold_quantity, returned_quantity)
            returned_at = _now()
            result = self.repository.execute("INSERT INTO returns(sale_id,returned_at,total_cents) VALUES(?,?,?)", (sale["id"], returned_at, total))
            return_id = result.lastrowid
            for line, quantity in prepared:
                self.repository.execute("UPDATE sale_lines SET returned_quantity=returned_quantity+? WHERE id=?", (quantity, line["id"]))
                return_line = self.repository.execute(
                    "INSERT INTO return_lines(return_id,sale_line_id,quantity,restocked) VALUES(?,?,?,?)",
                    (return_id, line["id"], quantity, int(restock)),
                )
                trace_rows = self.repository.all(
                    """SELECT st.id,st.lot_id,st.serial_id,st.quantity-
                              COALESCE((SELECT SUM(rt.quantity) FROM return_line_trace rt
                                        WHERE rt.sale_line_trace_id=st.id),0) AS remaining
                       FROM sale_line_trace st WHERE st.sale_line_id=? ORDER BY st.id""",
                    (line["id"],),
                )
                has_trace = bool(trace_rows)
                trace_remaining = quantity
                for trace in trace_rows:
                    allocated = min(trace_remaining, trace["remaining"])
                    if allocated <= 0:
                        continue
                    self.repository.execute(
                        "INSERT INTO return_line_trace(return_line_id,sale_line_trace_id,quantity) VALUES(?,?,?)",
                        (return_line.lastrowid, trace["id"], allocated),
                    )
                    if restock and trace["lot_id"] is not None:
                        self.repository.execute("UPDATE lots SET quantity=quantity+? WHERE id=?", (allocated, trace["lot_id"]))
                    elif restock and trace["serial_id"] is not None:
                        serial = self.repository.one("SELECT lot_id FROM serial_numbers WHERE id=?", (trace["serial_id"],))
                        self.repository.execute("UPDATE serial_numbers SET status='available' WHERE id=?", (trace["serial_id"],))
                        if serial["lot_id"] is not None:
                            self.repository.execute("UPDATE lots SET quantity=quantity+? WHERE id=?", (allocated, serial["lot_id"]))
                    elif trace["serial_id"] is not None:
                        self.repository.execute("UPDATE serial_numbers SET status='retired' WHERE id=?", (trace["serial_id"],))
                    trace_remaining -= allocated
                    if trace_remaining == 0:
                        break
                if has_trace and trace_remaining:
                    raise BusinessRuleError("No se pudo identificar el lote o serie de los artículos devueltos.")
                if restock:
                    self.repository.execute(
                        """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                           ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                        (sale["location_id"], line["product_id"], quantity),
                    )
                    self._record_inventory_movement(
                        sale["location_id"], line["product_id"], quantity, "devolución apta",
                        receipt, unit_cost_cents=line["unit_cost_cents"], occurred_at=returned_at,
                    )
                else:
                    self._record_inventory_movement(
                        sale["location_id"], line["product_id"], 0, "merma devolución",
                        receipt, unit_cost_cents=line["unit_cost_cents"],
                        waste_quantity=quantity, occurred_at=returned_at,
                    )
            self.repository.execute("UPDATE sales SET state=? WHERE id=?", (next_state.name, sale["id"]))
            original_payments = self.repository.all("SELECT method,amount_cents FROM payments WHERE sale_id=? ORDER BY id", (sale["id"],))
            allocated = 0
            account_refund = 0
            for index, payment in enumerate(original_payments):
                amount = total - allocated if index == len(original_payments) - 1 else total * payment["amount_cents"] // sale["total_cents"]
                if amount:
                    self.repository.execute("INSERT INTO return_payments(return_id,method,amount_cents) VALUES(?,?,?)", (return_id, payment["method"], amount))
                    allocated += amount
                    if payment["method"] == "cuenta corriente":
                        account_refund += amount
            if sale["customer_id"] is not None:
                if account_refund:
                    self.repository.execute(
                        "UPDATE customers SET balance_cents=balance_cents-? WHERE id=?",
                        (account_refund, sale["customer_id"]),
                    )
                new_refunded = refunded + total
                new_reversed_points = sale["loyalty_points_earned"] * new_refunded // sale["total_cents"]
                old_reversed_points = sale["loyalty_points_earned"] * refunded // sale["total_cents"]
                if new_reversed_points > old_reversed_points:
                    self.repository.execute(
                        "UPDATE customers SET loyalty_points=MAX(0,loyalty_points-?) WHERE id=?",
                        (new_reversed_points - old_reversed_points, sale["customer_id"]),
                    )
            return total