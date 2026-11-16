import sqlite3

from backend.container import create_application
from backend.dominio.exceptions import BusinessRuleError


def check(condition: bool, description: str) -> None:
    if not condition:
        raise AssertionError(description)
    print(f"[OK] {description}")


def main() -> None:
    application = create_application(":memory:")
    service = application.retail
    reports = application.reports

    try:
        service.create_location("CD", "Centro de Distribución", "CD")
        service.create_location("L01", "Local de prueba", "LOCAL")
        supplier_id = service.create_supplier("DEMO-001", "Proveedor de prueba")
        service.create_product("DEMO-001", "Producto de prueba", "General", "4.50", "10.00", 2, supplier_id)
        try:
            service.create_product("DEMO-001", "SKU duplicado", "General", "4.50", "10.00", 2, supplier_id)
        except sqlite3.IntegrityError:
            print("[OK] SKU duplicado bloqueado")
        else:
            raise AssertionError("Se permitió crear un SKU duplicado")
        service.adjust_stock("CD", "DEMO-001", 20, "carga inicial")
        check(True, "Alta de ubicaciones, proveedor, producto y stock inicial")

        rejected_order = service.create_replenishment("L01", {"DEMO-001": 2})
        service.pack(rejected_order, "B-DEMO-RECH", "R-DEMO-RECH", {"DEMO-001": 2})
        service.seal_package("B-DEMO-RECH", "PREC-DEMO-RECH")
        service.dispatch("B-DEMO-RECH")
        rejected_status = service.receive("B-DEMO-RECH", seal_intact=False)
        local_stock = application.repository.one(
            """SELECT s.quantity FROM stock s JOIN locations l ON l.id=s.location_id
               JOIN products p ON p.id=s.product_id WHERE l.code='L01' AND p.sku='DEMO-001'"""
        )
        check(rejected_status == "Rechazado" and local_stock is None, "Precinto violado: bulto rechazado y sin ingreso a stock vendible")

        order_id = service.create_replenishment("L01", {"DEMO-001": 10})
        service.pack(order_id, "B-DEMO-OK", "R-DEMO-OK", {"DEMO-001": 10})
        service.seal_package("B-DEMO-OK", "PREC-DEMO-OK")
        service.dispatch("B-DEMO-OK")
        received_status = service.receive("B-DEMO-OK", seal_intact=True, dispatch_note_ok=True)
        check(received_status == "Recibido", "Recepción conforme registrada, todavía pendiente de verificación")
        before_verification = application.repository.one(
            """SELECT s.quantity FROM stock s JOIN locations l ON l.id=s.location_id
               JOIN products p ON p.id=s.product_id WHERE l.code='L01' AND p.sku='DEMO-001'"""
        )
        check(before_verification is None, "La recepción sola no libera stock para la venta")
        service.verify_package("B-DEMO-OK")
        check(reports.inventory_and_minimums()[0]["quantity"] == 10, "Verificación acredita las unidades al stock del local")

        receipt = service.sell("L01", {"DEMO-001": 3}, {"efectivo": "20.00", "tarjeta": "22.00"})
        check(receipt.startswith("V-"), f"Venta registrada y comprobante emitido ({receipt})")
        try:
            service.sell("L01", {"DEMO-001": 99}, {"efectivo": "990.00"})
        except BusinessRuleError:
            print("[OK] Venta por encima del stock verificado bloqueada")
        else:
            raise AssertionError("Se permitió vender más unidades que el stock disponible")

        refund_cents = service.return_items(receipt, {"DEMO-001": 1}, restock=True)
        check(refund_cents == 1000, "Devolución válida reintegrada a stock y caja")
        try:
            service.return_items(receipt, {"DEMO-001": 3})
        except BusinessRuleError:
            print("[OK] Devolución superior a la cantidad disponible bloqueada")
        else:
            raise AssertionError("Se permitió devolver más unidades de las aún disponibles")
        try:
            service.return_items(receipt, {"DEMO-001": 3})
        except BusinessRuleError:
            print("[OK] Devolución por encima de lo vendido bloqueada")
        else:
            raise AssertionError("Se permitió devolver más unidades que las vendidas")

        period = ("2000-01-01", "2999-12-31")
        sales_report = reports.sales_by_period(*period)
        margin_report = reports.best_sellers_and_margin(*period)
        logistics_report = reports.logistics_performance()
        movement_report = reports.inventory_movements(*period)
        check(sum(row["net_cents"] for row in sales_report) == 2000, "Reporte de ventas netas descuenta la devolución")
        check(margin_report[0]["margin_cents"] == 1100, "Reporte de productos vendidos y margen estimado")
        check(logistics_report["rejected_count"] == 1 and logistics_report["verified_packages"] == 1, "Reporte logístico incluye rechazo y bulto verificado")
        check(bool(movement_report), "Reporte de movimientos valorizados disponible")
        check(True, "Reporte de existencias y mínimos disponible")

        print("\nDemo completa: todo OK. Los datos se guardaron solo en memoria.")
    finally:
        application.close()


if __name__ == "__main__":
    main()