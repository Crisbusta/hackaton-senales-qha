from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    Classification,
    ConversationMode,
    FieldStatus,
    FinancialField,
    WriteRequest,
)
from app.modules.conversation import progress
from app.modules.conversation.schemas import ConversationState

E0, E1, E2, E4 = (
    ConversationMode.GREETING,
    ConversationMode.DUE_DATE,
    ConversationMode.BALANCE_OVERVIEW,
    ConversationMode.AMOUNT_SIMULATION,
)

DATA = {
    "vencimiento": FinancialField(value="3 de noviembre de 2026", status=FieldStatus.CONFIRMED),
    "minimo": FinancialField(value=74350, status=FieldStatus.CONFIRMED),
    "saldo": FinancialField(value=1064000, status=FieldStatus.CONFIRMED),
    "minimo_intereses": FinancialField(value=23100, status=FieldStatus.CONFIRMED),
}


def state(mode=E0, turn=2, **kwargs) -> ConversationState:
    return ConversationState(user_id="u1", mode=mode, turn_count=turn, **kwargs)


def reached(s, reply, previous=E0, answered=E0, calculations=None) -> set[str]:
    found = progress.turn_milestones(s, previous, reply, DATA, calculations or {}, answered)
    return {m.key for m in found}


def test_first_turn_is_first_contact() -> None:
    assert reached(state(turn=1), "Hola") == {"primer_contacto"}


def test_a_milestone_needs_the_figure_in_the_reply() -> None:
    # Llegar a E2 no basta: tiene que haber visto la cifra.
    assert reached(state(mode=E2), "Para eso necesito tu estado de cuenta.") == set()
    assert reached(state(mode=E2), "Tu pago mínimo es $74.350.") == {"vio_minimo_y_saldo"}


def test_due_date_counts_with_or_without_the_year() -> None:
    assert "vio_vencimiento" in reached(state(mode=E1), "Vence el 3 de noviembre.")


def test_breakdown() -> None:
    assert "vio_desglose" in reached(state(), "De eso, $23.100 son intereses.")


def test_simulation_needs_e4_a_chosen_amount_and_a_result_in_the_reply() -> None:
    s = state(mode=E2, chosen_payment=100_000)
    calcs = {"intereses_pagando_monto_elegido": 217_060}

    assert "simulo_pago" in reached(
        s, "Pagarías $217.060 de intereses.", answered=E4, calculations=calcs
    )
    assert "simulo_pago" not in reached(
        s, "¿Cuánto quieres probar?", answered=E4, calculations=calcs
    )


def test_no_figure_milestones_when_she_hid_figures() -> None:
    hidden = state(mode=E2, preferences={PREF_SHOW_FINANCIAL_FIGURES: False})

    assert reached(hidden, "Tu pago mínimo es $74.350.") == set()


def test_pause_resume_purchase_pause_and_support() -> None:
    assert "pausa_respetada" in reached(state(mode=ConversationMode.PAUSE), "Lo dejamos.", E2)
    assert "retomo" in reached(state(mode=E0), "Qué bueno.", ConversationMode.PAUSE)
    assert "pausa_antes_de_comprar" in reached(
        state(mode=ConversationMode.INSTALLMENT_PAUSE), "Podemos esperar."
    )
    assert "apoyo" in reached(state(mode=ConversationMode.SUPPORT), "Aquí estoy.")


def test_record_turn_marks_each_milestone_once_and_logs_the_turn() -> None:
    s = state(mode=E2)
    request = WriteRequest(
        user_id="u1", message="x", mode=E2, financial_data=DATA,
        classification=Classification(),
    )

    first = progress.record_turn(
        s, E1, "Tu mínimo es $74.350.", request, "disposicion_explorar", None
    )
    s.turn_count += 1
    second = progress.record_turn(s, E2, "Tu mínimo es $74.350.", request, "tangente", None)

    assert first == ["vio_minimo_y_saldo"]
    assert second == []
    assert [e.mode_to for e in s.timeline] == [E2, E2]
    assert s.timeline[0].milestones == first
