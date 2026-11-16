import argparse
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from backend.container import create_application
from backend.datos.demo_accounts import DEMO_ACCOUNTS
from backend.dominio.payment_strategies import PAYMENT_STRATEGIES


PRODUCT_NAMES = (
    "Yerba", "Café", "Arroz", "Fideos", "Galletitas", "Azúcar", "Harina", "Aceite",
    "Leche", "Queso", "Manteca", "Yogur", "Jugo", "Agua mineral", "Gaseosa",
    "Mermelada", "Atún", "Lentejas", "Garbanzos", "Avena", "Cereal", "Chocolate",
    "Jabón", "Detergente", "Lavandina", "Shampoo", "Papel higiénico", "Servilletas",
    "Pan rallado", "Mayonesa",
)
CATEGORIES = ("Almacén", "Bebidas", "Limpieza", "Snacks")
PAYMENT_METHODS = ("efectivo", "tarjeta", "transferencia")
PRODUCT_COUNT = 15


def _shipment_timeline(shipment_day: date, rng: random.Random) -> dict[str, datetime]:
    prepared_at = datetime.combine(
        shipment_day,
        time(hour=rng.randrange(9, 13)),
        tzinfo=timezone.utc,
    )
    sealed_at = prepared_at + timedelta(minutes=30)
    dispatched_at = sealed_at + timedelta(hours=2, minutes=30)
    received_at = dispatched_at + timedelta(hours=rng.randint(2, 4))
    verified_at = received_at + timedelta(minutes=rng.randint(20, 60))
    return {
        "prepared": prepared_at,
        "sealed": sealed_at,
        "dispatched": dispatched_at,
        "received": received_at,
        "verified": verified_at,
    }


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
    for name in rng.sample(PRODUCT_NAMES, k=PRODUCT_COUNT):
        cost_cents = rng.randrange(250, 4500)
        price_cents = cost_cents + rng.randrange(150, 2800)
        sku = f"SKU-{rng.randrange(10_000_000, 100_000_000)}"
        while sku in used_skus:
            sku = f"SKU-{rng.randrange(10_000_000, 100_000_000)}"
        used_skus.add(sku)
        service.create_product(
            sku,
            name,
            rng.choice(categories),
            f"{cost_cents / 100:.2f}",
            f"{price_cents / 100:.2f}",
            20,
            supplier_id,
        )
        products.append({"sku": sku, "price_cents": price_cents})
        service.adjust_stock(central_code, sku, 1200, "Carga semilla aleatoria")

    shipment_lines = {product["sku"]: 500 for product in products}
    today = date.today()
    shipment_day = today
    for index, location in enumerate(locations, start=1):
        timeline = _shipment_timeline(shipment_day, rng)
        order_id = service.create_replenishment(location, shipment_lines)
        package_code = f"B-{suffix}-{index:02d}"
        with repository.transaction():
            repository.execute(
                "UPDATE replenishment_orders SET created_at=? WHERE id=?",
                (
                    (timeline["prepared"] - timedelta(minutes=30)).isoformat(timespec="seconds"),
                    order_id,
                ),
            )
        service.pack(
            order_id,
            package_code,
            f"R-{suffix}-{index:02d}",
            shipment_lines,
            occurred_at=timeline["prepared"].isoformat(timespec="seconds"),
        )
        service.seal_package(
            package_code,
            f"P-{suffix}-{index:02d}",
            occurred_at=timeline["sealed"].isoformat(timespec="seconds"),
        )
        service.dispatch(
            package_code,
            occurred_at=timeline["dispatched"].isoformat(timespec="seconds"),
        )
        service.receive(
            package_code,
            seal_intact=True,
            dispatch_note_ok=True,
            occurred_at=timeline["received"].isoformat(timespec="seconds"),
        )
        service.verify_package(
            package_code,
            occurred_at=timeline["verified"].isoformat(timespec="seconds"),
        )

    rejection_scenarios = ((False, True, None), (True, False, "discard"))
    for index, (seal_intact, dispatch_note_ok, disposition) in enumerate(
        rejection_scenarios,
        start=1,
    ):
        rejected_destination = rng.choice(locations)
        rejected_product = rng.choice(products)
        rejected_lines = {rejected_product["sku"]: rng.randint(1, 5)}
        rejected_order_id = service.create_replenishment(rejected_destination, rejected_lines)
        rejected_timeline = _shipment_timeline(shipment_day, rng)
        rejected_at = rejected_timeline["received"] + timedelta(minutes=rng.randint(15, 60))
        rejected_package_code = f"B-{suffix}-R-{index:02d}"
        with repository.transaction():
            repository.execute(
                "UPDATE replenishment_orders SET created_at=? WHERE id=?",
                (
                    (rejected_timeline["prepared"] - timedelta(minutes=30)).isoformat(
                        timespec="seconds"
                    ),
                    rejected_order_id,
                ),
            )
        service.pack(
            rejected_order_id,
            rejected_package_code,
            f"R-{suffix}-R-{index:02d}",
            rejected_lines,
            occurred_at=rejected_timeline["prepared"].isoformat(timespec="seconds"),
        )
        service.seal_package(
            rejected_package_code,
            f"P-{suffix}-R-{index:02d}",
            occurred_at=rejected_timeline["sealed"].isoformat(timespec="seconds"),
        )
        service.dispatch(
            rejected_package_code,
            occurred_at=rejected_timeline["dispatched"].isoformat(timespec="seconds"),
        )
        service.receive(
            rejected_package_code,
            seal_intact=seal_intact,
            dispatch_note_ok=dispatch_note_ok,
            occurred_at=rejected_timeline["received"].isoformat(timespec="seconds"),
            rejected_at=rejected_at.isoformat(timespec="seconds"),
        )
        if disposition:
            resolved_at = rejected_at + timedelta(minutes=rng.randint(15, 60))
            service.resolve_rejected_package(
                rejected_package_code,
                disposition,
                occurred_at=resolved_at.isoformat(timespec="seconds"),
            )

    remaining = {
        location: {product["sku"]: 500 for product in products}
        for location in locations
    }
    first_day = today - timedelta(days=days - 1)
    sale_count = 0
    used_sale_times: set[datetime] = set()
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
                strategy = PAYMENT_STRATEGIES[method]
                payment_cents = (
                    total_cents
                    if method == "efectivo"
                    else strategy.charge_for(total_cents)
                )
                receipt = service.sell(
                    location,
                    items,
                    {method: f"{payment_cents // 100}.{payment_cents % 100:02d}"},
                )
                sale_datetime = datetime.combine(
                    sale_day,
                    time(hour=rng.randint(9, 20), minute=rng.randrange(60)),
                    tzinfo=timezone.utc,
                )
                while sale_datetime in used_sale_times:
                    sale_datetime = datetime.combine(
                        sale_day,
                        time(hour=rng.randint(9, 20), minute=rng.randrange(60)),
                        tzinfo=timezone.utc,
                    )
                used_sale_times.add(sale_datetime)
                with repository.transaction():
                    repository.execute(
                        "UPDATE sales SET sold_at=? WHERE receipt=?",
                        (sale_datetime.isoformat(timespec="seconds"), receipt),
                    )
                sale_count += 1

    return {
        "locations": locations,
        "products": len(products),
        "sales": sale_count,
        "rejected_packages": len(rejection_scenarios),
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
            f"{result['sales']} ventas, {result['rejected_packages']} bultos rechazados "
            f"entre {result['start_date']} y {result['end_date']}."
        )
    finally:
        application.close()


if __name__ == "__main__":
    main()
