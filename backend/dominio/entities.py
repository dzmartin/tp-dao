# Herramientas de la biblioteca estandar para crear clases de datos y opciones de texto.
from dataclasses import dataclass
from enum import StrEnum


# StrEnum define opciones limitadas cuyos valores tambien se comportan como texto.
class LocationType(StrEnum):
    DISTRIBUTION_CENTER = "CD"
    STORE = "LOCAL"


# Etiquetas para los estados posibles de un bulto; no controlan las transiciones.
class PackageState(StrEnum):
    PREPARED = "Preparado"
    SEALED = "Precintado"
    CANCELLED = "Cancelado"
    IN_TRANSIT = "En tránsito"
    RECEIVED = "Recibido"
    VERIFIED = "Verificado"
    REJECTED = "Rechazado"


# dataclass genera automaticamente el constructor (__init__) usando estos atributos.
# frozen=True impide cambiar los atributos de una instancia despues de crearla.
@dataclass(frozen=True)
class Location:
    # La anotacion va despues de :, por ejemplo id: int; describe el tipo esperado.
    id: int
    code: str
    name: str
    type: LocationType


# Esta entidad tambien contiene datos y recibe sus valores mediante el constructor generado.
@dataclass(frozen=True)
class Product:
    id: int
    sku: str
    name: str
    category: str
    # Los importes se guardan en centavos para evitar imprecisiones de punto flotante.
    cost_cents: int
    price_cents: int
    minimum_stock: int
    # Puede ser un entero o None si el producto no tiene proveedor asignado.
    supplier_id: int | None
