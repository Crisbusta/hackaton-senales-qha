import asyncio

import pytest
from pydantic import ValidationError

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    FieldStatus,
    FinancialField,
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
