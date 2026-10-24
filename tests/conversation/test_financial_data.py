"""Qué datos del banco viajan al redactor, y qué pasa si se le escapa una cifra.

Este archivo probaba la PRECARGA: en los escalones viajaba toda la Escalera y
la respuesta se verificaba después, con un reintento si traía una cifra que no
correspondía. Existía porque el modo se decidía después de redactar, así que no
se sabía de antemano qué datos hacían falta.

Con el turno en tres pasos sí se sabe: el modo final está resuelto antes de
pedir la redacción, así que viajan exactamente los campos de ese modo. La
verificación posterior se queda como red de seguridad —sin reintento: si
salta, algo está mal más arriba— y lo que estos tests protegen ahora es la
inyección exacta.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentReply,
    ConversationMode,
    Intent,
    fields_for_mode,
)
from app.modules.banking.provider import FIXTURES_DIR, MockBankProvider
from app.modules.conversation.figures import PRELOAD_FIELDS, amounts_in
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import STEP_BY_STEP_REPLY, ConversationService
from app.modules.whatsapp.schemas import IncomingMessage
from tests.conftest import TwoStepAgent, turno

USER_ID = "+56912345678"


class SpyAgent(TwoStepAgent):
    """El doble compartido de `conftest`, con el nombre que ya usaban estos
    tests. Recibe pares (Classification, AgentReply): usa `turno()`."""


class SlowFirstCallAgent(SpyAgent):
    async def write(self, request):
        if not self.requests:
            await asyncio.sleep(1)
        return await super().write(request)


class BrokenBank:
    async def get_snapshot(self, user_id: str):
        raise ConnectionError("bank down")


def turn(
    bank,
    *responses: AgentReply,
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


def reply_with(message: str, intent: str = "UNKNOWN", **kwargs) -> tuple:
    return turno(message, intent=intent, **kwargs)


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
def test_ladder_modes_send_exactly_their_fields(bank, mode) -> None:
    """Sin precarga: lo que no es del modo no entra al contexto.

    La precarga mandaba los 9 campos de la Escalera en cualquier escalón, con
    la instrucción "solo si ella lo pide" al lado. Era la causa 1 de las
    respuestas vagas: el modelo tenía la cifra y una regla ambigua sobre
    cuándo decirla, y contestaba "lo podemos ver en un momento".
    """
    agent, _ = turn(bank, mode=mode)

    sent = agent.requests[0].financial_data
    assert set(sent) == set(fields_for_mode(mode))
    assert len(sent) < len(PRELOAD_FIELDS)


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


def test_una_cifra_que_no_viajo_no_se_envia(bank) -> None:
    """La red de seguridad, sin reintento.

    Antes se reintentaba con los datos recortados, porque la precarga hacía
    probable que el modelo tomara una cifra de otro escalón. Ahora solo
    recibe las de su modo, así que una cifra de más significa que algo está
    mal más arriba: la respuesta no sale y no se gasta otra llamada.
    """
    agent, reply = turn(bank, reply_with("Debes $1.064.000.", Intent.INFO_REQUEST))

    assert reply == STEP_BY_STEP_REPLY
    pedido = agent.requests[0]
    assert pedido.mode is ConversationMode.DUE_DATE
    assert set(pedido.financial_data) == {"vencimiento"}
    assert len(agent.write_requests) == 1


def test_una_redaccion_lenta_no_arrastra_al_turno_siguiente(bank) -> None:
    """Sin el segundo llamado, el presupuesto del turno es de una redacción.

    Antes la primera llamada podía consumirlo y dejar al reintento sin
    tiempo: medido contra el modelo real, al entrar a PAUSA_COMPRA el
    reintento produjo la respuesta correcta y se descartó por 0,4 s.
    """
    agent = SlowFirstCallAgent(reply_with("Te cuento.", Intent.OFF_TOPIC))

    _, reply = turn(bank, agent=agent, timeout=3.5)

    assert reply == "Te cuento."
    assert len(agent.write_requests) == 1


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
