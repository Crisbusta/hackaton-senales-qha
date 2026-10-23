"""Lectura determinista de los datos bancarios.

Dos responsabilidades:
- Traducir la tarjeta de crédito a `financial_data`, con las mismas claves
  que el estado de cuenta en PDF (ver FIELDS_BY_MODE en el contrato).
- Resumir flujo de caja y deudas. Son sumas, no estimaciones: el LLM nunca
  calcula estas cifras.
"""

from collections import defaultdict
from datetime import date

from pydantic import BaseModel

from app.modules.agent.interface import FieldStatus, FinancialField
from app.modules.banking.schemas import (
    ESSENTIAL_CATEGORIES,
    BankSnapshot,
    CreditCard,
    Transaction,
    TransactionCategory,
)

_MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip


def format_date_es(value: date) -> str:
    """date(2026, 11, 3) -> '3 de noviembre de 2026'."""
    return f"{value.day} de {_MONTHS_ES[value.month - 1]} de {value.year}"


def credit_card_fields(card: CreditCard) -> dict[str, FinancialField]:
    """Campos del estado de cuenta, con las claves que usa el agente."""
    statement = card.statement
    rate = f"{statement.monthly_rate_pct.normalize():f}".replace(".", ",")

    def confirmed(value: int | str) -> FinancialField:
        return FinancialField(value=value, status=FieldStatus.CONFIRMED)

    return {
        "vencimiento": confirmed(format_date_es(statement.due_date)),
        "minimo": confirmed(statement.minimum_payment),
        "saldo": confirmed(card.total_debt),
        "facturado": confirmed(statement.billed_amount),
        "minimo_intereses": confirmed(statement.minimum_breakdown.interest),
        "minimo_cuotas": confirmed(statement.minimum_breakdown.installments),
        "minimo_abono": confirmed(statement.minimum_breakdown.principal),
        "cupo": confirmed(card.credit_limit),
        "tasa": confirmed(f"{rate} % mensual"),
    }


def monthly_installments_total(card: CreditCard) -> int:
    """Suma mensual de las compras en cuotas que aún tienen cuotas por pagar."""
    return sum(p.installment_amount for p in card.installment_purchases if p.remaining_installments)


class MonthlyCashflow(BaseModel):
    month: str  # "2026-10"
    income: int = 0
    essential_spending: int = 0
    debt_payments: int = 0
    other_spending: int = 0
    income_deposits: int = 0


def monthly_cashflow(transactions: list[Transaction]) -> list[MonthlyCashflow]:
    """Ingresos y gastos por mes calendario, ordenados cronológicamente."""
    debt = {TransactionCategory.CARD_PAYMENT, TransactionCategory.LOAN_PAYMENT}
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for tx in transactions:
        bucket = totals[tx.booked_at.strftime("%Y-%m")]
        if tx.category is TransactionCategory.INCOME:
            bucket["income"] += tx.amount
            bucket["income_deposits"] += 1
        elif tx.category in ESSENTIAL_CATEGORIES:
            bucket["essential_spending"] += -tx.amount
        elif tx.category in debt:
            bucket["debt_payments"] += -tx.amount
        else:
            bucket["other_spending"] += -tx.amount

    return [MonthlyCashflow(month=month, **values) for month, values in sorted(totals.items())]


class DebtOverview(BaseModel):
    credit_card_debt: int
    consumer_loan_debt: int
    total_debt: int
    # Lo mínimo que comprometen sus deudas cada mes: mínimos de tarjeta + cuotas de créditos.
    minimum_monthly_commitment: int


def debt_overview(snapshot: BankSnapshot) -> DebtOverview:
    card_debt = sum(card.total_debt for card in snapshot.credit_cards)
    loan_debt = sum(loan.outstanding_balance for loan in snapshot.consumer_loans)
    commitment = sum(card.statement.minimum_payment for card in snapshot.credit_cards) + sum(
        loan.installment_amount for loan in snapshot.consumer_loans
    )
    return DebtOverview(
        credit_card_debt=card_debt,
        consumer_loan_debt=loan_debt,
        total_debt=card_debt + loan_debt,
        minimum_monthly_commitment=commitment,
    )
