"""Qué cifras entran al contexto del agente en cada modo (T09).

Las cifras son las del escenario ficticio de Patricia
(`app/modules/banking/fixtures/patricia.json`) y tienen que coincidir con
las de `docs/casos_p0.md`: si el fixture y los casos se separan, la demo
muestra un número y el caso de prueba espera otro.
"""

import asyncio

import pytest

from app.modules.agent.interface import ConversationMode, FieldStatus
from app.modules.banking.provider import MockBankProvider
from app.modules.banking.schemas import CreditCard
from app.modules.conversation.financial import build_financial_data

# Las cifras que docs/casos_p0.md promete al jurado.
VENCIMIENTO = "3 de noviembre de 2026"
MINIMO = 74350
SALDO = 1064000
FACTURADO = 868000


@pytest.fixture
def card() -> CreditCard:
    snapshot = asyncio.run(MockBankProvider("patricia").get_snapshot("+56912345678"))
    assert snapshot is not None
    return snapshot.credit_cards[0]


def datos(card, modo, **kwargs):
    return build_financial_data(card, mode=modo, **kwargs)


def valores(data) -> dict:
    return {name: field.value for name, field in data.items()}


# --- el filtro por modo -------------------------------------------------


def test_greeting_gets_no_figures_at_all(card: CreditCard) -> None:
    assert datos(card, ConversationMode.GREETING) == {}


def test_first_step_gets_only_the_due_date(card: CreditCard) -> None:
    assert valores(datos(card, ConversationMode.DUE_DATE)) == {"vencimiento": VENCIMIENTO}


def test_second_step_adds_minimum_and_balance(card: CreditCard) -> None:
    assert valores(datos(card, ConversationMode.BALANCE_OVERVIEW)) == {
        "vencimiento": VENCIMIENTO,
        "minimo": MINIMO,
        "saldo": SALDO,
    }


def test_third_step_adds_the_minimum_breakdown(card: CreditCard) -> None:
    data = valores(datos(card, ConversationMode.MINIMUM_BREAKDOWN))

    assert data["minimo_intereses"] + data["minimo_cuotas"] + data["minimo_abono"] == MINIMO


@pytest.mark.parametrize(
    "modo",
    [
        ConversationMode.PAUSE,
        ConversationMode.INSTALLMENT_PAUSE,
        ConversationMode.SUPPORT,
        ConversationMode.CLOSING,
    ],
)
def test_pauses_support_and_closing_never_get_figures(card: CreditCard, modo) -> None:
    """Es la garantía de C01 y C10: tras un rechazo o una angustia fuerte, el
    modelo no tiene ninguna cifra en el contexto, así que no puede filtrarla
    ni por error de redacción."""
    assert datos(card, modo) == {}


# --- datos condicionales ------------------------------------------------


def test_billed_amount_enters_only_when_she_just_asked_for_it(card: CreditCard) -> None:
    sin_preguntar = datos(card, ConversationMode.BALANCE_OVERVIEW)
    preguntando = datos(
        card,
        ConversationMode.BALANCE_OVERVIEW,
        previous_intent="pregunta_monto_facturado",
    )

    assert "facturado" not in sin_preguntar
    assert valores(preguntando)["facturado"] == FACTURADO


def test_billed_amount_does_not_enter_from_the_greeting(card: CreditCard) -> None:
    """El dato condicional necesita el escalón Y la pregunta: preguntar en E0
    no lo habilita, porque en E0 todavía no vio ninguna cifra."""
    data = datos(card, ConversationMode.GREETING, previous_intent="pregunta_monto_facturado")

    assert data == {}


# --- la pausa antes de comprar en cuotas (C05) --------------------------


def test_installment_pause_gets_figures_only_when_she_asks_for_the_calculation(
    card: CreditCard,
) -> None:
    data = datos(card, ConversationMode.INSTALLMENT_PAUSE, requested_installment_calculation=True)

    assert set(data) == {"minimo", "minimo_con_cuota"}
    assert data["minimo"].value == MINIMO


def test_the_installment_effect_is_absent_until_t10_computes_it(card: CreditCard) -> None:
    """`minimo_con_cuota` lo calcula el simulador (T10, de B), que todavía no
    existe. Hasta entonces viaja ABSENT y el agente dice que no lo tiene, que
    es la verdad. El día que T10 aterrice, este test cambia de expectativa."""
    data = datos(card, ConversationMode.INSTALLMENT_PAUSE, requested_installment_calculation=True)

    assert data["minimo_con_cuota"].status is FieldStatus.ABSENT
    assert data["minimo_con_cuota"].value is None


