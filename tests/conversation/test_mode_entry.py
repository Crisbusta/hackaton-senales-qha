"""Al ENTRAR a PAUSA_COMPRA en un turno, la respuesta se pide de nuevo con el modo nuevo."""

import asyncio

from app.modules.agent.interface import (
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMode,
    Intent,
)
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import ConversationService
from app.modules.whatsapp.schemas import IncomingMessage

USER_ID = "+56912345678"
OPTIONS = "Podemos esperar diez minutos, o te cuento cuánto le sumaría la cuota. Tú eliges."


class SpyAgent:
    def __init__(self, *responses: AgentResponse, slow_first: float = 0.0) -> None:
        self.responses = list(responses)
        self.requests: list[AgentRequest] = []
        self.slow_first = slow_first

    async def process(self, request: AgentRequest) -> AgentResponse:
        if not self.requests and self.slow_first:
            await asyncio.sleep(self.slow_first)
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else AgentResponse(message="ok")


class FailingSecondCall(SpyAgent):
    async def process(self, request: AgentRequest) -> AgentResponse:
        if self.requests:
            raise RuntimeError("provider down")
        return await super().process(request)


def turn(agent, mode=ConversationMode.GREETING, timeout=5.0):
    repo = InMemoryConversationRepository()
    state = asyncio.run(repo.get(USER_ID))
    state.mode = mode
    asyncio.run(repo.save(state))
    service = ConversationService(agent=agent, repository=repo, agent_timeout_s=timeout)
    incoming = IncomingMessage(
        message_sid="SM1",
        from_number=f"whatsapp:{USER_ID}",
        body="Me dan ganas de comprar en cuotas.",
    )
    reply = asyncio.run(service.handle(incoming))
    return reply, asyncio.run(repo.get(USER_ID))


def installment(message: str = "Te leo.", **kwargs) -> AgentResponse:
    return AgentResponse(message=message, intent=Intent.INSTALLMENT_INTENT, **kwargs)


def test_entering_the_installment_pause_answers_with_the_options_in_the_same_turn() -> None:
    agent = SpyAgent(installment(essential=False), AgentResponse(message=OPTIONS))

    reply, state = turn(agent)

    assert reply == OPTIONS
    assert len(agent.requests) == 2
    assert agent.requests[0].mode is ConversationMode.GREETING
    assert agent.requests[1].mode is ConversationMode.INSTALLMENT_PAUSE
    assert agent.requests[1].financial_data == {}
    assert state.mode is ConversationMode.INSTALLMENT_PAUSE


def test_the_first_call_keeps_the_classification() -> None:
    agent = SpyAgent(installment(essential=False), AgentResponse(message=OPTIONS, intent="ambiguo"))

    _, state = turn(agent)

    assert state.last_intent == Intent.INSTALLMENT_INTENT
    assert state.installment_essential is False


def test_no_second_call_when_already_in_the_installment_pause() -> None:
    agent = SpyAgent(installment())

    turn(agent, mode=ConversationMode.INSTALLMENT_PAUSE)

    assert len(agent.requests) == 1


def test_no_second_call_for_detours_with_fixed_texts() -> None:
    agent = SpyAgent(AgentResponse(message="Lo dejamos acá.", intent=Intent.DECLINED))

    _, state = turn(agent)

    assert state.mode is ConversationMode.PAUSE
    assert len(agent.requests) == 1


def test_without_time_left_the_first_reply_stays() -> None:
    agent = SpyAgent(installment("primera"), slow_first=1.0)

    reply, _ = turn(agent, timeout=3.5)

    assert reply == "primera"
    assert len(agent.requests) == 1


def test_if_the_second_call_fails_the_first_reply_stays() -> None:
    agent = FailingSecondCall(installment("primera"))

    reply, _ = turn(agent)

    assert reply == "primera"


def test_resuming_from_a_pause_answers_as_a_welcome_in_the_same_turn() -> None:
    pause_text = AgentResponse(
        message="Está bien, lo dejamos acá.", intent=Intent.VOLUNTARY_RESUME, protected_text=True
    )
    agent = SpyAgent(pause_text, AgentResponse(message="Qué bueno que volviste."))

    reply, state = turn(agent, mode=ConversationMode.PAUSE)

    assert reply == "Qué bueno que volviste."
    assert agent.requests[1].mode is ConversationMode.GREETING
    assert state.mode is ConversationMode.GREETING


def test_a_crisis_text_is_never_rewritten() -> None:
    crisis = AgentResponse(
        message="Llama al *4141 o al 131.",
        intent=Intent.DISTRESS,
        event=ConversationEvent.CRISIS_SUPPORT_NEEDED,
        protected_text=True,
    )
    agent = SpyAgent(crisis)

    reply, _ = turn(agent, mode=ConversationMode.PAUSE)

    assert reply == "Llama al *4141 o al 131."
    assert len(agent.requests) == 1
