import sqlite3

from container import Application
from exceptions import BusinessRuleError


def _quantities(prompt: str) -> dict[str, int]:
	value = input(prompt).strip()
	return {sku.strip(): int(quantity) for sku, quantity in (item.split(":", 1) for item in value.split(",") if item.strip())}


def _payments() -> dict[str, str]:
	value = input("Cobros (medio:importe separados por coma; efectivo/tarjeta/transferencia): ").strip()
	return {method.strip(): amount.strip() for method, amount in (item.split(":", 1) for item in value.split(",") if item.strip())}


def _show_reports(application: Application) -> None:
	start = input("Desde (AAAA-MM-DD): ").strip()
	end = input("Hasta (AAAA-MM-DD): ").strip()
	print("\nVentas netas por fecha/local/medio:")
	for row in application.reports.sales_by_period(start, end):
		print(row)
	print("\nProductos vendidos y margen estimado:")
	for row in application.reports.best_sellers_and_margin(start, end):
		print(row)
	print("\nExistencias y mínimos:")
	for row in application.reports.inventory_and_minimums():
		print(row)
	logistics = application.reports.logistics_performance()
	print("\nRendimiento logístico:", logistics)
	print("\nMovimientos y valorización:")
	for row in application.reports.inventory_movements(start, end):
		print(row)


def _run_action(option: str, application: Application) -> bool:
	service = application.retail
	if option == "1":
		location_type = input("Tipo (CD/LOCAL): ").strip().upper()
		print("Ubicación creada, id:", service.create_location(input("Código: "), input("Nombre: "), location_type))
	elif option == "2":
		print("Proveedor creado, id:", service.create_supplier(input("CUIT/identificador: "), input("Nombre: "), input("Teléfono: "), input("Email: ")))
	elif option == "3":
		supplier = input("Id de proveedor (vacío si no aplica): ").strip()
		product_id = service.create_product(
			input("SKU: "), input("Nombre: "), input("Categoría: "), input("Costo: "), input("Precio: "),
			int(input("Stock mínimo: ")), int(supplier) if supplier else None,
		)
		print("Producto creado, id:", product_id)
	elif option == "4":
		service.adjust_stock(input("Código de ubicación: "), input("SKU: "), int(input("Cantidad (negativa para egreso): ")), input("Motivo: "))
		print("Stock actualizado.")
	elif option == "5":
		print("Orden creada, id:", service.create_replenishment(input("Local destino: "), _quantities("Necesidades SKU:cantidad: ")))
	elif option == "6":
		package_id = service.pack(int(input("Id de orden: ")), input("Código de bulto: "), input("Remito: "), _quantities("Contenido SKU:cantidad: "))
		print("Bulto preparado, id:", package_id)
	elif option == "7":
		service.seal_package(input("Código de bulto: "), input("Precinto: "))
		print("Bulto precintado.")
	elif option == "8":
		service.dispatch(input("Código de bulto: "))
		print("Despacho registrado.")
	elif option == "9":
		code = input("Código de bulto: ")
		seal_ok = input("¿Precinto intacto? (s/n): ").strip().lower() == "s"
		note_ok = input("¿Remito conforme? (s/n): ").strip().lower() == "s"
		state = service.receive(code, seal_ok, note_ok)
		print("Estado:", state)
		if state == "Recibido" and input("¿Verificar contenido y liberar stock? (s/n): ").strip().lower() == "s":
			service.verify_package(code)
			print("Verificado; stock disponible actualizado.")
	elif option == "10":
		receipt = service.sell(input("Local: "), _quantities("Venta SKU:cantidad: "), _payments())
		print("Comprobante:", receipt)
	elif option == "11":
		receipt = input("Comprobante de venta: ")
		quantities = _quantities("Devolución SKU:cantidad: ")
		restock = input("¿Mercadería inspeccionada y apta para reventa? (s/n): ").strip().lower() == "s"
		print("Importe reintegrado (centavos):", service.return_items(receipt, quantities, restock))
	elif option == "12":
		_show_reports(application)
	elif option == "0":
		return False
	else:
		print("Opción desconocida.")
	return True


def run_menu(application: Application) -> None:
	actions = (
		"1. Alta de sucursal/CD", "2. Alta de proveedor", "3. Alta de producto",
		"4. Ajuste/ingreso de stock", "5. Crear orden de reposición", "6. Preparar bulto",
		"7. Precintar bulto", "8. Despachar bulto", "9. Recibir y verificar bulto",
		"10. Registrar venta y cobros", "11. Registrar devolución", "12. Reportes", "0. Salir",
	)
	while True:
		print("\n=== Ventas, inventario y logística ===")
		print("\n".join(actions))
		try:
			if not _run_action(input("Opción: ").strip(), application):
				break
		except (BusinessRuleError, sqlite3.IntegrityError, ValueError) as error:
			print("Operación no realizada:", error)
