"""T10: simulación de pagos (E4) y cálculo de cuotas (PAUSA_COMPRA)."""

import asyncio

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationMode,
    Intent,
)
from app.modules.banking.provider import MockBankProvider
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import ConversationService
from app.modules.conversation.simulation import (
    chose_installment_calculation,
    installment_purchase,
    payment_amount,
)
from app.modules.whatsapp.schemas import IncomingMessage

USER_ID = "+56912345678"


class SpyAgent:
    def __init__(self, *responses: AgentResponse) -> None:
        self.responses = list(responses)
        self.requests: list[AgentRequest] = []

    async def process(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else AgentResponse(message="ok")


class Conversation:
    """Varios turnos sobre el mismo estado, con el banco de Patricia."""

    def __init__(self, *responses: AgentResponse, mode=ConversationMode.GREETING, **state):
        self.agent = SpyAgent(*responses)
        self.repo = InMemoryConversationRepository()
        seeded = asyncio.run(self.repo.get(USER_ID))
        seeded.mode = mode
        seeded.max_mode_reached = state.pop("max_mode_reached", mode)
        for key, value in state.items():
            setattr(seeded, key, value)
        asyncio.run(self.repo.save(seeded))
        self.service = ConversationService(
            agent=self.agent,
            repository=self.repo,
            agent_timeout_s=5,
            bank=MockBankProvider("patricia"),
        )
        self._sid = 0

    def send(self, body: str) -> str | None:
        self._sid += 1
        incoming = IncomingMessage(
            message_sid=f"SM{self._sid}", from_number=f"whatsapp:{USER_ID}", body=body
        )
        return asyncio.run(self.service.handle(incoming))

    @property
    def last_request(self) -> AgentRequest:
        return self.agent.requests[-1]

    @property
    def state(self):
        return asyncio.run(self.repo.get(USER_ID))


# --- Detección determinista -----------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿y si pago 100 mil?", 100_000),
        ("y si pago $100.000", 100_000),
        ("si abonara 80 lucas", 80_000),
        ("podría pagar 90.000", 90_000),
        ("me pagaron 100 mil", None),  # un ingreso, no un pago a simular
        ("quiero pagar", None),  # sin monto no se simula
    ],
)
def test_payment_amount(text: str, expected: int | None) -> None:
    assert payment_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$60.000 en 6 cuotas", (60_000, 6)),
        ("60 lucas en 6 cuotas", (60_000, 6)),
        ("unas zapatillas de 45 mil en 3 cuotas", (45_000, 3)),
        ("6 cuotas", None),
    ],
)
def test_installment_purchase(text: str, expected) -> None:
    assert installment_purchase(text) == expected


@pytest.mark.parametrize(("text", "expected"), [("2", True), ("La segunda", True), ("1", False)])
def test_option_two(text: str, expected: bool) -> None:
    assert chose_installment_calculation(text) is expected


# --- E4: simular un pago --------------------------------------------------


def test_payment_question_moves_to_e4_with_results_in_the_same_turn() -> None:
    chat = Conversation(mode=ConversationMode.BALANCE_OVERVIEW)

    chat.send("¿y si pago 100 mil?")

    request = chat.last_request
    assert request.mode is ConversationMode.AMOUNT_SIMULATION
    assert request.financial_data["monto_elegido"].value == 100_000
    assert request.calculations == {
        "meses_pagando_monto_elegido": 13,
        "intereses_pagando_monto_elegido": 217_060,
        "meses_pagando_minimo": 19,
        "intereses_pagando_minimo": 314_722,
        "meses_ahorrados": 6,
        "intereses_ahorrados": 97_662,
        # Con lo que entró en octubre, sus esenciales y la cuota del crédito
        # ya consumen todo el ingreso (principio 7).
        "faltante_para_gastos_esenciales": 100_000,
    }


def test_simulation_figures_pass_the_final_check() -> None:
    reply = "Pagando $100.000 al mes terminas en 13 meses y te ahorras $97.662 en intereses."
    chat = Conversation(AgentResponse(message=reply), mode=ConversationMode.BALANCE_OVERVIEW)

    assert chat.send("¿y si pago 100 mil?") == reply
    assert len(chat.agent.requests) == 1


def test_a_payment_below_the_interest_reports_the_interest() -> None:
    chat = Conversation(mode=ConversationMode.BALANCE_OVERVIEW)

    chat.send("¿y si pago 20 mil?")

    assert chat.last_request.calculations["intereses_del_mes"] == 29_792
    assert "meses_pagando_monto_elegido" not in chat.last_request.calculations


def test_no_simulation_before_she_has_seen_her_balance() -> None:
    chat = Conversation(mode=ConversationMode.GREETING)

    chat.send("¿y si pago 100 mil?")

    assert chat.last_request.mode is ConversationMode.GREETING
    assert chat.last_request.calculations == {}


def test_no_simulation_after_she_declined_figures() -> None:
    chat = Conversation(
        mode=ConversationMode.BALANCE_OVERVIEW,
        preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
    )

    chat.send("¿y si pago 100 mil?")

    assert chat.last_request.mode is ConversationMode.BALANCE_OVERVIEW
    assert chat.last_request.calculations == {}


# --- PAUSA_COMPRA: cuánto suma la cuota -----------------------------------


def test_installment_option_two_then_amount_gives_new_minimum() -> None:
    chat = Conversation(mode=ConversationMode.INSTALLMENT_PAUSE)

    chat.send("2")
    assert chat.last_request.requested_installment_calculation is True

    reply = "Serían $10.000 al mes; tu mínimo pasaría de $74.350 a unos $84.350."
    chat.agent.responses.append(AgentResponse(message=reply))
    assert chat.send("$60.000 en 6 cuotas") == reply

    request = chat.last_request
    assert request.financial_data["minimo"].value == 74_350
    assert request.financial_data["minimo_con_cuota"].value == 84_350
    assert request.calculations == {"cuota_mensual": 10_000}


def test_essential_purchase_gets_no_calculation() -> None:
    chat = Conversation(
        AgentResponse(message="ok", intent=Intent.INSTALLMENT_INTENT, essential=True),
        mode=ConversationMode.GREETING,
    )

    chat.send("necesito comprar los remedios de mi mamá en cuotas")
    chat.send("2")

    assert chat.state.mode is ConversationMode.INSTALLMENT_PAUSE
    assert chat.last_request.requested_installment_calculation is False


def test_leaving_the_installment_pause_resets_the_request() -> None:
    chat = Conversation(
        AgentResponse(message="ok"),
        AgentResponse(message="ok", intent=Intent.VOLUNTARY_RESUME),
        mode=ConversationMode.INSTALLMENT_PAUSE,
    )

    chat.send("2")
    chat.send("mejor no, sigamos con lo otro")

    assert chat.state.installment_calc_requested is False
