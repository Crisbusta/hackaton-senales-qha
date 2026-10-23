import asyncio

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMode,
    FieldStatus,
)
from app.modules.agent.mock import MockAgent
from app.modules.banking.provider import MockBankProvider
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


# --- las reglas anti-bucle a traves del servicio ------------------------

def _resp(message, intent, event=None, protected=False):
    return AgentResponse(
        message=message, intent=intent, event=event, protected_text=protected
    )


def test_the_offer_to_continue_is_made_once_per_step() -> None:
    """AB2. Sin esto, Contigo pregunta "¿seguimos?" en cada turno."""
    agent = SpyAgent(
        # Turno 1: el agente responde estando en E0, donde no puede ofrecer.
        _resp("Puedo mirarlo por ti.", "solicitud_informacion",
              ConversationEvent.REQUESTED_BALANCE),
        # Turno 2: ya en E1, y aca si usa la oferta.
        _resp("Vence el 3 de noviembre. ¿Seguimos o lo dejamos aquí?", "tangente"),
        _resp("Eso lo vemos en un momento.", "tangente"),
    )
    service, _ = make_service(agent)

    run(service, "cuando vence?", "SM1")   # E0 -> E1
    run(service, "y eso cuando es?", "SM2")
    assert agent.requests[1].mode is ConversationMode.DUE_DATE
    assert agent.requests[1].can_offer_continue is True   # todavia disponible

    run(service, "otra cosa", "SM3")
    # la oferta salio en E1, asi que en E1 no se vuelve a ofrecer
    assert agent.requests[2].can_offer_continue is False


def test_the_crisis_line_is_offered_once() -> None:
    """AB7: repetir el mismo limite suena a bucle."""
    agent = SpyAgent(
        _resp("Te leo. El *4141 es gratis.", "angustia_fuerte",
              ConversationEvent.CRISIS_SUPPORT_NEEDED, protected=True),
        _resp("Acá estoy contigo.", "verguenza_culpa"),
    )
    service, _ = make_service(agent)

    run(service, "no quiero vivir", "SM1")
    assert agent.requests[0].already_stated_limits == []

    run(service, "me da verguenza", "SM2")
    assert agent.requests[1].already_stated_limits == ["linea_crisis"]


def test_the_second_empty_acknowledgement_gets_no_reply() -> None:
    """AB8. Y ahorra la llamada al modelo: el agente no se invoca."""
    agent = SpyAgent(_resp("De nada, Patricia.", "cierre"))
    service, _ = make_service(agent)

    assert run(service, "gracias", "SM1") == "De nada, Patricia."
    assert run(service, "gracias", "SM2") is None
    assert len(agent.requests) == 1


def test_content_after_an_acknowledgement_is_answered_again() -> None:
    agent = SpyAgent(
        _resp("De nada.", "cierre"),
        _resp("Te leo.", "tangente"),
        _resp("De nada otra vez.", "cierre"),
    )
    service, _ = make_service(agent)

    run(service, "gracias", "SM1")
    run(service, "igual me da pena todo esto", "SM2")

    assert run(service, "gracias", "SM3") == "De nada otra vez."


def test_the_request_carries_what_she_already_reached() -> None:
    """`max_mode_reached` y `previous_intent` llegaban vacios al agente, asi
    que `retoma_voluntaria` no podia reconocer lo ya visto."""
    agent = SpyAgent(
        _resp("Vence el 3 de noviembre.", "solicitud_informacion",
              ConversationEvent.REQUESTED_BALANCE),
        _resp("El pago mínimo es $74.350.", "disposicion_explorar",
              ConversationEvent.ACCEPTED_NEXT_STEP),
        _resp("Qué bueno que volviste.", "retoma_voluntaria"),
    )
    service, repo = make_service(agent)

    run(service, "cuando vence?", "SM1")
    run(service, "ya, sigamos", "SM2")
    run(service, "hola, ya estoy mejor", "SM3")

    assert agent.requests[2].mode is ConversationMode.BALANCE_OVERVIEW
    assert agent.requests[2].max_mode_reached is ConversationMode.BALANCE_OVERVIEW
    assert agent.requests[2].previous_intent == "disposicion_explorar"
    # tras retomar vuelve a E0, pero el maximo se conserva
    assert state_of(repo).mode is ConversationMode.GREETING
    assert state_of(repo).max_mode_reached is ConversationMode.BALANCE_OVERVIEW


# --- T09: las cifras del banco llegan al agente -------------------------


