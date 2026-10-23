import asyncio

import pytest
from pydantic import ValidationError

from app.modules.agent.interface import (
    INSTALLMENT_CALC_FIELDS,
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    FieldStatus,
    FinancialField,
    Intent,
    calculations_allowed,
    fields_for_mode,
)


def test_request_requires_mode() -> None:
    with pytest.raises(ValidationError):
        AgentRequest(user_id="u1", message="Hola")  # falta mode


def test_request_defaults() -> None:
    request = AgentRequest(user_id="u1", message="Hola", mode=ConversationMode.GREETING)

    assert request.recent_history == []
    assert request.financial_data == {}
    assert request.preferences == {}
    assert request.can_offer_continue is False
    assert request.already_stated_limits == []


def test_response_defaults() -> None:
    response = AgentResponse(message="Hola")

    assert response.intent == "UNKNOWN"
    assert response.event is None
    assert response.essential is None


def test_invalid_role_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConversationMessage(role="system", content="x")


def test_unknown_event_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentResponse(message="x", event="NOT_AN_EVENT")


def test_crisis_event_parses_from_string() -> None:
    """Sin este evento, angustia_fuerte (T05) no tiene ningun efecto
    persistente en el backend: el texto fijo saldria bien, pero el modo
    SUPPORT no quedaria registrado para los turnos siguientes."""
    response = AgentResponse.model_validate({"message": "x", "event": "CRISIS_SUPPORT_NEEDED"})

    assert response.event is ConversationEvent.CRISIS_SUPPORT_NEEDED


def test_financial_field_unreliable_carries_both_values() -> None:
    # El caso de T07 "con conflicto": dos pagos minimos, ninguno se elige.
    campo = FinancialField(value=[74350, 47350], status=FieldStatus.UNRELIABLE)

    assert campo.status is FieldStatus.UNRELIABLE
    assert campo.value == [74350, 47350]


def test_financial_field_absent_has_no_value() -> None:
    campo = FinancialField(status=FieldStatus.ABSENT)

    assert campo.value is None


def test_minimal_injection_only_current_mode_fields_travel() -> None:
    # En E1 el agente no debe recibir el saldo ni el minimo: la inyeccion
    # minima es responsabilidad del backend (T06) al construir el request.
    request = AgentRequest(
        user_id="u1",
        message="cuanto debo",
        mode=ConversationMode.DUE_DATE,
        financial_data={
            "due_date": FinancialField(value="2026-11-03", status=FieldStatus.CONFIRMED),
        },
    )

    assert "balance" not in request.financial_data
    assert "minimum_payment" not in request.financial_data


def test_protocol_can_be_implemented_structurally() -> None:
    class EchoAgent:
        async def process(self, request: AgentRequest) -> AgentResponse:
            return AgentResponse(message=request.message)

    agent: Agent = EchoAgent()
    request = AgentRequest(
        user_id="u1",
        message="Hola",
        mode=ConversationMode.GREETING,
        preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
    )

    assert asyncio.run(agent.process(request)).message == "Hola"


# --- campos agregados en PR #4 -----------------------------------------

def test_new_fields_have_backward_compatible_defaults() -> None:
    # Un request escrito antes de PR #4 sigue siendo valido: nadie que ya
    # use el contrato se rompe por estos campos.
    request = AgentRequest(user_id="u1", message="Hola", mode=ConversationMode.GREETING)

    assert request.max_mode_reached is None
    assert request.calculations == {}
    assert request.previous_intent is None
    assert request.requested_installment_calculation is False


def test_max_mode_reached_accepts_a_mode() -> None:
    request = AgentRequest(
        user_id="u1",
        message="hola, ya estoy mejor",
        mode=ConversationMode.GREETING,
        max_mode_reached=ConversationMode.BALANCE_OVERVIEW,
    )

    assert request.max_mode_reached is ConversationMode.BALANCE_OVERVIEW


def test_calculations_carries_simulator_output() -> None:
    # Las cifras del simulador (T10) llegan resueltas: el agente las
    # redacta, no las calcula.
    request = AgentRequest(
        user_id="u1",
        message="y si pago 50 mil mas?",
        mode=ConversationMode.AMOUNT_SIMULATION,
        calculations={"months_with_extra": 43, "interest_saved": 123000},
    )

    assert request.calculations["months_with_extra"] == 43


def test_previous_intent_enables_conditional_data() -> None:
    request = AgentRequest(
        user_id="u1",
        message="y eso que significa?",
        mode=ConversationMode.BALANCE_OVERVIEW,
        previous_intent="pregunta_monto_facturado",
    )

    assert request.previous_intent == "pregunta_monto_facturado"


