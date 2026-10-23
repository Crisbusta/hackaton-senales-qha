"""Detección determinista de pedidos de simulación (T10).

Se evalúa ANTES de llamar al agente, para que el cálculo ya viaje en el
mismo turno (el mismo problema de orden que resuelve la precarga). Solo
reconoce montos explícitos: si ella no escribe una cifra, no se simula nada.
"""

import re
import unicodedata

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    ConversationMode,
    FieldStatus,
    FinancialField,
)
from app.modules.banking.schemas import BankSnapshot
from app.modules.banking.service import monthly_cashflow
from app.modules.conversation import figures
from app.modules.conversation.figures import amounts_in
from app.modules.conversation.schemas import ConversationState
from app.modules.finance.calculator import (
    affordability,
    compare_with_minimum,
    installment_effect,
)
from app.modules.finance.schemas import PayoffPlan

# "100 mil", "60 lucas", "1,5 millones"
_SPOKEN_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mil|lucas?|millon(?:es)?)\b")
_INSTALLMENTS = re.compile(r"\b(\d{1,2})\s*cuotas?\b")

# Palabras completas: "me pagaron 100 mil" es un ingreso, no un pago a simular.
_PAYMENT_CUE = re.compile(r"\b(pag|abon)(o|ar|aria|ara|ando)\b")
_OPTION_TWO = {"2", "2.", "la 2", "opcion 2", "la dos", "dos", "la segunda", "segunda"}
_OPTION_TWO_PHRASES = ("cuanto suma", "cuanto me suma", "cuanto sumaria", "ver cuanto", "muestrame")


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())


def _multiplier(unit: str) -> int:
    return 1_000_000 if unit.startswith("millon") else 1_000


def parse_amount(text: str) -> int | None:
    """El monto más grande que ella escribió, en pesos. None si no hay ninguno."""
    normalized = _normalize(text)
    amounts = set(amounts_in(normalized))
    for number, unit in _SPOKEN_AMOUNT.findall(normalized):
        amounts.add(int(float(number.replace(",", ".")) * _multiplier(unit)))
    return max(amounts) if amounts else None


def payment_amount(text: str) -> int | None:
    """Monto de un pago que ella quiere simular: "¿y si pago 100 mil?"."""
    normalized = _normalize(text)
    if not _PAYMENT_CUE.search(normalized):
        return None
    return parse_amount(normalized)


def chose_installment_calculation(text: str) -> bool:
    """Eligió la opción 2 de PAUSA_COMPRA: ver cuánto le suma la cuota."""
    normalized = _normalize(text).rstrip("!?. ")
    return normalized in _OPTION_TWO or any(p in normalized for p in _OPTION_TWO_PHRASES)


def installment_purchase(text: str) -> tuple[int, int] | None:
    """(monto, cuotas) de una compra: "$60.000 en 6 cuotas" o "60 lucas en 6 cuotas"."""
    normalized = _normalize(text)
    installments = _INSTALLMENTS.search(normalized)
    if not installments:
        return None
    without_installments = _INSTALLMENTS.sub(" ", normalized)
    amount = parse_amount(without_installments)
    count = int(installments.group(1))
    if amount is None or count <= 0:
        return None
    return amount, count


# --- Plan y cálculo del turno ---------------------------------------------

# Desde estos escalones ella ya vio su mínimo y su saldo: puede simular un pago.
_SIMULATION_READY = (
    ConversationMode.BALANCE_OVERVIEW,
    ConversationMode.MINIMUM_BREAKDOWN,
    ConversationMode.AMOUNT_SIMULATION,
)


def plan_turn(state: ConversationState, text: str) -> None:
    """Ajusta el estado ANTES de llamar al agente, si ella pidió una simulación.

    - "¿y si pago 100 mil?" desde E2-E4 → modo E4 con ese monto.
    - En PAUSA_COMPRA, elegir la opción 2 → pidió el cálculo de la cuota
      (salvo que la compra sea esencial).
    """
    if state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False:
        return
    ready = state.mode in _SIMULATION_READY or (
        state.mode in figures.LADDER_MODES and state.max_mode_reached in _SIMULATION_READY
    )
    if ready:
        amount = payment_amount(text)
        if amount:
            state.chosen_payment = amount
            state.mode = ConversationMode.AMOUNT_SIMULATION
    if (
        state.mode == ConversationMode.INSTALLMENT_PAUSE
        and state.installment_essential is not True
        and chose_installment_calculation(text)
    ):
        state.installment_calc_requested = True


def _confirmed(value: int) -> FinancialField:
    return FinancialField(value=value, status=FieldStatus.CONFIRMED)


def _payoff(suffix: str, plan: PayoffPlan) -> dict[str, int]:
    if plan.months is None or plan.total_interest is None:
        return {}
    return {
        f"meses_pagando_{suffix}": plan.months,
        f"intereses_pagando_{suffix}": plan.total_interest,
    }


def build_turn_figures(
    state: ConversationState, snapshot: BankSnapshot | None, text: str
) -> tuple[dict[str, FinancialField], dict[str, int]]:
    """Campos extra de `financial_data` y `calculations` del simulador para este turno."""
    if snapshot is None or not snapshot.credit_cards:
        return {}, {}
    card = snapshot.credit_cards[0]
    statement = card.statement
    fields: dict[str, FinancialField] = {}
    calculations: dict[str, int] = {}

    if state.mode == ConversationMode.AMOUNT_SIMULATION and state.chosen_payment:
        chosen = state.chosen_payment
        fields["monto_elegido"] = _confirmed(chosen)
        comparison = compare_with_minimum(
            card.total_debt, statement.monthly_rate_pct, chosen, statement.minimum_payment
        )
        calculations |= _payoff("monto_elegido", comparison.chosen)
        calculations |= _payoff("minimo", comparison.minimum_only)
        if comparison.months_saved is not None and comparison.interest_saved is not None:
            calculations["meses_ahorrados"] = comparison.months_saved
            calculations["intereses_ahorrados"] = comparison.interest_saved
        if comparison.chosen.months is None:
            # El monto no alcanza a cubrir los intereses: se informa cuánto son.
            calculations["intereses_del_mes"] = int(
                (card.total_debt * statement.monthly_rate_pct / 100).to_integral_value()
            )
        months = monthly_cashflow(snapshot.transactions)
        if months:
            last = months[-1]
            loans = sum(loan.installment_amount for loan in snapshot.consumer_loans)
            check = affordability(last.month, last.income, last.essential_spending, loans, chosen)
            if check.shortfall:
                calculations["faltante_para_gastos_esenciales"] = check.shortfall

    if state.mode == ConversationMode.INSTALLMENT_PAUSE and state.installment_calc_requested:
        purchase = installment_purchase(text)
        if purchase:
            effect = installment_effect(statement.minimum_payment, *purchase)
            fields["minimo_con_cuota"] = _confirmed(effect.new_minimum)
            calculations["cuota_mensual"] = effect.monthly_installment

    return fields, calculations
