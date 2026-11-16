import random
import unittest
from datetime import datetime, timedelta

from backend.container import create_application
from backend.datos.seed import create_random_seed


class SeedTests(unittest.TestCase):
    def test_seed_creates_random_sales_across_multiple_days_and_locations(self):
        application = create_application(":memory:")
        try:
            result = create_random_seed(application, days=2, rng=random.Random(42))

            self.assertEqual(result["products"], 15)
            skus = [row["sku"] for row in application.repository.all("SELECT sku FROM products")]
            self.assertEqual(len(skus), 15)
            self.assertEqual(len(set(skus)), 15)
            self.assertTrue(
                all(sku.startswith("SKU-") and len(sku) == 12 and sku[4:].isdigit() for sku in skus)
            )
            locations = application.repository.all("SELECT name FROM locations WHERE type='LOCAL'")
            self.assertEqual(len({location["name"] for location in locations}), 2)
            self.assertTrue(
                all(
                    location["name"] in {"Local Centro", "Local Norte", "Local Sur", "Local Oeste"}
                    for location in locations
                )
            )
            products = application.repository.all("SELECT name,category FROM products")
            self.assertEqual(len({product["name"] for product in products}), 15)
            category_names = {
                row["name"] for row in application.repository.all("SELECT name FROM categories")
            }
            self.assertEqual(category_names, {"Almacén", "Bebidas", "Limpieza", "Snacks"})
            self.assertTrue(all(product["category"] in category_names for product in products))
            supplier_names = {
                row["name"] for row in application.repository.all("SELECT name FROM suppliers")
            }
            self.assertEqual(len(supplier_names), 1)
            self.assertIn(
                supplier_names.pop(),
                {"Distribuidora Estrella", "Distribuidora Central", "Distribuidora Andina"},
            )
            self.assertEqual(len(result["locations"]), 2)
            self.assertGreaterEqual(result["sales"], 4)
            self.assertLessEqual(result["sales"], 12)
            sold_at = [
                datetime.fromisoformat(row["sold_at"])
                for row in application.repository.all("SELECT sold_at FROM sales")
            ]
            self.assertEqual(len(sold_at), result["sales"])
            self.assertGreater(len({sale.date() for sale in sold_at}), 1)
            self.assertGreater(len({(sale.hour, sale.minute) for sale in sold_at}), 1)
            self.assertEqual(len(set(sold_at)), len(sold_at))
            self.assertEqual(
                len(application.reports.net_sales_by_day(result["start_date"], result["end_date"])),
                2,
            )
            packages = application.repository.all(
                "SELECT id,status,prepared_at,dispatched_at,received_at FROM packages"
            )
            self.assertEqual(len(packages), 4)
            self.assertEqual(result["rejected_packages"], 2)
            self.assertEqual(
                sum(package["status"] == "Rechazado" for package in packages),
                2,
            )
            for package in packages:
                events = application.repository.all(
                    """SELECT state,occurred_at FROM package_events
                       WHERE package_id=? ORDER BY id""",
                    (package["id"],),
                )
                states = [event["state"] for event in events]
                expected_last_state = (
                    "Rechazado" if package["status"] == "Rechazado" else "Verificado"
                )
                self.assertEqual(
                    states,
                    ["Preparado", "Precintado", "En tránsito", "Recibido", expected_last_state],
                )
                event_times = [
                    datetime.fromisoformat(event["occurred_at"])
                    for event in events
                ]
                self.assertEqual(event_times, sorted(event_times))
                self.assertEqual(event_times[1] - event_times[0], timedelta(minutes=30))
                self.assertEqual(event_times[2] - event_times[1], timedelta(hours=2, minutes=30))
                self.assertEqual(package["prepared_at"], events[0]["occurred_at"])
                self.assertEqual(package["dispatched_at"], events[2]["occurred_at"])
                self.assertEqual(package["received_at"], events[3]["occurred_at"])
                if package["status"] == "Rechazado":
                    self.assertGreater(event_times[4], event_times[3])
            dispositions = application.repository.all(
                "SELECT disposition FROM rejected_package_dispositions"
            )
            self.assertEqual([row["disposition"] for row in dispositions], ["discard"])
            unresolved = application.repository.one(
                """SELECT COUNT(*) AS count FROM packages p
                   WHERE p.status='Rechazado' AND NOT EXISTS (
                       SELECT 1 FROM rejected_package_dispositions d WHERE d.package_id=p.id
                   )"""
            )
            self.assertEqual(unresolved["count"], 1)
            logistics = application.reports.logistics_performance(
                result["end_date"],
                result["end_date"],
            )
            self.assertEqual(logistics["rejected_count"], 2)
            self.assertEqual(logistics["verified_packages"], 2)
            self.assertGreater(logistics["average_transit_hours"], 0)
            self.assertEqual(logistics["waste_count"], 1)
            self.assertGreater(logistics["waste_quantity"], 0)
            self.assertGreater(logistics["waste_valuation_cents"], 0)
            shortcuts = application.auth.setup_status()["demo_shortcuts"]
            self.assertEqual(len(shortcuts), 4)
            self.assertEqual(
                [account["username"] for account in shortcuts],
                ["demo-admin", "demo-cd", "demo-local-centro", "demo-local-norte"],
            )
            self.assertEqual(
                application.auth.authenticate("demo-admin", "EstrellaAdmin26")["role"],
                "ADMIN",
            )
            self.assertEqual(
                application.auth.authenticate("demo-local-norte", "EstrellaLocal2")["role"],
                "LOCAL",
            )
        finally:
            application.close()


if __name__ == "__main__":
    unittest.main()
