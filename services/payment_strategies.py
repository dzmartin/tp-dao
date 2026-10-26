from abc import ABC, abstractmethod

from exceptions import BusinessRuleError


class PaymentStrategy(ABC):
    """Strategy pattern for validating supported payment methods."""

    method: str

    @abstractmethod
    def validate(self, amount_cents: int) -> None:
        raise NotImplementedError


class PositiveAmountPayment(PaymentStrategy):
    def validate(self, amount_cents: int) -> None:
        if amount_cents <= 0:
            raise BusinessRuleError("Cada cobro debe ser mayor que cero.")


class CashPayment(PositiveAmountPayment):
    method = "efectivo"


class CardPayment(PositiveAmountPayment):
    method = "tarjeta"


class TransferPayment(PositiveAmountPayment):
    method = "transferencia"

class CurrentAccountPayment(PositiveAmountPayment):
    method = "cuenta corriente"


PAYMENT_STRATEGIES: dict[str, PaymentStrategy] = {
    strategy.method: strategy()
    for strategy in (CashPayment, CardPayment, TransferPayment, CurrentAccountPayment)
}