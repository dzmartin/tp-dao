from __future__ import annotations

import sqlite3
from .domain import ReglaNegocioError


class Repositorio:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection


class RepositorioEstablecimiento(Repositorio):
    def guardar(self, code: str, name: str, kind: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO establishments(code, name, type) VALUES (?, ?, ?)",
            (code, name, kind),
        )
        establishment_id = int(cursor.lastrowid)
        self.connection.execute(
            """INSERT INTO inventory(
                   establishment_id, product_id, quantity, minimum_stock
               )
               SELECT ?, id, 0, 0 FROM products""",
            (establishment_id,),
        )
        return establishment_id

    def buscar(self, establishment_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM establishments WHERE id = ?", (establishment_id,)
        ).fetchone()

    def por_codigo(self, code: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM establishments WHERE code = ?", (code,)
        ).fetchone()


class RepositorioCatalogo(Repositorio):
    def crear_proveedor(self, tax_id: str, name: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO suppliers(tax_id, name) VALUES (?, ?)", (tax_id, name)
        )
        return int(cursor.lastrowid)

    def crear_producto(
        self,
        sku: str,
        name: str,
        category: str,
        cost_cents: int,
        price_cents: int,
        supplier_ids: tuple[int, ...],
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO categories(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (category,),
        )
        category_row = self.connection.execute(
            "SELECT id FROM categories WHERE name = ?", (category,)
        ).fetchone()
        if category_row is None:
            raise RuntimeError("No se pudo recuperar la categoría recién guardada.")
        product_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(products)").fetchall()
        }
        if "minimum_stock" in product_columns:
            cursor = self.connection.execute(
                """INSERT INTO products(
                       sku, name, category_id, cost_cents, price_cents, minimum_stock
                   ) VALUES (?, ?, ?, ?, ?, 0)""",
                (sku, name, int(category_row["id"]), cost_cents, price_cents),
            )
        else:
            cursor = self.connection.execute(
                """INSERT INTO products(
                       sku, name, category_id, cost_cents, price_cents
                   ) VALUES (?, ?, ?, ?, ?)""",
                (sku, name, int(category_row["id"]), cost_cents, price_cents),
            )
        product_id = int(cursor.lastrowid)
        self.connection.execute(
            """INSERT INTO inventory(
                   establishment_id, product_id, quantity, minimum_stock
               )
               SELECT id, ?, 0, 0 FROM establishments""",
            (product_id,),
        )
        for supplier_id in supplier_ids:
            self.connection.execute(
                "INSERT INTO product_suppliers(product_id, supplier_id) VALUES (?, ?)",
                (product_id, supplier_id),
            )
        return product_id

    def producto(self, sku: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT p.*, c.name AS category
               FROM products p
               JOIN categories c ON c.id = p.category_id
               WHERE p.sku = ?""",
            (sku,),
        ).fetchone()

    def producto_por_id(self, product_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()

    def proveedor(self, supplier_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM suppliers WHERE id = ?", (supplier_id,)
        ).fetchone()


class RepositorioExistencia(Repositorio):
    def cantidad(self, establishment_id: int, product_id: int) -> int:
        row = self.connection.execute(
            """SELECT quantity FROM inventory
               WHERE establishment_id = ? AND product_id = ?""",
            (establishment_id, product_id),
        ).fetchone()
        return int(row["quantity"]) if row else 0

    def cambiar(
        self,
        establishment_id: int,
        product_id: int,
        delta: int,
        movement_type: str,
        cost_cents: int,
        reference: str,
        occurred_at: str,
    ) -> None:
        updated = self.connection.execute(
            """UPDATE inventory SET quantity = quantity + ?
               WHERE establishment_id = ? AND product_id = ?""",
            (delta, establishment_id, product_id),
        )
        if updated.rowcount == 0:
            if delta < 0:
                raise ReglaNegocioError("No hay stock disponible para este movimiento.")
            self.connection.execute(
                """INSERT INTO inventory(
                       establishment_id, product_id, quantity, minimum_stock
                   ) VALUES (?, ?, ?, 0)""",
                (establishment_id, product_id, delta),
            )
        self.connection.execute(
            """INSERT INTO inventory_movements(
                establishment_id, product_id, movement_type, quantity_delta,
                unit_cost_cents, reference, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                establishment_id,
                product_id,
                movement_type,
                delta,
                cost_cents,
                reference,
                occurred_at,
            ),
        )

    def establecer_minimo(
        self, establishment_id: int, product_id: int, minimum_stock: int
    ) -> None:
        updated = self.connection.execute(
            """UPDATE inventory SET minimum_stock = ?
               WHERE establishment_id = ? AND product_id = ?""",
            (minimum_stock, establishment_id, product_id),
        )
        if updated.rowcount == 0:
            self.connection.execute(
                """INSERT INTO inventory(
                       establishment_id, product_id, quantity, minimum_stock
                   ) VALUES (?, ?, 0, ?)""",
                (establishment_id, product_id, minimum_stock),
            )

    def stock_bajo(self, establishment_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT ? AS establishment_id, p.sku, p.name,
                          COALESCE(e.quantity, 0) AS quantity,
                          COALESCE(e.minimum_stock, 0) AS minimum_stock,
                          p.cost_cents
                   FROM products p
                   LEFT JOIN inventory e
                     ON e.product_id = p.id AND e.establishment_id = ?
                   WHERE COALESCE(e.quantity, 0) <= COALESCE(e.minimum_stock, 0)
                   ORDER BY p.sku""",
                (establishment_id, establishment_id),
            ).fetchall()
        )

    def existencias(self, establishment_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT e.quantity, e.minimum_stock, p.sku, p.name,
                          p.cost_cents
                   FROM inventory e JOIN products p ON p.id = e.product_id
                   WHERE e.establishment_id = ? ORDER BY p.sku""",
                (establishment_id,),
            ).fetchall()
        )


