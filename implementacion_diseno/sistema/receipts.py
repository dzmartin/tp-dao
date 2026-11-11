from __future__ import annotations

from datetime import datetime
from textwrap import wrap
from typing import Any


PAGE_WIDTH = 612
PAGE_HEIGHT = 792


def _format_money(amount_cents: int) -> str:
    whole, cents = divmod(abs(amount_cents), 100)
    whole_text = f"{whole:,}".replace(",", ".")
    sign = "-" if amount_cents < 0 else ""
    return f"{sign}$ {whole_text},{cents:02d}"


def _format_datetime(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(value)


def _text_command(text: object, x: int, y: int, size: int, bold: bool = False) -> str:
    font = "F2" if bold else "F1"
    normalized_text = str(text).replace("\r", " ").replace("\n", " ")
    encoded = normalized_text.encode("cp1252", errors="replace").hex().upper()
    return f"BT /{font} {size} Tf 1 0 0 1 {x} {y} Tm <{encoded}> Tj ET"


def _to_unicode_cmap() -> bytes:
    mappings = []
    for code in range(256):
        try:
            character = bytes((code,)).decode("cp1252")
        except UnicodeDecodeError:
            continue
        mappings.append(f"<{code:02X}><{ord(character):04X}>")
    sections = [
        "\n".join(mappings[index : index + 100])
        for index in range(0, len(mappings), 100)
    ]
    cmap = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
    ]
    for section in sections:
        entries = section.count("\n") + 1
        cmap.extend((f"{entries} beginbfchar", section, "endbfchar"))
    cmap.extend(
        (
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        )
    )
    return "\n".join(cmap).encode("ascii")


def crear_pdf_comprobante(
    sale: dict[str, Any],
    items: list[dict[str, Any]],
    payments: list[dict[str, Any]],
) -> bytes:
    pages: list[list[str]] = [[]]
    y = PAGE_HEIGHT - 54

    def begin_page(continuation: bool = False) -> None:
        nonlocal y
        if pages[-1]:
            pages.append([])
        y = PAGE_HEIGHT - 54
        pages[-1].append(
            _text_command(
                "COMPROBANTE DE VENTA"
                if not continuation
                else "COMPROBANTE DE VENTA (continuacion)",
                54,
                y,
                18,
                bold=True,
            )
        )
        y -= 28
        pages[-1].append(
            _text_command(f"Comprobante: {sale['receipt']}", 54, y, 11, bold=True)
        )
        y -= 18
        if not continuation:
            pages[-1].append(
                _text_command(f"Sucursal: {sale['store']}", 54, y, 10)
            )
            y -= 16
            pages[-1].append(
                _text_command(
                    f"Fecha: {_format_datetime(sale['sold_at'])}", 54, y, 10
                )
            )
            pages[-1].append(
                _text_command(f"Estado: {sale['status']}", 350, y, 10)
            )
            y -= 27
        pages[-1].append("0.82 G 54 %d m 558 %d l S 0 G" % (y, y))
        y -= 19
        for label, x in (
            ("PRODUCTO", 54),
            ("CANT.", 374),
            ("PRECIO UNIT.", 430),
            ("SUBTOTAL", 510),
        ):
            pages[-1].append(_text_command(label, x, y, 8, bold=True))
        y -= 16

    def add_line(text: object, x: int = 54, size: int = 9, bold: bool = False) -> None:
        nonlocal y
        if y < 58:
            begin_page(continuation=True)
        pages[-1].append(_text_command(text, x, y, size, bold))
        y -= size + 5

    def add_rule() -> None:
        nonlocal y
        if y < 58:
            begin_page(continuation=True)
        pages[-1].append(f"0.9 G 54 {y} m 558 {y} l S 0 G")
        y -= 12

    begin_page()
    for item in items:
        name_lines = wrap(str(item["name"]), width=47, break_long_words=True) or [""]
        required_height = (len(name_lines) + 1) * 14 + 20
        if y < 58 + required_height:
            begin_page(continuation=True)
        row_y = y
        for index, name_line in enumerate(name_lines):
            add_line(name_line, size=9, bold=index == 0)
        add_line(f"SKU: {item['sku']}", size=8)
        quantity = int(item["sold_quantity"])
        price = int(item["unit_price_cents"])
        pages[-1].append(_text_command(quantity, 374, row_y, 9))
        pages[-1].append(_text_command(_format_money(price), 430, row_y, 9))
        pages[-1].append(
            _text_command(_format_money(quantity * price), 510, row_y, 9)
        )
        add_rule()

    if y < 82:
        begin_page(continuation=True)
    pages[-1].append(_text_command("TOTAL", 430, y, 11, bold=True))
    pages[-1].append(
        _text_command(_format_money(int(sale["total_cents"])), 510, y, 11, bold=True)
    )
    y -= 30
    add_line("PAGOS Y REINTEGROS", size=10, bold=True)
    for payment in payments:
        kind = "Reintegro" if payment["kind"] == "reintegro" else "Pago"
        if y < 78:
            begin_page(continuation=True)
        payment_y = y
        add_line(f"{kind} - {payment['method']}", size=9)
        pages[-1].append(
            _text_command(
                _format_money(int(payment["amount_cents"])), 430, payment_y, 9
            )
        )

    for page_number, commands in enumerate(pages, start=1):
        commands.append(
            _text_command(f"Pagina {page_number} de {len(pages)}", 500, 28, 8)
        )

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding /ToUnicode 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding /ToUnicode 5 0 R >>",
        b"",
    ]
    cmap = _to_unicode_cmap()
    objects[4] = (
        f"<< /Length {len(cmap)} >>\nstream\n".encode("ascii")
        + cmap
        + b"\nendstream"
    )
    page_ids = [6 + index * 2 for index in range(len(pages))]
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    ).encode("ascii")
    for index, commands in enumerate(pages):
        page_id = page_ids[index]
        stream_id = page_id + 1
        stream = ("\n".join(commands) + "\n").encode("ascii")
        objects.extend(
            [
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                    f"/Contents {stream_id} 0 R >>"
                ).encode("ascii"),
                f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
                + stream
                + b"endstream",
            ]
        )

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)
