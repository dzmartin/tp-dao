from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from exceptions import BusinessRuleError, NotFoundError
from repositories.sqlite_repository import SQLiteRepository
from services.payment_strategies import PAYMENT_STRATEGIES


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
            cursor = self.repository.execute(
                "INSERT INTO locations(code,name,type) VALUES(?,?,?)",
                (code.strip(), name.strip(), location_type),
            )
            return cursor.lastrowid

    def create_supplier(self, tax_id: str, name: str, phone: str = "", email: str = "") -> int:
        with self.repository.transaction():
            cursor = self.repository.execute(
                "INSERT INTO suppliers(tax_id,name,phone,email) VALUES(?,?,?,?)",
                (tax_id.strip(), name.strip(), phone.strip(), email.strip()),
            )
            return cursor.lastrowid

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
            cursor = self.repository.execute(
                """INSERT INTO products(sku,name,category,cost_cents,price_cents,minimum_stock,supplier_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (sku.strip(), name.strip(), category.strip(), _cents(cost), _cents(price), minimum_stock, supplier_id),
            )
            return cursor.lastrowid

    def adjust_stock(self, location_code: str, sku: str, delta: int, reason: str = "ajuste") -> None:
        if delta == 0:
            raise BusinessRuleError("El movimiento de stock no puede ser cero.")
        with self.repository.transaction():
            location = _require(self.repository.one("SELECT id FROM locations WHERE code=?", (location_code,)), "Local inexistente.")
            product = _require(self.repository.one("SELECT id FROM products WHERE sku=?", (sku,)), "Producto inexistente.")
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
            self.repository.execute(
                "INSERT INTO inventory_movements(occurred_at,location_id,product_id,quantity_delta,movement_type,reference) VALUES(?,?,?,?,?,?)",
                (_now(), location["id"], product["id"], delta, reason, ""),
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
            order = self.repository.one("SELECT origin_id FROM replenishment_orders WHERE id=?", (package["order_id"],))
            for line in self.repository.all("SELECT * FROM package_lines WHERE package_id=?", (package["id"],)):
                stock = self.repository.one("SELECT quantity FROM stock WHERE location_id=? AND product_id=?", (order["origin_id"], line["product_id"]))
                if stock is None or stock["quantity"] < line["quantity"]:
                    raise BusinessRuleError("El stock del CD cambió; no se puede despachar el bulto.")
                self.repository.execute("UPDATE stock SET quantity=quantity-? WHERE location_id=? AND product_id=?", (line["quantity"], order["origin_id"], line["product_id"]))
                self.repository.execute(
                    "INSERT INTO inventory_movements(occurred_at,location_id,product_id,quantity_delta,movement_type,reference) VALUES(?,?,?,?,?,?)",
                    (_now(), order["origin_id"], line["product_id"], -line["quantity"], "despacho", package_code),
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
                    "INSERT INTO inventory_movements(occurred_at,location_id,product_id,quantity_delta,movement_type,reference) VALUES(?,?,?,?,?,?)",
                    (_now(), order["destination_id"], line["product_id"], line["quantity"], "recepción verificada", package_code),
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

    def sell(self, location_code: str, items: dict[str, int], payments: dict[str, int | float | str | Decimal]) -> str:
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
                line_total = product["price_cents"] * quantity
                total += line_total
                prepared.append((product, quantity, line_total))
            payment_values = []
            for method, value in payments.items():
                strategy = PAYMENT_STRATEGIES.get(method)
                if strategy is None:
                    raise BusinessRuleError(f"Medio de pago no admitido: {method}.")
                amount = _cents(value)
                strategy.validate(amount)
                payment_values.append((method, amount))
            if not payment_values or sum(amount for _, amount in payment_values) != total:
                raise BusinessRuleError("La suma de los cobros debe coincidir exactamente con el total de la venta.")
            receipt = f"V-{uuid4().hex[:12].upper()}"
            sale = self.repository.execute(
                "INSERT INTO sales(receipt,location_id,sold_at,total_cents) VALUES(?,?,?,?)",
                (receipt, location["id"], _now(), total),
            )
            for product, quantity, _ in prepared:
                self.repository.execute(
                    "INSERT INTO sale_lines(sale_id,product_id,quantity,unit_price_cents,unit_cost_cents) VALUES(?,?,?,?,?)",
                    (sale.lastrowid, product["id"], quantity, product["price_cents"], product["cost_cents"]),
                )
                self.repository.execute("UPDATE stock SET quantity=quantity-? WHERE location_id=? AND product_id=?", (quantity, location["id"], product["id"]))
                self.repository.execute(
                    "INSERT INTO inventory_movements(occurred_at,location_id,product_id,quantity_delta,movement_type,reference) VALUES(?,?,?,?,?,?)",
                    (_now(), location["id"], product["id"], -quantity, "venta", receipt),
                )
            for method, amount in payment_values:
                self.repository.execute("INSERT INTO payments(sale_id,method,amount_cents) VALUES(?,?,?)", (sale.lastrowid, method, amount))
            return receipt

    def return_items(self, receipt: str, items: dict[str, int], restock: bool = True) -> int:
        if not items or any(quantity <= 0 for quantity in items.values()):
            raise BusinessRuleError("La devolución debe incluir cantidades positivas.")
        with self.repository.transaction():
            sale = _require(self.repository.one("SELECT * FROM sales WHERE receipt=?", (receipt,)), "No existe una venta con ese comprobante.")
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
            returned_at = _now()
            result = self.repository.execute("INSERT INTO returns(sale_id,returned_at,total_cents) VALUES(?,?,?)", (sale["id"], returned_at, total))
            return_id = result.lastrowid
            for line, quantity in prepared:
                self.repository.execute("UPDATE sale_lines SET returned_quantity=returned_quantity+? WHERE id=?", (quantity, line["id"]))
                self.repository.execute("INSERT INTO return_lines(return_id,sale_line_id,quantity,restocked) VALUES(?,?,?,?)", (return_id, line["id"], quantity, int(restock)))
                if restock:
                    self.repository.execute(
                        """INSERT INTO stock(location_id,product_id,quantity) VALUES(?,?,?)
                           ON CONFLICT(location_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                        (sale["location_id"], line["product_id"], quantity),
                    )
                    self.repository.execute(
                        "INSERT INTO inventory_movements(occurred_at,location_id,product_id,quantity_delta,movement_type,reference) VALUES(?,?,?,?,?,?)",
                        (returned_at, sale["location_id"], line["product_id"], quantity, "devolución apta", receipt),
                    )
                else:
                    self.repository.execute(
                        "INSERT INTO inventory_movements(occurred_at,location_id,product_id,quantity_delta,movement_type,reference) VALUES(?,?,?,?,?,?)",
                        (returned_at, sale["location_id"], line["product_id"], 0, "merma devolución", receipt),
                    )
            original_payments = self.repository.all("SELECT method,amount_cents FROM payments WHERE sale_id=? ORDER BY id", (sale["id"],))
            allocated = 0
            for index, payment in enumerate(original_payments):
                amount = total - allocated if index == len(original_payments) - 1 else total * payment["amount_cents"] // sale["total_cents"]
                if amount:
                    self.repository.execute("INSERT INTO return_payments(return_id,method,amount_cents) VALUES(?,?,?)", (return_id, payment["method"], amount))
                    allocated += amount
            return total