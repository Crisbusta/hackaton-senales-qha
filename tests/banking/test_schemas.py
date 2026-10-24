from datetime import date
from decimal import Decimal

from app.modules.banking.schemas import CreditCard, StatementHistoryEntry

BREAKDOWN = {"interest": 23100, "installments": 43000, "principal": 8250}
STATEMENT = {
    "period_end": "2026-10-15",
    "due_date": "2026-11-03",
    "billed_amount": 868000,
    "minimum_payment": 74350,
    "minimum_breakdown": BREAKDOWN,
    "monthly_rate_pct": "2.8",
}
CARD = {
    "card_id": "cc",
    "masked_number": "**** 6612",
    "credit_limit": 1500000,
    "total_debt": 1064000,
    "statement": STATEMENT,
}


def test_statement_history_is_optional() -> None:
    card = CreditCard.model_validate(CARD)

    assert card.statement_history == []
    assert card.statement.monthly_rate_pct == Decimal("2.8")


def test_statement_history_entry_parses() -> None:
    entry = StatementHistoryEntry.model_validate(
        {
            "period_start": "2026-09-16",
            "period_end": "2026-10-15",
            "due_date": "2026-11-03",
            "previous_balance": 873502,
            "purchases": 43000,
            "interest": 23100,
            "fees": 2990,
            "tax": 408,
            "payments": 75000,
            "billed_amount": 868000,
            "minimum_payment": 74350,
            "minimum_breakdown": BREAKDOWN,
            "credit_used": 1064000,
        }
    )
    card = CreditCard.model_validate(CARD | {"statement_history": [entry.model_dump()]})

    assert card.statement_history == [entry]
    assert entry.due_date == date(2026, 11, 3)
