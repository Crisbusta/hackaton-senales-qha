import asyncio
from datetime import date

import pytest

from app.modules.agent.interface import FieldStatus
from app.modules.banking.provider import MockBankProvider
from app.modules.banking.schemas import BankSnapshot
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
        7,
        4,
    ]


def test_monthly_cashflow_shows_variable_income(patricia: BankSnapshot) -> None:
    months = {m.month: m for m in monthly_cashflow(patricia.transactions)}

    assert list(months) == ["2026-09", "2026-10"]
    september, october = months["2026-09"], months["2026-10"]
    assert (september.income, september.income_deposits) == (277000, 2)
    assert (october.income, october.income_deposits) == (301000, 2)
    assert september.essential_spending == 220500
    assert october.essential_spending == 242100
    assert (september.debt_payments, october.debt_payments) == (130700, 131800)
    assert october.other_spending == 12000


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
