import sqlite3
import unittest

from exceptions import BusinessRuleError
from repositories.sqlite_repository import SQLiteRepository
from services.report_service import ReportService
from services.retail_service import RetailService


class RetailWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.repository = SQLiteRepository(":memory:")
        self.service = RetailService(self.repository)
        self.reports = ReportService(self.repository)
        self.cd = self.service.create_location("CD", "Centro", "CD")
        self.store = self.service.create_location("L01", "Local Centro", "LOCAL")
        self.service.create_product("SKU-1", "Producto", "Almacén", "4.50", "10.00", 2)
        self.service.adjust_stock("CD", "SKU-1", 20, "compra")

    def tearDown(self):
        self.repository.close()

    def _package(self, code="B-1", remito="R-1"):
        order_id = self.service.create_replenishment("L01", {"SKU-1": 5})
        self.service.pack(order_id, code, remito, {"SKU-1": 5})
        self.service.seal_package(code, "PREC-1")
        self.service.dispatch(code)
        return order_id

    def _stock(self):
        row = self.repository.one(
            "SELECT s.quantity FROM stock s JOIN locations l ON l.id=s.location_id JOIN products p ON p.id=s.product_id WHERE l.code='L01' AND p.sku='SKU-1'"
        )
        return row["quantity"] if row else 0

    def test_broken_seal_rejects_package_without_releasing_stock(self):
        self._package()
        self.assertEqual(self.service.receive("B-1", seal_intact=False), "Rechazado")
        self.assertEqual(self._stock(), 0)
        self.assertEqual(
            [row["state"] for row in self.repository.all("SELECT state FROM package_events ORDER BY id")],
            ["Preparado", "Precintado", "En tránsito", "Recibido", "Rechazado"],
        )
        with self.assertRaises(BusinessRuleError):
            self.service.sell("L01", {"SKU-1": 1}, {"efectivo": 10})

    def test_only_verified_package_becomes_sellable(self):
        self._package()
        self.assertEqual(self.service.receive("B-1", seal_intact=True), "Recibido")
        self.assertEqual(self._stock(), 0)
        with self.assertRaises(BusinessRuleError):
            self.service.sell("L01", {"SKU-1": 1}, {"efectivo": 10})
        self.service.verify_package("B-1")
        self.assertEqual(self._stock(), 5)
        receipt = self.service.sell("L01", {"SKU-1": 2}, {"efectivo": 10, "tarjeta": 10})
        self.assertTrue(receipt.startswith("V-"))
        self.assertEqual(self._stock(), 3)

    def test_returns_cannot_exceed_sold_quantity_and_impact_stock_and_cash_reports(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")
        receipt = self.service.sell("L01", {"SKU-1": 2}, {"efectivo": 10, "tarjeta": 10})
        self.assertEqual(self.service.return_items(receipt, {"SKU-1": 1}), 1000)
        self.assertEqual(self._stock(), 4)
        with self.assertRaises(BusinessRuleError):
            self.service.return_items(receipt, {"SKU-1": 2})
        sales = self.reports.sales_by_period("2000-01-01", "2999-12-31")
        self.assertEqual(sum(row["net_cents"] for row in sales), 1000)
        self.assertEqual(self.reports.best_sellers_and_margin("2000-01-01", "2999-12-31")[0]["margin_cents"], 550)

    def test_product_and_supplier_identifiers_are_unique(self):
        supplier_id = self.service.create_supplier("TAX-1", "Proveedor")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_supplier("TAX-1", "Duplicado")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_product("SKU-1", "Duplicado", "Otra", 1, 2, 0, supplier_id)

    def test_rejected_quantities_can_be_repacked_and_order_is_not_completed_early(self):
        order_id = self.service.create_replenishment("L01", {"SKU-1": 5})
        self.service.pack(order_id, "B-1", "R-1", {"SKU-1": 2})
        self.service.seal_package("B-1", "PREC-1")
        self.service.dispatch("B-1")
        self.service.receive("B-1", seal_intact=False)
        self.service.pack(order_id, "B-2", "R-2", {"SKU-1": 2})
        self.service.seal_package("B-2", "PREC-2")
        self.service.dispatch("B-2")
        self.service.receive("B-2", seal_intact=True)
        self.service.verify_package("B-2")
        self.assertEqual(self._stock(), 2)
        order = self.repository.one("SELECT status FROM replenishment_orders WHERE id=?", (order_id,))
        self.assertEqual(order["status"], "Preparando")
        self.service.pack(order_id, "B-3", "R-3", {"SKU-1": 3})
        self.service.seal_package("B-3", "PREC-3")
        self.service.dispatch("B-3")
        self.service.receive("B-3", seal_intact=True)
        self.service.verify_package("B-3")
        self.assertEqual(self._stock(), 5)

    def test_reports_include_rejected_bultos_and_low_stock(self):
        self._package()
        self.service.receive("B-1", seal_intact=False)
        logistics = self.reports.logistics_performance()
        self.assertEqual(logistics["rejected_count"], 1)
        self.assertEqual(logistics["rejected_packages"][0]["reason"], "Precinto violado.")
        minimums = self.reports.inventory_and_minimums()
        self.assertEqual(minimums[0]["quantity"], 0)
        self.assertEqual(minimums[0]["at_minimum"], 1)

    def test_package_event_history_cannot_be_modified(self):
        self._package()
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.execute("UPDATE package_events SET detail='alterado' WHERE id=1")

    def test_invalid_stock_adjustments_do_not_change_stock_or_movements(self):
        movement_count = self.repository.one("SELECT COUNT(*) AS count FROM inventory_movements")["count"]

        for location_code, sku, delta in (
            ("CD", "SKU-1", 0),
            ("CD", "SKU-1", -21),
            ("INVALID", "SKU-1", 1),
            ("CD", "INVALID", 1),
        ):
            with self.subTest(location_code=location_code, sku=sku, delta=delta):
                with self.assertRaises(BusinessRuleError):
                    self.service.adjust_stock(location_code, sku, delta)

        self.assertEqual(
            self.repository.one(
                """SELECT s.quantity FROM stock s
                   JOIN locations l ON l.id=s.location_id
                   JOIN products p ON p.id=s.product_id
                   WHERE l.code='CD' AND p.sku='SKU-1'"""
            )["quantity"],
            20,
        )
        self.assertEqual(
            self.repository.one("SELECT COUNT(*) AS count FROM inventory_movements")["count"],
            movement_count,
        )

    def test_invalid_sale_payments_do_not_create_sales_or_consume_stock(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")

        for payments in ({}, {"efectivo": 0}, {"bitcoin": 10}, {"efectivo": 9}):
            with self.subTest(payments=payments):
                with self.assertRaises(BusinessRuleError):
                    self.service.sell("L01", {"SKU-1": 1}, payments)

        self.assertEqual(self._stock(), 5)
        self.assertEqual(self.repository.one("SELECT COUNT(*) AS count FROM sales")["count"], 0)

    def test_rejected_dispatch_note_does_not_release_stock(self):
        self._package()
        self.assertEqual(self.service.receive("B-1", seal_intact=True, dispatch_note_ok=False), "Rechazado")

        with self.assertRaises(BusinessRuleError):
            self.service.verify_package("B-1")

        self.assertEqual(self._stock(), 0)
        logistics = self.reports.logistics_performance()
        self.assertEqual(logistics["rejected_count"], 1)
        self.assertEqual(logistics["rejected_packages"][0]["reason"], "Remito no conforme.")

    def test_unfit_return_is_recorded_as_waste_without_restocking(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")
        receipt = self.service.sell("L01", {"SKU-1": 1}, {"efectivo": 10})

        self.assertEqual(self.service.return_items(receipt, {"SKU-1": 1}, restock=False), 1000)
        self.assertEqual(self._stock(), 4)
        movement = self.repository.one(
            "SELECT quantity_delta,movement_type FROM inventory_movements WHERE reference=? ORDER BY id DESC",
            (receipt,),
        )
        self.assertEqual(movement["quantity_delta"], 0)
        self.assertEqual(movement["movement_type"], "merma devolución")
        self.assertEqual(self.repository.one("SELECT restocked FROM return_lines")["restocked"], 0)


if __name__ == "__main__":
    unittest.main()