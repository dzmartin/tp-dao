import random
import unittest

from backend.container import create_application
from backend.datos.seed import create_random_seed


class SeedTests(unittest.TestCase):
    def test_seed_creates_random_sales_across_multiple_days_and_locations(self):
        application = create_application(":memory:")
        try:
            result = create_random_seed(application, days=2, rng=random.Random(42))

            self.assertEqual(result["products"], 5)
            skus = [row["sku"] for row in application.repository.all("SELECT sku FROM products")]
            self.assertEqual(len(skus), 5)
            self.assertEqual(len(set(skus)), 5)
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
            self.assertEqual(
                {product["name"] for product in products},
                {"Yerba", "Café", "Arroz", "Fideos", "Galletitas"},
            )
            self.assertEqual(len({product["category"] for product in products}), 4)
            self.assertEqual(
                {row["name"] for row in application.repository.all("SELECT name FROM categories")},
                {"Almacén", "Bebidas", "Limpieza", "Snacks"},
            )
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
            self.assertEqual(
                len(application.reports.net_sales_by_day(result["start_date"], result["end_date"])),
                2,
            )
            self.assertEqual(
                application.repository.one("SELECT COUNT(*) AS count FROM packages")["count"],
                2,
            )
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
