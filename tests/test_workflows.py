import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

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

    def _deliver(self, sku, quantity, code, lot_code="", expiry_date=None):
        self.service.adjust_stock("CD", sku, quantity, "recepción inicial", lot_code, expiry_date)
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
        self.assertEqual(self.reports.best_sellers_and_margin("2000-01-01", "2999-12-31")[0]["margin_cents"], 550)

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
        self.assertEqual(
            self.repository.one(
                "SELECT COUNT(*) AS count FROM serial_numbers WHERE location_id=? AND product_id=(SELECT id FROM products WHERE sku='SKU-2') AND status='available'",
                (self.cd,),
            )["count"],
            3,
        )

    def test_best_sale_price_uses_promotion_and_updates_customer_account_and_points(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")
        price_list_id = self.service.create_price_list("Mayorista", {"SKU-1": "10.00"})
        promotion_id = self.service.create_promotion(
            "Descuento 20", "percent", 2000, date.today().isoformat(), date.today().isoformat(), sku="SKU-1"
        )
        customer_id = self.service.create_customer("CLI-1", "Cliente", credit_limit="20.00")
        receipt = self.service.sell(
            "L01", {"SKU-1": 1}, {"cuenta corriente": "8.00"}, customer_id, price_list_id
        )
        sale = self.repository.one("SELECT total_cents,price_list_id,customer_id FROM sales WHERE receipt=?", (receipt,))
        line = self.repository.one("SELECT unit_price_cents,discount_cents,promotion_id FROM sale_lines WHERE sale_id=(SELECT id FROM sales WHERE receipt=?)", (receipt,))
        customer = self.repository.one("SELECT balance_cents,loyalty_points FROM customers WHERE id=?", (customer_id,))
        self.assertEqual(sale["total_cents"], 800)
        self.assertEqual(sale["price_list_id"], price_list_id)
        self.assertEqual(sale["customer_id"], customer_id)
        self.assertEqual(line["unit_price_cents"], 800)
        self.assertEqual(line["discount_cents"], 200)
        self.assertEqual(line["promotion_id"], promotion_id)
        self.assertEqual((customer["balance_cents"], customer["loyalty_points"]), (800, 0))
        self.service.return_items(receipt, {"SKU-1": 1})
        customer = self.repository.one("SELECT balance_cents FROM customers WHERE id=?", (customer_id,))
        self.assertEqual(customer["balance_cents"], 0)

    def test_lot_tracking_uses_fefo_and_restores_returned_lot(self):
        self.service.create_product("LOT-1", "Producto lote", "General", 2, 10, 0, tracking_mode="lot")
        self._deliver("LOT-1", 1, "LOT-LATE", "LOT-LATE", (date.today() + timedelta(days=8)).isoformat())
        self._deliver("LOT-1", 1, "LOT-EARLY", "LOT-EARLY", (date.today() + timedelta(days=3)).isoformat())
        receipt = self.service.sell("L01", {"LOT-1": 1}, {"efectivo": 10})
        trace = self.repository.one(
            """SELECT l.code FROM sale_line_trace st
               JOIN serial_numbers sn ON sn.id=st.serial_id JOIN lots l ON l.id=sn.lot_id
               JOIN sale_lines sl ON sl.id=st.sale_line_id JOIN sales s ON s.id=sl.sale_id
               WHERE s.receipt=?""",
            (receipt,),
        )
        self.assertEqual(trace["code"], "LOT-EARLY")
        self.service.return_items(receipt, {"LOT-1": 1})
        early = self.repository.one(
            """SELECT lots.quantity FROM lots JOIN locations ON locations.id=lots.location_id
               WHERE lots.code='LOT-EARLY' AND locations.code='L01'"""
        )["quantity"]
        self.assertEqual(early, 1)
        self.assertEqual(
            self.repository.one(
                """SELECT COUNT(*) AS count FROM serial_numbers sn JOIN lots l ON l.id=sn.lot_id
                   JOIN locations ON locations.id=l.location_id
                   WHERE l.code='LOT-EARLY' AND locations.code='L01' AND sn.status='available'"""
            )["count"],
            1,
        )

    def test_every_stocked_unit_gets_a_random_unique_serial(self):
        self.service.create_product("SER-1", "Equipo", "General", 20, 50, 0, tracking_mode="serial")
        self._deliver("SER-1", 3, "SERIAL-BULK")
        serials = self.repository.all(
            "SELECT serial FROM serial_numbers WHERE product_id=(SELECT id FROM products WHERE sku='SER-1')"
        )
        values = [row["serial"] for row in serials]
        self.assertEqual(len(values), 3)
        self.assertEqual(len(set(values)), 3)
        self.assertTrue(all(value.startswith("SN-") for value in values))
        with self.assertRaisesRegex(BusinessRuleError, "se generan automáticamente"):
            self.service.adjust_stock("CD", "SER-1", 1, "compra", serials=["SN-MANUAL"])

    def test_serial_sale_and_return_update_serial_status(self):
        self.service.create_product("SER-2", "Equipo serializado", "General", 20, 50, 0, tracking_mode="serial")
        self._deliver("SER-2", 1, "SERIAL-SALE")
        serial = self.repository.one(
            "SELECT serial FROM serial_numbers WHERE product_id=(SELECT id FROM products WHERE sku='SER-2')"
        )["serial"]

        receipt = self.service.sell("L01", {"SER-2": 1}, {"efectivo": 50})
        status = self.repository.one("SELECT status FROM serial_numbers WHERE serial=?", (serial,))["status"]
        self.assertEqual(status, "sold")

        self.service.return_items(receipt, {"SER-2": 1})
        status = self.repository.one("SELECT status FROM serial_numbers WHERE serial=?", (serial,))["status"]
        self.assertEqual(status, "available")

    def test_untracked_products_receive_auto_serials_and_keep_them_after_replenishment(self):
        self.service.adjust_stock("CD", "SKU-1", 2, "ingreso")
        serials_before = {
            row["serial"]
            for row in self.repository.all(
                "SELECT serial FROM serial_numbers WHERE location_id=? AND product_id=(SELECT id FROM products WHERE sku='SKU-1') AND status='available'",
                (self.cd,),
            )
        }
        self.assertEqual(len(serials_before), 22)

        order_id = self.service.create_replenishment("L01", {"SKU-1": 2})
        self.service.pack(order_id, "B-SERIAL", "R-SERIAL", {"SKU-1": 2})
        self.service.seal_package("B-SERIAL", "PREC-SERIAL")
        self.service.dispatch("B-SERIAL")
        self.service.receive("B-SERIAL", seal_intact=True)
        self.service.verify_package("B-SERIAL")
        serials_at_store = {
            row["serial"]
            for row in self.repository.all(
                """SELECT serial FROM serial_numbers WHERE location_id=? AND product_id=
                   (SELECT id FROM products WHERE sku='SKU-1') AND status='available'""",
                (self.store,),
            )
        }
        self.assertTrue(serials_at_store)
        self.assertEqual(len(serials_at_store), 2)
        self.assertTrue(serials_at_store.issubset(serials_before))

    def test_account_credit_limit_blocks_sale_without_changing_stock(self):
        self._package()
        self.service.receive("B-1", seal_intact=True)
        self.service.verify_package("B-1")
        customer_id = self.service.create_customer("CLI-LIMIT", "Cliente", credit_limit="5.00")

        with self.assertRaisesRegex(BusinessRuleError, "supera el crédito disponible"):
            self.service.sell("L01", {"SKU-1": 1}, {"cuenta corriente": "10.00"}, customer_id)

        self.assertEqual(self._stock(), 5)
        self.assertEqual(self.repository.one("SELECT COUNT(*) AS count FROM sales")["count"], 0)

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

    def test_physical_count_updates_lot_quantities_and_rejects_duplicate_lots(self):
        self.service.create_product("LOT-2", "Producto por lote", "General", 2, 10, 0, tracking_mode="lot")
        expiry = (date.today() + timedelta(days=8)).isoformat()
        self._deliver("LOT-2", 2, "LOT-COUNT", "LOT-A", expiry)
        count_id = self.service.open_inventory_count("L01")

        with self.assertRaisesRegex(BusinessRuleError, "no pueden repetirse"):
            self.service.set_inventory_count(
                count_id,
                {"SKU-1": 0, "LOT-2": 2},
                {"LOT-2": {"lots": [{"code": "LOT-A", "quantity": 1}, {"code": "LOT-A", "quantity": 1}]}},
            )
        self.service.set_inventory_count(
            count_id, {"SKU-1": 0, "LOT-2": 2}, {"LOT-2": {"lots": [{"code": "LOT-A", "quantity": 2, "expiry_date": expiry}]}}
        )
        self.service.close_inventory_count(count_id)
        lot = self.repository.one(
            """SELECT lots.quantity FROM lots JOIN locations ON locations.id=lots.location_id
               WHERE lots.code='LOT-A' AND locations.code='L01'"""
        )
        self.assertEqual(lot["quantity"], 2)

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

    def test_logistics_reports_filter_period_and_location_and_value_waste(self):
        self._package()
        self.service.receive("B-1", seal_intact=False)

        self._package("B-2", "R-2")
        self.service.receive("B-2", seal_intact=True)
        self.service.verify_package("B-2")
        receipt = self.service.sell("L01", {"SKU-1": 1}, {"efectivo": 10})
        self.service.return_items(receipt, {"SKU-1": 1}, restock=False)

        today = date.today().isoformat()
        report = self.reports.logistics_performance(today, today, "L01")
        self.assertEqual(report["rejected_count"], 1)
        self.assertEqual(report["verified_packages"], 1)
        self.assertEqual(report["waste_quantity"], 1)
        self.assertEqual(report["waste_valuation_cents"], 450)
        self.assertEqual(report["waste_movements"][0]["cost_is_estimated"], 0)

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(self.reports.logistics_performance(yesterday, yesterday)["rejected_count"], 0)
        self.assertEqual(self.reports.logistics_performance(today, today, "CD")["waste_quantity"], 0)

    def test_inventory_movement_values_use_cost_captured_at_movement_time(self):
        self._deliver("SKU-1", 3, "VALUATION-BULK")
        self.repository.execute("UPDATE products SET cost_cents=9999 WHERE sku='SKU-1'")

        movements = self.reports.inventory_movements(
            date.today().isoformat(), date.today().isoformat(), "L01"
        )
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