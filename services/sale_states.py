from abc import ABC, abstractmethod

from exceptions import BusinessRuleError


class SaleState(ABC):
    name: str

    def ensure_return_allowed(self) -> None:
        pass

    @abstractmethod
    def after_return(self, sold_quantity: int, returned_quantity: int) -> "SaleState":
        raise NotImplementedError


class ConfirmedSaleState(SaleState):
    name = "Confirmada"

    def after_return(self, sold_quantity: int, returned_quantity: int) -> SaleState:
        return FullyReturnedSaleState() if returned_quantity == sold_quantity else PartiallyReturnedSaleState()


class PartiallyReturnedSaleState(SaleState):
    name = "Devolución parcial"

    def after_return(self, sold_quantity: int, returned_quantity: int) -> SaleState:
        return FullyReturnedSaleState() if returned_quantity == sold_quantity else self


class FullyReturnedSaleState(SaleState):
    name = "Devuelta"

    def ensure_return_allowed(self) -> None:
        raise BusinessRuleError("La venta ya fue devuelta por completo.")

    def after_return(self, sold_quantity: int, returned_quantity: int) -> SaleState:
        self.ensure_return_allowed()
        return self


_SALE_STATES: dict[str, type[SaleState]] = {
    state.name: state
    for state in (ConfirmedSaleState, PartiallyReturnedSaleState, FullyReturnedSaleState)
}


def sale_state_from_name(name: str) -> SaleState:
    state_type = _SALE_STATES.get(name)
    if state_type is None:
        raise ValueError(f"Estado de venta desconocido: {name}.")
    return state_type()
