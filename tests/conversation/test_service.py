import asyncio

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMode,
)
from app.modules.agent.mock import MockAgent
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import NO_FIGURES_REPLY, ConversationService
from app.modules.whatsapp.schemas import IncomingMessage
from app.shared.exceptions import IntegrationError

USER = "whatsapp:+56912345678"
USER_ID = "+56912345678"


class SpyAgent:
    """Devuelve respuestas preprogramadas y guarda los requests recibidos."""

    def __init__(self, *responses: AgentResponse) -> None:
        self.responses = list(responses)
        self.requests: list[AgentRequest] = []

    async def process(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else AgentResponse(message="ok")


class SlowAgent:
    async def process(self, request: AgentRequest) -> AgentResponse:
        await asyncio.sleep(1)
        return AgentResponse(message="tarde")


class BrokenAgent:
    async def process(self, request: AgentRequest) -> AgentResponse:
        raise RuntimeError("provider exploded")


def make_service(agent, timeout: float = 5.0):
    repo = InMemoryConversationRepository()
    return ConversationService(agent=agent, repository=repo, agent_timeout_s=timeout), repo


def msg(body: str, sid: str) -> IncomingMessage:
    return IncomingMessage(message_sid=sid, from_number=USER, body=body)


def run(service: ConversationService, body: str, sid: str) -> str | None:
    return asyncio.run(service.handle(msg(body, sid)))


def state_of(repo: InMemoryConversationRepository):
    return asyncio.run(repo.get(USER_ID))


# --- Flujo básico --------------------------------------------------------


def test_reply_comes_from_agent_and_turn_is_persisted() -> None:
    service, repo = make_service(MockAgent())

    reply = run(service, "Hola", "SM1")

    state = state_of(repo)
    assert reply and reply.startswith("Hola")
    assert [(m.role, m.content) for m in state.history] == [
        ("user", "Hola"),
        ("assistant", reply),
    ]
    assert state.turn_count == 1
    assert state.last_intent == "saludo"


def test_history_is_sent_to_agent_on_next_turn() -> None:
    agent = SpyAgent()
    service, _ = make_service(agent)

    run(service, "uno", "SM1")
    run(service, "dos", "SM2")

    assert [m.content for m in agent.requests[1].recent_history] == ["uno", "ok"]
    assert agent.requests[1].user_id == USER_ID


# --- Deduplicación -------------------------------------------------------


def test_duplicate_message_sid_is_ignored() -> None:
    agent = SpyAgent()
    service, repo = make_service(agent)

    first = run(service, "Hola", "SM1")
    second = run(service, "Hola", "SM1")

    assert first == "ok"
    assert second is None
    assert len(agent.requests) == 1
    assert state_of(repo).turn_count == 1


# --- Preferencias y reglas deterministas ---------------------------------


def test_explicit_decline_is_persisted_and_sent_to_agent() -> None:
    agent = SpyAgent(
        AgentResponse(message="ok", event=ConversationEvent.DECLINED_BALANCE_REVIEW),
    )
    service, repo = make_service(agent)

    run(service, "Prefiero no verlo todavía", "SM1")
    run(service, "hola", "SM2")

    state = state_of(repo)
    assert state.preferences[PREF_SHOW_FINANCIAL_FIGURES] is False
    assert state.mode is ConversationMode.PAUSE
    assert [e.event for e in state.events] == [ConversationEvent.DECLINED_BALANCE_REVIEW]
    assert agent.requests[1].preferences == {PREF_SHOW_FINANCIAL_FIGURES: False}
    assert agent.requests[1].mode is ConversationMode.PAUSE


def test_current_request_overrides_previous_decline() -> None:
    agent = SpyAgent(
        AgentResponse(message="ok", event=ConversationEvent.DECLINED_BALANCE_REVIEW),
        AgentResponse(message="ok", event=ConversationEvent.REQUESTED_BALANCE),
    )
    service, repo = make_service(agent)

    run(service, "Prefiero no verlo", "SM1")
    run(service, "Ahora sí, quiero saber cuánto debo", "SM2")

    assert state_of(repo).preferences[PREF_SHOW_FINANCIAL_FIGURES] is True


def test_crisis_event_switches_to_support_mode() -> None:
    agent = SpyAgent(
        AgentResponse(message="ok", event=ConversationEvent.CRISIS_SUPPORT_NEEDED),
    )
    service, repo = make_service(agent)

    run(service, "...", "SM1")

    assert state_of(repo).mode is ConversationMode.SUPPORT


@pytest.mark.parametrize("reply", ["Debes $74.350", "Tu saldo es 1.234.567", "Son 74350 pesos"])
def test_figures_are_blocked_after_decline(reply: str) -> None:
    agent = SpyAgent(
        AgentResponse(message="ok", event=ConversationEvent.DECLINED_BALANCE_REVIEW),
        AgentResponse(message=reply),
    )
    service, repo = make_service(agent)

    run(service, "Prefiero no verlo", "SM1")
    sent = run(service, "hola", "SM2")

    assert sent == NO_FIGURES_REPLY
    assert state_of(repo).history[-1].content == NO_FIGURES_REPLY


def test_figures_allowed_without_decline() -> None:
    service, _ = make_service(SpyAgent(AgentResponse(message="Tu pago mínimo es $74.350")))

    assert run(service, "cuánto debo", "SM1") == "Tu pago mínimo es $74.350"


def test_mock_flow_respects_decline_end_to_end() -> None:
    service, _ = make_service(MockAgent())

    run(service, "Prefiero no verlo todavía", "SM1")
    reply = run(service, "Hola", "SM2")

    assert reply and "números" in reply
    assert not any(ch.isdigit() for ch in reply)


# --- Errores -------------------------------------------------------------


def test_agent_timeout_raises_integration_error() -> None:
    service, repo = make_service(SlowAgent(), timeout=0.01)

    with pytest.raises(IntegrationError):
        run(service, "Hola", "SM1")
    assert state_of(repo).history == []


def test_agent_failure_raises_integration_error() -> None:
    service, _ = make_service(BrokenAgent())

    with pytest.raises(IntegrationError):
        run(service, "Hola", "SM1")


# --- regresion encontrada en el recorrido end-to-end real (23 oct) ---

CRISIS_TEXT = (
    "Te leo. Dejemos las cuentas de lado por ahora, no hay apuro con eso. "
    "¿Estás a salvo en este momento? Si quieres hablar con alguien, el *4141 "
    "es gratis y atiende a toda hora; si sientes que estás en peligro ahora, "
    "llama al 131."
)


def test_the_crisis_line_survives_the_no_figures_preference() -> None:
    """El fallo real: `contains_amount` lee "4141" como un monto. Con la
    preferencia de no ver cifras activa, un mensaje de ideación suicida
    recibía NO_FIGURES_REPLY en vez de la línea de ayuda.
    """
    agent = SpyAgent(
        AgentResponse(
            message="Está bien, lo dejamos acá.",
            intent="rechazo_pausa",
            event=ConversationEvent.DECLINED_BALANCE_REVIEW,
            protected_text=True,
        ),
        AgentResponse(
            message=CRISIS_TEXT,
            intent="angustia_fuerte",
            event=ConversationEvent.CRISIS_SUPPORT_NEEDED,
            protected_text=True,
        ),
    )
    service, repo = make_service(agent)

    run(service, "no quiero verlo", "SM1")
    assert state_of(repo).preferences[PREF_SHOW_FINANCIAL_FIGURES] is False

    reply = run(service, "ya no quiero vivir", "SM2")

    assert reply == CRISIS_TEXT
    assert "*4141" in reply and "131" in reply
    assert reply != NO_FIGURES_REPLY
    assert state_of(repo).mode is ConversationMode.SUPPORT


def test_a_model_written_reply_with_figures_is_still_blocked() -> None:
    """La guarda anterior no debe abrir la puerta: lo que redactó el modelo
    sigue filtrándose."""
    agent = SpyAgent(
        AgentResponse(
            message="Está bien.",
            intent="rechazo_pausa",
            event=ConversationEvent.DECLINED_BALANCE_REVIEW,
            protected_text=True,
        ),
        AgentResponse(message="Tu saldo es $1.064.000.", intent="solicitud_informacion"),
    )
    service, _ = make_service(agent)

    run(service, "no quiero verlo", "SM1")

    assert run(service, "y mi saldo?", "SM2") == NO_FIGURES_REPLY
