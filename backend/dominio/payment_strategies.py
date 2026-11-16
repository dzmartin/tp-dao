from abc import ABC, abstractmethod

from backend.dominio.exceptions import BusinessRuleError

#PATRON STRATEGY APLICADO PARA EL CALCULO DE COBROS SEGUN EL MEDIO DE PAGO. CADA ESTRATEGIA 
#DEFINE UN NUMERADOR Y DENOMINADOR PARA CALCULAR EL PRECIO AJUSTADO.
class PaymentStrategy(ABC):
    """Strategy for validating payment methods and calculating adjusted prices."""

    method: str
    price_numerator: int
    price_denominator: int

    @abstractmethod
    def validate(self, amount_cents: int) -> None:
        raise NotImplementedError

    def charge_for(self, base_cents: int) -> int:
        return self._rounded_ratio(base_cents, self.price_numerator, self.price_denominator)

    def base_for(self, charge_cents: int) -> int:
        return self._rounded_ratio(charge_cents, self.price_denominator, self.price_numerator)

    @staticmethod
    def _rounded_ratio(amount: int, numerator: int, denominator: int) -> int:
        return (2 * amount * numerator + denominator) // (2 * denominator)


class PositiveAmountPayment(PaymentStrategy):
    def validate(self, amount_cents: int) -> None:
        if amount_cents <= 0:
            raise BusinessRuleError("Cada cobro debe ser mayor que cero.")

#Aplicar promociones, descuentos y listas de precios
class CashPayment(PositiveAmountPayment):
    method = "efectivo"
    price_numerator = 4
    price_denominator = 5


class CardPayment(PositiveAmountPayment):
    method = "tarjeta"
    price_numerator = 11
    price_denominator = 10


class TransferPayment(PositiveAmountPayment):
    method = "transferencia"
    price_numerator = 1
    price_denominator = 1


PAYMENT_STRATEGIES: dict[str, PaymentStrategy] = {
    strategy.method: strategy()
    for strategy in (CashPayment, CardPayment, TransferPayment)
}