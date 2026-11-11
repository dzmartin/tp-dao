from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Iterable

from .domain import (
    DevolucionRegistrada,
    Establecimiento,
    EstadoBulto,
    EventoBulto,
    ReglaNegocioError,
    TipoEstablecimiento,
    TipoMovimiento,
    VentaRegistrada,
)
from .patterns import (
    FabricaEstablecimientos,
    MaquinaEstadosBulto,
    PublicadorEventos,
    RegistroEstrategiasPago,
)
from .repositories import (
    RepositorioBulto,
    RepositorioCatalogo,
    RepositorioEstablecimiento,
    RepositorioExistencia,
    RepositorioReposicion,
    RepositorioVenta,
)
from .storage import BaseDeDatos


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _nombre_requerido(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ReglaNegocioError(f"{field} no puede estar vacío.")
    return value


class ServicioCatalogo:
    def __init__(self, db: BaseDeDatos) -> None:
        self.db = db

    def crear_establecimiento(
        self, codigo: str, nombre: str, tipo: TipoEstablecimiento | str
    ) -> Establecimiento:
        kind = TipoEstablecimiento(tipo)
        code = _nombre_requerido(codigo, "El código")
        name = _nombre_requerido(nombre, "El nombre")
        with self.db.transaccion() as connection:
            establishment_id = RepositorioEstablecimiento(connection).guardar(
                code, name, kind.value
            )
        return FabricaEstablecimientos.crear(establishment_id, code, name, kind)

    def obtener_establecimiento(self, establishment_id: int) -> Establecimiento:
        row = RepositorioEstablecimiento(self.db.connection).buscar(establishment_id)
        if row is None:
            raise ReglaNegocioError(f"No existe el establecimiento {establishment_id}.")
        return FabricaEstablecimientos.crear(
            int(row["id"]),
            str(row["code"]),
            str(row["name"]),
            TipoEstablecimiento(row["type"]),
        )

    def crear_proveedor(self, identificador_fiscal: str, nombre: str) -> int:
        tax_id = _nombre_requerido(identificador_fiscal, "El identificador fiscal")
        name = _nombre_requerido(nombre, "El nombre")
        with self.db.transaccion() as connection:
            return RepositorioCatalogo(connection).crear_proveedor(tax_id, name)

    def crear_producto(
        self,
        sku: str,
        nombre: str,
        categoria: str,
        costo_centavos: int,
        precio_centavos: int,
        proveedor_ids: int | Iterable[int],
    ) -> int:
        sku = _nombre_requerido(sku, "El SKU")
        name = _nombre_requerido(nombre, "El nombre")
        category = _nombre_requerido(categoria, "La categoría")
        supplier_ids = (
            (proveedor_ids,)
            if isinstance(proveedor_ids, int)
            else tuple(dict.fromkeys(proveedor_ids))
        )
        if not supplier_ids:
            raise ReglaNegocioError("El producto debe tener al menos un proveedor.")
        if costo_centavos < 0 or precio_centavos <= 0:
            raise ReglaNegocioError(
                "El costo no puede ser negativo y el precio debe ser positivo."
            )
        with self.db.transaccion() as connection:
            suppliers = RepositorioCatalogo(connection)
            for supplier_id in supplier_ids:
                if suppliers.proveedor(supplier_id) is None:
                    raise ReglaNegocioError(f"No existe el proveedor {supplier_id}.")
            return suppliers.crear_producto(
                sku,
                name,
                category,
                costo_centavos,
                precio_centavos,
                supplier_ids,
            )

    def actualizar_precio(self, sku: str, precio_centavos: int) -> None:
        if precio_centavos <= 0:
            raise ReglaNegocioError("El precio debe ser positivo.")
        with self.db.transaccion() as connection:
            product = RepositorioCatalogo(connection).producto(sku)
            if product is None:
                raise ReglaNegocioError(f"No existe el producto {sku}.")
            connection.execute(
                "UPDATE products SET price_cents = ? WHERE id = ?",
                (precio_centavos, int(product["id"])),
            )

    def actualizar_producto(
        self,
        sku: str,
        nombre: str,
        categoria: str,
        costo_centavos: int,
        precio_centavos: int,
        proveedor_ids: int | Iterable[int],
    ) -> None:
        sku = _nombre_requerido(sku, "El SKU")
        name = _nombre_requerido(nombre, "El nombre")
        category = _nombre_requerido(categoria, "La categoría")
        supplier_ids = (
            (proveedor_ids,)
            if isinstance(proveedor_ids, int)
            else tuple(dict.fromkeys(proveedor_ids))
        )
        if not supplier_ids:
            raise ReglaNegocioError("El producto debe tener al menos un proveedor.")
        if costo_centavos < 0 or precio_centavos <= 0:
            raise ReglaNegocioError(
                "El costo no puede ser negativo y el precio debe ser positivo."
            )
        with self.db.transaccion() as connection:
            catalog = RepositorioCatalogo(connection)
            product = catalog.producto(sku)
            if product is None:
                raise ReglaNegocioError(f"No existe el producto {sku}.")
            for supplier_id in supplier_ids:
                if catalog.proveedor(supplier_id) is None:
                    raise ReglaNegocioError(f"No existe el proveedor {supplier_id}.")
            connection.execute(
                "INSERT INTO categories(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
                (category,),
            )
            category_row = connection.execute(
                "SELECT id FROM categories WHERE name = ?", (category,)
            ).fetchone()
            if category_row is None:
                raise RuntimeError("No se pudo recuperar la categoría actualizada.")
            product_id = int(product["id"])
            connection.execute(
                """UPDATE products
                   SET name = ?, category_id = ?, cost_cents = ?,
                       price_cents = ?
                   WHERE id = ?""",
                (
                    name,
                    int(category_row["id"]),
                    costo_centavos,
                    precio_centavos,
                    product_id,
                ),
            )
            connection.execute(
                "DELETE FROM product_suppliers WHERE product_id = ?", (product_id,)
            )
            connection.executemany(
                "INSERT INTO product_suppliers(product_id, supplier_id) VALUES (?, ?)",
                ((product_id, supplier_id) for supplier_id in supplier_ids),
            )


class ServicioInventario:
    def __init__(self, db: BaseDeDatos) -> None:
        self.db = db

    def disponible(self, establecimiento_id: int, sku: str) -> int:
        if RepositorioEstablecimiento(self.db.connection).buscar(establecimiento_id) is None:
            raise ReglaNegocioError(
                f"No existe el establecimiento {establecimiento_id}."
            )
        product = RepositorioCatalogo(self.db.connection).producto(sku)
        if product is None:
            raise ReglaNegocioError(f"No existe el producto {sku}.")
        return RepositorioExistencia(self.db.connection).cantidad(
            establecimiento_id, int(product["id"])
        )

    def configurar_stock_minimo(
        self, establecimiento_id: int, sku: str, stock_minimo: int
    ) -> None:
        if stock_minimo < 0:
            raise ReglaNegocioError("El stock mínimo no puede ser negativo.")
        with self.db.transaccion() as connection:
            if RepositorioEstablecimiento(connection).buscar(establecimiento_id) is None:
                raise ReglaNegocioError(
                    f"No existe el establecimiento {establecimiento_id}."
                )
            product = RepositorioCatalogo(connection).producto(sku)
            if product is None:
                raise ReglaNegocioError(f"No existe el producto {sku}.")
            RepositorioExistencia(connection).establecer_minimo(
                establecimiento_id, int(product["id"]), stock_minimo
            )

    def ajustar_stock_centro(
        self, establecimiento_id: int, sku: str, variacion: int, referencia: str
    ) -> None:
        if variacion == 0:
            raise ReglaNegocioError("El ajuste de stock debe ser distinto de cero.")
        reference = _nombre_requerido(referencia, "La referencia")
        with self.db.transaccion() as connection:
            establishments = RepositorioEstablecimiento(connection)
            establishment = establishments.buscar(establecimiento_id)
            if establishment is None:
                raise ReglaNegocioError(
                    f"No existe el establecimiento {establecimiento_id}."
                )
            if establishment["type"] != TipoEstablecimiento.CENTRO_DISTRIBUCION:
                raise ReglaNegocioError(
                    "Los ajustes manuales de stock solo se permiten en el centro de distribución."
                )
            product = RepositorioCatalogo(connection).producto(sku)
            if product is None:
                raise ReglaNegocioError(f"No existe el producto {sku}.")
            inventory = RepositorioExistencia(connection)
            current = inventory.cantidad(establecimiento_id, int(product["id"]))
            if current + variacion < 0:
                raise ReglaNegocioError("El ajuste dejaría el stock por debajo de cero.")
            if variacion < 0:
                reserved = RepositorioBulto(connection).cantidad_reservada(
                    establecimiento_id, int(product["id"])
                )
                if current + variacion < reserved:
                    raise ReglaNegocioError(
                        f"No se puede reducir el stock por debajo de las "
                        f"{reserved} unidades reservadas en envíos preparados. "
                        "Cancelá o despachá esos envíos primero."
                    )
            inventory.cambiar(
                establecimiento_id,
                int(product["id"]),
                variacion,
                TipoMovimiento.AJUSTE.value,
                int(product["cost_cents"]),
                reference,
                ahora(),
            )

    def listar_stock_bajo(self, establecimiento_id: int) -> list[dict[str, object]]:
        if RepositorioEstablecimiento(self.db.connection).buscar(establecimiento_id) is None:
            raise ReglaNegocioError(
                f"No existe el establecimiento {establecimiento_id}."
            )
        return [
            dict(row)
            for row in RepositorioExistencia(self.db.connection).stock_bajo(
                establecimiento_id
            )
        ]


class ServicioReposicion:
    def __init__(self, db: BaseDeDatos) -> None:
        self.db = db

    def consolidar_necesidades(
        self, centro_id: int, necesidades: dict[int, dict[str, int]]
    ) -> tuple[int, dict[int, int]]:
        if not necesidades:
            raise ReglaNegocioError("Debe incluirse al menos una sucursal.")
        with self.db.transaccion() as connection:
            establishments = RepositorioEstablecimiento(connection)
            center = establishments.buscar(centro_id)
            if center is None or center["type"] != TipoEstablecimiento.CENTRO_DISTRIBUCION:
                raise ReglaNegocioError("El origen debe ser un centro de distribución.")
            catalog = RepositorioCatalogo(connection)
            repos = RepositorioReposicion(connection)
            timestamp = ahora()
            batch_id = repos.crear_lote(timestamp)
            orders: dict[int, int] = {}
            for destination_id, demand in necesidades.items():
                destination = establishments.buscar(destination_id)
                if (
                    destination is None
                    or destination["type"] != TipoEstablecimiento.SUCURSAL
                ):
                    raise ReglaNegocioError(
                        f"El destino {destination_id} no es una sucursal válida."
                    )
                if not demand:
                    raise ReglaNegocioError(
                        f"La solicitud de la sucursal {destination_id} está vacía."
                    )
                order_id = repos.crear_orden(
                    batch_id, centro_id, destination_id, timestamp
                )
                orders[destination_id] = order_id
                for sku, quantity in demand.items():
                    if quantity <= 0:
                        raise ReglaNegocioError(
                            f"La cantidad solicitada para {sku} debe ser positiva."
                        )
                    product = catalog.producto(sku)
                    if product is None:
                        raise ReglaNegocioError(f"No existe el producto {sku}.")
                    repos.agregar_linea(
                        order_id, int(product["id"]), int(quantity)
                    )
            return batch_id, orders


class AcreditadorExistencias:
    def __init__(self, db: BaseDeDatos) -> None:
        self.db = db

    def actualizar(self, event: EventoBulto) -> None:
        if event.estado not in (EstadoBulto.EN_TRANSITO, EstadoBulto.VERIFICADO):
            return
        connection = self.db.connection
        package = RepositorioBulto(connection).buscar(event.bulto_id)
        if package is None:
            raise ReglaNegocioError(f"No existe el bulto {event.bulto_id}.")
        repo = RepositorioExistencia(connection)
        for line in RepositorioBulto(connection).lineas(event.bulto_id):
            delta = -int(line["quantity"]) if event.estado is EstadoBulto.EN_TRANSITO else int(line["quantity"])
            location_id = (
                int(package["source_id"])
                if event.estado is EstadoBulto.EN_TRANSITO
                else int(package["destination_id"])
            )
            repo.cambiar(
                location_id,
                int(line["product_id"]),
                delta,
                (
                    TipoMovimiento.DESPACHO.value
                    if event.estado is EstadoBulto.EN_TRANSITO
                    else TipoMovimiento.RECEPCION.value
                ),
                int(line["cost_cents"]),
                f"bulto:{event.bulto_id}",
                event.fecha,
            )


class AuditorLogistico:
    def __init__(self, db: BaseDeDatos) -> None:
        self.db = db

    def actualizar(self, event: EventoBulto) -> None:
        self.db.connection.execute(
            """INSERT INTO logistics_audit(package_id, occurred_at, state, detail)
               VALUES (?, ?, ?, ?)""",
            (event.bulto_id, event.fecha, event.estado.value, event.detalle),
        )


class ServicioLogistica:
    def __init__(self, db: BaseDeDatos, event_publisher: PublicadorEventos) -> None:
        self.db = db
        self.event_publisher = event_publisher

    def preparar_bulto(
        self,
        orden_id: int,
        remito: str,
        precinto: str,
        lineas: dict[str, int],
    ) -> int:
        note = _nombre_requerido(remito, "El remito")
        seal = _nombre_requerido(precinto, "El precinto")
        if not lineas:
            raise ReglaNegocioError("El bulto debe contener al menos un producto.")
        with self.db.transaccion() as connection:
            orders = RepositorioReposicion(connection)
            order = orders.orden(orden_id)
            if order is None:
                raise ReglaNegocioError(f"No existe la orden {orden_id}.")
            if order["status"] == "Completada":
                raise ReglaNegocioError("La orden no admite más bultos.")
            packages = RepositorioBulto(connection)
            catalog = RepositorioCatalogo(connection)
            inventory = RepositorioExistencia(connection)
            package_id = packages.crear(orden_id, note, seal, ahora())
            order_lines = {int(row["product_id"]): row for row in orders.lineas(orden_id)}
            for sku, quantity in lineas.items():
                if quantity <= 0:
                    raise ReglaNegocioError(
                        f"La cantidad del producto {sku} debe ser positiva."
                    )
                product = catalog.producto(sku)
                if product is None:
                    raise ReglaNegocioError(f"No existe el producto {sku}.")
                product_id = int(product["id"])
                order_line = order_lines.get(product_id)
                if order_line is None:
                    raise ReglaNegocioError(
                        f"El producto {sku} no pertenece a la orden {orden_id}."
                    )
                previous = packages.cantidad_enviada(orden_id, product_id)
                if previous + quantity > int(order_line["requested"]):
                    raise ReglaNegocioError(
                        f"Los bultos superarían la cantidad pedida de {sku}."
                    )
                available = inventory.cantidad(int(order["source_id"]), product_id)
                reserved = packages.cantidad_reservada(
                    int(order["source_id"]), product_id
                )
                available_to_prepare = max(0, available - reserved)
                if quantity > available_to_prepare:
                    raise ReglaNegocioError(
                        f"Stock insuficiente en el centro para preparar {sku}: "
                        f"hay {available_to_prepare} unidades disponibles y se "
                        f"solicitaron {quantity}."
                    )
                packages.agregar_linea(package_id, product_id, quantity)
            self._transition(
                packages,
                package_id,
                EstadoBulto.PREPARADO,
                "Bulto preparado.",
            )
            return package_id

    def precintar(self, bulto_id: int) -> None:
        self._advance(bulto_id, "Precinto colocado.")

    def despachar(self, bulto_id: int) -> None:
        with self.db.transaccion() as connection:
            package_repo = RepositorioBulto(connection)
            package = self._get_package(package_repo, bulto_id)
            self._ensure_state(package, EstadoBulto.PRECINTADO)
            inventory = RepositorioExistencia(connection)
            for line in package_repo.lineas(bulto_id):
                available = inventory.cantidad(
                    int(package["source_id"]), int(line["product_id"])
                )
                if available < int(line["quantity"]):
                    raise ReglaNegocioError(
                        f"Stock insuficiente en el centro para {line['sku']}."
                    )
            self._transition(
                package_repo,
                bulto_id,
                EstadoBulto.EN_TRANSITO,
                "Bulto despachado desde el centro de distribución.",
            )
            connection.execute(
                "UPDATE replenishment_orders SET status = 'En tránsito' WHERE id = ?",
                (int(package["order_id"]),),
            )

    def cancelar_preparacion(self, bulto_id: int) -> None:
        with self.db.transaccion() as connection:
            package_repo = RepositorioBulto(connection)
            package = self._get_package(package_repo, bulto_id)
            current_state = EstadoBulto(package["status"])
            if current_state not in (EstadoBulto.PREPARADO, EstadoBulto.PRECINTADO):
                raise ReglaNegocioError(
                    "Solo se pueden cancelar envíos preparados o precintados "
                    "que todavía no salieron del centro."
                )
            order_id = int(package["order_id"])
            request_id = package["request_id"]
            if request_id is not None:
                for line in package_repo.lineas(bulto_id):
                    updated_line = connection.execute(
                        """UPDATE replenishment_request_lines
                           SET fulfilled = fulfilled - ?
                           WHERE request_id = ? AND product_id = ? AND fulfilled >= ?""",
                        (
                            int(line["quantity"]),
                            int(request_id),
                            int(line["product_id"]),
                            int(line["quantity"]),
                        ),
                    )
                    if updated_line.rowcount != 1:
                        raise ReglaNegocioError(
                            f"No se pudo liberar la cantidad de {line['sku']} "
                            "del pedido original."
                        )
                connection.execute(
                    """UPDATE replenishment_requests
                       SET status = 'Pendiente',
                           order_id = CASE WHEN order_id = ? THEN NULL ELSE order_id END
                       WHERE id = ?""",
                    (order_id, int(request_id)),
                )
            self._transition(
                package_repo,
                bulto_id,
                EstadoBulto.CANCELADO,
                "Envío cancelado antes de salir del centro; cantidades liberadas.",
            )
            active_packages = int(
                connection.execute(
                    """SELECT COUNT(*) AS count FROM packages
                       WHERE order_id = ? AND status != 'Cancelado'""",
                    (order_id,),
                ).fetchone()["count"]
            )
            if active_packages == 0:
                connection.execute(
                    "UPDATE replenishment_orders SET status = 'Cancelada' WHERE id = ?",
                    (order_id,),
                )

    def recibir(self, bulto_id: int) -> None:
        self._advance(bulto_id, "Bulto recibido en la sucursal; pendiente de verificación.")

    def verificar_recepcion(
        self, bulto_id: int, intacto: bool, coincide_remito: bool
    ) -> EstadoBulto:
        with self.db.transaccion() as connection:
            package_repo = RepositorioBulto(connection)
            package = self._get_package(package_repo, bulto_id)
            self._ensure_state(package, EstadoBulto.RECIBIDO)
            next_state = MaquinaEstadosBulto.siguiente(
                EstadoBulto(package["status"]), intacto, coincide_remito
            )
            if not coincide_remito:
                detail = "El contenido no coincide con el remito; bulto rechazado."
            elif intacto:
                detail = (
                    "El contenido coincide con el remito y el precinto está intacto; "
                    "bulto verificado."
                )
            else:
                detail = (
                    "El contenido coincide con el remito, pero el precinto está "
                    "violado; bulto rechazado."
                )
            self._transition(package_repo, bulto_id, next_state, detail)
            order_lines = RepositorioReposicion(connection).lineas(
                int(package["order_id"])
            )
            all_verified = all(
                int(
                    connection.execute(
                        """SELECT COALESCE(SUM(pl.quantity), 0) AS quantity
                           FROM packages p JOIN package_lines pl ON pl.package_id = p.id
                           WHERE p.order_id = ? AND p.status = 'Verificado'
                             AND pl.product_id = ?""",
                        (
                            int(package["order_id"]),
                            int(line["product_id"]),
                        ),
                    ).fetchone()["quantity"]
                )
                >= int(line["requested"])
                for line in order_lines
            )
            connection.execute(
                "UPDATE replenishment_orders SET status = ? WHERE id = ?",
                (
                    "Completada" if all_verified else "Pendiente",
                    int(package["order_id"]),
                ),
            )
            return next_state

    def historial(self, bulto_id: int) -> list[dict[str, object]]:
        if RepositorioBulto(self.db.connection).buscar(bulto_id) is None:
            raise ReglaNegocioError(f"No existe el bulto {bulto_id}.")
        return [
            dict(row)
            for row in RepositorioBulto(self.db.connection).eventos(bulto_id)
        ]

    def _advance(self, bulto_id: int, detail: str) -> None:
        with self.db.transaccion() as connection:
            repo = RepositorioBulto(connection)
            package = self._get_package(repo, bulto_id)
            current = EstadoBulto(package["status"])
            self._transition(repo, bulto_id, MaquinaEstadosBulto.siguiente(current), detail)

    def _transition(
        self,
        repo: RepositorioBulto,
        package_id: int,
        next_state: EstadoBulto,
        detail: str,
    ) -> None:
        timestamp = ahora()
        repo.cambiar_estado(package_id, next_state.value, timestamp, detail)
        event = EventoBulto(package_id, next_state, timestamp, detail)
        self.event_publisher.publicar(event)

    @staticmethod
    def _get_package(repo: RepositorioBulto, package_id: int) -> sqlite3.Row:
        package = repo.buscar(package_id)
        if package is None:
            raise ReglaNegocioError(f"No existe el bulto {package_id}.")
        return package

    @staticmethod
    def _ensure_state(package: sqlite3.Row, expected: EstadoBulto) -> None:
        actual = EstadoBulto(package["status"])
        if actual is not expected:
            raise ReglaNegocioError(
                f"Transición inválida: el bulto está en {actual}, se esperaba {expected}."
            )


class ServicioVentas:
    def __init__(self, db: BaseDeDatos, payment_strategies: RegistroEstrategiasPago) -> None:
        self.db = db
        self.payment_strategies = payment_strategies

    def registrar_venta(
        self,
        sucursal_id: int,
        productos: dict[str, int],
        pagos: dict[str, int],
    ) -> VentaRegistrada:
        if not productos or not pagos:
            raise ReglaNegocioError("La venta requiere productos y al menos un cobro.")
        with self.db.transaccion() as connection:
            establishment = RepositorioEstablecimiento(connection).buscar(sucursal_id)
            if (
                establishment is None
                or establishment["type"] != TipoEstablecimiento.SUCURSAL
            ):
                raise ReglaNegocioError("Las ventas solo pueden registrarse en sucursales.")
            inventory = RepositorioExistencia(connection)
            catalog = RepositorioCatalogo(connection)
            lines: list[dict[str, int]] = []
            total = 0
            for sku, quantity in productos.items():
                if quantity <= 0:
                    raise ReglaNegocioError(
                        f"La cantidad de {sku} debe ser positiva."
                    )
                product = catalog.producto(sku)
                if product is None:
                    raise ReglaNegocioError(f"No existe el producto {sku}.")
                product_id = int(product["id"])
                available = inventory.cantidad(sucursal_id, product_id)
                if quantity > available:
                    raise ReglaNegocioError(
                        f"Stock insuficiente para {sku}: hay {available} y se pidieron {quantity}."
                    )
                unit_price = int(product["price_cents"])
                total += unit_price * quantity
                lines.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "unit_price_cents": unit_price,
                        "unit_cost_cents": int(product["cost_cents"]),
                    }
                )
            strategy_pairs: list[tuple[str, int]] = []
            for method, amount in pagos.items():
                strategy = self.payment_strategies.obtener(method)
                strategy.procesar(amount)
                strategy_pairs.append((strategy.medio, amount))
            if sum(amount for _, amount in strategy_pairs) != total:
                raise ReglaNegocioError(
                    f"Los cobros deben sumar exactamente ${total / 100:.2f}."
                )
            timestamp = ahora()
            receipt = f"V-{uuid.uuid4().hex[:12].upper()}"
            sale_id = RepositorioVenta(connection).crear(
                sucursal_id, timestamp, receipt, total, lines, strategy_pairs
            )
            for line in lines:
                inventory.cambiar(
                    sucursal_id,
                    line["product_id"],
                    -line["quantity"],
                    TipoMovimiento.VENTA.value,
                    line["unit_cost_cents"],
                    f"venta:{sale_id}",
                    timestamp,
                )
            return VentaRegistrada(sale_id, receipt, total)

    def registrar_devolucion(
        self,
        venta_id: int,
        lineas: list[tuple[int, int, bool]],
        medio_reintegro: str = "efectivo",
    ) -> DevolucionRegistrada:
        if not lineas:
            raise ReglaNegocioError("La devolución debe incluir al menos una línea.")
        with self.db.transaccion() as connection:
            sale = RepositorioVenta(connection).venta(venta_id)
            if sale is None:
                raise ReglaNegocioError(f"No existe la venta {venta_id}.")
            if sale["status"] == "Devuelta":
                raise ReglaNegocioError("La venta ya fue devuelta completamente.")
            strategy = self.payment_strategies.obtener(medio_reintegro)
            timestamp = ahora()
            refund_total = 0
            validated: list[tuple[sqlite3.Row, int, bool, int]] = []
            seen: set[int] = set()
            for line_id, quantity, restock in lineas:
                if line_id in seen:
                    raise ReglaNegocioError(
                        "Cada línea de venta debe aparecer una sola vez en la devolución."
                    )
                seen.add(line_id)
                if quantity <= 0:
                    raise ReglaNegocioError(
                        "La cantidad devuelta debe ser positiva."
                    )
                sale_line = connection.execute(
                    """SELECT * FROM sale_lines WHERE id = ? AND sale_id = ?""",
                    (line_id, venta_id),
                ).fetchone()
                if sale_line is None:
                    raise ReglaNegocioError(
                        f"La línea {line_id} no pertenece a la venta {venta_id}."
                    )
                already_returned = connection.execute(
                    """SELECT COALESCE(SUM(rl.quantity), 0) AS quantity
                       FROM return_lines rl JOIN returns r ON r.id = rl.return_id
                       WHERE r.sale_id = ? AND rl.sale_line_id = ?""",
                    (venta_id, line_id),
                ).fetchone()
                remaining = int(sale_line["quantity"]) - int(already_returned["quantity"])
                if quantity > remaining:
                    raise ReglaNegocioError(
                        f"La cantidad supera las unidades todavía no devueltas de la línea {line_id}."
                    )
                amount = quantity * int(sale_line["unit_price_cents"])
                refund_total += amount
                validated.append((sale_line, quantity, restock, amount))
            strategy.procesar(refund_total)
            cursor = connection.execute(
                "INSERT INTO returns(sale_id, returned_at, amount_cents) VALUES (?, ?, ?)",
                (venta_id, timestamp, refund_total),
            )
            return_id = int(cursor.lastrowid)
            for sale_line, quantity, restock, _ in validated:
                connection.execute(
                    """INSERT INTO return_lines(return_id, sale_line_id, quantity, restock)
                       VALUES (?, ?, ?, ?)""",
                    (return_id, int(sale_line["id"]), quantity, int(restock)),
                )
                if restock:
                    RepositorioExistencia(connection).cambiar(
                        int(sale["establishment_id"]),
                        int(sale_line["product_id"]),
                        quantity,
                        TipoMovimiento.DEVOLUCION.value,
                        int(sale_line["unit_cost_cents"]),
                        f"devolucion:{return_id}",
                        timestamp,
                    )
            connection.execute(
                """INSERT INTO payments(sale_id, method, amount_cents, kind, occurred_at)
                   VALUES (?, ?, ?, 'reintegro', ?)""",
                (venta_id, strategy.medio, refund_total, timestamp),
            )
            returned = connection.execute(
                """SELECT COALESCE(SUM(rl.quantity), 0) AS quantity
                   FROM return_lines rl JOIN returns r ON r.id = rl.return_id
                   WHERE r.sale_id = ?""",
                (venta_id,),
            ).fetchone()
            sold = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS quantity FROM sale_lines WHERE sale_id = ?",
                (venta_id,),
            ).fetchone()
            status = (
                "Devuelta"
                if int(returned["quantity"]) == int(sold["quantity"])
                else "Devolución parcial"
            )
            connection.execute(
                "UPDATE sales SET status = ? WHERE id = ?", (status, venta_id)
            )
            return DevolucionRegistrada(return_id, refund_total, any(r for _, _, r, _ in validated))


