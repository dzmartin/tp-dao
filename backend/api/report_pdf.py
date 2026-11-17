from datetime import date

from backend.api.pdf_utils import build_pdf, pdf_money, pdf_text


def _wrapped_lines(value: str, max_chars: int) -> list[str]:
    words = value.split()
    lines = []
    current = ""
    for word in words:
        while len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        if current and len(current) + len(word) + 1 > max_chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


def _date_label(value: str) -> str:
    return date.fromisoformat(value).strftime("%d/%m/%Y")


def report_summary_pdf(
    report: dict,
    requester: str,
    role: str,
    generated_at: str,
    start_date: str,
    end_date: str,
    locations: list[dict],
    include_logistics: bool,
    include_categories: bool,
) -> bytes:
    pages: list[bytes] = []
    commands: list[str] = []
    y = 0

    def begin_page() -> None:
        nonlocal commands, y
        if commands:
            pages.append("\n".join(commands).encode("ascii"))
        commands = []
        y = 794
        commands.append(f"BT /F2 10 Tf 48 {y} Td (La pulga mercado | RESUMEN DE REPORTES) Tj ET")
        y -= 28

    def ensure_space(height: int) -> None:
        if y - height < 52:
            begin_page()

    def add_text(value: object, size: int = 9, bold: bool = False, indent: int = 48) -> None:
        nonlocal y
        font = "F2" if bold else "F1"
        max_chars = max(40, int(495 / (size * 0.53)))
        for line in _wrapped_lines(str(value), max_chars):
            ensure_space(size + 5)
            commands.append(
                f"BT /{font} {size} Tf {indent} {y} Td ({pdf_text(line)}) Tj ET"
            )
            y -= size + 5

    def add_section(title: str) -> None:
        nonlocal y
        ensure_space(28)
        y -= 3
        commands.append(f"0.78 0.82 0.79 RG 48 {y} m 547 {y} l S 0 0 0 RG")
        y -= 17
        add_text(title, size=11, bold=True)
        y -= 3

    begin_page()
    add_text("RESUMEN DE TODOS LOS REPORTES", size=19, bold=True)
    add_text(f"Período: {_date_label(start_date)} al {_date_label(end_date)}", size=10, bold=True)
    add_text(f"Solicitado por: {requester} · Perfil: {role}")
    add_text(f"Generado el: {generated_at}")
    add_text(
        "Locales incluidos: "
        + (", ".join(f"{location['name']} ({location['code']})" for location in locations) or "ninguno")
    )
    for location in locations:
        add_text(
            f"{location['name']} ({location['code']}) · Domicilio: "
            f"{location.get('address') or 'No informado'} · CUIT: "
            f"{location.get('cuit') or 'No informado'}",
            size=8,
            indent=62,
        )

    sales = report["sales"]
    daily_sales = report["daily_sales"]
    best_sellers = report["best_sellers"][:10]
    category_sales = report["category_sales"]
    inventory = report["inventory"]
    logistics = report["logistics"]
    movements = report["movements"]
    net_sales = sum(row["net_cents"] for row in sales)
    low_stock = [row for row in inventory if row["at_minimum"]]
    inventory_valuation = sum(row["valuation_cents"] for row in inventory)
    sold_units = sum(row["net_units"] for row in report["best_sellers"])
    positive_units = sum(max(0, row["quantity_delta"]) for row in movements)
    negative_units = sum(abs(min(0, row["quantity_delta"])) for row in movements)
    net_movement_value = sum(row["valuation_delta_cents"] for row in movements)

    add_section("Indicadores principales")
    add_text(f"Ventas netas del período: {pdf_money(net_sales)}")
    add_text(f"Unidades vendidas netas: {sold_units}")
    add_text(
        f"Productos en inventario: {len(inventory)} · En o por debajo del mínimo: {len(low_stock)}"
    )
    add_text(f"Valuación del inventario reportado: {pdf_money(inventory_valuation)}")
    add_text(
        f"Movimientos de inventario: {len(movements)} · Entradas: {positive_units} u. · "
        f"Salidas: {negative_units} u. · Valuación neta: {pdf_money(net_movement_value)}"
    )
    if include_logistics:
        average = logistics["average_transit_hours"]
        average_label = "No disponible" if average is None else f"{average:.1f} horas"
        add_text(
            f"Bultos verificados: {logistics['verified_packages']} · Rechazados: "
            f"{logistics['rejected_count']} · Tránsito promedio: {average_label}"
        )
        add_text(
            f"Mermas: {logistics['waste_quantity']} u. · "
            f"Valuación: {pdf_money(logistics['waste_valuation_cents'])}"
        )

    add_section("Ventas netas por medio de pago")
    totals_by_method: dict[str, int] = {}
    for row in sales:
        totals_by_method[row["payment_method"]] = (
            totals_by_method.get(row["payment_method"], 0) + row["net_cents"]
        )
    if totals_by_method:
        for method, cents in sorted(totals_by_method.items()):
            add_text(f"{method.capitalize()}: {pdf_money(cents)}", indent=62)
    else:
        add_text("Sin ventas ni devoluciones en el período.")

    add_section("Ventas netas por día")
    if daily_sales:
        for row in daily_sales:
            add_text(f"{_date_label(row['activity_date'])}: {pdf_money(row['net_cents'])}", indent=62)
    else:
        add_text("Sin ventas ni devoluciones en el período.")

    add_section("Productos más vendidos y margen · Top 10")
    if best_sellers:
        for row in best_sellers:
            add_text(
                f"{row['name']} · {row['net_units']} u. · Venta neta: "
                f"{pdf_money(row['net_revenue_cents'])} · Margen: {pdf_money(row['margin_cents'])}",
                indent=62,
            )
    else:
        add_text("Sin productos vendidos netos en el período.")

    if include_categories:
        add_section("Ventas por categoría")
        if category_sales:
            for row in category_sales:
                add_text(
                    f"{row['category']} · {row['net_units']} u. · "
                    f"Venta neta: {pdf_money(row['net_revenue_cents'])}",
                    indent=62,
                )
        else:
            add_text("Sin ventas por categoría en el período.")

    add_section("Existencias y stock mínimo")
    if inventory:
        for row in inventory:
            status = " · ALERTA DE MÍNIMO" if row["at_minimum"] else ""
            add_text(
                f"{row['location']} · {row['sku']} · {row['name']} · Stock: {row['quantity']} · "
                f"Mínimo: {row['minimum_stock']} · Valuación: {pdf_money(row['valuation_cents'])}{status}",
                indent=62,
            )
    else:
        add_text("Sin registros de inventario.")

    if include_logistics:
        add_section("Bultos rechazados")
        if logistics["rejected_packages"]:
            for row in logistics["rejected_packages"]:
                add_text(
                    f"{row['code']} · Destino: {row['destination']} · Remito: "
                    f"{row['dispatch_note']} · Motivo: {row.get('reason') or 'No informado'}",
                    indent=62,
                )
        else:
            add_text("Sin bultos rechazados en el período.")

        add_section("Mermas registradas")
        if logistics["waste_movements"]:
            for row in logistics["waste_movements"]:
                estimated = " · Valuación estimada" if row["cost_is_estimated"] else ""
                add_text(
                    f"{_date_label(row['waste_date'])} · {row['location']} · {row['sku']} "
                    f"{row['name']} · {row['waste_quantity']} u. · "
                    f"{pdf_money(row['valuation_cents'])}{estimated}",
                    indent=62,
                )
        else:
            add_text("Sin mermas registradas en el período.")

    add_section("Movimientos de inventario")
    if movements:
        for row in movements:
            estimated = " · Valuación estimada" if row["cost_is_estimated"] else ""
            add_text(
                f"{_date_label(row['movement_date'])} · {row['location']} · "
                f"{row['sku']} {row['name']} · {row['movement_type']} · "
                f"{row['quantity_delta']:+} u. · "
                f"{pdf_money(row['valuation_delta_cents'])}{estimated}",
                indent=62,
            )
    else:
        add_text("Sin movimientos de inventario en el período.")

    pages.append("\n".join(commands).encode("ascii"))
    total_pages = len(pages)
    pages = [
        content
        + f"\nBT /F1 8 Tf 490 28 Td ({pdf_text(f'Página {page_number} de {total_pages}')}) Tj ET".encode(
            "ascii"
        )
        for page_number, content in enumerate(pages, start=1)
    ]
    return build_pdf(pages)
