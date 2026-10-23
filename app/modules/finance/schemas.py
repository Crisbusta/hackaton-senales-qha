from pydantic import BaseModel


class PayoffPlan(BaseModel):
    """Resultado de pagar un monto fijo cada mes."""

    monthly_payment: int
    # None: el pago no alcanza a cubrir los intereses del mes, la deuda no baja.
    months: int | None
    total_interest: int | None
    total_paid: int | None


class PaymentComparison(BaseModel):
    chosen: PayoffPlan
    minimum_only: PayoffPlan
    months_saved: int | None
    interest_saved: int | None


class Affordability(BaseModel):
    """¿El pago elegido deja cubiertos los gastos esenciales? (principio 7)."""

    reference_month: str
    income: int
    essential_spending: int
    other_debt_payments: int
    available_after_essentials: int
    shortfall: int  # cuánto le faltaría para cubrir esenciales; 0 si alcanza


class InstallmentEffect(BaseModel):
    monthly_installment: int
    new_minimum: int
