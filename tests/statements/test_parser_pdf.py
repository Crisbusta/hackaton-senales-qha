"""Parser sobre los estados de cuenta ficticios reales (Banco Austral)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.agent.interface import FieldStatus
from app.modules.banking.service import format_date_es
from app.modules.statements import parse_statement, validate_statement

FIXTURES = Path(__file__).parent / "fixtures"
MONTHS = ("2025-11", "2026-08", "2026-10")


def _load(month: str) -> bytes:
    return (FIXTURES / f"{month}_estado_de_cuenta_ficticio.pdf").read_bytes()


@pytest.fixture(scope="module")
def october():
    return parse_statement(_load("2026-10"))


def test_october_fields_use_bank_keys_and_formats(october):
    values = {key: field.value for key, field in october.fields.items()}
    assert values == {
        "vencimiento": format_date_es(date(2026, 11, 3)),
        "facturado": 868000,
        "minimo": 74350,
        "saldo": 1064000,
        "cupo": 1500000,
        "tasa": "2,8 % mensual",
        "minimo_intereses": 23100,
        "minimo_cuotas": 43000,
        "minimo_abono": 8250,
    }
    assert values["vencimiento"] == "3 de noviembre de 2026"
    assert all(f.status is FieldStatus.CONFIRMED for f in october.fields.values())


def test_october_metadata(october):
    assert october.bank_name == "BANCO AUSTRAL (ficticio)"
    assert october.masked_card == "**** 6612"
    assert october.statement_date == date(2026, 10, 15)
    assert (october.period_start, october.period_end) == (date(2026, 9, 16), date(2026, 10, 15))
    assert october.due_date == date(2026, 11, 3)
    assert october.credit_available == 436000
    assert october.payoff_amount == 1067300
    assert october.monthly_revolving_rate == Decimal("2.80")
    assert october.annual_rates.revolving == Decimal("33.60")
    assert october.annual_rates.late_payment == Decimal("38.40")
    assert [(s.month, s.amount) for s in october.upcoming_installments] == [
        ("2026-11", 43000),
        ("2026-12", 43000),
        ("2027-01", 43000),
        ("2027-02", 25000),
        ("2027-03", 14000),
        ("2027-04", 14000),
    ]
    assert october.warnings == []


def test_october_summary(october):
    assert october.summary.model_dump() == {
        "previous_balance": 873502,
        "holder_operations": 43000,
        "additional_card_operations": 0,
        "additional_products": 0,
        "interest_and_collection": 23100,
        "commissions": 2990,
        "credit_tax": 408,
        "payments": -75000,
        "total": 868000,
    }


def test_october_installments(october):
    rows = [
        (
            i.description,
            i.purchase_date,
            i.total_amount,
            i.installment_number,
            i.total_installments,
            i.installment_amount,
        )
        for i in october.installments
    ]
    assert rows == [
        ("Tienda de hogar (refrigerador)", date(2026, 5, 20), 168000, 5, 12, 14000),
        ("Farmacia Cruz Verde (remedios)", date(2026, 7, 28), 108000, 3, 6, 18000),
        ("Tienda de ropa", date(2026, 8, 30), 66000, 2, 6, 11000),
    ]


def test_single_payment_purchases_are_not_installments():
    november = parse_statement(_load("2025-11"))
    assert len(november.purchases) == 3
    assert [i.description for i in november.installments] == ["Optica (lentespara mama)"]
    # "cuotas del mes" del desglose no incluye las compras 01/01.
    assert november.fields["minimo_cuotas"].value == 12000
    assert november.summary.holder_operations == 95400


@pytest.mark.parametrize("month", MONTHS)
def test_fixtures_parse_and_validate(month):
    statement = parse_statement(_load(month))
    assert statement.warnings == []
    assert all(f.status is FieldStatus.CONFIRMED for f in statement.fields.values())
    assert validate_statement(statement) == []


@pytest.mark.parametrize("month", MONTHS)
def test_holder_identity_never_leaves_the_parser(month):
    dumped = parse_statement(_load(month)).model_dump_json()
    for secret in ("PATRICIA", "Patricia", "11.111.111", "111111111", "XXXX XXXX"):
        assert secret not in dumped
