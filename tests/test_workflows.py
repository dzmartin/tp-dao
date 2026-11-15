import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.dominio.exceptions import BusinessRuleError
from backend.persistencia.sqlite_repository import SQLiteRepository
from backend.servicios.report_service import ReportService
from backend.servicios.retail_service import RetailService


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

    def _deliver(self, sku, quantity, code):
        self.service.adjust_stock("CD", sku, quantity, "recepción inicial")
        order_id = self.service.create_replenishment("L01", {sku: quantity})
        self.service.pack(order_id, code, f"R-{code}", {sku: quantity})
        self.service.seal_package(code, f"PREC-{code}")
        self.service.dispatch(code)
        self.service.receive(code, seal_intact=True)
        self.service.verify_package(code)

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
        margin = self.reports.best_sellers_and_margin("2000-01-01", "2999-12-31")[0]["margin_cents"]
        self.assertEqual(margin, 550)

    def test_cash_overpayment_records_only_the_amount_due_and_mixed_payment_change(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")

        cash_receipt = self.service.sell("L01", {"SKU-1": 1}, {"efectivo": "15.00"})
        cash_payments = self.repository.all(
            """SELECT p.method,p.amount_cents FROM payments p
               JOIN sales s ON s.id=p.sale_id WHERE s.receipt=?""",
            (cash_receipt,),
        )
        self.assertEqual(
            [(payment["method"], payment["amount_cents"]) for payment in cash_payments],
            [("efectivo", 1000)],
        )

        mixed_receipt = self.service.sell(
            "L01", {"SKU-1": 1}, {"efectivo": "7.00", "tarjeta": "5.00"}
        )
        mixed_payments = self.repository.all(
            """SELECT p.method,p.amount_cents FROM payments p
               JOIN sales s ON s.id=p.sale_id WHERE s.receipt=? ORDER BY p.method""",
            (mixed_receipt,),
        )
        self.assertEqual(
            [(payment["method"], payment["amount_cents"]) for payment in mixed_payments],
            [("efectivo", 500), ("tarjeta", 500)],
        )

    def test_sale_state_transitions_from_confirmed_to_partial_and_returned(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")
        receipt = self.service.sell("L01", {"SKU-1": 2}, {"efectivo": 20})

        sale = self.repository.one("SELECT state FROM sales WHERE receipt=?", (receipt,))
        self.assertEqual(sale["state"], "Confirmada")

        self.service.return_items(receipt, {"SKU-1": 1})
        sale = self.repository.one("SELECT state FROM sales WHERE receipt=?", (receipt,))
        self.assertEqual(sale["state"], "Devolución parcial")

        self.service.return_items(receipt, {"SKU-1": 1})
        sale = self.repository.one("SELECT state FROM sales WHERE receipt=?", (receipt,))
        self.assertEqual(sale["state"], "Devuelta")
        with self.assertRaisesRegex(BusinessRuleError, "devuelta por completo"):
            self.service.return_items(receipt, {"SKU-1": 1})

    def test_repository_adds_sale_state_to_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """CREATE TABLE sales (
                    id INTEGER PRIMARY KEY,
                    receipt TEXT NOT NULL UNIQUE,
                    location_id INTEGER NOT NULL,
                    sold_at TEXT NOT NULL,
                    total_cents INTEGER NOT NULL
                )"""
            )
            connection.commit()
            connection.close()

            repository = SQLiteRepository(database_path)
            try:
                columns = {row["name"] for row in repository.all("PRAGMA table_info(sales)")}
                self.assertIn("state", columns)
            finally:
                repository.close()

    def test_suggested_supplier_purchase_order_receives_partially_into_cd(self):
        supplier_id = self.service.create_supplier("SUP-1", "Proveedor")
        self.service.create_product("SKU-2", "Otro", "General", 2, 5, 3, supplier_id)
        suggestions = self.service.suggested_purchases()
        suggestion = next(item for item in suggestions if item["sku"] == "SKU-2")
        self.assertEqual(suggestion["suggested_quantity"], 3)

        order_id = self.service.create_purchase_order(supplier_id, {"SKU-2": 3})
        self.assertNotIn("SKU-2", [item["sku"] for item in self.service.suggested_purchases()])
        self.service.receive_purchase_order(order_id, {"SKU-2": 2})
        self.assertEqual(
            self.repository.one("SELECT status FROM purchase_orders WHERE id=?", (order_id,))["status"],
            "Parcial",
        )
        self.service.receive_purchase_order(order_id, {"SKU-2": 1})
        self.assertEqual(
            self.repository.one("SELECT status FROM purchase_orders WHERE id=?", (order_id,))["status"],
            "Recibida",
        )
        self.assertEqual(
            self.repository.one(
                "SELECT quantity FROM stock WHERE location_id=? AND product_id=(SELECT id FROM products WHERE sku='SKU-2')",
                (self.cd,),
            )["quantity"],
            3,
        )
    def test_sale_uses_product_price_and_rejects_current_account_payment(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")

        with self.assertRaisesRegex(BusinessRuleError, "Medio de pago no admitido"):
            self.service.sell("L01", {"SKU-1": 1}, {"cuenta corriente": "10.00"})

        self.assertEqual(self._stock(), 5)
        self.assertEqual(self.repository.one("SELECT COUNT(*) AS count FROM sales")["count"], 0)
        receipt = self.service.sell("L01", {"SKU-1": 1}, {"efectivo": "10.00"})
        sale = self.repository.one("SELECT total_cents FROM sales WHERE receipt=?", (receipt,))
        line = self.repository.one(
            "SELECT unit_price_cents FROM sale_lines WHERE sale_id=(SELECT id FROM sales WHERE receipt=?)",
            (receipt,),
        )
        self.assertEqual(sale["total_cents"], 1000)
        self.assertEqual(line["unit_price_cents"], 1000)

    def test_physical_count_reconciles_stock_and_audits_delta(self):
        count_id = self.service.open_inventory_count("L01")
        self.service.set_inventory_count(count_id, {"SKU-1": 4})
        self.service.close_inventory_count(count_id)
        self.assertEqual(self._stock(), 4)
        movement = self.repository.one(
            "SELECT quantity_delta,movement_type FROM inventory_movements WHERE reference=?",
            (str(count_id),),
        )
        self.assertEqual((movement["quantity_delta"], movement["movement_type"]), (4, "conciliación inventario físico"))

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

    def test_categories_are_managed_and_linked_to_products(self):
        category_id = self.service.create_category("Bebidas")
        self.service.create_product("SKU-DRINK", "Agua", "Bebidas", 1, 2, 0)
        product = self.repository.one(
            "SELECT category,category_id FROM products WHERE sku='SKU-DRINK'"
        )
        self.assertEqual(product["category_id"], category_id)
        self.assertEqual(product["category"], "Bebidas")

        self.service.rename_category(category_id, "Bebidas sin alcohol")
        product = self.repository.one(
            "SELECT category FROM products WHERE sku='SKU-DRINK'"
        )
        self.assertEqual(product["category"], "Bebidas sin alcohol")
        with self.assertRaises(BusinessRuleError):
            self.service.delete_category(category_id)

        unused_id = self.service.create_category("Sin uso")
        self.service.delete_category(unused_id)
        self.assertIsNone(self.repository.one("SELECT id FROM categories WHERE id=?", (unused_id,)))

    def test_consolidated_replenishment_creates_destination_suborders(self):
        second_store = self.service.create_location("L02", "Local Norte", "LOCAL")
        batch_id = self.service.create_consolidated_replenishment([
            {"destination_code": "L01", "items": {"SKU-1": 2}},
            {"destination_code": "L02", "items": {"SKU-1": 3}},
        ])
        orders = self.repository.all(
            """SELECT id,destination_id,code FROM replenishment_orders
               WHERE batch_id=? ORDER BY id""",
            (batch_id,),
        )
        self.assertEqual(len(orders), 2)
        self.assertEqual({row["destination_id"] for row in orders}, {self.store, second_store})

        self.service.pack(orders[1]["id"], "B-MULTI", "R-MULTI", {"SKU-1": 3})
        self.service.seal_package("B-MULTI", "PREC-MULTI")
        self.service.dispatch("B-MULTI")
        self.service.receive("B-MULTI", seal_intact=True)
        self.service.verify_package("B-MULTI")
        stock = self.repository.one(
            """SELECT s.quantity FROM stock s JOIN locations l ON l.id=s.location_id
               JOIN products p ON p.id=s.product_id WHERE l.code='L02' AND p.sku='SKU-1'"""
        )
        self.assertEqual(stock["quantity"], 3)

    def test_one_package_allocates_quantities_across_same_local_orders_oldest_first(self):
        first_order = self.service.create_replenishment("L01", {"SKU-1": 2})
        second_order = self.service.create_replenishment("L01", {"SKU-1": 3})

        package_id = self.service.pack_for_destination(
            "L01", "B-CONSOLIDATED", "R-CONSOLIDATED", {"SKU-1": 4}
        )
        allocations = self.repository.all(
            """SELECT pol.order_id,pol.quantity FROM package_order_lines pol
               WHERE pol.package_id=? ORDER BY pol.order_id""",
            (package_id,),
        )
        self.assertEqual(
            [(line["order_id"], line["quantity"]) for line in allocations],
            [(first_order, 2), (second_order, 2)],
        )

        self.service.seal_package("B-CONSOLIDATED", "PREC-CONSOLIDATED")
        self.service.dispatch("B-CONSOLIDATED")
        self.service.receive("B-CONSOLIDATED", seal_intact=True)
        self.service.verify_package("B-CONSOLIDATED")
        statuses = self.repository.all(
            "SELECT id,status FROM replenishment_orders WHERE id IN (?,?) ORDER BY id",
            (first_order, second_order),
        )
        self.assertEqual(
            [(order["id"], order["status"]) for order in statuses],
            [(first_order, "Completada"), (second_order, "Preparando")],
        )
        self.assertEqual(self._stock(), 4)

    def test_rejected_consolidated_package_reopens_all_linked_orders(self):
        first_order = self.service.create_replenishment("L01", {"SKU-1": 2})
        second_order = self.service.create_replenishment("L01", {"SKU-1": 3})
        self.service.pack_for_destination(
            "L01", "B-CONSOLIDATED-REJECTED", "R-CONSOLIDATED-REJECTED",
            {"SKU-1": 4},
        )
        self.service.seal_package("B-CONSOLIDATED-REJECTED", "PREC-CONSOLIDATED-REJECTED")
        self.service.dispatch("B-CONSOLIDATED-REJECTED")
        self.assertEqual(
            self.service.receive("B-CONSOLIDATED-REJECTED", seal_intact=False),
            "Rechazado",
        )
        statuses = self.repository.all(
            "SELECT id,status FROM replenishment_orders WHERE id IN (?,?) ORDER BY id",
            (first_order, second_order),
        )
        self.assertEqual([order["status"] for order in statuses], ["Con rechazo", "Con rechazo"])

        retry_package = self.service.pack_for_destination(
            "L01", "B-CONSOLIDATED-RETRY", "R-CONSOLIDATED-RETRY", {"SKU-1": 4}
        )
        retry_orders = self.repository.all(
            "SELECT order_id FROM package_orders WHERE package_id=? ORDER BY order_id",
            (retry_package,),
        )
        self.assertEqual(
            [order["order_id"] for order in retry_orders], [first_order, second_order]
        )

    def test_logistics_reports_filter_period_and_location_and_value_waste(self):
        self._package()
        self.service.receive("B-1", seal_intact=False)

        self._package("B-2", "R-2")
        self.service.receive("B-2", seal_intact=True)
        self.service.verify_package("B-2")
        receipt = self.service.sell("L01", {"SKU-1": 1}, {"efectivo": 10})
        self.service.return_items(receipt, {"SKU-1": 1}, restock=False)

        today = datetime.now(timezone.utc).date()
        today_iso = today.isoformat()
        report = self.reports.logistics_performance(today_iso, today_iso, "L01")
        self.assertEqual(report["rejected_count"], 1)
        self.assertEqual(report["verified_packages"], 1)
        self.assertEqual(report["waste_quantity"], 1)
        self.assertEqual(report["waste_valuation_cents"], 450)
        self.assertEqual(report["waste_movements"][0]["cost_is_estimated"], 0)

        yesterday = (today - timedelta(days=1)).isoformat()
        self.assertEqual(self.reports.logistics_performance(yesterday, yesterday)["rejected_count"], 0)
        self.assertEqual(
            self.reports.logistics_performance(today_iso, today_iso, "CD")["waste_quantity"], 0
        )

    def test_inventory_movement_values_use_cost_captured_at_movement_time(self):
        self._deliver("SKU-1", 3, "VALUATION-BULK")
        self.repository.execute("UPDATE products SET cost_cents=9999 WHERE sku='SKU-1'")

        today = datetime.now(timezone.utc).date().isoformat()
        movements = self.reports.inventory_movements(today, today, "L01")
        adjustment = next(row for row in movements if row["movement_type"] == "recepción verificada")
        self.assertEqual(adjustment["valuation_delta_cents"], 1350)
        self.assertEqual(adjustment["unit_cost_cents"], 450)
        self.assertEqual(adjustment["cost_is_estimated"], 0)

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

    def test_local_stock_cannot_be_added_without_logistics_or_physical_count(self):
        with self.assertRaisesRegex(BusinessRuleError, "solo aumenta al verificar"):
            self.service.adjust_stock("L01", "SKU-1", 1, "ajuste manual")
        self.assertEqual(self._stock(), 0)
        self._package("B-VERIFY", "R-VERIFY")
        self.service.receive("B-VERIFY", seal_intact=True)
        self.assertEqual(self._stock(), 0)
        self.service.verify_package("B-VERIFY")
        self.assertEqual(self._stock(), 5)

    def test_invalid_sale_payments_do_not_create_sales_or_consume_stock(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")

        invalid_payments = ({}, {"efectivo": 0}, {"bitcoin": 10}, {"efectivo": 9}, {"tarjeta": 11})
        for payments in invalid_payments:
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