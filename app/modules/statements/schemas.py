"""Modelos del estado de cuenta leído desde PDF (T07).

Privacidad: el modelo no tiene dónde guardar el nombre ni el RUT del titular,
a propósito. El número de tarjeta sale siempre enmascarado (`**** 6612`).

Montos en `int` (pesos chilenos, sin decimales) y tasas en `Decimal`. Nunca
`float`: un monto no debe poder redactarse como "74349,99999999999".
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.modules.agent.interface import FinancialField


class StatementError(Exception):
    """El archivo no es un PDF legible o excede los límites de tamaño.

    Es el único error que levanta `parse_statement`. Un PDF legible con
    contenido inesperado no es un error: devuelve campos ABSENT y warnings.
    """


class Purchase(BaseModel):
    """Una fila de la sección 2 (operaciones con la tarjeta titular)."""

    description: str
    purchase_date: date
    total_amount: int  # "Monto Total Operación o Cobro"
    installment_number: int  # el "05" de "05/12"
    total_installments: int  # el "12" de "05/12"
    installment_amount: int  # "Valor Cuota Mensual" de este periodo


class StatementSummary(BaseModel):
    """Líneas de "RESUMEN DEL TOTAL A PAGAR", tal como vienen impresas.

    `payments` es negativo (el documento lo imprime como "-$75.000"), así que
    la suma de todas las líneas menos `total` debe dar `total`.
    """

    previous_balance: int | None = None
    holder_operations: int | None = None
    additional_card_operations: int | None = None
    additional_products: int | None = None
    interest_and_collection: int | None = None
    commissions: int | None = None
    credit_tax: int | None = None
    payments: int | None = None
    total: int | None = None

    def addends(self) -> list[int | None]:
        return [
            self.previous_balance,
            self.holder_operations,
            self.additional_card_operations,
            self.additional_products,
            self.interest_and_collection,
            self.commissions,
            self.credit_tax,
            self.payments,
        ]


class AnnualRates(BaseModel):
    """Tasas anuales en porcentaje (`Decimal("33.60")` = 33,60 %)."""

    revolving: Decimal | None = None  # interés adicional (rotativo)
    late_payment: Decimal | None = None  # interés por mora
    installment_purchase: Decimal | None = None
    cash_advance: Decimal | None = None
    cae_revolving: Decimal | None = None
    cae_cash_advance: Decimal | None = None


class ScheduledInstallment(BaseModel):
    """Una columna de "vencimiento próximos 6 meses"."""

    month: str  # "2026-11"
    amount: int


class ParsedStatement(BaseModel):
    # Mismas claves y formatos que `banking.service.credit_card_fields`.
    fields: dict[str, FinancialField]

    bank_name: str | None = None
    masked_card: str | None = None  # siempre "**** NNNN"
    statement_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    due_date: date | None = None

    purchases: list[Purchase] = Field(default_factory=list)  # toda la sección 2
    installments: list[Purchase] = Field(default_factory=list)  # solo en cuotas (NN/MM, MM > 1)
    summary: StatementSummary = Field(default_factory=StatementSummary)
    annual_rates: AnnualRates = Field(default_factory=AnnualRates)
    monthly_revolving_rate: Decimal | None = None  # 2.80 de "Tasa int.2,80%"

    credit_available: int | None = None  # cupo DISPONIBLE
    payoff_amount: int | None = None  # "Para terminar con el total de su deuda..."
    upcoming_installments: list[ScheduledInstallment] = Field(default_factory=list)

    # Códigos legibles (sin texto del documento) de lo que no se pudo leer.
    warnings: list[str] = Field(default_factory=list)