class RepositorioReposicion(Repositorio):
    def crear_lote(self, created_at: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO replenishment_batches(created_at) VALUES (?)",
            (created_at,),
        )
        return int(cursor.lastrowid)

    def crear_orden(
        self,
        batch_id: int,
        source_id: int,
        destination_id: int,
        created_at: str,
    ) -> int:
        cursor = self.connection.execute(
            """INSERT INTO replenishment_orders(
                batch_id, source_id, destination_id, created_at
            ) VALUES (?, ?, ?, ?)""",
            (batch_id, source_id, destination_id, created_at),
        )
        return int(cursor.lastrowid)

    def agregar_linea(self, order_id: int, product_id: int, quantity: int) -> None:
        self.connection.execute(
            """INSERT INTO replenishment_lines(order_id, product_id, requested)
               VALUES (?, ?, ?)""",
            (order_id, product_id, quantity),
        )

    def orden(self, order_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT o.*, source.type AS source_type, destination.type AS destination_type
               FROM replenishment_orders o
               JOIN establishments source ON source.id = o.source_id
               JOIN establishments destination ON destination.id = o.destination_id
               WHERE o.id = ?""",
            (order_id,),
        ).fetchone()

    def lineas(self, order_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT l.*, p.sku, p.cost_cents, p.name
                   FROM replenishment_lines l
                   JOIN products p ON p.id = l.product_id
                   WHERE l.order_id = ? ORDER BY p.sku""",
                (order_id,),
            ).fetchall()
        )


