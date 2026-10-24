import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.agent.interface import FieldStatus
from app.modules.banking.provider import MockBankProvider
from app.modules.banking.schemas import BankSnapshot, TransactionCategory
from app.modules.banking.service import (
    credit_card_fields,
    debt_overview,
    format_date_es,
    monthly_cashflow,
    monthly_installments_total,
)


@pytest.fixture
def patricia() -> BankSnapshot:
    data = asyncio.run(MockBankProvider("patricia").get_snapshot("u1"))
    assert data is not None
    return data


# --- Coherencia del escenario con el estado de cuenta ficticio (docs/casos_p0.md)


def test_card_matches_the_demo_statement(patricia: BankSnapshot) -> None:
    fields = credit_card_fields(patricia.credit_cards[0])

    assert {k: f.value for k, f in fields.items()} == {
        "vencimiento": "3 de noviembre de 2026",
        "minimo": 74350,
        "saldo": 1064000,
        "facturado": 868000,
        "minimo_intereses": 23100,
        "minimo_cuotas": 43000,
        "minimo_abono": 8250,
        "cupo": 1500000,
        "tasa": "2,8 % mensual",
    }
    assert all(f.status is FieldStatus.CONFIRMED for f in fields.values())


def test_minimum_breakdown_adds_up(patricia: BankSnapshot) -> None:
    statement = patricia.credit_cards[0].statement
    breakdown = statement.minimum_breakdown

    assert breakdown.interest + breakdown.installments + breakdown.principal == (
        statement.minimum_payment
    )


def test_installment_purchases_explain_the_minimum(patricia: BankSnapshot) -> None:
    card = patricia.credit_cards[0]

    assert monthly_installments_total(card) == card.statement.minimum_breakdown.installments


# --- Coherencia con los 12 estados de cuenta en PDF (2025-11 a 2026-10) ------


def test_card_equals_the_october_2026_statement(patricia: BankSnapshot) -> None:
    # Cifras impresas en 2026-10_estado_de_cuenta_ficticio.pdf.
    card = patricia.credit_cards[0]
    statement = card.statement

    assert card.masked_number == "**** 6612"
    assert (card.credit_limit, card.total_debt, card.available_credit) == (
        1500000,
        1064000,
        436000,
    )
    assert statement.period_end == date(2026, 10, 15)
    assert statement.due_date == date(2026, 11, 3)
    assert (statement.billed_amount, statement.minimum_payment) == (868000, 74350)
    assert statement.minimum_breakdown.model_dump() == {
        "interest": 23100,
        "installments": 43000,
        "principal": 8250,
    }
    assert statement.monthly_rate_pct == Decimal("2.8")


def test_installments_match_the_statement_items(patricia: BankSnapshot) -> None:
    # "05/12" en el estado 2026-10 = 5 cuotas ya facturadas, 7 por facturar.
    purchases = [
        (p.description, p.purchased_at, p.total_amount, p.installment_amount)
        + (f"{p.paid_installments:02d}/{p.total_installments:02d}",)
        for p in patricia.credit_cards[0].installment_purchases
    ]

    assert purchases == [
        ("Tienda de hogar (refrigerador)", date(2026, 5, 20), 168000, 14000, "05/12"),
        ("Farmacia Cruz Verde (remedios)", date(2026, 7, 28), 108000, 18000, "03/06"),
        ("Tienda de ropa", date(2026, 8, 30), 66000, 11000, "02/06"),
    ]


def test_unbilled_installments_explain_used_credit(patricia: BankSnapshot) -> None:
    # Cupo utilizado = total facturado + cuotas que aún no se facturan.
    card = patricia.credit_cards[0]
    unbilled = sum(
        p.installment_amount * p.remaining_installments for p in card.installment_purchases
    )

    assert card.total_debt - card.statement.billed_amount == unbilled == 196000


def test_history_covers_the_twelve_statements(patricia: BankSnapshot) -> None:
    history = patricia.credit_cards[0].statement_history

    assert [h.period_end.strftime("%Y-%m") for h in history] == [
        "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10",
    ]  # fmt: skip
    assert [h.billed_amount for h in history] == [
        641972, 666392, 665459, 665611, 729794, 784899,
        819052, 832886, 842593, 849694, 873502, 868000,
    ]  # fmt: skip
    assert [h.minimum_payment for h in history] == [
        35950, 25300, 25300, 25300, 27750, 29850,
        31100, 45100, 45500, 63050, 74550, 74350,
    ]  # fmt: skip
    # Los PDF informan el acumulado de intereses, comisiones e impuestos del año.
    assert sum(h.interest + h.fees + h.tax for h in history) == 295376


