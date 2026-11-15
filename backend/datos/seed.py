import argparse
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from backend.container import create_application
from backend.datos.demo_accounts import DEMO_ACCOUNTS


PRODUCT_NAMES = ("Yerba", "Café", "Arroz", "Fideos", "Galletitas")
CATEGORIES = ("Almacén", "Bebidas", "Limpieza", "Snacks")
PAYMENT_METHODS = ("efectivo", "tarjeta", "transferencia")


def create_random_seed(application, days: int = 30, rng: random.Random | None = None) -> dict:
    if not 1 <= days <= 90:
        raise ValueError("La cantidad de días debe estar entre 1 y 90.")
    rng = rng or random.Random()
    service = application.retail
    repository = application.repository
    demo_usernames = tuple(account["username"] for account in DEMO_ACCOUNTS)
    placeholders = ",".join("?" for _ in demo_usernames)
    if repository.one(
        f"SELECT 1 FROM app_users WHERE username IN ({placeholders}) LIMIT 1",
        demo_usernames,
    ):
        raise ValueError(
            "La base ya contiene cuentas demo; usa una base vacía para generar otra semilla."
        )
    suffix = f"{rng.randrange(100000, 1000000)}"

    central = repository.one("SELECT code FROM locations WHERE type='CD' ORDER BY id LIMIT 1")
    central_code = central["code"] if central else f"CD-{suffix}"
    if not central:
        service.create_location(central_code, "Centro de Distribución", "CD")

    locations = []
    location_ids = []
    local_names = rng.sample(("Centro", "Norte", "Sur", "Oeste"), k=2)
    for index, local_name in enumerate(local_names, start=1):
        code = f"L-{suffix}-{index:02d}"
        location_ids.append(
            service.create_location(
                code,
                f"Local {local_name}",
                "LOCAL",
            )
        )
        locations.append(code)

    for account in DEMO_ACCOUNTS:
        location_id = (
            location_ids[account["location"]]
            if account["location"] is not None
            else None
        )
        application.auth.create_user(
            account["username"],
            account["password"],
            account["role"],
            location_id,
        )
    with repository.transaction():
        for account in DEMO_ACCOUNTS:
            repository.execute(
                "INSERT INTO demo_account_markers(username) VALUES(?)",
                (account["username"],),
            )

    supplier_id = service.create_supplier(
        f"SEED-{suffix}",
        f"Distribuidora {rng.choice(('Estrella', 'Central', 'Andina'))}",
    )
    categories = list(CATEGORIES)
    for category in categories:
        service.create_category(category)

    products = []
    used_skus = {row["sku"] for row in repository.all("SELECT sku FROM products")}
    for index, name in enumerate(PRODUCT_NAMES, start=1):
        cost_cents = rng.randrange(250, 4500)
        price_cents = cost_cents + rng.randrange(150, 2800)
        sku = f"SKU-{rng.randrange(10_000_000, 100_000_000)}"
        while sku in used_skus:
            sku = f"SKU-{rng.randrange(10_000_000, 100_000_000)}"
        used_skus.add(sku)
        service.create_product(
            sku,
            name,
            categories[(index - 1) % len(categories)],
            f"{cost_cents / 100:.2f}",
            f"{price_cents / 100:.2f}",
            20,
            supplier_id,
        )
        products.append({"sku": sku, "price_cents": price_cents})
        service.adjust_stock(central_code, sku, 1000, "Carga semilla aleatoria")

    shipment_lines = {product["sku"]: 500 for product in products}
    for index, location in enumerate(locations, start=1):
        order_id = service.create_replenishment(location, shipment_lines)
        package_code = f"B-{suffix}-{index:02d}"
        service.pack(order_id, package_code, f"R-{suffix}-{index:02d}", shipment_lines)
        service.seal_package(package_code, f"P-{suffix}-{index:02d}")
        service.dispatch(package_code)
        service.receive(package_code, seal_intact=True, dispatch_note_ok=True)
        service.verify_package(package_code)

    remaining = {
        location: {product["sku"]: 500 for product in products}
        for location in locations
    }
    today = date.today()
    first_day = today - timedelta(days=days - 1)
    sale_count = 0
    for day_offset in range(days):
        sale_day = first_day + timedelta(days=day_offset)
        for location in locations:
            for _ in range(rng.randint(1, 3)):
                available = [
                    product for product in products
                    if remaining[location][product["sku"]] > 0
                ]
                line_count = min(len(available), rng.randint(1, 2))
                items = {}
                for product in rng.sample(available, line_count):
                    quantity = min(
                        remaining[location][product["sku"]],
                        rng.randint(1, 2),
                    )
                    items[product["sku"]] = quantity
                    remaining[location][product["sku"]] -= quantity
                total_cents = sum(
                    product["price_cents"] * items.get(product["sku"], 0)
                    for product in products
                )
                method = rng.choice(PAYMENT_METHODS)
                receipt = service.sell(
                    location,
                    items,
                    {method: f"{total_cents / 100:.2f}"},
                )
                sold_at = datetime.combine(
                    sale_day,
                    time(hour=rng.randint(9, 20), minute=rng.randrange(60)),
                    tzinfo=timezone.utc,
                ).isoformat(timespec="seconds")
                with repository.transaction():
                    repository.execute(
                        "UPDATE sales SET sold_at=? WHERE receipt=?",
                        (sold_at, receipt),
                    )
                sale_count += 1

    return {
        "locations": locations,
        "products": len(products),
        "sales": sale_count,
        "start_date": first_day.isoformat(),
        "end_date": today.isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera datos aleatorios en una base demo.")
    parser.add_argument(
        "--database",
        default=str(Path(__file__).resolve().parents[1] / "data" / "retail-demo.db"),
        help="Ruta de la base SQLite destino (predeterminada: backend/data/retail-demo.db).",
    )
    parser.add_argument("--days", type=int, default=30, help="Días de ventas a generar (1 a 90).")
    parser.add_argument("--seed", type=int, help="Semilla numérica opcional para repetir los mismos datos.")
    arguments = parser.parse_args()

    application = create_application(arguments.database)
    try:
        result = create_random_seed(
            application,
            arguments.days,
            random.Random(arguments.seed),
        )
        print(
            f"Semilla completa: {result['products']} productos, "
            f"{result['sales']} ventas entre {result['start_date']} y {result['end_date']}."
        )
    finally:
        application.close()


if __name__ == "__main__":
    main()
