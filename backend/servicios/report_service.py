from backend.persistencia.sqlite_repository import SQLiteRepository


class ReportService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def sales_by_period(self, start_date: str, end_date: str, location_code: str | None = None) -> list[dict]:
        rows = self.repository.all(
            """SELECT activity_date, location, payment_method, SUM(amount_cents) AS net_cents
               FROM (
                   SELECT substr(s.sold_at,1,10) AS activity_date, l.code AS location,
                          p.method AS payment_method, p.amount_cents AS amount_cents
                   FROM payments p JOIN sales s ON s.id=p.sale_id JOIN locations l ON l.id=s.location_id
                   UNION ALL
                   SELECT substr(r.returned_at,1,10), l.code, rp.method, -rp.amount_cents
                   FROM return_payments rp JOIN returns r ON r.id=rp.return_id
                   JOIN sales s ON s.id=r.sale_id JOIN locations l ON l.id=s.location_id
               )
               WHERE activity_date BETWEEN ? AND ? AND (? IS NULL OR location=?)
               GROUP BY activity_date, location, payment_method
               ORDER BY activity_date, location, payment_method""",
            (start_date, end_date, location_code, location_code),
        )
        return [dict(row) for row in rows]

    def net_sales_by_day(
        self, start_date: str, end_date: str, location_code: str | None = None
    ) -> list[dict]:
        daily_totals: dict[str, int] = {}
        for row in self.sales_by_period(start_date, end_date, location_code):
            daily_totals[row["activity_date"]] = (
                daily_totals.get(row["activity_date"], 0) + row["net_cents"]
            )
        return [
            {"activity_date": activity_date, "net_cents": net_cents}
            for activity_date, net_cents in sorted(daily_totals.items())
        ]

    def best_sellers_and_margin(
        self, start_date: str, end_date: str, location_code: str | None = None
    ) -> list[dict]:
        rows = self.repository.all(
            """SELECT p.sku, p.name, SUM(sl.quantity-sl.returned_quantity) AS net_units,
                      SUM((sl.unit_price_cents-sl.unit_cost_cents)*(sl.quantity-sl.returned_quantity)) AS margin_cents,
                      SUM(sl.unit_price_cents*(sl.quantity-sl.returned_quantity)) AS net_revenue_cents
               FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id JOIN products p ON p.id=sl.product_id
               JOIN locations l ON l.id=s.location_id
               WHERE substr(s.sold_at,1,10) BETWEEN ? AND ? AND (? IS NULL OR l.code=?)
               GROUP BY p.id HAVING net_units > 0
               ORDER BY net_units DESC, margin_cents DESC""",
            (start_date, end_date, location_code, location_code),
        )
        return [dict(row) for row in rows]

    def inventory_and_minimums(self, location_code: str | None = None) -> list[dict]:
        rows = self.repository.all(
            """SELECT l.code AS location, p.sku, p.name, COALESCE(s.quantity,0) AS quantity,
                      p.minimum_stock, COALESCE(s.quantity,0) <= p.minimum_stock AS at_minimum,
                      COALESCE(s.quantity,0)*p.cost_cents AS valuation_cents
               FROM locations l CROSS JOIN products p LEFT JOIN stock s
                 ON s.location_id=l.id AND s.product_id=p.id
               WHERE l.type='LOCAL' AND (? IS NULL OR l.code=?)
               ORDER BY l.code,p.sku""",
            (location_code, location_code),
        )
        return [dict(row) for row in rows]

    def logistics_performance(
        self,
        start_date: str = "0001-01-01",
        end_date: str = "9999-12-31",
        location_code: str | None = None,
    ) -> dict:
        rejected = self.repository.all(
            """SELECT p.code, p.dispatch_note, p.seal, p.status, l.code AS destination,
                      p.dispatched_at, p.received_at,
                      (SELECT detail FROM package_events e WHERE e.package_id=p.id AND e.state='Rechazado' ORDER BY e.id DESC LIMIT 1) AS reason
               FROM packages p JOIN replenishment_orders o ON o.id=p.order_id
               JOIN locations l ON l.id=o.destination_id
               WHERE p.status='Rechazado' AND substr(p.received_at,1,10) BETWEEN ? AND ?
                 AND (? IS NULL OR l.code=?)
               ORDER BY p.received_at DESC""",
            (start_date, end_date, location_code, location_code),
        )
        transit = self.repository.one(
            """SELECT COUNT(*) AS verified_packages,
                      AVG((julianday(received_at)-julianday(dispatched_at))*24.0) AS average_transit_hours
               FROM packages p JOIN replenishment_orders o ON o.id=p.order_id
               JOIN locations l ON l.id=o.destination_id
               WHERE p.status='Verificado' AND p.dispatched_at IS NOT NULL AND p.received_at IS NOT NULL
                 AND substr(p.dispatched_at,1,10) BETWEEN ? AND ? AND (? IS NULL OR l.code=?)""",
            (start_date, end_date, location_code, location_code),
        )
        waste = self.repository.all(
            """SELECT substr(m.occurred_at,1,10) AS waste_date, l.code AS location,
                      p.sku, p.name, m.reference, m.waste_quantity,
                      m.waste_quantity*m.unit_cost_cents AS valuation_cents,
                      m.cost_is_estimated
               FROM inventory_movements m JOIN locations l ON l.id=m.location_id
               JOIN products p ON p.id=m.product_id
               WHERE m.waste_quantity>0 AND substr(m.occurred_at,1,10) BETWEEN ? AND ?
                 AND (? IS NULL OR l.code=?)
               ORDER BY m.occurred_at,m.id""",
            (start_date, end_date, location_code, location_code),
        )
        return {
            "rejected_count": len(rejected),
            "rejected_packages": [dict(row) for row in rejected],
            "verified_packages": transit["verified_packages"],
            "average_transit_hours": transit["average_transit_hours"],
            "waste_count": len(waste),
            "waste_quantity": sum(row["waste_quantity"] for row in waste),
            "waste_valuation_cents": sum(row["valuation_cents"] for row in waste),
            "waste_movements": [dict(row) for row in waste],
        }

    def inventory_movements(
        self, start_date: str, end_date: str, location_code: str | None = None
    ) -> list[dict]:
        rows = self.repository.all(
            """SELECT substr(m.occurred_at,1,10) AS movement_date, l.code AS location,
                      p.sku, p.name, m.movement_type, m.quantity_delta, m.reference,
                      m.quantity_delta*m.unit_cost_cents AS valuation_delta_cents,
                      m.unit_cost_cents, m.cost_is_estimated
               FROM inventory_movements m JOIN locations l ON l.id=m.location_id
               JOIN products p ON p.id=m.product_id
               WHERE movement_date BETWEEN ? AND ? AND (? IS NULL OR l.code=?)
               ORDER BY m.occurred_at,m.id""",
            (start_date, end_date, location_code, location_code),
        )
        return [dict(row) for row in rows]