def test_each_statement_adds_up_and_chains_to_the_next(patricia: BankSnapshot) -> None:
    history = patricia.credit_cards[0].statement_history

    for h in history:
        assert (
            h.previous_balance + h.purchases + h.interest + h.fees + h.tax - h.payments
            == h.billed_amount
        )
        breakdown = h.minimum_breakdown
        assert breakdown.interest + breakdown.installments + breakdown.principal == (
            h.minimum_payment
        )
        assert breakdown.interest == h.interest
    for previous, current in zip(history, history[1:], strict=False):
        assert current.previous_balance == previous.billed_amount
        assert current.period_start == previous.period_end + timedelta(days=1)


def test_latest_history_entry_is_the_current_statement(patricia: BankSnapshot) -> None:
    card = patricia.credit_cards[0]
    latest, statement = card.statement_history[-1], card.statement

    assert (latest.period_end, latest.due_date) == (statement.period_end, statement.due_date)
    assert (latest.billed_amount, latest.minimum_payment) == (
        statement.billed_amount,
        statement.minimum_payment,
    )
    assert latest.minimum_breakdown == statement.minimum_breakdown
    assert latest.credit_used == card.total_debt


def test_account_card_payments_are_the_statement_payments(patricia: BankSnapshot) -> None:
    history = patricia.credit_cards[0].statement_history
    payments = [
        tx for tx in patricia.transactions if tx.category is TransactionCategory.CARD_PAYMENT
    ]
    first_day = min(tx.booked_at for tx in patricia.transactions)
    covered = [h for h in history if h.period_end >= first_day]

    assert [h.period_end.strftime("%Y-%m") for h in covered] == [
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    for h in covered:
        in_period = [tx for tx in payments if h.period_start <= tx.booked_at <= h.period_end]
        assert -sum(tx.amount for tx in in_period) == h.payments
    assert len(payments) == len(covered)


def test_she_pays_about_the_minimum(patricia: BankSnapshot) -> None:
    # Cada abono cubre el mínimo del estado anterior, lejos del total facturado.
    history = patricia.credit_cards[0].statement_history

    for previous, current in zip(history, history[1:], strict=False):
        assert previous.minimum_payment <= current.payments < previous.billed_amount / 2


def test_amounts_are_integers_at_the_source(patricia: BankSnapshot) -> None:
    # Que FinancialField conserve el int depende del contrato (PR #4: value: int).
    card = patricia.credit_cards[0]

    assert isinstance(card.total_debt, int)
    assert isinstance(card.statement.minimum_payment, int)
    assert all(isinstance(tx.amount, int) for tx in patricia.transactions)


# --- Cálculos deterministas ----------------------------------------------


def test_available_credit(patricia: BankSnapshot) -> None:
    assert patricia.credit_cards[0].available_credit == 436000


def test_remaining_installments(patricia: BankSnapshot) -> None:
    loan = patricia.consumer_loans[0]

    assert loan.remaining_installments == 11
    assert [p.remaining_installments for p in patricia.credit_cards[0].installment_purchases] == [
        7,
        3,
        4,
    ]


def test_monthly_cashflow_shows_variable_income(patricia: BankSnapshot) -> None:
    months = {m.month: m for m in monthly_cashflow(patricia.transactions)}

    assert list(months) == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert [(m.income, m.income_deposits) for m in months.values()] == [
        (450000, 2),
        (324000, 2),
        (277000, 2),
        (301000, 2),
    ]
    assert [m.essential_spending for m in months.values()] == [253200, 234500, 220500, 242100]
    # Pago de tarjeta del periodo + cuota del crédito de consumo (58.900).
    assert [m.debt_payments for m in months.values()] == [130900, 132900, 136900, 133900]
    assert all(m.other_spending == 12000 for m in months.values())  # transporte


def test_income_does_not_cover_everything(patricia: BankSnapshot) -> None:
    months = monthly_cashflow(patricia.transactions)
    net = [m.income - m.essential_spending - m.debt_payments - m.other_spending for m in months]

    assert net == [53900, -55400, -92400, -87000]
    assert net[-1] < 0 and net[-2] < 0


def test_debt_overview(patricia: BankSnapshot) -> None:
    overview = debt_overview(patricia)

    assert overview.credit_card_debt == 1064000
    assert overview.consumer_loan_debt == 640000
    assert overview.total_debt == 1704000
    assert overview.minimum_monthly_commitment == 74350 + 58900


@pytest.mark.parametrize(
    ("value", "expected"),
    [(date(2026, 11, 3), "3 de noviembre de 2026"), (date(2027, 1, 31), "31 de enero de 2027")],
)
def test_format_date_es(value: date, expected: str) -> None:
    assert format_date_es(value) == expected