def make_bank_service(agent, provider=None, timeout: float = 5.0):
    """Servicio con banco vinculado. Sin `provider`, el escenario de Patricia."""
    repo = InMemoryConversationRepository()
    service = ConversationService(
        agent=agent,
        repository=repo,
        agent_timeout_s=timeout,
        bank_provider=MockBankProvider("patricia") if provider is None else provider,
    )
    return service, repo


def test_without_a_bank_provider_the_agent_gets_no_figures() -> None:
    """El default es el caso seguro: sin proveedor, ninguna cifra. Es lo que
    hacia `main` hasta ahora, y por eso C02 no podia pasar."""
    agent = SpyAgent(_resp("Vence el 3 de noviembre.", "solicitud_informacion"))
    service, _ = make_service(agent)

    run(service, "cuando vence?", "SM1")

    assert agent.requests[0].financial_data == {}


def test_the_due_date_reaches_the_agent_on_the_first_step() -> None:
    """C02 turno 2: el dato tiene que estar en el contexto antes de que el
    modelo redacte. Si no llega, el turno sale evasivo."""
    agent = SpyAgent(
        _resp("Puedo mirar la fecha contigo.", "disposicion_explorar"),
        _resp("Vence el 3 de noviembre.", "solicitud_informacion"),
    )
    service, _ = make_bank_service(agent)

    run(service, "quiero ver lo de la tarjeta, pero poquito", "SM1")
    run(service, "ya", "SM2")

    data = agent.requests[1].financial_data
    assert data["vencimiento"].value == "3 de noviembre de 2026"
    # un dato por vez: el minimo todavia no
    assert "minimo" not in data


def test_the_minimum_and_balance_arrive_one_step_later() -> None:
    """C02 turno 3: $74.350 y $1.064.000, las cifras que promete casos_p0."""
    agent = SpyAgent(
        _resp("Puedo mirar la fecha contigo.", "disposicion_explorar"),
        _resp("Vence el 3 de noviembre.", "disposicion_explorar"),
        _resp("El minimo es $74.350 y el saldo $1.064.000.", "solicitud_informacion"),
    )
    service, _ = make_bank_service(agent)

    run(service, "quiero verlo de a poco", "SM1")
    run(service, "veamos", "SM2")
    run(service, "y cuanto es en total?", "SM3")

    data = agent.requests[2].financial_data
    assert data["minimo"].value == 74350
    assert data["saldo"].value == 1064000


def test_a_decline_stops_the_bank_from_being_consulted() -> None:
    """C01: tras el rechazo no basta con filtrar la respuesta; el dato no
    entra al contexto, asi que no hay nada que filtrar."""

    class ExplodingProvider:
        async def get_snapshot(self, user_id: str):
            raise AssertionError("el banco no debe consultarse tras un rechazo")

    agent = SpyAgent(
        _resp("Lo dejamos aqui.", "rechazo_pausa", ConversationEvent.DECLINED_BALANCE_REVIEW),
        _resp("Aqui estoy.", "solicitud_informacion"),
    )
    service, _ = make_bank_service(agent, provider=ExplodingProvider())

    run(service, "no quiero ver nada de la tarjeta ahora", "SM1")
    run(service, "cuanto debo?", "SM2")

    assert agent.requests[1].financial_data == {}


def test_a_broken_bank_provider_does_not_lose_the_turn() -> None:
    """Perder la conversacion por un proveedor caido seria peor que
    responder sin cifras. Los campos salen ABSENT y el turno sigue."""

    class BrokenProvider:
        async def get_snapshot(self, user_id: str):
            raise RuntimeError("open finance down")

    agent = SpyAgent(
        _resp("Puedo mirarlo contigo.", "disposicion_explorar"),
        _resp("Esa fecha no la tengo a mano.", "solicitud_informacion"),
    )
    service, _ = make_bank_service(agent, provider=BrokenProvider())

    run(service, "quiero verlo", "SM1")
    reply = run(service, "cuando vence?", "SM2")

    assert reply == "Esa fecha no la tengo a mano."
    assert agent.requests[1].financial_data["vencimiento"].status is FieldStatus.ABSENT


