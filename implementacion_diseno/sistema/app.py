from __future__ import annotations

from pathlib import Path

from .patterns import ReaccionLogistica, RegistroEstrategiasPago
from .services import (
    AcreditadorExistencias,
    AuditorLogistico,
    ServicioCatalogo,
    ServicioInventario,
    ServicioLogistica,
    ServicioReportes,
    ServicioReposicion,
    ServicioVentas,
)
from .storage import BaseDeDatos


class Sistema:
    """Application facade that wires services, repositories and pattern collaborators."""

    def __init__(self, database_path: str | Path = "data/sistema.db") -> None:
        self.db = BaseDeDatos(database_path)
        event_reaction = ReaccionLogistica(
            (AcreditadorExistencias(self.db), AuditorLogistico(self.db))
        )
        self.catalogo = ServicioCatalogo(self.db)
        self.inventario = ServicioInventario(self.db)
        self.reposicion = ServicioReposicion(self.db)
        self.logistica = ServicioLogistica(self.db, event_reaction)
        self.ventas = ServicioVentas(self.db, RegistroEstrategiasPago())
        self.reportes = ServicioReportes(self.db)

    def cerrar(self) -> None:
        self.db.cerrar()

    def __enter__(self) -> Sistema:
        return self

    def __exit__(self, *_: object) -> None:
        self.cerrar()