# --- el monto que ella eligió (E4) --------------------------------------


def test_her_own_amount_comes_back_as_a_confirmed_field(card: CreditCard) -> None:
    data = datos(card, ConversationMode.AMOUNT_SIMULATION, chosen_amount=60000)

    assert data["monto_elegido"].value == 60000
    assert data["monto_elegido"].status is FieldStatus.CONFIRMED


def test_chosen_amount_is_absent_when_she_never_named_one(card: CreditCard) -> None:
    data = datos(card, ConversationMode.AMOUNT_SIMULATION)

    assert data["monto_elegido"].status is FieldStatus.ABSENT


# --- sin banco vinculado (C03) ------------------------------------------


def test_without_a_linked_bank_every_admissible_field_is_absent() -> None:
    """C03: mostrar lo que sí aparece y decir qué no aparece. Para poder
    decirlo, el agente necesita recibir la clave con su estado; un
    diccionario vacío lo dejaría adivinando si el dato existe."""
    data = build_financial_data(None, mode=ConversationMode.BALANCE_OVERVIEW)

    assert set(data) == {"vencimiento", "minimo", "saldo"}
    assert all(field.status is FieldStatus.ABSENT for field in data.values())
    assert all(field.value is None for field in data.values())


def test_without_a_linked_bank_a_mode_without_fields_stays_empty() -> None:
    assert build_financial_data(None, mode=ConversationMode.PAUSE) == {}


# --- el re-filtro del agente no pierde nada (planteado por B, 23 oct) ---


def contexto_del_modelo(card, modo, previous_intent=None, pidio=False):
    """El bloque DATOS_DISPONIBLES tal como lo ve el modelo.

    Atraviesa las dos capas a propósito: el filtro del backend
    (`build_financial_data`) y el re-filtro del agente (`prompts`). Es la
    única forma de comprobar que la defensa en profundidad no se come los
    datos que el backend sí autorizó.
    """
    from app.modules.agent import prompts
    from app.modules.agent.interface import AgentRequest
    from app.modules.agent.service import _build_data

    data = build_financial_data(
        card,
        mode=modo,
        previous_intent=previous_intent,
        requested_installment_calculation=pidio,
    )
    request = AgentRequest(
        user_id="u",
        message="m",
        mode=modo,
        financial_data=data,
        previous_intent=previous_intent,
        requested_installment_calculation=pidio,
    )
    contexto = prompts.armar_contexto(
        {"modo": modo.value}, _build_data(request), None, previous_intent, pidio, None
    )
    return contexto.split("DATOS_DISPONIBLES")[1].split("HISTORIAL")[0].strip()


def test_the_agents_second_filter_keeps_what_the_backend_authorized(card: CreditCard) -> None:
    """Los dos lados llaman `fields_for_mode` con los mismos tres argumentos,
    así que el re-filtro es idempotente: no descarta nada. Si alguna vez un
    lado deja de pasar `previous_intent` o `requested_installment_calculation`,
    este test cae y se ve por qué."""
    bloque = contexto_del_modelo(card, ConversationMode.BALANCE_OVERVIEW)

    assert "3 de noviembre de 2026" in bloque
    assert "$74.350" in bloque
    assert "$1.064.000" in bloque


def test_the_conditional_field_survives_the_second_filter(card: CreditCard) -> None:
    bloque = contexto_del_modelo(
        card, ConversationMode.BALANCE_OVERVIEW, previous_intent="pregunta_monto_facturado"
    )

    assert "$868.000" in bloque


def test_the_installment_calculation_survives_the_second_filter(card: CreditCard) -> None:
    bloque = contexto_del_modelo(card, ConversationMode.INSTALLMENT_PAUSE, pidio=True)

    assert "minimo: $74.350" in bloque
    # lo que no tenemos se dice, no se omite ni se inventa
    assert "minimo_con_cuota: (ausente)" in bloque


@pytest.mark.parametrize("modo", [ConversationMode.PAUSE, ConversationMode.SUPPORT])
def test_after_a_decline_or_a_crisis_the_model_sees_no_data_at_all(
    card: CreditCard, modo
) -> None:
    assert contexto_del_modelo(card, modo) == "ninguno"