class ServicioReportes:
    def __init__(self, db: BaseDeDatos) -> None:
        self.db = db

    @staticmethod
    def _periodo(desde: str, hasta: str) -> tuple[str, str]:
        try:
            start = date.fromisoformat(desde)
            end = date.fromisoformat(hasta)
        except ValueError as error:
            raise ReglaNegocioError("Las fechas deben tener formato AAAA-MM-DD.") from error
        if end < start:
            raise ReglaNegocioError("La fecha final no puede ser anterior a la inicial.")
        return f"{start.isoformat()}T00:00:00", f"{end.isoformat()}T23:59:59.999999"

    def ventas_e_ingresos(self, desde: str, hasta: str) -> list[dict[str, object]]:
        start, end = self._periodo(desde, hasta)
        rows = self.db.connection.execute(
            """SELECT e.name AS sucursal, date(p.occurred_at) AS fecha,
                      p.method AS medio,
                      SUM(CASE WHEN p.kind = 'cobro' THEN p.amount_cents
                               ELSE -p.amount_cents END) AS importe_neto_centavos
               FROM payments p JOIN sales s ON s.id = p.sale_id
               JOIN establishments e ON e.id = s.establishment_id
               WHERE p.occurred_at BETWEEN ? AND ?
               GROUP BY e.name, date(p.occurred_at), p.method
               ORDER BY fecha, sucursal, medio""",
            (start, end),
        ).fetchall()
        return [dict(row) for row in rows]

    def productos_y_margen(self, desde: str, hasta: str) -> list[dict[str, object]]:
        start, end = self._periodo(desde, hasta)
        rows = self.db.connection.execute(
            """SELECT p.sku, p.name,
                      SUM(sl.quantity) AS unidades_vendidas,
                      SUM(sl.quantity * sl.unit_price_cents) AS ingresos_centavos,
                      SUM(sl.quantity * sl.unit_cost_cents) AS costo_centavos,
                      SUM(sl.quantity * (sl.unit_price_cents - sl.unit_cost_cents))
                        - COALESCE((
                            SELECT SUM(rl.quantity * returned_line.unit_price_cents)
                            FROM return_lines rl
                            JOIN returns r ON r.id = rl.return_id
                            JOIN sale_lines returned_line ON returned_line.id = rl.sale_line_id
                            JOIN sales returned_sale ON returned_sale.id = returned_line.sale_id
                            WHERE returned_line.product_id = p.id
                              AND returned_sale.sold_at BETWEEN ? AND ?
                        ), 0)
                        + COALESCE((
                            SELECT SUM(rl.quantity * returned_line.unit_cost_cents)
                            FROM return_lines rl
                            JOIN returns r ON r.id = rl.return_id
                            JOIN sale_lines returned_line ON returned_line.id = rl.sale_line_id
                            JOIN sales returned_sale ON returned_sale.id = returned_line.sale_id
                            WHERE returned_line.product_id = p.id
                              AND returned_sale.sold_at BETWEEN ? AND ?
                              AND rl.restock = 1
                        ), 0) AS margen_estimado_centavos
               FROM sale_lines sl JOIN products p ON p.id = sl.product_id
               JOIN sales s ON s.id = sl.sale_id
               WHERE s.sold_at BETWEEN ? AND ?
               GROUP BY p.id, p.sku, p.name ORDER BY unidades_vendidas DESC, p.sku""",
            (start, end, start, end, start, end),
        ).fetchall()
        return [dict(row) for row in rows]

    def existencias_y_minimos(self) -> list[dict[str, object]]:
        rows = self.db.connection.execute(
            """SELECT e.name AS establecimiento, p.sku, p.name AS producto,
                      i.quantity AS existencia,
                      i.minimum_stock AS minimo,
                      i.quantity * p.cost_cents AS valorizacion_centavos
               FROM establishments e CROSS JOIN products p
               JOIN inventory i
                 ON i.establishment_id = e.id AND i.product_id = p.id
               ORDER BY e.name, p.sku"""
        ).fetchall()
        return [dict(row) for row in rows]

    def bultos_rechazados(self, desde: str, hasta: str) -> list[dict[str, object]]:
        start, end = self._periodo(desde, hasta)
        rows = self.db.connection.execute(
            """SELECT p.id AS bulto_id, p.dispatch_note AS remito, p.seal AS precinto,
                      e.name AS sucursal, a.occurred_at AS fecha_rechazo, a.detail AS detalle
               FROM logistics_audit a JOIN packages p ON p.id = a.package_id
               JOIN replenishment_orders o ON o.id = p.order_id
               JOIN establishments e ON e.id = o.destination_id
               WHERE a.state = ? AND a.occurred_at BETWEEN ? AND ?
               ORDER BY a.occurred_at""",
            (EstadoBulto.RECHAZADO.value, start, end),
        ).fetchall()
        return [dict(row) for row in rows]

    def transito_promedio(self, desde: str, hasta: str) -> list[dict[str, object]]:
        start, end = self._periodo(desde, hasta)
        rows = self.db.connection.execute(
            """SELECT source.name AS centro, destination.name AS sucursal,
                      COUNT(*) AS bultos,
                      AVG((julianday(p.received_at) - julianday(p.dispatched_at)) * 24)
                        AS horas_promedio
               FROM packages p JOIN replenishment_orders o ON o.id = p.order_id
               JOIN establishments source ON source.id = o.source_id
               JOIN establishments destination ON destination.id = o.destination_id
               WHERE p.dispatched_at BETWEEN ? AND ? AND p.received_at IS NOT NULL
               GROUP BY source.id, destination.id
               ORDER BY source.name, destination.name""",
            (start, end),
        ).fetchall()
        return [dict(row) for row in rows]

    def movimientos_y_valorizacion(
        self, establecimiento_id: int, desde: str, hasta: str
    ) -> dict[str, object]:
        start, end = self._periodo(desde, hasta)
        if RepositorioEstablecimiento(self.db.connection).buscar(establecimiento_id) is None:
            raise ReglaNegocioError(
                f"No existe el establecimiento {establecimiento_id}."
            )
        rows = self.db.connection.execute(
            """SELECT m.occurred_at AS fecha, m.movement_type AS tipo, p.sku,
                      m.quantity_delta AS variacion, m.unit_cost_cents AS costo_unitario_centavos,
                      m.quantity_delta * m.unit_cost_cents AS valor_variacion_centavos,
                      m.reference AS referencia
               FROM inventory_movements m JOIN products p ON p.id = m.product_id
               WHERE m.establishment_id = ? AND m.occurred_at BETWEEN ? AND ?
               ORDER BY m.occurred_at, m.id""",
            (establecimiento_id, start, end),
        ).fetchall()
        movement_list = [dict(row) for row in rows]
        return {
            "movimientos": movement_list,
            "variacion_valorizada_centavos": sum(
                int(row["valor_variacion_centavos"]) for row in movement_list
            ),
        }
