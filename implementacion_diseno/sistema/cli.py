from __future__ import annotations

import argparse
import json
import uuid
from datetime import date
from typing import Any

from .app import Sistema
from .domain import TipoEstablecimiento


def _json_default(value: Any) -> str:
    return str(value)


def demo(database: str) -> dict[str, object]:
    run = uuid.uuid4().hex[:8].upper()
    with Sistema(database) as system:
        center = system.catalogo.crear_establecimiento(
            f"CD-{run}", "Centro de distribución demo", TipoEstablecimiento.CENTRO_DISTRIBUCION
        )
        store = system.catalogo.crear_establecimiento(
            f"LOC-{run}", "Sucursal demo", TipoEstablecimiento.SUCURSAL
        )
        supplier_id = system.catalogo.crear_proveedor(f"FISCAL-{run}", "Proveedor demo")
        system.catalogo.crear_producto(
            f"SKU-{run}", "Producto demo", "General", 500, 1000, supplier_id
        )
        system.inventario.ajustar_stock_centro(
            center.id, f"SKU-{run}", 10, "Stock inicial de demostración"
        )
        batch_id, orders = system.reposicion.consolidar_necesidades(
            center.id, {store.id: {f"SKU-{run}": 5}}
        )
        order_id = orders[store.id]
        package_id = system.logistica.preparar_bulto(
            order_id, f"REM-{run}", f"SELLO-{run}", {f"SKU-{run}": 5}
        )
        system.logistica.precintar(package_id)
        system.logistica.despachar(package_id)
        system.logistica.recibir(package_id)
        package_state = system.logistica.verificar_recepcion(
            package_id, intacto=True, coincide_remito=True
        )
        sale = system.ventas.registrar_venta(
            store.id, {f"SKU-{run}": 2}, {"efectivo": 2000}
        )
        return {
            "base": database,
            "centro_id": center.id,
            "sucursal_id": store.id,
            "sku": f"SKU-{run}",
            "lote_reposicion_id": batch_id,
            "orden_id": order_id,
            "bulto_id": package_id,
            "estado_bulto": package_state.value,
            "venta_id": sale.id,
            "comprobante": sale.comprobante,
            "total_venta_centavos": sale.total_centavos,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sistema de ventas e inventario del Tema 3."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init", help="Crear la base SQLite.")
    init_parser.add_argument("--database", default="data/sistema.db")
    demo_parser = commands.add_parser(
        "demo", help="Ejecutar un flujo completo con datos de demostración."
    )
    demo_parser.add_argument("--database", default="data/demo.db")
    reports_parser = commands.add_parser(
        "reports", help="Consultar los reportes obligatorios."
    )
    reports_parser.add_argument("--database", default="data/sistema.db")
    reports_parser.add_argument(
        "--from-date", default=date.today().isoformat(), dest="from_date"
    )
    reports_parser.add_argument(
        "--to-date", default=date.today().isoformat(), dest="to_date"
    )
    web_parser = commands.add_parser(
        "web", help="Iniciar la interfaz web del sistema."
    )
    web_parser.add_argument("--database", default="data/sistema.db")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "init":
        with Sistema(args.database):
            pass
        print(f"Base inicializada: {args.database}")
        return
    if args.command == "demo":
        print(json.dumps(demo(args.database), ensure_ascii=False, indent=2))
        return
    if args.command == "web":
        from .web import serve

        serve(args.database, args.host, args.port)
        return
    with Sistema(args.database) as system:
        result = {
            "ventas_e_ingresos": system.reportes.ventas_e_ingresos(
                args.from_date, args.to_date
            ),
            "productos_y_margen": system.reportes.productos_y_margen(
                args.from_date, args.to_date
            ),
            "existencias_y_minimos": system.reportes.existencias_y_minimos(),
            "bultos_rechazados": system.reportes.bultos_rechazados(
                args.from_date, args.to_date
            ),
            "transito_promedio": system.reportes.transito_promedio(
                args.from_date, args.to_date
            ),
            "movimientos_y_valorizacion": {
                row["name"]: system.reportes.movimientos_y_valorizacion(
                    int(row["id"]), args.from_date, args.to_date
                )
                for row in system.db.connection.execute(
                    "SELECT id, name FROM establishments ORDER BY name"
                ).fetchall()
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
