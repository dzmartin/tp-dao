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


# -----------------------------------------------------------------------------
# Version alternativa basada en clases, atributos y constructores de las semanas 4 y 5.
# Todo este bloque esta comentado para comparar; no cambia el codigo que se ejecuta.
# Si se usa esta version, se reemplazan las definiciones de arriba por estas.
# LocationType y PackageState pasan a ser textos simples: Python no valida sus opciones.
#
# class LocationType:
#     DISTRIBUTION_CENTER = "CD"
#     STORE = "LOCAL"
#
#
# class PackageState:
#     PREPARED = "Preparado"
#     SEALED = "Precintado"
#     IN_TRANSIT = "En tránsito"
#     RECEIVED = "Recibido"
#     VERIFIED = "Verificado"
#     REJECTED = "Rechazado"
#
#
# class Location:
#     def __init__(self, id, code, name, type):
#         self.id = id
#         self.code = code
#         self.name = name
#         self.type = type
#
#
# class Product:
#     def __init__(self, id, sku, name, category, cost_cents, price_cents,
#                  minimum_stock, supplier_id=None):
#         self.id = id
#         self.sku = sku
#         self.name = name
#         self.category = category
#         self.cost_cents = cost_cents
#         self.price_cents = price_cents
#         self.minimum_stock = minimum_stock
#         self.supplier_id = supplier_id
#
# En esta alternativa, los objetos se pueden modificar despues de crearlos.
# Tampoco se generan automaticamente la comparacion por atributos ni el __repr__.
#Solo la primera propiedad: que los objetos no se puedan modificar después de crearlos sí se debe a frozen=True.

#Las otras dos tienen causas distintas:

#@dataclass genera automáticamente la comparación por atributos. Una clase normal no lo hace, salvo que implementes __eq__.
#En la alternativa, supplier_id=None hace que ese argumento sea opcional. La anotación original supplier_id: int | None solo indica que puede valer None; como no tenía un valor por defecto, igual había que pasar el argumento al crear el producto.