class RepositorioBulto(Repositorio):
    def crear(
        self,
        order_id: int,
        dispatch_note: str,
        seal: str,
        prepared_at: str,
    ) -> int:
        cursor = self.connection.execute(
            """INSERT INTO packages(
                order_id, dispatch_note, seal, status, prepared_at
            ) VALUES (?, ?, ?, 'Preparado', ?)""",
            (order_id, dispatch_note, seal, prepared_at),
        )
        return int(cursor.lastrowid)

    def agregar_linea(self, package_id: int, product_id: int, quantity: int) -> None:
        self.connection.execute(
            "INSERT INTO package_lines(package_id, product_id, quantity) VALUES (?, ?, ?)",
            (package_id, product_id, quantity),
        )

    def buscar(self, package_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT p.*, o.source_id, o.destination_id, o.status AS order_status,
                      o.request_id
               FROM packages p JOIN replenishment_orders o ON o.id = p.order_id
               WHERE p.id = ?""",
            (package_id,),
        ).fetchone()

    def lineas(self, package_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT pl.*, p.sku, p.cost_cents
                   FROM package_lines pl JOIN products p ON p.id = pl.product_id
                   WHERE pl.package_id = ? ORDER BY p.sku""",
                (package_id,),
            ).fetchall()
        )

    def cantidad_enviada(self, order_id: int, product_id: int) -> int:
        row = self.connection.execute(
            """SELECT COALESCE(SUM(pl.quantity), 0) AS quantity
               FROM packages p JOIN package_lines pl ON pl.package_id = p.id
               WHERE p.order_id = ?
                 AND p.status NOT IN ('Rechazado', 'Cancelado')
                 AND pl.product_id = ?""",
            (order_id, product_id),
        ).fetchone()
        return int(row["quantity"])

    def cantidad_reservada(self, source_id: int, product_id: int) -> int:
        row = self.connection.execute(
            """SELECT COALESCE(SUM(pl.quantity), 0) AS quantity
               FROM package_lines pl
               JOIN packages p ON p.id = pl.package_id
               JOIN replenishment_orders o ON o.id = p.order_id
               WHERE o.source_id = ? AND pl.product_id = ?
                 AND p.status IN ('Preparado', 'Precintado')""",
            (source_id, product_id),
        ).fetchone()
        return int(row["quantity"])

    def cambiar_estado(
        self,
        package_id: int,
        state: str,
        occurred_at: str,
        detail: str,
    ) -> None:
        timestamp_column = {
            "En tránsito": "dispatched_at",
            "Recibido": "received_at",
            "Verificado": "verified_at",
        }.get(state)
        if timestamp_column is None:
            self.connection.execute(
                "UPDATE packages SET status = ? WHERE id = ?", (state, package_id)
            )
        else:
            self.connection.execute(
                f"UPDATE packages SET status = ?, {timestamp_column} = ? WHERE id = ?",
                (state, occurred_at, package_id),
            )
        self.connection.execute(
            """INSERT INTO package_events(package_id, state, occurred_at, detail)
               VALUES (?, ?, ?, ?)""",
            (package_id, state, occurred_at, detail),
        )

    def eventos(self, package_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT state, occurred_at, detail FROM package_events
                   WHERE package_id = ? ORDER BY id""",
                (package_id,),
            ).fetchall()
        )


class RepositorioVenta(Repositorio):
    def crear(
        self,
        establishment_id: int,
        sold_at: str,
        receipt: str,
        total_cents: int,
        lines: list[dict[str, int]],
        payments: list[tuple[str, int]],
    ) -> int:
        cursor = self.connection.execute(
            """INSERT INTO sales(establishment_id, sold_at, status, receipt, total_cents)
               VALUES (?, ?, 'Confirmada', ?, ?)""",
            (establishment_id, sold_at, receipt, total_cents),
        )
        sale_id = int(cursor.lastrowid)
        for line in lines:
            self.connection.execute(
                """INSERT INTO sale_lines(
                    sale_id, product_id, quantity, unit_price_cents, unit_cost_cents
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    sale_id,
                    line["product_id"],
                    line["quantity"],
                    line["unit_price_cents"],
                    line["unit_cost_cents"],
                ),
            )
        for method, amount in payments:
            self.connection.execute(
                """INSERT INTO payments(sale_id, method, amount_cents, kind, occurred_at)
                   VALUES (?, ?, ?, 'cobro', ?)""",
                (sale_id, method, amount, sold_at),
            )
        return sale_id

    def venta(self, sale_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sales WHERE id = ?", (sale_id,)
        ).fetchone()

    def lineas(self, sale_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT sl.*, p.sku FROM sale_lines sl
                   JOIN products p ON p.id = sl.product_id
                   WHERE sl.sale_id = ? ORDER BY sl.id""",
                (sale_id,),
            ).fetchall()
        )


class RepositorioProveedor(Repositorio):
    def buscar(self, supplier_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM suppliers WHERE id = ?", (supplier_id,)
        ).fetchone()


class RepositorioMovimientoInventario(Repositorio):
    def listar(self, establishment_id: int) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT m.*, p.sku FROM inventory_movements m
                   JOIN products p ON p.id = m.product_id
                   WHERE m.establishment_id = ? ORDER BY m.id""",
                (establishment_id,),
            ).fetchall()
        )
