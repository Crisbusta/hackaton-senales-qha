"""Precarga de datos del banco + verificación posterior de cifras por modo."""

import asyncio
import json
from pathlib import Path

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationMode,
    Intent,
)
from app.modules.banking.provider import FIXTURES_DIR, MockBankProvider
from app.modules.conversation.figures import PRELOAD_FIELDS, amounts_in
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import STEP_BY_STEP_REPLY, ConversationService
from app.modules.whatsapp.schemas import IncomingMessage

USER_ID = "+56912345678"


class SpyAgent:
    def __init__(self, *responses: AgentResponse) -> None:
        self.responses = list(responses)
        self.requests: list[AgentRequest] = []

    async def process(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else AgentResponse(message="ok")


class SlowFirstCallAgent(SpyAgent):
    async def process(self, request: AgentRequest) -> AgentResponse:
        if not self.requests:
            await asyncio.sleep(1)
        return await super().process(request)


class BrokenBank:
    async def get_snapshot(self, user_id: str):
        raise ConnectionError("bank down")


def turn(
    bank,
    *responses: AgentResponse,
    mode=ConversationMode.GREETING,
    last_intent=None,
    preferences=None,
    body="",
    agent=None,
    timeout=5.0,
):
    """Ejecuta un turno con estado sembrado. Devuelve (agente, respuesta enviada)."""
    agent = agent or SpyAgent(*responses)
    repo = InMemoryConversationRepository()
    state = asyncio.run(repo.get(USER_ID))
    state.mode = mode
    state.max_mode_reached = mode if mode != ConversationMode.SUPPORT else state.max_mode_reached
    state.last_intent = last_intent
    state.preferences = preferences or {}
    asyncio.run(repo.save(state))

    service = ConversationService(agent=agent, repository=repo, agent_timeout_s=timeout, bank=bank)
    incoming = IncomingMessage(message_sid="SM1", from_number=f"whatsapp:{USER_ID}", body=body)
    return agent, asyncio.run(service.handle(incoming))


@pytest.fixture
def bank() -> MockBankProvider:
    return MockBankProvider("patricia")


def reply_with(message: str, intent: str = "UNKNOWN", **kwargs) -> AgentResponse:
    return AgentResponse(message=message, intent=intent, **kwargs)


# --- Qué se precarga -------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    [
        ConversationMode.GREETING,
        ConversationMode.DUE_DATE,
        ConversationMode.BALANCE_OVERVIEW,
        ConversationMode.MINIMUM_BREAKDOWN,
    ],
)
def test_ladder_modes_preload_the_whole_ladder(bank, mode) -> None:
    agent, _ = turn(bank, mode=mode)

    sent = agent.requests[0].financial_data
    assert set(sent) == {f for f in PRELOAD_FIELDS if f != "monto_elegido"}
    assert sent["minimo"].value == 74350


@pytest.mark.parametrize(
    "mode", [ConversationMode.PAUSE, ConversationMode.SUPPORT, ConversationMode.CLOSING]
)
def test_detours_never_preload(bank, mode) -> None:
    agent, _ = turn(bank, mode=mode)

    assert agent.requests[0].financial_data == {}


def test_declined_figures_sends_nothing(bank) -> None:
    agent, _ = turn(
        bank,
        mode=ConversationMode.BALANCE_OVERVIEW,
        preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
    )

    assert agent.requests[0].financial_data == {}


def test_without_consent_no_bank_data_is_used(tmp_path: Path) -> None:
    data = json.loads((FIXTURES_DIR / "patricia.json").read_text(encoding="utf-8"))
    data["consent"]["granted"] = False
    (tmp_path / "sin_consentimiento.json").write_text(json.dumps(data), encoding="utf-8")

    agent, _ = turn(
        MockBankProvider("sin_consentimiento", fixtures_dir=tmp_path),
        mode=ConversationMode.BALANCE_OVERVIEW,
    )

    assert agent.requests[0].financial_data == {}


