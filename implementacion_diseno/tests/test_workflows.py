import unittest
import sqlite3

from sistema import Sistema
from sistema.domain import EstadoBulto, ReglaNegocioError, TipoEstablecimiento


class FlujoSistemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = Sistema(":memory:")
        self.center = self.system.catalogo.crear_establecimiento(
            "CD-1", "Centro", TipoEstablecimiento.CENTRO_DISTRIBUCION
        )
        self.store = self.system.catalogo.crear_establecimiento(
            "LOC-1", "Sucursal", TipoEstablecimiento.SUCURSAL
        )
        self.supplier = self.system.catalogo.crear_proveedor("TAX-1", "Proveedor")
        self.system.catalogo.crear_producto(
            "SKU-1", "Producto", "General", 500, 1000, self.supplier
        )
        self.system.inventario.configurar_stock_minimo(
            self.center.id, "SKU-1", 2
        )
        self.system.inventario.ajustar_stock_centro(
            self.center.id, "SKU-1", 10, "Apertura"
        )

    def tearDown(self) -> None:
        self.system.cerrar()

    def _create_package(self, quantity: int = 5) -> int:
        _, orders = self.system.reposicion.consolidar_necesidades(
            self.center.id, {self.store.id: {"SKU-1": quantity}}
        )
        package_id = self.system.logistica.preparar_bulto(
            orders[self.store.id],
            f"REM-{orders[self.store.id]}",
            f"SEAL-{orders[self.store.id]}",
            {"SKU-1": quantity},
        )
        return package_id

    def _verify_package(self, package_id: int) -> None:
        self.system.logistica.precintar(package_id)
        self.system.logistica.despachar(package_id)
        self.system.logistica.recibir(package_id)
        self.assertEqual(
            self.system.logistica.verificar_recepcion(
                package_id, True, coincide_remito=True
            ),
            EstadoBulto.VERIFICADO,
        )

    def test_verified_package_adds_stock_and_sale_reduces_it(self) -> None:
        package_id = self._create_package()
        self._verify_package(package_id)
        self.assertEqual(self.system.inventario.disponible(self.store.id, "SKU-1"), 5)

        sale = self.system.ventas.registrar_venta(
            self.store.id,
            {"SKU-1": 2},
            {"efectivo": 1000, "tarjeta": 1000},
        )
        self.assertEqual(sale.total_centavos, 2000)
        self.assertEqual(self.system.inventario.disponible(self.store.id, "SKU-1"), 3)

    def test_broken_seal_does_not_make_stock_sellable(self) -> None:
        package_id = self._create_package()
        self.system.logistica.precintar(package_id)
        self.system.logistica.despachar(package_id)
        self.system.logistica.recibir(package_id)
        self.assertEqual(
            self.system.logistica.verificar_recepcion(
                package_id, False, coincide_remito=True
            ),
            EstadoBulto.RECHAZADO,
        )
        self.assertEqual(self.system.inventario.disponible(self.store.id, "SKU-1"), 0)
        self.assertEqual(len(self.system.logistica.historial(package_id)), 5)
        with self.assertRaises(ReglaNegocioError):
            self.system.ventas.registrar_venta(
                self.store.id, {"SKU-1": 1}, {"efectivo": 1000}
            )
        rejected = self.system.reportes.bultos_rechazados(
            "2000-01-01", "2100-01-01"
        )
        self.assertEqual(len(rejected), 1)

    def test_remito_mismatch_rejects_package_without_adding_stock(self) -> None:
        package_id = self._create_package()
        self.system.logistica.precintar(package_id)
        self.system.logistica.despachar(package_id)
        self.system.logistica.recibir(package_id)
        self.assertEqual(
            self.system.logistica.verificar_recepcion(
                package_id, intacto=True, coincide_remito=False
            ),
            EstadoBulto.RECHAZADO,
        )
        self.assertEqual(
            self.system.inventario.disponible(self.store.id, "SKU-1"), 0
        )
        event = self.system.logistica.historial(package_id)[-1]
        self.assertIn("no coincide con el remito", event["detail"])

    def test_package_event_history_is_immutable(self) -> None:
        package_id = self._create_package()
        with self.assertRaises(sqlite3.IntegrityError):
            self.system.db.connection.execute(
                "UPDATE package_events SET detail = 'modified' WHERE package_id = ?",
                (package_id,),
            )

    def test_sale_and_refund_preserve_stock_and_reporting(self) -> None:
        self._verify_package(self._create_package())
        sale = self.system.ventas.registrar_venta(
            self.store.id, {"SKU-1": 2}, {"efectivo": 2000}
        )
        line_id = int(
            self.system.db.connection.execute(
                "SELECT id FROM sale_lines WHERE sale_id = ?", (sale.id,)
            ).fetchone()["id"]
        )
        returned = self.system.ventas.registrar_devolucion(
            sale.id, [(line_id, 1, True)], "efectivo"
        )
        self.assertEqual(returned.importe_centavos, 1000)
        self.assertEqual(self.system.inventario.disponible(self.store.id, "SKU-1"), 4)
        income = self.system.reportes.ventas_e_ingresos("2000-01-01", "2100-01-01")
        self.assertEqual(income[0]["importe_neto_centavos"], 1000)
        product = self.system.reportes.productos_y_margen(
            "2000-01-01", "2100-01-01"
        )[0]
        self.assertEqual(product["margen_estimado_centavos"], 500)

    def test_return_cannot_exceed_quantity_sold(self) -> None:
        self._verify_package(self._create_package())
        sale = self.system.ventas.registrar_venta(
            self.store.id, {"SKU-1": 1}, {"efectivo": 1000}
        )
        line_id = int(
            self.system.db.connection.execute(
                "SELECT id FROM sale_lines WHERE sale_id = ?", (sale.id,)
            ).fetchone()["id"]
        )
        with self.assertRaises(ReglaNegocioError):
            self.system.ventas.registrar_devolucion(
                sale.id, [(line_id, 2, True)]
            )

    def test_invalid_package_transition_is_rejected(self) -> None:
        package_id = self._create_package()
        with self.assertRaises(ReglaNegocioError):
            self.system.logistica.despachar(package_id)

    def test_prepared_packages_reserve_center_stock(self) -> None:
        _, first_orders = self.system.reposicion.consolidar_necesidades(
            self.center.id, {self.store.id: {"SKU-1": 8}}
        )
        self.system.logistica.preparar_bulto(
            first_orders[self.store.id], "REM-RESERVE-1", "SEAL-RESERVE-1",
            {"SKU-1": 8},
        )
        second_store = self.system.catalogo.crear_establecimiento(
            "LOC-2", "Sucursal 2", TipoEstablecimiento.SUCURSAL
        )
        _, second_orders = self.system.reposicion.consolidar_necesidades(
            self.center.id, {second_store.id: {"SKU-1": 3}}
        )
        with self.assertRaisesRegex(ReglaNegocioError, "Stock insuficiente"):
            self.system.logistica.preparar_bulto(
                second_orders[second_store.id],
                "REM-RESERVE-2",
                "SEAL-RESERVE-2",
                {"SKU-1": 3},
            )
        with self.assertRaisesRegex(ReglaNegocioError, "unidades reservadas"):
            self.system.inventario.ajustar_stock_centro(
                self.center.id, "SKU-1", -3, "Ajuste bajo reserva"
            )
        self.assertEqual(
            self.system.db.connection.execute(
                "SELECT COUNT(*) FROM packages"
            ).fetchone()[0],
            1,
        )

    def test_minimum_stock_is_independent_per_establishment(self) -> None:
        self.system.inventario.configurar_stock_minimo(
            self.center.id, "SKU-1", 5
        )
        self.system.inventario.configurar_stock_minimo(
            self.store.id, "SKU-1", 2
        )
        self.system.inventario.ajustar_stock_centro(
            self.center.id, "SKU-1", -6, "Prueba de mínimo"
        )

        center_low = self.system.inventario.listar_stock_bajo(self.center.id)
        store_low = self.system.inventario.listar_stock_bajo(self.store.id)
        self.assertEqual(center_low[0]["minimum_stock"], 5)
        self.assertEqual(store_low[0]["minimum_stock"], 2)

    def test_dispatched_package_cannot_be_cancelled(self) -> None:
        package_id = self._create_package(quantity=2)
        self.system.logistica.precintar(package_id)
        self.system.logistica.despachar(package_id)
        with self.assertRaisesRegex(ReglaNegocioError, "todavía no salieron"):
            self.system.logistica.cancelar_preparacion(package_id)

    def test_nested_service_transaction_rolls_back_with_outer_transaction(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "cancelar operación"):
            with self.system.db.transaccion():
                self.system.catalogo.crear_proveedor("TAX-ROLLBACK", "Temporal")
                raise RuntimeError("cancelar operación")
        supplier = self.system.db.connection.execute(
            "SELECT id FROM suppliers WHERE tax_id = 'TAX-ROLLBACK'"
        ).fetchone()
        self.assertIsNone(supplier)


if __name__ == "__main__":
    unittest.main()