# --- revisión de backend en #4: montos enteros -------------------------

def test_amounts_are_integers_not_floats() -> None:
    """El peso chileno no tiene decimales. Con `float`, un saldo que pasa
    por el simulador puede terminar redactado como "74349,99999999999"."""
    field = FinancialField(value=74350, status=FieldStatus.CONFIRMED)
    assert isinstance(field.value, int)

    with pytest.raises(ValidationError):
        FinancialField(value=74349.99, status=FieldStatus.CONFIRMED)


def test_non_numeric_values_still_travel_as_text() -> None:
    # Fechas y tasas se leen pero no se operan: viajan ya formateadas.
    for value in ("3 de noviembre de 2026", "2,8 % mensual"):
        assert FinancialField(value=value, status=FieldStatus.CONFIRMED).value == value


def test_unreliable_field_keeps_both_amounts_as_integers() -> None:
    field = FinancialField(value=[74350, 47350], status=FieldStatus.UNRELIABLE)
    assert all(isinstance(v, int) for v in field.value)


def test_calculations_and_mentioned_amount_are_integers() -> None:
    request = AgentRequest(
        user_id="u1", message="50 mil mas",
        mode=ConversationMode.AMOUNT_SIMULATION,
        calculations={"meses_con_extra": 43},
    )
    assert isinstance(request.calculations["meses_con_extra"], int)

    with pytest.raises(ValidationError):
        AgentResponse(message="x", mentioned_amount=59999.5)


# --- revisión de backend en #4: etiquetas publicadas -------------------

def test_the_sixteen_labels_are_published() -> None:
    """El backend necesita nombrarlas para las transiciones de T06; como
    strings sueltos, un typo rompe una transición sin que nadie lo note."""
    assert len(list(Intent)) == 16
    assert Intent.DISTRESS == "angustia_fuerte"
    assert Intent.INSTALLMENT_INTENT == "intencion_compra_cuotas"


def test_intent_stays_a_free_string_in_the_response() -> None:
    # Si el modelo inventa una etiqueta, se registra tal cual en la traza
    # en vez de reventar el turno.
    assert AgentResponse(message="x", intent="algo_nuevo").intent == "algo_nuevo"


# --- revisión de backend en #4: una sola tabla de campos por modo ------

def test_fields_by_mode_is_the_single_source_of_truth() -> None:
    assert fields_for_mode(ConversationMode.GREETING) == []
    assert fields_for_mode(ConversationMode.DUE_DATE) == ["vencimiento"]
    assert "saldo" not in fields_for_mode(ConversationMode.DUE_DATE)
    for mode in (ConversationMode.PAUSE, ConversationMode.SUPPORT,
                 ConversationMode.CLOSING):
        assert fields_for_mode(mode) == [], mode


def test_conditional_fields_need_both_the_question_and_the_mode() -> None:
    assert "facturado" in fields_for_mode(
        ConversationMode.BALANCE_OVERVIEW, Intent.ASKS_BILLED_AMOUNT)
    # preguntar no alcanza: el dato no existe antes de su escalón
    assert "facturado" not in fields_for_mode(
        ConversationMode.DUE_DATE, Intent.ASKS_BILLED_AMOUNT)
    assert "tasa" not in fields_for_mode(
        ConversationMode.BALANCE_OVERVIEW, "pregunta_intereses")
    assert "tasa" in fields_for_mode(
        ConversationMode.MINIMUM_BREAKDOWN, "pregunta_intereses")


def test_installment_pause_has_no_figures_until_she_asks_for_the_calc() -> None:
    assert fields_for_mode(ConversationMode.INSTALLMENT_PAUSE) == []
    assert fields_for_mode(
        ConversationMode.INSTALLMENT_PAUSE,
        requested_installment_calculation=True,
    ) == list(INSTALLMENT_CALC_FIELDS)


def test_calculations_are_allowed_only_where_they_were_asked_for() -> None:
    assert calculations_allowed(ConversationMode.AMOUNT_SIMULATION)
    assert calculations_allowed(
        ConversationMode.INSTALLMENT_PAUSE, requested_installment_calculation=True)
    for mode in (ConversationMode.GREETING, ConversationMode.BALANCE_OVERVIEW,
                 ConversationMode.SUPPORT, ConversationMode.INSTALLMENT_PAUSE):
        assert not calculations_allowed(mode), mode