def test_crisis_mode_carries_no_figures_even_after_the_ladder() -> None:
    """C10: ya habiendo visto cifras, una angustia fuerte las saca del
    contexto por completo."""
    agent = SpyAgent(
        _resp("Puedo mirar la fecha contigo.", "disposicion_explorar"),
        _resp("Vence el 3 de noviembre.", "disposicion_explorar"),
        _resp("Me quedo contigo.", "angustia_fuerte",
              ConversationEvent.CRISIS_SUPPORT_NEEDED, protected=True),
        _resp("Aqui sigo.", "cuidadora_agotada", protected=True),
    )
    service, repo = make_bank_service(agent)

    run(service, "quiero verlo", "SM1")
    run(service, "veamos", "SM2")
    run(service, "no quiero seguir viviendo", "SM3")
    run(service, "no, no estoy bien", "SM4")

    assert state_of(repo).mode is ConversationMode.SUPPORT
    assert agent.requests[3].financial_data == {}


# --- T09: la pausa antes de comprar en cuotas (C05) ---------------------


def test_asking_for_the_installment_effect_enables_the_calculation() -> None:
    """C05 turnos 1-2: "me dan ganas de comprar" lleva a PAUSA_COMPRA, y
    "muestrame" habilita el calculo sin sacarla de ese modo."""
    agent = SpyAgent(
        _resp("Podemos esperar, o ver que le haria a tu minimo.", "intencion_compra_cuotas"),
        _resp("Cuanto y en cuantas cuotas?", "solicitud_informacion"),
    )
    service, repo = make_bank_service(agent)

    run(service, "me dan ganas de comprar en cuotas", "SM1")
    assert state_of(repo).mode is ConversationMode.INSTALLMENT_PAUSE

    run(service, "muestrame", "SM2")
    state = state_of(repo)
    # sigue en la pausa de compra: el dato que pidio vive aqui
    assert state.mode is ConversationMode.INSTALLMENT_PAUSE
    assert state.requested_installment_calculation is True


def test_the_enabled_calculation_puts_the_minimum_in_context() -> None:
    agent = SpyAgent(
        _resp("Podemos esperar, o ver el efecto.", "intencion_compra_cuotas"),
        _resp("Cuanto y en cuantas cuotas?", "solicitud_informacion"),
        _resp("Son $10.000 al mes.", "solicitud_informacion"),
    )
    service, _ = make_bank_service(agent)

    run(service, "quiero comprar en cuotas", "SM1")
    run(service, "muestrame", "SM2")
    run(service, "$60.000 en 6 cuotas", "SM3")

    data = agent.requests[2].financial_data
    assert set(data) == {"minimo", "minimo_con_cuota"}
    assert data["minimo"].value == 74350
    # T10 (de B) todavia no calcula: ABSENT, no inventado
    assert data["minimo_con_cuota"].status is FieldStatus.ABSENT


def test_an_essential_purchase_gets_no_pause_and_no_calculation() -> None:
    """docs/maquina-de-estados.md: si `essential` es true, no se ofrece pausa
    ni calculo. Proponerle esperar antes de comprar un remedio es el sermon
    que el caso prohibe."""
    agent = SpyAgent(
        AgentResponse(
            message="Entiendo, son los remedios de tu mama.",
            intent="intencion_compra_cuotas",
            essential=True,
        )
    )
    service, repo = make_bank_service(agent)

    run(service, "necesito comprar los remedios de mi mama en cuotas", "SM1")

    state = state_of(repo)
    assert state.mode is ConversationMode.GREETING
    assert state.requested_installment_calculation is False


def test_her_mentioned_amount_is_remembered() -> None:
    agent = SpyAgent(
        AgentResponse(
            message="Serian $10.000 al mes.",
            intent="solicitud_informacion",
            mentioned_amount=60000,
        )
    )
    service, repo = make_bank_service(agent)

    run(service, "$60.000 en 6 cuotas", "SM1")

    assert state_of(repo).chosen_amount == 60000


def test_leaving_the_installment_pause_clears_the_calculation_request() -> None:
    """C06c: "mejor no, dejemoslo" en medio de la compra no deja la solicitud
    encendida para el proximo tema."""
    agent = SpyAgent(
        _resp("Podemos esperar.", "intencion_compra_cuotas"),
        _resp("Cuanto y en cuantas cuotas?", "solicitud_informacion"),
        _resp("Lo dejamos aqui.", "rechazo_pausa", ConversationEvent.DECLINED_BALANCE_REVIEW),
    )
    service, repo = make_bank_service(agent)

    run(service, "quiero comprar en cuotas", "SM1")
    run(service, "muestrame", "SM2")
    assert state_of(repo).requested_installment_calculation is True

    run(service, "mejor no, dejemoslo", "SM3")
    assert state_of(repo).requested_installment_calculation is False
