from __future__ import annotations

from typing import Protocol

from .domain import (
    Establecimiento,
    EstadoBulto,
    EventoBulto,
    ReglaNegocioError,
    TipoEstablecimiento,
)


class FabricaEstablecimientos:
    @staticmethod
    def crear(
        establishment_id: int,
        code: str,
        name: str,
        kind: TipoEstablecimiento,
    ) -> Establecimiento:
        if kind is TipoEstablecimiento.CENTRO_DISTRIBUCION:
            return CentroDistribucion(establishment_id, code, name)
        if kind is TipoEstablecimiento.SUCURSAL:
            return Sucursal(establishment_id, code, name)
        raise ReglaNegocioError(f"Tipo de establecimiento no soportado: {kind}")


class CentroDistribucion(Establecimiento):
    def __init__(self, establishment_id: int, code: str, name: str) -> None:
        super().__init__(
            establishment_id, code, name, TipoEstablecimiento.CENTRO_DISTRIBUCION
        )


class Sucursal(Establecimiento):
    def __init__(self, establishment_id: int, code: str, name: str) -> None:
        super().__init__(establishment_id, code, name, TipoEstablecimiento.SUCURSAL)


class EstrategiaCobro(Protocol):
    medio: str

    def procesar(self, importe_centavos: int) -> None: ...


class PagoEfectivo:
    medio = "efectivo"

    def procesar(self, importe_centavos: int) -> None:
        _validar_importe(importe_centavos)


class PagoTarjeta:
    medio = "tarjeta"

    def procesar(self, importe_centavos: int) -> None:
        _validar_importe(importe_centavos)


class PagoTransferencia:
    medio = "transferencia"

    def procesar(self, importe_centavos: int) -> None:
        _validar_importe(importe_centavos)


class PagoCuentaCorriente:
    medio = "cuenta corriente"

    def procesar(self, importe_centavos: int) -> None:
        _validar_importe(importe_centavos)


def _validar_importe(importe_centavos: int) -> None:
    if importe_centavos <= 0:
        raise ReglaNegocioError("El importe del pago debe ser positivo.")


class RegistroEstrategiasPago:
    def __init__(self) -> None:
        strategies = (
            PagoEfectivo(),
            PagoTarjeta(),
            PagoTransferencia(),
            PagoCuentaCorriente(),
        )
        self._strategies: dict[str, EstrategiaCobro] = {
            strategy.medio: strategy for strategy in strategies
        }

    def obtener(self, medio: str) -> EstrategiaCobro:
        try:
            return self._strategies[medio.strip().lower()]
        except KeyError as error:
            raise ReglaNegocioError(f"Medio de pago no admitido: {medio}") from error


class EstadoBultoState(Protocol):
    estado: EstadoBulto

    def siguiente(
        self,
        intacto: bool | None = None,
        coincide_remito: bool | None = None,
    ) -> EstadoBulto: ...


class _EstadoPreparado:
    estado = EstadoBulto.PREPARADO

    def siguiente(
        self,
        intacto: bool | None = None,
        coincide_remito: bool | None = None,
    ) -> EstadoBulto:
        return EstadoBulto.PRECINTADO


class _EstadoPrecintado:
    estado = EstadoBulto.PRECINTADO

    def siguiente(
        self,
        intacto: bool | None = None,
        coincide_remito: bool | None = None,
    ) -> EstadoBulto:
        return EstadoBulto.EN_TRANSITO


class _EstadoEnTransito:
    estado = EstadoBulto.EN_TRANSITO

    def siguiente(
        self,
        intacto: bool | None = None,
        coincide_remito: bool | None = None,
    ) -> EstadoBulto:
        return EstadoBulto.RECIBIDO


class _EstadoRecibido:
    estado = EstadoBulto.RECIBIDO

    def siguiente(
        self,
        intacto: bool | None = None,
        coincide_remito: bool | None = None,
    ) -> EstadoBulto:
        if coincide_remito is None:
            raise ReglaNegocioError("Debe verificarse el contenido contra el remito.")
        if not coincide_remito:
            return EstadoBulto.RECHAZADO
        if intacto is None:
            raise ReglaNegocioError("Debe verificarse el estado del precinto.")
        return EstadoBulto.VERIFICADO if intacto else EstadoBulto.RECHAZADO


class _EstadoFinal:
    def __init__(self, estado: EstadoBulto) -> None:
        self.estado = estado

    def siguiente(
        self,
        intacto: bool | None = None,
        coincide_remito: bool | None = None,
    ) -> EstadoBulto:
        raise ReglaNegocioError(f"El bulto ya está en estado final: {self.estado}.")


class MaquinaEstadosBulto:
    _states: dict[EstadoBulto, type] = {
        EstadoBulto.PREPARADO: _EstadoPreparado,
        EstadoBulto.PRECINTADO: _EstadoPrecintado,
        EstadoBulto.EN_TRANSITO: _EstadoEnTransito,
        EstadoBulto.RECIBIDO: _EstadoRecibido,
    }

    @classmethod
    def siguiente(
        cls,
        current: EstadoBulto,
        intacto: bool | None = None,
        coincide_remito: bool | None = None,
    ) -> EstadoBulto:
        if current in (EstadoBulto.VERIFICADO, EstadoBulto.RECHAZADO):
            state = _EstadoFinal(current)
        else:
            state_type = cls._states.get(current)
            if state_type is None:
                raise ReglaNegocioError(f"Estado de bulto desconocido: {current}")
            state = state_type()
        return state.siguiente(intacto, coincide_remito)


class ObservadorLogistico(Protocol):
    def actualizar(self, event: EventoBulto) -> None: ...


class PublicadorEventos(Protocol):
    def publicar(self, event: EventoBulto) -> None: ...


class BusEventos:
    def __init__(self) -> None:
        self._observers: list[ObservadorLogistico] = []

    def suscribir(self, observer: ObservadorLogistico) -> None:
        self._observers.append(observer)

    def publicar(self, event: EventoBulto) -> None:
        for observer in self._observers:
            observer.actualizar(event)


class ReaccionLogistica:
    """Dispatches durable package events to inventory and audit observers."""

    def __init__(self, observers: tuple[ObservadorLogistico, ...]) -> None:
        self._bus = BusEventos()
        for observer in observers:
            self._bus.suscribir(observer)

    def publicar(self, event: EventoBulto) -> None:
        self._bus.publicar(event)
