"""Modelo de datos bancarios del usuario.

Inspirado en las categorías habituales de Open Finance (cuentas, saldos,
movimientos, tarjetas, créditos), sin pretender cumplir un estándar
específico. Un proveedor real se adapta a estos modelos.

Convenciones:
- Montos en `int`, pesos chilenos (CLP no tiene decimales).
- Movimientos con signo: positivo = abono, negativo = cargo.
- Tasas en `Decimal` como porcentaje (2.8 = 2,8 %), nunca `float`.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class TransactionCategory(StrEnum):
    INCOME = "ingreso"
    HEALTH = "salud"  # farmacia, consultas: gasto esencial
    GROCERIES = "alimentacion"  # supermercado, feria: gasto esencial
    UTILITIES = "servicios_basicos"  # luz, agua, gas, internet: gasto esencial
    TRANSPORT = "transporte"
    CARD_PAYMENT = "pago_tarjeta"
    LOAN_PAYMENT = "pago_credito"
    TRANSFER = "transferencia"
    OTHER = "otro"


ESSENTIAL_CATEGORIES = frozenset(
    {TransactionCategory.HEALTH, TransactionCategory.GROCERIES, TransactionCategory.UTILITIES}
)


class Customer(BaseModel):
    customer_id: str
    first_name: str


class Account(BaseModel):
    account_id: str
    type: Literal["cuenta_vista", "cuenta_corriente"]
    masked_number: str
    available_balance: int
    current_balance: int


class Transaction(BaseModel):
    transaction_id: str
    account_id: str
    booked_at: date
    description: str
    amount: int
    category: TransactionCategory


class DebitCard(BaseModel):
    card_id: str
    masked_number: str
    linked_account_id: str
    status: Literal["activa", "bloqueada"]


class InstallmentPurchase(BaseModel):
    description: str
    purchased_at: date
    total_amount: int
    installment_amount: int
    total_installments: int = Field(gt=0)
    paid_installments: int = Field(ge=0)

    @computed_field
    @property
    def remaining_installments(self) -> int:
        return self.total_installments - self.paid_installments


class MinimumPaymentBreakdown(BaseModel):
    interest: int
    installments: int
    principal: int


class CreditCardStatement(BaseModel):
    period_end: date
    due_date: date
    billed_amount: int
    minimum_payment: int
    minimum_breakdown: MinimumPaymentBreakdown
    monthly_rate_pct: Decimal


class CreditCard(BaseModel):
    card_id: str
    masked_number: str
    credit_limit: int
    total_debt: int
    statement: CreditCardStatement
    installment_purchases: list[InstallmentPurchase] = Field(default_factory=list)

    @computed_field
    @property
    def available_credit(self) -> int:
        return max(self.credit_limit - self.total_debt, 0)


class ConsumerLoan(BaseModel):
    loan_id: str
    original_amount: int
    outstanding_balance: int
    installment_amount: int
    total_installments: int = Field(gt=0)
    paid_installments: int = Field(ge=0)
    next_due_date: date
    cae_pct: Decimal

    @computed_field
    @property
    def remaining_installments(self) -> int:
        return self.total_installments - self.paid_installments


class BankConsent(BaseModel):
    """Autorización que el usuario otorgó en el portal de su banco.

    Como en Open Finance, el consentimiento no se pide por chat: se otorga
    fuera de la conversación, con las credenciales del banco.
    """

    granted: bool
    granted_at: datetime | None = None
    channel: Literal["portal_banco"] = "portal_banco"


class BankSnapshot(BaseModel):
    """Todo lo que el banco expone del usuario en un momento dado."""

    bank_name: str
    retrieved_at: datetime
    consent: BankConsent
    customer: Customer
    accounts: list[Account] = Field(default_factory=list)
    transactions: list[Transaction] = Field(default_factory=list)
    debit_cards: list[DebitCard] = Field(default_factory=list)
    credit_cards: list[CreditCard] = Field(default_factory=list)
    consumer_loans: list[ConsumerLoan] = Field(default_factory=list)
