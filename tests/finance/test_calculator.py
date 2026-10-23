from decimal import Decimal

import pytest

from app.modules.finance.calculator import (
    MAX_MONTHS,
    affordability,
    compare_with_minimum,
    installment_effect,
    payoff_plan,
)

# Estado de cuenta ficticio de Patricia (docs/casos_p0.md).
BALANCE = 1_064_000
RATE = Decimal("2.8")
MINIMUM = 74_350


def test_paying_more_than_the_minimum_saves_months_and_interest() -> None:
    result = compare_with_minimum(BALANCE, RATE, chosen_payment=100_000, minimum_payment=MINIMUM)

    assert result.chosen.months == 13
    assert result.minimum_only.months == 19
    assert result.months_saved == 6
    assert result.interest_saved == 97_662


def test_totals_are_consistent() -> None:
    plan = payoff_plan(BALANCE, RATE, 100_000)

    assert plan.total_paid == BALANCE + plan.total_interest


def test_a_payment_that_does_not_cover_interest_never_pays_off() -> None:
    interest_first_month = 29_792  # 2,8 % de 1.064.000

    plan = payoff_plan(BALANCE, RATE, interest_first_month)

    assert plan.months is None
    assert plan.total_interest is None


def test_very_long_payoff_is_capped() -> None:
    plan = payoff_plan(10_000_000, Decimal("0.5"), 50_050)

    assert plan.months is None or plan.months <= MAX_MONTHS


def test_zero_balance_takes_no_months() -> None:
    assert payoff_plan(0, RATE, 10_000).months == 0


def test_results_are_integer_pesos() -> None:
    plan = payoff_plan(BALANCE, RATE, 100_000)

    assert isinstance(plan.total_interest, int)
    assert isinstance(plan.total_paid, int)


@pytest.mark.parametrize(("balance", "payment"), [(-1, 1000), (1000, 0)])
def test_invalid_inputs_are_rejected(balance: int, payment: int) -> None:
    with pytest.raises(ValueError):
        payoff_plan(balance, RATE, payment)


def test_installment_effect_matches_case_c05() -> None:
    # C05: "$60.000 en 6 cuotas" → "$10.000 al mes; tu mínimo pasaría a unos $84.350".
    effect = installment_effect(MINIMUM, 60_000, 6)

    assert effect.monthly_installment == 10_000
    assert effect.new_minimum == 84_350


def test_installment_rounds_up() -> None:
    assert installment_effect(MINIMUM, 100_000, 3).monthly_installment == 33_334


def test_affordability_flags_a_shortfall_on_essentials() -> None:
    result = affordability(
        "2026-10", income=301_000, essential_spending=242_100, other_debt_payments=58_900,
        payment=100_000,
    )  # fmt: skip

    assert result.available_after_essentials == 0
    assert result.shortfall == 100_000


def test_affordability_without_shortfall() -> None:
    result = affordability(
        "2026-10", income=500_000, essential_spending=200_000, other_debt_payments=50_000,
        payment=100_000,
    )  # fmt: skip

    assert result.shortfall == 0
