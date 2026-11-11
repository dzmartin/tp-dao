from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReglaNegocioError(ValueError):
    """Raised when an operation violates a domain rule."""


class TipoEstablecimiento(StrEnum):
    CENTRO_DISTRIBUCION = "CD"
    SUCURSAL = "SUCURSAL"


class EstadoBulto(StrEnum):
    PREPARADO = "Preparado"
    PRECINTADO = "Precintado"
    EN_TRANSITO = "En tránsito"
    RECIBIDO = "Recibido"
    VERIFICADO = "Verificado"
    RECHAZADO = "Rechazado"
    CANCELADO = "Cancelado"


class TipoMovimiento(StrEnum):
    AJUSTE = "ajuste"
    DESPACHO = "despacho"
    RECEPCION = "recepcion"
    DEVOLUCION = "devolucion"
    VENTA = "venta"


@dataclass(frozen=True)
class Establecimiento:
    id: int
    codigo: str
    nombre: str
    tipo: TipoEstablecimiento


@dataclass(frozen=True)
class Producto:
    id: int
    sku: str
    nombre: str
    categoria: str
    costo_centavos: int
    precio_centavos: int
    proveedor_ids: tuple[int, ...]


@dataclass(frozen=True)
class LineaPedido:
    sku: str
    cantidad: int


@dataclass(frozen=True)
class EventoBulto:
    bulto_id: int
    estado: EstadoBulto
    fecha: str
    detalle: str


@dataclass(frozen=True)
class VentaRegistrada:
    id: int
    comprobante: str
    total_centavos: int


@dataclass(frozen=True)
class DevolucionRegistrada:
    id: int
    importe_centavos: int
    vuelve_al_stock: bool
