from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from backend.dominio.exceptions import BusinessRuleError, NotFoundError
from backend.persistencia.sqlite_repository import SQLiteRepository
from backend.dominio.payment_strategies import PAYMENT_STRATEGIES
from backend.dominio.sale_states import ConfirmedSaleState, sale_state_from_name


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
                          EXISTS(SELECT 1 FROM sales WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM inventory_counts WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM replenishment_orders WHERE origin_id=? OR destination_id=?) OR
                          EXISTS(SELECT 1 FROM purchase_orders WHERE destination_id=?) AS used""",
                (location_id,) * 7,
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
                          EXISTS(SELECT 1 FROM sales WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM app_users WHERE location_id=?) OR
                          EXISTS(SELECT 1 FROM replenishment_orders WHERE origin_id=? OR destination_id=?) OR
                          EXISTS(SELECT 1 FROM purchase_orders WHERE destination_id=?) OR
                          EXISTS(SELECT 1 FROM inventory_counts WHERE location_id=?) AS used""",
                (location_id,) * 8,
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
            _require(
                self.repository.one("SELECT name FROM categories WHERE id=?", (category_id,)),
                "Categoría inexistente.",
            )
            self.repository.execute("UPDATE categories SET name=? WHERE id=?", (normalized, category_id))
            self.repository.execute("UPDATE products SET category=? WHERE category_id=?", (normalized, category_id))

    def delete_category(self, category_id: int) -> None:
        with self.repository.transaction():
            _require(
                self.repository.one("SELECT id FROM categories WHERE id=?", (category_id,)),
                "Categoría inexistente.",
            )
            used = self.repository.one(
                "SELECT EXISTS(SELECT 1 FROM products WHERE category_id=?) AS used",
                (category_id,),
            )["used"]
            if used:
                raise BusinessRuleError("No se puede borrar una categoría que usan productos.")
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
    ) -> int:
        if minimum_stock < 0 or _cents(cost) < 0 or _cents(price) < 0:
            raise BusinessRuleError("Costo, precio y stock mínimo no pueden ser negativos.")
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
                                        supplier_id,category_id)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    sku.strip(), name.strip(), category_record["name"], _cents(cost), _cents(price),
                    minimum_stock, supplier_id, category_record["id"],
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
    ) -> None:
        normalized_category = category.strip()
        if not sku.strip() or not name.strip() or not normalized_category:
            raise BusinessRuleError("SKU, nombre y categoría son obligatorios.")
        if minimum_stock < 0 or _cents(cost) < 0 or _cents(price) < 0:
            raise BusinessRuleError("Costo, precio y stock mínimo no pueden ser negativos.")
        with self.repository.transaction():
            current = _require(
                self.repository.one("SELECT sku FROM products WHERE id=?", (product_id,)),
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
            self.repository.execute(
                "INSERT OR IGNORE INTO categories(name) VALUES(?)", (normalized_category,)
            )
            category_record = self.repository.one(
                "SELECT id,name FROM categories WHERE name=? COLLATE NOCASE", (normalized_category,)
            )
            self.repository.execute(
                """UPDATE products SET sku=?,name=?,category=?,category_id=?,cost_cents=?,price_cents=?,
                       minimum_stock=?,supplier_id=? WHERE id=?""",
                (
                    sku.strip(), name.strip(), category_record["name"], category_record["id"],
                    _cents(cost), _cents(price), minimum_stock, supplier_id, product_id,
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
                          EXISTS(SELECT 1 FROM inventory_count_lines WHERE product_id=?) AS used""",
                (product_id,) * 7,
            )["used"]
            if references:
                raise BusinessRuleError("No se puede eliminar un producto con stock u operaciones históricas.")
            self.repository.execute("DELETE FROM stock WHERE product_id=? AND quantity=0", (product_id,))
            self.repository.execute("DELETE FROM products WHERE id=?", (product_id,))

    def adjust_stock(
        self,
        location_code: str,
        sku: str,
        delta: int,
        reason: str = "ajuste",
    ) -> None:
        if delta == 0:
            raise BusinessRuleError("El movimiento de stock no puede ser cero.")
        with self.repository.transaction():
            location = _require(
                self.repository.one("SELECT id,type FROM locations WHERE code=?", (location_code,)),
                "Ubicación inexistente.",
            )
            product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), "Producto inexistente.")
            if location["type"] == "LOCAL" and delta > 0:
                raise BusinessRuleError("El stock de un local solo aumenta al verificar una recepción o conciliar un conteo físico.")
            current = self.repository.one(
                "SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (location["id"], product["id"])
            )
            quantity = (current["quantity"] if current else 0) + delta
            if quantity < 0:
                raise BusinessRuleError("El ajuste dejaría el stock por debajo de cero.")
            self.repository.execute(
                """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                   ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=excluded.quantity""",
                (location["id"], product["id"], quantity),
            )
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

    def receive_purchase_order(self, order_id: int, quantities: dict[str, int]) -> None:
        if not quantities or any(quantity <= 0 for quantity in quantities.values()):
            raise BusinessRuleError("La recepción debe incluir cantidades positivas.")
        with self.repository.transaction():
            order = _require(self.repository.one("SELECT * FROM purchase_orders WHERE id=?", (order_id,)), "Orden de compra inexistente.")
            if order["status"] == "Recibida":
                raise BusinessRuleError("La orden de compra ya fue recibida por completo.")
            for sku, quantity in quantities.items():
                product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                line = _require(
                    self.repository.one("SELECT quantity,received_quantity FROM purchase_order_lines WHERE order_id=? AND product_id=?", (order_id, product["id"])),
                    f"El producto {sku} no pertenece a la orden.",
                )
                if line["received_quantity"] + quantity > line["quantity"]:
                    raise BusinessRuleError(f"La recepción excede lo pedido para {sku}.")
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

    def set_inventory_count(self, count_id: int, quantities: dict[str, int]) -> None:
        if not quantities or any(quantity < 0 for quantity in quantities.values()):
            raise BusinessRuleError("El conteo debe incluir cantidades iguales o mayores a cero.")
        with self.repository.transaction():
            count = _require(self.repository.one("SELECT status FROM inventory_counts WHERE id=?", (count_id,)), "Conteo inexistente.")
            if count["status"] != "Abierto":
                raise BusinessRuleError("El inventario físico ya está cerrado.")
            for sku, quantity in quantities.items():
                product = _require(
                    self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)),
                    f"Producto {sku} inexistente.",
                )
                updated = self.repository.execute(
                    "UPDATE inventory_count_lines SET counted_quantity=? WHERE count_id=? AND product_id=?",
                    (quantity, count_id, product["id"]),
                )
                if updated.rowcount != 1:
                    raise BusinessRuleError(f"El producto {sku} no pertenece a este inventario.")

    def close_inventory_count(self, count_id: int) -> None:
        with self.repository.transaction():
            count = _require(self.repository.one("SELECT * FROM inventory_counts WHERE id=?", (count_id,)), "Conteo inexistente.")
            if count["status"] != "Abierto":
                raise BusinessRuleError("El inventario físico ya está cerrado.")
            lines = self.repository.all(
                "SELECT cl.* FROM inventory_count_lines cl WHERE cl.count_id=?",
                (count_id,),
            )
            counted_lines = [line for line in lines if line["counted_quantity"] is not None]
            if not counted_lines:
                raise BusinessRuleError("Cuenta al menos un producto antes de cerrar.")
            for line in counted_lines:
                delta = line["counted_quantity"] - line["expected_quantity"]
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
        return self._pack_single_order(order_id, package_code, dispatch_note, lines)

    def pack_for_destination(
        self, destination_code: str, package_code: str, dispatch_note: str, lines: dict[str, int]
    ) -> int:
        orders = self.repository.all(
            """SELECT o.id FROM replenishment_orders o
               JOIN locations l ON l.id=o.destination_id
               WHERE l.code=? AND o.status IN ('Abierta','Preparando','Con rechazo')
               ORDER BY o.created_at,o.id""",
            (destination_code,),
        )
        return self._pack_orders(
            [order["id"] for order in orders], package_code, dispatch_note, lines
        )

    def _pack_orders(
        self, order_ids: list[int], package_code: str, dispatch_note: str, lines: dict[str, int]
    ) -> int:
        if not lines or any(quantity <= 0 for quantity in lines.values()):
            raise BusinessRuleError("El bulto debe incluir cantidades positivas.")
        if not order_ids:
            raise BusinessRuleError("No hay órdenes abiertas para ese local.")
        unique_order_ids = list(dict.fromkeys(order_ids))
        with self.repository.transaction():
            placeholders = ",".join("?" for _ in unique_order_ids)
            orders = self.repository.all(
                f"""SELECT o.* FROM replenishment_orders o
                    WHERE o.id IN ({placeholders}) ORDER BY o.created_at,o.id""",
                tuple(unique_order_ids),
            )
            if len(orders) != len(unique_order_ids) or any(
                order["status"] not in ("Abierta", "Preparando", "Con rechazo")
                for order in orders
            ):
                raise BusinessRuleError("Una o más órdenes ya no admiten bultos.")
            if len({order["destination_id"] for order in orders}) != 1:
                raise BusinessRuleError("Un bulto solo puede consolidar órdenes de un mismo local.")
            if len({order["origin_id"] for order in orders}) != 1:
                raise BusinessRuleError("Las órdenes consolidadas deben salir del mismo origen.")

            pending_by_order: dict[int, dict[int, int]] = {}
            for order in orders:
                pending_by_order[order["id"]] = {}
                requested_lines = self.repository.all(
                    """SELECT rl.product_id,rl.quantity,
                              COALESCE(SUM(CASE WHEN p.status<>'Rechazado'
                                                THEN pol.quantity ELSE 0 END),0) AS packed
                       FROM replenishment_lines rl
                       LEFT JOIN package_order_lines pol
                         ON pol.order_id=rl.order_id AND pol.product_id=rl.product_id
                       LEFT JOIN packages p ON p.id=pol.package_id
                       WHERE rl.order_id=?
                       GROUP BY rl.product_id,rl.quantity""",
                    (order["id"],),
                )
                for line in requested_lines:
                    remaining = line["quantity"] - line["packed"]
                    if remaining > 0:
                        pending_by_order[order["id"]][line["product_id"]] = remaining

            allocations: dict[int, dict[int, int]] = {}
            package_lines: dict[int, int] = {}
            for sku, quantity in lines.items():
                product = _require(
                    self.repository.one("SELECT * FROM products WHERE sku=?", (sku,)),
                    f"Producto {sku} inexistente.",
                )
                available = sum(
                    order_pending.get(product["id"], 0)
                    for order_pending in pending_by_order.values()
                )
                if available == 0:
                    raise BusinessRuleError(f"El producto {sku} no está pendiente para ese local.")
                if quantity > available:
                    raise BusinessRuleError(
                        f"El bulto excede la cantidad solicitada pendiente de {sku}."
                    )

                origin_id = orders[0]["origin_id"]
                on_hand = self.repository.one(
                    "SELECT quantity FROM stock WHERE location_id=? AND product_id=?",
                    (origin_id, product["id"]),
                )
                on_hand_quantity = on_hand["quantity"] if on_hand else 0
                reserved = self.repository.one(
                    """SELECT COALESCE(SUM(pl.quantity),0) AS quantity FROM package_lines pl
                       JOIN packages p ON p.id=pl.package_id
                       WHERE p.status IN ('Preparado','Precintado') AND pl.product_id=?""",
                    (product["id"],),
                )["quantity"]
                if reserved + quantity > on_hand_quantity:
                    raise BusinessRuleError(f"Stock insuficiente en el CD para {sku}.")

                remaining_to_allocate = quantity
                for order in orders:
                    order_pending = pending_by_order[order["id"]]
                    allocation = min(
                        order_pending.get(product["id"], 0), remaining_to_allocate
                    )
                    if not allocation:
                        continue
                    allocations.setdefault(order["id"], {})[product["id"]] = allocation
                    order_pending[product["id"]] -= allocation
                    remaining_to_allocate -= allocation
                    if not remaining_to_allocate:
                        break
                package_lines[product["id"]] = quantity

            primary_order_id = next(order["id"] for order in orders if order["id"] in allocations)
            cursor = self.repository.execute(
                "INSERT INTO packages(code,order_id,dispatch_note,status,prepared_at) VALUES(?,?,?,'Preparado',?)",
                (package_code, primary_order_id, dispatch_note, _now()),
            )
            package_id = cursor.lastrowid
            for order_id, product_allocations in allocations.items():
                self.repository.execute(
                    "INSERT INTO package_orders(package_id,order_id) VALUES(?,?)",
                    (package_id, order_id),
                )
                for product_id, quantity in product_allocations.items():
                    self.repository.execute(
                        """INSERT INTO package_order_lines(package_id,order_id,product_id,quantity)
                           VALUES(?,?,?,?)""",
                        (package_id, order_id, product_id, quantity),
                    )
            for product_id, quantity in package_lines.items():
                self.repository.execute(
                    "INSERT INTO package_lines(package_id,product_id,quantity) VALUES(?,?,?)",
                    (package_id, product_id, quantity),
                )
            self._event(package_id, "Preparado", "Bulto armado.")
            affected_order_ids = tuple(allocations)
            affected_placeholders = ",".join("?" for _ in affected_order_ids)
            self.repository.execute(
                f"UPDATE replenishment_orders SET status='Preparando' WHERE id IN ({affected_placeholders})",
                affected_order_ids,
            )
            return package_id

    def _pack_single_order(self, order_id: int, package_code: str, dispatch_note: str, lines: dict[str, int]) -> int:
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
            self.repository.execute(
                "INSERT INTO package_orders(package_id,order_id) VALUES(?,?)",
                (package_id, order_id),
            )
            for sku, quantity in lines.items():
                product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                requested = self.repository.one("SELECT quantity FROM replenishment_lines WHERE order_id=? AND product_id=?", (order_id, product["id"]))
                if requested is None:
                    raise BusinessRuleError(f"El producto {sku} no pertenece a la orden.")
                already_packed = self.repository.one(
                    """SELECT COALESCE(SUM(pol.quantity),0) AS quantity
                       FROM package_order_lines pol JOIN packages p ON p.id=pol.package_id
                       WHERE pol.order_id=? AND p.status<>'Rechazado' AND pol.product_id=?""",
                    (order_id, product["id"]),
                )["quantity"]
                if already_packed + quantity > requested["quantity"]:
                    raise BusinessRuleError(f"El bulto excede la cantidad solicitada de {sku}.")
                on_hand = self.repository.one("SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (order["origin_id"], product["id"]))
                on_hand_quantity = on_hand["quantity"] if on_hand else 0
                reserved = self.repository.one(
                    """SELECT COALESCE(SUM(pl.quantity),0) AS quantity FROM package_lines pl
                       JOIN packages p ON p.id=pl.package_id
                       WHERE p.status IN ('Preparado','Precintado') AND pl.product_id=?""",
                    (product["id"],),
                )["quantity"]
                if reserved + quantity > on_hand_quantity:
                    raise BusinessRuleError(f"Stock insuficiente en el CD para {sku}.")
                self.repository.execute("INSERT INTO package_lines VALUES(?,?,?)", (package_id, product["id"], quantity))
                self.repository.execute(
                    """INSERT INTO package_order_lines(package_id,order_id,product_id,quantity)
                       VALUES(?,?,?,?)""",
                    (package_id, order_id, product["id"], quantity),
                )
            self._event(package_id, "Preparado", "Bulto armado.")
            self.repository.execute("UPDATE replenishment_orders SET status='Preparando' WHERE id=?", (order_id,))
            return package_id

    def _event(self, package_id: int, state: str, detail: str = "") -> None:
        self.repository.execute(
            "INSERT INTO package_events(package_id,state,occurred_at,detail) VALUES(?,?,?,?)",
            (package_id, state, _now(), detail),
        )

    def _package_order_ids(self, package_id: int, primary_order_id: int) -> list[int]:
        orders = self.repository.all(
            "SELECT order_id FROM package_orders WHERE package_id=? ORDER BY order_id",
            (package_id,),
        )
        return [order["order_id"] for order in orders] or [primary_order_id]

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
                self.repository.execute("UPDATE stock SET quantity=quantity-? WHERE location_id=? AND product_id=?", (line["quantity"], order["origin_id"], line["product_id"]))
                self._record_inventory_movement(
                    order["origin_id"], line["product_id"], -line["quantity"], "despacho", package_code
                )
            self.repository.execute("UPDATE packages SET status='En tránsito',dispatched_at=? WHERE id=?", (_now(), package["id"]))
            self._event(package["id"], "En tránsito", "Despacho registrado desde el CD.")
            self.repository.execute(
                """UPDATE replenishment_orders SET status='En tránsito'
                   WHERE id IN (SELECT order_id FROM package_orders WHERE package_id=?)""",
                (package["id"],),
            )

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
                self.repository.execute(
                    """UPDATE replenishment_orders SET status='Con rechazo'
                       WHERE id IN (SELECT order_id FROM package_orders WHERE package_id=?)""",
                    (package["id"],),
                )
                return "Rechazado"
            return "Recibido"

    def resolve_rejected_package(self, package_code: str, disposition: str) -> None:
        if disposition not in ("stock", "discard"):
            raise BusinessRuleError("La resolución debe ser devolver al stock o descartar.")
        with self.repository.transaction():
            package = _require(
                self.repository.one(
                    """SELECT p.id,p.status,o.origin_id FROM packages p
                       JOIN replenishment_orders o ON o.id=p.order_id WHERE p.code=?""",
                    (package_code,),
                ),
                "Bulto inexistente.",
            )
            if package["status"] != "Rechazado":
                raise BusinessRuleError("Solo se pueden resolver bultos rechazados por el local.")
            if self.repository.one(
                "SELECT package_id FROM rejected_package_dispositions WHERE package_id=?",
                (package["id"],),
            ):
                raise BusinessRuleError("Este bulto rechazado ya fue resuelto.")
            lines = self.repository.all(
                "SELECT product_id,quantity FROM package_lines WHERE package_id=?",
                (package["id"],),
            )
            if not lines:
                raise BusinessRuleError("El bulto rechazado no tiene productos para resolver.")
            self.repository.execute(
                """INSERT INTO rejected_package_dispositions(package_id,disposition,resolved_at)
                   VALUES(?,?,?)""",
                (package["id"], disposition, _now()),
            )
            for line in lines:
                if disposition == "stock":
                    self.repository.execute(
                        """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                           ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                        (package["origin_id"], line["product_id"], line["quantity"]),
                    )
                    self._record_inventory_movement(
                        package["origin_id"], line["product_id"], line["quantity"],
                        "devolución bulto rechazado", package_code,
                    )
                else:
                    self._record_inventory_movement(
                        package["origin_id"], line["product_id"], 0,
                        "merma bulto rechazado", package_code,
                        waste_quantity=line["quantity"],
                    )

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
                self._record_inventory_movement(
                    order["destination_id"], line["product_id"], line["quantity"],
                    "recepción verificada", package_code,
                )
            self.repository.execute("UPDATE packages SET status='Verificado' WHERE id=?", (package["id"],))
            self._event(package["id"], "Verificado", "Remito y contenido verificados; stock liberado.")
            for order_id in self._package_order_ids(package["id"], package["order_id"]):
                unfulfilled = self.repository.one(
                    """SELECT COUNT(*) AS count FROM replenishment_lines rl WHERE rl.order_id=? AND rl.quantity >
                       (SELECT COALESCE(SUM(pol.quantity),0)
                        FROM package_order_lines pol JOIN packages p ON p.id=pol.package_id
                        WHERE pol.order_id=rl.order_id AND p.status='Verificado'
                          AND pol.product_id=rl.product_id)""",
                    (order_id,),
                )["count"]
                self.repository.execute(
                    "UPDATE replenishment_orders SET status=? WHERE id=?",
                    ("Preparando" if unfulfilled else "Completada", order_id),
                )

    def sell(
        self,
        location_code: str,
        items: dict[str, int],
        payments: dict[str, int | float | str | Decimal],
    ) -> str:
        if not items or any(quantity <= 0 for quantity in items.values()):
            raise BusinessRuleError("La venta debe incluir cantidades positivas.")
        with self.repository.transaction():
            location = _require(self.repository.one("SELECT * FROM locations WHERE code=?", (location_code,)), "Local inexistente.")
            if location["type"] != "LOCAL":
                raise BusinessRuleError("Las ventas solo se registran en locales.")
            prepared = []
            total = 0
            for sku, quantity in items.items():
                product = _require(self.repository.one("SELECT * FROM products WHERE sku=?", (sku,)), f"Producto {sku} inexistente.")
                stock = self.repository.one("SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (location["id"], product["id"]))
                if stock is None or stock["quantity"] < quantity:
                    raise BusinessRuleError(f"Stock verificado insuficiente para {sku}.")
                unit_price = product["price_cents"]
                line_total = unit_price * quantity
                total += line_total
                prepared.append((product, quantity, unit_price))
            payment_values = []
            cash_tendered = 0
            other_payments = 0
            for method, value in payments.items():
                strategy = PAYMENT_STRATEGIES.get(method)
                if strategy is None:
                    raise BusinessRuleError(f"Medio de pago no admitido: {method}.")
                amount = _cents(value)
                strategy.validate(amount)
                if method == "efectivo":
                    cash_tendered += amount
                else:
                    other_payments += amount
                    payment_values.append((method, amount))
            cash_due = total - other_payments
            if not payments or cash_due < 0 or cash_tendered < cash_due:
                raise BusinessRuleError(
                    "Los cobros deben cubrir el total; solo el efectivo puede superar el importe pendiente."
                )
            if cash_due > 0:
                payment_values.append(("efectivo", cash_due))
            receipt = f"V-{uuid4().hex[:12].upper()}"
            sale = self.repository.execute(
                """INSERT INTO sales(receipt,location_id,sold_at,total_cents,state)
                   VALUES(?,?,?,?,?)""",
                (receipt, location["id"], _now(), total, ConfirmedSaleState.name),
            )
            for product, quantity, unit_price in prepared:
                self.repository.execute(
                    """INSERT INTO sale_lines(sale_id,product_id,quantity,unit_price_cents,unit_cost_cents)
                       VALUES(?,?,?,?,?)""",
                    (sale.lastrowid, product["id"], quantity, unit_price, product["cost_cents"]),
                )
                self.repository.execute("UPDATE stock SET quantity=quantity-? WHERE location_id=? AND product_id=?", (quantity, location["id"], product["id"]))
                self._record_inventory_movement(
                    location["id"], product["id"], -quantity, "venta", receipt,
                    unit_cost_cents=product["cost_cents"],
                )
            for method, amount in payment_values:
                self.repository.execute("INSERT INTO payments(sale_id,method,amount_cents) VALUES(?,?,?)", (sale.lastrowid, method, amount))
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
                self.repository.execute(
                    "INSERT INTO return_lines(return_id,sale_line_id,quantity,restocked) VALUES(?,?,?,?)",
                    (return_id, line["id"], quantity, int(restock)),
                )
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
            for index, payment in enumerate(original_payments):
                amount = total - allocated if index == len(original_payments) - 1 else total * payment["amount_cents"] // sale["total_cents"]
                if amount:
                    self.repository.execute("INSERT INTO return_payments(return_id,method,amount_cents) VALUES(?,?,?)", (return_id, payment["method"], amount))
                    allocated += amount
            return total