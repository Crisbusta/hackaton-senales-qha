"""Motor financiero determinista (T10).

El LLM nunca calcula: redacta los resultados de este módulo. Aritmética con
`Decimal`; los resultados salen en pesos enteros (CLP).

Supuestos (los resultados son ilustrativos, no una oferta del banco):
- Interés mensual compuesto sobre el saldo, a la tasa del estado de cuenta.
- Se paga el mismo monto todos los meses, sin compras nuevas ni comisiones.
- "Pagar solo el mínimo" supone que el mínimo se mantiene fijo en el monto
  actual. En la realidad el mínimo baja junto con el saldo, así que el plazo
  real de pagar solo el mínimo suele ser todavía más largo.
- Una compra en cuotas se reparte en cuotas iguales, sin interés.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.modules.finance.schemas import (
    Affordability,
    InstallmentEffect,
    PaymentComparison,
    PayoffPlan,
)

# Tope de la simulación: 30 años. Más allá, se informa como "no se paga".
MAX_MONTHS = 360


def _pesos(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def payoff_plan(balance: int, monthly_rate_pct: Decimal, monthly_payment: int) -> PayoffPlan:
    if balance < 0:
        raise ValueError("balance must be >= 0")
    if monthly_payment <= 0:
        raise ValueError("monthly_payment must be > 0")

    rate = Decimal(monthly_rate_pct) / 100
    remaining = Decimal(balance)
    months = 0
    total_interest = Decimal(0)
    total_paid = Decimal(0)

    while remaining > 0:
        interest = Decimal(_pesos(remaining * rate))
        if monthly_payment <= interest or months >= MAX_MONTHS:
            return PayoffPlan(
                monthly_payment=monthly_payment, months=None, total_interest=None, total_paid=None
            )
        remaining += interest
        paid = min(Decimal(monthly_payment), remaining)
        remaining -= paid
        total_interest += interest
        total_paid += paid
        months += 1

    return PayoffPlan(
        monthly_payment=monthly_payment,
        months=months,
        total_interest=_pesos(total_interest),
        total_paid=_pesos(total_paid),
    )


def compare_with_minimum(
    balance: int, monthly_rate_pct: Decimal, chosen_payment: int, minimum_payment: int
) -> PaymentComparison:
    chosen = payoff_plan(balance, monthly_rate_pct, chosen_payment)
    minimum_only = payoff_plan(balance, monthly_rate_pct, minimum_payment)
    both = chosen.months is not None and minimum_only.months is not None
    return PaymentComparison(
        chosen=chosen,
        minimum_only=minimum_only,
        months_saved=minimum_only.months - chosen.months if both else None,
        interest_saved=minimum_only.total_interest - chosen.total_interest if both else None,
    )


def affordability(
    reference_month: str,
    income: int,
    essential_spending: int,
    other_debt_payments: int,
    payment: int,
) -> Affordability:
    available = income - essential_spending - other_debt_payments
    return Affordability(
        reference_month=reference_month,
        income=income,
        essential_spending=essential_spending,
        other_debt_payments=other_debt_payments,
        available_after_essentials=available,
        shortfall=max(payment - available, 0),
    )


def installment_effect(
    current_minimum: int, purchase_amount: int, installments: int
) -> InstallmentEffect:
    if purchase_amount <= 0 or installments <= 0:
        raise ValueError("purchase_amount and installments must be > 0")
    monthly = -(-purchase_amount // installments)  # hacia arriba: nunca subestimar
    return InstallmentEffect(monthly_installment=monthly, new_minimum=current_minimum + monthly)