def test_bank_failure_does_not_break_the_turn() -> None:
    agent, reply = turn(BrokenBank(), mode=ConversationMode.BALANCE_OVERVIEW)

    assert agent.requests[0].financial_data == {}
    assert reply == "ok"


# --- Verificación posterior ------------------------------------------------


def test_figure_of_the_step_she_moved_to_is_sent(bank) -> None:
    # En E1 pide más: el turno termina en E2, así que el mínimo y el saldo valen.
    agent, reply = turn(
        bank,
        reply_with("Tu pago mínimo es $74.350 y el saldo $1.064.000.", Intent.INFO_REQUEST),
        mode=ConversationMode.DUE_DATE,
    )

    assert reply == "Tu pago mínimo es $74.350 y el saldo $1.064.000."
    assert len(agent.requests) == 1


def test_due_date_year_is_not_mistaken_for_an_amount(bank) -> None:
    agent, reply = turn(bank, reply_with("Vence el 3 de noviembre de 2026.", Intent.INFO_REQUEST))

    assert reply == "Vence el 3 de noviembre de 2026."
    assert len(agent.requests) == 1


def test_figure_ahead_of_the_step_triggers_a_retry_without_it(bank) -> None:
    # En E0 pregunta cuánto debe: la Escalera sube a E1 (solo la fecha).
    agent, reply = turn(
        bank,
        reply_with("Debes $1.064.000.", Intent.INFO_REQUEST),
        reply_with("Partamos por la fecha: vence el 3 de noviembre de 2026."),
    )

    assert reply == "Partamos por la fecha: vence el 3 de noviembre de 2026."
    retry = agent.requests[1]
    assert retry.mode is ConversationMode.DUE_DATE
    assert set(retry.financial_data) == {"vencimiento"}


def test_retry_that_still_has_extra_figures_falls_back(bank) -> None:
    _, reply = turn(
        bank,
        reply_with("Debes $1.064.000.", "saludo"),
        reply_with("Igual te cuento: $74.350."),
    )

    assert reply == STEP_BY_STEP_REPLY


def test_no_time_for_retry_falls_back(bank) -> None:
    # La primera llamada consume 1 s de 3,5: quedan menos de MIN_RETRY_S.
    agent = SlowFirstCallAgent(reply_with("Debes $1.064.000.", "saludo"))

    _, reply = turn(bank, agent=agent, timeout=3.5)

    assert reply == STEP_BY_STEP_REPLY
    assert len(agent.requests) == 1


def test_amount_she_wrote_can_be_repeated(bank) -> None:
    agent, reply = turn(
        bank,
        reply_with("Si pagas $100.000 este mes, avanzas más rápido.", "tangente"),
        mode=ConversationMode.BALANCE_OVERVIEW,
        body="y si pago $100.000?",
    )

    assert reply == "Si pagas $100.000 este mes, avanzas más rápido."
    assert len(agent.requests) == 1


def test_billed_amount_only_when_she_asks(bank) -> None:
    _, reply = turn(
        bank,
        reply_with("Te facturaron $868.000.", Intent.ASKS_BILLED_AMOUNT),
        mode=ConversationMode.BALANCE_OVERVIEW,
    )

    assert reply == "Te facturaron $868.000."


def test_protected_texts_are_never_rewritten(bank) -> None:
    _, reply = turn(
        bank,
        reply_with("Llama al *4141 o al 131.", "angustia_fuerte", protected_text=True),
    )

    assert reply == "Llama al *4141 o al 131."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$74.350", {74350}),
        ("1.064.000 pesos", {1064000}),
        ("son 74350", {74350}),
        ("$ 6", {6}),
        ("en 6 cuotas, 2,8 %", set()),
        ("Llama al *4141", {4141}),
    ],
)
def test_amounts_in(text: str, expected: set[int]) -> None:
    assert amounts_in(text) == expected
